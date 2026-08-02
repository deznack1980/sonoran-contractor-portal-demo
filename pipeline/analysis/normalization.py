"""Status and permit-type normalization (derived fields only).

Raw ``permits.status`` and ``permits.permit_type`` are never overwritten.

Resolution order:
1. Municipal / global dictionary (KnowledgeEngine) when provided
2. Built-in bootstrap aliases (seeded into dictionaries; kept as fallback)
3. ``unknown`` / ``other`` — and enqueue for human review when a knowledge
   engine is attached (never silently invent permanent mappings)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pipeline.knowledge.engine import KnowledgeEngine

# Canonical statuses consumed by SIGNAL_STRENGTH_BY_STATUS / construction stage.
CANONICAL_STATUSES = frozenset(
    {
        "issued",
        "in review",
        "applied",
        "finaled",
        "expired",
        "cancelled",
        "withdrawn",
        "unknown",
    }
)

# Canonical high-level permit types (lowercase).
CANONICAL_PERMIT_TYPES = frozenset(
    {
        "commercial",
        "residential",
        "plumbing",
        "mechanical",
        "electrical",
        "building",
        "gas",
        "water heater",
        "pool",
        "other",
    }
)


@dataclass(frozen=True)
class StatusNormalization:
    raw_status: str | None
    normalized_status: str
    status_mapping_rule: str


@dataclass(frozen=True)
class PermitTypeNormalization:
    raw_permit_type: str | None
    normalized_permit_type: str
    permit_type_mapping_rule: str


# Exact lowercase match after strip (global bootstrap rules).
_STATUS_EXACT: dict[str, str] = {
    "issued": "issued",
    "permit issued": "issued",
    "in review": "in review",
    "under review": "in review",
    "applied": "applied",
    "application submitted": "applied",
    "finaled": "finaled",
    "final": "finaled",
    "completed": "finaled",
    "complete": "finaled",
    "expired": "expired",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "withdrawn": "withdrawn",
}

# Municipality-specific bootstrap. Phoenix OPEN = issued/active permit.
_STATUS_BY_MUNICIPALITY: dict[str, dict[str, str]] = {
    "phoenix": {"open": "issued"},
    "phoenix_az": {"open": "issued"},
}

_PERMIT_TYPE_EXACT: dict[str, str] = {
    "com": "commercial",
    "commercial": "commercial",
    "res": "residential",
    "residential": "residential",
    "plmb": "plumbing",
    "plum": "plumbing",
    "plumb": "plumbing",
    "plumbing": "plumbing",
    "elec": "electrical",
    "electrical": "electrical",
    "electric": "electrical",
    "mech": "mechanical",
    "mechanical": "mechanical",
    "bld": "building",
    "bldg": "building",
    "building": "building",
    "gas": "gas",
    "gas line": "gas",
    "water heater": "water heater",
    "waterheater": "water heater",
    "wh": "water heater",
    "pool": "pool",
    "swimming pool": "pool",
}

_TYPE_TO_CATEGORY: dict[str, str] = {
    "commercial": "Commercial",
    "residential": "Residential",
    "plumbing": "Plumbing",
    "mechanical": "Mechanical",
    "electrical": "Electrical",
    "building": "Building",
    "gas": "Gas",
    "water heater": "Water Heater",
    "pool": "Pool",
}


def _municipality_key(municipality: str | None) -> str:
    if not municipality:
        return ""
    return str(municipality).strip().lower().replace(" ", "_")


def category_from_normalized_permit_type(normalized_permit_type: str) -> str | None:
    return _TYPE_TO_CATEGORY.get(normalized_permit_type)


def normalize_permit_status(
    raw_status: str | None,
    municipality: str | None = None,
    knowledge: KnowledgeEngine | None = None,
) -> str:
    return explain_status_normalization(raw_status, municipality, knowledge).normalized_status


def explain_status_normalization(
    raw_status: str | None,
    municipality: str | None = None,
    knowledge: KnowledgeEngine | None = None,
) -> StatusNormalization:
    if raw_status is None or not str(raw_status).strip():
        return StatusNormalization(raw_status, "unknown", "empty_or_null->unknown")

    cleaned = str(raw_status).strip()
    key = cleaned.lower()

    # 1) Dictionary (approved + seeded knowledge)
    if knowledge is not None:
        hit = knowledge.lookup_status(cleaned, municipality)
        if hit is not None:
            return StatusNormalization(cleaned, hit.canonical, hit.rule)

    muni = _municipality_key(municipality)
    muni_rules = _STATUS_BY_MUNICIPALITY.get(muni, {})
    if key in muni_rules:
        canonical = muni_rules[key]
        return StatusNormalization(
            cleaned,
            canonical,
            f"bootstrap:municipality:{muni}:{key}->{canonical}",
        )

    if key == "open":
        result = StatusNormalization(cleaned, "unknown", "open_unmapped_globally->unknown")
        if knowledge is not None:
            knowledge.enqueue_unknown(
                kind="status",
                municipality=municipality,
                raw_value=cleaned,
                suggested_interpretation="unknown",
                confidence=0.2,
            )
        return result

    if key in _STATUS_EXACT:
        canonical = _STATUS_EXACT[key]
        return StatusNormalization(cleaned, canonical, f"bootstrap:exact:{key}->{canonical}")

    if "withdrawn" in key:
        return StatusNormalization(cleaned, "withdrawn", "bootstrap:contains:withdrawn")
    if "cancel" in key:
        return StatusNormalization(cleaned, "cancelled", "bootstrap:contains:cancel")
    if "expir" in key:
        return StatusNormalization(cleaned, "expired", "bootstrap:contains:expir")
    if "final" in key or "complet" in key:
        return StatusNormalization(cleaned, "finaled", "bootstrap:contains:final|complet")
    if "review" in key:
        return StatusNormalization(cleaned, "in review", "bootstrap:contains:review")
    if key == "issued" or key.endswith(" issued") or key.startswith("issued "):
        return StatusNormalization(cleaned, "issued", "bootstrap:pattern:issued")
    if "appl" in key:
        return StatusNormalization(cleaned, "applied", "bootstrap:contains:appl")

    if knowledge is not None:
        knowledge.enqueue_unknown(
            kind="status",
            municipality=municipality,
            raw_value=cleaned,
            suggested_interpretation="unknown",
            confidence=0.15,
        )
    return StatusNormalization(cleaned, "unknown", f"unmapped:{key}->unknown")


def normalize_permit_type(
    raw_permit_type: str | None,
    description: str | None = None,
    municipality: str | None = None,
    knowledge: KnowledgeEngine | None = None,
) -> str:
    return explain_permit_type_normalization(
        raw_permit_type, description, municipality, knowledge
    ).normalized_permit_type


def explain_permit_type_normalization(
    raw_permit_type: str | None,
    description: str | None = None,
    municipality: str | None = None,
    knowledge: KnowledgeEngine | None = None,
) -> PermitTypeNormalization:
    if raw_permit_type is None or not str(raw_permit_type).strip():
        desc = (description or "").lower()
        if "water heater" in desc or "waterheater" in desc:
            return PermitTypeNormalization(
                raw_permit_type, "water heater", "description:water heater"
            )
        if "pool" in desc:
            return PermitTypeNormalization(raw_permit_type, "pool", "description:pool")
        if "gas " in desc or "gas line" in desc:
            return PermitTypeNormalization(raw_permit_type, "gas", "description:gas")
        return PermitTypeNormalization(raw_permit_type, "other", "empty_or_null->other")

    cleaned = str(raw_permit_type).strip()
    key = cleaned.lower()

    # 1) Dictionary
    if knowledge is not None:
        hit = knowledge.lookup_permit_code(cleaned, municipality)
        if hit is not None:
            trade = hit.canonical
            # Normalize dictionary trade/category into canonical permit type.
            if trade in CANONICAL_PERMIT_TYPES:
                return PermitTypeNormalization(cleaned, trade, hit.rule)
            # Map title-case categories like "Commercial"
            lower = trade.lower()
            if lower in CANONICAL_PERMIT_TYPES:
                return PermitTypeNormalization(cleaned, lower, hit.rule)
            mapped = {
                "commercial": "commercial",
                "residential": "residential",
                "plumbing": "plumbing",
                "electrical": "electrical",
                "mechanical": "mechanical",
            }.get(lower)
            if mapped:
                return PermitTypeNormalization(cleaned, mapped, hit.rule)

    if key in _PERMIT_TYPE_EXACT:
        canonical = _PERMIT_TYPE_EXACT[key]
        return PermitTypeNormalization(
            cleaned, canonical, f"bootstrap:exact:{key}->{canonical}"
        )

    if key in {"elec", "electrical"} or key.startswith("elec"):
        return PermitTypeNormalization(cleaned, "electrical", f"bootstrap:prefix:elec:{key}")
    if "plumb" in key or key in {"plmb", "plum"}:
        return PermitTypeNormalization(cleaned, "plumbing", f"bootstrap:contains:plumb:{key}")
    if "mech" in key:
        return PermitTypeNormalization(cleaned, "mechanical", f"bootstrap:contains:mech:{key}")
    if "commercial" in key or key == "com":
        return PermitTypeNormalization(cleaned, "commercial", f"bootstrap:contains:commercial:{key}")
    if "residential" in key or key == "res":
        return PermitTypeNormalization(cleaned, "residential", f"bootstrap:contains:residential:{key}")
    if "pool" in key:
        return PermitTypeNormalization(cleaned, "pool", f"bootstrap:contains:pool:{key}")
    if "gas" in key:
        return PermitTypeNormalization(cleaned, "gas", f"bootstrap:contains:gas:{key}")
    if "water heater" in key or "waterheater" in key:
        return PermitTypeNormalization(cleaned, "water heater", f"bootstrap:contains:water heater:{key}")
    if "build" in key or key in {"bld", "bldg"}:
        return PermitTypeNormalization(cleaned, "building", f"bootstrap:contains:build:{key}")

    desc = (description or "").lower()
    if "water heater" in desc:
        return PermitTypeNormalization(
            cleaned, "water heater", "type_unmapped+description:water heater"
        )
    if "pool" in desc and "spa" not in key:
        return PermitTypeNormalization(cleaned, "pool", "type_unmapped+description:pool")

    if knowledge is not None:
        knowledge.enqueue_unknown(
            kind="permit_code",
            municipality=municipality,
            raw_value=cleaned,
            description=description,
            suggested_interpretation="other",
            confidence=0.15,
        )
    return PermitTypeNormalization(cleaned, "other", f"unmapped:{key}->other")


@dataclass(frozen=True)
class NormalizationDiagnostics:
    raw_status: str | None
    normalized_status: str
    status_mapping_rule: str
    raw_permit_type: str | None
    normalized_permit_type: str
    permit_type_mapping_rule: str
    project_category: str
    category_mapping_rule: str
    scale_basis: str


def build_normalization_diagnostics(
    *,
    raw_status: str | None,
    raw_permit_type: str | None,
    description: str | None,
    municipality: str | None,
    project_category: str,
    category_mapping_rule: str,
    valuation: float | None,
    square_footage: float | None,
    knowledge: KnowledgeEngine | None = None,
) -> NormalizationDiagnostics:
    status = explain_status_normalization(raw_status, municipality, knowledge)
    ptype = explain_permit_type_normalization(
        raw_permit_type, description, municipality, knowledge
    )
    if valuation and valuation > 0:
        scale_basis = "valuation"
    elif square_footage and square_footage > 0:
        scale_basis = "square_footage"
    else:
        scale_basis = "neutral_missing_data"
    return NormalizationDiagnostics(
        raw_status=status.raw_status,
        normalized_status=status.normalized_status,
        status_mapping_rule=status.status_mapping_rule,
        raw_permit_type=ptype.raw_permit_type,
        normalized_permit_type=ptype.normalized_permit_type,
        permit_type_mapping_rule=ptype.permit_type_mapping_rule,
        project_category=project_category,
        category_mapping_rule=category_mapping_rule,
        scale_basis=scale_basis,
    )
