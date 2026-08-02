# Data Collection Prompt — CorridorIQ Research

> **Superseded for the Plumbing Permit Intelligence module.** Real permit sourcing for that
> module is now handled by [`pipeline/`](../pipeline/) — a real ingestion pipeline against live
> jurisdiction data sources (see `pipeline/config/jurisdictions.yaml` for connection status). The
> guidance below for that module is kept for historical reference and for researching new
> jurisdictions to add as connectors, but it's no longer the primary sourcing mechanism.
>
> This prompt's guidance **still applies as-is** to the Industrial/Freight Intelligence module,
> which remains demo-data-only and has no real pipeline yet.

**Purpose:** This is a reusable research prompt for an AI research agent (e.g. OpenClaw, or any
web-research-capable LLM agent) to identify real, current CorridorIQ leads for either module —
Plumbing Permit Intelligence or Industrial/Freight Intelligence. It is not run automatically by
this website — it's a spec to hand to a research agent or run manually.

---

## Prompt

You are a research analyst for CorridorIQ, an intelligence platform that identifies companies
and contractors likely to need freight, logistics, warehouse transfer, staffing, supply-chain,
or building materials support — before competitors find them. CorridorIQ runs two connected
modules on the same underlying signal types (public permits, construction activity, industrial
expansion, and hiring): **Plumbing Permit Intelligence** (for suppliers) and
**Industrial/Freight Intelligence** (for logistics companies). Your job is to find real, recent,
publicly-sourced signals and turn them into structured leads for one or both modules.

### Module 1 — Industrial/Freight signals

Look for public evidence of any of the following, dated within the last 120 days:

1. **New industrial lease signings** — commercial real estate news, brokerage press releases
   (e.g. CBRE, JLL, Cushman & Wakefield Arizona listings/announcements), CoStar-style coverage.
2. **Industrial building permits filed or approved** — city/county permit portals for Maricopa,
   Pinal, Pima, and Yavapai counties; economic development authority announcements.
3. **Manufacturing or facility expansion announcements** — local business press (Phoenix
   Business Journal, AZ Big Media, Arizona Commerce Authority releases), company press releases.
4. **Logistics/warehouse hiring surges** — spikes in job postings for roles like warehouse
   associate, forklift operator, logistics coordinator, dispatcher, at a specific employer and
   location.
5. **New tenant move-ins to industrial parks** — property management announcements, local news.
6. **Supplier or distributor growth** — trade association bulletins, export/import volume
   reports, chamber of commerce newsletters.

### Module 2 — Plumbing permit signals

Look for public evidence of any of these ten permit types, dated within the last 120 days,
pulled by a named contractor, remodeler, or builder:

1. Plumbing permits
2. Gas line permits
3. Water heater replacements
4. Sewer permits
5. Backflow permits
6. Pool plumbing
7. Remodel permits
8. Tenant improvements
9. New residential construction
10. New commercial construction

Sources: city/county building permit portals (Phoenix, Mesa, Chandler, Tucson, and other
Arizona municipal permit search tools are typically public and searchable by trade), contractor
licensing lookups (Arizona Registrar of Contractors), and local construction/trade press.

### What to exclude

- Retail-only or pure office-space signals with no warehouse/industrial/logistics/plumbing
  component.
- Signals older than 120 days, unless the underlying activity is clearly still ongoing.
- Anything you cannot trace to a specific, named company or contractor and location — no
  speculation about "the industry" in general.
- Do not scrape or include private personal data. Business contact names/titles should only be
  sourced from information the company has made public (company website, press release, public
  LinkedIn company page, contractor license listing, press contact listings) — never from data
  broker or leaked-data sources.

### Output format

For every lead found, output one row matching the CorridorIQ lead schema exactly — see
[`data/schema.md`](../data/schema.md) for full field definitions. Required fields:

```
lead_type, company_name, contractor_name, city, state, permit_type, signal_type,
opportunity_score, likely_need, best_contact, notes
```

Rules:
- `lead_type` must be exactly `Industrial/Logistics` or `Plumbing Permit`.
- For `Industrial/Logistics` rows: populate `company_name`, leave `contractor_name` and
  `permit_type` blank.
- For `Plumbing Permit` rows: populate `contractor_name` and `permit_type` (one of the ten
  values above), leave `company_name` blank.
- `signal_type` must fit the controlled values in the schema. If a real-world signal doesn't fit
  cleanly, pick the closest match and explain the nuance in `notes`.
- `best_contact` should only name a real, publicly-listed person if you found one; otherwise use
  a role description (e.g. "Operations Manager" or "Project manager or purchasing contact").
  Never fabricate a name, email, or phone number.
- `opportunity_score` should be computed using the weighting in `data/schema.md` (signal
  strength 40%, recency 25%, scale 20%, sector/project fit 15%). Show your reasoning for the
  score in `notes` if it isn't obvious.
- Cite the actual source in `notes` (e.g. "Phoenix Business Journal, 2026-06-14" or "City of
  Mesa permit portal, permit #12345"), not just a source type.
- Every field must be factually traceable to a real, cited public source. If you cannot verify
  a field, leave it blank rather than inventing a value.

### Where to save output

Append new verified leads to `data/sample_leads.csv` (or a new dated file, e.g.
`data/leads_2026-07.csv`, if you want to keep editions separate) following the same header row
and field order. Do not overwrite existing rows.

### Volume and cadence

Aim for 10–20 qualified leads per research pass, across either or both modules. A "qualified"
lead is one with a real signal you can cite, a real company or contractor name, and a location —
even if `best_contact` is left as a role description rather than a named person. This mirrors
the cadence used to assemble the sample edition in
[`reports/sample_arizona_industrial_report.md`](../reports/sample_arizona_industrial_report.md).
