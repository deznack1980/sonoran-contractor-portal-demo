"""Organization-scoped material-list persistence.

Material lists are operational CRM records. Every read/write enforces the user's
company access and organization boundary before returning or mutating data.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pipeline.auth.rbac import can_access_company, require_permission
from pipeline.auth.service import write_audit
from pipeline.crm.service import ValidationError


STATUSES = {"draft", "ready_for_review", "pricing_requested", "needs_catalog_review", "priced"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_company_access(conn: sqlite3.Connection, user: dict, company_id: int) -> None:
    if not can_access_company(conn, user, company_id):
        raise ValidationError("company not assigned to you")


def _serialize(conn: sqlite3.Connection, list_id: int) -> dict:
    row = conn.execute(
        """
        SELECT ml.*, c.display_name AS company_name,
               p.job_address AS project_name, p.permit_number
        FROM material_lists ml
        JOIN companies c ON c.id=ml.company_id
        LEFT JOIN projects pr ON pr.id=ml.project_id
        LEFT JOIN permits p ON p.id=pr.permit_id
        WHERE ml.id=?
        """,
        (list_id,),
    ).fetchone()
    if row is None:
        raise ValidationError("material list not found")
    items = [
        dict(item)
        for item in conn.execute(
            """
            SELECT id, line_number, product_id, quantity, unit,
                   requested_description AS description, manufacturer,
                   sku_snapshot AS sku, supplier_price_snapshot AS supplier_price,
                   quantity_available_snapshot AS quantity_available,
                   lead_time_days_snapshot AS lead_time_days, match_status
            FROM material_list_items
            WHERE material_list_id=?
            ORDER BY line_number, id
            """,
            (list_id,),
        )
    ]
    result = dict(row)
    result["rows"] = items
    return result


def latest(conn: sqlite3.Connection, user: dict, params: dict) -> dict:
    require_permission(user, "projects.view_assigned")
    try:
        company_id = int(params.get("company_id"))
    except (TypeError, ValueError):
        raise ValidationError("company_id is required")
    _require_company_access(conn, user, company_id)

    project_id = params.get("project_id")
    sql = (
        "SELECT id FROM material_lists WHERE organization_id=? AND company_id=? "
        + ("AND project_id=? " if project_id not in (None, "") else "AND project_id IS NULL ")
        + "ORDER BY updated_at DESC, id DESC LIMIT 1"
    )
    values = [user["organization_id"], company_id]
    if project_id not in (None, ""):
        values.append(int(project_id))
    row = conn.execute(sql, values).fetchone()
    return {"item": _serialize(conn, row["id"]) if row else None}


def save(conn: sqlite3.Connection, user: dict, data: dict, *, ip=None, ua=None) -> dict:
    require_permission(user, "projects.view_assigned")
    try:
        company_id = int(data.get("companyId") or data.get("company_id"))
    except (TypeError, ValueError):
        raise ValidationError("company_id is required")
    _require_company_access(conn, user, company_id)

    project_raw = data.get("projectId") or data.get("project_id")
    project_id = int(project_raw) if project_raw not in (None, "") else None
    if project_id is not None:
        project = conn.execute(
            "SELECT 1 FROM projects WHERE id=? AND contractor_company_id=?",
            (project_id, company_id),
        ).fetchone()
        if project is None:
            raise ValidationError("project does not belong to this contractor")

    status = str(data.get("status") or "draft").strip().lower().replace(" ", "_")
    if status not in STATUSES:
        raise ValidationError("invalid material list status")
    rows = data.get("rows") or []
    if not isinstance(rows, list):
        raise ValidationError("rows must be a list")

    now = _now()
    list_raw = data.get("materialListId") or data.get("material_list_id")
    existing = None
    if list_raw not in (None, ""):
        existing = conn.execute(
            "SELECT * FROM material_lists WHERE id=? AND organization_id=?",
            (int(list_raw), user["organization_id"]),
        ).fetchone()
        if existing is None or existing["company_id"] != company_id:
            raise ValidationError("material list not found")

    if existing:
        list_id = existing["id"]
        conn.execute(
            """
            UPDATE material_lists
            SET project_id=?, needed_by=?, delivery_preference=?, notes=?,
                source_name=?, status=?, updated_at=?
            WHERE id=?
            """,
            (
                project_id, data.get("neededBy"), data.get("deliveryPreference"),
                data.get("requestNotes"), data.get("sourceName"), status, now, list_id,
            ),
        )
        conn.execute("DELETE FROM material_list_items WHERE material_list_id=?", (list_id,))
        action = "update"
    else:
        cursor = conn.execute(
            """
            INSERT INTO material_lists (
                organization_id, company_id, project_id, created_by_user_id,
                needed_by, delivery_preference, notes, source_name, status,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user["organization_id"], company_id, project_id, user["id"],
                data.get("neededBy"), data.get("deliveryPreference"),
                data.get("requestNotes"), data.get("sourceName"), status, now, now,
            ),
        )
        list_id = cursor.lastrowid
        action = "create"

    line_number = 0
    for raw in rows:
        description = str(raw.get("description") or "").strip()
        quantity = float(raw.get("qty") or raw.get("quantity") or 0)
        if not description or quantity <= 0:
            continue
        line_number += 1
        product_raw = raw.get("productId") or raw.get("product_id")
        product_id = int(product_raw) if product_raw not in (None, "") else None
        match_status = "catalog" if product_id else "manual"
        conn.execute(
            """
            INSERT INTO material_list_items (
                material_list_id, line_number, product_id, quantity, unit,
                requested_description, manufacturer, sku_snapshot,
                supplier_price_snapshot, quantity_available_snapshot,
                lead_time_days_snapshot, match_status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                list_id, line_number, product_id, quantity,
                str(raw.get("unit") or "each").strip(),
                description, str(raw.get("manufacturer") or "").strip() or None,
                str(raw.get("sku") or "").strip() or None,
                raw.get("supplierPrice") if raw.get("supplierPrice") is not None
                else raw.get("supplier_price"),
                raw.get("quantityAvailable") if raw.get("quantityAvailable") is not None
                else raw.get("quantity_available"),
                raw.get("leadTimeDays") if raw.get("leadTimeDays") is not None
                else raw.get("lead_time_days"),
                match_status, now, now,
            ),
        )

    conn.commit()
    write_audit(
        conn, event_type="material_list_saved", success=True,
        user_id=user["id"], organization_id=user["organization_id"],
        resource_type="material_list", resource_id=list_id, action=action,
        ip_address=ip, user_agent=ua,
        details={"company_id": company_id, "project_id": project_id,
                 "status": status, "line_count": line_number},
    )
    return {"item": _serialize(conn, list_id)}
