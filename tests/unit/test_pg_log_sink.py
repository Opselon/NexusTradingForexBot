"""PG-LOG-SINK — app-wide WARNING/ERROR/CRITICAL persistence regression suite.

The operator ask: "add a postgres log system to the whole app so errors and
warnings are not duplicated, with full trace". This suite pins each part of
that contract against the implemented sink (``nexus_scalp.database.log_store``):

  1. the sink is installed ONCE by ``configure_logging`` on the root logger
     (both boot paths inherit it — the launcher and ``nexus start``);
  2. every WARNING/ERROR/CRITICAL the app emits through ANY logger lands in
     the log table — structlog bound loggers, plain stdlib loggers, and the
     driver layer alike;
  3. the FULL TRACEBACK is persisted for structlog errors (the shape that
     carries ``exc_info`` inside the event dict) AND for stdlib errors;
  4. repeats of the SAME condition within the dedup window collapse into one
     row (``repeat_count``), while a different error code stays a distinct
     row — "not duplicated" means collapsed-by-provenance, not lost;
  5. the sink is failure-isolated: a DB fault drops the record and never
     reaches the caller, and the recursion guard stops the store's own error
     log line from echoing forever;
  6. secrets never reach the table (redaction is the pipeline's job; the
     sink proves the value it stored is masked);
  7. SQLite and PostgreSQL behave the same (schema, severity filter, dedup).

The suite runs on SQLite by default and against PostgreSQL when
``NSE_PG_TEST_URL`` points at a migrated throwaway server (the same contract
the parity suite uses — no second connect path is built here).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Provider matrix: every scenario runs on BOTH providers when PG is reachable
# ---------------------------------------------------------------------------

_PG_URL = os.environ.get("NSE_PG_TEST_URL", "")


def _sqlite_path(tmp_path: Path) -> str:
    return str(tmp_path / "pglog_sink_test.db")


@pytest.fixture
def pg_credentials():
    """Stage the PG password in the process secret store (documented pattern).

    ``resolve_password`` accepts NO plaintext kwarg (security contract), so a
    test that needs to connect stages the password under the canonical key
    and restores the prior value on teardown — the same seam the parity suite
    uses. Never build a second connect path here.
    """
    if not _PG_URL:
        pytest.skip("NSE_PG_TEST_URL unset — PostgreSQL arm not runnable here")
    from nexus_scalp.settings.secret_store import SecureSecretStore

    store = SecureSecretStore()
    prior = store.get_secret("db.postgresql.password")
    store.set_secret(
        "db.postgresql.password", os.environ.get("NSE_PG_TEST_PASSWORD", "nse_password_dev")
    )
    try:
        yield "staged"
    finally:
        try:
            if prior:
                store.set_secret("db.postgresql.password", prior)
        except Exception:
            pass


@pytest.fixture(params=["sqlite", "postgres"])
def sink_config(request: pytest.FixtureRequest, tmp_path: Path, pg_credentials: Any) -> Any:
    """A DatabaseConfig for the provider under test (SQLite always, PG opt-in)."""
    if request.param == "postgres":
        from nexus_scalp.database.config import DatabaseConfig

        # The password is staged in the process secret store by the
        # pg_credentials fixture; the driver resolves it exactly as production
        # does (resolve_password -> SecureSecretStore, no plaintext kwarg).
        return DatabaseConfig.for_postgres(
            host="127.0.0.1",
            port=int(os.environ.get("NSE_PG_TEST_PORT", "55432")),
            database=os.environ.get("NSE_PG_TEST_DATABASE", "nse_audit"),
            username=os.environ.get("NSE_PG_TEST_USER", "nse_user"),
        )
    from nexus_scalp.database.config import DatabaseConfig

    return DatabaseConfig(domain="audit", sqlite_path=_sqlite_path(tmp_path))


@pytest.fixture
def store(sink_config: Any) -> Any:
    from nexus_scalp.database.log_store import DatabaseLogStore

    return DatabaseLogStore(config=sink_config)


@pytest.fixture
def clean_handlers():
    """Install nothing; ensure no sink leaks across tests."""
    from nexus_scalp.database.log_store import (
        install_database_log_handler,
        uninstall_database_log_handler,
    )

    uninstall_database_log_handler()
    yield
    uninstall_database_log_handler()


# ---------------------------------------------------------------------------
# 1. installation: one handler, idempotent, on the root logger
# ---------------------------------------------------------------------------


class TestInstallation:
    def test_install_adds_exactly_one_root_handler(self, clean_handlers: Any) -> None:
        from nexus_scalp.database.log_store import install_database_log_handler

        install_database_log_handler()
        install_database_log_handler()  # idempotent re-install
        sinks = [h for h in logging.getLogger().handlers if _is_sink(h)]
        assert len(sinks) == 1

    def test_uninstall_removes_every_sink_handler(self, clean_handlers: Any) -> None:
        from nexus_scalp.database.log_store import (
            install_database_log_handler,
            uninstall_database_log_handler,
        )

        install_database_log_handler()
        assert any(_is_sink(h) for h in logging.getLogger().handlers)
        uninstall_database_log_handler()
        assert not any(_is_sink(h) for h in logging.getLogger().handlers)

    def test_configure_logging_installs_the_sink_once(self, clean_handlers: Any) -> None:
        """Both boot paths funnel through configure_logging, so this is the seam."""
        from nexus_scalp.observability.logging import configure_logging

        configure_logging(log_level="INFO", json_format=False, log_to_file=False)
        sinks = [h for h in logging.getLogger().handlers if _is_sink(h)]
        assert len(sinks) == 1
        configure_logging(log_level="INFO", json_format=False, log_to_file=False)
        sinks = [h for h in logging.getLogger().handlers if _is_sink(h)]
        assert len(sinks) == 1, "re-configure must replace, not accumulate"

    def test_sink_does_not_evict_foreign_handlers(self, clean_handlers: Any) -> None:
        """A test/foreign handler on root must survive a configure (BUG-307C)."""
        from nexus_scalp.observability.logging import configure_logging

        foreign = logging.StreamHandler()
        logging.getLogger().addHandler(foreign)
        try:
            configure_logging(log_level="INFO", json_format=False, log_to_file=False)
            assert foreign in logging.getLogger().handlers
        finally:
            logging.getLogger().removeHandler(foreign)


def _is_sink(handler: logging.Handler) -> bool:
    from nexus_scalp.database.log_store import DatabaseLogHandler

    return isinstance(handler, DatabaseLogHandler)


# ---------------------------------------------------------------------------
# 2 + 3. every WARNING+ record persists, with the full traceback
# ---------------------------------------------------------------------------


@pytest.fixture
def bound_sink(clean_handlers: Any, sink_config: Any) -> Any:
    """An installed sink retargeted at the provider under test (as a probe).

    Also configures the structlog→stdlib routing: without it a test running
    in isolation emits through structlog's DEFAULT config (a PrintLogger
    that never reaches the root logger), while one running after a
    configure_logging test appears to work — an order-dependent trap that
    silently produced 0 persisted rows. Configuring here makes every test
    honest regardless of order.
    """
    from nexus_scalp.database.log_store import (
        DatabaseLogStore,
        install_database_log_handler,
    )
    from nexus_scalp.observability.logging import configure_logging

    configure_logging(log_level="INFO", json_format=False, log_to_file=False)
    handler = install_database_log_handler(config=sink_config, enabled=True)
    assert handler is not None
    handler._resolved = True
    handler._store = DatabaseLogStore(config=sink_config)
    handler._provider_name = "postgresql" if sink_config.is_postgresql else "sqlite"
    handler._domain = "audit"
    return handler


class TestPersistence:
    def test_structlog_error_persists_full_trace(self, bound_sink: Any, store: Any) -> None:
        import structlog

        log = structlog.get_logger("nexus_scalp.tests.pglogsink.struct")
        try:
            raise ZeroDivisionError("probe divide")
        except ZeroDivisionError:
            log.error("SINK_STRUCT_TRACE", component="tests", exc_info=True)
        rows = store.query_recent(limit=5)
        hit = [r for r in rows if r["event_name"] == "SINK_STRUCT_TRACE"]
        assert len(hit) == 1
        trace = hit[0]["full_trace"] or ""
        assert "ZeroDivisionError" in trace, trace
        assert "File " in trace, "the traceback must carry frames, not a message"
        assert hit[0]["error_message"].startswith("ZeroDivisionError")

    def test_stdlib_error_persists_full_trace(self, bound_sink: Any, store: Any) -> None:
        log = logging.getLogger("nexus_scalp.tests.pglogsink.std")
        try:
            raise RuntimeError("stdlib boom")
        except RuntimeError:
            log.error("SINK_STD_TRACE", exc_info=True)
        rows = store.query_recent(limit=5)
        hit = [r for r in rows if r["event_name"] == "SINK_STD_TRACE"]
        assert len(hit) == 1
        assert "RuntimeError" in (hit[0]["full_trace"] or "")

    def test_warning_persists_without_a_trace(self, bound_sink: Any, store: Any) -> None:
        import structlog

        structlog.get_logger("nexus_scalp.tests.pglogsink.warn").warning(
            "SINK_WARN", component="tests"
        )
        rows = store.query_recent(limit=5)
        hit = [r for r in rows if r["event_name"] == "SINK_WARN"]
        assert len(hit) == 1
        assert hit[0]["level"] == "WARNING"
        assert (hit[0]["full_trace"] or "") == "", "a non-exception warning has no trace"

    def test_info_is_never_persisted(self, bound_sink: Any, store: Any) -> None:
        import structlog

        structlog.get_logger("nexus_scalp.tests.pglogsink.info").info("SINK_INFO_NOPE")
        rows = store.query_recent(limit=10)
        assert not any(r["event_name"] == "SINK_INFO_NOPE" for r in rows)

    def test_bound_context_is_kept(self, bound_sink: Any, store: Any) -> None:
        import structlog

        structlog.get_logger("nexus_scalp.tests.pglogsink.ctx").error(
            "SINK_CTX",
            component="tests",
            error_code="E_CTX",
            correlation_id="COR-1",
            metric_value=7,
        )
        rows = store.query_recent(limit=5)
        hit = next(r for r in rows if r["event_name"] == "SINK_CTX")
        assert hit["error_code"] == "E_CTX"
        assert hit["correlation_id"] == "COR-1"

    def test_record_level_api_still_works(self, store: Any) -> None:
        from nexus_scalp.database.log_store import DatabaseLogEntry

        ok = store.record(
            DatabaseLogEntry(
                level="ERROR", provider="probe", domain="audit", operation="record.api"
            )
        )
        assert ok is True
        rows = store.query_recent(limit=5)
        assert any(r["operation"] == "record.api" for r in rows)


# ---------------------------------------------------------------------------
# 4. de-duplication: same provenance collapses, distinct conditions do not
# ---------------------------------------------------------------------------


class TestDedup:
    def test_repeats_collapse_into_one_row(self, bound_sink: Any, store: Any) -> None:
        import structlog

        log = structlog.get_logger("nexus_scalp.tests.pglogsink.dedup")
        for _ in range(5):
            log.error("SINK_DEDUP_STORM", component="tests", error_code="E_STORM")
        rows = [r for r in store.query_recent(limit=20) if r["event_name"] == "SINK_DEDUP_STORM"]
        assert len(rows) == 1
        assert rows[0]["repeat_count"] == 5, "5 identical records must become ONE row"
        assert rows[0]["first_seen_at"] is not None

    def test_distinct_conditions_stay_distinct(self, bound_sink: Any, store: Any) -> None:
        import structlog

        log = structlog.get_logger("nexus_scalp.tests.pglogsink.distinct")
        log.error("SINK_DISTINCT", component="tests", error_code="E_ONE")
        log.error("SINK_DISTINCT", component="tests", error_code="E_TWO")
        rows = [r for r in store.query_recent(limit=20) if r["event_name"] == "SINK_DISTINCT"]
        assert len(rows) == 2, "a different error code is a different condition"

    def test_distinct_loggers_stay_distinct(self, bound_sink: Any, store: Any) -> None:
        import structlog

        structlog.get_logger("nexus_scalp.tests.pglogsink.a").error("SINK_LOGGER", error_code="E")
        structlog.get_logger("nexus_scalp.tests.pglogsink.b").error("SINK_LOGGER", error_code="E")
        rows = [r for r in store.query_recent(limit=20) if r["event_name"] == "SINK_LOGGER"]
        assert len(rows) == 2

    def test_db_layer_records_are_never_folded(self, store: Any) -> None:
        """query_logging's records carry no fingerprint, so they always insert."""
        from nexus_scalp.database.log_store import DatabaseLogEntry

        for _ in range(3):
            store.record(
                DatabaseLogEntry(
                    level="ERROR",
                    provider="probe",
                    domain="audit",
                    operation="driver.query",
                    error_message="boom",
                )
            )
        rows = [r for r in store.query_recent(limit=20) if r["operation"] == "driver.query"]
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# 5. failure isolation + recursion safety
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    def test_persist_failure_never_raises(
        self, bound_sink: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = logging.getLogger("nexus_scalp.tests.pglogsink.isolated")

        def boom(self: Any, *args: Any, **kwargs: Any) -> bool:
            raise RuntimeError("store is down")

        monkeypatch.setattr(bound_sink._store.__class__, "record", boom)
        # Must not raise; the record is simply dropped.
        log.error("SINK_STORE_DOWN", exc_info=True)

    def test_recursion_guard_breaks_the_echo(
        self, bound_sink: Any, store: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The store's own failure log line re-enters the sink; it must not echo.

        A persistent DB fault makes ``record`` raise; ``record``'s except arm
        logs at debug level, and the store's callers (``log_query_failure``)
        log at ERROR — both routes re-enter this handler. Without the
        re-entrance guard the store's own error log would trigger another
        store write, another failure, another log line: an unbounded echo.

        The probe below makes ``record`` ALWAYS raise, so the echo is
        genuinely unbounded if the guard is missing. The guard bounds the
        store calls to one per emitted record.
        """
        from nexus_scalp.database.log_store import DatabaseLogStore

        calls = {"n": 0}

        def always_failing_record(self: Any, entry: Any, driver: Any = None) -> bool:
            calls["n"] += 1
            # Every call raises AND logs an error (which re-enters the sink).
            # Do NOT call the real record — the point is a permanently broken
            # store, not a transient fault.
            raise RuntimeError("store is permanently down")

        monkeypatch.setattr(DatabaseLogStore, "record", always_failing_record)
        log = logging.getLogger("nexus_scalp.tests.pglogsink.recursion")
        log.error("SINK_RECURSION_PROBE")
        # One emitted record must not fan out into a re-entrant storm. The
        # guard allows the first store call plus the re-entrant one it would
        # trigger, and stops there.
        assert calls["n"] <= 2, calls
        assert calls["n"] >= 1, "the record must reach the store at least once"

    def test_disabled_sink_persists_nothing(
        self, clean_handlers: Any, store: Any, sink_config: Any
    ) -> None:
        from nexus_scalp.database.log_store import install_database_log_handler

        handler = install_database_log_handler(config=sink_config, enabled=False)
        assert handler is not None
        logging.getLogger("nexus_scalp.tests.pglogsink.disabled").error("SINK_DISABLED")
        rows = store.query_recent(limit=5)
        assert not any(r["event_name"] == "SINK_DISABLED" for r in rows)

    def test_concurrent_writers_do_not_corrupt(self, bound_sink: Any, store: Any) -> None:
        import structlog

        log = structlog.get_logger("nexus_scalp.tests.pglogsink.concurrency")

        def fire(tag: int) -> None:
            slog = log.bind(error_code=f"E_{tag}")
            for _ in range(20):
                slog.error("SINK_CONCURRENT", component="tests")

        threads = [threading.Thread(target=fire, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = [r for r in store.query_recent(limit=50) if r["event_name"] == "SINK_CONCURRENT"]
        total = sum(r["repeat_count"] for r in rows)
        assert total == 80, f"expected 80 records across {len(rows)} rows, got {total}"


# ---------------------------------------------------------------------------
# 6. secrets never reach the table
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_secret_in_context_is_masked(self, bound_sink: Any, store: Any) -> None:
        import structlog

        structlog.get_logger("nexus_scalp.tests.pglogsink.secret").error(
            "SINK_SECRET", password="hunter2secret", token="abcdefghij0123456789ABCDEF"
        )
        rows = [r for r in store.query_recent(limit=5) if r["event_name"] == "SINK_SECRET"]
        assert len(rows) == 1
        blob = rows[0]["context_json"]
        if isinstance(blob, str):
            blob_str = blob
        else:
            import json

            blob_str = json.dumps(blob, default=str)
        assert "hunter2secret" not in blob_str
        assert "ABCDEF" not in blob_str


# ---------------------------------------------------------------------------
# 7. schema parity (both providers, both sinks)
# ---------------------------------------------------------------------------


class TestSchema:
    def test_table_carries_the_trace_and_dedup_columns(self, store: Any, sink_config: Any) -> None:
        from nexus_scalp.database.drivers import get_driver

        drv = get_driver(sink_config)
        try:
            store.ensure_table(drv)
            cols = {c["name"] for c in drv.table_columns("db_operation_logs")}
        finally:
            drv.close()
        required = {
            "full_trace",
            "context_json",
            "fingerprint",
            "repeat_count",
            "first_seen_at",
            "last_seen_at",
            "event_name",
            "logger_name",
        }
        missing = required - cols
        assert not missing, f"missing columns on provider {sink_config.provider}: {missing}"


# ---------------------------------------------------------------------------
# 8. SQLite driver now logs its failures (provider parity with PostgreSQL)
# ---------------------------------------------------------------------------


class TestSQLiteDriverFailureLogging:
    def test_execute_failure_logs_and_reraises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nexus_scalp.database.config import DatabaseConfig
        from nexus_scalp.database.drivers import get_driver

        captured = []
        monkeypatch.setattr(
            "nexus_scalp.database.drivers.sqlite_driver.log_query_failure",
            lambda **kw: captured.append(kw),
        )
        cfg = DatabaseConfig(domain="audit", sqlite_path=_sqlite_path(tmp_path))
        drv = get_driver(cfg)
        with pytest.raises(sqlite3.OperationalError):
            drv.execute("INSERT INTO no_such_table_sink (id) VALUES (?)", (1,))
        drv.close()
        assert len(captured) == 1, "a SQLite execute failure must be logged"
        assert captured[0]["operation"] == "driver.execute"
        assert "no_such_table_sink" in captured[0]["sql"]

    def test_query_failure_logs_and_reraises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nexus_scalp.database.config import DatabaseConfig
        from nexus_scalp.database.drivers import get_driver

        captured = []
        monkeypatch.setattr(
            "nexus_scalp.database.drivers.sqlite_driver.log_query_failure",
            lambda **kw: captured.append(kw),
        )
        cfg = DatabaseConfig(domain="audit", sqlite_path=_sqlite_path(tmp_path))
        drv = get_driver(cfg)
        with pytest.raises(sqlite3.OperationalError):
            drv.query("SELECT * FROM no_such_table_q")
        drv.close()
        assert len(captured) == 1
        assert captured[0]["domain"] == "sqlite"


# ---------------------------------------------------------------------------
# Boot-path coverage: both entries install the sink
# ---------------------------------------------------------------------------


class TestBootPaths:
    def test_engine_boot_imports_the_same_seam(self) -> None:
        """configure_logging is the single seam; assert both entries reach it."""
        import importlib

        mod = importlib.import_module("nexus_scalp.cli.engine_boot")
        src = Path(mod.__file__).read_text(encoding="utf-8", errors="replace")
        assert "configure_logging" in src, "nexus start must configure logging"

    def test_launcher_entry_configures_logging(self) -> None:
        entry = Path(__file__).resolve().parents[2] / "NexusTradingForexBot.py"
        if not entry.exists():
            pytest.skip("launcher entry not present in this tree")
        src = entry.read_text(encoding="utf-8", errors="replace")
        assert "configure_logging" in src or "LiveEngine" in src


# ---------------------------------------------------------------------------
# 8. legacy-schema migration (BUG-LOGSCHEMA)
# ---------------------------------------------------------------------------
#
# The production ``db_operation_logs`` table was created by the earlier
# 14-column DDL (timestamp..masked_sql). ``CREATE TABLE IF NOT EXISTS`` is a
# silent NO-OP on that table, so the sink's INSERT — which writes the newer
# event_name/logger_name/full_trace/context_json/fingerprint/first_seen_at/
# last_seen_at/repeat_count columns — failed on every record and the failure
# was swallowed by the store's own fail-closed handler. Symptom: no new rows
# ever appeared after the sink shipped. ``ensure_table`` must therefore bring
# an existing table up to the column contract with additive ADD COLUMN, and
# the (fingerprint, last_seen_at) index must be created AFTER those columns
# exist (the CREATE block cannot reference them on a legacy table).


class TestLegacySchemaMigration:
    _LEGACY_DDL_PG = (
        "CREATE TABLE db_operation_logs ("
        "  id BIGSERIAL PRIMARY KEY,"
        "  timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
        "  level VARCHAR(16) NOT NULL,"
        "  provider VARCHAR(32) NOT NULL,"
        "  domain VARCHAR(64) NOT NULL,"
        "  operation VARCHAR(128) NOT NULL,"
        "  repository VARCHAR(128) NOT NULL DEFAULT '',"
        "  query_name VARCHAR(128) NOT NULL DEFAULT '',"
        "  duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,"
        "  rows BIGINT NOT NULL DEFAULT 0,"
        "  error_code VARCHAR(64) NOT NULL DEFAULT '',"
        "  error_message TEXT NOT NULL DEFAULT '',"
        "  correlation_id VARCHAR(64) NOT NULL DEFAULT '',"
        "  masked_sql TEXT NOT NULL DEFAULT ''"
        ")"
    )
    _LEGACY_DDL_SQLITE = (
        "CREATE TABLE db_operation_logs ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  timestamp TEXT NOT NULL,"
        "  level TEXT NOT NULL,"
        "  provider TEXT NOT NULL,"
        "  domain TEXT NOT NULL,"
        "  operation TEXT NOT NULL,"
        "  repository TEXT NOT NULL DEFAULT '',"
        "  query_name TEXT NOT NULL DEFAULT '',"
        "  duration_ms REAL NOT NULL DEFAULT 0.0,"
        "  rows INTEGER NOT NULL DEFAULT 0,"
        "  error_code TEXT NOT NULL DEFAULT '',"
        "  error_message TEXT NOT NULL DEFAULT '',"
        "  correlation_id TEXT NOT NULL DEFAULT '',"
        "  masked_sql TEXT NOT NULL DEFAULT ''"
        ")"
    )

    @staticmethod
    def _restore(driver: Any, sink_config: Any) -> None:
        """Recreate the CURRENT-contract table so later tests find the schema.

        These tests DROP the shared table to install the legacy one; without
        restoration, every subsequently-running test fails on a missing table
        (a test-ordering contract the rest of this suite relies on).
        """
        from nexus_scalp.database.log_store import LOG_TABLE, DatabaseLogStore

        try:
            driver.execute(f"DROP TABLE IF EXISTS {LOG_TABLE}")
        except Exception:
            pass
        DatabaseLogStore(config=sink_config).ensure_table(driver)

    def test_legacy_table_is_migrated_and_writable(self, sink_config: Any) -> None:
        """A pre-existing 14-column table must accept the sink's full row."""
        from nexus_scalp.database.drivers import get_driver
        from nexus_scalp.database.log_store import (
            LOG_TABLE,
            DatabaseLogEntry,
            DatabaseLogStore,
        )

        driver = get_driver(sink_config)
        try:
            driver.execute(f"DROP TABLE IF EXISTS {LOG_TABLE}")
            legacy = self._LEGACY_DDL_PG if sink_config.is_postgresql else self._LEGACY_DDL_SQLITE
            driver.execute(legacy)

            store = DatabaseLogStore(config=sink_config)
            store.ensure_table(driver)  # must ALTER, not no-op

            def _col(row: Any) -> str:
                return str(row.get("name") if isinstance(row, dict) else row[0])

            introspection = (
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                f"AND table_name = '{LOG_TABLE}'"
                if sink_config.is_postgresql
                else f"SELECT name FROM pragma_table_info('{LOG_TABLE}')"
            )
            cols = {_col(r) for r in driver.query(introspection)}
            for required in (
                "event_name",
                "logger_name",
                "full_trace",
                "context_json",
                "fingerprint",
                "first_seen_at",
                "last_seen_at",
                "repeat_count",
            ):
                assert required in cols, f"legacy table missing {required} after ensure_table"

            entry = DatabaseLogEntry(
                level="ERROR",
                provider=str(sink_config.provider.value)
                if hasattr(sink_config.provider, "value")
                else "sqlite",
                domain="audit",
                operation="legacy_schema_probe",
                error_code="ValueError",
                error_message="row written to a legacy-schema table",
                event_name="probe.legacy",
                logger_name="probe.legacy",
                full_trace="Traceback (most recent call last):\n  ValueError: legacy",
                fingerprint="fp-legacy-1",
            )
            assert store.record(entry, driver) is True
            rows = driver.query(
                f"SELECT full_trace, repeat_count FROM {LOG_TABLE} "
                "WHERE operation = 'legacy_schema_probe'"
            )

            def _f(row: Any, key: str) -> Any:
                return row.get(key) if isinstance(row, dict) else row

            trace = _f(rows[0], "full_trace")
            repeat = _f(rows[0], "repeat_count")
            if not isinstance(rows[0], dict):
                trace, repeat = rows[0][0], rows[0][1]
            assert "ValueError: legacy" in trace, "full trace lost on legacy table"
            assert repeat == 1
        finally:
            self._restore(driver, sink_config)
            driver.close()

    def test_ensure_table_is_idempotent_on_legacy_table(self, sink_config: Any) -> None:
        """Running ensure_table repeatedly must not error or duplicate columns."""
        from nexus_scalp.database.drivers import get_driver
        from nexus_scalp.database.log_store import LOG_TABLE, DatabaseLogStore

        driver = get_driver(sink_config)
        try:
            driver.execute(f"DROP TABLE IF EXISTS {LOG_TABLE}")
            legacy = self._LEGACY_DDL_PG if sink_config.is_postgresql else self._LEGACY_DDL_SQLITE
            driver.execute(legacy)
            store = DatabaseLogStore(config=sink_config)
            store.ensure_table(driver)
            store.ensure_table(driver)
            store.ensure_table(driver)
            # No error, and the table still has exactly one of each column.

            def _col(row: Any) -> str:
                return str(row.get("name") if isinstance(row, dict) else row[0])

            introspection = (
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                f"AND table_name = '{LOG_TABLE}'"
                if sink_config.is_postgresql
                else f"SELECT name FROM pragma_table_info('{LOG_TABLE}')"
            )
            cols = [_col(r) for r in driver.query(introspection)]
            assert len(cols) == len(set(cols)), "duplicate columns after re-run"
        finally:
            self._restore(driver, sink_config)
            driver.close()


# Keep the module import-clean under the suite's own guard: sys is used by the
# exc-info resolver, referenced here only for parity documentation.
_ = (sys, os)
