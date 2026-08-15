"""Idempotently normalize permit-party identity fields from raw source JSON.

Only explicit contractor/builder fields populate general_contractor_name.
Applicants, responsible parties, professionals, owners, and project names are
preserved separately and never promoted to contractor evidence.
"""

from __future__ import annotations

import json
import sqlite3

from pipeline.company_resolution.lead_role_correction import extract_permit_evidence


def backfill_identity_fields(conn: sqlite3.Connection) -> dict[str, int]:
    updated: dict[str, int] = {}
    rows = conn.execute(
        "SELECT id,jurisdiction,raw_source_json FROM permits ORDER BY id"
    ).fetchall()
    with conn:
        for row in rows:
            try:
                raw = json.loads(row["raw_source_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                raw = {}
            explicit, _, separate = extract_permit_evidence(row["jurisdiction"], raw)
            cur = conn.execute(
                """UPDATE permits SET general_contractor_name=?,applicant_name=?,
                   applicant_organization=?,responsible_party_name=?,permit_professional_name=?,
                   contractor_source_role=?,contractor_source_field=?,
                   contractor_evidence_confidence=?,contractor_verification_status=?,
                   lead_type=?,why_this_lead=? WHERE id=?""",
                (explicit["name"] if explicit else None,separate["applicant_name"],
                 separate["applicant_organization"],separate["responsible_party_name"],
                 separate["permit_professional_name"],explicit["role"] if explicit else None,
                 explicit["field"] if explicit else None,explicit["confidence"] if explicit else None,
                 "verified" if explicit else "unverified",
                 "verified_contractor" if explicit else "unverified_permit_contact",
                 explicit["why"] if explicit else "Jurisdiction publishes no explicit contractor identity for this permit",
                 row["id"]),
            )
            updated[row["jurisdiction"]] = updated.get(row["jurisdiction"], 0) + cur.rowcount
    return updated
