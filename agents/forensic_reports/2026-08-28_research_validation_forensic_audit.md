# Research & Validation Engine — Deep Forensic Audit (2026-08-28)

**Author:** Hermes-Forensic-01 (Nexus Main orchestration)
**Scope:** Strategy Research & Validation Engine — full funnel, read-only DB evidence + code verification against `HEAD f9fa440`.
**Deliverable:** 14-section forensic report per the requested acceptance criteria.
**Conclusion (one line):** The validation pipeline code is correct and its gates are HONEST; the observed low pass-rate is driven by a structural substrate defect (the entire funnel re-evaluates slices of one ~109-trade losing book) and an outcome-lifecycle gap (273 never-filled decisions, 13 filled-but-no-outcome), NOT by over-strict thresholds or bugs in the WF/OOS math. Lowering gates would manufacture false science; the fix is evidence-accumulation + outcome completeness.

---

## 0. Evidence base (all read-only)

- `artifacts/audit.db` — `strategy_registry` (4082), `research_gates` (25,986), `research_runs` (4,330), `research_evidence` (18,228), `audit_experiences` (382), `audit_experience_outcomes` (109), `audit_broker_orders` (10,383), `audit_orders` (8,287).
- `artifacts/strategies.db` — `factory_candidates` (4,081), `factory_failures` (8,849), `factory_generations` (27).
- Code: `research/dataset.py`, `walkforward.py`, `oos.py`, `splitting.py`, `metrics.py`, `scoring.py`, `robustness.py`, `pipeline.py`, `discovery.py`, `worker.py`; `strategies/factory/orchestrator.py`, `benchmark.py`; `execution/order_manager.py`; `experience/intelligence.py`, `outcome_repair.py`.

---

## 1. Root cause analysis (ranked by confidence)

| # | Root cause | Confidence | Class |
|---|-----------|-----------|-------|
| R1 | **Single shared evidence substrate.** Every candidate (discovery + factory) is graded over subsets of the SAME ~109 executed-closed live trades. The R population is mean −0.067R, P(positive)=49.5%, and 3,378/4,080 registry rows have **negative OOS expectancy**. The funnel cannot find edge because the data contains essentially none. | HIGH (data-proven) | Evidence volume / quality |
| R2 | **Outcome lifecycle gap — never-filled decisions.** 273 of 382 ledger decisions have NO outcome row and `execution_id=''` (empty). These are limit orders that never filled or were never dispatched to the broker (verified: 223 have NO matching broker order at the exact decision price within 24h; 37 match a CANCELED broker pending; **13 match a FILLED broker deal but have no outcome row — a real capture miss**). `dataset.py:157-158` returns `MISSING_OUTCOME / 'not executed'` → the observed log flood. | HIGH (data + code proven) | Outcome data completeness |
| R3 | **No terminal no-fill outcome writer.** `OrderManager._record_experience_outcome` fires ONLY on position death (order_manager.py:4650, 5823). Cancelled / expired / replaced / un-dispatched pendings never get a terminal `ExperienceOutcome`, so the dataset can never distinguish "genuinely never executed" from "broker result lost". | HIGH (code proven) | Outcome lifecycle |
| R4 | **Sample sizes far below statistical requirement.** 4,288 backtests have median family size 40, but 1,729 candidates grade families of 72+ trades drawn from the same pool; OOS windows are 5–21 trades. On the observed R population, P(random subset mean > 0) = 0.37 (k=5) … 0.21 (k=30). With 4,082 candidates tested on overlapping data, false-discovery risk is extreme; multiple-testing correction is absent. | HIGH (computed) | Statistical power |
| R5 | **Benchmarks are "strategy-aware" but degenerate.** `_to_strategy_candidate` populates `discovery_evidence.sample_ids` via DSL replay over the ledger (`benchmark_dsl_matches_snapshot`). With an empty/loose filter set a candidate matches EVERY trade → 414 candidates produced an identical result signature (`20:20:20:…:-0.06R`); 1,163 distinct WF/OOS signatures exist but MANY are trivial reshuffles of the same pool. | HIGH (data proven) | Candidate duplication |
| R6 | **Diagnostic counters are cumulative gate-runs, not unique strategies.** 25,986 gate rows / 4,330 runs = exactly 6 gates/run (sequence STATIC→BACKTEST→WF→OOS→ROBUSTNESS→SCORING, order_index 0..5). BACKTEST/ROBUSTNESS/SCORING "PASSED: 4331" counts the gate EXECUTION, which is unconditional (no short-circuit) — it does NOT mean 4,331 strategies passed those gates as a quality bar. WF/OOS FAIL counts are real quality failures. | HIGH (DB proven) | Dashboard semantics |
| R7 | **Friction double-count (minor).** `compute_backtest` subtracts spread/slippage ticks from `realized_r` (metrics.py:125-132), but those R's are ALREADY broker-filled realized R that include spread/slippage. Consensus defaults are `spread_ticks=0`, so currently inert; but if assumptions are ever enabled it would over-penalize. | MEDIUM (code proven, latent) | Backtest model |
| R8 | **Walk-forward degradation instability (latent).** `degradation = (avg_val - avg_oos)/abs(avg_val)` (walkforward.py:160). When avg_val is tiny/near-zero (observed avg_val ≈ 0.003–0.035R), a small OOS swing produces |degradation|→∞, but the gate uses `avg_oos >= 0.0` not degradation, so it has not yet mis-rejected. OOS degradation has the same form (oos.py:101). Guards exist but the metric itself is fragile and should be replaced with a signed log-ratio or CI-based test. | MEDIUM (code proven, not yet harmful) | WF/OOS stability |

**Genuine strategy failure vs pipeline defect:** WF/OOS/SCORING math is correct and the gates are *honest*. The dominant reason nothing validates is R1+R4 (no edge + no power in the substrate), amplified by R2/R3 (incomplete outcomes). This is a **data/substrate problem, not a validation defect.**

---

## 2. Complete lifecycle trace for MISSING_OUTCOME trades

**Decision → Candidate → Experimental trade → Execution/Simulation → Close → Outcome generation → Persistence → Dataset inclusion**

For an executed-closed trade (e.g. `exp_3e8bcc1b…` → BUY_MARKET):
1. Signal policy writes DECISION row to `audit_experiences` (is_executed=False initially).
2. OrderManager dispatches; on fill, `intelligence.record_outcome` (intelligence.py:796) builds `ExperienceOutcome` and `ledger.record_outcome` persists it → `audit_experience_outcomes` (is_executed=1, is_closed=1, execution_id set).
3. `dataset.py:evaluate_sample` → `is_executed and is_closed` → ELIGIBLE → enters ResearchDataset.

For a never-filled decision (`exp_1d6244a1…` BUY_LIMIT @ 4394.295):
1. DECISION row written (is_executed=False, execution_id='').
2. Limit order MAY be dispatched; if it never fills (price never reaches, or it is never sent), NO broker fill occurs. **No `_record_experience_outcome` call** (gated on position death) → outcome never written.
3. On every `dataset.build()`/`audit()`, `evaluate_sample` returns `(False, MISSING_OUTCOME, 'not executed')` (dataset.py:157) → per-row `DATASET_REJECTED` info log. Nothing deduplicates → **permanent growing log flood** (observed 273 permanent rejects; the prompt's `DATASET_REJECTED … MISSING_OUTCOME … not executed` is exactly this path).

**Failure modes, classified:**
- TRADE_NEVER_DISPATCHED / NEVER_FILLED: 223 of 273 (no broker order at exact decision price ±24h; can't tell dispatch vs fill because no trailing telemetry on pending status).
- PENDING_CANCELLED/EXPIRED: 37 of 273 (exact-price CANCELED broker pending; delivery-side termination with no outcome writer).
- FILLED_BUT_NO_OUTCOME (real capture miss): 13 of 273 — exact-price FILLED broker deal, outcome row absent. `OutcomeRepairJob` cannot fix this (it only re-derives zero-R from broker deals, never creates missing rows for filled trades).
- NOT_EXECUTED vs DATA_LOSS indistinguishable: by design — `dataset.py` cannot tell "broker result lost" from "never ran" because no terminal outcome exists for either.

---

## 3. Dataset quality report

Classification of all 382 ledger records:

| Class | Count | % |
|-------|-------|---|
| MISSING_OUTCOME (not executed / never filled) | 273 | 71.5% |
| VALID_OUTCOME +R | 54 | 14.1% |
| VALID_OUTCOME −R | 53 | 13.9% |
| ZERO_R (reconstruction_source=NONE) | 2 | 0.5% |
| **Closed outcomes that DO enter research** | **109** | 28.5% |

R-population of the 109 outcomes: mean **−0.0669R**, std 0.4586, median −0.05R, P(R>0)=**49.5%**, range [−1.30, +1.10].

**Bias from removing MISSING_OUTCOME (per R2/R3):**
- 273/382 = 71% of decisions never produce a trade. This is NOT a random subsample — it is mechanically driven by pending-fill mechanics and dispatch gating.
- Dropping them silently would **understate the true non-execution rate** and **overstate realized expectancy** (survivorship/selection bias: only fills survive).
- 13 filled-but-no-outcome trades are pure data loss, not a strategy property; they should be RECOVERED, not discarded.
- Quantified impact: if the 273 were executed, the population expectancy is unknown but bounded below by the observed −0.067R on fills; the system currently reports edge only on the 28.5% that closed. The "missing" majority is decision-throughput, not strategy quality — but treating it as discarded evidence would distort expectancy upward.

---

## 4. Sample size & statistical power analysis

Observed: 109 closed outcomes total; R population mean −0.067R, std 0.46.

**Minimum evidence requirements (for the stated purposes):**
- Backtest rank/stability: ≥ 30 trades per strategy (scoring floor `MIN_EVIDENCE_SAMPLES=20`, `SMALL_SAMPLE_FLOOR=8`).
- Walk-forward (n_splits folds, guard `(n_splits+2)*3`): ≥ 15 samples/fold; the code requests `max(1, len//15 - 2)` folds, so a 109-trade pool yields 5 folds max but each fold window is ~7 trades → too thin for stable expectancy.
- OOS: ≥ 20–30 independent out-of-sample trades for a stable expectancy estimate; observed OOS windows = 5–21, with 145 rows at size 0.
- Robustness: uses the FULL family (≤109), so it is not sample-limited but is correlation-inflated.

**Null-model simulation** (20k trials, resampling the observed R population):

| k (trades) | 5 | 8 | 10 | 15 | 20 | 30 |
|---|---|---|---|---|---|---|
| P(random mean > 0) | 0.37 | 0.34 | 0.33 | 0.28 | 0.26 | 0.21 |

A "positive" backtest/WF on k≤15 trades is **more likely noise than signal** (≥63% false-positive under the null). Yet 4,082 candidates are ranked on exactly these windows.

**Multiple hypothesis testing:** testing 4,082 candidates (each on overlapping data from the same 109 trades) without Bonferroni/Šidák/Holm correction → expected false "passes" under the null ≈ 4,082 × 0.21 (OOS-PASS rate at k≈20) ≈ 857, versus observed 564 OOS-passes and only 3 VALIDATED. The observed pass-rate is *below* the false-discovery expectation — i.e. the data is if anything WORSE than random, confirming no real edge. **Correcting for multiple testing would not rescue any candidate; it would demand even more evidence.**

---

## 5. Walk-forward implementation audit

`walkforward.py` + `splitting.walk_forward_folds`:
- **Chronological ordering:** ✓ folds advance by `block = n//(n_splits+2)`; train always precedes validation precedes OOS (splitting.py:197-210). No future leak in ordering.
- **Train/val/OOS windows:** ✓ expanding-window; OOS block sits strictly after val block.
- **No overlap train↔val:** ✓ `train_block = ordered[:val_start_idx]`; val starts at `val_start_idx`.
- **Purge/embargo:** present (splitting.py:218-248) but **default `purge_seconds=embargo_seconds=0.0`** in pipeline/orchestrator calls → NO purge/embargo actually applied. For point-in-time R-multiple outcomes this is a latent leak risk (a sample's outcome can bleed across the boundary). Should be enabled with a calibrated horizon.
- **Lookahead through indicators:** N/A — R multiples are realized, not indicator-derived at eval time.
- **Feature normalization leakage:** `deterministic_normalization_fit` exists (splitting.py:276) but is **never called** by the WF engine; metrics use raw `realized_r`. No fit-on-train refit-on-test path is exercised → no normalization leak, but also no friction-normalization.
- **Parameter leakage:** none — WF does not tune parameters; it only backtests the (deterministic) candidate against folds.
- **Degradation formula:** `degradation = (avg_val - avg_oos)/abs(avg_val)` (walkforward.py:160). **Instability:** when avg_val ≈ 0 (observed avg_val 0.003–0.035R) denominator is tiny → unstable. Gate condition uses `avg_oos >= 0.0` (line 167), so not yet causing mis-rejection, but the reported `degradation` metric is unreliable and should be replaced (see R8).

**Verdict:** WF math is correct and leakage-free given current defaults; the only real gaps are (a) purge/embargo disabled by default, (b) fragile degradation metric.

---

## 6. OOS implementation audit

`oos.py` + `splitting.split_temporal`:
- **Genuinely unseen OOS:** ✓ `split = train+validation (in-sample) | oos`; OOS is the latest `oos_frac=0.2` tail by decision_timestamp; factory uses the SAME `dataset_id` (ds_*) for all gates, so no separate contaminated set. **No overlap** between in-sample and OOS by construction (split_temporal:105 `oos = ordered[val_end:]`).
- **No duplicate timestamps across splits:** ✓ strict index boundary.
- **No shared future info:** ✓ OOS is temporally later.
- **No parameter tuning on OOS:** ✓ OOS only calls `compute_backtest`; no optimization.
- **Execution assumptions BACKTEST vs WF vs OOS vs ROBUSTNESS:** ALL four call `compute_backtest` with the SAME `ExecutionAssumptions()` (defaults spread=0, slippage=0; metrics.py/backtest.py/robustness.py). **No model mismatch** — contrary to the prompt's suspicion, the engines are consistent. The only inconsistency is R8 (friction double-count if assumptions ever enabled).
- **Spread/slippage/commission/fill:** modeled identically (zero by default). Latency, session, missing bars, symbol precision: NOT modeled in research (assumed constant); this is a simplification, not a discrepancy between engines.
- **OOS gate:** `oos_exp >= 0.0` AND `oos_samples > 0` AND `degradation <= 1.0` (oos.py:106-118). Honest. 3,378 registry rows are REAL_NEGATIVE (OOS expectancy < 0); 138 ZERO_SAMPLES; 564 PASS.

**Verdict:** OOS is correctly isolated and consistent. The failures are genuine: strategies do not hold up out-of-sample on the substrate.

---

## 7. Data leakage audit

- **Within-split leakage:** none found in code paths (no refit, no shuffle, no future features at eval).
- **Purge/embargo:** DISABLED by default (purge_seconds=embargo_seconds=0 passed from pipeline/orchestrator). This is the only potential leakage vector; for point-in-time outcomes the horizon bleed is small but non-zero. **Recommend enabling** a calibrated embargo (e.g. 1 bar + max holding duration ≈ 4000s observed max).
- **Dataset overlap across candidates:** **MAJOR** — discovery families and factory `sample_ids` are subsets of the SAME 109 trades; the same outcome appears in hundreds of candidates' families. This is "leakage across hypotheses," not temporal leakage, but it inflates apparent independent evidence and defeats multiple-testing correction.
- **Normalization fit leakage:** `deterministic_normalization_fit` defined but unused → no leak, but also no train-scoped scaling applied.

---

## 8. Gate execution order diagram

Actual pipeline (verified from `research_gates` order_index 0..5, 4,329/4,330 runs with identical sequence):

```
DISCOVERY ──(worker discovers families / factory generates DSL)──┐
                                                                   ▼
RESEARCH_WORKER / FACTORY_ORCHESTRATOR.validate_candidate:
  1. STATIC_VALIDATION   (order_index 0)  ── fail → REJECTED (short-circuits)
  2. BACKTEST            (order_index 1)  ── ALWAYS RUNS (no short-circuit)
  3. WALK_FORWARD        (order_index 2)  ── ALWAYS RUNS
  4. OOS                 (order_index 3)  ── ALWAYS RUNS
  5. ROBUSTNESS          (order_index 4)  ── ALWAYS RUNS
  6. SCORING             (order_index 5)  ── ALWAYS RUNS → verdict
        │
        ├─ verdict VALIDATED  → lifecycle VALIDATED
        ├─ verdict REJECTED   → lifecycle REJECTED
        └─ verdict INCONCLUSIVE → lifecycle REJECTED (lifecycle-repair 2026-08-23)
```

**Critical finding:** gates 2-6 do NOT short-circuit. A failing BACKTEST/WF/OOS still runs the remaining gates and emits PASSED rows for them. This is why `BACKTEST PASSED: 4331`, `ROBUSTNESS PASSED: 4331`, `SCORING PASSED: 4331` while `WALK_FORWARD FAILED: 4267` / `OOS FAILED: 3767`. Those "PASSED" counters measure **gate execution counts, not strategy quality**. The UI/dashboard must define them as "gates executed" not "strategies passed".

---

## 9. Diagnostic counter explanation

From `research_gates` (25,986 rows) and `research_runs` (4,330):

| Counter (observed) | True meaning |
|---|---|
| BACKTEST PASSED: 4331 | 4,331 BACKTEST gate-rows with status PASSED = every run's backtest gate completed (unconditional). NOT 4,331 strategies that passed a backtest quality bar. |
| ROBUSTNESS PASSED: 4331 | same — robustness gate executed for every run. |
| SCORING PASSED: 4331 | same — scoring gate executed for every run. |
| STATIC_VALIDATION PASSED: 4331 | 4,331 runs cleared static gates (1 run had a 12-gate duplicate; 1 strategy had 7 runs). |
| WALK_FORWARD FAILED: 4267 | REAL: 4,267 runs whose WF gate returned FAILED (RESEARCH class). |
| WALK_FORWARD PASSED: 64 | real passes (includes the 3 VALIDATED + near-passes). |
| OOS FAILED: 3767 | REAL: 3,767 runs with OOS FAILED. |
| OOS PASSED: 564 | real OOS passes. |

**Therefore the displayed "PASSED" counts are cumulative gate-invocations, not independent strategy certifications.** The dashboard MUST relabel or divide by runs/unique-strategies. 4,079 distinct strategies, 4,330 runs (some re-validated up to 7×), 8,849 factory_failures.

---

## 10. Strategy uniqueness analysis

- **Registry:** 4,082 rows from 4,079 strate**ged** ids; 27 generations, 7 COMPLETED / 20 FAILED.
- **Factory candidates:** 4,081; sources RANDOM_EXPLORATION 2,933 / MUTATION 860 / TEMPLATE 121 / DIVERSITY 60 / LLM 54 / REGIME_SPECIALIST 36 / CROSSOVER 16. LLM provider usage: **1 request, 1 failure** → LLM path is effectively dead.
- **Result-signature duplication:** 1,163 distinct (WF+OOS) result signatures across 4,082 registry rows. The top signature (`20:20:20:…:-0.06R`) is shared by **414 candidates**; 330 share another; 318 a third. These are NOT distinct strategies — they are DSL permutations selecting the same ledger subset.
- **Context-fingerprint fragmentation:** the 109 outcomes collapse into only **28 context fingerprints** (XAUUSD|M1|session|regime|vol|trend); only **1** fingerprint has ≥20 outcomes (LONDON/RANGING_MEAN_REVERSION/BULLISH = 21). So the entire "strategy space" being searched is ~28 coarse buckets over one symbol/timeframe; 4,082 candidates is ~145× oversampling of a 28-bucket space.
- **Structural dedup:** `dsl_hash` canonicalization exists, but `behavioral_preview_signature` (benchmark.py:313) was added specifically because DSL-level dedup could NOT see that different filters select the same samples (the "345-cluster pathology", now ~414). Recommend: fingerprint on `(sorted sample_id subset)` and reject candidates whose subset intersects a known-zero-edge cluster (CLONE_SKIPPED exists but only triggers when `clone_prescreen_enabled` and a pathological cluster is known).

---

## 11. Exact files / components responsible

| Component | File | Role in finding |
|---|---|---|
| Outcome eligibility | `src/nexus_scalp/research/dataset.py:151-217, 318-344` | emits MISSING_OUTCOME per un-executed decision; per-run log flood (L329-337) |
| No-fill outcome writer (MISSING) | `src/nexus_scalp/execution/order_manager.py:4650, 5823` (`_record_experience_outcome`) | only fires on position death → no terminal outcome for cancels/expiries/un-dispatched |
| Shared substrate | `src/nexus_scalp/research/pipeline.py:198` (`_select_family`), `strategies/factory/orchestrator.py:1282` (`_to_strategy_candidate`) | candidates grade subsets of same 109 trades |
| DSL→sample replay | `src/nexus_scalp/strategies/factory/benchmark.py:97-139` (`dsl_matches_snapshot`, `benchmark_subset_for_candidate`) | loose/empty filters match everything → duplicate signatures |
| Diagnostic counters | `src/nexus_scalp/research/observability.py:792` + `web/server.py:5860` (`/api/research/diagnostics`), `get_research_summary` | cumulative gate-runs, not unique strategies |
| WF/OOS math | `src/nexus_scalp/research/walkforward.py:160`, `oos.py:101` | degradation instability (latent) |
| Purge/embargo default | `src/nexus_scalp/research/pipeline.py:198` (validate_candidate params), `orchestrator.evaluate_candidate` | purge/embargo never passed → disabled |
| Friction | `src/nexus_scalp/research/metrics.py:125-132` | double-counts spread/slippage on already-filled R (latent, default 0) |
| Multiple-testing | (absent) | no correction anywhere in scoring/pipeline |
| Outcome repair limitation | `src/nexus_scalp/experience/outcome_repair.py` | cannot create missing outcomes for filled-but-no-outcome trades |

---

## 12. Recommended fixes (ranked by priority)

**P0 — Evidence integrity**
1. Add a **terminal no-fill outcome writer** in OrderManager: on pending cancel/expire/replace/un-dispatched, write `ExperienceOutcome(is_executed=False, is_closed=True, exit_reason=EXPIRED_UNFILLED/CANCELLED_UNFILLED/NOT_DISPATCHED, realized_r=0, realized_pnl=0, marker payload)`. This separates "never executed" from "result lost" and stops the MISSING_OUTCOME flood without weakening gates.
2. **Recover the 13 filled-but-no-outcome trades** via a bounded job that joins `audit_experience_outcomes` ↔ `audit_broker_deals` on ticket/price/time and back-fills the missing outcome rows (do NOT invent R — derive from broker realized PnL).
3. **Dashboard counter relabel**: "PASSED" counters → "gates executed (cumulative)". Add unique-strategy and run counts. Never present gate-execution counts as pass rates.

**P1 — Statistical honesty**
4. **Enable purge/embargo** in WF/OOS calls (calibrate to ~1 bar + observed max holding 4000s).
5. **Multiple-hypothesis correction**: report a Benjamini-Hochberg FDR-adjusted OOS p-value; require adjusted p < α for VALIDATED. Or require per-candidate evidence floors scaled by total candidates tested.
6. **Minimum independent evidence floor**: raise `MIN_EVIDENCE_SAMPLES` and require ≥ 30 OOS trades for VALIDATED (currently min_trades=20, OOS can be 5).

**P2 — Dedup / multiplicity**
7. **Subset-fingerprint dedup**: reject/clone-skip candidates whose `sorted(sample_ids)` subset matches a known zero-edge cluster (extend `behavioral_preview_signature` to the validation gate, not only the pre-screen).
8. **Cap generations until substrate grows**: with 109 outcomes and 28 fingerprints, 4,082 candidates is wasteful; gate generation count on available evidence (EVIDENCE_BUILDING already exists — use it as the default, not REJECTED).

**P3 — Metric hardening (no threshold relaxation)**
9. Replace `degradation = (a-b)/|a|` with a **signed log-ratio** or CI-overlap test; keep gate logic identical.
10. Make `compute_backtest` friction-aware ONLY when `realized_r` is a gross (pre-cost) estimate; add a flag so filled R's are not re-penalized (remove double-count, R7).
11. Wire `deterministic_normalization_fit` into WF if/when any normalized feature is used.

**Explicitly NOT recommended:** lowering WF/OOS thresholds, marking failures passed, disabling gates, filling missing outcomes with fake data, removing failing tests. These would manufacture false science.

---

## 13. Tests that prove each fix

| Fix | Test (extend existing file) |
|---|---|
| P0-1 terminal no-fill writer | `tests/unit/test_research_task4_dataset.py`: simulate pending cancel → assert `ExperienceOutcome` row with `is_executed=False, exit_reason=CANCELLED_UNFILLED`; assert `dataset.audit()` classifies as NOT_EXECUTED quietly (no per-row log). |
| P0-2 recover 13 filled | `tests/unit/test_research_task4_dataset.py`: fixture with a broker deal + missing outcome → recovery job creates row with correct R from PnL; count of MISSING_OUTCOME drops by 13. |
| P0-3 counter relabel | `tests/integration/test_research_api.py`: assert `/api/research/diagnostics` returns `gates_executed` + `unique_strategies` + `runs`, and that BACKTEST "passed" count == run count, not strategy count. |
| P1-4 purge/embargo | `tests/unit/test_research_phase09b.py`: assert `walk_forward_folds` with `embargo_seconds>0` drops boundary-adjacent samples; assert no sample's outcome crosses the train/val boundary. |
| P1-5 FDR | `tests/unit/test_strategy_factory_phase22.py`: fixture of N candidates on same pool → assert VALIDATED requires BH-adjusted p < α. |
| P1-6 OOS floor | `tests/unit/test_research_task4_validation.py`: candidate with 5 OOS trades and positive expectancy → INCONCLUSIVE (not VALIDATED) when floor=30. |
| P2-7 subset dedup | `tests/unit/test_strategy_factory_g28_clone_and_stats.py`: two DSLs selecting identical sample subset → second is CLONE_SKIPPED at validation gate. |
| P3-9 degradation | `tests/unit/test_research_phase09b.py`: avg_val≈0 case → assert degradation metric finite & signed; gate still uses avg_oos≥0. |
| P3-10 friction | `tests/unit/test_performance_metric_truth.py`: filled R with assumptions.spread>0 → no double subtraction (add `already_net` flag). |

All fixes covered by regression tests (acceptance criterion: every fix has an automated test).

---

## 14. Before / after metrics

| Metric | Before (current) | After (P0+P1 fixes) |
|---|---|---|
| Registry total | 4,082 | unchanged (re-run on larger pool) |
| VALIDATED | 3 | unknown — expected to REMAIN low until evidence accumulates; NOT forced up |
| MISSING_OUTCOME log flood | 273 permanent rejects/run | 0 (terminal outcomes written; NOT_EXECUTED quiet) |
| Filled-but-no-outcome | 13 lost trades | 0 (recovered) |
| Distinct result signatures | 1,163 / 4,082 | fewer (subset dedup collapses clones) |
| Dashboard "BACKTEST PASSED" | 4,331 (misleading) | relabeled "gates executed: 4,331 / unique strategies: 4,079" |
| WF purge/embargo | disabled | enabled |
| Multiple-testing | none | BH-FDR adjusted |
| Genuine strategy-fail vs pipeline-defect | conflated | separated: pipeline proven honest; low pass = no edge in substrate |

**Final decision:** REWORK_REQUIRED (root cause is substrate + outcome-completeness, not gate strictness). The validation engine is trustworthy; its INPUTS are not. Increase evidence volume, complete the outcome lifecycle, correct dashboard semantics, and add multiplicity controls. Do NOT relax thresholds.

---

*Generated by Hermes-Forensic-01. All numbers derived from read-only queries against `artifacts/audit.db` (109,110,382,10,383 rows) and `artifacts/strategies.db` (4,081,8,849,27 rows) at HEAD f9fa440, cross-checked against source code. No writes performed to either database.*
