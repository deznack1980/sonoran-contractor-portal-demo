"""Review-queue priority scoring for the Municipal Knowledge Engine.

Ranks unresolved unknowns by *business impact* so reviewers work the most
valuable mappings first. Nothing here touches opportunity-score weights or the
publish threshold — it only orders the review queue.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta, timezone

from pipeline.config.settings import (
    KNOWLEDGE_ACTIVE_WINDOW_DAYS,
    KNOWLEDGE_CONFIDENCE_TIERS,
    KNOWLEDGE_PRIORITY_CAPS,
    KNOWLEDGE_PRIORITY_TIERS,
    KNOWLEDGE_PRIORITY_WEIGHTS,
)

GLOBAL_MUNI = "_global_"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cutoff(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def muni_candidates(municipality_slug: str | None) -> list[str] | None:
    """Return jurisdiction slugs a knowledge muni maps to.

    ``None`` means "all jurisdictions" (global rule). Otherwise returns the
    likely slug variants ("phoenix", "phoenix_az").
    """
    if not municipality_slug or municipality_slug == GLOBAL_MUNI:
        return None
    slug = str(municipality_slug).strip().lower()
    variants = {slug}
    if slug.endswith("_az"):
        variants.add(slug[:-3])
    else:
        variants.add(f"{slug}_az")
    return sorted(variants)


def _match_clause(kind: str) -> tuple[str, str]:
    """Return (permit_predicate, bind_expression) for a queue kind."""
    if kind == "status":
        return "LOWER(TRIM(p.status)) = LOWER(TRIM(?))", "eq"
    if kind == "permit_code":
        return "LOWER(TRIM(p.permit_type)) = LOWER(TRIM(?))", "eq"
    if kind == "keyword":
        return "LOWER(COALESCE(p.description,'')) LIKE '%' || LOWER(TRIM(?)) || '%'", "like"
    raise ValueError(f"unsupported kind {kind}")


def compute_affected(
    conn: sqlite3.Connection,
    kind: str,
    municipality_slug: str | None,
    raw_value: str,
    *,
    active_window_days: int = KNOWLEDGE_ACTIVE_WINDOW_DAYS,
) -> dict:
    """Count permits affected by an unknown value, plus their avg score."""
    predicate, _ = _match_clause(kind)
    candidates = muni_candidates(municipality_slug)

    where = [predicate]
    params: list = [raw_value]
    if candidates is not None:
        placeholders = ",".join("?" for _ in candidates)
        where.append(f"p.jurisdiction IN ({placeholders})")
        params.extend(candidates)
    where_sql = " AND ".join(where)

    recent_cut = _cutoff(30)
    active_cut = _cutoff(active_window_days)

    sql = f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN substr(COALESCE(p.issued_date,''),1,10) >= ? THEN 1 ELSE 0 END) AS recent,
            SUM(CASE WHEN substr(COALESCE(p.issued_date,''),1,10) >= ? THEN 1 ELSE 0 END) AS active,
            AVG(pr.opportunity_score) AS avg_score
        FROM permits p
        LEFT JOIN projects pr ON pr.permit_id = p.id
        WHERE {where_sql}
    """
    row = conn.execute(sql, [recent_cut, active_cut, *params]).fetchone()
    return {
        "total": int(row["total"] or 0),
        "recent": int(row["recent"] or 0),
        "active": int(row["active"] or 0),
        "avg_score": float(row["avg_score"]) if row["avg_score"] is not None else 0.0,
    }


def _log_norm(value: float, cap: float) -> float:
    if value <= 0 or cap <= 0:
        return 0.0
    return min(100.0, 100.0 * math.log10(value + 1) / math.log10(cap + 1))


def priority_tier(score: float) -> str:
    for band in KNOWLEDGE_PRIORITY_TIERS:
        if score >= band["min"]:
            return band["name"]
    return KNOWLEDGE_PRIORITY_TIERS[-1]["name"]


def confidence_tier(confidence: float | None) -> str:
    if confidence is None:
        return "Low"
    for band in KNOWLEDGE_CONFIDENCE_TIERS:
        if confidence >= band["min"]:
            return band["name"]
    return KNOWLEDGE_CONFIDENCE_TIERS[-1]["name"]


def compute_priority_score(kind: str, occurrence_count: int, affected: dict) -> float:
    """Weighted 0–100 business-impact score for a queue item."""
    w = KNOWLEDGE_PRIORITY_WEIGHTS
    caps = KNOWLEDGE_PRIORITY_CAPS

    recent_factor = _log_norm(affected["recent"], caps["recent_volume"])
    total_factor = _log_norm(
        max(affected["total"], occurrence_count), caps["total_volume"]
    )
    active_factor = _log_norm(affected["active"], caps["active_permits"])
    status_factor = 100.0 if kind == "status" else 0.0
    category_factor = 100.0 if kind in ("permit_code", "keyword") else 0.0
    high_score_factor = max(0.0, min(100.0, affected["avg_score"]))

    score = (
        w["recent_volume"] * recent_factor
        + w["total_volume"] * total_factor
        + w["active_permits"] * active_factor
        + w["status_impact"] * status_factor
        + w["category_impact"] * category_factor
        + w["high_score_impact"] * high_score_factor
    )
    return round(score, 2)


def recompute_priorities(conn: sqlite3.Connection) -> int:
    """Recalculate priority for every pending queue item and persist it."""
    now = _utcnow()
    rows = conn.execute(
        "SELECT * FROM knowledge_review_queue WHERE status='pending'"
    ).fetchall()
    updated = 0
    for row in rows:
        affected = compute_affected(
            conn, row["kind"], row["municipality_slug"], row["raw_value"]
        )
        score = compute_priority_score(
            row["kind"], int(row["occurrence_count"] or 0), affected
        )
        conn.execute(
            """
            UPDATE knowledge_review_queue
            SET priority_score=?, priority_tier=?,
                affected_permit_count=?, affected_recent_count=?,
                affected_active_count=?, affected_avg_score=?,
                priority_computed_at=?
            WHERE id=?
            """,
            (
                score,
                priority_tier(score),
                affected["total"],
                affected["recent"],
                affected["active"],
                round(affected["avg_score"], 2),
                now,
                row["id"],
            ),
        )
        updated += 1
    conn.commit()
    return updated
