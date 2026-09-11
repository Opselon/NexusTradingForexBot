# 70D Data, Labels & Signal Forensics — Agent 3 Wave 2 (host-with-artifacts verification pass)

**Date:** 2026-09-11 | **Repo HEAD at audit start:** 5f295b8d lineage (main) | **Agent:** Agent-3 (Data, Labels & Signal Forensics)
**Supersedes/extends:** `t70d_data_signal_forensics_2026-09-09.md` (that pass ran WITHOUT host artifacts; this pass re-verified every load-bearing claim against the REAL artifact on this host, plus new probes).
**Parallel safety:** read-only forensics. No trainer/inference/evaluation-governance files touched, no retrain, no promotion, no label/feature code change. Only this report + `scratch/ns_agent3_*` probes.

---

## 1. VERDICT SUMMARY (all rows now artifact-verified, not just evidence-chain-verified)

| Area | Verdict | Evidence class |
|---|---|---|
| Artifact integrity | CLEAN | dataset.parquet sha256 == manifest `3ae687eaaa1f32a6…` (verified by hash on this host) |
| Duplicates / ordering | CLEAN | 99,946 rows, 0 dup timestamps, strictly sorted |
| Gap census | CLEAN (matches 09-03 audit) | 78 gaps >60s: 16 weekend/holiday-class (>50h), 59 session breaks (3m–3h), 2 sub-minute, 1 mid; largest 53h (July 4) |
| Label causality | CLEAN | executable barrier-attack probe on real bars: mutating a bar's high changes ONLY labels inside its horizon (bar ≤ T), never earlier rows (see §3) |
| Train/eval split integrity | CLEAN with a documentation caveat | `is_eval_sample` is the labeler's stride/embargo mask (NOT a holdout split): eval rows are scattered uniformly through all 4 months (26.1–28.0% per month). The chronological 70/15/15 holdout is recomputed per-run by the walk-forward trainer; the dataset's `row_counts` (train 69,962/val 14,991/test 14,993) are the FACTORY's positional split, not what the WF path scores. No leakage either way (verified §2) |
| Class imbalance | NOT the expectancy driver | eval-share NT .553 / BUY .232 / SELL .215; weekly NT-share drifts 0.50→0.62 (mild non-stationarity, absorbed by CB weights + boost) |
| Data sufficiency | **INSUFFICIENT (confirmed on artifact)** | 26,947 eval rows / 3.5 months vs 267k-param policy; documented prerequisite ≥1y M1 → ≥300k eval rows (MLFix §9.1) |
| News 10D (feat 50..59) | **DEAD — CONFIRMED with root cause + NEW quantification** | all 10 dims ≡ 0.0 in the artifact; news.db holds **0 analyzed articles inside the bars window** (2026-05-01..08-17; 114 unanalyzed). Provisioning gap, not projection bug |
| Liquidity 10D | 9/10 informative; **feat_67 = dead by construction (CONFIRMED on real data)** | artifact: feat_67 has exactly 2 values: 3.0 (99,655 rows) + 1.8935 (291 warmup rows); Kruskal H=0.40 vs null-p95 0.03 |
| NEW: feat_9 `rapid_reversal_spike` | **DEAD in every offline build (new finding)** | constant 0.0 across all 99,946 rows; engine semantics require a live-tick displacement the bar-synthetic builder cannot produce (§4.2) |
| NEW: feat_8 low-variance artifact | borderline-degenerate in offline builds | distribution = synthetic-tick convention (displacement ≡ half-spread); live ticks have real displacement → offline/live distribution mismatch is inherent to bar-synthetic builds (§4.2) |
| Economic signal | **GENUINELY WEAK — now proven model-free** | model-free Kruskal-Wallis on eval rows: base block carries label information (feat_8 H=453.8, feat_12 H=84.6 …), liquidity block marginal (top H=40.7), news block none. Below-baseline OOS metrics are a signal problem, not a plumbing defect |
| NEW: session-boundary label horizon | bounded, minor (documented, NOT a defect fix target) | 325/26,947 eval rows (1.2%) have a >10min gap inside their 15-bar horizon; 154 directional; only 32 (0.27% of directional) have their barrier resolved POST-gap on information unavailable at decision time. Bound on expectancy distortion: <1 trade-equivalent per benchmark arm — cannot cause −0.55..−0.8R |

**No code defect was found in the labeler, dataset factory, split logic, or feature projection that meets the bar for a safe patch.** The four actionable items remain DATA/PROCESS prerequisites (§6), unchanged from the 09-09 report but now with executable evidence.

---

## 2. DATASET AUDIT (direct on-artifact verification)

- Integrity: `dataset.parquet` sha256 recomputed == `3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe` (manifest + verification.json agree). 99,946 rows, 0 duplicate timestamps, sorted ascending, `verify_70d_artifact` gates all true.
- Gap census (recomputed from dataset timestamps): 78 gaps >60s — buckets: >50h ×16 (weekends/holiday), 3m–3h ×59 (daily session breaks), 3–60s ×2, one 3–50h. Matches `t70d_data_quality_gap_audit_2026-09-03.md`. Session/holiday-explained; no instrument outage.
- Lineage: `label_origin=CLEAN_HISTORICAL`, `production_eligible=true`, schema hash `235b8fccc96b7e0e`, seed 42, builder `compute_70d_frame_fast` with `parity_self_check=true`. Manifest binds `git_commit 76254b9e` (dirty tree at build time — recorded provenance, acceptable for research artifacts).
- **Split-semantics caveat (documentation, not defect):** the eval mask story. `is_eval_sample` comes from the TripleBarrierLabeler (stride/embargo evaluated rows); `is_purged` is its complement. The trainer's `_filter_trainable_rows` consumes exactly these (26,947 rows) and then builds its own chronological purged folds (`_split_fold_with_embargo`, purge 15/embargo 15). The manifest `row_counts` 69,962/14,991/14,993 (train/val/test) are the DatasetFactory positional split carried in the frame the factory path writes — a DIFFERENT consumer path than the WF trainer used for the 3-dim benchmark. Both are leak-free; but anyone reading `row_counts` as "the OOS holdout" is misreading the artifact. The 3-dim benchmark's `oos_samples=7942` per fold-window comes from the trainer folds, consistent with 26,947/10-fold partitioning.
- Class distribution (artifact, eval rows): NT 14,898 (55.3%) / BUY 6,261 (23.2%) / SELL 5,788 (21.5%). Full frame: NT 87,897 / BUY 6,261 / SELL 5,788 — all BUY/SELL labels live in eval rows by construction (NT rows carry the stride skip). Weekly NT-share drift: 0.50 (w19) → 0.62 (w30) → 0.57 (w33): mild non-stationarity; handled by class-balanced weights, ruled out as primary cause.

## 3. LABEL AUDIT (executable causality attack on real bars — new this pass)

Probe: loaded the real 20k-bar M1 tail, ran the canonical `TripleBarrierLabeler(no_trade_stride_bars=2)`, then mutated bars and re-labeled:

- Mutate the LAST bar (×1.05 close): zero label changes (barrier not crossed; nothing else can move).
- Spike a mid-frame bar's high (`close+5×ATR` at T=n−50): exactly the rows with T inside their horizon re-labeled (row T−1 …); **no row with index > T changed** (no future-leak into the past), and rows after T changed only via the labeler's documented stride cursor (different rows get evaluated), which is bookkeeping, not leakage.
- Barrier geometry re-verified on real bars: spread-adjusted entries (BUY ask/SELL bid), TP 1.1×ATR/SL 1.0×ATR, step-dynamic spread on sell side, dual-touch neutralization, MAE 0.75 time-exit guard. `test_label_integrity.py` (8) + `test_gap_safe_sequences.py` green on this host.
- Horizon crossing session breaks (quantified, new): 325/26,947 eval rows (1.21%) have a >10-min gap inside their 15-bar horizon (96%+ are the 22:46–22:59 UTC pre-close rows). 154 are directional; 122 of those resolve PRE-gap (live-attainable), **32 resolve POST-gap** (label uses overnight drift unknown at decision time). Post-gap first bars drift +$7.94 mean (82.9% up) vs intra-session −$0.004 — a real upward overnight drift the labeler rides.
- **Why NOT a defect fix:** 32 rows = 0.27% of directional eval rows; even total adversarial mislabeling moves OOS expectancy by <1 trade-equivalent per benchmark arm (40 trades) — the −0.55..−0.8R verdicts are untouched by it. Any horizon-truncation change would alter label semantics ⇒ schema/label-config change ⇒ full retrain; not justified by a 0.27% mass. Documented for the ≥1y retrain prerequisite instead (§6).

## 4. FEATURE AUDIT (direct on-artifact)

### 4.1 News 10D — dead, root cause proven to be coverage, not just "news_frame=None"
- Artifact: feat_50..59 ≡ 0.0 for all 99,946 rows (verified numerically).
- Build report states the reason (`news_family.status=FEATURE_DISABLED`, "no real news coverage over bars window; neutral zeros per contract — synthetic news forbidden").
- NEW quantification from the live `artifacts/news.db` (199 MB): news_articles 24,682 total; **published inside the bars window 2026-05-01..08-17: 114; analyzed (news_analysis): 0**. The analyzer pipeline first saw the feed 2026-08-21; calendar_events (91 rows) start 2026-09-07. **A news-aware retrain on the current bars window is impossible regardless of wiring** — the DB cannot supply causal news for May–August. This converts the 09-09 "tooling exists, was never used" into a hard data prerequisite: news retrain requires (a) news ingestion running ≥ the trading window, (b) ≥1y M1 bars window overlapping that news coverage, (c) the novelty-slot parity fix routed to the train/serve lane (verified still true: `CurrentNewsContext` has no novelty field; train-side encodes {0..3}→clamped 3.0 while live always emits 0.0 — probe re-confirmed on the canonical producers).

### 4.2 Liquidity 10D — feat_67 saturated (CONFIRMED on real data); NEW: feat_9 constant-zero
- feat_67 `liquidity_confluence`: exactly 2 distinct values on the real artifact — 3.0 (99,655 rows) and 1.8934944 (291 rows = the May 1 18:09–22:59 warmup tail before the 4000-bar causal window fills). Label-conditional Kruskal H = 0.40 (approx p ≈ 0.82) with null p95 = 0.03: no usable information; the H exists only through the warmup block's slightly different NT share (0.876 vs 0.879 — negligible). The 09-09 mechanism claim (score formula saturates its 3.0 cap: diversity 1+ln(S) + tf/1440×0.5 + strength×0.25 ≥ 4.31 for ≥5-source zones) holds on the artifact. **Zero-variance-by-construction dimension confirmed.** Safe resolution remains: semantic redesign + schema-hash bump + retrain — owner decision, not patched here.
- The other 9 liquidity dims are information-bearing on real data (model-free Kruskal H, eval rows, permutation-null-calibrated): feat_61 H=40.7, feat_65 H=32.3, feat_66 H=17.8, feat_60 H=15.5, feat_68 H=14.1, feat_62/63/64 H≈5, feat_69 H=3.2 — all above their permutation nulls.
- **NEW — feat_9 `rapid_reversal_spike_val` ≡ 0.0 in every offline build:** the engine computes it from `live_tick_displacement = mid_price − last_close` where the dataset builder synthesizes the tick at `bid=close, ask=close+0.20` ⇒ displacement ≡ +0.10 < 0.6×ATR ⇒ spike condition never fires. This is a bar-synthetic-build artifact (live ticks carry real displacement), so the dimension is dead in TRAINING but live-served with real values — the same offline/live distribution class as feat_8 `norm_displacement` (artifact distribution = half-spread/ATR, mean 0.0546 — synthetic-tick convention, not real displacement). **Consequences:** (1) feat_9 is a wasted input in every model trained from bar-synthetic datasets; (2) feat_8's offline distribution does not match live's. Both are inherent to `schema_v2_incremental`'s synthetic-tick convention, NOT projection bugs. Fixing requires either tick-level history in the builder (new dataset prerequisite) or a documented builder change + schema-hash bump + retrain — outside "safe patch" scope, documented for owner decision (route: dataset lane + parity lane).

### 4.3 Base 50D
- Model-free per-dimension information (Kruskal H vs permutation null, eval rows): feat_8 H=453.8, feat_12 H=84.6, feat_23 H=59.2, feat_24 H=45.2, feat_43 H=32.2, feat_42 H=26.7, feat_16 H=26.7, feat_32 H=26.4, feat_40 H=23.7 … — the base block carries genuine label-separable structure (mostly through feat_8's magnitude asymmetry, session flags, HTF trend/confirmation flags).
- Binary/benign dims (feat_3 doji, feat_16–19 session flags, feat_29 breakout, feat_40/43 HTF, feat_48 OB-swept) are 2-value by design — variance is legitimately discrete, not "dead."
- BUG-234 regression pin (feat_41/42 > 0.95 nonzero) re-verified on the artifact: feat_41/42 not in the low-variance list (std ≥ 0.99/0.47 respectively) — no regression.

## 5. ECONOMIC SIGNAL DIAGNOSIS (final, evidence-ranked)

1. **Weak market signal at current data volume — PRIMARY (now proven model-free).** The label-separable structure that exists is concentrated in a handful of base dims and is not enough: every honestly-held-out model sits below the majority baseline (OOS bacc 0.365–0.392 vs 0.569; dir-prec 0.24 vs 0.33 random) and below naive baselines economically (all three arms lose after friction: −0.55..−0.8R/trade, PF 0.18–0.32). A 10-fold purged walk-forward over 26,947 rows leaves ~794 OOS rows per fold — high-variance expectancy estimates (Agent-4's seed spread corroborates: net_R −0.243..+0.125 across seeds).
2. **Dead/degenerate feature families — SECONDARY (amplifier, now precisely measured):** news 10D all-zero (0 analyzed in-window articles — hard data prerequisite), feat_67 saturated by construction, feat_9 constant-zero in offline builds, feat_8 distribution mismatched offline-vs-live. The +20D "keep" question cannot be answered positively on this dataset regardless of model (news carries zero bits; liquidity block's model-layer contribution ≈ 0.0035 accuracy per block_importance artifact).
3. **Class imbalance / label noise — RULED OUT** (§2/§3; moderate imbalance handled; causality attack clean; friction/MAE guards verified on real bars).
4. **Live-champion degradation — OUT OF LANE** (smoke-grade bundle owned by parity lane) — converts "no edge" into "guaranteed negative realized expectancy" but is not the missing-edge cause.

## 6. DATA BLOCKERS → EXACT RETRAIN PREREQUISITES (unchanged in substance, now evidence-complete)

1. **≥1 year XAUUSD M1** (→ ≥300k evaluable rows at stride 2; acquisition path per MLFix §9.1: `adapter.get_historical_bars` batching or broker export). Rebuild → rerun F2/F2b harness → promote only if OOS bacc > 0.42 and dir-prec > 0.35.
2. **News-aware retrain additionally requires:** news ingestion live ≥ the bars window (current DB has 0 analyzed articles before 2026-08-21 — a May–August news-join is impossible today), then `news_bridge.build_news_frame_from_db` → `--news-frame` build → news-on/off ablation on identical folds. Prerequisite owned by parity lane: CurrentNewsContext novelty slot mismatch (slot 56).
3. **feat_67 / feat_9 / feat_8 decisions** (liquidity-confluence redesign; tick-level builder or documented builder change) each require semantic redesign + schema-hash bump + full retrain — bundle into the same next-generation dataset build; not patchable today without invalidating every existing artifact.
4. **Optionally (cheap, high-value):** horizon truncation at session gaps (or exclusion of pre-close rows) at the next label-config change — eliminates the 32-row post-gap ambiguity; immaterial to current verdicts.

## 7. ACTIONS TAKEN

- Read-only forensics. No source change, no label change, no feature change, no retrain, no promotion, no registry write beyond this report + probes.
- Probes saved: `scratch/ns_agent3_boundary_mass.py`, `ns_agent3_boundary_mass_all.py`, `ns_agent3_boundary_evalmask.py`, `ns_agent3_train_boundary.py` (evidence for §2/§3 numbers; all runnable read-only against the artifact).
- Focused suites re-run green on this host: test_label_integrity (8), test_gap_safe_sequences + test_a2_data_lineage_bounded (8), test_no_future_leakage (3), liquidity family (57), news bridge (11) — 87 passed (see `pytest_a3_focused.txt`).
