"""Agent 18 replay-engine forensic regression suite (CHG-0062, TASK-AGENT18-REPLAY).

Covers the live-risk parity surface that the stage-1 probes exposed:

* REPLAY-18-A  LIMIT/market execution fidelity (pending-queue semantics)
* REPLAY-18-B  Determinism / ordering / logical-clock isolation
* REPLAY-18-C  SL/TP first-touch and friction direction

RED-before rule (protocol): the file must be COMMITTED with failures on the
current engine, then hardened one signal at a time. A passing panel without a
red-then-green transition is treated as NOT VERIFIED (INV-018 violation).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal
from nexus_scalp.research.event_source import BarEventSource, TickEventSource
from nexus_scalp.research.streaming_replay import (
    FrozenPolicyRunner,
    ReplayExecutionConfig,
    ReplaySessionConfig,
    StreamingReplayEngine,
)

MODEL = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
T0 = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)


def _mk(action: ActionType, entry: float, sl: float, tp: float, ts: datetime) -> TradeProposal:
    return TradeProposal(
        request_id="agent18-p",
        execution_id=None,
        symbol="XAUUSD",
        generated_at=ts,
        action=action,
        confidence=0.99,
        proposed_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward_ratio=2.0,
        reason_code="AGENT18-PROBE",
        risk_allowed=True,
        final_action=action.value,
    )


class _ProbeRunner(FrozenPolicyRunner):
    """Frozen-compatible policy stub (PHI/C C subclass, not the PHI/C helper).

    Construction deliberately avoids the frozen C-class call trampoline by
    assigning the frozen fields directly; delegation trims the C trampoline
    wrapper and removes a class-level slot conflict path.
    """

    def __init__(self, fn=None, *_, **__) -> None:
        object.__setattr__(self, "params", {})
        object.__setattr__(self, "policy", self)  # type: ignore[assignment]
        object.__setattr__(self, "_fingerprint", "agent18-probe")
        object.__setattr__(self, "_fn", fn)

    def fingerprint(self) -> str:  # type: ignore[override]
        return "agent18-probe"

    def evaluate(self, probs_tensor, tick, fv, regime_state=None):  # type: ignore[override]
        return self._fn(probs_tensor, tick, fv, regime_state)  # type: ignore[attr-defined]


def _bar_records(n: int, t0: datetime) -> list[dict]:
    return [
        {
            "kind": "BAR",
            "timestamp": t0 + timedelta(minutes=k),
            "open": 3300.0 + k * 0.05,
            "high": 3300.1 + k * 0.05,
            "low": 3299.9 + k * 0.05,
            "close": 3300.0 + k * 0.05,
            "tick_volume": 100,
            "spread": 0.2,
            "symbol": "XAUUSD",
            "timeframe": "M1",
        }
        for k in range(n)
    ]


# ==============================================================================
# REPLAY-18-A  LIMIT / market execution fidelity
# ==============================================================================


class TestReplay18APendingQueue:
    """LIMIT proposals must be PENDING (not instantly filled at the tick ask).

    The downstream expectation: a BUY_LIMIT 0.80 below the market mid must
    NOT deposit a trade on the same tick that spawned the proposal.
    """

    def test_buy_limit_below_market_stays_pending_on_spawn_tick(self) -> None:
        """BUY_LIMIT well below the market -> no order/trade on the spawn tick."""

        def _fn(probs, tick, fv, regime_state=None):  # type: ignore[no-untyped-def]
            return _mk(ActionType.BUY_LIMIT, tick.bid - 0.80, tick.bid - 1.80, tick.bid + 0.20, tick.timestamp)

        # 60 warmup bars, then two proposal ticks (2 min apart => past throttle is
        # irrelevant because the pending-queue fix is independent of throttle).
        bars = _bar_records(60 + 5, T0)

        cfg = ReplaySessionConfig(
            model_artifact_path=MODEL,
            execution=ReplayExecutionConfig(volume_min=0.1, volume_step=0.1),
            decide_on="bar_close",
            git_commit="agent18-18A-buy-limit-pending",
        )
        eng = StreamingReplayEngine(cfg, policy=_ProbeRunner(_fn))
        res = eng.run(BarEventSource(bars), run_id="AGENT18-18A-BUY-LIMIT-PENDING")

        # Expectation when the pending queue exists:
        # - the engine accepts the LIMIT -> it is PENDING, not filled.
        # - trades  == 0 (no tick has yet hit the limit level).
        # - pending_orders payload in the result is non-empty (new field).
        # Current engine: LIMIT is rejected by RiskEngine (-> 0 orders, 0 trades),
        # so the test will STAY RED until the queue + bridging path lands.
        assert hasattr(res, "pending_orders") or hasattr(res, "pending_order_count")
        assert list(getattr(res, "pending_orders", []) or []) != [] or int(getattr(res, "pending_order_count", 0) or 0) > 0


# ==============================================================================
# REPLAY-18-B  Determinism / logical-clock isolation
# ==============================================================================


class TestReplay18BDeterminism:
    """Same bar stream + same config -> identical event/ledger/digest triplet."""

    def test_same_bars_same_config_identical_triplet(self) -> None:
        bars = _bar_records(360, T0)
        cfg = ReplaySessionConfig(
            model_artifact_path=MODEL,
            policy_params={"confidence_threshold": 0.35},
            decide_on="bar_close",
            git_commit="agent18-18B-determinism",
        )
        r1 = StreamingReplayEngine(cfg).run(BarEventSource(bars), run_id="AGENT18-18B-A")
        r2 = StreamingReplayEngine(cfg).run(BarEventSource(bars), run_id="AGENT18-18B-B")
        assert r1.event_hash == r2.event_hash
        assert r1.ledger_hash == r2.ledger_hash
        assert r1.trades == r2.trades
        assert r1.orders == r2.orders


# ==============================================================================
# REPLAY-18-C  SL/TP first-touch on the real production proposals
# ==============================================================================


class TestReplay18CSLTP:

    def test_sl_beats_tp_in_shadow_on_the_new_limit_path(self) -> None:
        """A triggered LIMIT with a reachable SL before TP -> SL wins.

        This test exercises the pending-queue + SL/TP surveillance that the
        18A queue introduces. It stays red until both pieces land.
        """

        calls = {"n": 0}

        def _fn(probs, tick, fv, regime_state=None):  # type: ignore[no-untyped-def]
            if calls["n"]:
                return _mk(ActionType.NO_TRADE, tick.bid, tick.bid * 0.99, tick.bid * 1.01, tick.timestamp)
            calls["n"] += 1
            # SELL_LIMIT slightly above market: will be triggered, then SL
            # (above entry) before TP (far below). Bars after trigger pull
            # the mid down then up -> SL first.
            entry = tick.bid + 0.80
            return _mk(ActionType.SELL_LIMIT, entry, entry + 1.50, entry - 2.00, tick.timestamp)

        # warmup + spread
        bars: list[dict] = []
        price = 3300.0
        for k in range(60):
            bars.append(
                {"kind": "BAR", "timestamp": T0 + timedelta(minutes=k),
                 "open": price, "high": price + 0.2, "low": price - 0.2, "close": price,
                 "tick_volume": 100, "spread": 0.2, "symbol": "XAUUSD", "timeframe": "M1"}
            )
        # the trigger bar level + pull-through that first touches SL
        trigger = T0 + timedelta(minutes=60)
        bars += [
            {"kind": "BAR", "timestamp": trigger, "open": 3300.0, "high": 3301.0, "low": 3299.0, "close": 3300.0,
             "tick_volume": 100, "spread": 0.2, "symbol": "XAUUSD", "timeframe": "M1"},
            {"kind": "BAR", "timestamp": trigger + timedelta(minutes=1), "open": 3300.0, "high": 3302.5, "low": 3298.0, "close": 3300.0,
             "tick_volume": 100, "spread": 0.2, "symbol": "XAUUSD", "timeframe": "M1"},
        ]

        cfg = ReplaySessionConfig(
            model_artifact_path=MODEL,
            execution=ReplayExecutionConfig(volume_min=0.1, volume_step=0.1),
            decide_on="bar_close",
            git_commit="agent18-18C-sl-tp-pending",
        )
        eng = StreamingReplayEngine(cfg, policy=_ProbeRunner(_fn))
        res = eng.run(BarEventSource(bars), run_id="AGENT18-18C-SL-TP")
        # Red before the pending queue + SL/TP tick-surveillance for pendings.
        assert len(res.trades) == 1 and res.trades[0]["exit_reason"] == "SL"
