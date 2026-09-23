# 2026-09-22 — AGENT-ML-ARCH — ML-ARCH-003

## Task
Temporal Multihead Attention vs Positional Encoding Ablation (Stream D, P2).
Ablate Multihead Attention (heads 2/4) and Positional Encodings (sinusoidal /
rotary / none) in financial sequence modeling, with a causality proof and a
latency benchmark. `docs/ml-system/tasks/ML-ARCH-003.md`.

## Outcome
**DONE.** Research artifacts only — nothing was wired into live serving
(NON_GOALS honored; `TCNAttentionV1`, `ScalpNet`, `model_factory.py`,
`model_lab/architectures.py` are all untouched).

## Deliverables
- `src/nexus_scalp/models/attention.py` (new) — `CausalSelfAttention` (mask as a
  construction property, not a caller argument — closes the bug at
  `model_generation/architectures.py:273` and `models/scalp_net.py:231`),
  `make_causal_mask`, `rotate_half` / `apply_rotary`, positional encoders
  `none` / `sinusoidal` / `learned` / `rotary` behind `POSITIONAL_ENCODINGS`.
- `src/nexus_scalp/models/attention_ablation.py` (new) — arm spec, pure-TCN
  control, attention arm, matrix builder, runner with a shared batch-order RNG,
  sklearn-free macro-F1, val metrics, `process_time`-based latency.
- `tests/unit/test_attention_ablation.py` (new, 86 tests, critical-suite
  registered, manifest 233 → 234).
- `docs/research/ATTENTION_ABLATION.md` (new) — full 20-arm evidence table.

## Key finding (proven, not assumed)

**The causal mask is a no-op at the decision row of a last-timestep-pooled
model.** With identical weights, `CausalSelfAttention(causal=True)` and
`CausalSelfAttention(causal=False)` produce a *bit-identical* last row
(`atol=0.0`); only intermediate rows differ. Position `T-1` may attend to
`0..T-1` under the mask, which is exactly its unmasked allowed set.

Direct consequence: in the ablation matrix, causal and unmasked arms return
bit-identical loss and accuracy across all 8 matched pairs (max diff `0.0`).
An ablation that flips only the mask while pooling the last timestep is
degenerate by construction. The mask is a **correctness invariant for the
intermediate sequence**, not a predictive-loss knob. To make it bind on the
decision you must change the pooling (mean-pool over positions — proven to
diverge in `test_mask_changes_a_mean_pooled_output`) or read a mid-sequence
position.

Reported to the operator: do not pay the mask latency premium on a
last-timestep-pooled model (~+396 µs/sample for zero decision-row benefit), and
apply the mask only if mid-window states are ever consumed.

## Benchmark numbers (20 arms, fixed synthetic fold, seed 1337, 60 steps)

| Arm | params | val_loss | val_acc | macro-F1 | latency/sample |
|---|---|---|---|---|---|
| control (no attention) | 43 011 | 0.28291 | 0.9583 | 0.3262 | ~299 µs |
| best unmasked | 59 779 | 0.22490 | 0.9635 | 0.3271 | ~857 µs |
| best causal | 59 779 | 0.22490 | 0.9635 | 0.3271 | ~1253 µs |

Attention improves val loss −20% and accuracy +0.5 pt, for +187%..+320% latency.
Head count 2 vs 4: no measurable difference at this problem size. `learned` was
the best positional arm; `rotary` mid-pack; `sinusoidal` worst of the
attention-using arms. (Train class balance 492/10/10, so macro-F1 is
minority-dominated — read the loss column, not F1.)

## Verification evidence
```
pytest tests/unit/test_attention_ablation.py  →  86 passed (serial)
ruff check 0.16.8 (CI pin)                   →  All checks passed!
ruff format --check                          →  3 files already formatted
mypy src/nexus_scalp/models/attention*.py    →  Success: no issues found
scripts/ci/verify_critical_suite_manifest.py →  CRITICAL_SUITE_MANIFEST_OK: 234 paths
```

AC-1 proven three ways: autograd Jacobian `dY_t/dX_{t+k} == 0` exactly (float64)
for heads 2/4 and seq lens 7/16; attention weights carry zero mass above the
diagonal; past-perturbation probe leaves earlier outputs bit-identical. The
incumbent unmasked layer is proven non-causal by the same Jacobian method.

AC-2 is the benchmark table plus the mechanism analysis. The task's "Sharpe"
wording maps onto val loss / accuracy / macro-F1 because the harness trains on
synthetic data (no M1 bars are committed to git, so no P&L series exists for
this ablation).

## Process notes (defects found and fixed in this task's own work)
1. `CausalConv1dBlock` operates in `(B, H, T)` while the encoders/attention are
   `(B, T, H)` — an initial `NoAttentionArm.forward` omitted the transpose back,
   cascading shape errors into ~18 tests.
2. `torch.log` has no float overload — a module-level float literal raised
   `TypeError` at class construction.
3. `compute_val_metrics` took `(N, T)` labels into `CrossEntropyLoss`, which
   needs `(N,)` — labels are now reduced to the decision position, and
   `(N, 1, C)` logits squeezed.
4. My first `compute_macro_f1` divided by the class count; sklearn's macro
   convention **skips** a class absent from both predictions and labels. Now
   matched, hand-verified.
5. A first-draft shift-invariance test was ill-posed: the conv stack's unpadded
   residual (the ML-ARCH-002 finding) makes a window-prepend legitimately change
   downstream states. Replaced with a direct attention-weight-mass discriminator.
6. `/tmp` carries a foreign `bisect.py` that shadows the stdlib and breaks any
   interpreter launched from `/tmp`; probes were moved to the scratch dir. Not
   deleted (foreign artifact).

## Next run
The remaining ML tasks are human-gated: `ML-ARCH-001` AC-3 (operator must pick
sunset Option A/B/C per DEC-0010), `ML-GOV-002`, `ML-FEAT-003`, `ML-EXP-002`
(this task's completion unblocks `ML-EXP-002`'s `ML-ARCH-003` dependency, but
`ML-EXP-002` is itself `HUMAN_DECISION_REQUIRED: YES`), `ML-OBS-002`.
`ML-CI-001`'s scope is `.github/workflows/*`, which the swarm is forbidden to
modify. Next candidate is whatever the operator unblocks.
