"""
LiveSequenceService — extracted Cluster 4 (Temporal Sequence Contract & Bar Gap).
"""

from __future__ import annotations

import contextlib
from collections import deque
from dataclasses import dataclass


@dataclass
class LiveSequenceState:
    buffer: deque[list[float]]
    seq_len: int
    max_gap_us: int
    last_bar_ts_us: int | None
    gap_invalid: bool


class LiveSequenceService:
    CANONICAL_SEQ_LEN: int = 32
    CANONICAL_MAX_GAP_US: int = 10 * 60 * 1_000_000

    @staticmethod
    def defaults() -> LiveSequenceState:
        return LiveSequenceState(
            buffer=deque(maxlen=64),
            seq_len=32,
            max_gap_us=10 * 60 * 1_000_000,
            last_bar_ts_us=None,
            gap_invalid=False,
        )

    @staticmethod
    def rebind_from_meta(
        state: LiveSequenceState, meta: dict | None, bundle_path: str | None = None
    ) -> None:
        try:
            from nexus_scalp.model_generation.temporal_contract import (
                CANONICAL_MAX_GAP_US,
                CANONICAL_SEQ_LEN,
            )
        except Exception:
            return
        seq_len = None
        max_gap = None
        if isinstance(meta, dict):
            tc = meta.get("temporal_contract")
            if isinstance(tc, dict):
                v = tc.get("seq_len")
                if isinstance(v, int) and v > 0:
                    seq_len = int(v)
                g = tc.get("max_gap_us")
                if isinstance(g, int) and g >= 0:
                    max_gap = int(g)
            if seq_len is None:
                v2 = meta.get("seq_len")
                if isinstance(v2, int) and v2 > 0:
                    seq_len = int(v2)
            if max_gap is None:
                g2 = meta.get("max_gap_us")
                if isinstance(g2, int) and g2 >= 0:
                    max_gap = int(g2)
        if isinstance(seq_len, int) and seq_len >= 2:
            state.seq_len = int(seq_len)
            with contextlib.suppress(Exception):
                old = list(state.buffer)
                state.buffer = deque(old[-int(seq_len) :], maxlen=max(64, int(seq_len)))
        else:
            state.seq_len = int(CANONICAL_SEQ_LEN)
        state.max_gap_us = int(max_gap) if isinstance(max_gap, int) else int(CANONICAL_MAX_GAP_US)

    @staticmethod
    def maybe_build_sequence_tensor(
        state: LiveSequenceState, x_scaled_now: list[float], bar_ts: object = None
    ) -> object | None:
        try:
            import torch as _torch
        except Exception:
            return None
        with contextlib.suppress(Exception):
            if bar_ts is not None:
                ts_us = None
                if hasattr(bar_ts, "timestamp"):
                    ts_us = int(bar_ts.timestamp() * 1_000_000)  # type: ignore[union-attr]
                elif isinstance(bar_ts, int):
                    ts_us = int(bar_ts)
                if ts_us is not None:
                    last = state.last_bar_ts_us
                    if last is not None and ts_us - int(last) > int(state.max_gap_us):
                        state.gap_invalid = True
                        state.buffer.clear()
                    state.last_bar_ts_us = int(ts_us)
        if state.gap_invalid:
            return None
        if state.buffer is None:  # type: ignore[unreachable]
            return None
        state.buffer.append([float(v) for v in x_scaled_now])
        need = int(state.seq_len)
        if len(state.buffer) < need:
            return None
        if len(x_scaled_now) != 70:
            return None
        try:
            arr = _torch.tensor(list(state.buffer)[-need:], dtype=_torch.float32)
            return arr.unsqueeze(0)
        except Exception:
            return None

    @staticmethod
    def note_bar_gap(state: LiveSequenceState, gap_us: int) -> None:
        if int(gap_us) > int(state.max_gap_us):
            state.gap_invalid = True
            state.buffer.clear()
        else:
            state.gap_invalid = False

    @staticmethod
    def reset(state: LiveSequenceState) -> None:
        state.buffer.clear()
        state.gap_invalid = False
        state.last_bar_ts_us = None
