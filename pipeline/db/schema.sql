PRAGMA foreign_keys = ON;

-- ============================================================
-- jurisdictions: connection status per city. Honestly tracks
-- connected vs pending so the dashboard never implies coverage
-- that doesn't exist.
-- ============================================================
CREATE TABLE IF NOT EXISTS jurisdictions (
    slug                    TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    state                   TEXT NOT NULL DEFAULT 'AZ',
    status                  TEXT NOT NULL CHECK(status IN ('connected','pending')),
    connector_type          TEXT,
    endpoint_url            TEXT,
    resource_id             TEXT,
    notes                   TEXT,
    last_synced_at          TEXT,
    last_sync_status        TEXT,
    last_sync_record_count  INTEGER,
    last_sync_error         TEXT
);

-- ============================================================
-- ingestion_runs: per-run audit log
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    jurisdiction_slug  TEXT NOT NULL REFERENCES jurisdictions(slug),
    run_started_at     TEXT NOT NULL,
    run_finished_at    TEXT,
    records_fetched    INTEGER DEFAULT 0,
    records_inserted   INTEGER DEFAULT 0,
    records_updated    INTEGER DEFAULT 0,
    status             TEXT,
    error_message      TEXT
);

-- ============================================================
-- permits: raw ingested permit records. All source-dependent
-- fields nullable — unavailable fields stay NULL, never invented.
-- ============================================================
CREATE TABLE IF NOT EXISTS permits (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    jurisdiction                 TEXT NOT NULL REFERENCES jurisdictions(slug),
    permit_number                 TEXT,
    permit_type                   TEXT,
    permit_subtype                 TEXT,
    status                        TEXT,
    description                   TEXT,
    filed_date                     TEXT,
    issued_date                    TEXT,
    expiration_date                 TEXT,
    finaled_date                   TEXT,
    inspection_status               TEXT,
    last_inspection_date             TEXT,
    job_address                    TEXT,
    city                          TEXT,
    state                         TEXT DEFAULT 'AZ',
    zip                           TEXT,
    parcel_number                  TEXT,
    apn                           TEXT,
    latitude                      REAL,
    longitude                     REAL,
    owner_name                     TEXT,
    general_contractor_name           TEXT,
    plumbing_contractor_name           TEXT,
    contractor_license_number          TEXT,
    valuation                     REAL,
    square_footage                  REAL,
    occupancy_type                  TEXT,
    permit_url                     TEXT,
    public_notes                   TEXT,
    inspector                     TEXT,
    project_description              TEXT,
    applicant_name                   TEXT,
    applicant_organization           TEXT,
    responsible_party_name           TEXT,
    permit_professional_name         TEXT,
    contractor_source_role           TEXT,
    contractor_source_field          TEXT,
    contractor_evidence_confidence   REAL,
    contractor_verification_status   TEXT,
    lead_type                        TEXT,
    why_this_lead                    TEXT,
    raw_source_json                 TEXT,
    contractor_company_id            INTEGER REFERENCES companies(id),
    first_seen_at                   TEXT NOT NULL,
    last_updated_at                 TEXT NOT NULL,
    UNIQUE(jurisdiction, permit_number)
);
CREATE INDEX IF NOT EXISTS idx_permits_contractor_company ON permits(contractor_company_id);
CREATE INDEX IF NOT EXISTS idx_permits_jurisdiction ON permits(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_permits_issued_date  ON permits(issued_date);
CREATE INDEX IF NOT EXISTS idx_permits_status       ON permits(status);
CREATE INDEX IF NOT EXISTS idx_permits_city         ON permits(city);
CREATE INDEX IF NOT EXISTS idx_permits_parcel       ON permits(parcel_number);

-- ============================================================
-- projects: one row per permit, holds the rule-based analysis
-- output (category, stage, estimated value, scores).
-- ============================================================
CREATE TABLE IF NOT EXISTS projects (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    permit_id                   INTEGER NOT NULL REFERENCES permits(id),
    jurisdiction                 TEXT NOT NULL REFERENCES jurisdictions(slug),
    contractor_id                 INTEGER REFERENCES contractors(id),
    project_category              TEXT,
    construction_stage             TEXT,
    estimated_plumbing_scope         TEXT,
    estimated_material_value          REAL,
    estimated_gross_profit            REAL,
    opportunity_score               REAL,
    confidence_score                REAL,
    analysis_version                TEXT,
    analyzed_at                    TEXT,
    mapping_kb_version              INTEGER,
    project_lifecycle               TEXT,
    opportunity_date                TEXT,
    opportunity_date_basis          TEXT,
    opportunity_timing              TEXT,
    -- Sprint 4 — nullable links to canonical companies (raw text kept on permits).
    contractor_company_id           INTEGER REFERENCES companies(id),
    owner_company_id                INTEGER REFERENCES companies(id),
    developer_company_id            INTEGER REFERENCES companies(id),
    architect_company_id            INTEGER REFERENCES companies(id),
    engineer_company_id             INTEGER REFERENCES companies(id),
    UNIQUE(permit_id)
);
CREATE INDEX IF NOT EXISTS idx_projects_contractor_company ON projects(contractor_company_id);
CREATE INDEX IF NOT EXISTS idx_projects_owner_company      ON projects(owner_company_id);
CREATE INDEX IF NOT EXISTS idx_projects_developer_company  ON projects(developer_company_id);
CREATE INDEX IF NOT EXISTS idx_projects_architect_company  ON projects(architect_company_id);
CREATE INDEX IF NOT EXISTS idx_projects_engineer_company   ON projects(engineer_company_id);
CREATE INDEX IF NOT EXISTS idx_projects_lifecycle ON projects(project_lifecycle);
CREATE INDEX IF NOT EXISTS idx_projects_opp_date  ON projects(opportunity_date);
CREATE INDEX IF NOT EXISTS idx_projects_opportunity  ON projects(opportunity_score DESC);
CREATE INDEX IF NOT EXISTS idx_projects_category     ON projects(project_category);
CREATE INDEX IF NOT EXISTS idx_projects_jurisdiction  ON projects(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_projects_contractor   ON projects(contractor_id);

-- ============================================================
-- estimated_materials: per-project material estimates + confidence
-- ============================================================
CREATE TABLE IF NOT EXISTS estimated_materials (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id                INTEGER NOT NULL REFERENCES projects(id),
    material_name              TEXT NOT NULL,
    confidence_pct              REAL NOT NULL,
    rationale                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_est_materials_project ON estimated_materials(project_id);

-- ============================================================
-- contractors: fully derived/rebuilt from permits+projects each run
-- ============================================================
CREATE TABLE IF NOT EXISTS contractors (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    name                       TEXT NOT NULL,
    normalized_name              TEXT NOT NULL,
    license_number               TEXT,
    contractor_type              TEXT,
    cities_worked                TEXT,
    jurisdictions_worked            TEXT,
    jurisdiction_breakdown           TEXT,
    permit_count                 INTEGER DEFAULT 0,
    first_permit_date              TEXT,
    last_permit_date               TEXT,
    estimated_annual_volume          REAL,
    commercial_permit_count          INTEGER DEFAULT 0,
    residential_permit_count         INTEGER DEFAULT 0,
    commercial_pct                REAL,
    residential_pct               REAL,
    avg_project_value              REAL,
    largest_project_value            REAL,
    largest_project_permit_id         INTEGER REFERENCES permits(id),
    growth_trend                  TEXT,
    opportunity_rating              REAL,
    updated_at                   TEXT NOT NULL,
    UNIQUE(normalized_name)
);
CREATE INDEX IF NOT EXISTS idx_contractors_name        ON contractors(normalized_name);
CREATE INDEX IF NOT EXISTS idx_contractors_opportunity  ON contractors(opportunity_rating DESC);

-- ============================================================
-- suppliers / quotes / deliveries: launch empty. No fabricated
-- pricing — is_example flags any illustrative-only row.
-- ============================================================
CREATE TABLE IF NOT EXISTS suppliers (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    is_example               INTEGER NOT NULL DEFAULT 0,
    delivery_radius_miles      REAL,
    preferred_brands          TEXT,
    contact_info             TEXT,
    notes                    TEXT,
    created_at               TEXT NOT NULL,
    -- Sprint 6 — supplier catalog + routing origin (additive; back-compat).
    code                     TEXT,
    warehouse_address         TEXT,
    city                     TEXT,
    state                    TEXT,
    postal_code               TEXT,
    latitude                 REAL,
    longitude                REAL,
    active                   INTEGER NOT NULL DEFAULT 1,
    updated_at               TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_suppliers_code ON suppliers(code);

CREATE TABLE IF NOT EXISTS quotes (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id                 INTEGER NOT NULL REFERENCES suppliers(id),
    project_id                  INTEGER REFERENCES projects(id),
    material_name                TEXT NOT NULL,
    unit                        TEXT,
    unit_price                  REAL,
    estimated_delivery_days        INTEGER,
    quote_date                  TEXT,
    valid_until                  TEXT,
    notes                       TEXT,
    organization_id             INTEGER   -- Sprint 4: future per-tenant ownership (NULL = shared)
);
CREATE INDEX IF NOT EXISTS idx_quotes_supplier_material ON quotes(supplier_id, material_name);
CREATE INDEX IF NOT EXISTS idx_quotes_project           ON quotes(project_id);

CREATE TABLE IF NOT EXISTS deliveries (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id             INTEGER NOT NULL REFERENCES projects(id),
    supplier_id             INTEGER REFERENCES suppliers(id),
    quote_id               INTEGER REFERENCES quotes(id),
    status                 TEXT NOT NULL DEFAULT 'pending',
    scheduled_date           TEXT,
    delivered_date           TEXT,
    delivery_address         TEXT,
    tracking_notes           TEXT,
    created_at              TEXT NOT NULL,
    organization_id         INTEGER   -- Sprint 4: future per-tenant ownership (NULL = shared)
);
CREATE INDEX IF NOT EXISTS idx_deliveries_project ON deliveries(project_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_status  ON deliveries(status);

-- ============================================================
-- Municipal Knowledge Engine
-- Dictionaries + unknown review queue. Scoring weights live
-- elsewhere; these tables teach CorridorIQ new terminology
-- without code changes once entries are approved.
-- ============================================================

CREATE TABLE IF NOT EXISTS municipalities (
    slug                TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'AZ',
    permit_source       TEXT,
    active              INTEGER NOT NULL DEFAULT 1,
    last_synchronization TEXT,
    metadata_json       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permit_code_dictionary (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_slug   TEXT NOT NULL REFERENCES municipalities(slug),
    raw_permit_code     TEXT NOT NULL,
    friendly_name       TEXT,
    trade               TEXT,
    project_category    TEXT,
    description         TEXT,
    plumbing_relevance  REAL,
    ai_confidence       REAL,
    human_reviewed      INTEGER NOT NULL DEFAULT 0,
    last_observed_date  TEXT,
    mapping_confidence  REAL,
    mapping_source      TEXT,
    lifecycle_state     TEXT NOT NULL DEFAULT 'active'
                        CHECK(lifecycle_state IN ('draft','active','rejected','deprecated')),
    reviewed_by         TEXT,
    reviewed_at         TEXT,
    review_notes        TEXT,
    kb_version          INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(municipality_slug, raw_permit_code)
);
CREATE INDEX IF NOT EXISTS idx_permit_code_muni ON permit_code_dictionary(municipality_slug);

CREATE TABLE IF NOT EXISTS status_dictionary (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_slug   TEXT NOT NULL REFERENCES municipalities(slug),
    raw_status          TEXT NOT NULL,
    canonical_status    TEXT NOT NULL,
    mapping_rule        TEXT,
    confidence          REAL,
    human_reviewed      INTEGER NOT NULL DEFAULT 0,
    last_observed       TEXT,
    mapping_confidence  REAL,
    mapping_source      TEXT,
    lifecycle_state     TEXT NOT NULL DEFAULT 'active'
                        CHECK(lifecycle_state IN ('draft','active','rejected','deprecated')),
    reviewed_by         TEXT,
    reviewed_at         TEXT,
    review_notes        TEXT,
    kb_version          INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(municipality_slug, raw_status)
);
CREATE INDEX IF NOT EXISTS idx_status_dict_muni ON status_dictionary(municipality_slug);

CREATE TABLE IF NOT EXISTS keyword_dictionary (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword             TEXT NOT NULL UNIQUE,
    category            TEXT,
    trade               TEXT,
    suggested_products  TEXT,
    weight              REAL NOT NULL DEFAULT 1.0,
    confidence          REAL,
    human_reviewed      INTEGER NOT NULL DEFAULT 0,
    mapping_confidence  REAL,
    mapping_source      TEXT,
    lifecycle_state     TEXT NOT NULL DEFAULT 'active'
                        CHECK(lifecycle_state IN ('draft','active','rejected','deprecated')),
    reviewed_by         TEXT,
    reviewed_at         TEXT,
    review_notes        TEXT,
    kb_version          INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_dictionary (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_family      TEXT NOT NULL UNIQUE,
    keywords            TEXT,
    manufacturers       TEXT,
    confidence          REAL,
    human_reviewed      INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- Unresolved review records for unknown codes / statuses / keywords.
-- Never silently invent a permanent mapping without review.
CREATE TABLE IF NOT EXISTS knowledge_review_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                TEXT NOT NULL CHECK(kind IN ('status','permit_code','keyword')),
    municipality_slug   TEXT,
    raw_value           TEXT NOT NULL,
    description         TEXT,
    suggested_interpretation TEXT,
    confidence          REAL,
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','approved','rejected')),
    reviewer_notes      TEXT,
    resolved_at         TEXT,
    priority_score          REAL,
    priority_tier           TEXT,
    affected_permit_count   INTEGER,
    affected_recent_count   INTEGER,
    affected_active_count   INTEGER,
    affected_avg_score      REAL,
    priority_computed_at    TEXT,
    UNIQUE(kind, municipality_slug, raw_value)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_queue_status ON knowledge_review_queue(status, kind);
CREATE INDEX IF NOT EXISTS idx_knowledge_queue_priority ON knowledge_review_queue(priority_score DESC);

-- ============================================================
-- mapping_audit_log: permanent, append-only history of every
-- dictionary mapping change. Never deleted.
-- ============================================================
CREATE TABLE IF NOT EXISTS mapping_audit_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                TEXT NOT NULL,
    municipality_slug   TEXT,
    raw_value           TEXT NOT NULL,
    previous_mapping    TEXT,
    new_mapping         TEXT,
    action              TEXT NOT NULL
                        CHECK(action IN ('created','approved','rejected','updated','deprecated','reactivated')),
    reviewer            TEXT,
    reason              TEXT,
    mapping_confidence  REAL,
    mapping_source      TEXT,
    affected_permit_count   INTEGER,
    score_impact_summary    TEXT,
    kb_version          INTEGER,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mapping_audit_raw ON mapping_audit_log(kind, municipality_slug, raw_value);
CREATE INDEX IF NOT EXISTS idx_mapping_audit_created ON mapping_audit_log(created_at DESC);

-- ============================================================
-- knowledge_meta: key/value store. Holds the monotonically
-- increasing knowledge-base version used to stamp reprocessed
-- permits for reproducibility.
-- ============================================================
CREATE TABLE IF NOT EXISTS knowledge_meta (
    key                 TEXT PRIMARY KEY,
    value               TEXT,
    updated_at          TEXT
);

-- ============================================================
-- Sprint 4 — Company Intelligence Foundation
-- The company is the primary business entity. Canonical, shared
-- construction intelligence (never per-tenant). Permit / project
-- scoring is untouched — these tables layer a company-centered
-- model on top of the existing permit pipeline.
-- ============================================================

-- companies: canonical company record. Name alone is NOT a unique key;
-- identity resolution decides linkage. Never hard-deleted — deprecated
-- companies point at their canonical survivor via merged_into_id.
CREATE TABLE IF NOT EXISTS companies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    legal_name          TEXT,
    display_name        TEXT,
    normalized_name     TEXT NOT NULL,
    dba_name            TEXT,
    company_type_primary TEXT,
    website             TEXT,
    main_phone          TEXT,
    main_email          TEXT,
    address_line_1      TEXT,
    address_line_2      TEXT,
    city                TEXT,
    state               TEXT,
    postal_code         TEXT,
    country             TEXT DEFAULT 'US',
    latitude            REAL,
    longitude           REAL,
    license_number      TEXT,
    license_state       TEXT,
    license_status      TEXT,
    year_established    INTEGER,
    employee_range      TEXT,
    revenue_range       TEXT,
    lead_type           TEXT,
    lead_verification_status TEXT,
    lead_source         TEXT,
    why_this_lead       TEXT,
    source_system       TEXT,
    source_record_id    TEXT,
    merged_into_id      INTEGER REFERENCES companies(id),
    lifecycle_state     TEXT NOT NULL DEFAULT 'active'
                        CHECK(lifecycle_state IN ('active','merged','deprecated')),
    first_seen_at       TEXT,
    last_seen_at        TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_normalized ON companies(normalized_name);
CREATE INDEX IF NOT EXISTS idx_companies_license    ON companies(license_number);
CREATE INDEX IF NOT EXISTS idx_companies_location   ON companies(state, city);
CREATE INDEX IF NOT EXISTS idx_companies_source     ON companies(source_system, source_record_id);
CREATE INDEX IF NOT EXISTS idx_companies_merged     ON companies(merged_into_id);

-- company_roles: a company may hold multiple roles (contractor +
-- developer + owner, etc.). No duplicate company per role.
CREATE TABLE IF NOT EXISTS company_roles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    role_type           TEXT NOT NULL
                        CHECK(role_type IN ('contractor','fulfillment_partner','supplier',
                              'manufacturer','developer','property_owner','architect',
                              'engineer','municipality','other')),
    is_primary          INTEGER NOT NULL DEFAULT 0,
    effective_from      TEXT,
    effective_to        TEXT,
    source              TEXT,
    confidence          REAL,
    verification_status TEXT,
    evidence_source     TEXT,
    evidence_field      TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(company_id, role_type)
);
CREATE INDEX IF NOT EXISTS idx_company_roles_company ON company_roles(company_id);
CREATE INDEX IF NOT EXISTS idx_company_roles_type    ON company_roles(role_type);

CREATE TABLE IF NOT EXISTS permit_party_evidence (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    permit_id           INTEGER NOT NULL REFERENCES permits(id),
    company_id          INTEGER REFERENCES companies(id),
    party_name          TEXT NOT NULL,
    source_role         TEXT NOT NULL,
    source_field        TEXT NOT NULL,
    lead_type           TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    confidence          REAL NOT NULL,
    is_contractor_evidence INTEGER NOT NULL DEFAULT 0,
    why_this_lead       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(permit_id, source_field, party_name)
);
CREATE INDEX IF NOT EXISTS idx_permit_party_evidence_permit ON permit_party_evidence(permit_id);
CREATE INDEX IF NOT EXISTS idx_permit_party_evidence_company ON permit_party_evidence(company_id);
CREATE INDEX IF NOT EXISTS idx_permit_party_evidence_type ON permit_party_evidence(lead_type, verification_status);

CREATE TABLE IF NOT EXISTS company_lead_classification (
    company_id          INTEGER PRIMARY KEY REFERENCES companies(id),
    lead_type           TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    source              TEXT,
    source_field        TEXT,
    confidence          REAL NOT NULL,
    why_this_lead       TEXT NOT NULL,
    explicit_contractor_permits INTEGER NOT NULL DEFAULT 0,
    ambiguous_permit_contacts INTEGER NOT NULL DEFAULT 0,
    classification_version TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_company_lead_type ON company_lead_classification(lead_type);

CREATE TABLE IF NOT EXISTS lead_role_assignment_history (
    migration_key       TEXT NOT NULL,
    permit_id           INTEGER NOT NULL REFERENCES permits(id),
    prior_general_contractor_name TEXT,
    prior_contractor_company_id INTEGER,
    prior_project_contractor_company_id INTEGER,
    recorded_at         TEXT NOT NULL,
    PRIMARY KEY(migration_key, permit_id)
);

-- contacts: never invented from permit descriptions. Only populated
-- from real source contact fields.
CREATE TABLE IF NOT EXISTS contacts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    first_name          TEXT,
    last_name           TEXT,
    full_name           TEXT,
    job_title           TEXT,
    department          TEXT,
    email               TEXT,
    phone               TEXT,
    mobile_phone        TEXT,
    preferred_contact_method TEXT,
    is_primary          INTEGER NOT NULL DEFAULT 0,
    source              TEXT,
    source_record_id    TEXT,
    first_seen_at       TEXT,
    last_seen_at        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);

-- company_aliases: every source name variant preserved.
CREATE TABLE IF NOT EXISTS company_aliases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    alias_name          TEXT NOT NULL,
    normalized_alias    TEXT NOT NULL,
    source              TEXT,
    source_record_id    TEXT,
    first_seen_at       TEXT,
    last_seen_at        TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(company_id, normalized_alias)
);
CREATE INDEX IF NOT EXISTS idx_company_aliases_norm ON company_aliases(normalized_alias);

-- company_match_review_queue: ambiguous identity matches (60–94) that
-- must be reviewed before linking. Low-confidence never auto-merges.
CREATE TABLE IF NOT EXISTS company_match_review_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_type  TEXT NOT NULL,
    source_record_id    TEXT NOT NULL,
    candidate_company_id INTEGER REFERENCES companies(id),
    proposed_company_name TEXT,
    normalized_name     TEXT,
    match_confidence    REAL,
    match_reasons       TEXT,
    conflicting_fields  TEXT,
    lifecycle_state     TEXT NOT NULL DEFAULT 'pending'
                        CHECK(lifecycle_state IN ('pending','approved','rejected')),
    reviewed_by         TEXT,
    reviewed_at         TEXT,
    review_notes        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(source_record_type, source_record_id, candidate_company_id)
);
CREATE INDEX IF NOT EXISTS idx_company_match_state ON company_match_review_queue(lifecycle_state);

-- company_intelligence: derived metrics per company (one row each).
-- Only verified permit/project activity — no fabricated spend/revenue.
CREATE TABLE IF NOT EXISTS company_intelligence (
    company_id          INTEGER PRIMARY KEY REFERENCES companies(id),
    total_permits       INTEGER,
    active_permits      INTEGER,
    total_projects      INTEGER,
    active_projects     INTEGER,
    projects_last_7_days INTEGER,
    projects_last_30_days INTEGER,
    projects_last_90_days INTEGER,
    commercial_project_count INTEGER,
    residential_project_count INTEGER,
    municipality_count  INTEGER,
    first_activity_date TEXT,
    latest_activity_date TEXT,
    average_opportunity_score REAL,
    highest_opportunity_score REAL,
    estimated_opportunity_total REAL,
    permit_growth_30d   REAL,
    permit_growth_90d   REAL,
    permit_growth_12m   REAL,
    activity_trend      TEXT,
    company_priority_score REAL,
    company_priority_tier  TEXT,
    metrics_calculated_at TEXT,
    model_version       TEXT
);
CREATE INDEX IF NOT EXISTS idx_company_intel_priority ON company_intelligence(company_priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_company_intel_latest   ON company_intelligence(latest_activity_date DESC);

-- company_activity: canonical company timeline. Deduplicated on rerun
-- via dedupe_key (NULL for user-authored notes so many are allowed).
CREATE TABLE IF NOT EXISTS company_activity (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    activity_type       TEXT NOT NULL,
    activity_date       TEXT,
    project_id          INTEGER REFERENCES projects(id),
    permit_id           INTEGER REFERENCES permits(id),
    contact_id          INTEGER REFERENCES contacts(id),
    title               TEXT,
    description         TEXT,
    source              TEXT,
    metadata_json       TEXT,
    dedupe_key          TEXT UNIQUE,
    organization_id     INTEGER,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_company_activity_company ON company_activity(company_id, activity_date DESC);
CREATE INDEX IF NOT EXISTS idx_company_activity_type    ON company_activity(activity_type);

-- company_identity_audit_log: permanent, append-only record of every
-- identity decision. Companies with linked records are never hard-deleted.
CREATE TABLE IF NOT EXISTS company_identity_audit_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    action              TEXT NOT NULL
                        CHECK(action IN ('created','linked','merged','unlinked',
                              'match_approved','match_rejected','alias_added','profile_updated')),
    source_company_id   INTEGER,
    target_company_id   INTEGER,
    source_record_type  TEXT,
    source_record_id    TEXT,
    previous_values_json TEXT,
    new_values_json     TEXT,
    reason              TEXT,
    confidence          REAL,
    performed_by        TEXT,
    performed_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_company_audit_target ON company_identity_audit_log(target_company_id);
CREATE INDEX IF NOT EXISTS idx_company_audit_time   ON company_identity_audit_log(performed_at DESC);

-- ============================================================
-- Sprint 5 — Secure Employee CRM and Access Control
-- Shared Company Intelligence stays canonical + read-only for sales.
-- Everything below is organization-owned CRM / auth / RBAC / audit.
-- ============================================================

-- organizations: separates shared intelligence from org-owned CRM records.
CREATE TABLE IF NOT EXISTS organizations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    slug                TEXT NOT NULL UNIQUE,
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- users: individual employee accounts. password_hash is never serialized.
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id),
    email               TEXT NOT NULL,
    normalized_email    TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    first_name          TEXT,
    last_name           TEXT,
    display_name        TEXT,
    phone               TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    failed_login_count  INTEGER NOT NULL DEFAULT 0,
    locked_until        TEXT,
    last_login_at       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_org ON users(organization_id);

-- roles / permissions / role_permissions / user_roles (RBAC).
CREATE TABLE IF NOT EXISTS roles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    display_name        TEXT,
    description         TEXT,
    is_system_role      INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permissions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    permission_key      TEXT NOT NULL UNIQUE,
    description         TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id             INTEGER NOT NULL REFERENCES roles(id),
    permission_id       INTEGER NOT NULL REFERENCES permissions(id),
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id             INTEGER NOT NULL REFERENCES users(id),
    role_id             INTEGER NOT NULL REFERENCES roles(id),
    assigned_by         INTEGER REFERENCES users(id),
    assigned_at         TEXT NOT NULL,
    PRIMARY KEY (user_id, role_id)
);

-- sessions: server-side sessions (HTTP-only cookie holds the opaque token).
CREATE TABLE IF NOT EXISTS sessions (
    token               TEXT PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id),
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL,
    revoked_at          TEXT,
    ip_address          TEXT,
    user_agent          TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- crm_company_relationships: org-owned sales state per company (one active).
CREATE TABLE IF NOT EXISTS crm_company_relationships (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id),
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    relationship_status TEXT NOT NULL DEFAULT 'new',
    assigned_user_id    INTEGER REFERENCES users(id),
    assigned_by         INTEGER REFERENCES users(id),
    assigned_at         TEXT,
    lead_source         TEXT,
    lead_type           TEXT,
    lead_verification_status TEXT,
    lead_classification_source TEXT,
    why_this_lead       TEXT,
    priority_override   TEXT,
    do_not_contact      INTEGER NOT NULL DEFAULT 0,
    do_not_contact_reason TEXT,
    first_contact_at    TEXT,
    last_contact_at     TEXT,
    next_followup_at     TEXT,
    qualified_at        TEXT,
    won_at              TEXT,
    lost_at             TEXT,
    lost_reason         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(organization_id, company_id)
);
CREATE INDEX IF NOT EXISTS idx_crm_rel_assigned ON crm_company_relationships(assigned_user_id);
CREATE INDEX IF NOT EXISTS idx_crm_rel_status   ON crm_company_relationships(relationship_status);

-- crm_activities: append-mostly sales activity log (edits keep audit history).
CREATE TABLE IF NOT EXISTS crm_activities (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id),
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    project_id          INTEGER REFERENCES projects(id),
    permit_id           INTEGER REFERENCES permits(id),
    contact_id          INTEGER REFERENCES contacts(id),
    user_id             INTEGER NOT NULL REFERENCES users(id),
    activity_type       TEXT NOT NULL,
    activity_outcome    TEXT,
    subject             TEXT,
    notes               TEXT,
    activity_at         TEXT NOT NULL,
    duration_minutes    INTEGER,
    next_followup_at     TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crm_act_company ON crm_activities(company_id);
CREATE INDEX IF NOT EXISTS idx_crm_act_user    ON crm_activities(user_id);

-- crm_activity_revisions: retained edit history for activities.
CREATE TABLE IF NOT EXISTS crm_activity_revisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id         INTEGER NOT NULL REFERENCES crm_activities(id),
    edited_by           INTEGER REFERENCES users(id),
    previous_values_json TEXT,
    created_at          TEXT NOT NULL
);

-- crm_tasks: org-owned tasks.
CREATE TABLE IF NOT EXISTS crm_tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id),
    company_id          INTEGER REFERENCES companies(id),
    project_id          INTEGER REFERENCES projects(id),
    assigned_user_id    INTEGER REFERENCES users(id),
    created_by_user_id  INTEGER NOT NULL REFERENCES users(id),
    task_type           TEXT,
    title               TEXT NOT NULL,
    description         TEXT,
    priority            TEXT NOT NULL DEFAULT 'normal',
    due_at              TEXT,
    completed_at        TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crm_tasks_assigned ON crm_tasks(assigned_user_id);
CREATE INDEX IF NOT EXISTS idx_crm_tasks_status   ON crm_tasks(status);

-- crm_assignment_history: permanent, append-only assignment audit.
CREATE TABLE IF NOT EXISTS crm_assignment_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id),
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    previous_user_id    INTEGER REFERENCES users(id),
    new_user_id         INTEGER REFERENCES users(id),
    assigned_by         INTEGER REFERENCES users(id),
    reason              TEXT,
    assigned_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crm_assign_company ON crm_assignment_history(company_id);

-- security_audit_log: append-only. Never contains secrets/hashes/tokens.
CREATE TABLE IF NOT EXISTS security_audit_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER REFERENCES users(id),
    organization_id     INTEGER REFERENCES organizations(id),
    event_type          TEXT NOT NULL,
    resource_type       TEXT,
    resource_id         TEXT,
    action              TEXT,
    success             INTEGER,
    ip_address          TEXT,
    user_agent          TEXT,
    details_json        TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sec_audit_user ON security_audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_sec_audit_time ON security_audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sec_audit_event ON security_audit_log(event_type);

-- ============================================================
-- Sprint 6 — Multi-Supplier Product Pricing (V1)
-- One master product record; prices live as per-supplier offers.
-- Adding a new supplier never requires a schema change.
-- ============================================================

-- products: canonical master product (no price stored here).
CREATE TABLE IF NOT EXISTS products (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    sku                      TEXT NOT NULL UNIQUE,
    manufacturer             TEXT,
    manufacturer_part_number TEXT,
    product_name             TEXT,
    description              TEXT,
    category                 TEXT,
    unit_of_measure          TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_mpn      ON products(manufacturer_part_number);
CREATE INDEX IF NOT EXISTS idx_products_name     ON products(product_name);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);

-- suppliers: extended in the earlier "suppliers / quotes / deliveries" block
-- above with Sprint 6 catalog + routing columns (code, warehouse, lat/long).

-- supplier_products: a supplier's offer for a master product. cost_price is a
-- restricted field never serialized to sales users.
CREATE TABLE IF NOT EXISTS supplier_products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id         INTEGER NOT NULL REFERENCES suppliers(id),
    product_id          INTEGER NOT NULL REFERENCES products(id),
    supplier_sku        TEXT,
    selling_price       REAL,
    cost_price          REAL,
    quantity_available  REAL,
    lead_time_days      INTEGER,
    active              INTEGER NOT NULL DEFAULT 1,
    source_file         TEXT,
    effective_date      TEXT,
    imported_at         TEXT,
    updated_at          TEXT,
    UNIQUE(supplier_id, product_id)
);
CREATE INDEX IF NOT EXISTS idx_supplier_products_supplier ON supplier_products(supplier_id);
CREATE INDEX IF NOT EXISTS idx_supplier_products_product  ON supplier_products(product_id);
CREATE INDEX IF NOT EXISTS idx_supplier_products_sku      ON supplier_products(supplier_sku);

-- supplier_import_log: audit + history of catalog imports (for the admin UI).
CREATE TABLE IF NOT EXISTS supplier_import_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id         INTEGER REFERENCES suppliers(id),
    supplier_code       TEXT,
    source_file         TEXT,
    imported            INTEGER NOT NULL DEFAULT 0,
    updated             INTEGER NOT NULL DEFAULT 0,
    skipped             INTEGER NOT NULL DEFAULT 0,
    failed              INTEGER NOT NULL DEFAULT 0,
    zero_price_excluded INTEGER NOT NULL DEFAULT 0,
    duplicate_skus      INTEGER NOT NULL DEFAULT 0,
    missing_mpn         INTEGER NOT NULL DEFAULT 0,
    total_rows          INTEGER NOT NULL DEFAULT 0,
    performed_by        INTEGER REFERENCES users(id),
    started_at          TEXT,
    finished_at         TEXT,
    notes               TEXT
);
CREATE INDEX IF NOT EXISTS idx_supplier_import_supplier ON supplier_import_log(supplier_id);

-- pricing_config: admin-editable delivery/route assumptions (key/value).
-- Falls back to settings.py defaults when a key is absent.
CREATE TABLE IF NOT EXISTS pricing_config (
    key                 TEXT PRIMARY KEY,
    value               TEXT,
    updated_at          TEXT,
    updated_by          INTEGER REFERENCES users(id)
);

-- ============================================================
-- pipeline_runs: audit + orchestration record for whole-pipeline
-- runs (e.g. the automated morning refresh). One row per run.
-- Doubles as the cross-process lock: an in-flight run holds a
-- 'running' row and a second overlapping run is refused.
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type                TEXT NOT NULL,          -- e.g. 'morning_refresh'
    trigger_source          TEXT,                   -- 'scheduler' | 'manual' | 'cli'
    triggered_by            INTEGER REFERENCES users(id),  -- NULL for scheduler/cli
    started_at              TEXT NOT NULL,
    completed_at            TEXT,
    status                  TEXT NOT NULL DEFAULT 'running'
                            CHECK(status IN ('running','succeeded','partial','failed')),
    jurisdictions_attempted INTEGER NOT NULL DEFAULT 0,
    jurisdictions_succeeded INTEGER NOT NULL DEFAULT 0,
    jurisdictions_failed    INTEGER NOT NULL DEFAULT 0,
    records_received        INTEGER NOT NULL DEFAULT 0,
    records_created         INTEGER NOT NULL DEFAULT 0,
    records_updated         INTEGER NOT NULL DEFAULT 0,
    records_unchanged       INTEGER NOT NULL DEFAULT 0,
    submitted_only_records  INTEGER NOT NULL DEFAULT 0,
    errors_json             TEXT,                   -- JSON array of {stage,jurisdiction,error}
    summary_json            TEXT,                   -- JSON blob: freshness + stage timings + counts
    created_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_type_time
    ON pipeline_runs(run_type, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status);


-- ============================================================
-- Server-backed material list workflow
-- Organization-owned drafts and immutable line snapshots.
-- ============================================================
CREATE TABLE IF NOT EXISTS material_lists (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id     INTEGER NOT NULL REFERENCES organizations(id),
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    project_id          INTEGER REFERENCES projects(id),
    created_by_user_id  INTEGER NOT NULL REFERENCES users(id),
    needed_by           TEXT,
    delivery_preference TEXT,
    notes               TEXT,
    source_name         TEXT,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK(status IN ('draft','ready_for_review','pricing_requested','needs_catalog_review','priced')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_material_lists_org_company
    ON material_lists(organization_id, company_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_material_lists_project
    ON material_lists(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS material_list_items (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    material_list_id            INTEGER NOT NULL REFERENCES material_lists(id) ON DELETE CASCADE,
    line_number                 INTEGER NOT NULL,
    product_id                  INTEGER REFERENCES products(id),
    quantity                    REAL NOT NULL,
    unit                        TEXT,
    requested_description       TEXT NOT NULL,
    manufacturer                TEXT,
    sku_snapshot                TEXT,
    supplier_price_snapshot     REAL,
    quantity_available_snapshot REAL,
    lead_time_days_snapshot     INTEGER,
    match_status                TEXT NOT NULL DEFAULT 'manual'
                                CHECK(match_status IN ('catalog','manual')),
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    UNIQUE(material_list_id, line_number)
);
CREATE INDEX IF NOT EXISTS idx_material_list_items_list
    ON material_list_items(material_list_id, line_number);
