# PILOT VALIDATION RESULT — P0 Producer Fix Proof
> 2026-09-04 · HEAD at pilot run: d19195ec · pushed through fc36b2fb (origin/main)
> Objective: prove the corrected 70D ScalpNet v3 training/serving/artifact
> pipeline is structurally correct enough that a full 34×10 retrain is justified.

## Verdict: **PILOT PASSED — FULL 34×10 JUSTIFIED** (still isolated output; no promotion)

## 1. Old retrain termination (user-authorized)
- Identity verified EXACTLY via Win32_Process CommandLine: `train_70d_liquidity_production.py --dataset-id ds_70d_clean_m1_20260904 --folds 34 --epochs 10 --batch 256 --seed 42` (bash 14744→18832, wrapper 26528, worker 25032).
- Kill order child→wrapper→shell; ALL_FOUR_PIDS_GONE verified; zero residual trainers.
- Worker CPU at kill: 32,355 s (~9 h). It never finalized: champion `model.pt` hash identical before/after (c8c0b5b0…), mtime 2026-09-03 22:29:43 predates launch.
- Output classified ABORTED/INVALID evidence; record: `artifacts/forensics/evidence_snapshots/old_worker_termination_record.json`.

## 2. Evidence snapshot (preserved, untouched)
`artifacts/forensics/evidence_snapshots/`:
- model.pt.evidence_20260904_c8c0b5b0 → c8c0b5b06d4c094dc04c9e8ff45cbfffc6f3fb396d42e3df46449068b1dbfd2b ([4,32] head vs 3-class meta — the P0)
- model.meta.json.evidence_20260904_c8c0b5b0 → b4faf0fe…, model.scaler.npz → b3c65b65…
- Live champion path still byte-identical after the whole task (verified repeatedly).

## 3. Producer fix (10 commits, all pushed; beforePush FULL PASS before push)
| Commit | Content |
|---|---|
| 4c4b007e | champion_guard (realpath, symlink/traversal-safe) + isolated candidate default output + hard emission gate module |
| cf6a5574 | provenance stamping + allow_champion_save opt-in |
| 902ec0d1 | typed provenance binding: declare_dataset_provenance() / bind_dataset() from ArtifactStore manifest |
| 393f8ae6 | atomic bundle publication in trainer: stage→gate→manifest→verify→commit |
| 367d7f40 | 9 permanent P0 regression tests (exact failure modes) |
| d19195ec | safe weights_only loader + hot-swap governance (path allow-list, hash binding, eligibility) |
| 087bbc59 | pilot producer + pilot-subset provenance |
| bc15c013 | 5 hot-swap attack tests |
| 3bd692c3 | lint fixes |
| fc36b2fb | legacy wf_candidate test updated for isolated default |

## 4. Pilot run (canonical pipeline, reduced workload ONLY)
- Command: `scripts/dev/pilot_70d_3class.py` · folds 4 · epochs 3 · batch 256 · seed 42
- Dataset: ds_70d_clean_m1_20260904, manifest dataset_hash verified = 3ae687ea… (full 99,946 rows present)
- Pilot subset: contiguous temporal TAIL 24,000 rows (2026-07-22 15:23 → 2026-08-17 19:24) — NEVER random; subset_hash 605fc099… recorded in meta + manifest
- Labels: 21210/1478/1312 (NO_TRADE/BUY/SELL)
- Output: `artifacts/model_generation/models/pilot_70d_3class_20260904_130906/` (isolated; champion never touched)
- Real log: `artifacts/model_generation/pilots/pilot_20260904_130906.log` (+ report json)
- Duration: 5.4 s total (vs 9 h unscaled → full 34×10 estimate ≈ 25–30× pilot fold/epoch load on same data basis ≈ ~8–9 h, unchanged expectation)

## 5. Hard contract gate evidence (serialized tensors are the source of truth)
- EMISSION_GATE_PASS head=3 input=70 seq=32 dataset=ds_70d_clean_m1_20260904 (staged AND re-opened from disk)
- manifest.json binds model_sha256=4ce21183d749…, metadata_sha256, scaler_sha256, dataset_id/sha, schema_hash 235b8fccc96b7e0e, label_schema_id, git_commit, command, seed/folds/epochs, lineage CLEAN_HISTORICAL, production_eligible=true (gated, not assumed)
- Bundle files: model.pt + model.scaler.npz + model.meta.json + manifest.json (commit-marker semantics; stale sidecar pairing rejected — test case 8)

## 6. Genuine-training proof (fresh, not historical)
- param_count=331,459; final_absmean=0.149347; classifier.weight std=0.098859 (non-degenerate)
- Fold best_val_loss trajectory: 1.01353 / 1.01616 / 0.99747 / 1.06836 (distinct per-fold movement; loss fell below 1.0 in fold 3)
- Reproducibility: second independent run produced byte-identical model_sha 4ce21183d749 (same seed/config) — determinism proven

## 7. Behavioral health (serialized candidate)
- mean_max_probability=0.3686 (historical degeneracy reference ≈0.28 — better), logit_std=0.1114 (≠0, no constant-logit collapse)
- pred_dist=[138, 326, 48] on 512 random canonical vectors (all 3 classes reachable; no class collapse)
- NaN/Inf: none · Determinism: identical double-run · Feature-group sensitivity (liquidity block +0.5): max|Δprob|=0.0066 (features influence output)
- Behavioral gate: PASS

## 8. OOS / walk-forward sanity
- 4 purged+embargoed folds ran (purge 15 + embargo 15; per-fold scaler fit on TRAIN split only — `_fit_scaler(X_train_raw)`)
- OOS samples 1,945 · trade_rate 4.8% · win_rate 18.3% · profit_factor 0.27 — expected for 3-epoch pilot (NOT a performance verdict; pipeline produced temporally valid OOS evidence)
- Sharpe-proxy fold dispersion recorded in log (fold2 anomaly documented as sharpe-proxy scale artifact, not leakage)

## 9. Calibration sanity
- Max-prob distribution centered ~0.37 (no overconfidence collapse, no uniform mush); full ECE deferred to the 34×10 gate as planned.

## 10. Offline/Live parity
- Same canonical (B,32,70) float32 market-derived tensor through offline safe-loader path AND serving construction path: max|Δlogit|=0.000e+00, max|Δprob|=0.000e+00, decisions identical (tol 1e-6). Same model_sha both paths. PARITY PASS.

## 11. Security / governance status
- Hot swap: PATH_REJECTED for external/traversal paths; BUNDLE_HASH_MISMATCH for stale sidecar pairing; CANDIDATE_NOT_PRODUCTION_ELIGIBLE for ineligible candidates; valid verified candidate loads (5 tests, real method body).
- Champion protection: trainer/producer write paths structurally denied (realpath guard); regression case 6 asserts champion bytes unchanged; whole-task before/after hashes identical (c8c0b5b0).
- Safe loading: weights_only + pure-state_dict + expected-shape enforcement (safe_loader).
- Residual: legacy torch.load(weights_only=False) call sites in research/forensics/lifecycle inspection modules still exist (inspect-only paths; not on the production serving path). Tracked as remaining P2 hardening.

## 12. Champion protection proof (task-long)
- Champion sha256 at task start, mid, and end: c8c0b5b06d4c094dc04c9e8ff45cbfffc6f3fb396d42e3df46449068b1dbfd2b — UNCHANGED.
- Tests use tmp_path/isolated dirs; suite includes explicit champion-overwrite regression.

## 13. Failure-mode classification (pilot)
- No producer/artifact/class-contract/provenance/leakage/scaler/window/fold failure detected.
- Architecture capacity: **INCONCLUSIVE by design** — pilot objective was pipeline correctness; capacity verdict requires the 34×10 evidence (behavioral + calibration + OOS at production scale).

## 14. Decision
**PILOT PASSED — FULL 34×10 JUSTIFIED**, into an isolated candidate directory with the fixed producer (`three_model.train_variant(..., output_dir=<isolated>)` or the pilot producer with folds=34/epochs=10), canonical dataset ds_70d_clean_m1_20260904, seed 42, batch 256, real disk log, provenance binding, hard emission gate, atomic bundle. NO promotion; governance decides after the full evidence chain.
