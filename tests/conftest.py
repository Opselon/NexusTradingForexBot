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


# ---------------------------------------------------------------------------
# AUTH-SESSION-ISOLATION (P1 test-integrity, 2026-09-12): the WEB-AUTH-P0
# knobs (NSE_WEB_AUTH_DISABLE / NSE_WEB_AUTH_TOKEN, read live by
# web.server._install_web_auth_if_enabled + web.auth._resolve_token) are
# process-global mutable state. Historically tests/unit/test_alt_ui_runtime_
# contract.py mutated os.environ at import time (setdefault -> fires during
# COLLECTION, so the whole session inherits the value) and
# tests/unit/test_node_runtime_role.py set it in-body without restoring, so
# auth-contract outcomes flipped by collection order alone: with DISABLE
# leaked, create_app installs NO middleware and the 401-contract tests
# (e.g. test_replay_toggle_requires_auth) go red-inverted / sibling probe
# tests go fake-GREEN (200 served by an auth layer that was silently off).
#
# Isolation boundary (two lifetimes, both hook-based):
#  * SESSION: one scrub at collection finish removes every NSE_WEB_AUTH_* key
#    that import-time mutation put there (import happens during COLLECTION,
#    before the first test) plus the operator's ambient values — so the test
#    session starts auth-neutral in every environment. A pre-collection
#    snapshot is kept and restored at session end (no permanent process
#    mutation by the harness either).
#  * PER-TEST: the env state captured AFTER the test's fixtures have been set
#    up is the baseline; teardown (tryfirst -> BEFORE fixture finalizers, so
#    monkeypatch's own undo still wins) restores it, which undoes direct
#    in-body os.environ writes (the historic node-role pattern) while
#    PRESERVING deliberate module-/session-scoped pins (e.g. #158's
#    operator_routes mp.setenv, whose token must outlive individual tests —
#    the Starlette middleware resolves it lazily on the app's FIRST request,
#    mid-test). Function-scoped monkeypatch pins undo normally because their
#    finalizers run after this restore.
# Semantics identical to monkeypatch, at the boundary where outer-scope
# legitimate pins keep working. This is harness lifecycle ONLY: production
# keeps reading the same env vars with the same precedence; src/ untouched.
# ---------------------------------------------------------------------------
_NSE_WEB_AUTH_ENV_PREFIX = "NSE_WEB_AUTH_"


def _web_auth_env_keys() -> list[str]:
    return [k for k in os.environ if k.startswith(_NSE_WEB_AUTH_ENV_PREFIX)]


def _web_auth_snapshot() -> dict[str, str]:
    return {k: os.environ[k] for k in _web_auth_env_keys()}


def _web_auth_apply(state: dict[str, str]) -> None:
    for k in _web_auth_env_keys():
        if k not in state:
            del os.environ[k]
    os.environ.update(state)


#: Mutable containers instead of `global` rebindings (ruff PLW0603).
#: session_snapshot: pre-scrub ambient state, restored at session end.
#: baseline: env state after the current test's fixture setup.
_web_auth_session_state: dict[str, dict[str, str] | None] = {"snapshot": None}
_web_auth_test_state: dict[str, dict[str, str] | None] = {"baseline": None}


def pytest_collection_finish(session: pytest.Session) -> None:
    """Scrub NSE_WEB_AUTH_* once, after collection (all module imports have
    happened) and before the first test — ambient + import-time pollution are
    invisible to tests from here on."""
    if _web_auth_session_state["snapshot"] is not None:  # defensive
        return
    _web_auth_session_state["snapshot"] = _web_auth_snapshot()
    _web_auth_apply({})


def pytest_unconfigure(config: pytest.Config) -> None:
    """Session end: put the process env back exactly as it was before the
    scrub (ambient operator values restored; test residue never)."""
    snapshot = _web_auth_session_state["snapshot"]
    if snapshot is not None:
        _web_auth_apply(snapshot)
        _web_auth_session_state["snapshot"] = None


@pytest.hookimpl(trylast=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Baseline = the family state AFTER this test's fixtures ran their setup
    (trylast -> the runner's item.setup() already executed, including
    module-scoped fixture instantiation and their env pins)."""
    _web_auth_test_state["baseline"] = _web_auth_snapshot()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """Undo direct call-phase os.environ writes by restoring the post-setup
    baseline BEFORE any fixture finalizer runs (monkeypatch's own undo, and
    module-fixture mp.undo(), still get the last word)."""
    baseline = _web_auth_test_state["baseline"]
    if baseline is not None:
        _web_auth_apply(baseline)


@pytest.fixture(autouse=True, scope="session")
def _isolate_web_auth_secret_store(tmp_path_factory: pytest.TempPathFactory):
    """Never let the WEB-AUTH generated-token fallback touch the operator's
    real DPAPI keystore (%LOCALAPPDATA%\\NexusScalpEngine\\secrets.enc).
    auth._resolve_token()'s last-resort branch (no env token, no stored
    secret) GENERATES and PERSISTS a token through SecureSecretStore, whose
    default root is app_data_root() — so an env-less create_app() in any test
    is a machine-mutating side effect (secrets.enc mtime evidence, A-6).
    Redirect the store's root resolver to a session tmp dir. Session scope
    (not function): module-scoped client fixtures (test_operator_routes) call
    create_app before function-scoped fixtures exist, so a function-scoped
    guard would be installed too late; a stable session dir also keeps
    generated-token persistence semantics (stable across 'restarts') intact.
    Precedent for targeting this seam: tests/unit/test_web_auth.py::
    test_generated_token_persisted patches the resolver (its
    web_auth.app_data_root patch is inert — auth.py has no such module
    binding; the name resolves inside secret_store.py, which is what we
    patch here)."""
    from nexus_scalp.settings import secret_store as _secret_store_mod

    run_dir = tmp_path_factory.mktemp("web_auth_secrets")
    _real_app_data_root = _secret_store_mod.app_data_root
    _secret_store_mod.app_data_root = lambda: run_dir
    try:
        yield
    finally:
        _secret_store_mod.app_data_root = _real_app_data_root
