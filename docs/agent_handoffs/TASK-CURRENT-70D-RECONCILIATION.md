# TASK-CURRENT-70D-RECONCILIATION — Handoff

> Agent: NSE Current-State Reconciliation & Governance Verification · 2026-08-19
> Starting HEAD: 3f3f3d938c4cb959907ae5a01e475c5db1af47ed (origin/main in sync, ls-remote verified)

## 1. Mission

Reconstruct the ACTUAL current 70D state from current code + artifacts + DB +
remote HEAD. Historical reports (TASK-08 "NO CANDIDATE", TASK-09, shadow-live
validation) are evidence, not truth. Fix only PROVEN gaps. No promotion.

## 2. Reconciled truth (verified, not assumed)

| item | value | evidence |
| :--- | :--- | :--- |
| Git | HEAD == origin/main == 3f3f3d9, 0/0 | rev-parse + ls-remote |
| Canonical 70D schema | **scalp_v3**, 70D, hash **235b8fccc96b7e0e** | schema_contract.py, schema.py, dataset manifests, shadow70, TEST-SCHEMA-70D |
| scalp_v3 vs scalp_v4 | SEMANTICALLY DIFFERENT (news vs family @50..59); v4 LEGACY | feature name tuples + hashes |
| Champion | primary_scalp_scalp_v1_50d @ v1.0, scalp_v1/50D/4cls, hash 9105cef7…, **RESTORED_CANDIDATE** (BUG-104) | file sha256 + meta.json + registry |
| 70D datasets | **READY**: ds_d3f35b (1146 rows, real M5) + ds_d3886c (66), both scalp_v3 hash ✓ | dataset manifests + parquet |
| Candidate | **NO_REAL_70D_CANDIDATE**. wf_candidate on disk = smoke artifact: scalp_v4 tag (drift), no hashes, no validation, 1-epoch training, NOT registered | meta.json, registry, integrity probe, governance verify |
| Shadow | NO_VALIDATED_CANDIDATE / IDLE; 2 smoke obs; BUG-105/106 hook fixed in code | shadow70 tables + live_engine source |
| Governance | control plane present (TASK-08); 0 audits, 0 state rows, not frozen; verdict INSUFFICIENT_EVIDENCE | API + DB |
| Latency | LIVE_70D_LATENCY = NOT_MEASURABLE (no candidate); virtual harness p50 0.11ms (NOT live) | 70d_shadow_live_evidence.json |
| UI | mirrors backend truth (shared API contracts); parallel AI-Hub UI WIP untouched | Web/ diffs |
| Proven defects | wf_candidate scalp_v4 tag (drift, smoke), champion RESTORED_CANDIDATE (operator), dataset regime imbalance 96.3% TRENDING (TEST-70D-MODEL-30 fails by design) | executables |

## 3. Delivered

- `docs/70D_CURRENT_STATE_RECONCILIATION.md` — full reconciled state with
  VALUE/SOURCE/VERIFIED_AT/CONFIDENCE table.
- `docs/70D_SCHEMA_REFERENCE_MATRIX.md` — every schema reference classified
  (ACTIVE_RUNTIME/TRAINING/DATASET/SHADOW/GOVERNANCE/LEGACY/INVALID) +
  exact scalp_v3-vs-scalp_v4 comparison.
- `artifacts/validation/70d_current_state.json` — machine-readable current
  state (git/champion/canonical_schema/candidate/dataset/shadow/governance/
  latency/ui/warnings/blockers, each with value+source+verified).
- `artifacts/validation/current_model_inventory.json` — 19 artifacts with
  path/model_id/schema/dimension/classes/scaler/hash/status.
- `tests/unit/test_schema_70d_reconciliation.py` — EXTENDED with
  TEST-CURRENT-70D-01..20 (all 28 tests green).

## 4. Tests run

| suite | result |
| :--- | :--- |
| test_schema_70d_reconciliation (28 incl. TEST-CURRENT-70D) | 28 passed |
| test_shadow70_runtime+safety+news_family+health_drift | 58 passed (earlier run) |
| 70D parity/validation/inference suites (9 files) | 185 passed, 8 skipped, **1 failed: test_70d_model_30_regime_coverage_gate** (PROVEN dataset property: ds_cb30f8 96.3% TRENDING; test fails loudly by design — NOT a code regression; committed at 1270be1) |

## 5. Proven defects NOT fixed (by scope decision)

- wf_candidate scalp_v4 tag: PROVEN drift but the artifact is a smoke probe —
  the correct fix is retraining/re-tagging a real candidate, NOT repairing a
  throwaway. Documented; left for the training task.
- Champion RESTORED_CANDIDATE: operator decision per INV-015 (restore
  external / retrain+promote / accept). NOT auto-resolved.
- ds_cb30f8 regime imbalance: dataset property; TEST-70D-MODEL-30 documents it.

## 6. EXACT NEXT-AGENT INSTRUCTIONS

1. Do NOT promote. Do NOT call /api/models/promotion/execute.
2. The single upstream blocker is a REAL trained 70D candidate. Run the
   A/B/C benchmark (scratch/bench_70d_abc_driver.py) + walk-forward training
   on ds_d3f35b12d63148da (scalp_v3, 1146 rows) with the fair-benchmark
   protocol (docs/MODEL_BENCHMARK_70D_LIQUIDITY.md). The driver trains via
   CandidateTrainer with "ds_70d_abc" datasets.
3. The trained candidate must: schema_id=scalp_v3 (NOT scalp_v4), manifest
   with feature_schema_hash=235b8fccc96b7e0e, artifact_hash, scaler_hash,
   training_dataset_id=ds_d3f35b…, training_commit, validation/OOS/robustness
   results. The wf_candidate smoke artifact must NOT be reused.
4. Register the trained candidate in the model lifecycle registry (status
   CHALLENGER or VALIDATED_CANDIDATE), then POST /api/models/shadow70/attach.
5. Verify shadow observations accumulate (sample floor). Only then is
   promotion preview meaningful; final promotion is human-authorized.
6. Champion identity remains an open operator decision (INV-015) — surface
   it, do not resolve it.
7. Quality gates: ruff check/format src tests, mypy src, pytest unit +
   integration, beforePush.ps1 — separate your failures from parallel WIP.
8. Commit contract: `AGENT-<N>: <imperative>` with Agent/Role/Task/Scope/
   Current HEAD/Evidence/Fix/Tests/Risk/Handoff. Stage only owned files.
   Re-verify `git diff --cached --name-only` before commit (parallel agents
   can wipe the index).
9. New handoff: docs/agent_handoffs/TASK-CURRENT-70D-RECONCILIATION.md (this)
   → supersede with the training task's report when the candidate lands.

## 7. Risk

LOW — this task changed docs + tests only; no production code modified; no
promotion; parallel swarm WIP in the working tree untouched (web AI-Hub UI,
live_engine, server, integrity, cli, forensics, incidents are OTHER agents'
uncommitted changes — do not commit them).