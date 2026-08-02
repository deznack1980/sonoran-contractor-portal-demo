"""SQLite -> data/exports/*.json.

These files are the entire interface between the pipeline and the static
dashboard/pages (dashboard.js/contractors.js never touch SQLite directly).
Each file is stamped with generated_at so the frontend can honestly show
"data as of ___" rather than implying real-time freshness.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from pipeline.config.settings import DATA_EXPORTS_DIR, REPORT_MIN_OPPORTUNITY_SCORE
from pipeline.connectors.base import utcnow_iso
from pipeline.reports.report_templates import contractor_display, has_named_party

HIGH_OPPORTUNITY_LIMIT = 100
RECENT_PERMITS_LIMIT = 300
RECENT_PERMITS_WINDOW_DAYS = 30
CONTRACTORS_LIMIT = 200
CONTRACTOR_MATCHING_LIMIT = 500
JOB_BRIEF_RECENT_DAYS = 7


def _write_json(filename: str, payload) -> None:
    DATA_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_EXPORTS_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)


def _job_key(jurisdiction: str, permit_number: str) -> str:
    return f"{jurisdiction}|{permit_number}"


def _row_to_job_brief(conn: sqlite3.Connection, row) -> dict:
    materials = [
        dict(m)
        for m in conn.execute(
            """
            SELECT material_name, confidence_pct, rationale
            FROM estimated_materials
            WHERE project_id = ?
            ORDER BY confidence_pct DESC
            """,
            (row["project_id"],),
        ).fetchall()
    ]
    lead = contractor_display(row)
    return {
        "key": _job_key(row["jurisdiction"], row["permit_number"]),
        "jurisdiction": row["jurisdiction"],
        "jurisdiction_name": row["jurisdiction_name"],
        "permit_number": row["permit_number"],
        "permit_type": row["permit_type"],
        "permit_subtype": row["permit_subtype"],
        "status": row["status"],
        "description": row["description"],
        "project_description": row["project_description"],
        "filed_date": row["filed_date"],
        "issued_date": row["issued_date"],
        "expiration_date": row["expiration_date"],
        "finaled_date": row["finaled_date"],
        "job_address": row["job_address"],
        "city": row["city"],
        "state": row["state"],
        "zip": row["zip"],
        "parcel_number": row["parcel_number"],
        "owner_name": row["owner_name"],
        "general_contractor_name": row["general_contractor_name"],
        "plumbing_contractor_name": row["plumbing_contractor_name"],
        "contractor_license_number": row["contractor_license_number"],
        "outreach_lead": lead,
        "has_named_party": has_named_party(row),
        "valuation": row["valuation"],
        "square_footage": row["square_footage"],
        "project_id": row["project_id"],
        "project_category": row["project_category"],
        "construction_stage": row["construction_stage"],
        "project_lifecycle": row["project_lifecycle"],
        "opportunity_date": row["opportunity_date"],
        "opportunity_date_basis": row["opportunity_date_basis"],
        "opportunity_timing": row["opportunity_timing"],
        "opportunity_score": row["opportunity_score"],
        "confidence_score": row["confidence_score"],
        "estimated_material_value": row["estimated_material_value"],
        "estimated_gross_profit": row["estimated_gross_profit"],
        "estimated_plumbing_scope": row["estimated_plumbing_scope"],
        "materials": materials,
        "job_url": f"job.html?j={row['jurisdiction']}&p={row['permit_number']}",
    }


def export_job_briefs(conn: sqlite3.Connection) -> None:
    """Polished single-job briefs for the qualifying outreach window + high scores.

    Consumed by job.html so Priority Outreach rows can deep-link to a full
    job brief without scraping SQLite from the browser.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=JOB_BRIEF_RECENT_DAYS)).strftime("%Y-%m-%d")
    sql = """
        SELECT p.jurisdiction, j.name AS jurisdiction_name,
               p.permit_number, p.permit_type, p.permit_subtype, p.status,
               p.description, p.project_description,
               p.filed_date, p.issued_date, p.expiration_date, p.finaled_date,
               p.job_address, p.city, p.state, p.zip, p.parcel_number,
               p.owner_name, p.general_contractor_name, p.plumbing_contractor_name,
               p.contractor_license_number, p.valuation, p.square_footage,
               pr.id AS project_id, pr.project_category, pr.construction_stage,
               pr.project_lifecycle, pr.opportunity_date, pr.opportunity_date_basis,
               pr.opportunity_timing,
               pr.opportunity_score, pr.confidence_score,
               pr.estimated_material_value, pr.estimated_gross_profit,
               pr.estimated_plumbing_scope
        FROM projects pr
        JOIN permits p ON p.id = pr.permit_id
        JOIN jurisdictions j ON j.slug = p.jurisdiction
        WHERE {where}
        ORDER BY pr.opportunity_score DESC
    """

    jobs: dict[str, dict] = {}
    # Qualifying window (same idea as the daily outreach list).
    for row in conn.execute(
        sql.format(where="p.issued_date >= ? AND pr.opportunity_score >= ?"),
        (cutoff, REPORT_MIN_OPPORTUNITY_SCORE),
    ):
        brief = _row_to_job_brief(conn, row)
        jobs[brief["key"]] = brief

    # Always include every threshold-qualified job so ranked-report links resolve.
    for row in conn.execute(
        sql.format(where="pr.opportunity_score >= ?"),
        (REPORT_MIN_OPPORTUNITY_SCORE,),
    ):
        brief = _row_to_job_brief(conn, row)
        jobs.setdefault(brief["key"], brief)

    _write_json(
        "job_briefs.json",
        {
            "generated_at": utcnow_iso(),
            "window_days": JOB_BRIEF_RECENT_DAYS,
            "score_floor": REPORT_MIN_OPPORTUNITY_SCORE,
            "count": len(jobs),
            "jobs": jobs,
        },
    )


def export_jurisdictions_status(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT slug, name, state, status, connector_type, notes,
               last_synced_at, last_sync_status, last_sync_record_count
        FROM jurisdictions
        ORDER BY (status = 'connected') DESC, name ASC
        """
    ).fetchall()
    _write_json("jurisdictions_status.json", {
        "generated_at": utcnow_iso(),
        "jurisdictions": [dict(r) for r in rows],
    })


def export_permits_recent(conn: sqlite3.Connection) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_PERMITS_WINDOW_DAYS)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT p.permit_number, p.jurisdiction, p.permit_type, p.status, p.description,
               p.issued_date, p.filed_date, p.job_address, p.city, p.valuation,
               p.square_footage, p.general_contractor_name, pr.project_category,
               pr.project_lifecycle, pr.opportunity_date, pr.opportunity_timing,
               pr.opportunity_score, pr.confidence_score, pr.estimated_material_value
        FROM permits p
        LEFT JOIN projects pr ON pr.permit_id = p.id
        WHERE p.issued_date >= ?
        ORDER BY p.issued_date DESC
        LIMIT ?
        """,
        (cutoff, RECENT_PERMITS_LIMIT),
    ).fetchall()
    _write_json("permits_recent.json", {
        "generated_at": utcnow_iso(),
        "window_days": RECENT_PERMITS_WINDOW_DAYS,
        "permits": [dict(r) for r in rows],
    })


LIFECYCLE_PIPELINE_WINDOW_DAYS = 60
LIFECYCLE_PIPELINE_LIMIT = 500


def export_lifecycle_pipeline(conn: sqlite3.Connection) -> None:
    """Project-lifecycle feed for the dashboard (Phase 5).

    Windowed by the EARLIEST usable date (opportunity_date, falling back to
    issued/filed) so submitted-but-not-yet-issued projects appear — the whole
    point of lifecycle tracking. Includes lifecycle stage + opportunity timing
    so the dashboard can filter/segment client-side without hitting SQLite.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=LIFECYCLE_PIPELINE_WINDOW_DAYS)
    ).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT p.permit_number, p.jurisdiction, p.permit_type, p.status,
               p.description, p.issued_date, p.filed_date, p.finaled_date,
               p.job_address, p.city, p.valuation,
               p.general_contractor_name, p.owner_name,
               pr.project_category, pr.project_lifecycle, pr.opportunity_date,
               pr.opportunity_date_basis, pr.opportunity_timing,
               pr.construction_stage, pr.opportunity_score,
               pr.estimated_material_value
        FROM permits p
        LEFT JOIN projects pr ON pr.permit_id = p.id
        WHERE COALESCE(pr.opportunity_date, p.issued_date, p.filed_date) >= ?
        ORDER BY COALESCE(pr.opportunity_date, p.issued_date, p.filed_date) DESC
        LIMIT ?
        """,
        (cutoff, LIFECYCLE_PIPELINE_LIMIT),
    ).fetchall()

    projects = []
    for r in rows:
        d = dict(r)
        opp = d.get("opportunity_date")
        if opp:
            try:
                days = max(
                    0,
                    (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(str(opp)[:10]).replace(tzinfo=timezone.utc)
                    ).days,
                )
            except ValueError:
                days = None
        else:
            days = None
        d["days_since_opportunity"] = days
        d["issued_today"] = bool(d.get("issued_date") and str(d["issued_date"])[:10] == today)
        d["job_url"] = f"job.html?j={d['jurisdiction']}&p={d['permit_number']}"
        projects.append(d)

    _write_json(
        "lifecycle_pipeline.json",
        {
            "generated_at": utcnow_iso(),
            "window_days": LIFECYCLE_PIPELINE_WINDOW_DAYS,
            "today": today,
            "count": len(projects),
            "projects": projects,
        },
    )


def export_high_opportunity_projects(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT p.permit_number, p.jurisdiction, p.city, p.job_address,
               COALESCE(
                 NULLIF(trim(p.general_contractor_name), ''),
                 NULLIF(trim(p.plumbing_contractor_name), ''),
                 NULLIF(trim(p.owner_name), '')
               ) AS general_contractor_name,
               pr.id AS project_id, pr.project_category, pr.construction_stage,
               pr.project_lifecycle, pr.opportunity_date, pr.opportunity_timing,
               pr.opportunity_score, pr.confidence_score, pr.estimated_material_value,
               pr.estimated_gross_profit, pr.estimated_plumbing_scope
        FROM projects pr JOIN permits p ON p.id = pr.permit_id
        WHERE pr.opportunity_score IS NOT NULL
        ORDER BY pr.opportunity_score DESC
        LIMIT ?
        """,
        (HIGH_OPPORTUNITY_LIMIT,),
    ).fetchall()
    _write_json("high_opportunity_projects.json", {
        "generated_at": utcnow_iso(),
        "projects": [dict(r) for r in rows],
    })


def export_contractors(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT name, contractor_type, cities_worked, permit_count, first_permit_date,
               last_permit_date, estimated_annual_volume, commercial_pct, residential_pct,
               avg_project_value, largest_project_value, growth_trend, opportunity_rating
        FROM contractors
        ORDER BY last_permit_date DESC
        LIMIT ?
        """,
        (CONTRACTORS_LIMIT,),
    ).fetchall()
    _write_json("contractors.json", {
        "generated_at": utcnow_iso(),
        "contractors": [dict(r) for r in rows],
    })


def export_contractor_matching(conn: sqlite3.Connection) -> None:
    """Contractors matched across 2+ connected jurisdictions — the signal
    that a contractor expanding into a new market is a live buying
    opportunity there. Also includes single-jurisdiction contractors
    (flagged accordingly) so the page can double as a general contractor
    directory, sorted multi-jurisdiction-first.
    """
    rows = conn.execute(
        """
        SELECT name, license_number, contractor_type, cities_worked,
               jurisdictions_worked, jurisdiction_breakdown, permit_count,
               first_permit_date, last_permit_date, commercial_pct,
               avg_project_value, largest_project_value, growth_trend,
               opportunity_rating
        FROM contractors
        ORDER BY
            (json_array_length(jurisdictions_worked) > 1) DESC,
            opportunity_rating DESC
        LIMIT ?
        """,
        (CONTRACTOR_MATCHING_LIMIT,),
    ).fetchall()

    contractors = []
    multi_count = 0
    for r in rows:
        d = dict(r)
        jurisdictions_worked = json.loads(d["jurisdictions_worked"] or "[]")
        d["jurisdiction_breakdown"] = json.loads(d["jurisdiction_breakdown"] or "{}")
        d["jurisdictions_worked"] = jurisdictions_worked
        d["is_multi_jurisdiction"] = len(jurisdictions_worked) > 1
        if d["is_multi_jurisdiction"]:
            multi_count += 1
        contractors.append(d)

    connected_names = [
        r["name"] for r in conn.execute("SELECT name FROM jurisdictions WHERE status = 'connected' ORDER BY name")
    ]

    _write_json("contractor_matching.json", {
        "generated_at": utcnow_iso(),
        "connected_jurisdictions": connected_names,
        "total_contractors": len(contractors),
        "multi_jurisdiction_count": multi_count,
        "contractors": contractors,
    })


def export_dashboard_summary(conn: sqlite3.Connection) -> None:
    """KPI strip for the live dashboard.

    Money tiles are scoped to scored opportunities (not the entire historical
    permit dump) so Estimated Material Value and Gross Profit are distinct,
    defensible figures — same idea as Clearport's focused district/funding
    readouts rather than a raw database sum.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    todays_new_permits = conn.execute(
        "SELECT COUNT(*) AS n FROM permits WHERE substr(issued_date, 1, 10) = ?", (today,)
    ).fetchone()["n"]

    high_opportunity_count = conn.execute(
        "SELECT COUNT(*) AS n FROM projects WHERE opportunity_score >= 85"
    ).fetchone()["n"]

    pipeline = conn.execute(
        """
        SELECT
            COUNT(*) AS project_count,
            COALESCE(SUM(estimated_material_value), 0) AS material_value_sum,
            COALESCE(SUM(estimated_gross_profit), 0) AS gross_profit_sum
        FROM projects
        WHERE opportunity_score >= ?
          AND estimated_material_value IS NOT NULL
        """,
        (REPORT_MIN_OPPORTUNITY_SCORE,),
    ).fetchone()

    category_breakdown = {
        row["project_category"] or "Unknown": row["n"]
        for row in conn.execute(
            "SELECT project_category, COUNT(*) AS n FROM projects GROUP BY project_category"
        ).fetchall()
    }

    jurisdiction_counts = conn.execute(
        "SELECT status, COUNT(*) AS n FROM jurisdictions GROUP BY status"
    ).fetchall()
    counts_by_status = {r["status"]: r["n"] for r in jurisdiction_counts}
    connected = counts_by_status.get("connected", 0)
    pending = counts_by_status.get("pending", 0)

    material = round(pipeline["material_value_sum"], 2)
    profit = round(pipeline["gross_profit_sum"], 2)

    _write_json("dashboard_summary.json", {
        "generated_at": utcnow_iso(),
        "todays_new_permits_count": todays_new_permits,
        "high_opportunity_count": high_opportunity_count,
        "pipeline_project_count": pipeline["project_count"],
        "pipeline_score_floor": REPORT_MIN_OPPORTUNITY_SCORE,
        "category_breakdown": category_breakdown,
        "totals": {
            # Material = estimated buyable plumbing scope on scored jobs.
            "estimated_material_value_sum": material,
            # Kept for older clients; same number as material — UI no longer
            # shows a duplicate "Potential revenue" tile.
            "potential_revenue_sum": material,
            "potential_gross_profit_sum": profit,
        },
        "jurisdictions_connected_count": connected,
        "jurisdictions_pending_count": pending,
        "jurisdictions_tracked_count": connected + pending,
    })


def export_all(conn: sqlite3.Connection) -> None:
    export_jurisdictions_status(conn)
    export_permits_recent(conn)
    export_lifecycle_pipeline(conn)
    export_high_opportunity_projects(conn)
    export_contractors(conn)
    export_contractor_matching(conn)
    export_dashboard_summary(conn)
    export_job_briefs(conn)


def export_all_permits_csv(conn: sqlite3.Connection):
    """Full permit archive for record-keeping (CSV).

    Writes:
      - data/exports/all_permits.csv             (always current snapshot)
      - data/exports/all_permits_YYYY-MM-DD.csv  (dated archive copy)

    Includes scoring/material fields from `projects` when present.
    """
    import csv

    DATA_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """
        SELECT
            p.id,
            p.jurisdiction,
            p.permit_number,
            p.permit_type,
            p.permit_subtype,
            p.status,
            p.description,
            p.filed_date,
            p.issued_date,
            p.expiration_date,
            p.finaled_date,
            p.inspection_status,
            p.last_inspection_date,
            p.job_address,
            p.city,
            p.state,
            p.zip,
            p.parcel_number,
            p.apn,
            p.latitude,
            p.longitude,
            p.owner_name,
            p.general_contractor_name,
            p.plumbing_contractor_name,
            p.contractor_license_number,
            p.valuation,
            p.square_footage,
            p.occupancy_type,
            p.permit_url,
            p.public_notes,
            p.first_seen_at,
            p.last_updated_at,
            pr.project_category,
            pr.construction_stage,
            pr.project_lifecycle,
            pr.opportunity_date,
            pr.opportunity_date_basis,
            pr.opportunity_timing,
            pr.opportunity_score,
            pr.confidence_score,
            pr.estimated_material_value,
            pr.estimated_gross_profit,
            pr.estimated_plumbing_scope
        FROM permits p
        LEFT JOIN projects pr ON pr.permit_id = p.id
        ORDER BY p.issued_date DESC, p.id DESC
        """
    ).fetchall()

    fieldnames = [
        "id",
        "jurisdiction",
        "permit_number",
        "permit_type",
        "permit_subtype",
        "status",
        "description",
        "filed_date",
        "issued_date",
        "expiration_date",
        "finaled_date",
        "inspection_status",
        "last_inspection_date",
        "job_address",
        "city",
        "state",
        "zip",
        "parcel_number",
        "apn",
        "latitude",
        "longitude",
        "owner_name",
        "general_contractor_name",
        "plumbing_contractor_name",
        "contractor_license_number",
        "valuation",
        "square_footage",
        "occupancy_type",
        "permit_url",
        "public_notes",
        "first_seen_at",
        "last_updated_at",
        "project_category",
        "construction_stage",
        "project_lifecycle",
        "opportunity_date",
        "opportunity_date_basis",
        "opportunity_timing",
        "opportunity_score",
        "confidence_score",
        "estimated_material_value",
        "estimated_gross_profit",
        "estimated_plumbing_scope",
    ]

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    latest = DATA_EXPORTS_DIR / "all_permits.csv"
    dated = DATA_EXPORTS_DIR / f"all_permits_{stamp}.csv"

    def _write(path) -> None:
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row[k] for k in fieldnames})

    _write(latest)
    _write(dated)
    return [latest, dated]


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    export_all(connection)
    print(f"Exported JSON snapshots to {DATA_EXPORTS_DIR}")
    connection.close()
