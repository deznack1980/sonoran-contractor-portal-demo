"""opportunity_score, confidence_score, and estimated value/profit formulas.

Reuses the 40/25/20/15 (signal strength / recency / scale / sector fit)
weighting already documented for the original demo product in
data/demo/schema.md, adapted to real permit fields.

Normalization is applied to *derived* status/type values only — raw
``status`` and ``permit_type`` on the permit row are never overwritten.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from pipeline.analysis import constants as C
from typing import TYPE_CHECKING

from pipeline.analysis.normalization import (
    build_normalization_diagnostics,
    normalize_permit_status,
    normalize_permit_type,
)

if TYPE_CHECKING:
    from pipeline.knowledge.engine import KnowledgeEngine


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _municipality_from_row(permit_row: dict) -> str | None:
    # Prefer jurisdiction slug so dictionary keys stay stable across runs.
    for key in ("municipality", "jurisdiction", "jurisdiction_name"):
        value = permit_row.get(key)
        if value:
            return str(value)
    return None


def score_signal_strength(
    permit_type: str | None,
    status: str | None,
    *,
    municipality: str | None = None,
    description: str | None = None,
    knowledge: KnowledgeEngine | None = None,
) -> float:
    """Score using normalized status / type. Raw inputs are not mutated."""
    normalized_status = normalize_permit_status(status, municipality, knowledge)
    base = C.SIGNAL_STRENGTH_BY_STATUS.get(normalized_status, C.SIGNAL_STRENGTH_DEFAULT)
    normalized_type = normalize_permit_type(
        permit_type, description, municipality, knowledge
    )
    if normalized_type == "commercial":
        base = min(100, base + C.SIGNAL_STRENGTH_COMMERCIAL_BONUS)
    return base


def score_recency(issued_date: str | None, filed_date: str | None) -> float:
    reference = _parse_date(issued_date) or _parse_date(filed_date)
    if reference is None:
        return 0.0

    age_days = (datetime.now(timezone.utc) - reference).days
    if age_days <= C.RECENCY_FULL_SCORE_DAYS:
        return 100.0
    if age_days >= C.RECENCY_ZERO_SCORE_DAYS:
        return 0.0

    span = C.RECENCY_ZERO_SCORE_DAYS - C.RECENCY_FULL_SCORE_DAYS
    decayed = 100.0 * (1 - (age_days - C.RECENCY_FULL_SCORE_DAYS) / span)
    return max(0.0, decayed)


def score_scale(valuation: float | None, square_footage: float | None) -> float:
    if not valuation and not square_footage:
        return float(C.SCALE_NEUTRAL_SCORE)

    scores = []
    if valuation and valuation > 0:
        scores.append(min(100.0, 100.0 * math.log10(valuation + 1) / math.log10(C.SCALE_VALUATION_CAP)))
    if square_footage and square_footage > 0:
        scores.append(min(100.0, 100.0 * math.log10(square_footage + 1) / math.log10(C.SCALE_SQFT_CAP)))

    return sum(scores) / len(scores) if scores else float(C.SCALE_NEUTRAL_SCORE)


def scale_basis(valuation: float | None, square_footage: float | None) -> str:
    """Diagnostic only — does not change scale math."""
    if valuation and valuation > 0:
        return "valuation"
    if square_footage and square_footage > 0:
        return "square_footage"
    return "neutral_missing_data"


def score_sector_fit(project_category: str) -> float:
    return float(C.SECTOR_FIT_BY_CATEGORY.get(project_category, C.SECTOR_FIT_BY_CATEGORY["Other"]))


def _opportunity_components(
    permit_row: dict,
    project_category: str,
    knowledge: KnowledgeEngine | None = None,
) -> dict:
    """Raw 0–100 component scores used by the opportunity formula."""
    municipality = _municipality_from_row(permit_row)
    signal = score_signal_strength(
        permit_row.get("permit_type"),
        permit_row.get("status"),
        municipality=municipality,
        description=permit_row.get("description"),
        knowledge=knowledge,
    )
    recency = score_recency(permit_row.get("issued_date"), permit_row.get("filed_date"))
    scale = score_scale(permit_row.get("valuation"), permit_row.get("square_footage"))
    sector = score_sector_fit(project_category)
    return {
        "signal_strength": signal,
        "recency": recency,
        "scale": scale,
        "sector_fit": sector,
        "scale_basis": scale_basis(permit_row.get("valuation"), permit_row.get("square_footage")),
    }


def compute_opportunity_score(
    permit_row: dict,
    project_category: str,
    knowledge: KnowledgeEngine | None = None,
) -> float:
    parts = _opportunity_components(permit_row, project_category, knowledge)
    score = (
        parts["signal_strength"] * C.WEIGHT_SIGNAL_STRENGTH
        + parts["recency"] * C.WEIGHT_RECENCY
        + parts["scale"] * C.WEIGHT_SCALE
        + parts["sector_fit"] * C.WEIGHT_SECTOR_FIT
    )
    return round(min(100.0, max(0.0, score)), 1)


def compute_opportunity_score_breakdown(
    permit_row: dict,
    project_category: str,
    knowledge: KnowledgeEngine | None = None,
) -> dict:
    """Same score as ``compute_opportunity_score``, plus weighted contributions."""
    parts = _opportunity_components(permit_row, project_category, knowledge)
    municipality = _municipality_from_row(permit_row)
    normalized_status = normalize_permit_status(
        permit_row.get("status"), municipality, knowledge
    )
    normalized_type = normalize_permit_type(
        permit_row.get("permit_type"),
        permit_row.get("description"),
        municipality,
        knowledge,
    )
    factors = [
        {
            "key": "signal_strength",
            "label": _signal_factor_label(permit_row, normalized_status, normalized_type),
            "raw_score": round(parts["signal_strength"], 1),
            "weight": C.WEIGHT_SIGNAL_STRENGTH,
            "points": round(parts["signal_strength"] * C.WEIGHT_SIGNAL_STRENGTH, 1),
        },
        {
            "key": "recency",
            "label": _recency_factor_label(permit_row),
            "raw_score": round(parts["recency"], 1),
            "weight": C.WEIGHT_RECENCY,
            "points": round(parts["recency"] * C.WEIGHT_RECENCY, 1),
        },
        {
            "key": "scale",
            "label": _scale_factor_label(permit_row, parts["scale_basis"]),
            "raw_score": round(parts["scale"], 1),
            "weight": C.WEIGHT_SCALE,
            "points": round(parts["scale"] * C.WEIGHT_SCALE, 1),
        },
        {
            "key": "sector_fit",
            "label": _sector_factor_label(project_category),
            "raw_score": round(parts["sector_fit"], 1),
            "weight": C.WEIGHT_SECTOR_FIT,
            "points": round(parts["sector_fit"] * C.WEIGHT_SECTOR_FIT, 1),
        },
    ]
    drivers = _contextual_score_drivers(permit_row, project_category, knowledge)
    final = compute_opportunity_score(permit_row, project_category, knowledge)
    return {
        "final_score": final,
        "factors": factors,
        "drivers": drivers,
        "scale_basis": parts["scale_basis"],
        "normalized_status": normalized_status,
        "normalized_permit_type": normalized_type,
    }


def _signal_factor_label(permit_row: dict, normalized_status: str, normalized_type: str) -> str:
    raw = (permit_row.get("status") or "").strip() or "Unknown status"
    if normalized_type == "commercial":
        return f"Permit Signal ({raw} -> {normalized_status} / Commercial)"
    return f"Permit Signal ({raw} -> {normalized_status})"


def _recency_factor_label(permit_row: dict) -> str:
    if permit_row.get("issued_date"):
        return "Recent Issue Date"
    if permit_row.get("filed_date"):
        return "Recent Filing Date"
    return "Recency (date unknown)"


def _scale_factor_label(permit_row: dict, basis: str) -> str:
    if basis == "neutral_missing_data":
        return "Project Scale (neutral_missing_data)"
    valuation = permit_row.get("valuation") or 0
    sqft = permit_row.get("square_footage") or 0
    if valuation >= 1_000_000:
        return "High Material Value"
    if valuation >= 250_000 or sqft >= 10_000:
        return "Large Remodel / Build"
    return "Project Scale"


def _sector_factor_label(project_category: str) -> str:
    if project_category and project_category not in ("Other",):
        return f"{project_category} Project"
    return "Sector Fit"


def _contextual_score_drivers(
    permit_row: dict,
    project_category: str,
    knowledge: KnowledgeEngine | None = None,
) -> list[str]:
    text = f"{permit_row.get('permit_type') or ''} {permit_row.get('description') or ''}".lower()
    drivers: list[str] = []
    keyword_labels = (
        ("water heater", "Water Heater"),
        ("gas line", "Gas Work"),
        ("gas ", "Gas Work"),
        ("re-pipe", "Re-pipe Scope"),
        ("repipe", "Re-pipe Scope"),
        ("backflow", "Backflow Work"),
        ("sewer", "Sewer / Drainage"),
        ("copper", "Copper Piping"),
        ("pex", "PEX Piping"),
        ("remodel", "Remodel Scope"),
        ("tenant improvement", "Tenant Improvement"),
        ("ti ", "Tenant Improvement"),
    )
    seen: set[str] = set()
    for keyword, label in keyword_labels:
        if keyword in text and label not in seen:
            drivers.append(label)
            seen.add(label)
    municipality = _municipality_from_row(permit_row)
    if (
        normalize_permit_type(
            permit_row.get("permit_type"),
            permit_row.get("description"),
            municipality,
            knowledge,
        )
        == "commercial"
    ):
        drivers.append("Commercial Permit Type")
    if project_category in ("Restaurant", "Medical", "Hotel", "School", "Manufacturing"):
        drivers.append(f"{project_category} Sector")
    return drivers


def compute_confidence_score(permit_row: dict) -> float:
    present = sum(1 for field in C.CONFIDENCE_CHECK_FIELDS if permit_row.get(field))
    return round(100.0 * present / len(C.CONFIDENCE_CHECK_FIELDS), 1)


def compute_construction_stage(
    status: str | None,
    municipality: str | None = None,
    knowledge: KnowledgeEngine | None = None,
) -> str:
    normalized = normalize_permit_status(status, municipality, knowledge)
    return C.CONSTRUCTION_STAGE_BY_STATUS.get(normalized, C.CONSTRUCTION_STAGE_DEFAULT)


# ---------------------------------------------------------------
# Project lifecycle intelligence (Sprint 3) — additive, no scoring change.
# These consume the existing normalized status (read-only) plus raw-status
# keyword hints. They never modify weights, thresholds, or normalization.
# ---------------------------------------------------------------
def compute_project_lifecycle(
    status: str | None,
    municipality: str | None = None,
    knowledge: KnowledgeEngine | None = None,
) -> str:
    """Map a permit's status to a standardized project lifecycle stage."""
    raw = (status or "").strip().lower()
    # Keyword refinements distinguish stages the canonical status can't
    # (e.g. Chandler's hardcoded "Under Construction").
    for keyword, stage in C.LIFECYCLE_KEYWORD_REFINEMENTS:
        if keyword in raw:
            return stage
    normalized = normalize_permit_status(status, municipality, knowledge)
    return C.LIFECYCLE_STAGE_BY_STATUS.get(normalized, C.LIFECYCLE_STAGE_DEFAULT)


def compute_opportunity_date(permit_row: dict) -> tuple[str | None, str]:
    """Earliest usable outreach date + which source date it came from.

    Returns ``(date_string_or_None, basis_label)``. Source date columns are
    read only — never overwritten.
    """
    for basis, column in C.OPPORTUNITY_DATE_PRIORITY:
        value = permit_row.get(column)
        if value is not None and str(value).strip():
            return str(value), basis
    return None, "Unknown"


def compute_opportunity_timing(lifecycle_stage: str | None) -> str:
    """Opportunity timing (Excellent..Historical) based SOLELY on lifecycle."""
    if not lifecycle_stage:
        return C.OPPORTUNITY_TIMING_DEFAULT
    return C.OPPORTUNITY_TIMING_BY_LIFECYCLE.get(
        lifecycle_stage, C.OPPORTUNITY_TIMING_DEFAULT
    )


def days_since(date_value: str | None) -> int | None:
    """Whole days between a date string and now (UTC). None if unparseable."""
    parsed = _parse_date(date_value)
    if parsed is None:
        return None
    return max(0, (datetime.now(timezone.utc) - parsed).days)


def compute_estimated_material_value(valuation: float | None, square_footage: float | None, project_category: str) -> float | None:
    fraction = C.PLUMBING_FRACTION_BY_CATEGORY.get(project_category, C.PLUMBING_FRACTION_BY_CATEGORY["Other"])

    if valuation and valuation > 0:
        return round(valuation * fraction, 2)
    if square_footage and square_footage > 0:
        return round(square_footage * C.PLUMBING_VALUE_PER_SQFT_FALLBACK, 2)
    return None


def compute_estimated_gross_profit(estimated_material_value: float | None, project_category: str) -> float | None:
    if estimated_material_value is None:
        return None
    margin = C.GROSS_MARGIN_BY_CATEGORY.get(project_category, C.DEFAULT_GROSS_MARGIN)
    return round(estimated_material_value * margin, 2)


def attach_normalization_diagnostics(
    permit_row: dict,
    project_category: str,
    category_mapping_rule: str,
    knowledge: KnowledgeEngine | None = None,
) -> dict:
    """Build explainable normalization fields without mutating permit_row."""
    diag = build_normalization_diagnostics(
        raw_status=permit_row.get("status"),
        raw_permit_type=permit_row.get("permit_type"),
        description=permit_row.get("description"),
        municipality=_municipality_from_row(permit_row),
        project_category=project_category,
        category_mapping_rule=category_mapping_rule,
        valuation=permit_row.get("valuation"),
        square_footage=permit_row.get("square_footage"),
        knowledge=knowledge,
    )
    return {
        "raw_status": diag.raw_status,
        "normalized_status": diag.normalized_status,
        "status_mapping_rule": diag.status_mapping_rule,
        "raw_permit_type": diag.raw_permit_type,
        "normalized_permit_type": diag.normalized_permit_type,
        "permit_type_mapping_rule": diag.permit_type_mapping_rule,
        "project_category": diag.project_category,
        "category_mapping_rule": diag.category_mapping_rule,
        "scale_basis": diag.scale_basis,
    }
