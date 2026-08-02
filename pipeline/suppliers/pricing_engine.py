"""Price comparison for a project's estimated materials.

Suppliers/quotes launch empty (no fabricated pricing) — every function here
must degrade gracefully to a clear "no suppliers configured yet" result
rather than erroring or rendering broken output. Once real supplier price
books exist in `suppliers`/`quotes`, the same functions produce real
comparisons with no code changes needed.
"""

import sqlite3
from dataclasses import dataclass, field


@dataclass
class SupplierOption:
    supplier_id: int
    supplier_name: str
    covers_all_materials: bool
    total_price: float | None
    materials_priced: int
    materials_needed: int
    max_delivery_days: int | None


@dataclass
class PriceComparison:
    project_id: int
    materials_needed: list[str] = field(default_factory=list)
    has_supplier_data: bool = False
    options: list[SupplierOption] = field(default_factory=list)
    lowest_cost: SupplierOption | None = None
    fastest_delivery: SupplierOption | None = None
    single_supplier_option: SupplierOption | None = None
    best_overall_value: SupplierOption | None = None
    note: str = ""


def compare_suppliers_for_project(conn: sqlite3.Connection, project_id: int, min_confidence: float = 60.0) -> PriceComparison:
    materials = [
        row["material_name"]
        for row in conn.execute(
            "SELECT material_name FROM estimated_materials WHERE project_id = ? AND confidence_pct >= ?",
            (project_id, min_confidence),
        ).fetchall()
    ]

    comparison = PriceComparison(project_id=project_id, materials_needed=materials)

    if not materials:
        comparison.note = "No materials estimated with sufficient confidence for this project."
        return comparison

    supplier_count = conn.execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()["n"]
    if supplier_count == 0:
        comparison.note = "No suppliers configured yet. Add supplier price books to enable comparison."
        return comparison

    suppliers = conn.execute("SELECT id, name FROM suppliers").fetchall()
    options: list[SupplierOption] = []

    for supplier in suppliers:
        quotes = conn.execute(
            """
            SELECT material_name, unit_price, estimated_delivery_days
            FROM quotes
            WHERE supplier_id = ? AND project_id IS NULL AND material_name IN ({})
            """.format(",".join("?" * len(materials))),
            (supplier["id"], *materials),
        ).fetchall()

        priced = {q["material_name"]: q for q in quotes}
        materials_priced = len(priced)
        total_price = sum(q["unit_price"] for q in priced.values() if q["unit_price"] is not None)
        delivery_days = [q["estimated_delivery_days"] for q in priced.values() if q["estimated_delivery_days"] is not None]
        max_delivery = max(delivery_days) if delivery_days else None

        if materials_priced == 0:
            continue

        options.append(
            SupplierOption(
                supplier_id=supplier["id"],
                supplier_name=supplier["name"],
                covers_all_materials=(materials_priced == len(materials)),
                total_price=round(total_price, 2) if priced else None,
                materials_priced=materials_priced,
                materials_needed=len(materials),
                max_delivery_days=max_delivery,
            )
        )

    comparison.options = options
    comparison.has_supplier_data = len(options) > 0

    if not options:
        comparison.note = "Suppliers are configured, but none have quotes for the materials this project needs yet."
        return comparison

    full_coverage = [o for o in options if o.covers_all_materials]

    if full_coverage:
        comparison.lowest_cost = min(full_coverage, key=lambda o: o.total_price or float("inf"))
        delivery_known = [o for o in full_coverage if o.max_delivery_days is not None]
        comparison.fastest_delivery = min(delivery_known, key=lambda o: o.max_delivery_days) if delivery_known else None
        comparison.single_supplier_option = full_coverage[0]
        # Best overall value: normalize price and delivery, weight 60/40 toward price.
        comparison.best_overall_value = _best_value(full_coverage)
    else:
        comparison.note = "No single supplier covers every needed material yet — showing partial coverage only."
        comparison.lowest_cost = min(options, key=lambda o: o.total_price or float("inf"))

    return comparison


def _best_value(options: list[SupplierOption]) -> SupplierOption | None:
    priced = [o for o in options if o.total_price is not None]
    if not priced:
        return None

    prices = [o.total_price for o in priced]
    min_price, max_price = min(prices), max(prices)
    deliveries = [o.max_delivery_days for o in priced if o.max_delivery_days is not None]
    min_delivery = min(deliveries) if deliveries else None
    max_delivery = max(deliveries) if deliveries else None

    def value_score(option: SupplierOption) -> float:
        price_range = (max_price - min_price) or 1
        price_score = 1 - ((option.total_price - min_price) / price_range)

        if option.max_delivery_days is not None and min_delivery is not None and max_delivery is not None:
            delivery_range = (max_delivery - min_delivery) or 1
            delivery_score = 1 - ((option.max_delivery_days - min_delivery) / delivery_range)
        else:
            delivery_score = 0.5  # unknown delivery — neutral

        return 0.6 * price_score + 0.4 * delivery_score

    return max(priced, key=value_score)
