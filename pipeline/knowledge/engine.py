"""Dictionary lookups, unknown-queue enqueue, and approve/reject workflow.

Hard-coded bootstrap rules remain a fallback until a dictionary entry exists.
Approved dictionary rows win. Unknown values are queued for review — never
silently promoted to permanent mappings.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pipeline.config.settings import (
    KNOWLEDGE_MAPPING_SOURCES,
    KNOWLEDGE_MIN_ACTIVE_CONFIDENCE,
)

# Global municipality slug used for rules that apply to every city.
GLOBAL_MUNI = "_global_"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _muni_slug(value: str | None) -> str:
    if not value:
        return GLOBAL_MUNI
    text = str(value).strip().lower().replace(" ", "_")
    if text.endswith("_az") or text == GLOBAL_MUNI:
        return text
    # Allow bare city names ("phoenix") and jurisdiction slugs.
    return text


@dataclass(frozen=True)
class DictHit:
    canonical: str
    rule: str
    confidence: float | None
    human_reviewed: bool
    extra: dict[str, Any]


class KnowledgeEngine:
    """In-memory cache of municipal dictionaries backed by SQLite."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        self._status: dict[tuple[str, str], dict] = {}
        self._codes: dict[tuple[str, str], dict] = {}
        self._keywords: list[dict] = []
        self._products: dict[str, dict] = {}
        # key -> payload with occurrence_delta for this run
        self._pending_queue: dict[tuple[str, str, str], dict] = {}
        if conn is not None:
            self.reload(conn)

    def reload(self, conn: sqlite3.Connection) -> None:
        self._status.clear()
        self._codes.clear()
        self._keywords.clear()
        self._products = {}

        # Only ACTIVE mappings affect production normalization. Draft /
        # rejected / deprecated entries are preserved but never applied.
        active = "COALESCE(lifecycle_state,'active') = 'active'"

        for row in conn.execute(
            f"SELECT * FROM status_dictionary WHERE {active}"
        ):
            key = (_muni_slug(row["municipality_slug"]), str(row["raw_status"]).strip().lower())
            self._status[key] = dict(row)

        for row in conn.execute(
            f"SELECT * FROM permit_code_dictionary WHERE {active}"
        ):
            key = (
                _muni_slug(row["municipality_slug"]),
                str(row["raw_permit_code"]).strip().lower(),
            )
            self._codes[key] = dict(row)

        self._keywords = [dict(r) for r in conn.execute(
            f"SELECT * FROM keyword_dictionary WHERE {active} ORDER BY weight DESC, keyword"
        )]
        self._products = {
            r["product_family"]: dict(r)
            for r in conn.execute("SELECT * FROM product_dictionary")
        }

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def lookup_status(self, raw_status: str | None, municipality: str | None) -> DictHit | None:
        if raw_status is None or not str(raw_status).strip():
            return None
        raw_key = str(raw_status).strip().lower()
        muni = _muni_slug(municipality)
        for key in ((muni, raw_key), (GLOBAL_MUNI, raw_key)):
            row = self._status.get(key)
            if row:
                return DictHit(
                    canonical=row["canonical_status"],
                    rule=row.get("mapping_rule")
                    or f"dictionary:status:{key[0]}:{raw_key}->{row['canonical_status']}",
                    confidence=row.get("confidence"),
                    human_reviewed=bool(row.get("human_reviewed")),
                    extra=row,
                )
        return None

    def lookup_permit_code(
        self, raw_code: str | None, municipality: str | None
    ) -> DictHit | None:
        if raw_code is None or not str(raw_code).strip():
            return None
        raw_key = str(raw_code).strip().lower()
        muni = _muni_slug(municipality)
        for key in ((muni, raw_key), (GLOBAL_MUNI, raw_key)):
            row = self._codes.get(key)
            if row:
                # Prefer trade as normalized type signal; fall back to category.
                canonical = (row.get("trade") or row.get("project_category") or "other")
                canonical = str(canonical).strip().lower()
                return DictHit(
                    canonical=canonical,
                    rule=f"dictionary:permit_code:{key[0]}:{raw_key}->{canonical}",
                    confidence=row.get("ai_confidence"),
                    human_reviewed=bool(row.get("human_reviewed")),
                    extra=row,
                )
        return None

    def match_keywords(self, text: str | None) -> list[dict]:
        hay = (text or "").lower()
        if not hay:
            return []
        hits = []
        for row in self._keywords:
            kw = (row.get("keyword") or "").lower()
            if kw and kw in hay:
                hits.append(row)
        return hits

    # ------------------------------------------------------------------
    # Unknown queue
    # ------------------------------------------------------------------
    def enqueue_unknown(
        self,
        *,
        kind: str,
        municipality: str | None,
        raw_value: str,
        description: str | None = None,
        suggested_interpretation: str | None = None,
        confidence: float | None = None,
    ) -> None:
        """Buffer an unresolved review record (flushed with ``flush_queue``)."""
        if not raw_value or not str(raw_value).strip():
            return
        muni = _muni_slug(municipality) if municipality else GLOBAL_MUNI
        raw = str(raw_value).strip()
        key = (kind, muni, raw.lower())
        existing = self._pending_queue.get(key)
        if existing:
            existing["occurrence_delta"] += 1
            if description and not existing.get("description"):
                existing["description"] = description
            return
        self._pending_queue[key] = {
            "kind": kind,
            "municipality_slug": muni,
            "raw_value": raw,
            "description": description,
            "suggested_interpretation": suggested_interpretation,
            "confidence": confidence,
            "occurrence_delta": 1,
        }

    def flush_queue(self, conn: sqlite3.Connection) -> int:
        """Upsert buffered unknowns into knowledge_review_queue."""
        if not self._pending_queue:
            return 0
        now = _utcnow()
        written = 0
        for item in self._pending_queue.values():
            muni = item["municipality_slug"]
            delta = int(item.get("occurrence_delta") or 1)
            conn.execute(
                """
                INSERT INTO knowledge_review_queue (
                    kind, municipality_slug, raw_value, description,
                    suggested_interpretation, confidence, occurrence_count,
                    first_seen, last_seen, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(kind, municipality_slug, raw_value) DO UPDATE SET
                    occurrence_count = knowledge_review_queue.occurrence_count + excluded.occurrence_count,
                    last_seen = excluded.last_seen,
                    description = COALESCE(excluded.description, knowledge_review_queue.description),
                    suggested_interpretation = COALESCE(
                        excluded.suggested_interpretation,
                        knowledge_review_queue.suggested_interpretation
                    ),
                    confidence = COALESCE(excluded.confidence, knowledge_review_queue.confidence)
                WHERE knowledge_review_queue.status = 'pending'
                """,
                (
                    item["kind"],
                    muni,
                    item["raw_value"],
                    item.get("description"),
                    item.get("suggested_interpretation"),
                    item.get("confidence"),
                    delta,
                    now,
                    now,
                ),
            )
            written += 1
        self._pending_queue.clear()
        conn.commit()
        return written

    # ------------------------------------------------------------------
    # Knowledge-base version (reproducibility)
    # ------------------------------------------------------------------
    @staticmethod
    def get_kb_version(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT value FROM knowledge_meta WHERE key='kb_version'"
        ).fetchone()
        if row and row["value"]:
            try:
                return int(row["value"])
            except (TypeError, ValueError):
                return 1
        return 1

    @staticmethod
    def bump_kb_version(conn: sqlite3.Connection) -> int:
        version = KnowledgeEngine.get_kb_version(conn) + 1
        conn.execute(
            """
            INSERT INTO knowledge_meta (key, value, updated_at)
            VALUES ('kb_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (str(version), _utcnow()),
        )
        return version

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------
    @staticmethod
    def write_audit(
        conn: sqlite3.Connection,
        *,
        kind: str,
        municipality_slug: str | None,
        raw_value: str,
        previous_mapping: str | None,
        new_mapping: str | None,
        action: str,
        reviewer: str | None,
        reason: str | None,
        mapping_confidence: float | None,
        mapping_source: str | None,
        affected_permit_count: int | None,
        score_impact_summary: str | None,
        kb_version: int | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO mapping_audit_log (
                kind, municipality_slug, raw_value, previous_mapping, new_mapping,
                action, reviewer, reason, mapping_confidence, mapping_source,
                affected_permit_count, score_impact_summary, kb_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                municipality_slug,
                raw_value,
                previous_mapping,
                new_mapping,
                action,
                reviewer,
                reason,
                mapping_confidence,
                mapping_source,
                affected_permit_count,
                score_impact_summary,
                kb_version,
                _utcnow(),
            ),
        )

    def _previous_mapping(
        self, conn: sqlite3.Connection, kind: str, muni: str, raw_value: str
    ) -> str | None:
        raw_key = str(raw_value).strip()
        if kind == "status":
            r = conn.execute(
                "SELECT canonical_status FROM status_dictionary "
                "WHERE municipality_slug=? AND raw_status=? COLLATE NOCASE",
                (muni, raw_key),
            ).fetchone()
            return r["canonical_status"] if r else None
        if kind == "permit_code":
            r = conn.execute(
                "SELECT trade FROM permit_code_dictionary "
                "WHERE municipality_slug=? AND raw_permit_code=? COLLATE NOCASE",
                (muni, raw_key),
            ).fetchone()
            return r["trade"] if r else None
        if kind == "keyword":
            r = conn.execute(
                "SELECT category FROM keyword_dictionary WHERE keyword=? COLLATE NOCASE",
                (raw_key.lower(),),
            ).fetchone()
            return r["category"] if r else None
        return None

    # ------------------------------------------------------------------
    # Approve / reject
    # ------------------------------------------------------------------
    def approve_queue_item(
        self,
        conn: sqlite3.Connection,
        queue_id: int,
        *,
        canonical_status: str | None = None,
        trade: str | None = None,
        project_category: str | None = None,
        friendly_name: str | None = None,
        category: str | None = None,
        suggested_products: str | None = None,
        notes: str | None = None,
        mapping_confidence: float | None = None,
        mapping_source: str | None = None,
        reviewed_by: str | None = None,
        activate: bool | None = None,
        reason: str | None = None,
        reprocess: bool = True,
        regenerate_reports: bool = False,
    ) -> dict:
        row = conn.execute(
            "SELECT * FROM knowledge_review_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"queue item {queue_id} not found")
        if row["status"] != "pending":
            raise ValueError(f"queue item {queue_id} is already {row['status']}")

        now = _utcnow()
        kind = row["kind"]
        muni = row["municipality_slug"] or GLOBAL_MUNI
        ensure_municipality(conn, muni)

        confidence = float(mapping_confidence) if mapping_confidence is not None else 90.0
        source = mapping_source or "Human judgment"
        if source not in KNOWLEDGE_MAPPING_SOURCES:
            source = "Human judgment"

        # Lifecycle: low-confidence stays draft (never silently affects prod)
        # unless the reviewer explicitly activates it.
        if activate is True:
            lifecycle = "active"
        elif activate is False:
            lifecycle = "draft"
        else:
            lifecycle = "active" if confidence >= KNOWLEDGE_MIN_ACTIVE_CONFIDENCE else "draft"

        previous = self._previous_mapping(conn, kind, muni, row["raw_value"])

        # Resolve the proposed canonical value per kind.
        if kind == "status":
            proposed = (canonical_status or row["suggested_interpretation"] or "unknown").strip().lower()
        elif kind == "permit_code":
            proposed = (trade or row["suggested_interpretation"] or "other").strip().lower()
        elif kind == "keyword":
            proposed = (category or row["suggested_interpretation"] or "Other")
        else:
            raise ValueError(f"unsupported kind {kind}")

        # Impact summary (read-only preview) captured for the audit log.
        impact_summary = None
        affected_count = row["affected_permit_count"]
        if lifecycle == "active":
            try:
                from pipeline.knowledge.impact import preview_impact

                preview = preview_impact(
                    conn,
                    kind=kind,
                    municipality_slug=muni,
                    raw_value=row["raw_value"],
                    proposed_mapping=proposed,
                )
                affected_count = preview["affected_permit_count"]
                impact_summary = (
                    f"affected={preview['affected_permit_count']} "
                    f"avg {preview['avg_score_before']}->{preview['avg_score_after']} "
                    f"60+={preview['above_before']}->{preview['above_after']} "
                    f"enter={preview['entering_queue']} leave={preview['leaving_queue']}"
                )
            except Exception as exc:  # pragma: no cover - preview is best-effort
                impact_summary = f"preview_unavailable: {exc}"

        kb_version = self.bump_kb_version(conn)

        if kind == "status":
            conn.execute(
                """
                INSERT INTO status_dictionary (
                    municipality_slug, raw_status, canonical_status, mapping_rule,
                    confidence, human_reviewed, last_observed,
                    mapping_confidence, mapping_source, lifecycle_state,
                    reviewed_by, reviewed_at, review_notes, kb_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(municipality_slug, raw_status) DO UPDATE SET
                    canonical_status=excluded.canonical_status,
                    mapping_rule=excluded.mapping_rule,
                    confidence=excluded.confidence,
                    human_reviewed=1,
                    last_observed=excluded.last_observed,
                    mapping_confidence=excluded.mapping_confidence,
                    mapping_source=excluded.mapping_source,
                    lifecycle_state=excluded.lifecycle_state,
                    reviewed_by=excluded.reviewed_by,
                    reviewed_at=excluded.reviewed_at,
                    review_notes=excluded.review_notes,
                    kb_version=excluded.kb_version,
                    updated_at=excluded.updated_at
                """,
                (
                    muni, row["raw_value"], proposed,
                    f"approved:queue:{queue_id}->{proposed}",
                    confidence / 100.0, now,
                    confidence, source, lifecycle,
                    reviewed_by, now, notes, kb_version, now, now,
                ),
            )
        elif kind == "permit_code":
            cat_val = project_category or _title(proposed)
            conn.execute(
                """
                INSERT INTO permit_code_dictionary (
                    municipality_slug, raw_permit_code, friendly_name, trade,
                    project_category, description, plumbing_relevance, ai_confidence,
                    human_reviewed, last_observed_date,
                    mapping_confidence, mapping_source, lifecycle_state,
                    reviewed_by, reviewed_at, review_notes, kb_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(municipality_slug, raw_permit_code) DO UPDATE SET
                    friendly_name=excluded.friendly_name,
                    trade=excluded.trade,
                    project_category=excluded.project_category,
                    description=excluded.description,
                    plumbing_relevance=excluded.plumbing_relevance,
                    ai_confidence=excluded.ai_confidence,
                    human_reviewed=1,
                    last_observed_date=excluded.last_observed_date,
                    mapping_confidence=excluded.mapping_confidence,
                    mapping_source=excluded.mapping_source,
                    lifecycle_state=excluded.lifecycle_state,
                    reviewed_by=excluded.reviewed_by,
                    reviewed_at=excluded.reviewed_at,
                    review_notes=excluded.review_notes,
                    kb_version=excluded.kb_version,
                    updated_at=excluded.updated_at
                """,
                (
                    muni, row["raw_value"], friendly_name or row["raw_value"], proposed,
                    cat_val, row["description"],
                    0.8 if proposed in {"plumbing", "gas", "water heater", "pool"} else 0.3,
                    confidence / 100.0, now[:10],
                    confidence, source, lifecycle,
                    reviewed_by, now, notes, kb_version, now, now,
                ),
            )
        elif kind == "keyword":
            conn.execute(
                """
                INSERT INTO keyword_dictionary (
                    keyword, category, trade, suggested_products, weight,
                    confidence, human_reviewed,
                    mapping_confidence, mapping_source, lifecycle_state,
                    reviewed_by, reviewed_at, review_notes, kb_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1.0, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword) DO UPDATE SET
                    category=excluded.category,
                    trade=excluded.trade,
                    suggested_products=excluded.suggested_products,
                    confidence=excluded.confidence,
                    human_reviewed=1,
                    mapping_confidence=excluded.mapping_confidence,
                    mapping_source=excluded.mapping_source,
                    lifecycle_state=excluded.lifecycle_state,
                    reviewed_by=excluded.reviewed_by,
                    reviewed_at=excluded.reviewed_at,
                    review_notes=excluded.review_notes,
                    kb_version=excluded.kb_version,
                    updated_at=excluded.updated_at
                """,
                (
                    row["raw_value"].lower(), proposed, trade, suggested_products,
                    confidence / 100.0,
                    confidence, source, lifecycle,
                    reviewed_by, now, notes, kb_version, now, now,
                ),
            )

        conn.execute(
            """
            UPDATE knowledge_review_queue
            SET status='approved', reviewer_notes=?, resolved_at=?
            WHERE id=?
            """,
            (notes, now, queue_id),
        )

        self.write_audit(
            conn,
            kind=kind,
            municipality_slug=muni,
            raw_value=row["raw_value"],
            previous_mapping=previous,
            new_mapping=proposed,
            action="approved",
            reviewer=reviewed_by,
            reason=reason or notes,
            mapping_confidence=confidence,
            mapping_source=source,
            affected_permit_count=affected_count,
            score_impact_summary=impact_summary,
            kb_version=kb_version,
        )
        conn.commit()
        self.reload(conn)

        reprocessed = 0
        if lifecycle == "active" and reprocess:
            reprocessed = self.reprocess_affected(
                conn, kind, muni, row["raw_value"], kb_version
            )
            if regenerate_reports:
                try:
                    from pipeline.reports.generate_reports import generate_all_reports

                    generate_all_reports(conn)
                except Exception as exc:  # pragma: no cover
                    print(f"Warning: report regeneration failed: {exc}")

        return {
            "ok": True,
            "id": queue_id,
            "kind": kind,
            "status": "approved",
            "lifecycle_state": lifecycle,
            "mapping_confidence": confidence,
            "mapping_source": source,
            "proposed_mapping": proposed,
            "previous_mapping": previous,
            "kb_version": kb_version,
            "reprocessed_permits": reprocessed,
            "score_impact_summary": impact_summary,
        }

    def approve_batch(
        self,
        conn: sqlite3.Connection,
        queue_ids: list[int],
        *,
        proposed_mapping: str,
        mapping_confidence: float | None = None,
        mapping_source: str | None = None,
        reviewed_by: str | None = None,
        activate: bool | None = None,
        reason: str | None = None,
        reprocess: bool = True,
        regenerate_reports: bool = False,
    ) -> dict:
        """Approve several queue items sharing municipality + proposed mapping."""
        from pipeline.config.settings import KNOWLEDGE_BATCH_MIN_CONFIDENCE

        if not queue_ids:
            raise ValueError("no queue ids supplied")
        confidence = float(mapping_confidence) if mapping_confidence is not None else 90.0
        if confidence < KNOWLEDGE_BATCH_MIN_CONFIDENCE:
            raise ValueError(
                f"batch confidence {confidence} below minimum "
                f"{KNOWLEDGE_BATCH_MIN_CONFIDENCE}"
            )

        rows = []
        for qid in queue_ids:
            r = conn.execute(
                "SELECT * FROM knowledge_review_queue WHERE id=?", (qid,)
            ).fetchone()
            if r is None:
                raise ValueError(f"queue item {qid} not found")
            if r["status"] != "pending":
                raise ValueError(f"queue item {qid} is already {r['status']}")
            rows.append(r)

        kinds = {r["kind"] for r in rows}
        munis = {r["municipality_slug"] or GLOBAL_MUNI for r in rows}
        if len(kinds) != 1:
            raise ValueError("batch items must share the same kind")
        if len(munis) != 1:
            raise ValueError("batch items must share the same municipality")

        kind = rows[0]["kind"]
        results = []
        for r in rows:
            kwargs = dict(
                mapping_confidence=confidence,
                mapping_source=mapping_source,
                reviewed_by=reviewed_by,
                activate=activate,
                reason=reason,
                reprocess=reprocess,
                regenerate_reports=False,  # regenerate once at the end
            )
            if kind == "status":
                kwargs["canonical_status"] = proposed_mapping
            elif kind == "permit_code":
                kwargs["trade"] = proposed_mapping
            elif kind == "keyword":
                kwargs["category"] = proposed_mapping
            results.append(self.approve_queue_item(conn, r["id"], **kwargs))

        if regenerate_reports:
            try:
                from pipeline.reports.generate_reports import generate_all_reports

                generate_all_reports(conn)
            except Exception as exc:  # pragma: no cover
                print(f"Warning: report regeneration failed: {exc}")

        return {
            "ok": True,
            "approved": len(results),
            "kind": kind,
            "proposed_mapping": proposed_mapping,
            "results": results,
        }

    def reprocess_affected(
        self,
        conn: sqlite3.Connection,
        kind: str,
        municipality_slug: str | None,
        raw_value: str,
        kb_version: int,
    ) -> int:
        """Re-score only the permits affected by a mapping change.

        Stamps ``projects.mapping_kb_version`` for reproducibility. The full
        dataset is never re-run here.
        """
        from pipeline.analysis.run_analysis import analyze_permit
        from pipeline.config.settings import ANALYSIS_VERSION
        from pipeline.knowledge.impact import _fetch_affected

        rows = _fetch_affected(conn, kind, municipality_slug, raw_value)
        now = _utcnow()
        for row in rows:
            permit = dict(row)
            result = analyze_permit(permit, self)
            conn.execute(
                """
                INSERT INTO projects (permit_id, jurisdiction, project_category,
                    construction_stage, project_lifecycle, opportunity_date,
                    opportunity_date_basis, opportunity_timing,
                    estimated_plumbing_scope, estimated_material_value,
                    estimated_gross_profit, opportunity_score, confidence_score,
                    analysis_version, analyzed_at, mapping_kb_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(permit_id) DO UPDATE SET
                    project_category=excluded.project_category,
                    construction_stage=excluded.construction_stage,
                    project_lifecycle=excluded.project_lifecycle,
                    opportunity_date=excluded.opportunity_date,
                    opportunity_date_basis=excluded.opportunity_date_basis,
                    opportunity_timing=excluded.opportunity_timing,
                    estimated_plumbing_scope=excluded.estimated_plumbing_scope,
                    estimated_material_value=excluded.estimated_material_value,
                    estimated_gross_profit=excluded.estimated_gross_profit,
                    opportunity_score=excluded.opportunity_score,
                    confidence_score=excluded.confidence_score,
                    analysis_version=excluded.analysis_version,
                    analyzed_at=excluded.analyzed_at,
                    mapping_kb_version=excluded.mapping_kb_version
                """,
                (
                    permit["id"], permit["jurisdiction"], result["project_category"],
                    result["construction_stage"], result["project_lifecycle"],
                    result["opportunity_date"], result["opportunity_date_basis"],
                    result["opportunity_timing"],
                    result["estimated_plumbing_scope"],
                    result["estimated_material_value"], result["estimated_gross_profit"],
                    result["opportunity_score"], result["confidence_score"],
                    ANALYSIS_VERSION, now, kb_version,
                ),
            )
            proj = conn.execute(
                "SELECT id FROM projects WHERE permit_id=?", (permit["id"],)
            ).fetchone()
            if proj:
                pid = proj["id"]
                conn.execute("DELETE FROM estimated_materials WHERE project_id=?", (pid,))
                for m in result["materials"]:
                    conn.execute(
                        """
                        INSERT INTO estimated_materials
                            (project_id, material_name, confidence_pct, rationale)
                        VALUES (?, ?, ?, ?)
                        """,
                        (pid, m.material_name, m.confidence_pct, m.rationale),
                    )
        conn.commit()
        return len(rows)

    def reject_queue_item(
        self, conn: sqlite3.Connection, queue_id: int, *,
        notes: str | None = None, reviewed_by: str | None = None,
        reason: str | None = None,
    ) -> dict:
        row = conn.execute(
            "SELECT * FROM knowledge_review_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"queue item {queue_id} not found")
        if row["status"] != "pending":
            raise ValueError(f"queue item {queue_id} is already {row['status']}")
        now = _utcnow()
        conn.execute(
            """
            UPDATE knowledge_review_queue
            SET status='rejected', reviewer_notes=?, resolved_at=?
            WHERE id=?
            """,
            (notes, now, queue_id),
        )
        self.write_audit(
            conn,
            kind=row["kind"],
            municipality_slug=row["municipality_slug"] or GLOBAL_MUNI,
            raw_value=row["raw_value"],
            previous_mapping=None,
            new_mapping=None,
            action="rejected",
            reviewer=reviewed_by,
            reason=reason or notes,
            mapping_confidence=None,
            mapping_source=None,
            affected_permit_count=row["affected_permit_count"],
            score_impact_summary=None,
            kb_version=self.get_kb_version(conn),
        )
        conn.commit()
        return {"ok": True, "id": queue_id, "status": "rejected"}

    def deprecate_mapping(
        self,
        conn: sqlite3.Connection,
        *,
        kind: str,
        municipality_slug: str | None,
        raw_value: str,
        reviewed_by: str | None = None,
        reason: str | None = None,
        reprocess: bool = True,
    ) -> dict:
        """Retire an active mapping without deleting it (history preserved)."""
        muni = municipality_slug or GLOBAL_MUNI
        previous = self._previous_mapping(conn, kind, muni, raw_value)
        now = _utcnow()
        kb_version = self.bump_kb_version(conn)
        if kind == "status":
            conn.execute(
                "UPDATE status_dictionary SET lifecycle_state='deprecated', "
                "updated_at=?, kb_version=? WHERE municipality_slug=? AND raw_status=? COLLATE NOCASE",
                (now, kb_version, muni, raw_value),
            )
        elif kind == "permit_code":
            conn.execute(
                "UPDATE permit_code_dictionary SET lifecycle_state='deprecated', "
                "updated_at=?, kb_version=? WHERE municipality_slug=? AND raw_permit_code=? COLLATE NOCASE",
                (now, kb_version, muni, raw_value),
            )
        elif kind == "keyword":
            conn.execute(
                "UPDATE keyword_dictionary SET lifecycle_state='deprecated', "
                "updated_at=?, kb_version=? WHERE keyword=? COLLATE NOCASE",
                (now, kb_version, raw_value.lower()),
            )
        else:
            raise ValueError(f"unsupported kind {kind}")
        self.write_audit(
            conn, kind=kind, municipality_slug=muni, raw_value=raw_value,
            previous_mapping=previous, new_mapping=None, action="deprecated",
            reviewer=reviewed_by, reason=reason, mapping_confidence=None,
            mapping_source=None, affected_permit_count=None,
            score_impact_summary=None, kb_version=kb_version,
        )
        conn.commit()
        self.reload(conn)
        reprocessed = 0
        if reprocess:
            reprocessed = self.reprocess_affected(conn, kind, muni, raw_value, kb_version)
        return {"ok": True, "kind": kind, "raw_value": raw_value,
                "lifecycle_state": "deprecated", "kb_version": kb_version,
                "reprocessed_permits": reprocessed}


def _title(value: str) -> str:
    if value == "water heater":
        return "Water Heater"
    return value.replace("_", " ").title()


def ensure_municipality(conn: sqlite3.Connection, slug: str, name: str | None = None) -> None:
    now = _utcnow()
    display = name or slug.replace("_", " ").title()
    conn.execute(
        """
        INSERT INTO municipalities (slug, name, state, permit_source, active,
                                    metadata_json, created_at, updated_at)
        VALUES (?, ?, 'AZ', NULL, 1, NULL, ?, ?)
        ON CONFLICT(slug) DO NOTHING
        """,
        (slug, display, now, now),
    )


def sync_municipalities_from_jurisdictions(conn: sqlite3.Connection) -> int:
    """Mirror jurisdictions into municipalities knowledge table."""
    now = _utcnow()
    ensure_municipality(conn, GLOBAL_MUNI, "Global")
    rows = conn.execute("SELECT * FROM jurisdictions").fetchall()
    for row in rows:
        meta = {
            "connector_type": row["connector_type"],
            "endpoint_url": row["endpoint_url"],
            "resource_id": row["resource_id"],
            "jurisdiction_status": row["status"],
            "notes": row["notes"],
            "last_sync_status": row["last_sync_status"],
            "last_sync_record_count": row["last_sync_record_count"],
        }
        conn.execute(
            """
            INSERT INTO municipalities (
                slug, name, state, permit_source, active, last_synchronization,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                name=excluded.name,
                state=excluded.state,
                permit_source=excluded.permit_source,
                active=excluded.active,
                last_synchronization=excluded.last_synchronization,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                row["slug"],
                row["name"],
                row["state"] or "AZ",
                row["connector_type"],
                1 if row["status"] == "connected" else 0,
                row["last_synced_at"],
                json.dumps(meta),
                now,
                now,
            ),
        )
    conn.commit()
    return len(rows)


def export_knowledge_snapshot(conn: sqlite3.Connection) -> dict:
    """JSON payload for the review dashboard / static export."""
    pending = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM knowledge_review_queue
            WHERE status='pending'
            ORDER BY COALESCE(priority_score, -1) DESC,
                     occurrence_count DESC, last_seen DESC
            """
        )
    ]
    by_kind = {"status": [], "permit_code": [], "keyword": []}
    tier_counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for item in pending:
        by_kind.setdefault(item["kind"], []).append(item)
        tier = item.get("priority_tier")
        if tier in tier_counts:
            tier_counts[tier] += 1

    return {
        "generated_at": _utcnow(),
        "kb_version": KnowledgeEngine.get_kb_version(conn),
        "priority_tiers": tier_counts,
        "counts": {
            "pending_total": len(pending),
            "pending_status": len(by_kind.get("status", [])),
            "pending_permit_code": len(by_kind.get("permit_code", [])),
            "pending_keyword": len(by_kind.get("keyword", [])),
            "status_dictionary": conn.execute(
                "SELECT COUNT(*) AS n FROM status_dictionary"
            ).fetchone()["n"],
            "permit_code_dictionary": conn.execute(
                "SELECT COUNT(*) AS n FROM permit_code_dictionary"
            ).fetchone()["n"],
            "keyword_dictionary": conn.execute(
                "SELECT COUNT(*) AS n FROM keyword_dictionary"
            ).fetchone()["n"],
            "product_dictionary": conn.execute(
                "SELECT COUNT(*) AS n FROM product_dictionary"
            ).fetchone()["n"],
            "municipalities": conn.execute(
                "SELECT COUNT(*) AS n FROM municipalities"
            ).fetchone()["n"],
        },
        "queue": pending,
        "queue_by_kind": by_kind,
        "municipalities": [dict(r) for r in conn.execute(
            "SELECT slug, name, state, permit_source, active, last_synchronization FROM municipalities ORDER BY name"
        )],
    }
