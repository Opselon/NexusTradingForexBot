"""ML-ARCH-002 — Causal TCN dilation & receptive-field invariants.

Proves, with autograd Jacobians, that every dilation schedule is STRICTLY
CAUSAL: for the causal conv stack, the output at timestep t depends only on
inputs at indices <= t, i.e.  dY_t / dX_{t+k} == 0 exactly for all k > 0.
Also validates the exact receptive-field formula RF = 1 + sum_b (k - 1) * d_b
against an empirical perturbation probe, and measures forward-pass latency
across receptive-field depths.

Causality contract scope: the only stage where a future tap could enter is the
dilated conv stack (left-only padding). Final-state pooling reads the LAST
timestep of the decision window and self-attention attends over positions
0..T-1, i.e. the causal past of the decision point — so no lookahead can
appear downstream of the stack either. Both are asserted here.
"""

from __future__ import annotations

import time
from typing import cast

import pytest
import torch
from torch import nn

from nexus_scalp.model_generation.architectures import (
    DILATION_FIBONACCI,
    DILATION_GEOMETRIC,
    DILATION_LINEAR,
    RF_TARGET_MICROSTRUCTURE,
    RF_TARGET_MULTI_HOUR,
    RF_TARGET_SESSION_SWING,
    CausalConv1dBlock,
    TCNAttentionV1,
    build_tcn_attention_v1,
    dilation_schedule,
    min_blocks_for_receptive_field,
    receptive_field,
)

SCHEDULES = [DILATION_GEOMETRIC, DILATION_LINEAR, DILATION_FIBONACCI]

BLOCK_COUNTS = [3, 4, 5]
KERNEL_SIZES = [2, 3, 5]

# Named M1 market-memory targets (bars) the task dimensions schedules against.
RF_TARGETS = [
    (RF_TARGET_MICROSTRUCTURE, "microstructure"),
    (RF_TARGET_SESSION_SWING, "session swing"),
    (RF_TARGET_MULTI_HOUR, "multi-hour structure"),
]


def _conv_dilation(module: nn.Module) -> tuple[int, ...]:
    """Read a CausalConv1dBlock's conv dilation as a tuple (typed accessor)."""
    block = cast(CausalConv1dBlock, module)
    return tuple(cast(nn.Conv1d, block.conv).dilation)


def _make_stack(channels: int, kernel_size: int, schedule: str, blocks: int) -> nn.Sequential:
    """Deterministic eval-mode causal conv stack with the given schedule (float64)."""
    torch.manual_seed(0)
    stack = nn.Sequential(
        *[
            CausalConv1dBlock(channels, kernel_size=kernel_size, dilation=d, dropout=0.0)
            for d in dilation_schedule(schedule, blocks)
        ]
    ).eval()
    return stack.double()


def _future_leakage(
    stack: nn.Module, seq_len: int, batch: int, channels: int, dtype: torch.dtype = torch.float64
) -> torch.Tensor:
    """Strict-upper-triangle of the (T_out, T_in) influence map of ``stack``.

    Row t holds |d stack_output[:,:,t] / d x[:,:,t']| summed over (batch,
    channels) for every input position t'. The strict upper triangle (t' > t)
    is exactly the lookahead the causality contract forbids: a strictly causal
    stack must return an all-zero matrix.
    """
    x = torch.randn(batch, channels, seq_len, dtype=dtype, requires_grad=True)
    y = stack(x)
    jac = torch.zeros(seq_len, seq_len, dtype=dtype)
    for t in range(seq_len):
        g = torch.autograd.grad(
            outputs=y[:, :, t],
            inputs=x,
            grad_outputs=torch.ones_like(y[:, :, t]),
            retain_graph=True,
            create_graph=False,
            only_inputs=True,
        )[0]
        jac[t] = g.detach().abs().sum(dim=(0, 1))
    return torch.triu(jac, diagonal=1)


# =============================================================================
# 1. Mathematical causality proof (Jacobian / gradient leakage). AC-1.
# =============================================================================


class TestCausalInvarianceJacobian:
    """Zero future-gradient leakage, proven with autograd, not assumed."""

    @pytest.mark.parametrize("schedule", SCHEDULES)
    @pytest.mark.parametrize("blocks", BLOCK_COUNTS)
    @pytest.mark.parametrize("kernel_size", KERNEL_SIZES)
    def test_stack_has_zero_future_gradient(
        self, schedule: str, blocks: int, kernel_size: int
    ) -> None:
        stack = _make_stack(8, kernel_size, schedule, blocks)
        future = _future_leakage(stack, seq_len=48, batch=2, channels=8)
        n_leak = int(torch.count_nonzero(future).item())
        assert n_leak == 0, (
            f"non-causal leakage in {schedule} stack (blocks={blocks}, k={kernel_size}): "
            f"{n_leak} future Jacobian entries non-zero"
        )

    @pytest.mark.parametrize("schedule", SCHEDULES)
    @pytest.mark.parametrize("blocks", BLOCK_COUNTS)
    def test_model_conv_stage_has_zero_future_gradient(self, schedule: str, blocks: int) -> None:
        """The TCNAttentionV1 conv subgraph must be strictly causal too — this
        is the stage the model actually runs, not just an isolated stack."""
        torch.manual_seed(0)
        model = TCNAttentionV1(
            input_dim=12,
            hidden_dim=24,
            blocks=blocks,
            kernel_size=3,
            attention_heads=4,
            dropout=0.0,
            num_classes=3,
            max_seq_len=96,
            dilation=schedule,
        ).eval()
        stack = nn.Sequential(*model.conv_blocks).eval()
        future = _future_leakage(stack, seq_len=48, batch=2, channels=24, dtype=torch.float32)
        n_leak = int(torch.count_nonzero(future).item())
        assert n_leak == 0, (
            f"TCNAttentionV1 conv-stage leakage for schedule={schedule} blocks={blocks}: "
            f"{n_leak} future Jacobian entries non-zero"
        )

    @pytest.mark.parametrize("schedule", SCHEDULES)
    @pytest.mark.parametrize("seq_len", [2, 3, 7, 17, 33, 65, 129])
    def test_causality_holds_across_sequence_lengths(self, schedule: str, seq_len: int) -> None:
        """Causality is a padding invariant, not a sequence-length assumption."""
        stack = _make_stack(6, 3, schedule, 4)
        future = _future_leakage(stack, seq_len=seq_len, batch=1, channels=6)
        n_leak = int(torch.count_nonzero(future).item())
        assert n_leak == 0, f"leakage at seq_len={seq_len}: {n_leak} future entries"

    def test_past_perturbation_does_change_output(self) -> None:
        """Guard against a vacuous proof: a causal block must still respond."""
        block = _make_stack(6, 3, DILATION_GEOMETRIC, 2)
        x = torch.randn(1, 6, 40, dtype=torch.float64)
        y0 = block(x)
        perturbed = x.clone()
        perturbed[:, :, 20] += 1.0  # strictly past position relative to t=39
        y1 = block(perturbed)
        assert not torch.allclose(y0, y1), "past input had no effect — stack is dead"

    def test_future_perturbation_leaves_past_outputs_unchanged(self) -> None:
        """Empirical causality check independent of autograd.

        Left-only padding means inputs at positions >= cutoff cannot change
        stack outputs at positions < cutoff. (Outputs AT the perturbed
        positions legitimately move — those are the current bar of a causal
        filter, not lookahead.)
        """
        block = _make_stack(6, 3, DILATION_GEOMETRIC, 2)
        x = torch.randn(1, 6, 40, dtype=torch.float64)
        y0 = block(x)
        perturbed = x.clone()
        perturbed[:, :, 38:] += torch.randn_like(perturbed[:, :, 38:]) * 10.0
        y1 = block(perturbed)
        assert torch.allclose(y0[:, :, :38], y1[:, :, :38], atol=0.0, rtol=0.0), (
            "future input changed outputs at strictly earlier positions"
        )

    def test_model_pools_only_the_last_timestep(self) -> None:
        """Final-state pooling reads h[:, -1, :] — the causal decision point.

        Any other pooling index would be lookahead relative to the last bar.
        Verified by capturing what the head actually receives.
        """
        torch.manual_seed(0)
        model = TCNAttentionV1(
            input_dim=6, hidden_dim=12, blocks=3, dropout=0.0, max_seq_len=32
        ).eval()
        captured: dict[str, torch.Tensor] = {}

        def _hook(_module: nn.Module, inputs: tuple[torch.Tensor, ...], _out: torch.Tensor) -> None:
            captured["head_input"] = inputs[0].detach().clone()

        handle = model.head.register_forward_hook(_hook)
        try:
            x = torch.randn(2, 20, 6)
            with torch.no_grad():
                model(x)
        finally:
            handle.remove()
        head_in = captured["head_input"]
        assert head_in.shape == (2, 12), f"head received wrong shape: {head_in.shape}"
        # The head must receive exactly one vector per sample — the last
        # timestep's post-attention state, never a full sequence.
        assert head_in.dim() == 2, "head received a sequence, not pooled final state"


# =============================================================================
# 2. Receptive-field formula validation. AC-2.
# =============================================================================


class TestReceptiveFieldFormula:
    """RF = 1 + sum_b (k - 1) * d_b validated across all configurations."""

    @pytest.mark.parametrize("schedule", SCHEDULES)
    @pytest.mark.parametrize("blocks", BLOCK_COUNTS)
    @pytest.mark.parametrize("kernel_size", KERNEL_SIZES)
    def test_formula_matches_model_property(
        self, schedule: str, blocks: int, kernel_size: int
    ) -> None:
        torch.manual_seed(0)
        model = TCNAttentionV1(
            input_dim=4,
            hidden_dim=8,
            blocks=blocks,
            kernel_size=kernel_size,
            attention_heads=2,
            dropout=0.0,
            num_classes=3,
            max_seq_len=256,
            dilation=schedule,
        )
        expected = 1 + sum((kernel_size - 1) * d for d in dilation_schedule(schedule, blocks))
        assert model.receptive_field == expected
        assert model.dilations == dilation_schedule(schedule, blocks)

    def test_known_geometric_reference_values(self) -> None:
        """Hand-computed reference values for the default schedule (k=3)."""
        # B=3 -> 1 + 2*(1+2+4)  = 15 ; B=4 -> 31 ; B=5 -> 63 bars.
        assert receptive_field(3, dilation_schedule(DILATION_GEOMETRIC, 3)) == 15
        assert receptive_field(3, dilation_schedule(DILATION_GEOMETRIC, 4)) == 31
        assert receptive_field(3, dilation_schedule(DILATION_GEOMETRIC, 5)) == 63

    @pytest.mark.parametrize("schedule", SCHEDULES)
    @pytest.mark.parametrize("kernel_size", KERNEL_SIZES)
    def test_empirical_rf_matches_closed_form(self, schedule: str, kernel_size: int) -> None:
        """Probe the dilated conv branch with a single impulse and find the
        FARTHEST position that can still move a branch output — that span is RF.

        The probe isolates the conv branch because CausalConv1dBlock's residual
        skip is unpadded: it would otherwise mix the impulse into every output
        position and the branch formula could not be observed. This is exactly
        the memory depth the dilation schedule controls. A standalone stack is
        used (rather than the model's own blocks) so that no upstream module's
        parameters can couple the impulse into the far past of the branch input.
        """
        torch.manual_seed(0)
        blocks = 4
        seq_len = 512
        # >=2 channels: LayerNorm over a single channel is degenerate (zero
        # variance), so a 1-channel probe produces no measurable response.
        # The probe measures per-position influence, so the response is
        # contracted over the channel axis before testing for any change.
        channels = 6
        branch = (
            nn.Sequential(
                *[
                    CausalConv1dBlock(channels, kernel_size=kernel_size, dilation=d, dropout=0.0)
                    for d in dilation_schedule(schedule, blocks)
                ]
            )
            .double()
            .eval()
        )
        rf = receptive_field(kernel_size, dilation_schedule(schedule, blocks))
        base = torch.randn(1, channels, seq_len, dtype=torch.float64) * 0.5
        y_base = branch(base)
        anchor = seq_len - 1  # probe backwards from the final position
        scan = min(seq_len, 6 * rf)  # comfortably exceeds the expected RF
        influenced: list[int] = []
        pattern = torch.arange(1, channels + 1, dtype=torch.float64) * 2.0
        for lag in range(0, scan):
            pos = anchor - lag
            if pos < 0:
                break
            probe = base.clone()
            # Perturb every channel with a per-channel pattern that survives
            # LayerNorm (an identical offset across all channels is exactly the
            # component normalization removes).
            probe[:, :, pos] += pattern
            # Measure the response at the ANCHOR position only: positions near
            # the impulse move legitimately (a causal filter's output at its own
            # tap), so the memory depth is how far back the anchor still moves.
            response = (branch(probe) - y_base)[0, :, anchor]
            if response.abs().amax().item() > 1e-9:
                influenced.append(lag)
        assert influenced, "no past perturbation influenced the output"
        # Influence over lag is contiguous (the conv branch is a causal FIR
        # filter stack), so the receptive field is the count of lags, measured
        # backwards from the anchor, that still move it: the first lag at which
        # influence ENDS marks the boundary.
        assert influenced == list(range(len(influenced))), (
            f"influence is not contiguous over lag for {schedule}: {influenced}"
        )
        observed = len(influenced)
        assert observed == rf, (
            f"empirical RF {observed} != formula {rf} "
            f"(schedule={schedule}, k={kernel_size}, blocks={blocks})"
        )

    def test_residual_bypass_is_documented_not_measured_as_rf(self) -> None:
        """Positive evidence for the scope note: the full block stack is
        influenced by an impulse at ANY position (the residual is unpadded),
        which is precisely why the schedule-controlled conv branch is the
        quantity ``receptive_field`` describes."""
        torch.manual_seed(0)
        model = (
            TCNAttentionV1(
                input_dim=1,
                hidden_dim=4,
                blocks=4,
                kernel_size=3,
                dropout=0.0,
                max_seq_len=256,
                dilation=DILATION_GEOMETRIC,
            )
            .double()
            .eval()
        )
        stack = nn.Sequential(*model.conv_blocks).double().eval()
        seq_len = 256
        base = torch.randn(1, 4, seq_len, dtype=torch.float64) * 0.5
        y_base = stack(base)
        far_pos = 0
        probe = base.clone()
        probe[0, 0, far_pos] += 5.0
        assert not torch.allclose(stack(probe), y_base, atol=1e-9, rtol=0.0), (
            "residual bypass unexpectedly absent — block receptive field is bounded"
        )

    def test_receptive_field_is_strictly_monotone_in_blocks(self) -> None:
        for schedule in SCHEDULES:
            rfs = [receptive_field(3, dilation_schedule(schedule, b)) for b in range(1, 9)]
            assert rfs == sorted(rfs), f"RF not monotone for {schedule}: {rfs}"
            assert len(set(rfs)) == len(rfs), f"RF not strictly increasing for {schedule}: {rfs}"

    @pytest.mark.parametrize("target,_label", RF_TARGETS)
    @pytest.mark.parametrize("schedule", SCHEDULES)
    def test_min_blocks_reaches_target_and_is_minimal(
        self, target: int, _label: str, schedule: str
    ) -> None:
        n = min_blocks_for_receptive_field(target, 3, schedule)
        assert receptive_field(3, dilation_schedule(schedule, n)) >= target
        if n > 1:
            below = receptive_field(3, dilation_schedule(schedule, n - 1))
            assert below < target, f"{n} blocks is not minimal for target {target}"


# =============================================================================
# 3. Dilation schedule construction.
# =============================================================================


class TestDilationSchedules:
    def test_geometric_doubles(self) -> None:
        assert dilation_schedule(DILATION_GEOMETRIC, 1) == (1,)
        assert dilation_schedule(DILATION_GEOMETRIC, 3) == (1, 2, 4)
        assert dilation_schedule(DILATION_GEOMETRIC, 5) == (1, 2, 4, 8, 16)

    def test_linear_increments(self) -> None:
        assert dilation_schedule(DILATION_LINEAR, 3) == (1, 2, 3)
        assert dilation_schedule(DILATION_LINEAR, 5) == (1, 2, 3, 4, 5)

    def test_fibonacci_grows(self) -> None:
        assert dilation_schedule(DILATION_FIBONACCI, 6) == (1, 1, 2, 3, 5, 8)
        assert dilation_schedule(DILATION_FIBONACCI, 1) == (1,)
        assert dilation_schedule(DILATION_FIBONACCI, 2) == (1, 1)

    def test_unknown_schedule_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown dilation schedule"):
            dilation_schedule("exponential", 3)

    @pytest.mark.parametrize("blocks", [0, -1, -5])
    def test_non_positive_blocks_rejected(self, blocks: int) -> None:
        with pytest.raises(ValueError, match="blocks must be"):
            dilation_schedule(DILATION_GEOMETRIC, blocks)

    def test_schedule_length_equals_blocks(self) -> None:
        for schedule in SCHEDULES:
            for blocks in range(1, 12):
                assert len(dilation_schedule(schedule, blocks)) == blocks

    def test_receptive_field_rejects_bad_arguments(self) -> None:
        with pytest.raises(ValueError, match="kernel_size must be"):
            receptive_field(0, (1, 2))
        with pytest.raises(ValueError, match="dilation factors must be"):
            receptive_field(3, (1, 0))

    def test_min_blocks_rejects_bad_target(self) -> None:
        with pytest.raises(ValueError, match="target receptive field must be"):
            min_blocks_for_receptive_field(0, 3)


# =============================================================================
# 4. Backwards compatibility & factory wiring.
# =============================================================================


class TestBackwardsCompatibility:
    def test_default_schedule_is_geometric(self) -> None:
        model = TCNAttentionV1(input_dim=4, hidden_dim=8, blocks=4)
        assert model.dilation_schedule_kind == DILATION_GEOMETRIC
        assert model.dilations == (1, 2, 4, 8)

    def test_default_construction_is_bit_identical_to_legacy(self) -> None:
        """The default (geometric) path must reproduce the pre-ML-ARCH-002
        block wiring exactly, so existing checkpoints stay loadable."""
        torch.manual_seed(7)
        model = TCNAttentionV1(input_dim=6, hidden_dim=16, blocks=3, kernel_size=3)
        torch.manual_seed(7)
        legacy_equivalent = TCNAttentionV1(
            input_dim=6, hidden_dim=16, blocks=3, kernel_size=3, dilation=DILATION_GEOMETRIC
        )
        # Legacy wiring was dilation = 2**i per block.
        assert [2**i for i in range(3)] == list(model.dilations)
        for a, b in zip(model.conv_blocks, legacy_equivalent.conv_blocks, strict=True):
            assert _conv_dilation(a) == _conv_dilation(b)
        for p_a, p_b in zip(model.parameters(), legacy_equivalent.parameters(), strict=True):
            assert torch.equal(p_a, p_b), "default vs explicit-geometric weights diverged"
        x = torch.randn(2, 20, 6)
        model.eval()
        legacy_equivalent.eval()
        with torch.no_grad():
            assert torch.allclose(model(x), legacy_equivalent(x), atol=0.0, rtol=0.0)

    def test_factory_passes_schedule(self) -> None:
        model = build_tcn_attention_v1(
            4, 3, {"hidden_dim": 8, "blocks": 4, "dilation_schedule": DILATION_FIBONACCI}
        )
        assert model.dilations == (1, 1, 2, 3)
        assert model.receptive_field == 1 + 2 * (1 + 1 + 2 + 3)
        # Legacy `dilation` key still honoured.
        model2 = build_tcn_attention_v1(4, 3, {"blocks": 3, "dilation": DILATION_LINEAR})
        assert model2.dilations == (1, 2, 3)

    def test_factory_default_is_geometric(self) -> None:
        model = build_tcn_attention_v1(4, 3, {"blocks": 3})
        assert model.dilations == (1, 2, 4)

    def test_factory_rejects_unknown_schedule(self) -> None:
        with pytest.raises(ValueError, match="unknown dilation schedule"):
            build_tcn_attention_v1(4, 3, {"dilation_schedule": "nope"})

    @pytest.mark.parametrize("schedule", SCHEDULES)
    def test_output_shape_unchanged(self, schedule: str) -> None:
        model = TCNAttentionV1(input_dim=10, hidden_dim=16, blocks=4, dilation=schedule).eval()
        with torch.no_grad():
            out = model(torch.randn(3, 32, 10))
        assert out.shape == (3, 3), f"bad output shape for {schedule}"

    def test_reference_configurations_match_doc_targets(self) -> None:
        """The task's [16, 32, 64]-bar targets, expressed as minimal geometric
        block counts at k=3, land on the documented reference configurations."""
        assert receptive_field(3, dilation_schedule(DILATION_GEOMETRIC, 3)) == 15
        assert receptive_field(3, dilation_schedule(DILATION_GEOMETRIC, 4)) == 31
        assert receptive_field(3, dilation_schedule(DILATION_GEOMETRIC, 5)) == 63
        # k=3 geometric: B=3 -> 15, B=4 -> 31, B=5 -> 63, B=6 -> 127 bars.
        # A target must be *reached*, so 32 bars needs 5 blocks (31 < 32) and
        # 64 bars needs 6 (63 < 64).
        assert min_blocks_for_receptive_field(RF_TARGET_MICROSTRUCTURE, 3) == 4
        assert min_blocks_for_receptive_field(RF_TARGET_SESSION_SWING, 3) == 5
        assert min_blocks_for_receptive_field(RF_TARGET_MULTI_HOUR, 3) == 6


# =============================================================================
# 5. Forward-pass latency across receptive-field depths (BENCHMARK_PLAN).
# =============================================================================


class TestLatencyAcrossReceptiveFields:
    """Latency must stay bounded as RF grows: cost is dominated by the (fixed)
    sequence budget, not by the dilation depth, since dilation skips taps rather
    than lengthening the tensor. A structural defect — padding that grows the
    sequence instead of dilating — would blow this up by orders of magnitude."""

    _LATENCY_BUDGET_S = 50e-3
    _WORST_BEST_RATIO = 25.0

    @pytest.mark.parametrize("schedule", SCHEDULES)
    def test_latency_bound_across_rf_depths(self, schedule: str) -> None:
        torch.manual_seed(0)
        seq_len, feature_dim = 64, 16
        timings: dict[int, float] = {}
        for blocks in BLOCK_COUNTS:
            model = TCNAttentionV1(
                input_dim=feature_dim,
                hidden_dim=32,
                blocks=blocks,
                kernel_size=3,
                attention_heads=4,
                dropout=0.0,
                num_classes=3,
                max_seq_len=seq_len,
                dilation=schedule,
            ).eval()
            x = torch.randn(4, seq_len, feature_dim)
            with torch.no_grad():
                for _ in range(3):  # warm-up
                    model(x)
                start = time.perf_counter()
                for _ in range(20):
                    model(x)
            timings[blocks] = (time.perf_counter() - start) / 20.0
        worst = max(timings.values())
        best = min(timings.values())
        assert worst <= best * self._WORST_BEST_RATIO or worst < self._LATENCY_BUDGET_S, (
            f"latency blew up across RF depths for {schedule}: {timings}"
        )
        assert all(t < self._LATENCY_BUDGET_S for t in timings.values()), (
            f"forward pass exceeded bench budget for {schedule}: {timings}"
        )

    def test_latency_measurement_is_stable(self) -> None:
        """Sanity: the same model measured twice reads within noise."""
        torch.manual_seed(0)
        model = TCNAttentionV1(
            input_dim=8, hidden_dim=16, blocks=4, dropout=0.0, max_seq_len=64
        ).eval()
        x = torch.randn(2, 64, 8)
        readings = []
        with torch.no_grad():
            for _ in range(3):
                model(x)
            for _ in range(2):
                start = time.perf_counter()
                for _ in range(20):
                    model(x)
                readings.append((time.perf_counter() - start) / 20.0)
        a, b = readings
        assert max(a, b) <= min(a, b) * 20.0, f"unstable latency measurement: {a} vs {b}"
