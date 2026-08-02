"""CLI: create a CorridorIQ employee account.

    python -m pipeline.auth.create_user --email jane@corridoriq.com \
        --first Jane --last Doe --role sales_representative

If no password is supplied a secure temporary one is generated and printed
once. The account is forced to change it on first login. Passwords are never
committed or stored in plaintext.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from pipeline.auth import passwords
from pipeline.auth.rbac import ROLES
from pipeline.auth.seed import DEFAULT_ORG_SLUG, seed_auth
from pipeline.auth.service import create_user
from pipeline.db.database import init_db


def _resolve_org(conn, slug: str) -> int:
    row = conn.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()
    if row is None:
        raise SystemExit(f"Organization not found: {slug}")
    return row["id"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a CorridorIQ user account.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--first", default="", help="First name")
    parser.add_argument("--last", default="", help="Last name")
    parser.add_argument("--phone", default=None)
    parser.add_argument("--org", default=DEFAULT_ORG_SLUG, help="Organization slug")
    parser.add_argument("--role", action="append", choices=sorted(ROLES.keys()),
                        help="Role (repeatable). Default: sales_representative")
    parser.add_argument("--password", default=None,
                        help="Temporary password (omit to auto-generate securely)")
    parser.add_argument("--no-force-change", action="store_true",
                        help="Do not force a password change on first login")
    args = parser.parse_args(argv)

    conn = init_db()
    seed_auth(conn)
    org_id = _resolve_org(conn, args.org)
    roles = args.role or ["sales_representative"]

    password = args.password
    generated = False
    if not password:
        # Offer interactive entry, else generate.
        if sys.stdin and sys.stdin.isatty():
            entered = getpass.getpass("Temporary password (blank to auto-generate): ")
            if entered:
                password = entered
        if not password:
            password = passwords.generate_temp_password()
            generated = True

    try:
        user_id = create_user(
            conn, organization_id=org_id, email=args.email, password=password,
            first_name=args.first, last_name=args.last, phone=args.phone,
            role_names=roles, must_change_password=not args.no_force_change,
        )
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}")

    print(f"Created user #{user_id}: {args.email}")
    print(f"  Organization: {args.org}")
    print(f"  Roles: {', '.join(roles)}")
    print(f"  Must change password on first login: {not args.no_force_change}")
    if generated:
        print("\n  Temporary password (shown once — store securely, do not commit):")
        print(f"    {password}\n")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
