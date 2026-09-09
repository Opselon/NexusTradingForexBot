# 70D Data, Labels & Signal Forensics — Agent 3 (Data/Labels/Features lane), read-only verification + one feature-defect finding

**Date:** 2026-09-09 | **Repo HEAD at audit start:** 46fd8ed0 | **Agent:** Agent-3 (Data, Labels & Signal Forensics)
**Scope:** dataset lineage/sufficiency, triple-barrier labels, News 10D dead features, Liquidity 10D information content, negative-expectancy diagnosis. Parallel-safety respected: no trainer/inference/benchmark-governance files touched.
**Environment note:** this box has NO `data/raw/*` and NO model/dataset artifacts (gitignored, production-host only). Every dataset-statistic claim is re-verified against the committed evidence chain (master report §D/§G, MLFix §11.1 addendum, manifests-as-documented) plus executable probes on the CODE; anything requiring the artifacts is marked HOST-VERIFY with the exact command.

---

## 1. VERDICT SUMMARY

| Area | Verdict | One-line cause |
|---|---|---|
| Dataset lineage (gaps/dupes/OHLC/ordering) | CLEAN (re-verified) | 100k M1, 0 dupes, 78 gaps = session/holiday closures; lineage `CLEAN_HISTORICAL`, sha-pinned manifests |
| Label causality (triple-barrier) | CLEAN (re-verified) | barriers on `i+1..i+horizon` only; tail-break; collision→NO_TRADE; no future info at i |
| Label noise / imbalance | NOT the expectancy driver | 55/23/21% eval split is moderate; CB-weights+boost+stride handle it |
| Data sufficiency for the trading claim | **INSUFFICIENT** | 26,947 eval rows over 3.5 months cannot support a 267k-param 3-class scalp policy; ≥1y M1 documented prerequisite |
| News 10D (feat 50..59) | **CONFIRMED DEAD — provisioning gap, not a projection bug** | every production build ran `news_frame=None` ⇒ documented FEATURE_DISABLED all-zero block; tooling to join real news exists and was never used |
| Liquidity 10D (feat 60..69) | 9/10 informative; **feat_67 confluence DEAD BY CONSTRUCTION at scale** | score formula saturates at its 3.0 cap on realistic pool scenes ⇒ zero-variance dimension |
| Negative expectancy | **GENUINELY WEAK SIGNAL on current data** + dead families + (separate lane) smoke-grade live champion | OOS acc 0.37–0.40 < majority 0.57; dir-prec 0.24 < 0.33 random |

No code defect was found in the labeler, sequence builder, gap handling, or dataset factory. The two actionable findings are (a) a proven feature-quality defect (feat_67) whose SAFE resolution is semantic redesign + schema-hash bump + retrain — documented here, not patched, and (b) the news-data provisioning gap with exact retrain prerequisites.

---

## 2. DATASET AUDIT (re-verified against committed evidence; host artifacts absent)

- Volume/range: 100,000 M1 bars, 2026-05-01 17:15 → 2026-08-17 19:24 UTC (t70d_data_quality_gap_audit_2026-09-03.md; master report §D).
- Integrity: 0 duplicate timestamps, strictly increasing, OHLC-valid 100%, epoch↔`time_utc` 0 mismatches, sane price range.
- Gap census: 78 gaps >60s — 59 daily close/open breaks (2h01m), 14 weekends (~2d1h), 1 holiday, 1 extended weekend, 2 intraday 3-min outliers. Largest 53h = July 4 weekend. All explained by session/holiday calendar; no instrument outage. Gap-safety is enforced at the SEQUENCE level (`SequenceBuilder` `valid=False` on gap-straddling windows, pinned by `test_gap_safe_sequences`) — labels need no gap logic because the horizon is bar-counted (see §3).
- Lineage governance: authoritative dataset `ds_70d_clean_m1_20260904` — 99,946 rows / 26,947 eval (NO_TRADE 14,898 / BUY 6,261 / SELL 5,788), stride 2, purge 15 + embargo 15, `label_origin=CLEAN_HISTORICAL`, `production_eligible=true`, dataset sha256 `3ae687eaaa1f32a6…` (MLFix §11.1, machine-verified on the production host 2026-09-04). The 5,752-row figure is the superseded stride-3 tail-era baseline — not a contradiction (MLFix §11.1).
- Split hygiene: chronological 70/15/15 with boundary purge tagged `_split="purged"` (BUG-244); eval∩purge = 0 pinned by `tests/unit/test_a2_data_lineage_bounded.py` (skips on this host — raw CSV absent — and runs fully on dev machines).

### Data sufficiency for the intended trading claim
**3.5 months is NOT sufficient.** Sizing: 26,947 eval rows for 267,492 params, 3-class decision policy; untouched-OOS evidence across 5 candidates (master report §G) shows OOS balanced-acc 0.365–0.392 and directional precision 0.240–0.245 — BELOW both the majority baseline (0.569) and 3-class random (0.33). No architecture on record overcomes the volume. The documented requirement (MLFix §9.1 + master report §J) is **≥1 year XAUUSD M1 → ≥300k evaluable labeled rows**, then rebuild → rerun the F2/F2b harness → promote only if OOS balanced_acc > 0.42 and directional prec > 0.35. Exact acquisition path already identified: engine `adapter.get_historical_bars` batching or broker export.

HOST-VERIFY (production host):
```
.venv/Scripts/python.exe -c "import polars as pl; print(pl.read_parquet('artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet').height)"
```

---

## 3. LABEL AUDIT (re-verified at code level — no defect, no redesign)

`src/nexus_scalp/labeling/triple_barrier.py` (v3.6), read line-by-line this session:

- **Temporal alignment / causality:** barriers evaluated strictly on `future_highs/lows/closes/spreads = [i+1 : i+1+horizon]` (lines 133–136); `horizon = min(15, n-1-i)`; `horizon <= 0 ⇒ break` (no fabricated tail labels). No reference to bars ≤ i anywhere in the verdict path. Matches the leakage table (master report §E) and `test_label_is_forward_only`.
- **Barrier semantics:** TP 1.1×ATR / SL 1.0×ATR from spread-adjusted entries (BUY ask, SELL bid); feasibility `tp_dist > max($0.35, entry_spread)` else stride-skip; step-dynamic future spread on the sell side; MAE 0.75 guard on time-exit promotions (lines 197–214); simultaneous TP/SL or dual-TP collisions → NO_TRADE (bias-free, lines 166–174).
- **Gaps & weekends inside the horizon:** the horizon is BAR-counted, so session breaks simply don't exist inside a 15-bar window (last Friday bars end 22:59; weekend never enters). The only intraday gaps (2×3-min) are ordinary OHLC discontinuities the barrier path handles by construction.
- **Stride/embargo:** non-trade labels advance `no_trade_stride_bars` (2 in the retrain contract), trades advance `exit_step + labeler_embargo(3)`; fold boundaries add purge 15 + embargo 15 — pinned by `test_label_integrity.py` (green here: 3.9s, all pass).
- **Class distribution / noise:** eval 55.3% NT / 23.2% BUY / 21.5% SELL. Moderate, and the trainer's Class-Balanced weights + `active_class_boost=3.0` + oversampling path address it (`walk_forward_trainer._build_class_weights`; 3-wide canonical contract enforced). **Imbalance is not the expectancy driver.** Noise reducers (collision neutralization, feasibility, MAE guard) are already in place; no evidence of systematic mislabeling found.

---

## 4. FEATURE AUDIT

### 4.1 News 10D — CONFIRMED DEAD; root cause is provisioning, not projection
- Every recorded production/smoke build passed `news_frame=None` (`three_model.train_variant` callers; `regen_70d_clean_dataset.py` default), so the builder emits the documented `FEATURE_DISABLED` all-zero block (`schema_v2.py` / `schema_v2_incremental.py`: `else: news10=[0.0]*10; news_status=FEATURE_DISABLED`). MLFix §71 says it plainly: "smoke build passed news_frame=None ⇒ news block all-zero ⇒ model never learned news semantics."
- The projection code itself is correct and parity-tested: canonical selection = fields 0..8 + `news_state` (`features/schema_contract.NEWS_10D_NAMES`, hashed `235b8fccc96b7e0e`); live path uses `vectorize_news_context → build_news_10` (BUG-190/197/217 repairs landed, tests green here).
- The tooling to make them real EXISTS and was never exercised end-to-end: `news_bridge.build_news_frame_from_db` (DB→causal frame), `news_benchmark_readiness` gate, `doctor model-dataset-build --with-news`, `regen_70d_clean_dataset.py --news-frame`. The production host's `artifacts/news.db` (178 MB) was never joined.
- **Latent trap (documented, not patched):** live `CurrentNewsContext` has NO `novelty` field (`src/nexus_scalp/news/models.py:338-361`), so `vectorize_news_context` always emits `novelty=0.0` at slot 56, while a future news-joined training frame carries the encoded analysis novelty {0..3} (`news_bridge.build_news_frame_from_db` + `_encode_novelty`). Probe (this host, matched-state): train block `[1,0.8,0.5,0.3,0.1,0,1.0,0.7,0.6,2.0]` vs live `[1,0.8,0.5,0.3,0.1,0,0.0,0.7,0.6,2.0]` — slot 56 mismatch. Harmless today (both sides all-zero) but becomes a train/serve distribution mismatch the moment a news-aware dataset is built. **Prerequisite for the news retrain — route to the parity lane.**
- Dead-code doc drift: `news_bridge.news_10d_vector()` has zero callers and its docstring describes the superseded "first-10" selection while its body correctly iterates canonical `NEWS_10D_NAMES`. Cosmetic; flagged for cleanup, no behavior impact.

### 4.2 Liquidity 10D — 9/10 informative; **feat_67 `liquidity_confluence` is a constant 3.0 at production scale (defect)**
Mechanism (code): `liquidity_engine.liquidity_confluence` (lines 1022–1069) scores the best pool-zone anywhere in the window: `score = (1 + ln(distinct_sources)) + (tf_sum/1440)*0.5 + sum(strength)*0.25`, clipped to [0,3], over ALL confirmed pools in a `LIQUIDITY_HISTORY_LIMIT = HTF_HISTORY_BARS = 4000`-bar causal window. Saturation arithmetic: ≥5 distinct sources in one zone ⇒ raw ≥ 4.31 ⇒ clipped 3.0. With 14 pool source types (swing H/L, EQH/EQL, PDH/PDL, PWH/PWL, session H/L, HTF swings/EQH/EQL) over thousands of bars, such a zone essentially always exists.

Probe (this host, executable, synthetic M1, canonical engine path `compute_liquidity_features`, 80 decision points, windows 1k–3k bars):

```
feature                         std    uniq     min     max
bsl_distance_atr             0.6516     80   0.000   3.000
ssl_distance_atr             0.6127     78   0.000   3.000
eqh_strength                 0.1666     80   0.330   0.994
eql_strength                 0.2319     80   0.000   0.998
htf_liquidity_score          2.7867     80  -3.000   3.000
internal_liquidity_distance  0.7077     79   0.049   3.000
external_liquidity_distance  0.5510      8   0.214   3.000
liquidity_confluence         0.0000      1   3.000   3.000   <-- DEAD (saturated)
liquidity_sweep_state        1.0619      4  -2.000   2.000
post_sweep_displacement      0.3761     15   0.000   1.817
```

Reproduced on a second seed and on small (200–400-bar) windows — always `uniq=1, value=3.0`; only tiny artificial pool scenes (1–8 pools) vary. Conclusion: at any realistic window depth the feature is a **zero-variance dimension by construction** — it cannot contribute information in training or live, and silently wastes scaler capacity.

**Not patched — why:** fixing it is a semantic redesign choice (rescore only zones near price, or cap source diversity contribution), which changes feature semantics ⇒ schema-hash bump ⇒ full retrain, and overlaps the train/serve parity lane. Per mission constraints ("safe fixes only", "do not tune to the test set") this is documented for an explicit owner decision instead.

HOST-VERIFY (production host, against the authoritative artifact):
```
.venv/Scripts/python.exe -c "import polars as pl; f=pl.read_parquet('artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet'); print(f['feat_67'].std(), f['feat_67'].n_unique())"
```
Expected if the mechanism holds on real bars: std ≈ 0, n_unique = 1.

### 4.3 Base 50D
Causal, parity-pinned, and the BUG-234 HTF repair (feat_41/42 no longer train-zero) is regression-pinned in `test_a2_data_lineage_bounded.py` (`>0.95 nonzero`) — green on dev machines, skip-gated here. No new findings.

---

## 5. ECONOMIC SIGNAL DIAGNOSIS (why expectancy is negative)

Ranked, with the evidence that rules each cause in/out:

1. **Genuinely weak market signal at current data volume — PRIMARY.** All honestly-held-out candidates sit below the two trivial baselines: OOS acc 0.371–0.405 vs majority 0.569; OOS directional precision 0.240–0.245 vs 0.33 random (master report §G). With 26,947 eval rows from 3.5 months, no in-family variant (2D/seq/tuned) escapes this. Negative expectancy is the expected outcome of trading a below-baseline policy.
2. **Dead feature families — SECONDARY (amplifier).** 10/70 news dims carry zero information in every build (provisioning gap) and 1/10 liquidity dims is saturated (feat_67, §4.2) ⇒ the model family that was supposed to earn the +20D keep has never been given real inputs, so the "liquidity ablation shows no gain" evidence is confounded.
3. **Class imbalance / labels — RULED OUT.** Moderate imbalance, handled; labels leak-free and noise-guarded (§3).
4. **Live-champion degradation — REAL but OUT OF THIS LANE.** The served `70d_liquidity` bundle is smoke-grade/epsilon-diverged with near-uniform outputs (MLFix §5/§7). That is a deployment-parity/artifact issue owned by the parity lane; it converts "no edge" into "guaranteed negative expectancy at the broker", but fixing it changes nothing about the missing edge itself.

Simple-baseline comparison (from the committed F2/F2b evidence, restated): majority NT ≈ 0.55–0.57; uniform-random directional ≈ 0.33; best model dir-prec 0.245. The pipeline is honest; the edge is absent.

---

## 6. ACTIONS TAKEN / NOT TAKEN

- Re-ran locally (green): `test_label_integrity.py`, `test_gap_safe_sequences.py`, `test_paper_live_training_lineage.py`, `test_news_bridge_phase13b.py`, `test_liquidity_engine_features.py`, `test_liquidity_engine_contract.py`, `test_shadow70_news_family.py`, `test_bug197_news_count_bounds.py`, `test_bug217_news_state_bounds.py`. Skips here (real bars/artifacts absent) are the expected environment-honest behavior: `test_no_future_leakage.py` (3 skips), `test_70d_bug106_incremental_phase19.py` (2 skips), `test_a2_data_lineage_bounded.py` (skip), `test_fidelity_data_to_decision.py::test_news_block_semantics_train_vs_live_documented` (RED here only because `artifacts/news.db` is empty on this box — the test itself is the incident signal; on the production host with the populated DB it asserts the 0/1 training flag).
- No production code, labels, or features were changed. No retrain. No promotion.
- No new tests added: no code defect was patched (the two findings are documented-for-owner by design), and this host cannot run data-dependent regression tests. The confluence HOST-VERIFY one-liner above is the immediate verification step before any fix task is opened.
