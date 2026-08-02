"""Company name / identity field normalization.

Deterministic, dependency-free helpers used by matching, aliasing, and backfill.
Generalizes the contractor-name normalization already used in
``pipeline/contractors/rebuild.py`` so companies dedup consistently.
"""

from __future__ import annotations

import re

# Trailing legal suffixes stripped to form the dedup key.
_SUFFIX_RE = re.compile(
    r"\b(LLC|L\.L\.C\.?|INC|INCORPORATED|CO|CORP|CORPORATION|LTD|LP|LLP|PLLC|PLC|PC)\.?\s*$",
    re.IGNORECASE,
)

# Tokens that suggest a value is a company/firm rather than a person/residence.
_COMPANY_HINTS = (
    "LLC", "L.L.C", "INC", "CORP", "CO ", " CO", "COMPANY", "LTD", "LP", "LLP",
    "PLLC", "PLC", "PC", "CONSTRUCTION", "CONTRACT", "BUILDER", "BUILDERS",
    "HOMES", "HOMEBUILDERS", "DEVELOPMENT", "DEVELOPERS", "PLUMBING",
    "ELECTRIC", "MECHANICAL", "ROOFING", "HVAC", "PROPERT", "HOLDINGS",
    "GROUP", "ENTERPRISES", "INDUSTRIES", "SERVICES", "SYSTEMS", "ENGINEERING",
    "ARCHITECT", "DESIGN", "CAPITAL", "PARTNERS", "REALTY", "INVESTMENTS",
)


def normalize_company_name(raw_name: str | None) -> str:
    """Return a stable dedup key for a company name (may be empty)."""
    if not raw_name:
        return ""
    name = str(raw_name).strip().upper()
    name = re.sub(r"[.,]", "", name)
    name = re.sub(r"[&]", " AND ", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Strip a trailing legal suffix (repeat once in case of "X INC LLC").
    prev = None
    while prev != name:
        prev = name
        name = _SUFFIX_RE.sub("", name).strip()
    return name


def looks_like_company(value: str | None) -> bool:
    """Heuristic: does this string look like a firm rather than a person?"""
    if not value:
        return False
    text = f" {str(value).strip().upper()} "
    return any(hint in text for hint in _COMPANY_HINTS)


def normalize_phone(value: str | None) -> str | None:
    """Digits-only US phone (last 10 digits), or None."""
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) < 10:
        return None
    return digits[-10:]


def email_domain(value: str | None) -> str | None:
    """Lowercased domain from an email, or None. Public domains are ignored."""
    if not value or "@" not in str(value):
        return None
    domain = str(value).rsplit("@", 1)[-1].strip().lower()
    if not domain or "." not in domain:
        return None
    if domain in _PUBLIC_EMAIL_DOMAINS:
        return None
    return domain


_PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
        "icloud.com", "live.com", "msn.com", "comcast.net", "cox.net",
        "me.com", "protonmail.com",
    }
)


def normalize_license(value: str | None) -> str | None:
    """Uppercased alphanumeric license, or None."""
    if not value:
        return None
    lic = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    return lic or None


def normalize_city(value: str | None) -> str | None:
    if not value:
        return None
    city = re.sub(r"\s+", " ", str(value).strip().upper())
    return city or None


# Generic placeholder values that are NOT real company names — never create a
# canonical company from these (owner-builder markers, blanks, "unknown", etc.).
_PLACEHOLDER_NAMES = frozenset(
    {
        "OWNER", "OWNER BUILDER", "OWNER/BUILDER", "OWNERBUILDER", "HOMEOWNER",
        "HOME OWNER", "SELF", "SAME", "SAME AS OWNER", "SAME AS ABOVE", "N/A",
        "NA", "NONE", "NO", "TBD", "TBA", "UNKNOWN", "UNK", "NOT AVAILABLE",
        "NOT APPLICABLE", "PROPERTY OWNER", "OWNER OCCUPIED", "OWN", "N A",
        "TO BE DETERMINED", "PENDING", "VARIOUS", "GENERAL CONTRACTOR",
        "CONTRACTOR", "APPLICANT", "TENANT", "-", "--", ".",
    }
)


def is_placeholder_name(value: str | None) -> bool:
    """True when a name is a generic placeholder rather than a real company."""
    normalized = normalize_company_name(value)
    if not normalized or len(normalized) < 2:
        return True
    return normalized in _PLACEHOLDER_NAMES
