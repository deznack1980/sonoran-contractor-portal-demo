# CorridorIQ

CorridorIQ is an AI-powered construction procurement intelligence platform. It identifies
construction projects early from **real public permit data**, estimates likely material
requirements, builds contractor profiles, and lays groundwork for connecting contractors with
suppliers.

The platform runs two modules:

- **Plumbing Permit Intelligence** (primary focus) — for suppliers. Ingests real public permit
  records, scores each project's opportunity, estimates likely materials (PEX, copper, valves,
  water heaters, etc.), and builds contractor profiles from real permit history. Live today for
  nine Arizona cities (Mesa, Tempe, Gilbert, Scottsdale, Chandler, Peoria, Goodyear,
  Phoenix, Buckeye); remaining jurisdictions are tracked in
  [`pipeline/config/jurisdictions.yaml`](pipeline/config/jurisdictions.yaml) with exact status and
  the reason each one is or isn't connected yet.
- **Industrial/Freight Intelligence** (secondary/legacy) — for logistics companies. The original
  MVP concept for freight brokers, carriers, and staffing firms. Still uses illustrative demo
  data only — it has not been connected to a real data pipeline.

**Nothing in the live dashboard or generated reports is fabricated.** Every permit, contractor,
and score traces back to a real ingested public record. Jurisdictions without a verified data
source are marked "pending," never faked.

## Project structure

```
CorridorIQ/
├── index.html                 Homepage
├── dashboard.html              Live dashboard — real data from data/exports/
├── dashboard.js                Fetches data/exports/*.json, renders the dashboard
├── contractors.html            Contractor matching page — cross-jurisdiction directory
├── contractors.js              Fetches contractor_matching.json, renders searchable table
├── style.css                   Site styling
├── script.js                   Nav, form validation, interactivity
│
├── pipeline/                   Backend: ingestion, analysis, contractors, reports
│   ├── run.py                    CLI entry point (see "Running the pipeline" below)
│   ├── config/
│   │   ├── settings.py             Paths and tunable constants
│   │   └── jurisdictions.yaml      All 13 tracked AZ cities — status, connector, notes
│   ├── db/
│   │   ├── schema.sql              SQLite schema (7 required tables + 2 supporting tables)
│   │   ├── database.py             Connection helper, schema/seed application
│   │   └── corridoriq.db           SQLite database file (generated, not committed)
│   ├── connectors/                 Per-jurisdiction data source connectors
│   ├── ingestion/                  Fetch + upsert into SQLite
│   ├── analysis/                   Rule-based category/materials/scoring engine
│   ├── contractors/                Contractor profile aggregation
│   ├── suppliers/                  Price comparison (empty until real supplier data is added)
│   ├── buysheet/                   Per-project buy sheet generation
│   ├── export/                     SQLite → data/exports/*.json
│   └── reports/                    The 4 auto-generated markdown reports
│
├── data/
│   ├── exports/                  Generated JSON snapshots the dashboard reads (gitignore-able)
│   └── demo/                     FICTIONAL demo data from the original pitch site — see its README
│
├── reports/
│   ├── generated/                Real reports, generated from real data (gitignore-able)
│   └── demo/                     FICTIONAL demo report from the original pitch site
│
├── prompts/
│   └── data_collection_prompt.md   Research prompt for the (secondary) freight module
└── README.md                     This file
```

## Tech stack

- **Frontend:** pure HTML, CSS, and vanilla JavaScript. No frameworks, no build step, no npm, no
  external libraries or CDNs.
- **Backend/pipeline:** Python + SQLite. Uses `requests`, `beautifulsoup4`, `pyyaml`, and
  `python-dateutil` (see `pipeline/requirements.txt`) — this is a data-pipeline concern, separate
  from the frontend's no-npm rule.

## Running the pipeline

Install dependencies once:

```
pip install -r pipeline/requirements.txt
```

Run the full chain (init database → ingest real permits → analyze → rebuild contractor
profiles → export JSON → generate reports):

```
python pipeline/run.py
```

Or run a single step:

```
python pipeline/run.py ingest
python pipeline/run.py analyze
python pipeline/run.py rebuild-contractors
python pipeline/run.py export
python pipeline/run.py export-permits
python pipeline/run.py reports
```

`export-permits` writes the **full permit archive** (all ingested permits + scores) to:

- `data/exports/all_permits.csv` — always the latest snapshot
- `data/exports/all_permits_YYYY-MM-DD.csv` — dated copy for your records

There's no scheduler wired up yet — re-run manually (or wire up Task Scheduler/cron yourself) to
refresh the data.

## Opening CorridorIQ (launch port)

Double-click **CorridorIQ HQ** on your Desktop (or Start Menu), or run from this folder:

```
CorridorIQHQ.bat
```

That starts the secure portal API on port `8780` (if it isn't already running) and opens
`login.html` for employee sign-in. To recreate the shortcuts after moving the folder:

```
powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1
```

## Running the website locally

The dashboard fetches JSON over `fetch()`, which browsers block under `file://`. A local server
is required (not optional, now that the dashboard exists):

```
python -m http.server 8000
```

Then open:

```
http://localhost:8000            homepage
http://localhost:8000/dashboard.html   live dashboard
```

(If `python` isn't on your PATH, try `python3 -m http.server 8000` or `py -m http.server 8000`
on Windows.)

## Where things live

- **Live dashboard:** `dashboard.html` / `dashboard.js`, reading `data/exports/*.json`
- **Full permit archive (CSV):** `python pipeline/run.py export-permits` → `data/exports/all_permits.csv`
- **SQLite source of truth:** `pipeline/db/corridoriq.db`
- **Contractor matching across jurisdictions:** `contractors.html` / `contractors.js`, reading
  `data/exports/contractor_matching.json` — surfaces contractors pulling permits in more than one
  connected jurisdiction (a contractor expanding into a new market is a live buying signal there).
  Matched by normalized contractor name across jurisdictions (license number is recorded but not
  used as part of the match key, since it's inconsistently available per source — see the
  docstring in `pipeline/contractors/rebuild.py` for why).
- **Jurisdiction connection status (source of truth):** `pipeline/config/jurisdictions.yaml`
- **Scoring/materials-estimation methodology:** `pipeline/analysis/constants.py` (all weights,
  fractions, and margins are documented heuristic assumptions — see the module docstrings)
- **Real generated reports:** `reports/generated/`
- **Fictional demo content from the original pitch site:** `data/demo/`, `reports/demo/` — see
  `data/demo/README.md`

## Status

**Connected (9):** Mesa (Socrata), Tempe, Gilbert, Scottsdale, Chandler, Peoria, Goodyear,
Phoenix, and **Buckeye** (EnerGov hosted FeatureServer on maps.buckeyeaz.gov). Buckeye was
added 2026-07-14: ~51k permits with valuation/sqft; no contractor names on that layer.

**Pending (4):** Glendale, Surprise, Avondale, Queen Creek — each has a dated reason in
`pipeline/config/jurisdictions.yaml`. Re-check (2026-07-14): Glendale `Building_Safety` and
Avondale `Accela` folders require ArcGIS tokens; Surprise/Queen Creek have no public
per-permit layers. No jurisdiction is marked "connected" without a verified endpoint
returning real individual records.

Sales briefs list every permit at or above `REPORT_MIN_OPPORTUNITY_SCORE` (default 60),
sorted highest score to lowest — no quantity cap. Gilbert's public layer still has no
contractor-identity field — that gap is in the source, not fabricated away.

Suppliers/quotes/deliveries tables exist with the correct schema but launch empty — no fabricated
pricing. Price comparison and buy-sheet cost estimates fall back to clearly-labeled heuristic
estimates until real supplier price books are added.
