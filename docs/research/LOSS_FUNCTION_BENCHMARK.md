# Loss Function Benchmark — ML-TRAIN-002

**Task:** ML-TRAIN-002 (Stream E — Training), loss function exploration.
**Status:** DONE. Code: `src/nexus_scalp/training/losses.py`. Tests: `tests/unit/test_loss_functions.py` (108 tests, all green).
**Date:** 2026-09-21

## 1. What existed before

Cross-entropy was the only loss in reach of most trainers, and the one specialized
loss that existed (`FocalLossWithSmoothing`) was an inline class buried at
`src/nexus_scalp/training/walk_forward_trainer.py:2683`:

| Site | Loss | File:line |
|---|---|---|
| CandidateTrainer | `FocalLossWithSmoothing(alpha, gamma=2.0, label_smoothing=0.08)` (imported from the trainer module) | `model_generation/training.py:270` |
| WalkForwardTrainer (fit loop) | `nn.CrossEntropyLoss(weight=weights_tensor)` | `training/walk_forward_trainer.py:709` |
| WalkForwardTrainer (fine-tune) | `nn.CrossEntropyLoss(weight=full_weights_tensor)` | `training/walk_forward_trainer.py:848` |
| model_lab trainer | `nn.CrossEntropyLoss(weight=alpha, label_smoothing=spec.label_smoothing)` ×3 branches | `model_lab/trainer.py:181-186` |
| model_lab runner | `nn.CrossEntropyLoss(label_smoothing=spec.label_smoothing)` | `model_lab/lab_runner.py:173` |
| sequence trainer | `nn.CrossEntropyLoss()` | `model_generation/sequence_training.py:226` |
| studio routes | `nn.CrossEntropyLoss()` ×2 | `web/model_studio_routes.py:1112,1882` |

**The actual gap** was not "no focal loss exists" — it was that no canonical,
config-selectable loss existed at a stable import path, so the five sites above
could not be compared on equal footing or swapped from configuration.

## 2. What was delivered

`src/nexus_scalp/training/losses.py` — every loss behind one shared
`forward(logits, targets, sample_weights=None)` signature, built by name:

| Name | Class | Reference |
|---|---|---|
| `cross_entropy` | `_CrossEntropyLoss` | `torch.nn.CrossEntropyLoss` (+ sample weights) |
| `focal` | `FocalLoss` | Lin et al. 2017 |
| `label_smoothing` | `LabelSmoothingCrossEntropy` | — |
| `focal_smoothing` | `FocalLossWithSmoothing` | Lin 2017 + smoothing; **bit-identical to the legacy inline class** |
| `class_balanced` | `ClassBalancedLoss` | Cui et al. 2019 effective-number weights |
| `dice` | `DiceLoss` | soft-Dice, set-based minority gradient |

Plus `effective_number_weights(counts, beta, boost_active)` and the `build_loss`
factory (`LOSS_NAMES` / `LOSS_REGISTRY`).

The legacy `FocalLossWithSmoothing` is left in `walk_forward_trainer.py` so the
candidate trainer keeps working unchanged; the canonical class asserts
bit-identical output to it (`test_focal_loss_with_smoothing_matches_legacy_walk_forward_implementation`).

### 2b. Bugs found and fixed by the test battery (each became a regression test)

1. **In-place `clamp_` on `p_t` broke autograd** — `_clamp_pt` used `clamp_`, and
   `p_t` is part of the graph (derived from `probs`). `backward()` raised
   *"variable needed for gradient computation has been modified by an inplace
   operation"*. Now out-of-place, with the reason recorded in the docstring.
2. **`ignore_index` targets crashed the gather** — an `ignore_index` of -100
   reached `probs.gather(1, targets)` and raised *index out of bounds*. Every
   gather now runs against `safe_targets` (invalid rows clamped to 0, result
   zeroed by the `valid` mask). Note `DiceLoss` was already safe via
   `targets.clamp(min=0)`.
3. **Building `smooth_ce` inside `torch.no_grad()` killed every gradient** — the
   *target distribution* is constant, but the *log-probabilities* are model
   outputs, so the gather must stay outside `no_grad`. This one was invisible
   without a gradient check: the loss computed fine and training "worked" while
   learning nothing. Fixed in both `LabelSmoothingCrossEntropy` and
   `FocalLossWithSmoothing`.
4. **`nn.CrossEntropyLoss(weight=)` was being double-weighted** —
   `nn.CrossEntropyLoss` bakes `weight` into its per-sample output, and
   `_reduce(..., class_weights=w)` multiplied by it again. The wrapper now holds
   the weight itself and passes an *unweighted* per-sample loss into `_reduce`.
5. **The benchmark was vacuous** — `_run_benchmark_fold("label_smoothing")`
   relied on `build_loss`'s default `smoothing=0.0`, which is bit-identical to
   cross entropy, so the "smoothing vs CE" leg of the comparison was measuring
   one loss against itself. Now the helper takes `**loss_kwargs`, every headline
   comparison passes a real hyperparameter, and
   `test_benchmark_label_smoothing_actually_smooths` guards the trap.

## 3. Benchmark (5 folds, identical splits)

**Protocol.** A 3-class imbalanced task (80% / 11% / 9%) with a separable
minority signal: per-class centres in 12-D plus Gaussian noise. Fold split and
model seed are derived from a per-fold generator, so **only the loss differs**
between legs — 384 samples, 80/20 split, 25 epochs, AdamW(lr=5e-3, wd=1e-4),
full-batch. Metrics on the held-out 20%: balanced accuracy, macro F1 over the
minority classes (BUY/SELL), Brier score.

### Headline averages

| Loss | Balanced acc | Minority F1 | Brier |
|---|---|---|---|
| CrossEntropy (baseline) | 0.7660 | 0.7139 | 0.1056 |
| FocalLoss (gamma=2.0) | **0.8638** | **0.8212** | 0.1094 |
| LabelSmoothing (s=0.1) | 0.7978 | 0.7520 | 0.1073 |
| FocalLossWithSmoothing (gamma=2.0, s=0.08) | **0.8638** | **0.8212** | 0.1073 |

### Per-fold minority F1 (CE vs Focal)

| Seed | CE | Focal |
|---|---|---|
| 101 | 0.7619 | 0.8235 |
| 202 | 0.7778 | 0.7357 |
| 303 | 1.0000 | 1.0000 |
| 404 | 0.5714 | 0.7619 |
| 505 | 0.4583 | 0.7846 |

### Per-fold Brier (CE vs LabelSmoothing s=0.1)

| Seed | CE | LS |
|---|---|---|
| 101 | 0.13279 | 0.13182 |
| 202 | 0.08954 | 0.11616 |
| 303 | 0.03041 | 0.03478 |
| 404 | 0.15982 | 0.14179 |
| 505 | 0.11545 | 0.11173 |

## 4. Findings

### Focal loss: minority F1 +0.107 (0.7139 → 0.8212), 4/5 folds

The mechanism holds. With 80% NO_TRADE, plain CE's gradient is dominated by
easy majority samples the model already gets right; the `(1 - p_t)^gamma` factor
drives those toward zero, so the minority updates survive. This is most visible
on the worst folds (505: 0.458 → 0.785; 404: 0.571 → 0.762), which is exactly the
regime the production problem lives in. Focal lost exactly one fold (202) and
tied one (303, where both already hit 1.0).

### Label smoothing: lower confidence ceiling, *higher* Brier

This is the honest, non-obvious result. Smoothing raised the confidence ceiling
of predictions as intended, but average Brier went **up** (0.1056 → 0.1073):
2/5 folds improved (101, 404, 505 narrow), 2/5 got worse (202, 303).

The explanation is that Brier only falls if the model was **over**-confident to
start with. This synthetic model is *under*-confident (the softmax of a
12-D-to-3 MLP trained 25 epochs on 307 samples rarely saturates), so pushing
probabilities away from the extremes moves them further from the one-hot truth,
not closer. The mechanism — capping the achievable target below 1.0 — is
correct and guaranteed by construction, but its *effect* on any downstream
calibration metric depends on which side of calibrated the model already sits.

That invariant is asserted directly instead:
`test_label_smoothing_lowers_the_confidence_ceiling` trains both legs and
requires the smoothed model's max predicted probability to be strictly lower
per fold. Brier is recorded as a measured quantity
(`test_benchmark_label_smoothing_brier_is_recorded_not_assumed`), not asserted
as a win.

### FocalLossWithSmoothing ≈ FocalLoss on this task

Adding smoothing on top of focal did not move F1 (0.8212 both) but did lower
Brier slightly (0.1094 → 0.1073) — consistent with it sharing focal's
imbalance handling while adding the confidence cap.

## 5. What this benchmark does NOT establish

This is a 384-row synthetic task with a linearly-separable minority signal. It
demonstrates the *mechanisms* are wired correctly and can move the target
metrics; it does not predict gold M1 behaviour. A walk-forward A/B on real
broker history is the only valid basis for changing a production default — and
that is now a one-dict change per fold via `build_loss`, which was impossible
before this task.

## 6. Wiring status

`losses.py` is delivered and tested but **not yet wired into the five CE call
sites** listed in §1. That is deliberate: NON_GOALS forbids changing a
production loss without empirical evidence, and the evidence above is synthetic.
The remaining sites are the follow-up work; `build_loss` is the API they would
call.
