"""Read-only audit for the verified contractor compatibility rebuild."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def contractor_audit_snapshot(conn: sqlite3.Connection) -> dict:
    """Measure current and expected contractor state without changing records."""
    raw_name = (
        "TRIM(COALESCE(NULLIF(p.plumbing_contractor_name,''),"
        "NULLIF(p.general_contractor_name,''),''))<>''"
    )
    verified = (
        "c.lifecycle_state='active' AND c.lead_type='verified_contractor' "
        "AND c.lead_verification_status='verified'"
    )
    contractor_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(contractors)")
    }
    has_company_id = "company_id" in contractor_columns

    def count(sql, params=()):
        return int(conn.execute(sql, params).fetchone()[0])

    excluded_rows = conn.execute(
        f"""
        SELECT COALESCE(c.lead_type,'unresolved_or_unlinked') AS classification,
               COUNT(*) AS permits
        FROM permits p
        LEFT JOIN companies c ON c.id=p.contractor_company_id
        WHERE {raw_name} AND NOT ({verified})
        GROUP BY COALESCE(c.lead_type,'unresolved_or_unlinked')
        ORDER BY permits DESC,classification
        """
    ).fetchall()
    jurisdiction_rows = conn.execute(
        f"""
        SELECT p.jurisdiction,COUNT(*) AS permits,COUNT(DISTINCT c.id) AS contractors
        FROM permits p JOIN companies c ON c.id=p.contractor_company_id
        WHERE {verified}
        GROUP BY p.jurisdiction ORDER BY permits DESC,p.jurisdiction
        """
    ).fetchall()
    foreign_key_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]

    return {
        "existing_contractor_count": count("SELECT COUNT(*) FROM contractors"),
        "existing_noncanonical_contractor_count": count(
            "SELECT COUNT(*) FROM contractors WHERE company_id IS NULL"
            if has_company_id else "SELECT COUNT(*) FROM contractors"
        ),
        "raw_candidate_permits": count(f"SELECT COUNT(*) FROM permits p WHERE {raw_name}"),
        "verified_contractor_count": count(
            f"SELECT COUNT(DISTINCT c.id) FROM companies c JOIN permits p "
            f"ON p.contractor_company_id=c.id WHERE {verified}"
        ),
        "verified_contractor_permits": count(
            f"SELECT COUNT(*) FROM permits p JOIN companies c "
            f"ON c.id=p.contractor_company_id WHERE {verified}"
        ),
        "excluded_permits_by_classification": {
            row["classification"]: int(row["permits"]) for row in excluded_rows
        },
        "projects_requiring_compatibility_relink": count(
            f"""SELECT COUNT(*) FROM projects pr
                JOIN companies c ON c.id=pr.contractor_company_id
                LEFT JOIN contractors ct ON ct.company_id=c.id
                WHERE {verified}
                  AND (ct.id IS NULL OR pr.contractor_id IS NULL OR pr.contractor_id<>ct.id)"""
            if has_company_id else
            f"""SELECT COUNT(*) FROM projects pr
                JOIN companies c ON c.id=pr.contractor_company_id WHERE {verified}"""
        ),
        "projects_left_unresolved": count(
            f"""SELECT COUNT(*) FROM projects pr JOIN permits p ON p.id=pr.permit_id
                LEFT JOIN companies c ON c.id=pr.contractor_company_id
                WHERE {raw_name} AND NOT ({verified})"""
        ),
        "verified_counts_by_jurisdiction": [
            {"jurisdiction": row["jurisdiction"], "permits": int(row["permits"]),
             "contractors": int(row["contractors"])}
            for row in jurisdiction_rows
        ],
        "database_quick_check": quick_check,
        "foreign_key_issue_count": len(foreign_key_issues),
    }


def compare_snapshots(before: dict, after: dict) -> dict:
    return {
        "before": before,
        "after": after,
        "contractor_count_change": (
            after["existing_contractor_count"] - before["existing_contractor_count"]
        ),
        "projects_relinked": max(
            0,
            before["projects_requiring_compatibility_relink"]
            - after["projects_requiring_compatibility_relink"],
        ),
        "verified_contractors_materialized": after["existing_contractor_count"],
        "remaining_unresolved_projects": after["projects_left_unresolved"],
        "database_integrity_ok": (
            after["database_quick_check"] == "ok"
            and after["foreign_key_issue_count"] == 0
        ),
    }


def render_markdown(snapshot: dict) -> str:
    data = snapshot.get("after", snapshot)
    lines = [
        "# CorridorIQ Verified Contractor Rebuild Audit",
        "",
        "This report is generated with read-only SQL. It does not modify database records.",
        "",
        "## Core counts",
        "",
        f"- Existing compatibility contractors: {data['existing_contractor_count']:,}",
        f"- Verified canonical contractors: {data['verified_contractor_count']:,}",
        f"- Verified contractor permits: {data['verified_contractor_permits']:,}",
        f"- Projects requiring compatibility relink: {data['projects_requiring_compatibility_relink']:,}",
        f"- Projects left unresolved: {data['projects_left_unresolved']:,}",
        "",
        "## Excluded permit candidates",
        "",
    ]
    excluded = data["excluded_permits_by_classification"]
    lines.extend(
        f"- {classification}: {count:,}"
        for classification, count in excluded.items()
    )
    if not excluded:
        lines.append("- None")
    lines.extend([
        "",
        "## Database integrity",
        "",
        f"- PRAGMA quick_check: {data['database_quick_check']}",
        f"- Foreign-key issues: {data['foreign_key_issue_count']}",
    ])
    if "projects_relinked" in snapshot:
        lines.extend([
            "",
            "## Before/after result",
            "",
            f"- Contractor count change: {snapshot['contractor_count_change']:+,}",
            f"- Projects relinked: {snapshot['projects_relinked']:,}",
            f"- Integrity passed: {'Yes' if snapshot['database_integrity_ok'] else 'No'}",
        ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only contractor rebuild audit")
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    parser.add_argument("--markdown", type=Path, help="Optional Markdown output path")
    parser.add_argument("--before", type=Path, help="Prior JSON snapshot to compare")
    args = parser.parse_args()

    from pipeline.config.settings import DB_PATH

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA query_only=ON")
    try:
        current = contractor_audit_snapshot(conn)
    finally:
        conn.close()
    result = compare_snapshots(json.loads(args.before.read_text()), current) if args.before else current
    payload = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(render_markdown(result), encoding="utf-8")
    if not args.output and not args.markdown:
        print(payload)


if __name__ == "__main__":
    main()
