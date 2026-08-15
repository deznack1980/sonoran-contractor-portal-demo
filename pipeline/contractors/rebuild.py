"""Verified-company rebuild of the legacy ``contractors`` compatibility table.

``companies`` and ``permits.contractor_company_id`` are the canonical identity
layer.  The legacy table remains because exports, reports, and
``projects.contractor_id`` still consume it, but it must never independently
infer a contractor from a raw permit name.

The rebuild is atomic and idempotent.  It is intentionally destructive only to
fully-derived compatibility rows; production callers must create and review a
database backup before running it manually.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from pipeline.analysis.scoring import score_recency, score_scale
from pipeline.connectors.base import utcnow_iso

_SUFFIX_RE = re.compile(
    r"\b(LLC|L\.L\.C\.?|INC|INCORPORATED|CO|CORP|CORPORATION|LTD|LP|PLC|PC)\.?\s*$",
    re.IGNORECASE,
)
_RESIDENTIAL = {"Residential", "Apartment"}


def normalize_contractor_name(raw_name: str) -> str:
    name = raw_name.strip().upper()
    name = re.sub(r"[.,]", "", name)
    name = re.sub(r"\s+", " ", name)
    return _SUFFIX_RE.sub("", name).strip()


def _parse_date(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _verified_rows(conn: sqlite3.Connection):
    """Return permit activity only for active, explicitly verified companies."""
    return conn.execute(
        """
        SELECT c.id AS company_id, c.display_name, c.legal_name,
               c.normalized_name AS company_normalized_name,
               c.license_number AS company_license_number,
               c.lead_verification_status,
               c.lead_source, c.why_this_lead,
               clc.source AS classification_source,
               clc.why_this_lead AS classification_rule,
               clc.classification_version,
               clc.explicit_contractor_permits,
               CASE WHEN TRIM(COALESCE(c.main_phone,''))<>''
                          OR TRIM(COALESCE(c.main_email,''))<>''
                          OR TRIM(COALESCE(c.website,''))<>''
                          OR EXISTS (
                              SELECT 1 FROM contacts ct
                              WHERE ct.company_id=c.id
                                AND (TRIM(COALESCE(ct.phone,''))<>''
                                     OR TRIM(COALESCE(ct.mobile_phone,''))<>''
                                     OR TRIM(COALESCE(ct.email,''))<>'')
                          ) THEN 1 ELSE 0 END AS has_contact_info,
               p.id AS permit_id, p.jurisdiction, p.city, p.issued_date,
               p.general_contractor_name, p.plumbing_contractor_name,
               p.contractor_license_number, p.valuation,
               p.contractor_source_role, p.contractor_source_field,
               p.contractor_evidence_confidence,
               p.contractor_verification_status,
               pr.id AS project_id, pr.project_category,
               pr.estimated_material_value
        FROM companies c
        JOIN permits p ON p.contractor_company_id=c.id
        LEFT JOIN projects pr ON pr.permit_id=p.id
        LEFT JOIN company_lead_classification clc ON clc.company_id=c.id
        WHERE c.lifecycle_state='active'
          AND c.lead_type='verified_contractor'
          AND COALESCE(c.lead_verification_status,clc.verification_status)='verified'
        ORDER BY c.id,p.id
        """
    ).fetchall()


def _group_verified_rows(rows) -> dict[int, dict]:
    groups: dict[int, dict] = {}
    for row in rows:
        company_id = int(row["company_id"])
        group = groups.setdefault(
            company_id,
            {
                "company_id": company_id,
                "name": (row["display_name"] or row["legal_name"]
                         or row["company_normalized_name"] or f"Verified company {company_id}").strip(),
                "normalized_name": normalize_contractor_name(
                    row["company_normalized_name"] or row["display_name"] or row["legal_name"] or ""
                ),
                "license_number": row["company_license_number"],
                "cities": set(),
                "jurisdiction_counts": defaultdict(int),
                "permits": [],
                "has_contact_info": int(row["has_contact_info"] or 0),
                "verification_status": row["lead_verification_status"] or "verified",
                "classification_source": row["classification_source"] or row["lead_source"],
                "classification_rule": row["classification_rule"] or row["why_this_lead"],
                "classification_version": row["classification_version"],
                "contractor_evidence_count": int(row["explicit_contractor_permits"] or 0),
            },
        )
        if row["city"]:
            group["cities"].add(row["city"])
        if row["jurisdiction"]:
            group["jurisdiction_counts"][row["jurisdiction"]] += 1
        if row["contractor_license_number"] and not group["license_number"]:
            group["license_number"] = row["contractor_license_number"]
        group["permits"].append(row)
    return groups


def _compatibility_name(base: str, company_id: int, used: set[str]) -> str:
    """Honor the legacy unique name key without merging distinct companies."""
    candidate = base or f"VERIFIED COMPANY {company_id}"
    if candidate not in used:
        used.add(candidate)
        return candidate
    candidate = f"{candidate} [COMPANY {company_id}]"
    used.add(candidate)
    return candidate


def _metrics(group: dict, now_dt: datetime) -> dict:
    permits = group["permits"]
    permit_count = len(permits)
    dated = [(p, _parse_date(p["issued_date"])) for p in permits]
    dates = [date for _, date in dated if date is not None]
    first_permit_date = min(dates).isoformat() if dates else None
    last_permit_date = max(dates).isoformat() if dates else None

    commercial = sum(1 for p in permits if p["project_category"] and p["project_category"] not in _RESIDENTIAL)
    residential = sum(1 for p in permits if p["project_category"] in _RESIDENTIAL)
    commercial_pct = round(100.0 * commercial / permit_count, 1) if permit_count else 0.0
    residential_pct = round(100.0 * residential / permit_count, 1) if permit_count else 0.0

    project_values = [float(p["valuation"]) for p in permits if p["valuation"] is not None]
    avg_project_value = round(sum(project_values) / len(project_values), 2) if project_values else None
    largest = max(permits, key=lambda p: p["valuation"] or 0, default=None)
    largest_project_value = float(largest["valuation"]) if largest and largest["valuation"] is not None else None
    largest_project_permit_id = largest["permit_id"] if largest_project_value is not None else None

    cutoff_recent = now_dt - timedelta(days=90)
    cutoff_prior = now_dt - timedelta(days=180)
    recent_count = sum(1 for _, date in dated if date and date >= cutoff_recent)
    prior_count = sum(1 for _, date in dated if date and cutoff_prior <= date < cutoff_recent)
    growth_trend = "increasing" if recent_count > prior_count else (
        "declining" if recent_count < prior_count else "flat"
    )

    materials = [float(p["estimated_material_value"]) for p in permits if p["estimated_material_value"] is not None]
    recent_materials = [
        float(p["estimated_material_value"])
        for p, date in dated
        if date and date >= now_dt - timedelta(days=365)
        and p["estimated_material_value"] is not None
    ]
    estimated_annual_volume = round(sum(recent_materials), 2) if recent_materials else None
    estimated_material_opportunity = round(sum(materials), 2) if materials else None

    scale_score = score_scale(avg_project_value, None)
    recency_score = score_recency(last_permit_date, None) if last_permit_date else 0.0
    opportunity_rating = round(
        0.35 * scale_score + 0.35 * recency_score + 0.30 * commercial_pct, 1
    )
    jurisdiction_breakdown = dict(sorted(group["jurisdiction_counts"].items()))
    contractor_type = "plumbing" if any(
        str(p["plumbing_contractor_name"] or "").strip() for p in permits
    ) else "general"
    return {
        "permit_count": permit_count,
        "first_permit_date": first_permit_date,
        "last_permit_date": last_permit_date,
        "estimated_annual_volume": estimated_annual_volume,
        "estimated_material_opportunity": estimated_material_opportunity,
        "commercial_permit_count": commercial,
        "residential_permit_count": residential,
        "commercial_pct": commercial_pct,
        "residential_pct": residential_pct,
        "avg_project_value": avg_project_value,
        "largest_project_value": largest_project_value,
        "largest_project_permit_id": largest_project_permit_id,
        "growth_trend": growth_trend,
        "opportunity_rating": opportunity_rating,
        "jurisdiction_breakdown": jurisdiction_breakdown,
        "contractor_type": contractor_type,
    }


def _update_company_intelligence(conn: sqlite3.Connection, group: dict, metrics: dict, now: str) -> None:
    jurisdictions = sorted(metrics["jurisdiction_breakdown"])
    conn.execute(
        """
        INSERT INTO company_intelligence
            (company_id,contractor_permit_count,contractor_jurisdictions_worked,
             contractor_jurisdiction_breakdown,contractor_commercial_pct,
             contractor_average_project_value,contractor_last_permit_date,
             contractor_estimated_annual_volume,contractor_estimated_material_opportunity,
             contractor_opportunity_rating,contractor_has_contact_info,
             contractor_verification_status,contractor_classification_source,
             contractor_classification_rule,contractor_classification_version,
             contractor_metrics_calculated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(company_id) DO UPDATE SET
             contractor_permit_count=excluded.contractor_permit_count,
             contractor_jurisdictions_worked=excluded.contractor_jurisdictions_worked,
             contractor_jurisdiction_breakdown=excluded.contractor_jurisdiction_breakdown,
             contractor_commercial_pct=excluded.contractor_commercial_pct,
             contractor_average_project_value=excluded.contractor_average_project_value,
             contractor_last_permit_date=excluded.contractor_last_permit_date,
             contractor_estimated_annual_volume=excluded.contractor_estimated_annual_volume,
             contractor_estimated_material_opportunity=excluded.contractor_estimated_material_opportunity,
             contractor_opportunity_rating=excluded.contractor_opportunity_rating,
             contractor_has_contact_info=excluded.contractor_has_contact_info,
             contractor_verification_status=excluded.contractor_verification_status,
             contractor_classification_source=excluded.contractor_classification_source,
             contractor_classification_rule=excluded.contractor_classification_rule,
             contractor_classification_version=excluded.contractor_classification_version,
             contractor_metrics_calculated_at=excluded.contractor_metrics_calculated_at
        """,
        (
            group["company_id"], metrics["permit_count"], json.dumps(jurisdictions),
            json.dumps(metrics["jurisdiction_breakdown"]), metrics["commercial_pct"],
            metrics["avg_project_value"], metrics["last_permit_date"],
            metrics["estimated_annual_volume"], metrics["estimated_material_opportunity"],
            metrics["opportunity_rating"], group["has_contact_info"],
            group["verification_status"], group["classification_source"],
            group["classification_rule"], group["classification_version"], now,
        ),
    )


def rebuild_contractors(conn: sqlite3.Connection) -> int:
    """Atomically rebuild compatibility rows from verified canonical companies."""
    groups = _group_verified_rows(_verified_rows(conn))
    now = utcnow_iso()
    now_dt = datetime.now(timezone.utc)

    contractor_metric_columns = (
        "contractor_permit_count=NULL, contractor_jurisdictions_worked=NULL, "
        "contractor_jurisdiction_breakdown=NULL, contractor_commercial_pct=NULL, "
        "contractor_average_project_value=NULL, contractor_last_permit_date=NULL, "
        "contractor_estimated_annual_volume=NULL, contractor_estimated_material_opportunity=NULL, "
        "contractor_opportunity_rating=NULL, contractor_has_contact_info=NULL, "
        "contractor_verification_status=NULL, contractor_classification_source=NULL, "
        "contractor_classification_rule=NULL, contractor_classification_version=NULL, "
        "contractor_metrics_calculated_at=NULL"
    )

    with conn:
        conn.execute("UPDATE projects SET contractor_id=NULL")
        conn.execute("DELETE FROM contractors")
        conn.execute(f"UPDATE company_intelligence SET {contractor_metric_columns}")
        used_names: set[str] = set()

        for company_id in sorted(groups):
            group = groups[company_id]
            metrics = _metrics(group, now_dt)
            normalized = _compatibility_name(group["normalized_name"], company_id, used_names)
            conn.execute(
                """
                INSERT INTO contractors
                    (company_id,name,normalized_name,license_number,contractor_type,
                     cities_worked,jurisdictions_worked,jurisdiction_breakdown,
                     permit_count,first_permit_date,last_permit_date,estimated_annual_volume,
                     commercial_permit_count,residential_permit_count,commercial_pct,residential_pct,
                     avg_project_value,largest_project_value,largest_project_permit_id,
                     growth_trend,opportunity_rating,estimated_material_opportunity,
                     has_contact_info,verification_status,classification_source,
                     classification_rule,classification_version,contractor_evidence_count,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    company_id, group["name"], normalized, group["license_number"],
                    metrics["contractor_type"], json.dumps(sorted(group["cities"])),
                    json.dumps(sorted(metrics["jurisdiction_breakdown"])),
                    json.dumps(metrics["jurisdiction_breakdown"]), metrics["permit_count"],
                    metrics["first_permit_date"], metrics["last_permit_date"],
                    metrics["estimated_annual_volume"], metrics["commercial_permit_count"],
                    metrics["residential_permit_count"], metrics["commercial_pct"],
                    metrics["residential_pct"], metrics["avg_project_value"],
                    metrics["largest_project_value"], metrics["largest_project_permit_id"],
                    metrics["growth_trend"], metrics["opportunity_rating"],
                    metrics["estimated_material_opportunity"], group["has_contact_info"],
                    group["verification_status"], group["classification_source"],
                    group["classification_rule"], group["classification_version"],
                    group["contractor_evidence_count"], now,
                ),
            )
            contractor_id = conn.execute(
                "SELECT id FROM contractors WHERE company_id=?", (company_id,)
            ).fetchone()["id"]
            project_ids = {p["project_id"] for p in group["permits"] if p["project_id"] is not None}
            conn.executemany(
                "UPDATE projects SET contractor_id=? WHERE id=?",
                [(contractor_id, project_id) for project_id in sorted(project_ids)],
            )
            _update_company_intelligence(conn, group, metrics, now)
    return len(groups)


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    count = rebuild_contractors(connection)
    print(f"Rebuilt {count} verified contractor profiles.")
    connection.close()
