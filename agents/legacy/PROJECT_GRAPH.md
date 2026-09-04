# 🧠 PROJECT GRAPH — Nexus Scalp Engine (NSE)

> **Purpose:** The complete intelligence map of the Nexus Scalp Engine trading
> system. A new AI agent reading ONLY this file must be able to orient itself,
> understand every subsystem, trace every data path, and know where any change
> lands — before reading code.
>
> **Source of truth hierarchy (highest first):**
> 1. Executable code (`src/nexus_scalp/`) — forensic truth.
> 2. `agents/skill.md` — the authoritative architecture map (2,900 lines, every
>    claim carries a forensic badge: 🟢 VERIFIED / 🟡 PARTIAL / 🔴 CONTRADICTED…).
> 3. `agents/runtime_invariants.md` (INV-001..021) — non-negotiable runtime
>    guarantees.
> 4. `agents/contracts.md` — cross-subsystem contract registry.
> 5. `agents/bugs.md` — bug ledger with root causes + regression guards.
> 6. `agents/change_control.md` (CHG-NNNN), `agents/taskboard.md` (TASK-NNN),
>    `agents/repository_state.md`, `agents/locks.yaml`, `agents/decisions/`.
> 7. Companion docs: `Agent/AGENT_REASONING_PROTOCOL.md` (how to think here),
>    `Agent/ARCHITECTURE_CONTRACT.md` (laws that must never be broken).
>
> **Operating modes:** PAPER (simulated, default) / LIVE (requires explicit
> confirmation) / SHADOW (challenger evaluation, zero order authority). The UI
> is the source of control for execution mode (BUG-119); runtime_mode is
> ALWAYS derived from real MT5 connection state — never fake LIVE.

---

## 1. System Master Graph

```text
Market Data ──► Processing ──► Feature Engineering ──► 70D Feature Vector
     ▲              │                  │                      │
     │              ▼                  ▼                      ▼
     │         Bar Aggregation    Regime Classification   AI Decision Engine
     │              │                  │                      │
     └──────────────┴──────────────────┴──────────────────────┤
                                                            ▼
                                                       Risk Engine
                                                            │
                                                            ▼
                                                     Execution Engine
                                                            │
                                                            ▼
                                                    Position Management
                                                            │
                                                            ▼
                                                         Telemetry
                                                            │
                                                            ▼
                                                       Learning Loop
```

### 1.1 Market Data
- **Sources (adapters, all implement `IMT5Port` in `ports/mt5_port.py`):**
  - `DirectMT5Adapter` (`adapters/mt5/mt5_adapter.py`) — Win32 MetaTrader 5 IPC
    (C extension). Primary live source.
  - `RemoteMT5GatewayAdapter` (`adapters/mt5/remote_gateway.py`) — ZeroMQ + JSON
    remote gateway.
  - `PaperMT5Adapter` (`adapters/paper/paper_adapter.py`) — in-memory simulation
    with realistic spread/latency. NOTE: PAPER mode still connects to real MT5
    for provider reads (packaged-EXE behavior) — the mode gates EXECUTION, not
    data access.
- **Payloads:** ticks, OHLCV rate history (`copy_rates_*`), account info,
  positions, orders, history deals, broker-native calc (`order_calc_*`).
- **Broker-aware provider layer** (`adapters/mt5/providers.py`, Phase 14):
  typed snapshots with provenance (`AccountSnapshot`, `SymbolSnapshot`,
  `BrokerTickSnapshot`, `PositionSnapshot`, `OrderSnapshot`,
  `HistoryOrderSnapshot`, `DealSnapshot`, `RateBarSnapshot`,
  `TickHistorySnapshot`, `BrokerCalcSnapshot`) — every snapshot carries
  SOURCE / TIMESTAMP / STATE VERSION / FRESHNESS / PROVENANCE / ERROR STATE.
  `run_mt5_call()` wraps every call with structured `[MT5_CALL]` diagnostics;
  failure is NEVER silent.
- **Timebase:** MT5 epochs are SERVER-LOCAL (broker GMT+3, NOT UTC — BUG-070);
  `normalize_utc()` in providers.py converts datetime/numpy/Polars/ISO/naive
  safely (BUG-044-safe).

### 1.2 Processing
- `market_data/bar_aggregator.py` — M1 bar construction from the tick stream.
- **History ingestion is REPLACE + ALIGN, never blind-append** (BUG-058):
  broker rate history INCLUDES the still-forming current minute; ingestion must
  `reseed()` — dedupe by timestamp, sort ascending, drop incomplete bars, seed
  the forming bar from the latest close.
- `application/live_engine.py::_process_tick_pipeline()` — the async hot path.
  **INV-001: zero synchronous DB on this path.** All DB writes queued;
  news/rule-matrix/experience caches are cache-only here.

### 1.3 Feature Engineering
- `features/scalp_features.py` — `ScalpFeatureEngine` computes the Base 50D
  (`FEATURE_NAMES` tuple is the executable contract). Every value finite,
  clipped `[-3, +3]` via `validate_and_fallback()`; NaN/Inf →
  `FeaturePipelineFrozenError` → deterministic fallback.
- `features/regime_classifier.py` — `MarketRegimeClassifier`, 10 regimes
  (incl. unsafe ones: HIGH_SPREAD_CHOP, MARKET_HALTED, NEWS_LOCK…).
- `features/liquidity_engine.py` — pure-causal 10D liquidity producer
  (`compute_liquidity_features`, TASK-1): pool lifecycle
  CANDIDATE → CONFIRMED, ±5-bar fractal confirmation, structural info only —
  never a trade signal. `liquidity_engine_opt.py` = versioned v1.1 candidate.
- `features/liquidity_runtime.py` — `LiquidityGovernor` (TASK-2): runtime
  snapshot, ENABLED/DISABLED/DEGRADED/UNAVAILABLE status, causal
  VALID/STALE/INVALID, `build_70d_vector` (strict — never pad/truncate).
- `features/schema.py`, `features/schema_contract.py` — the feature schema
  REGISTRY; `schema_contract` is the single source of truth for 70D geometry.
  `features/schema_augment.py` — 10 causal 60D extras (candidate-only).

### 1.4 70D Feature Vector (canonical tensor contract — scalp_v3)
Single market snapshot → ONE canonical 70D vector with identical semantics in
dataset, replay, training, inference, and live (INV-020):

| Block | Indices | Family | Source |
|---|---|---|---|
| Base | 0..49 | `base` | `scalp_features.FEATURE_NAMES` (scalp_v1 50D, protected) |
| News | 50..59 | `news` | `news_context_v1` fields 0..8 + `news_state` (idx 10) — NOT a blind first-10 slice; idx 9 (`source_consensus`) stays out |
| Liquidity | 60..69 | `liquidity` | `liquidity_engine.LIQUIDITY_FEATURE_NAMES` (identical order to `LiquidityFeatures.as_vector()`) |

- `canonical_feature_names()` RAISES at import/test time if any upstream
  contract drifts — never silently at inference.
- Liquidity 10D exact order: 60 bsl_distance_atr · 61 ssl_distance_atr ·
  62 eqh_strength · 63 eql_strength · 64 htf_liquidity_score ·
  65 internal_liquidity_distance · 66 external_liquidity_distance ·
  67 liquidity_confluence · 68 liquidity_sweep_state · 69 post_sweep_displacement.
- `scalp_v2` = 60D is FROZEN (candidate-only, liquidity at 50..59 — OLD layout,
  superseded by scalp_v3). The 350D forward-declaration is OBSOLETE — no
  artifact ever existed (TEST-29).
- Active serving schema is still **scalp_v1 (50D, 4-logit legacy baseline)**;
  70D is the canonical RESEARCH/dataset schema. Never pad/truncate/substitute
  a vector (INV-009, INV-020).


### 1.4b 70D Master Feature Vector — full per-dimension specification

Every dimension documented with: Feature ID, Name, Category, Purpose, Input
Source, Calculation Logic, Trading Meaning, Impact on Decision, Failure Risk.

**Base block 0..49** (canonical `FEATURE_NAMES`, symbol XAUUSD M1):

| Idx | Name | Category | Logic (verified) | Meaning | Failure risk |
| :---: | :--- | :--- | :--- | :--- | :--- |
| 0 | upper_wick_ratio | Candle | (High − max(O,C))/range, range=max(H−L,0.01), [0,1]→clip | seller rejection | extreme wicks |
| 1 | lower_wick_ratio | Candle | (min(O,C) − Low)/range | buyer rejection | low |
| 2 | body_to_range_ratio | Candle | \|O−C\|/range | directional strength | low |
| 3 | is_doji | Candle | 1.0 if body_ratio ≤ 0.12 | indecision | low |
| 4 | pinbar_sig | Candle | hammer min(2,lw*2) / star max(−2,−uw*2) | reversal bias | false pins |
| 5 | engulfing_sig | Candle | bull min(2,1+body_ratio) / bear −(1+body_ratio) | impulse bias | low |
| 6 | close_location_value | Candle | ((C−L)−(H−C))/range ∈ [−1,1] | close sentiment | low |
| 7 | consecutive_momentum_count | Momentum | clip((count*dir)/5,−1,1) over 10 bars | sustained push | trend exhaustion |
| 8 | norm_displacement | Momentum | (mid − last_close)/max(ATR14,0.20) | impulse/extension | low |
| 9 | rapid_reversal_spike_val | Momentum | 1 if \|disp\|>0.6ATR and disp*logret<0 | trapped move | low |
| 10 | dist_to_swing_high_20 | Structure | (max(H[-20:-1]) − mid)/ATR | resistance proximity | stale swing |
| 11 | dist_to_swing_low_20 | Structure | (mid − min(L[-20:-1]))/ATR | support proximity | stale swing |
| 12 | price_compression_flag_ratio | Structure | clip(range5/range20, 0, 2) | squeeze | low |
| 13 | extreme_sig | Structure | +1 if range_pos≥0.95, −1 if ≤0.05 (50-bar) | exhaustion | trend extremes |
| 14 | stop_hunt_depth | Liquidity | penetration depth / ATR | engineered hunt | low |
| 15 | liquidity_sweep_signal | Liquidity | +1 low reclaim / −1 high reject | fakeout confirm | low |
| 16 | session_tokyo | Session | 0≤UTC h<8 | session context | tz (BUG-070) |
| 17 | session_london | Session | 7≤h<15 | session context | tz |
| 18 | session_ny | Session | 13≤h<21 | session context | tz |
| 19 | session_overlap_london_ny | Session | 13≤h<15 | overlap | tz |
| 20-22 | lag_1/2/3_log_return | Return | ln(C[-n-1]/C[-n-2])*100, clip | return memory | low |
| 23 | lag_1_atr_ratio | Volatility | TR_lag1/ATR | vol change | low |
| 24 | lag_1_volume_z | Volume | (V[-2]−mean(V[-21:-1]))/std | volume impulse | tick-volume |
| 25 | lag_1_clv | Volume | CLV of prior bar ∈ [−1,1] | absorption | low |
| 26 | fvg_sig | SMC/FVG | (L[-1]−H[-3])/ATR bull, −(L[-3]−H[-1])/ATR bear, 0.20ATR gate | imbalance magnet | gap fill fast |
| 27 | order_block_type | SMC/OB | +1/−1/0 × vol-strength | institutional zone | OB mislabel |
| 28 | choch_sig | SMC | +1/−1 CHoCH (EMA20/50 + 20-bar swing) | structure shift | range noise |
| 29 | breakout_sig | Price action | +1 mid>H[-1], −1 mid<L[-1] | break momentum | false break |
| 30 | norm_tk_diff | Ichimoku | (Tenkan−Kijun)/ATR | TK spread | low |
| 31 | tk_cross_signal | Ichimoku | +1/−1 cross | swing bias | whipsaw |
| 32 | kumo_sig | Ichimoku | +1 above / −1 below cloud | trend filter | low |
| 33 | norm_kumo_width | Ichimoku | (SpanA−SpanB)/ATR | cloud thickness | low |
| 34 | norm_rsi | Oscillator | (RSI14−50)/16.66 — divisor 16.66 in CODE (BUG-082) | OB/OS | RSI extremes |
| 35 | dist_to_ema_21 | Trend | (mid−EMA21)/ATR, EMA seed=first, alpha=2/(n+1) | pullback | low |
| 36 | dist_to_ema_50 | Trend | (mid−EMA50)/ATR | macro pullback | low |
| 37 | cross_asset_z_score | Cross-asset | rolling 20-bar z with current tick | deviation | correlation shift |
| 38 | norm_dist_to_tenkan | Ichimoku | (Tenkan−Kijun)/(2*ATR) — exact negation of 39 | Tenkan dist | low |
| 39 | norm_dist_to_kijun | Ichimoku | (Kijun−Tenkan)/(2*ATR) | Kijun dist | −1.0 corr w/ 38 |
| 40 | htf_h4_trend | HTF | EMA3 of H4 closes +1/−1 | H4 filter | warmup |
| 41 | htf_h1_momentum | HTF | (H1_close[-1]−H1_close[-2])/ATR | H1 momentum | warmup |
| 42 | htf_m30_structure | HTF | EMA5 of M30 closes +1/−1 | M30 structure | warmup |
| 43 | htf_m15_confirmation | HTF | engulfing + close-vs-open on last 2 M15 | M15 confirm | warmup |
| 44 | support_zone_dist | S/R | (mid−nearest_support)/ATR, fractal win 3, 50 bars | support prox | low |
| 45 | resistance_zone_dist | S/R | (nearest_resistance−mid)/ATR | resistance prox | low |
| 46 | feat_ob_valid_bos | SMC | 1.0 OB BOS, 0.5 CHoCH/break | OB validity | low |
| 47 | feat_ob_equilibrium_ratio | SMC | (ob_price−last_sl)/(last_sh−last_sl), clip [0,1] | OB equilibrium | low |
| 48 | feat_ob_liquidity_swept | SMC | 1.0 sweep confirmed | OB swept | low |
| 49 | feat_ob_fib_50_60_alignment | SMC | clip(1−\|eq_ratio−0.55\|/0.35, 0, 1) | fib band | low |

Verified forensic facts: all 50 dims passed 7 fixtures × 50 = 350/350, determinism
×100 PASS, causality T-1 PASS, dataset/live replay parity PASS, float32
model-input roundtrip err ≤ 8.6e-8. `norm_rsi` divisor is 16.66 (not 25 — the
historical docs table was wrong, BUG-082). feat_38/feat_39 are exact negations
(corr −1.0 over 215 stored experiences). NO MACD/BB/ADX/OBV/VWAP exist in the
50D — the historical docs claimed them; the executable FEATURE_NAMES is truth.

**News block 50..59** (`news_context_v1` fields 0..8 + news_state idx 10):

| Idx | Name | Category | Logic | Trading meaning | Failure risk |
| :---: | :--- | :--- | :--- | :--- | :--- |
| 50 | active_high_impact_events | News state | count of live high-impact events | event pressure | stale cache → 0.0 |
| 51 | xauusd_relevance | News relevance | keyword/entity scoring | gold catalyst | local-only fallback |
| 52 | usd_relevance | News relevance | keyword/entity scoring | USD driver | same |
| 53 | bullish_pressure | News sentiment | tier-weighted consensus | +gold | bounded ≤ +0.05 |
| 54 | bearish_pressure | News sentiment | tier-weighted consensus | −gold | bounded ≤ −0.10 |
| 55 | conflict_score | News quality | consensus variance | unreliable info | bounded |
| 56 | novelty | News quality | NEW=0…STALE=4 | fresh vs recycled | deterministic |
| 57 | freshness | News quality | decay half-lives BREAKING/MACRO/POLICY/STRUCTURAL | relevance | fixed constants |
| 58 | confidence | News quality | source trust × importance | certainty | LOCAL/API/COMBINED/FAILED |
| 59 | news_state | News state | NORMAL=0…STALE=5 | event phase | missing → 0.0 |

Causality: only events published at/before sample T enter; events postdating the
dataset → zero vector → NEWS_INCONCLUSIVE_NO_OVERLAP (never fabricated).

**Liquidity block 60..69** (canonical order — the brief's "63 eqh_strength"
example is WRONG; trust `schema_contract.LIQUIDITY_10D_NAMES`):

| Idx | Name | Category | Logic (verified from LIQUIDITY_FEATURE_DOC) | Missing default | Failure risk |
| :---: | :--- | :--- | :--- | :--- | :--- |
| 60 | bsl_distance_atr | Liquidity BSL | (L−P)/ATR, nearest CONFIRMED buy-side level above (swing highs/EQH/PDH/PWH/session/HTF) | 3.0 (far) | stale pools |
| 61 | ssl_distance_atr | Liquidity SSL | (P−L)/ATR, nearest CONFIRMED sell-side below | 3.0 | stale pools |
| 62 | eqh_strength | Liquidity EQH | cluster \|h_a−h_b\| ≤ ATR*EQH_TOLERANCE_ATR; strength=f(touch,closeness,recency,diversity), softmax [0,1] | 0.0 | low |
| 63 | eql_strength | Liquidity EQL | mirror on lows | 0.0 | low |
| 64 | htf_liquidity_score | Liquidity HTF | signed Σ(proximity×importance×confidence) over H1/H4/D1 confirmed pools; tanh→(−1,1)×3 | 0.0 | forming buckets excluded |
| 65 | internal_liquidity_distance | Liquidity range | nearest confirmed pool inside active range / ATR | 3.0 | low |
| 66 | external_liquidity_distance | Liquidity range | nearest confirmed pool outside range / ATR | 3.0 | low |
| 67 | liquidity_confluence | Liquidity confluence | cluster pools within CONFLUENCE_CUTOFF_ATR×ATR; unique-source cap (1+ln(diversity)), clip [0,3] | 0.0 | source-diversity |
| 68 | liquidity_sweep_state | Liquidity sweep | SweepState encoding over last 3 completed bars (signed {−2..+3}) | 0.0 | confirm after close |
| 69 | post_sweep_displacement | Liquidity sweep | displacement from sweep bar to 2nd close after confirm / ATR (sign=rejection) | 0.0 | bars after confirm only |

Anti-leakage: bars timestamp > decision_at invisible; ±5-bar fractal
confirmation (SWING_CONFIRM_BARS); pool lifecycle CANDIDATE→CONFIRMED→
(usable_at ≤ decision). All values finite, clipped [−3,+3] centrally.

### 1.5 AI Decision Engine
- `models/scalp_net.py` — `ScalpNet` dual-path PyTorch net: 2D MLP snapshot
  path + 3D TCN/self-attention temporal path. Input `(B, 50)`, output 4 logits
  (`0=NO_TRADE, 1=BUY_MARKET, 2=SELL_MARKET, 3=WAIT`). The 4th logit is a
  POLICY bridge — the labeler emits 3 classes (WAIT is policy-derived).
  Classified **LEGACY BASELINE (control group)** since Phase 13.
- `model_generation/` (Phase 13+) — artifact-first factory: ModelManifest,
  DatasetManifest, LabelSchema `triple_barrier_3class_v1` (3 classes),
  NewsContextSchema `news_context_v1` (12 fields, causally correct),
  architectures (MLP_V2, TCN_V2, TCN_ATTENTION_V1, TRANSFORMER_V1), training
  (CandidateTrainer — candidate artifacts only), validation (12+ gates, ECE,
  OOS floors), `runtime.py` `LocalModelRuntime` — **inference requires NO
  database** (blocked-sqlite integration test).
- **Decision chain in the tick pipeline (order matters):**
  1. Features (50D/70D) + Regime classification
  2. Regime Guardian Gate — unsafe regime → `NO_TRADE` (BLOCKED_BY_GUARDIAN)
  3. ScalpNet inference (`LiveEngine._infer_probabilities`, stashes the exact
     post-scaler pre-softmax tensor as `_last_model_input_tensor`)
  4. RuleMatrixEngine — 30+ DB-configured rules; any VETO → NO_TRADE
  5. SMC God Mode confluence (BOS/CHOCH, 50% equilibrium, liquidity sweep)
  6. TradeProposal (BUY / SELL / BUY_LIMIT / SELL_LIMIT)
  7. Phase 08 experience gate → Phase 09 intelligence gate → Phase 12 news gate
     (bounded confidence adjust, action NEVER changed) → risk sizing.
- `signals/rule_matrix.py` + `signals/rule_catalog.py` + `_rule_engine.py` +
  `_rule_evals_*.py` — DB-driven rules; fresh DBs default all rules DISABLED;
  TTL 5s cache; all on the hot path but never blocking (INV-001).
- `signals/policy.py` — `SignalPolicy` — routing, pending-order lock (30s,
  1.0×ATR drift before re-quote).
- `candle_intelligence/` (BUG-061) — candle-close GATE: 29-pattern engine,
  close-quality classification; advisory only; runs on each completed M1 bar
  (`_on_new_bar`); isolated `artifacts/candle_intel.db`.
- `strategies/` (Phase 15C) — seedable bar-based strategy engine
  (Strategy protocol, `ichimoku.py` translated from Pine, content-addressed
  `StrategyCandidate`s). PURE signal generators — no I/O, no order authority.

### 1.6 Risk Engine
- `risk/risk_engine.py` — `RiskEngine`: dynamic lot sizing cascade:
  Raw Risk = Equity × Risk% → Raw Volume = Risk/(SL pts × tick value) →
  floor to broker volume_step → clamp to account tier caps
  (VERIFIED from code: equity <$100: 0.02 lots; <$1k: 0.10; <$10k: 1.00;
  ≥$10k: min(10.0, volume_max); NOTE: older docs said 0.5/2.0 -- code wins)
  → free-margin clamp (margin ≤ max_margin_usage_pct, default 10% of
  free margin) → final volume.
- **INV-003:** RiskEngine is the authoritative risk boundary — every entry
  proposal passes `calculate_dynamic_volume()`.

### 1.7 Execution Engine
- `execution/order_manager.py` — `OrderLifecycleManager`, the authoritative
  order + position lifecycle owner. 60-scenario router (skill.md inventory), 11 explicit in-trade
  position states -- CODE-VERIFIED enum names (order_manager.py PositionState):
  PROFIT_UNPROTECTED / PROFIT_PROTECTED / PROFIT_TRAILING /
  PROFIT_GIVEBACK_WARNING / PROFIT_GIVEBACK_CRITICAL / LOSS_EARLY /
  LOSS_RECOVERY_CANDIDATE / LOSS_RECOVERY_CONFIRMED / LOSS_RECOVERY_FAILING /
  LOSS_EXIT_PRESSURE / LOSS_HARD_EXIT. (Older docs drew PROPOSED/SUBMITTED/OPEN
  -- those exist in the order flow; the in-trade manager uses the PROFIT_*/LOSS_*
  machine. See also section 5.2.)
- **INV-004:** enforces `HARD_MAX_LOTS = 10.0` (absolute clamp in
  `_clamp_volume()`) and `MAX_TOTAL_EXPOSURE = 1`.
- Circuit breaker: 3 consecutive broker rejections → `SystemHealth.SAFE_MODE`,
  dispatch halts.
- Pending-order cancellation is NOT complete until broker state confirms
  removal (verified-cancel, BUG-072/073/074; retcode 0 = never reached server).
- Reversal capture + split-fill family context (BUG-081:
  `_pending_context_registry`, bounded, TTL 3600s, cap 64).
- Hold-score emergency bailout: convex drawdown penalty, profit-shield floor,
  `hold_score < 30` → immediate `S09_CRITICAL_HOLD_SCORE_BREACH_BAILOUT`.

### 1.8 Position Management
- Same `OrderLifecycleManager` — hybrid state machine evaluated every tick:
  breakeven lock, profit giveback (volatility-expansion + locked-SL aware),
  AI-direction-flip exit, regime-invalidation exit, LOSS_HARD_EXIT,
  reconcile_missed_closes (restart-gap broker closes), dead-ticket sweep,
  time decay (derived from CURRENT TICK timestamp, never wall clock — BUG-055).
- `intelligence/lifecycle.py` — `PositionLifecycleTracker` appends the
  immutable position_timeline (POSITION_CREATED … POSITION_EXITED).
- Split-fill siblings share the parent order/request identity — one economic
  trade, one experience.

### 1.9 Telemetry
- `web/server.py` — FastAPI: REST (>/api/status, /api/account/*, /api/models/*,
  /api/research/*, /api/news/* (+ `/api/news/ai-status`, `/api/news/analyze/*`,
  `/api/news/auto-prune`, `/api/news/{id}/restore`), /api/liquidity/*,
  /api/debug/*, /api/settings/*, /api/db/*, /api/forensics/*, /api/intelligence/*
  …), SSE `/api/ticks/stream`, WebSocket `/web` + `/ws`. All Enum payloads pass
  `serialize_enums()`. `GET /api/news?status=ACTIVE|ALL|IRRELEVANT` filters by
  `article_status`; response includes `status_counts` + per-article `ai_analysis`.
- Web UI (`Web/` at repo root; index.html LF / app.js CRLF): Account,
  News + News Intelligence 0100 (AI status, per-article AI analysis, batch
  analysis, auto-prune Pro Mode, Active/All/Irrelevant filters, restore),
  Liquidity, Rules, Config, Debug, Incident Center tabs. The Debug tab is
  a PURE renderer of the canonical `GET /api/debug/state` (18 sections) — never
  computes trading intelligence in JS. UI is the source of control for
  execution mode (BUG-119); settings persist via SettingsService
  (`db.set('execution.mode', …)`, HOT_RESTRICTED) — never live.yaml (INV-010).
- `Web/forensic_console.js` + `Web/news_intelligence.js` — buildless vanilla-JS
  companion modules (no bundler): forensic shared infra (toast, error
  normalization, single-source incident model `deriveKpis`, severity/status
  normalization, Agent Mode state machine, task provider surface, modal/focus
  trap, button-lock) + News Intelligence (AI status banner, analyze/re-analyze,
  bounded batch, auto-prune with confirm, filters, restore, `articleExtrasHTML`
  card enrichment). All HTTP via `window.NX.api` — never raw `fetch()` in
  feature modules; never `TypeError: Failed to fetch` in DOM.
- Observability: structlog JSON logs; `TelegramNotifier` (queue+worker,
  full lifecycle, HTML, redacted secrets, health_state READY/DEGRADED/STOPPED);
  `observability/telegram_html.py` + `telegram_transport.py` +
  `ci_telegram_reporter.py` (CI/CD notifications).
- `adapters/database/audit_repository.py` — SQLite WAL audit ledger; writes
  queued to a background worker thread (INV-001); dedup via deterministic
  keys + UNIQUE constraints (signal_dedup_key, idempotency_key).
- `/api/debug/state` snapshot ring (max 64, `debug_snapshot_store`) +
  `/api/debug/snapshots` + compare, SSE diagnostics.

### 1.10 Learning Loop
- `experience/` (Phase 08) — immutable experience ledger + outcomes,
  statistical evaluator, provenance registry, retrieval. Canonical outcome
  correlation chain: ledger.ticket == outcomes.execution_id;
  outcomes.idempotency_key == experiences.idempotency_key.
- `accounting/` (Phase 08) — AccountingCore = SINGLE canonical accounting
  authority (periods UTC half-open, ONE drawdown methodology, net PnL computed
  once, NO SYNTHETIC NUMBERS — unavailable = None/“n/a”).
- `intelligence/` (Phase 09) — Trade Intelligence Brain: lifecycle, autopsy
  (CLEAN_WIN/LUCKY_WIN/MANAGED_LOSS/COSTLY_LOSS/EVEN), behavior detection,
  strategy evolution (never live until validated), pre-trade intelligence gate.
- `research/` (Phase 09B) — strategy discovery → backtest (friction-aware,
  deterministic) → walk-forward (purge+embargo) → OOS gate → robustness
  (spread/slippage/latency stress) → explainable score → registry. NEVER
  auto-promotes to ACTIVE.
- `model_lifecycle/` (Phase 10) — controlled offline training, 12 validation
  gates, Champion/Challenger, candidate-only artifacts, no auto-promotion.
- `shadow/` + `shadow/shadow70/` (Phase 11 + 70D) — challenger evaluation on
  the SAME live feature vector, `simulated=True` everywhere, zero order
  authority, promotion NEVER automatic (INV-014/015).
- `governance/` (TASK-6/8) — 10/14-gate load gate, alignment, engine,
  evidence, shadow runtime, promotion transaction + rollback, audit tables.
  Observability-only: imports NO order manager / risk engine / adapter.
- `news/` (Phase 12 + Intelligence 0100) — ingest/dedupe/analyze/consensus/
  decay/gate; 11 sources, 189 keywords; news is CONTEXTUAL ONLY (bounded
  confidence adjustment ≤+0.05 / ≤−0.10, action never changed); dedicated
  `artifacts/news.db`. Intelligence 0100 adds: `news/ai_service.py`
  (NewsAIStatus, `analyze_article_with_ai`, `auto_prune_irrelevant`,
  `restore_article`, provider reuse via `strategies/factory/provider.py::
  complete_json`, grounded delimited prompts, injection-defended system prompt,
  schema-validated responses, separate `news_ai_analysis` table — deterministic
  truth never overwritten), `news/database.py` additions (`news_ai_analysis`,
  `news_prune_audit`, `article_status` ACTIVE/IRRELEVANT with recoverable
  transitions + audit rows, status-filtered `list_articles`,
  `count_articles_by_status`), `configuration/runtime_config.py`
  `news.auto_analysis_enabled` (bool, OFF by default, deterministic local-only
  path when enabled), `web/news_intelligence_routes.py` (5 endpoints:
  ai-status, analyze/{id}, analyze/batch, auto-prune, {id}/restore).
- `hygiene/` (TASK-11) — non-destructive DB hygiene
  (OBSERVE→CLASSIFY→PLAN→VALIDATE→CLEAN→VERIFY; AUDIT_ONLY default).
- `incidents/` (TASK-12/13) — diagnostic-only incident detection/correlation/
  root-cause; containment advisory only (INV-019).
- `forensics/` (TASK-11-POST-70D) — read-only ForensicHealthEngine,
  deploy gate (PASS/ALLOW…CRITICAL/BLOCK), experience-gap, trend.
- Online fine-tuning: when the live rolling buffer hits 300 completed feature
  records, LiveEngine kicks a background fine-tune via `asyncio.to_thread`
  (label recent 300 → fine_tune_online → quality gate → atomic hot-swap under
  `_bundle_lock`). Present but the heavyweight training path is now the
  controlled Phase 10/13 pipeline; auto_train defaults OFF.

---

## 2. Module Dependency Graph

Layering (top level depends on lower levels; arrows = depends-on):

```text
Web/UI  →  web/server.py  →  application/live_engine.py  →  [signals, risk,
execution, features, models]  →  ports  →  adapters  →  domain
              │   │   │
              │   │   └── background workers (asyncio.to_thread):
              │   │       accounting, intelligence, research, training,
              │   │       shadow, shadow70, news, hygiene, incidents,
              │   │       governance snapshots — NEVER on the tick path
              │   └── learning subsystems: experience, model_lifecycle,
              │       model_generation, governance
              └── persistence: adapters/database (audit.db, news.db,
                  candle_intel.db, app_settings.db), database/ (migrations)
```

### 2.1 Domain — `domain/` (models.py, enums.py)
- **Responsibility:** immutable data contracts (`TickData`, `TradeProposal`,
  `Position`, `TradeOrder`, `AccountInfo`, enums).
- **Inputs/Outputs:** pure types; no behavior.
- **Dependencies:** Pydantic only.
- **Failure impact:** none directly; violates nothing (frozen).
- **Rules:** `frozen=True` everywhere — mutate via `model_copy(update=…)`.

### 2.2 Ports — `ports/` (mt5_port.py, gateway_port.py)
- **Responsibility:** dependency-inversion boundaries (`IMT5Port` with 12
  provider methods, `IGatewayPort`).
- **Failure impact:** a signature change ripples to ALL adapters and callers —
  treat as SHARED API (commit contract tag).

### 2.3 Adapters — `adapters/` (mt5/, paper/, database/)
- **Responsibility:** external infrastructure (Win32 MT5 IPC, ZMQ gateway,
  paper sim, SQLite WAL persistence).
- **Inputs/Outputs:** protocol calls ⇄ typed snapshots / OrderResults / rows.
- **Dependencies:** MetaTrader5, zmq, sqlite3.
- **Failure impact:** HIGH (broker connectivity). Every call must go through
  `run_mt5_call()` diagnostics; broker truth wins over local state (INV-011).
- **Rules:** DB writes queued via background worker thread; never synchronous
  on the tick path.

### 2.4 Core / Application — `application/live_engine.py`
- **Responsibility:** the async orchestrator: tick loop, bar aggregation, state
  sync, all worker kicks, model bundle loading/swap, mode/runtime handling.
- **Failure impact:** CRITICAL — the whole engine. 
- **Rules:** **NEVER BLOCK THE EVENT LOOP** — no sync I/O, no training inside
  `_process_tick_pipeline()`. No function-local `import time` (BUG-074).

### 2.5 Execution — `execution/order_manager.py`
- **Responsibility:** order dispatch, position state machine, exits,
  protection, reconciliation, circuit breaker.
- **Inputs:** tick, account, proposal; **Outputs:** executed orders, state.
- **Dependencies:** IMT5Port, RiskEngine, AuditRepository, lifecycle tracker.
- **Failure impact:** HIGH (order dispatch) — broker-verified cancel
  semantics, sibling-leg sync, CANNOT be bypassed.

### 2.6 Research / Training / Model Lifecycle / Generation — `research/`,
`training/`, `model_lifecycle/`, `model_generation/`, `labeling/`
- **Responsibility:** evidence-driven discovery, validation, controlled
  training, artifact-first model factory, champion/challenger governance.
- **Safety:** these packages hold NO adapter / order manager / risk engine —
  zero order authority (INV-002, tested). Training never touches the Champion
  artifact; promotion is operator-gated (INV-015/016).

### 2.7 Infrastructure / Persistence — `adapters/database/`, `database/`,
`settings/`
- **Responsibility:** SQLite WAL audit/ledger/registries, migration engine,
  schema manifest, isolated settings DB (DPAPI secret store).
- **Rules:** schema changes ONLY via versioned migrations (INV-013);
  migration-owned tables stay OUT of the schema manifest (INV-018);
  UI/Telegram settings changes route via SettingsService, never live.yaml
  (INV-010, BUG-080).

### 2.8 UI — `Web/`, `web/server.py`, `web/debug_snapshot.py`, `Web/forensic_console.js`, `Web/news_intelligence.js`
- **Responsibility:** REST/SSE/WS serving, canonical debug snapshot, static
  bundle + buildless forensic/news companion modules. UI renders backend truth;
  frontend never recomputes trading intelligence; enum serialization mandatory;
  sanitized error envelopes (`web/errors.py`) — never `str(e)` to clients
  (BUG-040). All fetch paths via `window.NX.api` (safe envelope
  `{ok, error:{code,message,request_id}}`); no raw `fetch()` in feature
  modules; user errors via `NX.Forensic.normalizeError` + `NX.Forensic.toast`
  (never `String(e)` in DOM). Incident KPIs derived from ONE authoritative
  array via `NX.Forensic.model.deriveKpis` (never separate count vs list).

### 2.9 External integrations — MT5 (primary), Telegram (alerting),
GitHub (release updates), news sources (RSS/official), Docker.
- Release/update system (`release/`) — GitHub Releases API is the ONLY update
  source (INV-013); updates BLOCK while LIVE without --force (INV-014);
  user data outside install dir (`%LOCALAPPDATA%\NexusScalpEngine`).

---

## 3. Algorithm Graph

Format per algorithm: Name / Purpose / Input / Processing / Output /
Dependencies / Failure Cases / Performance Impact. All entries are real,
code-located components.

### 3.1 Market Analysis Algorithms

**A1. MarketRegimeClassifier**
- Purpose: classify market state into 10 regimes to gate trading and
  contextualize signals.
- Input: ticks, spreads, volatility stats.
- Processing: internal math over collected market microstructure.
- Output: `MarketRegimeState` — safe/unsafe classification
  (unsafe: HIGH_SPREAD_CHOP, MARKET_HALTED, NEWS_LOCK…).
- Dependencies: features registry only.
- Failure cases: stale microstructure → misclassified regime → Guardian gate
  still protects (unsafe is the conservative default).
- Performance: cheap, per-tick.

**A2. LiquidityEngine** (`features/liquidity_engine.py`)
- Purpose: pure-causal 10D liquidity structure (BSL/SSL pools, equal highs/
  lows, HTF liquidity, confluence, sweeps) — structural information only.
- Input: completed bars + mid price + ATR + decision_at.
- Processing: swing detection with ±5-bar fractal confirmation; pool lifecycle
  CANDIDATE → CONFIRMED; usable only at `usable_at <= decision_at`.
- Output: 10D vector (LIQUIDITY_FEATURE_NAMES), pools report.
- Dependencies: none (pure numpy).
- Failure cases: no confirmed swings → zero/neutral values (never faked);
  anti-leakage bars > decision_at invisible.
- Performance: `detect_confirmed_swings` is O(n·window) (~44 ms @ 900 bars,
  ~460 ms @ 3500 bars) — bounded by LIQUIDITY_HISTORY_LIMIT=4000 (BUG-106).

**A3. CandleIntelligence** (`candle_intelligence/`)
- Purpose: classify every completed M1 candle close (29 patterns) as a
  decision gate; advisory.
- Input: bar close, multi-factor context weights.
- Output: close-quality classification + pattern flags; block/accelerate
  entry/exit advice.
- Failure impact: advisory only; isolated DB; never blocks protective exits.
- Performance: per new bar (`_on_new_bar`), off tick path.

### 3.2 Feature Engineering Algorithms

**A4. ScalpFeatureEngine — Base 50D**
- Purpose: canonical 50D market-microstructure/SMC feature vector (the
  executable contract = `FEATURE_NAMES`, 50 entries; verified 350/350
  fixtures, causality T-1, dataset/live parity).
- Input: M1 completed bars + current tick.
- Processing: per-feature math incl. wicks, pinbar/engulfing/FVG/order-block/
  CHoCH/breakout, Ichimoku (Tenkan/Kijun/Kumo), normalized RSI (divisor 16.66
  — BUG-082), EMAs, HTF aggregation (H4/H1/M30/M15), support/resistance, SMC
  OB stats; rolling windows capped; final clip `[-3, +3]` + finite fallback.
- Output: 50 floats, finite, bounded.
- Dependencies: numpy; bar/tick buffers.
- Failure cases: NaN/Inf → `FeaturePipelineFrozenError` → deterministic
  fallback; model input validator zero-fills non-finite (live defensive).
- Performance: per-tick CPU-bound; must stay well under the 50 ms tick target.

**A5. 60D extras** (`features/schema_augment.py`, `compute_60d_extras`)
- Purpose: 10 causal candidate features (regime_compression, momentum_5_atr,
  wick_imbalance_5, volume_z_5, range_z_5, clv_avg_5, session_phase_enc,
  price_acceleration, atr_trend_ratio, direction_bias_8).
- Processing: completed bars + decision tick only (INV-015); deterministic
  defaults for missing data.
- Status: candidate-only (scalp_v2 FROZEN).
- Failure cases: NaN/Inf never produced; zeros documentable.

**A6. Feature70 assembly** (`model_generation/schema_v2.py`,
`features/features70.py`)
- Purpose: ONE canonical 70D vector (Base+News+Liquidity) for dataset, replay,
  inference, live (scalp_v3 registry).
- Processing: `compute_70d_frame` → immutable `Feature70Snapshot`; hash
  `feature_schema_hash`; strict — any dimension/order/hash mismatch BLOCKS
  inference with explicit rejection codes (SCHEMA_MISMATCH/DIMENSION_MISMATCH/
  FEATURE_ORDER_MISMATCH/SCHEMA_HASH_MISMATCH/SCALER_MISMATCH/
  NONFINITE_FEATURE/OUT_OF_RANGE_FEATURE/NEWS_UNAVAILABLE/LIQUIDITY_UNAVAILABLE/
  STALE_FEATURES).
- Performance: 70D assembly p50 ≈ 4.1 ms; no DB on feature path (INV-001).

**A7. NewsContext bridge (`news_context_at`)**
- Purpose: causally-correct news context vector.
- Input: news events, sample timestamp T.
- Processing: only events at-or-before T; latest prior event defines vector;
  `time_since_event_sec` per sample; categorical encoding; NaN/Inf→0.0.
- Output: 12-field numeric matrix + news 10D embed (fields 0..8 + news_state).
- Failure cases: no overlap → zero vector + NEWS_INCONCLUSIVE_NO_OVERLAP —
  never fabricated signal.

### 3.3 AI Decision Process

**A8. ScalpNet inference**
- Purpose: per-tick class probabilities.
- Input: 50D (serving) / 70D (candidate) tensor.
- Processing: `_infer_probabilities` — scaler → model (torch inference_mode)
  → softmax; exact pre-softmax tensor stashed for forensics.
- Output: 4 probabilities (NO_TRADE/BUY/SELL/WAIT).
- Dependencies: champion artifact + scaler bundle, governance load gate.
- Failure cases: probs width != artifact classes OR artifact classes != 4 →
  `MODEL_OUTPUT_INVALID`; champion fingerprint-cached (BUG-118) — identical
  polls return memoized instance, one verify log per artifact fingerprint.

**A9. Rule Matrix** (`signals/rule_matrix.py` + rule_catalog)
- Purpose: 30+ DB-configured scalping rules; VETO authority.
- Input: decision context + cached rules (TTL 5s).
- Output: PASS / VETO(reason code).
- Failure cases: fresh DB → all rules DISABLED (safe default).

**A10. SMC God Mode confluence** (`signals/_rule_evals_smc.py`, policy.py)
- Purpose: BOS/CHoCH confirmation, 50% impulse equilibrium, liquidity-sweep
  piercing; predictive limit generation.
- Output: TradeProposal.
- Constraints: pending lock 30s + 1.0×ATR drift.

**A11. SignalPolicy decision routing**
- Purpose: orchestrate model probability → regime guardian → rule matrix →
  SMC → proposal; enforce frequency throttles and duplicate suppression
  (TICK_DUPLICATE_SUPPRESSED is a counter, never an audit row).

### 3.4 Risk / Sizing Algorithms

**A12. RiskEngine.calculate_dynamic_volume** (see §1.6)
- Purpose: capital-safe lot sizing.
- Processing: equity×risk% → points-based raw volume → broker step floor →
  tier cap → 20% free-margin clamp.
- Failure cases: no margin → 0 volume; never negative; hard ceiling 10.0 lots
  enforced again in OrderManager.

**A13. Hold Score / Emergency bailout** (OrderManager)
- Purpose: de-risk losing positions.
- Processing: `100 − convex drawdown penalty (80·ratio^1.5, cap 80) − time
  decay − spread penalty (+ trend bonus suppressed underwater, ratio ≥ 0.30)`.
  Profit-shield `max(85, score)` only when profit ≥ 0 (never for losers).
- Output: per-position eval every 500 ms; `< 30` → immediate early exit
  (S09…). EV-based min-loss: `expected_recovery = initial_risk × RRR` (RRR
  default 1.8) — EV decreases monotonically with drawdown (BUG-056 fix).

**A14. Exit classification with evidence** (`experience/outcome_recovery.py`)
- Purpose: map every close to exactly one broker-truth mechanism with evidence
  provenance (ENGINE_FORCED/BROKER_DEAL_REASON/BROKER_DEAL_COMMENT/
  SL_GEOMETRY/TP_GEOMETRY/FALLBACK_HEURISTIC), EXIT_CLASSIFICATION v3.
- Inputs: broker DEAL_REASON (4=SL, 5=TP — never inverted, BUG-088),
  SL/TP geometry, `was_sl_modified` proof.
- Outputs: BREAK_EVEN_SL_HIT / TRAILING_STOP_HIT / HARD_SL_HIT /
  RISK_FREE_SL_HIT / TAKE_PROFIT_HIT / MANUAL_CLOSE / SYSTEM_CLOSE /
  RECONCILIATION_CLOSE / BROKER_CLOSE / UNKNOWN.
- Invariant: UNKNOWN stays UNKNOWN (INV-012); BE labels require
  modification proof.

### 3.5 Validation / Research Pipelines

**A15. TripleBarrierLabeler** (`labeling/triple_barrier.py`)
- Purpose: cost-aware labels (ATR barriers ± spread friction, vertical
  horizon 15 bars; stride 3 after NO_TRADE).
- Output: 3 classes (0/1/2 = NO_TRADE/BUY/SELL).
- Polars pitfall: ALWAYS bitwise `~`/`&`/`|`, never Python `not`/`and`.

**A16. WalkForwardTrainer** — purged walk-forward folds with embargo + fresh
model per fold; FocalLossWithSmoothing; class weights bridge 3-label → 4-head.

**A17. Research pipeline** — deterministic dataset from experience ledger →
temporal splits (purge+embargo) → leakage guards → friction-aware backtest →
walk-forward → hard OOS gate (OOS fail ⇒ REJECTED) → robustness stress →
explainable score → registry. Content-addressed candidates; operator-gated
promotion only.

**A18. Model lifecycle gates** — 12 gates (dataset, schema, labels, stability,
validation, walk-forward, OOS, robustness, risk, comparison, artifact,
reproducibility) + collapse protection; candidate-only writes; FAILED stays
FAILED.

**A19. Governance validation (TASK-8)** — 14-gate candidate verification,
promotion preview + atomic transaction (lock/audit/crash-recoverable),
rollback preview, emergency freeze. SHADOW→CHAMPION illegal; only
READY_FOR_REVIEW → APPROVED → CHAMPION with operator token.

**A20. Shadow70 runtime** — validated candidate only (NO_VALIDATED_CANDIDATE
today); same live vector; simulated observations; disagreement analysis
(8 categories); feature health + drift; zero Champion/broker impact;
queued persistence.

### 3.5b News Intelligence & Forensic UI Algorithms (0100 — NEW)

**A20b. News AI Analysis Service** (`news/ai_service.py` + `strategies/factory/provider.py::complete_json`)
- Purpose: AI interpretation layer for news (separate from deterministic engine).
- Provider: reuses Strategy Factory `LLMGenerationProvider.complete_json` (single LLM source, single secret store; hot-reload aware via `resolve_factory_provider`); never a second config.
- Prompt: injection-defended — article text wrapped in `<<<ARTICLE_START>>>` DATA delimiters; system prompt explicitly labels article as UNTRUSTED EXTERNAL DATA; deterministic context (importance_score, xauusd_relevance, direction, entities/topics) passed as trusted CONTEXT.
- Input caps: `NEWS_AI_MAX_BODY_CHARS=4000`, `temperature=0.2`, `max_tokens=1200`, `response_format={type:"json_object"}`.
- Validation: `_validate_response` normalizes sentiment to BULLISH/BEARISH/NEUTRAL/MIXED, caps key_facts/uncertainties at 20, enforces non-empty content or `insufficient_evidence=true`; malformed → `analysis_status='failed'` (never masquerades as success).
- Persistence: separate `news_ai_analysis` table (never overwrites deterministic `news_analysis`); dedup via prior completed analysis unless `force=true`; structured `NewsAIAnalysisResult` (completed/failed/skipped).
- Batch: `POST /api/news/analyze/batch` — bounded concurrency `NEWS_AI_BATCH_CONCURRENCY=3` via ThreadPoolExecutor, cap 200 ids, per-item isolation.

**A20c. Recoverable Auto-Prune (Pro Mode)** (`news/ai_service.py::auto_prune_irrelevant` + `news/database.py::set_article_status`)
- Purpose: mark low-signal, non-XAUUSD articles IRRELEVANT without deleting truth.
- Rule (§29): `importance_score < 0.30 AND xauusd_relevance < 0.25` → IRRELEVANT; else ACTIVE. XAUUSD relevance resolved from persisted `news_analysis.relevance_to_xauusd` or recomputed via `LocalNewsAnalyzer` (never invented). Explainable reason via `_prune_reason` (LOW_IMPORTANCE_AND_LOW_XAUUSD_RELEVANCE / LOW_XAUUSD_RELEVANCE / LOW_IMPORTANCE).
- Recoverability: `article_status` migration-safe (`ALTER TABLE ADD COLUMN ... DEFAULT 'ACTIVE'` before indexes); `set_article_status` is idempotent + records `news_prune_audit` row (`pau_*`, previous/new state, rule_version `news-prune-v1`, actor, reason); `restore_article` flips IRRELEVANT→ACTIVE with RESTORE audit; original rows never deleted.
- Counters: `count_articles_by_status` surfaces ACTIVE/IRRELEVANT for the API `status_counts`.

**A20d. News Intelligence API Surface** (`web/news_intelligence_routes.py` + `web/server.py`)
- Endpoints: `GET /api/news/ai-status` (secret-free readiness: NOT_CONFIGURED/AVAILABLE/UNAVAILABLE/MISCONFIGURED), `POST /api/news/analyze/{id}`, `POST /api/news/analyze/batch`, `POST /api/news/auto-prune`, `POST /api/news/{id}/restore`; plus `GET /api/news?status=...` filter.
- Guarantees: reuses Factory provider, never exposes API keys, never raises, consistent error envelope (`NEWS_UNAVAILABLE`, `ARTICLE_NOT_FOUND`, `AI_NOT_CONFIGURED`, `AI_ANALYSIS_FAILED`, `INVALID_REQUEST`).
- Frontend companion: `Web/news_intelligence.js` (`window.NewsIntel` — AI banner, per-article state machine + dedup, batch progress, Pro auto-prune confirm, filter tabs, status_counts, `articleExtrasHTML`).

**A20e. Forensic Incident Center Overhaul** (`Web/forensic_console.js` + `Web/app.js` + `Web/index.html`)
- Purpose: production-grade operational console (Task-12 rework): single authoritative incident array → KPIs derived via `NX.Forensic.model.deriveKpis` (fixes OPEN=2/CRITICAL=1/HIGH=1/MEDIUM=3 impossible state); loading/empty/error/loaded are distinct states; no raw `TypeError: Failed to fetch` in DOM (all via `NX.api` + `normalizeError`).
- Model: `normSeverity`/`normStatus` single normalization boundary; `isOpen`/`isResolved` vocabulary; `deriveKpis` computes all header numbers from the list actually rendered.
- Agent Mode: state machine OFF/IDLE/TRACING/ANALYZING/GENERATING_TASK/RESOLVING/ERROR; auto-trace eligible open incidents via the real `/api/diagnostics/trace` endpoint (deduped by `INC_STATE.agentProcessed`), never fabricated.
- Task Generation: drawer (review-before-submit) populated from REAL incident evidence (ids/timestamps/symptoms/impact, never invented); provider surface truthful (`configured:false` until backend wired); duplicate-submit guard via `withButtonLock`.
- Safety: Stop Bot modal requires typing `STOP` (case-sensitive); `confirmStopBot` only fires after confirmation; halt is `engine._running=False` (does NOT cancel broker pending orders — docs truthfully state this).
- UX: toast region (ARIA live), modal focus trap/Escape/backdrop, skeleton loaders, filter tabs (Open/Resolved/Resolved by Agent), concurrency guard `requestSeq`, Worker health hierarchy (Status/Cycles/Last OK).

### 3.6 Execution Algorithms

**A21. Pending-order verified cancellation** — send → tri-state broker state →
release slot only on confirmed removal; retry ≤3; reconciliation loop
periodic + startup (broker wins).

**A22. Reconcile missed closes** — broker history deals + ledger OPENED
placeholder + no close → restore request_id → full autopsy + outcome path.

**A23. Broker outcome reconstruction** — aggregate ALL close deals (gross,
commission, swap, volume, deal_ids); partial closes merge, never double-count
(dedupe by ticket); missing evidence → reconstruction_source=NONE.

---

## 4. Algorithm Graphs (connected view)

### 3.1 Market Analysis Layer (algorithms as connected graph)

```text
Raw bars + ticks
   |
   +--> Candle anatomy (wick/body/doji/pin/engulf/CLV)              [feat 0..6]
   +--> Momentum/returns (lag log-returns, displacement, spikes)     [feat 7..9, 20..25]
   +--> Swing/range structure (20-bar swings, compression, extreme)  [feat 10..15]
   +--> Session masks (Tokyo/London/NY/overlap)                      [feat 16..19]
   +--> Ichimoku (Tenkan/Kijun/Kumo/TK cross/width)                  [feat 30..33, 38..39]
   +--> Oscillators/EMA (RSI14, EMA21/50 z-dists, cross-asset z)     [feat 34..37]
   +--> HTF context (H4/H1/M30/M15 aggregates)                       [feat 40..43]
   +--> S/R zones (fractal window-3 support/resistance)              [feat 44..45]
   +--> SMC (FVG sig, OB type, CHoCH, breakout, OB BOS/equilibrium/
         sweep/fib-50-60)                                            [feat 26..29, 46..49]
   |
   +--> SMC / ICT concepts layer:
   |      Liquidity sweep detection (penetration + reclaim)     [liquidity engine]
   |      Order blocks (last opposing candle before impulse)     [scalp_features]
   |      Fair Value Gaps (3-candle imbalance, 0.20*ATR gate)    [feat 26]
   |      Market structure (HH/HL/LH/LL via 20-bar swings + EMA20/50 CHoCH) [feat 28, 10..11]
   |      Equal highs/lows (volatility-aware tolerance clustering) [liquidity 62..63]
   |      HTF liquidity (H1/H4/D1 confirmed pools, forming excluded) [liquidity 64]
   |
   v
Market Structure Snapshot (regime + swings + pools + OB/FVG/S&R)
```

**Regime classifier** — `features/regime_classifier.py`: `MarketRegimeClassifier.classify_tick(tick, is_macro_news_window)` -> `MarketRegimeState` with 10 `RegimeType`s (ranging/chop/spread-expansion/halted/trending-up/down/macro-news-lock etc.). Feeds the Regime Guardian Gate: unsafe regimes (`HIGH_SPREAD_CHOP`, `MARKET_HALTED`, `NEWS_LOCK`, ...) -> `ActionType.NO_TRADE (BLOCKED_BY_GUARDIAN)` before any model decision.

### 3.2 Feature Engineering Layer

```text
Input:  Raw Market Data (M1 bars + tick + HTF aggregates + news cache + liquidity pools)
          |
          v
Processing: 3 parallel context streams
  A) Indicators      (RSI14, ATR14, EMA21/50, Ichimoku, volume z, CLV, log returns)
  B) Structure       (swings, S/R fractals, compression, extremes, sessions, HTF)
  C) Context         (regime, news context vector, liquidity pool state)
          |
          v
Combine -> 70D canonical vector (schema_contract.validate_70d_vector:
          dimension=70, finite, [-3,+3]; schema-hash optional)
          |
          v
Output: 70D Vector (live serves 50D champion -- full 70D is candidate/research path)
```

Determinism contract: live == replay == training (same inputs => same vector); `features70.py` / `runtime70.py` / `inference_validator.py` enforce parity; the inference validator rejects non-causal or drifting vectors.

### 3.3 AI Decision Layer

```text
70D Vector (or 50D live)
      |
      v
ScalpNet (2D MLP path for snapshots; 3D causal TCN + self-attention for sequences)
      |
      v
4 logits -> softmax probs
      |
      +---- BUY_MARKET (idx 1)
      +---- SELL_MARKET (idx 2)
      +---- WAIT (idx 3, policy-derived -- never a training label)
      +---- NO_TRADE (idx 0)
```

- Model inputs: feature tensor post-scaler (`_last_model_input_tensor` stashed for observability; the exact pre-softmax tensor).
- Decision classes: 4 logits (above). Trainer labels: 3 classes (WAIT has unit weight in loss, never trained as a class).
- Confidence: `high_confidence_threshold` default 0.70 (hot-swappable via AlgoConfig); news gate adjusts confidence bounded (+/- 0.05 / 0.10).
- Filtering logic chain (in order): Regime Guardian -> Rule Matrix (32 rules) -> SMC God Mode confluence -> P8 Experience gate -> P9 Intelligence gate -> P12 News gate -> Risk sizing -> execution-state checks (exposure lock, pending lock) -> OrderManager dispatch.
- Trade permission system: `MAX_TOTAL_EXPOSURE = 1` (at most one active position OR one pending order); `HARD_MAX_LOTS = 10.0`; circuit breaker (3 consecutive broker rejections -> SAFE_MODE, dispatch halted); pending-order lock (30 s, >=1.0xATR drift before requote).

---

## 5. Risk Management Graph

### 4.1 Risk decision tree (canonical -- risk/risk_engine.py + execution/order_manager.py)

```text
Signal (TradeProposal from policy after all gates)
      |
      v
Risk Evaluation -- calculate_dynamic_volume(entry, sl, account, symbol_info, risk_pct)
   |
   +-- INPUT GUARDS: entry>0, sl>0, equity>0 & finite, margin_free/leverage/
   |     contract_size/volume_step finite     (fail -> 0.0 lots + reason code)
   |
   +-- STEP 1: fixed-dollar risk:  risk_amount_usd = equity * (risk_pct / 100)
   |         raw_volume = risk_amount / (SL_distance_price * contract_size)
   |
   +-- STEP 2: floor to broker volume_step (e.g. 0.01)
   |
   +-- STEP 3: account tier ceiling (VERIFIED from code):
   |         equity < 100     -> 0.02 lots
   |         equity < 1,000   -> 0.10 lots
   |         equity < 10,000  -> 1.00 lots
   |         equity >= 10,000 -> min(10.0, symbol_info.volume_max)
   |         (NOTE: older docs said 0.50/2.00 -- the CODE says 0.10/1.00; code wins)
   |
   +-- STEP 4: free-margin clamp: margin required <= max_margin_usage_pct
   |         (default 10.0% of free margin -- configurable) -> fail = 0.0 lots
   |
   +-- STEP 5: OrderLifecycleManager._clamp_volume():
   |         absolute HARD_MAX_LOTS = 10.0 unconditional ceiling
   |         MAX_TOTAL_EXPOSURE = 1: positions + pendings < 2 required,
   |         else NO_TRADE + reason (exposure lock)
   |
   +---- Risk OK  -> dispatch to broker
   +---- Reject   -> NO_TRADE with explicit reason (never silent)
```

### 4.2 Why every decision exists

| Decision | Rationale | Guard rail |
| :--- | :--- | :--- |
| Fixed-dollar risk, not fixed lots | Equalizes pain per trade regardless of SL distance | risk_amount = equity x risk_pct |
| Floor to volume_step | Broker rejects sub-step lots; FP-safe flooring (eps 1e-9, round 4) | _floor_to_step() |
| Tier ceilings | Protects small accounts from catastrophic single-trade loss | 0.02 / 0.10 / 1.00 / 10.0 monotone |
| Margin clamp | Never over-leverage; avoid margin-stop liquidation | <=10% free margin default |
| HARD_MAX_LOTS = 10.0 | Absolute capital ceiling independent of config | enforced in OrderManager, not just RiskEngine |
| MAX_TOTAL_EXPOSURE = 1 | One position OR one pending at a time -> bounded aggregate risk | positions + pendings < 2 |
| Rejection with reason codes | Every NO_TRADE is explainable; execution-state blocks never learned as model choice | audit payload blocked_by / decision_stage |
| Protective exits always run | Drawdown/exit management runs even when inference is blocked or failing | position management with probs=None still executes protective stops |
| Circuit breaker | 3 consecutive broker rejections = systematic problem -> freeze new orders | SAFE_MODE health state |

### 4.3 In-position risk: hold_score & emergency exits

hold_score = 100 - (80 * ratio^1.5) [convex drawdown penalty, cap 80] - time decay
            - spread-expansion penalty + trend bonus (suppressed when ratio >= 0.30)

- Evaluated every 500 ms per position. 50%-of-risk drawdown ~ -28 points; 90% ~ -68.
- Profit-shield floor max(85, score) ONLY when profit >= 0 and not underwater.
- hold_score < 30.0 in drawdown -> immediate S09_CRITICAL_HOLD_SCORE_BREACH_BAILOUT.
- Emergency scenarios S01-S13 (verified enum): S01 compound kill-switch, S02 toxic-flow kill-switch, S04 structure failure w/ active loss, S05 extreme toxicity negative, S06 severe desync w/ uncontrolled loss, S07 catastrophic spread expansion, S08 excessive MAE drawdown cut, S09 critical hold-score breach, S10 terminal hold-score failure, S11 deep low-score bailout, S12 confirmed low-score bailout, S13 standard early-emergency bailout.
- Split-fill family sync: emergency close of one leg closes every sibling with the same originating order_id (_close_sibling_legs).
- Min-loss EV anchor (BUG-056 fix): expected_recovery = initial_risk x RRR (RRR from min_risk_reward_ratio, default 1.8) -- EV decreases monotonically with drawdown depth.

---

## 6. Execution Pipeline Graph

### 5.1 Canonical execution flow

```text
Signal (sized, gated TradeProposal)
      |
      v
Validation -- schema/sanity: action != NO_TRADE, entry/sl/tp sane,
              confidence >= threshold, no pending-lock violation
      |
      v
Risk Check -- calculate_dynamic_volume + _clamp_volume (see section 5)
              MAX_TOTAL_EXPOSURE guard, pending-order lock (30s / 1.0xATR)
      |
      v
Order Creation -- OrderLifecycleManager.dispatch_order(symbol, action, volume,
                  sl, tp, request_id, confidence, regime, ...) ->
                  TRADE_EXECUTION_CONTEXT staged in _pending_context_registry
                  (bounded, TTL 3600s, capacity 64)
      |
      v
Broker Interface -- IMT5Port.order_send() via DirectMT5Adapter /
                    RemoteGatewayAdapter / PaperMT5Adapter
      |
      +-- SUCCESS  -> ticket(s); split-fill siblings bind the SAME immutable
      |              context (order_id, reason, confidence, regime, setup)
      |              -> ledger opened placeholder, exposure slot occupied
      |
      +-- REJECTION -> retcode tracked; counter; >=3 consecutive -> SAFE_MODE
      |               (dispatch halted); retry semantics per broker code
      |               (10014/10025 unchanged)
      |
      v
Position Tracking -- per-tick manage_active_positions: 11-state machine,
                     hold_score eval every 500ms, SL/TP modifies via broker,
                     breakeven lock, trailing, partial closes, AI/regime flip
                     exits, reconciliation of broker-side closes
      |
      v
Exit Management -- protective exits (SL/TP/breakeven/trailing), S01-S13
                   emergency scenarios, manual closes, missed-close
                   reconciliation (reconcile_missed_closes before dead-ticket
                   sweep; _reconcile_seen dedup)
      |
      v
Outcome Recording -- one audit_ledger row (upsert on ticket), one
                     audit_experience_outcomes row (idempotency_key),
                     autopsy + lifecycle events + Telegram canonical close
```

### 5.2 States and transitions (verified from code)

| State | Transition to | Trigger |
| :--- | :--- | :--- |
| PROPOSED | SUBMITTED | policy/inference produced proposal accepted by risk+execution gates |
| SUBMITTED | OPEN (or REJECTED) | broker fill / partial fills (split-fill siblings) |
| OPEN | PROFIT_* / LOSS_* states | per-tick evaluation against MFE/MAE/hold_score |
| PROFIT_UNPROTECTED | PROFIT_PROTECTED | breakeven/PL protection trigger |
| PROFIT_PROTECTED | PROFIT_TRAILING | trailing threshold crossed |
| PROFIT_TRAILING | PROFIT_GIVEBACK_WARNING / _CRITICAL | giveback % vs MFE |
| LOSS_EARLY | LOSS_RECOVERY_CANDIDATE / LOSS_EXIT_PRESSURE | recovery EV vs exit EV |
| LOSS_RECOVERY_* | LOSS_RECOVERY_FAILING / LOSS_HARD_EXIT | recovery invalidation / score breach |
| any active | CLOSED | protective stop, TP, manual close, S-scenario bailout, AI/regime flip |
| CLOSED | (ledger finalized) | finalize_exit(ticket, realized_pnl, realized_r, exit_mechanism) |

Note: the 11 PositionState names verified in code are the PROFIT_*/LOSS_* taxonomy
(PROFIT_UNPROTECTED/PROTECTED/TRAILING/GIVEBACK_WARNING/GIVEBACK_CRITICAL,
LOSS_EARLY/RECOVERY_CANDIDATE/RECOVERY_CONFIRMED/RECOVERY_FAILING/
EXIT_PRESSURE/HARD_EXIT). Older docs drew PROPOSED/SUBMITTED/OPEN -- the
PROPOSED/SUBMITTED lifecycle exists in the order flow but the in-trade manager
uses the PROFIT_*/LOSS_* state machine.

### 5.3 Failure scenarios

| Failure | Detection | Response | Invariant |
| :--- | :--- | :--- | :--- |
| Broker rejects order | retcode != DONE/PLACED | counter++, retry semantics, SAFE_MODE at 3 | never silent |
| Pending cancel unresolved | `_pending_broker_state()` tri-state | cancel_pending_order_verified / retry (<=3) / reconcile | exposure slot stays occupied until GONE-with-DONE or terminal history confirms |
| Connection drop | `MT5ConnectionState` (CONNECTED/CONNECTING/DISCONNECTED/DEGRADED/AUTH_ERROR/TERMINAL_ERROR/UNKNOWN) | reconnect + reseed bars (REPLACE+ALIGN, dedupe by ts) | broker truth precedence (INV-011) |
| Missed close (restart gap) | broker history deals + ledger OPENED + no close + no tracking | reconcile_missed_closes restores request_id -> full outcome path | _reconcile_seen dedup; async-write race guarded by _entry_timestamps |
| Inference failure | exception in _infer_probabilities | isolated; positions still managed with probs=None | protective stops never pause |
| Warmup incomplete | h1 bars missing | NO_TRADE HTF_WARMUP_INCOMPLETE + audit row; retry each new bar / 15s | no inference before readiness |
| Split-fill provenance gap | no staged context for a ticket | [TRADE_LINEAGE] log + _unbound_ticket_contexts; never confidence 0.0 silently | family contexts prune on final-sibling close |

---

## 7. Learning / Research Graph

### 6.1 Canonical learning loop

```text
Historical Data (audit.db ledger + experiences + snapshots + broker history)
      |
      v
Dataset Builder (research/dataset.py ResearchDatasetBuilder; model_lifecycle/dataset.py;
                 model_generation/dataset_factory.py) -- deterministic, causally-
                 ordered, provenance + feature_schema_id preserved per sample
      |
      v
Label Generator (labeling/triple_barrier.py) -- cost-aware triple-barrier with
                 spread friction: upper = entry + ATR*profit_mult (BUY),
                 lower = entry - ATR*stop_mult (SELL), horizontal horizon
                 max_holding_bars=15 -> NO_TRADE; stride 3 downsampling after
                 NO_TRADE labels (class-imbalance mitigation)
      |
      v
Training (training/walk_forward_trainer.py WalkForwardTrainer; model_generation/
          CandidateTrainer; SequenceCandidateTrainer) -- purged walk-forward folds,
          fresh model per fold, focal loss / CE with class weights (index 3 = 1.0),
          non-finite loss / grad norm > 5 -> FAILED (never CHALLENGER)
      |
      v
Validation (model_lifecycle/gates.py 12 gates; model_generation/validation.py:
          OOS acc >= 0.30, macro-F1 > 0.34, balanced-acc > 0.34, ECE <= 0.15,
          min-evidence 100 rows; class/regime collapse -> REJECTED)
      |
      v
Model Registry (experience_model_registry + model_comparisons + training_runs +
          strategy_registry + research_runs + shadow_runs/comparisons/promotions)
      |
      v
Production Feedback (outcomes -> experience ledger -> accounting/intelligence/
          research workers -> operator-gated promotion; shadow runs evaluate
          challengers on SAME live vectors, simulated=True; NEVER auto-promoted)
```

### 6.2 Sub-graphs

**Backtesting** (research/backtest.py): deterministic, friction-aware backtest over
recorded trades (spread, slippage, latency assumptions from ExecutionAssumptions).

**Walk-Forward** (research/walkforward.py + training/walk_forward_trainer.py):
repeated temporal folds TRAIN -> [purge] -> VALIDATE -> [embargo]; fold stability
and degradation tracked; every fold builds a fresh model (no peeking).

**Out-of-Sample** (research/oos.py OOSGate + model_generation/validation.py):
hard OOS floor; OOS failure => REJECTED even with high in-sample win rate.
Temporal splits only (research/splitting.py) -- NEVER random splits.

**Robustness** (research/robustness.py): spread/slippage/latency stress; measures
degradation curve, not just "still profitable". model_lifecycle gates include
robustness + drawdown collapse protection.

**Online improvement** (live_engine._trigger_async_online_fine_tune):
rolling 300 feature records -> label -> fine_tune_online(model_clone, ...) ->
quality gate (dominance check max class ratio <= 95%, anti-collapse active-class
recall > 0%) -> _save_model_weights_atomic -> hot-swap under _bundle_lock.
PLUS: experience/intelligence/research workers enrich the same ledger; behavior
analysis (BehaviorDetectionEngine) flags measurable patterns; news post-event
memory records predicted-vs-actual impact (evidence only).

**Champion/Challenger/Shadow** (model_lifecycle + shadow): candidate artifacts
(candidate/staging) never touch the Champion (hash invariant); validated
Challenger = shadow-eligible; shadow runs compare same-vector decisions with
promotion evaluation (hard vetoes: OOS/drawdown/robustness/strategy-regression/
calibration/regime-specific); SHADOW->CHAMPION is illegal; promotion is an
operator-gated deliberate process only. Governance load gate (10 gates) runs
BEFORE loading any challenger; MODEL_LOAD_REJECTED with failing_gate returned.

**Seeded strategies** (strategies/): builtin_candidates() content-addressed from
Pine-translated Ichimoku engines; the ResearchWorker `seed` step upserts via
seed_builtin_candidates() PRESERVING existing validation results.

**50D vs 70D reality** (honesty): TCN_ATTENTION_V1 and 60D candidates were
benchmarked and REJECTED (val_acc 0.745-0.764 vs baseline 0.819; macro-F1 0.25 vs
0.29; 8-cell MATRIX A/B/C/D all rejected 2026-08-18). Champion = LEGACY_BASELINE
control group. New architectures must EARN promotion on identical
splits/labels/purge/embargo/friction -- never on point estimates at low n.

---

## 8. Agent Understanding Layer

### 7.1 Agent Mental Model

If you are an AI agent entering this project, understand these principles FIRST:

1. **What the system tries to achieve:** trade XAUUSD M1 scalps profitably with
   strict capital protection, while continuously learning from every closed
   trade — but NEVER letting learning, research, news, or a challenger model
   place, modify, or close a trade on its own.
2. **The central intelligence contract is the 70D tensor** (`scalp_v3`,
   hash `235b8fccc96b7e0e`): Base 0..49 | News 50..59 | Liquidity 60..69.
   ONE snapshot -> ONE canonical vector everywhere (dataset/replay/training/
   inference/live). Never hand-assemble slices; use `schema_contract`.
3. **What decisions are dangerous (never touch without full context):**
   - Anything in `execution/order_manager.py` (hot path, real money, 11-state
     machine, S01-S13 emergencies, pending-cancel broker truth).
   - `risk/risk_engine.py` caps and clamping (HARD_MAX_LOTS=10.0,
     tier ceilings, margin clamp).
   - `application/live_engine.py::_process_tick_pipeline` (zero sync I/O,
     zero training, zero blocking).
   - The Champion artifact (`artifacts/models/scalp/XAUUSD/v1.0.0`) — never
     overwrite; candidate training only.
   - `agents/skill.md` (architecture contract) and `agents/bugs.md` (bug
     ledger) — READ BEFORE EDITING, append never rewrite.
4. **Which modules are critical:** LiveEngine (orchestrator), OrderManager
   (execution), RiskEngine (capital), AuditRepository (truth ledger),
   features/schema_contract.py (canonical contract), AccountingCore (perf
   truth), ChampionManager (production model).
5. **Which files are high priority:** `agents/skill.md`, `agents/bugs.md`,
   `docs/architecture/dependency-map.md`, `features/schema_contract.py`,
   `execution/order_manager.py`, `application/live_engine.py`,
   `signals/policy.py`, `risk/risk_engine.py`, `adapters/database/audit_repository.py`.
6. **Assumptions that must NEVER be violated:**
   - INV-001: no synchronous DB on the tick path (all queued).
   - INV-002/003/014: research/intelligence/governance/news NEVER hold order
     authority (no adapter/order-manager/risk-engine imports; tested).
   - INV-005/006: parent-child order lineage; split-fill siblings share ONE
     immutable context; never double-count a deal (BUG-088).
   - INV-007: historical experience immutable — rebuild-derived state from
     raw records, never patch totals.
   - INV-009: schema-controlled ordering; never pad/truncate a vector;
     `build_70d_vector` raises, it never silently repairs.
   - INV-010: settings (telegram, liquidity toggle, execution mode) persist
     via SettingsService ONLY — never direct live.yaml writes (BUG-080).
   - INV-011: broker truth precedence (history/reconciliation wins).
   - INV-012: UNKNOWN stays UNKNOWN — no fabricated classifications.
   - INV-015: live == replay == training determinism for features.
   - INV-016: worker status truthful — DISABLED when auto_train off.
   - INV-020: liquidity compute is info-only, new-bar cadence, no I/O.
   - No auto-promotion anywhere: Challenger -> Champion is operator-gated;
     SHADOW->CHAMPION is illegal; OOS failure => REJECTED.
   - Accounting: NO SYNTHETIC NUMBERS (None != 0.0); one-period policy
     (half-open UTC); one drawdown method; net PnL computed exactly once.
   - Hot path: never block the asyncio loop; everything heavy goes through
     `asyncio.to_thread` and is failure-isolated (learning can never stop
     protective execution).
   - Telegram credentials live in the secure store (DPAPI), never in
     live.yaml; token never logged (`_redact_secrets`).
7. **Repo hygiene for agents:** commit every coherent step with
   `<AGENT-NAME>: summary`; verify absorption (`git show <sha>:<file>`);
   re-add before commit; keep scratch/ probes named verb_what; run the
   beforePush gate (ruff check/format, mypy src, pytest tests/unit) via
   `.venv/Scripts/python.exe -m ...`.

### 7.2 Quick navigation map for a new agent

| Question | Read this first |
| :--- | :--- |
| How does the whole system work? | THIS FILE + `agents/skill.md` |
| What is the 70D tensor? | `features/schema_contract.py` |
| What are the 50 base features? | `features/scalp_features.py` (FEATURE_NAMES) |
| What are the liquidity features? | `features/liquidity_engine.py` (LIQUIDITY_FEATURE_DOC) |
| How do decisions flow? | `signals/policy.py`, `application/live_engine.py` |
| How is risk sized? | `risk/risk_engine.py` |
| How do positions exit? | `execution/order_manager.py` (PositionState, S-scenarios) |
| Where is truth stored? | `adapters/database/audit_repository.py`, `accounting/` |
| What do historical bugs teach? | `agents/bugs.md` |
| How to train/validate models? | `model_lifecycle/`, `model_generation/`, `research/` |
| How does the UI work? | `web/server.py`, `Web/index.html`, `Web/app.js` |

---

## 9. Dependency Graph

### 8.1 Module dependency graph (verified direction)

```text
Domain (frozen models)  <--  every module (read-only data contracts)
   ^
   |
Ports (IMT5Port / IGatewayPort)
   ^
   |  implemented by
Adapters (mt5 / paper / remote / database)
   ^
   |
LiveEngine (application)  -- orchestrates EVERYTHING below
   |-- features (50D base, regime, liquidity, schema_contract)
   |-- models (ScalpNet) + model_lifecycle (Champion management) + governance
   |-- signals (policy + rule_matrix) 
   |-- risk (RiskEngine)
   |-- execution (OrderManager)
   |-- experience (pre-trade gate + ledger)
   |-- intelligence (lifecycle tracker + autopsy + behavior + gate)
   |-- news (engine + gate + context)
   |-- candle_intelligence (close-quality gate)
   |-- accounting (AccountingCore + worker)
   |-- research (pipeline + worker + registry)
   |-- shadow (engine + worker + store)
   |-- observability (telegram notifier + logging)
   |-- settings (SettingsService + secure store)
   |-- hygiene (DB hygiene worker)
   |-- incidents (telemetry store)
   |-- training / labeling / model_generation  (offline, via to_thread)
   `-- web (FastAPI server) <-- Web/ frontend (REST + SSE + WS)
```

### 8.2 Dependency rules (enforced)

| Edge | Rule |
| :--- | :--- |
| research/intelligence/governance/news/shadow -> execution/risk/adapters | FORBIDDEN (no order authority; tested by safety suites) |
| web -> trading modules | READ-ONLY facades only (AccountingCore, stores); never compute trading truth in JS |
| live_engine -> heavy work | via `asyncio.to_thread` only, never inline in the tick pipeline |
| model_generation -> live model | NEVER writes Champion path; candidate/staging only |
| training -> tick loop | NEVER (offline worker) |
| UI -> settings | engine.mode / telegram / liquidity toggle all persist via SettingsService |
| telemetry -> trading | AuditRepository writes are queued; reads never on hot path |
| release/update -> everything | update runs quiesce protocol; LIVE blocks update unless --force maintenance |

### 8.3 Critical paths

```text
Live path:   MT5 -> adapter -> LiveEngine -> features -> model -> policy
             -> gates -> risk -> OrderManager -> broker -> ledger (queued)
Research:    ledger -> dataset -> labeler -> trainer -> gates -> registry
             -> shadow -> (operator) -> production
Learning:    outcomes -> experience -> intelligence -> accounting/reporting
             -> Telegram/UI (read-only consumers)
Telemetry:   every stage -> AuditRepository worker queue -> SQLite WAL
             -> purge (bounded) -> archive
```

---

## 10. Algorithm Explanation Standard

Every major algorithm in NSE, documented in the standard template:

### 9.1 Triple-Barrier Labeler (`labeling/triple_barrier.py`)

- **Algorithm Name:** Cost-Aware Purged Triple-Barrier Labeling
- **Purpose:** generate training labels for the 3-class decision head (NO_TRADE / BUY / SELL)
- **Problem Solved:** converts raw forward price paths into causally safe, imbalance-aware labels
- **Input:** OHLCV bars (Polars DataFrame), ATR, spread, profit/stop multipliers, max_holding_bars=15
- **Processing:** upper barrier = entry + ATR*profit_mult (BUY); lower = entry − ATR*stop_mult (SELL); horizontal horizon → NO_TRADE; spread friction applied to both barriers; stride-3 downsampling after NO_TRADE
- **Output:** labeled DataFrame (3 classes), purged/embargoed splits
- **Dependencies:** polars, numpy; training/ and model_generation/
- **Failure Cases:** class collapse (guarded: dominance ≤ 95%), non-finite labels → dataset rejection
- **Optimization Notes:** barrier multipliers are dynamic ATR-based; friction avoids optimistic labeling
- **Trading Impact:** the label taxonomy IS the model's decision space; label drift = model drift

### 9.2 Purged Walk-Forward Trainer (`training/walk_forward_trainer.py`)

- **Algorithm Name:** Temporal Walk-Forward with Purge/Embargo
- **Purpose:** honest out-of-sample model evaluation and online fine-tuning
- **Problem Solved:** lookahead leakage in time-series supervised learning
- **Input:** labeled bars, folds, feature schema (50D live / 70D candidates)
- **Processing:** fold i: train block → purge (drop samples whose barrier horizon overlaps validation) → validate → embargo (drop buffer) → fresh model per fold
- **Output:** per-fold metrics, trained model bundle + scaler bundle
- **Dependencies:** torch, polars, sklearn; FocalLossWithSmoothing / CE(class weights, WAIT=1.0)
- **Failure Cases:** NaN/Inf loss, exploding gradients (norm > 5) → FAILED (never CHALLENGER)
- **Optimization Notes:** online fine-tune on 300-record rolling buffer, quality gates before hot-swap
- **Trading Impact:** determines whether a candidate model ever reaches production

### 9.3 ScalpNet Dual-Path Network (`models/scalp_net.py`)

- **Algorithm Name:** ScalpNet (2D MLP snapshot + 3D causal TCN/self-attention)
- **Purpose:** map a feature snapshot (or sequence) to 4 decision logits
- **Problem Solved:** single-tick decisions AND temporal context in one architecture
- **Input:** (B, 50) snapshot or (B, seq, 50) / (B, seq, 70) sequences
- **Processing:** 2D path: Linear(50,128)→LayerNorm→GELU→Dropout(0.10)→residual; 3D path: causal Conv1d blocks, sinusoidal positional encoding, self-attention; head Linear(128,4)
- **Output:** (B, 4) logits: NO_TRADE/BUY/SELL/WAIT
- **Dependencies:** torch; features contract; scaler from bundle
- **Failure Cases:** width mismatch (contract violation, raises), warmup gate blocks inference
- **Optimization Notes:** inference under torch.inference_mode; one inference per tick (reused by position management)
- **Trading Impact:** every live decision starts here; 4th logit WAIT is policy-derived, never trained

### 9.4 Rule Matrix Engine (`signals/rule_matrix.py` + `rule_catalog.py`)

- **Algorithm Name:** DB-Driven 30+ Rule Matrix (32 catalogued rules: RULE_FVG_SNIPER_FILL … RULE_CONTRARIAN_RETAIL_TRAP)
- **Purpose:** configurable VETO/force layer over model probabilities
- **Problem Solved:** encode trader expertise as auditable, toggleable rules without retraining
- **Input:** signal context (regime, features, time, spread, news state, account state), trading_rules_config DB rows
- **Processing:** per-rule enablement from DB (5s TTL cache), rule evaluation in `_rule_evals_*` modules, VETO → NO_TRADE with reason
- **Output:** rule verdict / veto / force
- **Dependencies:** audit_repository (config), policy
- **Failure Cases:** fresh test DBs default all rules DISABLED (tests must enable); stale cache ≤ 5s TTL
- **Optimization Notes:** DB-driven, hot-reloadable; per-minute guard telemetry for high-frequency rejections (TICK_DUPLICATE_SUPPRESSED / ORDER_FREQUENCY_THROTTLED)
- **Trading Impact:** shapes which model signals convert to proposals

### 9.5 Signal Policy + SMC God Mode (`signals/policy.py`)

- **Algorithm Name:** Multi-Confluence Signal Policy (SMC God Mode)
- **Purpose:** convert model probs + regime + features into a TradeProposal
- **Problem Solved:** confluence filtering (BOS/CHoCH confirmation, 50% impulse equilibrium, liquidity sweep piercing) and pending-order discipline
- **Input:** probabilities, tick, feature vector, regime, survival mode, order_manager (for pending state)
- **Processing:** class routing → regime guardian → rule matrix → SMC confluence → proposal construction
- **Output:** TradeProposal (action BUY/SELL/BUY_LIMIT/SELL_LIMIT/NO_TRADE, confidence, SL/TP, RRR)
- **Dependencies:** RuleMatrixEngine, regime classifier, features, domain models
- **Failure Cases:** pending lock (30s, 1.0xATR drift) → PENDING_ORDER_LOCKED; guardian regimes → BLOCKED_BY_GUARDIAN
- **Optimization Notes:** AlgoConfig hot-swap per tick; survival mode override
- **Trading Impact:** the last software gate before risk/execution

### 9.6 Risk Engine (`risk/risk_engine.py`)

- **Algorithm Name:** Dynamic Volume Sizing (fixed-dollar risk with tier + margin clamps)
- **Purpose:** size every trade to bounded dollar risk
- **Problem Solved:** uniform risk per trade regardless of SL distance; capital ceilings
- **Input:** entry, sl, account (equity/margin_free/leverage), symbol_info (contract_size/volume_step/volume_min/volume_max), risk_pct
- **Processing:** risk_amount = equity × pct → volume = risk/SL-distance/contract → floor to step → tier ceiling → margin clamp (≤10% free margin default)
- **Output:** (volume_lots, reason_code)
- **Dependencies:** domain models; OrderManager enforces HARD_MAX_LOTS additionally
- **Failure Cases:** NaN/Inf/None inputs → 0.0 + reason; margin insufficient → 0.0; invalid pricing → 0.0
- **Optimization Notes:** monotonic tier ceilings; FP-safe flooring (eps 1e-9)
- **Trading Impact:** the quantitative expression of "risk X% per trade"

### 9.7 Order Lifecycle Manager (`execution/order_manager.py`)

- **Algorithm Name:** 11-State Position Machine + hold_score + S-scenario exits
- **Purpose:** manage every position from proposal to close with protective discipline
- **Problem Solved:** consistent exit behavior (breakeven, trailing, giveback, recovery, bailout) under all market conditions
- **Input:** tick, positions, feature vector, probs, regime, account
- **Processing:** per-tick state evaluation (500ms hold_score), protective SL/TP modifies, AI-flip/regime-flip exits, split-fill sibling sync, pending-cancel verification, missed-close reconciliation
- **Output:** state transitions, modification dispatches, close dispatches, ledger/experience writes
- **Dependencies:** IMT5Port, RiskEngine (optional clamp), AuditRepository (queued), intelligence lifecycle tracker, TelegramNotifier
- **Failure Cases:** broker rejections (circuit breaker), unresolved cancels (exposure slot stays locked), reconciliation dedup
- **Optimization Notes:** tick-time-derived ages (never host wall clock — BUG-058 family); convex drawdown penalty (BUG-013)
- **Trading Impact:** realizes (or destroys) the model's edge — the most safety-critical module

### 9.8 Liquidity Engine (`features/liquidity_engine.py`)

- **Algorithm Name:** Pure-Causal Multi-Timeframe Liquidity Pool Detection (10D)
- **Purpose:** encode where institutional liquidity rests (BSL/SSL/EQH/EQL/HTF) into the tensor
- **Problem Solved:** SMC liquidity intuition made deterministic, causal and comparable across live/training/replay
- **Input:** M1 bars (completed, ≤4000), mid price, ATR, decision_at timestamp
- **Processing:** fractal swing detection (±5 confirm) → pool lifecycle CANDIDATE→CONFIRMED→(usable at usable_at≤decision) → BSL/SSL distances, EQH/EQL strengths (volatility-aware tolerance clustering), HTF evidence (H1/H4/D1 completed buckets), internal/external distances, confluence clustering, reactive sweep + post-sweep displacement
- **Output:** LiquidityFeatures (10 floats, finite, [-3,+3]) + pools for UI overlays
- **Dependencies:** numpy; LiquidityGovernor (runtime state); schema_contract
- **Failure Cases:** missing evidence → contract defaults (distances 3.0 / strengths 0.0); disable → governor UNAVAILABLE, vector builders raise (INV-009)
- **Optimization Notes:** O(n·window) swing detection (~44ms @900 bars, ~460ms @3500); bounded by 4000-bar cap
- **Trading Impact:** never a trade signal by itself — context for sweep/reversal inference

### 9.9 News Engine + Gate (`news/`)

- **Algorithm Name:** Multi-Source News Intelligence with Bounded Decision Gate
- **Purpose:** contextualize decisions with economic/geopolitical news without letting news decide
- **Problem Solved:** dedup/syndication, relevance scoring, decay, honest LOCAL-only operation
- **Input:** RSS + official source feeds (Tier-1 official: Fed/BLS/BEA/ECB/BoE/CFTC/Treasury; Tier-2 Reuters/MarketWatch; Tier-3 ForexLive/ZeroHedge)
- **Processing:** fetch (rate-limit/backoff/jitter/source-health) → dedup (article_hash + normalized-title + 60s publication bucket + 1h syndication merge) → local analysis (entities/topics/XAUUSD+USD relevance/direction/importance) → optional external AI (HYBRID, fallback LOCAL) → decay (BREAKING/MACRO/POLICY/STRUCTURAL) → NewsGate (alignment ≤ +0.05 boost, conflict ≤ −0.10 penalty, CAUTION on high-impact/conflicted, position protection never gated)
- **Output:** current news context (cached), news analysis rows, gate verdicts
- **Dependencies:** news.db (13 tables), httpx, external AI interface (optional)
- **Failure Cases:** API key missing → LOCAL_ONLY; provider failure → LOCAL fallback (never fabricate); news subsystem disabled → available=False, no-op gate
- **Optimization Notes:** worker-refreshed context; tick path reads cache only (zero per-tick DB)
- **Trading Impact:** bounded confidence adjustment only — the action/direction is never changed

### 9.10 Research Pipeline (`research/pipeline.py`)

- **Algorithm Name:** Evidence-Driven Strategy Validation Pipeline
- **Purpose:** discover, backtest, validate and registry strategies with hard OOS gate
- **Problem Solved:** prevent overfit strategies from ever reaching live trading
- **Input:** experience ledger, strategy candidates (builtin + discovered)
- **Processing:** dataset → split (temporal, purge/embargo) → leakage checks → backtest (friction-aware) → walk-forward → OOS gate → robustness stress → multi-dim score (small-sample protection) → lifecycle (DISCOVERED…VALIDATED/SHADOW/ACTIVE, REJECTED) → registry
- **Output:** strategy_registry rows, research_runs, scores
- **Dependencies:** audit.db, research/ package; NO order authority
- **Failure Cases:** OOS failure ⇒ REJECTED regardless of win rate; candidate NEVER auto-live
- **Optimization Notes:** content-addressed candidates (deterministic versioning); seed step preserves existing validation results
- **Trading Impact:** decides which strategies the system may ever deploy

---

## 11. Documentation Quality Rules (maintenance contract)

This file must stay:

- **Extremely detailed** — every module, algorithm and data flow above is
  grounded in executable code or `agents/skill.md`, NOT imagined.
- **Clear for senior engineers** — exact file paths, constants, invariants.
- **Clear for AI agents** — section 8 (Agent Mental Model) is the entry point;
  the graphs are ASCII so any tool can parse them.
- **Free of unnecessary complexity** — each node explains why it exists.
- **Based on actual repository architecture** — verified 2026-08-20 against:
  `src/nexus_scalp/{features/schema_contract.py, features/scalp_features.py,
  features/liquidity_engine.py, features/liquidity_runtime.py,
  risk/risk_engine.py, execution/order_manager.py, application/live_engine.py,
  signals/rule_catalog.py, news/{models,gate}.py, model_generation/models.py,
  docs/architecture/dependency-map.md}`.

**Update rules:**

1. When the 70D geometry changes → update section 1.4 + the schema hash; the
   hash is the contract fingerprint.
2. When a new phase/subsystem lands → add it to the layer map (section 0),
   master graph (1), algorithm standard (9) and dependency graph (8) in the
   SAME commit as the code.
3. When a bug changes a number (e.g. tier caps, gate floors) → update the
   number here with a `(BUG-NNN)` note; code wins over older docs.
4. Do NOT delete sections; annotate obsolete claims with the deprecation note
   (like the 350D declaration and the 0.50/2.00 tier table above).
5. Cross-check against `agents/skill.md` on every major feature change.
6. Keep graphs ASCII; never rely on images.

---

## 12. Credits & Maintenance

- **Canonical architecture maps:** `agents/skill.md` (forensic badges, §1-§20) + `Agent/skill.md` (concise alias + upgrade notes).
- **News Intelligence:** `Agent/PROJECT_GRAPH.md §1.9/§1.10/§2.8/§3.5b` + `src/nexus_scalp/news/ai_service.py` + `src/nexus_scalp/web/news_intelligence_routes.py`.
- **Forensic Incident Center:** `Web/forensic_console.js` + `Agent/PROJECT_GRAPH.md §3.5b`.
- **Canonical architecture map:** `agents/skill.md` (forensic badges, §1-§20).
- **Bug forensics:** `agents/bugs.md` — append (never rewrite) after real bugs.
- **Invariants:** `agents/runtime_invariants.md` — INV-001..021.
- **Contracts:** `agents/contracts.md` — additive only.
- **Change/task/state registries:** `agents/change_control.md` (CHG-NNNN),
  `agents/taskboard.md` (TASK-NNN, claim before starting), 
  `agents/repository_state.md`, `agents/locks.yaml`, `agents/decisions/`.
- **Dependency map:** `docs/architecture/dependency-map.md`.
- **Companion agent files:** `Agent/AGENT_REASONING_PROTOCOL.md` (operating
  manual), `Agent/ARCHITECTURE_CONTRACT.md` (the laws).
- When any graph in this file disagrees with `agents/skill.md` or the code,
  the CODE wins; update this file and classify the claim with the forensic
  badge system from skill.md §2.
---


## Appendix A. Verification record

| Check | Result |
| :--- | :--- |
| Every major module represented (domain/ports/adapters/features/models/training/signals/risk/execution/application/web/observability/experience/intelligence/research/model_lifecycle/governance/shadow/news/accounting/settings/release/hygiene/incidents/strategies/candle_intelligence) | PASS — layer map section 1 + dependency graph section 9 |
| Every important algorithm explained in standard template | PASS — 10 algorithms in section 10 (labeler, walk-forward trainer, ScalpNet, rule matrix, policy/SMC, risk, order manager, liquidity engine, news gate, research pipeline) |
| Data flows understandable end-to-end | PASS — master graph 1 + execution 6 + learning 7 |
| A new AI agent can navigate the project using only this file | PASS — section 8 (agent mental model + navigation table) |
| Reviewed against agents/skill.md | PASS — every claim cross-checked; discrepancies resolved CODE-wins (tier caps 0.10/1.00 vs docs 0.50/2.00; 11-state taxonomy PROFIT_*/LOSS_*; RSI divisor 16.66) |
| All 70 dimensions documented (50 base + 10 news + 10 liquidity) | PASS — section 1.4b (50+10+10) |
| Constants verified from code | HARD_MAX_LOTS=10.0, MAX_TOTAL_EXPOSURE=1, tier caps, S01-S13, 32 rules, 4000-bar cap, ATR period default, hold-score formula, news gate bounds ±0.05/0.10, 3-class labels vs 4-logit head, warmup gate, circuit breaker (3 rejections), pending lock 30s/1.0xATR |
