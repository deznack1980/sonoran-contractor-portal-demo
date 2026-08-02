"""V1 fulfillment comparison engine.

"Cheapest" = lowest estimated TOTAL fulfillment cost
(materials + delivery + handling), NOT lowest unit price. A supplier with a
lower unit price but a longer route or insufficient inventory may rank lower,
and an unavailable supplier is never auto-recommended.
"""

from __future__ import annotations

import math
import sqlite3

from pipeline.config import settings
from pipeline.products.config import get_delivery_config


def haversine_miles(lat1, lng1, lat2, lng2) -> float:
    r = 3958.7613  # Earth radius (miles)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def resolve_jobsite_coords(conn: sqlite3.Connection, jobsite: dict):
    """Return (lat, lng, basis) for a jobsite spec.

    Accepts explicit lat/lng, a city/state (matched to CITY_CENTROIDS), a
    permit_id, or a company_id — in that order of preference.
    """
    jobsite = jobsite or {}
    lat, lng = jobsite.get("lat"), jobsite.get("lng")
    if lat is not None and lng is not None:
        return float(lat), float(lng), "explicit coordinates"

    if jobsite.get("permit_id"):
        row = conn.execute("SELECT latitude, longitude, city, state FROM permits WHERE id=?",
                           (jobsite["permit_id"],)).fetchone()
        if row:
            if row["latitude"] is not None and row["longitude"] is not None:
                return row["latitude"], row["longitude"], "permit coordinates"
            jobsite = {"city": row["city"], "state": row["state"]}

    if jobsite.get("company_id"):
        row = conn.execute("SELECT latitude, longitude, city, state FROM companies WHERE id=?",
                           (jobsite["company_id"],)).fetchone()
        if row:
            if row["latitude"] is not None and row["longitude"] is not None:
                return row["latitude"], row["longitude"], "company coordinates"
            jobsite = {"city": row["city"], "state": row["state"]}

    city = (jobsite.get("city") or "").strip().upper()
    state = (jobsite.get("state") or "AZ").strip().upper()
    coords = settings.CITY_CENTROIDS.get((city, state))
    if coords:
        return coords[0], coords[1], f"{city.title()}, {state} centroid"
    return None, None, "unknown"


def estimate_miles(supplier_row, job_lat, job_lng, cfg) -> tuple[float, str]:
    """Estimate one-way route miles from supplier warehouse to jobsite."""
    slat, slng = supplier_row["latitude"], supplier_row["longitude"]
    if slat is None or slng is None or job_lat is None or job_lng is None:
        return float(cfg["delivery_default_miles"]), "default (missing coordinates)"
    miles = haversine_miles(slat, slng, job_lat, job_lng) * cfg["delivery_road_factor"]
    return round(miles, 1), "great-circle × road factor"


def delivery_cost(miles: float, cfg: dict) -> float:
    variable = miles * cfg["delivery_per_mile"]
    cost = cfg["delivery_base_fee"] + variable
    if cfg["delivery_markup_pct"]:
        cost *= (1 + cfg["delivery_markup_pct"] / 100.0)
    if cfg["delivery_min_charge"] and cost < cfg["delivery_min_charge"]:
        cost = cfg["delivery_min_charge"]
    return round(cost, 2)


def compare_offers(conn: sqlite3.Connection, product_id: int, quantity: float,
                   jobsite: dict, *, include_cost: bool = False) -> dict:
    """Rank every supplier offer for a product by total estimated fulfillment cost."""
    cfg = get_delivery_config(conn)
    qty = max(float(quantity or 1), 0.0) or 1.0
    job_lat, job_lng, job_basis = resolve_jobsite_coords(conn, jobsite)
    handling = round(cfg["delivery_handling_cost"], 2)

    product = conn.execute(
        "SELECT id, sku, product_name, manufacturer, manufacturer_part_number, "
        "category, unit_of_measure FROM products WHERE id=?", (product_id,)).fetchone()
    if product is None:
        return {"error": "unknown product"}

    rows = conn.execute(
        """
        SELECT sp.*, s.name AS supplier_name, s.code AS supplier_code,
               s.city AS supplier_city, s.state AS supplier_state,
               s.warehouse_address, s.latitude, s.longitude, s.active AS supplier_active
        FROM supplier_products sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE sp.product_id=?
        """,
        (product_id,),
    ).fetchall()

    offers = []
    for r in rows:
        warnings = []
        price = r["selling_price"]
        missing_price = price is None or price <= 0
        if missing_price:
            warnings.append("missing_price")
        material_subtotal = round((price or 0) * qty, 2)

        miles, miles_basis = estimate_miles(r, job_lat, job_lng, cfg)
        if miles_basis.startswith("default"):
            warnings.append("estimated_miles_default")
        delivery = delivery_cost(miles, cfg)

        qavail = r["quantity_available"]
        in_stock = bool(r["active"]) and bool(r["supplier_active"])
        if qavail is None:
            warnings.append("unknown_inventory")
            available = in_stock
        else:
            available = in_stock and qavail >= qty
            if qavail <= 0:
                warnings.append("out_of_stock")
            elif qavail < qty:
                warnings.append("insufficient_inventory")
        if not in_stock:
            warnings.append("inactive_offer")
        if r["lead_time_days"] is None:
            warnings.append("missing_lead_time")

        total = round(material_subtotal + delivery + handling, 2)
        offer = {
            "supplier_id": r["supplier_id"],
            "supplier": r["supplier_name"],
            "supplier_code": r["supplier_code"],
            "supplier_sku": r["supplier_sku"],
            "warehouse_location": ", ".join(
                [x for x in (r["supplier_city"], r["supplier_state"]) if x]) or None,
            "unit_price": price,
            "quantity_requested": qty,
            "material_subtotal": material_subtotal,
            "estimated_delivery_miles": miles,
            "delivery_basis": miles_basis,
            "estimated_delivery_cost": delivery,
            "handling_cost": handling,
            "estimated_total": total,
            "quantity_available": qavail,
            "available": available,
            "lead_time_days": r["lead_time_days"],
            "warnings": warnings,
            "rank": None,
        }
        if include_cost:
            offer["cost_price"] = r["cost_price"]
        offers.append(offer)

    # Rank only offers that are available AND have a valid price. Everything
    # else sinks to the bottom and never becomes the recommendation.
    def sortable(o):
        return o["available"] and "missing_price" not in o["warnings"]

    rankable = sorted([o for o in offers if sortable(o)], key=lambda o: o["estimated_total"])
    others = [o for o in offers if not sortable(o)]
    for i, o in enumerate(rankable, start=1):
        o["rank"] = i
    ordered = rankable + sorted(others, key=lambda o: o["estimated_total"])

    recommended = rankable[0] if rankable else None
    return {
        "product": {
            "id": product["id"], "sku": product["sku"],
            "product_name": product["product_name"],
            "manufacturer": product["manufacturer"],
            "manufacturer_part_number": product["manufacturer_part_number"],
            "category": product["category"],
            "unit_of_measure": product["unit_of_measure"],
        },
        "quantity": qty,
        "jobsite_basis": job_basis,
        "offers": ordered,
        "recommended_supplier_id": recommended["supplier_id"] if recommended else None,
        "recommended_reason": (
            f"{recommended['supplier']} — lowest total fulfillment cost"
            if recommended else "No available supplier with valid pricing."),
    }
