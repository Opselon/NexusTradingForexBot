"""Characterization for LiveSequence temporal contract.

Updated for the TRAIN/SERVE PARITY gate (2026-09-09): the sequence tensor is
built ONLY for sequence-trained artifacts ("sequence") and only from BAR
timestamps — the old tick-fed, mode-less behavior was the P0 defect (a
2D-trained champion served through the never-trained TCN/attention path).
"""

from collections import deque

from nexus_scalp.application.live_sequence import LiveSequenceService, LiveSequenceState


def _seq_state(seq_len: int = 4) -> LiveSequenceState:
    return LiveSequenceState(
        buffer=deque(maxlen=64),
        seq_len=seq_len,
        max_gap_us=LiveSequenceService.CANONICAL_MAX_GAP_US,
        last_bar_ts_us=None,
        gap_invalid=False,
        trained_mode="sequence",
    )


class TestLiveSequenceContract:
    def test_defaults(self):
        st = LiveSequenceService.defaults()
        assert st.seq_len == 32
        assert st.gap_invalid is False
        assert st.last_bar_ts_us is None

    def test_build_returns_none_until_seq_len(self):
        st = LiveSequenceService.defaults()
        st.seq_len = 4
        for _ in range(3):
            assert LiveSequenceService.maybe_build_sequence_tensor(st, [0.0] * 70) is None

    def test_build_returns_none_on_wrong_dim(self):
        st = LiveSequenceService.defaults()
        st.seq_len = 2
        LiveSequenceService.maybe_build_sequence_tensor(st, [0.0] * 70)
        assert LiveSequenceService.maybe_build_sequence_tensor(st, [0.0] * 50) is None

    def test_build_succeeds_at_seq_len(self):
        # sequence-trained artifact + bar timestamps => (1, L, 70) tensor
        st = _seq_state(seq_len=2)
        LiveSequenceService.maybe_build_sequence_tensor(
            st, [0.0] * 70, bar_ts=__import__("datetime").datetime(2026, 9, 9, 12, 0)
        )
        t2 = LiveSequenceService.maybe_build_sequence_tensor(
            st, [0.1] * 70, bar_ts=__import__("datetime").datetime(2026, 9, 9, 12, 1)
        )
        assert t2 is not None
        assert tuple(t2.shape) == (1, 2, 70)

    def test_2d_default_never_builds(self):
        # THE PARITY GATE: mode-less (2D-trained) artifact => never a sequence.
        st = LiveSequenceService.defaults()
        st.seq_len = 2
        for m in range(4):
            out = LiveSequenceService.maybe_build_sequence_tensor(
                st,
                [0.1] * 70,
                bar_ts=__import__("datetime").datetime(2026, 9, 9, 12, m),
            )
        assert out is None

    def test_gap_invalid_blocks_build(self):
        st = LiveSequenceService.defaults()
        st.seq_len = 1
        st.gap_invalid = True
        assert LiveSequenceService.maybe_build_sequence_tensor(st, [0.0] * 70) is None

    def test_note_bar_gap_invalidates(self):
        st = LiveSequenceService.defaults()
        LiveSequenceService.note_bar_gap(st, st.max_gap_us + 1)
        assert st.gap_invalid is True

    def test_note_bar_gap_within_window_clears(self):
        st = LiveSequenceService.defaults()
        st.gap_invalid = True
        LiveSequenceService.note_bar_gap(st, 0)
        assert st.gap_invalid is False

    def test_reset_clears(self):
        st = LiveSequenceService.defaults()
        st.buffer.append([0.0] * 70)
        st.gap_invalid = True
        st.last_bar_ts_us = 123
        LiveSequenceService.reset(st)
        assert len(st.buffer) == 0
        assert st.gap_invalid is False
        assert st.last_bar_ts_us is None

    def test_rebind_from_meta(self):
        st = LiveSequenceService.defaults()
        LiveSequenceService.rebind_from_meta(
            st, {"temporal_contract": {"seq_len": 16, "max_gap_us": 999}}
        )
        assert st.seq_len == 16
        assert st.max_gap_us == 999

    def test_rebind_falls_back_to_canonical(self):
        st = LiveSequenceService.defaults()
        LiveSequenceService.rebind_from_meta(st, None)
        assert st.seq_len == LiveSequenceService.CANONICAL_SEQ_LEN
        assert st.max_gap_us == LiveSequenceService.CANONICAL_MAX_GAP_US

    def test_bar_ts_gap_detection(self):
        # REPLAY-01 re-arm semantics: a >max_gap_us jump flags gap_invalid and
        # clears the window; the first FRESH bar after the jump re-arms the
        # window (gap_invalid False) and refills the buffer bar-by-bar —
        # matching the dataset-side SequenceBuilder, which re-validates windows
        # fully after the hole.
        st = _seq_state(seq_len=2)
        st.last_bar_ts_us = 0
        st.max_gap_us = 1000
        # gap jump: flag set, window cleared, boundary bar not buffered yet
        LiveSequenceService.maybe_build_sequence_tensor(st, [0.0] * 70, bar_ts=10_000_000)
        assert st.gap_invalid is False  # re-armed on this very bar (fresh bar)
        assert st.last_bar_ts_us == 10_000_000
        assert len(st.buffer) == 1  # boundary bar refills the window
        # a second bar at normal cadence extends the window (len 2 -> valid)
        LiveSequenceService.maybe_build_sequence_tensor(st, [0.1] * 70, bar_ts=10_001_000)
        assert st.gap_invalid is False
        assert len(st.buffer) == 2
