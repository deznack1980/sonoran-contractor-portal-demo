"""Connector, migration, classification, and rollback regressions."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from pipeline.company_resolution.lead_role_correction import (
    apply_lead_role_correction,
    migration_report,
    rollback_lead_role_correction,
)
from pipeline.company_resolution.normalize import normalize_company_name
from pipeline.config.settings import SCHEMA_PATH
from pipeline.db.database import migrate_schema
from pipeline.crm.service import list_my_companies
from pipeline.ingestion.upsert import upsert_permit
from pipeline.connectors.arcgis_hub import (
    build_goodyear_connector,
    build_peoria_connector,
    build_phoenix_connector,
    build_scottsdale_connector,
    build_tempe_connector,
)
from pipeline.connectors.socrata import build_mesa_connector


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def test_connectors_only_map_explicit_contractor_fields():
    mesa = build_mesa_connector().map_record({"contractor_name":"Mesa GC", "applicant":"Mesa Applicant"})
    assert mesa["general_contractor_name"] == "Mesa GC"
    assert mesa["applicant_name"] == "Mesa Applicant"
    assert build_mesa_connector().map_record({"applicant":"Applicant Only"})["general_contractor_name"] is None

    peoria = build_peoria_connector().map_record({
        "Applicant_Contact_Organization":"Design Applicant", "Applicant_Contact_Name":"A Person"})
    assert peoria["general_contractor_name"] is None
    assert peoria["applicant_organization"] == "Design Applicant"

    phoenix = build_phoenix_connector().map_record({"PROFESS_NAME":"Engineer Inc", "PERMIT_NAME":"Project LLC"})
    assert phoenix["general_contractor_name"] is None
    assert phoenix["permit_professional_name"] == "Engineer Inc"

    scottsdale = build_scottsdale_connector().map_record({"Builder":"Builder LLC", "ResponsibleParty":"Owner LLC"})
    assert scottsdale["general_contractor_name"] == "Builder LLC"
    assert scottsdale["responsible_party_name"] == "Owner LLC"
    assert build_scottsdale_connector().map_record({"ResponsibleParty":"Owner LLC"})["general_contractor_name"] is None
    assert build_scottsdale_connector().map_record({"Builder":"TBD"})["general_contractor_name"] is None

    tempe = build_tempe_connector().map_record({"ProjectName":"Project Holdings LLC"})
    assert tempe["general_contractor_name"] is None
    assert build_tempe_connector().map_record({"ContractorCompanyName":"Tempe GC"})["general_contractor_name"] == "Tempe GC"

    goodyear = build_goodyear_connector().map_record({"GenConName":"Goodyear GC"})
    assert goodyear["general_contractor_name"] == "Goodyear GC"
    assert goodyear["contractor_verification_status"] == "verified"


def test_schema_migration_is_additive_and_idempotent():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
      CREATE TABLE permits(id INTEGER PRIMARY KEY);
      CREATE TABLE companies(id INTEGER PRIMARY KEY);
      CREATE TABLE company_roles(id INTEGER PRIMARY KEY);
      CREATE TABLE crm_company_relationships(id INTEGER PRIMARY KEY);
    """)
    migrate_schema(c)
    migrate_schema(c)
    permit_cols = {r["name"] for r in c.execute("PRAGMA table_info(permits)")}
    company_cols = {r["name"] for r in c.execute("PRAGMA table_info(companies)")}
    crm_cols = {r["name"] for r in c.execute("PRAGMA table_info(crm_company_relationships)")}
    assert {"applicant_name","permit_professional_name","contractor_source_field","lead_type"} <= permit_cols
    assert {"lead_type","lead_verification_status","lead_source","why_this_lead"} <= company_cols
    assert {"lead_type","lead_verification_status","lead_classification_source","why_this_lead"} <= crm_cols
    c.close()


def test_upsert_clears_stale_company_link_when_contractor_identity_changes(conn):
    company_id = _company(conn,"Old Applicant LLC")
    permit_id = _permit(conn,"mesa_az","UPSERT-1",{"applicant":"Old Applicant LLC"},"Old Applicant LLC",company_id)
    mapped = {"jurisdiction":"mesa_az","permit_number":"UPSERT-1","general_contractor_name":None,
              "raw_source_json":json.dumps({"applicant":"Old Applicant LLC"})}
    upsert_permit(conn,mapped)
    assert conn.execute("SELECT contractor_company_id FROM permits WHERE id=?",(permit_id,)).fetchone()[0] is None
    assert conn.execute("SELECT contractor_company_id FROM projects WHERE permit_id=?",(permit_id,)).fetchone()[0] is None


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    for slug in ("mesa_az","peoria_az","phoenix_az","scottsdale_az","tempe_az","goodyear_az"):
        c.execute("INSERT INTO jurisdictions(slug,name,state,status) VALUES (?,?, 'AZ','connected')", (slug,slug))
    now = _now()
    c.execute("INSERT INTO organizations(name,slug,created_at,updated_at) VALUES ('Test','test',?,?)", (now,now))
    return c


def _company(c, name):
    now = _now()
    cur = c.execute(
        "INSERT INTO companies(display_name,normalized_name,lifecycle_state,created_at,updated_at) VALUES (?,?, 'active',?,?)",
        (name,normalize_company_name(name),now,now),
    )
    c.execute("INSERT INTO company_roles(company_id,role_type,is_primary,source,confidence,created_at,updated_at) VALUES (?, 'contractor',1,'permits',.8,?,?)", (cur.lastrowid,now,now))
    c.execute("INSERT INTO crm_company_relationships(organization_id,company_id,relationship_status,created_at,updated_at) VALUES (1,?,'new',?,?)", (cur.lastrowid,now,now))
    return cur.lastrowid


def _permit(c, jurisdiction, num, raw, mapped_name, company_id):
    now = _now()
    cur = c.execute(
        """INSERT INTO permits(jurisdiction,permit_number,general_contractor_name,
           contractor_company_id,raw_source_json,first_seen_at,last_updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (jurisdiction,num,mapped_name,company_id,json.dumps(raw),now,now),
    )
    c.execute("INSERT INTO projects(permit_id,jurisdiction,contractor_company_id) VALUES (?,?,?)", (cur.lastrowid,jurisdiction,company_id))
    return cur.lastrowid


def test_migration_rebuilds_links_classifies_crm_and_is_reversible(conn):
    verified = _company(conn,"Mesa GC LLC")
    specifier = _company(conn,"Derek Engineering Inc")
    owner = _company(conn,"Desert Properties LLC")
    unverified = _company(conn,"Jane Applicant")
    p_verified = _permit(conn,"mesa_az","M1",{"contractor_name":"Mesa GC LLC","applicant":"Jane Applicant"},"Mesa GC LLC",verified)
    p_specifier = _permit(conn,"phoenix_az","P1",{"PROFESS_NAME":"Derek Engineering Inc","PERMIT_NAME":"Warehouse"},"Derek Engineering Inc",specifier)
    p_owner = _permit(conn,"scottsdale_az","S1",{"Builder":None,"ResponsibleParty":"Desert Properties LLC","Owner":"Desert Properties LLC"},"Desert Properties LLC",owner)
    p_unverified = _permit(conn,"peoria_az","PE1",{"Applicant_Contact_Name":"Jane Applicant"},"Jane Applicant",unverified)
    conn.commit()

    result = apply_lead_role_correction(conn)
    assert result["after"] == {"mesa_az":1}
    assert conn.execute("SELECT contractor_company_id FROM permits WHERE id=?", (p_verified,)).fetchone()[0] == verified
    for pid in (p_specifier,p_owner,p_unverified):
        assert conn.execute("SELECT contractor_company_id FROM permits WHERE id=?", (pid,)).fetchone()[0] is None
        assert conn.execute("SELECT contractor_company_id FROM projects WHERE permit_id=?", (pid,)).fetchone()[0] is None

    types = {r["company_id"]:r["lead_type"] for r in conn.execute("SELECT company_id,lead_type FROM crm_company_relationships")}
    assert types[verified] == "verified_contractor"
    assert types[specifier] == "specifier_architect_engineer"
    assert types[owner] == "owner_developer"
    assert types[unverified] == "unverified_permit_contact"
    assert conn.execute("SELECT applicant_name FROM permits WHERE id=?", (p_unverified,)).fetchone()[0] == "Jane Applicant"
    assert migration_report(conn)["permit_project_mismatches"] == 0

    second = apply_lead_role_correction(conn)
    assert second["after"] == {"mesa_az":1}
    assert conn.execute("SELECT COUNT(*) FROM lead_role_assignment_history").fetchone()[0] == 4

    assert rollback_lead_role_correction(conn) == 4
    assert conn.execute("SELECT contractor_company_id FROM permits WHERE id=?", (p_specifier,)).fetchone()[0] == specifier
    assert conn.execute("SELECT general_contractor_name FROM permits WHERE id=?", (p_owner,)).fetchone()[0] == "Desert Properties LLC"


def test_explicit_field_placeholder_is_preserved_but_never_linked(conn):
    placeholder = _company(conn,"TBD")
    conn.execute("DELETE FROM crm_company_relationships WHERE company_id=?",(placeholder,))
    conn.execute("UPDATE companies SET source_system='permits' WHERE id=?",(placeholder,))
    pid = _permit(conn,"scottsdale_az","S-TBD",{"Builder":"TBD"},"TBD",placeholder)
    conn.commit()
    result = apply_lead_role_correction(conn)
    row = conn.execute("SELECT general_contractor_name,contractor_company_id,lead_type FROM permits WHERE id=?",(pid,)).fetchone()
    assert row["general_contractor_name"] is None
    assert row["contractor_company_id"] is None
    evidence = conn.execute("SELECT verification_status,is_contractor_evidence FROM permit_party_evidence WHERE permit_id=? AND source_field='Builder'",(pid,)).fetchone()
    assert dict(evidence) == {"verification_status":"unverified","is_contractor_evidence":0}
    assert result["assignment_stats"]["placeholder_companies_deprecated"] == 1
    assert conn.execute("SELECT lifecycle_state FROM companies WHERE id=?",(placeholder,)).fetchone()[0] == "deprecated"


def test_verified_contractor_queue_defaults_and_ranks_contact_first(conn):
    contact_ready = _company(conn,"Contact Ready Builders LLC")
    recent_no_contact = _company(conn,"Recent Builders LLC")
    unverified = _company(conn,"Unverified Applicant")
    conn.execute("UPDATE companies SET main_phone='602-555-0100',lead_type='verified_contractor' WHERE id=?", (contact_ready,))
    conn.execute("UPDATE companies SET lead_type='verified_contractor' WHERE id=?", (recent_no_contact,))
    conn.execute("UPDATE companies SET lead_type='unverified_permit_contact' WHERE id=?", (unverified,))
    conn.execute("UPDATE crm_company_relationships SET lead_type='verified_contractor' WHERE company_id IN (?,?)", (contact_ready,recent_no_contact))
    conn.execute("UPDATE crm_company_relationships SET lead_type='unverified_permit_contact' WHERE company_id=?", (unverified,))
    conn.execute("INSERT INTO company_intelligence(company_id,active_projects,projects_last_30_days,highest_opportunity_score,estimated_opportunity_total,model_version) VALUES (?,?,?,?,?,?)", (contact_ready,1,1,70,1000,"test"))
    conn.execute("INSERT INTO company_intelligence(company_id,active_projects,projects_last_30_days,highest_opportunity_score,estimated_opportunity_total,model_version) VALUES (?,?,?,?,?,?)", (recent_no_contact,5,5,99,9000,"test"))
    conn.commit()
    user = {"id":1,"organization_id":1,"permissions":{"companies.view","crm.relationships.view"}}
    result = list_my_companies(conn,user,{"page_size":20})
    assert [x["company_id"] for x in result["items"]] == [contact_ready,recent_no_contact]
    all_queues = list_my_companies(conn,user,{"page_size":20,"lead_type":"all"})
    assert all_queues["total"] == 3


def test_company_ui_exposes_lead_classification_controls():
    root = SCHEMA_PATH.parents[2]
    companies_html = (root / "companies.html").read_text(encoding="utf-8")
    my_companies_html = (root / "my-companies.html").read_text(encoding="utf-8")
    my_companies_js = (root / "my-companies.js").read_text(encoding="utf-8")
    profile_js = (root / "sales-company-profile.js").read_text(encoding="utf-8")
    for lead_type in ("verified_contractor","specifier_architect_engineer","owner_developer","unverified_permit_contact"):
        assert lead_type in companies_html
        assert lead_type in my_companies_html or lead_type in my_companies_js
    assert "Why this lead" in companies_html
    assert "Why this lead" in profile_js
