"""Sprint 3 tests — project lifecycle, opportunity_date, opportunity_timing.

These verify the additive lifecycle layer without touching scoring weights.
"""

from __future__ import annotations

from pipeline.analysis.scoring import (
    compute_opportunity_date,
    compute_opportunity_score,
    compute_opportunity_timing,
    compute_project_lifecycle,
    days_since,
)


def test_lifecycle_from_status():
    assert compute_project_lifecycle("Permit Issued") == "Permit Issued"
    assert compute_project_lifecycle("Applied") == "Application Submitted"
    assert compute_project_lifecycle("Under Review") == "Plan Review"
    assert compute_project_lifecycle("Completed") == "Finaled"
    assert compute_project_lifecycle("Cancelled") == "Closed"
    assert compute_project_lifecycle("Expired") == "Closed"


def test_lifecycle_keyword_refinements():
    # Chandler hard-codes "Under Construction"; keyword refinement wins.
    assert compute_project_lifecycle("Under Construction") == "Construction Active"
    assert compute_project_lifecycle("Ready for Inspection") == "Inspection"


def test_lifecycle_unknown():
    assert compute_project_lifecycle("ZZZ-nonsense") == "Unknown"
    assert compute_project_lifecycle(None) == "Unknown"


def test_opportunity_date_prefers_filed():
    date, basis = compute_opportunity_date(
        {"filed_date": "2026-01-10", "issued_date": "2026-03-01"}
    )
    assert date == "2026-01-10"
    assert basis == "Application Submitted"


def test_opportunity_date_falls_back_to_issued():
    date, basis = compute_opportunity_date(
        {"filed_date": None, "issued_date": "2026-03-01"}
    )
    assert date == "2026-03-01"
    assert basis == "Permit Issued"


def test_opportunity_date_falls_back_to_final_then_unknown():
    d, basis = compute_opportunity_date({"finaled_date": "2026-05-01"})
    assert d == "2026-05-01"
    assert basis == "Final"
    d2, basis2 = compute_opportunity_date({})
    assert d2 is None
    assert basis2 == "Unknown"


def test_opportunity_timing_by_stage():
    assert compute_opportunity_timing("Application Submitted") == "Excellent"
    assert compute_opportunity_timing("Plan Review") == "Good"
    assert compute_opportunity_timing("Permit Issued") == "Good"
    assert compute_opportunity_timing("Construction Active") == "Moderate"
    assert compute_opportunity_timing("Inspection") == "Late"
    assert compute_opportunity_timing("Finaled") == "Historical"
    assert compute_opportunity_timing("Closed") == "Historical"


def test_days_since():
    assert days_since(None) is None
    assert days_since("not-a-date") is None
    assert days_since("2020-01-01") > 1000


def test_scoring_unchanged_by_lifecycle():
    """Opportunity score must NOT depend on lifecycle/opportunity_date fields."""
    row = {
        "permit_type": "Commercial",
        "status": "Issued",
        "issued_date": "2026-07-20",
        "filed_date": "2026-05-01",
        "valuation": 500000,
        "square_footage": None,
        "description": "Tenant improvement",
    }
    baseline = compute_opportunity_score(row, "Commercial")
    # Adding lifecycle-only fields to the row does not change the score.
    row2 = dict(row, project_lifecycle="Permit Issued", opportunity_timing="Good",
                opportunity_date="2026-05-01")
    assert compute_opportunity_score(row2, "Commercial") == baseline
