"""Full E2E production repro: engine + real web route save (empty key) + hot-rebuild + provider stays available."""
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
    from nexus_scalp.settings import load_settings_service
    svc = load_settings_service()
    # Ensure a real key exists for the test
    before_key = svc.secrets.get_secret("factory.llm_api_key")
    print("1. stored key before:", (before_key[:6] + "...") if before_key else "(none)")

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

    # 2. THE EXACT UI SAVE: api_key="" (empty field) + meta fields
    r = client.post("/api/factory/llm-config", json={
        "base_url": "http://178.105.20.69:20128/v1",
        "api_key": "",
        "model": "claude-opus-5",
        "temperature": 0.7,
        "request_timeout_sec": 300,
        "max_requests_per_generation": 60,
    })
    print("2. UI save (empty key):", r.status_code, str(r.json())[:120])
    after_key = svc.secrets.get_secret("factory.llm_api_key")
    print("3. stored key after save:", (after_key[:6] + "...") if after_key else "(DELETED!)",
          "| PRESERVED:", after_key == before_key and after_key is not None)

    # 4. provider hot-rebuild + availability via the factory
    r2 = client.get("/api/factory/llm-config")
    j = r2.json()
    prov = (j.get("provider") or {})
    print("4. provider available (via engine factory):", prov.get("available"),
          "| model:", prov.get("model"), "| prompt:", prov.get("prompt_version"))

    # 5. live generation through the real engine factory provider
    from nexus_scalp.strategies.factory.provider import LLMGenerationProvider
    import time
    pv = eng.strategy_factory.provider
    ctx = {
        "feature_ids": ["norm_rsi", "lag_1_atr_ratio", "upper_wick_ratio", "volume_zscore"],
        "timeframes": ["M1"], "symbols": ["XAUUSD"],
        "max_conditions": 9, "max_features": 6, "max_timeframes": 1,
        "generation_objective": "One robust causally-clean strategy.",
    }
    t0 = time.time()
    dsls = pv.generate_dsls(ctx, 1)
    print(f"5. LIVE GENERATION via engine provider: {len(dsls)} dsls in {time.time()-t0:.1f}s")
    for d in dsls:
        print("   family:", d.get("family"), "| entry:", str(d.get("entry"))[:60])
    ok = (after_key == before_key and after_key is not None
          and prov.get("available") and dsls)
    print("E2E_RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(3)