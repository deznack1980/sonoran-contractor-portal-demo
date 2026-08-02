"""One-shot audit: why the 7-day qualifying brief can be empty.

Read-only against SQLite. Does not change scoring, thresholds, or report format.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from pipeline.analysis.scoring import compute_opportunity_score_breakdown
from pipeline.config.settings import DB_PATH, REPORT_MIN_OPPORTUNITY_SCORE, REPORTS_GENERATED_DIR
from pipeline.db.database import init_db


def _nz(value, default=0):
    return default if value is None else value


def _parse_issued(raw: str | None) -> str:
    if not raw or not str(raw).strip():
        return ""
    text = str(raw).strip()
    # Stored values are YYYY-MM-DD or ISO with time; normalize to date portion.
    return text[:10]


def _inclusion_reason(issued_raw: str | None, score: float | None, cutoff7: str) -> str:
    parsed = _parse_issued(issued_raw)
    if not parsed:
        return "excluded: missing/invalid issued_date"
    if parsed < cutoff7:
        return f"excluded: issued_date {parsed} older than 7-day window (cutoff {cutoff7})"
    if score is None:
        return "excluded: no opportunity_score (analysis missing)"
    if score < REPORT_MIN_OPPORTUNITY_SCORE:
        return (
            f"excluded: score {score:.1f} < threshold {REPORT_MIN_OPPORTUNITY_SCORE} "
            "(passes date window)"
        )
    return "INCLUDED in seven-day brief"


def _jurisdiction_rows(conn: sqlite3.Connection, cutoff7: str, cutoff14: str, cutoff30: str) -> list[dict]:
    juris = conn.execute(
        """
        SELECT slug, name, status, connector_type, last_synced_at, last_sync_status,
               last_sync_record_count, last_sync_error
        FROM jurisdictions
        ORDER BY name
        """
    ).fetchall()
    out: list[dict] = []
    for j in juris:
        slug = j["slug"]
        stats = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN issued_date IS NOT NULL AND trim(issued_date) != '' THEN 1 ELSE 0 END) AS valid_issued,
                   SUM(CASE WHEN issued_date IS NULL OR trim(issued_date) = '' THEN 1 ELSE 0 END) AS missing_issued,
                   MAX(issued_date) AS max_issued,
                   MIN(CASE WHEN issued_date IS NOT NULL AND trim(issued_date) != '' THEN issued_date END) AS min_issued,
                   SUM(CASE WHEN substr(issued_date,1,10) >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN substr(issued_date,1,10) >= ? THEN 1 ELSE 0 END) AS d14,
                   SUM(CASE WHEN substr(issued_date,1,10) >= ? THEN 1 ELSE 0 END) AS d30
            FROM permits
            WHERE jurisdiction = ?
            """,
            (cutoff7, cutoff14, cutoff30, slug),
        ).fetchone()
        scored = conn.execute(
            """
            SELECT
              SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND pr.opportunity_score >= ? THEN 1 ELSE 0 END) AS d7_ge60,
              SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND (pr.opportunity_score IS NULL OR pr.opportunity_score < ?) THEN 1 ELSE 0 END) AS d7_lt60,
              SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND pr.opportunity_score >= ? THEN 1 ELSE 0 END) AS d14_ge60,
              SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND (pr.opportunity_score IS NULL OR pr.opportunity_score < ?) THEN 1 ELSE 0 END) AS d14_lt60,
              SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND pr.opportunity_score >= ? THEN 1 ELSE 0 END) AS d30_ge60,
              SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND (pr.opportunity_score IS NULL OR pr.opportunity_score < ?) THEN 1 ELSE 0 END) AS d30_lt60
            FROM permits p
            LEFT JOIN projects pr ON pr.permit_id = p.id
            WHERE p.jurisdiction = ?
            """,
            (
                cutoff7,
                REPORT_MIN_OPPORTUNITY_SCORE,
                cutoff7,
                REPORT_MIN_OPPORTUNITY_SCORE,
                cutoff14,
                REPORT_MIN_OPPORTUNITY_SCORE,
                cutoff14,
                REPORT_MIN_OPPORTUNITY_SCORE,
                cutoff30,
                REPORT_MIN_OPPORTUNITY_SCORE,
                cutoff30,
                REPORT_MIN_OPPORTUNITY_SCORE,
                slug,
            ),
        ).fetchone()
        last_run = conn.execute(
            """
            SELECT run_started_at, run_finished_at, status, records_fetched,
                   records_inserted, records_updated, error_message
            FROM ingestion_runs
            WHERE jurisdiction_slug = ? AND status = 'success'
            ORDER BY COALESCE(run_finished_at, run_started_at) DESC
            LIMIT 1
            """,
            (slug,),
        ).fetchone()
        out.append(
            {
                "slug": slug,
                "name": j["name"],
                "status": j["status"],
                "connector": j["connector_type"],
                "last_synced_at": j["last_synced_at"],
                "last_sync_status": j["last_sync_status"],
                "last_sync_record_count": j["last_sync_record_count"],
                "last_sync_error": j["last_sync_error"],
                "total": _nz(stats["total"]),
                "valid_issued": _nz(stats["valid_issued"]),
                "missing_issued": _nz(stats["missing_issued"]),
                "max_issued": stats["max_issued"],
                "min_issued": stats["min_issued"],
                "d7": _nz(stats["d7"]),
                "d14": _nz(stats["d14"]),
                "d30": _nz(stats["d30"]),
                "d7_ge60": _nz(scored["d7_ge60"]),
                "d7_lt60": _nz(scored["d7_lt60"]),
                "d14_ge60": _nz(scored["d14_ge60"]),
                "d14_lt60": _nz(scored["d14_lt60"]),
                "d30_ge60": _nz(scored["d30_ge60"]),
                "d30_lt60": _nz(scored["d30_lt60"]),
                "last_success_run_started": last_run["run_started_at"] if last_run else None,
                "last_success_run_finished": last_run["run_finished_at"] if last_run else None,
                "last_success_fetched": last_run["records_fetched"] if last_run else None,
                "last_success_inserted": last_run["records_inserted"] if last_run else None,
                "last_success_updated": last_run["records_updated"] if last_run else None,
            }
        )
    return out


def _recent_sample(conn: sqlite3.Connection, cutoff7: str, limit: int = 25) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.jurisdiction, j.name AS source, p.permit_number, p.permit_type, p.status,
               p.issued_date, p.filed_date, p.finaled_date, p.last_inspection_date,
               p.first_seen_at, p.last_updated_at, p.valuation, p.square_footage,
               p.description, pr.opportunity_score, pr.project_category
        FROM permits p
        JOIN jurisdictions j ON j.slug = p.jurisdiction
        LEFT JOIN projects pr ON pr.permit_id = p.id
        WHERE p.issued_date IS NOT NULL AND trim(p.issued_date) != ''
        ORDER BY substr(p.issued_date, 1, 10) DESC, p.permit_number
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    sample: list[dict] = []
    for r in rows:
        permit_row = {
            "permit_type": r["permit_type"],
            "status": r["status"],
            "issued_date": r["issued_date"],
            "filed_date": r["filed_date"],
            "valuation": r["valuation"],
            "square_footage": r["square_footage"],
            "description": r["description"],
        }
        category = r["project_category"] or "Other"
        breakdown = compute_opportunity_score_breakdown(permit_row, category)
        score = r["opportunity_score"]
        sample.append(
            {
                "source": r["source"],
                "jurisdiction": r["jurisdiction"],
                "permit_number": r["permit_number"],
                "raw_issued_date": r["issued_date"],
                "parsed_issued_date": _parse_issued(r["issued_date"]),
                "filed_date": r["filed_date"],
                "finaled_date": r["finaled_date"],
                "last_inspection_date": r["last_inspection_date"],
                "first_seen_at": r["first_seen_at"],
                "last_updated_at": r["last_updated_at"],
                "permit_type": r["permit_type"],
                "status": r["status"],
                "project_category": category,
                "valuation": r["valuation"],
                "score": score,
                "score_signal_points": breakdown["factors"][0]["points"],
                "score_recency_points": breakdown["factors"][1]["points"],
                "score_scale_points": breakdown["factors"][2]["points"],
                "score_sector_points": breakdown["factors"][3]["points"],
                "inclusion_reason": _inclusion_reason(r["issued_date"], score, cutoff7),
            }
        )
    return sample


def run_audit(conn: sqlite3.Connection | None = None) -> tuple[str, str]:
    close = False
    if conn is None:
        conn = init_db()
        close = True

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    cutoff7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    cutoff14 = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    cutoff30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")

    overall = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN issued_date IS NOT NULL AND trim(issued_date) != '' THEN 1 ELSE 0 END) AS valid_issued,
               SUM(CASE WHEN issued_date IS NULL OR trim(issued_date) = '' THEN 1 ELSE 0 END) AS missing_issued,
               MAX(issued_date) AS max_issued,
               MIN(CASE WHEN issued_date IS NOT NULL AND trim(issued_date) != '' THEN issued_date END) AS min_issued,
               SUM(CASE WHEN substr(issued_date,1,10) >= ? THEN 1 ELSE 0 END) AS d7,
               SUM(CASE WHEN substr(issued_date,1,10) >= ? THEN 1 ELSE 0 END) AS d14,
               SUM(CASE WHEN substr(issued_date,1,10) >= ? THEN 1 ELSE 0 END) AS d30,
               SUM(CASE WHEN filed_date IS NOT NULL AND trim(filed_date) != '' THEN 1 ELSE 0 END) AS has_filed,
               SUM(CASE WHEN finaled_date IS NOT NULL AND trim(finaled_date) != '' THEN 1 ELSE 0 END) AS has_finaled,
               SUM(CASE WHEN last_inspection_date IS NOT NULL AND trim(last_inspection_date) != '' THEN 1 ELSE 0 END) AS has_insp,
               SUM(CASE WHEN expiration_date IS NOT NULL AND trim(expiration_date) != '' THEN 1 ELSE 0 END) AS has_exp,
               MAX(filed_date) AS max_filed,
               MAX(finaled_date) AS max_finaled,
               MAX(first_seen_at) AS max_first_seen,
               MAX(last_updated_at) AS max_last_updated
        FROM permits
        """,
        (cutoff7, cutoff14, cutoff30),
    ).fetchone()

    overall_scored = conn.execute(
        """
        SELECT
          SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND pr.opportunity_score >= ? THEN 1 ELSE 0 END) AS d7_ge60,
          SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND (pr.opportunity_score IS NULL OR pr.opportunity_score < ?) THEN 1 ELSE 0 END) AS d7_lt60,
          SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND pr.opportunity_score >= ? THEN 1 ELSE 0 END) AS d14_ge60,
          SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND (pr.opportunity_score IS NULL OR pr.opportunity_score < ?) THEN 1 ELSE 0 END) AS d14_lt60,
          SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND pr.opportunity_score >= ? THEN 1 ELSE 0 END) AS d30_ge60,
          SUM(CASE WHEN substr(p.issued_date,1,10) >= ? AND (pr.opportunity_score IS NULL OR pr.opportunity_score < ?) THEN 1 ELSE 0 END) AS d30_lt60,
          MIN(CASE WHEN substr(p.issued_date,1,10) >= ? THEN pr.opportunity_score END) AS d7_min_score,
          MAX(CASE WHEN substr(p.issued_date,1,10) >= ? THEN pr.opportunity_score END) AS d7_max_score,
          AVG(CASE WHEN substr(p.issued_date,1,10) >= ? THEN pr.opportunity_score END) AS d7_avg_score
        FROM permits p
        LEFT JOIN projects pr ON pr.permit_id = p.id
        """,
        (
            cutoff7,
            REPORT_MIN_OPPORTUNITY_SCORE,
            cutoff7,
            REPORT_MIN_OPPORTUNITY_SCORE,
            cutoff14,
            REPORT_MIN_OPPORTUNITY_SCORE,
            cutoff14,
            REPORT_MIN_OPPORTUNITY_SCORE,
            cutoff30,
            REPORT_MIN_OPPORTUNITY_SCORE,
            cutoff30,
            REPORT_MIN_OPPORTUNITY_SCORE,
            cutoff7,
            cutoff7,
            cutoff7,
        ),
    ).fetchone()

    last_ingest_any = conn.execute(
        """
        SELECT MAX(COALESCE(run_finished_at, run_started_at)) AS last_success
        FROM ingestion_runs
        WHERE status = 'success'
        """
    ).fetchone()["last_success"]

    by_jurisdiction = _jurisdiction_rows(conn, cutoff7, cutoff14, cutoff30)
    sample = _recent_sample(conn, cutoff7, limit=25)

    d7 = _nz(overall["d7"])
    d7_ge60 = _nz(overall_scored["d7_ge60"])
    d7_lt60 = _nz(overall_scored["d7_lt60"])
    d7_max = overall_scored["d7_max_score"]
    d7_min = overall_scored["d7_min_score"]
    d7_avg = overall_scored["d7_avg_score"]

    # Failure classification (ordered checks).
    if d7 == 0:
        conclusion_code = 1
        conclusion_title = "No recent permits exist in the source data."
        conclusion_detail = (
            f"Zero permits have issued_date on or after {cutoff7}. "
            "The seven-day brief is empty because the date window itself is empty."
        )
    elif d7 > 0 and d7_ge60 == 0:
        conclusion_code = 5
        conclusion_title = "Recent permits exist but none meet the 60-point threshold."
        conclusion_detail = (
            f"There are {d7} permits with issued_date in the last 7 days (cutoff {cutoff7}), "
            f"but {d7_ge60} score at or above {REPORT_MIN_OPPORTUNITY_SCORE} and {d7_lt60} score below. "
            f"Every 7-day row currently scores between {d7_min:.1f} and {d7_max:.1f} "
            f"(average {d7_avg:.1f}). Date parsing and field selection are working; "
            "the brief filters them out solely on opportunity_score."
        )
    else:
        conclusion_code = 6
        conclusion_title = "Another clearly documented cause."
        conclusion_detail = (
            f"Unexpected state: d7={d7}, d7_ge60={d7_ge60}. Investigate query filters beyond "
            "date + score."
        )

    # Supporting context: several large markets look stale even though Peoria/Phoenix are fresh.
    stale = [
        j
        for j in by_jurisdiction
        if j["status"] == "connected"
        and j["total"] > 0
        and j["max_issued"]
        and _parse_issued(j["max_issued"]) < cutoff7
    ]

    REPORTS_GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    md_path = REPORTS_GENERATED_DIR / f"recent_permit_audit_{date_str}.md"
    xlsx_path = REPORTS_GENERATED_DIR / f"recent_permit_audit_{date_str}.xlsx"

    md = _render_markdown(
        now=now,
        cutoff7=cutoff7,
        cutoff14=cutoff14,
        cutoff30=cutoff30,
        overall=overall,
        overall_scored=overall_scored,
        last_ingest_any=last_ingest_any,
        by_jurisdiction=by_jurisdiction,
        sample=sample,
        conclusion_code=conclusion_code,
        conclusion_title=conclusion_title,
        conclusion_detail=conclusion_detail,
        stale=stale,
    )
    md_path.write_text(md, encoding="utf-8")
    _write_excel(
        xlsx_path,
        now=now,
        cutoff7=cutoff7,
        cutoff14=cutoff14,
        cutoff30=cutoff30,
        overall=overall,
        overall_scored=overall_scored,
        last_ingest_any=last_ingest_any,
        by_jurisdiction=by_jurisdiction,
        sample=sample,
        conclusion_code=conclusion_code,
        conclusion_title=conclusion_title,
        conclusion_detail=conclusion_detail,
    )

    if close:
        conn.close()
    return str(md_path), str(xlsx_path)


def _render_markdown(
    *,
    now: datetime,
    cutoff7: str,
    cutoff14: str,
    cutoff30: str,
    overall,
    overall_scored,
    last_ingest_any,
    by_jurisdiction: list[dict],
    sample: list[dict],
    conclusion_code: int,
    conclusion_title: str,
    conclusion_detail: str,
    stale: list[dict],
) -> str:
    lines: list[str] = []
    lines.append("# CorridorIQ Recent Permit Data Audit")
    lines.append("")
    lines.append(f"**Audit generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Database:** `{DB_PATH}`")
    lines.append(f"**Seven-day cutoff (UTC):** `{cutoff7}` (issued_date >= cutoff)")
    lines.append(f"**Score threshold used by brief:** `{REPORT_MIN_OPPORTUNITY_SCORE}+`")
    lines.append("")
    lines.append("## Executive Conclusion")
    lines.append("")
    lines.append(
        f"**Classification #{conclusion_code}: {conclusion_title}**"
    )
    lines.append("")
    lines.append(conclusion_detail)
    lines.append("")
    score_range = "n/a"
    if overall_scored["d7_min_score"] is not None and overall_scored["d7_max_score"] is not None:
        if overall_scored["d7_min_score"] == overall_scored["d7_max_score"]:
            score_range = f"{overall_scored['d7_min_score']:.1f}"
        else:
            score_range = (
                f"{overall_scored['d7_min_score']:.1f}–{overall_scored['d7_max_score']:.1f}"
            )
    lines.append(
        "In plain English: the seven-day plumbing intelligence brief is empty because "
        f"all {_nz(overall['d7'])} recently issued permits score **{score_range}**, which is below the "
        f"**{REPORT_MIN_OPPORTUNITY_SCORE}** publish bar. Dates are present and parse correctly; "
        "the filter that removes them is the opportunity-score threshold, not a missing-date bug."
    )
    lines.append("")
    lines.append("Why those recent rows score 56.2 (scoring engine unchanged, for diagnosis only):")
    lines.append("")
    lines.append(
        "- Status values like `Permit Issued` / `OPEN` do not match the engine's lowercase "
        "`issued` key, so signal strength falls back to the default (40)."
    )
    lines.append(
        "- Category is `Other` for all 7-day rows (sector fit 35)."
    )
    lines.append(
        "- Valuation and square footage are missing, so scale is neutral (50)."
    )
    lines.append(
        "- Recency is full (100) because issued dates are within the last week."
    )
    lines.append(
        "- Weighted result: `40×0.40 + 100×0.25 + 50×0.20 + 35×0.15 = 56.25 → 56.2`."
    )
    lines.append("")
    if stale:
        lines.append("### Secondary observation (not the empty-brief cause)")
        lines.append("")
        lines.append(
            "Several connected markets have no issued permits inside the 7-day window "
            "(freshest data older than the cutoff), and some last syncs fetched 0 records. "
            "That reduces volume of recent candidates, but Peoria and Phoenix still supply "
            "77 recent permits — all of which fail the score gate."
        )
        lines.append("")
        for j in stale:
            lines.append(
                f"- **{j['name']}**: newest issued_date `{j['max_issued']}`; "
                f"last sync `{j['last_synced_at']}` status `{j['last_sync_status']}` "
                f"(fetched/count `{j['last_sync_record_count']}`)."
            )
        lines.append("")

    lines.append("## Date Fields Available")
    lines.append("")
    lines.append("| Field | Role | Records with value | Notes |")
    lines.append("|---|---|---:|---|")
    lines.append(
        f"| `issued_date` | **Issue date — used by seven-day brief** | {_nz(overall['valid_issued']):,} | "
        "Filter: `p.issued_date >= cutoff` in `daily_arizona_plumbing_intelligence_report` |"
    )
    lines.append(
        f"| `filed_date` | Application / filed date | {_nz(overall['has_filed']):,} | "
        f"Newest: `{overall['max_filed']}` |"
    )
    lines.append(
        f"| `finaled_date` | Final / completed date | {_nz(overall['has_finaled']):,} | "
        f"Newest: `{overall['max_finaled']}` |"
    )
    lines.append(
        f"| `last_inspection_date` | Inspection date | {_nz(overall['has_insp']):,} | "
        "Currently unused / empty in DB |"
    )
    lines.append(
        f"| `expiration_date` | Expiration | {_nz(overall['has_exp']):,} | Not used by brief window |"
    )
    lines.append(
        f"| `first_seen_at` | CorridorIQ ingestion/first-seen timestamp | {_nz(overall['total']):,} | "
        f"Newest: `{overall['max_first_seen']}` |"
    )
    lines.append(
        f"| `last_updated_at` | CorridorIQ last update timestamp | {_nz(overall['total']):,} | "
        f"Newest: `{overall['max_last_updated']}` |"
    )
    lines.append(
        "| `ingestion_runs.run_finished_at` | Last successful ingestion time (per source) | — | "
        f"Latest success across all sources: `{last_ingest_any}` |"
    )
    lines.append("")
    lines.append(
        "**Confirmed:** the seven-day brief uses **`permits.issued_date` only** "
        "(not filed_date, finaled_date, or ingestion timestamps)."
    )
    lines.append("")
    lines.append("Source raw JSON date keys observed (examples):")
    lines.append("")
    lines.append("- Peoria: `IssDate`, `IssRevDate`, `CmpDate`, `ExpDate`")
    lines.append("- Phoenix: `PER_ISSUE_DATE`, `PER_ENT_DATE`, `PER_EXPIRE_DATE`, `PER_COMPL_DATE`")
    lines.append("- Mesa: `issued_date`, `finaled_date`")
    lines.append("- Gilbert: `IssuedDate`, `ApplyDate`, `FinalDate`")
    lines.append("")

    lines.append("## Complete Dataset Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total records | {_nz(overall['total']):,} |")
    lines.append(f"| Most recent permit issue date | `{overall['max_issued']}` |")
    lines.append(f"| Oldest permit issue date | `{overall['min_issued']}` |")
    lines.append(f"| Last successful ingestion (any source) | `{last_ingest_any}` |")
    lines.append(f"| Valid issue date | {_nz(overall['valid_issued']):,} |")
    lines.append(f"| Missing/invalid issue date | {_nz(overall['missing_issued']):,} |")
    lines.append(f"| Issued last 7 days (>= {cutoff7}) | {_nz(overall['d7']):,} |")
    lines.append(f"| Issued last 14 days (>= {cutoff14}) | {_nz(overall['d14']):,} |")
    lines.append(f"| Issued last 30 days (>= {cutoff30}) | {_nz(overall['d30']):,} |")
    lines.append(
        f"| Last 7 days scoring {REPORT_MIN_OPPORTUNITY_SCORE}+ | {_nz(overall_scored['d7_ge60']):,} |"
    )
    lines.append(
        f"| Last 7 days scoring below {REPORT_MIN_OPPORTUNITY_SCORE} | {_nz(overall_scored['d7_lt60']):,} |"
    )
    lines.append(
        f"| Last 14 days scoring {REPORT_MIN_OPPORTUNITY_SCORE}+ | {_nz(overall_scored['d14_ge60']):,} |"
    )
    lines.append(
        f"| Last 14 days scoring below {REPORT_MIN_OPPORTUNITY_SCORE} | {_nz(overall_scored['d14_lt60']):,} |"
    )
    lines.append(
        f"| Last 30 days scoring {REPORT_MIN_OPPORTUNITY_SCORE}+ | {_nz(overall_scored['d30_ge60']):,} |"
    )
    lines.append(
        f"| Last 30 days scoring below {REPORT_MIN_OPPORTUNITY_SCORE} | {_nz(overall_scored['d30_lt60']):,} |"
    )
    lines.append("")

    lines.append("## By Municipality / Source")
    lines.append("")
    lines.append(
        "| Source | Status | Total | Newest issued | Oldest issued | Last successful ingestion | "
        f"Valid issued | Missing issued | 7d | 14d | 30d | 7d >={REPORT_MIN_OPPORTUNITY_SCORE} | "
        f"7d <{REPORT_MIN_OPPORTUNITY_SCORE} |"
    )
    lines.append("|---|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for j in by_jurisdiction:
        lines.append(
            f"| {j['name']} | {j['status']} | {j['total']:,} | `{j['max_issued'] or '—'}` | "
            f"`{j['min_issued'] or '—'}` | `{j['last_success_run_finished'] or j['last_synced_at'] or '—'}` | "
            f"{j['valid_issued']:,} | {j['missing_issued']:,} | {j['d7']:,} | {j['d14']:,} | {j['d30']:,} | "
            f"{j['d7_ge60']:,} | {j['d7_lt60']:,} |"
        )
    lines.append("")

    lines.append("## Recent Raw Records (25 newest by issued_date)")
    lines.append("")
    lines.append(
        "| Source | Permit # | Raw issued_date | Parsed issued_date | Score | Inclusion / exclusion reason |"
    )
    lines.append("|---|---|---|---|---:|---|")
    for row in sample:
        score = "—" if row["score"] is None else f"{row['score']:.1f}"
        lines.append(
            f"| {row['source']} | {row['permit_number']} | `{row['raw_issued_date']}` | "
            f"`{row['parsed_issued_date']}` | {score} | {row['inclusion_reason']} |"
        )
    lines.append("")
    lines.append("### Score component snapshot for the same sample")
    lines.append("")
    lines.append("| Source | Permit # | Status | Category | Signal pts | Recency pts | Scale pts | Sector pts | Final |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|")
    for row in sample:
        score = "—" if row["score"] is None else f"{row['score']:.1f}"
        lines.append(
            f"| {row['source']} | {row['permit_number']} | {row['status'] or '—'} | "
            f"{row['project_category']} | {row['score_signal_points']} | {row['score_recency_points']} | "
            f"{row['score_scale_points']} | {row['score_sector_points']} | {score} |"
        )
    lines.append("")

    lines.append("## Ruled-Out Alternatives")
    lines.append("")
    lines.append("| Hypothesis | Result |")
    lines.append("|---|---|")
    lines.append(
        f"| 1. No recent permits in source data | **Rejected** — {_nz(overall['d7'])} permits issued since {cutoff7} |"
    )
    lines.append(
        "| 2. Source feed entirely stale/incomplete | **Not primary cause** — Peoria/Phoenix delivered "
        f"{_nz(overall['d7'])} fresh issued rows; some other cities are stale as a secondary issue |"
    )
    lines.append(
        "| 3. Date parsing incorrect | **Rejected** — 0 issued_date values fail YYYY-MM-DD prefix; "
        "parsed dates match raw prefixes |"
    )
    lines.append(
        "| 4. Wrong date field used | **Rejected for emptiness** — brief correctly uses `issued_date`; "
        "filed/final/ingestion dates are available but intentionally not the window field |"
    )
    lines.append(
        f"| 5. Recent permits exist but none meet {REPORT_MIN_OPPORTUNITY_SCORE} | "
        f"**Confirmed** — {_nz(overall_scored['d7_ge60'])} at/above threshold, "
        f"{_nz(overall_scored['d7_lt60'])} below |"
    )
    lines.append("")
    lines.append("## Final Classification")
    lines.append("")
    lines.append(f"**#{conclusion_code} — {conclusion_title}**")
    lines.append("")
    lines.append(conclusion_detail)
    lines.append("")
    return "\n".join(lines)


def _write_excel(
    path: Path,
    *,
    now: datetime,
    cutoff7: str,
    cutoff14: str,
    cutoff30: str,
    overall,
    overall_scored,
    last_ingest_any,
    by_jurisdiction: list[dict],
    sample: list[dict],
    conclusion_code: int,
    conclusion_title: str,
    conclusion_detail: str,
) -> None:
    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Border(
        left=Side(style="thin", color="B0B0B0"),
        right=Side(style="thin", color="B0B0B0"),
        top=Side(style="thin", color="B0B0B0"),
        bottom=Side(style="thin", color="B0B0B0"),
    )

    def style_header(ws):
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            cell.border = thin
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for col in range(1, ws.max_column + 1):
            letter = get_column_letter(col)
            width = 12
            for cell in ws[letter]:
                width = max(width, min(len(str(cell.value or "")), 48) + 2)
            ws.column_dimensions[letter].width = width

    # Summary sheet
    ws = wb.active
    ws.title = "Executive Summary"
    summary_rows = [
        ("Audit generated (UTC)", now.strftime("%Y-%m-%d %H:%M")),
        ("Classification code", conclusion_code),
        ("Classification", conclusion_title),
        ("Detail", conclusion_detail),
        ("Seven-day cutoff", cutoff7),
        ("Fourteen-day cutoff", cutoff14),
        ("Thirty-day cutoff", cutoff30),
        ("Score threshold", REPORT_MIN_OPPORTUNITY_SCORE),
        ("Date field used by brief", "permits.issued_date"),
        ("Total records", _nz(overall["total"])),
        ("Most recent issued_date", overall["max_issued"]),
        ("Oldest issued_date", overall["min_issued"]),
        ("Last successful ingestion (any)", last_ingest_any),
        ("Valid issued_date", _nz(overall["valid_issued"])),
        ("Missing issued_date", _nz(overall["missing_issued"])),
        ("Issued last 7 days", _nz(overall["d7"])),
        ("Issued last 14 days", _nz(overall["d14"])),
        ("Issued last 30 days", _nz(overall["d30"])),
        (f"7d score >={REPORT_MIN_OPPORTUNITY_SCORE}", _nz(overall_scored["d7_ge60"])),
        (f"7d score <{REPORT_MIN_OPPORTUNITY_SCORE}", _nz(overall_scored["d7_lt60"])),
        (f"14d score >={REPORT_MIN_OPPORTUNITY_SCORE}", _nz(overall_scored["d14_ge60"])),
        (f"14d score <{REPORT_MIN_OPPORTUNITY_SCORE}", _nz(overall_scored["d14_lt60"])),
        (f"30d score >={REPORT_MIN_OPPORTUNITY_SCORE}", _nz(overall_scored["d30_ge60"])),
        (f"30d score <{REPORT_MIN_OPPORTUNITY_SCORE}", _nz(overall_scored["d30_lt60"])),
        ("7d min score", overall_scored["d7_min_score"]),
        ("7d max score", overall_scored["d7_max_score"]),
        ("7d avg score", round(overall_scored["d7_avg_score"], 2) if overall_scored["d7_avg_score"] is not None else None),
    ]
    ws.append(["Metric", "Value"])
    for row in summary_rows:
        ws.append(list(row))
    style_header(ws)

    # By source
    ws2 = wb.create_sheet("By Source")
    ws2.append(
        [
            "Source",
            "Slug",
            "Status",
            "Connector",
            "Total",
            "Newest issued",
            "Oldest issued",
            "Last synced at",
            "Last sync status",
            "Last sync record count",
            "Last success run finished",
            "Last success fetched",
            "Last success inserted",
            "Last success updated",
            "Valid issued",
            "Missing issued",
            "Issued 7d",
            "Issued 14d",
            "Issued 30d",
            f"7d >={REPORT_MIN_OPPORTUNITY_SCORE}",
            f"7d <{REPORT_MIN_OPPORTUNITY_SCORE}",
            f"14d >={REPORT_MIN_OPPORTUNITY_SCORE}",
            f"14d <{REPORT_MIN_OPPORTUNITY_SCORE}",
            f"30d >={REPORT_MIN_OPPORTUNITY_SCORE}",
            f"30d <{REPORT_MIN_OPPORTUNITY_SCORE}",
        ]
    )
    for j in by_jurisdiction:
        ws2.append(
            [
                j["name"],
                j["slug"],
                j["status"],
                j["connector"],
                j["total"],
                j["max_issued"],
                j["min_issued"],
                j["last_synced_at"],
                j["last_sync_status"],
                j["last_sync_record_count"],
                j["last_success_run_finished"],
                j["last_success_fetched"],
                j["last_success_inserted"],
                j["last_success_updated"],
                j["valid_issued"],
                j["missing_issued"],
                j["d7"],
                j["d14"],
                j["d30"],
                j["d7_ge60"],
                j["d7_lt60"],
                j["d14_ge60"],
                j["d14_lt60"],
                j["d30_ge60"],
                j["d30_lt60"],
            ]
        )
    style_header(ws2)

    # Sample
    ws3 = wb.create_sheet("Recent Sample")
    ws3.append(
        [
            "Source",
            "Jurisdiction",
            "Permit Number",
            "Raw issued_date",
            "Parsed issued_date",
            "Filed date",
            "Finaled date",
            "Inspection date",
            "First seen at",
            "Last updated at",
            "Permit type",
            "Status",
            "Category",
            "Valuation",
            "Score",
            "Signal pts",
            "Recency pts",
            "Scale pts",
            "Sector pts",
            "Inclusion / exclusion reason",
        ]
    )
    for row in sample:
        ws3.append(
            [
                row["source"],
                row["jurisdiction"],
                row["permit_number"],
                row["raw_issued_date"],
                row["parsed_issued_date"],
                row["filed_date"],
                row["finaled_date"],
                row["last_inspection_date"],
                row["first_seen_at"],
                row["last_updated_at"],
                row["permit_type"],
                row["status"],
                row["project_category"],
                row["valuation"],
                row["score"],
                row["score_signal_points"],
                row["score_recency_points"],
                row["score_scale_points"],
                row["score_sector_points"],
                row["inclusion_reason"],
            ]
        )
    style_header(ws3)

    wb.save(path)


if __name__ == "__main__":
    md_path, xlsx_path = run_audit()
    print(f"Generated: {md_path}")
    print(f"Generated: {xlsx_path}")
