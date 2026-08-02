"""All tunable weights, fractions, and margins for the rule-based analysis
engine, in one place for easy tuning and audit.

Every number here is a documented heuristic assumption, not a measured
value. Sanity-check against real plumbing-industry cost ratios before
relying on estimated_material_value / estimated_gross_profit for business
decisions — see the note in the Phase 1 plan.
"""

# ---------------------------------------------------------------
# opportunity_score weighting (mirrors the 40/25/20/15 split already
# documented in data/demo/schema.md for the original demo product)
# ---------------------------------------------------------------
WEIGHT_SIGNAL_STRENGTH = 0.40
WEIGHT_RECENCY = 0.25
WEIGHT_SCALE = 0.20
WEIGHT_SECTOR_FIT = 0.15

RECENCY_FULL_SCORE_DAYS = 30
RECENCY_ZERO_SCORE_DAYS = 120

# Scale scoring caps — values at/above these get a full scale score.
SCALE_VALUATION_CAP = 5_000_000
SCALE_SQFT_CAP = 100_000
SCALE_NEUTRAL_SCORE = 50  # used when both valuation and sqft are NULL

# Signal strength: permit_type x status -> 0-100 base score.
# Falls back to SIGNAL_STRENGTH_DEFAULT if no match.
SIGNAL_STRENGTH_DEFAULT = 40
SIGNAL_STRENGTH_BY_STATUS = {
    "issued": 85,
    "in review": 60,
    "applied": 55,
    "finaled": 45,  # already complete — lower forward-looking opportunity
    "expired": 15,
    "cancelled": 5,
    "withdrawn": 5,
}
SIGNAL_STRENGTH_COMMERCIAL_BONUS = 10  # COM permit_type adds to base

# Sector/project fit baseline by project_category (0-100).
# Existing weights for legacy categories are unchanged; new trade categories
# from permit-type normalization are additive only.
SECTOR_FIT_BY_CATEGORY = {
    "Medical": 90,
    "Restaurant": 88,
    "Manufacturing": 85,
    "Warehouse": 82,
    "Hotel": 80,
    "School": 78,
    "Apartment": 75,
    "Commercial": 70,
    "Industrial": 78,
    "Plumbing": 68,
    "Mechanical": 62,
    "Electrical": 55,
    "Building": 60,
    "Gas": 65,
    "Water Heater": 66,
    "Pool": 58,
    "Residential": 45,
    "Other": 35,
}

# ---------------------------------------------------------------
# confidence_score: data-completeness checklist
# ---------------------------------------------------------------
CONFIDENCE_CHECK_FIELDS = [
    "valuation", "square_footage", "description",
    "general_contractor_name", "issued_date",
]

# ---------------------------------------------------------------
# estimated_material_value: valuation x category_plumbing_fraction
# ---------------------------------------------------------------
PLUMBING_FRACTION_BY_CATEGORY = {
    "Medical": 0.08,
    "Restaurant": 0.09,
    "Hotel": 0.06,
    "Apartment": 0.05,
    "Manufacturing": 0.04,
    "Warehouse": 0.03,
    "School": 0.05,
    "Commercial": 0.04,
    "Industrial": 0.035,
    "Residential": 0.05,
    "Other": 0.03,
}
# Fallback rate applied to square_footage (dollars of plumbing value per
# sqft) when valuation is NULL but square_footage is present.
PLUMBING_VALUE_PER_SQFT_FALLBACK = 3.5

# estimated_gross_profit = estimated_material_value x margin, by category
GROSS_MARGIN_BY_CATEGORY = {
    "Medical": 0.26,
    "Restaurant": 0.24,
    "Hotel": 0.22,
    "Apartment": 0.22,
    "Manufacturing": 0.20,
    "Warehouse": 0.20,
    "School": 0.22,
    "Commercial": 0.22,
    "Industrial": 0.20,
    "Residential": 0.24,
    "Other": 0.20,
}
DEFAULT_GROSS_MARGIN = 0.22

# ---------------------------------------------------------------
# construction_stage inference from permit status
# ---------------------------------------------------------------
CONSTRUCTION_STAGE_BY_STATUS = {
    "applied": "Filed",
    "in review": "Filed",
    "issued": "Permitted",
    "finaled": "Final",
    "expired": "Closed",
    "cancelled": "Closed",
    "withdrawn": "Closed",
}
CONSTRUCTION_STAGE_DEFAULT = "Unknown"

# ---------------------------------------------------------------
# Project lifecycle (Sprint 3) — a richer, sales-facing lifecycle
# stage derived from the SAME normalized status. This is additive:
# it does not change scoring weights, thresholds, or normalization.
# ---------------------------------------------------------------
# Canonical lifecycle stages, ordered earliest -> latest.
LIFECYCLE_STAGES = (
    "Application Submitted",
    "Plan Review",
    "Permit Issued",
    "Construction Active",
    "Inspection",
    "Finaled",
    "Closed",
    "Unknown",
)

# Normalized status -> lifecycle stage.
LIFECYCLE_STAGE_BY_STATUS = {
    "applied": "Application Submitted",
    "in review": "Plan Review",
    "issued": "Permit Issued",
    "finaled": "Finaled",
    "expired": "Closed",
    "cancelled": "Closed",
    "withdrawn": "Closed",
}
LIFECYCLE_STAGE_DEFAULT = "Unknown"

# Raw-status keyword refinements for stages the canonical status cannot
# distinguish (e.g. "Under Construction", "Ready for Inspection"). Checked in
# order; first match wins and overrides the status-based mapping.
LIFECYCLE_KEYWORD_REFINEMENTS = (
    ("inspect", "Inspection"),
    ("under construction", "Construction Active"),
    ("construction", "Construction Active"),
)

# opportunity_timing depends SOLELY on lifecycle stage (independent of the
# opportunity_score). Earlier stage = earlier outreach = better timing.
OPPORTUNITY_TIMING_BY_LIFECYCLE = {
    "Application Submitted": "Excellent",
    "Plan Review": "Good",
    "Permit Issued": "Good",
    "Construction Active": "Moderate",
    "Inspection": "Late",
    "Finaled": "Historical",
    "Closed": "Historical",
    "Unknown": "Moderate",
}
OPPORTUNITY_TIMING_DEFAULT = "Moderate"

# opportunity_date selection order. Each entry is (basis_label, permit column).
# The first non-empty date wins — always the EARLIEST usable outreach date.
# Note: our sources do not expose separate "created" / "plan review" date
# columns; the application/submitted date (filed_date) is the earliest signal
# available, then issued, then finaled. Source dates are never overwritten.
OPPORTUNITY_DATE_PRIORITY = (
    ("Application Submitted", "filed_date"),
    ("Permit Issued", "issued_date"),
    ("Final", "finaled_date"),
)

# Recommended sales action per lifecycle stage (Phase 4).
LIFECYCLE_SALES_ACTION = {
    "Application Submitted": "Initial introduction — get on the radar before the material buyout.",
    "Plan Review": "Share capabilities and indicative pricing; position for the buyout.",
    "Permit Issued": "Prepare a material quote immediately.",
    "Construction Active": "Follow up for change orders and additional material needs.",
    "Inspection": "Confirm final material pulls and punch-list items.",
    "Finaled": "Archive — retain for contractor relationship history.",
    "Closed": "Archive.",
    "Unknown": "Verify current status in the city portal before outreach.",
}
LIFECYCLE_SALES_ACTION_DEFAULT = "Review permit details before outreach."

# Dashboard filter label -> lifecycle stage (Phase 5).
LIFECYCLE_FILTER_TO_STAGE = {
    "Submitted": "Application Submitted",
    "In Review": "Plan Review",
    "Issued": "Permit Issued",
    "Construction": "Construction Active",
    "Inspection": "Inspection",
    "Final": "Finaled",
    "Closed": "Closed",
}
