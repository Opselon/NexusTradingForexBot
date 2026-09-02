# TASK-09 — 70D CANDIDATE VALIDATION / SHADOW / GOVERNANCE READINESS — EVIDENCE EXECUTION REPORT

> Agent: AGENT-09 | 2026-08-19 | branch `main`
> Continuation of the evidence-chain documentation (TASK-09 VERIFIED,
> NO_CANDIDATE) — this session EXECUTED the missing chain steps:
> OOS (true temporal), manifest-pipeline fix (BUG-117), governance preview.

---

## 1. BUG-106 — FIRST GATE (status: FIXED, re-verified)

- Production fix (TASK-05): `LIQUIDITY_HISTORY_LIMIT=4000` bounded causal
  window — verified present in schema_v2.py (lines 349/420/620), aligned
  with the live aggregator 4000-bar cap (train == live parity).
- Root cause confirmed by my own cProfile: `compute_liquidity_features`
  = 87.7% of runtime at 400 rows (update_pool_states 3.35s, _bars_to_arrays
  4.46s cumulative, detect_confirmed_swings 2.26s) — full-history rescan
  per row.
- Measured (this session): canonical full-history 1000 rows = 82.7s
  (12 rows/s); bounded builder 1000 rows = 73.25s (14 rows/s);
  **incremental builder (byte-identical) 1000 rows = 25.18s (40 rows/s)** —
  2.9x vs bounded, 3.3x vs full-history.
- **Parity: 0 feature diffs / 24,220 cells** between canonical and
  incremental on real XAUUSD M5 (pool ORDER matched — canonical sh→sl
  order; internal_external_distances uses users[-20:]).
- artifacts/benchmarks/bug106_profile.json written (gitignored dir,
  evidence mirrored in scratch/bug106_*.out.txt + docs).

## 2. PARITY — dataset == replay == inference == runtime

- Re-verified via the incremental-builder equivalence proof + the existing
  TASK-03 parity suites (68 passed / 3 skipped per the prior TASK-09
  report; test_70d_parity_task3.py etc. present).
- The 70D contract layout verified from executable source:
  feat_0..49 Base | feat_50..59 News 10D | feat_60..69 Liquidity 10D
  (compute_70d_frame lines 639-646).

## 3. REAL 70D DATASET

| Field | Value |
| :--- | :--- |
| dataset_id | ds_task5_real70d_2500 (real, verified by AGENT-05) |
| source | data/raw/XAUUSD_M5.parquet (real broker history) |
| symbol / timeframe | XAUUSD / M5 |
| rows | 2,446 (2446-row verified subset; purged triple-barrier labels) |
| schema | scalp_v3 (70D) |
| dimension | 70 |
| label schema | triple_barrier_3class_v1 (purge 3 / embargo 3) |
| labels | 2197 NO_TRADE / 139 SELL / 110 BUY (88% NO_TRADE — class imbalance confirmed) |
| verify_70d_artifact | PASS (dimension/schema hash/finite/in-range/no dup timestamps) |
| news | FEATURE_DISABLED (neutral zeros; real news DB covers only 2026-08-17..18 — documented missing-value policy; NEWS_INCONCLUSIVE_NO_OVERLAP class) |

An additional 20K-bar real dataset build (ag09_real_70d_v1, incremental +
parity self-check) was started; at ~20 min it had not completed (the
incremental builder's per-row pool-state recompute remains the cost
driver). The 2,446-row verified dataset is the evidence basis — bounded,
real, verified.

## 4. FAIR A/B/C — executed by AGENT-05 (c5d6739), confirmed here

Identical budgets/splits/labels (epochs=6, lr=1e-3, seed=42, val n=490):

| Cell | Schema | val_acc | macro-F1 | ECE |
| :--- | :--- | :--- | :--- | :--- |
| A (50D base) | scalp_v1 | 0.2408 | 0.1815 | 0.105 |
| B (60D base+extras) | scalp_v2 | 0.2388 | 0.1800 | 0.111 |
| C (70D base+news+liq) | scalp_v3 | 0.2041 | 0.1538 | 0.146 |

**VERDICT: NEGATIVE for 70D** (delta C−B macro-F1 = −0.0262; calibration
worsened). Liquidity 10D did NOT add predictive value on this slice.
Models: task5_abc_A/B/C_v1.

## 5. TRUE TEMPORAL OOS (this session — OOS LOCK)

The benchmark eval split is INTERSPERSED (not temporal). A true OOS must
be the last temporal window never seen by training. Executed:
- Split ds_task5_real70d_2500 at 2025-03-24 (train 2,129 rows < cutoff;
  OOS 317 rows = 284 NO_TRADE / 18 SELL / 15 BUY).
- Retrained C with IDENTICAL hyperparameters (no tuning) → ag09_oos_C_v1.
- Evaluated on the untouched tail.

**Result (OOS LOCKED):**
```
accuracy            0.1451   (floor 0.30 → FAIL)
balanced accuracy   0.3315   (floor 0.34 → FAIL)
macro-F1            0.1013   (floor 0.34 → FAIL; no-info baseline 0.333)
ECE                 0.3207   (floor 0.15 → FAIL)
Brier               0.7165
confusion           30/232/22 (NO_TRADE mostly predicted SELL — degenerate)
```
The 70D candidate COLLAPSES on genuinely-unseen future data — all
validation gates FAIL. artifact_hash cfc3b1f4… (deterministic, re-run
identical). Recorded: artifacts/validation/70d_oos_results.json.

## 6. ROBUSTNESS (feature-perturbation sweep, scaled-space)

| level | acc | macro-F1 | Δacc |
| :--- | :--- | :--- | :--- |
| none | 0.5034 | 0.5187 | 0.0 |
| small (0.01σ) | 0.5017 | 0.5171 | −0.0017 |
| medium (0.03σ) | 0.5034 | 0.5195 | 0.0 |
| large (0.05σ) | 0.5068 | 0.5233 | +0.0034 |

Note: this sweep ran on the INTERSPERSED validation split (informative for
stability, NOT the temporal OOS). On the temporal OOS the model is already
below all floors; perturbation deltas are secondary. Recorded:
artifacts/validation/70d_robustness_results.json (written by the OOS
script's companion probe).

## 7. CALIBRATION

- On the interspersed eval split: ECE 0.0692, Brier 0.5833,
  well_calibrated=True — the model's probabilities were calibrated ON THE
  SPLIT IT SAW (not meaningful for generalization).
- On the TRUE temporal OOS: ECE 0.3207, Brier 0.7165 — NOT calibrated on
  unseen data. The honest calibration verdict is FAIL on temporal OOS.

## 8. CANDIDATE REGISTRY + MANIFEST (BUG-117 FIXED)

- `governance/verify.py` reads provenance at manifest TOP-LEVEL; the
  trainer wrote only build_metadata and ModelManifest lacked two fields →
  every candidate FAILed the gates regardless of science. **BUG-117**.
- Fix: ModelManifest += liquidity_algorithm_version + training_commit
  (additive); CandidateTrainer resolves feature_schema_hash (canonical
  contract), git HEAD, liquidity algorithm version, writes TOP-LEVEL +
  stamps oos_artifact/robustness_artifact via
  experiment.training['evidence'].
- After fix, ag09_oos_C_v1 manifest: feature_schema_hash 235b8fcc ==
  canonical ✓, liquidity_algorithm_version 70d-v1.0.0 ✓, training_commit
  e6f8449 ✓, oos_artifact artifacts/validation/70d_oos_results.json ✓.

## 9. GOVERNANCE PREVIEW (read-only, promotion NOT called)

| Gate | task5_abc_C_v1 | ag09_oos_C_v1 |
| :--- | :--- | :--- |
| artifact_exists / hash | PASS | PASS |
| manifest_valid | PASS | PASS |
| schema_registered / runtime / dimension | PASS | PASS |
| scaler_valid | PASS | PASS |
| feature_schema_hash_matches | FAIL | **PASS** |
| liquidity_version_matches | FAIL | **PASS** |
| training_commit_recorded | FAIL | **PASS** |
| oos_artifact_recorded | SKIP | **PASS** |
| shadow_evidence_recorded | SKIP | SKIP |
| news/liquidity contract | PASS | PASS |
| **ELIGIBLE** | **NO** (FAILs) | **NO — INSUFFICIENT_EVIDENCE** (shadow SKIP) |

ag09_oos_C_v1 passes every technical gate; only shadow evidence is
missing (requires a validated candidate in live shadow — and the temporal
OOS already fails the scientific gates). Promotion was NEVER called.

## 10. VERDICT

```
NO_CANDIDATE (no validated, scientifically-viable 70D candidate)
INSUFFICIENT_EVIDENCE (shadow sample floor not met)
NOT_ELIGIBLE (temporal OOS fails all validation floors)
```

The 70D/liquidity evidence chain is now COMPLETE and NEGATIVE:
- A/B/C fair benchmark: 70D does not beat 60D on the real slice (−0.026).
- True temporal OOS: the 70D candidate degenerates (macro-F1 0.10).
- Governance: gates now mechanically pass for the manifest; science does
  not.

This is the brief's valid success state: the evidence is sufficient for
governance to make a REAL decision — which is NOT to promote.

## 11. Tests added/verified this session

- tests/unit/test_70d_bug106_incremental_phase19.py (TEST-TASK09-01): 4
  tests (parity proof, speedup property, build integration, canonical
  path) — 4 passed.
- Governance preview probe: scratch/ag09_governance_preview.py.
- OOS execution probe: scratch/ag09_oos_temporal.py (deterministic re-run
  identical).
- BUG-117 regression: the manifest completeness contract is covered by the
  governance preview + TEST-TASK09-09 (documented).

## 12. Champion state — UNTOUCHED (this session)

Champion remains artifacts/models/scalp/XAUUSD/v1.0.0/model.pt (hash
9105cef7… — the BUG-104 restored state, operator decision pending).
No training/research/shadow path wrote to the Champion path. Candidate
artifacts live under artifacts/model_generation/models/ (isolated).

## 13. Commit log (this session)

- 02aae46 AGENT-09: BUG-106 verification — incremental builder tests +
  profile evidence (TASK-09) [test + probes; incremental builder itself
  committed by Hermes-Bug106; schema_v2 wiring absorbed by parallel HEAD]

(Final commit with BUG-117 fix + reports follows this document.)