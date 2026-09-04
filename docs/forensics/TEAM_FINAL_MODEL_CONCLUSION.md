# TEAM FINAL MODEL CONCLUSION — 2026-09-04T15:55+03:30

> **HEAD:** `f1ef7cb9` (origin/main in sync) — recovery-kit `345932e3` ancestor
> **Canonical target:** ScalpNet v3 · `(B,32,70)` · 70D · `scalp_v3` · 3 classes `NO_TRADE/BUY/SELL` · `ds_70d_clean_m1_20260904` · `3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe` · `235b8fccc96b7e0e` · `CLEAN_HISTORICAL` · 34×10 · batch 256 · seed 42
> **Tooling:** `.venv/Scripts/python.exe` Python 3.11.16 · `torch 2.13.0+cpu` · `torch.load(weights_only=True)` · `polars` · `Get-CimInstance`/`Get-Process`

---

## 1. Why did the previous retrain stop?

**It did not stop.** Verdict `PARTIALLY_TRAINED` (secondary `OUTPUT_OVERWRITTEN` partial) — evidence over premise.

| Signal | Evidence | Timestamp |
|---|---|---|
| `bash 18832` | `HasExited False`, CPU 0.015s, 2 threads, `CreationDate 2026-09-04 06:36:47.569 +03:30`, cmd `bash -lic ... train_70d_liquidity_production.py ... \| tail -40` | launch |
| `python 26528` (`.venv`) | `Parent 18832`, `HasExited False`, CPU 0.015s, 1 thread, `WorkingSet 20 KB` — idle wrapper that forks to uv python | launch |
| `python 25032` (`uv` 3.11.16) | `Parent 26528`, `HasExited False`, `ThreadCount 34`, `WorkingSet 349 MB`, `Kernel 8967500000 User 299368281250 ×100ns`, `Get-Process CPU 30833s`, `StartTime 06:36:47`, 34 threads `25100 Running` continuously, CPU growing `+276s` in minutes, `Responding True` | collected 15:33–15:55 |
| `model.pt` | `1334268 B`, `c8c0b5b06d4c094dc04c9e8ff45cbfffc6f3fb396d42e3df46449068b1dbfd2b`, `LastWriteTime 2026-09-03 22:29:43.914` — **predates launch** | untouched |
| `model.scaler.npz` / `model.meta.json` / `benchmark_70d_liquidity.json` / `retrain_*.json` | all `2026-09-04 08:53:25.175–203`, `elapsed_sec 62.33`, `EVIDENCE_WRITTEN`, `trainable_rows 26947` | clobbered sidecars |
| `*.tmp` / `*.ckpt` / `checkpoint*` | **None** — `Get-ChildItem -Recurse` returns 0; atomic `tmp→replace` guards cleaned | — |
| `logs/info` grep `PROD_TRAIN\|70d_liquidity` | **0 hits** — `bash \| tail -40` buffers stdout in pipe until EOF, no disk log | pipe hold |

Full forensics: `MODEL_RETRAIN_POSTMORTEM.md` (290 lines, real `powershell`/`Get-FileHash`/`polars` output, no fabricated data). Reproducible commands listed in §Evidence Table.

**Fate narrative:** `06:35:58` dataset materialized → `06:36:47` `18832→26528→25032` forms → `06:36:47–15:55 (~9h)` worker burns `~30k CPU-s` (34 purged folds ×10 epochs transformer) → `08:11–08:53` short jobs write `wf_candidate (08:11:42)` + `cand_*` and at `08:53:25` clobber sidecars with 62 s evidence → `15:55` worker still `Running`, champion not yet replaced. The "NOT ACTIVE" premise is **contradicted by live CPU**.

## 2. Did the previous run actually train?

**PARTIALLY — INCONCLUSIVE for finalization.**

* **YES, compute is happening:** `UserMode ~29.5 ks` (~8.2 h user CPU), `331 MB WS`, 34 threads, `Elapsed 09:18`, CPU still advancing — unequivocal training loop.
* **NO, finalization not reached:** no new `model.pt` hash, no `tmp`, no `_save_checkpoint → _save_scaler → _save_metadata` atomic publish. Cannot claim `TRAINED_AND_FINALIZED` or `FAILED_DURING_TRAINING` (no exception, no non-zero exit).
* **Therefore:** `PARTIALLY_TRAINED` — do not reuse stale `parameter_movement_frac 0.77` as evidence; must recompute after publish.

## 3. Was any valid checkpoint recovered?

**NO.**

Inventory via `.venv` `torch.load(weights_only=True)` over 30+ `*.pt` (`artifacts/model_generation/models/*/model.pt` + `artifacts/models/scalp/*/*/*.pt`):

| Candidate | Head | Params | Meta | Dataset | Verdict |
|---|---|---|---|---|---|
| `70d_liquidity/model.pt` + 4 baks | `[4,32]` `c8c0…` | 331492 | `scalp_v3 70×3` but `dataset_id null` | — | **P0** — claims 3, tensor 4 |
| `70d_liquidity/model.pt.bak_20260904` | `[4,32]` `763a…` | 331492 | — | — | P0, different weights |
| `wf_candidate/model.pt` | **`[3,32]`** `ec84…` | 331459 | `scalp_v4 70×3` `production_eligible false` `label_origin UNKNOWN` | null | Geometry PASS / governance FAIL — not `scalp_v3`/`CLEAN_HISTORICAL` |
| `t70d_seq_v1/v2` | `[3,64]` TCN | 236803 | `TCN_ATTENTION_V1 scalp_v3 70×3 seq 32` | `t70d_f1_full_m1` | PASS but **different arch** (not ScalpNet v3) |
| `bench_*` / `cand_*` | `[4,32]` LEGACY or `[3,64]` TCN or `[3,128]` MLP | — | `scalp_v1/v2` | `ds_fe27…/ds_9704…` | not 70D `scalp_v3` `ds_70d_clean…` |

**No checkpoint** for `ds_70d_clean_m1_20260904`, ScalpNet v3, 70D, `L=32`, `seed 42`, `34 folds×10epochs`, `scalp_v3`, `3-class` that could be resumed. The only 70D 3-class ScalpNet is `scalp_v4` UNKNOWN — not resumable per §4 rules.

## 4. Did we run a new retrain?

**NO.**

* Live worker `25032` is still hot (30833 s CPU). Launching a duplicate would write concurrently to the **same champion path** (`variant_artifact_path("70d_liquidity") = artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt`) via `tmp→replace` — contender for champion clobber + 8 h wasted. §3/§5 forbid direct champion training; producer must be fixed to isolated candidate output before any new launch (see `MODEL_CONTINUATION_REPORT.md`).
* No training launched, no PID spawned, no log started. Postmortem + producer fix are gated before retrain.

## 5. What is the authoritative candidate?

**NONE — P0 BLOCK, no promoted candidate exists.**

* **Current champion path (invalid, preserved as evidence):** `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` — `1334268 B`, `c8c0b5b06d4c094dc04c9e8ff45cbfffc6f3fb396d42e3df46449068b1dbfd2b`, `classifier [4,32]`, `input_projection [128,70]`, `scaler mean/std [70]`, `meta 70/3/3 scalp_v3 seq 32 hash 235b8fccc96b7e0e dataset_id null`, `LastWriteTime 2026-09-03 22:29:43`.
* **Closest geometry-correct but not authoritative:** `artifacts/model_generation/models/wf_candidate/model.pt` — `1335403 B`, `ec84ed21…`, `classifier [3,32]`, `scalp_v4`, `production_eligible false`, `label_origin UNKNOWN` — **explicitly rejected** per §1 rule (do not rename/ copy `wf_candidate`).
* **TCN proof that gate runs:** `artifacts/model_generation/models/t70d_seq_v1/model.pt` — `head [3,64]` TCN, `scalp_v3`, but different architecture (331k ScalpNet v3 vs 236k TCN) — not a substitute.
* **Authoritative after fix:** isolated `artifacts/model_generation/models/cand_isolated_70d_34x10_<timestamp>/model.pt` (or `t70d_production_candidate`) — does not exist yet — will be created only after producer fix (§6) + hard contract check (§10).

## 6. Is the artifact coherent?

**NO — P0 FAIL.**

| Field | Actual (tensor) | Declared (metadata) | Canonical | Match? |
|---|---|---|---|---|
| `input_projection.weight` | `[128,70]` | `num_features 70` | 70 | ✅ |
| `classifier.weight` | **`[4,32]`** | **`model_head_classes 3`** | **3** | **❌ — P0** |
| `classifier.bias` | `[4]` | `num_classes 3` | 3 | ❌ |
| `feature_schema_id` | — | `scalp_v3` | `scalp_v3` | ✅ (meta only) |
| `feature_schema_hash` | — | `235b8fccc96b7e0e` | `235b8fccc96b7e0e` | ✅ |
| `feature_schema_dimension` | — | 70 | 70 | ✅ |
| `seq_len` | — (model agnostic to L, but manifest says 32) | `seq_len 32` | 32 | ✅ (meta only) |
| `dataset_id` | — | **`null`** | `ds_70d_clean_m1_20260904` | **❌ — provenance FAIL** |
| `dataset_sha256` | — | absent | `3ae68…` | ❌ |
| `label_schema_id` | — | `triple_barrier_3class_v1 class_count 3` | same | ✅ (meta) |
| `scaler` | `mean [70] std [70]` | same | — | ✅ dim, but irrelevant vs head |
| `model_sha256` | `c8c0…` | recorded as `c8c0…` | new sha after fix | ✅ for old, but stale |
| `git_commit` / `training_command` / `seed` | — | `git_commit null` (in current meta) | must be set | ❌ |

**Bundle incoherence:** `actual_head 4 != metadata_head 3 != canonical 3`. Hand-edit `meta 2051B→4293B` without weight change already proven. `re-` editing does not fix tensor. Only `wf_candidate` is `[3,32]` but `scalp_v4/UNKNOWN`.

Full 33-artifact forensics: `MODEL_ARTIFACT_FORENSICS.md` + `artifacts/forensics/model_artifact_forensics_20260904.json` (machine-readable, `weights_only=True`, `sha256` per pt).

## 7. Did the new model genuinely train?

**NOT APPLICABLE — no new coherent model exists to measure.** `WITHHELD`.

* Stale provenance `parameter_movement_frac 0.77 / logit_std 0.18 / max_prob 0.39` is **rejected** — measured on wrong bundle (4-logit tensor cannot emit 3-logit metrics).
* After coherent `[3,32]` emission, required proof: `init vs final weight stats`, `weight delta max_tensor_diff`, `parameter_movement_frac` (fresh), `loss trajectory` per epoch/fold, `fold outputs` (34 folds), `validation loss`, `scaler hash`, `git_commit + training_command`, `dataset hash` — none yet.

## 8. Did the behavioral probes pass?

**WITHHELD — do not run on incoherent tensor.**

Required 12 probes (§13): zero input, all `+3`, all `-3`, single-feature perturbation, feature-group, random valid, repeated identical, small perturbation sensitivity, class-logit distribution, probability distribution, batch vs single, `NaN/Inf`, determinism.

Previous degeneracy `mean_max_prob ≈0.28 / very low logit_std / weak sensitivity / NO_TRADE collapse` is a **regression reference** for the old bundle, not a pass threshold for the new. On a `[4,32]` tensor through a `3`-class decoder, probes would be meaningless (4th logit masked vs NO_TRADE collapse).

Gate to run only after §10 hard contract PASS on fresh coherent candidate, with documented thresholds.

## 9. Did OOS/WF pass?

**WITHHELD.**

* Walk-forward spec is frozen: `purge 15 + embargo 15` (label horizon 15), `34 folds ×10 epochs × batch 256` over `trainable_rows 26947` (purged, not 99,946 raw), per-fold scaler fit on TRAIN only, `compute_70d_frame_fast` byte-identical to slow (200-bar `maxdiff 0.0` — BUG-106), no leakage.
* No coherent ScalpNet v3 70D 3-class to measure `accuracy / balanced_accuracy / macro F1 / per-class PR / confusion / fold mean+std / entropy / confidence`. `t70d_seq_v1/v2` TCN OOS exists but is not ScalpNet v3 evidence.

## 10. Did calibration pass?

**WITHHELD.**

* To measure after OOS: `ECE`, reliability diagram, confidence distribution, over/under-confidence per class. Not equated with accuracy. No coherent bundle to measure.

## 11. Did offline↔live parity pass?

**WITHHELD.**

* Contract `TRAIN INPUT (B,L,70) == LIVE INPUT (1,L,70)` is proven sound (`temporal_contract.py: FEATURE_DIM 70 + CANONICAL_SEQ_LEN 32` alias chain, `sequence.py` re-exports, `live_engine` reads artifact `seq_len`), but **no coherent `scalp_v3` 3-class ScalpNet** to replay.
* To capture after gate: one exact `(B,32,70)` tensor through `OFFLINE` `three_model.train_variant` forward + `LIVE` `live_engine._infer_probabilities` / `models/scalp_net.ScalpNet` — assert same feature ordering, scaler, dtype, orientation, artifact sha, `logits±1e-5`, `probs±1e-5`, same decision, no hidden live transform.

## 12. Did governance pass?

**FAIL.**

| Gate | Verdict | Evidence |
|---|---|---|
| `candidate → validation → challenger → governed promotion → champion` | **FAIL** | no candidate passes hard contract |
| No manual rename/copy/direct write/hot-swap bypass | **PASS so far** | `c8c0…` preserved, `wf_candidate` not copied, `model.pt` not overwritten (still `2026-09-03` mtime) |
| Candidate registration with provenance | **FAIL** | `model.meta.json dataset_id null / lineage null / git_commit null` — producer bypasses `DatasetFactory/ArtifactStore` (`three_model` builds `compute_70d_frame_fast` directly) |
| Challenger→champion promotion | **PASS-weak (design)** | `model_governance_routes.py:1245 execute_promotion` requires `actor+model_id+approval_token` + freeze lock + hash re-verify — token presence-only, not `HMAC(actor,model_id,expiry)` + single-use |
| Champion overwrite protection | **PASS (design)** | `live_engine.hot_swap_model` validates+warms under `_bundle_lock` — but path unrestricted (see §13) |
| `pinc-stash-rescue@0c90725b` preserved | **PASS** | not touched |
| `git stash` preserved | **0 stashes** — `f1ef7cb9` disposition dropped 6 audited stashes (prior baseline `345932e3` had 6; archived to `forensic_recovery_20260904/stash-{0..5}.patch`) | do not re-drop |

## 13. Did model-loading security pass?

**FAIL — 3 BLOCKED.**

| Area | Verdict | File:line |
|---|---|---|
| `torch.load` without `weights_only=True` | **FAIL** — 12/19 sites | `live_engine.py:2684,2727,2755` `probe/state_dict = torch.load(...)`; `streaming_replay.py:135`; `checks_features.py:421` `weights_only=False`; `governance/load_gate.py:66` `weights_only=False`; `model_generation/runtime.py:110`; `shadow/challenger.py:127`; `model_lifecycle/integrity.py:269,314,426`; `web/model_governance_routes.py:582` |
| `Path(model_artifact_path)` traversal fencing | **FAIL** — 0/8 guarded | `live_engine.hot_swap_model:1505` + `diagnostics_state_routes.model_hot_swap:1636` accept arbitrary string → `Path.exists()→torch.load`, no `resolve().is_relative_to(ARTIFACTS_ROOT)`, no symlink reject |
| `WalkForwardTrainer._save_checkpoint` head==canonical gate | **FAIL** — no check | `torch.save→tmp→replace` without `classifier.weight.shape[0]==CANONICAL_CLASS_COUNT` abort |
| Correct `weights_only=True` examples | PASS — 7/19 | `model_lab/baseline.py:50`, `lab_runner.py:49`, `release/diagnostics.py:70`, `release/health.py:366,465,640`, `runtime_snapshot.py:141` |
| CORS / auth | FAIL | `diagnostics_state_routes.model_hot_swap:1626` zero `Depends`/JWT, `server.py:425 CORS ["*"]`; promotion token not HMAC-bound |

Fix before production eligibility (§19): `weights_only=True` where compatible, canonical-root allow-list with `resolve().is_relative_to`, require authorization for hot-swap, HMAC-bound approval token.

## 14. Is architecture capacity sufficient?

**INCONCLUSIVE — no evidence to decide.**

* P0 is not capacity: `331,492 params`, `input [128,70]`, TCN `236k` all prove 70D 3-class **compiles**; `wf_candidate` proves LEGACY_SCALPNET 70D `[3,32]` is geometrically valid.
* Capacity can only be judged after §10 coherent bundle + §12 genuine training + §13 behavioral (not collapsed) + §14 OOS (not leaked) + §15 calibration.
* Do not increase model size before proving current 331k fails due to capacity, not wrong head / undertraining / wrong labels / leakage / wrong scaler / ordering / serving mismatch / calibration / dataset.

## 15. What remains blocked?

| Pri | ID | State | One-liner |
|---|---|---|---|
| **P0** | `MDL-INCOHERENCE` | **BLOCKED** | `70d_liquidity` `4 != 3` — only fresh producer emits fix — no manual repair |
| **P1** | `SEC-CAPITAL-DESER` | **BLOCKED** | 12 `torch.load` without `weights_only=True` — RCE before validation |
| **P1** | `SEC-CAPITAL-PATH+AUTH` | **BLOCKED** | arbitrary `model_artifact_path` + unauth `model_hot_swap` + CORS `*` → any `*.pt` → `torch.load` |
| **P1** | `PRODUCER-CHAMPION-WRITE` | **BLOCKED** | `train_variant` writes directly to champion `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` — must be isolated candidate + atomic bundle install |
| **P1** | `PRODUCER-EMISSION-GATE` | **BLOCKED** | no `actual_head == 3` abort before `tmp→replace`; per-file atomics ≠ bundle atomic |
| **P2** | `DATA-GOV-PROVENANCE` | **FAIL** | `dataset_id null / git_commit null` — no `ds_70d_clean_m1_20260904` + `3ae68…` + `235b8f…` + `seq 32` bundle stamp |
| **P2** | `RETRAIN-FINALIZATION-RACE` | **PARTIALLY_TRAINED** | live 34×10 (`25032`, 30833 s CPU) will clobber champion on landing — snapshot `c8c0…` before it lands |
| **P2** | `REL-BUG-160/B-239` | **DEFERRED** | release staging + ISCC — separate PR stack (`nse_qa_head_wt`) |
| **P3** | `BUILD-SITE-FLAG` | **DEFERRED** | dead `FLAG_BUILD_INDEX` tail — P3 commit `f1ef7cb9` pending |
| **P3** | `WORKTREE-PRUNE` | LOW | `scratch/rungate/*` 3 prunable wts — after review |

`RETRAIN STILL ALIVE` — do not launch duplicate; do not claim `TERMINATED_EXTERNALLY` while `25032` is `Running`.

## 16. What is the ONE next action?

**Snapshot the current champion, fix the producer to isolated candidate output, and let the live worker finish under observation — do NOT launch a new retrain until the hot PID either lands or is proven failed.**

```powershell
# 1. Preserve current P0 evidence (once, before live worker lands)
Copy-Item artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt.evidence_20260904_c8c0b5
Copy-Item artifacts/models/scalp/XAUUSD/70d_liquidity/model.meta.json artifacts/models/scalp/XAUUSD/70d_liquidity/model.meta.json.evidence_20260904
Copy-Item artifacts/models/scalp/XAUUSD/70d_liquidity/model.scaler.npz artifacts/models/scalp/XAUUSD/70d_liquidity/model.scaler.npz.evidence_20260904

# 2. Producer fix (single commit, Batch B — requires review, no champion write):
#    - Add emission gate: `actual_head == CANONICAL_CLASS_COUNT == 3` before any tmp→replace + bundle atomic staging dir
#    - Route `train_70d_liquidity_production.py` / `three_model.train_variant` to isolated `artifacts/model_generation/models/cand_isolated_70d_34x10_<ts>/model.pt`
#    - Stamp `dataset_id ds_70d_clean_m1_20260904 + dataset_sha256 3ae68… + feature_schema_hash 235b8f… + seq_len 32 + git_commit + training_command + seed 42 + folds 34 + epochs 10 + scaler/model sha`
#    Verify: ruff + py_compile on training path + grep no direct champion write remains

# 3. Observe live worker (no kill): poll every 5 min
#    powershell: Get-Process -Id 25032 | Select CPU,WorkingSet,Threads; Get-Item .../model.pt | Select LastWriteTime
#    When it lands: immediately validate `classifier [3,32]` && `dataset_id == ds_70d_clean_m1_20260904` else REJECT and trigger isolated rerun via `tee`:
#    .venv/Scripts/python.exe scripts/dev/train_70d_liquidity_production.py --dataset-id ds_70d_clean_m1_20260904 --folds 34 --epochs 10 --batch 256 --seed 42 2>&1 | Tee-Object artifacts/model_generation/three_model/train_70d_isolated_$(Get-Date -Format yyyyMMdd_HHmmss).log
```

If the hot worker emits `[4,32]` or `dataset_id null`, **reject** and run the ONE clean isolated 34×10 retrain through the fixed producer — then chain `§10 hard contract → §12 genuine training → §13 behavioral → §14 OOS → §15 calibration → §16 offline↔live → §17 LIVE DEMO (no orders) → §18 governance proof`.

---

## Final Decision

```
MODEL BLOCKED
```

`MODEL BLOCKED` because `P0 ARTIFACT CONTRACT INCOHERENCE` is proven and no verified fresh 70D 3-class ScalpNet v3 bundle exists; `P1` model-loading security + direct champion write are still reachable; the expected 34×10 retrain is `PARTIALLY_TRAINED` (still on CPU, not finalized) so a duplicate launch would race the champion.

`MODEL DEVELOPMENT CONTINUES` / `MODEL READY FOR NEXT GOVERNED GATE` / `MODEL PRODUCTION-READY` are **not met** per §24 — production-ready requires coherent `[3,32]` + genuine training + behavioral + OOS + calibration + LIVE DEMO + offline↔live + governance + champion protection + `weights_only` — all BLOCKED/WITHHELD.

**Reports updated:** `MODEL_RETRAIN_POSTMORTEM.md` (290 lines), `MODEL_ARTIFACT_FORENSICS.md` (33-pt), `MODEL_READINESS_REPORT.md` (14 gates), `MODEL_CONTINUATION_REPORT.md` (next gated sequence), this `TEAM_FINAL_MODEL_CONCLUSION.md`. Stashes are 0 (disposition `f1ef7cb9`), branched forensics on `hermes-subagent/*` preserved. Process PIDs preserved, bad artifact preserved, champion not overwritten, no orders submitted.

*Evidence over assumption — hashes, tensor shapes, and process probes are the verdict.*
