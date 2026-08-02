"""Local Company Intelligence API (stdlib http.server, no dependencies).

    python -m pipeline.company_resolution.api

Read endpoints are paginated. Write endpoints (match approve/reject) are
auditable and refresh the static export so the UI stays consistent offline.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pipeline.company_resolution.export import export_all
from pipeline.company_resolution.queries import (
    company_metrics,
    company_permits,
    company_projects,
    company_timeline,
    get_company,
    list_companies,
    list_match_review,
)
from pipeline.company_resolution.review import approve_match, reject_match
from pipeline.config.settings import COMPANY_API_PORT
from pipeline.db.database import init_db

HOST = "127.0.0.1"
PORT = COMPANY_API_PORT

_COMPANY_RE = re.compile(r"^/api/companies/(\d+)(?:/(projects|permits|timeline|metrics))?$")
_REVIEW_RE = re.compile(r"^/api/company-match-review/(\d+)/(approve|reject)$")


class CompanyHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quieter console
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _flatten(self, qs: dict) -> dict:
        return {k: (v[0] if isinstance(v, list) and v else v) for k, v in qs.items()}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        filters = self._flatten(parse_qs(parsed.query))
        conn = init_db()
        try:
            if path == "/api/health":
                return self._json(200, {"ok": True, "service": "company-intelligence"})
            if path == "/api/companies":
                return self._json(200, list_companies(conn, filters))
            if path == "/api/company-match-review":
                return self._json(200, list_match_review(conn, filters))
            m = _COMPANY_RE.match(path)
            if m:
                cid = int(m.group(1))
                sub = m.group(2)
                if sub is None:
                    data = get_company(conn, cid)
                    return self._json(200 if data else 404, data or {"error": "not found"})
                if sub == "projects":
                    active = (filters.get("active") or "") in ("1", "true", "yes")
                    return self._json(200, {"items": company_projects(conn, cid, active)})
                if sub == "permits":
                    return self._json(200, {"items": company_permits(conn, cid)})
                if sub == "timeline":
                    return self._json(200, {"items": company_timeline(conn, cid)})
                if sub == "metrics":
                    return self._json(200, company_metrics(conn, cid) or {})
            return self._json(404, {"error": "unknown route"})
        except Exception as exc:  # pragma: no cover
            return self._json(500, {"error": str(exc)})
        finally:
            conn.close()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        conn = init_db()
        try:
            m = _REVIEW_RE.match(path)
            if m:
                qid = int(m.group(1))
                action = m.group(2)
                reviewer = body.get("reviewed_by", "reviewer")
                notes = body.get("notes")
                if action == "approve":
                    result = approve_match(conn, qid, reviewed_by=reviewer, notes=notes)
                else:
                    result = reject_match(conn, qid, reviewed_by=reviewer, notes=notes,
                                          create_separate=bool(body.get("create_separate")))
                if result.get("ok"):
                    export_all(conn)
                return self._json(200 if result.get("ok") else 400, result)
            return self._json(404, {"error": "unknown route"})
        except Exception as exc:  # pragma: no cover
            return self._json(500, {"error": str(exc)})
        finally:
            conn.close()


def main():
    server = ThreadingHTTPServer((HOST, PORT), CompanyHandler)
    print(f"Company Intelligence API on http://{HOST}:{PORT}")
    print("  GET  /api/companies?q=&role=&tier=&municipality=&active=&page=&page_size=")
    print("  GET  /api/companies/<id>[/projects|/permits|/timeline|/metrics]")
    print("  GET  /api/company-match-review?state=pending")
    print("  POST /api/company-match-review/<id>/approve|reject")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
