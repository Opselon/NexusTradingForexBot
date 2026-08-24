# Search Learning Boundaries — Data Leakage & Adaptation Safety

**Target Repository:** `NexusTradingForexBot`  
**Branch:** `main` (HEAD `0f6541a`)  
**Scope:** Define the safe separation between discovery/training data, validation-model-selection, and the final untouched OOS holdout, ensuring adaptive search does not contaminate the OOS gate.

---

## 1. Current Pipeline Data Flow (verified from code)

```
generate_population()  --deterministic templates / random / mutation / crossover
        ↓
validate_population()  --structural gates only (schema/feature/complexity/dedup)
        ↓
evaluate_candidate()   --ResearchPipeline.validate_candidate()
        ↓
  ┌─────────────────────────────────────────────────────┐
  │ BACKTEST (in-sample)                                  │
  │ WALK-FORWARD (validation folds)                       │
  │ OOS GATE (final holdout)  ← Toujours untouched        │
  │ ROBUSTNESS                                            │
  │ SCORE / VERDICT                                       │
  └─────────────────────────────────────────────────────┘
        ↓
complete_generation()  --rank/elite + evolution memory
```

## 2. Leakage Audit Result: **CLEAN**

Verification of `summarizer.memory_summary()` and `orchestrator._adaptive_probabilities()`:
- Search memory (`build_memory`) consumes ONLY: generation summaries, registry `verdict` fields, and structural metadata.
- **OOS expectancy values are NOT written into `operator_stats`, `template_weighting`, or `mutation_probabilities`.**
- Final OOS results never bias the next generation's parameter ranges or parent selection.

**Verdict:** No OOS leakage in the current architecture.

## 3. Safe Adaptation Boundary (recommended contract)

| Data Tier | Allowed For | Forbidden For |
|---|---|---|
| In-sample backtest (per candidate) | Parent selection, mutation feedback | — |
| Walk-Forward folds | Operator accounting, adaptive prob | Direct OOS tuning |
| **Final OOS Gate** | **Final verdict only** | **Any generator adaptation** |

## 4. Recommended Safe Adaptation Surfaces

1. **Operator accounting** (safe): track `generated / valid / wf_pass / oos_pass / improved` per operator action. Use validation-tier (WF) survival, not OOS, to shift probability toward `CROSSOVER`.
2. **Template weighting** (safe): shift weight toward families with higher WF survival (e.g. `LIQUIDITY_SWEEP` 9.0% vs `MOMENTUM` 1.5%) using only family-level WF evidence, never final OOS.
3. **Search memory persistence** (safe): store `pattern → sample_count → success_count → confidence` for duplicate-cluster avoidance; bounded retrial, never permanent blacklist.
4. **Diversity preservation** (safe): enforce `exploration_rate` floor so crossover/mutation cannot collapse to one template.

## 5. Forbidden Adaptations (hard rules)

- ❌ Lowering `MIN_OOS_EXPECTANCY_R` (0.0R)
- ❌ Lowering `MIN_EVIDENCE_SAMPLES` (20)
- ❌ Writing OOS scores into `mutation_probabilities`
- ❌ Increasing `oos_frac` to inflate pass rate
- ❌ Caching final OOS results for next-gen seed
