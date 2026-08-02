# ⚠ Demo Data — Fictional, Not Real

Everything in this folder is **fictional demonstration data**, invented to show the shape of the
CorridorIQ product before real data ingestion existed. No company, contractor, contact, permit,
or figure in `sample_leads.csv`, `sample_leads.xlsx`, or `schema.md` corresponds to a real permit,
person, or business.

**This data is never presented as real anywhere in the product.**

For real, live permit data — sourced from verified public jurisdiction APIs, never fabricated —
see:

- [`/data/exports/`](../exports/) — JSON snapshots generated from the real SQLite database, consumed by [`dashboard.html`](../../dashboard.html)
- [`/pipeline/`](../../pipeline/) — the ingestion pipeline, connector source code, and per-jurisdiction connection status (`pipeline/config/jurisdictions.yaml`)
- [`/reports/generated/`](../../reports/generated/) — auto-generated reports built only from real ingested permits

This folder is kept only as a design reference for the original demo/pitch version of the site.
