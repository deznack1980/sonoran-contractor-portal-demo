"""Admin-editable delivery / route assumptions.

Values live in the ``pricing_config`` table and fall back to ``settings.py``
defaults when unset. Only admins (supplier_pricing.manage) may change them.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pipeline.config import settings

# config key -> settings default attribute
_DEFAULTS = {
    "delivery_per_mile": "DELIVERY_PER_MILE",
    "delivery_base_fee": "DELIVERY_BASE_FEE",
    "delivery_min_charge": "DELIVERY_MIN_CHARGE",
    "delivery_markup_pct": "DELIVERY_MARKUP_PCT",
    "delivery_handling_cost": "DELIVERY_HANDLING_COST",
    "delivery_road_factor": "DELIVERY_ROAD_FACTOR",
    "delivery_default_miles": "DELIVERY_DEFAULT_MILES",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_delivery_config(conn: sqlite3.Connection) -> dict:
    """Return the effective delivery config (defaults overlaid with overrides)."""
    cfg = {k: float(getattr(settings, attr)) for k, attr in _DEFAULTS.items()}
    try:
        rows = conn.execute("SELECT key, value FROM pricing_config").fetchall()
    except sqlite3.OperationalError:
        rows = []
    for r in rows:
        if r["key"] in cfg:
            try:
                cfg[r["key"]] = float(r["value"])
            except (TypeError, ValueError):
                pass
    return cfg


def set_delivery_config(conn: sqlite3.Connection, updates: dict,
                        updated_by: int | None = None) -> dict:
    """Persist one or more delivery-config overrides. Unknown keys are ignored."""
    now = _now()
    for key, value in updates.items():
        if key not in _DEFAULTS:
            continue
        float(value)  # validate numeric (raises ValueError otherwise)
        conn.execute(
            "INSERT INTO pricing_config (key, value, updated_at, updated_by) "
            "VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (key, str(value), now, updated_by),
        )
    conn.commit()
    return get_delivery_config(conn)
