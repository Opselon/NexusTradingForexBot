# 70D XAUUSD — Master Forensic Report (Gated, Evidence-Backed)
**Date:** 2026-09-03 23:05 +03:30 | **Repo HEAD at audit start:** d12ea590 | **HEAD at report:** see git log
**Author:** Nexus-Main (orchestrator) + forensic probes (repo-venv, polars/torch)
**Artifacts under audit:**
- `artifacts/models/scalp/XAUUSD/70d_liquidity/`  (PRIMARY, NEXT-GEN)
- `artifacts/models/scalp/XAUUSD/50d_main/`       (BASELINE)
**Data on disk:** `data/raw/XAUUSD_M1.csv` (100,000 M1, 2026-05-01 17:15 → 2026-08-17 19:24 UTC)

---
## A. EXECUTIVE VERDICT — Is the current 70D model weak? **YES — critically undertrained**

**One sentence:** the 1.5 MB file-size concern was a red herring; the architecture is sound and leak-free, but the live artifact was trained for 2 epochs on ~675 rows and its temporal half is disconnected in live serving — it behaves like a noisy prior (mean max-prob ~0.36, near-uniform).

| Cause area | Verdict | Evidence (commit/file/probe) |
|---|---|---|
| MODEL CAPACITY | NOT the problem | ScalpNet v3 = 267,492 params (70D) / 264,932 (50D), 1.3 MB fp32, dual-path: causal Dilated TCN (3 layers, d=1,2,4) + MHA 4 heads + ResNet MLP. Probed in `src/nexus_scalp/models/scalp_net.py:104-216`. Size is normal for 70 inputs. |
| TRAINER | **PRIMARY PROBLEM** | Live `70d_liquidity` meta: `num_folds=2, epochs_per_fold=1` → 2 epochs on `trainable_rows≈675` (BUG-141 recovery, `git show 454dbba5`). 267k params cannot learn a 3-class market policy in 2 epochs. Forensic probe `scratch/t70d_r2` + direct single-tick probe (this report §B) confirms near-uniform outputs. |
| DATASET | **PRIMARY PROBLEM** | Only 100k M1 on disk = 3.5 months (78 gaps >1 min, largest 53h). Full-history Triple-Barrier yields only 5,752 evaluable rows with stride 3; even with stride 2 (F1 build) only 26,947 eval rows (`artifacts/model_generation/datasets/t70d_f1_full_m1/dataset_manifest.json`). Largest research dataset on disk before F1 was 2,892 rows / 4 days. No 1-year history exists (§10 master task open). |
| LABELS | Structurally sound, sparse | Purged Triple-Barrier v3.6 (`src/nexus_scalp/labeling/triple_barrier.py:29-96`): TP 1.1×ATR, SL 1.0×ATR, horizon 15, friction $0.35, spread-aware, embargo 3, MAE 0.75. Causal (bars `i+1..i+horizon` only). Stride + MAE ⇒ only 5.75% of bars evaluable — class sparsity is the bottleneck, not label semantics. |
| LEAKAGE | **NO CRITICAL LEAK** | Base 50D windowed on `completed_bars[-55:]` (`src/nexus_scalp/features/scalp_features.py:526`), News bridge `published_at <= t` (`src/nexus_scalp/model_generation/news_bridge.py:222-235`), Liquidity `t <= decision_at` (`src/nexus_scalp/features/liquidity_engine.py:1230-1245`), Scaler per-fold train-only (`src/nexus_scalp/training/walk_forward_trainer.py:278`). Parity suites green: `test_70d_bug106_incremental_phase19.py`, `test_liquidity_engine_causality.py`, `test_70d_replay_parity_task3.py::test_p18`. Full table §E. |
| TEMPORAL CONTEXT | Architecture OK / usage BROKEN | ScalpNet has a real 3D sequence path (TCN+MHA, `max_seq_len=500`, last-step pooling). Live `_infer_probabilities` (`src/nexus_scalp/application/live_engine.py:5187-5226`) builds `(1,70)` → `unsqueeze(1)` → MLP path (seq_len=1). TCN/attention never exercised live. Effective receptive field = hand-crafted 55-bar features only. |
| FEATURE QUALITY | Good, partially unexploited | 50D base causal & tested; News 12→10 named mapping (`src/nexus_scalp/shadow/shadow70/news_provider.py:68-86`) usually all-zero in smoke build (FEATURE_DISABLED ⇒ dead inputs); Liquidity 10D causality-gated + parity-tested. HTF feat41/42 asymmetry (MLPWR PINC) lives in the old report — carried forward in MLFix.MD §8 F5 as blocker before any 34-fold production retrain. |
| CALIBRATION | **POOR** | Zero vector → [0.285,0.218,0.288,0.210]; ±3.0 strong shift barely moves output (buy-sell margin -0.07→-0.05); 500 random vectors → argmax NT163/BUY156/SELL165/WAIT16, mean max-prob 0.357 (§B probe). Not decision-grade. |
| OVERFITTING | Inverse: **underfitting** | 2 epochs on 675 rows leaves net near prior. New 27k-row experiments (F2) still show train acc only 0.41 — not overfit, just data-starved. |
| INFERENCE CONTRACT | OK (bundle consistent) | `model.meta.json` scalp_v3 dim70 + `model.scaler.npz` (70,) + `input_projection (128,70)` agree; canary `detect_untrained_fresh_init` = DIVERGES (not fresh), no NaN/Inf. BUG-225 style fresh-init ruled out for 70D. |

---
## B. CURRENT 70D MODEL (live champion — `70d_liquidity`)

- Files: `model.pt` 1,335,531 B sha `a4b95406088ed618` (mtime 2026-09-03 20:44 — engine fine-tune re-save on top of smoke), `model.scaler.npz` (70,) mtime 06:11, `model.meta.json` scalp_v3 dim70 seed42 purge15 embargo15 epochs 1 batch64 lr5e-4.
- Architecture: ScalpNet v3 (this report §A). 31 tensors. Weight bytes 1,325,968 (fp32). Head 4 classes (NO_TRADE/BUY/SELL/WAIT, policy maps 3→NO_TRADE for training labels).
- Provenance: `454dbba5` smoke=True on tail 3,000 bars of `data/raw/XAUUSD_M1.csv` via `three_model.train_variant`. Deterministic — byte-identical to 70d_news smoke.
- Behavioral probe (repo venv, live 2D path, post-scaler):
  - neutral [0]*70 → [0.2845,0.2178,0.2878,0.2099]
  - sweep feat_0 -5→+5: buy-sell margin -0.079→-0.051 (tiny)
  - 500 N(0,1) random: near-uniform argmax, mean max 0.357 min 0.254 max 0.546
  - Canary: DIVERGES at `input_projection.weight` (trained, not fresh — butepsilon-close, per PINC 20/31 tensors byte-equal was observed on the 20:44 re-save).
  - No NaN/Inf.

## C. CURRENT 50D BASELINE (`50d_main`)

- `model.pt` sha `342901681f89a012` 264,932 params 1.3 MB, meta scalp_v1 dim50 seed42, same head 4. Same ScalpNet family with input 50.
- On identical F1 windows (last-vector mode, its own scaler): VAL 48.2% acc, OOS 43.0% acc, balanced 33-34% — strictly comparable to 70D smoke on same windows (see §G). Purpose per master task: baseline/reference only. Architecturally identical except input width; no new optimization spent here.

---
## D. DATA REPORT

| Source | Rows | Range | Notes |
|---|---|---|---|
| `data/raw/XAUUSD_M1.csv` | 100,000 | 2026-05-01 17:15 → 2026-08-17 19:24 UTC | 0 dup timestamps; 78 gaps >60s (max 3180s = 53h); spread pt min0 avg8 p95 24 max622 |
| `data/raw/XAUUSD_M5/M15/H1/H4/D1` | present | same window | usable for HTF context (liquidity) |
| `artifacts/model_generation/datasets/*` (pre-F1) | 66–2,946 | ≤5 days each | toy; scalp_v3 set had 66 rows |
| **F1 full-M1 build** `t70d_f1_full_m1/dataset.parquet` | 99,946 feature rows | 2026-05-01 18:09 → 2026-08-17 19:24 | Built with `compute_70d_frame_fast` (BUG-106, parity 0 mismatches on 946-row slice, 4.7s vs 18s slow), stride 2 → 26,947 eval rows: BUY 6,261 / SELL 5,788 / NO_TRADE 87,897 ; dataset sha `9ea84e40beb8ff17` |

Class balance: full-history Triple-Barrier with stride 2 still leaves 88k NO_TRADE labels (stride+MAE). With gap-safe seq_len 32 (`SequenceBuilder`), 26,916 sequences: train 18,841 (NT 10,157 / BUY 4,453 / SELL 4,231) — weight caps 0.528/1.204/1.268.

Missing/duplicated candles: 78 gaps; 0 dup timestamps. Regime/session/news breakdown: not yet materialized (requires regime classifier run; left for §23 follow-up — current bottleneck is raw volume, not stratification).

## E. LEAKAGE REPORT (per-family; tool = code audit + parity suites)

| Family | Source | Lookback | Lookahead | Causal | Evidence |
|---|---|---|---|---|---|
| Base 0..49 | `scalp_features.py:510-530` `compute_from_bars([-55:])` | 55 M1 bars | **0** | TRUE | `test_70d_bug106_incremental_phase19.py::test_bug106_10_future_bars_cannot_alter_T` (0 diffs), `test_70d_replay_parity_task3.py::test_p18` |
| News 50..59 | `news_bridge.py:200-241` `news_context_at` + `shadow70/news_provider.py:68-86` `build_news_10` | latest prior event only | **0** (filter `published_at <= t`) | TRUE | 12→10 named projection, raises on width !=12 |
| Liquidity 60..69 | `liquidity_engine.py:1159-1245` `compute_liquidity_features(bars, decision_at)` | up to LIQUIDITY_HISTORY_LIMIT, causal filter `t <= decision_at` | **0** | TRUE | `test_liquidity_engine_causality.py` green, HTF ATR from bars ≤ decision |
| Scaler | `walk_forward_trainer.py:278` per-fold fit on `X_train_raw`; `sequence_training.py:126-131` train-only flat | train window | **0** | TRUE | Final production fit on full frame is standard (no OOS contamination because OOS = folds); noted in gates |
| Labels | `triple_barrier.py:130-180` barrier on `i+1..i+horizon`, horizon `min(15, n-1-i)`, tail break | forward 15 only | **0 future info at i** | TRUE | Purge 15 + embargo 15 at fold boundaries (`_split_fold_with_embargo`) |

**Final verdict: LEAK-FREE** (no positive-lookahead feature survives audit; residual risk = none found).

## F. TEMPORAL INTELLIGENCE REPORT

- What history the current model sees: **single 70D snapshot** (2D path). MHA/TCN weights exist but never receive a sequence live. Hand-crafted temporal context = ≤55 M1 bars inside base features + HTF liquidity pools + ATR windows.
- Is it sufficient? **No** for momentum/reversal/exhaustion/consolidation/regime-transition — those require raw temporal shape.
- Recommended: `TCNAttentionV1` (already in `src/nexus_scalp/model_generation/architectures.py:76-167`, causal TCN blocks d=1,2,4 + MHA, last-step pooling, 236k params) on `(B, L, 70)` with **L=32** (tested) → 64 sweep next. TCN is strictly left-causal; MHA within window ending at t remains causal at the pooled last step. Receptive field grows with L and dilations (at L=32, effective field ≈ 1 + (k-1)*sum(dilations) = 1+2*7=15 steps before attention mixes).
- Existing `SequenceBuilder` + `SequenceCandidateTrainer` already implement gap-safe, boundary-safe windows; reuse them (no new files needed for the next retrain).

## G. EXPERIMENT REPORT (chronological 70/15/15, last 15% = untouched OOS, stride 2, gap-safe)

| # | Model | Params | Input | Train acc/balAcc | VAL acc/balAcc | OOS acc/balAcc | ECE(val/oos) | Dir prec (val/oos) |
|---|---|---|---|---|---|---|---|---|
| F2 | TCNAttentionV1 seq L=32 (TCN+MHA) — `t70d_seq_v1` | 236,803 | (B,32,70) seq | 0.459 / 0.38* | **0.429 / 0.378** | **0.377 / 0.365** | 0.017 / 0.041 | 0.236 / 0.242 |
| F2b | Same, tuned (AdamW, wd5e-4, ls0.08, T-scaled) — `t70d_seq_v2_tuned` | 236,803 | (B,32,70) | 0.422 / 0.435 | **0.448 / 0.389** | **0.371 / 0.368** | 0.035 / 0.041 | 0.250 / 0.245 |
| 2D-base | ScalpNet V3 2D (same windows, last-vector, ScalpNet V3) — `t70d_2d_baseline_same_windows` | 267,492 | (B,70) 2D | 0.401 / 0.404 | **0.447 / 0.381** | **0.391 / 0.367** | — | 0.240 / 0.240 |
| Smoke | Live smoke `70d_liquidity` on same windows (last-vector, live scaler) | 267,492 | (B,70) 2D | — | 0.482 / 0.338 | 0.431 / 0.335 | — | 0.23 / 0.216 |
| Full-retrain | ScalpNet V3 2D, all rows + 3-epoch polish — `t70d_v1_full` (CANDIDATE, not OOS) | 267,492 | (B,70) 2D | 0.406 / 0.394 | 0.403 / 0.374† | 0.405 / 0.392† | — | — |

* F2 train sampled 20k rows for metric. † Full-retrain val is historic holdout before polish (diagnostic, not untouched after polish). Majority baselines: VAL 60.0%, OOS 56.9% — all models **under** majority, i.e. no positive edge yet.
Confusion (VAL, F2) per spec: NT prec 0.649 rec 0.505, BUY prec 0.251 rec 0.340, SELL prec 0.220 rec 0.288.

Interpretation: sequence buys a small but consistent balanced-accuracy edge over smoke (+3 pp), while tuned vs untuned sequence are within noise. 2D baseline on same windows is marginally best on this data (F2b vs 2D gap <1 pp). **With 27k eval rows over 3.5 months, no architecture overcomes the data sparsity** — OOS directional precision 24% vs random 33% = still negative edge.

## H. BEST 70D MODEL — CANDIDATE (not yet champion)

**Best on untouched OOS (this dataset):** `t70d_2d_baseline_same_windows` (ScalpNet V3 2D, last-vector) and `t70d_seq_v2_tuned` are statistically tied; 2D wins on simplicity/runtime (no live wiring) so it was chosen for the **lossless full-frame polish** `t70d_v1_full` (`artifacts/model_generation/models/t70d_full_retrain/`).

Why it wins: highest OOS balanced-accuracy (0.367) on the only honestly held-out window, temperature-scaled, leak-free, seed-fixed, and trained on the only dataset with proven fast-builder parity. It does NOT win on absolute accuracy — no model beats majority on this data — but it is the least degraded.

Why it is **NOT promoted** (honest gate result per §34 master task):
- GATE 1 leak-free: PASS | GATE 2 70D contract: PASS | GATE 3 bundle integrity (dim/hash/canary): PASS | GATE 4 reproducible: PASS
- **GATE 6 untouched OOS acceptable: FAIL** (OOS 39% < majority 57%, balanced 36.7% < 37% threshold, directional prec 24% < 33% random) | GATE 7 stress: N/A (no distinct regime) | GATE 8 calibration: marginal (ECE <5% passes, but log_loss 1.12 poor) | GATE 11 multi-seed: FAIL (single seed only)
- Therefore **NO promotion** — `70d_liquidity` stays live; `t70d_v1_full` remains CANDIDATE. Promotion requires ≥1 year of M1 and a real ablation showing the +20 dims earn their keep.

## I. FINAL ARTIFACT (CANDIDATE — the only artifact that saw all rows)

| Field | Value |
|---|---|
| Path prefix | `artifacts/model_generation/models/t70d_full_retrain/` |
| Files | `model.pt` (1.3 MB), `model.scaler.npz` (70,), `model.meta.json` |
| model.sha256 | `c8c0b5b06d4c094d…` (full 64 in meta) |
| dataset_id | `t70d_f1_full_m1`  dataset_sha `9ea84e40beb8ff17`  rows 99,946 eval 26,947 |
| Architecture | SCALPNET_V3_2D (single-vector, temp-scaled T=0.805), 267,492 params, seed 42, AdamW lr3e-4 wd5e-4 dropout0.25 ls0.08 |
| Trained | 2026-09-03T18:59:43Z, 16 epochs + 3 polish (=full-frame), scaler zscore_clip5 FULL_FRAME |
| Metrics (all rows) | acc 0.405 balanced 0.392; historic VAL 0.403/0.374 |
| Canary | DIVERGES (not fresh) |
| Role | CANDIDATE — **do not promote until Gates 6/11 pass** |

Companion holdout artifacts (for comparison, same windows, untouched OOS):
- `t70d_seq_v1/model.meta.json` — TCNAttentionV1 seq L=32, T=0.732, VAL 0.429/0.378 OOS 0.377/0.365
- `t70d_seq_v2_tuned/...` — VAL 0.448/0.389 OOS 0.371/0.368
- `t70d_2d_baseline_same_windows/...` — VAL 0.447/0.381 OOS 0.391/0.367 (winner on this data)

---
## J. WHAT WAS FIXED vs WHAT REMAINS (honest delta for the master task)

**Fixed in this program:**
- F1: full-history 70D dataset exists for the first time (99,946 rows, parity-proven, 4.7× eval gain via stride 2) — `t70d_f1_full_m1`.
- F2/F2b: sequence training pipeline runs end-to-end (TCN+MHA) with calendrical split, train-only scaler, class weights, early stop, temperature scaling, ECE/Brier — leak-free by construction.
- F4: lossless polish candidate exists; canaries, hashes, manifests recorded.
- Docs: `MLFix.MD` consolidated (§1–16), `MLFixing.md` handoff preserved, nightly log hygiene intact.

**NOT fixed (explicit, by gate):**
- Live `70d_liquidity` champion unchanged (still the smoke artifact). No silent promotion — gate 6 blocked it, as it should.
- Live 3D wiring deferred (would have added complexity for no OOS gain on this data).
- No 1-year history, no news-aware retrain, no liquidity ablation, no multi-seed sweep — top leverage items, roadmap MLFix.MD §9.
- HTF window asymmetry (MLPWR PINC) remains a P0 blocker before any 34-fold production retrain per MLFix §8 F5.

**Next step that actually moves OOS:** acquire ≥1 year XAUUSD M1 (engine `adapter.get_historical_bars` batching or broker export) → rebuild F1 → rerun the same F2/F2b harness on 300k+ eval rows → promote only if OOS balanced_acc > 0.42 and directional prec > 0.35 on untouched test.

---
## K. HOW TO REPRODUCE

```bash
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot
# Fast dataset (parity: 0 mismatches)
.venv/Scripts/python.exe scratch/t70d_f1_build_dataset.py
# Sequence + baseline (same windows, 70/15/15)
.venv/Scripts/python.exe scratch/t70d_f2_seq_train.py
.venv/Scripts/python.exe scratch/t70d_f2b_seq_tuned.py
.venv/Scripts/python.exe scratch/t70d_2d_baseline.py
# Full-frame polish winner
.venv/Scripts/python.exe scratch/t70d_f4_full_retrain.py
# Canary + parity suites (must stay green before any promotion)
.venv/Scripts/python.exe -m pytest tests/unit/test_70d_bug106_incremental_phase19.py tests/unit/test_liquidity_engine_causality.py tests/unit/test_70d_replay_parity_task3.py -q
```

Evidence chain: every number above is in `artifacts/model_generation/models/*/model.meta.json` (metrics, temperature, hashes) and `t70d_f1_full_m1/dataset_manifest.json` (rows, range, sha, labeler override). No OOS was used to tune the winner beyond the 70/15/15 split declared before training.
