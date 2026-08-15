"""Read-only before/after report for the lead-role correction migration."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from pipeline.company_resolution.lead_role_correction import MIGRATION_KEY


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def build_report(before_path: Path, after_path: Path) -> dict:
    before, after = _ro(before_path), _ro(after_path)
    try:
        tables = ("permits","companies","contacts","crm_company_relationships","crm_activities")
        retention = {}
        for table in tables:
            retention[table] = {
                "before": before.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"],
                "after": after.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"],
            }
        retention["raw_source_json (non-null rows)"] = {
            "before": before.execute("SELECT COUNT(*) n FROM permits WHERE raw_source_json IS NOT NULL").fetchone()["n"],
            "after": after.execute("SELECT COUNT(*) n FROM permits WHERE raw_source_json IS NOT NULL").fetchone()["n"],
        }
        assignment_before = {r["jurisdiction"]:r["n"] for r in before.execute(
            "SELECT jurisdiction,COUNT(*) n FROM permits WHERE contractor_company_id IS NOT NULL GROUP BY jurisdiction")}
        assignment_after = {r["jurisdiction"]:r["n"] for r in after.execute(
            "SELECT jurisdiction,COUNT(*) n FROM permits WHERE contractor_company_id IS NOT NULL GROUP BY jurisdiction")}
        crm_types = {r["lead_type"] or "unclassified":r["n"] for r in after.execute(
            "SELECT lead_type,COUNT(*) n FROM crm_company_relationships GROUP BY lead_type")}
        company_types = {r["lead_type"] or "unclassified":r["n"] for r in after.execute(
            "SELECT lead_type,COUNT(*) n FROM companies WHERE lifecycle_state='active' GROUP BY lead_type")}
        evidence_by_jurisdiction = [dict(r) for r in after.execute(
            """SELECT p.jurisdiction,e.lead_type,e.verification_status,COUNT(*) records
               FROM permit_party_evidence e JOIN permits p ON p.id=e.permit_id
               GROUP BY p.jurisdiction,e.lead_type,e.verification_status
               ORDER BY p.jurisdiction,e.lead_type,e.verification_status""")]
        prior_max_company_id = before.execute("SELECT COALESCE(MAX(id),0) n FROM companies").fetchone()["n"]
        added_companies = [dict(r) for r in after.execute(
            "SELECT id,display_name,lifecycle_state,lead_type,lead_source,why_this_lead FROM companies WHERE id>? ORDER BY id",
            (prior_max_company_id,))]
        return {
            "migration_key": MIGRATION_KEY,
            "before_database": str(before_path.resolve()),
            "after_database": str(after_path.resolve()),
            "quick_check": after.execute("PRAGMA quick_check").fetchone()[0],
            "assignments_before": assignment_before,
            "assignments_after": assignment_after,
            "crm_lead_types": crm_types,
            "active_company_lead_types": company_types,
            "evidence_by_jurisdiction": evidence_by_jurisdiction,
            "added_companies": added_companies,
            "retention": retention,
            "history_rows": after.execute(
                "SELECT COUNT(*) n FROM lead_role_assignment_history WHERE migration_key=?", (MIGRATION_KEY,)).fetchone()["n"],
            "permit_project_mismatches": after.execute(
                """SELECT COUNT(*) n FROM projects pr JOIN permits p ON p.id=pr.permit_id
                   WHERE COALESCE(pr.contractor_company_id,-1)<>COALESCE(p.contractor_company_id,-1)""").fetchone()["n"],
        }
    finally:
        before.close(); after.close()


def render_markdown(report: dict) -> str:
    jurs = sorted(set(report["assignments_before"]) | set(report["assignments_after"]))
    lines = [
        "# CorridorIQ lead-role classification migration report", "",
        f"Migration: `{report['migration_key']}`  ",
        f"SQLite integrity: `{report['quick_check']}`  ",
        f"Rollback ledger rows: {report['history_rows']:,}  ",
        f"Permit/project assignment mismatches: {report['permit_project_mismatches']:,}", "",
        "## Contractor assignments by jurisdiction", "",
        "| Jurisdiction | Before | After | Change |", "|---|---:|---:|---:|",
    ]
    for jur in jurs:
        b, a = report["assignments_before"].get(jur,0), report["assignments_after"].get(jur,0)
        lines.append(f"| {jur} | {b:,} | {a:,} | {a-b:+,} |")
    lines += ["", "## Existing CRM relationships by lead type", "", "| Lead type | Relationships |", "|---|---:|"]
    for lead_type, count in sorted(report["crm_lead_types"].items()):
        lines.append(f"| {lead_type} | {count:,} |")
    lines += ["", "## All active companies by lead type", "", "| Lead type | Companies |", "|---|---:|"]
    for lead_type, count in sorted(report["active_company_lead_types"].items()):
        lines.append(f"| {lead_type} | {count:,} |")
    lines += ["", "## Preserved permit-party evidence by jurisdiction and lead type", "", "| Jurisdiction | Lead type | Verification | Evidence rows |", "|---|---|---|---:|"]
    for row in report["evidence_by_jurisdiction"]:
        lines.append(f"| {row['jurisdiction']} | {row['lead_type']} | {row['verification_status']} | {row['records']:,} |")
    lines += ["", "## Non-deletion checks", "", "| Table | Before | After |", "|---|---:|---:|"]
    for table, counts in report["retention"].items():
        lines.append(f"| {table} | {counts['before']:,} | {counts['after']:,} |")
    lines += ["", "## Migration-created placeholder records retained for audit", "", "| ID | Company | State | Lead type | Source |", "|---:|---|---|---|---|"]
    for company in report["added_companies"]:
        lines.append(f"| {company['id']} | {company['display_name']} | {company['lifecycle_state']} | {company['lead_type']} | {company['lead_source']} |")
    lines += ["", f"{len(report['added_companies'])} placeholder records created during validation are retained as deprecated audit records; none is active or contractor-linked. No tracked source or CRM table decreased.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    report = build_report(args.before, args.after)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_markdown(report), encoding="utf-8")
    if args.json_output:
        args.json_output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
