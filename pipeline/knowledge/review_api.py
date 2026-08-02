"""Lightweight local API for the Municipal Knowledge review dashboard.

Serves the prioritized review queue, impact previews, approve/reject/batch
actions, deprecation, and the audit log against SQLite. Run:

    python -m pipeline.knowledge.review_api

Default: http://127.0.0.1:8765

Nothing here changes scoring weights or the publish threshold — it only
governs how unknown mappings are triaged and promoted.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pipeline.db.database import init_db
from pipeline.knowledge.engine import KnowledgeEngine, export_knowledge_snapshot
from pipeline.knowledge.impact import preview_impact

HOST = "127.0.0.1"
PORT = 8765


def _refresh_static_export(conn):
    from pipeline.config.settings import DATA_EXPORTS_DIR

    DATA_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_EXPORTS_DIR / "knowledge_queue.json").write_text(
        json.dumps(export_knowledge_snapshot(conn), indent=2), encoding="utf-8"
    )


def _filter_queue(items: list[dict], qs: dict) -> list[dict]:
    kind = (qs.get("kind") or [None])[0]
    muni = (qs.get("municipality") or [None])[0]
    tier = (qs.get("tier") or [None])[0]
    search = (qs.get("q") or [None])[0]
    out = items
    if kind:
        out = [i for i in out if i["kind"] == kind]
    if muni:
        out = [i for i in out if (i.get("municipality_slug") or "") == muni]
    if tier:
        out = [i for i in out if (i.get("priority_tier") or "") == tier]
    if search:
        s = search.lower()
        out = [
            i
            for i in out
            if s in (i.get("raw_value") or "").lower()
            or s in (i.get("description") or "").lower()
        ]
    return out


class KnowledgeHandler(BaseHTTPRequestHandler):
    engine = KnowledgeEngine()

    def log_message(self, fmt, *args):
        print(f"[knowledge-api] {self.address_string()} {fmt % args}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        conn = init_db()
        try:
            if path == "/api/health":
                return self._json(200, {"ok": True})
            if path == "/api/queue":
                snapshot = export_knowledge_snapshot(conn)
                items = _filter_queue(snapshot["queue"], qs)
                limit = qs.get("limit")
                if limit:
                    try:
                        items = items[: int(limit[0])]
                    except ValueError:
                        pass
                return self._json(
                    200,
                    {
                        "generated_at": snapshot["generated_at"],
                        "kb_version": snapshot.get("kb_version"),
                        "counts": snapshot["counts"],
                        "priority_tiers": snapshot.get("priority_tiers"),
                        "items": items,
                    },
                )
            if path == "/api/snapshot":
                return self._json(200, export_knowledge_snapshot(conn))
            if path == "/api/municipalities":
                rows = conn.execute(
                    """
                    SELECT slug, name, state, permit_source, active, last_synchronization
                    FROM municipalities ORDER BY name
                    """
                ).fetchall()
                return self._json(200, {"municipalities": [dict(r) for r in rows]})
            if path == "/api/audit":
                limit = int((qs.get("limit") or ["100"])[0])
                rows = conn.execute(
                    "SELECT * FROM mapping_audit_log ORDER BY created_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return self._json(200, {"audit": [dict(r) for r in rows]})
            return self._json(404, {"error": "not found"})
        finally:
            conn.close()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid JSON"})

        preview_match = re.fullmatch(r"/api/queue/(\d+)/preview", path)
        approve_match = re.fullmatch(r"/api/queue/(\d+)/approve", path)
        reject_match = re.fullmatch(r"/api/queue/(\d+)/reject", path)
        conn = init_db()
        try:
            self.engine.reload(conn)

            if preview_match:
                queue_id = int(preview_match.group(1))
                row = conn.execute(
                    "SELECT * FROM knowledge_review_queue WHERE id=?", (queue_id,)
                ).fetchone()
                if row is None:
                    return self._json(404, {"error": "queue item not found"})
                proposed = (
                    payload.get("proposed_mapping")
                    or payload.get("canonical_status")
                    or payload.get("trade")
                    or payload.get("category")
                    or row["suggested_interpretation"]
                    or ("unknown" if row["kind"] == "status" else "other")
                )
                try:
                    preview = preview_impact(
                        conn,
                        kind=row["kind"],
                        municipality_slug=row["municipality_slug"],
                        raw_value=row["raw_value"],
                        proposed_mapping=proposed,
                    )
                except Exception as exc:  # pragma: no cover
                    return self._json(500, {"error": str(exc)})
                return self._json(200, preview)

            if path == "/api/queue/batch/approve":
                try:
                    result = self.engine.approve_batch(
                        conn,
                        [int(i) for i in payload.get("ids", [])],
                        proposed_mapping=payload.get("proposed_mapping"),
                        mapping_confidence=payload.get("mapping_confidence"),
                        mapping_source=payload.get("mapping_source"),
                        reviewed_by=payload.get("reviewed_by"),
                        activate=payload.get("activate"),
                        reason=payload.get("reason"),
                        reprocess=payload.get("reprocess", True),
                        regenerate_reports=payload.get("regenerate_reports", False),
                    )
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
                _refresh_static_export(conn)
                return self._json(200, result)

            if approve_match:
                queue_id = int(approve_match.group(1))
                try:
                    result = self.engine.approve_queue_item(
                        conn,
                        queue_id,
                        canonical_status=payload.get("canonical_status"),
                        trade=payload.get("trade"),
                        project_category=payload.get("project_category"),
                        friendly_name=payload.get("friendly_name"),
                        category=payload.get("category"),
                        suggested_products=payload.get("suggested_products"),
                        notes=payload.get("notes"),
                        mapping_confidence=payload.get("mapping_confidence"),
                        mapping_source=payload.get("mapping_source"),
                        reviewed_by=payload.get("reviewed_by"),
                        activate=payload.get("activate"),
                        reason=payload.get("reason"),
                        reprocess=payload.get("reprocess", True),
                        regenerate_reports=payload.get("regenerate_reports", False),
                    )
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
                _refresh_static_export(conn)
                return self._json(200, result)

            if reject_match:
                queue_id = int(reject_match.group(1))
                try:
                    result = self.engine.reject_queue_item(
                        conn,
                        queue_id,
                        notes=payload.get("notes"),
                        reviewed_by=payload.get("reviewed_by"),
                        reason=payload.get("reason"),
                    )
                except ValueError as exc:
                    return self._json(400, {"error": str(exc)})
                _refresh_static_export(conn)
                return self._json(200, result)

            if path == "/api/priority/recompute":
                from pipeline.knowledge.priority import recompute_priorities

                n = recompute_priorities(conn)
                _refresh_static_export(conn)
                return self._json(200, {"ok": True, "recomputed": n})

            return self._json(404, {"error": "not found"})
        finally:
            conn.close()


def main():
    server = ThreadingHTTPServer((HOST, PORT), KnowledgeHandler)
    print(f"Municipal Knowledge API listening on http://{HOST}:{PORT}")
    print(
        "Endpoints: GET /api/queue|/api/audit|/api/snapshot  "
        "POST /api/queue/<id>/preview|approve|reject  /api/queue/batch/approve"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
