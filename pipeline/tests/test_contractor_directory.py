"""Release 15.4 secured contractor-directory regressions."""

from __future__ import annotations

import pytest

from pipeline.auth.rbac import AuthzError
from pipeline.config.settings import PROJECT_ROOT
from pipeline.contractors.directory import list_contractors
from pipeline.contractors.rebuild import rebuild_contractors
from pipeline.tests.test_verified_contractor_rebuild import _fixture_population, conn


def _user(*permissions):
    return {"id": 1, "organization_id": 1, "permissions": list(permissions), "roles": []}


def test_directory_returns_only_verified_contractors_with_metric_parity(conn):
    _fixture_population(conn)
    rebuild_contractors(conn)
    payload = list_contractors(conn, _user("companies.view"), {"page_size": 10})
    assert payload["source"] == "verified_canonical_company_api"
    assert payload["total"] == 1
    assert payload["stats"]["total"] == 1
    assert payload["stats"]["contact_ready"] == 1
    item = payload["items"][0]
    assert item["display_name"] == "Desert Flow Plumbing LLC"
    assert item["permit_count"] == 2
    assert item["jurisdictions_worked"] == ["mesa_az", "phoenix_az"]
    assert item["jurisdiction_breakdown"] == {"mesa_az": 1, "phoenix_az": 1}
    assert item["commercial_pct"] == 100.0
    assert item["estimated_material_opportunity"] == 30000.0
    assert item["verification_status"] == "verified"
    assert item["classification_source"] == "permit_party_evidence"
    assert item["has_contact_info"] is True


def test_directory_filters_and_rejects_unprivileged_users(conn):
    _fixture_population(conn)
    rebuild_contractors(conn)
    assert list_contractors(conn, _user("companies.view"), {"q": "missing"})["total"] == 0
    assert list_contractors(conn, _user("companies.view"), {"contact": "missing"})["total"] == 0
    assert list_contractors(conn, _user("companies.view"), {"multi": "1"})["total"] == 1
    with pytest.raises(AuthzError):
        list_contractors(conn, _user("companies.view_assigned"), {})


def test_directory_page_has_no_static_export_fallback():
    html = (PROJECT_ROOT / "contractors.html").read_text(encoding="utf-8")
    js = (PROJECT_ROOT / "contractors.js").read_text(encoding="utf-8")
    server = (PROJECT_ROOT / "pipeline/api/server.py").read_text(encoding="utf-8")
    common = (PROJECT_ROOT / "portal-common.js").read_text(encoding="utf-8")
    assert "portal.css" in html and "portal-common.js" in html
    assert 'CIQ.guard("companies.view"' in js
    assert 'CIQ.api.get("/api/contractors?' in js
    assert "contractor_matching.json" not in js
    assert '"contractors.html", "contractors.js"' in server
    assert 'path == "/api/contractors"' in server
    assert 'href: "contractors.html"' in common
