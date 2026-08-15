"""Insert-or-update permit rows keyed on (jurisdiction, permit_number)."""

import sqlite3

from pipeline.connectors.base import PERMIT_FIELDS, utcnow_iso

_UPDATE_FIELDS = [f for f in PERMIT_FIELDS if f not in ("permit_number",)] + ["raw_source_json"]


def upsert_permit(conn: sqlite3.Connection, mapped: dict, *, detect_unchanged: bool = False) -> str:
    """Insert or update one permit row.

    Returns ``'inserted'``, ``'updated'``, or (only when ``detect_unchanged`` is
    True) ``'unchanged'``. When a record is unchanged and detection is enabled,
    the row is left completely untouched — ``last_updated_at`` is NOT bumped — so
    downstream incremental analysis can safely re-analyze only rows whose
    ``last_updated_at`` moved. The default (``detect_unchanged=False``) preserves
    the original always-write behaviour used by the full pipeline run.
    """
    now = utcnow_iso()
    existing = conn.execute(
        "SELECT * FROM permits WHERE jurisdiction = ? AND permit_number = ?",
        (mapped["jurisdiction"], mapped["permit_number"]),
    ).fetchone()

    if existing is None:
        columns = ["jurisdiction", "permit_number"] + _UPDATE_FIELDS + ["first_seen_at", "last_updated_at"]
        placeholders = ", ".join(f":{c}" for c in columns)
        values = {c: mapped.get(c) for c in columns if c not in ("first_seen_at", "last_updated_at")}
        values["first_seen_at"] = now
        values["last_updated_at"] = now
        conn.execute(
            f"INSERT INTO permits ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        return "inserted"

    if detect_unchanged:
        existing_row = dict(existing)
        # Compare only the fields the source actually maps into. A field that is
        # identical (including NULL) contributes no change.
        changed = any(
            existing_row.get(field) != mapped.get(field) for field in _UPDATE_FIELDS
        )
        if not changed:
            return "unchanged"

    set_clause = ", ".join(f"{c} = :{c}" for c in _UPDATE_FIELDS) + ", last_updated_at = :last_updated_at"
    values = {c: mapped.get(c) for c in _UPDATE_FIELDS}
    values["last_updated_at"] = now
    values["id"] = existing["id"]
    conn.execute(f"UPDATE permits SET {set_clause} WHERE id = :id", values)
    # A changed contractor identity invalidates the permit-specific canonical
    # link. The explicit-evidence rebuild will relink it; preserving the old ID
    # would keep a stale applicant/professional relationship.
    if existing["general_contractor_name"] != mapped.get("general_contractor_name"):
        conn.execute("UPDATE permits SET contractor_company_id=NULL WHERE id=?", (existing["id"],))
        conn.execute("UPDATE projects SET contractor_company_id=NULL WHERE permit_id=?", (existing["id"],))
    return "updated"
