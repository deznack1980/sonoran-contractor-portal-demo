"""Full rebuild of the `contractors` table from permits + projects.

Runs after analysis, before export, every pipeline run. Contractors are
fully derived data — cheap to regenerate at this volume — so this deletes
and re-inserts rather than incrementally upserting, then re-links
projects.contractor_id in the same transaction so the whole step is atomic
and idempotent.
"""

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


def normalize_contractor_name(raw_name: str) -> str:
    name = raw_name.strip().upper()
    name = re.sub(r"[.,]", "", name)
    name = re.sub(r"\s+", " ", name)
    name = _SUFFIX_RE.sub("", name).strip()
    return name


def _parse_date(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def rebuild_contractors(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT p.id AS permit_id, p.jurisdiction, p.city, p.issued_date,
               p.general_contractor_name, p.plumbing_contractor_name,
               p.contractor_license_number, p.valuation,
               pr.id AS project_id, pr.project_category, pr.estimated_material_value
        FROM permits p
        LEFT JOIN projects pr ON pr.permit_id = p.id
        """
    ).fetchall()

    groups: dict[str, dict] = {}

    for row in rows:
        raw_name = row["plumbing_contractor_name"] or row["general_contractor_name"]
        if not raw_name or not raw_name.strip():
            continue

        normalized = normalize_contractor_name(raw_name)
        if not normalized:
            continue

        # Dedup key is normalized_name ALONE, not (name, license_number).
        # License numbers are inconsistently present across jurisdictions
        # (e.g. Mesa's public data has none, Tempe's does) — keying on both
        # would fragment the same real contractor into separate rows per
        # jurisdiction, which defeats cross-jurisdiction matching entirely.
        # This is a known, disclosed limitation (see README "Status"): two
        # unrelated companies sharing a generic normalized name would
        # over-merge. Acceptable trade-off at this scale; revisit with
        # fuzzy/license-aware matching if it gets noisy.
        key = normalized

        group = groups.setdefault(
            key,
            {
                "name": raw_name.strip(),
                "normalized_name": normalized,
                "license_number": None,
                "contractor_type": "plumbing" if row["plumbing_contractor_name"] else "general",
                "cities": set(),
                "jurisdiction_counts": defaultdict(int),
                "permits": [],
            },
        )
        if row["city"]:
            group["cities"].add(row["city"])
        if row["contractor_license_number"] and not group["license_number"]:
            group["license_number"] = row["contractor_license_number"]
        group["jurisdiction_counts"][row["jurisdiction"]] += 1
        group["permits"].append(row)

    # projects.contractor_id references contractors(id) — null it out first so
    # the rebuild's DELETE doesn't violate the foreign key constraint.
    conn.execute("UPDATE projects SET contractor_id = NULL")
    conn.execute("DELETE FROM contractors")

    now = utcnow_iso()
    now_dt = datetime.now(timezone.utc)
    inserted = 0

    for normalized, group in groups.items():
        license_number = group["license_number"]
        permits = group["permits"]
        permit_count = len(permits)

        dated = [(p, _parse_date(p["issued_date"])) for p in permits]
        dates = [d for _, d in dated if d is not None]
        first_permit_date = min(dates).isoformat() if dates else None
        last_permit_date = max(dates).isoformat() if dates else None

        commercial = sum(1 for p in permits if p["project_category"] not in ("Residential", None))
        residential = permit_count - commercial
        commercial_pct = round(100.0 * commercial / permit_count, 1) if permit_count else 0.0
        residential_pct = round(100.0 - commercial_pct, 1) if permit_count else 0.0

        values = [p["estimated_material_value"] for p in permits if p["estimated_material_value"]]
        avg_project_value = round(sum(values) / len(values), 2) if values else None
        largest = max(permits, key=lambda p: p["estimated_material_value"] or 0, default=None)
        largest_project_value = largest["estimated_material_value"] if largest else None
        largest_project_permit_id = largest["permit_id"] if largest and largest["estimated_material_value"] else None

        cutoff_recent = now_dt - timedelta(days=90)
        cutoff_prior = now_dt - timedelta(days=180)
        recent_count = sum(1 for _, d in dated if d and d >= cutoff_recent)
        prior_count = sum(1 for _, d in dated if d and cutoff_prior <= d < cutoff_recent)
        if recent_count > prior_count:
            growth_trend = "increasing"
        elif recent_count < prior_count:
            growth_trend = "declining"
        else:
            growth_trend = "flat"

        recent_365 = [p["estimated_material_value"] or 0 for p, d in dated if d and d >= now_dt - timedelta(days=365)]
        estimated_annual_volume = round(sum(recent_365), 2) if recent_365 else None

        scale_score = score_scale(avg_project_value, None)
        recency_score = score_recency(last_permit_date, None) if last_permit_date else 0.0
        commercial_score = commercial_pct
        opportunity_rating = round(
            0.35 * scale_score + 0.35 * recency_score + 0.30 * commercial_score, 1
        )

        jurisdiction_breakdown = dict(group["jurisdiction_counts"])

        conn.execute(
            """
            INSERT INTO contractors (name, normalized_name, license_number, contractor_type,
                                      cities_worked, jurisdictions_worked, jurisdiction_breakdown,
                                      permit_count, first_permit_date, last_permit_date,
                                      estimated_annual_volume, commercial_permit_count,
                                      residential_permit_count, commercial_pct, residential_pct,
                                      avg_project_value, largest_project_value, largest_project_permit_id,
                                      growth_trend, opportunity_rating, updated_at)
            VALUES (:name, :normalized_name, :license_number, :contractor_type,
                    :cities_worked, :jurisdictions_worked, :jurisdiction_breakdown,
                    :permit_count, :first_permit_date, :last_permit_date,
                    :estimated_annual_volume, :commercial_permit_count,
                    :residential_permit_count, :commercial_pct, :residential_pct,
                    :avg_project_value, :largest_project_value, :largest_project_permit_id,
                    :growth_trend, :opportunity_rating, :updated_at)
            """,
            {
                "name": group["name"],
                "normalized_name": normalized,
                "license_number": license_number,
                "contractor_type": group["contractor_type"],
                "cities_worked": json.dumps(sorted(group["cities"])),
                "jurisdictions_worked": json.dumps(sorted(jurisdiction_breakdown.keys())),
                "jurisdiction_breakdown": json.dumps(jurisdiction_breakdown),
                "permit_count": permit_count,
                "first_permit_date": first_permit_date,
                "last_permit_date": last_permit_date,
                "estimated_annual_volume": estimated_annual_volume,
                "commercial_permit_count": commercial,
                "residential_permit_count": residential,
                "commercial_pct": commercial_pct,
                "residential_pct": residential_pct,
                "avg_project_value": avg_project_value,
                "largest_project_value": largest_project_value,
                "largest_project_permit_id": largest_project_permit_id,
                "growth_trend": growth_trend,
                "opportunity_rating": opportunity_rating,
                "updated_at": now,
            },
        )
        contractor_id = conn.execute(
            "SELECT id FROM contractors WHERE normalized_name = ?",
            (normalized,),
        ).fetchone()["id"]

        project_ids = [p["project_id"] for p in permits if p["project_id"]]
        if project_ids:
            conn.executemany(
                "UPDATE projects SET contractor_id = ? WHERE id = ?",
                [(contractor_id, pid) for pid in project_ids],
            )

        inserted += 1

    conn.commit()
    return inserted


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    n = rebuild_contractors(connection)
    print(f"Rebuilt {n} contractor profiles.")
    connection.close()
