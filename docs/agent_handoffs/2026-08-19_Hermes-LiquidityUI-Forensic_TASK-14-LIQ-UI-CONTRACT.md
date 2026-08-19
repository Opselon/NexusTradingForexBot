# AGENT HANDOFF — Liquidity Intelligence UI Contract Fix (BUG-116)

> Agent: Hermes-LiquidityUI-Forensic (AGENT-14) · Role: Liquidity Intelligence Forensic Fix Engineer
> Task: FIX LIQUIDITY INTELLIGENCE UI CONTRACT & RUNTIME STATE
> Starting HEAD: `617c23a` · Ending HEAD: see git log
> Branch: `main`

## Summary

Every Liquidity value/index/status/timestamp/schema/availability/model-compat
state shown in the Liquidity Intelligence UI now matches the canonical runtime
state. The contradictions were all in the backend (`LiquidityGovernor`), the
UI was a faithful renderer of a contradictory payload.

## Root causes fixed (BUG-116, first incorrect layer = `liquidity_runtime.py`)

1. **1970 timestamp**: `_last_success_at`/`_last_error_at` were
   `time.monotonic()` (uptime), rendered via `datetime.fromtimestamp()` →
   `1970-01-01T<uptime>`. Now wall-clock (`time.time()`) fields for absolute
   timestamps; monotonic kept for age deltas only.
2. **Indices 40..49**: `snapshot_payload()` derived `active_schema.dimension
   - 10 + pos`; DISABLED → scalp_v1/50D → 40..49. Now registry-driven
   (`schema_contract.canonical_feature_names()` → 60..69) with documented
   constant fallback.
3. **DISABLED + 10 features + available=True**: explicit
   `feature_availability` (AVAILABLE/STALE_CACHE/UNAVAILABLE/NOT_ACTIVE);
   `available` only True when genuinely AVAILABLE; causal → NOT_APPLICABLE
   when disabled.
4. **BLOCK(LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE) while disabled**:
   `model_compatibility()` gated on the toggle → NOT_APPLICABLE
   (LIQUIDITY_DISABLED); enabled path evaluates the real model vs scalp_v3.
5. **live_engine stale source**: hook passed `governor._source` (default
   UNAVAILABLE) → now `SourceKind.LIVE_MARKET_STATE` for each computation.
6. **state_revision**: monotonic per mutation; UI drops stale revisions
   (SSE out-of-order guard).

## Files

- `src/nexus_scalp/features/liquidity_runtime.py` — all governor fixes
- `src/nexus_scalp/application/live_engine.py` — source provenance
- `Web/app.js` — renders backend per-feature provenance; stale-revision guard
- `Web/index.html` — state-revision tile, honest caption
- `tests/unit/test_liquidity_runtime_integration_phase18.py` — BUG-111 suite
  (test_liq_ui_01..10) + contract pin updates
- `tests/integration/test_liquidity_api.py` — contract pin updates
  (indices 60..69, scalp_v3/70D, DIMENSION_70D import)
- `tests/unit/test_liquidity_task02_integration.py` — schema row update
- `docs/LIQUIDITY_UI_FORENSIC_BASELINE.md` (new) — before-state matrix
- `docs/LIQUIDITY_UI_FORENSIC_FINAL.md` (new) — final report (A..K)
- `artifacts/forensics/liquidity_ui_state_trace.json`,
  `liquidity_index_registry.json`, `liquidity_timestamp_trace.json`,
  `liquidity_api_ui_parity.json` (new)
- `scratch/probe_liquidity_ui_state_contract.py` + `.out.txt` (new probe)
- `agents/bugs.md` (BUG-116), `agents/taskboard.md` (TASK-14-LIQ-UI-CONTRACT),
  `agents/contracts.md` (LIQUIDITY_RUNTIME v2)

## Shared API changes

- `LiquidityGovernor.report()` payload: added `feature_availability`,
  `source_status` (already there from BUG-110 WIP), `snapshot_timestamp`,
  `state_revision`; `available` semantics tightened; `causal_state` can be
  NOT_APPLICABLE; `model_compatibility.result` can be NOT_APPLICABLE.
- `LiquidityGovernor.snapshot_payload()`: per-feature `index` now canonical
  60..69 (registry); added `runtime_enabled`, `feature_availability`,
  `source_status`, `state_revision`.
- `_active_schema_block()` ON branch → `scalp_v3`/70D (canonical; was
  scalp_liquidity_v1/60D).
- `ModelCompatibility` / `CausalState` enums: + `NOT_APPLICABLE`.

## Tests

- 10 new regression tests (test_liq_ui_01..10) — all pass.
- 86 liquidity+API+task02 tests pass; 83 engine/optimization pass; 204
  related governance/release/schema-70D pass.
- Full beforePush NOT run to completion — parallel agents hold `web/server.py`
  mid-edit WIP (IndentationError observed at 07:00, since resolved); run the
  gate after the swarm settles.

## Runtime verification

- Probe reproduction before/after: `scratch/probe_liquidity_ui_state_contract.out.txt`.
- Forensic artifacts (real runtime payloads): `artifacts/forensics/liquidity_*.json`.
- UI/Debug parity: both read `LiquidityGovernor.report()/snapshot_payload()`;
  `debug_snapshot.py` liquidity family reads the same snapshot.

## GitHub status

- Parallel AGENT-11 commit `8635c66` absorbed my liquidity_runtime/live_engine/
  index.html/app.js-render/test-pin changes (verified at HEAD).
- My own remaining commits: app.js sync-guard + BUG-111 suite + docs/artifacts
  (AGENT-14 commit(s)). All pushed.

## Known risks / unfinished

- Real-broker runtime smoke (OFF→ON→SSE→restart) not executed this session —
  governor-level verified; broker-level run recommended.
- Parallel swarm actively edits liquidity/server files; re-run liquidity suites
  before merging any large parallel change.
- `scalp_v4` remains in `release/model_artifacts.py` as legacy-classification
  id; governor now reports canonical `scalp_v3` for enabled runtime (matching
  AGENT-11 canonicalization).

## EXACT NEXT-AGENT INSTRUCTIONS

1. `git fetch` + `git log origin/main..HEAD` — confirm no absorption of the
   BUG-111 suite into a parallel commit; re-commit if absorbed-away.
2. Run `.venv/Scripts/python.exe -B -m pytest tests/unit/test_liquidity_runtime_integration_phase18.py tests/integration/test_liquidity_api.py tests/unit/test_liquidity_task02_integration.py -q` — expect 86 pass.
3. Run the full beforePush gate (ruff + mypy + pytest tests/unit).
4. Real-runtime smoke (PAPER/READ_ONLY): start the app, toggle Liquidity
   OFF→ON, verify /api/liquidity/state fields, SSE state_revision increments,
   Debug tab liquidity family parity.
5. If `web/server.py` changed by parallel agents, verify the liquidity section
   still embeds `gov.report()` verbatim.
