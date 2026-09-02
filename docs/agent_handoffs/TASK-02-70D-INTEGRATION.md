# TASK-02-70D-INTEGRATION — FINAL HANDOFF (60D Liquidity Integration)

> Agent: AGENT-02 (Hermes-LiquidityIntegration) · 2026-08-19 · Branch: main
> Task: REAL 70D LIQUIDITY INTEGRATION + UI CONTROL + DATASET/PARITY VALIDATION
> (TASK-02 of the 10-task 60D Liquidity Intelligence program — the tasking
> specifies the 60D `scalp_liquidity_v1` contract; the "70D" in the title
> refers to the program series.)

## CURRENT STATE
- branch: `main`
- HEAD: `2ad88bb` (STEP-14/15) + final ruff-clean commit
- origin/main: in sync (every commit pushed + verified local==remote)

## LIQUIDITY
- enabled/disabled: **DISABLED by default** (`model.liquidity_features_enabled=false`
  in config; missing setting resolves false; no inference from model presence)
- algorithm_version: `scalp_liquidity_v1.0.0`
- schema: `scalp_liquidity_v1` (dim 60; indices 0..49 = protected scalp_v1,
  50..59 = liquidity); ACTIVE live schema remains `scalp_v1`
- OFF -> pure 50D (unchanged semantics); ON -> 60D exact contract

## UI
- toggle: exists (Enable/Disable Liquidity Intelligence), reads/writes the
  backend via /api/liquidity/state + /api/liquidity/toggle (NEVER UI-only)
- persistence: governor persists via SettingsService -> SettingsDatabase
  (typed bool; the swarm's original path called a non-existent method and
  stored truthy strings — FIXED in STEP-1/2, restart-persistence proven)
- hot reload: OFF<->ON applies live on the same governor (no restart);
  proven by tests
- UI index derivation: liquidity block index = active schema dimension - 10
  (no hardcoded 60/70)

## DATASET
- source: `data/raw/XAUUSD_M1.parquet` (REAL broker data, 100k rows,
  2026-05-01..2026-08-17, no dup timestamps, no nulls)
- full-frame stats: 99,946 rows, 60 feat cols, all finite, all in [-3,3]
- dataset artifact: `ds_liq_task02_real_1k` (1146 real rows, 60 feat cols,
  scalp_liquidity_v1, triple-barrier labels 60 buy/53 sell/1033 no-trade,
  split 802/171/173, verify_ok) — artifacts/ gitignored by design
- feature distributions (feat_50..59): BSL/SSL mean ~1.7 ATR with ~25% +3
  saturation; EQH/EQL 0..1; HTF -3..3 mean ~0; internal/external 1.0/1.77;
  confluence mean 2.71 (21% sat — note for research); sweep rare (mean 0.15)

## PARITY
- dataset == replay: replay reads the artifact's feat_* columns -> the exact
  60D vector the builder wrote (TEST-TASK02-13/14)
- dataset == runtime: both call the SAME canonical
  `compute_liquidity_features` (structural; TEST-LIQ-29/30 + golden)
- 50D protected: TEST-60D-BASE-01 + TEST-TASK02-08 (OFF == pure 50D)

## MODEL
- baseline: 50D scalp_v1 (existing Champion, untouched)
- candidate path: CandidateTrainer on the 60D contract (TEST-TASK02-17/18:
  cand_* id, COMPLETED, Champion path untouched)
- NO promotion anywhere (TASK-02 never promotes; verified by test)

## PERFORMANCE (real data)
- per-row: ~11.6 ms (200-row cProfile) / 70s per 1000-row frame (contended)
- p50=39.6ms p95=49.7ms max=54.4ms per 501-bar window (latency probe)
- live hot-path: liquidity runs on bar-close cadence only (11.6ms << 60s) —
  NO hot-path regression
- PROVEN bottleneck: `_bars_to_arrays`/`_bar_times` re-derivation (~40% of
  time); designed fix (thread arrays once) documented in
  docs/LIQUIDITY_60D_FROZEN.md — NOT applied (engine frozen)

## TESTS (136 passed)
- tests/unit/test_liquidity_engine_{contract,causality,features}.py: 60
- tests/unit/test_liquidity_task02_integration.py: 29 (TEST-TASK02-01..21)
- tests/unit/test_liquidity_runtime_integration_phase18.py: 38 (swarm 70D)
- tests/integration/test_liquidity_api.py: 14 (60D contract)
- ruff check + format: clean on all touched files; node --check clean;
  index.html div-balance clean

## COMMITS (TASK-02, all pushed + remote-verified)
| Step | SHA |
| :--- | :--- |
| STEP-0 baseline | cf84f79 |
| STEP-1/2 flag contract + persistence fix | 9eab99e |
| STEP-3 API state | a703b8e |
| STEP-4/5 UI toggle | 5bca9f3 |
| STEP-6/7 hot reload + runtime 60D vector | 8d8270e |
| STEP-9 real-data causality | 4de3ce7 |
| STEP-10/16/17 golden + freeze | 2e9b0f0 |
| STEP-21/22 performance | b41d76c |
| STEP-8/11/12 real dataset + parity | 3a6405d |
| STEP-14/15 candidate path | 2ad88bb |

## BUGS
- (proven, fixed) Governor persistence called `SettingsService.set()` which
  does not exist -> toggles never persisted across restart; ALSO the truthy
  string `"0"` bug stored enabled=true. Fixed: `SettingsService.db.set(key,
  real_bool, value_type="bool")`. Regression: TEST-TASK02-06/27.

## REMAINING RISKS
- PROVEN: causality on real data (swing +5 delay 500/500, HTF exclusion
  25/25, sweep invariant 25/25, historical invariance 25/25)
- PROVEN: performance bottleneck (array re-derivation) with designed fix
- NOT PROVEN: full-scale 100k dataset artifact (compute took ~8-19 min and
  the swarm's CPU load made bounded builds hang; 1k/5k slices verified —
  the 100k FRAME stats are proven, the artifact build recipe is verified on
  1k)
- UNKNOWN: real trained 60D candidate quality (no full training run —
  OUT OF SCOPE for TASK-02: "a weak 60D model is a valid research result",
  the training experiment belongs to TASK-04-70D-MODEL-VALIDATION)
- CONTENTION: `features/liquidity_runtime.py` + `web/server.py` are shared
  with the parallel 70D swarm (TASK-03-70D-PARITY renamed SCHEMA_70D ->
  scalp_v3; their governor schema block may show 70D when enabled). The
  TASK-02 60D contract is enforced at the ENGINE level (build_runtime_60d_vector)
  + tests + golden; the swarm owns the governor's 70D semantics.

## NEXT AGENT (TASK-03 / TASK-04-70D-MODEL-VALIDATION)
1. Read docs/LIQUIDITY_60D_FROZEN.md + docs/LIQUIDITY_60D.md +
   tests/golden/liquidity_70d_reference.json (the frozen contract).
2. TASK-04: run the real A/B — BASELINE 50D scalp_v1 vs CANDIDATE 60D
   scalp_liquidity_v1 on the SAME dataset/timestamps/split/purge/embargo/
   procedure (use ds_liq_task02_real_1k or build a larger slice with the
   documented array-threading optimization under a NEW schema version).
3. Do NOT change the frozen constants without schema/version review +
   new tests + new golden re-baseline + new commit + new handoff.
4. The swarm's 70D chain (TASK-03-70D-PARITY, TASK-05-70D-SHADOW) builds on
   scalp_liquidity_v1 as the family producer at 50..59 — coordinate any
   change via agents/taskboard.md.
5. Confluence saturation (feat_57 ~21% at +3) and BSL/SSL ~25% +3
   saturation are research signals for TASK-07-70D-LIQUIDITY-RESEARCH.