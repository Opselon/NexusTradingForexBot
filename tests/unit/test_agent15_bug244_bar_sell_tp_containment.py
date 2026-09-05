"""Agent 15 RED-GREEN regression: BUG-244 bar-mode SELL TP containment (P0).

Phantom-TP probe (gap-down): a SELL position whose TP lies ABOVE a gap-down
bar's high must never fire a phantom TP exit. Pre-fix, the buggy expression
'low <= TP >= low' degenerated to 'TP >= low' (always true for TP above the
bar high), closing SELLs at TP before price ever reached it.

Also covers: SL-first tie-break, BUY-branch regression guard, ledger
determinism. Related fix commit: 3f5bef2d (streaming_replay.py:900).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal
from nexus_scalp.research.event_source import BarEventSource
from nexus_scalp.research.streaming_replay import (
    FrozenPolicyRunner,
    ReplaySessionConfig,
    StreamingReplayEngine,
)

MODEL = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
T0 = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)


def _mk(action, entry, sl, tp, ts):
    return TradeProposal(
        request_id="a15-244",
        symbol="XAUUSD",
        generated_at=ts,
        action=action,
        confidence=0.99,
        proposed_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward_ratio=2.0,
        reason_code="A15-244",
        risk_allowed=True,
        final_action=action.value,
    )


class _Runner(FrozenPolicyRunner):
    """Frozen-compatible policy stub (delegation to a plain callable)."""

    def __init__(self, fn=None, *_, **__) -> None:
        object.__setattr__(self, "params", {})
        object.__setattr__(self, "policy", self)
        object.__setattr__(self, "_fingerprint", "a15-244")
        object.__setattr__(self, "_fn", fn)

    def fingerprint(self):
        return "a15-244"

    def evaluate(self, probs_tensor, tick, fv, regime_state=None):
        return self._fn(probs_tensor, tick, fv, regime_state)


def _bars(n, lo, hi, gap=None, gap_at=None):
    """n flat bars [lo,hi]; optionally switch to the gap range from gap_at."""
    out = []
    for k in range(n):
        L, H = gap if gap and gap_at is not None and k >= gap_at else (lo, hi)
        c = (L + H) / 2
        out.append(
            {
                "kind": "BAR",
                "timestamp": T0 + timedelta(minutes=k),
                "open": c,
                "high": H,
                "low": L,
                "close": c,
                "tick_volume": 10,
                "spread": 0.2,
                "symbol": "XAUUSD",
                "timeframe": "M1",
            }
        )
    return out


def _run(bars, fn, rid):
    cfg = ReplaySessionConfig(
        model_artifact_path=MODEL, decide_on="bar_close", git_commit="a15-244"
    )
    return StreamingReplayEngine(cfg, policy=_Runner(fn)).run(BarEventSource(bars), run_id=rid)


def _no_trade(t):
    return _mk(ActionType.NO_TRADE, t.bid, t.bid - 1.0, t.bid + 1.0, t.timestamp)


def test_bug244_no_phantom_sell_tp_above_gap():
    """SELL TP=2640 above gap-down bar [2600,2610] -> NO TP exit (pre-fix: phantom)."""

    def fn(probs, tick, fv, regime_state=None):
        if tick.timestamp == T0 + timedelta(minutes=65):
            return _mk(ActionType.SELL_MARKET, tick.bid, 2665.0, 2640.0, tick.timestamp)
        return _no_trade(tick)

    res = _run(_bars(70, 2645.0, 2660.0, gap=(2600.0, 2610.0), gap_at=66), fn, "A15-PHANTOM")
    tp = [x for x in res.trades if x["exit_reason"] == "TP"]
    assert not tp, f"phantom TP exit recorded: {tp}"


def test_bug244_sl_first_tie_break_intact_for_sell():
    """SELL SL=2658 & TP=2650 both inside [2645,2660] -> SL-first wins at 2658."""

    def fn(probs, tick, fv, regime_state=None):
        if tick.timestamp == T0 + timedelta(minutes=65):
            return _mk(ActionType.SELL_MARKET, tick.bid, 2658.0, 2650.0, tick.timestamp)
        return _no_trade(tick)

    res = _run(_bars(70, 2645.0, 2660.0), fn, "A15-SL-FIRST")
    assert res.trades, "no exit recorded"
    first = res.trades[0]
    assert first["exit_reason"] == "SL" and abs(first["exit_price"] - 2658.0) < 1e-9


def test_bug244_buy_branch_unchanged():
    """BUY branch regression guard: SL inside range still SL-first at 2649."""

    def fn(probs, tick, fv, regime_state=None):
        if tick.timestamp == T0 + timedelta(minutes=65):
            return _mk(ActionType.BUY_MARKET, tick.ask, 2649.0, 2654.0, tick.timestamp)
        return _no_trade(tick)

    res = _run(_bars(70, 2645.0, 2660.0), fn, "A15-BUY")
    assert res.trades, "no exit recorded"
    first = res.trades[0]
    assert first["exit_reason"] == "SL" and abs(first["exit_price"] - 2649.0) < 1e-9


def test_bug244_ledger_hash_determinism():
    """Same bars + same run_id -> identical ledger_hash (determinism guard)."""

    def fn(probs, tick, fv, regime_state=None):
        if tick.timestamp == T0 + timedelta(minutes=65):
            return _mk(ActionType.SELL_MARKET, tick.bid, 2658.0, 2650.0, tick.timestamp)
        return _no_trade(tick)

    bars = _bars(70, 2645.0, 2660.0)
    r1 = _run(bars, fn, "A15-DET")
    r2 = _run(bars, fn, "A15-DET")
    assert r1.ledger_hash == r2.ledger_hash
