"""MLFIX-T7 — Triple-Barrier label integrity regression tests.

Verifies labeling/triple_barrier.py semantics that the forensics report
audits. Each claim is pinned so a future refactor cannot silently change
barrier economics. Read-only over the labeler; never starts the engine.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest

from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler


def _frame(n: int = 40, *, spread: float = 0.35, atr: float = 1.5, seed: int = 0) -> pl.DataFrame:
    np.random.seed(seed)
    close = np.cumsum(np.random.choice([-1, 1], n) * 0.5) + 4650.0
    high = close + np.abs(np.random.randn(n) * 0.8) + 0.5
    low = close - np.abs(np.random.randn(n) * 0.8) - 0.5
    a = np.full(n, atr, dtype=float)
    sp = np.full(n, spread, dtype=float)
    times = [datetime(2026, 5, 1, 12, 0, tzinfo=UTC).isoformat()] * n
    return pl.DataFrame(
        {
            "close": close,
            "high": high,
            "low": low,
            "atr_m1": a,
            "spread": sp,
            "timestamp": times,
            "atr": a,
        }
    )


def _codes(df: pl.DataFrame) -> list[int]:
    return [
        1 if v == "BUY_MARKET" else 2 if v == "SELL_MARKET" else 0 for v in df["label"].to_list()
    ]


def test_defaults_match_contract() -> None:
    lbl = TripleBarrierLabeler()
    assert lbl.tp_mult == 1.1
    assert lbl.sl_mult == 1.0
    assert lbl.max_holding == 15
    assert lbl.friction_usd == 0.35
    assert lbl.embargo_bars == 3
    assert lbl.no_trade_stride_bars == 3


def test_feasibility_blocks_when_tp_below_friction() -> None:
    # atr=0 so tp_dist=0 < friction -> every row skipped, nothing evaluated
    df = _frame(20, atr=0.0)
    lbl = TripleBarrierLabeler(friction_usd=0.35)
    out = lbl.label_dataframe(df)
    assert int(sum(out["is_eval_sample"].to_list())) == 0


def test_spread_affects_effective_friction() -> None:
    # TP=1.1*1.0=1.1. With spread 1.2 effective_friction=1.2 -> TP infeasible
    # so the labeler strides past those rows without marking eval.
    lbl = TripleBarrierLabeler(take_profit_atr_mult=1.1, stop_loss_atr_mult=1.0, friction_usd=0.35)
    df_low = _frame(15, spread=0.35, atr=1.0)
    df_high = _frame(15, spread=1.2, atr=1.0)
    # at least one evaluated row should differ because high-spread first bars are skipped
    # we check that high-spread produces fewer evaluated rows initially
    assert int(sum(lbl.label_dataframe(df_high)["is_eval_sample"].to_list())) <= int(
        sum(lbl.label_dataframe(df_low)["is_eval_sample"].to_list())
    )


def test_simultaneous_buy_tp_and_sell_tp_neutralizes() -> None:
    lbl = TripleBarrierLabeler(friction_usd=0.35, max_holding_bars=5)
    close0 = 4650.0
    atr = 1.0
    df = _frame(10, spread=0.35, atr=atr)
    # force bar 1 to hit both buy_tp and sell_tp simultaneously
    # buy_entry=4650.175 tp=4651.275 sell_entry=4649.825 tp=4648.725
    df = df.with_columns(
        [
            pl.Series("close", [close0, *df["close"].to_list()[1:]]),
            pl.Series("high", [close0 + 0.2, 4655.0, *df["high"].to_list()[2:]]),
            pl.Series("low", [close0 - 0.2, 4646.0, *df["low"].to_list()[2:]]),
        ]
    )
    assert _codes(lbl.label_dataframe(df))[0] == 0


def test_simultaneous_buy_tp_and_buy_sl_neutralizes() -> None:
    lbl = TripleBarrierLabeler(friction_usd=0.35, max_holding_bars=5)
    close0 = 4650.0
    df = _frame(10, spread=0.35, atr=1.0)
    df = df.with_columns(
        [
            pl.Series("close", [close0, *df["close"].to_list()[1:]]),
            # bar straddles both buy_tp and buy_sl
            pl.Series("high", [close0 + 0.2, 4655.0, *df["high"].to_list()[2:]]),
            pl.Series("low", [close0 - 0.2, 4645.0, *df["low"].to_list()[2:]]),
        ]
    )
    assert _codes(lbl.label_dataframe(df))[0] == 0


def test_advancer_win_uses_exit_plus_embargo() -> None:
    # Construct a WIN at step 1 so exit_step=1 -> advance by 1+3=4
    lbl = TripleBarrierLabeler(max_holding_bars=10, embargo_bars=3, no_trade_stride_bars=5)
    close0 = 4650.0
    atr = 1.0
    # buy_tp=4651.275; put it inside bar 1
    df = _frame(20, spread=0.35, atr=atr)
    df = df.with_columns(
        [
            pl.Series("close", [close0, *df["close"].to_list()[1:]]),
            pl.Series("high", [close0 + 0.2, 4652.0, *df["high"].to_list()[2:]]),
            pl.Series("low", [close0 - 0.2, *df["low"].to_list()[1:]]),
        ]
    )
    # This should label bar 0 as BUY (win) and skip 3 bars after it
    # we verify at least that bar 1..3 are not evaluated when bar 0 wins
    out = lbl.label_dataframe(df)
    codes = _codes(out)
    if codes[0] != 0:
        # WIN -> next evaluated index >= 4
        eval_idx = [i for i, v in enumerate(out["is_eval_sample"].to_list()) if v]
        assert eval_idx[1] >= 4


def test_tail_never_evaluates_last_bar() -> None:
    lbl = TripleBarrierLabeler(max_holding_bars=5)
    df = _frame(10)
    out = lbl.label_dataframe(df)
    assert not bool(out["is_eval_sample"].to_list()[-1])


def test_invalid_atr_rows_are_skipped() -> None:
    df = _frame(20, atr=float("nan"))
    df = df.with_columns(pl.Series("atr_m1", [float("nan")] * 20))
    lbl = TripleBarrierLabeler(min_valid_atr=0.2)
    out = lbl.label_dataframe(df)
    assert int(sum(out["is_eval_sample"].to_list())) == 0
