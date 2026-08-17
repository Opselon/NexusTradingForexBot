"""Repro: does CURRENT manage_active_positions ever return None when provider returns None?"""

import sys
from pathlib import Path

src = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(src))

# Engine-style import order first (resolves the package __init__ chain):
from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter  # noqa: E402,F401
from nexus_scalp.domain.models import TickData  # noqa: E402
from nexus_scalp.execution.order_manager import OrderLifecycleManager  # noqa: E402


class NullPositionsAdapter:
    """Adapter whose get_positions() violates the contract by returning None."""

    def get_positions(self, symbol=None):
        return None

    def get_pending_orders(self, symbol=None):
        return []

    def get_closed_deals_history(self, symbol, hours_back=24):
        return []

    def cancel_pending_order(self, ticket):
        return True

    def close_position(self, ticket, volume=None):
        return True

    def modify_position(self, ticket, stop_loss, take_profit):
        return True


class FakeAudit:
    def log_order(self, *a, **k):
        pass

    def log_ledger_opened(self, *a, **k):
        pass

    def log_ledger_closed(self, *a, **k):
        pass

    def has_ledger_opened(self, ticket):
        return False


def main():
    om = OrderLifecycleManager.__new__(OrderLifecycleManager)
    om.adapter = NullPositionsAdapter()
    om.audit = FakeAudit()
    om.notifier = None
    om.rule_matrix = None
    om.algo_config = None
    om.risk_engine = None
    om.experience_engine = None
    om._reconcile_seen = {}
    om._entry_timestamps = {}
    om._entry_prices = {}
    om._entry_tps = {}
    om._entry_sls = {}
    om._entry_atr = {}
    om._entry_spread = {}
    om._entry_order_ids = {}
    om._entry_reasons = {}
    om._entry_confidences = {}
    om._entry_regimes = {}
    om._last_known_volume = {}
    om._entry_directions = {}
    om._entry_fill_latency_ms = {}
    om._entry_risks = {}
    om._initial_risks = {}
    om._mfe_tracker = {}
    om._mae_tracker = {}
    om._peak_profit_usd = {}
    om._peak_drawdown_usd = {}
    om._time_in_profit_sec = {}
    om._time_in_drawdown_sec = {}
    om._last_tick_timestamps = {}
    om._last_modify_sl = {}
    om._last_mod_price = {}
    om._last_mod_time = {}
    om._sl_modified_flags = {}
    om._forced_exit_mechanisms = {}
    om._partial_closed_tickets = {}
    om._recovery_mode = {}
    om._recovery_start_time = {}
    om._recovery_budget = {}
    om._recovery_horizon = {}
    om._order_id_to_message_id = {}
    om._order_message_ids = {}
    om._processed_orders = {}
    om._consecutive_failures = 0
    om.global_state = "NORMAL"
    om._live_tickets_lock = __import__("threading").RLock()
    om._live_tickets_cache = {}
    om._pending_orders_setup_time = {}
    om._hold_score_tracker = {}
    om._lsf_state = {}
    om._lsf_lock = __import__("threading").RLock()
    om._rolling_spreads = []
    om._last_account_balance = 0.0
    om._last_account_equity = 0.0
    om._pending_entry_context = None
    om._last_tick_for_ticket = {}
    om._entry_timestamps_extra = {}
    om._last_trajectory = {}
    om._last_decision_at = {}
    om._last_giveback_at = {}
    om._last_hold_state = {}
    om._last_sl_apply = {}
    om._last_breakeven_check = {}
    om._trajectory = {}
    om._giveback_severity = {}
    om._recovery_locked = {}
    om._msg_throttle = {}
    om._falling_knife_state = {}
    om._admin_disabled = False
    om._console_telemetry_last = {}
    om._synthetic_unrealized = {}
    om._ref_protection = {}
    om._protection_state_cache = {}
    om._pending_lock = {}

    from datetime import UTC, datetime

    tick = TickData(
        symbol="XAUUSD",
        timestamp=datetime.now(UTC),
        bid=4395.04,
        ask=4395.29,
        last=0.0,
        volume=0.0,
        flags=1030,
    )

    result = om.manage_active_positions(symbol="XAUUSD", current_tick=tick, account=None)
    print("RETURN TYPE:", type(result).__name__)
    print("RETURN VALUE:", result)
    assert result == [], (
        "manage_active_positions must return [] when the position provider returns None"
    )
    print(
        "PASS: current code converts provider-None to empty collection at the order-manager boundary"
    )


if __name__ == "__main__":
    main()
