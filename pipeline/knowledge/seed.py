"""Seed dictionaries from Sprint 1.2 bootstrap rules + category keywords.

Idempotent. Existing human_reviewed rows are not downgraded.
"""

from __future__ import annotations

import sqlite3

from pipeline.analysis.category_rules import CATEGORY_KEYWORDS
from pipeline.analysis.materials_rubric import KEYWORD_MATERIAL_OVERLAYS, MATERIAL_NAMES
from pipeline.analysis.normalization import (
    _PERMIT_TYPE_EXACT,
    _STATUS_BY_MUNICIPALITY,
    _STATUS_EXACT,
)
from pipeline.knowledge.engine import (
    GLOBAL_MUNI,
    ensure_municipality,
    sync_municipalities_from_jurisdictions,
)


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seed_knowledge_base(conn: sqlite3.Connection) -> dict[str, int]:
    sync_municipalities_from_jurisdictions(conn)
    ensure_municipality(conn, GLOBAL_MUNI, "Global")
    now = _utcnow()
    counts = {"status": 0, "permit_code": 0, "keyword": 0, "product": 0}

    # Global status aliases
    for raw, canonical in _STATUS_EXACT.items():
        conn.execute(
            """
            INSERT INTO status_dictionary (
                municipality_slug, raw_status, canonical_status, mapping_rule,
                confidence, human_reviewed, last_observed, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1.0, 1, ?, ?, ?)
            ON CONFLICT(municipality_slug, raw_status) DO UPDATE SET
                canonical_status=excluded.canonical_status,
                mapping_rule=excluded.mapping_rule,
                updated_at=excluded.updated_at
            WHERE status_dictionary.human_reviewed = 0
               OR status_dictionary.canonical_status = excluded.canonical_status
            """,
            (
                GLOBAL_MUNI,
                raw,
                canonical,
                f"seed:exact:{raw}->{canonical}",
                now,
                now,
                now,
            ),
        )
        counts["status"] += 1

    # Municipality-specific status (e.g. Phoenix OPEN)
    for muni, mapping in _STATUS_BY_MUNICIPALITY.items():
        ensure_municipality(conn, muni)
        for raw, canonical in mapping.items():
            conn.execute(
                """
                INSERT INTO status_dictionary (
                    municipality_slug, raw_status, canonical_status, mapping_rule,
                    confidence, human_reviewed, last_observed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1.0, 1, ?, ?, ?)
                ON CONFLICT(municipality_slug, raw_status) DO UPDATE SET
                    canonical_status=excluded.canonical_status,
                    mapping_rule=excluded.mapping_rule,
                    human_reviewed=1,
                    updated_at=excluded.updated_at
                """,
                (
                    muni,
                    raw,
                    canonical,
                    f"seed:municipality:{muni}:{raw}->{canonical}",
                    now,
                    now,
                    now,
                ),
            )
            counts["status"] += 1

    # Global permit type codes
    trade_to_category = {
        "commercial": "Commercial",
        "residential": "Residential",
        "plumbing": "Plumbing",
        "mechanical": "Mechanical",
        "electrical": "Electrical",
        "building": "Building",
        "gas": "Gas",
        "water heater": "Water Heater",
        "pool": "Pool",
        "other": "Other",
    }
    for raw, trade in _PERMIT_TYPE_EXACT.items():
        cat = trade_to_category.get(trade, trade.title())
        relevance = 0.85 if trade in {"plumbing", "gas", "water heater", "pool"} else 0.4
        conn.execute(
            """
            INSERT INTO permit_code_dictionary (
                municipality_slug, raw_permit_code, friendly_name, trade,
                project_category, description, plumbing_relevance, ai_confidence,
                human_reviewed, last_observed_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, 1, ?, ?, ?)
            ON CONFLICT(municipality_slug, raw_permit_code) DO UPDATE SET
                trade=excluded.trade,
                project_category=excluded.project_category,
                friendly_name=excluded.friendly_name,
                updated_at=excluded.updated_at
            WHERE permit_code_dictionary.human_reviewed = 0
            """,
            (
                GLOBAL_MUNI,
                raw,
                trade.title() if trade != "water heater" else "Water Heater",
                trade,
                cat,
                f"Seeded from bootstrap normalize_permit_type exact map",
                relevance,
                now[:10],
                now,
                now,
            ),
        )
        counts["permit_code"] += 1

    # Category keywords
    for category, keywords in CATEGORY_KEYWORDS:
        for kw in keywords:
            conn.execute(
                """
                INSERT INTO keyword_dictionary (
                    keyword, category, trade, suggested_products, weight,
                    confidence, human_reviewed, created_at, updated_at
                ) VALUES (?, ?, NULL, NULL, 1.0, 1.0, 1, ?, ?)
                ON CONFLICT(keyword) DO UPDATE SET
                    category=excluded.category,
                    updated_at=excluded.updated_at
                WHERE keyword_dictionary.human_reviewed = 0
                """,
                (kw.lower(), category, now, now),
            )
            counts["keyword"] += 1

    # Material keyword overlays → product suggestions on keywords
    for keyword, material, confidence in KEYWORD_MATERIAL_OVERLAYS:
        conn.execute(
            """
            INSERT INTO keyword_dictionary (
                keyword, category, trade, suggested_products, weight,
                confidence, human_reviewed, created_at, updated_at
            ) VALUES (?, NULL, 'plumbing', ?, ?, ?, 1, ?, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                suggested_products=COALESCE(
                    keyword_dictionary.suggested_products, excluded.suggested_products
                ),
                trade=COALESCE(keyword_dictionary.trade, excluded.trade),
                updated_at=excluded.updated_at
            """,
            (
                keyword.strip().lower(),
                material,
                confidence / 100.0,
                confidence / 100.0,
                now,
                now,
            ),
        )
        counts["keyword"] += 1

    # Backfill Sprint 2.1 provenance columns for bootstrap-seeded rows so the
    # review UI shows their source/confidence and they remain active.
    for tbl in ("status_dictionary", "permit_code_dictionary", "keyword_dictionary"):
        try:
            conn.execute(
                f"UPDATE {tbl} SET mapping_source='Bootstrap rule' WHERE mapping_source IS NULL"
            )
            conn.execute(
                f"UPDATE {tbl} SET mapping_confidence=95 WHERE mapping_confidence IS NULL"
            )
            conn.execute(
                f"UPDATE {tbl} SET lifecycle_state='active' WHERE lifecycle_state IS NULL"
            )
        except sqlite3.OperationalError:
            # Columns not yet migrated (older DB); safe to skip.
            pass

    for material in MATERIAL_NAMES:
        conn.execute(
            """
            INSERT INTO product_dictionary (
                product_family, keywords, manufacturers, confidence,
                human_reviewed, created_at, updated_at
            ) VALUES (?, ?, NULL, 0.8, 1, ?, ?)
            ON CONFLICT(product_family) DO NOTHING
            """,
            (material, material.lower(), now, now),
        )
        counts["product"] += 1

    conn.commit()
    return counts


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    from pipeline.knowledge.seed import seed_knowledge_base

    result = seed_knowledge_base(connection)
    print("Seeded:", result)
    connection.close()
