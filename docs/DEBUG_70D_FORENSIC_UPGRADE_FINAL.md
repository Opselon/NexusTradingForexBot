# Debug 70D Forensic Upgrade — Final Report

**Task:** Upgrade the Debug tab into a full 70D runtime intelligence console
**Agent:** Hermes-Forensic-70D-UI (Debug UI / Runtime Observability Engineer)
**Date:** 2026-08-19
**Branch:** main

---

## 1. CURRENT RUNTIME CONTRACT

| Field | Value |
|---|---|
| schema_id (canonical) | `scalp_v3` (70D) |
| dimension | 70 |
| schema_hash | `235b8fccc96b7e0e` |
| algorithm_version | `1.0.0` (SCHEMA_VERSION) |
| family layout | 0..49 BASE (scalp_v1 protected) · 50..59 NEWS (news_context_v1 fields 0..8 + news_state idx 10) · 60..69 LIQUIDITY (liquidity_engine canonical order) |
| live model schema | `scalp_v1` (50D) — legacy live contract protected; 70D is candidate/shadow-only |
| expected model classes | 4 (NO_TRADE / BUY_MARKET / SELL_MARKET / WAIT) |

The Debug tab renders the ACTIVE contract from `schema_contract.canonical_feature_names()` — never a hardcoded 70D list. When a legacy 50D model is live the header shows `Dimension: 50` and the contract banner reports `70D CONTRACT BROKEN` (expected 70 vs actual 50) — no fake 70D display.

## 2. FEATURE MATRIX (all 70)

Registry-driven rows from `schema_contract`:

- **Base 0..49** — the protected scalp_v1 50D names (microstructure/trend/volatility/oscillators/volume/price action/order flow/MTF/structure/SMC), values from the live `FeatureVector.to_tensor_input()`.
- **News 50..59** — canonical news 10D: `active_high_impact_events, xauusd_relevance, usd_relevance, bullish_pressure, bearish_pressure, conflict_score, novelty, freshness, confidence, news_state` — from the SAME news context the Champion consumed (`vectorize_news_context` + schema_contract selection, never a blind first-10 slice).
- **Liquidity 60..69** — `bsl_distance_atr, ssl_distance_atr, eqh_strength, eql_strength, htf_liquidity_score, internal_liquidity_distance, external_liquidity_distance, liquidity_confluence, liquidity_sweep_state, post_sweep_displacement` — from `LiquidityGovernor.snapshot_payload()` (canonical indices).

Columns: INDEX / NAME / FAMILY / RAW / NORMALIZED / CLIPPED / FINAL / STATUS / SOURCE / TIMESTAMP / CAUSALITY. RAW/NORMALIZED/CLIPPED are populated ONLY when the runtime exposes that stage; otherwise `NOT_EXPOSED` — never a fabricated zero. Per-feature status: VALID / FALLBACK / STALE / UNAVAILABLE / INVALID. Health summary: TOTAL / VALID / INVALID / FALLBACK / UNAVAILABLE / STALE + min/max/mean of the active vector.

## 3. MODEL (input / output / tensor state)

- `_last_model_input_tensor` — the exact post-scaler, pre-softmax tensor the live path consumed (`_infer_probabilities` stash; observability only, INV-018).
- Input tensor shape / dtype / device / scaler ready / scaler hash / schema hash / model id / version / architecture.
- Probabilities mapped canonically: NO_TRADE / BUY_MARKET / SELL_MARKET / WAIT + full raw vector + predicted class (argmax) + confidence (max prob).
- `MODEL_OUTPUT_INVALID` is surfaced when: probs width != artifact classes, OR artifact classes != 4 (the 128-class regression). Never silently rendered.

## 4. POLICY (full decision chain)

Nine gates in order, each with actual value / threshold / status / reason:

```
SIGNAL → CONFIDENCE → REGIME → R:R → SAME-LEVEL → NEWS → EXPOSURE → RISK → EXECUTION
```

Sources: `TradeProposal` fields (`decision_stage`, `blocked_by`, `reason_code`, `confidence_before_filters/after_filters`, `guardian_status`, `risk_allowed`, `rejection_reason`), `_last_news_gate`, order-manager exposure. Statuses: PASS / FAIL / BLOCKED / UNAVAILABLE.

## 5. RISK (full risk state)

Balance / equity / free margin / margin / margin level / drawdown % / risk % / max lots / hard max lots / max positions / max spread / min R:R / max DD / kill switch / survival mode + RiskEngine decision (PASS / BLOCK / NOT_EVALUATED) + reason.

## 6. EXECUTION (full execution state)

Adapter type, broker connection state, order-manager global state, consecutive failures, processed-order count, live-tickets cache. Positions: ticket/direction/lots/entry/current/SL/TP/PnL/swap/commission/MFE/MAE/peak PnL/peak drawdown/hold seconds/breakeven/trailing/giveback/strategy exit state.

## 7. LIQUIDITY (all ten + context)

Ten values (60..69) + governor status/causal state/source/schema/last update/age/latency/algorithm version + **active pools** (side/source/state/price/confirmed_at) so the engineer sees WHY `bsl_distance_atr = X`.

## 8. NEWS (all active state)

Enabled / available / freshness / state / bullish / bearish / mixed(conflict) / high impact / active events / XAUUSD relevance / USD relevance / consensus / confidence / timestamp + the 10 news dimensions active in the model (50..59). News stays separate from Liquidity.

## 9. WORKERS (all worker state)

Accounting / History Sync / Intelligence / Research / Training / Shadow / Shadow70 / News / Telegram — each with state / cycle / last start / last success / last failure / duration / queue. A worker marked RUNNING whose last success is >10 min old is flagged **DEGRADED**.

## 10. DATABASE (health)

audit.db / news.db / candle_intel.db / research storage — path (masked) / size / WAL / health. Backend snapshot only; no per-request DB scans.

## 11. CHART (history/live state)

Data source / bars received / first+last timestamp / timeframe / overlays (liquidity / news / SMC) / reseed state.

## 12. SSE (connection/serialization state)

Connection status / connected_at / last event / event count / last latency / serialization errors / reconnect count. On failure: `SSE_SERIALIZATION_ERROR` with field, type, event, correlation_id — never a bare "disconnected". (Also fixed a latent NameError: `state_version` was undefined in the SSE serialization-error handler — now `version`.)

## 13. SNAPSHOT (schema)

One canonical payload `GET /api/debug/state`:

```
snapshot_id / correlation_id / timestamp / engine_attached /
runtime / contract / features / model / confidence / policy / risk /
exposure / execution / positions / exit / liquidity / news / workers /
database / caches / chart / sse / errors
```

Every section is real backend state or an explicit `UNAVAILABLE` marker with a reason + correlation_id (NO HIDDEN ERRORS). Rolling history: `GET /api/debug/snapshots` + `GET /api/debug/snapshots/{id}` (in-memory ring, max 64). Compare: `GET /api/debug/compare?a=&b=` → feature deltas (T0/T1/Δ) + model/confidence/regime/liquidity/news/policy/risk changes.

## 14. PERFORMANCE (debug overhead)

- The snapshot is assembled from in-memory engine attributes and cached worker reports — no DB scans, no feature recompute, no liquidity pool rebuild, no model reload (verified by test_debug_30: `_load_or_create_bundle` never invoked).
- Auto-refresh bounded at 3s via the existing `/api/debug/state` endpoint (the UI consumes the canonical snapshot; no aggressive per-subsystem polling).
- SSE diagnostics are counters on `app.state.sse_diag` — zero hot-path cost.

## 15. TESTS (exact counts)

`tests/unit/test_debug_snapshot_phase20.py` — **36 tests, all passing** (TEST-DEBUG-01..32 + API-level variants):

- Schema/contract: 01, 02, 03, 04, 05, 06, 27 (128-class regression), 28 (50D mismatch)
- Liquidity: 07; News: 08; Model: 09, 10; Confidence: 11; Policy: 12; Risk: 13
- Exposure: 14; Execution: 15; Positions: 16; Exit: 17; Workers: 18; DB: 19; Caches: 20; Chart: 21; SSE: 22
- Identity: 23; Snapshot: 24, 25, 26 (datetime), 31 (compare); Diff: 32
- Security: 29 (no secrets); Hot-path: 30 (no blocking)

Focused regression pass: test_debug_snapshot_phase20 + test_web_security + test_web_chart_forming_bar_bug082 = 47 passed.

## 16. BUGS (only proven)

- **Latent NameError fixed**: SSE serialization-error handler referenced undefined `state_version` (now `version`) — a crash in the error path would have killed the SSE loop exactly when it was needed most. (BUG-110 area.)
- **Backend hardening from the test suite**: `_entry_confidences` / `_peak_drawdown_usd` accessed via hasattr guards; news-worker format normalized to `state` key.
- No new runtime bugs introduced. `eqh_strength` is index **62** per schema_contract (60 bsl / 61 ssl / 62 eqh / 63 eql) — the brief's "63 eqh_strength" example was aligned to the canonical registry.

## 17. COMMITS (exact SHAs)

| SHA | Step | Content |
|---|---|---|
| `3f3f3d9` | STEP-01/02 | Backend canonical snapshot (`web/debug_snapshot.py`) + `/api/debug/state` + SSE diagnostics + `_last_model_input_tensor` |
| `987c550` | STEP-07 | Debug tab UI rebuild (index.html + app.js renderers) |
| `a369345` | STEP-08 | TEST-DEBUG-01..32 suite + regression fixtures + backend hardening |

All three pushed to `origin/main` and verified (`HEAD == origin/main`; GitHub API confirms commit contents).

## 18. NEXT AGENT (exact next actions)

1. **Live-runtime validation**: run the engine (LIVE/PAPER) and open the Debug tab — verify the 70D matrix shows real values, liquidity pools render, model input tensor matches the scaler output, and the contract banner reads `70D CONTRACT OK` once a 70D candidate is promoted.
2. **Browser E2E**: run the Playwright integration (`tests/integration/test_playwright_e2e.py`) against the new Debug tab to verify tab switching, filters, snapshot capture/download and compare UI interactions.
3. **Extend TEST-DEBUG-33+** if the runtime exposes additional canonical stages (e.g. raw logits when the model layer surfaces them — currently the live path stores softmax only, so `logits: null` is truthful NOT_EXPOSED).
4. Consider surfacing the 70D shadow runtime summary (`Shadow70Runtime.summary()`) in the Debug snapshot's model section when a candidate is attached (`_shadow70_runtime`).
