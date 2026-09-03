"""Repro the /api/factory/llm-config POST error exactly as the UI hits it."""
import sys
import traceback

from fastapi.testclient import TestClient

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")

from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.application.live_engine import LiveEngine


class StubAdapter:
    def connect(self): return True
    def disconnect(self): pass
    def is_connected(self): return True
    def get_account_info(self): raise RuntimeError("stub")
    def get_symbol_info(self, s): raise RuntimeError("stub")
    def get_last_tick(self, s): raise RuntimeError("stub")
    def get_historical_bars(self, *a, **k): return []
    def get_account_snapshot(self): return None
    def get_pending_orders(self, *a, **k): return []
    def get_positions(self, *a, **k): return []
    def get_broker_tick(self, *a, **k): return None
    def connection_state(self):
        from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState
        return MT5ConnectionState()


def main() -> None:
    cfg = AppConfig(
        execution={"symbol": "XAUUSD", "mode": "PAPER", "timeframe": "M1"},
        risk={"max_account_drawdown_pct": 10.0, "risk_per_trade_pct": 1.0},
        model={"confidence_threshold": 0.35},
        telegram={"enabled": False},
    )
    eng = LiveEngine(config=cfg, adapter=StubAdapter())
    from nexus_scalp.web.server import create_app
    app = create_app(engine_ref=eng)
    eng.server_state = app.state.server_state
    client = TestClient(app)
    payload = {
        "base_url": "http://178.105.20.69:20128/v1",
        "api_key": "",
        "model": "claude-opus-5",
        "temperature": 0.7,
        "request_timeout_sec": 300,
        "max_requests_per_generation": 60,
    }
    # GET first (status)
    r = client.get("/api/factory/llm-config")
    print("GET status:", r.status_code, str(r.json())[:200])
    # POST save (key blank — keep existing)
    r = client.post("/api/factory/llm-config", json=payload)
    print("POST status:", r.status_code)
    print("POST body:", str(r.json())[:300])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()