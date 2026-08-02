"""Category + keyword -> estimated materials list, each with a confidence %.

Category gives a base candidate list; keyword overlays bump specific
materials regardless of category. All percentages are documented heuristic
assumptions (see constants.py header), each estimate carries a `rationale`
string naming which rule produced it — the transparency requirement.
"""

from dataclasses import dataclass

MATERIAL_NAMES = [
    "PEX", "Copper", "PVC", "ABS", "Water Heaters", "Valves", "Ball Valves",
    "Backflow", "Fixtures", "Commercial Fixtures", "Gas Pipe", "Cleanouts",
    "Pressure Regulators", "Drainage",
]


@dataclass
class MaterialEstimate:
    material_name: str
    confidence_pct: float
    rationale: str


# category -> [(material, base_confidence_pct), ...]
CATEGORY_BASE_MATERIALS = {
    "Residential": [("PEX", 90), ("Fixtures", 90), ("Water Heaters", 70), ("Valves", 75), ("Drainage", 60)],
    "Apartment": [("PEX", 85), ("Fixtures", 88), ("Water Heaters", 75), ("Valves", 78), ("Cleanouts", 55)],
    "Commercial": [("Commercial Fixtures", 85), ("Copper", 55), ("PVC", 60), ("Backflow", 65), ("Valves", 70)],
    "Restaurant": [("Commercial Fixtures", 92), ("Backflow", 85), ("Gas Pipe", 80), ("Drainage", 75), ("Cleanouts", 60)],
    "Medical": [("Commercial Fixtures", 90), ("Copper", 70), ("Backflow", 80), ("Pressure Regulators", 60)],
    "Hotel": [("Fixtures", 85), ("Water Heaters", 80), ("Commercial Fixtures", 75), ("Valves", 70)],
    "School": [("Commercial Fixtures", 80), ("Backflow", 75), ("Drainage", 60), ("Valves", 65)],
    "Warehouse": [("Backflow", 55), ("PVC", 50), ("Drainage", 55), ("Cleanouts", 45)],
    "Manufacturing": [("Copper", 55), ("PVC", 55), ("Pressure Regulators", 60), ("Backflow", 60)],
    "Industrial": [("PVC", 55), ("Backflow", 55), ("Pressure Regulators", 55), ("Drainage", 50)],
    "Other": [("Fixtures", 40), ("PVC", 35)],
}

# keyword (in lowercased permit_type/description) -> (material, confidence_pct)
# overlays regardless of category; applied on top of base materials.
KEYWORD_MATERIAL_OVERLAYS = [
    ("water heater", "Water Heaters", 95),
    ("waterheater", "Water Heaters", 95),
    ("wh replacement", "Water Heaters", 90),
    ("backflow", "Backflow", 95),
    ("sewer", "Drainage", 90),
    ("sewer", "Cleanouts", 85),
    ("drain", "Drainage", 75),
    ("gas line", "Gas Pipe", 92),
    ("gas pipe", "Gas Pipe", 92),
    ("gas ", "Gas Pipe", 80),
    ("pool", "PVC", 85),
    ("re-pipe", "PEX", 88),
    ("repipe", "PEX", 88),
    ("copper", "Copper", 90),
    ("pex", "PEX", 90),
    ("pvc", "PVC", 85),
    ("abs pipe", "ABS", 85),
    ("ball valve", "Ball Valves", 90),
    ("ball valves", "Ball Valves", 90),
    ("valve", "Valves", 70),
    ("cleanout", "Cleanouts", 88),
    ("pressure regulator", "Pressure Regulators", 88),
    ("irrigation", "PVC", 70),
    ("commercial fixture", "Commercial Fixtures", 90),
    ("restroom", "Commercial Fixtures", 75),
    ("kitchen", "Commercial Fixtures", 70),
    ("fixture", "Fixtures", 65),
]


def estimate_materials(project_category: str, permit_type: str | None, description: str | None) -> list[MaterialEstimate]:
    text = f"{permit_type or ''} {description or ''}".lower()
    estimates: dict[str, MaterialEstimate] = {}

    for material, confidence in CATEGORY_BASE_MATERIALS.get(project_category, CATEGORY_BASE_MATERIALS["Other"]):
        estimates[material] = MaterialEstimate(
            material_name=material,
            confidence_pct=confidence,
            rationale=f"Category baseline for '{project_category}'",
        )

    for keyword, material, confidence in KEYWORD_MATERIAL_OVERLAYS:
        if keyword in text:
            existing = estimates.get(material)
            if existing is None or confidence > existing.confidence_pct:
                estimates[material] = MaterialEstimate(
                    material_name=material,
                    confidence_pct=confidence,
                    rationale=f"Keyword match: '{keyword.strip()}' in permit type/description",
                )

    return sorted(estimates.values(), key=lambda e: e.confidence_pct, reverse=True)


def predict_product_categories(
    project_category: str,
    permit_type: str | None,
    description: str | None,
    *,
    min_confidence: float = 55.0,
    limit: int = 8,
) -> list[str]:
    """Best-estimate plumbing product categories from permit wording.

    Uses the same rule engine as ``estimate_materials`` — not perfect, but
    grounded in category baselines plus description keyword overlays.
    """
    estimates = estimate_materials(project_category, permit_type, description)
    names: list[str] = []
    for est in estimates:
        if est.confidence_pct < min_confidence:
            continue
        names.append(est.material_name)
        if len(names) >= limit:
            break
    return names
