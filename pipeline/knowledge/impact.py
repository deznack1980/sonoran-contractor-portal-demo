"""Impact preview for proposed knowledge mappings.

Given a pending queue item and a proposed canonical mapping, re-scores the
affected permits with an in-memory override engine (no writes) and reports the
before/after sales impact so a reviewer can approve safely.

Scoring weights and the publish threshold are untouched — this only *previews*
what the existing engine would produce once the mapping is active.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from pipeline.analysis.run_analysis import analyze_permit
from pipeline.config.settings import REPORT_MIN_OPPORTUNITY_SCORE
from pipeline.knowledge.engine import KnowledgeEngine, _muni_slug, _title
from pipeline.knowledge.priority import muni_candidates, _match_clause, _cutoff

MAX_EXACT_PREVIEW = 6000


def _inject_override(
    engine: KnowledgeEngine, kind: str, muni_slug: str, raw_value: str, proposed: str
) -> None:
    muni = _muni_slug(muni_slug)
    raw_key = str(raw_value).strip().lower()
    proposed = str(proposed).strip().lower()
    if kind == "status":
        engine._status[(muni, raw_key)] = {
            "municipality_slug": muni,
            "raw_status": raw_value,
            "canonical_status": proposed,
            "mapping_rule": f"preview:status:{raw_key}->{proposed}",
            "confidence": 1.0,
            "human_reviewed": 1,
        }
    elif kind == "permit_code":
        engine._codes[(muni, raw_key)] = {
            "municipality_slug": muni,
            "raw_permit_code": raw_value,
            "trade": proposed,
            "project_category": _title(proposed),
            "ai_confidence": 1.0,
            "human_reviewed": 1,
        }
    elif kind == "keyword":
        engine._keywords.insert(
            0,
            {
                "keyword": raw_key,
                "category": _title(proposed),
                "trade": proposed,
                "suggested_products": None,
                "weight": 1.0,
                "confidence": 1.0,
            },
        )
    else:
        raise ValueError(f"unsupported kind {kind}")


def _fetch_affected(
    conn: sqlite3.Connection, kind: str, muni_slug: str | None, raw_value: str
) -> list[sqlite3.Row]:
    predicate, _ = _match_clause(kind)
    candidates = muni_candidates(muni_slug)
    where = [predicate]
    params: list = [raw_value]
    if candidates is not None:
        placeholders = ",".join("?" for _ in candidates)
        where.append(f"p.jurisdiction IN ({placeholders})")
        params.extend(candidates)
    where_sql = " AND ".join(where)
    sql = f"""
        SELECT p.*, pr.opportunity_score AS current_score
        FROM permits p
        LEFT JOIN projects pr ON pr.permit_id = p.id
        WHERE {where_sql}
        ORDER BY substr(COALESCE(p.issued_date,''),1,10) DESC
    """
    return conn.execute(sql, params).fetchall()


def preview_impact(
    conn: sqlite3.Connection,
    *,
    kind: str,
    municipality_slug: str | None,
    raw_value: str,
    proposed_mapping: str,
    current_fallback: str | None = None,
    example_limit: int = 8,
) -> dict[str, Any]:
    """Compute before/after sales impact for a proposed mapping (read-only)."""
    threshold = REPORT_MIN_OPPORTUNITY_SCORE
    recent_cut = _cutoff(30)

    affected = _fetch_affected(conn, kind, municipality_slug, raw_value)
    total = len(affected)

    sampled = False
    sample = affected
    if total > MAX_EXACT_PREVIEW:
        sampled = True
        # Keep the most recent slice (most sales-relevant) for exact recompute.
        sample = affected[:MAX_EXACT_PREVIEW]
    scale = (total / len(sample)) if sample else 1.0

    override = KnowledgeEngine(conn)
    _inject_override(override, kind, municipality_slug, raw_value, proposed_mapping)

    sum_before = 0.0
    sum_after = 0.0
    n_scored = 0
    above_before = 0
    above_after = 0
    entering = 0
    leaving = 0
    recent_count = 0
    examples: list[dict] = []
    entering_examples: list[dict] = []

    for row in sample:
        permit = dict(row)
        before = row["current_score"]
        after = analyze_permit(permit, override)["opportunity_score"]
        if before is None:
            before = after  # no production score yet; treat as neutral delta
        sum_before += before
        sum_after += after
        n_scored += 1
        b_ok = before >= threshold
        a_ok = after >= threshold
        above_before += 1 if b_ok else 0
        above_after += 1 if a_ok else 0
        if not b_ok and a_ok:
            entering += 1
        elif b_ok and not a_ok:
            leaving += 1
        issued = (permit.get("issued_date") or "")[:10]
        if issued and issued >= recent_cut:
            recent_count += 1

        example = {
            "permit_number": permit.get("permit_number"),
            "jurisdiction": permit.get("jurisdiction"),
            "city": permit.get("city"),
            "raw_status": permit.get("status"),
            "raw_permit_type": permit.get("permit_type"),
            "description": (permit.get("description") or "")[:120],
            "issued_date": issued or None,
            "score_before": round(float(before), 1),
            "score_after": round(float(after), 1),
            "delta": round(float(after) - float(before), 1),
        }
        if (not b_ok and a_ok) or (b_ok and not a_ok):
            entering_examples.append(example)
        examples.append(example)

    avg_before = round(sum_before / n_scored, 1) if n_scored else 0.0
    avg_after = round(sum_after / n_scored, 1) if n_scored else 0.0

    # Prefer permits that cross the threshold as representative examples.
    entering_examples.sort(key=lambda e: abs(e["delta"]), reverse=True)
    examples.sort(key=lambda e: e["score_after"], reverse=True)
    chosen: list[dict] = []
    seen = set()
    for e in entering_examples + examples:
        pn = e["permit_number"]
        if pn in seen:
            continue
        seen.add(pn)
        chosen.append(e)
        if len(chosen) >= example_limit:
            break

    def _scaled(n: int) -> int:
        return int(round(n * scale)) if sampled else n

    material = entering > 0 or leaving > 0

    return {
        "kind": kind,
        "municipality_slug": municipality_slug,
        "raw_value": raw_value,
        "proposed_mapping": proposed_mapping,
        "current_fallback": current_fallback
        or ("unknown" if kind == "status" else "Other"),
        "threshold": threshold,
        "affected_permit_count": total,
        "affected_recent_count": _scaled(recent_count),
        "affected_active_count": total,
        "sampled": sampled,
        "sample_size": len(sample),
        "avg_score_before": avg_before,
        "avg_score_after": avg_after,
        "above_before": _scaled(above_before),
        "above_after": _scaled(above_after),
        "entering_queue": _scaled(entering),
        "leaving_queue": _scaled(leaving),
        "net_queue_change": _scaled(entering - leaving),
        "material_change": material,
        "examples": chosen,
    }
