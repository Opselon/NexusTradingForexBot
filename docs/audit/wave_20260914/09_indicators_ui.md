# Lane 09 — Indicator / UI Canonicality Audit

- Wave: `wave_20260914` · Lane 09 (UI indicator intelligence) · Agent: Hermes lane-09
- Base: `9431edd2` (nse/master-active-scalper-wave @ origin tip at dispatch). HEAD moved to
  `44e198ad` mid-session by parallel lanes — `git diff --stat 9431edd2 HEAD -- src web Web frontend tests`
  is **empty**, so every code finding below holds byte-identically at both SHAs.
- Method: static trace (imports/grep) + executed live probes against `create_app()` TestClient and the
  indicator/feature calculators on the repo `.venv` (commands in Appendix A). Items marked **VERIFIED**
  were executed this session; items marked **READ** are static file:line evidence.

---

## 1. Canonical indicator engine (backend)

**READ** `src/nexus_scalp/indicators/` (1513 LOC):

| File | Role |
|---|---|
| `calculators.py` | Pure math: RSI (Wilder, `rsi_series` L45), Stoch %K, CCI, ADX, AO, Momentum, MACD, StochRSI, Williams %R, BBP, UO; EMA/SMA/WMA-seeded MAs; 5 pivot families |
| `xauusd_spec.py` | TradingView-convention spec math (H/L-aware variants, TV votes) per `agents/xauusd_indicator_math.md` §A–D |
| `resample.py` | M1→M5…MN1 OHLCV bucketing (`_TF_MINUTES` L13, raise on unknown tf L49) |
| `service.py` | `IndicatorService.snapshot()` — 11 oscillators + 15 MAs + 7×5 pivot matrix + 3 gauges + summary counts; `to_api_dict()` L374 |
| `ports.py` | `IndicatorResult(name, value, action)` — API row shape is exactly `{name, value, action}` (`as_api_dict` L41) |

Only **one** consumer of this package exists in the whole backend:
`src/nexus_scalp/web/api_v1/indicators.py:104` (`from nexus_scalp.indicators.service import IndicatorService`).
**VERIFIED**: `grep -rn "nexus_scalp.indicators" src/nexus_scalp --include=*.py | grep -v "^src/nexus_scalp/indicators"` → that single line.
No trading module (policy, rules, risk, execution, features, setup detector) imports it.

## 2. Does the 50D feature base include indicators? Yes — via *separate* math

`src/nexus_scalp/features/scalp_features.py` computes its own overlapping indicator set inside
`ScalpFeatureEngine.compute_from_bars`:

- ATR-14: simple mean of TR over 14 bars (L578–586), floor 0.20 (`safe_atr` L587)
- **RSI-14**: `np.diff(closes[-15:])` **simple-average** gains/losses (L760–765) — not Wilder
- EMA-21/50: `_compute_ema` seeded at `prices[0]` over the *truncated* window (L529–538, call L767–769)
- Ichimoku tenkan/kijun/senkou A/B, TK cross (L740–756)
- Cross-asset Z-score = 20-bar price z-score (L773–778)

`FEATURE_NAMES` 50-tuple (L164–215) includes `norm_rsi` (feat_34), `dist_to_ema_21/50` (35/36),
`norm_tk_diff`, `tk_cross_signal`, `kumo_sig`, `norm_kumo_width` (30–33, 38–39), HTF trend/momentum
(40–43). Guard L221 pins the tuple to the active schema dimension. 70D assembly
(`features70.py`, `schema_contract.py`) reuses the same 50 base + news/liquidity 20D — no fields are
sourced from `indicators/`.

**Numerically VERIFIED divergence** (Appendix A.3): on one 500-close synthetic series —
Wilder RSI14 (UI engine) = **48.4927** vs feature-engine RSI14 = **51.1436**; EMA21 = 3328.368 vs
3328.810; EMA50 = 3329.119 vs 3328.751. Same names, different numbers.

## 3. API surface exposing indicators

**READ** `src/nexus_scalp/web/api_v1/indicators.py` (mounted via `api_v1_wiring.py:45,69`):
`GET /api/v1/indicators{,/oscillators,/moving-averages,/pivots,/gauges,/summary}` — always
recompute the *full* snapshot from `engine.aggregator.get_completed_bars()[-limit:]` (L77) and slice;
`source_bar_count` appended L109. Bars come from the live engine only; no engine → 503
`ENGINE_UNAVAILABLE` (**VERIFIED** A.4, and route never fabricates synthetic bars — header L11–12).
SSE snapshot carries *no* indicator block: `radar` (server.py:1618) is a pure passthrough of
`LiveEngine._last_market_radar`, and the 50/70D `features` payload is built from canonical names
(server.py:1207–1226). So indicators reach the UIs **only** through the six v1 routes above.

## 4. Legacy `Web/` dashboard — value provenance

| Surface | Source | Classification |
|---|---|---|
| Technicals widget (`tv_widget.js`, embedded `index.html:245`, standalone `tv_widget.html`) | `GET /api/v1/indicators?timeframe=TF&limit=20000` (L303), TF buttons L338+, adaptive poll 30 s ok / 10 s error (L363–366) | **Backend-sourced**; OK/STALE/ERROR driven only by real fetch outcomes; distribution-bar widths & gauge arcs are pure display transforms of backend counts (`drawCycle`/`seg`); stale keeps `lastGood`, never fabricates |
| Feature matrix (`app.js` ~L520, L677–687, L239–364) | SSE/`get_system_state` `features` array + backend `feature_names` | **Backend-sourced** (`atr` shown at L4014–4018 from snapshot field — see D6 for the semantic problem) |
| Market Radar (app.js:9995–10050) | `snapshot.radar` verbatim; "WE NEVER recompute" (L9996–9997) | **Backend-sourced** (setup_detector over 50D features, `bar_handler.py:121–165`) |
| `FEATURE_NAMES_JS` (app.js:31–133) | hand-maintained mirror of backend tuple | **Stale risk** — currently byte-identical (**VERIFIED** A.2: `identical_order True`), referenced exactly once (its own definition; runtime paths use backend-sent names), untested, ungated |
| Command-center / replay / liquidity panels | respective `/api/*` routes | out of indicator scope |

No mock/stale/hardcoded indicator payload exists in `Web/` (**VERIFIED** by grep sweep for
mock/dummy/hardcoded+indicator terms — only honest "no data yet" placeholders).

## 5. React `frontend/` (alt UI, parity wave1 #186 = `f5fe5738`, VERIFIED ancestor of HEAD)

- Read model: `api/indicatorsApi.ts` (all six v1 routes; header pins "render what arrives, never
  fill gaps client-side") and `features/ai-analysis/api.ts:65–78` (summary/gauges/osc/ma/pivots).
- Consumed by: `features/ai-analysis/ui/AiAnalysisPage.tsx` — the **only** indicator wall
  (`IndicatorWallPanel` L414–454 renders backend `{name, value, action}` rows verbatim; gauges L307–335;
  pivots L339–357; TF list L34 `["M1","M5","M15","H1","H4","D1"]`).
- Dashboard "ATR" MetricCard (`pages/Dashboard/DashboardPage.tsx:294`) reads `snapshot.atr` — same
  mis-scaled value as legacy (D6), relabeled "ATR" with the "M1" hint dropped.
- **No client-side indicator math exists** in `frontend/src` (**VERIFIED**: grep for rsi/ema/macd/atr
  computation patterns → only display helpers; no `vitest`/test files at all: 0 `*.test.*` under
  `frontend/`, no test script in `frontend/package.json`).
- `radar` typed in `types/domain.ts:243` but **never rendered** by any React page (VERIFIED grep) →
  legacy-only parity gap for setup intelligence.
- `indicatorsApi.ts` (full-snapshot client) is exported but unused by any page — the pages use the
  ai-analysis sub-route queries instead.

## 6. Does the setup/policy engine consume the canonical indicator state the UI shows? **No.**

Import trace (**VERIFIED** by grep, Appendix A.1):

- `signals/policy.py:19–30` imports `AlgoConfig`, `regime_classifier`, `FeatureVector`, `RuleMatrixEngine` — never `indicators`.
- `signals/rule_matrix.py:14–19` — same; rule inputs are `FeatureVector` + regime + probs.
- `model_generation/setup_detector.py` (the radar engine feeding UI `radar`) consumes the **50D feature
  record rows** (`_sig(row,"norm_rsi",34)` L460 etc.), not IndicatorService.
- `execution/`, `risk/`, `application/` — zero `indicators` imports.

So the UI indicator wall and the trading brain are **two parallel computations over the same candles**:
the widget's 26 votes / 35 pivot levels never reach policy; the model's `norm_rsi`/EMA-distance features
are computed with *different formulas* (§2) than the displayed RSI/EMA the operator trusts. The one
shared canonical piece is the raw completed-bar window (both read `engine.aggregator`-fed data), and
`algo_config` knobs surfaced in the snapshot are genuinely consumed (atr_sl_buffer → policy.py:593,614,1293;
min_risk_reward → risk_engine.py:428; ai_zone_confidence → policy.py:706; fvg_mitigation_sensitivity →
scalp_features.py:699; order_block_lookback → scalp_features.py:928).

## 7. Divergences (file:line)

| # | Sev | Divergence | Evidence |
|---|---|---|---|
| D1 | P2 | **Williams %R violates its own spec's canonical display scale.** Spec (`agents/xauusd_indicator_math.md` §9, L101–106) pins −100..0 with Sell>−20/Buy<−80; service emits `abs(v_willr)` (0..100, `service.py:291–295`) and votes by TV *slope* (`xauusd_spec.williams_vote_tv` L418–421). The spec-conformant vote `williams_r_action_spec` (L181) exists but is **dead code** (VERIFIED: only the def matches). CI test deliberately accepts both shapes and calls one branch "engine-stale" (`tests/js/xauusd_indicator_math.test.js:120–146`). | READ + grep |
| D2 | P2 | **ADX displays a closes-only approximation while voting on an H/L DI computation** — value from `calc.adx(closes,14)` ("directional movement approximated from close deltas", `calculators.py:117–124`) vs action from `spec.adx_signal_from_bars(rows)` (`service.py:250–267`); short-window fallback maps non-directional ADX≥50 → **"Buy"** (`calculators.py:417–426`), polluting the summary vote counts with a strength-only signal. | READ |
| D3 | P1 | **500 crash on legal-looking timeframes.** `_ALLOWED_TFS` admits `1d/1w/1h` (lowercase) and `1D`-case-insensitively (L31–59), but `_normalize_tf` alias map (`service.py:91–113`) only covers lowercase + long forms; `"1D"` falls through `tf.upper()` → `"1D"` → `BarResampler` raises `ValueError: unsupported timeframe: 1D` (`resample.py:49`) → HTTP 500. **VERIFIED** (A.4): `1D/1W/1H → 500 INTERNAL_ERROR`, `1d/1w/1h → 200`, `bogus → 422`. Both UIs dodge it only because their button lists are curated. Dead `_VALID_TFS` set (`service.py:63–83`) shows a validation pass that never landed. | VERIFIED |
| D4 | P1 | **Degenerate pivot fabrication.** `service._pivots` injects a magic price `4430.0` when the window is empty (`service.py:349–353`), directly contradicting the API's contract comment "Never fabricates indicator values: if insufficient bars, value=None" (`api_v1/indicators.py:18`) — **VERIFIED** (A.5): zero bars → HTTP 200 with full 7×5 pivot matrix = 4430.0 (a plausible-looking XAU level), while oscillators/MAs correctly return `value: null`/Neutral. A cold engine shows operators fake support/resistance. | VERIFIED |
| D5 | P1→latent-P0 | **React pivot table cannot render the backend shape; whole-app crash when wave1 ships.** Backend `pivots.rows` is a **dict keyed by level** (VERIFIED A.5: `rows= dict`, `{"R3": {"Classic": …}}`); `AiAnalysisPage.tsx:346` calls `(pivots.rows ?? []).slice(0,20)` on it → `TypeError: rows.slice is not a function` (VERIFIED node repro, A.6) during render; no `ErrorBoundary` exists anywhere in `frontend/src` (VERIFIED grep) → unmounts the entire /alt tree, not just the panel. The DTOs are wrong twice over: `types/features.ts:34–38` declares `levels: Record<…> + rows: string[]` (inverted vs reality) and `ai-analysis/model.ts:121` declares `rows?: Row[]`; also `IndicatorReading.signal?/kind?/label?/params?` (`types/features.ts:24–31`) are phantom fields — backend rows only carry `name/value/action` (`ports.py:41–44`). Currently masked by D12 (stale bundle). | VERIFIED |
| D6 | P1 | **"ATR" means three different things across the stack.** SSE/REST `snapshot.atr` = `reg_state.realized_volatility_5m` — a 5-min realized-vol fraction, calibrated p50≈0.00062…p99≈0.0025 (`server.py:975`, `regime_classifier.py:140–149`), while `FeatureVector.atr_m1` = 14-bar ATR in USD (`scalp_features.py:578–586`) and the decision ledger logs *that* one (`decision_executor.py:338`). Legacy labels the rv value **"ATR (M1)"** (`index.html:502`, written by `app.js:4014–4018` — the `<0.1 → 6-decimal` formatting quirk is the tell) and React shows it as "ATR" (`DashboardPage.tsx:294`). Worse, the *backend* risk-plan default consumes it as ATR: `diagnostics_state_routes.py:839,849` `atr = state.get("atr") or 1.5; plan_sl = plan_entry - atr*1.5` → with rv≈0.001 the operator-facing suggested stop sits ~0.002 USD from entry (then feeds `calculate_volume` L868), i.e. the accounting panel's "RiskEngine single source" plan silently mixes two vol scales. | VERIFIED (values/lines) |
| D7 | P2 | **Rule-engine parameters are UI theater.** Both UIs render per-rule editable parameter forms persisted through `POST /api/rules/toggle` (`diagnostics_state_routes.py:559+`, Web `app.js:6470+`, React `rules/api.ts:43`); the cache loads `parameters` JSON (`rule_matrix.py:47,62–67`) — and **no rule evaluation ever reads it**: only `is_enabled` is used; the two rules that fetch `get_params` (L76, L227) never touch the dict (VERIFIED: `grep 'params\.'` → 0 hits). Consequences: seeded `rsi_threshold:85.0`, `bb_period:20/bb_std_dev:2.0`, `std_dev_threshold:3.5`, `gap_pip`, `squeeze_minute` (seed rows `audit_repository.py:3425–3450`) are decorative; thresholds are hardcoded literals (L230 velocity≥15, L360 z≥3.5, L461 rsi>85, per-rule SL/TP 1.5/2.5 … — 16 hardcoded `stop_loss=round(...)` blocks). | VERIFIED |
| D8 | P1 | **`RULE_CONTRARIAN_RETAIL_TRAP` is dead-by-attribute-name.** `rule_matrix.py:460` reads `fv.rsi_m15`; **`FeatureVector` has no `rsi_m15` field** — `getattr(fv,"rsi_m15",50.0) if hasattr(fv,"rsi_m15") else 50.0` pins the value to 50.0 forever (**VERIFIED** A.3: `'rsi_m15' in model_fields → False`, real field `rsi_14`). The >85/<15 branches are unreachable, even though the rule is seeded, listed in both rule UIs, and its reason code is whitelisted in the audit repo (`audit_repository.py:3446`). | VERIFIED |
| D9 | P2 | **Cosmetic indicator names in rules:** `RULE_VWAP_ELASTIC_BAND` uses a 20-bar price z-score, no VWAP anywhere (`rule_matrix.py:355–362`); `RULE_BOLLINGER_BURST_FADE` uses `is_at_extreme_high/low` 50-bar percentile flags, no Bollinger bands exist in the engine (`rule_matrix.py:389–425`; `grep -i bollinger src/nexus_scalp/features src/nexus_scalp/indicators` → none). Operators toggling "Bollinger"/"VWAP" rules are trading different math. | VERIFIED |
| D10 | P2 | **UI-indicator vs model-feature numeric divergence for the same names** (RSI/EMA/Ichimoku computed twice, §2): the widget's "RSI(14)" (Wilder, TV thresholds) ≠ feat_34 `norm_rsi` (simple-mean 15-window) ≠ model input. Also UI "Ichimoku Base Line" is kijun-only (`xauusd_spec.py:344–350`) vs full tenkan/kijun/kumo in features (`scalp_features.py:740–756`). A "Buy" needle on the widget can coexist with `norm_rsi` the model reads as mid-range; nothing reconciles them. **VERIFIED** numerically A.3. | VERIFIED |
| D11 | P3 | `FEATURE_NAMES_JS` mirror (`Web/app.js:31–133`) unreferenced (VERIFIED: 1 hit = definition) and unpinned by any test; today order-identical to backend (VERIFIED A.2) — drift bait only. | VERIFIED |
| D12 | P1 | **The /alt bundle being served is older than parity wave1.** `frontend/dist` (gitignored, VERIFIED) built 09-13 06:16, while `f5fe5738` (#186) merged 09-13 20:45 (**VERIFIED** mtimes vs commit date); bundle contains zero `api/v1/indicators`/`ai-analysis` strings (VERIFIED grep) and only the 7 legacy routes → /alt today exposes **no indicator wall at all**, and D5 is latent, not visible. Any rebuild (or CI `tsc && vite build`) activates the crash path. | VERIFIED |
| D13 | P3 | Minor envelope drift: `source_bar_count` is appended post-serialization (`api_v1/indicators.py:109`) — React type has it optional (`features.ts:58`), but the `/oscillators|/pivots|/gauges|/summary` slices drop it (L136–141 etc.), so a React panel mixing sub-routes can't report bar-window honesty uniformly. | READ |

## 8. Indicator underutilization (J)

- **The entire canonical indicator engine is display-only**: 11 oscillator votes, 15 MA votes,
  3 gauges, 7×5 pivot matrix → zero imports from `signals/`, `strategies/`, `risk/`, `execution/`,
  `application/`, `model_generation/` (VERIFIED §1, §6). Nothing in the funnel keys off overbought
  RSI, MA cross, or pivot rejection that the operator can see.
- 70D additions (`schema_contract.py` base families incl. `bsl_distance_atr`/`ssl_distance_atr`) are
  model-fed but never surfaced in the indicator wall — the reverse direction is clean though (feature
  routes expose them: `/api/v1/features/{contract,groups,current,status}`; note the **legacy-only**
  gap of `radar` in React, §5).
- Rule parameters fully ignored by evaluation (D7); one rule permanently dead (D8).
- `get_system_state().atr` (rv_5m) is the *only* volatility number the trading surfaces show, and it
  is mislabeled and misused as ATR (D6); the real `atr_m1` that policy sizes stops with
  (`policy.py:282–283` `raw_atr = getattr(feature_vector,"atr_m1",1.50)`) is never displayed as such.

## 9. Tests — indicator parity

| Layer | State | Verdict |
|---|---|---|
| Python unit | **Zero** tests import `nexus_scalp.indicators` or call `/api/v1/indicators*` (VERIFIED: `grep -rln 'indicators' tests --include=*.py` → none; `grep rsi tests/unit` → none indicator-related) | **FAIL — no canonical engine test coverage** |
| JS shape suite | `tests/js/xauusd_indicator_math.test.js` (tracked) pins 11/15/7 shape, vote vocabulary, §D bucket self-consistency, DM gap pattern, gauge-angle mapping — but **only against a live engine on :8080**, skipping cleanly when down. **VERIFIED**: ran it on this host → 31/31 "pass" via the engine-skip path in 42 ms — in CI (no engine, `js-tests.yml:64–72` glob `tests/js/*.test.js`) it contributes **zero real assertion signal**; it never compares values to TradingView goldens and never touches the features-vs-indicators math gap | PARTIAL/skips-everywhere |
| JS widget suite | `tests/js/tv_widget_redesign.test.js` pins widget render-state honesty + DOM contract + endpoint string | PASS (read-model integrity for legacy widget) |
| React | 0 test files, no test runner wired (`frontend/package.json` scripts: dev/build/preview/typecheck only) | FAIL — D5 shipped uncaught because nothing renders it |
| Route-parity gates | `test_alt_ui_route_parity.py` / `test_alt_ui_snapshot_field_coverage.py` exist **only on `origin/feat/alt-ui-pro-ux`** (VERIFIED: absent in HEAD, `git cat-file`), and their cache file `tests/js/coverage_ledger.json` is **gitignored** (`.gitignore:146 coverage*.json`, VERIFIED `git check-ignore`; the branch's `.gitignore` has no negation) — the indicators route list lives in that untracked local file only | NOT ANCESTRAL — gate unmerged & ledger uncommittable |

**Net: there is no indicator-parity test anywhere** — neither backend-snapshot vs UI, nor
indicators-engine vs features-engine formulas, nor React render-vs-backend-shape.

## 10. Recommended next tasks (for the integration owner; nothing changed outside this file)

1. **T-09.1 (P1)**: normalize-then-validate timeframes: map `1D→D1`, `1W→W1`, `1H→H1` in
   `_normalize_tf` (or validate post-normalization against `resample._TF_MINUTES` keys) and add a
   parametrized API test for every advertised alias. File: `indicators/service.py:86–113`,
   `api_v1/indicators.py:31–66`.
2. **T-09.2 (P1)**: fail pivots honestly on empty/degenerate windows (`value None` matrix) instead of
   the 4430.0 literal — `indicators/service.py:349–353` — matching the "never fabricate" contract.
3. **T-09.3 (P1)**: React pivots: consume `rows` as `Record<level, Record<column, number|null>>`
   (fix `types/features.ts:34–38`, `ai-analysis/model.ts:121`, `AiAnalysisPage.tsx:345–357`), delete the
   phantom `signal/kind/label/params` fields, and add a top-level `ErrorBoundary` to `/alt`; rebuild dist.
4. **T-09.4 (P1)**: rename/fix the volatility label truth: expose `atr_m1` (e.g. `snapshot.atr_m1`)
   separately from `realized_volatility_5m`, relabel both UIs, and make the accounting risk-plan default
   read the same field policy sizes with (`diagnostics_state_routes.py:839`).
5. **T-09.5 (P1)**: D8 one-line bug (`rsi_m15`→`rsi_14` — then governance-debate whether the rule should
   use M1 RSI at all) + rule-parameter wiring or explicit UI demotion (read-only display) — D7.
6. **T-09.6 (P2)**: a real parity suite: golden-series tests for `indicators/calculators` (Wilder RSI,
   TV votes) **and** a cross-check pinning known deltas between indicators-engine and features-engine
   math (or converge the features to canonical Wilder via a governed model retrain), executed in CI
   without a live engine (TestClient + seeded bars — the probes in Appendix A are the template).
7. **T-09.7 (P2)**: decide the J-question explicitly: either feed a curated indicator subset (e.g.
   ADX strength, pivot rejection) into policy gates via the existing `algo_config`-style contract, or
   document the wall as pure display and stop presenting it as "signal" next to model probabilities.

---

## Appendix A — executed evidence (this session)

- **A.1** `grep -rn "nexus_scalp.indicators" src/nexus_scalp --include=*.py | grep -v "^src/nexus_scalp/indicators"` → 1 hit (`web/api_v1/indicators.py:104`).
- **A.2** Temp-script diff of `Web/app.js` `FEATURE_NAMES_JS` vs `scalp_features.FEATURE_NAMES`: `js_count 50 py_count 50 identical_order True`.
- **A.3** `./.venv/Scripts/python.exe` probes: `'rsi_m15' in FeatureVector.model_fields → False` (`rsi_14 → True`); RSI/EMA dual-math table (§2); empty-window snapshot → `P row all columns = 4430.0`, `bar_count 0, last_close None`.
- **A.4** TestClient (`NSE_WEB_AUTH_DISABLE=1`, fake aggregator with 60 synthetic M1 bars): tf status table → `M1 200 · 1d 200 · 1D 500 · D1 200 · 1w 200 · 1W 500 · MN1 200 · 2h 200 · 1h 200 · 1H 500 · bogus 422`; no-engine → 503 `ENGINE_UNAVAILABLE`.
- **A.5** Same harness: `pivots.types: levels=list rows=dict columns=list` (dict-of-levels proof for D5); empty-window API → 200 with 4430.0 pivots + summary `{'Sell':0,'Neutral':26,'Buy':0}`.
- **A.6** `node -e "({R3:{}}).slice(0,20)"` → `TypeError: rows.slice is not a function`; `grep -rn "ErrorBoundary\|componentDidCatch" frontend/src` → none.
- **A.7** `node tests/js/xauusd_indicator_math.test.js` → 31 tests, 0 fail, all engine-skip path (42 ms). `node tests/js/pro_risk_viz.test.mjs` → 21 pass (not in CI glob `*.test.js`, `js-tests.yml:65`).
- **A.8** `git cat-file -e HEAD:tests/unit/test_alt_ui_route_parity.py` → MISSING; same on `origin/feat/alt-ui-pro-ux` → present; `git check-ignore -v tests/js/coverage_ledger.json` → `.gitignore:146`. `frontend/dist` gitignored; bundle mtime 09-13 06:16 < `f5fe5738` 09-13 20:45; `grep -c "api/v1/indicators" frontend/dist/assets/index-*.js` → 0.
- Scratch probes live under `%TEMP%\nse_lane09\` (outside the repo); no repo file other than this report was created or modified.
