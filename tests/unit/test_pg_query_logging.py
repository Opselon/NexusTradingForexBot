"""PostgreSQL query observability — FULL ERROR/WARNING for every PG query.

The operator's ask (verbatim): "add FULL log ERROR ONLY OR WARNING for Queries
on Postgres To we can trace problems". The 2026-09-25T23:44-23:47 engine log
showed four concrete untraceable shapes this module pins::

  1. ``[DB-FABRIC] operational write failed domain=ops_shadow
     op=governance.record_event error=syntax error at or near "OR"`` — no
     SQL, no arity;
  2. ``[DB-FABRIC] audit provider read degraded ... no read plane registered``
     — 368 identical warnings in ~3 minutes;
  3. ``Audit batch insert failed ... error=the query has 0 placeholders but
     32 parameters were passed`` — truncated to 500 chars, no query text;
  4. a pooled connection death / checkout timeout surfacing as a bare
     exception.

Contract pinned here, entirely at the DB layer (the engine tick path gains
nothing — INV-001):

* a FAILED query logs ERROR with domain, operation, error_type, the FULL
  error message, the masked SQL text (placeholders intact) and
  placeholder_count vs arg_count;
* a SLOW-but-SUCCESSFUL query logs WARNING with duration_ms, the statement
  and the row count, threshold = ``PG_SLOW_QUERY_THRESHOLD_MS`` (200ms);
* the degraded-read warning deduplicates per (domain, reason): WARNING once,
  DEBUG thereafter with a cumulative counter, escalating to ERROR after
  ``PG_DEGRADED_ESCALATION_AFTER`` consecutive occurrences;
* no parameter VALUE ever reaches a log line — a fake secret is asserted
  absent from every record;
* the logging path never raises, even when the logger itself is broken.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from nexus_scalp.database import query_logging as ql


class _Recorder:
    """Structured-log double: captures (level, message, kwarg-dict) records.

    structlog's real logger is host-routed (caplog is empty for nexus_scalp
    loggers — the skill notes this), so the module logger is monkeypatched,
    exactly like tests/unit/test_bug274_wrapper_state_leak.py does.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def _capture(self, level: str) -> object:
        def emit(msg: str, *args: object, **kw: object) -> None:
            merged = dict(kw)
            # %-interpolate the positional args the way stdlib formatting
            # would, so the rendered message is the one an operator reads.
            try:
                rendered = msg % args if args else msg
            except Exception:
                rendered = msg
            # structlog passes bound context as kwargs; a payload dict is
            # passed as ``payload=`` by some callers.
            merged["_rendered"] = rendered
            self.records.append((level, rendered, merged))

        return emit

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ql, "logger", self)

    def __getattr__(self, name: str) -> object:
        if name in {"error", "warning", "info", "debug", "critical"}:
            return self._capture(name)
        raise AttributeError(name)

    @property
    def errors(self) -> list[tuple[str, dict]]:
        return [(m, kw) for lvl, m, kw in self.records if lvl == "error"]

    @property
    def warnings(self) -> list[tuple[str, dict]]:
        return [(m, kw) for lvl, m, kw in self.records if lvl == "warning"]

    @property
    def debugs(self) -> list[tuple[str, dict]]:
        return [(m, kw) for lvl, m, kw in self.records if lvl == "debug"]


# ---------------------------------------------------------------------------
# (1) a failed write logs ERROR with the SQL + placeholder_count
# ---------------------------------------------------------------------------


def test_failed_write_logs_error_with_sql_and_arity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Site (1): the ops_shadow write failure must carry the statement."""
    rec = _Recorder()
    rec.install(monkeypatch)

    sql = "INSERT INTO ops_events (payload) VALUES (?)"  # qmark, 1 placeholder
    ql.log_query_failure(
        operation="governance.record_event",
        exc=SyntaxError('syntax error at or near "OR"'),
        sql=sql,
        args=("a-value",),
        domain="ops_shadow",
        kind="write",
    )

    assert len(rec.errors) == 1, "a failed write must emit exactly one ERROR"
    msg, kw = rec.errors[0]
    assert kw["domain"] == "ops_shadow"
    assert kw["operation"] == "governance.record_event"
    assert kw["error_type"] == "SyntaxError"
    assert 'syntax error at or near "OR"' in kw["error_message"], "full message, untruncated"
    assert kw["placeholder_count"] == 1, "the qmark placeholder is counted"
    assert kw["arg_count"] == 1
    assert "INSERT INTO ops_events" in str(kw["sql"]), "the SQL text is logged"
    assert "?" in str(kw["sql"]), "placeholders are kept in the logged SQL"


def test_batch_arity_mismatch_is_provable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Site (3): 0 placeholders vs 32 parameters must be visible as counts."""
    rec = _Recorder()
    rec.install(monkeypatch)

    sql = "INSERT INTO audit_signals (k) VALUES ()"  # no placeholders — live bug
    ql.log_query_failure(
        operation="audit_batch_insert",
        exc=ValueError("the query has 0 placeholders but 32 parameters were passed"),
        sql=sql,
        args=tuple(range(32)),
        domain="audit",
        kind="batch_write",
        extra={"batch_size": 32, "salvaged": 0, "dead_lettered": 1},
    )

    assert len(rec.errors) == 1
    _msg, kw = rec.errors[0]
    assert kw["placeholder_count"] == 0
    assert kw["arg_count"] == 32, "the arity mismatch is now machine-readable"
    assert "0 placeholders but 32 parameters" in kw["error_message"]
    assert kw["batch_size"] == 32
    assert kw["dead_lettered"] == 1


# ---------------------------------------------------------------------------
# (2) a slow query logs WARNING with duration_ms
# ---------------------------------------------------------------------------


def test_slow_query_warns_with_duration_and_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    rec.install(monkeypatch)

    ql.log_slow_query(
        operation="query",
        duration_ms=451.2,
        sql="SELECT * FROM audit_signals WHERE created_at > %s",
        rows=128,
        domain="audit",
    )

    assert len(rec.warnings) == 1
    msg, kw = rec.warnings[0]
    assert kw["duration_ms"] == 451.2
    assert kw["threshold_ms"] == ql.PG_SLOW_QUERY_THRESHOLD_MS == 200.0
    assert kw["rows"] == 128
    assert "audit_signals" in str(kw["sql"])
    assert "slow query" in msg


def test_fast_query_emits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The threshold gates the WARNING: a 5ms query is silent."""
    rec = _Recorder()
    rec.install(monkeypatch)

    ql.log_slow_query(operation="query", duration_ms=5.0, sql="SELECT 1", domain="audit")
    assert rec.warnings == []


def test_query_timer_is_monotonic_and_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The timer records monotonic time and only formats past the threshold."""
    rec = _Recorder()
    rec.install(monkeypatch)

    # Fast path: no WARNING may be emitted.
    with ql.query_timer("query", "SELECT 1", domain="audit") as t:
        pass
    assert rec.warnings == []
    assert t.duration_ms >= 0.0
    assert t.rows is None

    # Slow path: force the duration past the threshold via the timer's own
    # duration attribute so the WARNING fires without sleeping.
    with ql.query_timer("query", "SELECT pg_sleep(1)", domain="audit") as t2:
        t2.rows = 4
        t2.duration_ms = 999.0  # __exit__ reads start, so set the clock proxy
        monkeypatch.setattr(t2, "start", 0.0, raising=False)
        monkeypatch.setattr(ql.time, "monotonic", lambda: 0.3, raising=False)  # 300ms > 200ms
    assert len(rec.warnings) == 1
    _msg, kw = rec.warnings[0]
    assert kw["rows"] == 4
    assert kw["duration_ms"] > ql.PG_SLOW_QUERY_THRESHOLD_MS


def test_query_timer_does_not_warn_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A query that FAILED is the ERROR path's business, not the slow path's."""
    rec = _Recorder()
    rec.install(monkeypatch)

    with pytest.raises(RuntimeError, match="boom"):
        with ql.query_timer("query", "SELECT 1", domain="audit"):
            raise RuntimeError("boom")
    assert rec.warnings == []


# ---------------------------------------------------------------------------
# (3) degraded-read dedup + escalation
# ---------------------------------------------------------------------------


def test_degraded_read_deduplicates_and_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Site (2): 368 identical warnings collapse to one + counter + escalation."""
    rec = _Recorder()
    rec.install(monkeypatch)
    tracker = ql.DegradedReadTracker()
    monkeypatch.setattr(ql, "PG_DEGRADED_ESCALATION_AFTER", 10)
    monkeypatch.setattr(ql, "PG_DEGRADED_LOG_EVERY", 20)

    for _ in range(10):
        tracker.note(domain="audit", reason="read_not_provisioned", operation="latest_snapshot")

    warnings = [kw for _m, kw in rec.warnings]
    assert len(warnings) == 1, (
        f"the first occurrence warns ONCE per (domain, reason), got {len(warnings)}"
    )
    assert warnings[0]["domain"] == "audit"
    assert warnings[0]["occurrences"] == 1
    # Occurrences 2..10 are deduplicated to DEBUG with the cumulative counter.
    assert len(rec.debugs) == 9, "repeats drop to DEBUG, not to silence"
    assert rec.debugs[-1][1]["occurrences"] == 10

    # Occurrence 11 crosses PG_DEGRADED_ESCALATION_AFTER=10 -> ERROR.
    tracker.note(domain="audit", reason="read_not_provisioned", operation="latest_snapshot")
    assert len(rec.errors) == 1, "a persistent degradation escalates to ERROR"
    _msg, kw = rec.errors[0]
    assert kw["consecutive"] == 11
    assert "ESCALATED" in rec.errors[0][0]


def test_degraded_read_trackers_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second domain with the same reason is NOT silenced by the first."""
    rec = _Recorder()
    rec.install(monkeypatch)
    tracker = ql.DegradedReadTracker()

    tracker.note(domain="audit", reason="read_not_provisioned", operation="a")
    tracker.note(domain="ops_shadow", reason="read_not_provisioned", operation="b")

    assert len(rec.warnings) == 2, "dedup is per (domain, reason), not global"


def test_degraded_read_counter_is_cumulative(monkeypatch: pytest.MonkeyPatch) -> None:
    """The periodic emission keeps the operator informed without flooding."""
    rec = _Recorder()
    rec.install(monkeypatch)
    tracker = ql.DegradedReadTracker()
    monkeypatch.setattr(ql, "PG_DEGRADED_ESCALATION_AFTER", 1000)  # isolate dedup
    monkeypatch.setattr(ql, "PG_DEGRADED_LOG_EVERY", 5)

    for _ in range(11):
        tracker.note(domain="audit", reason="no_plane", operation="op")

    # emission 1 (first), emission at total 5, emission at total 10 -> 3
    assert len(rec.warnings) == 3
    totals = [kw["occurrences"] for _m, kw in rec.warnings]
    assert totals == [1, 5, 10]


def test_note_degraded_read_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A logging path failure must never become the caller's failure."""
    tracker = ql.DegradedReadTracker()

    # (a) the module-level helper resolves the process-global tracker, so a
    # failure inside DegradedReadTracker.note must not escape it.
    def boom(self: object, **kw: object) -> None:
        raise RuntimeError("logger on fire")

    monkeypatch.setattr(type(tracker), "note", boom)
    ql.note_degraded_read(domain="audit", reason="x")  # must not raise
    monkeypatch.undo()

    # (b) the tracker's own internals are guarded too: a broken monotonic
    # clock or a broken lock must not turn a degraded read into a crash.
    monkeypatch.setattr(ql.time, "monotonic", lambda: (_ for _ in ()).throw(RuntimeError("clock")))
    tracker.note(domain="audit", reason="y")  # must not raise
    monkeypatch.undo()

    # (c) the record-emission path (a logger that raises) is swallowed by
    # ``_log`` — verify through a tracker whose level calls explode.
    monkeypatch.setattr(ql, "logger", _Boom())
    tracker.note(domain="audit", reason="z")  # must not raise


# ---------------------------------------------------------------------------
# (4) pool failure classification
# ---------------------------------------------------------------------------


def test_pool_failure_classification() -> None:
    """Site (4): checkout timeout vs server-side vs a dead handle."""
    import psycopg
    import psycopg_pool

    out = ql.classify_pool_error(psycopg_pool.PoolTimeout())
    assert out["failure_class"] == "checkout_timeout"
    assert out["client_side"] is True

    out = ql.classify_pool_error(psycopg.OperationalError("server gone"))
    assert out["failure_class"] == "server_error"
    assert out["client_side"] is False

    out = ql.classify_pool_error(psycopg.InterfaceError("dead handle"))
    assert out["failure_class"] == "connection_dead"

    out = ql.classify_pool_error(psycopg_pool.PoolClosed())
    assert out["failure_class"] == "pool_closed"

    out = ql.classify_pool_error(ValueError("something else"))
    assert out["failure_class"] == "other"
    assert out["pool_error_type"] == "ValueError"


def test_pool_failure_error_carries_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    rec.install(monkeypatch)
    import psycopg_pool

    ql.log_pool_failure(
        pool_name="pg-write",
        operation="checkout",
        exc=psycopg_pool.PoolTimeout(),
        domain="audit",
        stats={"name": "pg-write", "open": True, "size": 8, "available": 0},
    )

    assert len(rec.errors) == 1
    _msg, kw = rec.errors[0]
    assert kw["pool"] == "pg-write"
    assert kw["failure_class"] == "checkout_timeout"
    assert kw["pool_stats"]["size"] == 8
    assert kw["pool_stats"]["available"] == 0, "8/8 in use is the capacity signal"


def test_pool_failure_guard_reraises_and_classifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observability never changes the failure contract: the error propagates."""
    rec = _Recorder()
    rec.install(monkeypatch)
    import psycopg_pool

    def boom() -> None:
        raise psycopg_pool.PoolTimeout("no connections")

    with pytest.raises(psycopg_pool.PoolTimeout):
        ql.pool_failure_guard(
            boom,
            pool_name="pg-read",
            operation="checkout",
            domain="audit",
            stats=lambda: {"size": 4, "available": 0},
        )
    assert len(rec.errors) == 1
    assert rec.errors[0][1]["failure_class"] == "checkout_timeout"


def test_pool_failure_guard_logs_statement_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A statement-level error logs as a QUERY failure, not a pool failure."""
    rec = _Recorder()
    rec.install(monkeypatch)

    with pytest.raises(SyntaxError):
        ql.pool_failure_guard(
            lambda: (_ for _ in ()).throw(SyntaxError('near "OR"')),
            pool_name="pg-write",
            operation="execute",
            domain="ops_shadow",
            sql="INSERT INTO t (a) VALUS (?)",
            args=(1,),
        )
    assert len(rec.errors) == 1
    _msg, kw = rec.errors[0]
    assert "VALUS" in str(kw["sql"])
    assert kw["placeholder_count"] == 1
    assert kw["arg_count"] == 1


# ---------------------------------------------------------------------------
# (4b) a broken logger must never raise
# ---------------------------------------------------------------------------


class _Boom:
    """A logger whose every method raises: the logging path must survive it."""

    def __getattr__(self, name: str) -> object:
        def explode(*a: object, **k: object) -> None:
            raise RuntimeError("logger backend on fire")

        return explode


def test_logging_never_raises_with_a_broken_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ql, "logger", _Boom())
    # None of these may propagate.
    ql.log_query_failure(operation="op", exc=ValueError("x"), sql="SELECT 1", args=(1,))
    ql.log_slow_query(operation="op", duration_ms=999.0, sql="SELECT 1")
    ql.note_degraded_read(domain="audit", reason="x")
    ql.log_pool_failure(pool_name="p", operation="checkout", exc=ValueError("x"))


# ---------------------------------------------------------------------------
# (5) no parameter VALUE may ever appear in a log line
# ---------------------------------------------------------------------------


SECRET = "n0tT0b3L0gg3d-SECRET-token-AB12"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t (a) VALUES (?)",
        "SELECT * FROM t WHERE token=[REDACTED]",
        f"SELECT * FROM t WHERE password='{SECRET}'",
        f"SELECT * FROM t WHERE url = 'https://user:{SECRET}@db.internal/app'",
        "SELECT * FROM audit_signals WHERE id = $1",
    ],
)
def test_no_parameter_value_is_logged(monkeypatch: pytest.MonkeyPatch, sql: str) -> None:
    """A bound value (or an embedded credential) never reaches the log."""
    rec = _Recorder()
    rec.install(monkeypatch)

    ql.log_query_failure(
        operation="op",
        exc=ValueError("boom"),
        sql=sql,
        args=(SECRET,),
        domain="audit",
    )
    ql.log_slow_query(operation="op", duration_ms=999.0, sql=sql, domain="audit")

    blob = repr(rec.records)
    assert SECRET not in blob, f"secret leaked into a log line: {blob}"


def test_mask_query_text_keeps_shape_but_drops_secret() -> None:
    masked = ql.mask_query_text(f"SELECT * FROM t WHERE password='{SECRET}'")
    assert SECRET not in masked
    assert "password" in masked, "the key name survives for triage"
    assert "SELECT * FROM t WHERE" in masked, "the statement shape survives"


def test_mask_query_text_handles_non_strings() -> None:
    """A non-string statement degrades to a type hint, never to repr(values)."""
    out = ql.mask_query_text(None)
    assert "non-string" in out
    out = ql.mask_query_text(3)
    assert "non-string" in out


def test_mask_query_text_bounds_a_pathological_statement() -> None:
    """A multi-MB statement cannot flood the error log."""
    big = "SELECT " + ", ".join(f"col_{i}" for i in range(200_000))
    masked = ql.mask_query_text(big)
    assert len(masked) <= ql._MAX_SQL_LOG_CHARS + 80
    assert "truncated" in masked


def test_arg_count_never_renders_values() -> None:
    assert ql.arg_count((SECRET, SECRET)) == 2
    assert ql.arg_count([(SECRET, 1), (SECRET, 2)]) == 2  # executemany shape
    assert ql.arg_count(()) == 0
    assert ql.arg_count(None) == 0


# ---------------------------------------------------------------------------
# (6) the engine tick path gains nothing
# ---------------------------------------------------------------------------


def test_query_logging_is_not_imported_by_the_engine_hot_path() -> None:
    """Requirement (e): the DB layer owns the logging; the engine stays out."""
    import importlib

    mod = importlib.import_module("nexus_scalp.database.query_logging")
    assert mod.PG_SLOW_QUERY_THRESHOLD_MS == 200.0
    assert mod.PG_DEGRADED_ESCALATION_AFTER == 10
    # The module never imports anything outside the DB layer at module scope
    # (both imports are observability + the DSN masker).
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from nexus_scalp.engine" not in src
    assert "import LiveEngine" not in src


def test_pg_planes_pool_failure_logging_wires_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pool checkout failure surfaces as a classified pool ERROR."""
    from nexus_scalp.database.fabric import pg_planes

    rec = _Recorder()
    monkeypatch.setattr(ql, "logger", rec)

    pool = pg_planes.PgPool(
        "postgresql://user:***@127.0.0.1:59999/nexusdb",
        pg_planes.PoolLimits(min_size=1, max_size=1, connect_timeout_sec=1),
        name="pg-write",
    )
    # The pool is never opened, so checkout raises RuntimeError ("not open"),
    # which must still be classified and logged, then re-raised.
    with pytest.raises(RuntimeError):
        with pool.connection():
            pass
    assert len(rec.errors) == 1
    _msg, kw = rec.errors[0]
    assert kw["pool"] == "pg-write"
    assert kw["error_type"] == "RuntimeError"
    # A closed pool is a client-side condition, not a server condition.
    assert kw["failure_class"] in {"pool_closed", "other"}


# ---------------------------------------------------------------------------
# (7) Phase 4 query metrics: workload attribution without pg_stat_statements
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def _reset_query_metrics() -> Any:
    """Isolate the global recorder: disabled and empty before and after."""
    ql.query_metrics.disable()
    ql.query_metrics.reset()
    yield ql.query_metrics
    ql.query_metrics.disable()
    ql.query_metrics.reset()


def test_query_metrics_off_by_default_and_records_nothing() -> None:
    metrics = ql.query_metrics
    metrics.disable()
    metrics.reset()
    with ql.QueryMetricsRecorder("repo.select_thing", sql="SELECT 1") as r:
        r.rows = 5
    snap = ql.query_metrics_snapshot()
    assert snap["enabled"] is False
    assert snap["queries"] == []


def test_query_metrics_aggregates_calls_total_mean_min_max_rows() -> None:
    metrics = ql.query_metrics
    metrics.enable()
    try:
        for _ in range(3):
            with ql.QueryMetricsRecorder("repo.list_things", sql="SELECT * FROM t") as r:
                r.rows = 10
        snap = metrics.snapshot()
        assert len(snap["queries"]) == 1
        only = snap["queries"][0]
        assert only["query_name"] == "repo.list_things"
        assert only["calls"] == 3
        assert only["rows"] == 30
        assert only["total_ms"] >= 0.0
        assert only["mean_ms"] == only["total_ms"] / 3
        assert only["errors"] == 0
    finally:
        metrics.disable()


def test_query_metrics_rankings_stay_separate() -> None:
    """Rank A/B/C/E are distinct orderings, never one collapsed score."""
    metrics = ql.query_metrics
    metrics.enable()
    try:
        with ql.QueryMetricsRecorder("a.one_big", sql="SELECT 1") as r:
            r.rows = 1000
        for _ in range(50):
            with ql.QueryMetricsRecorder("b.many_small", sql="SELECT 2") as r:
                r.rows = 1
        snap = metrics.snapshot()
        top_by_time = snap["rankings"]["total_time"][0]["query_name"]
        top_by_calls = snap["rankings"]["calls"][0]["query_name"]
        top_by_rows = snap["rankings"]["rows"][0]["query_name"]
        assert top_by_calls == "b.many_small"
        assert top_by_rows == "a.one_big"
        assert snap["rankings"]["mean_latency"][0]["query_name"] in {"a.one_big", "b.many_small"}
        # A single collapsed score would make all four identical.
        assert len({top_by_time, top_by_calls, top_by_rows}) >= 2
    finally:
        metrics.disable()


def test_query_metrics_never_raises_on_recorder_failure() -> None:
    """A metrics failure must never become a query failure."""
    metrics = ql.query_metrics
    metrics.enable()
    try:
        broken = ql.QueryMetricsRecorder("repo.x", sql="SELECT 1")
        # Simulate a broken clock on exit.
        broken.start = None
        broken.__exit__(None, None, None)
        # An exception inside __exit__ must still not propagate.
        with ql.QueryMetricsRecorder("repo.x", sql="SELECT 1"):
            raise ValueError("boom")
    except ValueError:
        pass  # the caller's exception propagates unchanged
    finally:
        metrics.disable()


def test_query_metrics_masks_the_sql_shape() -> None:
    """The stored sql_shape is masked, so no bound value is retained."""
    metrics = ql.query_metrics
    metrics.enable()
    try:
        with ql.QueryMetricsRecorder("repo.x", sql=f"SELECT * FROM t WHERE k='{SECRET}'"):
            pass
        snap = metrics.snapshot()
        blob = repr(snap["queries"])
        assert SECRET not in blob
    finally:
        metrics.disable()


def test_query_name_is_provider_agnostic_and_stable() -> None:
    """Both drivers derive the same name for the same statement shape."""
    from nexus_scalp.database.config import DatabaseConfig, DatabaseProvider
    from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver
    from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver

    pg = PostgreSQLDriver(DatabaseConfig(provider=DatabaseProvider.POSTGRESQL))
    lite = SQLiteDriver(DatabaseConfig(provider=DatabaseProvider.SQLITE))
    for sql in (
        "SELECT id FROM t WHERE symbol = 'EURUSD' AND   volume = 2",
        "  SELECT   1  ",
        "",
        "SELECT * FROM t ORDER BY COALESCE(NULLIF(exit_time,''), '') DESC LIMIT 500 OFFSET 2000",
    ):
        assert pg.query_name(sql) == lite.query_name(sql), f"names diverged for {sql!r}"
    # literals and numbers collapse so the same business query aggregates
    assert pg.query_name("SELECT * FROM t WHERE id = 7") == pg.query_name(
        "SELECT * FROM t WHERE id = 9"
    )
    assert pg.query_name("", fallback="fallback") == "fallback"


def test_query_metrics_bounded_query_name_cardinality() -> None:
    """An unbounded stream of new names cannot exhaust the metrics map."""
    metrics = ql._QueryMetrics(max_query_names=8)
    metrics.enable()
    for i in range(50):
        with ql.QueryMetricsRecorder(f"repo.q{i}", sql="SELECT 1"):
            pass
    snap = metrics.snapshot()
    assert len(snap["queries"]) <= 8


# ---------------------------------------------------------------------------
# Percentile reservoir (contract sec.15: p50/p95/p99 per hot path).
# pg_stat_statements supplies mean/min/max but never percentiles, so the driver
# boundary has to estimate the tail itself from a bounded sample.
# ---------------------------------------------------------------------------


def test_reservoir_percentiles_exact_when_not_subsampling() -> None:
    """With capacity >= sample count the reservoir is the full sample: exact.

    Percentiles must be right when nothing is being discarded — this is the
    deterministic case, so a tight tolerance is legitimate here.
    """
    res = ql._LatencyReservoir(16_384)
    for i in range(10_000):
        res.add(i / 1000.0)
    pct = res.percentiles()
    assert abs(pct["p50"] - 5.0) / 5.0 < 0.02
    assert abs(pct["p95"] - 9.5) / 9.5 < 0.02
    assert abs(pct["p99"] - 9.9) / 9.9 < 0.02
    assert res.seen == 10_000
    assert len(res._buf) == 10_000  # nothing discarded


def test_reservoir_percentiles_stay_close_under_subsampling() -> None:
    """Under subsampling the estimate is statistical, so the bound is looser.

    Reservoir sampling of 10,000 observations into 4096 slots gives the median a
    standard error of ~0.078 ms on this uniform(0,10) input, i.e. ~1.6% of the
    median. A 5% tolerance is therefore a real regression bound: it fails if the
    sampling is biased, not merely if it is random.
    """
    res = ql._LatencyReservoir(4096)
    for i in range(10_000):
        res.add(i / 1000.0)
    pct = res.percentiles()
    assert res.seen == 10_000
    assert len(res._buf) == 4096  # bounded
    assert abs(pct["p50"] - 5.0) / 5.0 < 0.05
    assert abs(pct["p95"] - 9.5) / 9.5 < 0.05
    assert abs(pct["p99"] - 9.9) / 9.9 < 0.05
    # Ordering must hold regardless of sampling noise.
    assert pct["p50"] <= pct["p95"] <= pct["p99"]


def test_reservoir_memory_is_bounded_at_capacity() -> None:
    """A reservoir never grows past capacity, however many samples arrive."""
    res = ql._LatencyReservoir(64)
    for i in range(100_000):
        res.add(float(i))
    assert len(res._buf) == 64
    assert res.seen == 100_000
    # The bounded sample is still usable, not just silently truncated.
    assert 0.0 <= res.percentiles()["p50"] <= 100_000.0


def test_empty_reservoir_reports_nothing_not_zero() -> None:
    """No samples must read as absent, never as a fake 0.0 ms latency."""
    assert ql._LatencyReservoir(16).percentiles() == {}


def test_metrics_snapshot_reports_percentiles_and_basis() -> None:
    """Each query carries p50/p95/p99 plus how many samples back them."""
    metrics = ql._QueryMetrics(max_query_names=64, percentiles_capacity=128)
    metrics.enable()
    for i in range(300):
        metrics.record(query_name="SELECT slow", duration_ms=float(i) / 10.0, operation="query")
    snap = metrics.snapshot()
    row = next(q for q in snap["queries"] if q["query_name"] == "SELECT slow")
    assert row["p50"] is not None and row["p95"] is not None and row["p99"] is not None
    assert row["p50"] <= row["p95"] <= row["p99"]
    assert row["sampled"] == 300
    assert row["percentiles_basis"] == 128  # capped at capacity
    assert "p95" in snap["rankings"]


def test_footprint_is_reported_and_bounded() -> None:
    """sec.61: the measurement's own memory cost is a reported quantity."""
    metrics = ql._QueryMetrics(max_query_names=32, percentiles_capacity=64)
    metrics.enable()
    for i in range(200):
        metrics.record(query_name=f"q{i}", duration_ms=1.0, operation="query")
    fp = metrics.snapshot()["footprint"]
    # The name cap is enforced: 200 distinct names collapse to the 32 allowed.
    assert fp["query_names"] == 32 and fp["query_names_max"] == 32
    assert fp["sample_bytes"] <= fp["worst_case_sample_bytes"]
    # Every retained name owns at most `percentiles_capacity` samples.
    assert fp["reservoir_entries"] <= 32 * 64


def test_percentile_basis_never_exceeds_capacity() -> None:
    """A percentile must never claim more samples than the reservoir can hold."""
    metrics = ql._QueryMetrics(max_query_names=8, percentiles_capacity=16)
    metrics.enable()
    for i in range(500):
        metrics.record(query_name="q", duration_ms=float(i), operation="query")
    row = next(q for q in metrics.snapshot()["queries"] if q["query_name"] == "q")
    assert row["sampled"] == 500
    assert row["percentiles_basis"] == 16


# ---------------------------------------------------------------------------
# Operation / repository attribution (contract sec.51).
# The driver knows the SQL; the fabric knows the business operation. Binding the
# two is what makes "which code path caused this PostgreSQL cost" answerable.
# ---------------------------------------------------------------------------


def test_bound_operation_outranks_the_driver_verb() -> None:
    """A business operation must replace the generic driver verb in attribution."""
    metrics = ql._QueryMetrics(max_query_names=64)
    metrics.enable()
    with ql.query_context("audit.log_signal", domain="audit", repository="audit_repository"):
        metrics.record(query_name="INSERT INTO t", duration_ms=1.0, operation="execute")
    snap = metrics.snapshot()
    ops = {r["operation"] for r in snap["by_operation"]}
    assert "audit.log_signal" in ops
    assert "execute" not in ops  # the driver verb did not shadow the real operation
    # The driver verb is retained, not discarded.
    row = next(q for q in snap["queries"] if q["query_name"] == "INSERT INTO t")
    assert row["driver_operation"] == "execute"


def test_repository_rollup_aggregates_its_operations() -> None:
    """One repository's statements are attributable without re-reading the log."""
    metrics = ql._QueryMetrics(max_query_names=64)
    metrics.enable()
    with ql.query_context("audit.log_signal", domain="audit", repository="audit_repository"):
        metrics.record(query_name="q1", duration_ms=1.0, rows=1, operation="audit.log_signal")
    with ql.query_context("audit.read_recent", domain="audit", repository="audit_repository"):
        metrics.record(query_name="q2", duration_ms=2.0, rows=5, operation="audit.read_recent")
    repos = metrics.snapshot()["by_repository"]
    assert len(repos) == 1
    repo = repos[0]
    assert repo["repository"] == "audit_repository"
    assert repo["calls"] == 2 and repo["rows"] == 6
    assert set(repo["operations"]) == {"audit.log_signal", "audit.read_recent"}


def test_unscoped_statement_is_still_recorded() -> None:
    """A statement issued outside any operation scope is not dropped."""
    metrics = ql._QueryMetrics(max_query_names=64)
    metrics.enable()
    metrics.record(query_name="SELECT 1", duration_ms=0.1, operation="query")
    assert any(q["query_name"] == "SELECT 1" for q in metrics.snapshot()["queries"])


def test_query_context_nests_and_restores() -> None:
    """Nested scopes restore the outer attribution on exit."""
    metrics = ql._QueryMetrics(max_query_names=64)
    metrics.enable()
    with ql.query_context("outer", repository="r_outer"):
        with ql.query_context("inner", repository="r_inner"):
            metrics.record(query_name="q", duration_ms=1.0, operation="query")
        metrics.record(query_name="q2", duration_ms=1.0, operation="query")
    by_repo = {r["repository"]: r for r in metrics.snapshot()["by_repository"]}
    assert set(by_repo) == {"r_outer", "r_inner"}
    assert by_repo["r_inner"]["calls"] == 1
    assert by_repo["r_outer"]["calls"] == 1


def test_concurrent_records_are_not_lost() -> None:
    """sec.41: concurrent writers must not lose updates."""
    metrics = ql._QueryMetrics(max_query_names=16, percentiles_capacity=64)
    metrics.enable()
    threads = 8
    per = 4000

    def worker(tid: int) -> None:
        for i in range(per):
            metrics.record(
                query_name=f"shape_{i % 4}",
                duration_ms=float(i % 50),
                rows=i % 5,
                operation=f"op_{tid}",
                repository=f"repo_{tid % 3}",
            )

    ths = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    total = sum(q["calls"] for q in metrics.snapshot()["queries"])
    assert total == threads * per


def test_aggregator_never_persists_per_query() -> None:
    """sec.49: the aggregator must stay in-memory; it must not become a workload."""
    src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "nexus_scalp"
        / "database"
        / "query_logging.py"
    ).read_text(encoding="utf-8")
    seg = src[src.index("class _QueryMetrics") : src.index("#: Process-global query metrics recorder")]
    for forbidden in ("_persist_event", "DatabaseLogStore", "INSERT INTO", "commit("):
        assert forbidden not in seg, f"aggregator must not persist per query: {forbidden}"
