"""Unit tests for status / permit-type normalization and scoring hooks."""

from __future__ import annotations

from pipeline.analysis.category_rules import classify_category
from pipeline.analysis.constants import (
    SCALE_NEUTRAL_SCORE,
    SIGNAL_STRENGTH_BY_STATUS,
    SIGNAL_STRENGTH_COMMERCIAL_BONUS,
    SIGNAL_STRENGTH_DEFAULT,
)
from pipeline.analysis.normalization import (
    normalize_permit_status,
    normalize_permit_type,
    explain_status_normalization,
)
from pipeline.analysis.scoring import (
    compute_opportunity_score,
    score_scale,
    score_signal_strength,
    scale_basis,
)
from pipeline.analysis.run_analysis import analyze_permit


def test_permit_issued_to_issued():
    assert normalize_permit_status("Permit Issued") == "issued"
    assert normalize_permit_status("ISSUED") == "issued"
    assert normalize_permit_status("Issued") == "issued"


def test_commercial_and_residential_types():
    assert normalize_permit_type("Commercial") == "commercial"
    assert normalize_permit_type("COM") == "commercial"
    assert normalize_permit_type("Residential") == "residential"
    assert normalize_permit_type("RES") == "residential"


def test_peoria_permit_issued_residential_not_default_signal():
    signal = score_signal_strength(
        "Residential", "Permit Issued", municipality="peoria_az"
    )
    assert signal == SIGNAL_STRENGTH_BY_STATUS["issued"]
    assert signal != SIGNAL_STRENGTH_DEFAULT
    assert classify_category("Residential", "Attached pergola", municipality="peoria_az") == (
        "Residential"
    )


def test_peoria_permit_issued_commercial_not_other():
    assert classify_category("Commercial", "Model Home Complex", municipality="peoria_az") == (
        "Commercial"
    )
    signal = score_signal_strength(
        "Commercial", "Permit Issued", municipality="peoria_az"
    )
    assert signal == SIGNAL_STRENGTH_BY_STATUS["issued"] + SIGNAL_STRENGTH_COMMERCIAL_BONUS


def test_phoenix_open_maps_to_issued():
    info = explain_status_normalization("OPEN", "phoenix_az")
    assert info.normalized_status == "issued"
    assert "phoenix_az:open->issued" in info.status_mapping_rule
    # Global OPEN must NOT become issued.
    global_open = explain_status_normalization("OPEN", None)
    assert global_open.normalized_status == "unknown"
    assert "unmapped" in global_open.status_mapping_rule or "open_unmapped" in global_open.status_mapping_rule


def test_unknown_status_fallback_visible():
    info = explain_status_normalization("SOME_WEIRD_STATUS", "mesa_az")
    assert info.normalized_status == "unknown"
    assert "unmapped" in info.status_mapping_rule


def test_unknown_permit_type_fallback():
    assert normalize_permit_type("ZZZ_CODE_99") == "other"


def test_missing_valuation_neutral_scale():
    assert score_scale(None, None) == SCALE_NEUTRAL_SCORE
    assert scale_basis(None, None) == "neutral_missing_data"


def test_commercial_bonus_after_normalization():
    with_bonus = score_signal_strength("Commercial", "issued", municipality="peoria_az")
    without = score_signal_strength("Residential", "issued", municipality="peoria_az")
    assert with_bonus - without == SIGNAL_STRENGTH_COMMERCIAL_BONUS


def test_analyze_permit_does_not_mutate_raw_fields():
    row = {
        "status": "Permit Issued",
        "permit_type": "Residential",
        "description": "Gas line repair",
        "issued_date": "2026-07-21",
        "jurisdiction": "peoria_az",
        "valuation": None,
        "square_footage": None,
    }
    before_status = row["status"]
    before_type = row["permit_type"]
    result = analyze_permit(row)
    assert row["status"] == before_status
    assert row["permit_type"] == before_type
    assert result["normalization"]["raw_status"] == "Permit Issued"
    assert result["normalization"]["normalized_status"] == "issued"
    assert result["normalization"]["raw_permit_type"] == "Residential"
    assert result["normalization"]["normalized_permit_type"] == "residential"
    assert result["project_category"] == "Residential"
    assert result["opportunity_score"] > 56.2
    assert result["normalization"]["scale_basis"] == "neutral_missing_data"


def test_final_and_cancelled_aliases():
    assert normalize_permit_status("Final") == "finaled"
    assert normalize_permit_status("Finaled") == "finaled"
    assert normalize_permit_status("Completed") == "finaled"
    assert normalize_permit_status("Cancelled") == "cancelled"
    assert normalize_permit_status("Canceled") == "cancelled"
    assert normalize_permit_status("Under Review") == "in review"
    assert normalize_permit_status("Application Submitted") == "applied"


def test_recent_peoria_commercial_clears_threshold():
    score = compute_opportunity_score(
        {
            "permit_type": "Commercial",
            "status": "Permit Issued",
            "issued_date": "2026-07-21",
            "jurisdiction": "peoria_az",
            "valuation": None,
            "description": "Model Home Complex",
        },
        "Commercial",
    )
    assert score >= 60
