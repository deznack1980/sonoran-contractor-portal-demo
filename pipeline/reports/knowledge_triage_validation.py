"""Sprint 2.1 validation report — knowledge review triage.

Generates:
    reports/generated/knowledge_triage_validation_<date>.md
    reports/generated/knowledge_triage_validation_<date>.xlsx

Read-only: recomputes review priorities, then summarizes the pending unknown
queue by business impact, confidence distribution, and per-municipality
fallback rates. Scoring weights and thresholds are never touched.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from pipeline.config.settings import REPORTS_GENERATED_DIR
from pipeline.knowledge.priority import confidence_tier, recompute_priorities


def _pending(conn: sqlite3.Connection) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM knowledge_review_queue
            WHERE status='pending'
            ORDER BY COALESCE(priority_score,-1) DESC, occurrence_count DESC
            """
        )
    ]


def _tier_counts(rows: list[dict]) -> dict[str, int]:
    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for r in rows:
        t = r.get("priority_tier") or "Low"
        counts[t] = counts.get(t, 0) + 1
    return counts


def _confidence_distribution(conn: sqlite3.Connection) -> dict[str, int]:
    dist = {"Verified": 0, "High": 0, "Moderate": 0, "Low": 0, "Unset": 0}
    for tbl in ("status_dictionary", "permit_code_dictionary", "keyword_dictionary"):
        for row in conn.execute(f"SELECT mapping_confidence FROM {tbl}"):
            c = row["mapping_confidence"]
            if c is None:
                dist["Unset"] += 1
            else:
                dist[confidence_tier(float(c))] += 1
    return dist


def _lifecycle_distribution(conn: sqlite3.Connection) -> dict[str, int]:
    dist: dict[str, int] = {}
    for tbl in ("status_dictionary", "permit_code_dictionary", "keyword_dictionary"):
        for row in conn.execute(
            f"SELECT COALESCE(lifecycle_state,'active') AS s, COUNT(*) AS n "
            f"FROM {tbl} GROUP BY s"
        ):
            dist[row["s"]] = dist.get(row["s"], 0) + row["n"]
    return dist


def _fallback_by_municipality(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.jurisdiction AS jur,
               COUNT(*) AS total,
               SUM(CASE WHEN pr.project_category='Other' OR pr.project_category IS NULL
                        THEN 1 ELSE 0 END) AS other_cat
        FROM permits p
        LEFT JOIN projects pr ON pr.permit_id = p.id
        GROUP BY p.jurisdiction
        ORDER BY total DESC
        """
    ).fetchall()
    out = []
    for r in rows:
        total = int(r["total"] or 0)
        other = int(r["other_cat"] or 0)
        out.append(
            {
                "jurisdiction": r["jur"],
                "total_permits": total,
                "other_category": other,
                "fallback_rate_pct": round(100.0 * other / total, 1) if total else 0.0,
            }
        )
    return out


def generate_knowledge_triage_validation(conn: sqlite3.Connection) -> list[str]:
    recompute_priorities(conn)
    pending = _pending(conn)
    tiers = _tier_counts(pending)
    top50 = pending[:50]

    affected_total = sum(int(r.get("affected_permit_count") or 0) for r in top50)
    affected_recent = sum(int(r.get("affected_recent_count") or 0) for r in top50)
    affected_active = sum(int(r.get("affected_active_count") or 0) for r in top50)

    conf_dist = _confidence_distribution(conn)
    lifecycle = _lifecycle_distribution(conn)
    fallback = _fallback_by_municipality(conn)
    kb_version = conn.execute(
        "SELECT value FROM knowledge_meta WHERE key='kb_version'"
    ).fetchone()
    kb_version = kb_version["value"] if kb_version else "1"

    today = date.today().isoformat()
    REPORTS_GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    md_path = REPORTS_GENERATED_DIR / f"knowledge_triage_validation_{today}.md"
    xlsx_path = REPORTS_GENERATED_DIR / f"knowledge_triage_validation_{today}.xlsx"

    # ---------------- Markdown ----------------
    lines: list[str] = []
    lines.append(f"# Knowledge Review Triage Validation — {today}")
    lines.append("")
    lines.append(
        "Unknown mappings ranked by business impact. Scoring weights and the "
        "60-point publish threshold are unchanged; this report only orders and "
        "previews the review queue."
    )
    lines.append("")
    lines.append(f"- **Knowledge-base version:** {kb_version}")
    lines.append(f"- **Total pending unknowns:** {len(pending)}")
    lines.append(
        f"- **Priority tiers:** Critical {tiers['Critical']} · High {tiers['High']} "
        f"· Medium {tiers['Medium']} · Low {tiers['Low']}"
    )
    lines.append(
        f"- **Top 50 impact:** {affected_total} permits affected "
        f"({affected_recent} recent, {affected_active} active)"
    )
    lines.append("")

    lines.append("## Mapping confidence distribution (dictionary entries)")
    lines.append("")
    lines.append("| Tier | Count |")
    lines.append("| --- | ---: |")
    for name in ("Verified", "High", "Moderate", "Low", "Unset"):
        lines.append(f"| {name} | {conf_dist.get(name, 0)} |")
    lines.append("")

    lines.append("## Mapping lifecycle distribution")
    lines.append("")
    lines.append("| State | Count |")
    lines.append("| --- | ---: |")
    for state in ("active", "draft", "rejected", "deprecated"):
        lines.append(f"| {state} | {lifecycle.get(state, 0)} |")
    lines.append("")

    lines.append("## Top 50 unknowns by priority")
    lines.append("")
    lines.append(
        "| # | Tier | Score | Kind | Municipality | Raw value | Occur | Affected | Recent | Active | Avg |"
    )
    lines.append("| ---: | --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for i, r in enumerate(top50, 1):
        lines.append(
            f"| {i} | {r.get('priority_tier') or 'Low'} "
            f"| {round(r.get('priority_score') or 0)} | {r['kind']} "
            f"| {r.get('municipality_slug') or '—'} | {r['raw_value']} "
            f"| {r.get('occurrence_count') or 0} | {r.get('affected_permit_count') or 0} "
            f"| {r.get('affected_recent_count') or 0} | {r.get('affected_active_count') or 0} "
            f"| {r.get('affected_avg_score') or 0} |"
        )
    lines.append("")

    lines.append("## Current fallback rate by municipality")
    lines.append("")
    lines.append("| Municipality | Permits | 'Other' category | Fallback rate |")
    lines.append("| --- | ---: | ---: | ---: |")
    for f in fallback:
        lines.append(
            f"| {f['jurisdiction']} | {f['total_permits']} "
            f"| {f['other_category']} | {f['fallback_rate_pct']}% |"
        )
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")

    # ---------------- Excel ----------------
    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF")

    def _style_header(ws):
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
        ws.freeze_panes = "A2"

    ws1 = wb.active
    ws1.title = "Summary"
    ws1.append(["Metric", "Value"])
    for row in [
        ("Knowledge-base version", kb_version),
        ("Total pending unknowns", len(pending)),
        ("Critical", tiers["Critical"]),
        ("High", tiers["High"]),
        ("Medium", tiers["Medium"]),
        ("Low", tiers["Low"]),
        ("Top 50 permits affected", affected_total),
        ("Top 50 recent affected", affected_recent),
        ("Top 50 active affected", affected_active),
    ]:
        ws1.append(list(row))
    _style_header(ws1)

    ws2 = wb.create_sheet("Top 50 by priority")
    ws2.append(
        ["#", "Tier", "Priority", "Kind", "Municipality", "Raw value",
         "Occurrences", "Affected", "Recent", "Active", "Avg score", "Suggested"]
    )
    for i, r in enumerate(top50, 1):
        ws2.append(
            [i, r.get("priority_tier") or "Low", round(r.get("priority_score") or 0),
             r["kind"], r.get("municipality_slug") or "—", r["raw_value"],
             r.get("occurrence_count") or 0, r.get("affected_permit_count") or 0,
             r.get("affected_recent_count") or 0, r.get("affected_active_count") or 0,
             r.get("affected_avg_score") or 0, r.get("suggested_interpretation") or "—"]
        )
    _style_header(ws2)

    ws3 = wb.create_sheet("Confidence")
    ws3.append(["Tier", "Count"])
    for name in ("Verified", "High", "Moderate", "Low", "Unset"):
        ws3.append([name, conf_dist.get(name, 0)])
    _style_header(ws3)

    ws4 = wb.create_sheet("Lifecycle")
    ws4.append(["State", "Count"])
    for state in ("active", "draft", "rejected", "deprecated"):
        ws4.append([state, lifecycle.get(state, 0)])
    _style_header(ws4)

    ws5 = wb.create_sheet("Fallback by municipality")
    ws5.append(["Municipality", "Permits", "Other category", "Fallback rate %"])
    for f in fallback:
        ws5.append(
            [f["jurisdiction"], f["total_permits"], f["other_category"], f["fallback_rate_pct"]]
        )
    _style_header(ws5)

    # Auto-size columns across all sheets.
    for ws in wb.worksheets:
        for col in ws.columns:
            width = max((len(str(c.value)) if c.value is not None else 0) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 48)

    wb.save(xlsx_path)

    return [str(md_path), str(xlsx_path)]


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    paths = generate_knowledge_triage_validation(connection)
    print("Wrote:")
    for p in paths:
        print(f"  {p}")
    connection.close()
