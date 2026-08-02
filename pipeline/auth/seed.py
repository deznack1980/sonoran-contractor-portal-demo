"""Idempotent seed of the default organization, roles, and permissions."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pipeline.auth.rbac import PERMISSIONS, ROLES

DEFAULT_ORG_SLUG = "corridoriq"
DEFAULT_ORG_NAME = "CorridorIQ"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_default_organization(conn: sqlite3.Connection) -> int:
    now = _now()
    conn.execute(
        "INSERT INTO organizations (name, slug, is_active, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?) ON CONFLICT(slug) DO NOTHING",
        (DEFAULT_ORG_NAME, DEFAULT_ORG_SLUG, now, now),
    )
    row = conn.execute(
        "SELECT id FROM organizations WHERE slug=?", (DEFAULT_ORG_SLUG,)
    ).fetchone()
    return row["id"]


def seed_auth(conn: sqlite3.Connection) -> None:
    """Seed org + permissions + roles + role_permissions. Safe to re-run."""
    now = _now()
    ensure_default_organization(conn)

    for key, desc in PERMISSIONS.items():
        conn.execute(
            "INSERT INTO permissions (permission_key, description, created_at) "
            "VALUES (?, ?, ?) ON CONFLICT(permission_key) DO UPDATE SET "
            "description=excluded.description",
            (key, desc, now),
        )

    perm_ids = {
        r["permission_key"]: r["id"]
        for r in conn.execute("SELECT id, permission_key FROM permissions")
    }

    for name, spec in ROLES.items():
        conn.execute(
            "INSERT INTO roles (name, display_name, description, is_system_role, "
            "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET display_name=excluded.display_name, "
            "description=excluded.description, updated_at=excluded.updated_at",
            (name, spec["display_name"], spec["description"], now, now),
        )
        role_id = conn.execute("SELECT id FROM roles WHERE name=?", (name,)).fetchone()["id"]
        # Reset this role's permission set to match the catalog (system roles).
        conn.execute("DELETE FROM role_permissions WHERE role_id=?", (role_id,))
        for pkey in spec["permissions"]:
            pid = perm_ids.get(pkey)
            if pid is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) "
                    "VALUES (?, ?)",
                    (role_id, pid),
                )
    conn.commit()


if __name__ == "__main__":
    from pipeline.db.database import init_db

    c = init_db()
    seed_auth(c)
    print("Seeded organizations, roles, and permissions.")
    c.close()
