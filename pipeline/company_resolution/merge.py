"""Create / link / merge canonical company records (all auditable)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pipeline.company_resolution.audit import write_identity_audit
from pipeline.company_resolution.models import CompanyInput
from pipeline.company_resolution.normalize import (
    normalize_city,
    normalize_company_name,
    normalize_license,
    normalize_phone,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------
# Roles / aliases
# ---------------------------------------------------------------
def ensure_role(
    conn: sqlite3.Connection,
    company_id: int,
    role_type: str,
    *,
    is_primary: bool = False,
    source: str | None = None,
    confidence: float | None = None,
    verification_status: str | None = None,
    evidence_source: str | None = None,
    evidence_field: str | None = None,
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO company_roles
            (company_id, role_type, is_primary, source, confidence,
             verification_status, evidence_source, evidence_field, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, role_type) DO UPDATE SET
            is_primary = MAX(company_roles.is_primary, excluded.is_primary),
            verification_status = COALESCE(excluded.verification_status, company_roles.verification_status),
            evidence_source = COALESCE(excluded.evidence_source, company_roles.evidence_source),
            evidence_field = COALESCE(excluded.evidence_field, company_roles.evidence_field),
            updated_at = excluded.updated_at
        """,
        (company_id, role_type, 1 if is_primary else 0, source, confidence,
         verification_status, evidence_source, evidence_field, now, now),
    )
    if is_primary:
        conn.execute(
            "UPDATE company_roles SET is_primary=0, updated_at=? "
            "WHERE company_id=? AND role_type<>?",
            (now, company_id, role_type),
        )
        conn.execute(
            "UPDATE companies SET company_type_primary=?, updated_at=? WHERE id=?",
            (role_type, now, company_id),
        )


def add_alias(
    conn: sqlite3.Connection,
    company_id: int,
    alias_name: str,
    *,
    source: str | None = None,
    source_record_id: str | None = None,
) -> None:
    normalized = normalize_company_name(alias_name)
    if not normalized:
        return
    now = _now()
    conn.execute(
        """
        INSERT INTO company_aliases
            (company_id, alias_name, normalized_alias, source, source_record_id,
             first_seen_at, last_seen_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, normalized_alias) DO UPDATE SET
            last_seen_at = excluded.last_seen_at
        """,
        (company_id, alias_name.strip(), normalized, source, source_record_id, now, now, now),
    )


# ---------------------------------------------------------------
# Company create / profile enrichment
# ---------------------------------------------------------------
def create_company(
    conn: sqlite3.Connection,
    data: CompanyInput,
    *,
    performed_by: str = "system",
    audit: bool = True,
) -> int:
    now = _now()
    normalized = normalize_company_name(data.name)
    cur = conn.execute(
        """
        INSERT INTO companies
            (legal_name, display_name, normalized_name, company_type_primary,
             main_phone, main_email, address_line_1, city, state,
             license_number, license_state, source_system, source_record_id,
             lifecycle_state, is_active, first_seen_at, last_seen_at,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, ?, ?)
        """,
        (
            data.name.strip() if data.name else None,
            data.name.strip() if data.name else None,
            normalized,
            data.role_type,
            normalize_phone(data.phone),
            (data.email or None),
            data.address_line_1,
            data.city,
            data.state,
            normalize_license(data.license_number),
            data.license_state,
            data.source_system,
            data.source_record_id,
            now,
            now,
            now,
            now,
        ),
    )
    company_id = cur.lastrowid
    ensure_role(conn, company_id, data.role_type, is_primary=True,
                source=data.source_system)
    add_alias(conn, company_id, data.name, source=data.source_system,
              source_record_id=data.source_record_id)
    if audit:
        write_identity_audit(
            conn,
            action="created",
            target_company_id=company_id,
            source_record_type=data.source_system,
            source_record_id=data.source_record_id,
            new_values={"normalized_name": normalized, "name": data.name},
            performed_by=performed_by,
        )
    return company_id


def enrich_company(conn: sqlite3.Connection, company_id: int, data: CompanyInput) -> None:
    """Fill only NULL/empty profile fields from a new source sighting. Never
    overwrites an existing non-null value."""
    row = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if row is None:
        return
    updates: dict[str, object] = {}
    candidates = {
        "main_phone": normalize_phone(data.phone),
        "main_email": (data.email or None),
        "address_line_1": data.address_line_1,
        "city": normalize_city(data.city),
        "state": (data.state or None),
        "license_number": normalize_license(data.license_number),
        "license_state": data.license_state,
    }
    for col, value in candidates.items():
        if value and not (row[col] and str(row[col]).strip()):
            updates[col] = value
    now = _now()
    if updates:
        set_clause = ", ".join(f"{c}=?" for c in updates)
        conn.execute(
            f"UPDATE companies SET {set_clause}, last_seen_at=?, updated_at=? WHERE id=?",
            (*updates.values(), now, now, company_id),
        )
    else:
        conn.execute(
            "UPDATE companies SET last_seen_at=?, updated_at=? WHERE id=?",
            (now, now, company_id),
        )


# ---------------------------------------------------------------
# Linking projects / permits
# ---------------------------------------------------------------
_PROJECT_ROLE_COLUMN = {
    "contractor": "contractor_company_id",
    "property_owner": "owner_company_id",
    "developer": "developer_company_id",
    "architect": "architect_company_id",
    "engineer": "engineer_company_id",
}


def link_project(conn: sqlite3.Connection, project_id: int, company_id: int,
                 role_type: str) -> bool:
    """Set the role-specific company FK on a project. Returns True if changed."""
    column = _PROJECT_ROLE_COLUMN.get(role_type, "contractor_company_id")
    cur = conn.execute(
        f"UPDATE projects SET {column}=? WHERE id=? AND "
        f"({column} IS NULL OR {column}<>?)",
        (company_id, project_id, company_id),
    )
    return cur.rowcount > 0


def link_permit(conn: sqlite3.Connection, permit_id: int, company_id: int) -> bool:
    cur = conn.execute(
        "UPDATE permits SET contractor_company_id=? WHERE id=? AND "
        "(contractor_company_id IS NULL OR contractor_company_id<>?)",
        (company_id, permit_id, company_id),
    )
    return cur.rowcount > 0


# ---------------------------------------------------------------
# Merge (never hard-delete)
# ---------------------------------------------------------------
def merge_companies(
    conn: sqlite3.Connection,
    survivor_id: int,
    victim_id: int,
    *,
    reason: str | None = None,
    performed_by: str = "system",
) -> None:
    """Merge victim into survivor: re-point links, move aliases/roles, mark
    victim deprecated. History preserved — nothing is deleted."""
    if survivor_id == victim_id:
        return
    now = _now()
    # Re-point project/permit FKs.
    for col in _PROJECT_ROLE_COLUMN.values():
        conn.execute(f"UPDATE projects SET {col}=? WHERE {col}=?", (survivor_id, victim_id))
    conn.execute(
        "UPDATE permits SET contractor_company_id=? WHERE contractor_company_id=?",
        (survivor_id, victim_id),
    )
    # Move aliases + the victim's own name as an alias.
    victim = conn.execute("SELECT * FROM companies WHERE id=?", (victim_id,)).fetchone()
    if victim:
        add_alias(conn, survivor_id, victim["display_name"] or victim["normalized_name"],
                  source="merge")
    for a in conn.execute("SELECT * FROM company_aliases WHERE company_id=?", (victim_id,)):
        add_alias(conn, survivor_id, a["alias_name"], source=a["source"],
                  source_record_id=a["source_record_id"])
    # Move roles.
    for r in conn.execute("SELECT role_type FROM company_roles WHERE company_id=?", (victim_id,)):
        ensure_role(conn, survivor_id, r["role_type"], source="merge")
    # Move timeline + contacts.
    conn.execute("UPDATE company_activity SET company_id=? WHERE company_id=?",
                 (survivor_id, victim_id))
    conn.execute("UPDATE contacts SET company_id=? WHERE company_id=?",
                 (survivor_id, victim_id))
    # Deprecate victim (never deleted).
    conn.execute(
        "UPDATE companies SET lifecycle_state='merged', is_active=0, "
        "merged_into_id=?, updated_at=? WHERE id=?",
        (survivor_id, now, victim_id),
    )
    write_identity_audit(
        conn,
        action="merged",
        source_company_id=victim_id,
        target_company_id=survivor_id,
        reason=reason,
        performed_by=performed_by,
    )
