# MLFix.md -- 70D XAUUSD Model: Forensic Findings, Fix Plan & Future Roadmap

**Task:** MASTER -- Forensic audit, research, retraining and production upgrade of the XAUUSD 70D AI model
**Author:** Nexus-Main (orchestrator) + Principal Incident Investigator (adversarial lane)
**Date:** 2026-09-03 | **Repo HEAD at audit:** d12ea590 -> 7882c39c -> 045baaf9
**Status:** FORENSICS PHASE COMPLETE -> FIX PHASE IN PROGRESS (consolidated -- single continuable doc)

> **Purpose.** Single entry point for any agent working the ML-repair lane (feature contracts -> datasets -> training -> champion artifacts -> live serving). Read this BEFORE touching `src/nexus_scalp/features*`, `model_generation/`, `training/`, `model_lifecycle/`, or `artifacts/models/`.
> **Contract:** read `agents/skill.md` + `agents/bugs.md` first; grep `^## BUG-` before claiming a number; claim TASK-ID in `agents/taskboard.md`; `<AGENT>: <imperative>` commits, commit-per-step. Repo python = `.venv/Scripts/python.exe` (always `-m`).
> Quality gate: `beforePush.sh` via `.venv/Scripts/python.exe -m ...` (ruff -> format -> mypy -> CRITICAL suite -> deploy gate).
> **Maintenance rule:** append dated updates at bottom (Doc log), never rewrite history sections. Every statement carries commit/file/probe reference.

- LAST UPDATED: 2026-09-03 ~22:00 +03:30 -- consolidated from MLFixing.md (381L, handoff v2) + MLFix.MD (129L) + PINC adversarial forensics (this session)
- Engine at writing: NOT running (web/API 127.0.0.1:8099, `engine_running=false`; last ASYNC RETRAIN 2026-09-03 20:44 local)

---
## 1. EXECUTIVE VERDICT

**Is the current 70D model weak? YES -- critically undertrained, now epsilon-diverged from random init (not just smoke).**

| Cause area | Verdict | Evidence |
|---|---|---|
| MODEL CAPACITY | **NOT the problem** | 331,492 params (70D) / 267,492 (alt count), 1.3 MB, dual-path ScalpNet v3 (causal TCN 3 layers + MHA 4 heads + ResNet MLP). Architecture sound. |
| TRAINER | **PROBLEM (primary)** | Live artifact `a4b95406088ed618` (mtime 2026-09-03 20:44) traces to `train_variant('70d_liquidity', smoke=True)` -> 2 folds × 1 epoch over ~675 rows (BUG-141 recovery 454dbba5). 331k params cannot learn a 3-class policy in 2 epochs. |
| CURRENT WEIGHTS | **DEGENERATE (epsilon-diverged)** | Tonight's independent probes (PINC): 20/31 tensors byte-identical to seed-42 fresh init; 16 tensors epsilon-drift (max 0.004 on input head, 0.16 on classifier bias). Logit std 0.06-0.10, KL(uniform)=0.012. |
| DATASET | **PROBLEM (primary)** | Only `data/raw/XAUUSD_M1.csv`: 100,000 bars, 2026-05-01 -> 2026-08-17 (3.5 months, 78 gaps >1m, largest 53h). Triple-Barrier yields only 5,752 evaluated rows (BUY 1,599 / SELL 1,539 / NO_TRADE 2,614) — **historical tail-era figure at stride 3 (§2)**. Research sets largest 2,892 rows / 4 days. **RECONCILED 2026-09-04:** authoritative full-history dataset is `ds_70d_clean_m1_20260904` — 99,946 rows / 26,947 eval (stride 2, purge 15 + embargo 15, sha `3ae687ea`, see §11.1) — not a contradiction, but tail vs full-history (§8 F5 stride 3→2 mitigation now realized). |
| LABELS | **Sound but sparse** | Purged Triple-Barrier v3.6 (TP 1.1×ATR, SL 1.0×ATR, horizon 15, $0.35 friction, spread-aware) causal; stride-3 + MAE ⇒ only 5.75% evaluable. On the authoritative full-history dataset the same barriers at **stride 2** yield 26,947 eval rows (see §11.1; barriers identical, captured in `label_config_hash`). |
| LEAKAGE | **No critical leak found** | Windowed builders, news `published_at <= t`, liquidity `t <= decision_at`, scaler fit on TRAIN per fold. Final production scaler on 100% is standard. |
| TEMPORAL CONTEXT | **Architecture OK / usage BROKEN** | ScalpNet has 3D sequence path (TCN+attention) but live feeds single 2D vector `(1,70)` -> MLP path only; seq_len=1. Temporal receptive field = hand-crafted features (≤55 bars) only. |
| FEATURE QUALITY | **Good but exploits HTF asymmetry** | 50D base causal; news 10D (12->10 mapping) usually zero in smoke build; liquidity 10D causality-gated. HTF feat41/42 are TRAIN-zero vs LIVE-3.0 (MLPWR-06-02). |
| CALIBRATION | **POOR -- near-uniform** | Zero vector -> [0.284,0.294,0.215,0.206]; ±3.0 moves BUY by 0.01; 500 random -> argmax [149,234,113,4] mean max-prob 0.283; group swings 0.06-0.12. |
| INFERENCE CONTRACT | **Structural OK, semantic BROKEN** | meta scalp_v3 dim70 + scaler (70,) + head (128,70) agree; canary now DIVERGES (epsilon) so structural gates pass while behavior fails. |

**One-line verdict:** the model is the right size for the job; it was trained on the wrong amount of data for the wrong number of steps, its current weights are a hair away from random noise, and it is self-perpetuated by a "keep baseline + always save" loop.

---
## 2. CURRENT BUNDLE FACTS (evidence)

### 70d_liquidity (PRIMARY) -- `artifacts/models/scalp/XAUUSD/70d_liquidity/`
- `model.pt`: 1,335,531 B, sha256 `a4b95406088ed618`, 31 tensors, ~331k params, no NaN/Inf. mtime 2026-09-03 20:44.
- `model.scaler.npz`: mean/std (70,) z-score + clip [-5,+5]; std_min 0.001 (feat42) signals TRAIN-constant column.
- `model.meta.json`: scalp_v3 dim70 head4 seed42 train_ratio 0.7 num_folds 2 purge15 embargo15 epochs_per_fold 1 batch64 lr5e-4 cpu. Columns `feat_0..feat_69` generic (semantic names in `features/schema_contract.py`).
- Provenance: smoke=True on tail-3,000 of `data/raw/XAUUSD_M1.csv` (454dbba5, 2026-08-29) + engine fine-tune touch 2026-09-03 20:44 (baseline kept per BUG-228).

### 50d_main (BASELINE) -- `artifacts/models/scalp/XAUUSD/50d_main/`
- ScalpNet v3 input 50, ~264k params, 1.3 MB, meta scalp_v1 seed42. Reference baseline only.

### Fallback contamination -- `artifacts/models/scalp/XAUUSD/v1.0.0/` + `EURUSD/v1.0.0/`
- Both byte-identical to seed-42 fresh init (TRUE, `BYTE_EQUAL_TO_FRESH_INIT`), hash `0872ae0b85b3c74b`. Any boot serving these serves noise.

### Data inventory

> **Historical sparsity note (tail-era, preserved).** When this doc was written on the tail=3000 smoke build, §1/§2 quoted **5,752 eval rows at stride 3** over the full 100k M1 span (the labeler's yield at that stride). That figure is historical and remains below for provenance. The authoritative post-fix dataset (§11.1) is `ds_70d_clean_m1_20260904` — 99,946 rows / 26,947 eval at stride 2, purge 15+15 (sha `3ae687ea`, verified on disk).

| Source | Size | Range | Notes |
|---|---|---|---|
| `data/raw/XAUUSD_M1.csv` | 100,000 bars | 2026-05-01 17:15 -> 2026-08-17 19:24 UTC | 0 dup timestamps; 78 gaps >1m largest 53h; spread avg 8 pts p95 24 max 622 |
| `data/raw/XAUUSD_M5/M15/H1/H4/D1` | present | same window | usable for HTF |
| `artifacts/model_generation/datasets/*` | 66-2,942 rows | ≤5 days each | toy-scale; M1+scalp_v3 has 66 rows |
| Labels over full M1 *(historical, stride 3)* | 5,752 eval rows | -- | BUY 1,599 / SELL 1,539 / NO_TRADE 2,614 | — tail-era sparsity (stride 3) preserved for provenance; see post-fix full-history addendum §11.1 |

### Tonight's canary snapshot (2026-09-03 ~21:30 local)
- `70d_liquidity` (70): `(False, 'DIVERGES_AT:input_projection.weight')` -- epsilon-diverged, NOT trained quality.
- `v1.0.0` (50): `(True, 'BYTE_EQUAL_TO_FRESH_INIT')` -- contaminated.
- `50d_main` / `70d_news`: diverge (trained, not fresh -- but 50d_main logit std only 0.01-0.02, also weak).

---
## 3. LEAKAGE AUDIT SUMMARY (per-family)

- **Base 50D** (`features/scalp_features.py::ScalpFeatureEngine`): windowed on `completed_bars[-55:]` + synthetic tick at bar-t close. Only backward indexing. Swing uses `[i-5, i+6]` inside closed window whose last element is bar t -> all ≤ t. CAUSAL. Anti-leak: `test_70d_bug106_incremental_phase19.py::test_bug106_10_future_bars_cannot_alter_T` (0 features change; PASS).
- **News 10D**: dataset `model_generation/news_bridge.py::news_context_at` filters `published_at <= sample_ts`. Live `shadow/shadow70/news_provider.py::build_news_10` is pure 12->10 projection. CAUSAL. **Gap:** smoke build passed `news_frame=None` -> news block all-zero / FEATURE_DISABLED ⇒ model never learned news semantics.
- **Liquidity 10D** (`features/liquidity_engine.py`): explicit `t <= decision_at`; H1/H4/D1 HTF from bars ≤ decision; defaults when insufficient. Parity suites green (`test_liquidity_engine_causality.py`, `test_70d_replay_parity_task3.py::test_p18_future_bars_do_not_change_historical_vector`). CAUSAL.
- **Scaler**: walk-forward folds fit scaler on fold-train only (`walk_forward_trainer.py:278`). Final production scaler on 100% is standard; OOS comes from folds/time-split. Fine-tune reuses artifact scaler unless incompatible.
- **Labels** (`labeling/triple_barrier.py`): barriers on `i+1 .. i+horizon`, horizon `min(15, n-1-i)`, tail beyond horizon skipped; purge 15 + embargo 15 at fold boundaries. CAUSAL.
- **TONIGHT'S FINDING (MLPWR-06-02) -- TRAIN vs LIVE HTF window asymmetry** (probe-proven, not yet BUG row):
  - Symptom: TRAIN-vs-LIVE parity corpus bit-exact on 68/70 features (`PARITY VERDICT: MISMATCH`, max_delta 3.0, TOL 1e-12) -- mismatches ONLY at idx 41 `htf_h1_momentum` (train 0.0 / live 3.0) and idx 42 `htf_m30_structure` (train 0.0 / live 1.0). Hash `235b8fccc96b7e0e` identical both sides.
  - Root cause: `compute_from_bars` slices last 55 bars for base (`scalp_features.py:526`) but aggregates HTF from FULL `completed_bars` (`:231-234` aggregate 15/30/60/240). TRAIN builder `schema_v2.compute_70d_frame:74` `window = all_bars[max(0,i-54):i+1]` -> ALWAYS 55 -> H1 buckets ≤1 -> h1_momentum 0.0 in EVERY training row. LIVE caller `live_engine._process_tick_pipeline:3554-3557` passes aggregator depth cap 4000 (~900 after BUG-058) -> after ~2h h1_momentum real (6.77 -> clipped 3.0).
  - Depth grid (same synthetic window, re-executed tonight):
    ```
    depth | h1_mom | feat41 | feat42
       55 |  0.000 |  0.000 |  0.0
       60 |  0.000 |  0.000 |  0.0
      120 |  6.770 |  3.000 |  0.0
      240 |  6.770 |  3.000 |  1.0
     4000 |  6.770 |  3.000 |  1.0
    ```
  - Consequence: champion fed slot-41/42 values at inference that TRAIN never contained (all-zero), through scaler std ~0 -> hard saturation. Poisons every online-retrain record (contributor to 17+ fine-tune rejections 2026-09-02). 2,206 live signals on 2026-09-03 decided at depth TRAIN never sees.
  - Probe inventory (scratch/, re-runnable read-only -- commit with fix): `mlpower_parity_corpus_probe.py` (+.out.txt), `mlpower_parity_feat41_diag.py`, `mlpower_parity_htf_window_diag.py`, `mlpower_parity_htf_live_train_callgrid.py`, `mlpower_parity_htf_realdepth_probe.py`.
  - Decision (feature owner + model owner): (a) TRAIN pass live-equivalent depth, or (b) bound live HTF to 55-bar semantics, or (c) window-normalize HTF inside `compute_from_bars`. ANY choice -> dataset regenerated + champion retrained. Prefer no name/order change (hash stable). Register BUG row with this evidence FIRST.
- **VERDICT: LEAK-FREE on lookahead; TRAIN/LIVE distribution shift on HTF is the leak-class defect to fix.**

---
## 4. TEMPORAL INTELLIGENCE -- WHAT THE MODEL ACTUALLY SEES

- **Training:** single 70D snapshot per row (2D -> MLP path). TCN + positional encoding + attention NEVER trained (3D branch; epochs never saw a sequence).
- **Live serving:** `_infer_probabilities` builds `(1,70)` -> scaler -> 2D tensor -> MLP path. Sequence path dead. `_last_70d_assembly_timings` confirms assembly but not sequence.
- **Consequence:** "temporal intelligence" lives ONLY in hand-crafted features (EMA/ATR/structure ≤55 bars, liquidity pools ≤ HISTORY_LIMIT, HTF ratios). Model is a static per-tick classifier.
- **Fix direction (this program):** train AND serve on sequences `(batch, L, 70)` through existing causal TCN+MHA path, L≈32-64 M1 bars, strict left-causal (TCN causal; attention bidirectional within window but window ends at t -> causal at last step; pooling `h[:, -1, :]`). Additive new method `train_and_validate_sequence` reusing fold/purge/embargo helpers; engine keeps bounded deque of last L post-scaler vectors per symbol.

---
## 5. ADVERSARIAL INDEPENDENT FINDINGS (this session -- PINC lane)

Tonight's read-only PINC probes (same live artifact, no engine restart, no writes):

- **Probes A-I** (`scratch/ns_pinc_probe_r2.py` + `scratch/ns_pinc_probe_r2_out.json`):
  - A zero: [0.284, 0.294, 0.215, 0.206] KL(uniform)=0.0125 near-uniform.
  - B/C all ±3: BUY 0.267 -> 0.257 (Δ margin 0.046) -- physiological, not informational.
  - D single-feature +3/-3: max swing 0.205 (dim 37), mean 0.072.
  - F 500 random N(0,1) clipped [-5,5]: argmax [149,234,113,4] mean_max_prob 0.283 (4-class chance 0.25).
  - G groups from zero: base 0.060 / news 0.080 / liq 0.125 -- all weak.
  - Logit std per class on random: [0.069,0.098,0.101,0.061] range [0.366,0.616,0.59,0.275].

- **Offline vs scaler** (`scratch/ns_pinc_exp2_offlive.py`): same vector raw vs scaler-transformed -> max logit diff 0.095, max prob diff 0.024. WAIT mass on 200 random mean 0.223 max 0.257 (trained_mass 0.777). 4-way head (NO_TRADE/BUY/SELL/WAIT) vs 3-class labels -> WAIT (index 3) never trained but eats ~22% prob mass on every vector, depressing trained-class posteriors.

- **Weight forensics vs seed-42 fresh init:**
  - `detect_untrained_fresh_init(70d_liquidity, 70)` -> `(False, 'DIVERGES_AT:input_projection.weight')` -- epsilon-diverged, NOT byte-equal.
  - 20/31 tensors byte-identical to fresh init (tcn_norm, attn_norm, attention biases, pos_encoder).
  - 16 tensors epsilon-drift: input_projection.weight maxdiff 0.0049, classifier.bias 0.161 -- one tiny update, not training.
  - `v1.0.0` (50D) -> `(True, 'BYTE_EQUAL_TO_FRESH_INIT')` -- still contaminated.
  - `50d_main` / `70d_news` diverge but also weak (50d_main logit std 0.01-0.02).

- **Provenance chain:**
  - Registry `experience_model_registry` first appearance of `a4b95406088ed618` = 2026-09-03 18:17:47 (CANDIDATE scalp_v3_70d + CHAMPION scalp_v1_50d dual row -- BUG-225 truthfulness sync artifact).
  - File mtime 20:44 = engine's ASYNC RETRAIN touch (buffer_size 300, 94 labeled rows [60,3,1,0] -> oversampled [60,51,51], early stop epoch 2, val_loss 0.600, "no improvement; keeping baseline weights" per BUG-228 honest path, then `ASYNC RETRAIN SUCCESS` + atomic save of the SAME baseline).
  - Consequence: `P0-3 divergence` (18:17 baseline already epsilon-diverged) is re-persisted on every retrain window while the canary stays GREEN -- structural gates pass on degenerate semantics.

- **First broken state:** MODEL. Market->Features still varies (HTF depth flips feat41/42), Features->ModelInput still varies (scaler preserves diff, albeit clipped), but ModelInput->Logits is flat (0.09 logit shift for 0.024 prob). The system stopped knowing at the weights.

- **Settings truth:** `app_settings.db` execution.mode = LIVE, model_artifact_path = `70d_liquidity/model.pt` (correct); `configuration_metadata` runtime_config.version=4 WEB_ENGINE_MODE. Boot correctly loads 70D expected_dim=70 + scaler (70,) + CHAMPION VERIFIED a4b954 at 18:48/18:49/19:48.

---
## 6. DONE -- EVIDENCE LEDGER (pre-PINC)

### 6.1 Feature-contract repairs (landed, committed)

| BUG | What | Where | Commit |
| :-- | :--- | :---- | :----- |
| BUG-190 | live 70D news block read raw `CurrentNewsContext.model_dump()` -- 4/10 slots wrong keys | `_build_live_feature_vector` / `_build_retrain_record` -> canonical projection (`governance.alignment.vectorize_news_context` + `shadow70.build_news_10`) | CHG-0038 fidelity-audit lane |
| BUG-197B | slot 50 carried RAW aggregate event count ⇒ every tick with ≥4 events failed `[-3,+3]` and blocked ALL 70D inference (13k+ failures in one log) | `vectorize_news_context` now emits bounded 0/1 flag at training-distribution max | `6b893f04`, ledger `5a895ab7` |
| BUG-217 | news state encoding BREAKING=4.0 / STALE=5.0 exceed `[-3,+3]` at slot 59 (latent) | repaired producer-side, clamped to training semantics (CHG-0052) | `c576dfac` |
| BUG-185 | rolling retrain buffer class-locked to 50D ⇒ every online fine-tune silently skipped while 70D champion served | `_retrain_record_dim()` builds records at loaded bundle width | `203f1873` + `b873c047` |
| BUG-141 | 70D bundle clobbered by 50D checkpoint write; no width guard on artifact writers | width-contract guards on writers + recovery recipe | `agents/bugs.md` BUG-141 |
| BUG-183 | production research path ran purge/embargo = 0.0 despite BUG-140 constants (false provenance) | wired `DEFAULT_PURGE_SECONDS=300` / `DEFAULT_EMBARGO_SECONDS=60` into pipeline/OOS/walk-forward/backtest | `11ea316`, `128f87c`, `967a468` |

### 6.2 BUG-225 -- untrained champion (detection LANDED, repair PENDING)

- Ledger: `agents/bugs.md` `## BUG-225` (full root-cause chain).
- Detection landed `3f5f9db7`: `src/nexus_scalp/model_lifecycle/integrity.py::detect_untrained_fresh_init` (byte-compare to seed-42), `CHECK-MDL-02 check_model_semantic_health()` in deploy-gate Model group (CRITICAL on byte-equal), `tests/unit/test_bug225_untrained_champion_canary.py` (7 tests) in `tests/critical_suite.txt`. `test_real_champion_artifact_is_trained` is the runtime invariant -- INTENTIONALLY RED at landing, GREEN since 20:44 (but green ≠ "well trained" per §2 caution and P0-3 in §5).
- Self-perpetuation (proven): quality gate correctly REJECTS every online fine-tune against degenerate baseline and rolls back -- but baseline IS the fresh init, and LiveEngine persists the returned model unconditionally (`ASYNC RETRAIN SUCCESS` + atomic save after `accepted=False`).

### 6.3 BUG-228 -- trainer honesty on zero-improvement fine-tunes (FIXED)

- Commit `52615bf7`: when early-stop restores `best_state == baseline` trie skip (INFO "no improvement; keeping baseline weights"), genuine gate rejections via structured logger. Regression `tests/unit/test_walk_forward_trainer.py::test_wf_zero_improvement_early_stop_skips_quality_gate_rejection`. Live-confirmed: 2026-09-03 20:44.872 `baseline_acc=0.667 ... val_acc=0.667`.

### 6.4 Live runtime observations (2026-09-03, verified this machine)

- CHAMPION VERIFIED `a4b95406088ed618` at 18:48 / 18:49 / 19:48 local -- matches disk hash.
- 20:44:00 local: `ASYNC RETRAIN START buffer_size=300` -> `no improvement; keeping baseline weights` -> `ASYNC RETRAIN SUCCESS`. Canary flips from byte-equal to `DIVERGES_AT:input_projection.weight` (P0-3) -- but trained-quality NOT proven.
- Snapshot 17:55Z: `engine_running=false` (only API/UI). Engine lifecycle is USER-OWNED -- do NOT start/stop/restart without explicit user OK.

---
## 7. CURRENT ARTIFACT TRUTH TABLE -- extended (tonight, 22:00 local re-probe)

| Artifact | Canary | sha16 | mtime | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `XAUUSD/70d_liquidity/model.pt` (70D) | `(False, DIVERGES_AT:...)` | `a4b95406088ed618` | 2026-09-03 20:44 | EPSILON-DIVERGED (not trained) |
| `XAUUSD/50d_main/model.pt` (50D) | diverge | `3429...` | 08-31 12:02 | Trained reference, but weak (logit std 0.01) |
| `XAUUSD/70d_news/model.pt` (70D) | diverge | -- | -- | Trained reference |
| `XAUUSD/v1.0.0/model.pt` (50D) | `(True, BYTE_EQUAL)` | `0872ae0b85b3c74b` | 08-24 06:46 | CONTAMINATED -- retire |
| `EURUSD/v1.0.0/model.pt` (50D) | `(True, BYTE_EQUAL)` | -- | 08-21 | CONTAMINATED |

If any boot path serves v1.0.0, it serves noise. Disposition in §8 step 9.

### Serving-bundle vs config duality (tonight's PINC resolution)

- `configs/base.yaml` + settings DB: both declare `70d_liquidity/model.pt` (70D) -- consistent since v2 rehydration.
- Class constant `ACTIVE_SCHEMA_ID = "scalp_v1"` / `FEATURE_DIM = 50` (live contract protected) vs artifact `scalp_v3` / 70 -- duality is by design; `effective_feature_dim` + `_declared_contract_dim_for_path` bridge it.
- Tonight: boots at 18:48/18:49/19:48 all loaded 70D correctly (`expected_dim=70`, scaler (70,), CHAMPION VERIFIED 70D). No 50D serving at 70D path.
- Remaining risk: the fallback `v1.0.0` contaminated bundle still exists on disk; a cold-start or mis-wired path could serve it.

Scalers: `70d_liquidity/model.scaler.npz` mtime 09-03 06:11 -- any retrain must keep scaler/model coherent (`scale_like_champion` std floor + clip `[-5,5]`).

---
## 8. FIX PLAN (what we are implementing now)

### F1. Sequence-capable dataset builder (reuse `model_generation/sequence.py`, don't duplicate)
- Build `(L, 70)` windows stride 1, label = label of window's LAST bar, windows never straddle fold boundaries (purge 15 + embargo 15 respected).

### F2. Sequence trainer path in `WalkForwardTrainer` (additive, no god-module)
- New method `train_and_validate_sequence(df, feature_cols, seq_len)` reusing fold/purge/embargo helpers; per-fold scaler on train-only; AdamW+CE(class-weighted); early stopping on fold val loss; exports BOTH `model.pt` + `model.meta.json` enriched with `seq_len`, `trained_mode="sequence"`, dataset hash, git commit, git-dirty flag, training rows, per-fold OOS metrics.
- Backward compat: 2D mode still works; meta declares the serving mode so loader/runtime can assert.

### F3. Live inference sequence feed (minimal, hot-path safe)
- Engine keeps bounded deque of last L post-scaler 70D vectors per symbol; `_infer_probabilities` builds `(1, L, 70)` when `meta.trained_mode == "sequence"`; falls back to 2D when buffer < L (cold start) -- explicit, logged, never silent.

### F4. Calibration + decision policy
- Temperature scaling on fold-validation logits (train-only fit); report ECE + Brier + reliability in meta; policy thresholds in signal policy (optimized on TRAIN/VAL only).

### F5. Full-history retrain (the actual fix) -- AND the mandatory HTF window fix FIRST
- **Before any retrain:** register + fix MLPWR-06-02 (HTF 41/42). Smallest correct layer per contract; regression pin; re-run `mlpower_parity_corpus_probe.py` -> MUST be MATCH.
- Then `three_model.train_variant('70d_liquidity', smoke=False)` over the entire 100k-bar M1 history (34 folds × 10 epochs default) with news frame when available; smoke=False is the documented production path (BUG-141 handoff: "34-fold retrain over full history remains the documented follow-up").
- Label sparsity reality: 5,752 eval rows over 3.5 months. Mitigations (evidence-driven, in order): widen evaluable coverage by lowering `no_trade_stride_bars` 3->2 in the RETRAIN-ONLY labeler (documented deviation, barriers identical), keep class weights, keep focal fine-tune. **No horizon/TP/SL change** (labels comparable).
- Compute envelope: feature build is bottleneck (20k bars ≈ >1.5 h single-threaded in OOS probe). Mitigation: `compute_70d_frame_fast` (BUG-106 incremental, O(n·window)) + liquidity HTF cache. Target: full retrain in hours, not days.

### F6. Promotion gates (34-gate master task -> operationalized)
- Leakage suite green (parity/causality + BUG-106 anti-leak).
- Chronological OOS: last 20% never touched during model selection; stress window = largest-gap/high-vol segment reported separately.
- Calibration gate: ECE on validation ≤ 0.05 target, Brier reported.
- Multi-seed: ≥3 seeds, report mean±std of OOS macro-F1; champion on mean, variance recorded.
- Artifact integrity: sha256 of all three files in meta + registry (model_lifecycle).

### F7. Behavioral promotion gate (NEW -- closes the epsilon-canary bypass)
- Extend `CHECK-MDL-02` (or new `CHECK-MDL-03`) beyond byte-equality: logit-std floor (~0.15), max-prob floor (~0.35 after WAIT-normalized), WAIT-mass ceiling (~0.30), single-feature and group sensitivity floors -- fails the current artifact (mean_max 0.283) and retrains until behavioral proof.
- Gate the persist path: `LiveEngine._trigger_async_online_fine_tune` must NOT call `_save_model_weights_atomic` when `zero_improvement` / `accepted==False`; log `RETRAIN_SKIPPED` instead of `REPLACED`.

---
## 9. FUTURE ROADMAP (after the fix lands)

1. **Data expansion (highest leverage).** Acquire ≥1 year of XAUUSD M1 from MT5 history download in the engine (`adapter.get_historical_bars` batching) or broker export -> target ≥ 300k evaluable labeled rows.
2. **News-aware retrain.** Join `artifacts/news.db` (178 MB) via causal bridge -> 50-59 stops being dead. Ablation: news-on vs news-off on identical folds.
3. **Liquidity ablation** (master task §16): 50D vs 70D(news) vs 70D(news+liq) on identical folds -- prove +20 dims earn their keep.
4. **Sequence length study:** L ∈ {16, 32, 64, 128} on fixed folds; pick on OOS macro-F1 per latency budget (sequence path adds ~1-2 ms at L=64 on CPU, 267k-param net).
5. **Architecture upgrades ONLY if OOS plateaus:** deeper TCN stack / GRU hybrid -- reuse `model_generation/architectures.py` registry.
6. **Distillation (§27) ONLY if** a sequence teacher beats student on untouched OOS by a margin surviving 3 seeds; teacher offline, student ≤ current param budget.
7. **Online fine-tune policy:** keep BUG-228 honest-skip; raise buffer floor 300 -> ≥1,500 labeled rows before attempting fine-tune (current ~100-row buffers never clear +3% gate -- root cause of constant rejections).
8. **Retire contaminated fallbacks:** delete or quarantine `v1.0.0` bundles after new champion is CHAMPION-verified (CHECK-MDL-02 already flags them).

---
## 10. KNOWN LIMITATIONS / HONESTY LEDGER

- The OOS evaluation probe of the CURRENT artifact (scratch/t70d_r2) was **killed for time** (feature build ~1.5 h/20k rows single-threaded); superseded by F2/F5 which produce OOS natively. Current weakness is instead established by direct behavioral probe (calibration row) + provenance (smoke) + weight forensics -- all reproducible.
- No 1-year dataset exists on disk yet (§9.1 is the unblock).
- Trainer changes must keep `test_walk_forward_trainer.py` + critical suite green; every fix lands as `<AGENT>: <task>` commits with beforePush gates.
- Tonight's PINC session was read-only (no artifact/engine writes, no restart) per USER-OWNED engine rule and parallel-agent non-interference; the epsilon-divergence was observed, not caused, by this session.

---
## 11. FIX STATUS BOARD (live)

| Item | Status |
|---|---|
| F1 full-history dataset (t70d_f1_full_m1, 99,946 rows / 26,947 eval, stride2, parity 0) | **DONE** (commit ccb7765c) — superseded by authoritative `ds_70d_clean_m1_20260904` (same counts, contract-clean; see §11.1) |
| F2 sequence trainer (t70d_seq_v1 TCN+MHA L=32; t70d_seq_v2_tuned) | **DONE** (same windows 70/15/15; VAL 0.429/0.448, OOS 0.377/0.371) |
| F2b 2D baseline same windows (t70d_2d_baseline_same_windows) | **DONE** (VAL 0.447, OOS 0.391, balAcc 0.367) |
| F3 live sequence feed | DEFERRED (no OOS gain over 2D on this data; revisit after 1y retrain) |
| F4 calibration (temperature scaling) | **DONE** (T=0.73/0.68/0.58/0.81; ECE 1.7-4.1%) |
| F5 full-frame lossless candidate (t70d_v1_full, sha c8c0b5b) | **DONE** -- CANDIDATE, NOT promoted (Gate 6 OOS FAIL: 36.7% < majority 57%) |
| F6 gates run + report | **DONE** -- docs/forensics/t70d_master_forensic_report_2026-09-03.md (A-K) |
| F7 behavioral gate + persist-path guard | **DONE** — BUG-236 persist-decision API (986a89df) + behavioral health gate CHG-0057 (f4447536/c2f87ee5/03f3ba96) + tests test_bug235/test_behavioral_model_health_gate_chg0057 |
| HTF window fix (MLPWR-06-02) -- BUG row + code + parity pin | **DONE** — BUG-234 shared HTF_HISTORY_BARS contract (783d3da1) + test_bug234_htf_window_parity |
| Temporal contract unification (TRAIN=(B,L,70) == LIVE=(1,L,70)) | **DONE** — temporal_contract.py CANONICAL_SEQ_LEN=32 (649a26b7/4a48834a/71f099b7) + test_temporal_sequence_contract |
| 3/4-class semantic contract (WAIT policy-bridge, no 22% mass theft) | **DONE** — canonical 3-class head SSoT (a9155c79) + model_class_contract.py + reconcile commit 6c7bf637 |
| Smoke/production separation (production_eligible flag) | **DONE** — smoke=True => production_eligible=false in meta; promotion rejects (f2c06078/6c7bf637) |
| Artifact provenance (git commit/dirty, dataset_id, run_id, hashes) | **DONE** — trainer meta enrichment + persist_decision (986a89df, 582e6c70) |
| Data/gap integrity + label integrity + no-future-leakage + lineage tests | **DONE** — 44b230c7 (data audit), 139b2325+3bf58800+c979e423 (lineage/labels), test_gap_safe/test_label_integrity/test_no_future_leakage/test_paper_live_training_lineage (72ac451f) |
| Wave-1 gate tests consolidated + ruff/format/mypy clean | **DONE** — commit 72ac451f (11 files, 4479 test lines) |
| CI green on wave-1 HEAD | IN PROGRESS — mypy/ruff fixups landed (da4d0c12, cd6882bb, 591c6da5, 4b2c5e3c) |
| Wave-2: clean dataset regeneration + production retrain | BLOCKED ON CI-GREEN (all wave-1 gates now implemented) |
| Wave-3: LIVE DEMO validation + offline/live equivalence | PENDING (after wave-2 candidate) |
| Retire `v1.0.0` contaminated fallbacks | PENDING (after CHAMPION) |
| 1-year M1 acquisition + news-aware retrain + liquidity ablation | PENDING (roadmap §9, highest leverage) |

---
## 11.1 RECONCILED ADDENDUM — tail sparsity vs full-history dataset (2026-09-04)

> **No contradiction:** the 5,752 figure and the 99,946 / 26,947 figures describe different scopes of the same M1 history.

| Scope | Where quoted | Rows | Eval rows | Stride | Purge / Embargo | Dataset ID | sha256 (prefix) | Status |
|---|---|---|---|---|---|---|---|---|
| Full M1 span, historical labeler | §1 DATASET / §2 Data inventory / §8 F5 (tail-era) | 100,000 bars | **5,752** (BUY 1,599 / SELL 1,539 / NO_TRADE 2,614) | **3** | 15 / 15 | — (no artifact; smoke tail=3000) | — | **Historical — preserved** |
| Full M1 span, post-fix full-history | §11 F1 (t70d_f1_full_m1, commit ccb7765c) | **99,946** | **26,947** (eval: NO_TRADE 14,898 / BUY 6,261 / SELL 5,788; total NO_TRADE 87,897 incl. non-eval; sha `ca237ec9…`) | **2** | 15 / 15 | `t70d_f1_full_m1` | `ca237ec913ba9cb5` | Superseded (parity-proven pilot) |
| Full M1 span, authoritative post-fix | **This addendum** | **99,946** | **26,947** (BUY 6,261 / SELL 5,788 / NO_TRADE 14,898; see manifest `label_distribution`) | **2** | **15 / 15** | **`ds_70d_clean_m1_20260904`** | **`3ae687eaaa1f32a6`** (full `3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe`) | **Authoritative** |

**Why the eval count jumps 5,752 → 26,947:** §8 F5’s RETRAIN-ONLY mitigation widens evaluable coverage by `no_trade_stride_bars 3→2` (documented deviation; barriers TP 1.1×ATR / SL 1.0×ATR / horizon 15 / $0.35 friction identical, captured in `label_config_hash`). Same bars, same barriers, denser sampling of the NO_TRADE class — not a data contradiction.

**Authoritative manifest (verified on disk 2026-09-04):**

```
artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset_manifest.json
  dataset_id: ds_70d_clean_m1_20260904
  rows:       99,946  (train 69,962 / val 14,991 / test 14,993; chronological 70/15/15)
  eval_rows:  26,947  (label_distribution: NO_TRADE 14,898 / BUY 6,261 / SELL 5,788)
  temporal:   2026-05-01 18:09 UTC → 2026-08-17 19:24 UTC
  purge:      purge_gap_bars 15, embargo_bars 15, labeler_embargo_bars 3
  stride:     no_trade_stride_bars 2
  lineage:    CLEAN_HISTORICAL (production_eligible true, governance_override false)
  dataset_sha256: 3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe
  feature_schema: scalp_v3 (hash 235b8fccc96b7e0e), label_schema triple_barrier_3class_v1
  parquet sha256 == dataset_sha256 (verified: polars read 99,946 rows, 101 cols)
  verification.json: all_gates_pass true (verify_70d_artifact, gap_safe L=32, label_integrity, lineage)
```

**Verify locally:**

```bash
cat artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset_manifest.json | python -m json.tool | head -80
# or: .venv/Scripts/python.exe -c "import polars as pl; print(pl.read_parquet('artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet').height)"
```

**Reading guidance:** treat every pre-2026-09-04 mention of “5,752 eval rows” as the tail-era sparsity baseline that motivated §8 F5. Treat `ds_70d_clean_m1_20260904` (99,946 / 26,947) as the current authoritative full-history dataset for any retrain, audit, or F1 citation. `t70d_f1_full_m1` is the same row/eval count at the same stride/purge but is superseded by the contract-clean `ds_70d` artifact (BUG-234 HTF 4000, temporal L=32 gap-safe, 3-class, CLEAN_HISTORICAL).

---

## 12. VERIFIED ML LEARNING-CHAIN MAP (do NOT re-investigate; from audited pass)

- Outcomes: `execution/order_manager.py:6067` -> `experience/intelligence.py:608 record_trade_outcome` -> ledger `record_outcome` (idempotency key) -> research worker `_refresh_dataset` rebuilds from immutable ledger.
- Online path: `_rolling_feature_records` deque(4000) -> every 50 bars (≥300 rows) -> `_trigger_async_online_fine_tune` -> `walk_forward_trainer.fine_tune_online` (clone-safe, purge, class-balanced focal loss, quality gate, BUG-141 width-guarded atomic save + provenance re-register). **BUT:** save is unconditional even on no-improvement (to be gated by F7).
- Landed fixes in this chain: BUG-183 (purge/embargo SSOT wired), BUG-185 (retrain buffer width follows loaded bundle), BUG-228 (zero-improvement skip + structured logging), BUG-226 (PAPER rows excluded from accounting).

---
## 13. VERIFICATION PLAYBOOK (run before claiming anything)

```bash
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot

# 1. Champion canary (7 tests; runtime invariant is the last)
.venv/Scripts/python.exe -m pytest tests/unit/test_bug225_untrained_champion_canary.py -q

# 2. Per-artifact fresh-init status
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); \
from nexus_scalp.model_lifecycle.integrity import detect_untrained_fresh_init as d; \
print('70d_liquidity', d('artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt',70)); \
print('v1.0.0-50d', d('artifacts/models/scalp/XAUUSD/v1.0.0/model.pt',50)); \
print('50d_main', d('artifacts/models/scalp/XAUUSD/50d_main/model.pt',50))"

# 3. Tonight's independent probes (A-I, offline_vs_scaler, weight forensics)
.venv/Scripts/python.exe scratch/ns_pinc_probe_r2.py           # A-I (must show near-uniform on current artifact)
.venv/Scripts/python.exe scratch/ns_pinc_exp2_offlive.py       # offline_vs_scaler + WAIT mass + canary + settings DB

# 4. MLPWR asymmetry reproduction (before AND after the §5/§8 HTF fix)
.venv/Scripts/python.exe scratch/mlpower_parity_htf_window_diag.py
.venv/Scripts/python.exe scratch/mlpower_parity_corpus_probe.py   # must be MATCH after fix
.venv/Scripts/python.exe scratch/mlpower_parity_htf_live_train_callgrid.py

# 5. Live state (port DRIFTS -- probe, don't assume 8080/8099)
curl -s -m 5 http://localhost:8099/api/live/state | head -c 600
curl -s -m 5 http://localhost:8099/api/status | python -m json.tool | head -30
netstat -ano | grep LISTEN | grep -E ":80[0-9][0-9]"

# 6. Trainer honesty lines (BUG-228) + tonight's P0-3 re-persist
grep -E "no improvement|QUALITY GATE|ASYNC RETRAIN" logs/info/2026/09/2026-09-03.log | tail

# 7. Registry truth
.venv/Scripts/python.exe -c "import sqlite3; db=sqlite3.connect('file:artifacts/audit.db?mode=ro',uri=True); [print(r) for r in db.execute(\"SELECT registered_at,model_id,artifact_fingerprint,lifecycle_status FROM experience_model_registry WHERE artifact_fingerprint='a4b95406088ed618' ORDER BY registered_at\").fetchall()]"
```

Pre-push gate: `beforePush.sh` / `beforePush.ps1` via `.venv/Scripts/python.exe -m ...` (ruff -> format -> mypy -> CRITICAL suite -> deploy gate). NOTE while contaminated/epsilon artifacts exist, behavioral gates will flag; structural canary alone is insufficient.

---
## 14. GUARDRAILS FOR THE NEXT AGENT

- Engine runtime is USER-OWNED: no kill/restart without explicit user OK.
- Working tree has parallel agents' WIP (live_engine.py, adapters, Web/, release/, dependency_intelligence/, ...) -- NOT yours; never reset/stash/clean it. Pre-commit: `git diff --cached --name-only | grep -cvx <your file>` must be 0 (fresh shell each call; parallel agents can empty your index -- re-`git add` right before commit; absorbed commits verified via `git show HEAD:<path>`).
- Registry updates are ADDITIVE (`agents/bugs.md`, `agents/taskboard.md`).
- `scratch/` probes: never delete; commit evidence probes with their fix. Tonight's PINC probes: `scratch/ns_pinc_probe_r2.py`, `scratch/ns_pinc_probe_r2_out.json`, `scratch/ns_pinc_exp2_offlive.py` are the independent reproduction artifacts.
- Windows: patch tool CRLF-mangles; re-read before patch; repo venv python via `-m`.
- Do not trust "trained" claims from dimension checks or green unit tests alone -- the whole BUG-225 class passed every structural gate while serving noise. Canary + hash + provenance + behavioral probe or it did not happen.
- `artifacts/` is gitignored (except .gitkeep) -- model provenance lives in DB + meta, not in version control. Record dataset_id + commit in every new meta.
- The sequence fix touches hot-path inference -- measure latency (`_last_70d_assembly_timings`) and keep the 2D fallback for cold-start (buffer < L).

---
## 15. ADVISORIES RECONCILED

- **BUG-232 / BUG-231 / BUG-212** (mode persistence, pending-order recovery, paper boundary) are ADJACENT to ML lane -- do not touch their files unless the task explicitly requires it. This doc's findings are ML-lane only.
- **BUG-225 residual risk (b):** online fine-tune labels come from paper fills of a degenerate model -- self-fulfilling. CLEAN-dataset retrain (§8 F5) must NOT use the live rolling buffer.
- **P0-3 divergence** (this session): the 18:17 baseline already epsilon-diverged; 20:44 re-persisted it identically. The fix for P0-3 is F7 (persist-path guard), not a re-seed.
- **34-fold hypothesis:** do not assume 34 folds without evidence. F5's 34-fold production contract must be validated by per-fold class balance + OOS/calibration metrics (§8 F6).

---
## 16. DOC LOG

- 2026-09-03 ~21:20 +03:30 -- Nexus-Main: handoff v2. Prior orchestrator draft (198L) + independently re-verified second draft merged into single doc (380L). Re-verification: canary 7/7, detect_untrained_fresh_init on 3 artifacts, depth-grid + caller-grid probes re-executed, live /health + /api/status on 8099, ASYNC RETRAIN log line, artifact mtimes.
- 2026-09-03 ~22:00 +03:30 -- Principal Incident Investigator (PINC, adversarial lane): **CONSOLIDATED MLFix.md (this file) -- single continuable doc.** Merged MLFixing.md (381L) + MLFix.MD (129L) + tonight's independent forensic session (A-I probes, weight forensics, offline-vs-scaler, WAIT leakage, provenance chain, first-broken-state isolation). New evidence: `a4b95406088ed618` is epsilon-diverged fresh noise (20 tensors still byte-equal, logit std 0.06-0.10), not a trained champion; 500-random mean_max 0.283; WAIT 22% mass leak; HTF window asymmetry quantified; engine STOPPED so no new LIVE tick was captured (read-only session). Roadmap re-sequenced: HTF fix (P0) and behavioral gate (P0) are now explicit blockers before F5 retrain.
- Evidence base: BUG-225/228/217/197B/190/185/141/183 ledger rows; commits `3f5f9db7`, `52615bf7`, `c576dfac`, `6b893f04`, `203f1873`, `11ea316`, `454dbba5`; MLPWR probe outputs (scratch/, re-executed tonight); PINC probes `scratch/ns_pinc_probe_r2.*` + `scratch/ns_pinc_exp2_offlive.py`.
- 2026-09-04 03:05 +03:30 -- Nexus-Main (7-agent ROOT FIX sprint, wave 1 COMPLETE): 6 subagents executed Fixes #1-#10 in isolated worktrees, all merged to main. HTF parity (BUG-234, 783d3da1), temporal contract (temporal_contract.py L=32, gap=10min, 649a26b7), 3-class head SSoT (a9155c79 + model_class_contract.py), persist-decision API (BUG-236, 986a89df), behavioral health gate CHG-0057 (f4447536), lineage module (139b2325/3bf58800/c979e423), data/gap audit (44b230c7). Orchestrator reconciled cross-agent conflicts (4a48834a, 6c7bf637) and consolidated 9 new test files (72ac451f, 4479 test lines). CI mypy/ruff fixups: da4d0c12, cd6882bb, 591c6da5, 4b2c5e3c. Wave-2 (clean dataset regen + production retrain) unblocks when CI is green.
- 2026-09-04 06:55 +03:30 -- reconciler (subagent-sa-0): **RECONCILED tail vs full-history sparsity.** Verified on disk `artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset_manifest.json` (dataset_id `ds_70d_clean_m1_20260904`, rows 99,946 / eval 26,947, dataset_sha256 `3ae687eaaa1f32a6…3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe`, purge 15+15, no_trade_stride_bars 2, feature_schema_hash `235b8fccc96b7e0e`, lineage CLEAN_HISTORICAL; parquet 99,946 rows verified via polars). Patched MLFix.md §1/§2/§11 to preserve the historical 5,752-eval tail-era table (stride 3, tail=3000 smoke) while marking it as historical and pointing to the authoritative full-history dataset; added §11.1 reconciled addendum (tail vs full-history table, manifest excerpt, verify commands, reading guidance). No new forensic report (artifacts/model_generation gap pre-existed; implementation failure absent). Edits own only MLFix.md.
- 2026-09-03 ~23:20 +03:30 -- Nexus-Main (MASTER 70D program): **F1-F6 executed and pushed (commit `ccb7765c`).** F1 full-history 70D dataset built (t70d_f1_full_m1: 99,946 rows, stride2 -> 26,947 eval, fast-vs-slow parity 0 mismatches, sha 9ea84e40beb8ff17). F2/F2b sequence harness (TCNAttentionV1 L=32 + tuned) and 2D baseline trained on identical chronological 70/15/15 windows with train-only scaler + class weights + early stop: OOS balanced-accuracy 0.365-0.368 vs smoke 0.335, directional precision 0.240-0.245 (all still BELOW majority 57% => no edge on this data). F4 lossless full-frame polish candidate t70d_v1_full (sha c8c0b5b06d4c094d) registered as CANDIDATE; live champion NOT replaced (Gate 6 honestly FAIL). F6 gated report: docs/forensics/t70d_master_forensic_report_2026-09-03.md (A-K with confusion matrices, hashes, repro commands). Artifacts (gitignored): artifacts/model_generation/models/{t70d_seq_v1,t70d_seq_v2_tuned,t70d_2d_baseline_same_windows,t70d_full_retrain}/ + datasets/t70d_f1_full_m1/. Next P0 lanes unchanged: HTF window fix THEN 1-year data + 34-fold production retrain (MLFix section 9).







- 2026-09-05 00:00 +03:30 -- Nexus-Main (7-agent swarm): **Wave-2 EXECUTED — full-history 34x10 isolated retrain PASS via corrected producer.** Command: `scripts/dev/pilot_70d_3class.py --rows 99946 --folds 34 --epochs 10 --batch 256 --seed 42` (canonical WalkForwardTrainer path, isolated output, allow_champion_save=False; dataset manifest hash verified pre-launch). Result: EMISSION_GATE_PASS head=3 input=70 seq=32; candidate `artifacts/model_generation/models/pilot_70d_3class_20260904_232534/` model_sha `c9982ddde1755591112da679ba82bfca1eeff4def1700f9a7481ca18525dd9ce`, scaler `b3c65b65...`, manifest binds dataset_id `ds_70d_clean_m1_20260904` + dataset_sha `3ae687ea...` + schema_hash `235b8fccc96b7e0e` + git `9f799ea2` + lineage CLEAN_HISTORICAL + production_eligible=true (gated). Genuine training: classifier.weight std 0.1094 (epsilon baseline regime was logit-std 0.06-0.10 / 20-31 tensors fresh-equal); behavioral gate PASS (mean_max_prob 0.4122, logit_std 0.2704, pred_dist [186,188,138], deterministic, no NaN/Inf, group_sensitivity 0.0085). CHAMPION PROTECTED: c8c0b5b06d4c094d byte-identical before/after (P0-2 evidence champion preserved; PROMOTION NOT EXECUTED — governance decides per §14 chain: fold OOS tables, ECE/calibration, offline<->live (B,32,70) replay, multi-seed dispersion still owed by promotion lane). §11 status: Wave-2 producer execution DONE; promotion-gate evidence chain PENDING. Evidence: artifacts/model_generation/pilots/pilot_20260904_232534_report.json (+.log). Parallel-agent note: this lane touched only MLFix.md + taskboard row; foreign WIP (walk_forward_trainer.py, scratch probes Agent-2/4/7, cand_mlagent3_fullprobe) observed active and left untouched.
