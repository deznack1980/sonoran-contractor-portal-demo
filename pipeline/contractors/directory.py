"""Secured, paginated contractor-directory queries.

The compatibility table is populated exclusively from verified canonical
companies by :mod:`pipeline.contractors.rebuild`. This service never falls
back to raw permit names or static exports.
"""

from __future__ import annotations

import json
import math
import sqlite3

from pipeline.auth.rbac import require_permission


def _integer(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _json(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def list_contractors(conn: sqlite3.Connection, user: dict, query: dict) -> dict:
    """Return verified contractors and complete directory metric parity."""
    require_permission(user, "companies.view")
    page = _integer(query.get("page"), 1, 1, 1_000_000)
    page_size = _integer(query.get("page_size"), 50, 1, 100)
    where = [
        "c.company_id IS NOT NULL", "c.verification_status='verified'",
        "co.lifecycle_state='active'", "co.lead_type='verified_contractor'",
    ]
    params: list = []
    search = str(query.get("q") or "").strip()
    if search:
        where.append("(c.name LIKE ? OR c.license_number LIKE ?)")
        token = f"%{search}%"
        params.extend([token, token])
    contact = str(query.get("contact") or "").lower()
    if contact == "available":
        where.append("c.has_contact_info=1")
    elif contact == "missing":
        where.append("c.has_contact_info=0")
    if str(query.get("multi") or "").lower() in {"1", "true", "yes"}:
        where.append("json_array_length(COALESCE(c.jurisdictions_worked,'[]')) > 1")
    sort_map = {
        "priority": "c.opportunity_rating DESC, c.last_permit_date DESC",
        "permits": "c.permit_count DESC, c.opportunity_rating DESC",
        "recent": "c.last_permit_date DESC, c.opportunity_rating DESC",
        "materials": "c.estimated_material_opportunity DESC, c.opportunity_rating DESC",
        "name": "c.name COLLATE NOCASE ASC",
    }
    order_by = sort_map.get(str(query.get("sort") or "priority"), sort_map["priority"])
    clause = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM contractors c JOIN companies co ON co.id=c.company_id WHERE {clause}",
        params,
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT c.*, co.display_name, co.main_phone, co.main_email, co.website
            FROM contractors c JOIN companies co ON co.id=c.company_id
            WHERE {clause} ORDER BY {order_by} LIMIT ? OFFSET ?""",
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["jurisdictions_worked"] = _json(item.get("jurisdictions_worked"), [])
        item["jurisdiction_breakdown"] = _json(item.get("jurisdiction_breakdown"), {})
        item["cities_worked"] = _json(item.get("cities_worked"), [])
        item["is_multi_jurisdiction"] = len(item["jurisdictions_worked"]) > 1
        item["has_contact_info"] = bool(item.get("has_contact_info"))
        items.append(item)
    stats = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN has_contact_info=1 THEN 1 ELSE 0 END) AS contact_ready,
                  SUM(CASE WHEN json_array_length(COALESCE(jurisdictions_worked,'[]'))>1 THEN 1 ELSE 0 END) AS multi_jurisdiction,
                  COALESCE(SUM(estimated_material_opportunity),0) AS material_opportunity
           FROM contractors WHERE company_id IS NOT NULL AND verification_status='verified'"""
    ).fetchone()
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, math.ceil(total / page_size)), "stats": dict(stats),
            "source": "verified_canonical_company_api"}
