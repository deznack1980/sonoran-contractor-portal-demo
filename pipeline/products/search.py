"""Product search across products + supplier offers.

Searchable by SKU, manufacturer part number, product name, description, and
category. Supplier cost fields are only included when the caller is authorized
(``include_cost``)."""

from __future__ import annotations

import sqlite3

from pipeline.config import settings


def _clamp(value, default, maximum):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(v, maximum))


def search_products(conn: sqlite3.Connection, q: str, *, category: str | None = None,
                    supplier_code: str | None = None, in_stock_only: bool = False,
                    include_cost: bool = False, page: int = 1,
                    page_size: int | None = None) -> dict:
    page = max(1, int(page or 1))
    page_size = _clamp(page_size or settings.PRODUCT_PAGE_SIZE_DEFAULT,
                       settings.PRODUCT_PAGE_SIZE_DEFAULT, settings.PRODUCT_PAGE_SIZE_MAX)

    where = ["sp.active=1"]
    params: list = []
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        where.append("(p.sku LIKE ? OR p.manufacturer_part_number LIKE ? OR "
                     "p.product_name LIKE ? OR p.description LIKE ? OR p.category LIKE ? "
                     "OR sp.supplier_sku LIKE ?)")
        params += [like, like, like, like, like, like]
    if category:
        where.append("p.category LIKE ?")
        params.append(f"%{category}%")
    if supplier_code:
        where.append("s.code = ?")
        params.append(supplier_code)
    if in_stock_only:
        where.append("COALESCE(sp.quantity_available,0) > 0")

    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM supplier_products sp "
        f"JOIN products p ON p.id=sp.product_id JOIN suppliers s ON s.id=sp.supplier_id "
        f"WHERE {where_sql}", params).fetchone()["n"]

    cost_col = ", sp.cost_price" if include_cost else ""
    rows = conn.execute(
        f"""
        SELECT p.id AS product_id, p.sku, p.product_name, p.manufacturer,
               p.manufacturer_part_number, p.category, p.unit_of_measure,
               s.id AS supplier_id, s.name AS supplier, s.code AS supplier_code,
               s.city AS warehouse_city, s.state AS warehouse_state,
               sp.supplier_sku, sp.selling_price, sp.quantity_available,
               sp.lead_time_days, sp.active {cost_col}
        FROM supplier_products sp
        JOIN products p ON p.id = sp.product_id
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE {where_sql}
        ORDER BY p.product_name, sp.selling_price
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()

    items = []
    for r in rows:
        d = {
            "product_id": r["product_id"], "sku": r["sku"],
            "product_name": r["product_name"], "manufacturer": r["manufacturer"],
            "manufacturer_part_number": r["manufacturer_part_number"],
            "category": r["category"], "unit_of_measure": r["unit_of_measure"],
            "supplier_id": r["supplier_id"], "supplier": r["supplier"],
            "supplier_code": r["supplier_code"], "supplier_sku": r["supplier_sku"],
            "supplier_price": r["selling_price"],
            "quantity_available": r["quantity_available"],
            "lead_time_days": r["lead_time_days"],
            "warehouse_location": ", ".join(
                [x for x in (r["warehouse_city"], r["warehouse_state"]) if x]) or None,
        }
        if include_cost:
            d["cost_price"] = r["cost_price"]
        items.append(d)

    return {"total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size, "items": items}
