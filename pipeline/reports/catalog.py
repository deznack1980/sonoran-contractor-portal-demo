"""Employee-facing reports catalog.

Describes the available reports and lists already-generated files by *name only*
(never absolute filesystem paths). Downloads are permission-checked and
path-traversal safe.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pipeline.auth.rbac import require_permission
from pipeline.config import settings

# Human-friendly report cards (Phase 9). file_prefix links to generated files.
REPORT_CARDS = [
    {"key": "company_opportunity", "title": "Company Opportunity Report",
     "description": "Ranked companies by priority score and recent project activity.",
     "audience": "Sales reps and managers", "file_prefixes": ["company_", "opportunity_companies"]},
    {"key": "project_opportunity", "title": "Project Opportunity Report",
     "description": "High-scoring projects and permits that created the opportunity.",
     "audience": "Sales reps", "file_prefixes": ["opportunity_", "project_"]},
    {"key": "employee_activity", "title": "Employee Activity Report",
     "description": "Calls, conversations, appointments, and quote requests per employee.",
     "audience": "Managers", "file_prefixes": ["employee_activity", "activity_"]},
    {"key": "followup", "title": "Follow-Up Report",
     "description": "Due and overdue follow-ups across the team.",
     "audience": "Managers and reps", "file_prefixes": ["followup", "follow_up"]},
    {"key": "crm_pipeline", "title": "CRM Pipeline Report",
     "description": "Relationship stages from new to won/lost.",
     "audience": "Managers", "file_prefixes": ["crm_pipeline", "pipeline_"]},
]

_ALLOWED_SUFFIXES = (".md", ".xlsx", ".pdf", ".csv")


def _category_for(name: str) -> str:
    lower = name.lower()
    for card in REPORT_CARDS:
        if any(lower.startswith(p) for p in card["file_prefixes"]):
            return card["key"]
    if lower.startswith("supplier"):
        return "supplier"
    if lower.startswith("security") or lower.startswith("crm_access"):
        return "security"
    if lower.startswith("ui_ux"):
        return "ui_ux"
    return "other"


def list_generated_files() -> list[dict]:
    out = []
    d = settings.REPORTS_GENERATED_DIR
    if not d.exists():
        return out
    for p in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_file() or p.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        st = p.stat()
        out.append({
            "name": p.name,
            "category": _category_for(p.name),
            "type": p.suffix.lstrip(".").upper(),
            "size_kb": round(st.st_size / 1024, 1),
            "generated_at": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                            .isoformat(timespec="seconds"),
        })
    return out


def catalog(conn: sqlite3.Connection, user: dict) -> dict:
    require_permission(user, "reports.view")
    return {"reports": REPORT_CARDS, "files": list_generated_files()}


def safe_generated_path(name: str):
    """Resolve a generated-report filename safely, or return None."""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    if not name.lower().endswith(_ALLOWED_SUFFIXES):
        return None
    d = settings.REPORTS_GENERATED_DIR.resolve()
    target = (d / name).resolve()
    if target.parent != d or not target.is_file():
        return None
    return target
