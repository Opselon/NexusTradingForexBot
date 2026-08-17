# FORENSIC ENGINEERING REPORT — Frontend + Backend Live-System Repair

**Date:** 2026-08-17
**Scope:** Phase 14 forensic repair — UI state recovery, AI-visible market visualization,
canonical live-state contract, no-synthetic-data enforcement.
**Contract anchor:** `agents/skill.md`, `agents/bugs.md` (BUG-020 lineage).

---

## ROOT CAUSES

### RC1 — Server served synthetic market data as live telemetry
- **file:** `src/nexus_scalp/web/server.py`
- **function:** `get_system_state()` (canonical dashboard snapshot; also served by SSE `/api/ticks/stream` and `/api/status`)
- **why it failed:** hardcoded fallback constants were used as initial values:
  `bid=2334.21`, `ask=2334.41`, `spread=20`, `atr=1.15`,
  `regime="NORMAL_VOLATILITY"`, `probs={"no_trade": 0.995, "buy": 0.002, "sell": 0.003}`,
  `ai_decision="NO_ACTION"`, `features_values=[0.0]*40` (40 not even 50).
  Whenever the engine had no live state, these constants were rendered as if real.
- **observed symptom:** dashboard showed 2334.21 / 20 pts / ATR 1.15 / NORMAL_VOLATILITY /
  99.5% NO_TRADE while the Debug Hub (which reads real `engine._last_*` state) showed healthy live values.
- **fix:** every section now defaults to explicit null + `available: False` + provenance
  `UNAVAILABLE`, and is overwritten ONLY by real engine state. Features are 50-dim
  schema-driven with per-entry `status` (VALID/NAN/UNAVAILABLE). Added `provenance`
  block (`LIVE_MT5 | ENGINE_STATE | MODEL_INFERENCE | ACCOUNTING_CORE | UNAVAILABLE`),
  snapshot identity (`state_version`, `snapshot_timestamp`), and per-section timestamps.
- **regression test:** `tests/unit/test_live_state_contract.py`
  (`test_no_fake_defaults_when_engine_offline`, `test_no_fake_defaults_when_engine_has_no_state_yet`,
  `test_real_values_flow_when_live`).

### RC2 — Hardcoded fake values baked into the static HTML
- **file:** `Web/index.html`
- **why it failed:** initial DOM text contained `2334.21 / 2334.41`, `20 pts`, `1.15`,
  `NORMAL_VOLATILITY`, `$10,000.00`, `78.5%`, `99.5% / 0.2% / 0.3%`, `NO_ACTION`, "Stable".
  Before the first SSE message or after a refresh with a slow stream, these fake values
  were visible.
- **fix:** all replaced with `—` / "WAITING FOR LIVE STATE" placeholders; the JS render
  functions (which are null-safe) fill real values when they arrive.
- **regression test:** `test_no_fake_defaults_when_engine_offline` (server side) + manual
  DOM check; grep guard: no `2334.21|99.5%|78.5` literals remain in `Web/`.

### RC3 — Frontend seeded fake (zero) features and had no REST bootstrap
- **file:** `Web/app.js`
- **functions:** `initApp()` (seeded `FEATURE_NAMES_JS` × `value:0.0`), `window.load` (never
  called `/api/status`), SSE `onerror` (no snapshot refetch on reconnect).
- **why it failed:** the UI rendered a full grid of zeros labeled as features before any
  real data existed (fake-data masquerade), and refresh/reconnect left sections empty
  because the UI depended on SSE alone.
- **fix:** removed the dummy seed (grid shows explicit "Awaiting live 50D feature stream…");
  `fetchSystemSnapshot()` runs on page load AND on every SSE `onopen` (convergence after
  disconnect/refresh); SSE reconnect is bounded exponential with resnapshot.
- **regression test:** manual lifecycle test matrix (load / refresh / SSE kill / restart).

### RC4 — Simulation endpoint injected fake ticks + fabricated outcomes into ("live") pipeline
- **file:** `src/nexus_scalp/web/server.py`
- **function:** `inject_tick` (`/api/simulation/tick`)
- **why it failed:** when no real tick existed, it fabricated `TickData(bid=2334.21,
  ask=2334.41)` and appended fake prediction outcomes (`prob_sim`, `TRUE_POSITIVE`) to
  `app.state.simulated_outcomes`, which was then served as the "AI Prediction vs Actual
  Movement" table. In LIVE mode this made synthetic prices look like production telemetry.
- **fix:** endpoint now requires explicit `SIMULATION`/`PAPER` mode and a real base tick;
  the fabricated outcomes list was removed from the state contract entirely.
- **regression test:** `test_simulation_tick_blocked_in_live_mode`.

### RC5 — Prediction history was fabricated, not ledger-backed
- **file:** `src/nexus_scalp/web/server.py` + `src/nexus_scalp/adapters/database/audit_repository.py`
- **why it failed:** `predictions` came from `app.state.simulated_outcomes` (per-click
  fabricated). `audit_signals` (the real per-M1 decision ledger) had NO read path.
- **fix:** added `AuditRepository.get_recent_predictions()`; `/api/status` now serves real
  ledger rows (time, action, confidence, regime, reason, real softmax probabilities from
  the payload JSON). The frontend table renders those rows with an explicit empty state
  when the DB has none.
- **regression test:** `test_predictions_never_fabricated` + `test_accounting_plan_*`.

### RC6 — Proposal/order-line rendering could crash on null equity/ATR
- **file:** `src/nexus_scalp/web/server.py`
- **functions:** `get_system_state()` overlay sections (`order_lines`, zone scanner)
- **why it failed:** `account_data["equity"]` and `atr` could be `None` in the new
  null-by-default model; `proposal.risk_reward_ratio` unguarded → `TypeError` /
  `'>' not supported between NoneType and int` (observed in logs).
- **fix:** null-guarded equity/atr and wrapped proposal overlay in try/except; zone
  scanning uses `atr if (atr is not None and atr > 0) else 1.50`.
- **regression test:** `test_no_fake_defaults_when_engine_has_no_state_yet` (exercises the
  zone scanner with `atr=None`).

### RC7 — Chart: no incremental provenance, no UTC policy, no AI-visible snapshot
- **file:** `Web/app.js` (+`Web/index.html`)
- **why it failed:** crosshair tooltip used browser-local time; no bar/sync metadata; no
  way to inspect what the model saw at a given candle.
- **fix:** added `formatUTCTime` (canonical UTC presentation), `chart-bars-meta` +
  `chart-source-badge`, AI View mode (`toggleAiView` → per-candle snapshot panel with
  OHLC/market/model/policy + provenance), Feature Delta view, click-to-select candle,
  DPI-safe `setTransform` in `drawChart`.

### RC8 — Research/Intelligence not auto-loading on tab switch
- **file:** `Web/app.js` `switchTab()`
- **fix:** `tab-research` → `loadResearchSummary()`, `tab-news` → `loadNewsState()`;
  intelligence behavior counter now reads `/api/intelligence/behavior` with explicit
  NO DATA state instead of hardcoded `--`.

### RC9 — Unused variable / rigor issues
- **fix:** removed unused `ask` local (F841); ruff format applied.

---

## FRONTEND FIXES
- `Web/index.html`: removed all fake literals; added `state-version-indicator`,
  `obs-strip` (REST/SSE age + snapshot version), `model-*` metadata grid,
  `chart-meta` bars/sync badges, `ai-snapshot-panel`, `feature-delta-view`,
  `intel-behavior` container, Risk Plan card (`rp-*`).
- `Web/app.js`: removed dummy feature seed; added `fetchSystemSnapshot()`
  (load + SSE reconnect); null-safe render path for every section; real prediction
  table from audit_signals; AI View + feature delta; UTC tooltip; `setTransform` DPI fix;
  click-to-select candle; research/news tab auto-load; real behavior detections;
  observability tracking (`lastApiResponseAt`, `sseLastEventAt`, `lastSnapshotVersion`).

## BACKEND FIXES
- `src/nexus_scalp/web/server.py`: rewritten `get_system_state()` (null-safe, 50-dim,
  provenance, `state_version`, timestamps); `/api/live/state` canonical `LiveUiState.1`
  contract; `/api/live/accounting` authoritative risk plan (RiskEngine single source of
  truth, account-size agnostic); simulation gated to PAPER/SIMULATION; real predictions
  from audit_signals; null-guarded overlays.
- `src/nexus_scalp/adapters/database/audit_repository.py`: added `get_recent_predictions()`
  (read path for the real decision ledger).

## API CONTRACT FIXES
- `/api/status` + SSE `/api/ticks/stream` share ONE `get_system_state()` — Debug Hub and
  main UI can never diverge.
- New `/api/live/state` (canonical LiveUiState.1: market/chart/features/model/strategy/
  risk/accounting/research/intelligence + provenance + timestamps).
- New `/api/live/accounting` (authoritative risk plan).
- Provenance literals: `LIVE_MT5`, `ENGINE_STATE`, `MODEL_INFERENCE`, `ACCOUNTING_CORE`,
  `RESEARCH_REGISTRY`, `UNAVAILABLE`.

## CHART FIXES
- UTC canonical presentation; bars meta (closed+forming counts, sync timestamp);
  DPI-safe canvas; AI View forensic panel; feature delta view; no synthetic candles
  (explicit empty/error states).

## ACCOUNTING FIXES
- All risk math already lived in `RiskEngine.calculate_dynamic_volume` (guarded,
  stepped, clamped, tiered for <$50 / <$100 / <$1k / <$10k / ≥$10k).
- NEW: `/api/live/accounting` exposes the SAME computation to the UI (single source of
  truth), with a deterministic plan (risk_usd, lot_size, min/max/step, margin_required,
  exposure_pct) across $10 .. $1M+ with explicit INSUFFICIENT_EQUITY note when below
  broker min lot.

## TESTS ADDED
- `tests/unit/test_live_state_contract.py` (16 tests):
  - no fake defaults (offline / no state / live flows)
  - canonical live-state contract
  - simulation blocked in LIVE
  - predictions never fabricated
  - accounting plan across 9 account sizes ($10 .. $1M) — no NaN/Inf/negative
  - non-finite equity rejection

## REMAINING RISKS
1. Per-candle HISTORICAL model probabilities are not yet stored per candle (audit_signals
   keeps one row per M1 decision; the AI View panel currently shows the LATEST live
   inference for any selected candle, clearly labeled "latest inference"). Full
   per-candle replay (feature vector + probs per closed candle) requires a per-bar
   snapshot store — recommended next phase.
2. `audit.db` at repo root is an empty file; the server creates `artifacts/audit.db`.
   The empty root file should be deleted or ignored to avoid confusion.
3. Frontend browser automation (Playwright) is not yet installed; acceptance was verified
   via TestClient + real uvicorn smoke — a headed browser pass is recommended.
4. `_last_probs` on a real engine is a torch Tensor with batch dim; the server reads
   indices 0..2 which matches the 4-class ScalpNet output contract, but a defensive
   length check could be added if the output head ever changes.