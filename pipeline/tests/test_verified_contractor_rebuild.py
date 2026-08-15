"""Release 15.3 verified contractor compatibility regressions."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from pipeline.company_resolution.normalize import normalize_company_name
from pipeline.config.settings import SCHEMA_PATH
from pipeline.contractors.audit import contractor_audit_snapshot, compare_snapshots
from pipeline.contractors.rebuild import rebuild_contractors
from pipeline.db.database import migrate_schema
from pipeline.export import export_json
from pipeline import pipeline_runs
from pipeline.reports import generate_reports


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    for slug in ("phoenix_az", "mesa_az"):
        c.execute(
            "INSERT INTO jurisdictions(slug,name,state,status) VALUES (?,?, 'AZ','connected')",
            (slug, slug),
        )
    return c


def _company(c, name, lead_type, verification, *, phone=None, source="fixture", rule=None):
    now = _now()
    company_id = c.execute(
        """INSERT INTO companies
           (display_name,normalized_name,main_phone,lead_type,lead_verification_status,
            lead_source,why_this_lead,lifecycle_state,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,'active',?,?)""",
        (name, normalize_company_name(name), phone, lead_type, verification,
         source, rule or f"Fixture classified {lead_type}", now, now),
    ).lastrowid
    c.execute(
        """INSERT INTO company_lead_classification
           (company_id,lead_type,verification_status,source,source_field,confidence,
            why_this_lead,explicit_contractor_permits,ambiguous_permit_contacts,
            classification_version,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (company_id, lead_type, verification, source, "fixture_field",
         1.0 if verification == "verified" else .4,
         rule or f"Fixture classified {lead_type}",
         2 if lead_type == "verified_contractor" else 0,
         0 if lead_type == "verified_contractor" else 1,
         "fixture-classification-v1", now),
    )
    c.execute("INSERT INTO company_intelligence(company_id) VALUES (?)", (company_id,))
    return company_id


def _permit_project(c, company_id, number, raw_name, *, jurisdiction="phoenix_az",
                    days_ago=5, valuation=100000, materials=10000):
    now = _now()
    issued = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()
    permit_id = c.execute(
        """INSERT INTO permits
           (jurisdiction,permit_number,permit_type,status,description,issued_date,city,state,
            valuation,general_contractor_name,contractor_company_id,contractor_source_role,
            contractor_source_field,contractor_evidence_confidence,
            contractor_verification_status,first_seen_at,last_updated_at)
           VALUES (?,?, 'Commercial','Permit Issued','Commercial tenant improvement',?,
                   'Phoenix','AZ',?,?,?,'contractor','contractor_name',1.0,'verified',?,?)""",
        (jurisdiction, number, issued, valuation, raw_name, company_id, now, now),
    ).lastrowid
    project_id = c.execute(
        """INSERT INTO projects
           (permit_id,jurisdiction,project_category,project_lifecycle,opportunity_date,
            opportunity_score,estimated_material_value,contractor_company_id,
            analysis_version,analyzed_at)
           VALUES (?,?, 'Commercial','Permit Issued',?,85,?,?, 'fixture',?)""",
        (permit_id, jurisdiction, issued, materials, company_id, now),
    ).lastrowid
    return permit_id, project_id


def _fixture_population(c):
    verified = _company(
        c, "Desert Flow Plumbing LLC", "verified_contractor", "verified",
        phone="602-555-0199", source="permit_party_evidence",
        rule="Explicit municipal contractor field on two permits",
    )
    architect = _company(c, "Precision Architecture", "specifier_architect_engineer", "role_inferred")
    owner = _company(c, "Sunset Property Holdings", "owner_developer", "role_inferred")
    applicant = _company(c, "Permit Filing Services", "unverified_permit_contact", "unverified")
    individual = _company(c, "John Smith", "unverified_permit_contact", "unverified")
    placeholder = _company(c, "TBD", "unverified_permit_contact", "unverified")

    verified_projects = [
        _permit_project(c, verified, "V-1", "Desert Flow Plumbing LLC", valuation=100000, materials=10000),
        _permit_project(c, verified, "V-2", "Desert Flow P&H", jurisdiction="mesa_az",
                        valuation=200000, materials=20000),
    ]
    excluded = [
        (architect, "A-1", "Precision Architecture"),
        (owner, "O-1", "Sunset Property Holdings"),
        (applicant, "U-1", "Permit Filing Services"),
        (individual, "I-1", "John Smith"),
        (placeholder, "P-1", "TBD"),
    ]
    excluded_projects = [_permit_project(c, cid, num, name) for cid, num, name in excluded]

    # Simulate the polluted legacy state that Release 15.3 must replace.
    old_ids = []
    for index, (_, _, name) in enumerate(excluded, start=1):
        old_ids.append(c.execute(
            """INSERT INTO contractors
               (name,normalized_name,permit_count,updated_at)
               VALUES (?,?,1,?)""",
            (name, f"LEGACY {index} {normalize_company_name(name)}", _now()),
        ).lastrowid)
    legacy_verified = c.execute(
        "INSERT INTO contractors(name,normalized_name,permit_count,updated_at) VALUES (?,?,2,?)",
        ("Desert Flow Plumbing LLC", "LEGACY DESERT FLOW", _now()),
    ).lastrowid
    for _, project_id in verified_projects:
        c.execute("UPDATE projects SET contractor_id=? WHERE id=?", (legacy_verified, project_id))
    for old_id, (_, project_id) in zip(old_ids, excluded_projects):
        c.execute("UPDATE projects SET contractor_id=? WHERE id=?", (old_id, project_id))
    c.commit()
    return verified


def test_rebuild_only_materializes_verified_canonical_contractors(conn):
    verified = _fixture_population(conn)
    conn.execute("PRAGMA query_only=ON")
    before = contractor_audit_snapshot(conn)
    conn.execute("PRAGMA query_only=OFF")

    assert rebuild_contractors(conn) == 1
    rows = conn.execute("SELECT * FROM contractors").fetchall()
    assert len(rows) == 1
    contractor = rows[0]
    assert contractor["company_id"] == verified
    assert contractor["name"] == "Desert Flow Plumbing LLC"
    assert contractor["permit_count"] == 2
    assert contractor["jurisdiction_breakdown"] == '{"mesa_az": 1, "phoenix_az": 1}'
    assert contractor["commercial_pct"] == 100.0
    assert contractor["avg_project_value"] == 150000.0
    assert contractor["estimated_material_opportunity"] == 30000.0
    assert contractor["has_contact_info"] == 1
    assert contractor["verification_status"] == "verified"
    assert contractor["classification_source"] == "permit_party_evidence"
    assert contractor["classification_version"] == "fixture-classification-v1"

    linked = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE contractor_company_id=? AND contractor_id=?",
        (verified, contractor["id"]),
    ).fetchone()[0]
    assert linked == 2
    assert conn.execute(
        """SELECT COUNT(*) FROM projects pr JOIN companies c ON c.id=pr.contractor_company_id
           WHERE c.lead_type<>'verified_contractor' AND pr.contractor_id IS NOT NULL"""
    ).fetchone()[0] == 0

    intelligence = conn.execute(
        "SELECT * FROM company_intelligence WHERE company_id=?", (verified,)
    ).fetchone()
    assert intelligence["contractor_permit_count"] == 2
    assert intelligence["contractor_estimated_material_opportunity"] == 30000.0
    assert intelligence["contractor_has_contact_info"] == 1

    after = contractor_audit_snapshot(conn)
    comparison = compare_snapshots(before, after)
    assert comparison["projects_relinked"] == 2
    assert comparison["remaining_unresolved_projects"] == 5
    assert comparison["database_integrity_ok"] is True


def test_rebuild_is_idempotent_and_preserves_exports_and_growth_report(conn, monkeypatch):
    _fixture_population(conn)
    rebuild_contractors(conn)
    first = [dict(row) for row in conn.execute(
        "SELECT * FROM contractors ORDER BY company_id"
    )]
    rebuild_contractors(conn)
    second = [dict(row) for row in conn.execute(
        "SELECT * FROM contractors ORDER BY company_id"
    )]
    for rows in (first, second):
        for row in rows:
            row.pop("id")
            row.pop("updated_at")
    assert first == second
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    payloads = {}
    monkeypatch.setattr(export_json, "_write_json", lambda filename, payload: payloads.setdefault(filename, payload))
    export_json.export_contractors(conn)
    export_json.export_contractor_matching(conn)
    export_json.export_high_opportunity_projects(conn)
    exported = payloads["contractor_matching.json"]["contractors"]
    assert len(exported) == 1
    assert exported[0]["company_id"] is not None
    assert exported[0]["verification_status"] == "verified"
    assert exported[0]["estimated_material_opportunity"] == 30000.0
    project_export = payloads["high_opportunity_projects.json"]["projects"]
    named_contractors = {row["general_contractor_name"] for row in project_export
                         if row["general_contractor_name"]}
    assert named_contractors == {"Desert Flow Plumbing LLC"}

    reports = {}
    monkeypatch.setattr(generate_reports, "_write_report", lambda name, content: reports.setdefault(name, content) or name)
    generate_reports.weekly_contractor_growth_report(conn)
    report = next(iter(reports.values()))
    assert "Desert Flow Plumbing LLC" in report
    assert "Precision Architecture" not in report


def test_existing_database_migration_adds_contractor_metrics_idempotently():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE companies(id INTEGER PRIMARY KEY);
        CREATE TABLE contractors(id INTEGER PRIMARY KEY,normalized_name TEXT UNIQUE);
        CREATE TABLE company_intelligence(company_id INTEGER PRIMARY KEY);
    """)
    migrate_schema(c)
    migrate_schema(c)
    contractor_columns = {row["name"] for row in c.execute("PRAGMA table_info(contractors)")}
    intelligence_columns = {row["name"] for row in c.execute("PRAGMA table_info(company_intelligence)")}
    assert {"company_id", "estimated_material_opportunity", "has_contact_info",
            "verification_status", "classification_source", "classification_rule",
            "classification_version"} <= contractor_columns
    assert {"contractor_permit_count", "contractor_jurisdiction_breakdown",
            "contractor_estimated_material_opportunity", "contractor_has_contact_info",
            "contractor_classification_version"} <= intelligence_columns
    assert len(c.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_contractors_company'"
    ).fetchall()) == 1
    c.close()


def test_morning_company_refresh_rebuilds_after_classification_and_metrics(monkeypatch):
    calls = []
    monkeypatch.setattr("pipeline.company_resolution.backfill.backfill_companies",
                        lambda conn, resume=True: calls.append("backfill"))
    monkeypatch.setattr("pipeline.company_resolution.lead_role_correction.apply_lead_role_correction",
                        lambda conn: calls.append("classification"))
    monkeypatch.setattr("pipeline.company_resolution.metrics.compute_company_metrics",
                        lambda conn: calls.append("metrics"))
    monkeypatch.setattr("pipeline.contractors.rebuild.rebuild_contractors",
                        lambda conn: calls.append("contractors"))
    monkeypatch.setattr("pipeline.company_resolution.timeline.rebuild_company_timeline",
                        lambda conn: calls.append("timeline"))
    monkeypatch.setattr("pipeline.company_resolution.export.export_all",
                        lambda conn: calls.append("export"))

    pipeline_runs._refresh_company_intelligence(object())
    assert calls == ["backfill", "classification", "metrics", "contractors", "timeline", "export"]
