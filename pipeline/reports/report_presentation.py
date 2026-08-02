"""Executive-report presentation helpers (priority badges, cards, summaries).

These helpers do not change scoring — they format engine output for sales.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pipeline.analysis import constants as C
from pipeline.analysis.materials_rubric import predict_product_categories
from pipeline.analysis.scoring import (
    compute_opportunity_date,
    compute_opportunity_score_breakdown,
    compute_opportunity_timing,
    compute_project_lifecycle,
    days_since,
)
from pipeline.config.settings import REPORT_MIN_OPPORTUNITY_SCORE, REPORT_PRIORITY_BANDS
from pipeline.reports.report_templates import contractor_display, money, permit_link


def priority_badge(score: float | None) -> str:
    """Map an opportunity score to a configured priority band name."""
    if score is None:
        return "—"
    for band in REPORT_PRIORITY_BANDS:
        if band["min"] <= score <= band["max"]:
            return band["name"]
    if score > REPORT_PRIORITY_BANDS[0]["max"]:
        return REPORT_PRIORITY_BANDS[0]["name"]
    if score >= REPORT_MIN_OPPORTUNITY_SCORE:
        return REPORT_PRIORITY_BANDS[-1]["name"]
    return "Below Threshold"


def recommended_next_action(score: float | None, has_contractor: bool) -> str:
    """Short, actionable outreach suggestion by priority band."""
    badge = priority_badge(score)
    if badge == "Platinum":
        return (
            "Call today — lead with stocked SKUs and same-day quote. "
            "Ask for PM / purchasing contact before competitors engage."
        )
    if badge == "Gold":
        return (
            "Outreach within 24 hours. Pre-build a quote skeleton from "
            "likely product categories and confirm job-site delivery options."
        )
    if badge == "Silver":
        return (
            "Schedule outreach this week. Match scope to inventory and ask "
            "whether materials are bid or buy-direct."
        )
    if badge == "Bronze":
        if has_contractor:
            return (
                "Add to weekly call list. Introduce volume / will-call pricing "
                "tied to this job's city."
            )
        return (
            "Look up the licensed filer in the city portal using permit # + "
            "address, then introduce supply capability."
        )
    if badge == "Watch":
        return (
            "Monitor and light-touch email. Revisit if valuation or "
            "contractor identity improves."
        )
    return "No action — below configured score threshold."


def score_breakdown_lines(permit_row: dict, project_category: str) -> list[str]:
    """Sales-friendly score explanation lines from the scoring engine."""
    detail = compute_opportunity_score_breakdown(permit_row, project_category or "Other")
    lines: list[str] = []
    for factor in detail["factors"]:
        lines.append(f"{factor['label']}")
        lines.append(f"+{factor['points']:.1f}")
        lines.append("")
    for driver in detail["drivers"]:
        lines.append(f"Driver: {driver}")
    if detail["drivers"]:
        lines.append("")
    lines.append("Final Score")
    lines.append(f"{detail['final_score']:.0f}%")
    return lines


def reason_for_score(permit_row: dict, project_category: str) -> str:
    """One-paragraph reason built from weighted factors + keyword drivers."""
    detail = compute_opportunity_score_breakdown(permit_row, project_category or "Other")
    top = sorted(detail["factors"], key=lambda f: f["points"], reverse=True)[:2]
    parts = [f"{f['label']} (+{f['points']:.1f})" for f in top]
    text = "Driven by " + " and ".join(parts) + "."
    if detail["drivers"]:
        text += " Signals: " + ", ".join(detail["drivers"][:4]) + "."
    return text


def product_categories_for_row(row) -> list[str]:
    """Predict likely plumbing products for a DB/report row."""
    try:
        category = row["project_category"]
    except (KeyError, IndexError, TypeError):
        category = "Other"
    try:
        permit_type = row["permit_type"]
    except (KeyError, IndexError, TypeError):
        permit_type = None
    description = None
    for key in ("description", "project_description"):
        try:
            value = row[key]
        except (KeyError, IndexError, TypeError):
            continue
        if value:
            description = f"{description or ''} {value}".strip()
    # Prefer stored scope string when present, else re-predict.
    try:
        scope = row["estimated_plumbing_scope"]
    except (KeyError, IndexError, TypeError):
        scope = None
    predicted = predict_product_categories(category or "Other", permit_type, description)
    if predicted:
        return predicted
    if scope:
        return [part.strip() for part in str(scope).split(",") if part.strip()]
    return []


def daily_summary_block(
    conn: sqlite3.Connection,
    rows: list,
    *,
    window_label: str | None = None,
) -> str:
    """Executive KPI block shown at the top of ranked permit reports."""
    total_processed = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    scores = [float(r["opportunity_score"]) for r in rows if r["opportunity_score"] is not None]
    above = len(scores)
    highest = max(scores) if scores else None
    average = sum(scores) / len(scores) if scores else None
    total_opp = sum((r["estimated_material_value"] or 0) for r in rows)

    band_counts = {band["name"]: 0 for band in REPORT_PRIORITY_BANDS}
    for score in scores:
        badge = priority_badge(score)
        if badge in band_counts:
            band_counts[badge] += 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "\n## Daily Summary\n",
        f"**Date:** {today}",
    ]
    if window_label:
        lines.append(f"**Window:** {window_label}")
    lines.extend(
        [
            f"**Total Permits Processed:** {total_processed:,}",
            f"**Permits Above Threshold ({REPORT_MIN_OPPORTUNITY_SCORE}+):** {above:,}",
            f"**Highest Score:** {f'{highest:.0f}%' if highest is not None else '—'}",
            f"**Average Score:** {f'{average:.1f}%' if average is not None else '—'}",
            f"**Estimated Total Opportunity:** {money(total_opp) if rows else '—'}",
            "",
            "### Priority Breakdown",
            "",
        ]
    )
    for band in REPORT_PRIORITY_BANDS:
        label = f"{band['name']} ({band['min']}-{band['max']})"
        lines.append(f"- **{label}:** {band_counts[band['name']]}")
    lines.append("")
    return "\n".join(lines)


def _get(row, key: str, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def lifecycle_sales_action(stage: str | None) -> str:
    """Recommended sales action for a lifecycle stage (Phase 4)."""
    return C.LIFECYCLE_SALES_ACTION.get(stage, C.LIFECYCLE_SALES_ACTION_DEFAULT)


def lifecycle_fields_for_row(row) -> dict:
    """Resolve lifecycle stage / opportunity date / timing for a report row.

    Prefers values already stored on the ``projects`` row; falls back to
    computing them from raw permit fields so reports work even before a
    re-analysis has populated the new columns.
    """
    stage = _get(row, "project_lifecycle")
    municipality = _get(row, "jurisdiction")
    if not stage:
        stage = compute_project_lifecycle(_get(row, "status"), municipality)

    opp_date = _get(row, "opportunity_date")
    basis = _get(row, "opportunity_date_basis")
    if not opp_date:
        permit_row = {
            "filed_date": _get(row, "filed_date"),
            "issued_date": _get(row, "issued_date"),
            "finaled_date": _get(row, "finaled_date"),
        }
        opp_date, basis = compute_opportunity_date(permit_row)

    timing = _get(row, "opportunity_timing") or compute_opportunity_timing(stage)
    return {
        "lifecycle_stage": stage,
        "opportunity_date": opp_date,
        "opportunity_date_basis": basis or "Unknown",
        "opportunity_timing": timing,
        "days_since_opportunity": days_since(opp_date),
        "sales_action": lifecycle_sales_action(stage),
    }


def format_permit_card(rank: int, row, *, include_jurisdiction_link: bool = True) -> str:
    """One executive-style permit section for markdown sales briefs."""
    score = _get(row, "opportunity_score")
    badge = priority_badge(score)
    contractor = contractor_display(row)
    has_contractor = contractor not in ("Not in source",) and not str(contractor).startswith("Project ·")
    owner = str(_get(row, "owner_name") or "").strip()
    description = str(_get(row, "description") or _get(row, "project_description") or "").strip()
    if len(description) > 280:
        description = description[:277].rstrip() + "..."

    products = product_categories_for_row(row)
    products_text = ", ".join(products) if products else "See permit description"

    permit_row = {
        "permit_type": _get(row, "permit_type"),
        "status": _get(row, "status"),
        "issued_date": _get(row, "issued_date"),
        "filed_date": _get(row, "filed_date"),
        "valuation": _get(row, "valuation"),
        "square_footage": _get(row, "square_footage"),
        "description": description,
    }
    category = _get(row, "project_category") or "Other"
    breakdown = score_breakdown_lines(permit_row, category)
    reason = reason_for_score(permit_row, category)
    action = recommended_next_action(score, has_contractor)

    if include_jurisdiction_link and _get(row, "jurisdiction"):
        permit_display = permit_link(_get(row, "jurisdiction"), _get(row, "permit_number"))
    else:
        permit_display = _get(row, "permit_number") or "—"

    material_value = _get(row, "estimated_material_value")
    permit_type = _get(row, "permit_type")
    issued = _get(row, "issued_date")
    life = lifecycle_fields_for_row(row)
    days_txt = (
        f"{life['days_since_opportunity']} days ago"
        if life["days_since_opportunity"] is not None
        else "—"
    )
    opp_date_txt = (
        f"{life['opportunity_date']} ({life['opportunity_date_basis']})"
        if life["opportunity_date"]
        else "—"
    )

    lines = [
        "======================================================",
        "",
        f"### Rank #{rank}",
        "",
        f"**Overall Score:** {score:.0f}%" if score is not None else "**Overall Score:** —",
        f"**Priority Badge:** {badge}",
        f"**Lifecycle Stage:** {life['lifecycle_stage']}",
        f"**Opportunity Timing:** {life['opportunity_timing']}",
        f"**Opportunity Date:** {opp_date_txt}",
        f"**Days Since Opportunity:** {days_txt}",
        f"**Permit Number:** {permit_display}",
        f"**Permit Type:** {permit_type or '—'}",
        f"**Issue Date:** {issued or '—'}",
        f"**Current Status:** {_get(row, 'status') or '—'}",
        f"**Contractor Name:** {contractor}",
        f"**Property Owner:** {owner or '—'}",
        f"**Job Address:** {_get(row, 'job_address') or '—'}",
        f"**City:** {_get(row, 'city') or '—'}",
        f"**Description of Work:** {description or '—'}",
        f"**Estimated Material Opportunity:** {money(material_value)}",
        f"**Likely Product Categories:** {products_text}",
        f"**Recommended Sales Action:** {life['sales_action']}",
        "",
        "**Reason for Score**",
        "",
        reason,
        "",
        "**Score Breakdown**",
        "",
    ]
    for line in breakdown:
        lines.append(line if line else "")
    lines.extend(
        [
            "",
            f"**Recommended Next Action:** {action}",
            "",
            "======================================================",
            "",
        ]
    )
    return "\n".join(lines)


def product_opportunity_summary(rows: list) -> str:
    """Aggregate product-category hits across ranked permits."""
    counts: dict[str, int] = {}
    for row in rows:
        for name in product_categories_for_row(row):
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return "\n## Product Opportunity Summary\n\nNo product signals available.\n"

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    lines = [
        "\n## Product Opportunity Summary\n",
        "Likely plumbing products across permits above threshold:\n",
    ]
    for name, count in ranked:
        lines.append(f"- **{name}:** {count} permit(s)")
    lines.append("")
    return "\n".join(lines)
