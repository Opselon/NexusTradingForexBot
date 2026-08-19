# LIQUIDITY UI Forensic Final Report — BUG-111 Root Cause, Fix, Verification

> Task: Fix Liquidity Intelligence UI contract & runtime state.
> Agent: Hermes-LiquidityUI-Forensic (AGENT-14) · 2026-08-19
> Commits: see §J. Baseline: `617c23a` (probe) → HEAD absorbed into `8635c66` (AGENT-11) + `AGENT-14` commits.

## A. BEFORE — exact incorrect UI values (proven, real payloads)

Reproduction: `scratch/probe_liquidity_ui_state_contract.py` +
`scratch/probe_liquidity_ui_state_contract.out.txt` (production sequence:
enabled → computed → toggled OFF; the exact state a live session leaves).

```
FIELD                  WRONG VALUE (UI == API payload — UI was faithful)
enabled                False
available              True            <- contradiction
status                 DISABLED
schema                 scalp_v1 / 50D  <- misleading (disabled path)
dimension              50              <- but liquidity claimed "60..69" elsewhere
feature indices        40..49          <- snapshot_payload derived math
source                 UNAVAILABLE / LIVE_MARKET_STATE mix
causal_state           VALID           <- while disabled+no source
last_update            1970-01-01T11:56:13+00:00   <- monotonic-as-epoch
latency_ms             66-108 ms
model_compatibility    BLOCK (LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE)
                        <- claims liquidity ENABLED while status=DISABLED
feature_count          10              <- values shown as active inputs
algorithm_version      scalp_liquidity_v1.0.0 (unverifiable provenance)
```

## B. ROOT CAUSES — first incorrect layer (all backend)

| # | Symptom | First wrong layer | Root cause |
| :-- | :-- | :-- | :-- |
| RC-1 | `last_update = 1970-01-01` | `liquidity_runtime.py` | `_last_success_at`/`_last_error_at` store `time.monotonic()` (uptime), rendered via `datetime.fromtimestamp()` (epoch) → 1970 + uptime. Monotonic is only valid for deltas; absolute timestamps need wall clock. |
| RC-2 | indices 40..49 | `liquidity_runtime.py::snapshot_payload` | index = `active_schema.dimension - 10 + pos`. DISABLED → active schema = `scalp_v1` (50D) → 40..49. Canonical registry (`schema_contract.py`) places liquidity at 60..69. UI re-derived the same wrong numbers in JS. |
| RC-3 | DISABLED + 10 "features" + `available=True` | `liquidity_runtime.py::report` | `available = snap is not None and status != UNAVAILABLE` ignores `enabled`; `features` dumped whenever a snapshot exists with no stale/not-active label. |
| RC-4 | BLOCK(LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE) while disabled | `liquidity_runtime.py::model_compatibility` | unconditionally evaluates current 50D model vs reserved 70D schema, even when liquidity disabled — a reason string that lies about enabled state. |
| RC-5 | source/causal/availability conflation | `liquidity_runtime.py` payload contract | `source` = last calc's producer, `causal` = snapshot age, `available` = snapshot existence — three orthogonal meanings collapsed into one row, letting the UI assemble "UNAVAILABLE + Available + VALID". |
| RC-6 | algorithm_version provenance | `liquidity_runtime.py` | hardcoded constant; producer carries no version; UI cannot distinguish configured vs active. |
| RC-7 | live_engine passed stale `_source` to the governor | `live_engine.py` | `source=self.liquidity_governor._source` (prior value, default UNAVAILABLE) instead of `SourceKind.LIVE_MARKET_STATE` for THIS computation. |

**The UI was a faithful renderer of a contradictory backend payload. The first incorrect value appears in `LiquidityGovernor` (runtime layer), not `app.js`.**

## C. FIX — exact source-layer changes

`src/nexus_scalp/features/liquidity_runtime.py`:
- NEW wall-clock fields `_last_success_wall_at` / `_last_error_wall_at` (UTC epoch); monotonic stays for age deltas only. `last_update` / `error_at` render wall clock.
- NEW `LIQUIDITY_BLOCK_START=60` / `LIQUIDITY_BLOCK_END=70` + registry-driven indices via `schema_contract.canonical_feature_names()` (fallback to the documented 60..69 constant — never derived from active dimension). Removed the derived `act["dimension"] - 10 + idx` math.
- `_active_schema_block()` ON branch → canonical `scalp_v3`/70D (BASE 0..49 | FAMILY 50..59 | LIQUIDITY 60..69) with `liquidity_indices`; OFF branch unchanged (honest pre-liquidity scalp_v1/50D) but never drives liquidity indices.
- `report()`: explicit `feature_availability` (AVAILABLE/STALE_CACHE/UNAVAILABLE/NOT_ACTIVE), `calculation_status` (SUCCESS/FAILED/NOT_RUN/DEGRADED), `source_status`; `available` only True when genuinely AVAILABLE; `causal_state` → NOT_APPLICABLE when disabled; `snapshot_timestamp` added; `state_revision` (monotonic per mutation).
- `model_compatibility()`: disabled → `NOT_APPLICABLE` (LIQUIDITY_DISABLED); enabled → real matrix vs canonical scalp_v3.
- `snapshot_payload()`: per-feature `index` from registry, plus `source_status`/`feature_availability`/`runtime_enabled`/`state_revision`; disabled → NOT_ACTIVE provenance with snapshot timestamp.

`src/nexus_scalp/application/live_engine.py`:
- governor compute hook passes `source=SourceKind.LIVE_MARKET_STATE` (real provenance), not the stale `_source`.

`Web/app.js` (renders backend payload only):
- per-feature index/source/status from the API object (removed `baseDim - 10 + i` derivation);
- availability map renders NOT_ACTIVE/STALE_CACHE explicitly; last-update shows snapshot_timestamp too;
- `syncLiquidityFromSnapshot` drops stale `state_revision` (SSE out-of-order guard).

`Web/index.html`: state-revision tile + honest caption.

## D. API — before/after payload (real)

`GET /api/liquidity/state` (disabled + retained snapshot):
```
BEFORE:  enabled=false available=TRUE status=DISABLED causal=VALID
         source=LIVE_MARKET_STATE last_update=1970-01-01T... schema=scalp_v1/50D
         model_compatibility=BLOCK(LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE)
         features={10 values}
AFTER:   enabled=false available=FALSE status=DISABLED
         feature_availability=NOT_ACTIVE causal_state=NOT_APPLICABLE
         source_status=LIVE_MARKET_STATE last_update=2026-08-19T02:56:19+00:00
         snapshot_timestamp=2026-08-19T09:30:00+00:00
         schema=scalp_v1/50D (liquidity_indices:[60,69])
         model_compatibility=NOT_APPLICABLE(LIQUIDITY_DISABLED)
         features={10 values, provenance NOT_ACTIVE} state_revision=N
```

`GET /api/liquidity/features`:
```
BEFORE:  schema_id=scalp_v1 dimension=50  idx 40..49  status=DISABLED
AFTER:   schema_id=scalp_v1 dimension=50  idx 60..69  status=DISABLED
         feature_availability=NOT_ACTIVE runtime_enabled=false
```

## E. UI — before/after

BEFORE: DISABLED badge + 50D + idx 40..48 + Available + Valid + BLOCK(...ENABLED...) + 1970.
AFTER: DISABLED badge + scalp_v1/50D (honest) + idx 60..69 per card + Availability: NOT ACTIVE + Causal: NOT_APPLICABLE + Model Compatibility: NOT_APPLICABLE (LIQUIDITY_DISABLED) + wall-clock Last Update + snap timestamp + rev N.

## F. RUNTIME — actual state after fix

```
enabled=false  status=DISABLED  feature_availability=NOT_ACTIVE
causal=NOT_APPLICABLE  source_status=LIVE_MARKET_STATE (last calc)
last_update=2026-08-19T... (wall clock)  snapshot_timestamp=2026-08-19T09:30:00+00:00
schema ON-path=scalp_v3/70D  indices=60..69 (registry)  state_revision monotonic
model_compat=NOT_APPLICABLE (disabled) / BLOCK(LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE) (enabled+50D)
```

## G. TESTS

- BUG-111 regression suite: 10 new tests (`test_liq_ui_01..10`) appended to
  `tests/unit/test_liquidity_runtime_integration_phase18.py`.
- Contract-pin updates: `test_liquidity_api.py` (indices 60..69, scalp_v3/70D),
  `test_liquidity_runtime_integration_phase18.py` (60..69, scalp_v3),
  `test_liquidity_task02_integration.py` (schema row).
- Counts: 86 liquidity+API+task02 tests pass; 83 engine/optimization tests pass;
  204 related suite tests pass (governance/release/schema-70D).
- Time-unit matrix covered by `test_liq_ui_05` + artifact `liquidity_timestamp_trace.json`.

## H. PERFORMANCE

- Governor additions are O(1) timestamp reads + one registry lookup per
  `snapshot_payload()` call (10-name index scan). No hot-path change
  (live_engine hook still pure numpy, per new-bar cadence).
- UI: +1 number compare per snapshot; zero added network.

## I. BUGS — proven

- **BUG-111** (new): monotonic-as-epoch 1970 timestamps; active-schema-derived
  liquidity indices (40..49 while disabled); DISABLED+features+available=True;
  model-compat BLOCK(LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE) while disabled;
  live_engine stale-`_source` provenance. Status: FIXED/VERIFIED (tests + artifacts).
- BUG-110 (parallel WIP, datetime SSE serialization) confirmed integrated and
  passing (AIHUB-07/08 tests).

## J. COMMITS

1. Backend governor + engine + UI/HTML absorbed into parallel `8635c66`
   (AGENT-11 "canonicalize 70D schema") — verified my fixes present at HEAD
   (`git show 8635c66:...`). No re-commit of absorbed files (contract rule).
2. `AGENT-14: ...` — Web/app.js stale-SSE guard + BUG-111 regression suite +
   forensic baseline/report + artifacts (see commit body).

## K. REMAINING RISKS

- PROVEN: scalpel-pinned old-contract tests (50+idx, scalp_liquidity_v1 60D
  schema id) updated to the canonical contract; all green at HEAD.
- NOT PROVEN: no real-broker live run of the OFF/ON/reconnect sequence in this
  session (runtime smoke below ran governor-level; UI verified by payload parity).
- UNKNOWN: parallel agents continue editing liquidity files; a rebase/merge may
  re-introduce derived-index math — guarded by `test_liq_ui_01/02`.
- The `scalp_v4` production-scope vs `scalp_v3` canonical id remains a
  documented dual (release/model_artifacts keep v4 for legacy classification);
  the governor now reports the canonical scalp_v3 for enabled runtime.

## NEXT-AGENT HANDOFF

- Verify the full beforePush gate (pending parallel-server.py WIP completion).
- Real-runtime smoke: run `nexus run --mode PAPER`, open /api/liquidity/state,
  toggle OFF/ON, watch SSE rev increments, confirm Debug tab parity.
- If AGENT-11's server.py canonicalization commits, re-run the API suites.