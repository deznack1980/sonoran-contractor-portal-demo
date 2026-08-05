"""Export every CorridorIQ company missing verified contact information.

Creates:
- one complete master CSV for tracking/import reconciliation
- numbered Perplexity-ready CSV batches (default: 100 companies each)
- a research instruction file defining evidence and non-fabrication rules

Usage:
    python scripts/export_contact_enrichment_queue.py
    python scripts/export_contact_enrichment_queue.py --batch-size 50
    python scripts/export_contact_enrichment_queue.py --output-dir C:\\path\\to\\folder
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.db.database import get_connection  # noqa: E402


OUTPUT_COLUMNS = [
    "company_id",
    "company_name",
    "legal_name",
    "license_number",
    "city",
    "state",
    "company_type",
    "priority_tier",
    "priority_score",
    "total_projects",
    "active_projects",
    "latest_activity_date",
    "permit_jurisdictions",
    "known_aliases",
    "research_phone",
    "research_email",
    "research_website",
    "research_address",
    "primary_contact_name",
    "primary_contact_title",
    "primary_contact_phone",
    "primary_contact_email",
    "other_useful_information",
    "source_urls",
    "research_confidence",
    "research_status",
    "researched_at",
]


RESEARCH_COLUMNS = {
    "research_phone": "",
    "research_email": "",
    "research_website": "",
    "research_address": "",
    "primary_contact_name": "",
    "primary_contact_title": "",
    "primary_contact_phone": "",
    "primary_contact_email": "",
    "other_useful_information": "",
    "source_urls": "",
    "research_confidence": "",
    "research_status": "",
    "researched_at": "",
}


INSTRUCTIONS = """# CorridorIQ Contact Enrichment — Perplexity Instructions

## Objective
Research the companies in the attached CSV and return the same CSV with verified contact
information. Preserve every row and every identity field exactly as supplied.

## Required research
For every company, look for:
- primary business phone
- public business email
- official website
- complete business address
- owner, president, purchasing manager, estimator, project manager, or other useful decision-maker
- that person's public business phone and email when verifiable
- contractor specialty, service area, license status, or other commercially useful information

## Identity controls
- Use company_id only as an immutable CorridorIQ identifier; never change it.
- Match the company using its name, legal name, license number, city, state, aliases, and permit
  jurisdictions together.
- Do not merge similarly named companies.
- If identity is ambiguous, set research_status to Ambiguous and explain why.
- Never guess or manufacture phone numbers, email addresses, people, titles, or websites.
- Do not infer an email pattern unless the exact email is publicly displayed by a reliable source.

## Evidence requirements
- Prefer official company websites, Arizona Registrar of Contractors records, municipal records,
  state corporate records, manufacturer/dealer directories, and established business profiles.
- Put every supporting URL in source_urls, separated by semicolons.
- A contact value without a supporting URL must not be entered.
- Use research_confidence: High, Medium, or Low.
- Use research_status: Found, Partial, Not Found, or Ambiguous.
- Use researched_at in YYYY-MM-DD format.

## Output rules
- Return CSV, not prose.
- Preserve the exact column order.
- Preserve all supplied rows, including Not Found and Ambiguous results.
- Do not alter company_id or any supplied identity/context column.
"""


def fetch_queue() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            c.id AS company_id,
            c.display_name AS company_name,
            COALESCE(c.legal_name, '') AS legal_name,
            COALESCE(c.license_number, '') AS license_number,
            COALESCE(c.city, '') AS city,
            COALESCE(c.state, '') AS state,
            COALESCE(c.company_type_primary, '') AS company_type,
            COALESCE(ci.company_priority_tier, 'Unscored') AS priority_tier,
            ROUND(COALESCE(ci.company_priority_score, 0), 1) AS priority_score,
            COALESCE(ci.total_projects, 0) AS total_projects,
            COALESCE(ci.active_projects, 0) AS active_projects,
            COALESCE(ci.latest_activity_date, '') AS latest_activity_date,
            COALESCE((
                SELECT group_concat(x.jurisdiction, ' | ')
                FROM (
                    SELECT DISTINCT p.jurisdiction
                    FROM projects pr
                    JOIN permits p ON p.id=pr.permit_id
                    WHERE pr.contractor_company_id=c.id
                    ORDER BY p.jurisdiction
                ) x
            ), '') AS permit_jurisdictions,
            COALESCE((
                SELECT group_concat(a.alias_name, ' | ')
                FROM company_aliases a
                WHERE a.company_id=c.id
            ), '') AS known_aliases
        FROM companies c
        LEFT JOIN company_intelligence ci ON ci.company_id=c.id
        WHERE c.lifecycle_state='active'
          AND TRIM(COALESCE(c.main_phone, ''))=''
          AND TRIM(COALESCE(c.main_email, ''))=''
          AND TRIM(COALESCE(c.website, ''))=''
          AND NOT EXISTS (
              SELECT 1
              FROM contacts ct
              WHERE ct.company_id=c.id
                AND (
                    TRIM(COALESCE(ct.phone, ''))<>''
                    OR TRIM(COALESCE(ct.mobile_phone, ''))<>''
                    OR TRIM(COALESCE(ct.email, ''))<>''
                )
          )
        ORDER BY
            COALESCE(ci.company_priority_score, 0) DESC,
            COALESCE(ci.active_projects, 0) DESC,
            COALESCE(ci.total_projects, 0) DESC,
            c.display_name
        """
    ).fetchall()
    conn.close()

    result = []
    for row in rows:
        item = dict(row)
        item.update(RESEARCH_COLUMNS)
        result.append(item)
    return result


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "exports" / "contact_enrichment",
    )
    args = parser.parse_args()

    if args.batch_size < 1 or args.batch_size > 1000:
        parser.error("--batch-size must be between 1 and 1000")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = fetch_queue()
    stamp = date.today().isoformat()
    master = output_dir / f"corridoriq_contact_enrichment_master_{stamp}.csv"
    write_csv(master, rows)

    for old_batch in output_dir.glob("perplexity_batch_*.csv"):
        old_batch.unlink()

    batch_count = 0
    for start in range(0, len(rows), args.batch_size):
        batch_count += 1
        batch = rows[start : start + args.batch_size]
        write_csv(output_dir / f"perplexity_batch_{batch_count:03d}.csv", batch)

    instructions = output_dir / "PERPLEXITY_RESEARCH_INSTRUCTIONS.md"
    instructions.write_text(INSTRUCTIONS, encoding="utf-8")

    print(f"Companies needing contact enrichment: {len(rows):,}")
    print(f"Master CSV: {master}")
    print(f"Perplexity batches: {batch_count} x up to {args.batch_size} companies")
    print(f"Instructions: {instructions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
