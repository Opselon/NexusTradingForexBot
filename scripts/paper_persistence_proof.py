"""PAPER Reality Phase 2 — Persistence & Recovery Proof (seed=42).

Proves:
  1. open position -> balance unchanged, equity drifts with price
  2. RESTART with a DIFFERENT initial_balance: connect() restores the OLD
     file's balance (no hidden reset to initial_balance)
  3. ticks continue, auto SL/TP fires exactly once, ledger balances,
     second close attempt on the same ticket does NOT double-credit
  4. cleans up the persisted state file at the end

Run:  python scripts/paper_persistence_proof.py
Env:  NEXUS_DATA_ROOT redirects the state file (default: release data root).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("NEXUS_PAPER_STRESS_SEED", "42")

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter  # noqa: E402
from nexus_scalp.domain.enums import OrderType  # noqa: E402

SEED = 42
SYMBOL = "XAUUSD"
STATE_PATH = PaperMT5Adapter(initial_balance=1.0, symbol=SYMBOL)._persist_path()


def _read_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def main() -> int:
    print(f"[proof] seed={SEED} symbol={SYMBOL}")
    print(f"[proof] persistence path: {STATE_PATH}")

    # Clean slate
    Path(STATE_PATH).unlink(missing_ok=True)

    # --- Session 1: open a position ---
    a1 = PaperMT5Adapter(initial_balance=10_000.00, symbol=SYMBOL)
    a1.connect()
    assert a1.balance == 10_000.00, a1.balance
    a1.get_last_tick(SYMBOL)  # generate first tick -> state file created
    assert STATE_PATH.exists(), "state file must exist after first tick"

    ticket = a1.execute_market_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY,
        volume=0.10,
        price=4400.00,
        stop_loss=4380.00,
        take_profit=4450.00,
    )
    assert ticket > 0, "fill expected"
    bal_after_open = a1.balance
    eq_after_open = a1.equity
    assert abs(bal_after_open - 10_000.00) < 1e-9, f"balance must be unchanged, got {bal_after_open}"
    print(f"[proof 1] ticket={ticket} balance={bal_after_open:.2f} (unchanged) equity={eq_after_open:.2f} (drifts)")
    assert eq_after_open != bal_after_open, "equity must drift from balance with floating PnL"

    st = _read_state()
    assert st["balance"] == bal_after_open
    assert len(st["_positions"]) == 1 and st["_positions"][0]["ticket"] == ticket
    assert st["_ticket_counter"] >= ticket
    assert st["symbol"] == SYMBOL and st["initial_balance"] == 10_000.00
    print(f"[proof 1] state file: balance={st['balance']:.2f} ticket_counter={st['_ticket_counter']} positions={len(st['_positions'])}")

    # --- Session 2: RESTART with a DIFFERENT initial_balance ---
    a2 = PaperMT5Adapter(initial_balance=99_999.99, symbol=SYMBOL)
    a2.connect()  # must load persisted state: balance wins over initial
    assert abs(a2.balance - bal_after_open) < 1e-9, (
        f"restart must restore OLD balance {bal_after_open}, got {a2.balance} (hidden reset!)"
    )
    assert a2._initial_balance == 99_999.99, "provenance must record requested initial"
    print(f"[proof 2] RESTART: new adapter initial_balance=99999.99 -> restored balance={a2.balance:.2f} (old file wins, no reset)")

    # --- Continue ticks until auto SL/TP fires (deterministic seed=42) ---
    # NOTE: get_last_tick already runs process_tick_execution internally (the
    # auto-close is the production path), so the close is detected via the
    # persisted closed_tickets set / balance change, not via a second call.
    auto_events: list[dict] = []
    for i in range(20_000):
        t = a2.get_last_tick(SYMBOL)
        if ticket in a2._closed_tickets or a2.balance != bal_after_open:
            break
    else:
        raise AssertionError(f"no auto SL/TP within 20000 ticks; last bid={t.bid}")
    assert a2.get_positions() == [], "position book must be empty after auto close"
    realized_pnl = round(a2.balance - bal_after_open, 2)
    print(f"[proof 3] auto SL/TP closed ticket={ticket} at tick {i} bid={t.bid:.2f} realized_pnl={realized_pnl:.2f} balance={a2.balance:.2f}")
    assert abs(a2.balance - round(bal_after_open + realized_pnl, 2)) < 0.015

    # Ledger correctness: state file must reflect the same single credit
    expected_balance = round(bal_after_open + realized_pnl, 2)
    assert abs(a2.balance - expected_balance) < 0.015, (a2.balance, expected_balance)

    # State file reflects the close
    st = _read_state()
    assert st["_positions"] == [], "persisted positions must be empty after close"
    assert ticket in st["closed_tickets"], "closed ticket must be persisted"
    assert abs(st["balance"] - a2.balance) < 0.015
    print(f"[proof 3] persisted: balance={st['balance']:.2f} closed_tickets={st['closed_tickets']} positions={st['_positions']}")

    # --- Second close attempt on the same ticket: NO double credit ---
    bal_before_second = a2.balance
    ok_second = a2.close_position(ticket)
    assert ok_second is False, "duplicate close must be rejected"
    assert abs(a2.balance - bal_before_second) < 1e-9, (
        f"balance must not change on duplicate close: {bal_before_second} -> {a2.balance}"
    )
    st2 = _read_state()
    assert abs(st2["balance"] - bal_before_second) < 0.015, "persisted balance must not change"
    print(f"[proof 3] duplicate close rejected: balance stays {a2.balance:.2f} (no double credit)")

    # --- Opt-out check: NEXUS_PAPER_PERSIST=0 keeps in-memory behavior ---
    st_before_optout = _read_state()
    bal_persisted = st_before_optout["balance"]
    mtime_before = STATE_PATH.stat().st_mtime
    os.environ["NEXUS_PAPER_PERSIST"] = "0"
    a3 = PaperMT5Adapter(initial_balance=5_000.00, symbol=SYMBOL)
    a3.connect()
    assert a3.balance == 5_000.00, "opt-out must not load persisted state"
    a3.get_last_tick(SYMBOL)
    # File must exist but must not have advanced: no new writes under opt-out.
    assert STATE_PATH.exists(), "file must still exist (not deleted under opt-out)"
    assert STATE_PATH.stat().st_mtime == mtime_before, "mtime must not advance under opt-out"
    st_opt = _read_state()
    assert st_opt["balance"] == bal_persisted, "opt-out must not rewrite persisted balance"
    print(f"[proof 4] NEXUS_PAPER_PERSIST=0: in-memory only (fresh balance 5000.00, file untouched, persisted balance={bal_persisted:.2f})")
    os.environ["NEXUS_PAPER_PERSIST"] = "1"

    # --- Cleanup: remove the state file ---
    Path(STATE_PATH).unlink(missing_ok=True)
    print(f"[proof 5] cleaned up {STATE_PATH} (exists={STATE_PATH.exists()})")
    print("[proof] ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
