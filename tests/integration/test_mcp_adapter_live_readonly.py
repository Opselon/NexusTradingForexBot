"""
Integration Test — MCPMT5Adapter LIVE read-only probe
=====================================================
PURPOSE: exercise the real MetaTrader 5 MCP endpoint through the read-only
       adapter, proving the new transport actually reaches a terminal
       (IMPL-F's whole point: the capability did not exist before this lane).
OWNER: MT5-PARITY-FORENSICS lane IMPL-F.
CONSUMES: NSE_MT5_MCP_URL / NSE_MT5_MCP_API_KEY from the environment or the
       repo-root .env (loaded by the adapter itself).
PROVIDES: a LIVE, READ-ONLY parity probe of the MCP read surface.
INVARIANTS:
       1. READ-ONLY. This suite ONLY calls read tools. It never constructs
          a trade_* call and asserts the adapter's write methods still fail
          closed even against a live endpoint.
       2. NEVER FAILS BECAUSE A BOX HAS NO TERMINAL. Without credentials, or
          with an unreachable endpoint, every test SKIPS with a named reason
          (a dev box without a running terminal bridge is the expected CI
          state, not a regression).
       3. NO CREDENTIALS IN OUTPUT. The URL is printed in failure messages
          (it is a loopback address); the key never is.
EXTEND: a new read tool gets a probe here. A write probe is forbidden.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.adapters.mt5.mcp_adapter import FORBIDDEN_TOOLS, MCPMT5Adapter

SYMBOL = "XAUUSD"
REPO_ROOT = Path(__file__).resolve().parents[2]

_SKIP_NO_CREDS = "NSE_MT5_MCP_URL/NSE_MT5_MCP_API_KEY not set and repo .env absent"
_SKIP_NO_ENDPOINT = "MT5 MCP endpoint is unreachable (no terminal bridge running)"


def _credentials_present() -> bool:
    """Credentials resolve from env now, or from the repo .env on adapter build.

    The adapter loads .env itself, so this check mirrors that resolution to
    decide skip-vs-run WITHOUT duplicating the key anywhere.
    """
    if os.environ.get("NSE_MT5_MCP_API_KEY"):
        return True
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("NSE_MT5_MCP_API_KEY=") and len(line.split("=", 1)[1]) > 0:
                return True
    return False


def _endpoint_reachable() -> bool:
    """A single initialize handshake tells us the bridge is live."""
    try:
        MCPMT5Adapter().connect()
        return True
    except Exception:
        return False


#: Compose the two conditions ONCE so the skip reason names the real cause
#: and a box without credentials does not also report an unreachable host.
_HAS_CREDS = _credentials_present()
_HAS_ENDPOINT = _endpoint_reachable() if _HAS_CREDS else False

pytestmark = pytest.mark.skipif(
    not (_HAS_CREDS and _HAS_ENDPOINT),
    reason=_SKIP_NO_CREDS if not _HAS_CREDS else _SKIP_NO_ENDPOINT,
)


@pytest.fixture(scope="module")
def adapter() -> MCPMT5Adapter:
    """A connected read-only adapter for the whole module."""
    instance = MCPMT5Adapter()
    instance.connect()
    yield instance  # type: ignore[misc]
    instance.disconnect()


# ===========================================================================
# Connection: the capability this lane added
# ===========================================================================
class TestLiveConnection:
    def test_connects_and_reports_available(self, adapter: MCPMT5Adapter) -> None:
        assert adapter.is_connected() is True
        #: A-14: the honest gate only this adapter provides.
        assert adapter.available is True

    def test_connection_state_is_connected(self, adapter: MCPMT5Adapter) -> None:
        assert adapter.connection_state().state == "CONNECTED"

    def test_terminal_state_names_the_transport(self, adapter: MCPMT5Adapter) -> None:
        state = adapter.get_terminal_state()
        assert state["transport"] == "MCP"
        assert state["available"] is True
        assert state.get("trade_server_last_known_time") is not None


# ===========================================================================
# Account + symbol (parity surface verified by lane A)
# ===========================================================================
class TestLiveAccount:
    def test_account_snapshot_is_available(self, adapter: MCPMT5Adapter) -> None:
        snap = adapter.get_account_snapshot()
        assert snap.available is True
        assert snap.login is not None
        assert snap.server is not None
        assert snap.balance is not None
        assert snap.equity is not None
        assert snap.currency is not None

    def test_account_info_legacy_contract(self, adapter: MCPMT5Adapter) -> None:
        info = adapter.get_account_info()
        assert info.login > 0
        assert info.equity >= 0.0

    def test_symbol_snapshot_spec(self, adapter: MCPMT5Adapter) -> None:
        snap = adapter.get_symbol_snapshot(SYMBOL)
        assert snap.available is True
        assert snap.spec.get("digits") is not None
        assert snap.spec.get("trade_tick_size") is not None
        assert snap.spec.get("volume_min") is not None

    def test_symbol_snapshot_live_quote(self, adapter: MCPMT5Adapter) -> None:
        snap = adapter.get_symbol_snapshot(SYMBOL)
        assert "bid" in snap.tick
        assert "ask" in snap.tick
        assert snap.spread_points is not None
        assert snap.spread_points >= 0

    def test_symbol_info_legacy_contract(self, adapter: MCPMT5Adapter) -> None:
        info = adapter.get_symbol_info(SYMBOL)
        assert info.symbol == SYMBOL
        assert info.digits >= 0
        assert info.point > 0.0

    def test_broker_tick_and_last_tick_agree(self, adapter: MCPMT5Adapter) -> None:
        broker = adapter.get_broker_tick(SYMBOL)
        assert broker.available is True
        last = adapter.get_last_tick(SYMBOL)
        assert broker.bid == pytest.approx(last.bid)
        assert broker.ask == pytest.approx(last.ask)


# ===========================================================================
# History: the range-only MCP surface (A-11) + empty-window contract (B-14m)
# ===========================================================================
class TestLiveHistory:
    def test_recent_bars_resolve(self, adapter: MCPMT5Adapter) -> None:
        bars = adapter.get_rate_history(SYMBOL, "M1", count=10)
        assert len(bars) > 0
        first = bars[0]
        assert first.open is not None
        assert first.time_utc is not None
        #: MCP bar timestamps parse as UTC with zero shift (evidence §1).
        assert first.time_utc.tzinfo is not None
        assert first.real_volume is None  # documented EXPECTED DIFFERENCE

    def test_historical_bars_typed(self, adapter: MCPMT5Adapter) -> None:
        bars = adapter.get_historical_bars(SYMBOL, "M1", count=10)
        assert len(bars) > 0
        assert all(b.symbol == SYMBOL for b in bars)
        assert all(b.timeframe == "M1" for b in bars)

    def test_recent_ticks_resolve(self, adapter: MCPMT5Adapter) -> None:
        from_dt = datetime.now(UTC) - timedelta(minutes=30)
        ticks = adapter.get_tick_history(SYMBOL, count=50, from_utc=from_dt)
        #: An empty result is legitimate (B-14m): a thin session yields no
        #: ticks. Non-empty asserts the ms-precision shape (evidence §2).
        if ticks:
            assert ticks[0].bid > 0.0
            assert ticks[0].time_msc is not None
            assert ticks[0].flags is None  # documented EXPECTED DIFFERENCE

    def test_future_datetime_to_is_clamped_not_errored(self, adapter: MCPMT5Adapter) -> None:
        #: A-11: MCP itself would answer "invalid datetime range" for a
        #: future boundary; the adapter clamps and still serves.
        from_dt = datetime.now(UTC) - timedelta(hours=1)
        ticks = adapter.get_tick_history(
            SYMBOL, count=10, from_utc=from_dt, to_utc=datetime.now(UTC) + timedelta(days=1)
        )
        assert isinstance(ticks, list)


# ===========================================================================
# Positions / history (read-only; may legitimately be empty)
# ===========================================================================
class TestLivePositions:
    def test_open_positions_is_a_list(self, adapter: MCPMT5Adapter) -> None:
        positions = adapter.get_all_positions()
        assert isinstance(positions, list)

    def test_pending_orders_is_a_list(self, adapter: MCPMT5Adapter) -> None:
        orders = adapter.get_pending_orders_snapshot()
        assert isinstance(orders, list)

    def test_positions_legacy_contract(self, adapter: MCPMT5Adapter) -> None:
        positions = adapter.get_positions()
        assert isinstance(positions, list)
        #: The bot-symbol filter holds for anything returned.
        assert all(p.symbol == "XAUUSD" for p in positions)

    def test_history_orders_window(self, adapter: MCPMT5Adapter) -> None:
        from_dt = datetime.now(UTC) - timedelta(days=30)
        orders = adapter.get_history_orders(from_utc=from_dt)
        assert isinstance(orders, list)
        if orders:
            assert orders[0].ticket is not None

    def test_closed_deals_history(self, adapter: MCPMT5Adapter) -> None:
        rows = adapter.get_closed_deals_history(SYMBOL, hours_back=24 * 30)
        assert isinstance(rows, list)
        for row in rows:
            assert row["source"] == "MCP"


# ===========================================================================
# READ-ONLY against a LIVE endpoint: the decisive test
# ===========================================================================
class TestLiveReadOnly:
    @pytest.mark.parametrize(
        "method, args",
        [
            ("send_order", (None,)),
            ("execute_market_order", (SYMBOL, 0, 0.01, 1.0, 0.9, 1.1)),
            ("place_pending_order", (SYMBOL, 0, 0.01, 1.0, 0.9, 1.1)),
            ("modify_position", (1, 0.9, 1.1)),
            ("close_position", (1,)),
            ("modify_order", (1, 0.9, 1.1)),
            ("cancel_pending_order", (1,)),
        ],
    )
    def test_write_methods_fail_closed_live(
        self, adapter: MCPMT5Adapter, method: str, args: tuple
    ) -> None:
        #: Against a REAL terminal: the mutation must still be refused and
        #: no MCP call issued. This is the contract the whole wave rests on.
        with pytest.raises((NotImplementedError, RuntimeError), match="READ-ONLY"):
            getattr(adapter, method)(*args)

    @pytest.mark.parametrize("tool", sorted(FORBIDDEN_TOOLS))
    def test_no_forbidden_tool_is_dispatched(self, adapter: MCPMT5Adapter, tool: str) -> None:
        with pytest.raises(RuntimeError, match="REFUSED_BY_POLICY"):
            adapter._transport.call_raw(tool, {})  # type: ignore[attr-defined]

    def test_read_only_flag_in_config(self) -> None:
        from nexus_scalp.configuration.config import AppConfig

        cfg = AppConfig.load_from_yaml(REPO_ROOT / "configs" / "base.yaml")
        assert cfg.mt5.mcp is not None
        assert cfg.mt5.mcp.read_only is True
