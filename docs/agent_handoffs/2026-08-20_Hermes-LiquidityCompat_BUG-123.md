# AGENT HANDOFF — BUG-123 Liquidity-Model Compatibility (2026-08-20)

Agent: Hermes-LiquidityCompat
Role: Liquidity-Model Compatibility Engineer
Task: BUG-123 (Liquidity Intelligence BLOCK investigation, 70D contract repair)

## Starting/Ending HEAD
- START: 7ce7198 (clean main); END: 774c5db (pushed, origin/main in sync)
- Commits (all pushed): 76ac71f (runtime engine), a62b80e (companion server/cli/order_manager/research from stash), b75d940 (regression suite), 774c5db (UI cells)

## Files
- src/nexus_scalp/features/liquidity_runtime.py — contract-based resolve_model_compatibility, model_schema_family, build_model_compatibility_contract, LiquidityGovernor._model_contract/compatibility_contract/model_compatibility, report() liquidity_contract + snapshot_coherence_revision, snapshot_payload per-value normalization/validity
- Web/index.html — Model Contract + Compatibility Reason cells
- Web/app.js — Model Contract/Reason rendering + State Revision row (absorbed into parallel commit 32547e9)
- tests/unit/test_liquidity_runtime_integration_phase18.py — test_liq_bug123_01..16 + legacy reason updates
- tests/integration/test_liquidity_api.py — reason assertions updated
- scratch/fix_70d_proof_artifact.py — builds artifacts/model_generation/models/liq70_proof (scalp_v3 70D, canonical hash 235b8fccc96b7e0e, LocalModelRuntime-loadable)

## Root Cause (CASE A confirmed)
Production champion = scalp_v1/50D (artifact input_projection.weight (128,50)); Liquidity enabled demands scalp_v3/70D. The 2026-08-19 BLOCK(LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE) was a REAL incompatibility, but the reason was generic, the model contract came from stale engine class attrs, and the canonical runtime contract (feature-order hash/normalization/dtype/indices) was never articulated.

## Fix
Contract-based verdict with family/dim/tensor/hash gates; diagnostic reasons (MODEL_INPUT_DIMENSION_MISMATCH, SCHEMA_VERSION_MISMATCH, MODEL_DIMENSION_EXCEEDS_RUNTIME, MODEL_TENSOR_DIMENSION_MISMATCH, NO_MODEL_METADATA->UNKNOWN, SCHEMA_DIMENSION_MATCH->PASS); REAL artifact model contract (champion + tensor width + hash/version/id); canonical liquidity_contract + snapshot_coherence_revision in report(); UI contract cells + State Revision. Compatibility recomputed per report (no stale cache); the 50D champion stays BLOCK truthfully until a scalp_v3 70D model is deployed.

## Verification
- 73 tests pass (57 unit + 16 integration)
- Real proofs: liq70_proof artifact (scalp_v3 70D, tensor 70, LocalModelRuntime predict OK); 50D champion artifact -> BLOCK (guard real)
- beforePush: ruff/mypy/pytest run on the changed file set; full gate may be blocked by parallel WIP

## Shared / Architecture
- LIQUIDITY_70D contract reason vocabulary CHANGED (old generic reason removed)
- INV-022 added; BUG-123 registered; CHG-0019 registered; taskboard row added

## EXACT NEXT-AGENT INSTRUCTIONS
1. To make the UI show COMPATIBLE: deploy a validated scalp_v3/scalp_v4 70D model to artifacts/models/scalp/XAUUSD/v1.0.0/model.pt (with model.scaler.npz 70D) or promote via the governance pipeline (verify_candidate runtime_schema_id=scalp_v3). The 70D candidate does NOT yet exist (wf_candidate is scalp_v4 smoke, unregistered — docs/70D_CURRENT_STATE_RECONCILIATION.md).
2. Do NOT weaken the compatibility gate; the block is truthful until a 70D model is live.
3. If a 70D model is promoted, registry rows (lifecycle CHAMPION, provenance) must carry feature_schema_id=scalp_v3 and build_metadata.input_dimension=70 for the checker to PASS.