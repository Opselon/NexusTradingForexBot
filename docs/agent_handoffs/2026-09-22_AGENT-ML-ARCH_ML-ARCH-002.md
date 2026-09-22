# ML-ARCH-002 — Causal TCN Dilation & Receptive Field Optimization

**Role:** AGENT-ML-ARCH (Stream D — Model Architecture)
**Status:** DONE — PR pending
**Date:** 2026-09-22
**Branch:** `agent/ml-arch/ml-arch-002` (worktree `/tmp/wt-ml-arch-ml-arch-002`)

## Objective

Optimize `CausalConv1dBlock` dilation schedules, kernel sizes and receptive-field depth in
`TCN_ATTENTION_V1`, ensuring the receptive field matches M1 market swing duration without
lookahead leakage.

## What changed

### `src/nexus_scalp/model_generation/architectures.py`

- **`dilation_schedule(kind, blocks)`** — factory for three schedules:
  - `geometric` — `2**b`. The historical default; behaviour byte-for-byte unchanged.
  - `linear` — `b + 1`.
  - `fibonacci` — `1, 1, 2, 3, 5, …`.
  - Validates the schedule name and that `blocks >= 1`.
- **`receptive_field(kernel_size, dilations)`** — the closed form `1 + Σ (k−1)·d_b`.
- **`min_blocks_for_receptive_field(target_rf, kernel_size, kind)`** — minimal block count
  reaching a target receptive field (monotone bounded search).
- **`TCNAttentionV1(..., dilation="geometric")`** — the conv stack is now
  schedule-parameterized; exposes `.dilations` and `.receptive_field`.
- **`build_tcn_attention_v1`** — forwards `dilation_schedule` (and legacy `dilation`) from
  the `ModelFactory` `params` dict, so experiments select schedules via config.
- Named RF targets: `RF_TARGET_MICROSTRUCTURE=16`, `RF_TARGET_SESSION_SWING=32`,
  `RF_TARGET_MULTI_HOUR=64`.

### `tests/unit/test_causal_conv_invariants.py` (new — 131 tests)

Jacobian causality proof, receptive-field closed-form + empirical validation, schedule
construction, backwards compatibility, latency bounds. Critical-suite registered
(`tests/critical_suite.txt` → 232 paths).

### `docs/research/TCN_RECEPTIVE_FIELD.md` (new)

The architectural recommendation the task's IMPLEMENTATION_PLAN step 5 requires.

## Verification evidence (all run locally)

| Gate | Result |
|------|--------|
| `pytest tests/unit/test_causal_conv_invariants.py` | **131 passed, 0 failed** |
| Regression `pytest tests/unit/test_model_benchmark_phase13b.py` | **27 passed, 0 failed** |
| `ruff check` (CI pin 0.16.8) on both touched files | All checks passed! |
| `ruff format --check` on both touched files | already formatted |
| `mypy src/nexus_scalp/model_generation/architectures.py` | clean |
| `scripts/ci/verify_critical_suite_manifest.py` | `CRITICAL_SUITE_MANIFEST_OK: 232 paths` |
| `py_compile architectures.py` | OK |

Environment: hermes venv Python 3.11.16 + torch 2.13.0+cpu (the `.venv-linux` slim venv was
wiped by a host reboot and has no torch; `.venv-linux/bin/python` cannot run these tests).

## Acceptance criteria

**AC-1 — Causal invariance mathematically proven via Jacobian test.** For every dilation
schedule × block count × kernel size, the autograd gradient of `stack_output[t]` with
respect to every input position is computed into a (T, T) influence map and the **strict
upper triangle is asserted identically zero** — in `float64`, so this is exact, not a
tolerance claim.

Coverage: 27 stack configurations (3 schedules × 3 block counts × 3 kernel sizes), 9
model conv-stage configurations (the stage `TCNAttentionV1` actually runs), and 21
sequence-length × schedule combinations. The task's ABORT_CONDITION never fired.

Two non-autograd cross-checks guard against a vacuous proof: a strictly-past perturbation
*must* move the output, and a future perturbation must leave strictly-earlier outputs
bit-identical at `atol=0, rtol=0`. A forward hook also asserts the model pools exactly the
last timestep into the head.

**AC-2 — Receptive field formula validated across all block configurations.** The closed
form matches `model.receptive_field` for all 27 combinations, and an *independent empirical
impulse probe* — perturb one position, count how many lags measured backwards from the
final position still move the final output — equals the closed form exactly.

Reference configurations (k=3, geometric): B=3 → 15 bars, B=4 → 31, B=5 → 63, B=6 → 127.
Minimal block counts to *reach* the task's targets: **16 → 4 blocks, 32 → 5, 64 → 6**.

## Key finding — the residual skip bounds what the schedule controls

`CausalConv1dBlock.forward` computes `x + conv_path(x)` over the **full, unpadded** time
axis. Measured directly: an input impulse at *any* position moves the block output at
*every* position. So the conv branch has the closed-form receptive field, but the **block
receptive field is the whole sequence**.

Consequence: the model's effective memory is bounded by the sequence window
(`max_seq_len=64`), not by the dilation schedule. The schedule governs only how deeply the
conv branch alone integrates — the multi-scale temporal features the attention layer then
attends over. The [16, 32, 64]-bar targets are **conv-branch depth budgets**, not
end-to-end memory budgets.

This is documented, not "fixed": the residual is what makes deep TCN stacks trainable, and
causally projecting it would change the numerics of every existing checkpoint. The report
flags it for the operator if bounded end-to-end memory ever becomes a hard requirement.
The formula's docstring now states its scope precisely so no future caller mistakes the two.

## Backwards compatibility is exact

The default (`geometric`) path reproduces the pre-ML-ARCH-002 wiring bit-for-bit, asserted
three ways: identical conv dilations, all parameters equal via `torch.equal`, and forward
output equal at `atol=0, rtol=0`. Existing checkpoints are unaffected. No frozen domain
model was modified; no non-causal convolution was introduced (NON_GOALS honoured).

## Recommendation for research config

Adopt **B=5, k=3, geometric** (63-bar conv-branch RF) as the research default — it covers
the session-swing horizon with one block of headroom at latency indistinguishable from B=3.
The current default of B=3 (15 bars) covers only microstructure. This is a research-config
recommendation only; ML-ARCH-002 changes no live model weight (per the ML contract,
`TCNAttentionV1` is a benchmark/research candidate — promotion to Champion still requires
the 12 gates and the atomic promotion API).

## Next unblocked task

`ML-ARCH-003` (Temporal Attention vs Positional Encoding) — its only dependency was
`ML-ARCH-002`, now DONE. It is `HUMAN_DECISION_REQUIRED: NO`, `PARALLEL_SAFE`, P2. It is
the next-cycle candidate.

## Artifacts

- `src/nexus_scalp/model_generation/architectures.py` (modified)
- `tests/unit/test_causal_conv_invariants.py` (new)
- `docs/research/TCN_RECEPTIVE_FIELD.md` (new)
- `docs/ml-system/tasks/ML-ARCH-002.md` (DONE + evidence)
- `docs/ml-system/TASK_BOARD.md`, `docs/ml-system/06_TASK_LEDGER.md` (status → DONE)
- `tests/critical_suite.txt` (+1 path)
- `agents/locks.yaml` (lock registered), `agents/taskboard.md` (+1 row)
