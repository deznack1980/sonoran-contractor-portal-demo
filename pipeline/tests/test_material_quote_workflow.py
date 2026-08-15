"""Revenue workflow tests for material-list supplier RFQs.

The first launch workflow intentionally stays supplier-agnostic and
email-first: CorridorIQ prepares a standardized request, snapshots the named
recipient, then tracks sent, response, and award states without exposing one
supplier's response to another.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from pipeline.auth import service as auth
from pipeline.auth.rbac import AuthzError
from pipeline.auth.seed import seed_auth
from pipeline.config.settings import SCHEMA_PATH
from pipeline.crm import service as crm
from pipeline.material_lists import service as material_lists
from pipeline.products import service as products


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ctx(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return auth.build_user_context(conn, row)


def _make_user(conn: sqlite3.Connection, org: int, email: str, role: str) -> int:
    """Create a service-test user without spending time on a password hash."""
    now = _now()
    user_id = conn.execute(
        "INSERT INTO users (organization_id,email,normalized_email,password_hash,display_name,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
        (org, email, email.lower(), "not-used-in-service-tests", email.split("@")[0], now, now),
    ).lastrowid
    role_id = conn.execute("SELECT id FROM roles WHERE name=?", (role,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO user_roles (user_id,role_id,assigned_at) VALUES (?,?,?)",
        (user_id, role_id, now),
    )
    conn.commit()
    return user_id


@pytest.fixture()
def env():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    seed_auth(conn)
    org = conn.execute("SELECT id FROM organizations WHERE slug='corridoriq'").fetchone()["id"]

    admin_id = _make_user(conn, org, "admin@corridoriq.com", "admin")
    manager_id = _make_user(conn, org, "manager@corridoriq.com", "sales_manager")
    rep_id = _make_user(conn, org, "rep@corridoriq.com", "sales_representative")
    other_rep_id = _make_user(conn, org, "other@corridoriq.com", "sales_representative")
    now = _now()
    company_id = conn.execute(
        "INSERT INTO companies (normalized_name,display_name,city,state,created_at,updated_at) "
        "VALUES ('ACME PLUMBING','Acme Plumbing','Phoenix','AZ',?,?)",
        (now, now),
    ).lastrowid
    conn.commit()

    admin = _ctx(conn, admin_id)
    manager = _ctx(conn, manager_id)
    crm.assign_company(conn, manager, company_id, rep_id, reason="RFQ workflow test")
    rep = _ctx(conn, rep_id)

    ready_supplier = products.add_supplier(
        conn, admin,
        {"code": "ready_supply", "name": "Ready Supply", "city": "Phoenix", "state": "AZ",
         "quote_contact_name": "Riley Quotes", "quote_email": "quotes@ready.example",
         "quote_phone": "602-555-0100"},
    )
    incomplete_supplier = products.add_supplier(
        conn, admin, {"code": "no_email", "name": "No Email Supply"},
    )
    yield {
        "conn": conn, "org": org, "company": company_id, "admin": admin,
        "rep": rep, "other_rep": _ctx(conn, other_rep_id),
        "ready_supplier": ready_supplier, "incomplete_supplier": incomplete_supplier,
    }
    conn.close()


def _save_list(env: dict) -> dict:
    return material_lists.save(
        env["conn"], env["rep"],
        {
            "companyId": env["company"],
            "companyName": "Acme Plumbing",
            "projectName": "123 Main Street",
            "neededBy": "2026-08-24",
            "quoteNeededBy": "2026-08-17",
            "jobsitePostalCode": "85004",
            "deliveryPreference": "delivery",
            "requestNotes": "Call before delivery.",
            "status": "ready for review",
            "rows": [
                {"qty": 12, "description": "1/2 inch copper pipe", "unit": "length",
                 "manufacturer": "Approved equivalent", "sku": "CU-050",
                 "allowSubstitution": True},
                {"qty": 2, "description": "Backflow preventer", "unit": "each",
                 "manufacturer": "Watts", "sku": "009M3QT",
                 "allowSubstitution": False},
            ],
        },
    )["item"]


def test_material_list_persists_required_rfq_fields_and_alternate_policy(env):
    material_list = _save_list(env)
    assert material_list["project_name"] == "123 Main Street"
    assert material_list["quote_needed_by"] == "2026-08-17"
    assert material_list["jobsite_postal_code"] == "85004"
    assert [row["allow_substitution"] for row in material_list["rows"]] == [1, 0]


def test_supplier_options_prioritize_quote_ready_contacts(env):
    options = material_lists.quote_supplier_options(env["conn"], env["rep"])["items"]
    assert [item["name"] for item in options] == ["Ready Supply", "No Email Supply"]
    assert options[0]["quote_email"] == "quotes@ready.example"
    assert options[0]["quote_contact_name"] == "Riley Quotes"


def test_only_admin_can_maintain_valid_supplier_quote_contacts(env):
    updated = products.update_supplier(
        env["conn"], env["admin"], env["ready_supplier"]["id"],
        {"quote_contact_name": "New Quote Desk", "quote_email": "NEW@READY.EXAMPLE",
         "quote_phone": "602-555-0199"},
    )
    assert updated["quote_email"] == "new@ready.example"

    with pytest.raises(crm.ValidationError, match="not valid"):
        products.update_supplier(
            env["conn"], env["admin"], env["ready_supplier"]["id"],
            {"quote_email": "not-an-email"},
        )
    with pytest.raises(AuthzError, match="supplier_pricing.manage"):
        products.update_supplier(
            env["conn"], env["rep"], env["ready_supplier"]["id"],
            {"quote_email": "quotes@ready.example"},
        )


def test_prepare_send_response_and_award_quote(env):
    material_list = _save_list(env)
    prepared = material_lists.prepare_quote_request(
        env["conn"], env["rep"], material_list["id"],
        {"supplier_id": env["ready_supplier"]["id"]},
    )["item"]

    assert prepared["status"] == "prepared"
    assert prepared["recipient_email"] == "quotes@ready.example"
    assert "Jobsite ZIP: 85004" in prepared["message"]
    assert "Project / job: 123 Main Street" in prepared["message"]
    assert "Quote due by: 2026-08-17" in prepared["message"]
    assert "12 length" in prepared["message"]
    assert "Alternates allowed" in prepared["message"]
    assert "Exact item only" in prepared["message"]
    assert "No Email Supply" not in prepared["message"]

    sent = material_lists.update_quote_request(
        env["conn"], env["rep"], prepared["id"], {"status": "sent"},
    )["item"]
    assert sent["sent_at"] and sent["status"] == "sent"

    competing = material_lists.prepare_quote_request(
        env["conn"], env["rep"], material_list["id"],
        {"supplier_id": env["incomplete_supplier"]["id"]},
    )["item"]
    assert competing["status"] == "prepared"

    with pytest.raises(crm.ValidationError, match="cannot move"):
        material_lists.update_quote_request(
            env["conn"], env["rep"], prepared["id"], {"status": "awarded"},
        )

    responded = material_lists.update_quote_request(
        env["conn"], env["rep"], prepared["id"],
        {"status": "responded", "quoted_total": 1842.55,
         "estimated_delivery_days": 3, "valid_until": "2026-08-31",
         "response_notes": "Freight included."},
    )["item"]
    assert responded["quoted_total"] == 1842.55
    assert responded["estimated_delivery_days"] == 3

    awarded = material_lists.update_quote_request(
        env["conn"], env["rep"], prepared["id"], {"status": "awarded"},
    )["item"]
    assert awarded["status"] == "awarded"
    assert awarded["quoted_total"] == 1842.55
    competing_after_award = material_lists.list_quote_requests(
        env["conn"], env["rep"], material_list["id"],
    )["items"]
    assert next(item for item in competing_after_award if item["id"] == competing["id"])["status"] == "cancelled"


def test_supplier_contact_is_snapshotted_and_missing_email_blocks_sent(env):
    material_list = _save_list(env)
    request = material_lists.prepare_quote_request(
        env["conn"], env["rep"], material_list["id"],
        {"supplier_id": env["ready_supplier"]["id"]},
    )["item"]
    products.update_supplier(
        env["conn"], env["admin"], env["ready_supplier"]["id"],
        {"quote_contact_name": "New Contact", "quote_email": "new@ready.example"},
    )
    historical = material_lists.list_quote_requests(
        env["conn"], env["rep"], material_list["id"],
    )["items"][0]
    assert historical["recipient_email"] == "quotes@ready.example"

    missing = material_lists.prepare_quote_request(
        env["conn"], env["rep"], material_list["id"],
        {"supplier_id": env["incomplete_supplier"]["id"]},
    )["item"]
    with pytest.raises(crm.ValidationError, match="quote email"):
        material_lists.update_quote_request(
            env["conn"], env["rep"], missing["id"], {"status": "sent"},
        )


def test_assignment_and_permission_boundaries_protect_quote_records(env):
    material_list = _save_list(env)
    request = material_lists.prepare_quote_request(
        env["conn"], env["rep"], material_list["id"],
        {"supplier_id": env["ready_supplier"]["id"]},
    )["item"]

    with pytest.raises(crm.ValidationError, match="assigned"):
        material_lists.list_quote_requests(env["conn"], env["other_rep"], material_list["id"])

    read_only_id = _make_user(env["conn"], env["org"], "readonly@corridoriq.com", "read_only")
    read_only = _ctx(env["conn"], read_only_id)
    with pytest.raises(AuthzError, match="products.quote"):
        material_lists.update_quote_request(
            env["conn"], read_only, request["id"], {"status": "sent"},
        )
