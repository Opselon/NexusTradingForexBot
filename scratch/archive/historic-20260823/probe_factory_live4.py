"""Live E2E with the REAL stored key: confirm auth OK + model responds."""
import sys, time
from nexus_scalp.settings import load_settings_service
from nexus_scalp.strategies.factory.provider import LLMGenerationProvider

svc = load_settings_service()
cfg = svc.get_factory_llm_config()
prov = LLMGenerationProvider(
    api_base_url=cfg["api_base_url"],
    model=cfg["model"],
    api_key=cfg["api_key"],
    temperature=0.4,
    request_timeout_sec=420.0,
    max_requests_per_generation=5,
    secret_store=svc.secrets,
)
print("available:", prov.available(), "| model:", prov.model, "| timeout:", prov.request_timeout_sec)
ctx = {
    "feature_ids": ["norm_rsi", "lag_1_atr_ratio", "upper_wick_ratio", "volume_zscore"],
    "timeframes": ["M1"], "symbols": ["XAUUSD"],
    "max_conditions": 9, "max_features": 6, "max_timeframes": 1,
    "generation_objective": "One diverse, robust, causally-clean strategy.",
}
t0 = time.time()
dsls = prov.generate_dsls(ctx, 1)
print(f"LIVE: {len(dsls)} dsls in {time.time()-t0:.1f}s")
for d in dsls:
    print("  family:", d.get("family"), "| setup:", str(d.get("setup"))[:90])
u = prov.usage.snapshot()
print("usage:", {k: u[k] for k in ("requests", "failures", "last_error", "total_tokens")})
sys.exit(0 if dsls else 2)
