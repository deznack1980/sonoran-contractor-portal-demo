"""Approve / reject ambiguous company identity matches (auditable)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pipeline.company_resolution.audit import write_identity_audit
from pipeline.company_resolution.merge import (
    add_alias,
    ensure_role,
    link_permit,
    link_project,
)
from pipeline.company_resolution.models import CompanyInput
from pipeline.company_resolution.merge import create_company


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _link_source(conn: sqlite3.Connection, source_type: str, source_id: str,
                 company_id: int) -> None:
    """Link the queued source permit/project to a company (contractor role)."""
    if source_type == "permit":
        permit_id = int(source_id)
        link_permit(conn, permit_id, company_id)
        pr = conn.execute("SELECT id FROM projects WHERE permit_id=?", (permit_id,)).fetchone()
        if pr:
            link_project(conn, pr["id"], company_id, "contractor")
    elif source_type == "project":
        link_project(conn, int(source_id), company_id, "contractor")


def approve_match(conn: sqlite3.Connection, queue_id: int, *,
                  reviewed_by: str = "reviewer", notes: str | None = None) -> dict:
    row = conn.execute(
        "SELECT * FROM company_match_review_queue WHERE id=?", (queue_id,)
    ).fetchone()
    if row is None:
        return {"ok": False, "error": "not found"}
    if row["lifecycle_state"] != "pending":
        return {"ok": False, "error": f"already {row['lifecycle_state']}"}

    company_id = row["candidate_company_id"]
    if company_id is None:
        # No candidate: approving means "create a new company for this name".
        company_id = create_company(
            conn,
            CompanyInput(name=row["proposed_company_name"] or "UNKNOWN",
                         source_system="match_review"),
        )
    else:
        add_alias(conn, company_id, row["proposed_company_name"] or "", source="match_review")
        ensure_role(conn, company_id, "contractor", source="match_review")

    _link_source(conn, row["source_record_type"], row["source_record_id"], company_id)
    conn.execute(
        "UPDATE company_match_review_queue SET lifecycle_state='approved', "
        "reviewed_by=?, reviewed_at=?, review_notes=?, updated_at=? WHERE id=?",
        (reviewed_by, _now(), notes, _now(), queue_id),
    )
    write_identity_audit(
        conn,
        action="match_approved",
        target_company_id=company_id,
        source_record_type=row["source_record_type"],
        source_record_id=row["source_record_id"],
        reason=notes,
        confidence=row["match_confidence"],
        performed_by=reviewed_by,
    )
    conn.commit()
    return {"ok": True, "company_id": company_id}


def reject_match(conn: sqlite3.Connection, queue_id: int, *,
                 reviewed_by: str = "reviewer", notes: str | None = None,
                 create_separate: bool = False) -> dict:
    row = conn.execute(
        "SELECT * FROM company_match_review_queue WHERE id=?", (queue_id,)
    ).fetchone()
    if row is None:
        return {"ok": False, "error": "not found"}
    if row["lifecycle_state"] != "pending":
        return {"ok": False, "error": f"already {row['lifecycle_state']}"}

    new_company_id = None
    if create_separate and row["proposed_company_name"]:
        new_company_id = create_company(
            conn,
            CompanyInput(name=row["proposed_company_name"], source_system="match_review"),
        )
        _link_source(conn, row["source_record_type"], row["source_record_id"], new_company_id)

    conn.execute(
        "UPDATE company_match_review_queue SET lifecycle_state='rejected', "
        "reviewed_by=?, reviewed_at=?, review_notes=?, updated_at=? WHERE id=?",
        (reviewed_by, _now(), notes, _now(), queue_id),
    )
    write_identity_audit(
        conn,
        action="match_rejected",
        target_company_id=new_company_id,
        source_record_type=row["source_record_type"],
        source_record_id=row["source_record_id"],
        reason=notes,
        confidence=row["match_confidence"],
        performed_by=reviewed_by,
    )
    conn.commit()
    return {"ok": True, "company_id": new_company_id}
