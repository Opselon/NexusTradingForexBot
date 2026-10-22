"""Lane B — silent-pass observability: failure-path log records (database core).

Every guarded ``except`` in this lane used to swallow its failure; the fix adds
one structured record per guarded operation. Each test here drives the guarded
operation so it raises, then asserts:

  1. the fix's event token appears in the captured log records, and
  2. the behavior is UNCHANGED — the operation still returns the same default
     it returned while the failure was silent.

Where the real failure path is impractical to reach end-to-end (private
sqlite3 seams, provider-bound SQL builders), the test drives the NARROWEST
injectable seam around the guarded site and the docstring names the seam.
Rate-limited emissions are de-duplicated per process, so tests that share a
token clear the relevant rate-limit state first.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import nexus_scalp.database.drivers.base as db_base_module
from nexus_scalp.adapters.database import audit_write_plane
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.database.engine import DatabaseMigrationEngine


@pytest.fixture()
def records(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> pytest.LogCaptureFixture:
    """Capture the project's structured logger around one test.

    The project's ``get_logger`` returns a structlog proxy. Outside the
    application's own ``configure_logging`` these loggers print only; the
    fixture wires structlog through the stdlib logger factory exactly as the
    application does, so caplog sees the same records the production sink
    sees.
    """
    from nexus_scalp.observability.logging import configure_logging

    configure_logging(log_to_file=False, log_level="DEBUG")
    caplog.set_level(logging.DEBUG)
    return caplog


def _assert_token(records: pytest.LogCaptureFixture, token: str) -> None:
    found = _only(records, lambda r: f"event={token}" in r.getMessage())
    assert found, f"expected structured record with event={token}; captured:\n" + "\n".join(
        f"{r.name} {r.levelname} {r.getMessage()}" for r in records.records
    )


def _only(records: pytest.LogCaptureFixture, pred: Any) -> list[logging.LogRecord]:
    return [r for r in records.records if pred(r)]


def error_type_of(message: str) -> str:
    m = re.search(r"error=(\w+)", message)
    return m.group(1) if m else ""


class _LogPuddle:
    """A write-shaped backend sentinel (no ``query`` attr -> refused)."""


# ---------------------------------------------------------------------------
# 1. DB_MIGRATION_PLAN_PROBE_FAILED — engine.plan() with a dead probe
# ---------------------------------------------------------------------------


def test_db_migration_plan_probe_failed_logged_and_plan_unchanged(
    records: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """_connect() failing leaves applied_ids empty; the plan is still returned.

    Seam: DatabaseMigrationEngine._connect — the exact connection the plan
    probe opens (and the narrowest injectable point before the plan rebuild).
    """
    eng = DatabaseMigrationEngine(db_path=tmp_path / "mig_plan.db", domain="candle_intel")
    with patch.object(DatabaseMigrationEngine, "_connect", side_effect=sqlite3.Error("probe down")):
        plan = eng.plan()
    _assert_token(records, "DB_MIGRATION_PLAN_PROBE_FAILED")
    # Behavior unchanged: a plan dict with sane defaults still comes back.
    assert plan["database"] == "candle_intel"
    assert isinstance(plan["pending"], list)
    assert "migration_state" in plan


# ---------------------------------------------------------------------------
# 2. DB_MIGRATION_STATUS_PROBE_FAILED — engine.status() with a dead probe
# ---------------------------------------------------------------------------


def test_db_migration_status_probe_failed_logged_and_sentinels_unchanged(
    records: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """A failed history probe keeps history_count=0 and last_migration={}.

    Seam: the same _connect injection; status() is plan() + probe, so the
    failure event of the STATUS stage is asserted here specifically.
    """
    eng = DatabaseMigrationEngine(db_path=tmp_path / "mig_status.db", domain="marketplace")
    with patch.object(DatabaseMigrationEngine, "_connect", side_effect=sqlite3.Error("probe down")):
        st = eng.status()
    _assert_token(records, "DB_MIGRATION_STATUS_PROBE_FAILED")
    # Behavior unchanged: the same sentinels a silent failure produced.
    assert st["history_count"] == 0
    assert st["last_migration"] == {}


# ---------------------------------------------------------------------------
# 3. DB_MIGRATION_DRIFT_PROBE_FAILED — status() drift verification degraded
# ---------------------------------------------------------------------------


def test_db_migration_drift_probe_failed_logged_and_drift_empty(
    records: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """An empty drift list is NOT proof of a clean schema — the record says so."""
    eng = DatabaseMigrationEngine(db_path=tmp_path / "mig_drift.db", domain="marketplace")
    with patch.object(DatabaseMigrationEngine, "_connect", side_effect=sqlite3.Error("probe down")):
        st = eng.status()
    _assert_token(records, "DB_MIGRATION_DRIFT_PROBE_FAILED")
    # Behavior unchanged: drift is still the empty list the silent path gave.
    assert st["drift"] == []


# ---------------------------------------------------------------------------
# 4. PG_CONFLICT_TARGET_LOOKUP_FAILED — rate-limited catalog-probe warning
# ---------------------------------------------------------------------------


def _clear_pg_rate_limit_state() -> None:
    from nexus_scalp.database.drivers import postgres_driver

    postgres_driver._pg_conflict_lookup_state.clear()


def test_pg_conflict_target_lookup_failed_logged_rate_limited(
    records: pytest.LogCaptureFixture,
) -> None:
    """One probe failure emits the rate-limited lookup event (once per window).

    Seam: the rate-limited warning helper the two failing catalog probes call
    (the same calls the real driver makes when its pk/unique queries raise);
    the suppressor is module state keyed by (event, table:kind), cleared here
    so this test owns its own emission.
    """
    _clear_pg_rate_limit_state()
    from nexus_scalp.database.drivers.postgres_driver import (
        _pg_probe_logger,
        _pg_rate_limited_probe_warning,
    )

    with patch.object(_pg_probe_logger, "warning") as warn:
        _pg_rate_limited_probe_warning(
            "PG_CONFLICT_TARGET_LOOKUP_FAILED", "audit_orders", "primary_keys"
        )
    warn.assert_called_once()
    rendered = warn.call_args.args[0] % warn.call_args.args[1:]
    assert "event=PG_CONFLICT_TARGET_LOOKUP_FAILED" in rendered
    assert "table=audit_orders" in rendered
    # Rate-limited contract intact: a second call inside the window is silent.
    with patch.object(_pg_probe_logger, "warning") as warn2:
        _pg_rate_limited_probe_warning(
            "PG_CONFLICT_TARGET_LOOKUP_FAILED", "audit_orders", "primary_keys"
        )
    warn2.assert_not_called()


# ---------------------------------------------------------------------------
# 5. PG_CONFLICT_CLAUSE_UNAVAILABLE — clause-build crash / uncovered row
# ---------------------------------------------------------------------------


class _PickingConn:
    """A psycopg-style connection whose catalog probes raise (narrow seam:
    the two catalog queries inside _resolve_conflict_target run on it)."""

    def __init__(self, fail_on: str) -> None:
        self._fail_on = fail_on

    def execute(self, sql: str, args: Any = None) -> Any:
        if self._fail_on == "both" or self._fail_on.lower() in sql.lower():
            raise sqlite3.Error("catalog unavailable")
        return iter(())


def test_pg_conflict_clause_unavailable_logged(
    records: pytest.LogCaptureFixture,
) -> None:
    """A crashed clause build returns None without an ON CONFLICT clause.

    Seam: _conflict_clause with a raising connection. The record is
    debug-level; behavior (None return) holds, exactly as the silent path.
    """
    _clear_pg_rate_limit_state()
    from nexus_scalp.database.drivers.postgres_driver import _conflict_clause

    parsed = {
        "table": "audit_orders",
        "verb": "REPLACE",
        "cols": ["ticket", "order_id", "payload"],
    }
    conn = _PickingConn("both")
    # The real record goes through caplog; keep the logger unpatched but
    # DEBUG-armed (its level is DEBUG-by-default under configure_logging).
    clause = _conflict_clause(parsed, conn)
    assert clause is None
    cli_records = [
        r for r in records.records if "event=PG_CONFLICT_CLAUSE_UNAVAILABLE" in r.getMessage()
    ]
    assert cli_records, "expected the PG_CONFLICT_CLAUSE_UNAVAILABLE debug record"


# ---------------------------------------------------------------------------
# 6. BIND_ARITY_LOG_PATH_FAILED — stdlib fallback when the diagnostic
#    channel itself raises
# ---------------------------------------------------------------------------


def test_bind_arity_log_path_failed_logged(records: pytest.LogCaptureFixture) -> None:
    """When log_query_failure raises, one stdlib ERROR record survives.

    Seam: nexus_scalp.database.query_logging.log_query_failure — the exact
    channel _log_bind_arity_failure reports through.
    """
    fallback_logger = logging.getLogger("nexus_scalp.database.drivers.base")
    with (
        patch(
            "nexus_scalp.database.query_logging.log_query_failure",
            side_effect=sqlite3.Error("log store down"),
        ),
        patch.object(fallback_logger, "error") as err,
    ):
        db_base_module._log_bind_arity_failure(
            "execute_upsert", "INSERT INTO x", 0, 4, "postgresql"
        )
    assert err.call_count >= 1
    assert "event=BIND_ARITY_LOG_PATH_FAILED" in err.call_args.args[0]


# ---------------------------------------------------------------------------
# 7-8. DB_FABRIC_READ_PLANE_LOOKUP_FAILED / DB_FABRIC_READ_PLANE_BOOTSTRAP_FAILED
# ---------------------------------------------------------------------------


def _shape_repo() -> AuditRepository:
    """The narrowest instance shape the read-plane helper touches."""
    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    return repo


def test_db_fabric_read_plane_lookup_failed_logged(
    records: pytest.LogCaptureFixture,
) -> None:
    """_registered_audit_read_plane with a failing accessor logs the lookup
    stage and still returns None (documented default preserved)."""
    repo = _shape_repo()
    with (
        patch(
            "nexus_scalp.database.fabric.get_domain_backend",
            side_effect=RuntimeError("fabric registry closed"),
        ),
        patch(
            "nexus_scalp.database.ops_provider.ensure_read_plane",
            side_effect=RuntimeError("bootstrap down"),
        ),
    ):
        backend = repo._registered_audit_read_plane()
    fab_audit_logs = _only(
        records,
        lambda r: "event=DB_FABRIC_READ_PLANE_LOOKUP_FAILED" in r.getMessage(),
    )[0].getMessage()
    assert error_type_of(fab_audit_logs) == "RuntimeError"
    _assert_token(records, "DB_FABRIC_READ_PLANE_BOOTSTRAP_FAILED")
    # Behavior unchanged: still None after both stages fail.
    assert backend is None


def test_db_fabric_read_plane_bootstrap_failed_logged_only(
    records: pytest.LogCaptureFixture,
) -> None:
    """An empty registry + crashing bootstrap emits ONLY the bootstrap stage."""
    repo = _shape_repo()
    # No registered probe stage: an empty registry means get_domain_backend
    # resolves to None (normal 'not provisioned yet'), so ONLY bootstrap fails.
    from nexus_scalp.database import fabric as fabric_mod

    with patch.dict(fabric_mod._DOMAIN_BACKENDS, {}, clear=False):
        fabric_mod._DOMAIN_BACKENDS.pop("audit", None)
        with patch(
            "nexus_scalp.database.ops_provider.ensure_read_plane",
            side_effect=RuntimeError("bootstrap down"),
        ):
            backend = repo._registered_audit_read_plane()
    fab_records = _only(records, lambda r: "event=DB_FABRIC" in r.getMessage())
    assert any("BOOTSTRAP_FAILED" in r.getMessage() for r in fab_records)
    assert not any("LOOKUP_FAILED" in r.getMessage() for r in fab_records)
    assert backend is None


# ---------------------------------------------------------------------------
# 9. AUDIT_PLANE_RESOLVER_FALLBACK — queue resolver fallback logged
# ---------------------------------------------------------------------------


def test_audit_plane_resolver_fallback_logged(
    records: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """A raising queue_resolver falls back to the constructor queue once.

    Seam: AuditWritePlane's queue_resolver — the exact runtime-swap guard the
    fix instrumented. The rate-limit state is per-resolver module state and is
    cleared here; the constructor-frozen queue is returned (unchanged).
    """
    audit_write_plane._resolver_warn_state.clear()
    sentinel_dead_letter: Any = _LogPuddle()  # unused; start() never called
    plane = audit_write_plane.AuditWritePlane(
        backend_factory=lambda: None,
        dead_letter_store=sentinel_dead_letter,
        overflow_dir=tmp_path / "ovf",
        queue_resolver=lambda: (_ for _ in ()).throw(RuntimeError("resolver dead")),
    )
    resolved = plane._effective_queue()
    assert resolved is plane._queue  # constructor-frozen value (unchanged)
    _assert_token(records, "AUDIT_PLANE_RESOLVER_FALLBACK")
    msg = _only(
        records,
        lambda r: "event=AUDIT_PLANE_RESOLVER_FALLBACK" in r.getMessage(),
    )[0].getMessage()
    assert "resolver=queue" in msg


# ---------------------------------------------------------------------------
# 10. DB_HEALTH_RESOLVE_CONFIG_SKIPPED — snapshot() excludes an unresolvable
#     domain from the configured-provider set (named, not silent)
# ---------------------------------------------------------------------------


def test_db_health_resolve_config_skipped_logged(
    records: pytest.LogCaptureFixture,
) -> None:
    """snapshot() with resolve_config raising excludes the domain and warns.

    Seam: DatabaseHealthService.resolve_config (the exact call snapshot makes
    per domain). check_domain is reduced to its snapshot outputs so the
    domain-set path is what is under test.
    """
    from nexus_scalp.database import health as health_mod

    svc = health_mod.DatabaseHealthService.__new__(health_mod.DatabaseHealthService)
    svc.domains = ("audit",)
    svc.workspace = None
    with (
        patch.object(
            health_mod.DatabaseHealthService,
            "resolve_config",
            side_effect=sqlite3.Error("config down"),
        ),
        patch.object(
            health_mod.DatabaseHealthService,
            "check_domain",
            lambda self, d: {"health": "Warning", "connected": False},
        ),
    ):
        snap = svc.snapshot()
    _assert_token(records, "DB_HEALTH_RESOLVE_CONFIG_SKIPPED")
    msg = _only(
        records,
        lambda r: "event=DB_HEALTH_RESOLVE_CONFIG_SKIPPED" in r.getMessage(),
    )[0].getMessage()
    assert "domain=audit" in msg
    # Behavior unchanged: the configured set is empty -> UNKNOWN active.
    assert snap["active_provider"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# 11. DB_HEALTH_SETTINGS_READ_FAILED — load_ui_config on a dead settings DB
# ---------------------------------------------------------------------------


def test_db_health_settings_read_failed_logged(
    records: pytest.LogCaptureFixture,
) -> None:
    """load_ui_config with an unreadable settings DB keeps the sqlite default
    declines and emits the error record naming the unverified payload."""
    from nexus_scalp.database import health as health_mod
    from nexus_scalp.settings import service as settings_service_mod

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("settings db unreadable")

    with patch.object(health_mod, "SettingsDatabase", new=boom):
        out = health_mod.load_ui_config()
    _assert_token(records, "DB_HEALTH_SETTINGS_READ_FAILED")
    # Behavior unchanged: provider/postgres stay the documented defaults.
    assert out["provider"] == "sqlite"
    assert out["postgres"] is None


# ---------------------------------------------------------------------------
# 12. LOG_STORE_INDEX_BOOTSTRAP_FAILED — dedup index bootstrap degraded
# ---------------------------------------------------------------------------


def test_log_store_index_bootstrap_failed_logged(
    records: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """When the fingerprint/last_seen_at index creation fails, the warning
    names the degradation; the table is still created (behavior unchanged)."""
    from nexus_scalp.database.config import DatabaseConfig
    from nexus_scalp.database.log_store import DatabaseLogStore

    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "lsidx.db"))
    store = DatabaseLogStore(config=cfg)

    class _IdxFailDriver:
        name = "sqlite"

        def __init__(self) -> None:
            self.executed: list[str] = []

        def execute(self, sql: str, args: Any = None) -> None:
            self.executed.append(sql)
            # Fail ONLY the late (fingerprint, last_seen_at) index statement --
            # the guarded CREATE the fix wraps. Every other DDL succeeds.
            if "_fp " in sql:
                raise sqlite3.Error("index bootstrap down")

        def query(self, sql: str, args: Any = None) -> list[dict[str, Any]]:
            if "pragma_table_info" in sql:
                return [{"name": "fingerprint"}, {"name": "last_seen_at"}]
            return []

        def close(self) -> None:
            return None

    driver = _IdxFailDriver()
    store.ensure_table(driver)
    _assert_token(records, "LOG_STORE_INDEX_BOOTSTRAP_FAILED")
    # Behavior unchanged: the CREATE TABLE went through before the failure.
    assert any("CREATE TABLE" in s for s in driver.executed)


# ---------------------------------------------------------------------------
# 13. DB_CONFIG_PERSISTED_ROW_UNPARSABLE — persisted overlay read/apply failed
# ---------------------------------------------------------------------------


def test_db_config_persisted_row_unparsable_logged(
    records: pytest.LogCaptureFixture,
) -> None:
    """A settings-DB failure under a NON-audit domain still yields a config.

    Behavior unchanged: the YAML/SQLite-fallback config is returned, so an
    operator's persisted provider is skipped ONLY with the warning attached.
    (Domain 'news': the audit test-isolation seam NEXUS_AUDIT_DB legitimately
    suppresses the persisted block for 'audit', which is that seam's
    documented behavior — the overlay warning belongs to non-seam domains.)
    """
    from nexus_scalp.database import config as config_mod
    from nexus_scalp.settings import service as settings_service_mod

    class ExplodingDB:
        def get(self, key: str) -> None:
            raise RuntimeError("row read failed")

        def close(self) -> None:
            return None

    with patch.object(config_mod, "SettingsDatabase", lambda *a, **k: ExplodingDB()):
        cfg = config_mod.load_database_config("news")
    _assert_token(records, "DB_CONFIG_PERSISTED_ROW_UNPARSABLE")
    # The log_store DEBUG record (nested config resolve during persist) can
    # reach caplog alongside this test's own record; select by domain tag
    # rather than the first token hit.
    msg = _only(
        records,
        lambda r: (
            "event=DB_CONFIG_PERSISTED_ROW_UNPARSABLE" in r.getMessage()
            and "domain=news" in r.getMessage()
        ),
    )[-1].getMessage()
    assert "domain=news" in msg
    assert cfg is not None  # fallback config still resolved
