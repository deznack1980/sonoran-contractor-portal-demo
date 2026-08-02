"""Shared string-building helpers for the generated sales briefs.

Every report opens with a live-data-coverage banner naming which
jurisdictions are connected vs. pending — the transparency requirement
extends to report output, not just the dashboard.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def coverage_line(conn: sqlite3.Connection) -> tuple[str, str, int, int]:
    connected = conn.execute(
        "SELECT name FROM jurisdictions WHERE status = 'connected' ORDER BY name"
    ).fetchall()
    pending = conn.execute(
        "SELECT name FROM jurisdictions WHERE status = 'pending' ORDER BY name"
    ).fetchall()
    connected_names = ", ".join(r["name"] for r in connected) or "none yet"
    pending_names = ", ".join(r["name"] for r in pending) or "none"
    return connected_names, pending_names, len(connected), len(pending)


def coverage_banner(conn: sqlite3.Connection) -> str:
    connected_names, pending_names, _, _ = coverage_line(conn)
    return (
        "> **Live data only** — every row below comes from ingested public permit records. "
        f"**Connected markets:** {connected_names}. "
        f"**Not yet included:** {pending_names}."
    )


def money(value) -> str:
    if value is None:
        return "—"
    return f"${value:,.0f}"


def pct(value) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}%"


_COMPANY_HINTS = (
    "LLC",
    "L.L.C",
    "INC",
    "CORP",
    "CO.",
    "COMPANY",
    "HOMES",
    "BUILDERS",
    "BUILDER",
    "CONSTRUCTION",
    "CONTRACTING",
    "CONTRACTORS",
    "PLUMBING",
    "ELECTRIC",
    "MECHANICAL",
    "DEVELOPMENT",
    "PROPERTIES",
    "PROPERTY",
    "HOLDINGS",
)


def looks_like_company(value: str | None) -> bool:
    """Heuristic: public project/party string that likely names a firm, not a residence."""
    if not value:
        return False
    upper = str(value).strip().upper()
    if len(upper) < 4:
        return False
    return any(hint in upper for hint in _COMPANY_HINTS)


def _row_get(row, key: str):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def contractor_display(row) -> str:
    """Best available outreach identity — never invent a name.

    Prefer published contractor / owner fields. When those are empty, some
    cities still publish a project or permit name (Tempe ProjectName,
    Phoenix PERMIT_NAME, Buckeye subdivision/project labels). Company-like
    strings are shown as-is; other project labels are marked ``Project ·``
    so buyers can chase the permit without mistaking a subdivision for a GC.
    """
    for key in ("general_contractor_name", "plumbing_contractor_name", "owner_name"):
        value = _row_get(row, key)
        if value is not None and str(value).strip():
            return str(value).strip()

    project = _row_get(row, "project_description")
    if project is not None and str(project).strip():
        text = str(project).strip()
        if looks_like_company(text):
            return text
        return f"Project · {text}"

    return "Not in source"


def has_named_party(row) -> bool:
    """True when a real person/firm name is available (not only a Project · label)."""
    for key in ("general_contractor_name", "plumbing_contractor_name", "owner_name"):
        value = _row_get(row, key)
        if value is not None and str(value).strip():
            return True
    project = _row_get(row, "project_description")
    return looks_like_company(project if project is None else str(project))


def job_brief_href(jurisdiction: str | None, permit_number: str | None) -> str | None:
    """Relative URL for the polished single-job brief page."""
    if not jurisdiction or not permit_number:
        return None
    from urllib.parse import quote

    return f"job.html?j={quote(str(jurisdiction), safe='')}&p={quote(str(permit_number), safe='')}"


def permit_link(jurisdiction: str | None, permit_number: str | None) -> str:
    """Markdown link to the job brief, or plain permit number if incomplete."""
    if not permit_number:
        return "—"
    href = job_brief_href(jurisdiction, permit_number)
    if not href:
        return str(permit_number)
    return f"[{permit_number}]({href})"


def contractor_coverage_note() -> str:
    return (
        "\n### Where contractor names come from\n\n"
        "- **Mesa / Peoria / Scottsdale / Goodyear:** contractor or applicant is usually "
        "published on the open feed — shown when present.\n"
        "- **Phoenix:** company/professional name (`PROFESS_NAME`) when the city publishes it; "
        "otherwise a company-like permit/project title when that is all the GIS layer has.\n"
        "- **Tempe:** contractor company fields exist but are often blank on newer permits; "
        "when blank, a company-like `ProjectName` (e.g. an LLC) is used if present.\n"
        "- **Buckeye / Gilbert / Chandler:** the public GIS layers do **not** include "
        "contractor contacts (Buckeye contacts live behind EnerGov/SmartGov login). "
        "Rows show a **Project ·** label from the city project/subdivision field when "
        "available, then use permit # + address to look up the filer in the city portal.\n"
    )

def report_header(
    title: str,
    conn: sqlite3.Connection,
    *,
    audience: str = "Plumbing suppliers & distributors",
    tagline: str = "Act on permits before the supply house does.",
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    connected_names, _, connected_n, _ = coverage_line(conn)
    return (
        f"# {title}\n\n"
        f"**CorridorIQ** · Buyer-ready intelligence brief  \n"
        f"**Audience:** {audience}  \n"
        f"**Generated:** {now}  \n"
        f"**Markets live:** {connected_n} ({connected_names})\n\n"
        f"*{tagline}*\n\n"
        f"{coverage_banner(conn)}\n"
    )


def actions_block(bullets: list[str]) -> str:
    lines = ["\n## Recommended next actions\n"]
    for i, bullet in enumerate(bullets, start=1):
        lines.append(f"{i}. {bullet}")
    lines.append("")
    return "\n".join(lines)


def scoring_footnote() -> str:
    return (
        "\n## How scores are built\n\n"
        "CorridorIQ opportunity scores weight **signal strength (40%)**, **recency (25%)**, "
        "**project scale (20%)**, and **sector fit (15%)** from real permit fields — "
        "so higher scores mean a tighter window to win the material order.\n"
    )
