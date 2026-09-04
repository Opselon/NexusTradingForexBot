"""Characterization for LiveSequence temporal contract."""

from nexus_scalp.application.live_sequence import LiveSequenceService


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
        st = LiveSequenceService.defaults()
        st.seq_len = 2
        LiveSequenceService.maybe_build_sequence_tensor(st, [0.0] * 70)
        t = LiveSequenceService.maybe_build_sequence_tensor(st, [0.1] * 70)
        assert t is not None
        assert tuple(t.shape) == (1, 2, 70)

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
        st = LiveSequenceService.defaults()
        st.last_bar_ts_us = 0
        st.max_gap_us = 1000
        LiveSequenceService.maybe_build_sequence_tensor(st, [0.0] * 70, bar_ts=10_000_000)
        assert st.gap_invalid is True
        assert len(st.buffer) == 0
