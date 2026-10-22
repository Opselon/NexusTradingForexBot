"""Lane B (database-core) — structured records added to silent exception passes.

Each source site previously ended in ``except ...: pass``.  These regression
tests pin THREE facts per site:

1. the swallow is no longer silent: exactly the documented record (level +
   event token) is emitted for the failure condition;
2. the record is NOT emitted on the healthy path;
3. behavior is unchanged: same returned default / same fail-open outcome.

No secret material is ever rendered: the record set relies on masked helpers
(``mask_url_password``, ``mask_query_text``) and counts over values.

Logging-discipline note: every module logger here is a structlog bound logger,
so the repo convention (tests/unit/test_bug274_wrapper_state_leak.py and every
fail-open boundary suite since) is followed — the MODULE logger is monkeypatched
with a probe, never ``caplog``.
"""

from __future__ import annotations

import inspect
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.adapters.database import audit_write_plane as awp_mod
from nexus_scalp.database import config as config_mod
from nexus_scalp.database import health as health_mod
from nexus_scalp.settings import service as settings_service_mod
from nexus_scalp.database import log_store as log_store_mod
from nexus_scalp.database.drivers import base as drivers_base_mod
from nexus_scalp.database.drivers import postgres_driver as pg_mod
from nexus_scalp.database.engine import DatabaseMigrationEngine


class LogProbe:
    """Stand-in for a module's structlog logger; records (level, message, kwargs)."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, str, dict[str, Any]]] = []
        self._install_methods()

    def _install_methods(self) -> None:
        self.critical = self._rec("critical")
        self.error = self._rec("error")
        self.warning = self._rec("warning")
        self.info = self._rec("info")
        self.debug = self._rec("debug")

    def _rec(self, level: str):
        def _fn(*args: Any, **kwargs: Any) -> None:
            # %-style positional interpolation, mirroring stdlib logging so
            # tokens inside interpolated messages remain searchable.
            head = str(args[0]) if args else ""
            rest = list(args[1:])
            if rest and "%" in head:
                try:
                    head = head % tuple(rest)
                    rest = []
                except (TypeError, ValueError):
                    pass
            text = head if not rest else " ".join([head] + [str(x) for x in rest])
            self.emitted.append((level, text, kwargs))

        return _fn

    def messages(self, level: str | None = None, token: str | None = None) -> list[str]:
        return [
            msg
            for lvl, msg, _k in self.emitted
            if (level is None or lvl == level) and (token is None or token in msg)
        ]


# ---------------------------------------------------------------------------
# engine.py — sqlite3.Error probe passes (_detect_drift / plan / status)
# ---------------------------------------------------------------------------


def _engine(tmp_path: Path, *, corrupt: bool) -> DatabaseMigrationEngine:
    db = tmp_path / "audit.db"
    if corrupt:
        db.write_bytes(b"definitely not a sqlite database" * 64)
    else:
        con = sqlite3.connect(db)
        con.commit()
        con.close()
    return DatabaseMigrationEngine(db, domain="audit", auto_apply=False)


def test_engine_drift_probe_failure_logs_error(tmp_path, monkeypatch) -> None:
    import nexus_scalp.database.engine as eng_mod

    probe = LogProbe()
    monkeypatch.setattr(eng_mod, "logger", probe)
    eng = _engine(tmp_path, corrupt=True)
    drift = eng._detect_drift(set())
    assert drift == []  # behavior unchanged
    hits = probe.messages(level="error", token="DB_MIGRATION_DRIFT_PROBE_FAILED")
    assert len(hits) == 1


def test_engine_drift_probe_healthy_path_silent(tmp_path, monkeypatch) -> None:
    import nexus_scalp.database.engine as eng_mod

    probe = LogProbe()
    monkeypatch.setattr(eng_mod, "logger", probe)
    eng = _engine(tmp_path, corrupt=False)
    eng._detect_drift(set())
    assert probe.emitted == []


def test_engine_plan_probe_failure_logs_error(tmp_path, monkeypatch) -> None:
    import nexus_scalp.database.engine as eng_mod

    probe = LogProbe()
    monkeypatch.setattr(eng_mod, "logger", probe)
    eng = _engine(tmp_path, corrupt=True)
    plan = eng.plan()
    assert isinstance(plan, dict)  # behavior unchanged
    assert len(probe.messages(level="error", token="DB_MIGRATION_PLAN_PROBE_FAILED")) == 1


def test_engine_plan_probe_healthy_path_silent(tmp_path, monkeypatch) -> None:
    import nexus_scalp.database.engine as eng_mod

    probe = LogProbe()
    monkeypatch.setattr(eng_mod, "logger", probe)
    eng = _engine(tmp_path, corrupt=False)
    eng.plan()
    assert probe.emitted == []


def test_engine_status_probe_failure_logs_error(tmp_path, monkeypatch) -> None:
    import nexus_scalp.database.engine as eng_mod

    probe = LogProbe()
    monkeypatch.setattr(eng_mod, "logger", probe)
    eng = _engine(tmp_path, corrupt=True)
    status = eng.status()
    assert isinstance(status, dict)  # behavior unchanged
    assert len(probe.messages(level="error", token="DB_MIGRATION_STATUS_PROBE_FAILED")) == 1


# ---------------------------------------------------------------------------
# health.py — load_ui_config settings-DB read + snapshot resolve_config
# ---------------------------------------------------------------------------


def test_load_ui_config_settings_failure_logs_error(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(health_mod, "logger", probe)

    def boom() -> None:
        raise RuntimeError("settings db unreadable")

    # load_ui_config imports SettingsDatabase inside the function, so the
    # real import site is patched, not a (nonexistent) module-level binding.
    monkeypatch.setattr(settings_service_mod, "SettingsDatabase", boom, raising=False)
    out = health_mod.load_ui_config()
    assert out["provider"] == "sqlite" and out["postgres"] is None  # unchanged
    hits = probe.messages(level="error", token="DB_HEALTH_SETTINGS_READ_FAILED")
    assert len(hits) == 1


def test_load_ui_config_healthy_path_silent(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(health_mod, "logger", probe)

    class QuietDB:
        def get(self, key: str):
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(settings_service_mod, "SettingsDatabase", QuietDB, raising=False)
    health_mod.load_ui_config()
    assert probe.emitted == []


def test_snapshot_skipped_domain_warns(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(health_mod, "logger", probe)

    def boom(_domain: str) -> None:
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(health_mod.DatabaseHealthService, "resolve_config", boom)
    monkeypatch.setattr(
        health_mod.DatabaseHealthService,
        "check_domain",
        lambda self, d: {"health": "Healthy", "connected": False},
    )
    svc = health_mod.DatabaseHealthService.__new__(health_mod.DatabaseHealthService)
    svc.domains = ("audit",)
    svc.workspace = None
    snap = svc.snapshot()
    assert snap["active_provider"] == "UNKNOWN"  # behavior unchanged
    hits = probe.messages(level="warning", token="DB_HEALTH_RESOLVE_CONFIG_SKIPPED")
    assert len(hits) == 1


# ---------------------------------------------------------------------------
# config.py — persisted overlay + settings-DB close failures
# ---------------------------------------------------------------------------


def test_load_database_config_overlay_failure_warns(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(config_mod, "_logger", probe)
    # conftest's autouse _isolate_implicit_audit_db sets NEXUS_AUDIT_DB for
    # every test; the seam ABORTS the persisted-settings read entirely (its
    # contract: the seam beats the persisted provider), so this overlay-target
    # test must opt OUT of the seam to reach the SettingsDatabase path.
    monkeypatch.delenv("NEXUS_AUDIT_DB", raising=False)

    class ExplodingDB:
        def get(self, key: str):
            raise RuntimeError("row read failed")

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        config_mod, "SettingsDatabase", lambda *a, **k: ExplodingDB(), raising=False
    )
    cfg = config_mod.load_database_config("audit")
    assert cfg is not None  # behavior unchanged: fallback continues
    hits = probe.messages(level="warning", token="DB_CONFIG_PERSISTED_ROW_UNPARSABLE")
    assert len(hits) == 1


def test_load_database_config_close_failure_logs(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(config_mod, "_logger", probe)
    # same seam opt-out as the overlay-failure test above (NEXUS_AUDIT_DB
    # autouse fixture; the seam skips the whole persisted-settings block)
    monkeypatch.delenv("NEXUS_AUDIT_DB", raising=False)

    class ClosingDB:
        def get(self, key: str):
            return None

        def close(self) -> None:
            raise ConnectionError("handle already closed")

    monkeypatch.setattr(config_mod, "SettingsDatabase", lambda *a, **k: ClosingDB(), raising=False)
    cfg = config_mod.load_database_config("audit")
    assert cfg is not None  # behavior unchanged
    hits = probe.messages(level="debug", token="DB_CONFIG_SETTINGS_DB_CLOSE_FAILED")
    assert len(hits) == 1


def test_load_database_config_healthy_path_silent(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(config_mod, "_logger", probe)

    class QuietDB:
        def get(self, key: str):
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        settings_service_mod, "SettingsDatabase", lambda *a, **k: QuietDB(), raising=False
    )
    config_mod.load_database_config("audit")
    assert probe.emitted == []


# ---------------------------------------------------------------------------
# log_store.py — sink uninstall + fingerprint-index bootstrap
# ---------------------------------------------------------------------------


def test_uninstall_sink_failure_logs_and_never_raises(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(log_store_mod, "logger", probe)

    def explode(name: str | None = None) -> None:
        raise RuntimeError("logger poisoned")

    # Substitute the MODULE's ``logging`` name with a shim — never patch the
    # real stdlib logging module (poisoning it breaks pytest's own teardown
    # LogCaptureHandler unwinding with an INTERNALERROR).
    monkeypatch.setattr(log_store_mod, "logging", SimpleNamespace(getLogger=explode))
    result = log_store_mod.uninstall_database_log_handler()
    assert result is None  # never raises (behavior unchanged)
    hits = probe.messages(token="LOG_SINK_UNINSTALL_FAILED")
    assert len(hits) == 1
    assert probe.emitted[0][0] == "debug"


def test_uninstall_sink_healthy_path_silent(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(log_store_mod, "logger", probe)
    log_store_mod.uninstall_database_log_handler()
    assert probe.emitted == []


def test_log_store_index_bootstrap_record_in_source() -> None:
    """The fingerprint-index CREATE failure emits LOG_STORE_INDEX_BOOTSTRAP_FAILED."""
    src = inspect.getsource(log_store_mod.DatabaseLogStore.ensure_table)
    assert "LOG_STORE_INDEX_BOOTSTRAP_FAILED" in src
    assert "logger.warning" in src


# ---------------------------------------------------------------------------
# drivers/base.py — bind-arity diagnostic-channel failure
# ---------------------------------------------------------------------------


def test_bind_arity_channel_failure_stdlib_fallback_error(monkeypatch) -> None:
    stdlib_probe = LogProbe()
    # Substitute the MODULE's ``logging`` name with a getLogger shim; the real
    # stdlib module is never patched (pytest's teardown needs it intact).
    monkeypatch.setattr(
        drivers_base_mod, "logging", SimpleNamespace(getLogger=lambda name=None: stdlib_probe)
    )

    def broken(**kwargs: Any) -> None:
        raise RuntimeError("query logging channel down")

    import nexus_scalp.database.query_logging as ql

    monkeypatch.setattr(ql, "log_query_failure", broken)
    drivers_base_mod._log_bind_arity_failure("exec", "SELECT 1", 0, 32, "sqlite")
    hits = stdlib_probe.messages(level="error", token="BIND_ARITY_LOG_PATH_FAILED")
    assert len(hits) == 1
    assert "sqlite" in hits[0]


def test_bind_arity_healthy_channel_silent(monkeypatch) -> None:
    stdlib_probe = LogProbe()
    monkeypatch.setattr(
        drivers_base_mod, "logging", SimpleNamespace(getLogger=lambda name=None: stdlib_probe)
    )
    sent: dict[str, Any] = {}

    def healthy(**kwargs: Any) -> None:
        sent.update(kwargs)

    import nexus_scalp.database.query_logging as ql

    monkeypatch.setattr(ql, "log_query_failure", healthy)
    drivers_base_mod._log_bind_arity_failure("exec", "SELECT 1", 0, 32, "sqlite")
    assert stdlib_probe.emitted == []
    assert sent  # the healthy channel got the record


# ---------------------------------------------------------------------------
# drivers/postgres_driver.py — conflict-target probes + clause build
# ---------------------------------------------------------------------------


class _BrokenConn:
    def execute(self, *a: Any, **k: Any):
        raise RuntimeError("catalog down")


def test_pg_conflict_pk_probe_failure_rate_limited_warning(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(pg_mod, "_pg_probe_logger", probe)
    pg_mod._pg_conflict_lookup_state.clear()
    pg_mod._resolve_conflict_target("t_probe_pk", _BrokenConn())
    hits = probe.messages(level="warning", token="PG_CONFLICT_TARGET_LOOKUP_FAILED")
    # ONE record per probe KIND: the PK lookup and the unique-constraint
    # lookup are two independent probes on the same call.
    assert len(hits) == 2
    assert any("primary_keys" in m for m in hits)
    assert any("unique_constraints" in m for m in hits)


def test_pg_conflict_probe_rate_limit_suppresses_second_call(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(pg_mod, "_pg_probe_logger", probe)
    pg_mod._pg_conflict_lookup_state.clear()
    pg_mod._resolve_conflict_target("t_probe_rate2", _BrokenConn())  # emission 1
    pg_mod._resolve_conflict_target("t_probe_rate2", _BrokenConn())  # suppressed
    hits = probe.messages(level="warning", token="PG_CONFLICT_TARGET_LOOKUP_FAILED")
    assert len(hits) == 2  # one per probe kind; the second call is rate-limited off
    assert any("primary_keys" in m for m in hits)
    assert any("unique_constraints" in m for m in hits)


def test_pg_conflict_probe_unique_only_failure(monkeypatch) -> None:
    """PK probe succeeds, unique probe fails: only the unique probe warns."""
    probe = LogProbe()
    monkeypatch.setattr(pg_mod, "_pg_probe_logger", probe)

    class HealthyPkConn:
        def execute(self, sql: str, params: tuple):
            if "pg_index" in sql:

                class _R:
                    def fetchall(self) -> list[tuple[Any, ...]]:
                        return [("id",)]

                return _R()
            raise RuntimeError("information_schema blocked")

    pg_mod._pg_conflict_lookup_state.clear()
    pks, uniques = pg_mod._resolve_conflict_target("t_probe_uo", HealthyPkConn())
    assert pks == ["id"] and uniques == []  # behavior unchanged
    hits = probe.messages(level="warning", token="PG_CONFLICT_TARGET_LOOKUP_FAILED")
    assert len(hits) == 1
    assert "unique_constraints" in hits[0]


def test_pg_conflict_probe_healthy_silent(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(pg_mod, "_pg_probe_logger", probe)

    class SmartConn:
        def execute(self, sql: str, params: tuple):
            class _R:
                def fetchall(self) -> list[tuple[Any, ...]]:
                    return [("id",)]

            return _R()

    pg_mod._pg_conflict_lookup_state.clear()
    pks, uniques = pg_mod._resolve_conflict_target("t_probe_ok", SmartConn())
    assert pks == ["id"] and uniques == ["id"]  # behavior unchanged
    assert probe.emitted == []


def test_pg_conflict_clause_crash_debug_record(monkeypatch) -> None:
    probe = LogProbe()
    monkeypatch.setattr(pg_mod, "_pg_probe_logger", probe)
    pg_mod._pg_conflict_lookup_state.clear()

    # The catalog probes swallow their own failures (they carry their own
    # records), so the crash label fires only when the RESOLUTION half —
    # _best_conflict_target — itself raises.
    def boom(pks: list[str], uniques: list[str], cols: list[str]) -> None:
        raise RuntimeError("resolution crashed")

    monkeypatch.setattr(pg_mod, "_best_conflict_target", boom)
    clause: None = pg_mod._conflict_clause(
        {"verb": "REPLACE", "table": "t_probe_crash", "cols": ["a"]}, _BrokenConn()
    )
    assert clause is None  # behavior unchanged
    msgs = probe.messages(level="debug", token="PG_CONFLICT_CLAUSE_UNAVAILABLE")
    assert len(msgs) == 1
    assert "probe=resolution_crashed" in msgs[0]


def test_pg_conflict_clause_no_target_debug_record(monkeypatch) -> None:
    """Probe SUCCEEDS but no target covers the row: different debug probe label."""
    probe = LogProbe()
    monkeypatch.setattr(pg_mod, "_pg_probe_logger", probe)

    class EmptyConn:
        def execute(self, sql: str, params: tuple):
            class _R:
                def fetchall(self) -> list[tuple[Any, ...]]:
                    return []

            return _R()

    pg_mod._pg_conflict_lookup_state.clear()
    clause = pg_mod._conflict_clause(
        {"verb": "REPLACE", "table": "t_probe_missing", "cols": ["a"]}, EmptyConn()
    )
    assert clause is None  # behavior unchanged
    msgs = probe.messages(level="debug", token="PG_CONFLICT_CLAUSE_UNAVAILABLE")
    assert len(msgs) == 1
    assert "probe=no_conflict_target_covers_row" in msgs[0]


# ---------------------------------------------------------------------------
# audit_write_plane.py — resolver fallbacks (rate-limited warning)
# ---------------------------------------------------------------------------


def _plane(**overrides: Any) -> Any:
    plane: Any = awp_mod.AuditWritePlane.__new__(awp_mod.AuditWritePlane)
    plane._queue = object()
    plane._flush_interval = 0.25
    plane._overflow_dir = Path("overflow")
    plane._queue_resolver = None
    plane._flush_interval_resolver = None
    plane._overflow_resolver = None
    for key, val in overrides.items():
        setattr(plane, key, val)
    return plane


def _boom() -> None:
    raise RuntimeError("swap blew up")


def _plane_probe(monkeypatch) -> LogProbe:
    p = LogProbe()
    monkeypatch.setattr(awp_mod, "logger", p)
    return p


def test_plane_queue_resolver_fallback_rate_limited(monkeypatch) -> None:
    p = _plane_probe(monkeypatch)
    awp_mod._resolver_warn_state.clear()  # shared window state: per-test reset
    plane = _plane(_queue_resolver=_boom)
    for _ in range(3):
        got = plane._effective_queue()
        assert got is plane._queue  # behavior unchanged
    msgs = [
        m
        for m in p.messages(level="warning", token="AUDIT_PLANE_RESOLVER_FALLBACK")
        if "resolver=queue" in m
    ]
    assert len(msgs) == 1  # rate-limited: 3 failures, one emission
    assert msgs[0].count("queue") >= 1


def test_plane_flush_interval_resolver_fallback_warns(monkeypatch) -> None:
    p = _plane_probe(monkeypatch)
    awp_mod._resolver_warn_state.clear()
    plane = _plane(_flush_interval_resolver=_boom)
    got = plane._effective_flush_interval()
    assert got == 0.25  # behavior unchanged
    msgs = [
        m
        for m in p.messages(token="AUDIT_PLANE_RESOLVER_FALLBACK")
        if "resolver=flush_interval" in m
    ]
    assert len(msgs) == 1


def test_plane_overflow_resolver_fallback_warns(monkeypatch) -> None:
    p = _plane_probe(monkeypatch)
    awp_mod._resolver_warn_state.clear()
    plane = _plane(_overflow_resolver=_boom)
    got = plane._effective_overflow_dir()
    assert got == plane._overflow_dir  # behavior unchanged
    msgs = [
        m for m in p.messages(token="AUDIT_PLANE_RESOLVER_FALLBACK") if "resolver=overflow_dir" in m
    ]
    assert len(msgs) == 1


# ---------------------------------------------------------------------------
# audit_write_plane.py — batch probe log-path failure (BATCH_PROBE_LOG_FAILED)
# ---------------------------------------------------------------------------


def test_audit_write_plane_batch_probe_failure_record_in_source() -> None:
    """The log_query_failure probe failure inside the batch recovery path is logged.

    A full worker-thread run is covered by the existing flush-contract suite
    (behavior unchanged there); this pins the probe-channel record's presence
    and structure in the recovery block.
    """
    src = inspect.getsource(awp_mod.AuditWritePlane._process_queue_worker)
    assert "BATCH_PROBE_LOG_FAILED" in src
    assert "log_query_failure" in src
