"""Sprint 6 (Sales Workspace) tests — dashboard, company lists, opportunities,
activity feed, follow-ups, manager workload, reports catalog, and the
record-level/permission regressions that back the redesigned UI.

These exercise the backend (where authorization is enforced). Hiding a nav link
is never trusted; every assertion here confirms the server enforces access.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from pipeline.auth import service as auth
from pipeline.auth.rbac import AuthzError
from pipeline.auth.seed import seed_auth
from pipeline.config.settings import PROJECT_ROOT, SCHEMA_PATH
from pipeline.crm import admin as crm_admin
from pipeline.crm import service as crm
from pipeline.reports import catalog as reports_catalog


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.execute("INSERT INTO jurisdictions (slug, name, state, status) "
              "VALUES ('phoenix_az','Phoenix','AZ','connected')")
    seed_auth(c)
    return c


def _org(c):
    return c.execute("SELECT id FROM organizations WHERE slug='corridoriq'").fetchone()["id"]


def _ctx(c, uid):
    return auth.build_user_context(c, c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())


def _company(c, name, city="PHOENIX"):
    now = _now()
    cur = c.execute("INSERT INTO companies (normalized_name, display_name, city, state, "
                    "lifecycle_state, created_at, updated_at) VALUES (?,?,?,?,'active',?,?)",
                    (name.upper(), name, city, "AZ", now, now))
    c.commit()
    return cur.lastrowid


def _intel(c, company_id, tier="High", score=75, **extra):
    cols = {"company_id": company_id, "company_priority_tier": tier,
            "company_priority_score": score, "active_projects": 2,
            "projects_last_30_days": 1, "total_projects": 3,
            "average_opportunity_score": 70, "highest_opportunity_score": 88,
            "municipality_count": 1, "latest_activity_date": _today(),
            "model_version": "test"}
    cols.update(extra)
    keys = ", ".join(cols)
    qs = ", ".join("?" for _ in cols)
    c.execute(f"INSERT INTO company_intelligence ({keys}) VALUES ({qs})", list(cols.values()))
    c.commit()


def _project(c, company_id, score=90, lifecycle="permitting", days_ago=1):
    now = _now()
    d = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    cur = c.execute("INSERT INTO permits (jurisdiction, permit_number, permit_type, description, job_address, city, "
                    "state, zip, status, contractor_company_id, issued_date, first_seen_at, last_updated_at) "
                    "VALUES ('phoenix_az',?, 'Commercial plumbing', 'Restroom and backflow plumbing',?, "
                    "'PHOENIX','AZ','85004','issued',?,?,?,?)",
                    (f"P{company_id}-{days_ago}", "123 Main St", company_id, d, now, now))
    pid = cur.lastrowid
    project = c.execute("INSERT INTO projects (permit_id, jurisdiction, contractor_company_id, "
                        "project_category, project_lifecycle, opportunity_score, opportunity_date, "
                        "opportunity_timing, estimated_material_value, estimated_plumbing_scope, "
                        "confidence_score, analysis_version, analyzed_at) "
                        "VALUES (?, 'phoenix_az', ?, 'commercial', ?, ?, ?, 'immediate', 5000, "
                        "'Commercial Fixtures, Backflow, Valves', 86, 'test-estimator', ?)",
                        (pid, company_id, lifecycle, score, d, now))
    c.executemany(
        "INSERT INTO estimated_materials (project_id,material_name,confidence_pct,rationale) VALUES (?,?,?,?)",
        [(project.lastrowid, "Commercial Fixtures", 90, "Restroom keyword"),
         (project.lastrowid, "Backflow", 85, "Backflow keyword")],
    )
    c.commit()
    return project.lastrowid


@pytest.fixture()
def env(conn):
    org = _org(conn)
    admin = auth.create_user(conn, organization_id=org, email="admin@corridoriq.com",
                             password="AdminPass123", role_names=["admin"], must_change_password=False)
    mgr = auth.create_user(conn, organization_id=org, email="mgr@corridoriq.com",
                           password="MgrPass123", role_names=["sales_manager"], must_change_password=False)
    rep = auth.create_user(conn, organization_id=org, email="rep@corridoriq.com",
                           password="RepPass123", role_names=["sales_representative"], must_change_password=False)
    rep2 = auth.create_user(conn, organization_id=org, email="rep2@corridoriq.com",
                            password="Rep2Pass123", role_names=["sales_representative"], must_change_password=False)
    co1 = _company(conn, "Alpha Plumbing")
    co2 = _company(conn, "Beta Builders")
    _intel(conn, co1, "Critical", 95)
    _intel(conn, co2, "Medium", 50)
    e = {"conn": conn, "org": org, "co1": co1, "co2": co2,
         "admin": _ctx(conn, admin), "mgr": _ctx(conn, mgr),
         "rep": _ctx(conn, rep), "rep2": _ctx(conn, rep2),
         "ids": {"admin": admin, "mgr": mgr, "rep": rep, "rep2": rep2}}
    # Assign co1 -> rep, co2 -> rep2.
    crm.assign_company(conn, e["admin"], co1, rep)
    crm.assign_company(conn, e["admin"], co2, rep2)
    return e


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

def test_dashboard_shape_and_counts(env):
    c = env["conn"]
    d = crm.dashboard(c, env["rep"])
    assert set(["kpis", "priority_companies", "followups_due",
                "recent_opportunity_activity", "activity_summary"]).issubset(d)
    # Record-level: rep sees only their one assigned company.
    assert d["kpis"]["my_companies"] == 1
    assert d["my_companies"] == 1  # back-compat key retained
    assert len(d["priority_companies"]) == 1
    assert d["priority_companies"][0]["company_id"] == env["co1"]
    for k in ("calls_logged", "companies_contacted"):
        assert k in d["activity_summary"]


def test_dashboard_overdue_and_calls_due(env):
    c = env["conn"]
    # Follow-up today for co1 (calls due today) and an overdue one via direct update.
    crm.create_activity(c, env["rep"], env["co1"],
                        {"activity_type": "call", "next_followup_at": _today() + "T09:00:00"})
    d = crm.dashboard(c, env["rep"])
    assert d["kpis"]["calls_due_today"] == 1
    past = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    c.execute("UPDATE crm_company_relationships SET next_followup_at=? WHERE company_id=?",
              (past + "T09:00:00", env["co1"]))
    c.commit()
    d2 = crm.dashboard(c, env["rep"])
    assert d2["kpis"]["overdue_followups"] == 1


def test_manager_dashboard_sees_all_org(env):
    d = crm.dashboard(env["conn"], env["mgr"])
    assert d["kpis"]["my_companies"] == 2  # manager scope = whole org
    assert d["assignment_scoped"] is False
    assert d["scope"] == "organization"


def test_rep_dashboard_is_assignment_scoped(env):
    d = crm.dashboard(env["conn"], env["rep"])
    assert d["assignment_scoped"] is True
    assert d["scope"] == "assignment"
    assert d["kpis"]["my_companies"] == 1


# --------------------------------------------------------------------------
# Role-aware landing + admin organization dashboard
# --------------------------------------------------------------------------

def test_default_landing_page_by_role(env):
    from pipeline.auth.rbac import default_landing_page

    assert env["admin"]["default_landing_page"] == "admin-dashboard.html"
    assert env["mgr"]["default_landing_page"] == "team-dashboard.html"
    assert env["rep"]["default_landing_page"] == "sales-dashboard.html"
    assert default_landing_page(["estimator"], {"projects.view_assigned"}) == "estimator-work-queue.html"
    assert default_landing_page(["read_only"], {"companies.view_assigned"}) == "readonly-dashboard.html"


def test_admin_dashboard_is_organization_scoped_not_assignment(env):
    c = env["conn"]
    # Unassigned company should still count in org totals.
    orphan = _company(c, "Orphan Mechanical")
    _intel(c, orphan, "High", 80)
    _project(c, env["co1"], score=85, lifecycle="Permit Issued")
    c.execute("UPDATE companies SET main_phone='602-555-0110', lead_type='verified_contractor' WHERE id=?",
              (env["co1"],))
    c.execute("UPDATE crm_company_relationships SET lead_type='verified_contractor' WHERE company_id=?",
              (env["co1"],))
    c.commit()

    dash = crm_admin.admin_dashboard(c, env["admin"])
    assert dash["scope"] == "organization"
    assert dash["assignment_scoped"] is False
    k = dash["kpis"]
    assert k["total_companies"] >= 3
    assert k["total_projects"] >= 1
    assert k["total_permits"] >= 1
    assert k["companies_awaiting_assignment"] >= 1
    assert k["verified_contractors"] == 1
    assert k["contact_ready_contractors"] == 1
    assert k["contractors_needing_enrichment"] == 0
    assert k["active_employees"] >= 4
    verification = dash["data_verification"]
    assert verification["database_status"] == "connected"
    assert verification["public_permit_records"] >= 1
    assert verification["corridoriq_project_records"] >= 1
    assert verification["crm_relationship_records"] == 2
    assert verification["jurisdiction_sources"] == 1
    assert verification["external_evidence_records"] == 0
    assert verification["external_evidence_companies"] == 0
    assert verification["external_evidence_sources"] == 0
    # Must never look like a personal assignment dashboard.
    assert "my_companies" not in k
    assert "No companies assigned to you" not in json.dumps(dash)


def test_rep_cannot_access_admin_dashboard(env):
    with pytest.raises(AuthzError):
        crm_admin.admin_dashboard(env["conn"], env["rep"])


def test_estimator_work_queue_accessible(env):
    c = env["conn"]
    org = env["org"]
    est_id = auth.create_user(c, organization_id=org, email="est@corridoriq.com",
                              password="EstPass123", role_names=["estimator"],
                              must_change_password=False)
    est = _ctx(c, est_id)
    assert est["default_landing_page"] == "estimator-work-queue.html"
    _project(c, env["co1"], score=90, lifecycle="Permit Issued")
    q = crm_admin.estimator_work_queue(c, est)
    assert q["scope"] == "estimator"
    assert q["assignment_scoped"] is True
    assert isinstance(q["queue"], list)
def test_list_reason_and_recommended_action(env):
    res = crm.list_my_companies(env["conn"], env["rep"])
    item = res["items"][0]
    assert "Critical" in item["reason"]
    assert item["recommended_action"] == "Make first contact"  # never contacted
    assert item["followup_overdue"] is False


def test_list_pagination(env):
    res = crm.list_my_companies(env["conn"], env["mgr"], {"page_size": 1})
    assert res["total"] == 2 and res["pages"] == 2 and len(res["items"]) == 1
    res2 = crm.list_my_companies(env["conn"], env["mgr"], {"page_size": 1, "page": 2})
    assert res2["items"][0]["company_id"] != res["items"][0]["company_id"]


def test_list_record_level_scoping(env):
    res = crm.list_my_companies(env["conn"], env["rep"])
    assert res["total"] == 1 and res["items"][0]["company_id"] == env["co1"]


def test_list_followup_filter(env):
    c = env["conn"]
    crm.create_activity(c, env["rep"], env["co1"],
                        {"activity_type": "call", "next_followup_at": _today() + "T10:00:00"})
    due = crm.list_my_companies(c, env["rep"], {"followup": "due"})
    assert due["total"] == 1
    none_due = crm.list_my_companies(c, env["rep2"], {"followup": "due"})
    assert none_due["total"] == 0


def test_list_never_contacted_filter(env):
    res = crm.list_my_companies(env["conn"], env["rep"], {"contacted": "never"})
    assert res["total"] == 1
    crm.create_activity(env["conn"], env["rep"], env["co1"], {"activity_type": "call"})
    res2 = crm.list_my_companies(env["conn"], env["rep"], {"contacted": "never"})
    assert res2["total"] == 0


# --------------------------------------------------------------------------
# Follow-ups due
# --------------------------------------------------------------------------

def test_followups_due(env):
    c = env["conn"]
    crm.create_activity(c, env["rep"], env["co1"],
                        {"activity_type": "call", "activity_outcome": "left_voicemail",
                         "next_followup_at": _today() + "T10:00:00"})
    items = crm.followups_due(c, env["rep"])
    assert len(items) == 1
    assert items[0]["company_id"] == env["co1"]
    assert items[0]["last_outcome"] == "left_voicemail"
    assert "recommended_action" in items[0]


def test_followups_due_scoped(env):
    c = env["conn"]
    crm.create_activity(c, env["rep"], env["co1"],
                        {"activity_type": "call", "next_followup_at": _today() + "T10:00:00"})
    assert crm.followups_due(c, env["rep2"]) == []


# --------------------------------------------------------------------------
# Opportunities
# --------------------------------------------------------------------------

def test_opportunities_record_level(env):
    c = env["conn"]
    _project(c, env["co1"], score=90)
    _project(c, env["co2"], score=95)
    rep_view = crm.opportunities(c, env["rep"])
    assert rep_view["total"] == 1
    assert rep_view["items"][0]["company_id"] == env["co1"]
    # Manager sees both.
    assert crm.opportunities(c, env["mgr"])["total"] == 2


def test_opportunities_score_filter(env):
    c = env["conn"]
    _project(c, env["co1"], score=90)
    _project(c, env["co1"], score=30, days_ago=2)
    res = crm.opportunities(c, env["rep"], {"score_min": 50})
    assert res["total"] == 1 and res["items"][0]["opportunity_score"] == 90


def test_opportunities_default_to_verified_contractors_and_separate_research(env):
    c = env["conn"]
    _project(c, env["co1"], score=90)
    _project(c, env["co2"], score=95)
    c.execute(
        "UPDATE companies SET lead_type='verified_contractor', "
        "lead_verification_status='verified', lead_source='contractor_name', "
        "why_this_lead='Explicit contractor field' WHERE id=?",
        (env["co1"],),
    )
    c.execute(
        "UPDATE companies SET lead_type='specifier_architect_engineer', "
        "lead_verification_status='role_inferred', lead_source='PROFESS_NAME', "
        "why_this_lead='Phoenix permit professional' WHERE id=?",
        (env["co2"],),
    )
    c.execute(
        "UPDATE crm_company_relationships SET lead_type='verified_contractor', "
        "lead_verification_status='verified', lead_classification_source='contractor_name', "
        "why_this_lead='Explicit contractor field' WHERE company_id=?",
        (env["co1"],),
    )
    c.execute(
        "UPDATE crm_company_relationships SET lead_type='specifier_architect_engineer', "
        "lead_verification_status='role_inferred', lead_classification_source='PROFESS_NAME', "
        "why_this_lead='Phoenix permit professional' WHERE company_id=?",
        (env["co2"],),
    )
    c.commit()

    default_queue = crm.opportunities(c, env["mgr"])
    assert default_queue["classification_ready"] is True
    assert default_queue["active_lead_type"] == "verified_contractor"
    assert [item["company_id"] for item in default_queue["items"]] == [env["co1"]]
    assert default_queue["items"][0]["lead_source"] == "contractor_name"

    specifier_queue = crm.opportunities(
        c, env["mgr"], {"lead_type": "specifier_architect_engineer"}
    )
    assert [item["company_id"] for item in specifier_queue["items"]] == [env["co2"]]
    assert specifier_queue["items"][0]["why_this_lead"] == "Phoenix permit professional"

    all_queues = crm.opportunities(c, env["mgr"], {"lead_type": "all"})
    assert all_queues["total"] == 2


def test_opportunities_return_real_crm_pipeline_fields(env):
    c = env["conn"]
    project_id = _project(c, env["co1"], score=92, lifecycle="Permit Issued")
    c.execute("UPDATE companies SET main_phone='602-555-0100', lead_type='verified_contractor' WHERE id=?",
              (env["co1"],))
    c.execute("UPDATE crm_company_relationships SET lead_type='verified_contractor' WHERE company_id=?",
              (env["co1"],))
    now = _now()
    c.execute(
        "INSERT INTO material_lists (organization_id,company_id,project_id,created_by_user_id,"
        "status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        (env["org"], env["co1"], project_id, env["ids"]["rep"], "draft", now, now),
    )
    c.commit()
    crm.update_relationship(c, env["admin"], env["co1"], {"relationship_status": "qualified"})

    result = crm.opportunities(c, env["admin"], {"active_only": "1"})
    item = result["items"][0]
    assert item["relationship_status"] == "qualified"
    assert item["assigned_user_id"] == env["ids"]["rep"]
    assert item["assigned_to"]
    assert item["has_contact_info"] == 1
    assert item["material_list_count"] == 1
    assert item["latest_material_list_status"] == "draft"
    assert item["quote_request_count"] == 0

    crm.update_relationship(c, env["admin"], env["co1"], {"relationship_status": "lost"})
    assert crm.opportunities(c, env["admin"], {"active_only": "1"})["total"] == 0


def test_project_records_ui_is_verified_contractor_only():
    html = (PROJECT_ROOT / "opportunities.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "opportunities.js").read_text(encoding="utf-8")
    assert 'id="flead"' not in html
    assert "Verified contractor projects only" in html
    assert 'href="my-companies.html"' in html
    assert "Architect / Engineer research" not in html
    assert 'lead_type: "verified_contractor"' in script
    assert "classification_ready" in script
    assert "ApplyCorridorIQLeadIntegrity.bat" in script


def test_opportunity_board_uses_real_status_assignment_and_material_signals():
    html = (PROJECT_ROOT / "opportunity-board.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "opportunity-board.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "opportunity-board.css").read_text(encoding="utf-8")
    assert 'value="unassigned"' in html
    assert 'id="boardContact"' in html
    assert 'id="boardStageNav"' in html
    assert 'class="pipeline-queue"' in html
    for field in ("relationship_status", "assigned_to",
                  "material_list_count", "quote_request_count", "has_contact_info"):
        assert field in script
    for signal in ("estimated_material_value", "estimated_plumbing_scope",
                   "activeStage", "Build Material List", "Contact-ready in view"):
        assert signal in script
    assert 'active_only: "1"' in script
    assert "crm.relationships.update" in script
    assert "/relationship" in script
    assert ".pipeline-opportunity" in css
    assert "@media(max-width:900px)" in css
    assert "@media(max-width:620px)" in css
    assert ".crm-board" not in css


def test_company_executive_brief_is_evidence_based_printable_and_allowlisted():
    html = (PROJECT_ROOT / "company-executive-brief.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "company-executive-brief.js").read_text(encoding="utf-8")
    profile = (PROJECT_ROOT / "sales-company-profile.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "portal.css").read_text(encoding="utf-8")
    server = (PROJECT_ROOT / "pipeline" / "api" / "server.py").read_text(encoding="utf-8")

    assert "Company Executive Outreach Brief" in html
    assert "Print / Save PDF" in html
    assert "Copy briefing" in html
    assert "company-executive-brief.html?id=${companyId}" in profile
    for section in ("Executive summary", "Who to contact", "External intelligence and source coverage", "Project opportunity pipeline",
                    "Permit evidence", "Relationship position", "Risks and data gaps",
                    "Next best actions", "Evidence standard"):
        assert section in script
    for source in ("az_roc", "azcc", "az_ucc", "adot", "Not researched", "Open source"):
        assert source in script
    assert "UCC filing documents a financing record" in script
    assert "externalIntelligenceSection" in profile
    assert "Missing information is shown as unavailable and is not inferred" in script
    assert "window.print()" in script
    assert "@media print" in css
    assert '"company-executive-brief.html"' in server
    assert '"company-executive-brief.js"' in server


def test_admin_dashboard_exposes_live_build_and_data_provenance():
    html = (PROJECT_ROOT / "admin-dashboard.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "admin-dashboard.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "portal.css").read_text(encoding="utf-8")
    server = (PROJECT_ROOT / "pipeline" / "api" / "server.py").read_text(encoding="utf-8")

    assert 'id="verificationPanel"' in html
    assert "LIVE TEST PROOF" in script
    assert "Release 15.4 Secured Contractor Directory: ACTIVE" in script
    assert "Verified contractor directory" in script
    for field in ("build_id", "database_status", "public_permit_records",
                  "corridoriq_project_records", "imported_contact_records",
                  "external_evidence_records", "external_evidence_companies",
                  "external_evidence_sources", "live_public_bids",
                  "public_bid_planholders", "matched_public_planholders",
                  "project_material_estimates", "estimated_material_categories",
                  "crm_activity_records", "crm_relationship_records",
                  "jurisdiction_sources"):
        assert field in script
    for source in ("Municipal source", "Research import", "Official / researched", "Entered in CRM",
                   "CorridorIQ derived"):
        assert source in script
    assert ".verification-panel" in css
    assert 'dashboard["data_verification"]["executive_briefs"] = "active"' in server
    assert 'dashboard["data_verification"]["external_intelligence"] = "active"' in server


def test_external_intelligence_import_is_secure_allowlisted_and_source_backed():
    html = (PROJECT_ROOT / "intelligence-import.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "intelligence-import.js").read_text(encoding="utf-8")
    common = (PROJECT_ROOT / "portal-common.js").read_text(encoding="utf-8")
    server = (PROJECT_ROOT / "pipeline" / "api" / "server.py").read_text(encoding="utf-8")
    service = (PROJECT_ROOT / "pipeline" / "external_intelligence.py").read_text(encoding="utf-8")
    schema = (PROJECT_ROOT / "pipeline" / "db" / "schema.sql").read_text(encoding="utf-8")

    assert "Download CSV template" in html and "Download research queue" in html
    assert "UCC records are financing filings" in html
    for source in ("Arizona ROC", "Corporation Commission", "Arizona UCC", "ADOT advertisements"):
        assert source in html
    for route in ("/api/admin/external-intelligence/coverage", "/api/admin/external-intelligence/research-queue", "/api/admin/external-intelligence/preview", "/api/admin/external-intelligence/import", "/api/admin/external-intelligence/refresh", "/api/admin/external-intelligence/roc-preview", "/api/admin/external-intelligence/roc-import"):
        assert route in script and route in server
    assert 'CIQ.guard("admin.system"' in script
    assert '"intelligence-import.html", "intelligence-import.js"' in server
    assert "Intelligence Import" in common
    assert "backup_database(conn)" in service
    assert "a valid http(s) source_url is required" in service
    assert "CREATE TABLE IF NOT EXISTS company_external_evidence" in schema
    assert "CREATE TABLE IF NOT EXISTS external_intelligence_imports" in schema
    assert "CREATE TABLE IF NOT EXISTS public_bid_opportunities" in schema
    assert "CREATE TABLE IF NOT EXISTS public_bid_planholders" in schema
    assert "Refresh live ADOT data" in html
    assert "Official ROC active-license CSV" in html


def test_lead_integrity_rollout_is_backed_up_and_build_ids_match():
    rollout = (PROJECT_ROOT / "scripts" / "apply_lead_integrity.py").read_text(encoding="utf-8")
    one_click = (PROJECT_ROOT / "ApplyCorridorIQLeadIntegrity.bat").read_text(encoding="utf-8")
    launcher = (PROJECT_ROOT / "CorridorIQHQ.bat").read_text(encoding="utf-8")
    server = (PROJECT_ROOT / "pipeline" / "api" / "server.py").read_text(encoding="utf-8")

    assert "conn.backup(target)" in rollout
    assert "PRAGMA quick_check" in rollout
    assert "nonverified_project_links" in rollout
    assert "replace_permit_events=True" in rollout
    assert "apply_lead_integrity.py\" --apply" in one_click
    assert "2026-08-15-secured-contractor-directory-r15.4" in launcher
    assert 'BUILD_ID = "2026-08-15-secured-contractor-directory-r15.4"' in server


# --------------------------------------------------------------------------
# Personal activity feed
# --------------------------------------------------------------------------

def test_my_activity_is_author_scoped(env):
    c = env["conn"]
    crm.create_activity(c, env["rep"], env["co1"], {"activity_type": "note", "notes": "hi"})
    mine = crm.my_activity(c, env["rep"])
    assert mine["total"] == 1 and mine["items"][0]["display_name"] == "Alpha Plumbing"
    assert crm.my_activity(c, env["rep2"])["total"] == 0


# --------------------------------------------------------------------------
# Manager workload
# --------------------------------------------------------------------------

def test_team_overview_fields_and_permission(env):
    c = env["conn"]
    crm.create_activity(c, env["rep"], env["co1"],
                        {"activity_type": "call", "activity_outcome": "appointment_set"})
    team = crm_admin.team_overview(c, env["mgr"])
    rep_row = next(r for r in team if r["user_id"] == env["ids"]["rep"])
    for k in ("assigned_companies", "calls_completed", "appointments",
              "overdue_tasks", "followups_due", "companies_no_activity", "quote_requests"):
        assert k in rep_row
    assert rep_row["assigned_companies"] == 1
    assert rep_row["calls_completed"] == 1
    assert rep_row["appointments"] == 1
    # Reps cannot view team workload.
    with pytest.raises(AuthzError):
        crm_admin.team_overview(c, env["rep"])


# --------------------------------------------------------------------------
# Reports catalog + safe download
# --------------------------------------------------------------------------

def test_reports_catalog_permission(env):
    cat = reports_catalog.catalog(env["conn"], env["rep"])
    assert "reports" in cat and isinstance(cat["files"], list)
    assert any(r["key"] == "company_opportunity" for r in cat["reports"])


def test_safe_generated_path_blocks_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(reports_catalog.settings, "REPORTS_GENERATED_DIR", tmp_path)
    good = tmp_path / "company_opportunity.md"
    good.write_text("ok", encoding="utf-8")
    assert reports_catalog.safe_generated_path("company_opportunity.md") == good.resolve()
    assert reports_catalog.safe_generated_path("../secret.md") is None
    assert reports_catalog.safe_generated_path("nope.exe") is None
    assert reports_catalog.safe_generated_path("missing.md") is None


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def test_recommended_action_matrix():
    ra = crm.recommended_action
    assert ra("assigned", None, None) == "Make first contact"
    assert ra("won", None, "2026-01-01") == "Maintain relationship"
    assert ra("lost", None, "2026-01-01") == "Closed — lost"
    assert ra("contacted", None, None, do_not_contact=1) == "Do not contact"
    assert ra("quote_requested", None, "2026-01-01") == "Send quote"
    assert ra("contacted", "1999-01-01T00:00:00", "2026-01-01") == "Follow up now (due)"


# --------------------------------------------------------------------------
# Security regression — redesigned UI cannot reach unassigned data
# --------------------------------------------------------------------------

def test_rep_cannot_open_unassigned_company(env):
    with pytest.raises(AuthzError):
        crm.get_company_detail(env["conn"], env["rep"], env["co2"])


def test_rep_cannot_see_unassigned_opportunities(env):
    c = env["conn"]
    _project(c, env["co2"], score=99)
    res = crm.opportunities(c, env["rep"])
    assert all(i["company_id"] != env["co2"] for i in res["items"])


# --------------------------------------------------------------------------
# HTTP integration — routes, static pages, and enforcement are server-side
# --------------------------------------------------------------------------

import http.client
import threading
import time
from http.server import ThreadingHTTPServer


@pytest.fixture()
def http_server(tmp_path, monkeypatch):
    import pipeline.api.server as server_mod
    db_file = tmp_path / "s6_ui.db"

    def factory():
        cc = sqlite3.connect(db_file)
        cc.row_factory = sqlite3.Row
        cc.execute("PRAGMA foreign_keys = ON")
        return cc

    c = factory()
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    c.execute("INSERT INTO jurisdictions (slug, name, state, status) "
              "VALUES ('phoenix_az','Phoenix','AZ','connected')")
    seed_auth(c)
    org = _org(c)
    admin_id = auth.create_user(c, organization_id=org, email="uiadmin@corridoriq.com",
                                password="UiAdmin123", role_names=["admin"], must_change_password=False)
    rep_id = auth.create_user(c, organization_id=org, email="uirep@corridoriq.com",
                              password="UiRep123", role_names=["sales_representative"],
                              must_change_password=False)
    company_id = _company(c, "UI Quote Plumbing")
    crm.assign_company(c, _ctx(c, admin_id), company_id, rep_id, reason="HTTP RFQ test")
    project_id = _project(c, company_id, score=91, lifecycle="Permit Issued")
    c.execute("UPDATE companies SET main_phone='602-555-0199', lead_type='verified_contractor' WHERE id=?",
              (company_id,))
    c.execute("UPDATE crm_company_relationships SET lead_type='verified_contractor' WHERE company_id=?",
              (company_id,))
    now = _now()
    supplier_id = c.execute(
        "INSERT INTO suppliers (name,code,active,quote_contact_name,quote_email,created_at,updated_at) "
        "VALUES ('UI Ready Supply','ui-ready',1,'Taylor Quotes','quotes@ui-ready.example',?,?)",
        (now, now),
    ).lastrowid
    c.execute(
        "INSERT INTO company_external_evidence (company_id,source_type,source_agency,evidence_type,"
        "source_record_id,title,status,summary,source_url,retrieved_at,confidence,match_method,created_at,updated_at) "
        "VALUES (?,'az_roc','Arizona Registrar of Contractors','contractor_license','ROC-UI-123',"
        "'Arizona contractor license','Active','Classification CR-37','https://roc.az.gov/contractor-search',"
        "?,95,'license_number',?,?)", (company_id, now, now, now),
    )
    c.commit()
    c.close()

    monkeypatch.setattr(server_mod, "_factory", factory)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.ApiHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)
    yield {"port": port, "rep_id": rep_id, "company_id": company_id, "project_id": project_id,
           "supplier_id": supplier_id}
    srv.shutdown()


def _req(port, method, path, body=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    resp = conn.getresponse()
    raw = resp.read()
    sc = resp.getheader("Set-Cookie")
    ct = resp.getheader("Content-Type")
    conn.close()
    try:
        data = json.loads(raw) if raw and ct and "json" in ct else raw
    except json.JSONDecodeError:
        data = raw
    return resp.status, data, sc


def _login(port, email, password):
    _, _, sc = _req(port, "POST", "/api/auth/login", {"email": email, "password": password})
    return sc.split(";")[0]


def test_http_new_routes_require_auth(http_server):
    for path in ("/api/sales/opportunities", "/api/sales/activity",
                 "/api/sales/followups", "/api/reports/catalog"):
        status, _, _ = _req(http_server["port"], "GET", path)
        assert status == 401, path


def test_http_workspace_endpoints_ok(http_server):
    port = http_server["port"]
    cookie = _login(port, "uiadmin@corridoriq.com", "UiAdmin123")
    for path in ("/api/sales/dashboard", "/api/sales/opportunities",
                 "/api/sales/activity", "/api/sales/followups",
                 "/api/manager/team", "/api/reports/catalog"):
        status, data, _ = _req(port, "GET", path, cookie=cookie)
        assert status == 200, path
        assert isinstance(data, dict)
    estimate_path = (f"/api/material-lists/project-estimate?company_id={http_server['company_id']}"
                     f"&project_id={http_server['project_id']}")
    status, estimate, _ = _req(port, "GET", estimate_path, cookie=cookie)
    assert status == 200
    assert estimate["project"]["estimated_plumbing_scope"] == "Commercial Fixtures, Backflow, Valves"
    assert estimate["suggestions"][0]["quantity_status"] == "needs_confirmation"


def test_http_material_list_rfq_lifecycle(http_server):
    port = http_server["port"]
    cookie = _login(port, "uirep@corridoriq.com", "UiRep123")
    material_payload = {
        "companyId": http_server["company_id"],
        "companyName": "UI Quote Plumbing",
        "projectName": "456 Market Street",
        "neededBy": "2026-08-24",
        "quoteNeededBy": "2026-08-17",
        "jobsitePostalCode": "85004",
        "deliveryPreference": "delivery",
        "status": "ready for review",
        "rows": [{"qty": 4, "description": "Copper pipe", "unit": "length",
                  "allowSubstitution": True}],
    }
    status, saved, _ = _req(port, "POST", "/api/material-lists", material_payload, cookie)
    assert status == 200
    list_id = saved["item"]["id"]

    status, options, _ = _req(port, "GET", "/api/suppliers/quote-options", cookie=cookie)
    assert status == 200
    assert options["items"][0]["quote_email"] == "quotes@ui-ready.example"

    status, prepared, _ = _req(
        port, "POST", f"/api/material-lists/{list_id}/quote-requests",
        {"supplier_id": http_server["supplier_id"]}, cookie,
    )
    assert status == 201
    request_id = prepared["item"]["id"]
    assert prepared["item"]["status"] == "prepared"

    status, sent, _ = _req(
        port, "PATCH", f"/api/material-quote-requests/{request_id}",
        {"status": "sent"}, cookie,
    )
    assert status == 200 and sent["item"]["sent_at"]

    status, response, _ = _req(
        port, "PATCH", f"/api/material-quote-requests/{request_id}",
        {"status": "responded", "quoted_total": 425.75,
         "estimated_delivery_days": 2}, cookie,
    )
    assert status == 200 and response["item"]["quoted_total"] == 425.75

    status, history, _ = _req(
        port, "GET", f"/api/material-lists/{list_id}/quote-requests", cookie=cookie,
    )
    assert status == 200 and history["items"][0]["status"] == "responded"


def test_http_me_permissions_are_json_array(http_server):
    port = http_server["port"]
    cookie = _login(port, "uiadmin@corridoriq.com", "UiAdmin123")
    status, data, _ = _req(port, "GET", "/api/auth/me", cookie=cookie)
    assert status == 200
    # Nav gating depends on permissions being a real array, not a stringified set.
    assert isinstance(data["user"]["permissions"], list)
    assert "admin.system" in data["user"]["permissions"]
    assert isinstance(data["user"]["roles"], list)
    assert data["user"]["default_landing_page"] == "admin-dashboard.html"
    assert data["user"]["dashboard_mode"] == "organization"
    assert data["user"]["organization"]["name"]


def test_http_login_returns_role_landing_page(http_server):
    port = http_server["port"]
    status, data, sc = _req(port, "POST", "/api/auth/login",
                            {"email": "uiadmin@corridoriq.com", "password": "UiAdmin123"})
    assert status == 200
    assert data["user"]["default_landing_page"] == "admin-dashboard.html"
    assert sc and "HttpOnly" in sc

    status2, data2, _ = _req(port, "POST", "/api/auth/login",
                             {"email": "uirep@corridoriq.com", "password": "UiRep123"})
    assert status2 == 200
    assert data2["user"]["default_landing_page"] == "sales-dashboard.html"


def test_http_admin_dashboard_org_wide_not_assignment(http_server):
    port = http_server["port"]
    admin_cookie = _login(port, "uiadmin@corridoriq.com", "UiAdmin123")
    status, data, _ = _req(port, "GET", "/api/admin/dashboard", cookie=admin_cookie)
    assert status == 200
    assert data["assignment_scoped"] is False
    assert data["scope"] == "organization"
    assert "total_companies" in data["kpis"]
    assert "total_projects" in data["kpis"]
    assert "my_companies" not in data["kpis"]

    rep_cookie = _login(port, "uirep@corridoriq.com", "UiRep123")
    status2, _, _ = _req(port, "GET", "/api/admin/dashboard", cookie=rep_cookie)
    assert status2 == 403

    # Rep sales dashboard remains assignment-limited.
    status3, sales, _ = _req(port, "GET", "/api/sales/dashboard", cookie=rep_cookie)
    assert status3 == 200
    assert sales["assignment_scoped"] is True


def test_http_static_pages_served(http_server):
    port = http_server["port"]
    for name in ("sales-dashboard.html", "admin-dashboard.html", "team-dashboard.html",
                 "estimator-work-queue.html", "readonly-dashboard.html",
                 "my-companies.html", "opportunities.html",
                 "activity.html", "reports.html", "portal.css", "portal-common.js"):
        status, _, _ = _req(port, "GET", "/" + name)
        assert status == 200, name


def test_material_list_mobile_and_action_clarity_contract():
    html = (PROJECT_ROOT / "material-list-intake.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "material-list-intake.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "portal.css").read_text(encoding="utf-8")

    assert 'class="grid-cards material-workspace"' in html
    assert 'id="prepareRfqBtn"' in html and 'id="rfqSupplier"' in html
    assert 'accept=".csv,text/csv"' in html
    assert "Automatic import supports CSV" in html
    assert "PDF, Excel, CSV, photo, or screenshot" not in html
    assert 'id="draftSaveState"' in html
    assert 'id="projectEstimatePanel"' in html
    assert "/api/material-lists/project-estimate" in script
    assert "PROJECT-SPECIFIC PRELIMINARY ESTIMATE" in script
    assert "Add suggestions to material list" in script
    assert "Quantity confirmation required" in script
    assert "estimateConfidencePct" in script and "quantityStatus" in script
    assert all(action in script for action in
               ("Open Email", "Mark Sent", "Record response", "Mark declined", "Award quote"))
    assert "scheduleLocalSave" in script
    assert "persistLocalDraft" in script
    assert "CIQ.guardUnsaved(() => dirty)" in script
    for label in ("Quantity", "Item", "Unit", "Manufacturer", "Alternates", "Catalog match", "Action"):
        assert f'data-label="{label}"' in script
    # Catalog results render in a body-level portal so table scrolling cannot
    # clip them, and the two-column workspace collapses before mobile.
    assert ".catalog-suggestions-portal{position:fixed" in css
    assert 'catalogPopup.className = "catalog-suggestions catalog-suggestions-portal"' in script
    assert "@media(max-width:1180px){.material-workspace{grid-template-columns:1fr}" in css
    assert "@media(max-width:700px)" in css and ".bom-table-wrap{overflow:visible}" in css


def test_http_reports_download_blocks_traversal(http_server):
    port = http_server["port"]
    cookie = _login(port, "uiadmin@corridoriq.com", "UiAdmin123")
    status, _, _ = _req(port, "GET", "/api/reports/download?name=../server.py", cookie=cookie)
    assert status == 404
    status2, _, _ = _req(port, "GET", "/api/reports/download?name=nope.md", cookie=cookie)
    assert status2 == 404


def test_http_rep_denied_admin_and_manager_routes(http_server):
    port = http_server["port"]
    cookie = _login(port, "uirep@corridoriq.com", "UiRep123")
    for path in ("/api/admin/users", "/api/manager/team", "/api/manager/assignments"):
        status, _, _ = _req(port, "GET", path, cookie=cookie)
        assert status == 403, path


# --------------------------------------------------------------------------
# Login regression — POST route, JSON responses, cookie, no "Unsupported POST"
# --------------------------------------------------------------------------

def test_login_route_accepts_post_and_returns_json(http_server):
    port = http_server["port"]
    # Valid login -> 200 JSON + session cookie (never an "Unsupported" 501).
    status, data, sc = _req(port, "POST", "/api/auth/login",
                            {"email": "uiadmin@corridoriq.com", "password": "UiAdmin123"})
    assert status == 200
    assert isinstance(data, dict) and data.get("user", {}).get("email") == "uiadmin@corridoriq.com"
    assert sc and "HttpOnly" in sc  # session cookie created
    # Invalid login -> JSON error, not a page reload / not a 501.
    status2, data2, _ = _req(port, "POST", "/api/auth/login",
                             {"email": "uiadmin@corridoriq.com", "password": "wrong"})
    assert status2 == 401
    assert isinstance(data2, dict) and "error" in data2
    assert "Unsupported" not in json.dumps(data2)


def test_login_html_never_native_submits(http_server):
    """The login form must POST via fetch, never natively to login.html."""
    port = http_server["port"]
    status, body, _ = _req(port, "GET", "/login.html")
    assert status == 200
    html = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)
    assert 'onsubmit="return false"' in html
    assert "/api/auth/login" in html
    assert 'credentials: "same-origin"' in html
    # The form must not POST straight to a page.
    assert 'action="login.html"' not in html


def test_login_form_browser_flow(http_server):
    """End-to-end: submitting the form logs in, sets a cookie, and redirects;
    bad credentials show an inline error without leaving the page or hitting
    an 'Unsupported POST' error."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    import pipeline.api.server as server_mod

    base = f"http://127.0.0.1:{http_server['port']}"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception:
                pytest.skip("Chromium not installed for Playwright")
            ctx = browser.new_context()
            page = ctx.new_page()

            # Invalid credentials -> inline error, still on login.html.
            page.goto(base + "/login.html", wait_until="networkidle")
            page.fill("#email", "uiadmin@corridoriq.com")
            page.fill("#password", "wrong-password")
            page.click("#loginBtn")
            page.wait_for_selector("#loginMsg.error", timeout=5000)
            assert page.url.endswith("/login.html")
            # The stock http.server 501 page would read "Unsupported method ('POST')".
            assert "Unsupported method" not in page.content()

            # Valid credentials -> redirect to the role landing page + session cookie.
            page.fill("#password", "UiAdmin123")
            page.click("#loginBtn")
            page.wait_for_url("**/admin-dashboard.html", timeout=8000)
            names = {c["name"] for c in ctx.cookies()}
            assert server_mod.COOKIE in names
            browser.close()
    except Exception as exc:  # environment without a usable browser
        if "Executable doesn't exist" in str(exc) or "chromium" in str(exc).lower():
            pytest.skip(f"Playwright browser unavailable: {exc}")
        raise


def test_opportunity_board_browser_flow_uses_real_crm_state(http_server):
    """The executive queue must show real signals, persist status changes,
    filter by stage, and remain actionable on a phone-sized viewport."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{http_server['port']}"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("Chromium not installed for Playwright")
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/login.html")
        page.fill("#email", "uiadmin@corridoriq.com")
        page.fill("#password", "UiAdmin123")
        page.click("#loginBtn")
        page.wait_for_url("**/admin-dashboard.html")
        page.goto(base + "/opportunity-board.html", wait_until="networkidle")

        card = page.locator(".op-card")
        assert card.count() == 1
        assert "UI Quote Plumbing" in card.inner_text()
        assert "Contact ready" in card.inner_text()
        assert "Assigned" in card.inner_text()
        commercial = card.locator(".pipeline-commercial").inner_text()
        assert "estimated material opportunity" in commercial.lower()
        assert "Commercial Fixtures, Backflow, Valves" in commercial
        assert page.get_by_role("link", name="Build Material List").is_visible()
        card.locator("select[data-status-company]").select_option("qualified")
        page.wait_for_selector('.op-card[data-stage="qualified"]', timeout=8000)
        page.locator('[data-stage-filter="qualified"]').click()
        assert page.locator('.op-card[data-stage="qualified"]').count() == 1
        assert page.locator("#queueTitle").inner_text() == "Qualified"

        page.set_viewport_size({"width": 390, "height": 800})
        page.locator('.op-card[data-stage="qualified"]').scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        mobile_card = page.locator('.op-card[data-stage="qualified"]')
        card_box = mobile_card.bounding_box()
        assert card_box and card_box["x"] >= 0 and card_box["x"] + card_box["width"] <= 390
        assert mobile_card.get_by_role("link", name="Build Material List").is_visible()
        assert errors == []
        browser.close()


def test_company_executive_brief_browser_flow(http_server):
    """A company profile opens a complete evidence-based report without
    requiring an export or exposing non-allowlisted assets."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{http_server['port']}"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("Chromium not installed for Playwright")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/login.html")
        page.fill("#email", "uiadmin@corridoriq.com")
        page.fill("#password", "UiAdmin123")
        page.click("#loginBtn")
        page.wait_for_url("**/admin-dashboard.html")
        proof = page.locator("#verificationPanel")
        proof.wait_for(state="visible")
        assert "LIVE TEST PROOF" in proof.inner_text()
        assert "Release 15.4 Secured Contractor Directory: ACTIVE" in proof.inner_text()
        assert "2026-08-15-secured-contractor-directory-r15.4" in proof.inner_text()
        assert "Verified contractor directory" in proof.inner_text()
        assert "External evidence" in proof.inner_text()
        assert "Municipal source" in proof.inner_text()
        assert "Entered in CRM" in proof.inner_text()
        page.goto(base + f"/sales-company-profile.html?id={http_server['company_id']}", wait_until="networkidle")
        page.get_by_role("link", name="Executive brief").click()
        page.wait_for_url("**/company-executive-brief.html?id=*")
        brief = page.locator("#executiveBrief")
        brief.wait_for(state="visible")
        assert "UI Quote Plumbing" in brief.inner_text()
        assert "Executive summary" in brief.inner_text()
        assert "Who to contact" in brief.inner_text()
        assert "External intelligence and source coverage" in brief.inner_text()
        assert "Arizona contractor license" in brief.inner_text()
        assert "Not researched" in brief.inner_text()
        assert "Project opportunity pipeline" in brief.inner_text()
        assert "Permit evidence" in brief.inner_text()
        assert "Risks and data gaps" in brief.inner_text()
        assert "Evidence standard" in brief.inner_text()
        assert page.get_by_role("button", name="Print / Save PDF").is_visible()
        assert errors == []
        browser.close()


def test_project_material_estimate_prefills_editable_bom_in_browser(http_server):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{http_server['port']}"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("Chromium not installed for Playwright")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/login.html")
        page.fill("#email", "uirep@corridoriq.com")
        page.fill("#password", "UiRep123")
        page.click("#loginBtn")
        page.wait_for_url("**/sales-dashboard.html")
        page.goto(base + f"/material-list-intake.html?company_id={http_server['company_id']}"
                         f"&project_id={http_server['project_id']}", wait_until="networkidle")
        panel = page.locator("#projectEstimatePanel")
        panel.wait_for(state="visible")
        assert "PROJECT-SPECIFIC PRELIMINARY ESTIMATE" in panel.inner_text()
        assert "Commercial Fixtures" in panel.inner_text()
        assert "Quantity confirmation required" in panel.inner_text()
        assert page.get_by_label("Description").count() == 2
        assert page.get_by_label("Quantity").first.input_value() == ""
        page.get_by_role("button", name="Submit for Review").click()
        assert page.get_by_text("Confirm quantities for all 2 suggested material items.").is_visible()
        assert errors == []
        browser.close()


def test_cart_dropdown_checkout_mouse_touch_and_keyboard(http_server):
    """Regression: the shared cart stays interactive and on-screen through checkout."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{http_server['port']}"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("Chromium not installed for Playwright")

        def login(page):
            page.goto(base + "/login.html")
            page.fill("#email", "uiadmin@corridoriq.com")
            page.fill("#password", "UiAdmin123")
            page.click("#loginBtn")
            page.wait_for_url("**/admin-dashboard.html")

        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page)
        page.evaluate("""CIQ.cart.clear();
          CIQ.cart.add({productId:1,supplierId:10,name:'Copper Pipe',sku:'CP-10',supplier:'Acme',unitPrice:12.5,quantity:2});
          CIQ.cart.add({productId:2,supplierId:11,name:'Valve',sku:'VL-2',supplier:'Best Supply',unitPrice:5,quantity:1});""")
        page.click("#ciqCartBtn")
        assert page.locator("#ciqCartDropdown").is_visible()
        assert page.locator(".cart-item").count() == 2
        assert page.locator("#ciqCartSubtotal").inner_text() == "$30.00"
        page.locator('[data-cart-key="1:10"] [data-cart-action="increase"]').click()
        assert page.locator("#ciqCartTotal").inner_text() == "$42.50"
        page.locator('[data-cart-key="2:11"] [data-cart-action="remove"]').click()
        assert page.locator(".cart-item").count() == 1
        page.keyboard.press("Escape")
        assert page.locator("#ciqCartDropdown").is_hidden()
        page.click("#ciqCartBtn")
        page.click("#content")
        assert page.locator("#ciqCartDropdown").is_hidden()
        page.click("#ciqCartBtn")
        page.click("#ciqCheckoutBtn")
        page.wait_for_url("**/checkout.html")
        assert "Copper Pipe" in page.locator("#checkoutLines").inner_text()
        assert page.locator("#placeOrderBtn").is_visible()
        page.click("#ciqCartBtn")
        assert page.locator("#ciqCartDropdown").is_visible()
        assert page.locator("#ciqCartBtn").get_attribute("aria-expanded") == "true"
        assert page.locator("#ciqCartDropdown .cart-item").count() == 1
        page.click("#ciqCartBtn")
        assert page.locator("#ciqCartDropdown").is_hidden()
        page.click("#placeOrderBtn")
        page.wait_for_url("**/material-list-intake.html?from_cart=1")
        assert page.get_by_label("Description").input_value() == "Copper Pipe"
        assert page.locator("#rfqPanel").is_visible()
        assert errors == []
        ctx.close()

        mobile = browser.new_context(viewport={"width": 390, "height": 700}, has_touch=True, is_mobile=True)
        phone = mobile.new_page()
        mobile_errors = []
        phone.on("pageerror", lambda error: mobile_errors.append(str(error)))
        login(phone)
        phone.evaluate("CIQ.cart.add({productId:3,supplierId:12,name:'Long mobile product description',sku:'M-3',supplier:'Mobile Supply',unitPrice:9.99,quantity:4})")
        phone.tap("#ciqCartBtn")
        panel = phone.locator("#ciqCartDropdown")
        assert panel.is_visible()
        box = panel.bounding_box()
        checkout_box = phone.locator("#ciqCheckoutBtn").bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390
        assert checkout_box and checkout_box["y"] + checkout_box["height"] <= 700
        phone.tap('[data-cart-action="increase"]')
        assert phone.locator("#ciqCartTotal").inner_text() == "$49.95"
        phone.tap("#ciqCheckoutBtn")
        phone.wait_for_url("**/checkout.html")
        phone.tap("#ciqCartBtn")
        checkout_panel = phone.locator("#ciqCartDropdown")
        assert checkout_panel.is_visible()
        checkout_panel_box = checkout_panel.bounding_box()
        checkout_button_box = phone.locator("#ciqCheckoutBtn").bounding_box()
        assert checkout_panel_box and checkout_panel_box["x"] >= 0
        assert checkout_panel_box["x"] + checkout_panel_box["width"] <= 390
        assert checkout_button_box and checkout_button_box["y"] + checkout_button_box["height"] <= 700
        phone.tap("#ciqCartBtn")
        phone.tap("#placeOrderBtn")
        phone.wait_for_url("**/material-list-intake.html?from_cart=1")
        assert phone.get_by_label("Description").input_value() == "Long mobile product description"
        assert mobile_errors == []
        mobile.close()
        browser.close()


def test_material_catalog_popup_uses_body_portal_and_all_input_modes(http_server):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{http_server['port']}"
    products = {"total": 2, "items": [
        {"product_id": 101, "sku": "COP-1", "supplier_sku": "S-COP-1", "product_name": "Copper Pipe", "manufacturer": "CopperCo", "manufacturer_part_number": "CP1", "unit_of_measure": "each", "supplier_price": 12.5, "quantity_available": 40, "lead_time_days": 0},
        {"product_id": 102, "sku": "COP-2", "supplier_sku": "S-COP-2", "product_name": "Copper Coupling", "manufacturer": "CopperCo", "manufacturer_part_number": "CP2", "unit_of_measure": "each", "supplier_price": 4.25, "quantity_available": 80, "lead_time_days": 1},
    ]}

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("Chromium not installed for Playwright")

        def prepare(context, viewport):
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/products/search?**", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(products)))
            page.goto(base + "/login.html")
            page.fill("#email", "uiadmin@corridoriq.com")
            page.fill("#password", "UiAdmin123")
            page.click("#loginBtn")
            page.wait_for_url("**/admin-dashboard.html")
            page.goto(base + "/material-list-intake.html")
            return page, errors

        desktop = browser.new_context(viewport={"width": 1920, "height": 1080})
        page, errors = prepare(desktop, (1920, 1080))
        field = page.get_by_label("Description")
        field.fill("copper")
        popup = page.locator(".catalog-suggestions-portal")
        popup.wait_for(state="visible")
        assert popup.locator(".catalog-option").count() == 2
        assert popup.evaluate("node => node.parentElement === document.body")
        input_box, popup_box = field.bounding_box(), popup.bounding_box()
        assert input_box and popup_box and popup_box["y"] >= input_box["y"] + input_box["height"]
        assert popup_box["x"] >= 0 and popup_box["x"] + popup_box["width"] <= 1920
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert field.input_value() == "Copper Pipe"
        assert page.get_by_text("Matched", exact=True).is_visible()

        field.fill("copper")
        popup.wait_for(state="visible")
        page.keyboard.press("Escape")
        assert popup.is_hidden()
        field.fill("copper")
        popup.wait_for(state="visible")
        page.get_by_label("Contractor / company").click()
        assert popup.is_hidden()
        page.get_by_label("Contractor / company").fill("Autosave Plumbing")
        page.get_by_label("Project / job address").fill("500 Saved Draft Way")
        field.fill("Copper draft survives navigation")
        page.wait_for_function("document.querySelector('#draftSaveState').textContent.includes('Saved on this device')")
        page.reload(wait_until="networkidle")
        assert page.get_by_label("Contractor / company").input_value() == "Autosave Plumbing"
        assert page.get_by_label("Project / job address").input_value() == "500 Saved Draft Way"
        assert page.get_by_label("Description").input_value() == "Copper draft survives navigation"
        assert errors == []
        desktop.close()

        mobile = browser.new_context(viewport={"width": 390, "height": 700}, is_mobile=True, has_touch=True)
        phone, mobile_errors = prepare(mobile, (390, 700))
        mobile_field = phone.get_by_label("Description")
        mobile_field.evaluate("node => node.scrollIntoView({block:'end'})")
        mobile_field.fill("copper")
        mobile_popup = phone.locator(".catalog-suggestions-portal")
        mobile_popup.wait_for(state="visible")
        mobile_box = mobile_popup.bounding_box()
        assert mobile_box and mobile_box["x"] >= 0 and mobile_box["x"] + mobile_box["width"] <= 390
        assert mobile_popup.evaluate("node => node.parentElement === document.body")
        assert "opens-above" in (mobile_popup.get_attribute("class") or "")
        phone.tap('.catalog-option[data-option="1"]')
        assert mobile_field.input_value() == "Copper Coupling"
        assert mobile_popup.is_hidden()
        assert mobile_errors == []
        mobile.close()
        browser.close()
