# CorridorIQ — Company Intelligence Data Dictionary (Sprint 4)

All timestamps are ISO-8601 UTC strings. `id` columns are SQLite
`INTEGER PRIMARY KEY AUTOINCREMENT`. All FK columns are nullable unless noted.

## `companies`
Canonical company record. Name alone is **not** a unique key.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | Stable internal key. |
| legal_name | TEXT | Registered legal name, if known. |
| display_name | TEXT | Human-friendly name (first-seen raw name). |
| normalized_name | TEXT NOT NULL | Dedup key (upper, punctuation/suffix stripped). Indexed. |
| dba_name | TEXT | "Doing business as". |
| company_type_primary | TEXT | Primary role (mirrors `company_roles.is_primary`). |
| website, main_phone, main_email | TEXT | Contact info from source only. |
| address_line_1/2, city, state, postal_code, country | TEXT | Location. `country` default `US`. Indexed on (state, city). |
| latitude, longitude | REAL | Geocode, if available. |
| license_number | TEXT | Contractor license. Indexed — strongest match signal. |
| license_state, license_status | TEXT | License metadata. |
| year_established | INTEGER | Source only; else NULL. |
| employee_range, revenue_range | TEXT | Source only; never fabricated. |
| source_system | TEXT | Origin (e.g. `permits`, jurisdiction slug). Indexed with source_record_id. |
| source_record_id | TEXT | Preserved source identifier. |
| merged_into_id | INTEGER FK→companies(id) | Non-NULL when this record was merged into a survivor. |
| lifecycle_state | TEXT | `active` \| `merged` \| `deprecated`. |
| is_active | INTEGER | 1 = active. |
| first_seen_at, last_seen_at | TEXT | Activity window. |
| created_at, updated_at | TEXT NOT NULL | Audit. |

## `company_roles`
A company may hold multiple roles (no duplicate company per role).

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| company_id | INTEGER FK→companies | NOT NULL. |
| role_type | TEXT | One of: contractor, fulfillment_partner, supplier, manufacturer, developer, property_owner, architect, engineer, municipality, other. |
| is_primary | INTEGER | 1 = primary role. |
| effective_from, effective_to | TEXT | Optional validity window. |
| source | TEXT | Where the role came from. |
| confidence | REAL | 0–100. |
| created_at, updated_at | TEXT NOT NULL | Audit. |

Unique: `(company_id, role_type)`.

## `contacts`
Real contacts only — never invented from permit descriptions.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| company_id | INTEGER FK→companies | NOT NULL. |
| first_name, last_name, full_name | TEXT | |
| job_title, department | TEXT | |
| email, phone, mobile_phone | TEXT | |
| preferred_contact_method | TEXT | |
| is_primary | INTEGER | |
| source, source_record_id | TEXT | Provenance. |
| first_seen_at, last_seen_at, created_at, updated_at | TEXT | Audit. |

## `company_aliases`
Every source name variant is preserved for future matching.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| company_id | INTEGER FK→companies | NOT NULL. |
| alias_name | TEXT NOT NULL | Raw variant. |
| normalized_alias | TEXT NOT NULL | Indexed. |
| source, source_record_id | TEXT | |
| first_seen_at, last_seen_at, created_at | TEXT | Audit. |

Unique: `(company_id, normalized_alias)`.

## `company_match_review_queue`
Ambiguous identity matches (confidence 60–94 with conflicts) awaiting review.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| source_record_type | TEXT NOT NULL | e.g. `permit`, `project`. |
| source_record_id | TEXT NOT NULL | |
| candidate_company_id | INTEGER FK→companies | Best candidate. |
| proposed_company_name | TEXT | Raw source name. |
| normalized_name | TEXT | |
| match_confidence | REAL | 0–100. |
| match_reasons | TEXT | Human-readable reasons (JSON/CSV). |
| conflicting_fields | TEXT | Fields that disagreed. |
| lifecycle_state | TEXT | `pending` \| `approved` \| `rejected`. |
| reviewed_by, reviewed_at, review_notes | TEXT | Review metadata. |
| created_at, updated_at | TEXT NOT NULL | Audit. |

Unique: `(source_record_type, source_record_id, candidate_company_id)`.

## `company_intelligence`
Derived metrics — verified permit/project activity only. Non-calculable → NULL.

| Column | Type | Notes |
|---|---|---|
| company_id | INTEGER PK FK→companies | One row per company. |
| total_permits, active_permits | INTEGER | Active = opportunity_date within `COMPANY_ACTIVE_WINDOW_DAYS`. |
| total_projects, active_projects | INTEGER | |
| projects_last_7/30/90_days | INTEGER | By opportunity_date. |
| commercial_project_count, residential_project_count | INTEGER | |
| municipality_count | INTEGER | Distinct jurisdictions. |
| first_activity_date, latest_activity_date | TEXT | |
| average_opportunity_score, highest_opportunity_score | REAL | From `projects.opportunity_score`. |
| estimated_opportunity_total | REAL | Sum of `estimated_material_value`. |
| permit_growth_30d/90d/12m | REAL | Ratio vs prior window; NULL if insufficient data. |
| activity_trend | TEXT | `increasing` \| `steady` \| `decreasing` \| NULL. |
| company_priority_score | REAL | 0–100, **separate** from permit scoring. Indexed. |
| company_priority_tier | TEXT | Critical/High/Medium/Low. |
| metrics_calculated_at | TEXT | |
| model_version | TEXT | `COMPANY_METRICS_MODEL_VERSION`. |

## `company_activity`
Canonical timeline. Deduplicated across reruns via `dedupe_key`.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| company_id | INTEGER FK→companies | NOT NULL. |
| activity_type | TEXT NOT NULL | permit_submitted, permit_issued, permit_finaled, project_created, project_stage_changed, company_discovered, company_profile_updated, manual_note, contact_attempt. |
| activity_date | TEXT | |
| project_id, permit_id, contact_id | INTEGER FK | Optional links. |
| title, description | TEXT | |
| source | TEXT | |
| metadata_json | TEXT | |
| dedupe_key | TEXT UNIQUE | Deterministic for auto events; NULL for user notes (many allowed). |
| organization_id | INTEGER | Phase 10: NULL = shared. Set for per-tenant notes/attempts. |
| created_at | TEXT NOT NULL | Audit. |

## `company_identity_audit_log`
Permanent, append-only. Companies with linked records are never hard-deleted.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| action | TEXT NOT NULL | created, linked, merged, unlinked, match_approved, match_rejected, alias_added, profile_updated. |
| source_company_id, target_company_id | INTEGER | |
| source_record_type, source_record_id | TEXT | |
| previous_values_json, new_values_json | TEXT | Before/after snapshots. |
| reason | TEXT | |
| confidence | REAL | |
| performed_by | TEXT | |
| performed_at | TEXT NOT NULL | Audit. |

## Extensions to existing tables (additive, nullable)

| Table | New column | Type | Notes |
|---|---|---|---|
| projects | contractor_company_id | INTEGER FK→companies | Nullable. |
| projects | owner_company_id | INTEGER FK→companies | Nullable. |
| projects | developer_company_id | INTEGER FK→companies | Nullable. |
| projects | architect_company_id | INTEGER FK→companies | Nullable. |
| projects | engineer_company_id | INTEGER FK→companies | Nullable. |
| permits | contractor_company_id | INTEGER FK→companies | Nullable. |
| quotes | organization_id | INTEGER | Phase 10 tenant readiness. |
| deliveries | organization_id | INTEGER | Phase 10 tenant readiness. |

**Preserved unchanged:** `permits.general_contractor_name`,
`permits.plumbing_contractor_name`, `permits.owner_name`,
`permits.contractor_license_number`, `permits.raw_source_json`,
`permits.status`, `permits.permit_type`, and all existing `projects` scoring
columns. These are the raw source values referenced by the sprint
(`raw_contractor_name` / `raw_owner_name` / `raw_applicant_name` equivalents).
