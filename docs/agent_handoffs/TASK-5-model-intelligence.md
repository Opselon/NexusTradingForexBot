# TASK-5 Handoff — Adaptive Model Intelligence / 60D Challenger / Continuous Learning

> Agent: Hermes-ModelIntelligence · TASK-5 · Status: IN_PROGRESS → READY_FOR_REVIEW
> Date: 2026-08-18 · Branch: main · Contract: MASTER MULTI-AGENT CONTRACT v2

## 1. CURRENT CHAMPION (frozen baseline — control group)

| Field | Value |
| :--- | :--- |
| model_id | `primary_scalp` |
| model_version | `1.0.0` |
| artifact path | `artifacts/models/scalp/XAUUSD/v1.0.0/model.pt` |
| feature_schema_id | `scalp_v1` (50D) |
| class_count | 4 logits (NO_TRADE/BUY/SELL/WAIT-policy-bridge) |
| artifact SHA-256 | `f0f70efb1b55855beb96ae807d81b44db07ae4d0fcff1da2965ea0a408f1d88b` |
| scaler SHA-256 | `811554e5286ea3104a9f759ccce611fb62a9994856d08b2dad82aeb6b99424e1` |
| scaler shape | mean(50,), std(50,) |
| snapshot | `docs/task5_champion_baseline.json` |

Champion is UNTOUCHED by TASK-5. `CandidateTrainer` writes only candidate ids
(`cand_*`, `task5_*_v1`); there is no promotion path. VERIFIED.

## 2. CURRENT MODEL CONTRACT

- **Live contract**: `FEATURE_VECTOR_50D` v1 (schema-controlled) — `features/schema.py`
  is the single declaration point; `LiveEngine.FEATURE_DIM` resolves from it.
- **Model manifest**: `MODEL_MANIFEST` v1 (model_lifecycle/ + model_generation/models.py)
  — self-describing artifact (schema/dim/labels/news/dataset/hash).
- **Label schema**: `triple_barrier_3class_v1` (0=NO_TRADE, 1=BUY, 2=SELL; WAIT is
  a POLICY state, never a training label). VERIFIED in `default_label_schema()`.
- **News context**: `news_context_v1` 12-field vector, causally built per sample.
- **INV-009**: 50D/60D/350D ordering is schema-controlled; dimension change is a
  contract change, never a refactor.
- **INV-002**: learning subsystems never hold order authority.

## 3. 60D SCHEMA DESIGN (`scalp_v2`)

`scalp_v2 = scalp_v1 (50D, unchanged) + 10 additional causal features`.

Authoritative registry: `features/schema.py` (dimension=60, supersedes scalp_v1).
Producer: `features/schema_augment.py::compute_60d_extras` — pure numpy, no I/O,
no DB, deterministic; verified identical live/replay/training.

| # | name (feat_N) | semantic | formula | causal source | live | replay | news |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 50 | regime_compression | 10/50 bar range ratio (consolidation) | range(h[-10:],l[-10:])/range(h[-50:],l[-50:]) | completed bars | YES | YES | no |
| 51 | momentum_5_atr | 5-bar price move / ATR | (close[-1]-close[-6])/ATR_14 | completed bars | YES | YES | no |
| 52 | wick_imbalance_5 | mean lower-upper wick imbalance, 5 bars | mean((l-min(o,c))-(max(o,c)-h))/mean(range) | completed bars | YES | YES | no |
| 53 | volume_z_5 | 5-bar volume vs 20-bar ref z | (mean(vol[-5:])-mean(vol[-25:-5]))/std+eps | tick_volume | YES | YES | no |
| 54 | range_z_5 | 5-bar range vs 20-bar ref z (vol burst) | (mean(rng[-5:])-mean(rng[-25:-5]))/std+eps | completed bars | YES | YES | no |
| 55 | clv_avg_5 | mean close-location-value, 5 bars | mean(((c-l)-(h-c))/rng) | completed bars | YES | YES | no |
| 56 | session_phase_enc | deterministic UTC session phase | hour(tick) → {-1, -0.75, 0, 0.25, 0.75, 1} | decision tick | YES | YES | no |
| 57 | price_acceleration | recent 5-bar move minus trend-normalized | (Δ5 - Δ20×0.25)/ATR | completed bars | YES | YES | no |
| 58 | atr_trend_ratio | ATR_14 now / ATR_14 five bars ago | ATR(now)/ATR(then) | completed bars | YES | YES | no |
| 59 | direction_bias_8 | signed 8-bar momentum persistence | sum(sign(c-o) last 8)/8 | completed bars | YES | YES | no |

All missing-data behaviors default to documented constants (never NaN/Inf);
all values clipped to [-3, +3] like the 50D sanitizer. `validate_60d_vector`
enforces exactly 60 finite floats. A 60D model CANNOT be loaded with a 50D
input (runtime manifest dimension gate) and vice versa.

## 4. FEATURE QUALITY RESULTS (spec 5, real M5 dataset)

Computed via `feature_quality_report()` on the real 60D frame (see
`docs/task5_feature_quality_report.md`). Detectors: DEAD / NEAR_CONSTANT /
OUTLIER_DOMINATED / duplicate groups.

## 5. NEWS FEATURE RESULTS (spec 6/33)

- Real news DB export: `build_news_frame_from_db` → 500 analysis rows,
  readiness gate PASSED (real, non-neutral, XAUUSD-relevant, multi-event).
- Matrix cells compare WITHOUT-NEWS vs WITH-NEWS on identical bars/split/labels.
- News is never forced: `news_benchmark_readiness` REJECTS synthetic/dead frames.

## 6. DATASET RESULT

- 50D baseline: `ds_cb30f87520e9e6a4` (Data Gate, 99,946 M5 rows).
- 60D dataset: built from the SAME raw M5 bars via `compute_60d_frame`,
  deterministic id, manifest `feature_schema_id=scalp_v2`, 60 feat columns,
  chronological split (70/15/15), purge/embargo 3, friction $0.35.
- Provenance: `DatasetManifest` (id, hash, schema, rows, range, splits,
  news digest, source commit).

## 7. TRAINING RESULT (spec 42 one controlled experiment)

See `docs/task5_experiment_report.md` (4 cells A/B/C/D, epochs=6, seed=42).

## 8. CALIBRATION RESULT (spec 14)

Validation now enforces ECE floor (0.15) + per-class metrics; overconfident
models are REJECTED.

## 9. BASELINE VS CHALLENGER

No candidate passed all gates → no CHALLENGER was created. Honest verdicts
per cell (see experiment report).

## 10. OOS RESULT

OOS gates: accuracy floor 0.30, macro-F1 floor 0.34 (above the 0.333
no-information baseline), balanced-accuracy floor 0.34, min-evidence 100 rows.

## 11. ROBUSTNESS RESULT

Robustness failures / class collapse / regime collapse block the challenger
gate (existing Phase 10 gates + new TASK-5 floors).

## 12. DRIFT RESULT

`detect_feature_drift` / `detect_prediction_drift` verified by tests
(TEST-MG-23/24); drift is an ALERT/RESEARCH trigger, never auto-retrain.

## 13. REPLAY PARITY RESULT

`LocalModelRuntime.predict` uses the SAME scaler transform as replay
(`SampleReplay`); TEST-MG-22 verifies predicted class equality.

## 14. SHADOW RESULT

Shadow mode (Phase 11) exists: Challenger runs on the SAME live feature
vector, has ZERO order authority (INV-002), records champion_ref + decision.
TASK-5 did not enable a new shadow run; TEST-MG-28 verifies no-order capability.

## 15. WORKER RESULT

`TrainingWorker` status is now TRUTHFUL: `DISABLED` (auto_train off),
`TRAINING` (inflight), `RUNNING`, `IDLE`. LiveEngine configures
`auto_train_enabled=False` → the API reports DISABLED, not fake RUNNING.
TEST-MG-25 verifies.

## 16. ARTIFACT INTEGRITY

- SHA-256 artifact hash checked on load (manifest vs file) — tamper → load fails.
- Scaler declared-in-manifest but missing → load fails.
- Schema/dimension mismatch → load fails.
- TEST-MG-20 verifies tamper blocks load.

## 17. BUGS FOUND (see agents/bugs.md)

- BUG-083: 60D path did not exist (no producer) — schema declared but
  unbuildable; audit-gap, fixed by schema_augment.py + schema_v2.py.
- (Real-data finding) log-return/ATR momentum features are ~0.0003-scale on
  M5 gold — near-dead; fixed to price-difference/ATR form during design.
- (Real-data finding) first-5-values duplicate detection is NOT proof of
  feature uniqueness — replaced with full-column equality in
  `verify_60d_artifact`.

## 18. BUGS FIXED

- Fixed `_atr` window broadcast crash (schema_augment) — pad prior-close
  vector to the bar-window width.
- Fixed `schema_v2.compute_60d_frame` polars dtype/named-row/mutable-n
  defects surfaced by the real run.

## 19. TESTS ADDED

35 TASK-5 tests (TEST-MG-01..30 mapped) appended to
`tests/unit/test_model_generation_phase13.py::TestTask5*`:
schema authority/ordering/dimension-reject, quality audit (dead/constant/
duplicate), 60D dataset provenance/reproducibility, candidate identity
determinism, no-news/news identical split, non-finite input/labels fail,
failed-run never challenger, negative-OOS/macro-F1/balanced/ECE/min-evidence
gates, artifact tamper, DB-free prediction, replay parity, feature/prediction
drift, worker truthfulness, champion immutability, shadow no-orders, rollback.

## 20. TEST RESULTS

- `test_model_generation_phase13.py` (incl. TASK-5): all pass.
- `test_model_benchmark_phase13b.py`: all pass.
- `test_model_lifecycle_phase10.py`, `test_scalp_features*.py`: all pass.
- Full gate: see section 24.

## 21. PERFORMANCE IMPACT

- 60D feature computation: ~1.7 min for 100k M5 bars (one-off, background).
- No changes to the tick hot path; no new synchronous I/O in live inference.
- Training stays background/isolation-safe (asyncio.to_thread, bounded).

## 22. CHAMPION SAFETY RESULT

PROVEN: Champion artifact hash verified before AND after TASK-5
(`docs/task5_champion_baseline.json`); candidate training writes only
candidate ids; no promotion path exists; LiveEngine continues to load
`primary_scalp` v1.0.0.

## 23. REMAINING RISKS

- 60D candidate results are point estimates on ONE historical dataset (M5).
- News DB export is bounded (limit=2000) — full-history export should be
  reviewed by TASK-6 for the shadow/ablation rerun.
- Statistical significance (bootstrap/CI) not yet implemented — flagged
  INCONCLUSIVE where insufficient.
- Live 60D inference requires an operator-approved promotion (TASK-6).

## 24. FILES CHANGED

- src/nexus_scalp/features/schema_augment.py (NEW — 60D producer)
- src/nexus_scalp/model_generation/schema_v2.py (NEW — 60D dataset builder)
- src/nexus_scalp/features/schema.py (60D entry description)
- src/nexus_scalp/model_generation/training.py (loss/grad gates, deterministic id)
- src/nexus_scalp/model_generation/validation.py (macro-F1/balanced/ECE/
  min-evidence gates)
- src/nexus_scalp/model_generation/benchmark.py (8-cell fair matrix)
- src/nexus_scalp/model_generation/__init__.py (exports)
- src/nexus_scalp/model_lifecycle/worker.py (truthful status)
- tests/unit/test_model_generation_phase13.py (TASK-5 tests)
- scratch/task5_experiment_60d_vs_50d.py + .out.txt (controlled experiment)
- scratch/task5_freezing_champion_snapshot.py + docs/task5_champion_baseline.json
- docs/task5_experiment_report.md, docs/task5_feature_quality_report.md
- docs/agent_handoffs/TASK-5-model-intelligence.md (this file)
- agents/taskboard.md, agents/change_control.md, agents/contracts.md,
  agents/runtime_invariants.md (if changed), agents/repository_state.md,
  agents/bugs.md (BUG-083), agents/skill.md

## 25. COMMITS

- (single coherent commit; see git log)

## 26. HANDOFF TO TASK-6 (Live Model Governance / Shadow Runtime)

EXACT NEXT-AGENT INSTRUCTIONS:
1. Review the 8-cell benchmark matrix runner (`benchmark.py` MATRIX) and the
   experiment report before designing the live governance gate.
2. The Champion pointer (`artifacts/models/scalp/XAUUSD/v1.0.0`) must remain
   intact; any promotion needs the MODEL_PROMOTION_STATE_MACHINE you own.
3. The 60D challenger, if any candidate ever passes ALL gates (incl. the new
   macro-F1/balanced/ECE/min-evidence floors), must enter SHADOW first —
   the shadow engine already records the same live feature vector.
4. Do NOT weaken the new validation floors to make a candidate appear (the
   ledger records 88% NO_TRADE; accuracy ≈ 0.88 is a class-collapse trap).
5. Live 60D inference requires: operator approval + a NEW champion artifact
   built by the 60D producer (schema_augment + schema_v2), verified by
   `verify_60d_artifact`, with the manifest feature_dimension=60.
6. Statistical significance (bootstrap/CI) is the top remaining evidence gap.
7. Worker status: API exposes DISABLED/IDLE/RUNNING/TRAINING truthfully —
   keep it that way; no hidden training.