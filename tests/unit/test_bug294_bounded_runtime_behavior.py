"""BUG-294 (perf-bounds lane, NSE master wave 2026-09-15) — BEHAVIORAL
regression net for bounded runtime state on the decision path.

CONVERGENCE NOTE: this lane was scoped from docs/audit/wave_20260914/
10_performance.md §3 item 3 (R9 stdout print), §4 / R7 (_processed_orders)
and §4 / R8 (_report_cache). Every SOURCE fix landed while this lane was
being scoped: R9 deleted in BUG-287 (b292cf4c), R7+R8 bounded via the shared
BoundedLRUMap seam in BUG-290/291 (bae2dc19). ID chain: preferred BUG-288 was
taken (#209 audit-worker queue race), 290/291 taken (role 1), 292/293 reserved
by the concurrent sibling lanes — this entry takes the next free number, 294,
same swarm-ID-collision convention the wave documented (276->278, 285->286).

What BUG-290/291 pinned was STRUCTURAL (the sites ARE BoundedLRUMap
instances with the canonical caps; AC-2 proves a same-instance cache
round-trip). The RED-BEFORE of the original findings is unbounded growth,
which a structural pin cannot demonstrate end-to-end. This battery closes
the behavioral gap — the three pins the mission specified and no existing
test provides:

  A1  The UNKNOWN-regime branch carries NO raw print() (inspect-scoped to
      the if-block, tighter than the file-wide R9 regex in
      test_obs_trace_chain.py) — AND log_signal survives sys.stdout being a
      raising object: stdout is write-invisible to the audit decision path
      (pre-fix, a blocked/failing console stalled the tick loop; the
      structured logger.warning still fires, so the diagnostic is intact).
  B1  Eviction through the REAL dispatch seam: shrink the manager's own
      BoundedLRUMap instance (caps are env-free by design, so the instance
      is the only deterministic lever — same technique BUG-290 test-om2
      uses on a probe), drive limit+N DISTINCT request_ids through
      om.dispatch_order, and pin: len capped, oldest evicted, newest
      retained, retained id STILL dedupes (refused without a broker send),
      evicted id re-ACCEPTED — the documented trade-off, made visible so
      the choice can never be un-known (deeper guards: executions UNIQUE
      identity + broker request-id idempotency, per the BUG-290 safety case).
  C1  The accounting cache under REAL browsing: period_series with
      caller-supplied moments churns `{kind}:{bounds.key}` forever (the
      exact R8 growth driver). Shrink the instance, browse beyond the cap
      through the public API, pin: residency capped, evictions counted, and
      a re-browse of an evicted period RECOMPUTES with identical derived
      truth (cache is advisory — money truth never depends on residency).

No src changes: the fixes landed; this net prevents the behavior class from
silently regressing under a structurally-green test suite.
"""

from __future__ import annotations

import inspect
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.bounded_map import BoundedLRUMap
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal


# ---------------------------------------------------------------------------
# A1 — UNKNOWN-regime audit path: no print, and stdout failure is invisible
# ---------------------------------------------------------------------------
def _proposal(regime: str = "UNKNOWN", request_id: str = "req-b294-a1") -> TradeProposal:
    return TradeProposal(
        request_id=request_id,
        execution_id="EXEC-20260915-120000-b294aa",
        symbol="XAUUSD",
        generated_at=datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC),
        action=ActionType.BUY,
        confidence=0.55,
        proposed_entry=2000.0,
        stop_loss=1999.0,
        take_profit=2002.0,
        risk_reward_ratio=2.0,
        reason_code="NO_TRADE_REGIME_UNKNOWN",
        regime=regime,
    )


def test_a1a_unknown_regime_branch_has_no_print() -> None:
    """inspect-scoped: the UNKNOWN if-block itself may never regrow a print().

    Stronger than the file-wide R9 regex pin (test_obs_trace_chain.py):
    a print() elsewhere in the module keeps that pin red, but THIS pin names
    the exact tick-path block lane-10 §3 item 3 flagged (audit_repository.py
    :2376 pre-fix). RED-BEFORE is structural: at 65d3cb50 this block contained
    `print(json.dumps(unknown_log))`.
    """
    src = inspect.getsource(AuditRepository.log_signal)
    start = src.find('regime_str == "UNKNOWN"')
    assert start != -1, "UNKNOWN-regime diagnostic block vanished from log_signal"
    # The block runs from the guard to its structured warning; the INSERT is
    # the first thing after it. Strip comments (the block DOCUMENTS the
    # removal and quotes the old call — text, not code).
    end = src.find("        query =", start)
    assert end != -1
    block = "\n".join(
        line for line in src[start:end].splitlines() if not line.lstrip().startswith("#")
    )
    assert "print(" not in block, (
        "R9/BUG-287 class REGRESSION: raw stdout write is back inside the "
        "UNKNOWN-regime branch of log_signal (tick-path blocking I/O; the "
        "structured logger.warning already carries the payload in `extra`)"
    )
    assert "logger.warning" in block, "the structured replacement must stay in the block"


def test_a1b_log_signal_survives_broken_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behavioral: stdout may be BLOCKED or FAILING on a trading box (broken
    pipe to a launcher console, closed handle). Pre-fix, `print(json.dumps(...))`
    on the UNKNOWN path made that a tick-loop stall (lane-10 §3.3). Now the
    enqueue and the structured warning must both complete with zero stdout
    writes — proven by sys.stdout raising on ANY write, not by capsys
    (structlog routing is host-dependent; see the OBS-TRACE ruling)."""

    class _RaisingStdout:
        def write(self, *_a: object) -> int:
            raise OSError("simulated blocked console/pipe")

        def flush(self) -> None:
            raise OSError("simulated blocked console/pipe")

    class _RecordingLogger:
        def __init__(self) -> None:
            self.warnings: list[tuple[str, dict]] = []

        def warning(self, msg: str, **kw: object) -> None:
            self.warnings.append((msg, dict(kw)))

        def error(self, msg: str, **kw: object) -> None:
            pass

        def info(self, msg: str, **kw: object) -> None:
            pass

        def critical(self, msg: str, **kw: object) -> None:
            pass

        def debug(self, msg: str, **kw: object) -> None:
            pass

    from nexus_scalp.adapters.database import audit_repository as _ar

    repo = AuditRepository(db_url="sqlite:///:memory:")
    dbl = _RecordingLogger()
    try:
        # Logger neutralized per the OBS-TRACE ruling (structlog console
        # routing to stdout is host-dependent); the RAW print() never went
        # through the logger, so if it were still in the block it would
        # crash on the raising stdout below regardless (true RED-BEFORE).
        monkeypatch.setattr(_ar, "logger", dbl)
        monkeypatch.setattr(sys, "stdout", _RaisingStdout())
        try:
            repo.log_signal(_proposal())  # UNKNOWN regime -> the old print site
        finally:
            monkeypatch.undo()
        assert dbl.warnings and any("UNKNOWN regime" in m for m, _ in dbl.warnings), (
            "structured diagnostic must still fire while stdout is broken"
        )
        assert repo.flush(timeout_sec=10.0), "audit queue did not drain"
        # Persisted: the row for THIS request survived the stdout hazard.
        # (Counted per request_id, never table-wide: every :memory: AuditRepository
        # shares one cache=shared DB in-process by design.)
        con = repo._connect_sqlite(timeout=5.0)
        try:
            (count,) = con.execute(
                "SELECT COUNT(*) FROM audit_signals WHERE request_id = ?",
                ("req-b294-a1",),
            ).fetchone()
        finally:
            con.close()
        assert count == 1
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# B1 — duplicate-dispatch guard: real eviction semantics through dispatch.py
# ---------------------------------------------------------------------------
class _DispatchSpyAdapter:
    """Same harness shape as test_execution_quality_contract.py EC-1."""

    def __init__(self) -> None:
        self.market_calls: list[dict] = []
        self._next_ticket = 9000

    def execute_market_order(self, **kw: object) -> int:
        self.market_calls.append(kw)
        self._next_ticket += 1
        return self._next_ticket

    def get_positions(self, symbol: str | None = None) -> list:
        return []

    def get_account_info(self) -> None:
        return None

    def get_symbol_info(self, symbol: str) -> None:
        return None


def _manager(adapter: object) -> object:
    from nexus_scalp.execution.order_manager import OrderLifecycleManager

    om = OrderLifecycleManager(
        adapter=adapter,  # type: ignore[arg-type]
        audit_repo=AuditRepository(db_url="sqlite:///:memory:"),
        experience_engine=None,
    )
    om.notifier = None  # type: ignore[attr-defined]
    return om


def _decision(request_id: str) -> MagicMock:
    d = MagicMock()
    # MAINTENANCE_WINDOW guard needs a real timestamp far outside the nightly
    # break (same CI-determinism ruling as the EC battery, f1c2c14e follow-up).
    d.generated_at = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    d.action = ActionType.BUY_MARKET
    d.symbol = "XAUUSD"
    d.proposed_entry = 2000.0
    d.stop_loss = 1999.0
    d.take_profit = 2002.0
    d.request_id = request_id
    d.confidence = 0.7
    d.regime = "TREND"
    d.execution_mode = "STANDARD"
    d.execution_id = "exec_b294"
    d.ticket = 0
    d.reason_code = "TEST"
    return d


def test_b1_processed_orders_eviction_through_the_real_dispatch_seam(monkeypatch) -> None:
    """Drive limit+N distinct request_ids through om.dispatch_order and pin
    the FULL bounded contract on the manager's REAL guard instance:
    capped size, oldest-gone, newest-present, retained id dedupes (zero
    extra broker sends), evicted id re-accepted (the documented BUG-290
    trade-off — visible, with the durable guards named in that ledger)."""
    from nexus_scalp.execution import order_manager as om_mod

    adapter = _DispatchSpyAdapter()
    om = _manager(adapter)
    guard = om._processed_orders  # type: ignore[attr-defined]
    assert isinstance(guard, BoundedLRUMap)
    assert guard.maxsize == om_mod.PROCESSED_ORDERS_GUARD_MAX == 50_000

    # Deterministic shrink of the SAME instance every seam operation flows
    # through (env-free cap; the instance is the only cheap lever — BUG-290
    # om-2 pinned the type on a probe because it never DROVE eviction).
    monkeypatch.setattr(guard, "_maxsize", 3)

    ids = [f"req-b294-{i}" for i in range(6)]
    for rid in ids:
        assert om.dispatch_order(_decision(rid), 0.10) is True  # type: ignore[attr-defined]
    assert len(adapter.market_calls) == 6, "each fresh request_id must dispatch once"
    assert len(guard) == 3, "guard exceeded its maxsize through the real seam"
    assert guard.evictions == 3
    # Oldest three evicted, newest three retained.
    for rid in ids[:3]:
        assert rid not in guard
    for rid in ids[3:]:
        assert rid in guard

    # Retained id STILL dedupes: refused with ZERO extra broker sends.
    assert om.dispatch_order(_decision(ids[-1]), 0.10) is False  # type: ignore[attr-defined]
    assert len(adapter.market_calls) == 6, "duplicate dispatch reached the broker"

    # Documented trade-off: an EVICTED old id is re-accepted (same-session
    # guard only — cross-boot protection is the executions UNIQUE identity,
    # BUG-290 safety case). Pinned so the choice is visible to any reader.
    assert om.dispatch_order(_decision(ids[0]), 0.10) is True  # type: ignore[attr-defined]
    assert len(adapter.market_calls) == 7


def test_b2_guard_shrink_is_only_a_test_lever_and_recovers() -> None:
    """The seam WRITES must keep working after an eviction (guard[rid] set
    paths in dispatch.py:144/512/586 survive popitem churn), and `in` lookups
    must never reorder (INV-001 hot-path contract the real guard relies on).
    """
    adapter = _DispatchSpyAdapter()
    om = _manager(adapter)
    guard: BoundedLRUMap = om._processed_orders  # type: ignore[attr-defined]
    guard._maxsize = 2  # test-local instance shrink (no module state mutated)

    for i in range(5):
        assert om.dispatch_order(_decision(f"req-b2-{i}"), 0.10) is True  # type: ignore[attr-defined]
    assert len(guard) == 2 and list(guard) == ["req-b2-3", "req-b2-4"]
    # `in` does not touch order: probing the LRU end must not save it.
    assert "req-b2-3" in guard
    assert om.dispatch_order(_decision("req-b2-5"), 0.10) is True  # type: ignore[attr-defined]
    assert "req-b2-3" not in guard and "req-b2-5" in guard


# ---------------------------------------------------------------------------
# C1 — accounting report cache under real browsing
# ---------------------------------------------------------------------------
def _core():
    from nexus_scalp.accounting.core import AccountingCore

    return AccountingCore(audit_repo=AuditRepository(db_url="sqlite:///:memory:"))


def test_c1_period_series_browsing_stays_bounded_and_truthful(monkeypatch) -> None:
    """R8's growth driver is historical browsing: period_series stores one key
    per {kind}:{bounds.key} forever. Browse beyond the cap through the public
    API on the REAL instance, pin residency capped + evictions counted, then
    re-browse an evicted period and pin the derived answer is IDENTICAL —
    the cache is advisory; money truth never depends on residency."""
    from nexus_scalp.accounting.periods import PeriodKind

    core = _core()
    cache = core._report_cache
    assert isinstance(cache, BoundedLRUMap)

    at = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(cache, "_maxsize", 4)

    # 6 consecutive DAY periods -> 6 distinct keys through the public API.
    reports = core.period_series(PeriodKind.DAY, count=6, at=at)
    assert len(reports) == 6
    assert len(cache) == 4, "cache exceeded maxsize under real browsing"
    assert cache.evictions == 2
    # Oldest browsed periods are the ones gone (LRU-first).
    assert "DAY:2026-08-15" not in cache and "DAY:2026-08-16" not in cache
    assert "DAY:2026-08-20" in cache

    # Re-browse an evicted period through BOTH public surfaces:
    # period_report recomputes on miss (it reads the cache, never writes it —
    # the write side is period_series warm), and the derived answer must be
    # IDENTICAL to the warm one. Cache is advisory; money truth never
    # depends on residency.
    evicted_at = datetime(2026, 8, 15, 9, 0, 0, tzinfo=UTC)
    warm = reports[0]
    cold = core.period_report(PeriodKind.DAY, at=evicted_at, use_cache=True)
    assert cold.key == warm.key == "2026-08-15"
    assert cold.net_pnl == warm.net_pnl
    assert cold.total_trades == warm.total_trades
    assert cold.has_data == warm.has_data
    # Re-adding the evicted key via the warm surface (period_series) still
    # enforces the cap — one more eviction, size never exceeds maxsize.
    core.period_series(PeriodKind.DAY, count=1, at=evicted_at)
    assert "DAY:2026-08-15" in cache
    assert len(cache) == 4


def test_c2_contains_semantics_preserved_for_report_serving() -> None:
    """The serving path reads via .get() (moves MRU) inside period_report and
    period_series; the get-warm/set-cold round-trip through the seam must
    behave exactly like the dict it replaced (behavior-preserving swap)."""
    from nexus_scalp.accounting.models import PeriodReport
    from nexus_scalp.accounting.periods import PeriodKind

    core = _core()
    start = datetime(2026, 7, 4, tzinfo=UTC)
    report = PeriodReport(
        kind=PeriodKind.DAY,
        key="2026-07-04",
        label="Jul 4",
        period_start=start,
        period_end=datetime(2026, 7, 5, tzinfo=UTC),
        total_trades=1,
        win_count=1,
        loss_count=0,
        gross_profit=25.0,
        gross_loss=0.0,
        net_pnl=25.0,
        has_data=True,
    )
    core._report_cache["DAY:2026-07-04"] = report
    got = core.period_report(PeriodKind.DAY, at=start, use_cache=True)
    assert got is report, "cached report must be served by identity (dict-parity)"
