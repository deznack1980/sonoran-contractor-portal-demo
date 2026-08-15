"""Read queries for the Company Intelligence API + static export.

Centralized so the HTTP API and the JSON exporter return identical shapes.
All list queries support pagination — never return the full dataset at once.
"""

from __future__ import annotations

import json
import sqlite3

from pipeline.config.settings import (
    COMPANY_PAGE_SIZE_DEFAULT,
    COMPANY_PAGE_SIZE_MAX,
)

_ROLE_COLUMNS = (
    "contractor_company_id",
    "owner_company_id",
    "developer_company_id",
    "architect_company_id",
    "engineer_company_id",
)

_CONTACT_INFO_SQL = (
    "(TRIM(COALESCE(c.main_phone,''))<>'' OR TRIM(COALESCE(c.main_email,''))<>'' "
    "OR TRIM(COALESCE(c.website,''))<>'' OR EXISTS (SELECT 1 FROM contacts ct "
    "WHERE ct.company_id=c.id AND (TRIM(COALESCE(ct.phone,''))<>'' OR "
    "TRIM(COALESCE(ct.mobile_phone,''))<>'' OR TRIM(COALESCE(ct.email,''))<>'')))"
)


def _clamp_page_size(value) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return COMPANY_PAGE_SIZE_DEFAULT
    return max(1, min(size, COMPANY_PAGE_SIZE_MAX))


def _roles_for(conn: sqlite3.Connection, company_id: int) -> list[str]:
    return [r["role_type"] for r in conn.execute(
        "SELECT role_type FROM company_roles WHERE company_id=? "
        "AND (role_type<>'contractor' OR verification_status='verified' OR verification_status IS NULL) "
        "ORDER BY is_primary DESC, role_type",
        (company_id,),
    )]


def list_companies(conn: sqlite3.Connection, filters: dict) -> dict:
    """Filtered, paginated company list. Sorted priority DESC, latest activity DESC."""
    page = max(1, int(filters.get("page", 1) or 1))
    page_size = _clamp_page_size(filters.get("page_size", COMPANY_PAGE_SIZE_DEFAULT))
    where = ["c.lifecycle_state='active'"]
    params: list = []

    q = (filters.get("q") or "").strip()
    if q:
        where.append("(c.normalized_name LIKE ? OR c.display_name LIKE ?)")
        like = f"%{q.upper()}%"
        params += [like, f"%{q}%"]

    role = (filters.get("role") or "").strip()
    if role:
        where.append("EXISTS (SELECT 1 FROM company_roles r WHERE r.company_id=c.id AND r.role_type=? "
                     "AND (r.role_type<>'contractor' OR r.verification_status='verified' OR r.verification_status IS NULL))")
        params.append(role)

    lead_type = (filters.get("lead_type") or "").strip()
    if lead_type and lead_type != "all":
        where.append("c.lead_type=?")
        params.append(lead_type)

    tier = (filters.get("tier") or "").strip()
    if tier:
        where.append("ci.company_priority_tier=?")
        params.append(tier)

    if (filters.get("commercial") or "").strip() in ("1", "true", "yes"):
        where.append("COALESCE(ci.commercial_project_count,0) > 0")
    if (filters.get("residential") or "").strip() in ("1", "true", "yes"):
        where.append("COALESCE(ci.residential_project_count,0) > 0")
    if (filters.get("active") or "").strip() in ("1", "true", "yes"):
        where.append("COALESCE(ci.active_projects,0) > 0")

    since = (filters.get("activity_since") or "").strip()
    if since:
        where.append("ci.latest_activity_date >= ?")
        params.append(since[:10])

    muni = (filters.get("municipality") or "").strip()
    if muni:
        where.append(
            "EXISTS (SELECT 1 FROM projects pr WHERE pr.contractor_company_id=c.id "
            "AND pr.jurisdiction=?)"
        )
        params.append(muni)

    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM companies c "
        f"LEFT JOIN company_intelligence ci ON ci.company_id=c.id WHERE {where_sql}",
        params,
    ).fetchone()["n"]

    rows = conn.execute(
        f"""
        SELECT c.id, c.display_name, c.normalized_name, c.city, c.state,
               c.company_type_primary, c.license_number, c.lead_type,
               c.lead_verification_status, c.lead_source, c.why_this_lead,
               ci.company_priority_score, ci.company_priority_tier,
               ci.total_projects, ci.active_projects, ci.projects_last_30_days,
               ci.average_opportunity_score, ci.highest_opportunity_score,
               ci.municipality_count, ci.commercial_project_count,
               ci.residential_project_count, ci.activity_trend,
               ci.latest_activity_date, ci.estimated_opportunity_total,
               CASE WHEN {_CONTACT_INFO_SQL} THEN 1 ELSE 0 END AS has_contact_info
        FROM companies c
        LEFT JOIN company_intelligence ci ON ci.company_id=c.id
        WHERE {where_sql}
        ORDER BY CASE WHEN c.lead_type='verified_contractor' THEN 0 ELSE 1 END,
                 has_contact_info DESC,
                 COALESCE(ci.projects_last_30_days,0) DESC,
                 COALESCE(ci.active_projects,0) DESC,
                 COALESCE(ci.highest_opportunity_score,0) DESC,
                 COALESCE(ci.estimated_opportunity_total,0) DESC,
                 c.display_name
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()

    items = []
    for r in rows:
        d = dict(r)
        d["roles"] = _roles_for(conn, r["id"])
        items.append(d)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "items": items,
    }


def get_company(conn: sqlite3.Connection, company_id: int) -> dict | None:
    company = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if company is None:
        return None
    intel = conn.execute(
        "SELECT * FROM company_intelligence WHERE company_id=?", (company_id,)
    ).fetchone()
    aliases = [dict(r) for r in conn.execute(
        "SELECT alias_name, source, first_seen_at FROM company_aliases "
        "WHERE company_id=? ORDER BY alias_name", (company_id,))]
    roles = [dict(r) for r in conn.execute(
        "SELECT role_type, is_primary, source, confidence FROM company_roles "
        "WHERE company_id=? ORDER BY is_primary DESC", (company_id,))]
    contacts = [dict(r) for r in conn.execute(
        "SELECT full_name, job_title, department, email, phone, mobile_phone, "
        "preferred_contact_method, is_primary, source, last_seen_at "
        "FROM contacts WHERE company_id=?", (company_id,))]
    # Data quality: potential duplicates (same normalized name, other ids).
    dupes = conn.execute(
        "SELECT COUNT(*) AS n FROM companies WHERE normalized_name=? AND id<>? "
        "AND lifecycle_state='active'",
        (company["normalized_name"], company_id),
    ).fetchone()["n"]
    source_records = conn.execute(
        "SELECT COUNT(*) AS n FROM projects WHERE contractor_company_id=? "
        "OR owner_company_id=? OR developer_company_id=? OR architect_company_id=? "
        "OR engineer_company_id=?",
        (company_id,) * 5,
    ).fetchone()["n"]
    return {
        "company": dict(company),
        "intelligence": dict(intel) if intel else None,
        "aliases": aliases,
        "roles": roles,
        "contacts": contacts,
        "data_quality": {
            "source_record_count": source_records,
            "potential_duplicates": dupes,
            "missing_fields": [
                f for f in ("main_phone", "main_email", "license_number",
                            "address_line_1", "website")
                if not (company[f] and str(company[f]).strip())
            ],
            "metrics_model_version": intel["model_version"] if intel else None,
        },
    }


def _company_project_filter() -> str:
    return " OR ".join(f"pr.{c}=?" for c in _ROLE_COLUMNS)


def company_projects(conn: sqlite3.Connection, company_id: int,
                     active_only: bool = False, limit: int = 500) -> list[dict]:
    where = f"({_company_project_filter()})"
    params = [company_id] * len(_ROLE_COLUMNS)
    rows = conn.execute(
        f"""
        SELECT DISTINCT pr.id AS project_id, p.permit_number, p.jurisdiction, p.city,
               p.job_address, p.status, pr.project_category, pr.project_lifecycle,
               pr.opportunity_score, pr.opportunity_date, pr.opportunity_timing,
               pr.estimated_material_value
        FROM projects pr JOIN permits p ON p.id = pr.permit_id
        WHERE {where}
        ORDER BY pr.opportunity_date DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    return [dict(r) for r in rows]


def company_permits(conn: sqlite3.Connection, company_id: int, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.permit_number, p.jurisdiction, p.permit_type, p.status, p.city,
               p.job_address, p.filed_date, p.issued_date, p.finaled_date, p.valuation
        FROM permits p
        WHERE p.contractor_company_id=?
        ORDER BY p.issued_date DESC
        LIMIT ?
        """,
        (company_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def company_timeline(conn: sqlite3.Connection, company_id: int, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        """
        SELECT activity_type, activity_date, title, description, permit_id, project_id, source
        FROM company_activity WHERE company_id=?
        ORDER BY COALESCE(activity_date, created_at) DESC, id DESC
        LIMIT ?
        """,
        (company_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def company_metrics(conn: sqlite3.Connection, company_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM company_intelligence WHERE company_id=?", (company_id,)
    ).fetchone()
    return dict(row) if row else None


def list_match_review(conn: sqlite3.Connection, filters: dict) -> dict:
    page = max(1, int(filters.get("page", 1) or 1))
    page_size = _clamp_page_size(filters.get("page_size", COMPANY_PAGE_SIZE_DEFAULT))
    state = (filters.get("state") or "pending").strip()
    where = ["q.lifecycle_state=?"]
    params: list = [state]
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM company_match_review_queue q WHERE {' AND '.join(where)}",
        params,
    ).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT q.*, c.display_name AS candidate_name
        FROM company_match_review_queue q
        LEFT JOIN companies c ON c.id = q.candidate_company_id
        WHERE {' AND '.join(where)}
        ORDER BY q.match_confidence DESC, q.id
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        for k in ("match_reasons", "conflicting_fields"):
            try:
                d[k] = json.loads(d[k]) if d[k] else []
            except (TypeError, ValueError):
                d[k] = []
        items.append(d)
    return {"total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size, "items": items}
