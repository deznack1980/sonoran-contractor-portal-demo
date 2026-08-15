"""Safety tests for the one-command contractor production preflight."""

from __future__ import annotations

import json
import sqlite3

from pipeline.config.settings import SCHEMA_PATH
from scripts.prepare_contractor_rebuild import prepare


def test_preflight_creates_verified_backup_and_audit_without_rebuilding(tmp_path):
    source_path = tmp_path / "corridoriq.db"
    conn = sqlite3.connect(source_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO jurisdictions(slug,name,state,status) VALUES ('phoenix_az','Phoenix','AZ','connected')")
    conn.execute("INSERT INTO contractors(name,normalized_name,updated_at) VALUES ('Legacy Architect','LEGACY ARCHITECT','now')")
    conn.commit()
    before = source_path.read_bytes()
    conn.close()

    result = prepare(source_path, tmp_path / "outputs")

    assert source_path.read_bytes() == before
    assert result["backup"].is_file()
    assert result["audit_json"].is_file()
    assert result["audit_markdown"].is_file()
    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    assert manifest["rebuild_executed"] is False
    assert len(manifest["backup_sha256"]) == 64
    assert result["snapshot"]["existing_contractor_count"] == 1
    assert result["snapshot"]["database_quick_check"] == "ok"
    backup = sqlite3.connect(result["backup"])
    assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert backup.execute("SELECT name FROM contractors").fetchone()[0] == "Legacy Architect"
    backup.close()
