"""Capture screenshots of the redesigned Sales Workspace for the Sprint 6
validation report. Seeds a throwaway DB, boots the API server, and drives a
headless browser at desktop and mobile widths.

Run:  python -m pipeline.reports.ui_screenshots
Output: reports/generated/screenshots/*.png
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from pipeline.auth import service as auth
from pipeline.auth.seed import seed_auth
from pipeline.config import settings
from pipeline.crm import service as crm

OUT = settings.REPORTS_GENERATED_DIR / "screenshots"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


def _seed(db_file: Path):
    c = sqlite3.connect(db_file)
    c.row_factory = sqlite3.Row
    c.executescript(settings.SCHEMA_PATH.read_text(encoding="utf-8"))
    c.execute("INSERT INTO jurisdictions (slug, name, state, status) VALUES "
              "('phoenix_az','Phoenix','AZ','connected'),('mesa_az','Mesa','AZ','connected')")
    seed_auth(c)
    org = c.execute("SELECT id FROM organizations WHERE slug='corridoriq'").fetchone()["id"]
    admin = auth.create_user(c, organization_id=org, email="demo.admin@corridoriq.com",
                             password="DemoAdmin123", role_names=["admin"], must_change_password=False)
    mgr = auth.create_user(c, organization_id=org, email="demo.manager@corridoriq.com",
                           password="DemoManager123", role_names=["sales_manager"], must_change_password=False)
    rep = auth.create_user(c, organization_id=org, email="demo.rep@corridoriq.com",
                           password="DemoRep123", role_names=["sales_representative"], must_change_password=False)
    rep2 = auth.create_user(c, organization_id=org, email="demo.rep2@corridoriq.com",
                            password="DemoRep2123", role_names=["sales_representative"], must_change_password=False)
    admin_ctx = auth.build_user_context(c, c.execute("SELECT * FROM users WHERE id=?", (admin,)).fetchone())
    rep_ctx = auth.build_user_context(c, c.execute("SELECT * FROM users WHERE id=?", (rep,)).fetchone())

    seeds = [
        ("Summit Mechanical", "PHOENIX", "Critical", 96, 4, 3),
        ("Copper State Plumbing", "MESA", "High", 82, 3, 2),
        ("Desert Ridge Builders", "SCOTTSDALE", "High", 78, 2, 1),
        ("Valley Commercial HVAC", "TEMPE", "Medium", 61, 1, 0),
        ("Ironwood Contractors", "CHANDLER", "Medium", 55, 2, 1),
        ("Saguaro Facilities", "GILBERT", "Low", 40, 0, 0),
    ]
    companies = []
    for i, (name, city, tier, score, active, last30) in enumerate(seeds):
        cur = c.execute("INSERT INTO companies (normalized_name, display_name, city, state, "
                        "main_phone, main_email, lifecycle_state, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,'active',?,?)",
                        (name.upper(), name, city, "AZ", "(602) 555-01" + str(10 + i),
                         "contact@" + name.split()[0].lower() + ".com", _now(), _now()))
        cid = cur.lastrowid
        companies.append(cid)
        c.execute("INSERT INTO company_intelligence (company_id, company_priority_tier, "
                  "company_priority_score, active_projects, projects_last_30_days, total_projects, "
                  "average_opportunity_score, highest_opportunity_score, municipality_count, "
                  "commercial_project_count, residential_project_count, latest_activity_date, "
                  "activity_trend, model_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'v1')",
                  (cid, tier, score, active, last30, active + 2, score - 8, min(99, score + 4),
                   1 + (i % 3), active, i % 2, _d(-i), "rising" if last30 else "steady"))
        c.execute("INSERT INTO company_roles (company_id, role_type, is_primary, source, confidence, created_at, updated_at) "
                  "VALUES (?,?,1,'permit',0.9,?,?)", (cid, "contractor", _now(), _now()))
        for j in range(active + 1):
            pcur = c.execute("INSERT INTO permits (jurisdiction, permit_number, permit_type, status, "
                             "description, job_address, city, state, contractor_company_id, issued_date, "
                             "valuation, first_seen_at, last_updated_at) VALUES "
                             "('phoenix_az',?,?,?,?,?,?, 'AZ',?,?,?,?,?)",
                             (f"CIQ-{cid}-{j}", "Commercial Alteration", "issued",
                              "Tenant improvement — mechanical, plumbing, and fixtures for a "
                              "retail build-out including water heater and backflow work.",
                              f"{100 + j} E Camelback Rd", city, cid, _d(-(j * 5 + 2)),
                              120000 + j * 25000, _now(), _now()))
            c.execute("INSERT INTO projects (permit_id, jurisdiction, contractor_company_id, "
                      "project_category, project_lifecycle, opportunity_score, opportunity_date, "
                      "opportunity_timing, estimated_material_value) VALUES "
                      "(?, 'phoenix_az', ?, 'commercial', ?, ?, ?, 'immediate', ?)",
                      (pcur.lastrowid, cid, ["permitting", "under_construction", "inspection"][j % 3],
                       max(30, score - j * 6), _d(-(j * 5 + 2)), 8000 + j * 1500))
    c.commit()

    # Assignments: first four to rep, last two to rep2.
    for cid in companies[:4]:
        crm.assign_company(c, admin_ctx, cid, rep)
    for cid in companies[4:]:
        crm.assign_company(c, admin_ctx, cid, rep2)

    # Activities + follow-ups for the rep's book of business.
    crm.create_activity(c, rep_ctx, companies[0], {"activity_type": "call",
        "activity_outcome": "spoke_with_contact", "subject": "Mike (PM)",
        "notes": "Discussed upcoming Camelback build-out. Wants pricing on water heaters.",
        "next_followup_at": _d(0) + "T09:30:00"})
    crm.create_activity(c, rep_ctx, companies[0], {"activity_type": "quote_request",
        "activity_outcome": "quote_requested", "subject": "Water heaters x6",
        "notes": "Sent to estimating."})
    crm.create_activity(c, rep_ctx, companies[1], {"activity_type": "call",
        "activity_outcome": "left_voicemail", "subject": "Front desk",
        "notes": "Left a voicemail, will retry.", "next_followup_at": _d(-2) + "T10:00:00"})
    crm.create_activity(c, rep_ctx, companies[2], {"activity_type": "meeting",
        "activity_outcome": "appointment_set", "subject": "Site walk",
        "notes": "Meeting booked for the Scottsdale project.", "next_followup_at": _d(1) + "T14:00:00"})
    # company[3] intentionally never contacted.

    # Tasks across buckets for the rep.
    for title, cid, due, status in [
        ("Call Summit Mechanical about quote", companies[0], _d(0) + "T11:00:00", "open"),
        ("Follow up on Copper State voicemail", companies[1], _d(-1) + "T09:00:00", "open"),
        ("Prep proposal for Desert Ridge", companies[2], _d(2) + "T15:00:00", "open"),
        ("Send intro email to Valley Commercial", companies[3], _d(3) + "T09:00:00", "open"),
        ("Log site-walk notes", companies[2], _d(-3) + "T09:00:00", "completed"),
    ]:
        c.execute("INSERT INTO crm_tasks (organization_id, company_id, assigned_user_id, "
                  "created_by_user_id, title, priority, due_at, status, completed_at, created_at, updated_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (org, cid, rep, rep, title, "high" if "quote" in title.lower() else "normal",
                   due, status, _now() if status == "completed" else None, _now(), _now()))
    c.commit()
    c.close()
    return {"company": companies[0]}


def _factory_for(db_file: Path):
    def factory():
        cc = sqlite3.connect(db_file)
        cc.row_factory = sqlite3.Row
        cc.execute("PRAGMA foreign_keys = ON")
        return cc
    return factory


def capture():
    from playwright.sync_api import sync_playwright
    import pipeline.api.server as server_mod

    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    db_file = tmp / "demo.db"
    info = _seed(db_file)
    server_mod._factory = _factory_for(db_file)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.ApiHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)
    base = f"http://127.0.0.1:{port}"

    pages = [
        ("dashboard", "/sales-dashboard.html"),
        ("my-companies", "/my-companies.html"),
        ("company-profile", f"/sales-company-profile.html?id={info['company']}"),
        ("opportunities", "/opportunities.html"),
        ("tasks", "/my-tasks.html"),
        ("activity", "/activity.html"),
        ("reports", "/reports.html"),
        ("team", "/team-dashboard.html"),
        ("assignments", "/assignments.html"),
        ("users", "/user-management.html"),
    ]
    written = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, w, h in [("desktop", 1440, 900), ("mobile", 390, 844)]:
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            page.goto(base + "/login.html", wait_until="networkidle")
            page.fill("#email", "demo.admin@corridoriq.com")
            page.fill("#password", "DemoAdmin123")
            page.click("#loginBtn")
            page.wait_for_url("**/sales-dashboard.html", timeout=10000)
            page.wait_for_timeout(700)
            for name, path in pages:
                page.goto(base + path, wait_until="networkidle")
                page.wait_for_timeout(900)
                fp = OUT / f"{name}-{label}.png"
                page.screenshot(path=str(fp), full_page=(label == "desktop"))
                written.append(fp.name)
            # Login screen itself (unauthenticated) once.
            if label == "desktop":
                lc = ctx.new_page()
                lc.goto(base + "/login.html", wait_until="networkidle")
                lc.wait_for_timeout(400)
                lc.screenshot(path=str(OUT / "login-desktop.png"))
                written.append("login-desktop.png")
                lc.close()
            ctx.close()
        browser.close()
    srv.shutdown()
    print(f"Wrote {len(written)} screenshots to {OUT}")
    for w in written:
        print("  " + w)
    return written


if __name__ == "__main__":
    capture()
