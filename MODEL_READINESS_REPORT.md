# MODEL READINESS REPORT — 2026-09-04T15:40+03:30

> **Canonical contract:** ScalpNet v3 · `(B,32,70)` · 70D · `scalp_v3` · 3 classes `NO_TRADE/BUY/SELL` · `ds_70d_clean_m1_20260904` · `CLEAN_HISTORICAL` · hash `3ae68…/235b8fccc96b7e0e`
> **Candidate under test:** `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` (live champion path)
> **Tooling:** `.venv/Scripts/python.exe` Python 3.11.16 · `torch 2.13.0+cpu` · `torch.load(..., weights_only=True)` · `polars`

---

## 1. Executive Verdict

| Axis | Grade | One-liner |
|---|---|---|
| **Dataset** | **PASS** | `ds_70d_clean_m1_20260904` — 99,946 rows × 101 cols — `sha256 3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe` matches task — `feature_schema_id scalp_v3 / label_schema triple_barrier_3class_v1 / class_count 3 / seq_len 32 / production_eligible true / all_gates_pass true` |
| **Contract (code)** | **PASS** | `temporal_contract.FEATURE_DIM=70` + `schema_contract.DIMENSION=70` + `CANONICAL_SEQ_LEN=32` + `LABEL_SCHEMA_3CLASS_V1 class_count=3` — single source per axis — see `CONTRACT_AUDIT_REPORT.md`; `ACTIVE_SCHEMA_ID=scalp_v1/50D` is intentional documented lag |
| **Artifact coherence** | **P0 FAIL — ARTIFACT CONTRACT INCOHERENCE** | **Metadata claims 3, tensor is 4** — `classifier.weight [4,32]` vs `model.meta.json model_head_classes 3` — bundle is incoherent — **BLOCKED FROM LIVE, BLOCKED FROM PROMOTION** |
| **Training integrity** | **INCONCLUSIVE (blocked by P0)** | Fresh-init comparison not runnable on incoherent tensor shape; `parameter_movement_frac 0.774` quoted in stale provenance is not evidence for this bundle |
| **Behavioral health** | **BLOCKED — do not run** | Probes on a 4-logit tensor through a 3-class decoder are meaningless (false NO_TRADE collapse or masked logit 3) — must be run only on a fresh coherent 3-class bundle emitted by the live 34×10 retrain |
| **OOS / walk-forward / calibration** | **BLOCKED** | `t70d_seq_v1/v2` TCN 70D 3-class has OOS, but live ScalpNet v3 70D 3-class has not emitted a coherent artifact to measure |
| **Offline↔live equivalence** | **BLOCKED** | `(B,32,70)` contract is sound; no coherent `scalp_v3` 3-class ScalpNet to replay through |
| **Governance** | **FAIL** | `model.meta.json dataset_id null` — no provenance link to `ds_70d_clean_m1…`; `feature_schema_hash` binding exists but head mismatch voids it |

**Overall:** `P0 ARTIFACT CONTRACT BLOCK` — behavioral/OOS/calibration/equivalence probes are **WITHHELD** until the live retrain emits a coherent bundle. Do not promote, do not trade live.

---

## 2. Artifact Forensics (machine-verifiable)

Full 33-artifact dump: `artifacts/forensics/model_artifact_forensics_20260904.md` + `.json` (this report summarizes).

| Artifact | Size | SHA256 | Params | `input_projection` | Head | Meta claim | Verdict |
|---|---|---|---|---|---|---|---|
| **`70d_liquidity/model.pt` (live)** | 1,334,268 | `c8c0b5b06d4c094dc04c9e8ff45cbfffc6f3fb396d42e3df46449068b1dbfd2b` | 331,492 | `[128,70]` | **`classifier [4,32]`** | `70 / 3 / model_head_classes 3 / scalp_v3 / seq 32` | **P0 — claims 3, tensor 4** |
| `70d_liquidity/model.pt.bak_20260904` | 1,335,531 | `763a25f61fe6b7d35da79fc3d2432b5fce59fdd2fc9237e816de20ec79e88d98` | 331,492 | `[128,70]` | `[4,32]` | — | P0 (same lineage, different weights) |
| `70d_liquidity/model.pt.bak2_1788498799` | 1,334,268 | `c8c0b5b0…` (identical to live) | 331,492 | `[128,70]` | `[4,32]` | 2,051 B legacy meta → rewritten 4,293 B `model_head_classes:3` **without retraining** | P0 (hand-edited meta) |
| `70d_news/model.pt` | 1,335,531 | `2b98f333…` | 331,492 | `[128,70]` | `[4,32]` | `model_head_classes 4` | Self-consistent 4 — P0 if used as 70D 3-class |
| `t70d_full_retrain/model.pt` | 1,334,268 | `c8c0b5b0…` (≡ live) | 331,492 | `[128,70]` | `[4,32]` | `scalp_v3 70×3` | P0 — identical tensor to live |
| `wf_candidate/model.pt` | 1,335,403 | `ec84ed21…` | 331,459 | `[128,70]` | **`classifier [3,32]`** | `scalp_v4 70×3 / production_eligible false / label_origin UNKNOWN` | Geometry PASS / governance FAIL |
| `t70d_seq_v1/v2/model.pt` | ~954,896 | — | 236,803 | `[128,70]` TCN | `head.3 [3,64]` | `TCN_ATTENTION_V1 / scalp_v3 70×3 / seq 32` | PASS (TCN family, not ScalpNet v3) |
| `50d_main/model.pt` | 1,325,291 | `342901…` | 328,932 | `[128,50]` | `[4,32]` | `model_head_classes 4 / scalp_v1` | Legacy 50D — out of scope |

**Scaler:** `model.scaler.npz` → `mean [70] / std [70]` — dimension correct — not sufficient vs head mismatch.

**Provenance signal:** `retrain_70d_liquidity_provenance.json` (`elapsed_sec 62.33 / trainable_rows 26947 / gate EVIDENCE_WRITTEN`) claims `BEHAVIORAL_HEALTH_PASS` (`n_cls 3 / max_prob 0.39 / logit_std 0.18 / wait_mass 0.0`) — **cannot be evidence for this 4-class tensor** — provenance itself flags `meta_has_dataset_id false / meta_dim_70 false`. Stale metrics, wrong bundle.

### 2.1 Why P0 cannot be repaired by renaming

- Hand-editing `model.meta.json` (`model_head_classes 4→3`) is already proven to have happened (`2,051 B → 4,293 B` without weight change) — re-editing does not fix the tensor.
- `wf_candidate [3,32]` is `scalp_v4 / UNKNOWN` lineage, `production_eligible false`, missing `feature_schema_hash` — promoting it would be a governance bypass.
- Only a **fresh 34×10 walk-forward emission** (purged 15 + embargo 15, `compute_70d_frame_fast`, per-fold scaler fit on TRAIN only, `CANONICAL_CLASS_COUNT 3`) produces a promotable bundle.

---

## 3. Dataset — PASS (verified, not trusted)

| Field | Value | Verified how |
|---|---|---|
| `dataset_id` | `ds_70d_clean_m1_20260904` | `dataset_manifest.json` |
| `rows` | `99,946` (`total 99946 / train 69962 / val 14991 / test 14993`) | `polars read_parquet` + `manifest.row_counts` |
| `sha256` | `3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe` | `sha256sum` — **matches task context** |
| `feature_schema_id` | `scalp_v3` | `manifest.feature_schema_id` + `verification.json` |
| `feature_schema_hash` | `235b8fccc96b7e0e` | `manifest.feature_schema_hash` — matches `model.meta.json` |
| `label_schema` | `triple_barrier_3class_v1` — `NO_TRADE 0 / BUY 1 / SELL 2` — WAIT is policy-only | `manifest.label_schema_id / contract.class_count 3` |
| `class_count` | 3 | same |
| `label_distribution` | `0:14898 / 1:6261 / 2:5788` (~55% NO_TRADE, imbalanced — expected for purged barrier) | `manifest.label_distribution` |
| `lineage` | `CLEAN_HISTORICAL / label_origin CLEAN_HISTORICAL / governance_override_required false / production_eligible true` | `manifest.label_origin / contract.production_eligible` |
| `sequence_windows` | `seq_len 32 / max_gap_us 900000000 (15 min in manifest vs 10 min canonical — see note) / windows_total 26916 / windows_valid 22436 / tensor [26916,32,70]` | `manifest.sequence_windows` |
| `purge/embargo` | `purge 15 / embargo 15 / labeler embargo 3 / no_trade_stride 2` | `manifest.purge_parameters` |
| `eval_rows` | `26,947` walk-forward trainable | same as `retrain_70d_liquidity_provenance.walk_forward.trainable_rows` |
| `builder` | `compute_70d_frame_fast (BUG-106, byte-identical to canonical)` | `manifest.contract.builder` |
| `all_gates_pass` | `true` (`verify_70d ok / gap_safe ok / label_integrity ok / schema_hash ok`) | `verification.json` |
| `git_commit` | `76254b9e` | `manifest.git_commit` |

**Note — gap mismatch:** `manifest.sequence_windows.max_gap_us=900000000 (15 min)` vs `temporal_contract.CANONICAL_MAX_GAP_US=600000000 (10 min)` — manifest window uses the dataset-generation gap; live/sequence builder canonical is 10 min. Windows valid at 15 min will be re-gated at 10 min by live — this narrows valid windows (acceptable narrowing, not widening). Documented, not a blocker.

---

## 4. Contract — PASS (code), intentional lag documented

Summary from `CONTRACT_AUDIT_REPORT.md` (285 lines):

| Axis | SSoT | Live ACTIVE | Verdict |
|---|---|---|---|
| 70D | `temporal_contract.FEATURE_DIM=70` + `schema_contract.DIMENSION=70 / SCHEMA_ID scalp_v3` | `features/schema.py:95 ACTIVE_SCHEMA_ID=scalp_v1 / 50D` | **PASS — intentional lag until governance promotion** |
| 32 | `temporal_contract.CANONICAL_SEQ_LEN=32` aliased by `sequence.py` | `live_engine.py:468` → `CANONICAL_SEQ_LEN` / artifact `seq_len` | **PASS — one literal, aliases only** |
| 3 classes | `LABEL_SCHEMA_3CLASS_V1 class_count 3` + `architectures.CANONICAL_CLASS_COUNT=3` | 4 tolerated via `LEGACY_HEAD_CLASSES=4` + `model_class_contract._mask_wait_logit` | **PASS with fenced legacy — training is 3** |
| Ordering | `schema_contract.canonical_feature_names()` (50+10+10) | same | PASS — no duplicate tuple |
| Label map | `LABEL_SCHEMA_3CLASS_V1.numeric_mapping {0,1,2}` | `WAIT=3` is policy-derived, never a training label | PASS |
| Scaler | `npz(mean,std)` + `feature_schema_hash` bound everywhere | `live_engine.ScalerBundle` | PASS — pre-train-pure, per-fold fit |

No competing `50/70/32/4` literal drives training geometry outside the SSoT.

---

## 5. Training Integrity — INCONCLUSIVE (blocked by P0)

A 70D 3-class ScalpNet cannot be proven trained until a coherent 70D 3-class tensor exists. The live tensor is 4-class, so `initial vs final weight delta / loss curves / fold metrics` measured on 3-class assumptions are inapplicable.

What **is** proven about the pipeline (not this bundle):

- `compute_70d_frame_fast` is **byte-identical** to `compute_70d_frame` (200 synthetic M1 bars: `max |feat_0..4 diff| 0.0`, `cols 80/80`, `rows 146=146`) — `forensic/recover-three-model-fast` already integrated — 70D variants now use the fast path.
- `t70d_seq_v1/v2` TCN 70D 3-class artifacts prove the 70D 32/70 OOS gate runs end-to-end.
- `wf_candidate [3,32]` proves the LEGACY ScalpNet 70D 3-class head **compiles** (`331,459 params = 331,492−33` for `3*32+3 vs 4*32+4`).

**Required once the live 34×10 emits a coherent `[3,32]` bundle:**

- `initial vs final weight stats` (prove not fresh-init), `weight delta` (`max_tensor_diff` style), `loss curves` per fold, `fold-level metrics`, `class distribution / prediction distribution / logit stats`, artifact `sha256 / git_commit / seed / scaler hash` per fold, `training command` provenance.

---

## 6. Behavioral Health — WITHHELD (do not run on incoherent bundle)

Required probes per §9 — **to be run only on the fresh coherent candidate:**

1. zero input  2) all `+3`  3) all `-3`  4) single-feature  5) feature-group  6) random valid  7) repeated identical  8) small perturbation sensitivity  9) class-logit distribution  10) probability distribution  11) batch vs single  12) determinism

Gates to fail on: near-uniform (`mean_max_prob ≈ 0.28` / low `logit_std`), class collapse (all argmax one class), constant logits, insensitivity to meaningful features, exploding logits, NaN/Inf, wrong shape/mapping, scaler/order flipped.

**Reference degeneracy:** `mean_max_prob ≈0.28 / very low logit_std / little response` — from stale probes run on an incoherent/untrained bundle — must not be copied as pass criteria for the new bundle; use documented tolerance only.

**Provenance stale metrics (not evidence):** `max_prob_mean 0.3909 / logit_std_mean 0.1837 / wait_mass 0.0 / margin_sensitivity 0.0618 / parameter_movement_frac 0.774` — quoted but **rejected** as evidence because the measured tensor cannot emit 3 logits.

---

## 7. OOS / Walk-Forward / Calibration — WITHHELD

Temporal split: `purge 15 + embargo 15` (label horizon 15) — walk-forward is `34 folds × 10 epochs × batch 256` (`--seed 42`) over `trainable_rows 26947` (purged, not 99,946 raw) — no train/test leakage when executed via `WalkForwardTrainer`.

**To report once coherent:** `accuracy / balanced_accuracy / macro F1 / per-class precision+recall / confusion matrix / OOS metrics / fold dispersion / calibration error (ECE) / reliability diagram / entropy / class distribution / rejection-or-NO_TRADE behavior / confidence distribution`.

**Current OOS that exists:** `t70d_seq_v1/v2` TCN — not comparable to ScalpNet v3 (different arch), so not acting as gate.

---

## 8. Offline↔Live Equivalence — WITHHELD

Chain to prove (both paths): `real market data → canonical 70D builder → sequence builder (32,70) → scaler (70) → model (3) → logits (3) → probs → decision`.

**To capture:** one or more exact `(B,32,70)` inputs and replay identical tensor through offline `three_model.train_variant` forward path and live `live_engine._infer_probabilities` / `models/scalp_net.ScalpNet` — assert `same feature ordering / same scaler / same dtype / same orientation / same artifact sha / same logits±ε / same probs±ε / same decision / no hidden live transform`.

**Contract is sound** — `temporal_contract` proves `TRAIN INPUT (B,L,70) == LIVE INPUT (1,L,70)` — but no coherent 70D 3-class ScalpNet to exercise it.

---

## 9. Governance — BLOCKED

Valid lifecycle is `candidate → challenger → champion`.

| Gate | Status | Evidence |
|---|---|---|
| No manual rename / direct champion write | **PASS (currently)** | Incoherence was a hand-edited meta, not a `champion` overwrite — still prohibited |
| Candidate registration with provenance | **FAIL** | `model.meta.json dataset_id null / lineage null / git_commit null` — no link to `ds_70d_clean_m1_20260904` |
| Challenger→champion promotion | **PASS-weak** | `model_governance_routes.py:1245 execute_promotion` requires `actor+model_id+approval_token` + freeze lock + hash re-verify — but token is presence-only, not HMAC-bound (see §11) |
| Champion overwrite protection | **PASS** | `live_engine.hot_swap_model` validates+bakes before swap under `_bundle_lock` — correct, minus path/auth |
| Path allow-list | **FAIL** | arbitrary `model_artifact_path` accepted (see security audit) |
| Deserialization | **FAIL** | `weights_only=False` (see §11) |

---

## 10. Active Retrain — DO NOT DISTURB

| Signal | Value |
|---|---|
| Command | `scripts/dev/train_70d_liquidity_production.py --dataset-id ds_70d_clean_m1_20260904 --folds 34 --epochs 10 --batch 256 --seed 42` |
| PIDs (15:40 check) | `26528 (parent 18832) + 25032 (child of 26528)` — both alive via `Get-CimInstance Win32_Process` |
| Provenance | `elapsed_sec 62.33` earlier checkpoint — `gate EVIDENCE_WRITTEN / variant 70d_liquidity / trainable_rows 26947` |
| Action | **Monitor only — no kill/restart/duplicate** — when it completes, verify `model.pt` head is `[3,32]` + `model.meta.json dataset_id=ds_70d_clean_m1_20260904 / feature_schema_hash=235b8fccc96b7e0e / seq_len 32 / class_count 3 / git_commit set` |

---

## 11. Security & Capital Safety — BLOCKED (2× P2)

Full file: `SECURITY_AUDIT_SEC_CAPITAL_DATA_BROKER.md`. Summary:

| Area | Grade | Detail |
|---|---|---|
| `torch.load / weights_only` | **FAIL** | 9 production loads: `live_engine.py:2684,2727,2755` + `governance/load_gate.py:66` + `runtime.py:110` + `integrity.py:269,314,426` + `checks_features.py:421` + `shadow/challenger.py:127` + `model_governance_routes.py:582` + `streaming_replay.py:135` — no `weights_only=True` — RCE before validation |
| Path traversal | **FAIL** | `live_engine.hot_swap_model:1505` + `diagnostics_state_routes.model_hot_swap:1636` accept arbitrary string → `Path.exists() → torch.load` — no `resolve().is_relative_to(ARTIFACTS_ROOT)` |
| Auth on `model_hot_swap` | **FAIL** | `diagnostics_state_routes.py:1626` — zero `Depends`/JWT/Bearer — `server.py:425 CORS ["*"]` |
| Promotion token | **FAIL-weak** | `execute_promotion` token is presence-only, not `HMAC(actor,model_id,expiry)` + single-use |
| Silent exception swallowing | **PASS** | `audit_repository.py` DDL `except: pass` documented — broker layers degrade to `UNAVAILABLE`, not silent trade hides — minor: `live_engine._expected_num_features_for_artifact:2688` silently falls back to `FEATURE_DIM` |
| Degraded-data trading | **PASS** | `live_freshness_gate()` + `DEGRADED→BLOCKED→NO_TRADE` — DEGRADED never trades |

---

## 12. Remaining Blockers (ranked — do not downgrade)

| Pri | ID | Status | One-liner |
|---|---|---|---|
| **P0** | MDL-INCOHERENCE | **BLOCKED** | `70d_liquidity` metadata 3 vs tensor 4 — must be re-emitted by live 34×10 retrain — no manual rename |
| **P1** | SEC-CAPITAL-DESER | **BLOCKED** | `torch.load` without `weights_only=True` — 9 call sites |
| **P1** | SEC-CAPITAL-PATH+AUTH | **BLOCKED** | arbitrary `model_artifact_path` + unauthenticated `model_hot_swap` + CORS `*` |
| **P2** | DATA-GOV-PROVENANCE | **FAIL** | `model.meta.json dataset_id/lineage/git_commit null` |
| **P2** | REL-BUG-160+BUG-239 | **DEFERRED** | `release.yml` pre-stage + ISCC flag — separate PR stack `nse_qa_head_wt` |
| **P3** | BUILD-SITE-FLAG | **DEFERRED** | `scripts/docs/build_site.py` 600-line `FLAG_BUILD_INDEX` dead tail + `docs-enhance` wiring — P3 commit `979624fc` pending publish |
| **P3** | WORKTREE-PRUNE | LOW | `scratch/rungate/*` 3 prunable wts — `git worktree prune` only after review |

---

## 13. Safe Level

```
SAFE TO CONTINUE VALIDATION (off-live)
SAFE FOR PAPER ONLY  (no live trading on any 70D ScalpNet artifact)
BLOCKED FROM LIVE
BLOCKED FROM MERGE (Batch B/C — model swap / lifecycle / risk)
BLOCKED FROM PROMOTION (any candidate while P0 holds)
```

No architecture change proposed — wrong head is not a capacity problem.

---

## 14. Next Verifiable Step (after retrain emits coherent bundle)

```
1. sha256sum model.pt  → new sha != c8c0b5b0
2. torch.load(weights_only=True)  → classifier.weight shape [3,32]
3. model.meta.json  → dataset_id ds_70d_clean_m1_20260904 + hash 235b8fccc96b7e0e + seq_len 32 + class_count 3 + git_commit != null + seed 42 + num_folds 34
4. behavioral 12-probe suite (machine + human JSON)
5. OOS walk-forward 34-fold metrics + calibration (ECE) + confusion matrix
6. offline↔live (B,32,70) replay parity  (same scaler, same logits±1e-5)
7. candidate→challenger→champion promotion proof (hash-gated, HMAC token after fix)
```

All until then: **monitor PID 26528/25032, preserve 6 stashes, do not promote.**

*Generated from direct tensor reads, manifest reads, and process probes — not from agent summaries.*
