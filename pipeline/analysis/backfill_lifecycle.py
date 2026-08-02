"""Backfill Sprint 3 lifecycle columns on existing `projects` rows.

Computes project_lifecycle / opportunity_date / opportunity_date_basis /
opportunity_timing for every scored permit from its stored source dates and
status. Does NOT touch opportunity_score, weights, thresholds, or normalization
rules — it only fills the additive lifecycle fields.
"""

from __future__ import annotations

import sqlite3

from pipeline.analysis.scoring import (
    compute_opportunity_date,
    compute_opportunity_timing,
    compute_project_lifecycle,
)
from pipeline.knowledge.engine import KnowledgeEngine


def backfill_lifecycle(conn: sqlite3.Connection, batch: int = 5000) -> int:
    knowledge = KnowledgeEngine(conn)
    rows = conn.execute(
        """
        SELECT pr.id AS project_id, p.jurisdiction, p.status,
               p.filed_date, p.issued_date, p.finaled_date
        FROM projects pr JOIN permits p ON p.id = pr.permit_id
        """
    ).fetchall()

    updates = []
    for r in rows:
        lifecycle = compute_project_lifecycle(r["status"], r["jurisdiction"], knowledge)
        opp_date, basis = compute_opportunity_date(
            {
                "filed_date": r["filed_date"],
                "issued_date": r["issued_date"],
                "finaled_date": r["finaled_date"],
            }
        )
        timing = compute_opportunity_timing(lifecycle)
        updates.append((lifecycle, opp_date, basis, timing, r["project_id"]))

    n = 0
    for i in range(0, len(updates), batch):
        conn.executemany(
            """
            UPDATE projects
               SET project_lifecycle=?, opportunity_date=?,
                   opportunity_date_basis=?, opportunity_timing=?
             WHERE id=?
            """,
            updates[i : i + batch],
        )
        conn.commit()
        n += len(updates[i : i + batch])
    return n


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    count = backfill_lifecycle(connection)
    print(f"Backfilled lifecycle fields on {count:,} projects")
    dist = connection.execute(
        "SELECT project_lifecycle, COUNT(*) n FROM projects "
        "GROUP BY project_lifecycle ORDER BY n DESC"
    ).fetchall()
    for r in dist:
        print(f"  {r['project_lifecycle']}: {r['n']:,}")
    connection.close()
