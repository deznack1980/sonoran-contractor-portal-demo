"""Regression tests for the Release 11 production rollout guardrails."""

from __future__ import annotations

from pipeline.config.settings import PROJECT_ROOT
from scripts import apply_lead_integrity as rollout


def test_wait_for_server_stop_tolerates_shutdown_race(monkeypatch):
    states = iter((True, True, False))
    sleeps = []
    monkeypatch.setattr(rollout, "_server_running", lambda: next(states))
    monkeypatch.setattr(rollout.time, "sleep", sleeps.append)

    assert rollout._wait_for_server_stop(attempts=3, delay_seconds=0.01) is True
    assert sleeps == [0.01, 0.01]


def test_wait_for_server_stop_keeps_safety_block(monkeypatch):
    sleeps = []
    monkeypatch.setattr(rollout, "_server_running", lambda: True)
    monkeypatch.setattr(rollout.time, "sleep", sleeps.append)

    assert rollout._wait_for_server_stop(attempts=3, delay_seconds=0) is False
    assert sleeps == [0, 0]


def test_rollout_checks_server_before_opening_database():
    source = (PROJECT_ROOT / "scripts" / "apply_lead_integrity.py").read_text(
        encoding="utf-8"
    )
    assert source.index("if args.apply and _server_running():") < source.index(
        "conn = init_db()"
    )


def test_windows_launcher_waits_for_port_and_reports_backup_truthfully():
    batch = (PROJECT_ROOT / "ApplyCorridorIQLeadIntegrity.bat").read_text(
        encoding="utf-8"
    )
    assert "for ($i=0; $i -lt 40; $i++)" in batch
    assert "if (-not $listener) { $released=$true; break }" in batch
    assert "The safety check stopped before database changes or a new backup." in batch
    assert "The backup was preserved." not in batch
