"""Validated multi-source company evidence import and coverage reporting.

The module accepts researched/official CSV exports but never treats a row as a
fact without a source URL, source record identifier, retrieval timestamp,
explicit company match method, and confidence score. UCC rows are financing
records only; the importer does not infer distress or creditworthiness.
"""

from __future__ import annotations

import base64
import binascii
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

from pipeline.auth.rbac import require_permission
from pipeline.auth.service import write_audit
from pipeline.contact_enrichment import database_path, identity_matches
from pipeline.company_resolution.normalize import normalize_company_name


MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10_000
ROC_MAX_UPLOAD_BYTES = 35 * 1024 * 1024
ROC_MAX_ROWS = 100_000
SOURCE_REGISTRY = {
    "az_roc": {
        "label": "Arizona Registrar of Contractors",
        "official_url": "https://roc.az.gov/contractor-search",
        "category": "License verification",
        "domains": ("roc.az.gov",),
    },
    "azcc": {
        "label": "Arizona Corporation Commission",
        "official_url": "https://arizonabusinesscenter.azcc.gov/businesssearch",
        "category": "Corporate registration",
        "domains": ("azcc.gov",),
    },
    "az_ucc": {
        "label": "Arizona Secretary of State UCC",
        "official_url": "https://azsos.gov/business/ucc",
        "category": "Financing filings",
        "domains": ("azsos.gov",),
    },
    "adot": {
        "label": "Arizona Department of Transportation",
        "official_url": "https://cnsads.azdot.gov/current",
        "category": "Public contract activity",
        "domains": ("azdot.gov",),
    },
    "municipal": {
        "label": "Municipal public record",
        "official_url": None,
        "category": "Permit evidence",
    },
    "company_website": {
        "label": "Company website",
        "official_url": None,
        "category": "First-party company evidence",
    },
    "researched_public_record": {
        "label": "Researched public record",
        "official_url": None,
        "category": "Other verified evidence",
    },
}
CSV_COLUMNS = (
    "company_id", "company_name", "source_type", "evidence_type",
    "source_record_id", "title", "status", "summary", "amount",
    "effective_date", "expiration_date", "source_url", "retrieved_at",
    "confidence", "match_method",
)
REQUIRED_COLUMNS = set(CSV_COLUMNS)
MATCH_METHODS = {"company_id", "license_number", "legal_name", "manual_verified"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value) -> str:
    return str(value or "").strip()


def safe_filename(value: str) -> str:
    return re.split(r"[\\/]", clean(value))[-1]


def valid_url(value: str) -> bool:
    parsed = urlparse(clean(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def valid_iso_date(value: str, *, allow_datetime: bool = False) -> bool:
    if not clean(value):
        return True
    try:
        candidate = clean(value).replace("Z", "+00:00")
        if allow_datetime:
            datetime.fromisoformat(candidate)
        else:
            datetime.strptime(candidate[:10], "%Y-%m-%d")
        return True
    except ValueError:
        return False


def parse_csv_bytes(filename: str, raw: bytes) -> list[dict]:
    filename = safe_filename(filename)
    if not filename.casefold().endswith(".csv"):
        raise ValueError("Choose a CSV external-intelligence file.")
    if not raw:
        raise ValueError("The uploaded CSV is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("The CSV is larger than the 8 MB upload limit.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("The CSV must use UTF-8 encoding.") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = [clean(item) for item in (reader.fieldnames or [])]
    missing = sorted(REQUIRED_COLUMNS.difference(headers))
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))
    if len(headers) != len(set(headers)):
        raise ValueError("The CSV contains duplicate column names.")
    reader.fieldnames = headers
    rows = [dict(row) for row in reader if any(clean(v) for v in row.values())]
    if not rows:
        raise ValueError("The CSV contains no evidence rows.")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"The CSV exceeds the {MAX_ROWS:,}-row limit.")
    return rows


def decode_upload(body: dict) -> tuple[str, list[dict]]:
    filename = safe_filename(body.get("filename", ""))
    encoded = clean(body.get("content_base64"))
    if len(encoded) > MAX_UPLOAD_BYTES * 2:
        raise ValueError("The CSV is larger than the upload limit.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("The uploaded CSV could not be decoded.") from exc
    return filename, parse_csv_bytes(filename, raw)


def _amount(value: str):
    value = clean(value).replace("$", "").replace(",", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def validate_row(conn: sqlite3.Connection, row: dict, *, organization_id: int | None = None):
    reasons = []
    try:
        company_id = int(clean(row.get("company_id")))
    except ValueError:
        return None, ["invalid company_id"]
    company = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if company is None:
        return None, ["company_id not found"]
    if organization_id is not None and conn.execute(
            "SELECT 1 FROM crm_company_relationships WHERE organization_id=? AND company_id=?",
            (organization_id, company_id)).fetchone() is None:
        reasons.append("company is not enrolled in this organization")
    if not identity_matches(conn, company, row.get("company_name", "")):
        reasons.append("company_name does not match canonical identity or alias")

    source_type = clean(row.get("source_type")).casefold()
    if source_type not in SOURCE_REGISTRY:
        reasons.append("unsupported source_type")
    if not clean(row.get("evidence_type")):
        reasons.append("evidence_type is required")
    if not clean(row.get("source_record_id")):
        reasons.append("source_record_id is required")
    if not clean(row.get("title")):
        reasons.append("title is required")
    if not valid_url(row.get("source_url", "")):
        reasons.append("a valid http(s) source_url is required")
    elif source_type in SOURCE_REGISTRY and SOURCE_REGISTRY[source_type].get("domains"):
        hostname = (urlparse(clean(row.get("source_url"))).hostname or "").casefold()
        allowed = SOURCE_REGISTRY[source_type]["domains"]
        if not any(hostname == domain or hostname.endswith("." + domain) for domain in allowed):
            reasons.append(f"source_url must use an official {SOURCE_REGISTRY[source_type]['label']} domain")
    if not clean(row.get("retrieved_at")) or not valid_iso_date(row.get("retrieved_at"), allow_datetime=True):
        reasons.append("retrieved_at must be an ISO date or datetime")
    for field in ("effective_date", "expiration_date"):
        if not valid_iso_date(row.get(field, "")):
            reasons.append(f"{field} must be YYYY-MM-DD")
    try:
        confidence = float(clean(row.get("confidence")))
        if confidence < 0 or confidence > 100:
            raise ValueError
    except ValueError:
        reasons.append("confidence must be a number from 0 to 100")
    if clean(row.get("match_method")).casefold() not in MATCH_METHODS:
        reasons.append("unsupported match_method")
    if clean(row.get("amount")) and _amount(row.get("amount")) is None:
        reasons.append("amount must be numeric")
    return company, reasons


def analyze_rows(conn: sqlite3.Connection, rows: list[dict], *, organization_id: int | None = None) -> dict:
    accepted, rejected, seen = [], [], set()
    for row_number, row in enumerate(rows, start=2):
        company, reasons = validate_row(conn, row, organization_id=organization_id)
        key = (clean(row.get("company_id")), clean(row.get("source_type")).casefold(),
               clean(row.get("evidence_type")).casefold(), clean(row.get("source_record_id")))
        if key in seen:
            reasons.append("duplicate evidence key in this CSV")
        seen.add(key)
        if reasons:
            rejected.append({"row_number": row_number, "row": row, "reasons": reasons})
        else:
            accepted.append((company, row))
    return {"accepted": accepted, "rejected": rejected,
            "counts": {"input_rows": len(rows), "importable": len(accepted), "rejected": len(rejected)}}


def rejection_summary(item: dict) -> dict:
    row = item["row"]
    return {"row_number": item["row_number"], "company_id": clean(row.get("company_id")),
            "company_name": clean(row.get("company_name")),
            "source_type": clean(row.get("source_type")), "reasons": item["reasons"]}


def preview_upload(conn: sqlite3.Connection, user, body: dict) -> dict:
    require_permission(user, "admin.system")
    filename, rows = decode_upload(body)
    analysis = analyze_rows(conn, rows, organization_id=user["organization_id"])
    return {"filename": filename, "counts": analysis["counts"],
            "rejections": [rejection_summary(item) for item in analysis["rejected"][:75]],
            "source_types": sorted({clean(row.get("source_type")).casefold() for row in rows})}


def backup_database(conn: sqlite3.Connection) -> Path:
    source = database_path(conn)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = source.with_name(f"corridoriq-before-intelligence-import-{stamp}.db")
    backup = sqlite3.connect(target)
    try:
        conn.backup(backup)
    finally:
        backup.close()
    return target


def apply_rows(conn: sqlite3.Connection, accepted: list) -> dict:
    created = updated = 0
    now = now_iso()
    for company, row in accepted:
        key = (company["id"], clean(row["source_type"]).casefold(),
               clean(row["evidence_type"]).casefold(), clean(row["source_record_id"]))
        exists = conn.execute(
            "SELECT id FROM company_external_evidence WHERE company_id=? AND source_type=? "
            "AND evidence_type=? AND source_record_id=?", key).fetchone()
        source_type = key[1]
        values = (
            SOURCE_REGISTRY[source_type]["label"], clean(row["title"]), clean(row.get("status")) or None,
            clean(row.get("summary")) or None, _amount(row.get("amount")),
            clean(row.get("effective_date")) or None, clean(row.get("expiration_date")) or None,
            clean(row["source_url"]), clean(row["retrieved_at"]), float(clean(row["confidence"])),
            clean(row["match_method"]).casefold(), json.dumps({"imported_company_name": clean(row["company_name"])}),
            now,
        )
        if exists:
            conn.execute(
                "UPDATE company_external_evidence SET source_agency=?,title=?,status=?,summary=?,amount=?,"
                "effective_date=?,expiration_date=?,source_url=?,retrieved_at=?,confidence=?,match_method=?,"
                "details_json=?,updated_at=? WHERE id=?", (*values, exists["id"]))
            updated += 1
        else:
            conn.execute(
                "INSERT INTO company_external_evidence (company_id,source_type,evidence_type,source_record_id,"
                "source_agency,title,status,summary,amount,effective_date,expiration_date,source_url,retrieved_at,"
                "confidence,match_method,details_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*key, *values[:-1], now, now))
            created += 1
    return {"imported_rows": len(accepted), "created_rows": created, "updated_rows": updated}


def import_upload(conn: sqlite3.Connection, user, body: dict, *, ip=None, ua=None) -> dict:
    require_permission(user, "admin.system")
    filename, rows = decode_upload(body)
    analysis = analyze_rows(conn, rows, organization_id=user["organization_id"])
    if analysis["rejected"]:
        raise ValueError(f"Import blocked: fix {len(analysis['rejected'])} rejected row(s).")
    if not analysis["accepted"]:
        raise ValueError("There are no validated evidence rows to import.")
    backup = backup_database(conn)
    try:
        stats = apply_rows(conn, analysis["accepted"])
        conn.execute(
            "INSERT INTO external_intelligence_imports (organization_id,filename,imported_by,input_rows,"
            "imported_rows,created_rows,updated_rows,backup_filename,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (user["organization_id"], filename, user["id"], len(rows), stats["imported_rows"],
             stats["created_rows"], stats["updated_rows"], backup.name, now_iso()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    write_audit(conn, event_type="external_intelligence_import", user_id=user["id"],
                organization_id=user["organization_id"], resource_type="intelligence_csv",
                resource_id=filename, action="import", success=True, ip_address=ip,
                user_agent=ua, details={"counts": stats, "backup_file": backup.name})
    return {"ok": True, "filename": filename, "backup_file": backup.name,
            "counts": {**analysis["counts"], **stats}}


def evidence_for_company(conn: sqlite3.Connection, company_id: int) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT id,company_id,source_type,source_agency,evidence_type,source_record_id,title,status,"
        "summary,amount,effective_date,expiration_date,source_url,retrieved_at,confidence,match_method "
        "FROM company_external_evidence WHERE company_id=? "
        "ORDER BY effective_date DESC, retrieved_at DESC, id DESC", (company_id,)).fetchall()]


def coverage_for_company(conn: sqlite3.Connection, company_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT source_type,COUNT(*) AS record_count,MAX(retrieved_at) AS last_retrieved,"
        "AVG(confidence) AS average_confidence FROM company_external_evidence "
        "WHERE company_id=? GROUP BY source_type", (company_id,)).fetchall()
    found = {row["source_type"]: row for row in rows}
    return [{"source_type": key, **meta, "status": "available" if key in found else "not_researched",
             "record_count": found[key]["record_count"] if key in found else 0,
             "last_retrieved": found[key]["last_retrieved"] if key in found else None,
             "average_confidence": found[key]["average_confidence"] if key in found else None}
            for key, meta in SOURCE_REGISTRY.items()]


def organization_coverage(conn: sqlite3.Connection, organization_id: int) -> dict:
    total = conn.execute(
        "SELECT COUNT(DISTINCT r.company_id) AS n FROM crm_company_relationships r "
        "WHERE r.organization_id=?", (organization_id,)).fetchone()["n"]
    rows = conn.execute(
        "SELECT e.source_type,COUNT(*) AS records,COUNT(DISTINCT e.company_id) AS companies,"
        "MAX(e.retrieved_at) AS last_retrieved FROM company_external_evidence e "
        "JOIN crm_company_relationships r ON r.company_id=e.company_id AND r.organization_id=? "
        "GROUP BY e.source_type", (organization_id,)).fetchall()
    by_source = {row["source_type"]: dict(row) for row in rows}
    public_bids = conn.execute(
        "SELECT COUNT(*) AS opportunities,(SELECT COUNT(*) FROM public_bid_planholders) AS planholders,"
        "(SELECT COUNT(*) FROM public_bid_planholders WHERE company_id IS NOT NULL) AS matched_planholders "
        "FROM public_bid_opportunities WHERE source_type='adot' AND is_active=1").fetchone()
    return {"total_crm_companies": total, "public_bids": dict(public_bids), "sources": [
        {"source_type": key, **meta, **by_source.get(key, {"records": 0, "companies": 0, "last_retrieved": None})}
        for key, meta in SOURCE_REGISTRY.items()]}


def research_queue(conn: sqlite3.Connection, organization_id: int, *, limit: int = 500) -> dict:
    """Return an organization-scoped, contact-first queue for external research."""
    limit = max(1, min(int(limit or 500), 2_500))
    rows = conn.execute(
        "SELECT c.id AS company_id,c.display_name AS company_name,c.legal_name,c.license_number,"
        "c.city,c.state,ci.company_priority_tier,ci.company_priority_score,"
        "CASE WHEN TRIM(COALESCE(c.main_phone,''))<>'' OR TRIM(COALESCE(c.main_email,''))<>'' "
        "OR TRIM(COALESCE(c.website,''))<>'' OR EXISTS (SELECT 1 FROM contacts ct WHERE ct.company_id=c.id "
        "AND (TRIM(COALESCE(ct.phone,''))<>'' OR TRIM(COALESCE(ct.mobile_phone,''))<>'' "
        "OR TRIM(COALESCE(ct.email,''))<>'')) THEN 1 ELSE 0 END AS has_contact_info "
        "FROM crm_company_relationships r JOIN companies c ON c.id=r.company_id "
        "LEFT JOIN company_intelligence ci ON ci.company_id=c.id "
        "WHERE r.organization_id=? AND c.lifecycle_state='active' "
        "ORDER BY has_contact_info DESC,COALESCE(ci.company_priority_score,0) DESC,c.display_name LIMIT ?",
        (organization_id, limit)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        found = {record["source_type"] for record in conn.execute(
            "SELECT DISTINCT source_type FROM company_external_evidence WHERE company_id=?",
            (row["company_id"],)).fetchall()}
        item["needed_sources"] = [key for key in ("az_roc", "azcc", "az_ucc", "adot") if key not in found]
        items.append(item)
    return {"items": items, "limit": limit}


def csv_template() -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerow({"company_id": "123", "company_name": "Example Contractor LLC",
                     "source_type": "az_roc", "evidence_type": "contractor_license",
                     "source_record_id": "ROC-123456", "title": "Arizona contractor license",
                     "status": "Active", "summary": "Classification CR-37",
                     "amount": "", "effective_date": "2024-01-01", "expiration_date": "2026-12-31",
                     "source_url": "https://roc.az.gov/contractor-search", "retrieved_at": now_iso(),
                     "confidence": "95", "match_method": "license_number"})
    return output.getvalue()


# ---------------------------------------------------------------------------
# Raw Arizona ROC active-license CSV import.
# ---------------------------------------------------------------------------

def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", clean(value).casefold())


ROC_FIELDS = {
    "license_number": ("licenseno", "licensenumber", "license"),
    "business_name": ("businessname", "companyname"),
    "dba_name": ("doingbusinessas", "dba"),
    "classification": ("class", "classification", "classcode"),
    "classification_description": ("classdescription", "classificationdescription"),
    "license_type": ("licensetype", "type"),
    "issue_date": ("issuedate", "licenseissuedate"),
    "expiration_date": ("expirationdate", "expirydate"),
    "status": ("status", "licensestatus"),
    "address": ("address", "streetaddress"),
    "city": ("city",), "state": ("state",),
    "postal_code": ("zip", "zipcode", "postalcode"),
}


def parse_roc_csv_bytes(filename: str, raw: bytes) -> list[dict]:
    filename = safe_filename(filename)
    if not filename.casefold().endswith(".csv"):
        raise ValueError("Choose the official Arizona ROC CSV file.")
    if not raw or len(raw) > ROC_MAX_UPLOAD_BYTES:
        raise ValueError("The ROC CSV is empty or larger than the 35 MB limit.")
    try:
        text_value = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text_value = raw.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise ValueError("The ROC CSV encoding is unsupported.") from exc
    parsed = list(csv.reader(io.StringIO(text_value, newline="")))
    header_index = next((i for i, row in enumerate(parsed[:20])
                         if "licenseno" in {_header_key(cell) for cell in row}
                         and "businessname" in {_header_key(cell) for cell in row}), None)
    if header_index is None:
        raise ValueError("This does not look like an Arizona ROC license export: License No and Business Name are missing.")
    headers = [_header_key(cell) for cell in parsed[header_index]]
    resolved = {}
    for field, aliases in ROC_FIELDS.items():
        resolved[field] = next((headers.index(alias) for alias in aliases if alias in headers), None)
    if resolved["license_number"] is None or resolved["business_name"] is None:
        raise ValueError("The ROC CSV must include License No and Business Name.")
    rows = []
    for values in parsed[header_index + 1:]:
        if not any(clean(value) for value in values):
            continue
        row = {field: clean(values[index]) if index is not None and index < len(values) else ""
               for field, index in resolved.items()}
        if row["license_number"] and row["business_name"]:
            rows.append(row)
    if not rows:
        raise ValueError("The ROC CSV contains no license rows.")
    if len(rows) > ROC_MAX_ROWS:
        raise ValueError(f"The ROC CSV exceeds the {ROC_MAX_ROWS:,}-row limit.")
    return rows


def decode_roc_upload(body: dict) -> tuple[str, list[dict]]:
    filename = safe_filename(body.get("filename", ""))
    encoded = clean(body.get("content_base64"))
    if len(encoded) > ROC_MAX_UPLOAD_BYTES * 2:
        raise ValueError("The ROC CSV is larger than the upload limit.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("The ROC CSV could not be decoded.") from exc
    return filename, parse_roc_csv_bytes(filename, raw)


def _license_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", clean(value).upper()).lstrip("0")


def analyze_roc_rows(conn: sqlite3.Connection, rows: list[dict], organization_id: int) -> dict:
    enrolled = conn.execute(
        "SELECT c.* FROM companies c JOIN crm_company_relationships r ON r.company_id=c.id "
        "WHERE r.organization_id=? AND c.lifecycle_state='active'", (organization_id,)).fetchall()
    by_license, by_name = {}, {}
    for company in enrolled:
        license_key = _license_key(company["license_number"])
        if license_key:
            by_license.setdefault(license_key, set()).add(company["id"])
        for value in (company["display_name"], company["legal_name"], company["dba_name"]):
            key = normalize_company_name(value)
            if key:
                by_name.setdefault(key, set()).add(company["id"])
    enrolled_ids = {company["id"] for company in enrolled}
    for alias in conn.execute(
            "SELECT company_id,normalized_alias FROM company_aliases WHERE company_id IN "
            "(SELECT company_id FROM crm_company_relationships WHERE organization_id=?)", (organization_id,)):
        if alias["company_id"] in enrolled_ids:
            by_name.setdefault(alias["normalized_alias"], set()).add(alias["company_id"])
    company_by_id = {company["id"]: company for company in enrolled}
    matches, unmatched, ambiguous = [], [], []
    for row_number, row in enumerate(rows, start=2):
        license_ids = by_license.get(_license_key(row["license_number"]), set())
        name_ids = set()
        for value in (row["business_name"], row.get("dba_name")):
            name_ids.update(by_name.get(normalize_company_name(value), set()))
        if len(license_ids) == 1:
            company_id = next(iter(license_ids))
            matches.append((company_by_id[company_id], row, "license_number"))
        elif not license_ids and len(name_ids) == 1:
            company_id = next(iter(name_ids))
            matches.append((company_by_id[company_id], row, "legal_name"))
        elif len(license_ids | name_ids) > 1:
            ambiguous.append({"row_number": row_number, "license_number": row["license_number"],
                              "business_name": row["business_name"], "candidate_count": len(license_ids | name_ids)})
        else:
            unmatched.append({"row_number": row_number, "license_number": row["license_number"],
                              "business_name": row["business_name"]})
    return {"matches": matches, "unmatched": unmatched, "ambiguous": ambiguous,
            "counts": {"source_rows": len(rows), "exact_matches": len(matches),
                       "unmatched": len(unmatched), "ambiguous": len(ambiguous),
                       "matched_companies": len({company["id"] for company, _, _ in matches})}}


def preview_roc_upload(conn: sqlite3.Connection, user, body: dict) -> dict:
    require_permission(user, "admin.system")
    filename, rows = decode_roc_upload(body)
    analysis = analyze_roc_rows(conn, rows, user["organization_id"])
    return {"filename": filename, "counts": analysis["counts"],
            "unmatched_sample": analysis["unmatched"][:25],
            "ambiguous_sample": analysis["ambiguous"][:25],
            "matching_policy": "Exact license number first; otherwise one exact legal name, DBA, or alias."}


def import_roc_upload(conn: sqlite3.Connection, user, body: dict, *, ip=None, ua=None) -> dict:
    require_permission(user, "admin.system")
    filename, rows = decode_roc_upload(body)
    analysis = analyze_roc_rows(conn, rows, user["organization_id"])
    if not analysis["matches"]:
        raise ValueError("No CorridorIQ companies matched the official ROC file.")
    backup = backup_database(conn)
    now = now_iso()
    accepted = []
    touched = set()
    try:
        for company, row, method in analysis["matches"]:
            touched.add(company["id"])
            classification = " · ".join(filter(None, [row.get("classification"), row.get("classification_description"), row.get("license_type")]))
            accepted.append((company, {
                "source_type": "az_roc", "evidence_type": "contractor_license",
                "source_record_id": row["license_number"], "title": "Arizona contractor license",
                "status": row.get("status") or "Published", "summary": classification,
                "amount": "", "effective_date": _roc_date(row.get("issue_date")),
                "expiration_date": _roc_date(row.get("expiration_date")),
                "source_url": SOURCE_REGISTRY["az_roc"]["official_url"], "retrieved_at": now,
                "confidence": "99" if method == "license_number" else "95", "match_method": method,
                "company_name": company["display_name"] or company["legal_name"],
            }))
            conn.execute(
                "UPDATE companies SET license_number=COALESCE(NULLIF(TRIM(license_number),''),?),"
                "license_state='AZ',license_status=?,address_line_1=COALESCE(NULLIF(TRIM(address_line_1),''),?),"
                "city=COALESCE(NULLIF(TRIM(city),''),?),state=COALESCE(NULLIF(TRIM(state),''),?),"
                "postal_code=COALESCE(NULLIF(TRIM(postal_code),''),?),updated_at=? WHERE id=?",
                (row["license_number"], row.get("status") or None, row.get("address") or None,
                 row.get("city") or None, row.get("state") or "AZ", row.get("postal_code") or None,
                 now, company["id"]))
            dba = clean(row.get("dba_name"))
            normalized_dba = normalize_company_name(dba)
            if dba and normalized_dba:
                conn.execute(
                    "INSERT OR IGNORE INTO company_aliases (company_id,alias_name,normalized_alias,source,source_record_id,"
                    "first_seen_at,last_seen_at,created_at) VALUES (?,?,?,'az_roc',?,?,?,?)",
                    (company["id"], dba, normalized_dba, row["license_number"], now, now, now))
        stats = apply_rows(conn, accepted)
        from pipeline.company_resolution.lead_role_correction import reclassify_companies_from_external_evidence
        classifications = reclassify_companies_from_external_evidence(conn, touched, commit=False)
        conn.execute(
            "INSERT INTO external_intelligence_imports (organization_id,filename,imported_by,input_rows,imported_rows,"
            "created_rows,updated_rows,backup_filename,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (user["organization_id"], filename, user["id"], len(rows), stats["imported_rows"],
             stats["created_rows"], stats["updated_rows"], backup.name, now))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    write_audit(conn, event_type="roc_license_import", user_id=user["id"],
                organization_id=user["organization_id"], resource_type="official_roc_csv",
                resource_id=filename, action="import", success=True, ip_address=ip,
                user_agent=ua, details={"counts": analysis["counts"], "evidence": stats,
                                        "classifications": classifications, "backup_file": backup.name})
    return {"ok": True, "filename": filename, "backup_file": backup.name,
            "counts": {**analysis["counts"], **stats}, "classifications": classifications}


def _roc_date(value: str) -> str:
    value = clean(value)
    if not value:
        return ""
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""
