"""Per-project buy sheet: estimated sell price, estimated cost, expected
margin, and the products needed.

Sell price / margin come from the rule-based analysis engine
(estimated_material_value / estimated_gross_profit on the `projects` row —
see pipeline/analysis/scoring.py for the formulas). Cost is estimated as
sell price minus that margin, UNLESS real supplier quotes exist for the
project, in which case the real quoted total replaces the heuristic cost
and the buy sheet says so explicitly.
"""

import sqlite3
from dataclasses import dataclass, field

from pipeline.suppliers.pricing_engine import compare_suppliers_for_project


@dataclass
class BuySheetLine:
    material_name: str
    confidence_pct: float


@dataclass
class BuySheet:
    project_id: int
    permit_number: str | None = None
    project_category: str | None = None
    estimated_sell_price: float | None = None
    estimated_cost: float | None = None
    expected_margin: float | None = None
    cost_source: str = "heuristic"  # 'heuristic' | 'supplier_quotes'
    products_needed: list[BuySheetLine] = field(default_factory=list)
    note: str = ""


def generate_buy_sheet(conn: sqlite3.Connection, project_id: int) -> BuySheet:
    project = conn.execute(
        """
        SELECT pr.*, p.permit_number
        FROM projects pr JOIN permits p ON p.id = pr.permit_id
        WHERE pr.id = ?
        """,
        (project_id,),
    ).fetchone()

    if project is None:
        return BuySheet(project_id=project_id, note="Project not found.")

    materials = conn.execute(
        "SELECT material_name, confidence_pct FROM estimated_materials WHERE project_id = ? ORDER BY confidence_pct DESC",
        (project_id,),
    ).fetchall()

    sheet = BuySheet(
        project_id=project_id,
        permit_number=project["permit_number"],
        project_category=project["project_category"],
        estimated_sell_price=project["estimated_material_value"],
        expected_margin=project["estimated_gross_profit"],
        products_needed=[BuySheetLine(m["material_name"], m["confidence_pct"]) for m in materials],
    )

    if sheet.estimated_sell_price is None:
        sheet.note = "No valuation or square footage on this permit — cannot estimate a buy sheet yet."
        return sheet

    heuristic_cost = round(sheet.estimated_sell_price - (sheet.expected_margin or 0), 2)
    sheet.estimated_cost = heuristic_cost
    sheet.cost_source = "heuristic"

    comparison = compare_suppliers_for_project(conn, project_id)
    if comparison.lowest_cost and comparison.lowest_cost.total_price is not None:
        sheet.estimated_cost = comparison.lowest_cost.total_price
        sheet.cost_source = "supplier_quotes"
        sheet.expected_margin = round(sheet.estimated_sell_price - sheet.estimated_cost, 2)
        sheet.note = f"Cost grounded in real quote from {comparison.lowest_cost.supplier_name}."
    else:
        sheet.note = (
            "Cost is a heuristic estimate (no supplier quotes available yet): "
            f"{comparison.note}"
        )

    return sheet
