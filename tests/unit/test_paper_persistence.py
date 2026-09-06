"""PAPER Reality Phase 2 — Persistence & Recovery unit tests.

Covers:
  - _persist_state writes {balance, equity, _ticket_counter, _positions, _last_tick_iso}
    with chmod 600 on POSIX
  - _load_state at connect() restores balance (loaded balance wins; NO reset to
    initial_balance even when a different initial is requested)
  - duplicate close after restart does NOT double-credit (closed_tickets)
  - NEXUS_PAPER_PERSIST=0 keeps in-memory behavior (fast tests)
  - _clear_persisted_state() removes the file (test teardown helper)
"""

from __future__ import annotations

import json
import os
import stat as stat_mod
from pathlib import Path

import pytest

os.environ.setdefault("NEXUS_PAPER_STRESS_SEED", "42")

from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.domain.enums import OrderType

SYMBOL = "XAUUSD"


@pytest.fixture()
def paper_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NEXUS_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("NEXUS_PAPER_PERSIST", raising=False)
    return tmp_path


def _state_path(root: Path) -> Path:
    return root / "paper_state.json"


def test_persist_creates_state_file_with_600(paper_root: Path) -> None:
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    a.connect()
    a.get_last_tick(SYMBOL)
    p = _state_path(paper_root)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    for key in ("balance", "equity", "_ticket_counter", "_positions", "_last_tick_iso"):
        assert key in data, f"missing key {key}"
    if os.name != "nt":
        assert stat_mod.S_IMODE(p.stat().st_mode) == 0o600


def test_open_position_balance_unchanged_equity_drifts(paper_root: Path) -> None:
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    a.connect()
    a.get_last_tick(SYMBOL)
    ticket = a.execute_market_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY,
        volume=0.10,
        price=4400.00,
        stop_loss=4380.00,
        take_profit=4450.00,
    )
    assert ticket > 0
    assert a.balance == 10_000.0  # balance unchanged by open
    assert a.equity != a.balance  # equity drifts with floating PnL
    data = json.loads(_state_path(paper_root).read_text(encoding="utf-8"))
    assert len(data["_positions"]) == 1
    assert data["_positions"][0]["ticket"] == ticket


def test_restart_restores_old_balance_not_initial(paper_root: Path) -> None:
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    a.connect()
    a.get_last_tick(SYMBOL)
    ticket = a.execute_market_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY,
        volume=0.10,
        price=4400.00,
        stop_loss=4380.00,
        take_profit=4450.00,
    )
    old_balance = a.balance

    # RESTART with a DIFFERENT initial_balance — loaded balance must win.
    b = PaperMT5Adapter(initial_balance=99_999.99, symbol=SYMBOL)
    b.connect()
    assert b.balance == old_balance, "loaded balance must win; no hidden reset"
    assert [p.ticket for p in b.get_positions()] == [ticket]
    assert b._ticket_counter >= ticket, "ticket counter must survive restart"


def test_duplicate_close_after_restart_no_double_credit(paper_root: Path) -> None:
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    a.connect()
    a.get_last_tick(SYMBOL)
    ticket = a.execute_market_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY,
        volume=0.10,
        price=4400.00,
        stop_loss=4380.00,
        take_profit=4450.00,
    )
    # Manual full close
    assert a.close_position(ticket) is True
    bal_after_first = a.balance

    # Restart: closed ticket survives
    b = PaperMT5Adapter(initial_balance=1.0, symbol=SYMBOL)
    b.connect()
    assert b.balance == bal_after_first
    # Second close attempt must be ignored and NOT credit again
    assert b.close_position(ticket) is False
    assert b.balance == bal_after_first, "duplicate close must not double-credit"
    data = json.loads(_state_path(paper_root).read_text(encoding="utf-8"))
    assert ticket in data["closed_tickets"]


def test_persist_opt_out_env(paper_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEXUS_PAPER_PERSIST", "0")
    a = PaperMT5Adapter(initial_balance=7_777.0, symbol=SYMBOL)
    a.connect()
    a.get_last_tick(SYMBOL)
    assert not _state_path(paper_root).exists(), "opt-out must not write state"
    assert a.balance == 7_777.0


def test_clear_persisted_state_helper(paper_root: Path) -> None:
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    a.connect()
    a.get_last_tick(SYMBOL)
    assert _state_path(paper_root).exists()
    a._clear_persisted_state()
    assert not _state_path(paper_root).exists()


def test_symbol_mismatch_does_not_restore(paper_root: Path) -> None:
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    a.connect()
    a.get_last_tick(SYMBOL)
    b = PaperMT5Adapter(initial_balance=1.0, symbol="EURUSD")
    b.connect()
    assert b.balance == 1.0, "different symbol must not restore the file"
