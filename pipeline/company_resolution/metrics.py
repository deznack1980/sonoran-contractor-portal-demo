"""company_intelligence — derived company metrics from verified activity only.

Never fabricates spend/revenue/preferred-products. Non-calculable fields are
stored NULL. The company priority score is independent of permit scoring.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone

from pipeline.config.settings import (
    COMPANY_ACTIVE_WINDOW_DAYS,
    COMPANY_METRICS_MODEL_VERSION,
    COMPANY_PRIORITY_CAPS,
    COMPANY_PRIORITY_TIERS,
    COMPANY_PRIORITY_WEIGHTS,
)

_RESIDENTIAL = {"Residential", "Apartment"}
_ROLE_COLUMNS = (
    "contractor_company_id",
    "owner_company_id",
    "developer_company_id",
    "architect_company_id",
    "engineer_company_id",
)


def _log_norm(value: float, cap: float) -> float:
    if value <= 0:
        return 0.0
    if value >= cap:
        return 100.0
    return 100.0 * math.log10(value + 1) / math.log10(cap + 1)


def priority_tier(score: float) -> str:
    for tier in COMPANY_PRIORITY_TIERS:
        if score >= tier["min"]:
            return tier["name"]
    return COMPANY_PRIORITY_TIERS[-1]["name"]


def _priority_score(m: dict) -> float:
    w = COMPANY_PRIORITY_WEIGHTS
    caps = COMPANY_PRIORITY_CAPS
    total = max(1, m["total_projects"])
    commercial_share = (m["commercial_project_count"] / total) if total else 0.0
    growth = m.get("permit_growth_90d")
    growth_factor = 50.0 if growth is None else max(0.0, min(100.0, growth * 50.0))
    factors = {
        "recent_activity": _log_norm(m["projects_last_30_days"], caps["recent_activity"]),
        "project_volume": _log_norm(m["total_projects"], caps["project_volume"]),
        "avg_opportunity": m["average_opportunity_score"] or 0.0,
        "commercial_mix": commercial_share * 100.0,
        "geographic_footprint": _log_norm(m["municipality_count"], caps["geographic_footprint"]),
        "activity_growth": growth_factor,
    }
    score = sum(factors[k] * w[k] for k in w)
    return round(min(100.0, max(0.0, score)), 1)


def _empty_metrics() -> dict:
    return {
        "project_ids": set(),
        "permit_ids": set(),
        "jurisdictions": set(),
        "scores": [],
        "material_total": 0.0,
        "commercial_project_count": 0,
        "residential_project_count": 0,
        "active_projects": 0,
        "projects_last_7_days": 0,
        "projects_last_30_days": 0,
        "projects_last_90_days": 0,
        "first_activity_date": None,
        "latest_activity_date": None,
        "count_prev_90": 0,
    }


def compute_company_metrics(conn: sqlite3.Connection) -> int:
    """Recompute company_intelligence for every active company. Returns count."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    d7 = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    d30 = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    d90 = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    d180 = (now - timedelta(days=COMPANY_ACTIVE_WINDOW_DAYS)).strftime("%Y-%m-%d")
    d90_prev = (now - timedelta(days=180)).strftime("%Y-%m-%d")

    agg: dict[int, dict] = {}

    # One streaming pass per role FK, deduping by (company, project).
    union = " UNION ALL ".join(
        f"SELECT {col} AS company_id, pr.id AS project_id, pr.permit_id AS permit_id, "
        f"pr.jurisdiction AS jurisdiction, pr.opportunity_score AS score, "
        f"pr.project_category AS category, pr.opportunity_date AS opp_date, "
        f"pr.estimated_material_value AS material "
        f"FROM projects pr WHERE {col} IS NOT NULL"
        for col in _ROLE_COLUMNS
    )
    cursor = conn.execute(union)
    for r in cursor:
        cid = r["company_id"]
        m = agg.get(cid)
        if m is None:
            m = _empty_metrics()
            agg[cid] = m
        pid = r["project_id"]
        if pid in m["project_ids"]:
            continue  # same project in two roles for one company — count once
        m["project_ids"].add(pid)
        if r["permit_id"] is not None:
            m["permit_ids"].add(r["permit_id"])
        if r["jurisdiction"]:
            m["jurisdictions"].add(r["jurisdiction"])
        if r["score"] is not None:
            m["scores"].append(float(r["score"]))
        if r["material"] is not None:
            m["material_total"] += float(r["material"])
        category = r["category"]
        if category in _RESIDENTIAL:
            m["residential_project_count"] += 1
        elif category:
            m["commercial_project_count"] += 1
        opp = (r["opp_date"] or "")[:10]
        if opp:
            if m["first_activity_date"] is None or opp < m["first_activity_date"]:
                m["first_activity_date"] = opp
            if m["latest_activity_date"] is None or opp > m["latest_activity_date"]:
                m["latest_activity_date"] = opp
            if opp >= d180:
                m["active_projects"] += 1
            if opp >= d7:
                m["projects_last_7_days"] += 1
            if opp >= d30:
                m["projects_last_30_days"] += 1
            if opp >= d90:
                m["projects_last_90_days"] += 1
            elif d90_prev <= opp < d90:
                m["count_prev_90"] += 1

    # Remove derived metrics that no longer have any project-role evidence.
    # This matters after lead-role correction unlinks architects, engineers, and
    # generic permit professionals from contractor_company_id.
    role_match = " OR ".join(
        f"pr.{column}=company_intelligence.company_id" for column in _ROLE_COLUMNS
    )
    conn.execute(
        f"DELETE FROM company_intelligence WHERE NOT EXISTS "
        f"(SELECT 1 FROM projects pr WHERE {role_match})"
    )

    model_version = COMPANY_METRICS_MODEL_VERSION
    calculated_at = now.isoformat(timespec="seconds")
    written = 0
    for cid, m in agg.items():
        total_projects = len(m["project_ids"])
        total_permits = len(m["permit_ids"])
        scores = m["scores"]
        avg_score = round(sum(scores) / len(scores), 1) if scores else None
        high_score = round(max(scores), 1) if scores else None
        last90 = m["projects_last_90_days"]
        prev90 = m["count_prev_90"]
        growth_90d = round(last90 / prev90, 2) if prev90 > 0 else None
        if growth_90d is None:
            trend = None
        elif growth_90d >= 1.15:
            trend = "increasing"
        elif growth_90d <= 0.85:
            trend = "decreasing"
        else:
            trend = "steady"

        metrics = {
            "total_projects": total_projects,
            "projects_last_30_days": m["projects_last_30_days"],
            "commercial_project_count": m["commercial_project_count"],
            "average_opportunity_score": avg_score,
            "municipality_count": len(m["jurisdictions"]),
            "permit_growth_90d": growth_90d,
        }
        priority = _priority_score(metrics)
        tier = priority_tier(priority)

        conn.execute(
            """
            INSERT INTO company_intelligence
                (company_id, total_permits, active_permits, total_projects, active_projects,
                 projects_last_7_days, projects_last_30_days, projects_last_90_days,
                 commercial_project_count, residential_project_count, municipality_count,
                 first_activity_date, latest_activity_date, average_opportunity_score,
                 highest_opportunity_score, estimated_opportunity_total,
                 permit_growth_30d, permit_growth_90d, permit_growth_12m, activity_trend,
                 company_priority_score, company_priority_tier, metrics_calculated_at, model_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id) DO UPDATE SET
                total_permits=excluded.total_permits, active_permits=excluded.active_permits,
                total_projects=excluded.total_projects, active_projects=excluded.active_projects,
                projects_last_7_days=excluded.projects_last_7_days,
                projects_last_30_days=excluded.projects_last_30_days,
                projects_last_90_days=excluded.projects_last_90_days,
                commercial_project_count=excluded.commercial_project_count,
                residential_project_count=excluded.residential_project_count,
                municipality_count=excluded.municipality_count,
                first_activity_date=excluded.first_activity_date,
                latest_activity_date=excluded.latest_activity_date,
                average_opportunity_score=excluded.average_opportunity_score,
                highest_opportunity_score=excluded.highest_opportunity_score,
                estimated_opportunity_total=excluded.estimated_opportunity_total,
                permit_growth_30d=excluded.permit_growth_30d,
                permit_growth_90d=excluded.permit_growth_90d,
                permit_growth_12m=excluded.permit_growth_12m,
                activity_trend=excluded.activity_trend,
                company_priority_score=excluded.company_priority_score,
                company_priority_tier=excluded.company_priority_tier,
                metrics_calculated_at=excluded.metrics_calculated_at,
                model_version=excluded.model_version
            """,
            (
                cid, total_permits, m["active_projects"], total_projects, m["active_projects"],
                m["projects_last_7_days"], m["projects_last_30_days"], m["projects_last_90_days"],
                m["commercial_project_count"], m["residential_project_count"], len(m["jurisdictions"]),
                m["first_activity_date"], m["latest_activity_date"], avg_score,
                high_score, round(m["material_total"], 2) if m["material_total"] else None,
                None, growth_90d, None, trend,
                priority, tier, calculated_at, model_version,
            ),
        )
        written += 1
    conn.commit()
    return written


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    n = compute_company_metrics(connection)
    print(f"Computed intelligence metrics for {n} companies.")
    connection.close()
