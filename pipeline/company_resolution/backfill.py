"""Idempotent, resumable backfill: link permits/projects to canonical companies.

- Reads existing contractor / owner text values (raw fields untouched).
- Resolves or creates canonical company records (confidence-based).
- Links projects + permits via nullable FKs. Ambiguous matches -> review queue.
- Never alters opportunity scores. Processes in chunks with a checkpoint so it
  is safe to interrupt and resume.

    python -m pipeline.company_resolution.backfill [--resume] [--chunk N] [--limit N]
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass

from pipeline.company_resolution.audit import enqueue_match_review
from pipeline.company_resolution.match import resolve
from pipeline.company_resolution.merge import (
    add_alias,
    create_company,
    enrich_company,
    ensure_role,
    link_permit,
    link_project,
)
from pipeline.company_resolution.models import (
    DECISION_MATCHED,
    DECISION_NEW,
    DECISION_POSSIBLE,
    CompanyInput,
)
from pipeline.company_resolution.normalize import is_placeholder_name, looks_like_company
from pipeline.config.settings import COMPANY_BACKFILL_CHUNK

_CHECKPOINT_KEY = "company_backfill_last_permit_id"


@dataclass
class BackfillStats:
    processed: int = 0
    linked: int = 0
    created: int = 0
    ambiguous: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict:
        return {
            "processed": self.processed,
            "linked": self.linked,
            "created": self.created,
            "ambiguous": self.ambiguous,
            "skipped": self.skipped,
            "failed": self.failed,
        }


def _get_checkpoint(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM knowledge_meta WHERE key=?", (_CHECKPOINT_KEY,)
    ).fetchone()
    return int(row["value"]) if row and row["value"] else 0


def _set_checkpoint(conn: sqlite3.Connection, permit_id: int) -> None:
    from datetime import datetime, timezone

    conn.execute(
        "INSERT INTO knowledge_meta (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (_CHECKPOINT_KEY, str(permit_id),
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def _resolve_and_link(
    conn: sqlite3.Connection,
    *,
    raw_name: str,
    role_type: str,
    license_number: str | None,
    city: str | None,
    state: str | None,
    permit_id: int | None,
    project_id: int | None,
    stats: BackfillStats,
) -> None:
    """Resolve one (name, role) sighting and link the permit/project."""
    if not raw_name or not raw_name.strip():
        return
    if is_placeholder_name(raw_name):
        stats.skipped += 1
        return
    data = CompanyInput(
        name=raw_name.strip(),
        role_type=role_type,
        license_number=license_number,
        city=city,
        state=state,
        source_system="permits",
    )
    result = resolve(conn, data)

    if result.decision == DECISION_POSSIBLE:
        enqueue_match_review(
            conn,
            source_record_type="permit" if permit_id else "project",
            source_record_id=str(permit_id or project_id),
            candidate_company_id=result.company_id,
            proposed_company_name=raw_name.strip(),
            normalized_name=data.name,
            match_confidence=result.confidence,
            match_reasons=result.reasons,
            conflicting_fields=result.conflicting_fields,
        )
        stats.ambiguous += 1
        return

    if result.decision == DECISION_NEW or result.company_id is None:
        company_id = create_company(conn, data)
        stats.created += 1
    else:  # DECISION_MATCHED
        company_id = result.company_id
        enrich_company(conn, company_id, data)
        add_alias(conn, company_id, raw_name.strip(), source="permits")
        ensure_role(conn, company_id, role_type, source="permits",
                    verification_status="verified" if role_type == "contractor" else None,
                    evidence_source="permit_explicit_field" if role_type == "contractor" else None)

    changed = False
    if project_id is not None:
        changed |= link_project(conn, project_id, company_id, role_type)
    if permit_id is not None and role_type == "contractor":
        changed |= link_permit(conn, permit_id, company_id)
    if changed:
        stats.linked += 1
    else:
        stats.skipped += 1


def backfill_companies(
    conn: sqlite3.Connection,
    *,
    chunk: int = COMPANY_BACKFILL_CHUNK,
    resume: bool = False,
    limit: int | None = None,
    verbose: bool = True,
) -> BackfillStats:
    stats = BackfillStats()
    last_id = _get_checkpoint(conn) if resume else 0
    if not resume:
        _set_checkpoint(conn, 0)

    total_seen = 0
    while True:
        rows = conn.execute(
            """
            SELECT p.id AS permit_id, p.city, p.state,
                   p.general_contractor_name, p.plumbing_contractor_name,
                   p.owner_name, p.contractor_license_number,
                   pr.id AS project_id
            FROM permits p
            LEFT JOIN projects pr ON pr.permit_id = p.id
            WHERE p.id > ?
            ORDER BY p.id
            LIMIT ?
            """,
            (last_id, chunk),
        ).fetchall()
        if not rows:
            break

        for row in rows:
            try:
                contractor = row["general_contractor_name"] or row["plumbing_contractor_name"]
                _resolve_and_link(
                    conn,
                    raw_name=contractor,
                    role_type="contractor",
                    license_number=row["contractor_license_number"],
                    city=row["city"],
                    state=row["state"],
                    permit_id=row["permit_id"],
                    project_id=row["project_id"],
                    stats=stats,
                )
                # Property owners: only when the value looks like a firm, never
                # invent a company for an individual homeowner.
                owner = row["owner_name"]
                if owner and looks_like_company(owner):
                    _resolve_and_link(
                        conn,
                        raw_name=owner,
                        role_type="property_owner",
                        license_number=None,
                        city=row["city"],
                        state=row["state"],
                        permit_id=None,
                        project_id=row["project_id"],
                        stats=stats,
                    )
                stats.processed += 1
            except Exception as exc:  # keep going; log failure count
                stats.failed += 1
                if verbose:
                    print(f"  ! permit {row['permit_id']} failed: {exc}")

            last_id = row["permit_id"]
            total_seen += 1
            if limit is not None and total_seen >= limit:
                break

        _set_checkpoint(conn, last_id)
        conn.commit()
        if verbose:
            print(f"  ... processed={stats.processed} linked={stats.linked} "
                  f"created={stats.created} ambiguous={stats.ambiguous} "
                  f"(checkpoint permit_id={last_id})")
        if limit is not None and total_seen >= limit:
            break

    if verbose:
        print(f"Backfill complete: {stats.as_dict()}")
    return stats


def main() -> None:
    from pipeline.db.database import init_db

    parser = argparse.ArgumentParser(description="Backfill companies from permits")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from the last checkpoint")
    parser.add_argument("--chunk", type=int, default=COMPANY_BACKFILL_CHUNK)
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N permits (for testing)")
    args = parser.parse_args()

    conn = init_db()
    try:
        backfill_companies(conn, chunk=args.chunk, resume=args.resume, limit=args.limit)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
