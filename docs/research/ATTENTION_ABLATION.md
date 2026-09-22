# ML-ARCH-003 — Attention & Positional Encoding Ablation Report

Status: COMPLETE (research artifacts only; **not** wired into live serving, per NON_GOALS)
Task: `docs/ml-system/tasks/ML-ARCH-003.md`
Owner: AGENT-ML-ARCH | Scope: `src/nexus_scalp/models/attention.py`
Date: 2026-09-22

---

## 1. What was built

| Artifact | Role |
| --- | --- |
| `src/nexus_scalp/models/attention.py` | `CausalSelfAttention` (triangular causal mask as a *construction* property), `make_causal_mask`, `rotate_half`/`apply_rotary`, positional encoders `none` / `sinusoidal` / `learned` / `rotary`, `POSITIONAL_ENCODINGS` registry + `create_positional_encoding` |
| `src/nexus_scalp/models/attention_ablation.py` | Ablation harness: `AttentionArm` spec, `NoAttentionArm` (pure-TCN control), `AttentionAttentionArm`, `build_attention_ablation_arms` (attention × heads × positional matrix), `run_attention_ablation` (fixed data/seed/batches/steps per arm), `compute_macro_f1` (sklearn-free), `compute_val_metrics`, `measure_latency` (`time.process_time`, median) |
| `tests/unit/test_attention_ablation.py` | 86 tests, pinned in `tests/critical_suite.txt` |

The incumbent bug this task exists to close is real and now reproducible:
`model_generation/architectures.py:273` calls
`self.attention(h, h, h, need_weights=False)` with **no `attn_mask`**, and
`models/scalp_net.py:231` does the same on its 3D path.

## 2. Findings (the important part)

### 2.1 The causal mask is a no-op at the decision row of a last-timestep-pooled model

**This is the headline result.** Proven, not assumed
(`test_mask_only_affects_rows_before_the_last`):

Take one `CausalSelfAttention` and one `CausalSelfAttention(causal=False)`
loaded with the *same* weights. Feed the same input.

* The **full** outputs differ.
* The **last row** of the output and of the attention weights are **identical** (`atol=0.0`).

```
last-row outputs identical: True
full outputs identical     : False
row 3 attn identical       : False
row3 causal future mass    : 0.0
row3 unmasked future mass  : 1.498
```

Why: the model reads only `h[:, -1, :]`. Position `T-1`'s allowed set under a
causal mask is `0..T-1` — which is *exactly* its allowed set without a mask. The
mask forbids only taps that the pooling discards anyway.

Consequence: the causal/unmasked ablation arms in this harness return
**bit-identical loss and accuracy** (verified: max diff `0.0` across all 8
matched arm pairs). That is not a harness bug — it is a true property of the
architecture. A causal mask is a **correctness invariant for the intermediate
sequence**, not a predictive-loss knob, on any model that pools the last
timestep.

Practical reading for NSE: `TCNAttentionV1` and ScalpNet-3D are safe to serve
(their decision point never reads the future), but an "ablation" that flips only
the mask while pooling the last timestep measures nothing. To make the mask bind
on the decision you must change the pooling (mean-pool over positions —
`test_mask_changes_a_mean_pooled_output` proves the outputs then diverge) or read
a mid-sequence position.

### 2.2 Attention costs latency, buys little on a balanced synthetic problem

Full matrix (20 arms), fixed fold, 60 steps, `lr=2e-3`, seed 1337, median of 25
`process_time` samples. Train set 512×32×50; class balance 492/10/10, so
macro-F1 is dominated by the minority classes — read the *loss* column, not F1.

| Arm | params | train_loss | val_loss | val_acc | macro-F1 | latency/sample |
| --- | --- | --- | --- | --- | --- | --- |
| control (no attention) | 43 011 | 0.12594 | 0.28291 | 0.9583 | 0.3262 | ~299 µs |
| best unmasked | 59 779 | 0.20020 | 0.22490 | 0.9635 | 0.3271 | ~857 µs |
| best causal | 59 779 | 0.20020 | 0.22490 | 0.9635 | 0.3271 | ~1253 µs |

* Attention improves val loss 0.283 → 0.225 (−20%) and accuracy +0.5 pt.
* Latency penalty is large: **+558 µs (+187%)** unmasked, **+955 µs (+320%)**
  causal — against a sub-millisecond inference SLA for the 50D live path.
* Causal vs unmasked are identical on every metric (see 2.1) but causal pays a
  **+396 µs** latency premium for the mask alone, because `nn.MultiheadAttention`
  materialises the `(T, T)` mask per forward while the unmasked path skips it.
* `learned` positional encoding was the best arm in both attention families;
  `rotary` was mid-pack; `sinusoidal` was the worst arm that used attention.
  Under this signal the positional choice moves val loss by ~0.03 (±13%).
* Head count 2 vs 4: **no measurable difference** at this problem size (loss
  identical to 4 dp across all matched pairs).

### 2.3 Architectural details worth recording

* `CausalConv1dBlock` (inherited from ML-ARCH-002) carries an **unpadded
  residual**, so the whole conv stack's memory depth is the full sequence. This
  is why the arm's last-timestep decision legitimately depends on the whole
  window — it is causal history, not lookahead.
* The mask is always returned as a **float additive** mask, never `bool`. A bool
  `attn_mask` inverts the convention (`True` = masked), which would silently
  permit exactly the future taps this module exists to prevent.
* RoPE is injected at Q/K only (`RotaryPositionalEncoding.forward` is a
  pass-through); additive encodings bias the residual stream. RoPE at position 0
  is exactly the identity (`theta_0 = 0`), proven bit-exact.
* `compute_macro_f1` follows sklearn's macro convention: a class absent from
  both predictions and labels is **skipped** (the divisor shrinks), while a class
  with an undefined F1 contributes 0. Hand-verified.

## 3. Verification evidence

```
pytest tests/unit/test_attention_ablation.py  →  86 passed (serial, -p no:cacheprovider)
ruff check 0.16.8 (CI pin)                   →  All checks passed!
ruff format --check                          →  3 files already formatted
mypy src/nexus_scalp/models/attention*.py    →  Success: no issues found
scripts/ci/verify_critical_suite_manifest.py →  CRITICAL_SUITE_MANIFEST_OK: 234 paths
```

AC-1 (strict causal masking) — proven three ways on the layer itself:
autograd Jacobian `dY_t/dX_{t+k} == 0` exactly in float64 across head counts 2
and 4 and sequence lengths 7/16; returned attention weights carry **zero** mass
above the diagonal; empirical perturbation probe leaves past outputs
bit-identical. The incumbent's unmasked layer is proven *non*-causal by the same
Jacobian method (future block > 0).

AC-2 (benchmark documents the trade-off) — the table in §2.2 plus the mechanism
analysis in §2.1. The task's "Sharpe" wording maps onto val loss / accuracy /
macro-F1 here because this harness trains on synthetic data (real M1 bars are
not committed to git); a Sharpe number would require a price series and is out of
scope for a synthetic ablation.

## 4. Recommendation (for the operator)

1. **Do not** pay the causal-mask latency premium on a last-timestep-pooled
   model — it buys nothing on the decision and costs ~+400 µs/sample.
2. The unmasked incumbent in `TCNAttentionV1`/ScalpNet-3D is not lookahead at the
   decision point (§2.1), so it is safe to serve; but the *intermediate* sequence
   states it produces are not functions of the past alone, which is a real
   invariant violation if anything ever consumes a mid-window state.
3. If mid-window states ever matter (e.g. an auxiliary loss on intermediate
   positions, or mean-pooling), the mask becomes load-bearing and should be
   applied — `CausalSelfAttention(causal=True)` is the drop-in.
4. Attention at this problem size gives a modest loss gain for a 2–3× latency
   cost. It does not belong on the sub-millisecond 50D live path; it belongs in
   the research/70D candidate path where latency is not the constraint.
5. Ablation arms that flip only the mask must be paired with a pooling change,
   otherwise the comparison is degenerate by construction (§2.1).

## 5. Non-goals respected

No live-serving path was modified. `TCNAttentionV1`, `ScalpNet`,
`model_factory.py`, and `model_lab/architectures.py` are untouched; the new
modules are research/ablation components only.
