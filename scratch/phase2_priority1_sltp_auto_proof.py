"""PAPER Reality Phase 2 — Priority 1: automatic SL/TP execution on every tick.

Proves the no-manual-call behaviour of PaperMT5Adapter.process_tick_execution():
  1. BUY with a tight SL below -> ticks advance -> SL crosses AUTOMATICALLY
     (no close_position call) -> position removed, balance updated,
     reconcile_accounting() passes, [PAPER_SLTP] audit event printed.
  2. SELL with a tight TP below -> TP hit automatically.
  3. Two open positions evaluated INDEPENDENTLY on the same tick stream.

Seed=42 => deterministic replay (adapter _rng + tick walk).

Run:  .venv/bin/python scratch/phase2_priority1_sltp_auto_proof.py
"""
from __future__ import annotations

import os
import sys

os.environ["NEXUS_PAPER_STRESS_SEED"] = "42"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter  # noqa: E402
from nexus_scalp.domain.enums import OrderType  # noqa: E402


def new_adapter() -> PaperMT5Adapter:
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    assert a.connect() is True
    assert a._seed == 42, f"expected env seed 42, got {a._seed}"
    return a


def banner(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


# ---------------------------------------------------------------------------
# Direct API proof: process_tick_execution with an explicit synthetic tick
# (independent of the auto path, proves the method contract).
# ---------------------------------------------------------------------------
def direct_api_proof() -> None:
    banner("DIRECT API: process_tick_execution(tick) contract")
    from datetime import UTC, datetime

    from nexus_scalp.domain.models import TickData

    a = new_adapter()
    t0 = a.get_last_tick("XAUUSD")
    ticket = a.execute_market_order(
        "XAUUSD", OrderType.BUY, 0.10, float(t0.ask),
        stop_loss=round(float(t0.bid) - 0.30, 2), take_profit=0.0,
    )
    pos = next(p for p in a.get_positions() if p.ticket == ticket)
    print(f"open BUY ticket={ticket} fill={pos.price_open:.2f} sl={pos.sl:.2f}")
    # Craft a tick that guarantees bid <= sl; verify deterministic slippage.
    forced = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC),
        bid=float(pos.sl) - 0.05, ask=float(pos.sl) + 0.10, last=float(pos.sl) - 0.05,
        volume=1.0, flags=6,
    )
    bal_before = float(a.balance)
    out = a.process_tick_execution(forced)
    print(f"forced tick bid={forced.bid:.2f} ask={forced.ask:.2f} -> {out}")
    assert len(out) == 1 and out[0]["event"] == "SL_HIT"
    assert out[0]["ticket"] == ticket
    assert out[0]["close_price"] <= pos.sl + 1e-9
    # no-manual-close guard: ticket is gone, manual close_position must return False
    assert a.close_position(ticket) is False, "double-credit guard broken"
    # second call on same tick is idempotent
    assert a.process_tick_execution(forced) == []
    # cached-tick form: get_last_tick caches as _last_tick; process_tick_execution()
    # with no argument uses the cached tick and does NOT call get_last_tick again.
    a2 = new_adapter()
    t0b = a2.get_last_tick("XAUUSD")
    tk2 = a2.execute_market_order(
        "XAUUSD", OrderType.SELL, 0.10, float(t0b.bid),
        stop_loss=0.0, take_profit=round(float(t0b.bid) - 0.25, 2),
    )
    p2 = next(p for p in a2.get_positions() if p.ticket == tk2)
    forced2 = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC),
        bid=float(p2.tp) - 0.10, ask=float(p2.tp), last=float(p2.tp),
        volume=1.0, flags=6,
    )
    a2._last_tick = forced2  # pretend this is the last generated tick
    out2 = a2.process_tick_execution()  # no tick arg => uses _last_tick
    print(f"cached-tick call () -> {out2}")
    assert len(out2) == 1 and out2[0]["event"] == "TP_HIT"
    assert abs(out2[0]["close_price"] - p2.tp) < 1e-9
    print("DIRECT API OK: explicit tick + cached-tick forms, double-credit guard, no recursion")


# ---------------------------------------------------------------------------
# Scenario 1: BUY + tight SL -> automatic SL_HIT via get_last_tick alone
# ---------------------------------------------------------------------------
def scenario_buy_sl() -> None:
    banner("SCENARIO 1: BUY 0.10 XAUUSD, tight SL 20c below entry (auto SL)")
    a = new_adapter()
    tick0 = a.get_last_tick("XAUUSD")
    print(f"seed tick: bid={tick0.bid:.2f} ask={tick0.ask:.2f}")

    ticket = a.execute_market_order(
        "XAUUSD", OrderType.BUY, 0.10, float(tick0.ask),
        stop_loss=round(float(tick0.bid) - 0.20, 2), take_profit=0.0,
    )
    assert ticket > 0, "BUY open failed"
    pos = next(p for p in a.get_positions() if p.ticket == ticket)
    bal_open = float(a.balance)
    eq_open = float(a.equity)
    print(
        f"OPEN  ticket={ticket} type=BUY vol={pos.volume} fill={pos.price_open:.2f} "
        f"sl={pos.sl:.2f} balance={bal_open:.2f} equity={eq_open:.2f}"
    )
    # sanity: process_tick_execution with no trigger returns []
    assert a.process_tick_execution() == []

    hit_tick_no = None
    last_tick = None
    hit_bal = None
    for n in range(1, 2001):
        before = len(a.get_positions())
        t = a.get_last_tick("XAUUSD")  # <-- NO manual close anywhere
        last_tick = t
        after = len(a.get_positions())
        if before == 1 and after == 0:
            hit_tick_no = n
            hit_bal = float(a.balance)
            break
    assert hit_tick_no is not None, "SL never hit within 2000 ticks"
    assert not a.get_positions(), "position still open after SL"
    recon = a.reconcile_accounting()
    # pnl is balance delta; should be negative for a stop (loss)
    delta = float(a.balance) - bal_open
    print(
        f"AUTO SL HIT on tick N={hit_tick_no}: "
        f"last tick bid={last_tick.bid:.2f} ask={last_tick.ask:.2f} "
        f"sl={pos.sl:.2f} balance {bal_open:.2f} -> {hit_bal:.2f} (delta {delta:+.2f})"
    )
    print(f"positions open: {len(a.get_positions())}")
    print(f"reconcile_accounting(): {recon}")
    assert recon["ok"] is True
    assert delta < 0, "stop should be a loss"
    # manual close after auto close must not double-credit
    assert a.close_position(ticket) is False
    print("SCENARIO 1 OK: automatic BUY SL execution (no manual close), accounting reconciled")
    print("  audit event [PAPER_SLTP] printed above by the adapter (SL_HIT)")


# ---------------------------------------------------------------------------
# Scenario 2: SELL + tight TP -> automatic TP_HIT
# ---------------------------------------------------------------------------
def scenario_sell_tp() -> None:
    banner("SCENARIO 2: SELL 0.10 XAUUSD, tight TP 15c below entry (auto TP)")
    a = new_adapter()
    tick0 = a.get_last_tick("XAUUSD")
    print(f"seed tick: bid={tick0.bid:.2f} ask={tick0.ask:.2f}")

    ticket = a.execute_market_order(
        "XAUUSD", OrderType.SELL, 0.10, float(tick0.bid),
        stop_loss=0.0, take_profit=round(float(tick0.ask) - 0.15, 2),
    )
    assert ticket > 0, "SELL open failed"
    pos = next(p for p in a.get_positions() if p.ticket == ticket)
    bal_open = float(a.balance)
    print(
        f"OPEN  ticket={ticket} type=SELL vol={pos.volume} fill={pos.price_open:.2f} "
        f"tp={pos.tp:.2f} balance={bal_open:.2f} equity={a.equity:.2f}"
    )

    hit_tick_no = None
    last_tick = None
    hit_bal = None
    for n in range(1, 2001):
        before = len(a.get_positions())
        t = a.get_last_tick("XAUUSD")
        last_tick = t
        after = len(a.get_positions())
        if before == 1 and after == 0:
            hit_tick_no = n
            hit_bal = float(a.balance)
            break
    assert hit_tick_no is not None, "TP never hit within 2000 ticks"
    assert not a.get_positions(), "position still open after TP"
    recon = a.reconcile_accounting()
    delta = float(a.balance) - bal_open
    print(
        f"AUTO TP HIT on tick N={hit_tick_no}: "
        f"last tick bid={last_tick.bid:.2f} ask={last_tick.ask:.2f} "
        f"tp={pos.tp:.2f} balance {bal_open:.2f} -> {hit_bal:.2f} (delta {delta:+.2f})"
    )
    print(f"reconcile_accounting(): {recon}")
    assert recon["ok"] is True
    assert delta > 0, "take-profit should be a gain"
    # TP fills exactly at the limit price (no positive slippage) — delta
    # must therefore equal (fill - open) inverted for SELL
    expected = round((float(pos.price_open) - float(pos.tp)) * float(pos.volume) * 100.0, 2)
    assert abs(delta - expected) < 0.015, (delta, expected)
    print("SCENARIO 2 OK: automatic SELL TP execution at limit price")


# ---------------------------------------------------------------------------
# Scenario 3: two open positions evaluated independently on the same stream
# ---------------------------------------------------------------------------
def scenario_two_positions() -> None:
    banner("SCENARIO 3: 2 positions (BUY-tight-SL + SELL-tight-TP) independent evaluation")
    a = new_adapter()
    tick0 = a.get_last_tick("XAUUSD")
    print(f"seed tick: bid={tick0.bid:.2f} ask={tick0.ask:.2f}")

    t_buy = a.execute_market_order(
        "XAUUSD", OrderType.BUY, 0.05, float(tick0.ask),
        stop_loss=round(float(tick0.bid) - 0.10, 2), take_profit=0.0,
    )
    t_sell = a.execute_market_order(
        "XAUUSD", OrderType.SELL, 0.07, float(tick0.bid),
        stop_loss=0.0, take_profit=round(float(tick0.bid) - 0.05, 2),
    )
    assert t_buy > 0 and t_sell > 0
    p_buy = next(p for p in a.get_positions() if p.ticket == t_buy)
    p_sell = next(p for p in a.get_positions() if p.ticket == t_sell)
    bal_open = float(a.balance)
    print(f"OPEN  BUY  ticket={t_buy} vol=0.05 fill={p_buy.price_open:.2f} sl={p_buy.sl:.2f}")
    print(f"OPEN  SELL ticket={t_sell} vol=0.07 fill={p_sell.price_open:.2f} tp={p_sell.tp:.2f}")
    assert len(a.get_positions()) == 2

    exits: list[tuple[int, int]] = []  # (tick_n, ticket)
    bal_before_each: dict[int, float] = {t_buy: bal_open, t_sell: bal_open}
    # Track which tickets have already exited so we know the per-exit delta.
    seen = set()
    last_bal = bal_open
    for n in range(1, 3001):
        before_tickets = {p.ticket for p in a.get_positions()}
        t = a.get_last_tick("XAUUSD")  # <-- NO manual close anywhere
        after_tickets = {p.ticket for p in a.get_positions()}
        departed = before_tickets - after_tickets
        if departed:
            for tk in departed:
                exits.append((n, tk))
                print(f"  tick N={n} auto-closed ticket={tk} (balance {last_bal:.2f} -> {a.balance:.2f})")
            last_bal = float(a.balance)
        if not after_tickets:
            break
    assert not a.get_positions(), f"positions left open: {a.get_positions()}"
    assert {tk for _, tk in exits} == {t_buy, t_sell}, exits
    recon = a.reconcile_accounting()
    total_delta = float(a.balance) - bal_open
    print(f"exit order (tick_N, ticket): {exits}")
    print(f"balance: {bal_open:.2f} -> {a.balance:.2f} (delta {total_delta:+.2f})")
    print(f"reconcile_accounting(): {recon}")
    assert recon["ok"] is True
    print("SCENARIO 3 OK: both positions evaluated independently (SNAPSHOT), both auto-closed")


if __name__ == "__main__":
    print("NEXUS_PAPER_STRESS_SEED =", os.environ.get("NEXUS_PAPER_STRESS_SEED"))
    direct_api_proof()
    scenario_buy_sl()
    scenario_sell_tp()
    scenario_two_positions()
    banner("ALL SCENARIOS PASSED (seed=42, zero manual close calls)")
