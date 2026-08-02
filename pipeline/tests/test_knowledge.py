"""Tests for Municipal Knowledge Engine dictionaries + review queue."""

from __future__ import annotations

import sqlite3

from pipeline.analysis.normalization import explain_permit_type_normalization, explain_status_normalization
from pipeline.db.database import apply_schema
from pipeline.knowledge.engine import GLOBAL_MUNI, KnowledgeEngine, ensure_municipality
from pipeline.knowledge.seed import seed_knowledge_base


def _mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_schema(conn)
    # Minimal jurisdiction for FK-free municipality sync path
    conn.execute(
        """
        INSERT INTO jurisdictions (slug, name, state, status)
        VALUES ('phoenix_az', 'Phoenix', 'AZ', 'connected')
        """
    )
    conn.commit()
    seed_knowledge_base(conn)
    return conn


def test_seed_loads_phoenix_open_into_dictionary():
    conn = _mem_db()
    row = conn.execute(
        """
        SELECT canonical_status, human_reviewed FROM status_dictionary
        WHERE municipality_slug IN ('phoenix', 'phoenix_az') AND lower(raw_status)='open'
        """
    ).fetchone()
    assert row is not None
    assert row["canonical_status"] == "issued"
    assert row["human_reviewed"] == 1


def test_dictionary_lookup_beats_bootstrap_for_new_code():
    conn = _mem_db()
    ensure_municipality(conn, "phoenix_az", "Phoenix")
    now = "2026-07-22T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO permit_code_dictionary (
            municipality_slug, raw_permit_code, friendly_name, trade, project_category,
            plumbing_relevance, ai_confidence, human_reviewed, last_observed_date,
            created_at, updated_at
        ) VALUES ('phoenix_az', 'CGD', 'Grading & Drainage', 'building', 'Building',
                  0.2, 1.0, 1, '2026-07-22', ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    engine = KnowledgeEngine(conn)
    hit = explain_permit_type_normalization("CGD", None, "phoenix_az", engine)
    assert hit.normalized_permit_type == "building"
    assert hit.permit_type_mapping_rule.startswith("dictionary:permit_code")


def test_unknown_enqueued_not_silently_classified():
    conn = _mem_db()
    engine = KnowledgeEngine(conn)
    result = explain_status_normalization("TOTALLY_NEW_STATUS", "mesa_az", engine)
    assert result.normalized_status == "unknown"
    n = engine.flush_queue(conn)
    assert n >= 1
    row = conn.execute(
        """
        SELECT status, suggested_interpretation FROM knowledge_review_queue
        WHERE kind='status' AND raw_value='TOTALLY_NEW_STATUS'
        """
    ).fetchone()
    assert row["status"] == "pending"


def test_approve_status_updates_dictionary():
    conn = _mem_db()
    engine = KnowledgeEngine(conn)
    explain_status_normalization("Closed", "gilbert_az", engine)
    engine.flush_queue(conn)
    queue_id = conn.execute(
        "SELECT id FROM knowledge_review_queue WHERE raw_value='Closed' AND kind='status'"
    ).fetchone()["id"]
    engine.approve_queue_item(
        conn, queue_id, canonical_status="finaled", notes="Gilbert closed ~= finaled"
    )
    engine.reload(conn)
    hit = explain_status_normalization("Closed", "gilbert_az", engine)
    assert hit.normalized_status == "finaled"
    assert "dictionary" in hit.status_mapping_rule or "approved" in hit.status_mapping_rule


def test_reject_leaves_unknown():
    conn = _mem_db()
    engine = KnowledgeEngine(conn)
    explain_permit_type_normalization("ZZZ99", "mystery work", "tempe_az", engine)
    engine.flush_queue(conn)
    queue_id = conn.execute(
        "SELECT id FROM knowledge_review_queue WHERE raw_value='ZZZ99'"
    ).fetchone()["id"]
    engine.reject_queue_item(conn, queue_id, notes="not useful")
    engine.reload(conn)
    hit = explain_permit_type_normalization("ZZZ99", None, "tempe_az", engine)
    assert hit.normalized_permit_type == "other"
    assert conn.execute(
        "SELECT status FROM knowledge_review_queue WHERE id=?", (queue_id,)
    ).fetchone()["status"] == "rejected"


def test_global_dictionary_seed_residential():
    conn = _mem_db()
    engine = KnowledgeEngine(conn)
    hit = explain_permit_type_normalization("Residential", None, "peoria_az", engine)
    assert hit.normalized_permit_type == "residential"
