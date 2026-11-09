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
# __editable__ .pth pins nexus_scalp to the MAIN checkout's src/. Any
# suite run from ANOTHER tree (a .worktrees/ worktree, an agent lane
# clone such as ../nse-cc-lane) would silently import the MAIN checkout's
# code instead of the copy under test — the branch checked out over
# there decides what every test actually exercises, so results become
# a verdict on someone else's WIP (observed: full-suite failures that
# vanish when the suite binds its own src/). Prepend THIS tree's src/
# and purge any pre-imported nexus_scalp modules BEFORE anything
# imports them. On the main checkout the inserted path equals the
# .pth target (no-op), and in CI the package is installed from the
# same tree (also a no-op).
# ---------------------------------------------------------------------------
import os
import sys
from pathlib import Path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if os.path.isdir(os.path.join(_ROOT, "src", "nexus_scalp")):
    _SRC = os.path.join(_ROOT, "src")
    if os.path.isdir(os.path.join(_SRC, "nexus_scalp")):
        if _SRC not in sys.path:
            sys.path.insert(0, _SRC)
        for _name in [_m for _m in list(sys.modules) if _m.startswith("nexus_scalp")]:
            del sys.modules[_name]


import contextlib  # noqa: E402
import logging  # noqa: E402
import tempfile  # noqa: E402

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
def _isolate_runner_home_and_tmpdir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    """Redirect HOME (+ the Windows user-data roots) and TMPDIR per test.

    RUNNER-ISOLATION (forensic audit, 2026-11-09): the self-hosted runner is
    a persistent machine whose real HOME holds
    ``~/.nexusscalpengine/config/nexus.yaml``. On Linux
    ``release.paths.app_data_root()`` resolves from ``Path.home()``, so ANY
    test that boots the engine, applies a runtime config, or loads the user
    config reads — and ``_sync_user_config_file`` rewrites — that shared
    file. The bug296 fix redirected HOME only inside that one test file's
    ``_clone_ws``; every other engine-booting test (first-run bootstrap,
    doctor/repair, forensics, wizard, update orchestration) still saw the
    runner's file. This fixture closes the general case: every test gets a
    fresh, empty HOME and TMPDIR under pytest's basetemp, so a poisoned
    ``nexus.yaml`` (e.g. an absolute ``model_artifact_path`` pointing at a
    stale ``/tmp`` bundle) is invisible and nothing a test writes can divert
    the next test — or the next CI run on the same runner.

    The TMPDIR half closes the second leak class: ``tempfile.mkdtemp()``
    never cleans up, and ~25 call sites in the suite (plus the shared
    ``tests/helpers/shadow70_fixtures.py`` helper) left a directory per call
    in the runner's real ``/tmp`` (107 ``tmp*`` dirs, 1687 ``model.pt``
    files on the Dubai box). Pointing ``tempfile`` at a per-test dir under
    pytest's basetemp puts those dirs under pytest's numbered-run retention
    instead of leaking them forever. Literal ``/tmp/...`` paths in test
    code are NOT redirected (only ``tempfile`` honors TMPDIR) — those
    remain individually auditable.

    Blast radius, checked before landing (see reports/area3.md):
      * No module-level ``Path.home()`` / ``app_data_root()`` captures exist
        in ``src/`` or ``tests/`` — every call site resolves per call, so a
        per-test redirect takes effect everywhere.
      * Every test that commits via git sets repo-local config (``-c
        user.*`` or in-repo ``git config``); none depends on ``~/.gitconfig``.
      * Tests that set HOME themselves (``test_health_data_check``,
        ``test_lifecycle_chaos``) override this fixture inside the test via
        their own monkeypatch; teardown restores in reverse order.
      * ``test_cli_subprocess`` passes ``dict(os.environ)`` to the CLI
        subprocess, which inherits the redirected HOME — more isolated, not
        less. ``test_cli_end_to_end`` already assumes a fresh HOME on CI.
      * Per-test (not per-session): a session HOME would still be shared
        across tests, so one test's config projection would be visible to
        the next — the exact failure this fixture removes.
      * ``tmp_path_factory`` basetemp is fixed at session start, so changing
        TMPDIR mid-session cannot move it; only ``tempfile.*`` follows.
      * ``tempfile.tempdir`` is cached process-wide on first use, so the env
        vars alone are not enough — it is set explicitly here and restored
        in the ``finally``.

    Residual risks (CI is the oracle for the full 5.6k-test suite — torch
    is unavailable locally): a test that spawns a subprocess needing the
    REAL home (none found by grep), and Windows, where ``Path.home()``
    reads USERPROFILE — covered here by redirecting USERPROFILE,
    LOCALAPPDATA and APPDATA as well.
    """
    run_dir = tmp_path_factory.mktemp("runner_home")
    home_dir = run_dir / "home"
    tmp_dir = run_dir / "tmp"
    home_dir.mkdir()
    tmp_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("LOCALAPPDATA", str(home_dir))
    monkeypatch.setenv("APPDATA", str(home_dir))
    monkeypatch.setenv("TMPDIR", str(tmp_dir))
    monkeypatch.setenv("TEMP", str(tmp_dir))
    monkeypatch.setenv("TMP", str(tmp_dir))
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(tmp_dir)
    try:
        yield
    finally:
        tempfile.tempdir = previous_tempdir


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
    explicit db_url/config callers are unaffected by construction.

    BUG-296/cross-test champion leakage (2026-11-09): the CI lane also pins
    NSE_DATABASE__SQLITE_PATH (assert_test_db_isolation.py), and that key
    sits ABOVE the NEXUS_AUDIT_DB seam in load_database_config's precedence
    (NSE_DATABASE__SQLITE_PATH > NEXUS_AUDIT_DB > persisted provider). Every
    champion-registry-writing test in the run therefore shared one job-level
    audit DB even though this fixture redirected NEXUS_AUDIT_DB per run:
    model_bootstrap._champion_binding resolved the job DB, found a CHAMPION
    row another test had just written, and bug296's repair-mint came back
    CHAMPION_GOVERNED (or, in test_market_radar_integration, the trust anchor
    reported HASH_MISMATCH against a Champion fingerprint from a different
    test's artifact). Monkeypatch the path key too so the whole audit seam
    resolves to THIS run's temp file and no cross-test state can leak in.
    """
    run_dir = tmp_path_factory.mktemp("audit_db")
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(run_dir / "audit.db"))
    monkeypatch.setenv("NSE_DATABASE__SQLITE_PATH", str(run_dir / "audit.db"))
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


@pytest.fixture(autouse=True)
def _reap_constructed_audit_repositories(monkeypatch: pytest.MonkeyPatch):
    """Close every ``AuditRepository`` a test constructs, at teardown.

    ``with sqlite3.connect(...)`` (and the repository's own per-call handles)
    only manage the TRANSACTION; the handle lives until the GC reclaims it.
    Tests build real engines over real repositories and never closed them, so
    the suite emitted thousands of ``ResourceWarning: unclosed database``
    lines — one per handle, and on Windows a locked DB file that made the next
    run fail. Nothing here changes production behaviour: it only makes the
    test's ownership explicit. Errors are suppressed because a fixture is not
    the place to fail a test that already passed (the repository's own close()
    already logs partial flushes).
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.settings.service import SettingsDatabase, SettingsService

    created: list[object] = []
    tracked = (AuditRepository, SettingsDatabase, SettingsService)
    for cls in tracked:
        real_init = cls.__init__

        def _tracking_init(self, *args, _real=real_init, **kwargs):
            _real(self, *args, **kwargs)
            created.append(self)

        monkeypatch.setattr(cls, "__init__", _tracking_init)
    try:
        yield
    finally:
        for obj in reversed(created):
            for closer in ("close", "shutdown"):
                fn = getattr(obj, closer, None)
                if callable(fn):
                    with contextlib.suppress(Exception):
                        fn()
                    break


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


@pytest.fixture(scope="session")
def verified_test_model_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One trusted synthetic 70D model per pytest worker, never in artifacts/."""
    from tests.helpers.synthetic_checkpoint import write_verified_trained_bundle

    root = tmp_path_factory.mktemp("verified_model_bundle")
    return write_verified_trained_bundle(root / "model.pt", num_features=70, steps=30)
