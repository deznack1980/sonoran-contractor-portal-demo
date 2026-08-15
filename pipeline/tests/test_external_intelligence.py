"""Release 15: traceable multi-source evidence import and reporting."""

from __future__ import annotations

import base64
import csv
from datetime import datetime, timezone
import io
import sqlite3

import pytest

from pipeline import external_intelligence
from pipeline.auth import service as auth
from pipeline.auth.seed import seed_auth
from pipeline.config.settings import SCHEMA_PATH
from pipeline.crm import service as crm


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@pytest.fixture()
def env(tmp_path):
    db_path = tmp_path / "external-intelligence.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    seed_auth(conn)
    org = conn.execute("SELECT id FROM organizations WHERE slug='corridoriq'").fetchone()["id"]
    admin_id = auth.create_user(
        conn, organization_id=org, email="admin@corridoriq.com",
        password="AdminPass123", role_names=["admin"], must_change_password=False,
    )
    company_id = conn.execute(
        "INSERT INTO companies (normalized_name,display_name,legal_name,license_number,"
        "lifecycle_state,created_at,updated_at) VALUES (?,?,?,?, 'active',?,?)",
        ("ACME CONTRACTING LLC", "ACME Contracting LLC", "ACME Contracting LLC", "ROC-123456", _now(), _now()),
    ).lastrowid
    conn.execute(
        "INSERT INTO crm_company_relationships (organization_id,company_id,relationship_status,created_at,updated_at) "
        "VALUES (?,?,'new',?,?)", (org, company_id, _now(), _now()),
    )
    conn.commit()
    user = auth.build_user_context(conn, conn.execute("SELECT * FROM users WHERE id=?", (admin_id,)).fetchone())
    yield {"conn": conn, "user": user, "company_id": company_id, "org": org, "db_path": db_path}
    conn.close()


def _upload(company_id, *, source_type="az_roc", source_url="https://roc.az.gov/contractor-search",
            record_id="ROC-123456", title="Arizona contractor license", summary="Classification CR-37"):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=external_intelligence.CSV_COLUMNS)
    writer.writeheader()
    writer.writerow({
        "company_id": company_id, "company_name": "ACME Contracting LLC",
        "source_type": source_type, "evidence_type": "contractor_license" if source_type == "az_roc" else "ucc_filing",
        "source_record_id": record_id, "title": title, "status": "Active",
        "summary": summary, "amount": "", "effective_date": "2026-01-15",
        "expiration_date": "2031-01-15", "source_url": source_url,
        "retrieved_at": _now(), "confidence": "95", "match_method": "license_number",
    })
    return {"filename": "verified_evidence.csv", "content_base64": base64.b64encode(output.getvalue().encode()).decode()}


def test_preview_import_coverage_and_company_detail(env):
    body = _upload(env["company_id"])
    preview = external_intelligence.preview_upload(env["conn"], env["user"], body)
    assert preview["counts"] == {"input_rows": 1, "importable": 1, "rejected": 0}

    result = external_intelligence.import_upload(env["conn"], env["user"], body)
    assert result["counts"]["created_rows"] == 1
    assert (env["db_path"].with_name(result["backup_file"])).exists()

    coverage = external_intelligence.organization_coverage(env["conn"], env["org"])
    roc = next(item for item in coverage["sources"] if item["source_type"] == "az_roc")
    assert roc["companies"] == 1 and roc["records"] == 1
    queue_item = external_intelligence.research_queue(env["conn"], env["org"])["items"][0]
    assert queue_item["company_id"] == env["company_id"]
    assert "az_roc" not in queue_item["needed_sources"]
    assert {"azcc", "az_ucc", "adot"}.issubset(queue_item["needed_sources"])

    detail = crm.get_company_detail(env["conn"], env["user"], env["company_id"])
    assert detail["external_evidence"][0]["source_url"] == "https://roc.az.gov/contractor-search"
    assert next(item for item in detail["source_coverage"] if item["source_type"] == "az_roc")["status"] == "available"
    assert next(item for item in detail["source_coverage"] if item["source_type"] == "azcc")["status"] == "not_researched"


def test_duplicate_source_record_updates_instead_of_multiplying(env):
    body = _upload(env["company_id"], summary="Initial classification")
    external_intelligence.import_upload(env["conn"], env["user"], body)
    changed = _upload(env["company_id"], summary="Updated verified classification")
    result = external_intelligence.import_upload(env["conn"], env["user"], changed)
    assert result["counts"]["created_rows"] == 0
    assert result["counts"]["updated_rows"] == 1
    row = env["conn"].execute("SELECT summary FROM company_external_evidence").fetchone()
    assert row["summary"] == "Updated verified classification"
    assert env["conn"].execute("SELECT COUNT(*) AS n FROM company_external_evidence").fetchone()["n"] == 1


def test_invalid_source_url_blocks_import(env):
    body = _upload(env["company_id"], source_url="javascript:alert(1)")
    preview = external_intelligence.preview_upload(env["conn"], env["user"], body)
    assert preview["counts"]["rejected"] == 1
    assert "valid http(s) source_url" in " ".join(preview["rejections"][0]["reasons"])
    with pytest.raises(ValueError, match="Import blocked"):
        external_intelligence.import_upload(env["conn"], env["user"], body)


def test_official_source_type_rejects_nonofficial_domain(env):
    body = _upload(env["company_id"], source_url="https://example.com/roc-record")
    preview = external_intelligence.preview_upload(env["conn"], env["user"], body)
    assert preview["counts"]["rejected"] == 1
    assert "official Arizona Registrar of Contractors domain" in " ".join(preview["rejections"][0]["reasons"])


def test_import_cannot_target_company_outside_admin_organization(env):
    other = env["conn"].execute(
        "INSERT INTO companies (normalized_name,display_name,legal_name,lifecycle_state,created_at,updated_at) "
        "VALUES ('ACME CONTRACTING LLC','ACME Contracting LLC','ACME Contracting LLC','active',?,?)",
        (_now(), _now()),
    ).lastrowid
    env["conn"].commit()
    preview = external_intelligence.preview_upload(env["conn"], env["user"], _upload(other))
    assert preview["counts"]["rejected"] == 1
    assert "not enrolled in this organization" in " ".join(preview["rejections"][0]["reasons"])


def test_ucc_record_stays_neutral_financing_evidence(env):
    body = _upload(
        env["company_id"], source_type="az_ucc", source_url="https://azsos.gov/business/ucc",
        record_id="202600000001", title="UCC financing statement",
        summary="Secured party and filing status only; no distress inference",
    )
    external_intelligence.import_upload(env["conn"], env["user"], body)
    row = env["conn"].execute("SELECT * FROM company_external_evidence").fetchone()
    assert row["source_type"] == "az_ucc"
    assert "distress inference" in row["summary"]
    assert not any(word in row["title"].casefold() for word in ("distress", "credit risk", "delinquent"))
