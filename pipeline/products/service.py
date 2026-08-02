"""Permission-enforced product/supplier service (the API's façade).

Sales users (products.view) may search and view comparisons but never see cost
fields or change pricing. Only admins (supplier_pricing.manage) may add
suppliers, upload catalogs, or change delivery assumptions. Cost visibility
requires supplier_pricing.view.
"""

from __future__ import annotations

import base64
import sqlite3
from datetime import datetime, timezone

from pipeline.auth.rbac import AuthzError, has_permission, require_permission
from pipeline.auth.service import write_audit
from pipeline.crm.service import ValidationError
from pipeline.products import config as pricing_config
from pipeline.products.importer import (
    ensure_supplier,
    extract_record,
    import_catalog,
    read_rows_from_bytes,
)
from pipeline.products.routing import compare_offers
from pipeline.products.search import search_products


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _can_see_cost(user: dict) -> bool:
    return has_permission(user, "supplier_pricing.view")


# --------------------------------------------------------------------------
# Sales-facing
# --------------------------------------------------------------------------

def search(conn: sqlite3.Connection, user: dict, params: dict) -> dict:
    require_permission(user, "products.view")
    return search_products(
        conn, params.get("q", ""),
        category=params.get("category"),
        supplier_code=params.get("supplier"),
        in_stock_only=str(params.get("in_stock", "")).lower() in ("1", "true", "yes"),
        include_cost=_can_see_cost(user),
        page=params.get("page", 1),
        page_size=params.get("page_size"),
    )


def quote(conn: sqlite3.Connection, user: dict, product_id: int, quantity: float,
          jobsite: dict, *, ip=None, ua=None) -> dict:
    require_permission(user, "products.view")
    result = compare_offers(conn, int(product_id), quantity, jobsite or {},
                            include_cost=_can_see_cost(user))
    if "error" in result:
        raise ValidationError(result["error"])
    return result


# --------------------------------------------------------------------------
# Admin-facing (supplier_pricing.manage)
# --------------------------------------------------------------------------

def list_suppliers(conn: sqlite3.Connection, user: dict) -> list[dict]:
    require_permission(user, "products.view")
    rows = conn.execute(
        "SELECT id, name, code, city, state, warehouse_address, latitude, longitude, "
        "active FROM suppliers WHERE code IS NOT NULL ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def add_supplier(conn: sqlite3.Connection, user: dict, data: dict, *, ip=None, ua=None) -> dict:
    require_permission(user, "supplier_pricing.manage")
    code = (data.get("code") or "").strip().lower()
    name = (data.get("name") or "").strip()
    if not code or not name:
        raise ValidationError("supplier code and name are required")
    if conn.execute("SELECT 1 FROM suppliers WHERE code=?", (code,)).fetchone():
        raise ValidationError("a supplier with that code already exists")
    sid = ensure_supplier(
        conn, code, name=name, warehouse_address=data.get("warehouse_address"),
        city=data.get("city"), state=data.get("state"),
        postal_code=data.get("postal_code"),
        latitude=data.get("latitude"), longitude=data.get("longitude"))
    write_audit(conn, event_type="admin_change", success=True, user_id=user["id"],
                organization_id=user.get("organization_id"), resource_type="supplier",
                resource_id=sid, action="add_supplier", ip_address=ip, user_agent=ua)
    row = conn.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
    return dict(row)


def _decode_upload(data: dict):
    filename = data.get("filename") or "upload.csv"
    content_b64 = data.get("content_base64")
    if not content_b64:
        raise ValidationError("content_base64 is required")
    try:
        raw = base64.b64decode(content_b64)
    except Exception:
        raise ValidationError("content_base64 is not valid base64")
    return filename, raw


def preview_catalog(conn: sqlite3.Connection, user: dict, data: dict) -> dict:
    """Dry run: parse the file, infer counts, do NOT write anything."""
    require_permission(user, "supplier_pricing.manage")
    filename, raw = _decode_upload(data)
    rows, headers = read_rows_from_bytes(filename, raw)
    profile = data.get("profile") or ("sonoran_magento" if "sonoran" in
                                      (data.get("supplier") or "").lower() else "generic")
    mapping = data.get("mapping")

    imported = updated = zero_price = skipped = missing_mpn = dups = 0
    seen = set()
    existing_skus = {r["sku"] for r in conn.execute("SELECT sku FROM products")}
    sample = []
    for i, row in enumerate(rows):
        try:
            rec = extract_record(row, profile, mapping)
        except Exception:
            skipped += 1
            continue
        if not rec["sku"]:
            skipped += 1
            continue
        if rec["selling_price"] is None or rec["selling_price"] <= 0:
            zero_price += 1
            continue
        if rec["sku"] in seen:
            dups += 1
        seen.add(rec["sku"])
        if not rec["manufacturer_part_number"]:
            missing_mpn += 1
        if rec["sku"] in existing_skus:
            updated += 1
        else:
            imported += 1
        if len(sample) < 10:
            s = {k: rec[k] for k in ("sku", "product_name", "category",
                                     "selling_price", "quantity_available",
                                     "manufacturer_part_number", "unit_of_measure")}
            if _can_see_cost(user):
                s["cost_price"] = rec["cost_price"]
            sample.append(s)

    return {
        "filename": filename, "profile": profile, "headers": headers,
        "total_rows": len(rows),
        "counts": {"new_products": imported, "updated_offers": updated,
                   "zero_price_excluded": zero_price, "skipped": skipped,
                   "duplicate_skus": dups, "missing_mpn": missing_mpn},
        "sample": sample,
    }


def import_catalog_upload(conn: sqlite3.Connection, user: dict, data: dict,
                          *, ip=None, ua=None) -> dict:
    require_permission(user, "supplier_pricing.manage")
    filename, raw = _decode_upload(data)
    supplier_code = (data.get("supplier") or "").strip().lower()
    if not supplier_code:
        raise ValidationError("supplier code is required")
    rows, _ = read_rows_from_bytes(filename, raw)
    profile = data.get("profile") or ("sonoran_magento" if "sonoran" in supplier_code
                                      else "generic")
    stats = import_catalog(
        conn, supplier_code=supplier_code, rows=rows, source_file=filename,
        profile=profile, mapping=data.get("mapping"),
        supplier_name=data.get("supplier_name"),
        supplier_location=data.get("location"), performed_by=user["id"])
    write_audit(conn, event_type="admin_change", success=True, user_id=user["id"],
                organization_id=user.get("organization_id"), resource_type="catalog",
                resource_id=supplier_code, action="catalog_import",
                details={"file": filename, **stats.as_dict()}, ip_address=ip, user_agent=ua)
    return stats.as_dict()


def import_history(conn: sqlite3.Connection, user: dict) -> list[dict]:
    require_permission(user, "supplier_pricing.manage")
    rows = conn.execute(
        "SELECT il.*, s.name AS supplier_name FROM supplier_import_log il "
        "LEFT JOIN suppliers s ON s.id = il.supplier_id "
        "ORDER BY il.id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]


def get_delivery_settings(conn: sqlite3.Connection, user: dict) -> dict:
    require_permission(user, "supplier_pricing.manage")
    return pricing_config.get_delivery_config(conn)


def update_delivery_settings(conn: sqlite3.Connection, user: dict, updates: dict,
                             *, ip=None, ua=None) -> dict:
    require_permission(user, "supplier_pricing.manage")
    try:
        cfg = pricing_config.set_delivery_config(conn, updates, updated_by=user["id"])
    except ValueError:
        raise ValidationError("delivery config values must be numeric")
    write_audit(conn, event_type="admin_change", success=True, user_id=user["id"],
                organization_id=user.get("organization_id"), resource_type="pricing_config",
                action="update_delivery_settings", details={"keys": list(updates.keys())},
                ip_address=ip, user_agent=ua)
    return cfg
