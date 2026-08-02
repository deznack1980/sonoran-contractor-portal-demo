# CorridorIQ — Company Intelligence Architecture (Sprint 4)

## Purpose

Move CorridorIQ from a **permit-centered** model to a **company-centered**
intelligence platform, without breaking the existing permit pipeline, scoring
engine, lifecycle engine, knowledge engine, reports, or dashboard.

The **company** is the primary business entity:

```text
Company
├── Contacts
├── Company roles (contractor, developer, owner, ...)
├── Projects
│   └── Permits
├── Activity history (timeline)
├── Intelligence metrics
└── Commercial relationship (future, per-tenant)
```

A company may hold **multiple roles**; we never create duplicate company records
just because a company plays more than one role.

## Design principles

1. **Canonical, shared intelligence.** Public construction data (companies,
   permits, projects, metrics, timeline) is canonical and shared — never
   duplicated per tenant.
2. **Non-destructive.** Raw permit fields (`permits.general_contractor_name`,
   `permits.owner_name`, `permits.raw_source_json`, `permits.status`,
   `permits.permit_type`) are never overwritten. Company links are **additive
   nullable foreign keys**.
3. **Confidence-based identity.** Name alone is not a unique key. Matching is
   deterministic and scored 0–100; ambiguous matches go to a review queue and
   are never silently merged.
4. **Auditable.** Every create / link / merge / match decision is recorded in
   `company_identity_audit_log`. Canonical companies with linked records are
   never hard-deleted — they are merged or deprecated with history preserved.
5. **Separation of scoring.** The **company priority score** is independent of
   the permit **opportunity score**. Sprint 4 does not modify the 40/25/20/15
   weights or the 60 publish threshold.

## Entity overview

| Entity | Role | Cardinality |
|---|---|---|
| `companies` | Canonical company | 1 |
| `company_roles` | Roles a company holds | Company 1—N Roles |
| `contacts` | Real source contacts (never invented) | Company 1—N Contacts |
| `company_aliases` | Every source name variant | Company 1—N Aliases |
| `company_intelligence` | Derived metrics (verified activity only) | Company 1—1 |
| `company_activity` | Deduplicated timeline | Company 1—N Activities |
| `company_match_review_queue` | Ambiguous identity matches (60–94) | — |
| `company_identity_audit_log` | Append-only identity history | — |
| `projects` (existing) | Analysis output per permit | Company 1—N via `*_company_id` |
| `permits` (existing) | Raw ingested permit | Company 1—N via `contractor_company_id` |

Projects link to companies through **five role-specific nullable FKs**
(`contractor_company_id`, `owner_company_id`, `developer_company_id`,
`architect_company_id`, `engineer_company_id`), so one company can appear on a
project in several capacities without duplication. This is the effective
many-to-many bridge between companies and projects/permits.

## Relationship to existing tables

- `projects.contractor_id` (legacy `contractors` table) is **retained**. The new
  `projects.contractor_company_id` points at the richer canonical `companies`
  record. The legacy `contractors` rebuild continues to work unchanged.
- `permits` and `projects` are extended only with nullable FK columns and
  indexes. No existing column is renamed, retyped, or dropped.

## Company identity resolution

Deterministic, confidence-based (`pipeline/company_resolution/`):

| Confidence | Decision | Behavior |
|---|---|---|
| 95–100 | `matched` | Automatic exact link |
| 80–94 | `matched` (probable) | Auto-link **unless** conflicting fields exist → review |
| 60–79 | `possible_match` | Enter `company_match_review_queue` |
| < 60 | `new_company` | Create a separate company record |

Signals considered: normalized legal name, DBA, contractor license number,
phone, email domain, address/city, municipality, and existing source id. All
source name variants are preserved as `company_aliases`.

## Company priority scoring (separate from permit scoring)

`company_priority_score` (0–100) is computed only from **verified permit/project
activity**: recent activity, project volume, average permit opportunity score,
commercial/residential mix, geographic footprint, and activity growth. Weights,
caps, and tiers are in `pipeline/config/settings.py`
(`COMPANY_PRIORITY_WEIGHTS`, `COMPANY_PRIORITY_CAPS`, `COMPANY_PRIORITY_TIERS`).
It never fabricates spend, revenue, preferred products, or customer behavior;
non-calculable fields are stored `NULL`.

## Migration assumptions & backward compatibility

- **Additive only.** New tables via `CREATE TABLE IF NOT EXISTS`; new columns via
  idempotent `ALTER TABLE ADD COLUMN` in `pipeline/db/database.py`
  (`_KNOWLEDGE_V21_COLUMNS`, extended for Sprint 4).
- **FK columns are nullable** with `DEFAULT NULL`, so SQLite can add them to the
  existing 109k-row `projects`/`permits` tables in place, and pre-existing rows
  remain valid.
- **Order-independent.** FK columns may reference `companies` before that table
  exists at `ALTER` time — SQLite only enforces FKs on row writes. Verified.
- **Existing tests, reports, dashboards keep working** because every read path
  either ignores the new columns or treats them as optional.
- **Backfill is idempotent and resumable** (`pipeline/company_resolution/backfill.py`),
  processes permits in configurable chunks, uses transactions, and never runs
  permit scoring (scoring inputs are unchanged).

## Multi-tenant readiness (Phase 10)

Billing and full tenant isolation are **out of scope**. To keep the door open:

- A nullable `organization_id` is added to **future user-owned relationship
  records** only: `company_activity` (manual notes / contact attempts),
  `quotes`, and `deliveries`. `NULL` means shared/canonical.
- Shared construction intelligence (`companies`, `permits`, `projects`,
  `company_intelligence`, auto `company_activity`) stays **single canonical
  copy** — never duplicated per tenant.
- Tenant-specific relationship activity (assignments, orders, quotes) will be
  layered on later against `organization_id`. **Boundary:** canonical
  intelligence is shared; commercial relationship state is per-organization.

See `corridoriq_company_intelligence_erd.mmd` for the Mermaid ERD and
`corridoriq_data_dictionary.md` for field-level documentation.
