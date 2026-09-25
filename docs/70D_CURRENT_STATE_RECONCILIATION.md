# 70D CURRENT STATE RECONCILIATION — Nexus Scalp Engine (NSE)

Generated at: 2026-08-19 06:55 (+03:30)
Git HEAD: 3f3f3d938c4cb959907ae5a01e475c5db1af47ed
origin/main: 3f3f3d938c4cb959907ae5a01e475c5db1af47ed (ls-remote verified, in sync, 0/0)
Remote verified: YES
Runtime status: engine not running in this session; state reconstructed from DB/artifacts/code (read-only)

> Agent: NSE Current-State Reconciliation & Governance Verification
> Method: current code + current artifacts + current database + current remote
> HEAD — historical reports treated as evidence, not truth.

---

## 1. Current Champion

| item | VALUE | SOURCE | VERIFIED_AT | CONFIDENCE |
| :--- | :--- | :--- | :--- | :--- |
| model_id | primary_scalp_scalp_v1_50d | experience_model_registry | 06:45 | HIGH |
| version | v1.0 | registry / meta.json | 06:45 | HIGH |
| path | artifacts/models/scalp/XAUUSD/v1.0.0/model.pt | config + registry | 06:45 | HIGH |
| SHA256 | 9105cef7d93e23b8a7d529ef797efba9283f36bc8190d2729909c1fc95634d2f | sha256sum (file) | 06:45 | PROVEN |
| schema | scalp_v1 / 50D / 4-class | meta.json + integrity probe | 06:45 | PROVEN |
| scaler | 50D | sha256 + npz shape | 06:45 | PROVEN |
| runtime loaded | YES (engine start path loads this path; not executed this session) | code inspection | 06:45 | HIGH |
| vs task5 baseline | **NOT byte-identical** — current 9105cef7 ≠ baseline f0f70efb | hash comparison | 06:45 | PROVEN |
| status | **RESTORED_CANDIDATE** (BUG-104: bench_a_v1-derived 50D weights) | model.meta.json status field | 06:45 | PROVEN |

## 2. Canonical 70D schema

```
CANONICAL_70D_SCHEMA = scalp_v3
dimension           = 70
feature_schema_hash = 235b8fccc96b7e0e
layout              = BASE 0..49 | NEWS 50..59 (news_state@59) | LIQUIDITY 60..69
```

Proven by: schema_contract.py SCHEMA_ID, schema.py registry, BOTH 70D dataset
manifests (feature_schema_hash == computed hash), shadow70 SHADOW70_SCHEMA_ID,
governance verify (runtime_schema_id), TEST-SCHEMA-70D-01..08 (58 tests green).

## 3. scalp_v3 vs scalp_v4

**SEMANTICALLY DIFFERENT.** Both are 70D, but scalp_v3's 50..59 = NEWS 10D
(canonical contract), scalp_v4's 50..59 = FAMILY 10D (TASK-02 integration
placeholder). Hashes differ (235b8fcc vs f97338e2). scalp_v4 has NO dataset,
NO runtime, NO shadow consumer, NO validated model. Full comparison:
`docs/70D_SCHEMA_REFERENCE_MATRIX.md`.

## 4. Datasets

| dataset_id | schema | rows | hash | schema_hash | quality |
| :--- | :--- | :--- | :--- | :--- | :--- |
| ds_d3886c503d6c0901 | scalp_v3 | 66 | 394b5194… | 235b8fccc96b7e0e ✓ | synthetic-ish (M1 window) |
| ds_d3f35b12d63148da | scalp_v3 | 1146 | aad73c8f… | 235b8fccc96b7e0e ✓ | real XAUUSD M5, finite, in-range, 0 dup timestamps |

**Current dataset state: READY** (real 70D scalp_v3 datasets exist with correct
schema hash). Note: ds_d3f35b manifest temporal_range shows 1970-01-01 — a
known naive-datetime writer artifact (flagged in TASK-09, not repaired).

Dataset/runtime parity: PROVEN GREEN by TASK-03 parity suites (68 passed/3
skipped at TASK-09 verification) + artifacts/validation/70d_liquidity_parity.json
(real-broker probe, 0 mismatches, exact=True, schema hash 235b8fcc…).

## 5. Candidate

**NO_REAL_70D_CANDIDATE.** The only 70D artifact on disk is wf_candidate:

| item | value |
| :--- | :--- |
| path | artifacts/model_generation/models/wf_candidate/model.pt |
| sha256 | 9265e4b7c88089c6… |
| schema declared | **scalp_v4** (drift vs canonical scalp_v3) |
| dimension / classes / scaler | 70 / 4 / 70 (tensor + scaler verified) |
| registered | NO (no lifecycle row, no training_run, no candidate row) |
| training | 1 epoch/fold × 3 folds, seed 42, purge/embargo 5, clip [-5,5] — **smoke config** |
| validation / OOS / robustness | NONE |
| feature_schema_hash / dataset_id | NONE in manifest |
| shadow attach | REJECTED (VALIDATION_STATUS_VALID gate: NOT_VALIDATED) |
| governance | FAIL on: artifact_hash_matches, manifest_valid, schema_matches_runtime, scaler_valid, feature_schema_hash_matches; SKIP on training/OOS/robustness/shadow/drift |

**Verdict: DISCOVERED / NOT_VALIDATED / NOT_ELIGIBLE.** It is a BUG-103/104
pipeline-smoke artifact, NOT the A/B/C benchmark candidate. The real A/B/C
benchmark + walk-forward training on ds_d3f35b is **NOT_RUN** (TASK-04
execution pending).

## 6. Shadow state

| item | value |
| :--- | :--- |
| runtime | NO_VALIDATED_CANDIDATE / IDLE |
| observations | 2 (both smoke_24 fixtures, SHADOW_BLOCKED, model_id empty) |
| worker | not running (nothing attached) |
| hook (BUG-105/106) | VERIFIED fixed: `_record_shadow70_observation` independent of 50D shadow, canonical `feature_schema_hash()`, `build_70d_vector` strict 50+10+10 |
| sample floor | NOT SATISFIED (no candidate) |

Shadow is IDLE truthfully. LIVE_70D_LATENCY = NOT_MEASURABLE (no real
candidate attached). Virtual harness: p50 0.11ms / p95 0.41ms / p99 2.66ms /
max 5.00ms (120 deterministic observations — NOT live-tick evidence).

## 7. Governance

| item | value |
| :--- | :--- |
| status API | present (TASK-08) |
| promotion preview | present |
| promotion execute | present (actor + approval token required) |
| audits / state rows | 0 / 0 (no promotion executed) |
| frozen | false |
| gate matrix | see below |

| Gate | Status |
| :--- | :--- |
| Technical artifact | PASS |
| Dataset | PASS |
| Training | NOT_RUN (no real candidate) |
| Walk-forward | NOT_RUN |
| OOS | NOT_RUN |
| Robustness | NOT_RUN |
| Calibration | NOT_RUN |
| Shadow | NO_VALIDATED_CANDIDATE |
| Drift | NO_VALIDATED_CANDIDATE |
| News contract | PASS |
| Liquidity contract | PASS |

**PROMOTION VERDICT: INSUFFICIENT_EVIDENCE / NO_CANDIDATE. Nothing promoted.**
No governance freeze active. verify_candidate with runtime_schema_id=scalp_v3
correctly reports the wf_candidate as NOT ELIGIBLE.

## 8. Latency

- LIVE_70D_LATENCY = **NOT_MEASURABLE** (no real candidate attached to the
  live shadow70 runtime).
- Measured proxies (existing evidence): 70D assembly p50 ≈ 4.1ms (TASK-03
  perf probe); virtual observation harness p50 0.11ms (synthetic). Neither is
  a live-tick end-to-end measurement; explicitly NOT fabricated.

## 9. UI / AI Hub

- Model Governance panel: present (TASK-08 promotion controls, freeze badge,
  preview/promote).
- Shadow70 panel: present, reports IDLE/NO_VALIDATED_CANDIDATE truthfully.
- Liquidity panel: backend-derived (schema/indices from canonical state).
- AI Hub/debug additions: parallel-agent WIP in working tree (AI view toggle,
  model-input debug). All UI states derive from canonical backend responses;
  no hardcoded model-state assumptions observed.
- **UI mirrors backend truth: YES** (verified via shared API contracts; a live
  browser session was not run in this environment).

## 10. Liquidity / News contract states

- liquidity_features_enabled: **false** (configs/base.yaml) — the Liquidity
  Intelligence toggle is OFF; the 70D liquidity block is produced lazily only
  when enabled + producer exists. No contradiction (DISABLED + no 70D active).
- News: enabled/disabled handled by the canonical news context; news_state at
  index 59 verified in the canonical names (TEST-SCHEMA-70D-02, TASK-10 fix
  still valid under scalp_v3).

## 11. Proven defects found (vs hardened findings)

| item | classification |
| :--- | :--- |
| wf_candidate tagged scalp_v4 (schema drift) | PROVEN (artifact manifest vs canonical) — but it's a smoke artifact; the correct action is retraining/re-tagging, NOT repairing the artifact |
| ChAMPION still RESTORED_CANDIDATE | PROVEN RECOVERY STATE — operator decision required (INV-015); documented, not auto-resolved |
| 50D dataset regime imbalance (96.3% TRENDING) | PROVEN DATASET PROPERTY — TEST-70D-MODEL-30 fails loudly by design (documented limitation, not a code bug) |
| governance/evidence 50-width cap + validation_result forcing | checked: present as DEFENSE-IN-DEPTH in load gate & verification; NOT a proven bug in current code paths (verify_gate behavior verified) |
| BUG-108 migration | PROVEN FIXED (38 migration tests pass at current HEAD) |

## 12. Blockers (exact)

1. **Real 70D candidate does not exist.** The A/B/C benchmark training on the
   real scalp_v3 dataset (ds_d3f35b) has not run. This is the single upstream
   blocker for: candidate validation → OOS → robustness → calibration →
   shadow floor → eligibility.
2. wf_candidate is unusable as-is (wrong schema id, no manifest hashes, no
   validation, smoke training config).
3. Champion identity pending operator decision (external restore / accept /
   retrain-promote per INV-015).

## 13. Verdict

```
CURRENT_70D_STATE_RECONCILED
```
(Single canonical schema established; all components verified; the pipeline
is blocked on REAL CANDIDATE EVIDENCE — not on schema ambiguity.)