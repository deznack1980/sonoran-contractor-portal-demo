"""Professional PDF sales report for CorridorIQ permit intelligence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from pipeline.config.settings import REPORT_MIN_OPPORTUNITY_SCORE, REPORT_PRIORITY_BANDS, REPORTS_GENERATED_DIR
from pipeline.reports.report_presentation import (
    lifecycle_fields_for_row,
    priority_badge,
    product_categories_for_row,
    reason_for_score,
    recommended_next_action,
    score_breakdown_lines,
)
from pipeline.reports.report_templates import contractor_display, money


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "CIQTitle",
            parent=base["Title"],
            fontSize=22,
            leading=26,
            textColor=colors.HexColor("#1F4E79"),
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "subtitle": ParagraphStyle(
            "CIQSubtitle",
            parent=base["Normal"],
            fontSize=12,
            leading=16,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#444444"),
            spaceAfter=8,
        ),
        "h1": ParagraphStyle(
            "CIQH1",
            parent=base["Heading1"],
            fontSize=16,
            textColor=colors.HexColor("#1F4E79"),
            spaceBefore=12,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "CIQH2",
            parent=base["Heading2"],
            fontSize=13,
            textColor=colors.HexColor("#2E75B6"),
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "CIQBody",
            parent=base["Normal"],
            fontSize=10,
            leading=13,
            alignment=TA_LEFT,
            spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "CIQSmall",
            parent=base["Normal"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#333333"),
            spaceAfter=2,
        ),
        "card_title": ParagraphStyle(
            "CIQCardTitle",
            parent=base["Heading3"],
            fontSize=11,
            textColor=colors.HexColor("#1F4E79"),
            spaceBefore=8,
            spaceAfter=4,
        ),
    }
    return styles


def _row_get(row, key: str, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def write_permit_intelligence_pdf(
    conn,
    db_rows: list,
    *,
    filename: str | None = None,
    title: str = "CorridorIQ Permit Intelligence Report",
    window_label: str = "All qualifying permits",
) -> str:
    """Generate a sales-meeting PDF with summary, ranked list, and product stats."""
    REPORTS_GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    if not filename:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filename = f"permit_intelligence_report_{date_str}.pdf"
    path = Path(REPORTS_GENERATED_DIR) / filename

    styles = _styles()
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title=title,
        author="CorridorIQ",
    )
    story: list = []

    # --- Title page ---
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_processed = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    scores = [float(r["opportunity_score"]) for r in db_rows if r["opportunity_score"] is not None]
    above = len(scores)
    highest = max(scores) if scores else None
    average = sum(scores) / len(scores) if scores else None
    total_opp = sum((_row_get(r, "estimated_material_value") or 0) for r in db_rows)

    story.append(Spacer(1, 1.5 * inch))
    story.append(Paragraph(_escape(title), styles["title"]))
    story.append(Paragraph("Plumbing Supplier Sales Brief", styles["subtitle"]))
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(f"<b>Date:</b> {generated}", styles["subtitle"]))
    story.append(Paragraph(f"<b>Window:</b> {_escape(window_label)}", styles["subtitle"]))
    story.append(
        Paragraph(
            f"<b>Score threshold:</b> {REPORT_MIN_OPPORTUNITY_SCORE}+ "
            "(all qualifying permits — no quantity cap)",
            styles["subtitle"],
        )
    )
    story.append(PageBreak())

    # --- Executive summary ---
    story.append(Paragraph("Executive Summary", styles["h1"]))
    summary_data = [
        ["Metric", "Value"],
        ["Total Permits Processed", f"{total_processed:,}"],
        [f"Permits Above Threshold ({REPORT_MIN_OPPORTUNITY_SCORE}+)", f"{above:,}"],
        ["Highest Score", f"{highest:.0f}%" if highest is not None else "—"],
        ["Average Score", f"{average:.1f}%" if average is not None else "—"],
        ["Estimated Total Opportunity", money(total_opp) if db_rows else "—"],
    ]
    summary_table = Table(summary_data, colWidths=[3.6 * inch, 2.8 * inch])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B0B0B0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 0.25 * inch))

    # --- Priority statistics ---
    story.append(Paragraph("Priority Statistics", styles["h1"]))
    band_counts = {band["name"]: 0 for band in REPORT_PRIORITY_BANDS}
    for score in scores:
        badge = priority_badge(score)
        if badge in band_counts:
            band_counts[badge] += 1
    band_data = [["Priority Band", "Score Range", "Count"]]
    for band in REPORT_PRIORITY_BANDS:
        band_data.append(
            [
                band["name"],
                f"{band['min']}-{band['max']}",
                str(band_counts[band["name"]]),
            ]
        )
    band_table = Table(band_data, colWidths=[2.2 * inch, 2.2 * inch, 2.0 * inch])
    band_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E75B6")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B0B0B0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(band_table)
    story.append(PageBreak())

    # --- Product opportunity summary ---
    story.append(Paragraph("Product Opportunity Summary", styles["h1"]))
    product_counts: dict[str, int] = {}
    for row in db_rows:
        for name in product_categories_for_row(row):
            product_counts[name] = product_counts.get(name, 0) + 1
    if product_counts:
        prod_data = [["Likely Product Category", "Permits"]]
        for name, count in sorted(product_counts.items(), key=lambda item: (-item[1], item[0])):
            prod_data.append([name, str(count)])
        prod_table = Table(prod_data, colWidths=[4.4 * inch, 2.0 * inch])
        prod_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B0B0B0")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
                    ("ALIGN", (1, 1), (1, -1), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(prod_table)
    else:
        story.append(Paragraph("No product signals available for this run.", styles["body"]))
    story.append(PageBreak())

    # --- Ranked permit list ---
    story.append(Paragraph("Ranked Permit List", styles["h1"]))
    story.append(
        Paragraph(
            f"Sorted highest score to lowest. Showing all {above} permits at or above "
            f"{REPORT_MIN_OPPORTUNITY_SCORE} (no quantity cap).",
            styles["body"],
        )
    )

    if not db_rows:
        story.append(Paragraph("No permits currently meet the score threshold.", styles["body"]))
    else:
        for rank, row in enumerate(db_rows, start=1):
            score = _row_get(row, "opportunity_score")
            badge = priority_badge(score)
            contractor = contractor_display(row)
            has_contractor = contractor not in ("Not in source",) and not str(contractor).startswith(
                "Project ·"
            )
            description = _row_get(row, "description") or _row_get(row, "project_description") or "—"
            if len(str(description)) > 180:
                description = str(description)[:177].rstrip() + "..."
            products = ", ".join(product_categories_for_row(row)) or "—"
            permit_row = {
                "permit_type": _row_get(row, "permit_type"),
                "status": _row_get(row, "status"),
                "issued_date": _row_get(row, "issued_date"),
                "filed_date": _row_get(row, "filed_date"),
                "valuation": _row_get(row, "valuation"),
                "square_footage": _row_get(row, "square_footage"),
                "description": description,
            }
            category = _row_get(row, "project_category") or "Other"
            breakdown = score_breakdown_lines(permit_row, category)
            # Compact factor lines: "Label +points" on one line each.
            factor_bits = []
            for i in range(0, len(breakdown) - 2, 3):
                label = breakdown[i]
                points = breakdown[i + 1] if i + 1 < len(breakdown) else ""
                if label and points and not label.startswith("Driver:") and label != "Final Score":
                    factor_bits.append(f"{label} {points}")
            factor_bits.append(f"Final Score {score:.0f}%")
            action = recommended_next_action(score, has_contractor)
            life = lifecycle_fields_for_row(row)
            days_txt = (
                f"{life['days_since_opportunity']}d ago"
                if life["days_since_opportunity"] is not None
                else "—"
            )
            opp_txt = (
                f"{life['opportunity_date']} ({life['opportunity_date_basis']})"
                if life["opportunity_date"]
                else "—"
            )

            block = (
                f"<b>Rank #{rank}</b> &nbsp;|&nbsp; <b>Score:</b> {score:.0f}% "
                f"&nbsp;|&nbsp; <b>Priority:</b> {_escape(badge)}<br/>"
                f"<b>Lifecycle:</b> {_escape(life['lifecycle_stage'])} "
                f"&nbsp;|&nbsp; <b>Timing:</b> {_escape(life['opportunity_timing'])} "
                f"&nbsp;|&nbsp; <b>Opportunity date:</b> {_escape(opp_txt)} "
                f"&nbsp;|&nbsp; <b>Age:</b> {_escape(days_txt)}<br/>"
                f"<b>Permit:</b> {_escape(_row_get(row, 'permit_number') or '—')} "
                f"&nbsp;|&nbsp; <b>Type:</b> {_escape(_row_get(row, 'permit_type') or '—')} "
                f"&nbsp;|&nbsp; <b>Issued:</b> {_escape(_row_get(row, 'issued_date') or '—')} "
                f"&nbsp;|&nbsp; <b>Status:</b> {_escape(_row_get(row, 'status') or '—')}<br/>"
                f"<b>Contractor:</b> {_escape(contractor)} "
                f"&nbsp;|&nbsp; <b>Owner:</b> {_escape(_row_get(row, 'owner_name') or '—')}<br/>"
                f"<b>Address:</b> {_escape(_row_get(row, 'job_address') or '—')}, "
                f"{_escape(_row_get(row, 'city') or '—')}<br/>"
                f"<b>Description:</b> {_escape(description)}<br/>"
                f"<b>Est. Material Opportunity:</b> {money(_row_get(row, 'estimated_material_value'))}<br/>"
                f"<b>Likely Products:</b> {_escape(products)}<br/>"
                f"<b>Reason for Score:</b> {_escape(reason_for_score(permit_row, category))}<br/>"
                f"<b>Score Breakdown:</b> {_escape(' · '.join(factor_bits))}<br/>"
                f"<b>Recommended Next Action:</b> {_escape(action)}<br/>"
                f"<b>Recommended Sales Action:</b> {_escape(life['sales_action'])}"
            )
            story.append(Paragraph(block, styles["small"]))
            story.append(Spacer(1, 0.06 * inch))

    doc.build(story)
    return str(path)
