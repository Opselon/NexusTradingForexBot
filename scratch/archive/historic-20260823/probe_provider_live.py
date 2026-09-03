"""scratch probe: full LLMGenerationProvider against the live endpoint."""
import sys, json, os
sys.path.insert(0, 'src')
from nexus_scalp.settings import load_settings_service
from nexus_scalp.strategies.factory.provider import LLMGenerationProvider

svc = load_settings_service()
cfg = svc.get_factory_llm_config()
print('cfg from settings:', {k: (v[:8]+'...' if k=='api_key' and v else v) for k,v in cfg.items()})
provider = LLMGenerationProvider(
    api_base_url=cfg['api_base_url'],
    model=cfg['model'],
    api_key=cfg['api_key'],
    temperature=cfg['temperature'],
    secret_store=svc.secrets,
)
print('available:', provider.available())
ctx = {
    'feature_ids': ['norm_rsi','norm_atr_ratio','upper_wick_ratio','lower_wick_ratio','body_to_range_ratio','is_doji','pinbar_sig','engulfing_sig','close_location_value','consecutive_momentum_count'],
    'timeframes': ['M1','M5','M15','M30','H1','H4','D1'],
    'symbols': ['XAUUSD'],
    'max_conditions': 9,
    'max_features': 6,
    'max_timeframes': 2,
    'generation_objective': 'Produce diverse, robust, causally-clean strategy hypotheses.',
}
sys, user = provider._build_messages(ctx, 3)
print('---SYSTEM (first 500)---')
print(sys[:500])
print('---USER---')
print(user[:600])
print('---CALL---')
dsls = provider.generate_dsls(ctx, 3)
print('returned', len(dsls), 'raw DSLs')
for d in dsls[:3]:
    print(json.dumps(d, indent=1)[:800])
    print('---')
print('usage:', provider.usage.snapshot())
svc.close()