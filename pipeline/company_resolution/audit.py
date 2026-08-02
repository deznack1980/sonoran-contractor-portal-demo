"""Auditing + review-queue helpers for company identity decisions."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_identity_audit(
    conn: sqlite3.Connection,
    *,
    action: str,
    target_company_id: int | None = None,
    source_company_id: int | None = None,
    source_record_type: str | None = None,
    source_record_id: str | None = None,
    previous_values: dict | None = None,
    new_values: dict | None = None,
    reason: str | None = None,
    confidence: float | None = None,
    performed_by: str = "system",
) -> None:
    """Append an immutable identity-decision record."""
    conn.execute(
        """
        INSERT INTO company_identity_audit_log
            (action, source_company_id, target_company_id, source_record_type,
             source_record_id, previous_values_json, new_values_json, reason,
             confidence, performed_by, performed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action,
            source_company_id,
            target_company_id,
            source_record_type,
            source_record_id,
            json.dumps(previous_values) if previous_values is not None else None,
            json.dumps(new_values) if new_values is not None else None,
            reason,
            confidence,
            performed_by,
            _now(),
        ),
    )


def enqueue_match_review(
    conn: sqlite3.Connection,
    *,
    source_record_type: str,
    source_record_id: str,
    candidate_company_id: int | None,
    proposed_company_name: str,
    normalized_name: str,
    match_confidence: float,
    match_reasons: list[str],
    conflicting_fields: list[str],
) -> int:
    """Insert (or refresh) a pending ambiguous-match review row. Idempotent."""
    now = _now()
    conn.execute(
        """
        INSERT INTO company_match_review_queue
            (source_record_type, source_record_id, candidate_company_id,
             proposed_company_name, normalized_name, match_confidence,
             match_reasons, conflicting_fields, lifecycle_state,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        ON CONFLICT(source_record_type, source_record_id, candidate_company_id)
        DO UPDATE SET
            match_confidence=excluded.match_confidence,
            match_reasons=excluded.match_reasons,
            conflicting_fields=excluded.conflicting_fields,
            proposed_company_name=excluded.proposed_company_name,
            updated_at=excluded.updated_at
        WHERE company_match_review_queue.lifecycle_state = 'pending'
        """,
        (
            source_record_type,
            source_record_id,
            candidate_company_id,
            proposed_company_name,
            normalized_name,
            match_confidence,
            json.dumps(match_reasons),
            json.dumps(conflicting_fields),
            now,
            now,
        ),
    )
    row = conn.execute(
        """
        SELECT id FROM company_match_review_queue
        WHERE source_record_type=? AND source_record_id=?
          AND ((candidate_company_id IS NULL AND ? IS NULL)
               OR candidate_company_id=?)
        """,
        (source_record_type, source_record_id, candidate_company_id, candidate_company_id),
    ).fetchone()
    return row["id"] if row else 0
