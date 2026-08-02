"""company_activity — canonical company timeline from permit/lifecycle records.

Deterministic dedupe_key makes reruns idempotent (INSERT OR IGNORE): the same
permit event for the same company is never duplicated.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

_ROLE_COLUMNS = (
    "contractor_company_id",
    "owner_company_id",
    "developer_company_id",
    "architect_company_id",
    "engineer_company_id",
)

_DATE_EVENTS = (
    ("filed_date", "permit_submitted", "Permit submitted"),
    ("issued_date", "permit_issued", "Permit issued"),
    ("finaled_date", "permit_finaled", "Permit finaled"),
)


def rebuild_company_timeline(conn: sqlite3.Connection, batch: int = 5000) -> int:
    """Populate company_activity from linked permits. Idempotent. Returns inserts."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    union = " UNION ALL ".join(
        f"SELECT pr.{col} AS company_id, p.id AS permit_id, pr.id AS project_id, "
        f"p.permit_number AS permit_number, p.filed_date, p.issued_date, "
        f"p.finaled_date, pr.project_lifecycle "
        f"FROM projects pr JOIN permits p ON p.id = pr.permit_id "
        f"WHERE pr.{col} IS NOT NULL"
        for col in _ROLE_COLUMNS
    )

    rows_buffer: list[tuple] = []
    inserted = 0
    seen_discovered: set[int] = set()

    def flush() -> int:
        if not rows_buffer:
            return 0
        cur = conn.executemany(
            """
            INSERT OR IGNORE INTO company_activity
                (company_id, activity_type, activity_date, project_id, permit_id,
                 title, description, source, dedupe_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'permits', ?, ?)
            """,
            rows_buffer,
        )
        conn.commit()
        n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        rows_buffer.clear()
        return n

    for r in conn.execute(union):
        cid = r["company_id"]
        permit_id = r["permit_id"]
        project_id = r["project_id"]
        permit_number = r["permit_number"] or permit_id

        if cid not in seen_discovered:
            seen_discovered.add(cid)
            rows_buffer.append(
                (cid, "company_discovered", None, None, None,
                 "Company discovered", "First linked from permit data",
                 f"discovered:{cid}", now)
            )

        for date_col, activity_type, label in _DATE_EVENTS:
            value = r[date_col]
            if value and str(value).strip():
                rows_buffer.append(
                    (cid, activity_type, str(value)[:10], project_id, permit_id,
                     f"{label} — {permit_number}", None,
                     f"{activity_type}:{permit_id}:{cid}", now)
                )

        if len(rows_buffer) >= batch:
            inserted += flush()

    inserted += flush()
    return inserted


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    n = rebuild_company_timeline(connection)
    print(f"Inserted {n} new company_activity rows.")
    connection.close()
