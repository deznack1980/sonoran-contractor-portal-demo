# Lead Data Schema

This document defines every field in [`sample_leads.csv`](sample_leads.csv). CorridorIQ runs two
connected intelligence modules — **Plumbing Permit Intelligence** (for suppliers) and
**Industrial/Freight Intelligence** (for logistics companies) — on one shared schema, so a single
CSV can carry both lead types. The `lead_type` field tells you which module a row belongs to.

## Fields

| Field | Type | Description |
|---|---|---|
| `lead_type` | string | `Industrial/Logistics` or `Plumbing Permit`. Determines which of `company_name` / `contractor_name` is populated and how the rest of the row should be read. |
| `company_name` | string | Populated for `Industrial/Logistics` rows: the company operating the facility. Left blank for `Plumbing Permit` rows. |
| `contractor_name` | string | Populated for `Plumbing Permit` rows: the plumbing contractor, remodeler, or builder who pulled the permit. Left blank for `Industrial/Logistics` rows. |
| `city` | string | City where the facility or permitted project is located. |
| `state` | string | Two-letter state code. All sample rows are `AZ`. |
| `permit_type` | string | For `Plumbing Permit` rows, one of the ten tracked permit types (see below). Blank for `Industrial/Logistics` rows. |
| `signal_type` | string | The event that triggered the lead. Industrial rows use signals like `New Lease Signed`, `Hiring Surge`, `Facility Expansion Announcement`, `New Tenant Move-in`, `Manufacturing Expansion`, `Supplier and Distributor Growth`. Plumbing rows use `Permit Filed` or `Permit Issued`. |
| `opportunity_score` | integer (0–100) | CorridorIQ's composite score for how likely this lead is to convert into freight, warehousing, staffing, or materials demand soon. See "How the score is built" below. |
| `likely_need` | string | The most probable need. Industrial rows use a single category (`Short-haul delivery`, `Overflow delivery`, `Warehouse transfer`, `Supplier runs`, `Staffing support`). Plumbing rows list the likely materials (e.g. `PEX fittings, valves, water heaters, gas line supplies`). |
| `best_contact` | string | The role most likely to own the buying decision — a job title (e.g. Operations Manager) for industrial leads, or a role description (e.g. "Project manager or purchasing contact") for plumbing leads. |
| `notes` | string | Free-text analyst notes explaining the signal and why it matters. May contain commas — always quoted in the CSV. |

## Plumbing permit types tracked

- Plumbing permits
- Gas line permits
- Water heater replacements
- Sewer permits
- Backflow permits
- Pool plumbing
- Remodel permits
- Tenant improvements
- New residential construction
- New commercial construction

## How the opportunity score is built

The `opportunity_score` is a weighted composite, roughly:

- **Signal strength (40%)** — how directly the signal implies purchasing or logistics need. A
  new lease, a facility expansion, or a newly issued commercial permit scores higher than a
  general hiring uptick or a routine renewal (e.g. an annual backflow test).
- **Signal recency (25%)** — signals lose value as they age. A signal from the last 30 days
  scores highest; anything older than ~120 days is heavily discounted.
- **Scale (20%)** — for industrial leads, larger facility size and headcount imply larger freight
  volume. For plumbing leads, project scope (new commercial construction > single-fixture
  replacement) plays the same role.
- **Sector/project fit (15%)** — some sectors and permit types carry higher baseline demand
  intensity than others (e.g. new commercial construction or cold storage expansion score higher
  than a routine compliance permit).

Scores of 85+ indicate leads worth prioritizing for immediate outreach. Scores in the 60–84
range are solid secondary targets. CorridorIQ does not currently publish leads scoring below 60.

## Notes on the sample data

All rows in `sample_leads.csv` are **fictional** — invented company names, contractor names, and
locations used to demonstrate the product shape. No real companies, people, or events are
represented. Production leads will be sourced from public records and news per the process in
[`prompts/data_collection_prompt.md`](../prompts/data_collection_prompt.md).
