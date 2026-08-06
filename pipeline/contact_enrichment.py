"""Safe preview and import of researched company contact CSV files.

The browser and command-line importer share this module so validation and
write behavior cannot drift. Existing canonical company fields are preserved;
only missing values are filled unless an explicit overwrite is requested by
the command-line tool. Every browser import is permission-gated, backed up,
transactional, and security-audited.
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


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5_000
CONTACT_FIELDS = (
    "research_phone",
    "research_email",
    "research_website",
    "research_address",
    "primary_contact_name",
    "primary_contact_title",
    "primary_contact_phone",
    "primary_contact_email",
)
REQUIRED_COLUMNS = {
    "company_id", "company_name", "research_status", "research_confidence",
    "source_urls", *CONTACT_FIELDS,
}
ALLOWED_STATUS = {"found", "partial", "not found", "ambiguous"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value) -> str:
    return str(value or "").strip()


def normalize_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean(value).casefold())


def valid_phone(value: str) -> bool:
    return not value or len(re.sub(r"\D", "", value)) >= 7


def valid_email(value: str) -> bool:
    return not value or bool(EMAIL_RE.match(value))


def valid_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value if "://" in value else "https://" + value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def source_urls(value: str) -> list[str]:
    urls = [part.strip() for part in re.split(r"[;\n]+", clean(value)) if part.strip()]
    return [url for url in urls if valid_url(url)]


def canonical_website(value: str) -> str:
    value = clean(value)
    if value and "://" not in value:
        return "https://" + value
    return value


def safe_filename(value: str) -> str:
    return re.split(r"[\\/]", clean(value))[-1]


def parse_csv_bytes(filename: str, raw: bytes) -> list[dict]:
    filename = safe_filename(filename)
    if not filename or not filename.casefold().endswith(".csv"):
        raise ValueError("Choose a CSV contact-enrichment file.")
    if not raw:
        raise ValueError("The uploaded CSV is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("The CSV is larger than the 5 MB upload limit.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("The CSV must use UTF-8 encoding.") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = [clean(header) for header in (reader.fieldnames or [])]
    if not headers:
        raise ValueError("The CSV has no header row.")
    if len(headers) != len(set(headers)):
        raise ValueError("The CSV contains duplicate column names.")
    missing = sorted(REQUIRED_COLUMNS.difference(headers))
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))
    reader.fieldnames = headers

    rows = [dict(row) for row in reader if any(clean(value) for value in row.values())]
    if not rows:
        raise ValueError("The CSV contains no company rows.")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"The CSV exceeds the {MAX_ROWS:,}-row import limit.")
    return rows


def decode_upload(body: dict) -> tuple[str, list[dict]]:
    filename = safe_filename(body.get("filename", ""))
    encoded = clean(body.get("content_base64"))
    if len(encoded) > (MAX_UPLOAD_BYTES * 2):
        raise ValueError("The CSV is larger than the 5 MB upload limit.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("The uploaded CSV could not be decoded.") from exc
    return filename, parse_csv_bytes(filename, raw)


def load_company(conn: sqlite3.Connection, company_id: int):
    return conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()


def identity_matches(conn: sqlite3.Connection, company, supplied_name: str) -> bool:
    supplied = normalize_identity(supplied_name)
    if not supplied:
        return False
    names = {
        normalize_identity(company["display_name"]),
        normalize_identity(company["legal_name"]),
    }
    names.update(
        normalize_identity(row["alias_name"])
        for row in conn.execute(
            "SELECT alias_name FROM company_aliases WHERE company_id=?", (company["id"],)
        )
    )
    return supplied in names


def validate_row(conn: sqlite3.Connection, row: dict):
    reasons = []
    try:
        company_id = int(clean(row.get("company_id")))
    except ValueError:
        return None, ["invalid company_id"]

    company = load_company(conn, company_id)
    if company is None:
        return None, ["company_id not found"]
    if not identity_matches(conn, company, row.get("company_name", "")):
        reasons.append("company_name does not match canonical identity or alias")

    status = clean(row.get("research_status")).casefold()
    confidence = clean(row.get("research_confidence")).casefold()
    if status not in ALLOWED_STATUS:
        reasons.append("research_status must be Found, Partial, Not Found, or Ambiguous")
    if status in {"found", "partial"} and confidence not in ALLOWED_CONFIDENCE:
        reasons.append("research_confidence must be High, Medium, or Low")

    values = {field: clean(row.get(field)) for field in CONTACT_FIELDS}
    has_contact = any(values.values())
    sources = source_urls(row.get("source_urls", ""))
    if has_contact and not sources:
        reasons.append("contact data requires at least one valid source URL")
    if values["research_email"] and not valid_email(values["research_email"]):
        reasons.append("invalid research_email")
    if values["primary_contact_email"] and not valid_email(values["primary_contact_email"]):
        reasons.append("invalid primary_contact_email")
    if not valid_phone(values["research_phone"]):
        reasons.append("research_phone has fewer than 7 digits")
    if not valid_phone(values["primary_contact_phone"]):
        reasons.append("primary_contact_phone has fewer than 7 digits")
    if values["research_website"] and not valid_url(values["research_website"]):
        reasons.append("invalid research_website")
    if status in {"not found", "ambiguous"} and has_contact:
        reasons.append("Not Found/Ambiguous rows cannot contain importable contact data")
    if status in {"found", "partial"} and not has_contact:
        reasons.append("Found/Partial row has no contact data")
    return company, reasons


def analyze_rows(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    accepted = []
    rejected = []
    skipped = []
    seen_company_ids = set()
    for row_number, row in enumerate(rows, start=2):
        company, reasons = validate_row(conn, row)
        company_id = company["id"] if company is not None else None
        if company_id is not None and company_id in seen_company_ids:
            reasons.append("duplicate company_id in this CSV")
        if company_id is not None:
            seen_company_ids.add(company_id)
        if reasons:
            rejected.append({"row_number": row_number, "row": row, "reasons": reasons})
            continue
        status = clean(row.get("research_status")).casefold()
        if status in {"not found", "ambiguous"}:
            skipped.append({"row_number": row_number, "row": row})
            continue
        accepted.append((company, row))
    return {
        "accepted": accepted,
        "rejected": rejected,
        "skipped": skipped,
        "counts": {
            "input_rows": len(rows),
            "importable": len(accepted),
            "actionable": len(accepted),
            "skipped": len(skipped),
            "rejected": len(rejected),
        },
    }


def rejection_summary(item: dict) -> dict:
    row = item["row"]
    return {
        "row_number": item["row_number"],
        "company_id": clean(row.get("company_id")),
        "company_name": clean(row.get("company_name")),
        "reasons": item["reasons"],
    }


def skipped_summary(item: dict) -> dict:
    row = item["row"]
    return {
        "row_number": item["row_number"],
        "company_id": clean(row.get("company_id")),
        "company_name": clean(row.get("company_name")),
        "status": clean(row.get("research_status")),
    }


def preview_upload(conn: sqlite3.Connection, user, body: dict) -> dict:
    require_permission(user, "admin.system")
    filename, rows = decode_upload(body)
    analysis = analyze_rows(conn, rows)
    return {
        "filename": filename,
        "counts": analysis["counts"],
        "rejections": [rejection_summary(item) for item in analysis["rejected"][:50]],
        "skipped_rows": [skipped_summary(item) for item in analysis["skipped"][:20]],
        "existing_company_fields_preserved": True,
    }


def database_path(conn: sqlite3.Connection) -> Path:
    rows = conn.execute("PRAGMA database_list").fetchall()
    filename = next((clean(row[2]) for row in rows if row[1] == "main"), "")
    if not filename:
        raise ValueError("A file-backed database is required for contact imports.")
    return Path(filename).resolve()


def backup_database(conn: sqlite3.Connection) -> Path:
    source_path = database_path(conn)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = source_path.with_name(f"corridoriq-before-contact-import-{stamp}.db")
    backup = sqlite3.connect(target)
    try:
        conn.backup(backup)
    finally:
        backup.close()
    return target


def update_company(conn, company, row: dict, overwrite: bool) -> dict:
    candidates = {
        "main_phone": clean(row.get("research_phone")),
        "main_email": clean(row.get("research_email")),
        "website": canonical_website(row.get("research_website")),
        "address_line_1": clean(row.get("research_address")),
    }
    updates = {}
    for column, value in candidates.items():
        if value and (overwrite or not clean(company[column])):
            updates[column] = value
    if updates:
        updates["updated_at"] = now_iso()
        set_sql = ", ".join(f"{column}=?" for column in updates)
        conn.execute(
            f"UPDATE companies SET {set_sql} WHERE id=?",
            [*updates.values(), company["id"]],
        )
    return updates


def upsert_named_contact(conn, company_id: int, row: dict, source: str) -> tuple[str, int | None]:
    name = clean(row.get("primary_contact_name"))
    email = clean(row.get("primary_contact_email"))
    phone = clean(row.get("primary_contact_phone"))
    title = clean(row.get("primary_contact_title"))
    if not any((name, email, phone)):
        return "none", None

    existing = None
    if email:
        existing = conn.execute(
            "SELECT * FROM contacts WHERE company_id=? AND lower(email)=lower(?) LIMIT 1",
            (company_id, email),
        ).fetchone()
    if existing is None and phone:
        digits = re.sub(r"\D", "", phone)
        candidates = conn.execute(
            "SELECT * FROM contacts WHERE company_id=? AND phone IS NOT NULL", (company_id,)
        ).fetchall()
        existing = next(
            (item for item in candidates if re.sub(r"\D", "", clean(item["phone"])) == digits),
            None,
        )
    if existing is None and name:
        existing = conn.execute(
            "SELECT * FROM contacts WHERE company_id=? AND lower(full_name)=lower(?) LIMIT 1",
            (company_id, name),
        ).fetchone()

    now = now_iso()
    if existing:
        effective = {
            "full_name": name or clean(existing["full_name"]),
            "job_title": title or clean(existing["job_title"]),
            "email": email or clean(existing["email"]),
            "phone": phone or clean(existing["phone"]),
            "source": source or clean(existing["source"]),
        }
        if all(clean(existing[column]) == value for column, value in effective.items()):
            return "unchanged", existing["id"]
        conn.execute(
            """
            UPDATE contacts
            SET full_name=?, job_title=?, email=?, phone=?, source=?,
                last_seen_at=?, updated_at=?
            WHERE id=?
            """,
            (
                effective["full_name"] or None,
                effective["job_title"] or None,
                effective["email"] or None,
                effective["phone"] or None,
                effective["source"] or None,
                now, now, existing["id"],
            ),
        )
        return "updated", existing["id"]

    cursor = conn.execute(
        """
        INSERT INTO contacts (
            company_id, full_name, job_title, email, phone, is_primary,
            source, first_seen_at, last_seen_at, created_at, updated_at
        ) VALUES (?,?,?,?,?,1,?,?,?,?,?)
        """,
        (company_id, name or None, title or None, email or None, phone or None,
         source, now, now, now, now),
    )
    return "created", cursor.lastrowid


def audit_identity_update(conn, company_id: int, updates: dict, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO company_identity_audit_log (
            action, target_company_id, new_values_json, reason, confidence,
            performed_by, performed_at
        ) VALUES ('profile_updated',?,?,?,?,?,?)
        """,
        (
            company_id,
            json.dumps({"company_fields": updates, "source_urls": source_urls(row.get("source_urls", ""))}),
            "Verified contact enrichment import",
            {"high": 95, "medium": 80, "low": 60}.get(
                clean(row.get("research_confidence")).casefold(), 60
            ),
            "contact_enrichment_import",
            now_iso(),
        ),
    )


def apply_rows(conn: sqlite3.Connection, accepted: list, *, overwrite: bool = False) -> dict:
    company_updates = contacts_created = contacts_updated = unchanged_rows = 0
    for company, row in accepted:
        updates = update_company(conn, company, row, overwrite)
        if updates:
            company_updates += 1
        source = "; ".join(source_urls(row.get("source_urls", "")))
        contact_action, _ = upsert_named_contact(conn, company["id"], row, source)
        contacts_created += contact_action == "created"
        contacts_updated += contact_action == "updated"
        if updates or contact_action in {"created", "updated"}:
            audit_identity_update(conn, company["id"], updates, row)
        else:
            unchanged_rows += 1
    return {
        "imported_rows": len(accepted),
        "company_profiles_updated": company_updates,
        "contacts_created": contacts_created,
        "contacts_updated": contacts_updated,
        "rows_already_current": unchanged_rows,
    }


def import_upload(conn: sqlite3.Connection, user, body: dict, *, ip=None, ua=None) -> dict:
    require_permission(user, "admin.system")
    filename, rows = decode_upload(body)
    analysis = analyze_rows(conn, rows)
    if analysis["rejected"]:
        count = len(analysis["rejected"])
        raise ValueError(f"Import blocked: fix or remove {count} rejected row(s), then preview again.")
    if not analysis["accepted"]:
        raise ValueError("There are no validated contact rows to import.")

    backup = backup_database(conn)
    try:
        stats = apply_rows(conn, analysis["accepted"], overwrite=False)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        try:
            write_audit(
                conn, event_type="contact_enrichment_import", user_id=user["id"],
                organization_id=user["organization_id"], resource_type="contact_csv",
                resource_id=filename, action="import", success=False,
                ip_address=ip, user_agent=ua, details={"error_type": type(exc).__name__},
            )
        except Exception:
            pass
        raise

    result = {
        "ok": True,
        "filename": filename,
        "backup_file": backup.name,
        "counts": {
            **analysis["counts"],
            **stats,
        },
    }
    write_audit(
        conn, event_type="contact_enrichment_import", user_id=user["id"],
        organization_id=user["organization_id"], resource_type="contact_csv",
        resource_id=filename, action="import", success=True,
        ip_address=ip, user_agent=ua,
        details={"backup_file": backup.name, **result["counts"]},
    )
    return result
