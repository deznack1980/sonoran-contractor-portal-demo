"""Shared constants and lightweight data structures for company resolution."""

from __future__ import annotations

from dataclasses import dataclass, field

ROLE_TYPES = (
    "contractor",
    "fulfillment_partner",
    "supplier",
    "manufacturer",
    "developer",
    "property_owner",
    "architect",
    "engineer",
    "municipality",
    "other",
)

AUDIT_ACTIONS = (
    "created",
    "linked",
    "merged",
    "unlinked",
    "match_approved",
    "match_rejected",
    "alias_added",
    "profile_updated",
)

ACTIVITY_TYPES = (
    "permit_submitted",
    "permit_issued",
    "permit_finaled",
    "project_created",
    "project_stage_changed",
    "company_discovered",
    "company_profile_updated",
    "manual_note",
    "contact_attempt",
)

# Decision returned by the matcher.
DECISION_MATCHED = "matched"
DECISION_POSSIBLE = "possible_match"
DECISION_NEW = "new_company"


@dataclass
class CompanyInput:
    """Normalized-ish company facts extracted from a source record."""

    name: str
    role_type: str = "contractor"
    license_number: str | None = None
    license_state: str | None = None
    city: str | None = None
    state: str | None = None
    phone: str | None = None
    email: str | None = None
    address_line_1: str | None = None
    source_system: str | None = None
    source_record_id: str | None = None


@dataclass
class MatchResult:
    decision: str                       # matched | possible_match | new_company
    company_id: int | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    conflicting_fields: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.decision == DECISION_POSSIBLE
