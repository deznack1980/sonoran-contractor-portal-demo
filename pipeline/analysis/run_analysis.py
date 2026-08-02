"""Runs the rule-based analysis engine over every permit, writing
`projects` + `estimated_materials` rows.

Raw ``permits.status`` and ``permits.permit_type`` are never overwritten —
normalization is derived at analysis time only. Unknown values are queued
in the Municipal Knowledge Engine for human review.
"""

import sqlite3

from pipeline.analysis.category_rules import classify_category_with_rule
from pipeline.analysis.materials_rubric import estimate_materials
from pipeline.analysis.scoring import (
    attach_normalization_diagnostics,
    compute_confidence_score,
    compute_construction_stage,
    compute_estimated_gross_profit,
    compute_estimated_material_value,
    compute_opportunity_date,
    compute_opportunity_score,
    compute_opportunity_timing,
    compute_project_lifecycle,
)
from pipeline.config.settings import ANALYSIS_VERSION, DATA_EXPORTS_DIR
from pipeline.connectors.base import utcnow_iso
from pipeline.knowledge.engine import KnowledgeEngine, export_knowledge_snapshot
from pipeline.knowledge.seed import seed_knowledge_base


def analyze_permit(permit_row: dict, knowledge: KnowledgeEngine | None = None) -> dict:
    """Pure analysis: permit dict -> analysis result dict.

    Does not mutate permit_row. Raw status / permit_type remain unchanged.
    """
    municipality = (
        permit_row.get("municipality")
        or permit_row.get("jurisdiction_name")
        or permit_row.get("jurisdiction")
    )
    project_category, category_rule = classify_category_with_rule(
        permit_row.get("permit_type"),
        permit_row.get("description"),
        municipality=municipality,
        knowledge=knowledge,
    )
    construction_stage = compute_construction_stage(
        permit_row.get("status"), municipality, knowledge
    )
    project_lifecycle = compute_project_lifecycle(
        permit_row.get("status"), municipality, knowledge
    )
    opportunity_date, opportunity_date_basis = compute_opportunity_date(permit_row)
    opportunity_timing = compute_opportunity_timing(project_lifecycle)
    opportunity_score = compute_opportunity_score(
        permit_row, project_category, knowledge
    )
    confidence_score = compute_confidence_score(permit_row)
    estimated_material_value = compute_estimated_material_value(
        permit_row.get("valuation"), permit_row.get("square_footage"), project_category
    )
    estimated_gross_profit = compute_estimated_gross_profit(
        estimated_material_value, project_category
    )
    materials = estimate_materials(
        project_category, permit_row.get("permit_type"), permit_row.get("description")
    )

    scope_summary = ", ".join(m.material_name for m in materials[:4]) if materials else None
    diagnostics = attach_normalization_diagnostics(
        permit_row, project_category, category_rule, knowledge
    )

    return {
        "project_category": project_category,
        "construction_stage": construction_stage,
        "project_lifecycle": project_lifecycle,
        "opportunity_date": opportunity_date,
        "opportunity_date_basis": opportunity_date_basis,
        "opportunity_timing": opportunity_timing,
        "estimated_plumbing_scope": scope_summary,
        "estimated_material_value": estimated_material_value,
        "estimated_gross_profit": estimated_gross_profit,
        "opportunity_score": opportunity_score,
        "confidence_score": confidence_score,
        "materials": materials,
        "normalization": diagnostics,
    }


def _store_analysis(conn: sqlite3.Connection, permit_row: dict, result: dict, now: str) -> None:
    """Persist one analysis result into projects + estimated_materials.

    Idempotent per permit (ON CONFLICT(permit_id)). Raw permit fields are never
    written here — scoring never mutates ingested data.
    """
    conn.execute(
        """
        INSERT INTO projects (permit_id, jurisdiction, project_category, construction_stage,
                               project_lifecycle, opportunity_date, opportunity_date_basis,
                               opportunity_timing,
                               estimated_plumbing_scope, estimated_material_value,
                               estimated_gross_profit, opportunity_score, confidence_score,
                               analysis_version, analyzed_at)
        VALUES (:permit_id, :jurisdiction, :project_category, :construction_stage,
                :project_lifecycle, :opportunity_date, :opportunity_date_basis,
                :opportunity_timing,
                :estimated_plumbing_scope, :estimated_material_value,
                :estimated_gross_profit, :opportunity_score, :confidence_score,
                :analysis_version, :analyzed_at)
        ON CONFLICT(permit_id) DO UPDATE SET
            project_category=excluded.project_category,
            construction_stage=excluded.construction_stage,
            project_lifecycle=excluded.project_lifecycle,
            opportunity_date=excluded.opportunity_date,
            opportunity_date_basis=excluded.opportunity_date_basis,
            opportunity_timing=excluded.opportunity_timing,
            estimated_plumbing_scope=excluded.estimated_plumbing_scope,
            estimated_material_value=excluded.estimated_material_value,
            estimated_gross_profit=excluded.estimated_gross_profit,
            opportunity_score=excluded.opportunity_score,
            confidence_score=excluded.confidence_score,
            analysis_version=excluded.analysis_version,
            analyzed_at=excluded.analyzed_at
        """,
        {
            "permit_id": permit_row["id"],
            "jurisdiction": permit_row["jurisdiction"],
            "project_category": result["project_category"],
            "construction_stage": result["construction_stage"],
            "project_lifecycle": result["project_lifecycle"],
            "opportunity_date": result["opportunity_date"],
            "opportunity_date_basis": result["opportunity_date_basis"],
            "opportunity_timing": result["opportunity_timing"],
            "estimated_plumbing_scope": result["estimated_plumbing_scope"],
            "estimated_material_value": result["estimated_material_value"],
            "estimated_gross_profit": result["estimated_gross_profit"],
            "opportunity_score": result["opportunity_score"],
            "confidence_score": result["confidence_score"],
            "analysis_version": ANALYSIS_VERSION,
            "analyzed_at": now,
        },
    )

    project_id = conn.execute(
        "SELECT id FROM projects WHERE permit_id = ?", (permit_row["id"],)
    ).fetchone()["id"]

    conn.execute("DELETE FROM estimated_materials WHERE project_id = ?", (project_id,))
    for material in result["materials"]:
        conn.execute(
            """
            INSERT INTO estimated_materials (project_id, material_name, confidence_pct, rationale)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, material.material_name, material.confidence_pct, material.rationale),
        )


def _finalize_knowledge(conn: sqlite3.Connection, knowledge: KnowledgeEngine) -> None:
    """Flush the unknown-value queue, re-rank it, and snapshot for the dashboard."""
    knowledge.flush_queue(conn)
    conn.commit()

    from pipeline.knowledge.priority import recompute_priorities

    recompute_priorities(conn)

    DATA_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    import json

    snapshot = export_knowledge_snapshot(conn)
    (DATA_EXPORTS_DIR / "knowledge_queue.json").write_text(
        json.dumps(snapshot, indent=2), encoding="utf-8"
    )


def _analyze_rows(conn: sqlite3.Connection, permits, knowledge: KnowledgeEngine, now: str) -> int:
    analyzed_count = 0
    for permit in permits:
        permit_row = dict(permit)
        raw_status = permit_row.get("status")
        raw_permit_type = permit_row.get("permit_type")

        result = analyze_permit(permit_row, knowledge)

        assert permit_row.get("status") == raw_status
        assert permit_row.get("permit_type") == raw_permit_type

        _store_analysis(conn, permit_row, result, now)
        analyzed_count += 1
    return analyzed_count


def run_analysis(conn: sqlite3.Connection) -> int:
    seed_knowledge_base(conn)
    knowledge = KnowledgeEngine(conn)

    permits = conn.execute("SELECT * FROM permits").fetchall()
    now = utcnow_iso()
    analyzed_count = _analyze_rows(conn, permits, knowledge, now)

    _finalize_knowledge(conn, knowledge)
    return analyzed_count


def run_analysis_for_permits(conn: sqlite3.Connection, permit_ids) -> int:
    """Incremental analysis: (re)score only the given permit ids.

    Used by the morning refresh so a run that touched a handful of permits does
    not recompute the entire dataset. Uses the exact same scoring/lifecycle
    logic as the full ``run_analysis`` — no scoring weights change. Returns the
    number of permits analyzed.
    """
    permit_ids = [int(pid) for pid in permit_ids]
    if not permit_ids:
        return 0

    seed_knowledge_base(conn)
    knowledge = KnowledgeEngine(conn)
    now = utcnow_iso()

    analyzed_count = 0
    # Chunk the IN () clause to stay well under SQLite's variable limit.
    for start in range(0, len(permit_ids), 500):
        chunk = permit_ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT * FROM permits WHERE id IN ({placeholders})", chunk
        ).fetchall()
        analyzed_count += _analyze_rows(conn, rows, knowledge, now)

    _finalize_knowledge(conn, knowledge)
    return analyzed_count


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    n = run_analysis(connection)
    print(f"Analyzed {n} permits.")
    connection.close()
