"""Sprint 2.1 tests — priority scoring, impact preview, lifecycle, audit, batch."""

from __future__ import annotations

import sqlite3

import pytest

from pipeline.analysis.normalization import normalize_permit_status
from pipeline.config.settings import SCHEMA_PATH
from pipeline.knowledge.engine import KnowledgeEngine, ensure_municipality
from pipeline.knowledge.impact import preview_impact
from pipeline.knowledge.priority import (
    compute_priority_score,
    confidence_tier,
    priority_tier,
    recompute_priorities,
)


def _utc(i=0):
    return f"2026-07-2{i}T00:00:00+00:00"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Jurisdiction + municipality
    c.execute(
        "INSERT INTO jurisdictions (slug, name, state, status) "
        "VALUES ('testcity','Test City','AZ','connected')"
    )
    ensure_municipality(c, "testcity", "Test City")
    # 5 recent permits with an unknown status 'ACTIVE'
    for i in range(5):
        c.execute(
            """
            INSERT INTO permits (jurisdiction, permit_number, permit_type, status,
                description, issued_date, valuation, first_seen_at, last_updated_at)
            VALUES ('testcity', ?, 'Commercial', 'ACTIVE', 'Tenant improvement',
                    '2026-07-20', 500000, ?, ?)
            """,
            (f"P{i}", _utc(), _utc()),
        )
    # Score them once with a plain engine (ACTIVE -> unknown => low signal)
    from pipeline.analysis.run_analysis import analyze_permit

    eng = KnowledgeEngine(c)
    for row in c.execute("SELECT * FROM permits"):
        permit = dict(row)
        res = analyze_permit(permit, eng)
        c.execute(
            """
            INSERT INTO projects (permit_id, jurisdiction, project_category,
                construction_stage, opportunity_score, confidence_score,
                analysis_version, analyzed_at)
            VALUES (?, 'testcity', ?, ?, ?, ?, 'test', ?)
            """,
            (permit["id"], res["project_category"], res["construction_stage"],
             res["opportunity_score"], res["confidence_score"], _utc()),
        )
    # Queue the unknown status
    c.execute(
        """
        INSERT INTO knowledge_review_queue (kind, municipality_slug, raw_value,
            occurrence_count, first_seen, last_seen, status)
        VALUES ('status','testcity','ACTIVE', 5, ?, ?, 'pending')
        """,
        (_utc(), _utc()),
    )
    c.commit()
    return c


def test_priority_tier_bands():
    assert priority_tier(80) == "Critical"
    assert priority_tier(50) == "High"
    assert priority_tier(25) == "Medium"
    assert priority_tier(0) == "Low"


def test_confidence_tier_bands():
    assert confidence_tier(97) == "Verified"
    assert confidence_tier(85) == "High"
    assert confidence_tier(65) == "Moderate"
    assert confidence_tier(10) == "Low"
    assert confidence_tier(None) == "Low"


def test_priority_score_rewards_impact():
    low = compute_priority_score("keyword", 1, {"recent": 0, "total": 1, "active": 0, "avg_score": 0})
    high = compute_priority_score(
        "status", 5000, {"recent": 400, "total": 5000, "active": 3000, "avg_score": 55}
    )
    assert high > low


def test_recompute_priorities_populates_queue(conn):
    n = recompute_priorities(conn)
    assert n == 1
    row = conn.execute(
        "SELECT * FROM knowledge_review_queue WHERE raw_value='ACTIVE'"
    ).fetchone()
    assert row["affected_permit_count"] == 5
    assert row["priority_score"] is not None
    assert row["priority_tier"] in {"Critical", "High", "Medium", "Low"}


def test_impact_preview_raises_scores(conn):
    recompute_priorities(conn)
    pv = preview_impact(
        conn, kind="status", municipality_slug="testcity",
        raw_value="ACTIVE", proposed_mapping="issued",
    )
    assert pv["affected_permit_count"] == 5
    assert pv["avg_score_after"] > pv["avg_score_before"]
    assert pv["current_fallback"] == "unknown"
    assert len(pv["examples"]) > 0


def test_low_confidence_stays_draft_and_not_applied(conn):
    recompute_priorities(conn)
    qid = conn.execute(
        "SELECT id FROM knowledge_review_queue WHERE raw_value='ACTIVE'"
    ).fetchone()["id"]
    eng = KnowledgeEngine(conn)
    res = eng.approve_queue_item(
        conn, qid, canonical_status="issued", mapping_confidence=40, reprocess=False
    )
    assert res["lifecycle_state"] == "draft"
    # Draft mapping must NOT affect production normalization.
    eng.reload(conn)
    assert normalize_permit_status("ACTIVE", "testcity", eng) == "unknown"


def test_high_confidence_activates_reprocesses_and_audits(conn):
    recompute_priorities(conn)
    qid = conn.execute(
        "SELECT id FROM knowledge_review_queue WHERE raw_value='ACTIVE'"
    ).fetchone()["id"]
    eng = KnowledgeEngine(conn)
    before_avg = conn.execute(
        "SELECT AVG(opportunity_score) AS a FROM projects"
    ).fetchone()["a"]
    res = eng.approve_queue_item(
        conn, qid, canonical_status="issued", mapping_confidence=95,
        reviewed_by="tester", reprocess=True,
    )
    assert res["lifecycle_state"] == "active"
    assert res["reprocessed_permits"] == 5
    assert res["kb_version"] >= 2
    # Applied mapping changes production normalization + raises scores.
    eng.reload(conn)
    assert normalize_permit_status("ACTIVE", "testcity", eng) == "issued"
    after_avg = conn.execute(
        "SELECT AVG(opportunity_score) AS a FROM projects"
    ).fetchone()["a"]
    assert after_avg > before_avg
    # Reprocessed permits stamped with the kb version.
    stamped = conn.execute(
        "SELECT COUNT(*) AS n FROM projects WHERE mapping_kb_version IS NOT NULL"
    ).fetchone()["n"]
    assert stamped == 5
    # Audit row recorded.
    audit = conn.execute(
        "SELECT * FROM mapping_audit_log WHERE action='approved' AND raw_value='ACTIVE'"
    ).fetchone()
    assert audit is not None
    assert audit["new_mapping"] == "issued"


def test_batch_confidence_gate(conn):
    recompute_priorities(conn)
    qid = conn.execute(
        "SELECT id FROM knowledge_review_queue WHERE raw_value='ACTIVE'"
    ).fetchone()["id"]
    eng = KnowledgeEngine(conn)
    with pytest.raises(ValueError):
        eng.approve_batch(
            conn, [qid], proposed_mapping="issued", mapping_confidence=50
        )


def test_deprecate_preserves_history(conn):
    recompute_priorities(conn)
    qid = conn.execute(
        "SELECT id FROM knowledge_review_queue WHERE raw_value='ACTIVE'"
    ).fetchone()["id"]
    eng = KnowledgeEngine(conn)
    eng.approve_queue_item(conn, qid, canonical_status="issued", mapping_confidence=95, reprocess=False)
    eng.deprecate_mapping(
        conn, kind="status", municipality_slug="testcity", raw_value="ACTIVE",
        reviewed_by="tester", reason="test", reprocess=False,
    )
    row = conn.execute(
        "SELECT lifecycle_state FROM status_dictionary WHERE raw_status='ACTIVE'"
    ).fetchone()
    assert row["lifecycle_state"] == "deprecated"
    # Deprecated mapping no longer applied.
    eng.reload(conn)
    assert normalize_permit_status("ACTIVE", "testcity", eng) == "unknown"
    # But the audit trail preserves it.
    actions = [
        r["action"]
        for r in conn.execute(
            "SELECT action FROM mapping_audit_log WHERE raw_value='ACTIVE' ORDER BY id"
        )
    ]
    assert "approved" in actions and "deprecated" in actions
