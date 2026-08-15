"""Reversible correction of permit-party roles and contractor assignments.

Only explicit municipal contractor/builder fields may drive permit/project
contractor links. Ambiguous parties remain available in permit_party_evidence
and CRM lead classification; no source, company, contact, activity, or CRM row
is deleted.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone

from pipeline.company_resolution.normalize import is_placeholder_name, normalize_company_name
from pipeline.company_resolution.match import resolve
from pipeline.company_resolution.merge import create_company, enrich_company, ensure_role
from pipeline.company_resolution.models import DECISION_NEW, DECISION_POSSIBLE, CompanyInput

MIGRATION_KEY = "lead_role_explicit_evidence_v1"
CLASSIFICATION_VERSION = "lead-role-v1"
LEAD_TYPES = (
    "verified_contractor",
    "specifier_architect_engineer",
    "owner_developer",
    "unverified_permit_contact",
)

_SPECIFIER = re.compile(r"\b(ARCHITECT|ARCHITECTURE|ARCHITECTURAL|ENGINEER|ENGINEERING|DESIGN|DESIGNER|DRAFTING|STUDIO|AIA)\b", re.I)
_OWNER = re.compile(r"\b(OWNER|DEVELOPER|DEVELOPMENT|PROPERTIES|PROPERTY|HOLDINGS|REALTY|HOMEOWNER)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value) -> str | None:
    value = " ".join(str(value or "").split()).strip()
    return value or None


def _raw(row) -> dict:
    try:
        return json.loads(row["raw_source_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _party(name, role, field, lead_type, verified, confidence, why):
    value = _clean(name)
    if not value:
        return None
    return {
        "name": value,
        "role": role,
        "field": field,
        "lead_type": lead_type,
        "verification": "verified" if verified else "unverified",
        "confidence": confidence,
        "is_contractor": 1 if verified else 0,
        "why": why,
    }


def extract_permit_evidence(jurisdiction: str, raw: dict) -> tuple[dict | None, list[dict], dict]:
    """Return (explicit contractor, all party evidence, separate permit fields)."""
    items: list[dict] = []
    separate = {
        "applicant_name": None,
        "applicant_organization": None,
        "responsible_party_name": None,
        "permit_professional_name": None,
    }
    explicit = None

    if jurisdiction == "mesa_az":
        explicit = _party(raw.get("contractor_name"), "contractor", "contractor_name",
                          "verified_contractor", True, 1.0,
                          "Mesa contractor_name is explicit contractor evidence")
        separate["applicant_name"] = _clean(raw.get("applicant"))
        items.append(_party(raw.get("applicant"), "applicant", "applicant",
                            "unverified_permit_contact", False, .35,
                            "Mesa applicant is contact evidence only"))
    elif jurisdiction == "peoria_az":
        separate["applicant_organization"] = _clean(raw.get("Applicant_Contact_Organization"))
        separate["applicant_name"] = _clean(raw.get("Applicant_Contact_Name"))
        items.extend([
            _party(raw.get("Applicant_Contact_Organization"), "applicant", "Applicant_Contact_Organization",
                   "unverified_permit_contact", False, .45,
                   "Peoria applicant organization is not contractor evidence"),
            _party(raw.get("Applicant_Contact_Name"), "applicant", "Applicant_Contact_Name",
                   "unverified_permit_contact", False, .30,
                   "Peoria applicant name is not contractor evidence"),
        ])
    elif jurisdiction == "phoenix_az":
        separate["permit_professional_name"] = _clean(raw.get("PROFESS_NAME"))
        items.extend([
            _party(raw.get("PROFESS_NAME"), "permit_professional", "PROFESS_NAME",
                   "unverified_permit_contact", False, .40,
                   "Phoenix PROFESS_NAME is an unverified permit professional"),
            _party(raw.get("PERMIT_NAME"), "project_name", "PERMIT_NAME",
                   "unverified_permit_contact", False, .15,
                   "Phoenix project name is not contractor evidence"),
        ])
    elif jurisdiction == "scottsdale_az":
        explicit = _party(raw.get("Builder"), "builder", "Builder",
                          "verified_contractor", True, 1.0,
                          "Scottsdale Builder is explicit contractor evidence")
        separate["responsible_party_name"] = _clean(raw.get("ResponsibleParty"))
        items.extend([
            _party(raw.get("ResponsibleParty"), "responsible_party", "ResponsibleParty",
                   "unverified_permit_contact", False, .40,
                   "Scottsdale ResponsibleParty is not contractor evidence"),
            _party(raw.get("Owner"), "owner", "Owner", "owner_developer", False, .75,
                   "Scottsdale publishes this party as Owner"),
        ])
    elif jurisdiction == "tempe_az":
        explicit = _party(raw.get("ContractorCompanyName"), "contractor", "ContractorCompanyName",
                          "verified_contractor", True, 1.0,
                          "Tempe ContractorCompanyName is explicit contractor evidence")
        items.append(_party(raw.get("ProjectName"), "project_name", "ProjectName",
                            "unverified_permit_contact", False, .15,
                            "Tempe project name is not contractor evidence"))
    elif jurisdiction == "goodyear_az":
        explicit = _party(raw.get("GenConName"), "general_contractor", "GenConName",
                          "verified_contractor", True, 1.0,
                          "Goodyear GenConName is explicit contractor evidence")
        items.append(_party(raw.get("OwnerName"), "owner", "OwnerName", "owner_developer",
                            False, .75, "Goodyear publishes this party as OwnerName"))

    if explicit and is_placeholder_name(explicit["name"]):
        explicit.update({
            "lead_type":"unverified_permit_contact", "verification":"unverified",
            "confidence":.05, "is_contractor":0,
            "why":f"{explicit['field']} contains a placeholder, not a contractor identity",
        })
        items.insert(0, explicit)
        explicit = None
    elif explicit:
        items.insert(0, explicit)
    return explicit, [item for item in items if item], separate


def _company_for_name(conn: sqlite3.Connection, name: str | None) -> int | None:
    normalized = normalize_company_name(name)
    if not normalized:
        return None
    rows = conn.execute(
        "SELECT id FROM companies WHERE normalized_name=? AND lifecycle_state='active' ORDER BY id LIMIT 2",
        (normalized,),
    ).fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


def _source_company_id(conn, item, prior_name, prior_company_id):
    if prior_company_id and _clean(prior_name) and normalize_company_name(item["name"]) == normalize_company_name(prior_name):
        return prior_company_id
    return _company_for_name(conn, item["name"])


def _classify_companies(conn: sqlite3.Connection, now: str) -> Counter:
    counts = Counter()
    companies = conn.execute(
        "SELECT id, display_name, legal_name FROM companies WHERE lifecycle_state='active'"
    ).fetchall()
    for company in companies:
        cid = company["id"]
        evidence = conn.execute(
            "SELECT source_role,source_field,lead_type,verification_status,is_contractor_evidence "
            "FROM permit_party_evidence WHERE company_id=?", (cid,)
        ).fetchall()
        explicit = sum(1 for e in evidence if e["is_contractor_evidence"])
        ambiguous = sum(1 for e in evidence if not e["is_contractor_evidence"])
        roles = {r["role_type"] for r in conn.execute(
            "SELECT role_type FROM company_roles WHERE company_id=?", (cid,))}
        name = _clean(company["display_name"] or company["legal_name"]) or "Company"
        fields = sorted({e["source_field"] for e in evidence})
        if explicit:
            lead_type, verification, confidence = "verified_contractor", "verified", 1.0
            why = f"{explicit} permit(s) contain an explicit contractor or builder field"
        elif roles & {"architect", "engineer"} or _SPECIFIER.search(name):
            lead_type, verification, confidence = "specifier_architect_engineer", "role_inferred", .85
            why = "Published role/name indicates architect, engineer, or design/specification influence"
        elif roles & {"property_owner", "developer"} or _OWNER.search(name) or any(e["lead_type"] == "owner_developer" for e in evidence):
            lead_type, verification, confidence = "owner_developer", "role_inferred", .80
            why = "Published owner/developer evidence or company role indicates project ownership/development"
        else:
            lead_type, verification, confidence = "unverified_permit_contact", "unverified", .35
            why = "Permit party is retained for research but no explicit contractor evidence exists"
        source = "permit_party_evidence" if evidence else "existing_crm_or_company_record"
        source_field = ", ".join(fields[:8]) if fields else None
        conn.execute(
            """INSERT INTO company_lead_classification
               (company_id,lead_type,verification_status,source,source_field,confidence,
                why_this_lead,explicit_contractor_permits,ambiguous_permit_contacts,
                classification_version,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(company_id) DO UPDATE SET
                 lead_type=excluded.lead_type, verification_status=excluded.verification_status,
                 source=excluded.source, source_field=excluded.source_field,
                 confidence=excluded.confidence, why_this_lead=excluded.why_this_lead,
                 explicit_contractor_permits=excluded.explicit_contractor_permits,
                 ambiguous_permit_contacts=excluded.ambiguous_permit_contacts,
                 classification_version=excluded.classification_version,
                 updated_at=excluded.updated_at""",
            (cid, lead_type, verification, source, source_field, confidence, why,
             explicit, ambiguous, CLASSIFICATION_VERSION, now),
        )
        conn.execute(
            "UPDATE companies SET lead_type=?,lead_verification_status=?,lead_source=?,why_this_lead=?,updated_at=? WHERE id=?",
            (lead_type, verification, source_field or source, why, now, cid),
        )
        conn.execute(
            "UPDATE crm_company_relationships SET lead_type=?,lead_verification_status=?,"
            "lead_classification_source=?,why_this_lead=?,updated_at=? WHERE company_id=?",
            (lead_type, verification, source_field or source, why, now, cid),
        )
        conn.execute(
            "UPDATE company_roles SET verification_status=?,evidence_source=?,evidence_field=?,updated_at=? "
            "WHERE company_id=? AND role_type='contractor'",
            ("verified" if explicit else "unverified", source, source_field, now, cid),
        )
        if not explicit:
            conn.execute(
                "UPDATE company_roles SET is_primary=0,updated_at=? WHERE company_id=? AND role_type='contractor'",
                (now,cid),
            )
        counts[lead_type] += 1
    return counts


def _deprecate_placeholder_companies(conn: sqlite3.Connection, now: str) -> int:
    """Keep placeholder records for auditability but remove them from active leads."""
    changed = 0
    rows = conn.execute(
        "SELECT id,display_name,legal_name,source_system FROM companies WHERE lifecycle_state='active'"
    ).fetchall()
    for row in rows:
        name = row["display_name"] or row["legal_name"]
        if row["source_system"] != "permits" or not is_placeholder_name(name):
            continue
        dependencies = conn.execute(
            """SELECT
               (SELECT COUNT(*) FROM crm_company_relationships WHERE company_id=?) +
               (SELECT COUNT(*) FROM contacts WHERE company_id=?) +
               (SELECT COUNT(*) FROM crm_activities WHERE company_id=?) AS n""",
            (row["id"],)*3,
        ).fetchone()["n"]
        if dependencies:
            continue
        conn.execute(
            "UPDATE companies SET lifecycle_state='deprecated',is_active=0,lead_type='unverified_permit_contact',"
            "lead_verification_status='unverified',why_this_lead='Placeholder retained for audit; not an actionable lead',updated_at=? WHERE id=?",
            (now,row["id"]),
        )
        conn.execute(
            "UPDATE company_roles SET is_primary=0,verification_status='unverified',updated_at=? WHERE company_id=?",
            (now,row["id"]),
        )
        changed += 1
    return changed


def apply_lead_role_correction(conn: sqlite3.Connection) -> dict:
    """Apply the v1 correction transactionally; safe to run repeatedly."""
    now = _now()
    before = {r["jurisdiction"]: r["n"] for r in conn.execute(
        "SELECT jurisdiction,COUNT(*) n FROM permits WHERE contractor_company_id IS NOT NULL GROUP BY jurisdiction"
    )}
    stats = Counter()
    with conn:
        rows = conn.execute(
            """SELECT p.id,p.jurisdiction,p.general_contractor_name,p.contractor_company_id,
                      p.raw_source_json,p.contractor_license_number,p.city,p.state,
                      pr.contractor_company_id AS project_company_id
               FROM permits p LEFT JOIN projects pr ON pr.permit_id=p.id ORDER BY p.id"""
        ).fetchall()
        for row in rows:
            conn.execute(
                """INSERT OR IGNORE INTO lead_role_assignment_history
                   (migration_key,permit_id,prior_general_contractor_name,
                    prior_contractor_company_id,prior_project_contractor_company_id,recorded_at)
                   VALUES (?,?,?,?,?,?)""",
                (MIGRATION_KEY,row["id"],row["general_contractor_name"],
                 row["contractor_company_id"],row["project_company_id"],now),
            )
            raw = _raw(row)
            explicit, evidence, separate = extract_permit_evidence(row["jurisdiction"], raw)
            contractor_id = None
            if explicit:
                contractor_id = _source_company_id(
                    conn, explicit, row["general_contractor_name"], row["contractor_company_id"])
                if contractor_id is None:
                    data = CompanyInput(
                        name=explicit["name"], role_type="contractor",
                        license_number=row["contractor_license_number"],
                        city=row["city"], state=row["state"], source_system="permits",
                    )
                    match = resolve(conn, data)
                    if match.decision == DECISION_POSSIBLE:
                        stats["explicit_identity_ambiguous"] += 1
                    elif match.decision == DECISION_NEW or match.company_id is None:
                        contractor_id = create_company(conn, data)
                        stats["explicit_company_created"] += 1
                    else:
                        contractor_id = match.company_id
                        enrich_company(conn, contractor_id, data)
                        ensure_role(conn, contractor_id, "contractor", source="permits",
                                    verification_status="verified",
                                    evidence_source="permit_explicit_field",
                                    evidence_field=explicit["field"])
                        stats["explicit_company_matched"] += 1
                if contractor_id:
                    stats["explicit_linked"] += 1
                else:
                    stats["explicit_unresolved"] += 1
            lead_type = "verified_contractor" if explicit else "unverified_permit_contact"
            why = explicit["why"] if explicit else "Jurisdiction publishes no explicit contractor identity for this permit"
            conn.execute(
                """UPDATE permits SET general_contractor_name=?,contractor_company_id=?,
                   applicant_name=?,applicant_organization=?,responsible_party_name=?,
                   permit_professional_name=?,contractor_source_role=?,contractor_source_field=?,
                   contractor_evidence_confidence=?,contractor_verification_status=?,
                   lead_type=?,why_this_lead=? WHERE id=?""",
                (explicit["name"] if explicit else None, contractor_id,
                 separate["applicant_name"],separate["applicant_organization"],
                 separate["responsible_party_name"],separate["permit_professional_name"],
                 explicit["role"] if explicit else None, explicit["field"] if explicit else None,
                 explicit["confidence"] if explicit else None,
                 "verified" if explicit else "unverified",lead_type,why,row["id"]),
            )
            conn.execute("UPDATE projects SET contractor_company_id=? WHERE permit_id=?", (contractor_id,row["id"]))
            for item in evidence:
                evidence_company = _source_company_id(
                    conn, item, row["general_contractor_name"], row["contractor_company_id"])
                conn.execute(
                    """INSERT INTO permit_party_evidence
                       (permit_id,company_id,party_name,source_role,source_field,lead_type,
                        verification_status,confidence,is_contractor_evidence,why_this_lead,
                        created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(permit_id,source_field,party_name) DO UPDATE SET
                         company_id=excluded.company_id,source_role=excluded.source_role,
                         lead_type=excluded.lead_type,verification_status=excluded.verification_status,
                         confidence=excluded.confidence,is_contractor_evidence=excluded.is_contractor_evidence,
                         why_this_lead=excluded.why_this_lead,updated_at=excluded.updated_at""",
                    (row["id"],evidence_company,item["name"],item["role"],item["field"],
                     item["lead_type"],item["verification"],item["confidence"],
                     item["is_contractor"],item["why"],now,now),
                )
        stats["placeholder_companies_deprecated"] = _deprecate_placeholder_companies(conn, now)
        lead_counts = _classify_companies(conn, now)
    after = {r["jurisdiction"]: r["n"] for r in conn.execute(
        "SELECT jurisdiction,COUNT(*) n FROM permits WHERE contractor_company_id IS NOT NULL GROUP BY jurisdiction"
    )}
    return {"migration_key":MIGRATION_KEY,"before":before,"after":after,
            "assignment_stats":dict(stats),"lead_types":dict(lead_counts)}


def rollback_lead_role_correction(conn: sqlite3.Connection) -> int:
    """Restore permit/project links and mapped names from the v1 ledger."""
    rows = conn.execute(
        "SELECT * FROM lead_role_assignment_history WHERE migration_key=?",
        (MIGRATION_KEY,),
    ).fetchall()
    with conn:
        for row in rows:
            conn.execute(
                "UPDATE permits SET general_contractor_name=?,contractor_company_id=? WHERE id=?",
                (row["prior_general_contractor_name"],row["prior_contractor_company_id"],row["permit_id"]),
            )
            conn.execute(
                "UPDATE projects SET contractor_company_id=? WHERE permit_id=?",
                (row["prior_project_contractor_company_id"],row["permit_id"]),
            )
    return len(rows)


def migration_report(conn: sqlite3.Connection) -> dict:
    before = {r["jurisdiction"]:r["n"] for r in conn.execute(
        """SELECT p.jurisdiction,COUNT(*) n FROM lead_role_assignment_history h
           JOIN permits p ON p.id=h.permit_id WHERE h.migration_key=?
           AND h.prior_contractor_company_id IS NOT NULL GROUP BY p.jurisdiction""", (MIGRATION_KEY,))}
    after = {r["jurisdiction"]:r["n"] for r in conn.execute(
        "SELECT jurisdiction,COUNT(*) n FROM permits WHERE contractor_company_id IS NOT NULL GROUP BY jurisdiction")}
    lead_types = {r["lead_type"]:r["n"] for r in conn.execute(
        "SELECT lead_type,COUNT(*) n FROM crm_company_relationships GROUP BY lead_type")}
    return {"migration_key":MIGRATION_KEY,"before":before,"after":after,
            "lead_types":lead_types,
            "permit_project_mismatches":conn.execute(
                """SELECT COUNT(*) n FROM projects pr JOIN permits p ON p.id=pr.permit_id
                   WHERE COALESCE(pr.contractor_company_id,-1)<>COALESCE(p.contractor_company_id,-1)""").fetchone()["n"]}
