"""Temporal Sequence Contract (FIX #1 + FIX #8).

Single source of truth for the unified TRAIN | OFFLINE INF | LIVE sequence
contract so that TRAIN INPUT = (B, L, 70) and LIVE INPUT = (1, L, 70) have
identical semantics.

Current defect (FIX #1): TCNAttentionV1 supports (B, T, 70) but
live_engine._infer_probabilities in live_engine.py:5187 built (1, 70)
=> ScalpNet unsqueeze(1) => MLP path seq_len=1, temporal half dead.
This module reuses the EXISTING SequenceBuilder (model_generation/sequence.py)
and TCNAttentionV1 (model_generation/architectures.py). No second builder.

Contract
--------
Sequence length L:
    L = 16 (SequenceBuilder default) OR L = 32 (F2 experiments).
    CONTRACT supports both; default CANONICAL_SEQ_LEN = 32. The chosen L is
    recorded in model.meta.json (seq_len) and in the inference contract so
    TRAIN | OFFLINE | LIVE agree. One value per artifact; do NOT mix.

Causal history:
    Every sequence window [i-L+1 .. i] is strictly historical (causal) with
    label = label of i (the LAST timestep). Purged Triple-Barrier provides the
    label; the training split drops windows that straddle a fold boundary
    (purge_gap_bars = 15 + embargo_bars = 15 so that label horizon=15 never
    leaks across folds). Live keeps the same window anchored at the most
    recent completed M1 bar.

HTF history:
    H1 (60m), H4 (240m), D1 (1440m) liquidity evidence over COMPLETED buckets
    only — a bucket contributes only when its end time <= decision_at
    (the still-forming candle is excluded). This is implemented inside
    feature/liquidity_engine.htf_liquidity_score and is already wired
    through schema_v2.compute_70d_frame and liquidity_runtime.

Gap handling (FIX #8):
    SequenceBuilder.max_gap_us = 10 * 60 * 1_000_000 (10 minutes).
    Any window containing an inter-bar gap > max_gap_us is marked
    valid=False. Training drops invalid windows; offline inference skips them
    (they never enter a fold); live inference refuses to form a sequence that
    would straddle a gap (falls back to cold-start 2D MLP with logging rather
    than fabricating continuity). Rolling features / HTF pools / labeling /
    OOS are all gap-gated as documented in docs/forensics/gap_handling_report.md.

Usage
-----
    Dataset generation:
        builder = get_canonical_sequence_builder(seq_len=32, max_gap_us=...)
        seq = builder.build(frame, news_enabled=...)

    Training / offline inference:
        Pass seq_len through model.meta.json; validate with
        validate_sequence_tensor_shape(x, expected_seq_len=L, expected_dim=70).

    Live inference:
        Maintain a bounded deque[L] of post-scaler 70D vectors; when len(deque) < L
        return the 2D cold-start path (honest fallback); otherwise stack deque
        into (1, L, 70) and call the same TCNAttentionV1 forward path as training.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import torch

if TYPE_CHECKING:  # runtime import is lazy (below) to avoid an import cycle:
    # sequence.py aliases this module's CANONICAL_* constants at import time.
    from nexus_scalp.model_generation.sequence import SequenceBuilder

# ---------------------------------------------------------------------------
# Canonical contract constants — SINGLE SOURCE OF TRUTH (TASK ARCH-SEQ-UNIFY)
# ---------------------------------------------------------------------------
#
# THIS module owns the canonical temporal contract values. sequence.py holds
# NO duplicate definitions: its SEQUENCE_LENGTH / MAX_GAP_US / FEATURE_DIM /
# SCHEMA_ID / SequenceContract defaults are frozen ALIASES re-exported from
# here (backward-compatible names, one direction only). Do not add a second
# literal for L / gap anywhere in the package; import from this module.
#
#: Canonical 70D feature dimension (scalp_v3).
FEATURE_DIM: int = 70

#: Canonical sequence lengths. SequenceBuilder defaults to 16; F2 experiments
#: used L=32. Both are valid per-artifact values; the artifact's meta
#: records the actual choice. CONTRACT default is 32.
CANONICAL_SEQ_LEN_DEFAULT_16: int = 16
CANONICAL_SEQ_LEN_DEFAULT_32: int = 32
CANONICAL_SEQ_LEN: int = CANONICAL_SEQ_LEN_DEFAULT_32

#: Canonical maximum inter-bar gap: 10 minutes (microseconds).
#: Windows containing a gap > this are invalid (valid=False).
CANONICAL_MAX_GAP_US: int = 10 * 60 * 1_000_000

#: Canonical purge + embargo (bars) for fold-boundary safety.
#: Label horizon of the purged Triple-Barrier is 15 bars; purge and embargo
#: are each 15 so no sequence straddles a fold boundary.
CANONICAL_PURGE_BARS: int = 15
CANONICAL_EMBARGO_BARS: int = 15

#: Canonical HTF timeframe buckets (minutes) used by liquidity.
#: Matches liquidity_engine.HTF_TIMEFRAMES_MIN.
CANONICAL_HTF_TIMEFRAMES_MIN: tuple[int, ...] = (60, 240, 1440)

#: Feature schema the 70D contract binds to (features/schema_contract.py).
CANONICAL_SCHEMA_ID: str = "scalp_v3"


# ---------------------------------------------------------------------------
# Builder factory
# ---------------------------------------------------------------------------


def get_canonical_sequence_builder(
    seq_len: int = CANONICAL_SEQ_LEN,
    max_gap_us: int | None = CANONICAL_MAX_GAP_US,
) -> SequenceBuilder:
    """Returns a canonical SequenceBuilder reusing the existing implementation.

    Do NOT invent a second builder — this just parameterizes the existing one
    through ONE place so TRAIN | OFFLINE | LIVE cannot diverge.
    """
    from nexus_scalp.model_generation.sequence import SequenceBuilder  # lazy: break import cycle

    return SequenceBuilder(seq_len=int(seq_len), max_gap_us=max_gap_us)


def validate_sequence_tensor_shape(
    x: torch.Tensor | np.ndarray,
    *,
    expected_seq_len: int = CANONICAL_SEQ_LEN,
    expected_dim: int = FEATURE_DIM,
) -> None:
    """Asserts x has the canonical 3D shape (B, L, 70) with honest errors."""
    if x is None:
        raise ValueError("temporal_contract: tensor is None — expected (B,L,70)")
    shape = tuple(x.shape)  # type: ignore[union-attr]
    if len(shape) != 3 or shape[1] != int(expected_seq_len) or shape[2] != int(expected_dim):
        raise ValueError(
            f"temporal_contract: expected shape (B,{expected_seq_len},{expected_dim})"
            f" but got {shape} — TRAIN vs LIVE dimensional mismatch"
        )


# ---------------------------------------------------------------------------
# Convenience: sequence length declared by a model artifact's meta.json
# ---------------------------------------------------------------------------


def meta_declared_seq_len(meta: dict[str, Any] | None) -> int | None:
    """Returns the artifact-declared seq_len (or trained_mode seq hint) or None.

    When present, the inference path must use this value; otherwise fall back
    to CANONICAL_SEQ_LEN with logging.
    """
    if meta is None:
        return None
    for key in ("seq_len", "sequence_length", "window", "L"):
        v = meta.get(key)
        if isinstance(v, int) and v > 0:
            return int(v)
    # also check nested temporal_contract block
    tc = meta.get("temporal_contract")
    if isinstance(tc, dict):
        v = tc.get("seq_len")
        if isinstance(v, int) and v > 0:
            return int(v)
    tm = meta.get("trained_mode")
    if isinstance(tm, str) and tm.startswith("sequence"):
        # e.g. "sequence_L32" — parse suffix
        try:
            return int(tm.split("_")[-1].replace("L", ""))
        except Exception:
            pass
    return None
