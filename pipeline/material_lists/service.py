"""Organization-scoped material-list persistence.

Material lists are operational CRM records. Every read/write enforces the user's
company access and organization boundary before returning or mutating data.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from pipeline.auth.rbac import can_access_company, require_permission
from pipeline.auth.service import write_audit
from pipeline.crm.service import ValidationError


STATUSES = {"draft", "ready_for_review", "pricing_requested", "needs_catalog_review", "priced"}
RFQ_STATUSES = {"prepared", "sent", "responded", "declined", "awarded", "cancelled"}
RFQ_TRANSITIONS = {
    "prepared": {"sent", "cancelled"},
    "sent": {"responded", "declined", "cancelled"},
    "responded": {"awarded", "cancelled"},
    "declined": {"prepared"},
    "cancelled": {"prepared"},
    "awarded": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_company_access(conn: sqlite3.Connection, user: dict, company_id: int) -> None:
    if not can_access_company(conn, user, company_id):
        raise ValidationError("company not assigned to you")


def _serialize(conn: sqlite3.Connection, list_id: int) -> dict:
    row = conn.execute(
        """
        SELECT ml.*, c.display_name AS company_name,
               COALESCE(NULLIF(TRIM(ml.project_name_snapshot),''), p.job_address) AS project_name,
               p.permit_number
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
                   lead_time_days_snapshot AS lead_time_days,
                   allow_substitution, suggestion_source, estimate_confidence_pct,
                   estimate_rationale, quantity_status, match_status
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


def project_estimate(conn: sqlite3.Connection, user: dict, params: dict) -> dict:
    """Return the explainable preliminary material estimate for one project.

    This is intentionally category-level guidance. Permit records rarely
    contain fixture schedules or takeoff quantities, so no quantity is
    invented; the user must confirm every quantity before pricing or RFQ.
    """
    require_permission(user, "projects.view_assigned")
    try:
        company_id = int(params.get("company_id"))
        project_id = int(params.get("project_id"))
    except (TypeError, ValueError):
        raise ValidationError("company_id and project_id are required")
    _require_company_access(conn, user, company_id)
    project = conn.execute(
        """
        SELECT pr.id AS project_id,pr.project_category,pr.project_lifecycle,
               pr.estimated_plumbing_scope,pr.estimated_material_value,
               pr.confidence_score,pr.analysis_version,pr.analyzed_at,
               p.permit_number,p.permit_type,p.description,p.job_address,
               p.city,p.state,p.zip AS postal_code
        FROM projects pr JOIN permits p ON p.id=pr.permit_id
        WHERE pr.id=? AND pr.contractor_company_id=?
        """,
        (project_id, company_id),
    ).fetchone()
    if project is None:
        raise ValidationError("project does not belong to this contractor")
    materials = [dict(row) for row in conn.execute(
        "SELECT material_name,confidence_pct,rationale FROM estimated_materials "
        "WHERE project_id=? ORDER BY confidence_pct DESC,material_name",
        (project_id,),
    ).fetchall()]
    return {
        "project": dict(project),
        "suggestions": [
            {"description": row["material_name"], "confidence_pct": row["confidence_pct"],
             "rationale": row["rationale"], "quantity": None,
             "quantity_status": "needs_confirmation", "suggestion_source": "project_estimate"}
            for row in materials
        ],
        "quantity_policy": (
            "Quantities are not inferred from permit text. Confirm quantities against plans, "
            "a fixture schedule, contractor takeoff, or field conditions before pricing."
        ),
    }


def _list_for_access(conn: sqlite3.Connection, user: dict, list_id: int) -> dict:
    row = conn.execute(
        "SELECT organization_id, company_id FROM material_lists WHERE id=?",
        (int(list_id),),
    ).fetchone()
    if row is None or row["organization_id"] != user["organization_id"]:
        raise ValidationError("material list not found")
    _require_company_access(conn, user, row["company_id"])
    return _serialize(conn, int(list_id))


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
    if status != "draft":
        pending = [raw for raw in rows if str(raw.get("description") or "").strip() and (
            float(raw.get("qty") or raw.get("quantity") or 0) <= 0 or
            str(raw.get("quantityStatus") or raw.get("quantity_status") or "confirmed").strip().lower()
            == "needs_confirmation")]
        if pending:
            raise ValidationError("confirm every suggested material quantity before review or pricing")

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
            SET project_id=?, project_name_snapshot=?, needed_by=?,
                quote_needed_by=?, jobsite_postal_code=?,
                delivery_preference=?, notes=?, source_name=?, status=?, updated_at=?
            WHERE id=?
            """,
            (
                project_id, str(data.get("projectName") or "").strip() or None,
                data.get("neededBy"), data.get("quoteNeededBy"),
                str(data.get("jobsitePostalCode") or "").strip() or None,
                data.get("deliveryPreference"), data.get("requestNotes"),
                data.get("sourceName"), status, now, list_id,
            ),
        )
        conn.execute("DELETE FROM material_list_items WHERE material_list_id=?", (list_id,))
        action = "update"
    else:
        cursor = conn.execute(
            """
            INSERT INTO material_lists (
                organization_id, company_id, project_id, created_by_user_id,
                project_name_snapshot, needed_by, quote_needed_by, jobsite_postal_code,
                delivery_preference, notes, source_name, status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user["organization_id"], company_id, project_id, user["id"],
                str(data.get("projectName") or "").strip() or None,
                data.get("neededBy"), data.get("quoteNeededBy"),
                str(data.get("jobsitePostalCode") or "").strip() or None,
                data.get("deliveryPreference"), data.get("requestNotes"),
                data.get("sourceName"), status, now, now,
            ),
        )
        list_id = cursor.lastrowid
        action = "create"

    line_number = 0
    for raw in rows:
        description = str(raw.get("description") or "").strip()
        quantity = float(raw.get("qty") or raw.get("quantity") or 0)
        quantity_status = str(raw.get("quantityStatus") or raw.get("quantity_status") or "confirmed").strip().lower()
        if quantity_status not in {"needs_confirmation", "confirmed"}:
            raise ValidationError("invalid quantity status")
        if not description or (quantity <= 0 and quantity_status != "needs_confirmation"):
            continue
        line_number += 1
        product_raw = raw.get("productId") or raw.get("product_id")
        product_id = int(product_raw) if product_raw not in (None, "") else None
        match_status = "catalog" if product_id else "manual"
        estimate_confidence = (raw.get("estimateConfidencePct") if raw.get("estimateConfidencePct") is not None
                               else raw.get("estimate_confidence_pct"))
        if estimate_confidence not in (None, ""):
            estimate_confidence = float(estimate_confidence)
            if not 0 <= estimate_confidence <= 100:
                raise ValidationError("estimate confidence must be between 0 and 100")
        conn.execute(
            """
            INSERT INTO material_list_items (
                material_list_id, line_number, product_id, quantity, unit,
                requested_description, manufacturer, sku_snapshot,
                supplier_price_snapshot, quantity_available_snapshot,
                lead_time_days_snapshot, allow_substitution, suggestion_source,
                estimate_confidence_pct, estimate_rationale, quantity_status,
                match_status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                1 if raw.get("allowSubstitution", raw.get("allow_substitution", True)) else 0,
                str(raw.get("suggestionSource") or raw.get("suggestion_source") or "").strip() or None,
                estimate_confidence,
                str(raw.get("estimateRationale") or raw.get("estimate_rationale") or "").strip() or None,
                quantity_status, match_status, now, now,
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


def quote_supplier_options(conn: sqlite3.Connection, user: dict) -> dict:
    """Business-safe supplier choices for the manual RFQ composer."""
    require_permission(user, "products.quote")
    rows = conn.execute(
        """
        SELECT id, name, code, quote_contact_name, quote_email, quote_phone,
               city, state
        FROM suppliers
        WHERE active=1 AND code IS NOT NULL
        ORDER BY CASE WHEN quote_email IS NOT NULL AND TRIM(quote_email)<>'' THEN 0 ELSE 1 END,
                 name
        """
    ).fetchall()
    return {"items": [dict(row) for row in rows]}


def _quote_request(conn: sqlite3.Connection, request_id: int) -> dict:
    row = conn.execute(
        """
        SELECT r.*, s.name AS supplier_name, s.code AS supplier_code,
               ml.company_id, c.display_name AS company_name
        FROM supplier_quote_requests r
        JOIN suppliers s ON s.id=r.supplier_id
        JOIN material_lists ml ON ml.id=r.material_list_id
        JOIN companies c ON c.id=ml.company_id
        WHERE r.id=?
        """,
        (int(request_id),),
    ).fetchone()
    if row is None:
        raise ValidationError("quote request not found")
    return dict(row)


def list_quote_requests(conn: sqlite3.Connection, user: dict, list_id: int) -> dict:
    require_permission(user, "products.quote")
    _list_for_access(conn, user, list_id)
    rows = conn.execute(
        "SELECT id FROM supplier_quote_requests WHERE organization_id=? "
        "AND material_list_id=? ORDER BY updated_at DESC, id DESC",
        (user["organization_id"], int(list_id)),
    ).fetchall()
    return {"items": [_quote_request(conn, row["id"]) for row in rows]}


def _rfq_copy(material_list: dict, supplier: sqlite3.Row, user: dict,
              quote_needed_by: str | None, postal_code: str | None) -> tuple[str, str]:
    project = material_list.get("project_name") or material_list.get("permit_number") or "Project"
    company = material_list.get("company_name") or "Contractor"
    subject = f"RFQ — {project} — {company}"[:240]
    greeting = supplier["quote_contact_name"] or f"{supplier['name']} quote team"
    requested_by = user.get("display_name") or user.get("email") or "CorridorIQ"
    lines = [
        f"Hello {greeting},",
        "",
        f"Please quote the following materials for {company}.",
        f"Project / job: {project}",
        f"Jobsite ZIP: {postal_code or 'Not provided'}",
        f"Delivery preference: {material_list.get('delivery_preference') or 'Best available option'}",
        f"Materials needed by: {material_list.get('needed_by') or 'Please advise'}",
        f"Quote due by: {quote_needed_by or 'As soon as possible'}",
        "",
        "Materials:",
    ]
    for item in material_list.get("rows") or []:
        details = [
            f"{item.get('line_number')}. {item.get('quantity'):g} {item.get('unit') or 'each'} — "
            f"{item.get('description')}",
        ]
        if item.get("manufacturer"):
            details.append(f"Manufacturer: {item['manufacturer']}")
        if item.get("sku"):
            details.append(f"SKU: {item['sku']}")
        details.append("Alternates allowed" if item.get("allow_substitution") else "Exact item only")
        lines.append(" | ".join(details))
    notes = str(material_list.get("notes") or "").strip()
    if notes:
        lines.extend(["", f"Project notes: {notes[:2000]}"])
    lines.extend([
        "",
        "Please include unit pricing, availability, lead time, delivery cost, quote validity, and applicable terms.",
        "",
        "Thank you,",
        requested_by,
        "CorridorIQ",
    ])
    return subject, "\n".join(lines)


def prepare_quote_request(conn: sqlite3.Connection, user: dict, list_id: int,
                          data: dict, *, ip=None, ua=None) -> dict:
    require_permission(user, "products.quote")
    material_list = _list_for_access(conn, user, list_id)
    if not material_list.get("rows"):
        raise ValidationError("add at least one material item before preparing a quote request")
    if any(float(item.get("quantity") or 0) <= 0 or item.get("quantity_status") != "confirmed"
           for item in material_list["rows"]):
        raise ValidationError("confirm every suggested material quantity before preparing a quote request")
    try:
        supplier_id = int(data.get("supplier_id"))
    except (TypeError, ValueError):
        raise ValidationError("supplier_id is required")
    supplier = conn.execute(
        "SELECT * FROM suppliers WHERE id=? AND active=1", (supplier_id,)
    ).fetchone()
    if supplier is None:
        raise ValidationError("active supplier not found")

    quote_needed_by = str(data.get("quote_needed_by") or material_list.get("quote_needed_by") or "").strip() or None
    postal_code = str(data.get("jobsite_postal_code") or material_list.get("jobsite_postal_code") or "").strip() or None
    if not postal_code:
        raise ValidationError("jobsite ZIP is required before preparing an RFQ")
    subject, message = _rfq_copy(material_list, supplier, user, quote_needed_by, postal_code)
    now = _now()
    follow_up_at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec="seconds")
    existing = conn.execute(
        "SELECT * FROM supplier_quote_requests WHERE material_list_id=? AND supplier_id=?",
        (int(list_id), supplier_id),
    ).fetchone()
    if existing and existing["status"] in {"sent", "responded", "awarded"}:
        return {"item": _quote_request(conn, existing["id"]), "reused": True}

    values = (
        user["organization_id"], int(list_id), supplier_id, user["id"],
        supplier["quote_contact_name"], supplier["quote_email"], subject, message,
        quote_needed_by, postal_code, follow_up_at, now, now,
    )
    if existing:
        conn.execute(
            """
            UPDATE supplier_quote_requests
            SET requested_by_user_id=?, status='prepared', recipient_name=?, recipient_email=?,
                subject=?, message=?, quote_needed_by=?, jobsite_postal_code=?,
                follow_up_at=?, sent_at=NULL, responded_at=NULL, quoted_total=NULL,
                estimated_delivery_days=NULL, valid_until=NULL, response_notes=NULL,
                updated_at=?
            WHERE id=? AND organization_id=?
            """,
            (
                user["id"], supplier["quote_contact_name"], supplier["quote_email"],
                subject, message, quote_needed_by, postal_code, follow_up_at,
                now, existing["id"], user["organization_id"],
            ),
        )
        request_id = existing["id"]
    else:
        cursor = conn.execute(
            """
            INSERT INTO supplier_quote_requests (
                organization_id, material_list_id, supplier_id, requested_by_user_id,
                status, recipient_name, recipient_email, subject, message,
                quote_needed_by, jobsite_postal_code, follow_up_at, created_at, updated_at
            ) VALUES (?,?,?,?,'prepared',?,?,?,?,?,?,?,?,?)
            """,
            values,
        )
        request_id = cursor.lastrowid
    conn.execute(
        "UPDATE material_lists SET status='pricing_requested', quote_needed_by=?, "
        "jobsite_postal_code=?, updated_at=? WHERE id=?",
        (quote_needed_by, postal_code, now, int(list_id)),
    )
    conn.commit()
    write_audit(
        conn, event_type="supplier_quote_request_prepared", success=True,
        user_id=user["id"], organization_id=user["organization_id"],
        resource_type="supplier_quote_request", resource_id=request_id,
        action="prepare", ip_address=ip, user_agent=ua,
        details={"material_list_id": int(list_id), "supplier_id": supplier_id},
    )
    return {"item": _quote_request(conn, request_id), "reused": False}


def update_quote_request(conn: sqlite3.Connection, user: dict, request_id: int,
                         data: dict, *, ip=None, ua=None) -> dict:
    require_permission(user, "products.quote")
    current = _quote_request(conn, request_id)
    if current["organization_id"] != user["organization_id"]:
        raise ValidationError("quote request not found")
    _list_for_access(conn, user, current["material_list_id"])
    new_status = str(data.get("status") or "").strip().lower()
    if new_status not in RFQ_STATUSES:
        raise ValidationError("invalid quote request status")
    if new_status != current["status"] and new_status not in RFQ_TRANSITIONS[current["status"]]:
        raise ValidationError(f"cannot move quote request from {current['status']} to {new_status}")
    if new_status == "sent" and not str(current.get("recipient_email") or "").strip():
        raise ValidationError("add a supplier quote email before marking the RFQ sent")

    quoted_total = current.get("quoted_total")
    delivery_days = current.get("estimated_delivery_days")
    valid_until = current.get("valid_until")
    response_notes = current.get("response_notes")
    if new_status in {"responded", "awarded"}:
        raw_total = data.get("quoted_total", quoted_total)
        try:
            quoted_total = float(raw_total)
        except (TypeError, ValueError):
            raise ValidationError("quoted total is required when recording a response")
        if quoted_total < 0:
            raise ValidationError("quoted total cannot be negative")
        raw_days = data.get("estimated_delivery_days", delivery_days)
        delivery_days = int(raw_days) if raw_days not in (None, "") else None
        if delivery_days is not None and delivery_days < 0:
            raise ValidationError("delivery days cannot be negative")
        valid_until = str(data.get("valid_until") or valid_until or "").strip() or None
        response_notes = str(data.get("response_notes") or response_notes or "").strip()[:4000] or None

    now = _now()
    sent_at = current.get("sent_at") or (now if new_status == "sent" else None)
    responded_at = current.get("responded_at") or (now if new_status == "responded" else None)
    conn.execute(
        """
        UPDATE supplier_quote_requests
        SET status=?, sent_at=?, responded_at=?, quoted_total=?,
            estimated_delivery_days=?, valid_until=?, response_notes=?, updated_at=?
        WHERE id=? AND organization_id=?
        """,
        (
            new_status, sent_at, responded_at, quoted_total, delivery_days,
            valid_until, response_notes, now, int(request_id), user["organization_id"],
        ),
    )
    if new_status in {"responded", "awarded"}:
        conn.execute(
            "UPDATE material_lists SET status='priced', updated_at=? WHERE id=?",
            (now, current["material_list_id"]),
        )
    if new_status == "awarded":
        conn.execute(
            "UPDATE supplier_quote_requests SET status='cancelled', updated_at=? "
            "WHERE material_list_id=? AND id<>? "
            "AND status IN ('prepared','sent','responded')",
            (now, current["material_list_id"], int(request_id)),
        )
    conn.commit()
    write_audit(
        conn, event_type="supplier_quote_request_updated", success=True,
        user_id=user["id"], organization_id=user["organization_id"],
        resource_type="supplier_quote_request", resource_id=int(request_id),
        action=new_status, ip_address=ip, user_agent=ua,
        details={"material_list_id": current["material_list_id"],
                 "supplier_id": current["supplier_id"]},
    )
    return {"item": _quote_request(conn, int(request_id))}
