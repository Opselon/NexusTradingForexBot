"""NSE-Swarm (role 2 Backend/Domain): BUG-292 — C3 spread-percentile gate I/O
must live OFF the event loop (perf-wave R6).

DEFECT (``docs/audit/wave_20260914/10_performance.md`` §11 risk 6, VERIFIED at
the pre-fix HEAD): ``LiveEngine._session_spread_percentile_provider`` opened a
SQLite connection and ran a same-day scan of ``audit_paper_executions``
*inside* policy evaluation. ``SignalPolicy`` calls that provider on every
spread-positive candidate, so the loop thread paid connection churn per
decision ("documented, bounded, but still loop-thread I/O + per-call
connect"). The audit's prescribed fix: "maintain the same-day
spread-percentile sketch incrementally off-loop (audit worker or maintenance
tick), expose RAM read; refresh cadence <= 60 s".

FIX CONTRACT pinned here:
  1. PARITY — the in-process sketch returns the identical number the SQL
     provider returned for the same rows / same ``now`` / same percentile
     (this is a trading-behavior path; a moved read may not move the value).
  2. ZERO I/O on the read surface; the only read is the maintenance-stage
     refresh at <= 60 s, off-loop and failure-isolated.
  3. Honest unknown preserved: thin sample -> None (never 0.0), and a dead
     refresh degrades to None too instead of defending a frozen distribution.
  4. The interpolation formula has ONE owner (``broker_history``), shared by
     both surfaces.
"""

from __future__ import annotations

import math
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.broker_history import (
    create_paper_executions_table,
    interpolated_percentile,
    session_spread_percentile,
)
from nexus_scalp.experience.spread_sketch import (
    MAX_REFRESH_FAILURE_STREAK,
    MAX_SAMPLES,
    SpreadSessionSketch,
)

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    create_paper_executions_table(conn)
    return conn


def _insert(
    conn: sqlite3.Connection,
    *,
    ts: datetime,
    symbol: str = "XAUUSD",
    spread: float,
    status: str = "FILLED",
    ticket: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_paper_executions
            (ts, symbol, order_type, volume, requested_price, bid_at_request,
             ask_at_request, spread, fill_price, slippage, rejection_reason,
             ticket, latency_ticks, status, source)
        VALUES (?, ?, 'BUY', 0.01, 2000.0, 2000.0, ?, ?, ?, 0.0, ?, ?, 0, ?, 'TEST')
        """,
        (
            ts.isoformat(),
            symbol,
            2000.0 + spread,
            spread,
            2000.0 + spread,
            None if status == "FILLED" else "test_reject",
            ticket,
            status,
        ),
    )


def _seed_mixed_session(conn: sqlite3.Connection) -> None:
    """A deliberately messy session: the shapes the SQL WHERE clause selects."""
    for i in range(9):
        _insert(conn, ts=NOW - timedelta(minutes=45 - i * 4), spread=0.10 + 0.03 * i, ticket=i)
    _insert(conn, ts=NOW - timedelta(minutes=5), spread=7.5, status="REJECTED", ticket=90)
    _insert(conn, ts=NOW - timedelta(minutes=6), spread=0.0, ticket=91)  # quote-less defensive row
    _insert(conn, ts=NOW - timedelta(hours=9), spread=50.0, ticket=92)  # outside the 4h window
    _insert(conn, ts=NOW - timedelta(days=1), spread=40.0, ticket=93)  # yesterday
    _insert(conn, ts=NOW - timedelta(minutes=7), spread=33.0, symbol="EURUSD", ticket=94)
    _insert(conn, ts=NOW - timedelta(minutes=8), spread=21.0, ticket=95)
    conn.commit()


# ---------------------------------------------------------------------------
# 1. PARITY with the SQL provider (the money-path contract)
# ---------------------------------------------------------------------------


class TestParityWithSqlProvider:
    @pytest.mark.parametrize("pct", [50.0, 60.0, 70.0, 85.0, 99.0])
    @pytest.mark.parametrize(
        "reference",
        [
            NOW,
            NOW - timedelta(minutes=17),
            NOW - timedelta(hours=3, minutes=58),  # window crosses the day floor
            NOW + timedelta(seconds=1),
        ],
    )
    def test_sketch_equals_sql_for_every_shape(self, pct: float, reference: datetime) -> None:
        conn = _conn()
        _seed_mixed_session(conn)
        sql_value = session_spread_percentile(conn, "XAUUSD", reference, pct)

        sketch = SpreadSessionSketch()
        assert sketch.refresh(conn, now_utc=NOW) > 0
        sketch_value = sketch.percentile("XAUUSD", reference, pct)

        if sql_value is None:
            assert sketch_value is None
        else:
            assert sketch_value is not None
            # Byte-parity is the contract: identical inputs -> identical float.
            assert sketch_value == sql_value
        conn.close()

    def test_both_surfaces_agree_on_the_thin_session_none(self) -> None:
        """Below min_samples BOTH return None — the shared honest-unknown."""
        conn = _conn()
        for i in range(3):
            _insert(conn, ts=NOW - timedelta(minutes=3 - i), spread=0.2 + 0.1 * i, ticket=i)
        conn.commit()
        assert session_spread_percentile(conn, "XAUUSD", NOW, 70.0) is None
        sketch = SpreadSessionSketch()
        sketch.refresh(conn, now_utc=NOW)
        assert sketch.percentile("XAUUSD", NOW, 70.0) is None
        conn.close()

    def test_empty_table_is_parity(self) -> None:
        conn = _conn()
        assert session_spread_percentile(conn, "XAUUSD", NOW, 70.0) is None
        sketch = SpreadSessionSketch()
        assert sketch.refresh(conn, now_utc=NOW) == 0
        assert sketch.percentile("XAUUSD", NOW, 70.0) is None
        conn.close()

    def test_percentile_range_contract_shared(self) -> None:
        """Out-of-range percentiles fail loud on BOTH surfaces (never a
        silent 0.0 fail-open on the trading path)."""
        conn = _conn()
        _seed_mixed_session(conn)
        sketch = SpreadSessionSketch()
        sketch.refresh(conn, now_utc=NOW)
        with pytest.raises(ValueError):
            session_spread_percentile(conn, "XAUUSD", NOW, 10.0)
        with pytest.raises(ValueError):
            sketch.percentile("XAUUSD", NOW, 10.0)
        conn.close()

    def test_parity_survives_host_ahead_clock_skew_on_the_read(self) -> None:
        """The refresh runs on host wall time, the read on TICK time. A tick a
        few seconds BEHIND the refresh stamp (broker-lags-host, the production
        skew shape) must still see the identical distribution — that is what
        COLD_REBUILD_LOOKBACK_SEC buys, and it is a real risk because the
        refresh uses datetime.now() while the policy passes tick.timestamp."""
        conn = _conn()
        base = datetime.now(UTC)
        for i in range(8):
            _insert(conn, ts=base - timedelta(seconds=90 - i * 5), spread=0.10 + 0.02 * i, ticket=i)
        conn.commit()
        sketch = SpreadSessionSketch()
        sketch.refresh(conn)  # host wall clock, "now"
        # A tick 60 s older than the refresh pass: still full parity.
        skewed = base - timedelta(seconds=60)
        assert sketch.percentile("XAUUSD", skewed, 70.0) == session_spread_percentile(
            conn, "XAUUSD", skewed, 70.0
        )
        assert sketch.truncated_reads == 0
        conn.close()

    def test_formula_has_one_owner(self) -> None:
        """The interpolation lives in broker_history ONLY — a copied formula
        in the sketch is exactly how a gate drifts silently."""
        import inspect

        import nexus_scalp.experience.spread_sketch as sketch_mod

        src = inspect.getsource(sketch_mod)
        assert (
            "from nexus_scalp.adapters.database.broker_history import interpolated_percentile"
            in src
        )
        assert "lo_i" not in src, "spread_sketch re-implemented the percentile formula"
        # The SQL provider calls the shared owner too.
        import nexus_scalp.adapters.database.broker_history as bh

        bh_src = inspect.getsource(bh.session_spread_percentile)
        assert "interpolated_percentile(spreads" in bh_src

    def test_interpolated_percentile_matches_numpy_semantics(self) -> None:
        """Lock the formula itself (numpy 'linear'): known values, no numpy
        import in production code."""
        assert interpolated_percentile([1.0], 70.0) == 1.0
        assert interpolated_percentile([1.0, 2.0], 50.0) == pytest.approx(1.5)
        assert interpolated_percentile([0.1, 0.2, 0.3, 0.4], 70.0) == pytest.approx(0.31)
        assert interpolated_percentile([0.1, 0.2, 0.3, 0.4], 99.0) == pytest.approx(0.397)
        with pytest.raises(ValueError):
            interpolated_percentile([], 50.0)


# ---------------------------------------------------------------------------
# 2. Off-loop cadence + failure posture (the INV-001 side of the fix)
# ---------------------------------------------------------------------------


class TestRefreshCadenceAndIsolation:
    def test_incremental_refresh_absorbs_new_rows_without_a_full_read(self) -> None:
        conn = _conn()
        _seed_mixed_session(conn)
        sketch = SpreadSessionSketch()
        first = sketch.refresh(conn, now_utc=NOW)
        assert sketch._watermark_rowid is not None
        watermark = sketch._watermark_rowid
        # New fill lands AFTER the refresh pass (the paper parity export path).
        _insert(conn, ts=NOW - timedelta(seconds=30), spread=2.5, ticket=200)
        conn.commit()
        second = sketch.refresh(conn, now_utc=NOW + timedelta(seconds=5))
        assert second == first + 1
        assert sketch._watermark_rowid > watermark
        # The new wide fill must move the percentile: proves the gate sees it
        # on the next pass instead of only after a restart.
        assert sketch.percentile("XAUUSD", NOW + timedelta(seconds=5), 99.0) == pytest.approx(
            session_spread_percentile(conn, "XAUUSD", NOW + timedelta(seconds=5), 99.0)
        )
        conn.close()

    def test_day_rollover_resets_instead_of_merging_stale_days(self) -> None:
        conn = _conn()
        _seed_mixed_session(conn)
        sketch = SpreadSessionSketch()
        assert sketch.refresh(conn, now_utc=NOW) > 0
        # Tomorrow: the incremental merge must NOT keep yesterday resident.
        tomorrow = NOW + timedelta(days=1)
        assert sketch.percentile("XAUUSD", tomorrow, 70.0) is None
        assert sketch.refresh(conn, now_utc=tomorrow) == 0
        assert sketch.cold_rebuilds == 2  # boot + rollover, never a mixed day

    def test_missing_substrate_is_honest_absence_not_a_fault(self) -> None:
        """A LIVE-only install has no audit_paper_executions at all. That must
        not burn the failure streak / alarm (which would cry wolf forever)."""
        bare = sqlite3.connect(":memory:")
        sketch = SpreadSessionSketch()
        assert sketch.refresh(bare, now_utc=NOW) == 0
        assert sketch.refresh_failures == 0
        assert sketch._failure_streak == 0
        assert sketch.percentile("XAUUSD", NOW, 70.0) is None
        bare.close()

    def test_refresh_never_raises_and_degrades_after_a_streak(self) -> None:
        class _BrokenConn:
            def execute(self, *_a, **_k):
                raise RuntimeError("database is locked")

        conn = _conn()
        _seed_mixed_session(conn)
        sketch = SpreadSessionSketch()
        assert sketch.refresh(conn, now_utc=NOW) > 0
        working = sketch.percentile("XAUUSD", NOW, 70.0)
        assert working is not None

        for _ in range(MAX_REFRESH_FAILURE_STREAK):
            assert sketch.refresh(_BrokenConn(), now_utc=NOW) >= 0  # never raises
        assert sketch.refresh_failures == MAX_REFRESH_FAILURE_STREAK
        # Stale-frozen distribution must NOT keep gating: honest unknown.
        assert sketch.percentile("XAUUSD", NOW, 70.0) is None
        assert sketch.stale_degradations > 0
        # A recovered pass clears the streak and the gate is live again.
        assert sketch.refresh(conn, now_utc=NOW) > 0
        assert sketch.percentile("XAUUSD", NOW, 70.0) == pytest.approx(working)
        conn.close()

    def test_failure_alarm_is_rate_limited_and_first_pass_always_due(self) -> None:
        """BUG-273 class: a 0.0-vs-monotonic throttle silently swallows the
        first warning on a young host."""
        import nexus_scalp.experience.spread_sketch as mod

        recorded: list[str] = []

        class _FakeLogger:
            def error(self, msg: str, *args: object, **kwargs: object) -> None:
                recorded.append(str(msg) % args if args else str(msg))

            def warning(self, msg: str, *args: object, **kwargs: object) -> None:
                recorded.append(str(msg) % args if args else str(msg))

        sketch = SpreadSessionSketch()
        assert sketch._last_failure_alarm is None
        original = mod.logger
        mod.logger = _FakeLogger()  # structlog PrintLogger cannot be handler-captured
        try:
            for _ in range(5):
                sketch.refresh(_BrokenExecute(), now_utc=NOW)  # type: ignore[arg-type]
        finally:
            mod.logger = original
        assert sketch.refresh_failures == 5
        alarms = [line for line in recorded if "SKETCH_REFRESH_DEAD" in line]
        assert len(alarms) == 1, f"expected one throttled alarm, got {alarms}"

    def test_staleness_guard_degrades_a_frozen_sketch(self) -> None:
        conn = _conn()
        _seed_mixed_session(conn)
        sketch = SpreadSessionSketch(max_age_sec=0.001)
        sketch.refresh(conn, now_utc=NOW)
        assert sketch.percentile("XAUUSD", NOW, 70.0) is not None
        time.sleep(0.02)  # past max_age_sec with no successful refresh
        assert sketch.percentile("XAUUSD", NOW, 70.0) is None
        assert sketch.stale_degradations >= 1
        conn.close()

    def test_sample_cap_keeps_newest_and_counts_drops(self) -> None:
        sketch = SpreadSessionSketch()
        sketch.replace_from_rows(
            [(1, NOW.isoformat(), "XAUUSD", 0.5)], day=NOW.strftime("%Y-%m-%d")
        )
        flood = [
            (10_000 + i, (NOW + timedelta(seconds=i)).isoformat(), "XAUUSD", 1.0 + i * 1e-6)
            for i in range(MAX_SAMPLES + 250)
        ]
        # note_rows goes through the same publish path as refresh.
        added = sketch.note_rows(flood)
        assert added == len(flood)
        resident = sketch.snapshot_debug()
        assert resident["samples"] == MAX_SAMPLES
        # 1 earlier sample + 250 of the flood fell off the oldest end.
        assert resident["dropped_by_cap"] == 251
        # newest won: the freshest value is still resident, the oldest is gone.
        values = [sample[2] for sample in sketch._samples]
        assert flood[-1][3] in values
        assert 0.5 not in values


class _BrokenExecute:
    def execute(self, *_a, **_k):
        raise RuntimeError("database is locked")


# ---------------------------------------------------------------------------
# 2b. THE defect itself: no connection may exist on the read path
# ---------------------------------------------------------------------------


class TestReadPathPerformsNoIo:
    """RED-BEFORE pin (the actual perf-wave R6 defect): at the pre-fix HEAD the
    provider opened a SQLite connection per call. Any connect attempt on the
    read surface now explodes, so the gate provably cannot reach the DB.
    """

    def test_provider_call_cannot_touch_sqlite(self) -> None:
        """Driven without LiveEngine (torch chain) — the seam is the fn itself.

        ``LiveEngine._session_spread_percentile_provider`` is a plain method
        over ``self.spread_session_sketch``; a stand-in object exercises the
        identical code path through ``unbound`` call, which keeps this pin
        hermetic and fast.
        """
        from nexus_scalp.application.live_engine import LiveEngine

        class _Stub:
            spread_session_sketch = None

        def _boom(*_args, **_kwargs):
            raise AssertionError("the spread-percentile read path opened a connection")

        original_connect = sqlite3.connect
        sqlite3.connect = _boom  # type: ignore[assignment]
        try:
            stub = _Stub()
            stub.spread_session_sketch = SpreadSessionSketch()
            value = LiveEngine._session_spread_percentile_provider(
                stub,
                "XAUUSD",
                NOW,
                70.0,  # type: ignore[arg-type]
            )
            assert value is None  # empty sketch -> honest unknown, no I/O

            # Now with a populated sketch: still zero connects.
            conn = original_connect(":memory:")
            create_paper_executions_table(conn)
            for i in range(8):
                _insert(conn, ts=NOW - timedelta(minutes=30 - i), spread=0.1 + 0.02 * i, ticket=i)
            conn.commit()
            filled = SpreadSessionSketch()
            assert filled.refresh(conn, now_utc=NOW) == 8
            stub.spread_session_sketch = filled
            read = LiveEngine._session_spread_percentile_provider(  # type: ignore[arg-type]
                stub, "XAUUSD", NOW, 70.0
            )
            assert read == pytest.approx(session_spread_percentile(conn, "XAUUSD", NOW, 70.0))
            conn.close()
        finally:
            sqlite3.connect = original_connect  # type: ignore[assignment]

    def test_repository_connect_surface_is_not_reached(self) -> None:
        """The provider must not call ``AuditRepository._connect_sqlite`` at
        all — that was the exact pre-fix line (per-call connect)."""
        from nexus_scalp.adapters.database.audit_repository import AuditRepository
        from nexus_scalp.application.live_engine import LiveEngine

        calls: list[float] = []

        def _spy(_self: object, timeout: float) -> sqlite3.Connection:
            calls.append(timeout)
            raise AssertionError("_connect_sqlite reached from the read path")

        original = AuditRepository._connect_sqlite
        AuditRepository._connect_sqlite = _spy  # type: ignore[assignment]
        try:

            class _Stub:
                spread_session_sketch = SpreadSessionSketch()

            assert (
                LiveEngine._session_spread_percentile_provider(  # type: ignore[arg-type]
                    _Stub(), "XAUUSD", NOW, 70.0
                )
                is None
            )
        finally:
            AuditRepository._connect_sqlite = original  # type: ignore[method-assign]
        assert calls == []

    def test_source_pin_no_connection_on_the_read_provider(self) -> None:
        """Class guard (BUG-285 write-only-comment / MIRROR-TEST class): the
        provider body must never mention a connection or the SQL provider."""
        import inspect

        from nexus_scalp.application.live_engine import LiveEngine

        src = inspect.getsource(LiveEngine._session_spread_percentile_provider)
        for forbidden in ("_connect_sqlite", "sqlite3.connect", "session_spread_percentile("):
            assert forbidden not in src, f"read-path I/O returned: {forbidden}"
        assert "spread_session_sketch.percentile" in src


# ---------------------------------------------------------------------------
# 3. Maintenance wiring: the sketch's ONLY caller-side I/O driver
# ---------------------------------------------------------------------------


class TestMaintenanceWiring:
    def _cycle(self, om):
        from nexus_scalp.application.live.maintenance import MaintenanceCycle

        return MaintenanceCycle(om)

    def test_first_pass_always_due_then_60s_cadence(self) -> None:
        import asyncio

        calls: list[int] = []

        class _Om:
            def refresh_spread_session_sketch(self) -> dict[str, object]:
                calls.append(1)
                return {"refreshed": True, "samples": 3, "reason": ""}

        cycle = self._cycle(_Om())
        assert cycle._last_spread_sketch_refresh_time is None
        # uptime 1s with a 60s interval: the 0.0-vs-monotonic shape (BUG-273)
        # would silently skip this first pass forever.
        asyncio.run(cycle._refresh_spread_sketch(now_t=1.0))
        assert len(calls) == 1
        asyncio.run(cycle._refresh_spread_sketch(now_t=31.0))
        assert len(calls) == 1  # < interval: not due
        asyncio.run(cycle._refresh_spread_sketch(now_t=61.0))
        assert len(calls) == 2

    def test_run_cycle_reaches_the_stage(self) -> None:
        """The wiring must be on the ACTUAL maintenance path, not just a
        callable helper (the "shipped but never called" failure class that
        the 2026-09-09/14 waves kept finding). Source pin over a live
        run_cycle drive: the full cycle owns eight other stages whose stubs
        would out-grow the point of this test."""
        import inspect

        from nexus_scalp.application.live.maintenance import MaintenanceCycle

        src = inspect.getsource(MaintenanceCycle.run_cycle)
        assert "await self._refresh_spread_sketch(now_t=now_t)" in src, (
            "run_cycle no longer drives the C3 sketch refresh"
        )
        # And the helper actually calls through to the engine seam off-loop.
        helper = inspect.getsource(MaintenanceCycle._refresh_spread_sketch)
        assert "asyncio.to_thread(refresh)" in helper
        assert "refresh_spread_session_sketch" in helper

    def test_stage_is_failure_isolated_and_off_loop(self) -> None:
        import asyncio
        import threading

        seen_threads: set[int] = set()

        class _Om:
            def refresh_spread_session_sketch(self) -> dict[str, object]:
                seen_threads.add(threading.get_ident())
                raise RuntimeError("boom")  # must never escape into the loop

        cycle = self._cycle(_Om())
        loop_thread = threading.get_ident()

        async def _drive() -> None:
            await cycle._refresh_spread_sketch(now_t=1.0)
            # An unhandled raise would have aborted this coroutine chain.
            await cycle._refresh_spread_sketch(now_t=30.0)  # not due yet

        asyncio.run(_drive())
        assert len(seen_threads) == 1 and loop_thread not in seen_threads, (
            "refresh did not run on a worker thread"
        )
        # The stamp advanced even though the stage raised: a fault cannot
        # turn the cadence into a per-tick retry storm.
        assert cycle._last_spread_sketch_refresh_time == 1.0

    def test_absent_seam_is_tolerated(self) -> None:
        """Duck-typed composition roots (tests, older stand-ins) must not blow
        up the maintenance cycle just because they lack the new seam."""
        import asyncio

        class _Om:
            pass

        cycle = self._cycle(_Om())
        asyncio.run(cycle._refresh_spread_sketch(now_t=1.0))  # no AttributeError
        assert cycle._last_spread_sketch_refresh_time is None

    def test_maintenance_module_does_not_do_db_io_itself(self) -> None:
        """Ownership rule (updated BUG-292): maintenance may CALL the engine
        refresh seam but must never hold a connection or bind the policy fn —
        the policy binding stays composition-root-only."""
        import inspect

        import nexus_scalp.application.live.maintenance as maintenance_mod

        src = inspect.getsource(maintenance_mod)
        assert "signal_policy.session_spread_percentile_fn" not in src
        assert "session_spread_percentile(" not in src
        assert "import sqlite3" not in src
        assert "refresh_spread_session_sketch" in src


# ---------------------------------------------------------------------------
# 4. Debug surface (an operator must distinguish thin-session from dead-refresh)
# ---------------------------------------------------------------------------


class TestObservabilitySurface:
    def test_snapshot_debug_shape(self) -> None:
        conn = _conn()
        _seed_mixed_session(conn)
        sketch = SpreadSessionSketch()
        sketch.refresh(conn, now_utc=NOW)
        snap = sketch.snapshot_debug()
        assert snap["session_day"] == "2026-09-15"
        assert snap["samples"] > 0
        for key in (
            "watermark_rowid",
            "refreshes",
            "refresh_failures",
            "failure_streak",
            "cold_rebuilds",
            "dropped_by_cap",
            "stale_degradations",
            "max_age_sec",
        ):
            assert key in snap, key
        conn.close()

    def test_policy_section_exposes_the_sketch(self) -> None:
        from nexus_scalp.web.debug_snapshot import _policy_section

        class _Engine:
            spread_session_sketch = SpreadSessionSketch()
            _last_proposal = None
            signal_policy = None
            config = None
            _news_enabled = False

        section = _policy_section(_Engine())
        assert "spread_session_sketch" in section
        assert section["spread_session_sketch"]["samples"] == 0

    def test_missing_sketch_degrades_to_unavailable_not_a_crash(self) -> None:
        from nexus_scalp.web.debug_snapshot import _policy_section

        class _Engine:
            _last_proposal = None
            signal_policy = None
            config = None
            _news_enabled = False

        section = _policy_section(_Engine())
        assert section["spread_session_sketch"] == {"available": False}

    def test_non_finite_spreads_are_refused(self) -> None:
        """A NaN/Inf spread row would poison the distribution (and the SQL
        path cannot see them because they never compare > 0.0 — same posture)."""
        sketch = SpreadSessionSketch()
        rows = [
            (1, NOW.isoformat(), "XAUUSD", math.nan),
            (2, NOW.isoformat(), "XAUUSD", math.inf),
            (3, NOW.isoformat(), "XAUUSD", None),
            (4, NOW.isoformat(), "XAUUSD", "abc"),
            (5, NOW.isoformat(), "XAUUSD", 0.25),
        ]
        assert sketch.replace_from_rows(rows, day=NOW.strftime("%Y-%m-%d")) == 1
        assert math.isfinite(sketch.percentile("XAUUSD", NOW, 50.0) or 0.0)

    def test_parity_envelope_breach_is_counted_not_silent(self) -> None:
        """Beyond MAX_SAMPLES fills inside ONE window the sketch measures the
        newest cap (a documented deviation) — but it must SAY so. A silent
        value change on a money path is the actual defect class here."""
        conn = _conn()
        # 5500 fills ALL inside the trailing 4 h window (2 s apart): the SQL
        # provider scans 5500, the capped sketch can only hold 5000.
        total = MAX_SAMPLES + 500
        for i in range(total):
            _insert(
                conn,
                ts=NOW - timedelta(seconds=(total - i) * 2),
                spread=0.05 + (i % 70) * 0.02,
                ticket=100_000 + i,
            )
        conn.commit()
        sql_value = session_spread_percentile(conn, "XAUUSD", NOW, 70.0)
        sketch = SpreadSessionSketch()
        resident = sketch.refresh(conn, now_utc=NOW)
        assert resident == MAX_SAMPLES  # capped
        assert sketch.truncated_reads == 0
        ram_value = sketch.percentile("XAUUSD", NOW, 70.0)
        assert sketch.truncated_reads >= 1, "cap-driven deviation went uncounted"
        assert sketch.snapshot_debug()["truncated_reads"] == sketch.truncated_reads
        # The deviation stays BOUNDED (same population, newest slice) and is
        # surfaced — never a silent mismatch an operator cannot see.
        assert sql_value is not None and ram_value is not None
        assert abs(sql_value - ram_value) < 0.5
        conn.close()

    def test_parity_holds_below_the_envelope_and_never_counts_truncation(self) -> None:
        conn = _conn()
        _seed_mixed_session(conn)
        sketch = SpreadSessionSketch()
        sketch.refresh(conn, now_utc=NOW)
        assert sketch.percentile("XAUUSD", NOW, 70.0) == session_spread_percentile(
            conn, "XAUUSD", NOW, 70.0
        )
        assert sketch.truncated_reads == 0
        conn.close()

    def test_backlog_drain_is_bounded_per_pass_and_continues(self) -> None:
        """A multi-hour maintenance stall must not make one refresh read
        forever: each pass takes at most REFRESH_MAX_QUERIES_PER_PASS batches,
        then the watermark carries the rest — and the drain CONVERGES."""
        from nexus_scalp.experience.spread_sketch import (
            REFRESH_BATCH_LIMIT,
            REFRESH_MAX_QUERIES_PER_PASS,
        )

        conn = _conn()
        total = REFRESH_BATCH_LIMIT * (REFRESH_MAX_QUERIES_PER_PASS + 1) + 10
        for i in range(total):
            _insert(
                conn,
                ts=NOW - timedelta(seconds=total - i),
                spread=0.1 + (i % 50) * 0.01,
                ticket=500_000 + i,
            )
        conn.commit()
        sketch = SpreadSessionSketch()
        sketch.refresh(conn, now_utc=NOW)  # cold rebuild (capped, one query)
        assert sketch._watermark_rowid is not None

        # Simulate the stall: the watermark is left far behind the table.
        sketch._watermark_rowid = 1
        sketch.refresh(conn, now_utc=NOW + timedelta(seconds=61))
        assert sketch.refresh_backlog_truncated >= 1, "an over-batch backlog went unrecorded"
        # Bounded per pass: at most MAX_QUERIES batches were absorbed.
        assert sketch._watermark_rowid > 1

        # ...and it converges: subsequent passes drain the rest.
        def _pending() -> int:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_paper_executions WHERE rowid > ?",
                (sketch._watermark_rowid or 0,),
            ).fetchone()
            return int(row[0])

        for _ in range(REFRESH_MAX_QUERIES_PER_PASS + 2):
            if _pending() == 0:
                break
            sketch.refresh(conn, now_utc=NOW + timedelta(seconds=61 * 2))
        assert _pending() == 0
        conn.close()
