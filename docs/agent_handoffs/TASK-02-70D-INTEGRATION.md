# TASK-02-70D-INTEGRATION — Handoff (Hermes-70D-Integration)

> Agent: Hermes-70D-Integration (TASK-2) · 2026-08-19
> Branch: `main` · Starting HEAD: `4001e4c` (TASK-2 began) — parallel agents
> committed `c56d334` (TASK-07 blocked-state) + `d1fb393` / `6bbd8e7`
> (TASK-04 model-validation) while this task ran; this task's work is
> additive on top of the current HEAD.

## 1. What TASK-1 actually delivered (verified at TASK-2 start)

- **Production engine** (uncommitted WIP when TASK-2 started):
  `src/nexus_scalp/features/liquidity_engine.py` (52 KB) — pure-causal 10D
  liquidity producer (`compute_liquidity_features`, `LiquidityFeatures`,
  `LIQUIDITY_FEATURE_NAMES`, pools/state machine).
- **Schema**: `scalp_liquidity_v1` (60D; liquidity at 50..59) registered in
  `features/schema.py`; `model.liquidity_features_enabled` flag in
  `ModelConfig` + `configs/base.yaml`; dataset builder
  `compute_liquidity_frame`/`build_liquidity_dataset`/
  `verify_liquidity_artifact` in `model_generation/schema_v2.py`.
- **Tests**: `tests/unit/test_liquidity_engine_causality.py` (343 lines),
  `test_liquidity_engine_contract.py` (382), `test_liquidity_engine_features.py`
  (334), fixtures `tests/helpers/liquidity_fixtures.py`.
- **Docs**: `docs/LIQUIDITY_60D_FORENSIC_BASELINE.md`,
  `docs/LIQUIDITY_60D_50D_CONTRACT_SNAPSHOT.json`, `docs/LIQUIDITY_60D.md`.
- TASK-1 did NOT deliver: runtime governor, API, UI, toggle, 70D schema —
  that was TASK-2's job.

## 2. Repo-reality adaptations (brief TEST-29 "adapt names")

- The brief's "News 10D at 50..59" does NOT match this repo: slot 50..59 is
  the **TASK-5 scalp_v2 momentum family** (`schema_augment.compute_60d_extras`).
  Real News = independent 12D `news_context_v1` stream (models).
- `scalp_v3` = **350D** already registered + asserted in existing tests
  (`test_accounting_core.py`, `test_experience_intelligence.py`). The 70D
  contract is therefore **`scalp_v4`**:
  `BASE 0..49 | FAMILY 50..59 | LIQUIDITY 60..69`.
- TASK-1's ledger already prescribed the separate-schema-id pattern
  (`scalp_liquidity_v1`), so `scalp_v4` is consistent with it.

## 3. What TASK-2 delivered

### New files
- `src/nexus_scalp/features/liquidity_runtime.py` — LiquidityGovernor
  (thread-safe snapshot + status ENABLED/DISABLED/DEGRADED/UNAVAILABLE +
  causal VALID/STALE/INVALID + latency + model compat),
  `build_70d_vector` (strict, no pad/truncate), `resolve_model_compatibility`,
  `LiquiditySnapshot`.
- `tests/unit/test_liquidity_runtime_integration_phase18.py` — TEST-70D-01..28
  mapped to the real repo contract (30 tests).
- `tests/integration/test_liquidity_api.py` — REST tests for
  /api/liquidity/state|features|toggle + live/status sections (9 tests).

### Modified files (TASK-2 owned)
- `src/nexus_scalp/features/schema.py` — adds `scalp_v4` (70D) registration
  (TASK-1's scalp_liquidity_v1 registration kept).
- `src/nexus_scalp/application/live_engine.py` — governor init (reads
  SettingsService persisted value first, falls back to config) + new-bar
  snapshot hook (pure numpy, info-only, failure-isolated).
- `src/nexus_scalp/settings/service.py` — `model.liquidity_features_enabled`
  added to MUTABILITY as HOT_RESTRICTED.
- `src/nexus_scalp/web/server.py` — `/api/liquidity/state`,
  `/api/liquidity/features`, `POST /api/liquidity/toggle`,
  `_liquidity_state_section()` helper, `liquidity` section in the canonical
  state graph (embedded in /api/status + /api/live/state + SSE).
- `Web/index.html` — Liquidity Intelligence tab (nav button + panel:
  status/schema/dimension/features/source/causal/last-update/latency/model
  compat/toggle + ten per-value cards idx 60..69).
- `Web/app.js` — `loadLiquidityState`, `toggleLiquidity`,
  `renderLiquidityPanel`, `syncLiquidityFromSnapshot`, chart pool overlays
  (window.__liquidityPools from real snapshot only), console traces
  `[LIQUIDITY_UI]` (no silent catch).
- `configs/base.yaml`, `docs/LIQUIDITY_60D.md` (appendix), registries
  (contracts/invariants/change_control/taskboard/repository_state).

## 4. Key semantics / invariants added

- **INV-020** (runtime_invariants.md): the liquidity toggle is
  information-only; never touches orders/SL/TP/risk/execution/account/news.
  Hot-reloadable without engine restart; persists via SettingsService.
- **Model compatibility** (no auto-migration): 60D model + 70D runtime →
  BLOCK with `LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE`; 70D+70D → PASS;
  unknown → UNKNOWN. Never pad/truncate/silently upgrade.
- **News/Liquidity independence**: either can be enabled/disabled/
  unavailable without affecting the other (TEST-70D-06..09/28).

## 5. Tests (TASK-2 verification)

- `tests/unit/test_liquidity_runtime_integration_phase18.py`: 30 passed.
- `tests/integration/test_liquidity_api.py`: 9 passed.
- TASK-1 suites (60 tests) still pass.
- Total liquidity suites: 99 passed.
- Quality gates on TASK-2 files: ruff check ✅, ruff format ✅, mypy ✅.
- NOTE: `tests/unit/test_model_governance_phase16.py::TestGovernance70`
  has 5 pre-existing failures from the PARALLEL TASK-08/70D-governance WIP
  (PROVEN: fail identically without TASK-2's changes; not owned by TASK-2).

## 6. Runtime smoke (real evidence)

```
GET /api/liquidity/state  -> ENABLED, scalp_v4/70D, 10 real values,
                             latency 8.01 ms, causal VALID, source LIVE_MARKET_STATE
GET /api/liquidity/features -> indices 60..69 exact
POST /api/liquidity/toggle  -> OFF -> DISABLED, ON -> ENABLED (hot, no restart)
GET /api/live/state         -> liquidity section present; news independent
GET /api/status             -> liquidity section present
```

## 7. Known risks / unfinished

- TASK-1's liquidity_engine.py + tests + docs are still UNCOMMITTED in this
  tree (TASK-1's own commit did not land). TASK-2 committed them together
  with the integration (agent-labelled, additive) so the 70D series has a
  frozen, testable base. If TASK-1 later publishes its own commit, expect an
  overlap — resolve additively (do NOT rewrite TASK-2's rows).
- The `scalp_v4` bench path (training a real 70D model) is owned by TASK-04
  (model-validation protocol) and TASK-03 (parity) — not in TASK-2 scope.
- Chart overlays draw from `report().pools` (state enums serialized as ints
  via serialize_enums); the UI color-maps side 1/-1.
- The live engine hook fires on new-bar cadence only; a long-idle feed yields
  DEGRADED/STALE via timestamps (intended, brief 25).

## 8. EXACT NEXT-AGENT INSTRUCTIONS (TASK-3)

1. READ FIRST: `agents/skill.md`, `agents/bugs.md` tail (next free BUG-NNN),
   `agents/contracts.md` (LIQUIDITY_RUNTIME/LIQUIDITY_API/FEATURE_SCHEMA_70D
   rows), `agents/runtime_invariants.md` (INV-020), `agents/taskboard.md`
   (TASK-02 row), `docs/agent_handoffs/TASK-02-70D-INTEGRATION.md`.
2. Run the liquidity suites to confirm the base:
   `.venv/Scripts/python.exe -m pytest tests/unit/test_liquidity_runtime_integration_phase18.py tests/integration/test_liquidity_api.py tests/unit/test_liquidity_engine_*.py -q`
3. TASK-3 (per brief series) builds the **training/live parity** layer:
   `model_generation/schema_v2.py::compute_liquidity_frame` exists (TASK-1)
   — wire it into the fair benchmark as the 70D dataset producer and prove
   live-vs-training parity for feat_60..69 with the SAME causal window
   (dataset builder vs `LiquidityGovernor.compute_from_engine`).
4. Reuse `build_70d_vector`/`resolve_model_compatibility` — do NOT re-implement
   family placement or the compat matrix.
5. If you touch `Web/app.js`: keep it PURE CRLF (normalize after edits,
   `node --check` after every edit). `Web/index.html` is LF; keep div balance
   (scripts/div_balance_check.py).
6. If you touch `web/server.py` or `live_engine.py`, keep the file's line
   ending uniform (server.py is LF; live_engine.py is LF) and re-run
   py_compile + affected tests.
7. Persist any NEW settings key through `SettingsService` + MUTABILITY
   (never live.yaml direct writes, INV-010).
8. Commit agent-labelled (`Hermes-70D-...: <imperative>`) with full body
   (Agent/Role/Scope/Why/Implementation/Verification/Risk/Handoff). Verify
   `git diff --cached --name-only` immediately before commit (parallel-agent
   hazard: other agents run git concurrently).
9. Final gate: beforePush.ps1 (or the equivalent 4-part command) — report
   pre-existing parallel-agent failures separately from your own.