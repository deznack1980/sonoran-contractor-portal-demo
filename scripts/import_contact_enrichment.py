"""Validate and import researched company contacts into CorridorIQ.

The importer is safe-by-default:
- dry-run unless --apply is supplied
- requires source URLs for any contact data
- verifies company_id and company name identity
- fills missing canonical company fields without overwriting by default
- deduplicates named contacts
- creates a SQLite backup immediately before an applied import
- writes rejected rows and a summary beside the input CSV

Usage:
    python scripts/import_contact_enrichment.py path\\to\\completed_batch.csv
    python scripts/import_contact_enrichment.py path\\to\\completed_batch.csv --apply
    python scripts/import_contact_enrichment.py path\\to\\completed_batch.csv --apply --overwrite
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.config.settings import DB_PATH  # noqa: E402
from pipeline.db.database import get_connection  # noqa: E402


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
    return [u for u in urls if valid_url(u)]


def canonical_website(value: str) -> str:
    value = clean(value)
    if value and "://" not in value:
        return "https://" + value
    return value


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


def backup_database() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = DB_PATH.with_name(f"corridoriq-before-contact-import-{stamp}.db")
    source = sqlite3.connect(DB_PATH)
    backup = sqlite3.connect(target)
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()
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
            "SELECT * FROM contacts WHERE company_id=? AND phone IS NOT NULL",
            (company_id,),
        ).fetchall()
        existing = next(
            (contact for contact in candidates if re.sub(r"\D", "", clean(contact["phone"])) == digits),
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
            SET full_name=?, job_title=?, email=?, phone=?,
                source=?, last_seen_at=?, updated_at=?
            WHERE id=?
            """,
            (
                effective["full_name"] or None,
                effective["job_title"] or None,
                effective["email"] or None,
                effective["phone"] or None,
                effective["source"] or None,
                now,
                now,
                existing["id"],
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
    source_list = source_urls(row.get("source_urls", ""))
    conn.execute(
        """
        INSERT INTO company_identity_audit_log (
            action, target_company_id, new_values_json, reason, confidence,
            performed_by, performed_at
        ) VALUES ('profile_updated',?,?,?,?,?,?)
        """,
        (
            company_id,
            json.dumps({"company_fields": updates, "source_urls": source_list}),
            "Verified contact enrichment import",
            {"high": 95, "medium": 80, "low": 60}.get(
                clean(row.get("research_confidence")).casefold(), 60
            ),
            "contact_enrichment_import",
            now_iso(),
        ),
    )


def write_rejections(path: Path, rejected: list[dict]) -> Path:
    target = path.with_name(path.stem + "_rejected.csv")
    fields = list(rejected[0].keys()) if rejected else ["company_id", "rejection_reasons"]
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rejected)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    path = args.csv_file.resolve()
    if not path.is_file():
        parser.error(f"file not found: {path}")

    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    conn = get_connection()
    accepted = []
    rejected = []
    skipped = []
    for row in rows:
        company, reasons = validate_row(conn, row)
        if reasons:
            rejected.append({**row, "rejection_reasons": " | ".join(reasons)})
            continue
        status = clean(row.get("research_status")).casefold()
        if status in {"not found", "ambiguous"}:
            skipped.append(row)
            continue
        accepted.append((company, row))

    print(f"Input rows: {len(rows):,}")
    print(f"Validated for import: {len(accepted):,}")
    print(f"Skipped Not Found/Ambiguous: {len(skipped):,}")
    print(f"Rejected: {len(rejected):,}")

    rejection_path = write_rejections(path, rejected)
    print(f"Rejection report: {rejection_path}")

    if not args.apply:
        print("DRY RUN ONLY — no database changes were made.")
        print("Run again with --apply after reviewing the rejection report.")
        conn.close()
        return 1 if rejected else 0

    backup = backup_database()
    print(f"Database backup: {backup}")

    company_updates = contacts_created = contacts_updated = unchanged_rows = 0
    try:
        for company, row in accepted:
            updates = update_company(conn, company, row, args.overwrite)
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
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.close()
    print(f"Company profiles updated: {company_updates:,}")
    print(f"Named contacts created: {contacts_created:,}")
    print(f"Named contacts updated: {contacts_updated:,}")
    print(f"Rows already current: {unchanged_rows:,}")
    print("IMPORT COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
