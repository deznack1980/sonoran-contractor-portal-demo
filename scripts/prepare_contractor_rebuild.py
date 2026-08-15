"""Create a verified SQLite backup and read-only contractor preflight audit.

This command deliberately has no rebuild/apply option. It is the mandatory
production gate before an operator separately authorizes contractor rebuilding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Support both documented Windows invocation styles:
# ``python scripts/prepare_contractor_rebuild.py`` and ``python -m scripts...``.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.config import settings
from pipeline.contractors.audit import contractor_audit_snapshot, render_markdown


def _readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA query_only=ON")
    return conn


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(database: Path, output_root: Path | None = None) -> dict:
    """Back up *database*, audit the immutable copy, and return artifact paths."""
    database = database.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"CorridorIQ database not found: {database}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_dir = database.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"corridoriq.pre-contractor-rebuild.{stamp}.db"
    if backup.exists():
        raise FileExistsError(f"Refusing to overwrite existing backup: {backup}")

    source = _readonly(database)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
        target.commit()
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup integrity check failed")
    finally:
        target.close()
        source.close()

    audit_conn = _readonly(backup)
    try:
        snapshot = contractor_audit_snapshot(audit_conn)
    finally:
        audit_conn.close()
    if snapshot["database_quick_check"] != "ok" or snapshot["foreign_key_issue_count"]:
        raise RuntimeError("Audit integrity checks failed; production rebuild remains blocked")

    output_dir = (output_root or settings.PROJECT_ROOT / "outputs" / "contractor-rebuild-audits") / stamp
    output_dir.mkdir(parents=True, exist_ok=False)
    report_json = output_dir / "before-audit.json"
    report_md = output_dir / "before-audit.md"
    manifest = output_dir / "backup-manifest.json"
    report_json.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    report_md.write_text(
        render_markdown(snapshot)
        + "\n## Backup proof\n\n"
        + f"- File: `{backup}`\n"
        + f"- Size: {backup.stat().st_size:,} bytes\n"
        + f"- SHA-256: `{_sha256(backup)}`\n"
        + "- Status: Review required; rebuild not executed.\n",
        encoding="utf-8",
    )
    manifest.write_text(json.dumps({
        "source_database": str(database), "backup": str(backup),
        "backup_bytes": backup.stat().st_size, "backup_sha256": _sha256(backup),
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audit_json": str(report_json), "audit_markdown": str(report_md),
        "rebuild_executed": False,
    }, indent=2) + "\n", encoding="utf-8")
    return {"backup": backup, "audit_json": report_json,
            "audit_markdown": report_md, "manifest": manifest, "snapshot": snapshot}


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up and audit before contractor rebuilding")
    parser.add_argument("--database", type=Path, default=settings.DB_PATH)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.database, args.output_root)
    except Exception as exc:
        print(f"BLOCKED: {exc}")
        print("No contractor rebuild was executed.")
        return 2
    snapshot = result["snapshot"]
    print("CONTRACTOR REBUILD PREFLIGHT COMPLETE")
    print(f"Backup: {result['backup']}")
    print(f"Audit report: {result['audit_markdown']}")
    print(f"Verified contractors: {snapshot['verified_contractor_count']:,}")
    print(f"Projects requiring relink: {snapshot['projects_requiring_compatibility_relink']:,}")
    print(f"Projects left unresolved: {snapshot['projects_left_unresolved']:,}")
    print("STOPPED FOR REVIEW: No contractor rebuild was executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
