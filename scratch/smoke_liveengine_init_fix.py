"""Smoke: LiveEngine.__init__ now assigns order_manager + critical attributes.

Mimics NexusTradingForexBot.py construction path with a stub adapter (no MT5,
no web). If __init__ completes and order_manager/_rolling_feature_records/
trainer exist, the init-order corruption is fixed.
"""
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig


class StubAdapter:
    def __init__(self):
        self._connected = True

    def connect(self): return True
    def disconnect(self): pass
    def is_connected(self): return True
    def get_account_info(self): raise RuntimeError("stub-no-broker")
    def get_symbol_info(self, symbol): raise RuntimeError("stub-no-broker")
    def get_last_tick(self, symbol): raise RuntimeError("stub-no-broker")
    def get_historical_bars(self, *a, **k): return []
    def get_account_snapshot(self): return None
    def get_pending_orders(self, *a, **k): return []
    def get_positions(self, *a, **k): return []
    def get_broker_tick(self, *a, **k): return None
    def connection_state(self):
        from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState
        return MT5ConnectionState()


def main() -> int:
    cfg = AppConfig(
        execution={"symbol": "XAUUSD", "mode": "PAPER", "timeframe": "M1"},
        risk={"max_account_drawdown_pct": 10.0, "risk_per_trade_pct": 1.0},
        model={"confidence_threshold": 0.35},
        telegram={"enabled": False},
    )
    eng = LiveEngine(config=cfg, adapter=StubAdapter())
    checks = {
        "order_manager": getattr(eng, "order_manager", None) is not None,
        "trainer": getattr(eng, "trainer", None) is not None,
        "_rolling_feature_records": getattr(eng, "_rolling_feature_records", None) is not None,
        "strategy_factory": getattr(eng, "strategy_factory", None) is not None,
        "champion_manager": getattr(eng, "champion_manager", None) is not None,
        "risk_engine": getattr(eng, "risk_engine", None) is not None,
        "signal_policy": getattr(eng, "signal_policy", None) is not None,
    }
    failed = [k for k, v in checks.items() if not v]
    print("CONSTRUCTION_SMOKE:", "PASS" if not failed else f"FAIL missing={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())