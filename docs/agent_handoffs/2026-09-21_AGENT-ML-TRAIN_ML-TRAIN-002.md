# 2026-09-21 — AGENT-ML-TRAIN — ML-TRAIN-002 (Loss Function Exploration)

**Status:** DONE — PR open. Code, tests, and benchmark delivered.

## Task
Implement and benchmark specialized financial loss functions (Focal Loss,
Class-Balanced Loss, Label Smoothing Cross-Entropy) to counter extreme NO_TRADE
class imbalance (70-85% of M1 samples) and reduce probability overconfidence.

## What the repo actually had (correcting the task's CURRENT_EVIDENCE)

The task's evidence section states `CandidateTrainer.loss_fn` is
`nn.CrossEntropyLoss(weight=class_weights)`. That is stale — the candidate
trainer has in fact used `FocalLossWithSmoothing(alpha, gamma=2.0,
label_smoothing=0.08)` since GATE-2 (`model_generation/training.py:270`), and
that class lived inline at `training/walk_forward_trainer.py:2683`.

So the real gap was **not** "no focal loss exists". It was:

1. The one specialized loss was an inline class in a 2,800-line trainer module,
   not at a stable import path.
2. Five other training sites hard-coded `nn.CrossEntropyLoss`, so no site could
   be compared on equal footing or swapped from configuration:
   - `training/walk_forward_trainer.py:709` (fit loop)
   - `training/walk_forward_trainer.py:848` (fine-tune)
   - `model_lab/trainer.py:181-186` (3 branches)
   - `model_lab/lab_runner.py:173`
   - `model_generation/sequence_training.py:226`
   - (`web/model_studio_routes.py:1112,1882` — studio only, not training)

## Delivered

`src/nexus_scalp/training/losses.py` — every loss behind one shared
`forward(logits, targets, sample_weights=None)` signature, built by name through
`build_loss`:

| Name | Class | Reference |
|---|---|---|
| `cross_entropy` | `_CrossEntropyLoss` | `torch.nn.CrossEntropyLoss` + sample weights |
| `focal` | `FocalLoss` | Lin et al. 2017 |
| `label_smoothing` | `LabelSmoothingCrossEntropy` | uniform soft targets |
| `focal_smoothing` | `FocalLossWithSmoothing` | **bit-identical to the legacy inline class** |
| `class_balanced` | `ClassBalancedLoss` | Cui et al. 2019 effective-number weights |
| `dice` | `DiceLoss` | soft-Dice; set-based, direct minority-F1 gradient |

Plus `effective_number_weights(counts, beta, boost_active)`, `LOSS_NAMES`,
`LOSS_REGISTRY`.

The legacy class is left in `walk_forward_trainer.py` so `CandidateTrainer` keeps
working unchanged; the canonical class is asserted bit-identical to it in
`test_focal_loss_with_smoothing_matches_legacy_walk_forward_implementation`.

## Bugs found and fixed by the test battery (each became a regression test)

These are the useful output of this task — four would have shipped silently.

1. **In-place `clamp_` on `p_t` broke autograd.** `_clamp_pt` used `clamp_` and
   `p_t` is part of the graph (derived from `probs`), so `backward()` raised
   *"variable needed for gradient computation has been modified by an inplace
   operation"*. Now out-of-place, reason recorded in the docstring.
2. **`ignore_index` targets crashed the gather.** `probs.gather(1, targets)`
   with a -100 target raised *index out of bounds*. Every gather now runs against
   `safe_targets` (invalid rows clamped to 0, result zeroed by `valid`).
3. **Building `smooth_ce` inside `torch.no_grad()` killed every gradient.** The
   *target distribution* is constant, but the *log-probabilities* are model
   outputs, so the gather must stay OUTSIDE `no_grad`. This one was invisible
   without a gradient check: the loss computed fine and training "worked" while
   learning nothing. Fixed in both `LabelSmoothingCrossEntropy` and
   `FocalLossWithSmoothing`.
4. **`nn.CrossEntropyLoss(weight=)` was double-weighted.** `nn.CrossEntropyLoss`
   bakes `weight` into its per-sample output, and `_reduce(..., class_weights=w)`
   multiplied by it again. The wrapper now holds the weight itself and passes an
   *unweighted* per-sample loss into `_reduce`.
5. **The benchmark was vacuous.** `_run_benchmark_fold("label_smoothing")`
   relied on `build_loss`'s default `smoothing=0.0`, which is bit-identical to
   cross entropy — so that leg compared one loss against itself. The helper now
   takes `**loss_kwargs`, and `test_benchmark_label_smoothing_actually_smooths`
   pins the trap.

## Benchmark (5 folds, identical splits; full numbers in
`docs/research/LOSS_FUNCTION_BENCHMARK.md`)

384 samples, 80/11/9 class balance, separable minority signal, 80/20 split,
25 epochs, full-batch AdamW. Per-fold generator ⇒ only the loss differs.

| Loss | Balanced acc | Minority F1 | Brier |
|---|---|---|---|
| CrossEntropy | 0.7660 | 0.7139 | 0.1056 |
| FocalLoss (γ=2) | **0.8638** | **0.8212** | 0.1094 |
| LabelSmoothing (s=0.1) | 0.7978 | 0.7520 | 0.1073 |
| FocalLossWithSmoothing (γ=2, s=0.08) | 0.8638 | 0.8212 | 0.1073 |

**Focal: minority F1 +0.107, wins 4/5 folds.** Most on the worst folds
(505: 0.458→0.785; 404: 0.571→0.762) — the regime the production problem lives
in. Lost one fold (202), tied one (303, both at 1.0).

**Label smoothing: lower confidence ceiling but HIGHER Brier.** The honest
non-obvious result. Brier went *up* (0.1056→0.1073): 2/5 folds improved, 2/5 got
worse. Brier only falls if the model was over-confident to begin with; this
synthetic model is under-confident, so pushing probabilities off the extremes
moves them further from truth. The *mechanism* (capping the achievable target
below 1.0) is guaranteed by construction, so that is what is asserted —
`test_label_smoothing_lowers_the_confidence_ceiling` requires strictly lower max
predicted probability per fold. Brier is recorded, not asserted as a win.

## What this does NOT establish

A 384-row synthetic task with a linearly-separable minority signal. It proves
the mechanisms are wired correctly and can move the target metrics; it does not
predict gold M1 behaviour. A walk-forward A/B on real broker history is the only
valid basis for changing a production default — now a one-dict change per fold
via `build_loss`, which was impossible before.

**Per NON_GOALS, no production loss was changed.** The five CE sites in §1 are
the follow-up work; `build_loss` is the API they would call.

## Verification

- `tests/unit/test_loss_functions.py`: **108/108 pass** (new). Includes
  `torch.autograd.gradcheck` at float64 across the full gamma×smoothing matrix,
  finite-difference gradient checks, and analytic-formula parity vs `torch.nn`.
- Regression: **124/124 pass** across `test_walk_forward_trainer`,
  `test_training_stage_progress`, `test_model_lab`, `test_model_generation_phase13`.
- `ruff check` + `ruff format --check`: clean on both new files.
- `mypy src/nexus_scalp/training/losses.py`: clean.
- Critical-suite manifest: 215 → 216 paths, `CRITICAL_SUITE_MANIFEST_OK`.

## Files
- `src/nexus_scalp/training/losses.py` (new)
- `tests/unit/test_loss_functions.py` (new, critical-suite registered)
- `docs/research/LOSS_FUNCTION_BENCHMARK.md` (new)
- `docs/ml-system/tasks/ML-TRAIN-002.md`, `docs/ml-system/TASK_BOARD.md`,
  `docs/ml-system/06_TASK_LEDGER.md` (status → DONE)
- `agents/taskboard.md`, `agents/locks.yaml`
