"""Runs ingestion for every 'connected' jurisdiction.

First run per jurisdiction defaults to a bounded lookback window rather than
a full historical backfill — Mesa's dataset alone has 150k+ rows going back
to 2003, and the dashboard only cares about recent activity. Subsequent runs
sync incrementally from `last_synced_at`.
"""

import sqlite3
import time as _time
from datetime import datetime, timedelta, timezone

from pipeline.config import settings
from pipeline.connectors.base import ConnectorNotConfiguredError, utcnow_iso
from pipeline.connectors.registry import build_connector
from pipeline.ingestion.upsert import upsert_permit

DEFAULT_FIRST_RUN_LOOKBACK_DAYS = 730  # ~2 years


def _parse_since(last_synced_at: str | None) -> datetime:
    if last_synced_at:
        return datetime.fromisoformat(last_synced_at)
    return datetime.now(timezone.utc) - timedelta(days=DEFAULT_FIRST_RUN_LOOKBACK_DAYS)


def run_ingestion(conn: sqlite3.Connection) -> None:
    connected = conn.execute(
        "SELECT slug, connector_type, last_synced_at FROM jurisdictions WHERE status = 'connected'"
    ).fetchall()

    if not connected:
        print("No connected jurisdictions to ingest.")
        return

    for row in connected:
        slug = row["slug"]
        since = _parse_since(row["last_synced_at"])
        run_started_at = utcnow_iso()

        print(f"[{slug}] ingesting since {since.isoformat()} ...")

        try:
            connector = build_connector(slug, row["connector_type"])
        except ConnectorNotConfiguredError as exc:
            print(f"[{slug}] SKIPPED — {exc}")
            _record_run(conn, slug, run_started_at, 0, 0, 0, "error", str(exc))
            continue

        # A connector failure (bad query, network error, source outage) must
        # not take down ingestion for every other jurisdiction — record it
        # and move on, same as the ConnectorNotConfiguredError path above.
        try:
            result = connector.run(since=since)
        except Exception as exc:  # noqa: BLE001
            print(f"[{slug}] FAILED — {exc}")
            conn.execute(
                "UPDATE jurisdictions SET last_sync_status = ?, last_sync_error = ? WHERE slug = ?",
                ("error", str(exc), slug),
            )
            conn.commit()
            _record_run(conn, slug, run_started_at, 0, 0, 0, "error", str(exc))
            continue

        inserted = updated = 0
        for mapped in result.records:
            outcome = upsert_permit(conn, mapped)
            if outcome == "inserted":
                inserted += 1
            else:
                updated += 1
        conn.commit()

        status = "success" if not result.errors else "success_with_errors"
        error_message = "; ".join(result.errors[:5]) if result.errors else None

        conn.execute(
            """
            UPDATE jurisdictions
            SET last_synced_at = ?, last_sync_status = ?, last_sync_record_count = ?, last_sync_error = ?
            WHERE slug = ?
            """,
            (utcnow_iso(), status, result.fetched_count, error_message, slug),
        )
        conn.commit()

        _record_run(
            conn, slug, run_started_at, result.fetched_count, inserted, updated, status, error_message
        )

        print(
            f"[{slug}] fetched={result.fetched_count} inserted={inserted} updated={updated} "
            f"errors={len(result.errors)}"
        )


def _record_run(conn, slug, run_started_at, fetched, inserted, updated, status, error_message):
    conn.execute(
        """
        INSERT INTO ingestion_runs (jurisdiction_slug, run_started_at, run_finished_at,
                                     records_fetched, records_inserted, records_updated,
                                     status, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (slug, run_started_at, utcnow_iso(), fetched, inserted, updated, status, error_message),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Morning-refresh ingestion: per-jurisdiction, retryable, failure-isolated.
# Reuses the same connectors and upsert as the full pipeline run.
# ---------------------------------------------------------------------------

class _NonRetryable(Exception):
    """Wraps an error that must NOT be retried (auth / invalid request)."""


def is_retryable_error(exc: Exception) -> bool:
    """Retry only transient network/server faults. Never retry authentication
    or invalid-request (4xx) failures, or configuration problems."""
    if isinstance(exc, ConnectorNotConfiguredError):
        return False
    try:
        import requests  # local import: connectors depend on it, tests may not
    except Exception:  # pragma: no cover
        requests = None

    if requests is not None:
        if isinstance(exc, (requests.exceptions.Timeout,
                            requests.exceptions.ConnectionError,
                            requests.exceptions.ChunkedEncodingError)):
            return True
        if isinstance(exc, requests.exceptions.HTTPError):
            resp = getattr(exc, "response", None)
            code = getattr(resp, "status_code", None)
            # 429 + 5xx are transient; 401/403 (auth) and 400/404 (invalid) are not.
            return code in (429, 500, 502, 503, 504)
        if isinstance(exc, requests.exceptions.RequestException):
            return True
    # Fall back to name-based detection so tests can raise lightweight stand-ins.
    name = type(exc).__name__.lower()
    if any(tok in name for tok in ("auth", "forbidden", "unauthorized",
                                   "invalid", "badrequest", "notconfigured")):
        return False
    return any(tok in name for tok in ("timeout", "connection", "temporary",
                                       "unavailable", "network"))


def _fetch_with_retry(connector, since, *, attempts, backoff_base, backoff_max, sleep):
    """Run the connector, retrying transient failures with exponential backoff.

    Returns (ConnectorResult, retries_used). Raises the last exception when all
    attempts are exhausted, or immediately for non-retryable errors.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return connector.run(since=since), attempt - 1
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not is_retryable_error(exc) or attempt >= attempts:
                raise
            delay = min(backoff_base * (2 ** (attempt - 1)), backoff_max)
            sleep(delay)
    raise last_exc  # pragma: no cover - loop always returns or raises


def _latest_source_dates(conn: sqlite3.Connection, slug: str) -> dict:
    row = conn.execute(
        """
        SELECT MAX(filed_date)  AS newest_submitted,
               MAX(issued_date) AS newest_issued,
               MAX(COALESCE(issued_date, filed_date, finaled_date)) AS newest_any
        FROM permits WHERE jurisdiction = ?
        """,
        (slug,),
    ).fetchone()
    return {
        "newest_submitted_date": row["newest_submitted"],
        "newest_issued_date": row["newest_issued"],
        "newest_source_date": row["newest_any"],
    }


def ingest_jurisdiction(
    conn: sqlite3.Connection,
    slug: str,
    connector_type: str,
    last_synced_at: str | None,
    *,
    attempts: int | None = None,
    backoff_base: float | None = None,
    backoff_max: float | None = None,
    sleep=_time.sleep,
) -> dict:
    """Ingest a single jurisdiction with retry + failure isolation.

    Always returns a stats dict (never raises for a source failure) so the
    caller can continue with the other jurisdictions. Detects unchanged records
    so only genuinely new/changed permits bump ``last_updated_at``.
    """
    attempts = attempts or settings.MORNING_RETRY_MAX_ATTEMPTS
    backoff_base = settings.MORNING_RETRY_BACKOFF_SECONDS if backoff_base is None else backoff_base
    backoff_max = settings.MORNING_RETRY_BACKOFF_MAX_SECONDS if backoff_max is None else backoff_max

    since = _parse_since(last_synced_at)
    run_started_at = utcnow_iso()
    stats = {
        "slug": slug,
        "status": "failed",
        "fetched": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "submitted_only": 0,
        "retries": 0,
        "error": None,
    }

    try:
        connector = build_connector(slug, connector_type)
    except ConnectorNotConfiguredError as exc:
        stats["error"] = str(exc)
        conn.execute(
            "UPDATE jurisdictions SET last_sync_status = ?, last_sync_error = ? WHERE slug = ?",
            ("error", str(exc), slug),
        )
        conn.commit()
        _record_run(conn, slug, run_started_at, 0, 0, 0, "error", str(exc))
        stats.update(_latest_source_dates(conn, slug))
        return stats

    try:
        result, retries = _fetch_with_retry(
            connector, since, attempts=attempts, backoff_base=backoff_base,
            backoff_max=backoff_max, sleep=sleep,
        )
    except Exception as exc:  # noqa: BLE001
        stats["error"] = f"{type(exc).__name__}: {exc}"
        conn.execute(
            "UPDATE jurisdictions SET last_sync_status = ?, last_sync_error = ? WHERE slug = ?",
            ("error", str(exc), slug),
        )
        conn.commit()
        _record_run(conn, slug, run_started_at, 0, 0, 0, "error", str(exc))
        stats.update(_latest_source_dates(conn, slug))
        return stats

    inserted = updated = unchanged = 0
    for mapped in result.records:
        outcome = upsert_permit(conn, mapped, detect_unchanged=True)
        if outcome == "inserted":
            inserted += 1
        elif outcome == "updated":
            updated += 1
        else:
            unchanged += 1
    conn.commit()

    status = "success" if not result.errors else "success_with_errors"
    error_message = "; ".join(result.errors[:5]) if result.errors else None

    conn.execute(
        """
        UPDATE jurisdictions
        SET last_synced_at = ?, last_sync_status = ?, last_sync_record_count = ?, last_sync_error = ?
        WHERE slug = ?
        """,
        (utcnow_iso(), status, result.fetched_count, error_message, slug),
    )
    conn.commit()
    _record_run(conn, slug, run_started_at, result.fetched_count, inserted, updated,
                status, error_message)

    stats.update({
        "status": status,
        "fetched": result.fetched_count,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "retries": retries,
        "error": error_message,
    })
    stats.update(_latest_source_dates(conn, slug))
    return stats
