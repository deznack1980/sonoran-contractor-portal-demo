"""Deterministic, confidence-based company identity resolution.

resolve() returns a MatchResult with one of three decisions:
    matched | possible_match | new_company
Low-confidence matches are never auto-merged.
"""

from __future__ import annotations

import sqlite3

from pipeline.company_resolution.models import (
    DECISION_MATCHED,
    DECISION_NEW,
    DECISION_POSSIBLE,
    CompanyInput,
    MatchResult,
)
from pipeline.company_resolution.normalize import (
    email_domain,
    normalize_city,
    normalize_company_name,
    normalize_license,
    normalize_phone,
)
from pipeline.config.settings import (
    COMPANY_MATCH_AUTO_MIN,
    COMPANY_MATCH_CONFLICT_FIELDS,
    COMPANY_MATCH_PROBABLE_MIN,
    COMPANY_MATCH_REVIEW_MIN,
    COMPANY_MATCH_SIGNALS,
)


def _candidate_ids(conn: sqlite3.Connection, data: CompanyInput,
                   normalized: str) -> set[int]:
    ids: set[int] = set()
    lic = normalize_license(data.license_number)
    if lic:
        for r in conn.execute(
            "SELECT id FROM companies WHERE license_number=? AND lifecycle_state='active'",
            (lic,),
        ):
            ids.add(r["id"])
    if normalized:
        for r in conn.execute(
            "SELECT id FROM companies WHERE normalized_name=? AND lifecycle_state='active'",
            (normalized,),
        ):
            ids.add(r["id"])
        for r in conn.execute(
            """
            SELECT c.id FROM company_aliases a
            JOIN companies c ON c.id = a.company_id
            WHERE a.normalized_alias=? AND c.lifecycle_state='active'
            """,
            (normalized,),
        ):
            ids.add(r["id"])
    phone = normalize_phone(data.phone)
    if phone:
        for r in conn.execute(
            "SELECT id FROM companies WHERE main_phone=? AND lifecycle_state='active'",
            (phone,),
        ):
            ids.add(r["id"])
    if data.source_system and data.source_record_id:
        for r in conn.execute(
            "SELECT id FROM companies WHERE source_system=? AND source_record_id=? "
            "AND lifecycle_state='active'",
            (data.source_system, data.source_record_id),
        ):
            ids.add(r["id"])
    return ids


def _score_candidate(conn: sqlite3.Connection, data: CompanyInput,
                     normalized: str, cand) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    scores: list[float] = []

    src_lic = normalize_license(data.license_number)
    cand_lic = normalize_license(cand["license_number"])
    src_phone = normalize_phone(data.phone)
    cand_phone = normalize_phone(cand["main_phone"])
    src_city = normalize_city(data.city)
    cand_city = normalize_city(cand["city"])

    name_match = normalized and normalized == cand["normalized_name"]
    alias_match = False
    if normalized and not name_match:
        alias_match = conn.execute(
            "SELECT 1 FROM company_aliases WHERE company_id=? AND normalized_alias=?",
            (cand["id"], normalized),
        ).fetchone() is not None

    lic_match = bool(src_lic and cand_lic and src_lic == cand_lic)

    # A unique source id is authoritative on its own.
    if (data.source_system and data.source_record_id
            and cand["source_system"] == data.source_system
            and cand["source_record_id"] == data.source_record_id):
        scores.append(COMPANY_MATCH_SIGNALS["source_id_exact"])
        reasons.append("source_id_exact")

    # License is only a CORROBORATING signal, never a standalone auto-match:
    # contractor license numbers in the source data include shared/placeholder
    # values (e.g. "HTE0001") that would otherwise collapse unrelated companies.
    # It reinforces a name/alias match but cannot merge two different names.
    if name_match:
        base = COMPANY_MATCH_SIGNALS["name_exact"]
        reasons.append("name_exact")
        if src_phone and cand_phone and src_phone == cand_phone:
            base = max(base, COMPANY_MATCH_SIGNALS["name_plus_phone"])
            reasons.append("name_plus_phone")
        if src_city and cand_city and src_city == cand_city:
            base = max(base, COMPANY_MATCH_SIGNALS["name_plus_city"])
            reasons.append("name_plus_city")
        if lic_match:
            base = max(base, COMPANY_MATCH_SIGNALS["license_exact"])
            reasons.append("name_plus_license")
        scores.append(base)
    elif alias_match:
        base = COMPANY_MATCH_SIGNALS["alias_exact"]
        reasons.append("alias_exact")
        if src_city and cand_city and src_city == cand_city:
            base = max(base, COMPANY_MATCH_SIGNALS["name_plus_city"])
        if lic_match:
            base = max(base, COMPANY_MATCH_SIGNALS["license_exact"])
            reasons.append("alias_plus_license")
        scores.append(base)
    if not name_match and src_phone and cand_phone and src_phone == cand_phone:
        scores.append(COMPANY_MATCH_SIGNALS["phone_exact"])
        reasons.append("phone_exact")
    sd = email_domain(data.email)
    cd = email_domain(cand["main_email"])
    if sd and cd and sd == cd:
        scores.append(COMPANY_MATCH_SIGNALS["email_domain"])
        reasons.append("email_domain")

    confidence = max(scores) if scores else 0.0

    # Conflicts demote otherwise-probable matches into review.
    conflicts: list[str] = []
    field_pairs = {
        "license_number": (src_lic, cand_lic),
        "city": (src_city, cand_city),
        "state": ((data.state or "").upper() or None, (cand["state"] or "").upper() or None),
        "main_phone": (src_phone, cand_phone),
    }
    for field in COMPANY_MATCH_CONFLICT_FIELDS:
        s, c = field_pairs.get(field, (None, None))
        if s and c and s != c:
            conflicts.append(field)
    return confidence, reasons, conflicts


def resolve(conn: sqlite3.Connection, data: CompanyInput) -> MatchResult:
    """Resolve a source company record against existing canonical companies."""
    normalized = normalize_company_name(data.name)
    if not normalized:
        return MatchResult(decision=DECISION_NEW, confidence=0.0,
                           reasons=["empty_name"])

    candidate_ids = _candidate_ids(conn, data, normalized)
    best: MatchResult | None = None
    for cid in candidate_ids:
        cand = conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
        if cand is None:
            continue
        conf, reasons, conflicts = _score_candidate(conn, data, normalized, cand)
        if conf <= 0:
            continue
        if best is None or conf > best.confidence:
            best = MatchResult(
                decision=DECISION_MATCHED,
                company_id=cid,
                confidence=conf,
                reasons=reasons,
                conflicting_fields=conflicts,
            )

    if best is None:
        return MatchResult(decision=DECISION_NEW, confidence=0.0,
                           reasons=["no_candidate"])

    # Map confidence + conflicts to a decision.
    if best.confidence >= COMPANY_MATCH_AUTO_MIN:
        best.decision = DECISION_MATCHED
    elif best.confidence >= COMPANY_MATCH_PROBABLE_MIN:
        best.decision = DECISION_POSSIBLE if best.conflicting_fields else DECISION_MATCHED
    elif best.confidence >= COMPANY_MATCH_REVIEW_MIN:
        best.decision = DECISION_POSSIBLE
    else:
        best.decision = DECISION_NEW
    return best
