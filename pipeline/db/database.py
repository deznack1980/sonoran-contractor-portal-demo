"""SQLite connection helper: applies schema.sql and seeds jurisdictions.yaml.

Idempotent — safe to call on every pipeline run. This is also the migration
mechanism for Phase 1: editing jurisdictions.yaml and re-running the pipeline
syncs the jurisdictions table forward.
"""

import sqlite3
from datetime import datetime, timezone

import yaml

from pipeline.config.settings import DB_PATH, SCHEMA_PATH, JURISDICTIONS_YAML


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Wait (rather than fail immediately) if another connection holds a lock —
    # e.g. a running API server or OneDrive sync briefly touching the file.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


# Columns added after the initial 2.0 schema. ``apply_schema`` creates new
# tables via CREATE TABLE IF NOT EXISTS, but pre-existing tables need explicit
# ALTERs. Each entry: (table, column, column_definition).
_KNOWLEDGE_V21_COLUMNS = [
    ("projects", "mapping_kb_version", "INTEGER"),
    ("status_dictionary", "mapping_confidence", "REAL"),
    ("status_dictionary", "mapping_source", "TEXT"),
    ("status_dictionary", "lifecycle_state", "TEXT NOT NULL DEFAULT 'active'"),
    ("status_dictionary", "reviewed_by", "TEXT"),
    ("status_dictionary", "reviewed_at", "TEXT"),
    ("status_dictionary", "review_notes", "TEXT"),
    ("status_dictionary", "kb_version", "INTEGER"),
    ("permit_code_dictionary", "mapping_confidence", "REAL"),
    ("permit_code_dictionary", "mapping_source", "TEXT"),
    ("permit_code_dictionary", "lifecycle_state", "TEXT NOT NULL DEFAULT 'active'"),
    ("permit_code_dictionary", "reviewed_by", "TEXT"),
    ("permit_code_dictionary", "reviewed_at", "TEXT"),
    ("permit_code_dictionary", "review_notes", "TEXT"),
    ("permit_code_dictionary", "kb_version", "INTEGER"),
    ("keyword_dictionary", "mapping_confidence", "REAL"),
    ("keyword_dictionary", "mapping_source", "TEXT"),
    ("keyword_dictionary", "lifecycle_state", "TEXT NOT NULL DEFAULT 'active'"),
    ("keyword_dictionary", "reviewed_by", "TEXT"),
    ("keyword_dictionary", "reviewed_at", "TEXT"),
    ("keyword_dictionary", "review_notes", "TEXT"),
    ("keyword_dictionary", "kb_version", "INTEGER"),
    ("knowledge_review_queue", "priority_score", "REAL"),
    ("knowledge_review_queue", "priority_tier", "TEXT"),
    ("knowledge_review_queue", "affected_permit_count", "INTEGER"),
    ("knowledge_review_queue", "affected_recent_count", "INTEGER"),
    ("knowledge_review_queue", "affected_active_count", "INTEGER"),
    ("knowledge_review_queue", "affected_avg_score", "REAL"),
    ("knowledge_review_queue", "priority_computed_at", "TEXT"),
    # Sprint 3 — project lifecycle intelligence (projects table).
    ("projects", "project_lifecycle", "TEXT"),
    ("projects", "opportunity_date", "TEXT"),
    ("projects", "opportunity_date_basis", "TEXT"),
    ("projects", "opportunity_timing", "TEXT"),
    # Sprint 4 — company intelligence links (nullable; raw text columns kept).
    ("projects", "contractor_company_id", "INTEGER REFERENCES companies(id)"),
    ("projects", "owner_company_id", "INTEGER REFERENCES companies(id)"),
    ("projects", "developer_company_id", "INTEGER REFERENCES companies(id)"),
    ("projects", "architect_company_id", "INTEGER REFERENCES companies(id)"),
    ("projects", "engineer_company_id", "INTEGER REFERENCES companies(id)"),
    ("permits", "contractor_company_id", "INTEGER REFERENCES companies(id)"),
    ("permits", "applicant_name", "TEXT"),
    ("permits", "applicant_organization", "TEXT"),
    ("permits", "responsible_party_name", "TEXT"),
    ("permits", "permit_professional_name", "TEXT"),
    ("permits", "contractor_source_role", "TEXT"),
    ("permits", "contractor_source_field", "TEXT"),
    ("permits", "contractor_evidence_confidence", "REAL"),
    ("permits", "contractor_verification_status", "TEXT"),
    ("permits", "lead_type", "TEXT"),
    ("permits", "why_this_lead", "TEXT"),
    ("companies", "lead_type", "TEXT"),
    ("companies", "lead_verification_status", "TEXT"),
    ("companies", "lead_source", "TEXT"),
    ("companies", "why_this_lead", "TEXT"),
    ("company_roles", "verification_status", "TEXT"),
    ("company_roles", "evidence_source", "TEXT"),
    ("company_roles", "evidence_field", "TEXT"),
    ("crm_company_relationships", "lead_type", "TEXT"),
    ("crm_company_relationships", "lead_verification_status", "TEXT"),
    ("crm_company_relationships", "lead_classification_source", "TEXT"),
    ("crm_company_relationships", "why_this_lead", "TEXT"),
    # Sprint 4 Phase 10 — future per-tenant ownership (NULL = shared canonical).
    ("quotes", "organization_id", "INTEGER"),
    ("deliveries", "organization_id", "INTEGER"),
    # Sprint 6 — supplier catalog + routing columns on the pre-existing
    # suppliers table (additive; a UNIQUE index on code is created by schema.sql).
    ("suppliers", "code", "TEXT"),
    ("suppliers", "warehouse_address", "TEXT"),
    ("suppliers", "city", "TEXT"),
    ("suppliers", "state", "TEXT"),
    ("suppliers", "postal_code", "TEXT"),
    ("suppliers", "latitude", "REAL"),
    ("suppliers", "longitude", "REAL"),
    ("suppliers", "active", "INTEGER NOT NULL DEFAULT 1"),
    ("suppliers", "updated_at", "TEXT"),
    ("suppliers", "quote_contact_name", "TEXT"),
    ("suppliers", "quote_email", "TEXT"),
    ("suppliers", "quote_phone", "TEXT"),
    # Revenue workflow — standardized material RFQ inputs.
    ("material_lists", "quote_needed_by", "TEXT"),
    ("material_lists", "jobsite_postal_code", "TEXT"),
    ("material_lists", "project_name_snapshot", "TEXT"),
    ("material_list_items", "allow_substitution", "INTEGER NOT NULL DEFAULT 1"),
    ("material_list_items", "suggestion_source", "TEXT"),
    ("material_list_items", "estimate_confidence_pct", "REAL"),
    ("material_list_items", "estimate_rationale", "TEXT"),
    ("material_list_items", "quantity_status", "TEXT NOT NULL DEFAULT 'confirmed'"),
    # Release 15.3 — verified-company contractor compatibility metrics.
    ("contractors", "company_id", "INTEGER REFERENCES companies(id)"),
    ("contractors", "estimated_material_opportunity", "REAL"),
    ("contractors", "has_contact_info", "INTEGER NOT NULL DEFAULT 0"),
    ("contractors", "verification_status", "TEXT"),
    ("contractors", "classification_source", "TEXT"),
    ("contractors", "classification_rule", "TEXT"),
    ("contractors", "classification_version", "TEXT"),
    ("contractors", "contractor_evidence_count", "INTEGER NOT NULL DEFAULT 0"),
    ("company_intelligence", "contractor_permit_count", "INTEGER"),
    ("company_intelligence", "contractor_jurisdictions_worked", "TEXT"),
    ("company_intelligence", "contractor_jurisdiction_breakdown", "TEXT"),
    ("company_intelligence", "contractor_commercial_pct", "REAL"),
    ("company_intelligence", "contractor_average_project_value", "REAL"),
    ("company_intelligence", "contractor_last_permit_date", "TEXT"),
    ("company_intelligence", "contractor_estimated_annual_volume", "REAL"),
    ("company_intelligence", "contractor_estimated_material_opportunity", "REAL"),
    ("company_intelligence", "contractor_opportunity_rating", "REAL"),
    ("company_intelligence", "contractor_has_contact_info", "INTEGER"),
    ("company_intelligence", "contractor_verification_status", "TEXT"),
    ("company_intelligence", "contractor_classification_source", "TEXT"),
    ("company_intelligence", "contractor_classification_rule", "TEXT"),
    ("company_intelligence", "contractor_classification_version", "TEXT"),
    ("company_intelligence", "contractor_metrics_calculated_at", "TEXT"),
]


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Idempotently add Sprint 2.1 columns to pre-existing tables."""
    for table, column, decl in _KNOWLEDGE_V21_COLUMNS:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column in cols:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contractors'").fetchone():
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_contractors_company ON contractors(company_id)")
    conn.commit()


def seed_jurisdictions(conn: sqlite3.Connection) -> None:
    with open(JURISDICTIONS_YAML, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    rows = config.get("jurisdictions", [])
    for row in rows:
        notes = (row.get("notes") or "").strip()
        conn.execute(
            """
            INSERT INTO jurisdictions (slug, name, state, status, connector_type,
                                        endpoint_url, resource_id, notes)
            VALUES (:slug, :name, :state, :status, :connector_type,
                    :endpoint_url, :resource_id, :notes)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name,
                state=excluded.state,
                status=excluded.status,
                connector_type=excluded.connector_type,
                endpoint_url=excluded.endpoint_url,
                resource_id=excluded.resource_id,
                notes=excluded.notes
            """,
            {
                "slug": row["slug"],
                "name": row["name"],
                "state": row.get("state", "AZ"),
                "status": row["status"],
                "connector_type": row.get("connector_type"),
                "endpoint_url": row.get("endpoint_url"),
                "resource_id": row.get("resource_id"),
                "notes": notes,
            },
        )
    conn.commit()


def init_db() -> sqlite3.Connection:
    conn = get_connection()
    # Migrate pre-existing tables first so schema.sql indexes that reference new
    # columns (e.g. priority_score) can be created. On a fresh DB the tables do
    # not exist yet, so migrate_schema is a no-op and apply_schema builds the
    # full current shape.
    migrate_schema(conn)
    apply_schema(conn)
    seed_jurisdictions(conn)
    # Municipal Knowledge Engine tables + bootstrap dictionary seed.
    try:
        from pipeline.knowledge.seed import seed_knowledge_base

        seed_knowledge_base(conn)
    except Exception as exc:  # pragma: no cover - keep DB usable if seed fails
        print(f"Warning: knowledge seed skipped: {exc}")
    # Sprint 5 — secure CRM: default organization, roles, permissions.
    try:
        from pipeline.auth.seed import seed_auth

        seed_auth(conn)
    except Exception as exc:  # pragma: no cover - keep DB usable if seed fails
        print(f"Warning: auth seed skipped: {exc}")
    return conn


if __name__ == "__main__":
    connection = init_db()
    count = connection.execute("SELECT COUNT(*) AS n FROM jurisdictions").fetchone()["n"]
    print(f"Database initialized at {DB_PATH}")
    print(f"Seeded {count} jurisdictions.")
    connection.close()
