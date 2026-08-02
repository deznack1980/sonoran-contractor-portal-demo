"""Sprint 4 tests — company identity resolution, linking, metrics, timeline."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from pipeline.company_resolution.audit import write_identity_audit
from pipeline.company_resolution.backfill import backfill_companies
from pipeline.company_resolution.match import resolve
from pipeline.company_resolution.merge import (
    add_alias,
    create_company,
    merge_companies,
)
from pipeline.company_resolution.metrics import compute_company_metrics
from pipeline.company_resolution.models import (
    DECISION_MATCHED,
    DECISION_NEW,
    DECISION_POSSIBLE,
    CompanyInput,
)
from pipeline.company_resolution.normalize import normalize_company_name
from pipeline.company_resolution.queries import list_companies
from pipeline.company_resolution.timeline import rebuild_company_timeline
from pipeline.config.settings import SCHEMA_PATH


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.execute(
        "INSERT INTO jurisdictions (slug, name, state, status) "
        "VALUES ('phoenix_az','Phoenix','AZ','connected')"
    )
    return c


def _add_permit_project(c, *, num, contractor, owner=None, score=80.0,
                        category="Commercial", city="PHOENIX",
                        license_number=None):
    now = _now()
    today = _today()
    cur = c.execute(
        """
        INSERT INTO permits (jurisdiction, permit_number, permit_type, status,
            description, issued_date, filed_date, city, state, valuation,
            general_contractor_name, owner_name, contractor_license_number,
            first_seen_at, last_updated_at)
        VALUES ('phoenix_az', ?, 'Commercial', 'Permit Issued', 'Tenant improvement',
                ?, ?, ?, 'AZ', 500000, ?, ?, ?, ?, ?)
        """,
        (num, today, today, city, contractor, owner, license_number, now, now),
    )
    permit_id = cur.lastrowid
    c.execute(
        """
        INSERT INTO projects (permit_id, jurisdiction, project_category,
            opportunity_score, opportunity_date, opportunity_date_basis,
            opportunity_timing, project_lifecycle, analysis_version, analyzed_at)
        VALUES (?, 'phoenix_az', ?, ?, ?, 'Application Submitted', 'Good',
                'Permit Issued', 'test', ?)
        """,
        (permit_id, category, score, today, _now()),
    )
    c.commit()
    return permit_id


# ---------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------
def test_normalize_company_name_strips_suffix_and_punct():
    assert normalize_company_name("Acme Plumbing, LLC.") == "ACME PLUMBING"
    assert normalize_company_name("Acme Plumbing Inc") == "ACME PLUMBING"
    assert normalize_company_name("A & B Builders") == "A AND B BUILDERS"
    assert normalize_company_name("") == ""
    assert normalize_company_name(None) == ""


# ---------------------------------------------------------------
# Matching
# ---------------------------------------------------------------
def test_license_reinforces_name_match(conn):
    # Same name + same license -> strong match (license corroborates the name).
    create_company(conn, CompanyInput(name="Acme Plumbing LLC", license_number="ROC123456"))
    res = resolve(conn, CompanyInput(name="Acme Plumbing", license_number="ROC-123456"))
    assert res.decision == DECISION_MATCHED
    assert res.confidence >= 95
    assert "name_plus_license" in res.reasons


def test_shared_license_does_not_merge_different_names(conn):
    # Junk/shared licenses (e.g. HTE0001) must NOT collapse unrelated companies.
    create_company(conn, CompanyInput(name="3411 Builders Inc", license_number="HTE0001"))
    res = resolve(conn, CompanyInput(name="Caliente Construction Inc", license_number="HTE0001"))
    assert res.decision == DECISION_NEW


def test_name_plus_address_match(conn):
    create_company(conn, CompanyInput(name="Desert Builders LLC", city="Phoenix"))
    res = resolve(conn, CompanyInput(name="Desert Builders", city="PHOENIX"))
    assert res.decision == DECISION_MATCHED
    assert "name_plus_city" in res.reasons


def test_alias_match(conn):
    cid = create_company(conn, CompanyInput(name="New Brand LLC"))
    add_alias(conn, cid, "Old Brand LLC")
    res = resolve(conn, CompanyInput(name="Old Brand"))
    assert res.company_id == cid
    assert "alias_exact" in res.reasons


def test_low_confidence_routes_to_review(conn):
    # Same phone, different name -> phone_exact (70) -> possible_match.
    create_company(conn, CompanyInput(name="Company A LLC", phone="(602) 555-1212"))
    res = resolve(conn, CompanyInput(name="Company B LLC", phone="602-555-1212"))
    assert res.decision == DECISION_POSSIBLE


def test_new_company_when_no_candidate(conn):
    res = resolve(conn, CompanyInput(name="Brand New Co LLC"))
    assert res.decision == DECISION_NEW


# ---------------------------------------------------------------
# Backfill: idempotency, linking, raw preservation, dup prevention
# ---------------------------------------------------------------
def test_backfill_links_and_preserves_raw(conn):
    _add_permit_project(conn, num="A1", contractor="Acme Plumbing LLC")
    _add_permit_project(conn, num="A2", contractor="Acme Plumbing LLC")
    stats = backfill_companies(conn, chunk=10, verbose=False)
    assert stats.created == 1  # both permits share one canonical company
    # Raw fields untouched.
    raw = conn.execute("SELECT general_contractor_name FROM permits WHERE permit_number='A1'").fetchone()
    assert raw["general_contractor_name"] == "Acme Plumbing LLC"
    # Links set on both project and permit.
    pr = conn.execute("SELECT contractor_company_id FROM projects").fetchall()
    assert all(r["contractor_company_id"] is not None for r in pr)
    pm = conn.execute("SELECT contractor_company_id FROM permits").fetchall()
    assert all(r["contractor_company_id"] is not None for r in pm)


def test_backfill_idempotent(conn):
    _add_permit_project(conn, num="B1", contractor="Beta Corp")
    backfill_companies(conn, chunk=10, verbose=False)
    before = conn.execute("SELECT COUNT(*) n FROM companies").fetchone()["n"]
    stats2 = backfill_companies(conn, chunk=10, verbose=False)
    after = conn.execute("SELECT COUNT(*) n FROM companies").fetchone()["n"]
    assert before == after
    assert stats2.created == 0


def test_backfill_does_not_change_scores(conn):
    _add_permit_project(conn, num="C1", contractor="Gamma LLC", score=77.0)
    before = conn.execute("SELECT opportunity_score FROM projects").fetchone()["opportunity_score"]
    backfill_companies(conn, chunk=10, verbose=False)
    after = conn.execute("SELECT opportunity_score FROM projects").fetchone()["opportunity_score"]
    assert before == after == 77.0


def test_duplicate_prevention(conn):
    _add_permit_project(conn, num="D1", contractor="Acme Plumbing LLC")
    _add_permit_project(conn, num="D2", contractor="ACME PLUMBING, INC")  # normalizes same
    backfill_companies(conn, chunk=10, verbose=False)
    n = conn.execute("SELECT COUNT(*) n FROM companies WHERE lifecycle_state='active'").fetchone()["n"]
    assert n == 1


# ---------------------------------------------------------------
# Metrics + priority separation
# ---------------------------------------------------------------
def test_metrics_calculation(conn):
    _add_permit_project(conn, num="M1", contractor="Metrics LLC", score=90, category="Commercial")
    _add_permit_project(conn, num="M2", contractor="Metrics LLC", score=70, category="Residential")
    backfill_companies(conn, chunk=10, verbose=False)
    compute_company_metrics(conn)
    row = conn.execute(
        "SELECT * FROM company_intelligence ci JOIN companies c ON c.id=ci.company_id "
        "WHERE c.normalized_name='METRICS'"
    ).fetchone()
    assert row["total_projects"] == 2
    assert row["commercial_project_count"] == 1
    assert row["residential_project_count"] == 1
    assert row["average_opportunity_score"] == 80.0
    assert row["company_priority_score"] is not None


def test_priority_score_separate_from_permit_score(conn):
    _add_permit_project(conn, num="S1", contractor="Sep LLC", score=88.0)
    backfill_companies(conn, chunk=10, verbose=False)
    compute_company_metrics(conn)
    permit_score = conn.execute("SELECT opportunity_score FROM projects").fetchone()["opportunity_score"]
    company_score = conn.execute(
        "SELECT company_priority_score FROM company_intelligence"
    ).fetchone()["company_priority_score"]
    assert permit_score == 88.0
    # Distinct concept: company priority is derived independently, not equal to permit score.
    assert company_score != permit_score


# ---------------------------------------------------------------
# Timeline dedup
# ---------------------------------------------------------------
def test_timeline_dedup(conn):
    _add_permit_project(conn, num="T1", contractor="Timeline LLC")
    backfill_companies(conn, chunk=10, verbose=False)
    first = rebuild_company_timeline(conn)
    assert first > 0
    second = rebuild_company_timeline(conn)
    assert second == 0  # idempotent


# ---------------------------------------------------------------
# Merge audit
# ---------------------------------------------------------------
def test_merge_repoints_and_audits(conn):
    pid = _add_permit_project(conn, num="MG1", contractor="Merge Victim LLC")
    backfill_companies(conn, chunk=10, verbose=False)
    victim = conn.execute("SELECT contractor_company_id FROM projects WHERE permit_id=?",
                          (pid,)).fetchone()["contractor_company_id"]
    survivor = create_company(conn, CompanyInput(name="Merge Survivor LLC"))
    merge_companies(conn, survivor, victim, reason="dupe")
    conn.commit()
    # Victim deprecated, links repointed.
    v = conn.execute("SELECT lifecycle_state, merged_into_id FROM companies WHERE id=?",
                     (victim,)).fetchone()
    assert v["lifecycle_state"] == "merged"
    assert v["merged_into_id"] == survivor
    linked = conn.execute("SELECT contractor_company_id FROM projects WHERE permit_id=?",
                          (pid,)).fetchone()["contractor_company_id"]
    assert linked == survivor
    audit = conn.execute(
        "SELECT COUNT(*) n FROM company_identity_audit_log WHERE action='merged'"
    ).fetchone()["n"]
    assert audit == 1


def test_audit_log_writes(conn):
    cid = create_company(conn, CompanyInput(name="Audited LLC"))
    write_identity_audit(conn, action="profile_updated", target_company_id=cid,
                         new_values={"x": 1})
    n = conn.execute(
        "SELECT COUNT(*) n FROM company_identity_audit_log WHERE target_company_id=?",
        (cid,),
    ).fetchone()["n"]
    assert n >= 2  # created (from create_company) + profile_updated


# ---------------------------------------------------------------
# Pagination + API filtering
# ---------------------------------------------------------------
def test_pagination_and_filtering(conn):
    for i in range(7):
        _add_permit_project(conn, num=f"PG{i}", contractor=f"Pager {i} LLC")
    backfill_companies(conn, chunk=50, verbose=False)
    compute_company_metrics(conn)
    page1 = list_companies(conn, {"page": 1, "page_size": 3})
    assert page1["page_size"] == 3
    assert len(page1["items"]) == 3
    assert page1["total"] >= 7
    assert page1["pages"] >= 3
    # Role filter: all are contractors.
    contractors = list_companies(conn, {"role": "contractor", "page_size": 100})
    assert contractors["total"] >= 7
    # Non-existent role -> none.
    manuf = list_companies(conn, {"role": "manufacturer", "page_size": 100})
    assert manuf["total"] == 0
    # Search filter.
    search = list_companies(conn, {"q": "Pager 3", "page_size": 100})
    assert search["total"] == 1
