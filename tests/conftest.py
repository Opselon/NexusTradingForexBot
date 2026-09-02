"""Repository-wide pytest fixture registration (TASK-06-70D-LIQUIDITY-OPTIMIZATION).

The 70D shadow suites (TASK-05-70D-SHADOW, parallel agent) declare pytest
fixtures in ``tests/helpers/shadow70_fixtures.py`` (``contract``,
``tmp_artifacts``). Without a conftest, pytest never discovers fixtures
defined in plain helper modules, so every test requesting ``contract``
ERRORs with "fixture 'contract' not found".

This conftest imports + registers those helpers fixtures so the standard
repo gate (``pytest tests/unit``) can collect them. Purely additive; no
production code touched.

An autouse fixture points the machine-wide settings DB (app_settings.db)
at a per-run temporary copy so unit tests can never read or write the
user's real configuration (cross-suite machine-state pollution fix:
tests/integration/test_model_lifecycle_api.py was mutating the real DB's
execution.mode, leaking into tests/unit/test_mt5_status_endpoint.py).
"""

from __future__ import annotations

import logging

import pytest

# Daemon worker threads (telegram notifier heartbeat, audit DB worker) can log
# into pytest's closed stdout at teardown. The stdlib logging module would print
# 'Logging error' tracebacks for those emits; disable that (workers already
# swallow their own errors) so a clean test run stays clean.
logging.raiseExceptions = False

# Register BEFORE importing so assert-rewriting applies before the module is
# loaded (the 'Module already imported so cannot be rewritten' warning appears
# when the order is reversed).
pytest.register_assert_rewrite("tests.helpers.shadow70_fixtures")
pytest_plugins = ["tests.helpers.shadow70_fixtures"]


@pytest.fixture(autouse=True)
def _isolate_settings_db(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Isolate NEXUS_SETTINGS_DB per pytest run (never touch the user's real
    %LOCALAPPDATA%\\NexusScalpEngine\\databases\\app_settings.db)."""
    run_dir = tmp_path_factory.mktemp("settings_db")
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(run_dir / "app_settings.db"))
    yield


@pytest.fixture(autouse=True)
def _isolate_implicit_audit_db(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    """BUG-223: the AuditRepository IMPLICIT default ("sqlite:///artifacts/
    audit.db", BUG-149-anchored to the runtime workspace) resolves to the
    PRODUCTION artifacts/audit.db whenever pytest runs from the repo root,
    so unit tests constructing OrderLifecycleManager without audit_repo
    appended test_req rows to the live trading ledger (957 rows found
    2026-08-31..09-02). Point the implicit default at a per-run temp file;
    explicit db_url/config callers are unaffected by construction."""
    run_dir = tmp_path_factory.mktemp("audit_db")
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(run_dir / "audit.db"))
    yield
