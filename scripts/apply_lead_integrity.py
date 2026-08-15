"""Apply CorridorIQ Release 11 lead integrity to the production database.

Dry-run (read only):
    python scripts/apply_lead_integrity.py

Apply with an automatic SQLite backup:
    python scripts/apply_lead_integrity.py --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.company_resolution.lead_role_correction import (  # noqa: E402
    apply_lead_role_correction,
    migration_report,
)
from pipeline.company_resolution.metrics import compute_company_metrics  # noqa: E402
from pipeline.company_resolution.timeline import rebuild_company_timeline  # noqa: E402
from pipeline.config import settings  # noqa: E402
from pipeline.db.database import init_db  # noqa: E402


def _server_running() -> bool:
    try:
        with urlopen(
            f"http://127.0.0.1:{settings.SALES_API_PORT}/api/health",
            timeout=1,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("service") == "corridoriq-sales"
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


def _snapshot(conn: sqlite3.Connection) -> dict:
    lead_types = {
        row["lead_type"] or "unclassified": row["n"]
        for row in conn.execute(
            """SELECT lead_type, COUNT(*) AS n
               FROM crm_company_relationships
               GROUP BY lead_type ORDER BY n DESC"""
        )
    }
    return {
        "permits": conn.execute("SELECT COUNT(*) AS n FROM permits").fetchone()["n"],
        "projects": conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"],
        "permit_contractor_links": conn.execute(
            "SELECT COUNT(*) AS n FROM permits WHERE contractor_company_id IS NOT NULL"
        ).fetchone()["n"],
        "project_contractor_links": conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE contractor_company_id IS NOT NULL"
        ).fetchone()["n"],
        "classified_relationships": sum(
            count for lead_type, count in lead_types.items() if lead_type != "unclassified"
        ),
        "unclassified_relationships": lead_types.get("unclassified", 0),
        "lead_types": lead_types,
        "nonverified_project_links": conn.execute(
            """SELECT COUNT(*) AS n
               FROM projects pr
               JOIN companies c ON c.id=pr.contractor_company_id
               WHERE COALESCE(c.lead_type,'unverified_permit_contact')
                     <> 'verified_contractor'"""
        ).fetchone()["n"],
    }


def _backup_database(conn: sqlite3.Connection, stamp: str) -> Path:
    backup_dir = settings.DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"corridoriq.pre-lead-integrity.{stamp}.db"
    target = sqlite3.connect(destination)
    try:
        conn.backup(target)
        check = target.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"backup quick_check failed: {check}")
    finally:
        target.close()
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Back up and correct CorridorIQ contractor/permit lead roles."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create a backup and apply the correction. Without this flag the command is read-only.",
    )
    args = parser.parse_args()

    conn = init_db()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    try:
        before = _snapshot(conn)
        print("LEAD INTEGRITY PREFLIGHT")
        print(json.dumps(before, indent=2, sort_keys=True))

        if not args.apply:
            print("\nREAD-ONLY CHECK COMPLETE")
            print("Run again with --apply after closing CorridorIQ.")
            return 0

        if _server_running():
            print("\nSTOPPED: CorridorIQ is currently running.")
            print("Close the portal server or use ApplyCorridorIQLeadIntegrity.bat.")
            return 2

        backup_path = _backup_database(conn, stamp)
        print(f"\nBACKUP: {backup_path}")

        correction = apply_lead_role_correction(conn)
        metrics_written = compute_company_metrics(conn)
        timeline_rows = rebuild_company_timeline(conn, replace_permit_events=True)
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        report = migration_report(conn)
        after = _snapshot(conn)

        result = {
            "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "database": str(settings.DB_PATH),
            "backup": str(backup_path),
            "before": before,
            "after": after,
            "correction": correction,
            "migration_report": report,
            "metrics_written": metrics_written,
            "timeline_rows": timeline_rows,
            "quick_check": quick_check,
        }
        report_path = backup_path.with_suffix(".json")
        report_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

        violations = []
        if quick_check != "ok":
            violations.append(f"database quick_check={quick_check}")
        if report["permit_project_mismatches"]:
            violations.append(
                f"permit/project mismatches={report['permit_project_mismatches']}"
            )
        if after["nonverified_project_links"]:
            violations.append(
                f"nonverified project links={after['nonverified_project_links']}"
            )

        print("\nLEAD INTEGRITY RESULT")
        print(json.dumps(after, indent=2, sort_keys=True))
        print(f"REPORT: {report_path}")
        if violations:
            print("VALIDATION FAILED: " + "; ".join(violations))
            print("The pre-change database backup is available for recovery.")
            return 1

        print("VALIDATION PASSED")
        print("Project Records can now operate as a verified-contractor queue.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
