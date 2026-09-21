# NSE Calibration Audit — ScalpNet Softmax Probability Calibration (ML-VAL-002)

**Scope:** `src/nexus_scalp/research/calibration.py` (new), 3-class (NO_TRADE /
BUY / SELL) contract. Reference: Guo et al. (2017), *On Calibration of Modern
Neural Networks*.

**Date:** 2026-09-21  ·  **Specialist:** AGENT-ML-VALIDATION  ·  **Status:** DONE

---

## 1. Headline finding — the live softmax is overconfident by construction

The production inference path applies raw softmax to logits and hands the
result to `SignalPolicy`, which thresholds it:

* `src/nexus_scalp/application/live/inference.py:170-185` —
  `InferenceService.infer_probabilities` returns raw softmax
  `[P_NO_TRADE, P_BUY, P_SELL]`. Classified `PRODUCTION`, confidence 100%.
* `src/nexus_scalp/signals/policy.py:550-600` — `SignalPolicy.evaluate_probabilities`
  gates on confidence `>= 0.35` and `>= 0.60` for order triggers.

**No temperature or Platt correction is applied anywhere on the live path.**
Softmax confidence is not a calibrated probability in general, and for a
network trained to convergence with a hard argmax objective it is
systematically overconfident — it *says* 0.90 when it is right ~65% of the
time. The policy then treats that 0.90 as evidence for the `>= 0.60`
high-conviction branch, which mis-sizes risk on exactly the trades where the
model is loudest.

**This is a latent P1/P2 accuracy-to-confidence gap, not a crash.** Nothing
breaks; money is just mis-allocated against a wrong confidence prior.

## 2. What already existed (do NOT duplicate)

A complete *binary* win/loss calibration subsystem already ships and is the
runtime risk-sizing path — this audit explicitly does not replace it:

* `src/nexus_scalp/model_lifecycle/confidence_calibration.py:140` —
  `fit_platt_calibration()`: Platt sigmoid `p = sigmoid(a*conf + b)`, numpy GD,
  deterministic, L2-regularized.
* `.../confidence_calibration.py:194` — `evaluate_calibration()`: binary ECE +
  Brier + per-bin `win_rate` on (confidence, win/loss) pairs.
* `.../confidence_calibration.py:244` — `ConfidenceCalibrator`: FAIL-CLOSED
  runtime provider (`NOT_CALIBRATED`/`DEGRADED` → flat 1.0x sizing).
* `src/nexus_scalp/model_lifecycle/calibration_collector.py:156` —
  `collect_calibration_evidence()` over `audit_experiences`.
* `src/nexus_scalp/governance/evidence.py:182` — `brier_score()` (binary).
* `src/nexus_scalp/model_lab/evaluation.py:70,85` — `ece()` / `brier()` /
  `log_loss()` for model-lab split evaluation.

None of these exposes a multiclass **temperature scaling** capability, which
is the gap ML-VAL-002 fills.

## 3. What ML-VAL-002 adds

`src/nexus_scalp/research/calibration.py` — research/validation-grade
multiclass calibration:

| Symbol | Purpose |
| --- | --- |
| `compute_ece(probs, labels, n_bins=10)` | Multiclass ECE (Guo eq. 3) |
| `reliability_diagram(...)` | Bin table for the reliability plot |
| `brier_score(...)` | Multiclass Brier |
| `log_loss` / `nll_loss(...)` | NLL of the true class |
| `fit_temperature(logits, labels)` | Fit scalar `T` minimizing NLL |
| `TemperatureScaler` | Fit → apply; `apply_to_logits` / `apply_to_probs` / `wrap` |
| `evaluate_temperature_scaling(...)` | Before/after `CalibrationReport` |
| `wrap_model_temperature(model, T)` | Torch model → temperature-scaled softmax |

**Fit method.** NLL(T) is smooth and unimodal in `log T`, so `T` is fitted by
golden-section search on `log T` — deterministic, no optimizer state, no seed
dependence, no GPU. This is the 1-D convex reduction of the Guo et al. LBFGS
recipe. Bounds `T ∈ [0.05, 100]`; a fit that exits that range raises
`ValueError`, which is the task's `ABORT_CONDITIONS` guard for
exploding/vanishing logits.

**Binning convention.** Half-open `[lo, hi)` except the LAST bin, which is
closed `[lo, 1.0]` — so a perfectly-calibrated flat model (confidence exactly
`1/C`) is captured in a real bin instead of orphaned, and confidence `1.0`
always lands in the top bin. This convention is single-sourced in
`_bin_mask()`; the initial draft inlined three slightly different variants in
`compute_ece` and `reliability_diagram`, which is exactly how binning bugs
start.

**Non-goals honored.** No production weight mutation. `TemperatureScaler.wrap`
is research-only — the live 50D inference service keeps its own softmax
(INV-012 frozen tensor contract). The module imports cleanly without torch
(lazy numpy/torch imports at function scope), so the slim test venv can
exercise the numeric core.

## 4. Empirical benchmark (20,000 OOS samples, BENCHMARK_PLAN)

Synthetic 3-class fold calibrated to the observed production shape
(confidence ≈ 0.90 while accuracy ≈ 0.69):

```
N=20000   confidence=0.8972   accuracy=0.6934
T fitted  = 3.5482
ECE      0.20399 -> 0.00640   (96.9% reduction)
Brier    0.16659 -> 0.13940
NLL      1.23380 -> 0.71445
reliability bins populated: 7 -> 7
```

Temperature scaling removes essentially all measured miscalibration on the
synthetic overconfident fold and cuts NLL by 42%. **On real NSE OOS folds the
reduction will be smaller** — this fixture is deliberately built at the
miscalibrated end of the range to prove the mechanism works. The honest
number requires fitting `T` on a training fold and evaluating on a held-out
fold (`test_oos_protocol_fit_train_eval_val_reduces_ece`), which is also
green.

## 5. Test evidence

`tests/unit/test_calibration_research.py` — **63 tests, all passing**:

```
63 passed, 1 warning in 1.51s
```

Coverage: ECE/Brier/NLL anchors and bounds, reliability bin accounting,
temperature identity/sharpen/flattening/argmax-invariance, deterministic fit,
serialization round-trip, OOS fit-train/eval-val protocol, fail-loud input
validation, single-class and degenerate-temperature aborts, torch interop,
and two contract-conformance tests: the module imports without torch and does
not touch `application.live` / `live_engine`.

```
ruff check          All checks passed!
ruff format --check clean
mypy --explicit-package-bases  Success: no issues found in 1 source file
verify_critical_suite_manifest.py  CRITICAL_SUITE_MANIFEST_OK: 220 paths
```

## 6. A test-design bug worth recording (found and fixed in this run)

The first draft of the test battery derived labels from `argmax(logits)`. That
makes the model **100% accurate by construction**: confidence and accuracy
agree in every bin, ECE reads ~0, and the optimal temperature runs to the
`T → 0` boundary — because at 100% accuracy the NLL minimum genuinely *is*
maximum sharpness. Every "temperature scaling improves calibration" test was
therefore self-fulfilling and could not have failed.

The fixtures now draw the label first, boost the logit toward it by *less*
than the softmax sharpening, and return the **drawn** labels (never the
argmax). The fixture comment records the trap, and
`test_ece_overconfident_fold_is_materially_miscalibrated` asserts the fold is
genuinely miscalibrated (confidence `> 0.80` while accuracy `< 0.80`) so the
improvement claim has something real to bite on.

Two related test-arithmetic errors were caught by actually running the math
rather than trusting it: the 3-class ECE ceiling is `1 - 1/C ≈ 0.667` (a
`> 0.9` assert is unreachable and would mask a regression), and ECE is *not*
monotone in overconfidence — it is zero at both the calibrated and the
confidently-wrong extremes, so the monotone quantity to assert on is mean
confidence.

## 7. Recommendation (operator decision, not implemented here)

The live path remains uncalibrated by design in this change: ML-VAL-002's
`NON_GOALS` forbid altering the model forward pass, and promoting a
temperature into `InferenceService.infer_probabilities` is a live-tensor
contract change requiring INV-012 review and a governance-gated promotion.

If the measured OOS gap on real NSE data is material, the path is:
fit `T` on a walk-forward validation fold → persist alongside the model
artifact → apply `TemperatureScaler.apply_to_logits` in
`infer_probabilities` under a feature flag → gate via the existing
`ConfidenceCalibrator`/`PromotionState` machinery rather than a direct
policy-threshold edit. That is a follow-up task (candidate: ML-GOV-003
adjacent), not this one.

---

**Evidence files:** `src/nexus_scalp/research/calibration.py`,
`tests/unit/test_calibration_research.py`, this report.
**Task:** `docs/ml-system/tasks/ML-VAL-002.md` — all acceptance criteria `[x]`.
