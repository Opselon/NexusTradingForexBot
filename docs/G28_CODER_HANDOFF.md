# G28 → NEXUS-CODER Handoff Contract

**From:** NEXUS-RESEARCHER (Agent 1)  
**To:** NEXUS-CODER (Agent 2)  
**Branch:** `main` (HEAD `0f6541a`)  
**Date:** 2026-08-24

This handoff contains evidence-backed, auditable improvement targets. **None of them weaken validation gates.** Every recommendation is justified by data in `artifacts/audit.db` (1165 evaluated candidates).

---

## RANKED IMPLEMENTATION TARGETS

### TARGET 1 — Semantic Clone Elimination (HIGHEST IMPACT)
**Evidence:**
- 345 candidates share identical behavior signature `(expectancy_r=-0.0607, total_trades=72, profit_factor=0.683)`.
- ~30% of total evaluation budget wasted on behavioral clones.

**Implement:** Before `evaluate_candidate()`, compute a behavioral-preview signature from the candidate's DSL + projected sample subset. If it matches a known cluster with ≥50 members and 0 OOS passes, skip evaluation and record `CLONE_SKIPPED` (counted, observable, reversible). Do NOT alter the DSL dedup hash logic — keep it as a structural gate; add a *semantic* pre-screen.

### TARGET 2 — Operator Accounting Persistence & Per-Action Tracking
**Evidence:**
- `operator_stats` is in-memory only (`factory_loop_state` empty); restart loses evidence.
- Crossover OOS survival 12.5% vs Mutation 2.1% — strong signal crossover should be weighted up, but currently unmeasured per-action.

**Implement:**
- Persist `operator_stats` to `factory_loop_state` (or a new `factory_operator_stats` table).
- Track per-action: `{generated, valid, wf_pass, oos_pass, improved, improvement_delta}` for each of: `add_filter`, `remove_filter`, `replace_indicator`, `change_threshold`, `change_timeframe`, `change_condition`, `simplify`, `crossover`.
- Do NOT use final OOS scores to set mutation probabilities (leakage boundary — see `SEARCH_LEARNING_BOUNDARIES.md`).

### TARGET 3 — Family-Slice Fold Geometry Floor
**Evidence:** 45 runs failed `"No out-of-sample samples available"`; 181 candidates had 0 WF folds (auto-fail).

**Implement:** Before evaluation, verify the family-restricted slice supports ≥3 WF folds AND a non-empty OOS remainder. If not, defer/skip and count `SLICE_TOO_SMALL` (visible, reversible).

### TARGET 4 — Crossover-Weighted Adaptive Probabilities (bounded)
**Evidence:** Crossover 12.5% OOS survival vs Random 7.5% vs Mutation 2.1%.

**Implement:** In `_adaptive_probabilities()`, shift operator weights toward crossover using **validation-tier (WF) survival only**, bounded by `[min, max]` clamps. Keep `exploration_rate` floor so search cannot collapse to one operator/family. Persist the change with `{old, new, reason, evidence_scope, confidence}`.

### TARGET 5 — Diversity Engine (structural + behavioral)
**Evidence:** Heavy M30 oversampling; 8 strategy families unevenly explored.

**Implement:** Extend `population_diversity()` to measure (a) DSL structural diversity, (b) family diversity, (c) behavioral-signature diversity. Enforce minimum spread across families and timeframes in `generate_population()`.

---

## EXPLICIT FORBIDDEN ACTIONS (do not implement)
- ❌ Lower `MIN_OOS_EXPECTANCY_R` (0.0R)
- ❌ Lower `MIN_EVIDENCE_SAMPLES` (20)
- ❌ Write OOS scores into mutation/search probabilities
- ❌ Increase `oos_frac` to inflate passes
- ❌ Treat `INCONCLUSIVE` as `VALIDATED`
- ❌ Permanently blacklist any family/template

## ACCEPTANCE EVIDENCE REQUIRED FROM CODER
1. Unit tests proving clone-skip fires on the known 345-cluster signature.
2. Test proving operator stats persist across `StrategyFactory` restart.
3. Test proving adaptive probabilities stay within `[min, max]` bounds.
4. Test proving OOS scores never enter probability computation path.
