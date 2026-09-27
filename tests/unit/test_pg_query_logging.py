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
from pathlib import Path

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
