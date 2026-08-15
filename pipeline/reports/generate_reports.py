"""Buyer-ready markdown / Excel / PDF briefs from real ingested permit data.

Ranked permit reports include every permit at or above
``REPORT_MIN_OPPORTUNITY_SCORE`` — there is no quantity cap.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from pipeline.config.settings import (
    REPORT_MIN_OPPORTUNITY_SCORE,
    REPORTS_GENERATED_DIR,
)
from pipeline.reports.report_presentation import (
    daily_summary_block,
    format_permit_card,
    product_opportunity_summary,
)
from pipeline.reports.report_templates import (
    actions_block,
    contractor_coverage_note,
    contractor_display,
    has_named_party,
    money,
    pct,
    report_header,
    scoring_footnote,
)

# Shared SELECT for ranked permit intelligence cards / Excel / PDF.
_RANKED_PERMIT_SELECT = """
    SELECT p.jurisdiction, p.permit_number, p.permit_type, p.status,
           p.city, p.job_address, p.issued_date, p.filed_date, p.finaled_date,
           p.general_contractor_name, p.plumbing_contractor_name, p.owner_name,
           p.description, p.project_description, p.valuation, p.square_footage,
           pr.project_category, pr.opportunity_score, pr.confidence_score,
           pr.estimated_material_value, pr.estimated_plumbing_scope,
           pr.construction_stage, pr.project_lifecycle, pr.opportunity_date,
           pr.opportunity_date_basis, pr.opportunity_timing
    FROM projects pr JOIN permits p ON p.id = pr.permit_id
"""


def _write_report(filename: str, content: str) -> str:
    REPORTS_GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_GENERATED_DIR / filename
    path.write_text(content, encoding="utf-8")
    return str(path)


def _fetch_ranked_permits(
    conn: sqlite3.Connection,
    *,
    issued_since: str | None = None,
) -> list:
    """All permits at/above threshold, highest score first (no row limit)."""
    where = ["pr.opportunity_score >= ?"]
    params: list = [REPORT_MIN_OPPORTUNITY_SCORE]
    if issued_since:
        where.append("p.issued_date >= ?")
        params.append(issued_since)
    sql = (
        _RANKED_PERMIT_SELECT
        + " WHERE "
        + " AND ".join(where)
        + " ORDER BY pr.opportunity_score DESC, p.issued_date DESC"
    )
    return conn.execute(sql, params).fetchall()


REPORT_INDEX_TYPES = [
    {
        "prefix": "daily_arizona_plumbing_intelligence_report_",
        "type": "qualifying",
        "label": "Qualifying permits (threshold+)",
        "blurb": "Last-7-day priority outreach list — every permit at/above the score threshold.",
    },
    {
        "prefix": "highest_opportunity_projects_",
        "type": "highest",
        "label": "Highest opportunity projects",
        "blurb": "Cross-market list of every permit clearing the opportunity bar.",
    },
    {
        "prefix": "largest_commercial_projects_",
        "type": "commercial",
        "label": "Largest commercial projects",
        "blurb": "Biggest commercial valuations for account pursuit.",
    },
    {
        "prefix": "weekly_contractor_growth_report_",
        "type": "growth",
        "label": "Weekly contractor growth",
        "blurb": "Accounts whose permit activity is accelerating.",
    },
    {
        "prefix": "daily_summary_",
        "type": "summary",
        "label": "Daily summary",
        "blurb": "Quick pipeline snapshot from the last run.",
    },
    {
        "prefix": "permit_intelligence_ranked_",
        "type": "excel",
        "label": "Ranked permits (Excel)",
        "blurb": "Formatted spreadsheet of every threshold-qualifying permit.",
    },
    {
        "prefix": "permit_intelligence_report_",
        "type": "pdf",
        "label": "Permit intelligence (PDF)",
        "blurb": "Sales-meeting PDF with summary, ranked list, and product stats.",
    },
    {
        "prefix": "lifecycle_data_audit_",
        "type": "lifecycle_audit",
        "label": "Lifecycle data audit",
        "blurb": "Which lifecycle dates each jurisdiction actually provides.",
    },
    {
        "prefix": "lifecycle_lead_time_validation_",
        "type": "lifecycle_leadtime",
        "label": "Lifecycle lead-time validation",
        "blurb": "How many days earlier submitted-permit tracking finds projects.",
    },
]


def write_reports_index() -> str:
    """Machine-readable catalog so the Reports page does not depend on directory listing."""
    import json

    REPORTS_GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    patterns = ("*.md", "*.xlsx", "*.pdf")
    paths = []
    for pattern in patterns:
        paths.extend(REPORTS_GENERATED_DIR.glob(pattern))
    for path in sorted(paths, key=lambda p: p.name, reverse=True):
        meta = next((m for m in REPORT_INDEX_TYPES if path.name.startswith(m["prefix"])), None)
        entries.append(
            {
                "file": path.name,
                "type": meta["type"] if meta else "other",
                "label": meta["label"] if meta else path.stem.replace("_", " "),
                "blurb": meta["blurb"] if meta else "Generated permit brief",
            }
        )

    latest: dict[str, str] = {}
    for entry in entries:
        latest.setdefault(entry["type"], entry["file"])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest": latest,
        "reports": entries,
    }
    index_path = REPORTS_GENERATED_DIR / "index.json"
    index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(index_path)


def _snapshot_table(rows: list[tuple[str, str]]) -> str:
    lines = ["\n## At a glance\n", "| Metric | Value |", "|---|---|"]
    for label, value in rows:
        lines.append(f"| {label} | {value} |")
    lines.append("")
    return "\n".join(lines)


def _append_permit_cards(lines: list[str], rows: list) -> None:
    lines.append("\n## Ranked Permit List\n")
    lines.append(
        f"Every permit at or above **{REPORT_MIN_OPPORTUNITY_SCORE}**, "
        "sorted highest score to lowest. No quantity cap.\n"
    )
    for i, row in enumerate(rows, start=1):
        lines.append(format_permit_card(i, row))


def daily_arizona_plumbing_intelligence_report(conn: sqlite3.Connection) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = _fetch_ranked_permits(conn, issued_since=cutoff)

    top_value = max((r["estimated_material_value"] or 0) for r in rows) if rows else 0
    cities = sorted({(r["city"] or "").title() for r in rows if r["city"]})
    named = sum(1 for r in rows if has_named_party(r))
    with_lead = sum(1 for r in rows if contractor_display(r) != "Not in source")

    lines = [
        report_header(
            "Daily Arizona Plumbing Intelligence",
            conn,
            tagline="Your 7-day priority outreach list — ranked by buying urgency.",
        )
    ]
    lines.append(
        daily_summary_block(conn, rows, window_label="Last 7 days (issued date)")
    )
    lines.append(
        _snapshot_table(
            [
                ("Permits in this brief", str(len(rows))),
                ("With named contractor / firm", f"{named} of {len(rows)}"),
                ("With any outreach lead (incl. project label)", f"{with_lead} of {len(rows)}"),
                ("Score threshold", f"{REPORT_MIN_OPPORTUNITY_SCORE}+ (no quantity cap)"),
                ("Window", "Last 7 days"),
                ("Largest estimated material ticket", money(top_value) if rows else "—"),
                ("Cities represented", ", ".join(cities) if cities else "—"),
            ]
        )
    )
    lines.append(
        "\n## Why this brief matters\n\n"
        "These permits were issued in the last week and already clear CorridorIQ’s "
        f"{REPORT_MIN_OPPORTUNITY_SCORE}+ opportunity bar. Every qualifying permit is "
        "listed — sorted highest score to lowest — so sales never misses a live ticket.\n\n"
        "Click any **permit number** for a polished job brief (scope, score, materials, next actions).\n"
    )

    if rows:
        _append_permit_cards(lines, rows)
        lines.append(product_opportunity_summary(rows))
        lines.append(contractor_coverage_note())
    else:
        lines.append(
            "\n## Ranked Permit List\n\nNo permits in the last 7 days met the "
            f"{REPORT_MIN_OPPORTUNITY_SCORE}+ threshold in connected markets.\n"
        )

    lines.append(
        actions_block(
            [
                "Call or email Platinum / Gold accounts first — lead with product availability and quote speed.",
                "Match each card’s likely product categories to your stocked SKUs before the conversation.",
                "Ask who is buying materials (PM vs. purchasing) and offer delivery / will-call options.",
                "For Project · or blank lead rows, use the permit number + address in the city portal "
                "(Buckeye EnerGov CSS / Tempe Accela / Gilbert Energov) to pull the licensed filer.",
            ]
        )
    )
    lines.append(scoring_footnote())

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _write_report(f"daily_arizona_plumbing_intelligence_report_{date_str}.md", "\n".join(lines))


def weekly_contractor_growth_report(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT name, permit_count, commercial_pct, avg_project_value, opportunity_rating,
               last_permit_date, cities_worked
        FROM contractors
        WHERE growth_trend = 'increasing'
        ORDER BY opportunity_rating DESC
        """
    ).fetchall()

    lines = [
        report_header(
            "Weekly Contractor Growth Brief",
            conn,
            tagline="Contractors whose permit volume is accelerating — lock the account early.",
        )
    ]
    lines.append(
        _snapshot_table(
            [
                ("Growing contractors surfaced", str(len(rows))),
                ("Trend window", "Last 90 days vs prior 90 days"),
                (
                    "Highest rated account",
                    f"{rows[0]['name']} ({rows[0]['opportunity_rating']:.0f})" if rows else "—",
                ),
            ]
        )
    )
    lines.append(
        "\n## Why this brief matters\n\n"
        "An increasing permit trend is a proxy for rising material demand. "
        "These accounts are expanding faster than their recent baseline — ideal targets for "
        "volume pricing, job-site delivery, and preferred-supplier conversations.\n"
    )

    if rows:
        lines.append("\n## Growth accounts to win\n")
        lines.append(
            "| Rank | Rating | Contractor | Permits | Commercial mix | Avg project $ | Markets |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for i, r in enumerate(rows, start=1):
            lines.append(
                f"| {i} | **{r['opportunity_rating']:.0f}** | {r['name']} | {r['permit_count']} | "
                f"{pct(r['commercial_pct'])} | {money(r['avg_project_value'])} | "
                f"{r['cities_worked'] or '—'} |"
            )
    else:
        lines.append("\n## Growth accounts to win\n\nNo contractors currently show an increasing trend.\n")

    lines.append(
        actions_block(
            [
                "Prioritize the highest-rated names with commercial mix above 40%.",
                "Offer a volume or will-call intro quote tied to their last active city.",
                "Ask which jobs are bidding this month and which trades they still need sourced.",
            ]
        )
    )
    lines.append(scoring_footnote())

    iso_year, iso_week, _ = datetime.now(timezone.utc).isocalendar()
    return _write_report(
        f"weekly_contractor_growth_report_{iso_year}-W{iso_week:02d}.md",
        "\n".join(lines),
    )


def largest_commercial_projects_report(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT p.permit_number, p.city, p.valuation,
               p.general_contractor_name, p.plumbing_contractor_name, p.owner_name,
               p.project_description,
               pr.project_category, pr.opportunity_score, pr.estimated_material_value
        FROM projects pr JOIN permits p ON p.id = pr.permit_id
        WHERE pr.project_category NOT IN ('Residential', 'Apartment', 'Other')
        ORDER BY
          CASE WHEN COALESCE(
                NULLIF(trim(p.general_contractor_name), ''),
                NULLIF(trim(p.plumbing_contractor_name), ''),
                NULLIF(trim(p.owner_name), '')
              ) IS NOT NULL THEN 0 ELSE 1 END,
          p.valuation DESC
        """
    ).fetchall()

    top_val = rows[0]["valuation"] if rows else None
    named = sum(1 for r in rows if has_named_party(r))
    lines = [
        report_header(
            "Largest Commercial Projects Brief",
            conn,
            tagline="Big tickets first — commercial scopes with real valuation signal.",
        )
    ]
    lines.append(
        _snapshot_table(
            [
                ("Projects listed", str(len(rows))),
                ("With contractor / applicant name", f"{named} of {len(rows)}"),
                ("Largest permit valuation", money(top_val)),
                ("Focus", "Non-residential (excludes Residential / Apartment / Other)"),
            ]
        )
    )
    lines.append(
        "\n## Why this brief matters\n\n"
        "High permit valuations often correlate with multi-trade buyouts and staged material releases. "
        "Use this list to staff pursuit teams and protect share on the largest live jobs.\n"
    )

    if rows:
        lines.append("\n## Largest commercial scopes\n")
        lines.append(
            "| Rank | Valuation | Permit | City | Category | Contractor / applicant | Opp. score | Est. material $ |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(rows, start=1):
            lines.append(
                f"| {i} | **{money(r['valuation'])}** | {r['permit_number']} | {r['city'] or '—'} | "
                f"{r['project_category'] or '—'} | {contractor_display(r)} | "
                f"{r['opportunity_score']:.0f} | {money(r['estimated_material_value'])} |"
            )
        lines.append(contractor_coverage_note())
    else:
        lines.append(
            "\n## Largest commercial scopes\n\nNo commercial projects found in connected markets yet.\n"
        )

    lines.append(
        actions_block(
            [
                "Assign an owner to each of the top 5 valuations this week.",
                "Confirm GC / plumbing sub contacts and whether materials are bid or buy-direct.",
                "Package freight + will-call options for multi-phase commercial releases.",
            ]
        )
    )
    lines.append(scoring_footnote())

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _write_report(f"largest_commercial_projects_{date_str}.md", "\n".join(lines))


def highest_opportunity_projects_report(conn: sqlite3.Connection) -> str:
    rows = _fetch_ranked_permits(conn)

    lines = [
        report_header(
            "Highest Opportunity Projects Brief",
            conn,
            tagline="Cross-market list — every CorridorIQ score at/above threshold.",
        )
    ]
    lines.append(daily_summary_block(conn, rows, window_label="All dates (score threshold)"))
    named = sum(1 for r in rows if has_named_party(r))
    lines.append(
        _snapshot_table(
            [
                ("Projects listed", str(len(rows))),
                ("With named contractor / firm", f"{named} of {len(rows)}"),
                ("Minimum score", f"{REPORT_MIN_OPPORTUNITY_SCORE}+ (no quantity cap)"),
                (
                    "Top score",
                    f"{rows[0]['opportunity_score']:.0f}" if rows else "—",
                ),
            ]
        )
    )
    lines.append(
        "\n## Why this brief matters\n\n"
        "This is the all-jurisdiction highlight reel: every job that clears the opportunity bar, "
        "sorted highest to lowest. Use it for Monday pipeline meetings and manager-level pursuit priorities.\n"
    )

    if rows:
        _append_permit_cards(lines, rows)
        lines.append(product_opportunity_summary(rows))
        lines.append(contractor_coverage_note())
    else:
        lines.append(
            "\n## Ranked Permit List\n\nNo projects currently meet the score threshold.\n"
        )

    lines.append(
        actions_block(
            [
                "Work Platinum / Gold scores first — those are the shortest competitive windows.",
                "Use likely product categories to pre-build a quote skeleton before you dial.",
                "Share this brief with outside sales so every rep starts from the same priority list.",
            ]
        )
    )
    lines.append(scoring_footnote())

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _write_report(f"highest_opportunity_projects_{date_str}.md", "\n".join(lines))


def generate_excel_and_pdf_reports(conn: sqlite3.Connection) -> list[str]:
    """Formatted Excel + PDF for the full threshold-qualified ranked list."""
    # Keep spreadsheet/PDF dependencies optional for installations that only
    # run the core CRM, contractor rebuild, or Markdown reports.
    from pipeline.reports.excel_export import write_ranked_permits_excel
    from pipeline.reports.pdf_export import write_permit_intelligence_pdf

    rows = _fetch_ranked_permits(conn)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    excel_path = write_ranked_permits_excel(
        rows,
        filename=f"permit_intelligence_ranked_{date_str}.xlsx",
    )
    pdf_path = write_permit_intelligence_pdf(
        conn,
        rows,
        filename=f"permit_intelligence_report_{date_str}.pdf",
        window_label="All dates — score threshold and above",
    )
    return [excel_path, pdf_path]


def generate_all_reports(conn: sqlite3.Connection) -> list:
    from pipeline.export.export_json import export_job_briefs
    from pipeline.reports.lifecycle_data_audit import generate_lifecycle_data_audit
    from pipeline.reports.lifecycle_lead_time_validation import (
        generate_lifecycle_lead_time_validation,
    )

    paths = [
        daily_arizona_plumbing_intelligence_report(conn),
        weekly_contractor_growth_report(conn),
        largest_commercial_projects_report(conn),
        highest_opportunity_projects_report(conn),
    ]
    paths.extend(generate_excel_and_pdf_reports(conn))
    paths.extend(generate_lifecycle_data_audit(conn))
    paths.extend(generate_lifecycle_lead_time_validation(conn))
    export_job_briefs(conn)
    index_path = write_reports_index()
    paths.append(index_path)
    return paths


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    paths = generate_all_reports(connection)
    for p in paths:
        print(f"Generated: {p}")
    connection.close()
