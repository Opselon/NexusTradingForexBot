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
    # TRAIN/SERVE PARITY GATE (P0 2026-09-09): the artifact's DECLARED serving
    # mode. Only sequence-trained artifacts ("sequence"/"sequence_L*") may
    # consume the (1, L, 70) TCN+attention path; every canonical
    # WalkForwardTrainer artifact trains the 2D MLP path on per-row vectors, so
    # the default is "2d" and the sequence tensor is then NEVER built (a
    # 2D-trained checkpoint must never be served through untrained temporal
    # weights).
    trained_mode: str = "2d"


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
            trained_mode="2d",
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
        # TRAIN/SERVE PARITY GATE: trained_mode binds the SERVING path to the
        # artifact's DECLARED training geometry. "sequence*" enables the 3D
        # path; anything else (2d/absent/unknown) pins the 2D MLP path.
        mode = ""
        if isinstance(meta, dict):
            tm = meta.get("trained_mode")
            if isinstance(tm, str):
                mode = tm.strip().lower()
        state.trained_mode = "sequence" if mode.startswith("sequence") else "2d"
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
        # TRAIN/SERVE PARITY GATE (P0 2026-09-09): 2D-trained artifacts NEVER
        # get a sequence tensor — live must run the path training optimized.
        if state.trained_mode != "sequence" or not str(state.trained_mode).startswith("sequence"):
            return None
        try:
            import torch as _torch
        except Exception:
            return None
        # GAP INVALIDATION (enforced): the bar timestamp is REQUIRED for buffer
        # writes (bar-aligned window, not a tick window). A >max_gap_us interval
        # between consecutive bars clears the buffer and invalidates the window
        # (honest None -> 2D fallback), matching the dataset-side gap contract.
        if bar_ts is None:
            return None
        ts_us: int | None = None
        if hasattr(bar_ts, "timestamp"):
            ts_us = int(bar_ts.timestamp() * 1_000_000)  # type: ignore[union-attr]
            # REPLAY-04 BAR-ALIGNMENT FLOOR: the live caller passes fv's RAW
            # TICK timestamp (sub-minute precision), but this window is
            # bar-aligned — one entry per completed M1 bar, dedupe on the
            # same bar, gaps measured bar-to-bar. Floor datetime inputs to
            # their containing minute so intra-bar ticks collapse onto the
            # bar stamp instead of injecting phantom rows / splitting gap
            # detection. Raw-int inputs are treated as ALREADY bar-aligned
            # (synthetic-test / pre-floored callers).
            ts_us -= ts_us % 60_000_000
        elif isinstance(bar_ts, int):
            ts_us = int(bar_ts)
        if ts_us is None:
            return None
        last = state.last_bar_ts_us
        if last is not None and ts_us - int(last) > int(state.max_gap_us):
            state.gap_invalid = True
            state.buffer.clear()
        # RE-ARM ON FIRST FRESH BAR (REPLAY-01 dataset parity): gap_invalid is a
        # ONE-WINDOW quarantine, never sticky. Whether the gap was detected on
        # this bar (above) or flagged earlier via note_bar_gap(), the first bar
        # that is NOT an intra-bar duplicate re-arms the window so the buffer
        # refills bar-by-bar and post-gap windows rebuild exactly like
        # SequenceBuilder (which re-validates windows fully after the boundary).
        # The old sticky-until-reset() semantics permanently starved the live
        # serving path (reset() has no production caller -> every window after
        # one gap returned None forever).
        if state.gap_invalid and (last is None or ts_us != int(last)):
            state.gap_invalid = False
        # BAR-ALIGNED WINDOW: ticks carrying the SAME bar timestamp as the
        # last buffered entry update nothing (one entry per completed M1 bar;
        # intra-bar ticks must not fill the window).
        if last is not None and ts_us == int(last):
            need = int(state.seq_len)
            if len(state.buffer) >= need:
                try:
                    arr = _torch.tensor(list(state.buffer)[-need:], dtype=_torch.float32)
                    return arr.unsqueeze(0)
                except Exception:
                    return None
            return None
        state.last_bar_ts_us = int(ts_us)
        # STICKY GATE REMOVED (REPLAY-01 starvation fix): the old
        # `if state.gap_invalid: return None` here kept every post-gap window
        # returning None forever (the flag was re-armed only by reset(), which
        # has no production caller). The flag is re-armed above on the first
        # fresh bar, so it can never be True at this point. The boundary bar's
        # vector is appended like any fresh bar: the first post-gap window then
        # spans exactly the L bars after the hole, matching SequenceBuilder,
        # whose first valid post-gap window includes the boundary row.
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
        """Bar-cadence gap ledger (completed M1 bars only).

        A gap beyond max_gap_us invalidates the window (buffer cleared; the
        next sequence build returns None until the buffer refills bar-by-bar).
        Within-window gaps re-arm the flag so a transient gap cannot poison
        every future window forever (the old flag was sticky until reset(),
        which no production caller invoked).
        """
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
