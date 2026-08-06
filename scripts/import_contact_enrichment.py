"""Validate and import researched company contacts into CorridorIQ.

The importer is dry-run by default. Applied imports are blocked when any row is
rejected, create a SQLite backup first, preserve existing canonical company
fields unless --overwrite is explicitly supplied, and deduplicate contacts.

Usage:
    python scripts/import_contact_enrichment.py path\\to\\completed_batch.csv
    python scripts/import_contact_enrichment.py path\\to\\completed_batch.csv --apply
    python scripts/import_contact_enrichment.py path\\to\\completed_batch.csv --apply --overwrite
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.contact_enrichment import (  # noqa: E402
    analyze_rows,
    apply_rows,
    backup_database,
    parse_csv_bytes,
)
from pipeline.db.database import get_connection  # noqa: E402


def write_rejections(path: Path, rejected: list[dict]) -> Path:
    target = path.with_name(path.stem + "_rejected.csv")
    rows = [
        {**item["row"], "rejection_reasons": " | ".join(item["reasons"])}
        for item in rejected
    ]
    fields = list(rows[0].keys()) if rows else ["company_id", "rejection_reasons"]
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
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

    try:
        rows = parse_csv_bytes(path.name, path.read_bytes())
    except ValueError as exc:
        parser.error(str(exc))

    conn = get_connection()
    analysis = analyze_rows(conn, rows)
    counts = analysis["counts"]
    print(f"Input rows: {counts['input_rows']:,}")
    print(f"Validated for import: {counts['importable']:,}")
    print(f"Skipped Not Found/Ambiguous: {counts['skipped']:,}")
    print(f"Rejected: {counts['rejected']:,}")

    rejection_path = write_rejections(path, analysis["rejected"])
    print(f"Rejection report: {rejection_path}")

    if not args.apply:
        print("DRY RUN ONLY — no database changes were made.")
        print("Run again with --apply after reviewing the rejection report.")
        conn.close()
        return 1 if analysis["rejected"] else 0
    if analysis["rejected"]:
        print("IMPORT BLOCKED — fix or remove rejected rows, then run the dry run again.")
        conn.close()
        return 1
    if not analysis["accepted"]:
        print("IMPORT BLOCKED — there are no validated contact rows to import.")
        conn.close()
        return 1

    backup = backup_database(conn)
    print(f"Database backup: {backup}")
    try:
        stats = apply_rows(conn, analysis["accepted"], overwrite=args.overwrite)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.close()
    print(f"Company profiles updated: {stats['company_profiles_updated']:,}")
    print(f"Named contacts created: {stats['contacts_created']:,}")
    print(f"Named contacts updated: {stats['contacts_updated']:,}")
    print(f"Rows already current: {stats['rows_already_current']:,}")
    print("IMPORT COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
