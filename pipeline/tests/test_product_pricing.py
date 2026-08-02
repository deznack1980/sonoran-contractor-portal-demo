"""Sprint 6 tests — multi-supplier product pricing.

Covers Sonoran import, zero-price exclusion, idempotency, product dedup,
supplier-specific pricing, search, delivery-cost math, lowest-total ranking,
out-of-stock/missing-price handling, and permission enforcement (sales are
read-only; only admins manage suppliers/catalogs/assumptions).
"""

from __future__ import annotations

import base64
import sqlite3
from datetime import datetime, timezone

import pytest

from pipeline.auth import service as auth
from pipeline.auth.rbac import AuthzError
from pipeline.auth.seed import seed_auth
from pipeline.config import settings
from pipeline.config.settings import SCHEMA_PATH
from pipeline.products import service as products
from pipeline.products.importer import import_catalog
from pipeline.products.routing import (
    compare_offers,
    delivery_cost,
    estimate_miles,
    haversine_miles,
)
from pipeline.products.search import search_products


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    seed_auth(c)
    return c


def _ctx(c, user_id):
    row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return auth.build_user_context(c, row)


@pytest.fixture()
def users(conn):
    org = conn.execute("SELECT id FROM organizations LIMIT 1").fetchone()["id"]
    admin_id = auth.create_user(conn, organization_id=org, email="admin@corridoriq.com",
                                password="AdminPass123", role_names=["admin"],
                                must_change_password=False)
    rep_id = auth.create_user(conn, organization_id=org, email="rep@corridoriq.com",
                              password="RepPass123", role_names=["sales_representative"],
                              must_change_password=False)
    return {"admin": _ctx(conn, admin_id), "rep": _ctx(conn, rep_id), "org": org}


def _attr(cost=None, mpn=None, uom="ea", lead="14 days"):
    parts = []
    if cost is not None:
        parts.append(f"cost={cost}")
    parts.append(f"ecc_default_uom={uom}")
    parts.append(f"ecc_lead_time={lead}")
    if mpn is not None:
        parts.append(f"mpn={mpn}")
    return ",".join(parts)


def _sonoran_rows():
    return [
        {"sku": "A1", "name": "Copper Strap", "description": "1/2 copper strap",
         "categories": "Default Category/PIPE/STRAPS", "price": "0.440000",
         "qty": "1549.0000", "is_in_stock": "1",
         "additional_attributes": _attr(cost="0.18", mpn="99999968847")},
        {"sku": "B2", "name": "Water Heater 75gal", "description": "75 gallon heater",
         "categories": "Default Category/WATER HEATERS", "price": "997.500000",
         "qty": "10.0000", "is_in_stock": "1",
         "additional_attributes": _attr(cost="700.00", mpn="WH75")},
        # Duplicate SKU of A1 (update, not a new product).
        {"sku": "A1", "name": "Copper Strap", "description": "1/2 copper strap",
         "categories": "Default Category/PIPE/STRAPS", "price": "0.500000",
         "qty": "1200.0000", "is_in_stock": "1",
         "additional_attributes": _attr(cost="0.20", mpn="99999968847")},
        # Zero-price row — excluded.
        {"sku": "C3", "name": "Freebie", "description": "no price",
         "categories": "Default Category/MISC", "price": "0.000000",
         "qty": "5.0000", "is_in_stock": "1",
         "additional_attributes": _attr(cost="1.00", mpn="FREE")},
        # Missing MPN.
        {"sku": "D4", "name": "Mystery Fitting", "description": "no mpn",
         "categories": "Default Category/FITTINGS", "price": "3.250000",
         "qty": "40.0000", "is_in_stock": "1",
         "additional_attributes": _attr(cost="1.50")},
    ]


def _import_sonoran(conn):
    return import_catalog(
        conn, supplier_code="sonoran", rows=_sonoran_rows(),
        source_file="test.csv", profile="sonoran_magento",
        supplier_name="Sonoran Supply",
        supplier_location={"city": "Phoenix", "state": "AZ",
                           "latitude": 33.4484, "longitude": -112.0740})


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

def test_sonoran_import_counts_and_dedup(conn):
    stats = _import_sonoran(conn)
    assert stats.total_rows == 5
    assert stats.zero_price_excluded == 1          # C3
    assert stats.duplicate_skus == 1               # second A1
    assert stats.missing_mpn == 1                  # D4
    # Products: A1, B2, D4 (C3 excluded) = 3 unique.
    assert conn.execute("SELECT COUNT(*) n FROM products").fetchone()["n"] == 3
    assert conn.execute("SELECT COUNT(*) n FROM supplier_products").fetchone()["n"] == 3
    # Zero-price product never created.
    assert conn.execute("SELECT COUNT(*) n FROM products WHERE sku='C3'").fetchone()["n"] == 0


def test_zero_price_excluded_from_offers(conn):
    _import_sonoran(conn)
    row = conn.execute("SELECT * FROM supplier_products sp JOIN products p ON p.id=sp.product_id "
                       "WHERE p.sku='C3'").fetchone()
    assert row is None


def test_duplicate_sku_updates_same_product(conn):
    _import_sonoran(conn)
    # A1 appeared twice; the later row (price 0.50) wins, still ONE product.
    rows = conn.execute("SELECT sp.selling_price FROM supplier_products sp "
                        "JOIN products p ON p.id=sp.product_id WHERE p.sku='A1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["selling_price"] == 0.50


def test_idempotent_rerun(conn):
    _import_sonoran(conn)
    products_before = conn.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
    stats = _import_sonoran(conn)
    assert stats.imported == 0
    # 4 update ops: A1, B2, D4 + the duplicate A1 row updates the same offer again.
    assert stats.updated == 4
    assert stats.products_created == 0
    assert conn.execute("SELECT COUNT(*) n FROM products").fetchone()["n"] == products_before


def test_attributes_parsed(conn):
    _import_sonoran(conn)
    p = conn.execute("SELECT * FROM products WHERE sku='A1'").fetchone()
    assert p["manufacturer_part_number"] == "99999968847"
    assert p["unit_of_measure"] == "ea"
    assert p["category"] == "PIPE/STRAPS"
    sp = conn.execute("SELECT * FROM supplier_products WHERE product_id=?", (p["id"],)).fetchone()
    assert sp["lead_time_days"] == 14
    assert sp["cost_price"] == 0.20


# --------------------------------------------------------------------------
# Supplier-specific pricing (generic profile / second supplier)
# --------------------------------------------------------------------------

def _import_supplier_b(conn):
    rows = [
        {"Item": "A1", "Price": "0.39", "Stock": "500", "Name": "Copper Strap",
         "Cost": "0.15", "PartNo": "99999968847"},
        {"Item": "Z9", "Price": "12.00", "Stock": "8", "Name": "B-only widget",
         "Cost": "6.00", "PartNo": "Z9PART"},
    ]
    mapping = {"sku": "Item", "price": "Price", "quantity": "Stock",
               "product_name": "Name", "cost": "Cost",
               "manufacturer_part_number": "PartNo"}
    return import_catalog(conn, supplier_code="supplierb", rows=rows,
                          source_file="b.csv", profile="generic", mapping=mapping,
                          supplier_name="Supplier B",
                          supplier_location={"city": "Tucson", "state": "AZ",
                                             "latitude": 32.2226, "longitude": -110.9747})


def test_supplier_specific_pricing_shares_product(conn):
    _import_sonoran(conn)
    _import_supplier_b(conn)
    # A1 is shared: one master product, two supplier offers with different prices.
    prod = conn.execute("SELECT id FROM products WHERE sku='A1'").fetchone()
    offers = conn.execute("SELECT selling_price FROM supplier_products WHERE product_id=?",
                          (prod["id"],)).fetchall()
    assert len(offers) == 2
    assert {round(o["selling_price"], 2) for o in offers} == {0.50, 0.39}
    # Total distinct products = A1,B2,D4,Z9 = 4
    assert conn.execute("SELECT COUNT(*) n FROM products").fetchone()["n"] == 4


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def test_search_by_various_fields(conn):
    _import_sonoran(conn)
    assert search_products(conn, "A1")["total"] >= 1                    # sku
    assert search_products(conn, "99999968847")["total"] >= 1           # mpn
    assert search_products(conn, "Water Heater")["total"] >= 1          # name
    assert search_products(conn, "gallon")["total"] >= 1               # description
    assert search_products(conn, "", category="WATER HEATERS")["total"] >= 1


def test_search_excludes_cost_by_default(conn):
    _import_sonoran(conn)
    res = search_products(conn, "A1", include_cost=False)
    assert all("cost_price" not in it for it in res["items"])
    res2 = search_products(conn, "A1", include_cost=True)
    assert any("cost_price" in it for it in res2["items"])


# --------------------------------------------------------------------------
# Delivery cost + routing
# --------------------------------------------------------------------------

def _cfg(**over):
    base = {"delivery_per_mile": 0.21, "delivery_base_fee": 0.0,
            "delivery_min_charge": 0.0, "delivery_markup_pct": 0.0,
            "delivery_handling_cost": 0.0, "delivery_road_factor": 1.25,
            "delivery_default_miles": 20.0}
    base.update(over)
    return base


def test_delivery_cost_math():
    assert delivery_cost(100, _cfg()) == 21.0
    assert delivery_cost(100, _cfg(delivery_base_fee=15)) == 36.0
    # Min charge floor.
    assert delivery_cost(10, _cfg(delivery_min_charge=50)) == 50.0
    # Markup.
    assert delivery_cost(100, _cfg(delivery_markup_pct=10)) == pytest.approx(23.1)


def test_haversine_and_estimate_miles():
    miles = haversine_miles(33.4484, -112.0740, 32.2226, -110.9747)  # PHX->TUS
    assert 100 < miles < 140
    row = {"latitude": 33.4484, "longitude": -112.0740}
    m, basis = estimate_miles(row, 32.2226, -110.9747, _cfg())
    assert m > 0 and "road factor" in basis
    # Missing coords -> default.
    m2, basis2 = estimate_miles({"latitude": None, "longitude": None}, None, None, _cfg())
    assert m2 == 20.0 and "default" in basis2


def test_lowest_total_cost_ranking(conn):
    """A cheaper unit price with a longer route must not automatically win."""
    _import_sonoran(conn)            # Sonoran A1 @ 0.50, Phoenix warehouse
    _import_supplier_b(conn)         # Supplier B A1 @ 0.39, Tucson warehouse (far)
    prod = conn.execute("SELECT id FROM products WHERE sku='A1'").fetchone()
    result = compare_offers(conn, prod["id"], 2, {"city": "Phoenix", "state": "AZ"})
    ranked = result["offers"]
    # Sonoran (local) should rank #1 despite higher unit price, because delivery
    # from Tucson dominates the small material difference.
    assert ranked[0]["supplier"] == "Sonoran Supply"
    assert ranked[0]["rank"] == 1
    assert result["recommended_supplier_id"] == ranked[0]["supplier_id"]
    # Both offers considered.
    assert len(ranked) == 2


def test_out_of_stock_not_recommended(conn):
    _import_sonoran(conn)            # B2 has qty 10
    prod = conn.execute("SELECT id FROM products WHERE sku='B2'").fetchone()
    result = compare_offers(conn, prod["id"], 50, {"city": "Phoenix", "state": "AZ"})
    offer = result["offers"][0]
    assert offer["available"] is False
    assert offer["rank"] is None
    assert "insufficient_inventory" in offer["warnings"]
    assert result["recommended_supplier_id"] is None


def test_missing_price_flagged_and_excluded(conn):
    _import_sonoran(conn)
    prod = conn.execute("SELECT id FROM products WHERE sku='B2'").fetchone()
    sup = conn.execute("SELECT id FROM suppliers WHERE code='sonoran'").fetchone()
    # Insert a second supplier offer with NO price (data gap).
    conn.execute("INSERT INTO suppliers (name, code, active, created_at, updated_at) "
                 "VALUES ('Gap Supply','gap',1,?,?)", (_now(), _now()))
    gap = conn.execute("SELECT id FROM suppliers WHERE code='gap'").fetchone()["id"]
    conn.execute("INSERT INTO supplier_products (supplier_id, product_id, selling_price, "
                 "quantity_available, active, imported_at, updated_at) "
                 "VALUES (?,?,?,?,1,?,?)", (gap, prod["id"], None, 100, _now(), _now()))
    conn.commit()
    result = compare_offers(conn, prod["id"], 2, {"city": "Phoenix", "state": "AZ"})
    gap_offer = next(o for o in result["offers"] if o["supplier"] == "Gap Supply")
    assert "missing_price" in gap_offer["warnings"]
    assert gap_offer["rank"] is None
    # The priced supplier is still recommended.
    assert result["recommended_supplier_id"] is not None


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------

def test_sales_can_search_and_quote(conn, users):
    _import_sonoran(conn)
    res = products.search(conn, users["rep"], {"q": "A1"})
    assert res["total"] >= 1
    # Sales never sees cost fields.
    assert all("cost_price" not in it for it in res["items"])
    prod = conn.execute("SELECT id FROM products WHERE sku='B2'").fetchone()
    q = products.quote(conn, users["rep"], prod["id"], 2, {"city": "Phoenix", "state": "AZ"})
    assert all("cost_price" not in o for o in q["offers"])


def test_sales_cannot_manage_catalog(conn, users):
    with pytest.raises(AuthzError):
        products.add_supplier(conn, users["rep"], {"code": "x", "name": "X"})
    with pytest.raises(AuthzError):
        products.import_catalog_upload(conn, users["rep"],
                                       {"supplier": "sonoran", "filename": "f.csv",
                                        "content_base64": base64.b64encode(b"sku,price\n").decode()})
    with pytest.raises(AuthzError):
        products.update_delivery_settings(conn, users["rep"], {"delivery_per_mile": 0.5})
    with pytest.raises(AuthzError):
        products.import_history(conn, users["rep"])


def test_admin_can_manage_catalog(conn, users):
    sup = products.add_supplier(conn, users["admin"],
                                {"code": "acme", "name": "Acme", "city": "Mesa", "state": "AZ"})
    assert sup["code"] == "acme"
    csv_text = "sku,price,qty,name\nP1,5.00,10,Widget\nP2,0,3,FreeThing\n"
    payload = {"supplier": "acme", "filename": "acme.csv",
               "content_base64": base64.b64encode(csv_text.encode()).decode(),
               "profile": "generic",
               "mapping": {"sku": "sku", "price": "price", "quantity": "qty",
                           "product_name": "name"}}
    preview = products.preview_catalog(conn, users["admin"], payload)
    assert preview["counts"]["zero_price_excluded"] == 1
    assert preview["counts"]["new_products"] == 1
    stats = products.import_catalog_upload(conn, users["admin"], payload)
    assert stats["imported"] == 1
    assert stats["zero_price_excluded"] == 1
    hist = products.import_history(conn, users["admin"])
    assert len(hist) >= 1


def test_admin_sees_cost_fields(conn, users):
    _import_sonoran(conn)
    res = products.search(conn, users["admin"], {"q": "A1"})
    assert any("cost_price" in it for it in res["items"])


def test_admin_can_change_delivery_assumptions(conn, users):
    cfg = products.update_delivery_settings(conn, users["admin"], {"delivery_per_mile": 0.5})
    assert cfg["delivery_per_mile"] == 0.5
    # Persisted + reflected in a fresh compare.
    _import_sonoran(conn)
    prod = conn.execute("SELECT id FROM products WHERE sku='B2'").fetchone()
    result = compare_offers(conn, prod["id"], 1, {"city": "Tucson", "state": "AZ"})
    assert result["offers"][0]["estimated_delivery_cost"] > 0
