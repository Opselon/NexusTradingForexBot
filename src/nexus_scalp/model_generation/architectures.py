"""MLFIX-T4 MODEL CLASS CONTRACT SSoT (PHI):

LABEL_SCHEMA_3CLASS_V1.class_count = 3 (NO_TRADE / BUY_MARKET / SELL_MARKET).
CANONICAL_CLASS_COUNT below is the SOOT for all model head size decisions.
Any factory, trainer, runtime, or integrity gate that derives a head width must
read it from here or from a declared manifest field — never re-define its
own literal 3 or 4. The legacy ScalpNet serving path keeps a 4-wide head for
backward compat, but the CANONICAL contract for fresh builds is 3-class unless
a legacy meta declares 4. WAIT (class index 3) is a policy-derived state and
never a training label.

TCN_ATTENTION_V1 — First New Architecture Benchmark Candidate (PHASE 13B).

A dedicated causal-temporal model that competes FAIRLY with the legacy
ScalpNet baseline under identical data/labels/splits/purge/embargo/friction.

Design (spec 4 / 5 / 6 of the benchmark task):

    INPUT (B, T, F)
        -> Linear projection
        -> N dilated CAUSAL convolution blocks (residual + LayerNorm)
        -> Multi-head self-attention (temporal representation)
        -> final-state pooling (last timestep)
        -> classification head -> 3 logits

Invariants:
    * STRICTLY CAUSAL: dilated causal padding means output at t sees only
      inputs <= t. No future timestep access.
    * Deterministic initialization under a configured seed (caller sets
      torch.manual_seed before construction).
    * Bounded parameter count; hidden_dim / blocks / heads / dropout all
      configurable with explicit architecture version.
    * THE HEAD IS ALWAYS 3 LOGITS (NO_TRADE/BUY/SELL). WAIT is a policy
      state, never a neural output of the new architecture (unlike the
      legacy 4-head bridge).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import torch
import torch.nn.functional as F
from torch import nn

ARCHITECTURE_VERSION = "1.0.0"

CANONICAL_CLASS_COUNT = 3
CANONICAL_CLASSES = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]

# ---------------------------------------------------------------------------
# Dilation schedules (ML-ARCH-002).
#
# A dilation schedule is the ordered tuple of per-block dilation factors of a
# causal conv stack. `geometric` (1, 2, 4, ...) is the historical default and
# its behaviour is byte-for-byte unchanged here; `linear`/`fibonacci` trade
# receptive-field growth rate against effective parameter sharing.
# ---------------------------------------------------------------------------
DILATION_GEOMETRIC = "geometric"
DILATION_LINEAR = "linear"
DILATION_FIBONACCI = "fibonacci"

_DILATION_ALIASES = (DILATION_GEOMETRIC, DILATION_LINEAR, DILATION_FIBONACCI)

# Named M1 market-memory targets (bars) the schedules are dimensioned against.
RF_TARGET_MICROSTRUCTURE = 16
RF_TARGET_SESSION_SWING = 32
RF_TARGET_MULTI_HOUR = 64


def dilation_schedule(kind: str, blocks: int) -> tuple[int, ...]:
    """Return the ordered dilation factors for ``blocks`` causal conv blocks.

    Args:
        kind: one of ``geometric`` (2**b), ``linear`` (b + 1) or ``fibonacci``.
        blocks: number of blocks; the schedule length equals this.

    Raises:
        ValueError: ``kind`` is unknown or ``blocks`` is not a positive int.
    """
    if kind not in _DILATION_ALIASES:
        raise ValueError(f"unknown dilation schedule {kind!r}; expected one of {_DILATION_ALIASES}")
    if blocks < 1:
        raise ValueError(f"blocks must be >= 1 to build a dilation schedule, got {blocks}")
    if kind == DILATION_GEOMETRIC:
        return tuple(2**b for b in range(blocks))
    if kind == DILATION_LINEAR:
        return tuple(b + 1 for b in range(blocks))
    seq = [1, 1]
    while len(seq) < blocks:
        seq.append(seq[-1] + seq[-2])
    return tuple(seq[:blocks])


def receptive_field(kernel_size: int, dilations: Iterable[int]) -> int:
    """Receptive field of the dilated conv branch of a causal conv stack.

    .. math::  RF = 1 + \\sum_{b} (k - 1) \\cdot d_b

    Each block contributes ``(k - 1) * d_b`` past-side context taps; the +1 is
    the current bar itself. This is the count of input bars that can influence
    one conv-branch output activation, and it is exact because the blocks are
    unit-stride and length-preserving.

    Scope note (verified empirically, ML-ARCH-002): ``CausalConv1dBlock`` adds
    an UNPADDED residual skip, so the *block* output mixes the block input at
    every position and the block receptive field is the full sequence. This
    function therefore describes the receptive field of the DILATED CONV BRANCH
    — the only stage whose memory depth the dilation schedule controls — which
    is the quantity the schedule is designed and dimensioned against.
    """
    k = int(kernel_size)
    if k < 1:
        raise ValueError(f"kernel_size must be >= 1, got {k}")
    total = 1
    for factor in dilations:
        dilation_factor = int(factor)
        if dilation_factor < 1:
            raise ValueError(f"dilation factors must be >= 1, got {dilation_factor}")
        total += (k - 1) * dilation_factor
    return total


def min_blocks_for_receptive_field(
    target_rf: int, kernel_size: int, kind: str = DILATION_GEOMETRIC
) -> int:
    """Smallest block count whose receptive field reaches ``target_rf`` bars.

    The search is bounded and monotone: receptive field grows strictly with
    block count for every supported schedule (each adds ``>= k - 1 >= 1``),
    so the first count meeting the target is minimal.
    """
    if target_rf < 1:
        raise ValueError(f"target receptive field must be >= 1, got {target_rf}")
    for blocks in range(1, 256):
        if receptive_field(kernel_size, dilation_schedule(kind, blocks)) >= target_rf:
            return blocks
    raise ValueError(
        f"no block count <= 255 reaches receptive field {target_rf} "
        f"with kernel_size={kernel_size} schedule={kind}"
    )


class CausalConv1dBlock(nn.Module):
    """Dilated causal conv + GELU + residual + LayerNorm.

    Left-side causal padding only: activation at timestamp t never sees
    data from t+1.. (strict causal temporal processing).
    """

    def __init__(
        self,
        channels: int,
        kernel_size: int = 3,
        dilation: int = 1,
        dropout: float = 0.15,
    ) -> None:
        super().__init__()
        self.padding = (kernel_size - 1) * dilation
        self.conv = nn.Conv1d(
            channels,
            channels,
            kernel_size=kernel_size,
            dilation=dilation,
            padding=0,
        )
        self.norm = nn.LayerNorm(channels)
        self.dropout = nn.Dropout(dropout)
        self.gelu = nn.GELU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B, C, T)  ->  (B, C, T) causally."""
        x_padded = F.pad(x, (self.padding, 0))
        h = self.conv(x_padded)
        h = self.gelu(h)
        h = h.transpose(1, 2)
        h = self.norm(h).transpose(1, 2)
        h = self.dropout(h)
        return x + h  # residual


class TCNAttentionV1(nn.Module):
    """Causal TCN + self-attention scalping model, 3-class head.

    Input:  (B, T, F) float tensor (sequence-length x feature-dim).
    Output: (B, 3) logits (NO_TRADE / BUY_MARKET / SELL_MARKET).
    """

    def __init__(
        self,
        input_dim: int = 50,
        hidden_dim: int = 128,
        blocks: int = 3,
        kernel_size: int = 3,
        attention_heads: int = 4,
        dropout: float = 0.15,
        num_classes: int = 3,
        max_seq_len: int = 64,
        dilation: str = DILATION_GEOMETRIC,
    ) -> None:
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.blocks = blocks
        self.num_classes = num_classes
        self.kernel_size = kernel_size
        self.dilation_schedule_kind = dilation
        self._dilations = dilation_schedule(self.dilation_schedule_kind, blocks)

        self.projection = nn.Linear(input_dim, hidden_dim)
        self.proj_norm = nn.LayerNorm(hidden_dim)

        # Dilated causal convolution blocks (schedule-controlled dilation).
        self.conv_blocks = nn.ModuleList(
            [
                CausalConv1dBlock(
                    hidden_dim,
                    kernel_size=kernel_size,
                    dilation=d,
                    dropout=dropout,
                )
                for d in self._dilations
            ]
        )

        self.attention = nn.MultiheadAttention(
            embed_dim=hidden_dim,
            num_heads=attention_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.attn_norm = nn.LayerNorm(hidden_dim)

        # Optional learned position embedding (bounded by max_seq_len).
        self.pos_embedding = nn.Parameter(torch.zeros(1, max_seq_len, hidden_dim))
        nn.init.normal_(self.pos_embedding, std=0.02)

        self.head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, num_classes),
        )

    @property
    def dilations(self) -> tuple[int, ...]:
        """Ordered per-block dilation factors actually used by this model."""
        return self._dilations

    @property
    def receptive_field(self) -> int:
        """Receptive field of the dilated conv branch, in input bars.

        ``1 + sum((k - 1) * d_b)`` over the realized dilation schedule. This is
        the memory depth the dilation schedule controls; because
        ``CausalConv1dBlock`` adds an unpadded residual, the whole-stack memory
        depth is the full sequence (see ``receptive_field`` scope note).
        """
        return receptive_field(self.kernel_size, self._dilations)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B, T, F). Returns (B, C) logits (no softmax)."""
        _, t, _ = x.shape
        h = self.proj_norm(self.projection(x))
        h = h.transpose(1, 2)  # (B, H, T)
        for block in self.conv_blocks:
            h = block(h)
        h = h.transpose(1, 2)  # (B, T, H)

        # positional encoding (truncate for short sequences)
        h = h + self.pos_embedding[:, :t, :]
        attn_out, _ = self.attention(h, h, h, need_weights=False)
        h = self.attn_norm(h + attn_out)

        # final-state pooling: use the LAST timestep (causal, no leakage)
        h_last = h[:, -1, :]
        return self.head(h_last)


def build_tcn_attention_v1(
    input_dim: int, num_classes: int, params: dict[str, Any]
) -> TCNAttentionV1:
    """Config-driven constructor for the ModelFactory registry."""
    return TCNAttentionV1(
        input_dim=input_dim,
        hidden_dim=int(params.get("hidden_dim", 128)),
        blocks=int(params.get("blocks", 3)),
        kernel_size=int(params.get("kernel_size", 3)),
        attention_heads=int(params.get("attention_heads", 4)),
        dropout=float(params.get("dropout", 0.15)),
        num_classes=num_classes,
        max_seq_len=int(params.get("max_seq_len", 64)),
        dilation=str(params.get("dilation_schedule", params.get("dilation", DILATION_GEOMETRIC))),
    )
