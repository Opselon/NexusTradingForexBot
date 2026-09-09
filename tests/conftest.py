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

# ---------------------------------------------------------------------------
# Worktree import isolation (BUG-231 test harness): the dev venv's
# __editable__ .pth pins nexus_scalp to the MAIN checkout's src/. When the
# suite runs inside an isolated git worktree (.../.worktrees/<name>), that
# pin would silently import the MAIN checkout's adapter instead of the
# worktree copy under test. Prepend the worktree's src/ and purge any
# pre-imported nexus_scalp modules BEFORE anything imports them. On the
# main checkout and in CI the condition is false -> no-op.
# ---------------------------------------------------------------------------
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.sep + ".worktrees" + os.sep in _ROOT + os.sep:
    _SRC = os.path.join(_ROOT, "src")
    if os.path.isdir(os.path.join(_SRC, "nexus_scalp")):
        if _SRC not in sys.path:
            sys.path.insert(0, _SRC)
        for _name in [_m for _m in list(sys.modules) if _m.startswith("nexus_scalp")]:
            del sys.modules[_name]


import logging  # noqa: E402

import pytest  # noqa: E402

# ---------------------------------------------------------------------------
# Disk-safety guard (2026-09-09): a crashing test interpreter must never write
# a core dump. A Linux teardown abort in the runtime gate / slow suites
# (NX-RUNTIMEGATE-ABORT class) produced multi-GB core files under
# /var/lib/apport/coredump (4GB in one day on a 48GB host). RLIMIT_CORE=0
# disables core dumps for THIS process and every child (gate stages, subprocess
# probes) at zero runtime cost. Windows has no `resource` module -> no-op.
# Import-time (not fixture) so it applies before pytest spawns anything.
# ---------------------------------------------------------------------------
try:
    import resource as _resource

    _resource.setrlimit(_resource.RLIMIT_CORE, (0, 0))
except (ImportError, OSError, ValueError):  # pragma: no cover - platform guard
    pass

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


@pytest.fixture(autouse=True)
def _isolate_paper_persist(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    """PAPER Reality Phase 2: paper_adapter persists account/positions to a JSON
    state file under the data root. Point that root at a per-run temp dir (and
    keep persistence ENABLED so recovery behaviour itself is testable) so unit
    tests never read or write the real user data root. Respects an explicit
    ``NEXUS_PAPER_PERSIST=0`` set by a test (opt-out) via :meth:`setenv` and
    via module-level monkeypatching."""
    run_dir = tmp_path_factory.mktemp("paper_persist")
    monkeypatch.setenv("NEXUS_DATA_ROOT", str(run_dir))
    if os.environ.get("NEXUS_PAPER_PERSIST", "") == "":
        monkeypatch.setenv("NEXUS_PAPER_PERSIST", "1")
    yield
