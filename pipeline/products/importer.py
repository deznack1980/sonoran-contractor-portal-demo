"""Idempotent supplier catalog importer.

Keeps one master product per SKU and a per-supplier offer row. Reruns update
existing offers rather than duplicating them. Rows with price <= 0 are excluded
and counted. Supports the Sonoran Magento export out of the box and a generic
column-mapping profile for future suppliers.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

# Keys we extract from Magento's packed ``additional_attributes`` column.
_ATTR_PATTERNS = {
    "cost": re.compile(r"(?:^|,)cost=([0-9]*\.?[0-9]+)"),
    "mpn": re.compile(r"(?:^|,)mpn=([^,]+)"),
    "uom": re.compile(r"(?:^|,)ecc_default_uom=([^,]+)"),
    "uom_alt": re.compile(r"(?:^|,)ecc_uom=([^,]+)"),
    "lead": re.compile(r"(?:^|,)ecc_lead_time=([^,]+)"),
}
_LEAD_DIGITS = re.compile(r"(\d+)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class ImportStats:
    total_rows: int = 0
    imported: int = 0          # new supplier offers created
    updated: int = 0           # existing supplier offers updated
    skipped: int = 0           # invalid / no sku
    failed: int = 0
    zero_price_excluded: int = 0
    duplicate_skus: int = 0    # same SKU appearing more than once in the file
    missing_mpn: int = 0
    products_created: int = 0
    products_matched: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def _to_float(value):
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_lead_time(value):
    if not value:
        return None
    s = str(value).strip().lower()
    if "same" in s:  # "same day"
        return 0
    m = _LEAD_DIGITS.search(s)
    return int(m.group(1)) if m else None


def _clean_category(value):
    if not value:
        return None
    # Magento categories look like "Default Category/A/B/C"; may contain several
    # comma-separated paths — keep the first, drop the root segment.
    first = str(value).split(",")[0].strip()
    for prefix in ("Default Category/", "Default Category"):
        if first.startswith(prefix):
            first = first[len(prefix):].lstrip("/")
    return first or None


def parse_additional_attributes(blob: str) -> dict:
    out = {}
    if not blob:
        return out
    for key in ("cost", "mpn", "lead"):
        m = _ATTR_PATTERNS[key].search(blob)
        if m:
            out[key] = m.group(1).strip()
    m = _ATTR_PATTERNS["uom"].search(blob) or _ATTR_PATTERNS["uom_alt"].search(blob)
    if m:
        out["uom"] = m.group(1).strip()
    return out


def extract_record(row: dict, profile: str, mapping: dict | None) -> dict:
    """Normalize a raw source row into product/offer fields."""
    if profile == "sonoran_magento":
        attrs = parse_additional_attributes(row.get("additional_attributes", ""))
        return {
            "sku": (row.get("sku") or "").strip(),
            "product_name": (row.get("name") or "").strip() or None,
            "description": (row.get("description") or "").strip() or None,
            "category": _clean_category(row.get("categories")),
            "unit_of_measure": attrs.get("uom"),
            "manufacturer_part_number": attrs.get("mpn"),
            "manufacturer": None,
            "selling_price": _to_float(row.get("price")),
            "cost_price": _to_float(attrs.get("cost")),
            "quantity_available": _to_float(row.get("qty")),
            "lead_time_days": _parse_lead_time(attrs.get("lead")),
            "supplier_sku": (row.get("sku") or "").strip(),
            "is_in_stock": row.get("is_in_stock"),
        }
    # Generic mapping profile: mapping maps our field name -> source column name.
    mapping = mapping or {}

    def g(field_name):
        col = mapping.get(field_name)
        return row.get(col) if col else None

    sku = (g("sku") or "").strip()
    return {
        "sku": sku,
        "product_name": (g("product_name") or "").strip() or None,
        "description": (g("description") or "").strip() or None,
        "category": _clean_category(g("category")),
        "unit_of_measure": (g("unit_of_measure") or "").strip() or None,
        "manufacturer_part_number": (g("manufacturer_part_number") or "").strip() or None,
        "manufacturer": (g("manufacturer") or "").strip() or None,
        "selling_price": _to_float(g("price")),
        "cost_price": _to_float(g("cost")),
        "quantity_available": _to_float(g("quantity")),
        "lead_time_days": _parse_lead_time(g("lead_time")),
        "supplier_sku": (g("supplier_sku") or sku).strip(),
        "is_in_stock": g("in_stock"),
    }


def ensure_supplier(conn: sqlite3.Connection, code: str, *, name: str | None = None,
                    warehouse_address=None, city=None, state=None, postal_code=None,
                    latitude=None, longitude=None) -> int:
    now = _now()
    row = conn.execute("SELECT id FROM suppliers WHERE code=?", (code,)).fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO suppliers (name, code, warehouse_address, city, state, "
        "postal_code, latitude, longitude, active, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,1,?,?)",
        (name or code.title(), code, warehouse_address, city, state, postal_code,
         latitude, longitude, now, now),
    )
    conn.commit()
    return cur.lastrowid


def _upsert_product(conn, rec, now) -> tuple[int, bool]:
    """Return (product_id, created)."""
    existing = conn.execute("SELECT id FROM products WHERE sku=?", (rec["sku"],)).fetchone()
    if existing is None:
        cur = conn.execute(
            "INSERT INTO products (sku, manufacturer, manufacturer_part_number, "
            "product_name, description, category, unit_of_measure, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (rec["sku"], rec["manufacturer"], rec["manufacturer_part_number"],
             rec["product_name"], rec["description"], rec["category"],
             rec["unit_of_measure"], now, now),
        )
        return cur.lastrowid, True
    pid = existing["id"]
    # Fill/refresh descriptive fields (COALESCE keeps existing when new is NULL).
    conn.execute(
        "UPDATE products SET "
        "manufacturer=COALESCE(?, manufacturer), "
        "manufacturer_part_number=COALESCE(?, manufacturer_part_number), "
        "product_name=COALESCE(?, product_name), "
        "description=COALESCE(?, description), "
        "category=COALESCE(?, category), "
        "unit_of_measure=COALESCE(?, unit_of_measure), "
        "updated_at=? WHERE id=?",
        (rec["manufacturer"], rec["manufacturer_part_number"], rec["product_name"],
         rec["description"], rec["category"], rec["unit_of_measure"], now, pid),
    )
    return pid, False


def _upsert_offer(conn, supplier_id, product_id, rec, source_file, now) -> bool:
    """Return True if a new offer was created, False if an existing one updated."""
    existing = conn.execute(
        "SELECT id FROM supplier_products WHERE supplier_id=? AND product_id=?",
        (supplier_id, product_id),
    ).fetchone()
    active = 1
    if rec.get("is_in_stock") is not None:
        try:
            active = 1 if int(float(rec["is_in_stock"])) != 0 else 0
        except (TypeError, ValueError):
            active = 1
    if existing is None:
        conn.execute(
            "INSERT INTO supplier_products (supplier_id, product_id, supplier_sku, "
            "selling_price, cost_price, quantity_available, lead_time_days, active, "
            "source_file, effective_date, imported_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (supplier_id, product_id, rec["supplier_sku"], rec["selling_price"],
             rec["cost_price"], rec["quantity_available"], rec["lead_time_days"],
             active, source_file, _today(), now, now),
        )
        return True
    conn.execute(
        "UPDATE supplier_products SET supplier_sku=?, selling_price=?, cost_price=?, "
        "quantity_available=?, lead_time_days=?, active=?, source_file=?, "
        "effective_date=?, updated_at=? WHERE id=?",
        (rec["supplier_sku"], rec["selling_price"], rec["cost_price"],
         rec["quantity_available"], rec["lead_time_days"], active, source_file,
         _today(), now, existing["id"]),
    )
    return False


def import_catalog(conn: sqlite3.Connection, *, supplier_code: str, rows,
                   source_file: str, profile: str = "generic",
                   mapping: dict | None = None, supplier_name: str | None = None,
                   supplier_location: dict | None = None,
                   performed_by: int | None = None) -> ImportStats:
    """Import an iterable of raw row dicts. Idempotent per (supplier, SKU)."""
    stats = ImportStats()
    started = _now()
    loc = supplier_location or {}
    supplier_id = ensure_supplier(conn, supplier_code, name=supplier_name, **loc)
    seen_skus: set[str] = set()
    now = _now()

    for row in rows:
        stats.total_rows += 1
        try:
            rec = extract_record(row, profile, mapping)
        except Exception:
            stats.failed += 1
            continue
        if not rec["sku"]:
            stats.skipped += 1
            continue
        price = rec["selling_price"]
        if price is None or price <= 0:
            stats.zero_price_excluded += 1
            continue
        if rec["sku"] in seen_skus:
            stats.duplicate_skus += 1
        seen_skus.add(rec["sku"])
        if not rec["manufacturer_part_number"]:
            stats.missing_mpn += 1
        try:
            product_id, created = _upsert_product(conn, rec, now)
            if created:
                stats.products_created += 1
            else:
                stats.products_matched += 1
            is_new = _upsert_offer(conn, supplier_id, product_id, rec, source_file, now)
            if is_new:
                stats.imported += 1
            else:
                stats.updated += 1
        except Exception:
            stats.failed += 1
    conn.commit()

    conn.execute(
        "INSERT INTO supplier_import_log (supplier_id, supplier_code, source_file, "
        "imported, updated, skipped, failed, zero_price_excluded, duplicate_skus, "
        "missing_mpn, total_rows, performed_by, started_at, finished_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (supplier_id, supplier_code, source_file, stats.imported, stats.updated,
         stats.skipped, stats.failed, stats.zero_price_excluded, stats.duplicate_skus,
         stats.missing_mpn, stats.total_rows, performed_by, started, _now()),
    )
    conn.commit()
    return stats


# --------------------------------------------------------------------------
# File readers
# --------------------------------------------------------------------------

def read_rows_from_file(path: str):
    """Yield (rows, headers) from a CSV or XLSX file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        return _read_xlsx(p)
    return _read_csv(p)


def _read_csv(p: Path):
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = [dict(r) for r in reader]
    return rows, headers


def read_rows_from_bytes(filename: str, data: bytes):
    """Yield (rows, headers) from in-memory CSV/XLSX bytes (admin upload)."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import io
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        return _rows_from_worksheet(ws, wb)
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(text.splitlines())
    headers = reader.fieldnames or []
    return [dict(r) for r in reader], headers


def _rows_from_worksheet(ws, wb):
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = list(next(rows_iter))
    except StopIteration:
        wb.close()
        return [], []
    headers = [str(h) if h is not None else "" for h in header]
    rows = []
    for values in rows_iter:
        rows.append({headers[i]: values[i] if i < len(values) else None
                     for i in range(len(headers))})
    wb.close()
    return rows, headers


def _read_xlsx(p: Path):
    from openpyxl import load_workbook

    wb = load_workbook(p, read_only=True, data_only=True)
    return _rows_from_worksheet(wb.active, wb)


SONORAN_DEFAULTS = {
    "supplier_code": "sonoran",
    "supplier_name": "Sonoran Supply",
    "profile": "sonoran_magento",
}
