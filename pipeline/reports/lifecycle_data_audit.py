"""Sprint 3 Phase 1 — lifecycle date availability audit.

For each jurisdiction, determine which lifecycle dates are actually available:
Application Submitted, Permit Created, Plan Review, Permit Issued, Inspection,
Final, Closed. Coverage is measured from mapped permit columns AND by probing
``raw_source_json`` for lifecycle keys that exist in the source but are not yet
mapped to a column.

Read-only. Generates:
    reports/generated/lifecycle_data_audit_<date>.md
    reports/generated/lifecycle_data_audit_<date>.xlsx
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from pipeline.config.settings import REPORTS_GENERATED_DIR

RAW_SAMPLE_SIZE = 250

# Lifecycle event -> (mapped columns that satisfy it, regex over raw JSON keys).
LIFECYCLE_EVENTS = [
    ("Application Submitted", ["filed_date"], r"appl|submit|file|apply"),
    ("Permit Created", [], r"creat|entered|ent_?date|per_ent|opendate|open_date"),
    ("Plan Review", [], r"review|plan.?check|plancheck|plan_?review"),
    ("Permit Issued", ["issued_date"], r"issue"),
    ("Inspection", ["inspection_status", "last_inspection_date"], r"inspect"),
    ("Final", ["finaled_date"], r"final|complet|cmpdate|completion"),
    ("Closed", [], r"clos|co_dt|cert.*occup|c_of_o|expire|expira"),
]


def _connected_jurisdictions(conn: sqlite3.Connection) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT slug, name, status FROM jurisdictions ORDER BY "
            "(status='connected') DESC, name"
        )
    ]


def _column_coverage(conn: sqlite3.Connection, slug: str, column: str) -> int:
    return conn.execute(
        f"SELECT COUNT(*) AS n FROM permits "
        f"WHERE jurisdiction=? AND {column} IS NOT NULL AND TRIM({column}) <> ''",
        (slug,),
    ).fetchone()["n"]


def _raw_keys(conn: sqlite3.Connection, slug: str) -> set[str]:
    keys: set[str] = set()
    rows = conn.execute(
        "SELECT raw_source_json FROM permits WHERE jurisdiction=? "
        "AND raw_source_json IS NOT NULL LIMIT ?",
        (slug, RAW_SAMPLE_SIZE),
    ).fetchall()
    for r in rows:
        try:
            obj = json.loads(r["raw_source_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            keys.update(str(k) for k in obj.keys())
    return keys


def _audit_jurisdiction(conn: sqlite3.Connection, jur: dict) -> dict:
    slug = jur["slug"]
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM permits WHERE jurisdiction=?", (slug,)
    ).fetchone()["n"]
    raw_keys = _raw_keys(conn, slug) if total else set()

    events: dict[str, dict] = {}
    for event, columns, pattern in LIFECYCLE_EVENTS:
        mapped = 0
        for col in columns:
            mapped += _column_coverage(conn, slug, col) if total else 0
        matched_keys = sorted(k for k in raw_keys if re.search(pattern, k, re.IGNORECASE))
        if mapped > 0:
            state = "Mapped"
        elif matched_keys:
            state = "Raw only"
        else:
            state = "None"
        events[event] = {
            "state": state,
            "mapped_count": mapped,
            "coverage_pct": round(100.0 * mapped / total, 1) if total else 0.0,
            "raw_keys": matched_keys,
        }
    return {
        "slug": slug,
        "name": jur["name"],
        "status": jur["status"],
        "total_permits": total,
        "events": events,
    }


def _symbol(state: str) -> str:
    return {"Mapped": "✓", "Raw only": "(raw)", "None": "—"}.get(state, "—")


def generate_lifecycle_data_audit(conn: sqlite3.Connection) -> list[str]:
    jurisdictions = _connected_jurisdictions(conn)
    audits = [_audit_jurisdiction(conn, j) for j in jurisdictions]
    event_names = [e[0] for e in LIFECYCLE_EVENTS]

    today = date.today().isoformat()
    REPORTS_GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    md_path = REPORTS_GENERATED_DIR / f"lifecycle_data_audit_{today}.md"
    xlsx_path = REPORTS_GENERATED_DIR / f"lifecycle_data_audit_{today}.xlsx"

    # ---------- Markdown ----------
    lines = [f"# Lifecycle Data Availability Audit — {today}", ""]
    lines.append(
        "Which lifecycle dates each jurisdiction actually provides. "
        "`✓` = mapped to a permit column; `(raw)` = present in the source feed "
        "(`raw_source_json`) but not yet mapped; `—` = not found. "
        "Nothing here changes scoring or normalization."
    )
    lines.append("")
    header = "| Jurisdiction | Permits | " + " | ".join(event_names) + " |"
    sep = "| --- | ---: | " + " | ".join(["---"] * len(event_names)) + " |"
    lines.append(header)
    lines.append(sep)
    for a in audits:
        cells = [_symbol(a["events"][e]["state"]) for e in event_names]
        lines.append(
            f"| {a['name']} ({a['status']}) | {a['total_permits']:,} | "
            + " | ".join(cells)
            + " |"
        )
    lines.append("")

    lines.append("## Coverage detail (mapped columns)")
    lines.append("")
    lines.append(
        "| Jurisdiction | Application Submitted | Permit Issued | Final | "
        "Notes on unmapped source dates |"
    )
    lines.append("| --- | ---: | ---: | ---: | --- |")
    for a in audits:
        sub = a["events"]["Application Submitted"]
        iss = a["events"]["Permit Issued"]
        fin = a["events"]["Final"]
        raw_notes = []
        for event in ("Permit Created", "Plan Review", "Inspection", "Closed"):
            ev = a["events"][event]
            if ev["state"] == "Raw only" and ev["raw_keys"]:
                raw_notes.append(f"{event}: {', '.join(ev['raw_keys'][:3])}")
        lines.append(
            f"| {a['name']} | {sub['mapped_count']:,} ({sub['coverage_pct']}%) "
            f"| {iss['mapped_count']:,} ({iss['coverage_pct']}%) "
            f"| {fin['mapped_count']:,} ({fin['coverage_pct']}%) "
            f"| {'; '.join(raw_notes) or '—'} |"
        )
    lines.append("")
    lines.append(
        "> Do not assume every jurisdiction exposes submitted dates. Where "
        "`Application Submitted` is `—`, CorridorIQ gracefully falls back to the "
        "issued date for `opportunity_date`."
    )
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    # ---------- Excel ----------
    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF")

    ws = wb.active
    ws.title = "Lifecycle matrix"
    ws.append(["Jurisdiction", "Status", "Permits"] + event_names)
    for a in audits:
        ws.append(
            [a["name"], a["status"], a["total_permits"]]
            + [_symbol(a["events"][e]["state"]) for e in event_names]
        )
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
    ws.freeze_panes = "A2"

    ws2 = wb.create_sheet("Coverage %")
    ws2.append(["Jurisdiction"] + [f"{e} (mapped)" for e in event_names])
    for a in audits:
        ws2.append([a["name"]] + [a["events"][e]["mapped_count"] for e in event_names])
    for cell in ws2[1]:
        cell.fill = header_fill
        cell.font = header_font
    ws2.freeze_panes = "A2"

    ws3 = wb.create_sheet("Raw keys detected")
    ws3.append(["Jurisdiction", "Event", "State", "Raw source keys"])
    for a in audits:
        for e in event_names:
            ev = a["events"][e]
            ws3.append([a["name"], e, ev["state"], ", ".join(ev["raw_keys"])])
    for cell in ws3[1]:
        cell.fill = header_fill
        cell.font = header_font
    ws3.freeze_panes = "A2"

    for sheet in wb.worksheets:
        for col in sheet.columns:
            width = max((len(str(c.value)) if c.value is not None else 0) for c in col)
            sheet.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 60)

    wb.save(xlsx_path)
    return [str(md_path), str(xlsx_path)]


if __name__ == "__main__":
    from pipeline.db.database import init_db

    connection = init_db()
    for p in generate_lifecycle_data_audit(connection):
        print(f"Wrote {p}")
    connection.close()
