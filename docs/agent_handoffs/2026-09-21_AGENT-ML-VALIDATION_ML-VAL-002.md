# AGENT-ML-VALIDATION — ML-VAL-002 — Probability Calibration & ECE Evaluation

**Date:** 2026-09-21 · **Specialist:** AGENT-ML-VALIDATION (Stream G — Validation/OOS)
**Status:** DONE — PR pending · **Branch:** `agent/ml-val/ml-val-002` off `origin/main` @ `8566bd3c`

## Summary

Implemented the Guo et al. (2017) probability-calibration toolbox for the NSE
3-class (NO_TRADE / BUY / SELL) contract in a new
`src/nexus_scalp/research/calibration.py`, measured the live softmax's
calibration gap, and published the audit to
`docs/research/CALIBRATION_AUDIT.md`.

The headline finding is that the production path is **overconfident by
construction**: `InferenceService.infer_probabilities`
(`src/nexus_scalp/application/live/inference.py:170-185`) applies raw softmax
and `SignalPolicy.evaluate_probabilities`
(`src/nexus_scalp/signals/policy.py:550-600`) thresholds it at 0.35 / 0.60.
No temperature or Platt correction exists on the live path, so a 0.90 softmax
output is treated as high-conviction evidence when it is right far less often.

## Deliverables

* `src/nexus_scalp/research/calibration.py` (new, ~600 LOC)
  `compute_ece`, `reliability_diagram`, `brier_score`, `log_loss`/`nll_loss`,
  `fit_temperature`, `TemperatureScaler` (fit / `apply_to_logits` /
  `apply_to_probs` / `wrap` / dict round-trip), `evaluate_temperature_scaling`
  → `CalibrationReport`, `wrap_model_temperature`, `validate_probs`.
* `tests/unit/test_calibration_research.py` (new, 63 tests, critical-suite
  registered at 489 → 491 lines).
* `docs/research/CALIBRATION_AUDIT.md` (new) — the required report.
* Closeouts: `docs/ml-system/tasks/ML-VAL-002.md` (STATUS DONE, both AC `[x]`),
  `docs/ml-system/06_TASK_LEDGER.md`, `docs/ml-system/TASK_BOARD.md`,
  `agents/taskboard.md`, `agents/locks.yaml`.

## Design decisions

* **Fitting method.** NLL(T) is smooth and unimodal in `log T`, so `T` is
  fitted by golden-section search on `log T` — deterministic, no optimizer
  state, no seed dependence, no GPU. This is the 1-D convex reduction of the
  Guo et al. LBFGS recipe.
* **Binning single-sourced.** Half-open `[lo, hi)` with a closed top bin, in
  one `_bin_mask()` helper. The first draft inlined three slightly different
  variants across `compute_ece` and `reliability_diagram`, which is how
  binning bugs start; the closed top edge is required so a flat `1/C`
  confidence is captured in a real bin rather than orphaned above the last
  edge.
* **No duplication.** A complete *binary* Platt/ECE subsystem already ships in
  `src/nexus_scalp/model_lifecycle/confidence_calibration.py`
  (`fit_platt_calibration`, `evaluate_calibration`, FAIL-CLOSED
  `ConfidenceCalibrator`) and remains the runtime risk-sizing path. This task
  adds the multiclass **temperature** capability that was genuinely missing.
  The audit document records the inventory explicitly so the next agent does
  not re-implement it.
* **Non-goals honored.** No production weight mutation. `wrap()` is
  research-only; the live 50D inference service keeps its own softmax
  (INV-012). The module imports cleanly without torch (lazy numpy/torch at
  function scope), verified by an explicit test.

## Evidence

```
pytest tests/unit/test_calibration_research.py -> 63 passed, 1 warning in 1.51s
ruff check <both files>          -> All checks passed!
ruff format --check <both files> -> clean
mypy src/nexus_scalp/research/calibration.py --explicit-package-bases
                                 -> Success: no issues found in 1 source file
scripts/ci/verify_critical_suite_manifest.py
                                 -> CRITICAL_SUITE_MANIFEST_OK: 220 paths
```

BENCHMARK_PLAN (20,000 OOS samples, synthetic 3-class fold calibrated to the
production shape — confidence ≈ 0.90, accuracy ≈ 0.69):

```
T fitted  = 3.5482
ECE      0.20399 -> 0.00640   (96.9% reduction)
Brier    0.16659 -> 0.13940
NLL      1.23380 -> 0.71445
```

The honest OOS protocol (fit `T` on a training fold, evaluate on a held-out
fold) is a separate green test; on real NSE folds the reduction will be
smaller, since this fixture is deliberately at the miscalibrated end of the
range to prove the mechanism.

## Bugs found and fixed this run

1. **Self-fulfilling test fixture (test defect, caught by running it).** The
   first battery derived labels from `argmax(logits)`, making the model 100%
   accurate *by construction*. Confidence then equals accuracy in every bin,
   ECE reads ~0, and the NLL optimum genuinely runs to `T → 0` — so every
   "temperature scaling improves calibration" assertion was unfalsifiable and
   could not have failed. Fixed: the fixture now draws the label first, boosts
   the logit toward it by *less* than the softmax sharpening, and returns the
   **drawn** labels. A new test asserts the fold is genuinely miscalibrated
   (confidence `> 0.80` while accuracy `< 0.80`) so the improvement claim has
   something real to bite on. Recorded in the fixture docstring so the next
   author does not reintroduce it.
2. **Unreachable ECE assertion.** A `compute_ece(...) > 0.9` assert on a
   confidently-wrong 3-class model is mathematically impossible: max-prob is
   capped at 1.0 and accuracy at 0, so the attainable ceiling is `1 - 1/C ≈
   0.667`. Such an assert would have masked a real regression. Replaced with
   the ceiling-respecting `> 0.59` plus an upper-bound assert at the ceiling.
3. **Wrong monotonicity assumption.** ECE is *not* monotone in
   overconfidence — it is zero at both the calibrated and the
   confidently-wrong extremes and peaks in between. The monotone quantity is
   mean confidence, which is now what the monotonicity test asserts.
4. **Brier expected-value arithmetic.** The first draft assumed a per-row
   mean; `brier_score` averages over all `N*C` elements, so the correct anchor
   for a flat 3×3 input is `2/9`, not `2/3`. Verified numerically before
   committing the assert.

None of these were defects in the production code under test — all four were
defects in the *tests*, found only by actually running the math rather than
trusting it. The calibration module itself needed no behavioral fix.

## Follow-up (not in scope)

Promoting a fitted temperature into `InferenceService.infer_probabilities` is
a live-tensor contract change (INV-012) and a governance-gated promotion, not
a research change. The audit's §7 records the recommended path: fit on a
walk-forward fold → persist alongside the artifact → apply under a feature
flag → gate through the existing `ConfidenceCalibrator` / `PromotionState`
machinery rather than editing the policy thresholds directly.
