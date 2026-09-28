# ACCOUNT_RUNTIME_FORENSIC_MATRIX — Phase 14

**Date:** 2026-08-17
**Real MT5 evidence:** MetaQuotes-Demo terminal, account **10011755849**, live XAUUSD tick 4390.51/4390.76 (2026-08-17 06:19 UTC), 250 real M1 bars via `copy_rates_from_pos`, 48 history orders / 42 deals in 1 day, broker-native `order_calc_profit`=1.0 / `order_calc_margin`=43.91 (0.01 lot @ ~4390).

**Legend:** source = producer; freshness = age at snapshot; failure point = exact layer where the value disappears when missing.

| UI FIELD | API FIELD | INTERNAL FIELD | PRODUCER | MT5 API | UPDATE WORKER | CACHE | TIMESTAMP | PROVENANCE | CURRENT VALUE (real smoke) | FAILURE POINT | ROOT CAUSE | FIX | TEST |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| SYMBOL | `symbol` | `config.execution.symbol` | LiveEngine | symbol_info | run_loop | — | snapshot | CONFIG/ENGINE_STATE | XAUUSD | config load | none | — | test_mt5_status_endpoint |
| BID | `bid` | `BrokerTickSnapshot.bid` | DirectMT5Adapter | symbol_info_tick | run_loop (20Hz) + 5s snapshot refresh | `_last_tick` | tick.time (UTC) | LIVE_MT5 | 4390.51 | `get_system_state` tick read | previously `engine._last_tick` only; now broker tick preferred | `get_broker_tick()` first, engine fallback | test_mt5_status_endpoint::test_live_tick_present |
| ASK | `ask` | `BrokerTickSnapshot.ask` | DirectMT5Adapter | symbol_info_tick | run_loop | `_last_tick` | tick.time | LIVE_MT5 | 4390.76 | same | same | same | same |
| SPREAD | `spread` | `(ask-bid)*100` | server.py | symbol_info_tick | snapshot build | — | snapshot_timestamp | LIVE_MT5 | 25.0 pts | rounding | none | — | test_live_state_contract |
| LAST | `last` | `BrokerTickSnapshot.last` | DirectMT5Adapter | symbol_info_tick | run_loop | `_last_tick` | tick.time | LIVE_MT5 | 4390.51 (=bid) | tick read | none | — | providers tests |
| ATR | `atr` | `MarketRegimeState.realized_volatility_5m` / `fv.atr_m1` | RegimeClassifier / ScalpFeatureEngine | bars + tick | _process_tick_pipeline | `_last_regime_state` | per tick | ENGINE_STATE | None until warmup READY | warmup gate | HTF_WARMUP_INCOMPLETE blocks inference chain (by design, BUG-004) | warmup gate persists | test_htf_warmup_gate |
| REGIME | `regime` | `MarketRegimeState.regime_type` | MarketRegimeClassifier | ticks | pipeline | `_last_regime_state` | per tick | ENGINE_STATE | None until first classification | classifier | no ticks yet | — | test_live_state_contract |
| REGIME STRENGTH | `regime_conf` | regime classifier conf | MarketRegimeClassifier | ticks | pipeline | `_last_regime_state` | per tick | ENGINE_STATE | — | classifier | only after first tick | — | — |
| MODEL ID | `model_id` | `champion_manager.model_id` | champion_manager registry | — | construction | `_bundle` | snapshot | MODEL_REGISTRY | primary_scalp_scalp_v1_50d | model_meta build | champ registry empty on fresh DB | reads champ + bundle | test_live_state_contract |
| MODEL VERSION | `model_version` | champ.model_version | champion_manager | — | construction | `_bundle` | snapshot | MODEL_REGISTRY | v1.0 | same | same | same | same |
| ARCHITECTURE | `architecture` | bundle.model class | LiveEngine bundle | — | load | `_bundle` | snapshot | MODEL_INFERENCE | ScalpNet | model_meta build | hardcoded label (legit) | — | — |
| ARTIFACT PATH | `artifact_path` | bundle.artifact_path | LiveEngine | — | load | `_bundle` | snapshot | MODEL_INFERENCE | artifacts/models/.../model.pt | model_meta build | none | — | — |
| SCALER | `scaler_ready` | bundle.scaler.is_ready() | ScalerBundle | — | load | `_bundle` | snapshot | ENGINE_STATE | READY (cold-start fallback persisted per BUG-015) | model_meta | missing npz on first boot | fallback scaler persist | test_log_autopsy_fixes |
| FEATURE SCHEMA | `feature_schema_id` | engine.FEATURE_SCHEMA_ID | features/schema.py | — | import | — | snapshot | REGISTRY | scalp_v1 | model_meta | none | — | — |
| INFERENCE TIME | `latency_ms` | `engine._last_inference_latency_ms` | LiveEngine._infer_probabilities | — | every inference | engine attr | per inference | MODEL_INFERENCE | (measured ms; None before first inference) | model_meta | field was never captured | timed inference in `_infer_probabilities` | test_live_state_contract |
| NO_TRADE % | `probs.no_trade` | `_last_probs[0]` | ScalpNet | — | pipeline | `_last_probs` | per tick | MODEL_INFERENCE | real softmax after first inference | probs build | None pre-inference | available=False until real | test_live_state_contract |
| BUY % | `probs.buy` | `_last_probs[1]` | ScalpNet | — | pipeline | `_last_probs` | per tick | MODEL_INFERENCE | same | same | same | same | same |
| SELL % | `probs.sell` | `_last_probs[2]` | ScalpNet | — | pipeline | `_last_probs` | per tick | MODEL_INFERENCE | same | same | same | same | same |
| ACCOUNT LOGIN | `account.login` | `AccountSnapshot.login` | DirectMT5Adapter | account_info | 5s snapshot refresh | `engine._account_snapshot` | captured_at | BROKER_NATIVE | 10011755849 | account block | domain AccountInfo dropped it | typed AccountSnapshot | test_mt5_providers_phase14 |
| BROKER | `account.server` | snap.server | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | MetaQuotes-Demo | same | same | same | same |
| COMPANY | `account.company` | snap.company | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | MetaQuotes Ltd. | same | same | same | same |
| CURRENCY | `account.currency` | snap.currency | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | USD | same | same | same | same |
| LEVERAGE | `account.leverage` | snap.leverage | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | 100 | same | same | same | same |
| TRADE MODE | `account.trade_mode` | snap.trade_mode | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | 0 (demo) | same | same | same | same |
| TRADE ALLOWED | `account.trade_allowed` | snap.trade_allowed | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | True | same | same | same | same |
| TRADE EXPERT | `mt5.connection.trade_expert` | `_conn_state._trade_expert` | DirectMT5Adapter | terminal_info | connect + tick | _conn_state | connect | BROKER_NATIVE | False (demo) | terminal block | never captured | conn_state.set_terminal | test_mt5_providers_phase14 |
| BALANCE | `account.balance` | snap.balance | DirectMT5Adapter | account_info | 5s | snapshot | captured_at | BROKER_NATIVE | 41003.70 | account block | legacy fallback only | typed snapshot first | test_mt5_status_endpoint |
| CREDIT | `account.credit` | snap.credit | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | 0.0 | same | same | same | same |
| EQUITY | `account.equity` | snap.equity | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | 41003.70 | same | same | same | same |
| PROFIT | `account.profit` | snap.profit | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | 0.0 fl | same | same | same | same |
| MARGIN | `account.margin` | snap.margin | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | 0.0 | same | same | same | same |
| FREE MARGIN | `account.margin_free` | snap.margin_free | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | 41003.70 | same | same | same | same |
| MARGIN LEVEL | `account.margin_level` | snap.margin_level | DirectMT5Adapter | account_info | same | same | captured_at | BROKER_NATIVE | 0.0 (no open pos → 0) | same | none | — | same |
| OPEN POSITIONS | `account.open_positions` + `positions[]` | `get_all_positions()` | DirectMT5Adapter | positions_get | snapshot refresh | engine attr | captured_at | BROKER_NATIVE | 0 | positions block | **BOT filter bug** (XAUUSD+magic) | added `get_all_positions()` unfiltered; classic `get_positions()` kept for bot management | test_mt5_status_endpoint |
| PENDING ORDERS | `account.pending_orders` + `orders[]` | `get_pending_orders_snapshot()` | DirectMT5Adapter | orders_get | snapshot refresh | engine attr | captured_at | BROKER_NATIVE | 0 | orders block | none (was missing entirely) | added provider + API | test_mt5_status_endpoint |
| HISTORICAL ORDERS | `history.orders[]` | `get_history_orders()` | DirectMT5Adapter | history_orders_get | API request (bounded) | none | from/to UTC | BROKER_NATIVE | 48 (1 day) | /api/mt5/status | endpoint missing | added | test_mt5_status_endpoint |
| HISTORICAL DEALS | `history.deals[]` | `get_history_deals()` | DirectMT5Adapter | history_deals_get | API request | none | from/to UTC | BROKER_NATIVE | 42 (1 day) | same | same | added | same |
| REALIZED PNL | `history.deals_net_result[]` | `DealSnapshot.net_result` | DirectMT5Adapter | history_deals_get | API request | none | deal time | BROKER_NATIVE | profit−|comm|−|swap|−|fee| | deal build | sign bug lineage (BUG-019) | net_result helper | test_mt5_providers_phase14::test_deal_net_result |
| FLOATING PNL | `account.floating` | `equity - balance` | server snapshot | account_info | snapshot | engine attr | captured_at | BROKER_NATIVE (derived) | 0.0 | account block | none | — | — |
| DRAWDOWN | `account.drawdown` | `(peak-equity)/peak` | LiveEngine._peak_equity | account_info | _update_survival_state | engine attr | per tick | ENGINE_STATE | 0.0 (flat) | account block | none | — | test_live_state_contract |
| MODE | `runtime_mode` | `engine._runtime_mode` | LiveEngine._update_runtime_mode | terminal_info + config | 5s | engine attr | refresh | ENGINE_STATE | SHADOW (test) / LIVE or LIVE_CONFIGURED/MT5_DISCONNECTED | **header MODE lies** | config-only display | runtime-mode badge; LIVE only when connected+allowed | test_mt5_status_endpoint::test_live_configured_but_mt5_disconnected |
| CONNECTION STATE | `mt5.connection.state` | `_conn_state` | DirectMT5Adapter | terminal_info | connect/tick | _conn_state | connect | BROKER_NATIVE | CONNECTED | /api/live/state mt5 | never exposed | added | test_mt5_status_endpoint::test_mt5_disconnect_surfaces |
| TICK STALENESS | `tick_stale` / `tick_freshness_ms` | BrokerTickSnapshot | DirectMT5Adapter | symbol_info_tick | snapshot | snap | tick.time | LIVE_MT5 | False / 0.0ms | /api/status | missing entirely | added STALE/LIVE badge | test_mt5_providers_phase14::test_stale_tick |

## Failure-isolation matrix (task §27)

| Failure | Trading continues | Chart continues | Accounting continues | UI shows |
| :--- | :--- | :--- | :--- | :--- |
| account_info fails | ✅ | ✅ | ✅ | account available=False, No fake |
| history_deals_get fails | ✅ | ✅ | live snapshot ✅ | history available=False |
| chart history fails | ✅ | ✅ (fallback ENGINE_STATE) | ✅ | source=ENGINE_STATE or error state |
| MT5 disconnect | ✅ engine survives | engine bars only | adapter live_state unavailable | runtime_mode=…DISCONNECTED, stale marked, bid=None |
| UI fails | ✅ | ✅ | ✅ | trading path untouched |

## Real-MT5 pipeline verification (2026-08-17 06:19 UTC)

```
MT5 (account_info/symbol_info_tick/copy_rates_from_pos/history_*)
  -> DirectMT5Adapter typed snapshots (BROKER_NATIVE)
  -> LiveEngine._account_snapshot / _last_tick
  -> web /api/status + /api/chart/history + /api/mt5/status + /api/live/state
  -> LiveUiState.2 JSON
  -> app.js renders (bid 4390.51 / ask 4390.76; account 10011755849; 250 bars)
```
Evidence: `_real_web_pipeline.py` run output preserved in ACCOUNT_RUNTIME_FORENSIC_REPORT.md.