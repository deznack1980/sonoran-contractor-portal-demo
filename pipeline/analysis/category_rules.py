"""Ordered classification: dictionary keywords -> normalized type -> Other.

Uses ``normalize_permit_type`` so full-word values classify correctly.
Raw ``permit_type`` is never overwritten. Unknown keywords can be queued
via KnowledgeEngine when attached.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pipeline.analysis.normalization import (
    category_from_normalized_permit_type,
    explain_permit_type_normalization,
)

if TYPE_CHECKING:
    from pipeline.knowledge.engine import KnowledgeEngine

CATEGORY_KEYWORDS = [
    ("Medical", ["hospital", "clinic", "medical office", "dental", "urgent care", "surgery center"]),
    ("Restaurant", ["restaurant", "cafe", "coffee shop", "commercial kitchen", "food service", "bar & grill", "brewery"]),
    ("Hotel", ["hotel", "motel", "resort", "hospitality"]),
    ("School", ["school", "classroom", "charter school", "elementary", "university", "campus"]),
    ("Warehouse", ["warehouse", "distribution center", "fulfillment", "logistics facility"]),
    ("Manufacturing", ["manufacturing", "factory", "industrial plant", "processing plant"]),
    ("Apartment", ["apartment", "multifamily", "multi-family", "condo", "townhome complex"]),
]


def classify_category(
    permit_type: str | None,
    description: str | None,
    new_residential: str | None = None,
    municipality: str | None = None,
    knowledge: KnowledgeEngine | None = None,
) -> str:
    return classify_category_with_rule(
        permit_type, description, new_residential, municipality, knowledge
    )[0]


def classify_category_with_rule(
    permit_type: str | None,
    description: str | None,
    new_residential: str | None = None,
    municipality: str | None = None,
    knowledge: KnowledgeEngine | None = None,
) -> tuple[str, str]:
    """Return (project_category, category_mapping_rule)."""
    text = f"{permit_type or ''} {description or ''}".lower()

    # 1) Knowledge keyword dictionary (approved mappings)
    if knowledge is not None:
        hits = knowledge.match_keywords(text)
        for hit in hits:
            if hit.get("category"):
                return (
                    hit["category"],
                    f"dictionary:keyword:{hit['keyword']}->{hit['category']}",
                )

    # 2) Bootstrap category keywords
    for category, keywords in CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in text:
                return category, f"bootstrap:keyword:{kw}->{category}"

    # 3) Normalized permit type
    type_info = explain_permit_type_normalization(
        permit_type, description, municipality, knowledge
    )
    from_type = category_from_normalized_permit_type(type_info.normalized_permit_type)
    if from_type:
        return (
            from_type,
            f"normalized_permit_type:{type_info.normalized_permit_type}->{from_type}",
        )

    # 4) New-residential indicator
    if (new_residential or "").strip().upper() == "Y":
        return "Residential", "new_residential:Y->Residential"

    # 5) Other fallback — do not invent a category. Unknown permit codes are
    # already queued by permit-type normalization; keyword suggestions are
    # added via the review dashboard / seed process, not auto-mined here.
    return "Other", f"fallback:other({type_info.permit_type_mapping_rule})"
