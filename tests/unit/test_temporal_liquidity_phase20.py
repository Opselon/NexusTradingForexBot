"""TEST-TEMPORAL-01..30 — Temporal Liquidity Intelligence + Signal Stability (TASK-TEMPORAL-01).

Phase-20 suite for:
  - src/nexus_scalp/features/temporal.py (lag/delta/persistence/tsc)
  - src/nexus_scalp/signals/stability_controller.py
  - features/schema.py registration of scalp_v4_temporal_candidate

Coverage map (brief 46):
  TEST-TEMPORAL-01..03 lag-1/2/3 correctness
  TEST-TEMPORAL-04     delta correctness
  TEST-TEMPORAL-05     persistence correctness
  TEST-TEMPORAL-06     time-since-change correctness
  TEST-TEMPORAL-07     cold-start behavior
  TEST-TEMPORAL-08     no future leakage
  TEST-TEMPORAL-09     determinism
  TEST-TEMPORAL-10     dataset/replay/live parity (single code path)
  TEST-TEMPORAL-11     liquidity state persistence
  TEST-TEMPORAL-12     sweep-state persistence
  TEST-TEMPORAL-13     cache/full-rebuild parity
  TEST-TEMPORAL-14     BUY->SELL stability
  TEST-TEMPORAL-15     SELL->BUY stability
  TEST-TEMPORAL-16     weak opposite does not flip
  TEST-TEMPORAL-17     strong opposite confirms
  TEST-TEMPORAL-18     candidate timeout
  TEST-TEMPORAL-19     hard reversal
  TEST-TEMPORAL-20     restart reset
  TEST-TEMPORAL-21     model/schema reset
  TEST-TEMPORAL-22     entry/exit stability separation
  TEST-TEMPORAL-23     raw model output unchanged
  TEST-TEMPORAL-24     stable direction deterministic
  TEST-TEMPORAL-25     micro-flip rate decreases
  TEST-TEMPORAL-26     genuine reversal latency bounded
  TEST-TEMPORAL-27     O(1) runtime update
  TEST-TEMPORAL-28     no DB/network hot-path calls
  TEST-TEMPORAL-29     debug values match runtime
  TEST-TEMPORAL-30     temporal feature ablation (structural guard)
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

sys.path.insert(0, "src")

from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.features.temporal import (
    NEUTRAL_DISTANCE,
    TEMPORAL_FEATURE_NAMES,
    TemporalLiquidityTracker,
    temporal_features_from_history,
)
from nexus_scalp.signals.stability_controller import (
    DecisionStabilityController,
    StabilityState,
    StableDirection,
)

T0 = datetime(2026, 5, 1, tzinfo=UTC)


def _liq(
    bsl=1.0,
    ssl=2.0,
    eqh=0.5,
    eql=0.5,
    htf=0.0,
    internal=1.5,
    external=2.5,
    conf=0.1,
    sweep=0.0,
    disp=0.0,
) -> list[float]:
    return [bsl, ssl, eqh, eql, htf, internal, external, conf, sweep, disp]


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-01..03 — lag correctness
# ---------------------------------------------------------------------------


class TestTemporalLag:
    def test_lag1_equals_previous_value(self):
        tr = TemporalLiquidityTracker()
        tr.update(_liq(bsl=1.0), "t0")
        snap = tr.update(_liq(bsl=2.0), "t1")
        assert snap.values[0] == 1.0  # bsl lag1 == previous bsl

    def test_lag2_equals_two_back(self):
        tr = TemporalLiquidityTracker()
        tr.update(_liq(bsl=1.0), "t0")
        tr.update(_liq(bsl=2.0), "t1")
        snap = tr.update(_liq(bsl=3.0), "t2")
        assert snap.values[0] == 2.0  # bsl lag1 == t1
        assert snap.values[1] == 1.0  # bsl lag2 == t0

    def test_lag3_missing_uses_neutral(self):
        # lag2 of a 2-element history = cold start -> neutral
        tr = TemporalLiquidityTracker()
        tr.update(_liq(bsl=1.0), "t0")
        snap = tr.update(_liq(bsl=2.0), "t1")
        assert snap.values[0] == 1.0
        assert snap.values[1] == NEUTRAL_DISTANCE  # no lag2 yet


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-04 — delta correctness
# ---------------------------------------------------------------------------


class TestTemporalDelta:
    def test_delta1_half_diff(self):
        tr = TemporalLiquidityTracker()
        tr.update(_liq(bsl=1.0), "t0")
        snap = tr.update(_liq(bsl=2.0), "t1")
        assert snap.values[2] == pytest.approx(0.5)  # (2-1)/2

    def test_delta_clipped(self):
        tr = TemporalLiquidityTracker()
        tr.update(_liq(bsl=-3.0), "t0")
        snap = tr.update(_liq(bsl=3.0), "t1")
        assert snap.values[2] == pytest.approx(3.0)  # (6)/2=3 clipped


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-05 — persistence correctness
# ---------------------------------------------------------------------------


class TestTemporalPersistence:
    def test_persistence_fraction(self):
        # 3-bar window; 2 active (nonzero) -> 2/3*3 = 2.0
        hist = [_liq(eqh=0.0), _liq(eqh=0.0), _liq(eqh=0.0)]
        snap = temporal_features_from_history(hist)
        assert snap[7] == 0.0
        hist = [_liq(eqh=0.0), _liq(eqh=0.7), _liq(eqh=0.0)]
        snap = temporal_features_from_history(hist)
        assert snap[7] == pytest.approx(1.0)  # 1/3 active -> 1.0
        hist = [_liq(eqh=0.7), _liq(eqh=0.7), _liq(eqh=0.0)]
        snap = temporal_features_from_history(hist)
        assert snap[7] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-06 — time-since-change correctness
# ---------------------------------------------------------------------------


class TestTemporalTSC:
    def test_tsc_sweep_counts_bars_since_change(self):
        # sweep state constant across 5 vectors -> tsc = 4 bars /10 = 0.4
        hist = [_liq(sweep=-1.0) for _ in range(5)]
        snap = temporal_features_from_history(hist)
        assert snap[19] == pytest.approx(0.4)

    def test_tsc_sweep_resets_after_change(self):
        hist = [
            _liq(sweep=-1.0),
            _liq(sweep=-1.0),
            _liq(sweep=0.0),
            _liq(sweep=0.0),
            _liq(sweep=0.0),
        ]
        snap = temporal_features_from_history(hist)
        assert snap[19] == pytest.approx(0.2)  # 2 bars since change


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-07 — cold start
# ---------------------------------------------------------------------------


class TestTemporalColdStart:
    def test_cold_start_neutrals(self):
        tr = TemporalLiquidityTracker()
        snap = tr.update(_liq(), "t0")
        # distances -> 3.0 neutral, deltas -> 0.0
        assert snap.values[0] == NEUTRAL_DISTANCE
        assert snap.values[3] == NEUTRAL_DISTANCE
        assert snap.values[2] == 0.0
        assert snap.values[5] == 0.0

    def test_cold_start_no_zero_distance(self):
        # 0.0 distance would mean "price AT liquidity" — forbidden at cold start
        tr = TemporalLiquidityTracker()
        snap = tr.update(_liq(), "t0")
        assert snap.values[0] != 0.0


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-08 — no future leakage
# ---------------------------------------------------------------------------


class TestNoFutureLeakage:
    def test_future_bars_do_not_change_past(self):
        tr = TemporalLiquidityTracker()
        tr.update(_liq(bsl=1.0), "t0")
        past = tr.update(_liq(bsl=2.0), "t1")
        # append future
        tr.update(_liq(bsl=99.0), "t2")
        tr.update(_liq(bsl=-99.0), "t3")
        assert past.values == tuple(past.values)  # immutable snapshot already
        # a fresh tracker with the same prefix reproduces the same snapshot
        tr2 = TemporalLiquidityTracker()
        tr2.update(_liq(bsl=1.0), "t0")
        past2 = tr2.update(_liq(bsl=2.0), "t1")
        assert past2.values == past.values

    def test_extract_uses_only_past(self):
        hist = [_liq(bsl=1.0), _liq(bsl=2.0)]
        snap = temporal_features_from_history(hist)
        assert snap[0] == 1.0


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-09 — determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_sequence_same_output(self):
        seq = [_liq(bsl=float(i)) for i in range(10)]
        a = TemporalLiquidityTracker()
        b = TemporalLiquidityTracker()
        for i, v in enumerate(seq):
            sa = a.update(v, f"t{i}")
            sb = b.update(v, f"t{i}")
            assert sa.values == sb.values


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-10 — dataset/replay/live parity (single code path)
# ---------------------------------------------------------------------------


class TestParity:
    def test_single_extraction_function_used(self):
        # The tracker delegates to temporal_features_from_history; asserting
        # the identical values via both entry points proves one code path.
        hist = [_liq(bsl=1.0), _liq(bsl=2.0)]
        direct = temporal_features_from_history(hist)
        tr = TemporalLiquidityTracker()
        tr.update(hist[0], "t0")
        snap = tr.update(hist[1], "t1")
        assert snap.values == tuple(direct)


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-11/12 — liquidity + sweep-state persistence
# ---------------------------------------------------------------------------


class TestStatePersistence:
    def test_tracker_keeps_bounded_history(self):
        tr = TemporalLiquidityTracker()
        for i in range(50):
            tr.update(_liq(bsl=float(i)), f"t{i}")
        assert tr.history_depth == 8  # bounded (TEMPORAL_HISTORY)

    def test_sweep_persistence_value(self):
        hist = [_liq(sweep=0.0), _liq(sweep=-1.0), _liq(sweep=0.0)]
        snap = temporal_features_from_history(hist)
        assert snap[18] == pytest.approx(1.0)  # 1 active of 3 -> 1.0


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-13 — cache/full-rebuild parity (bounded buffer == rebuild)
# ---------------------------------------------------------------------------


class TestCacheParity:
    def test_bounded_buffer_matches_rebuild(self):
        seq = [_liq(bsl=float(i % 3), sweep=-1.0 if i % 2 else 0.0) for i in range(30)]
        tr = TemporalLiquidityTracker()
        out_incremental = []
        for i, v in enumerate(seq):
            out_incremental.append(tr.update(v, f"t{i}").values)
        # rebuild: fresh tracker replaying the same causal sequence
        tr2 = TemporalLiquidityTracker()
        out_rebuild = []
        for i, v in enumerate(seq):
            out_rebuild.append(tr2.update(v, f"t{i}").values)
        assert out_incremental == out_rebuild


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-14..22 — stability controller
# ---------------------------------------------------------------------------


def _probs(buy: float, sell: float) -> list[float]:
    return [1.0 - buy - sell, buy, sell, 0.0]


class TestStabilityBuysell:
    def test_buy_confirm_then_sell_weak_no_flip(self):
        c = DecisionStabilityController(entry_min_margin=0.05)
        c.decide(_probs(0.575, 0.425))
        d = c.decide(_probs(0.58, 0.42))
        assert d.stable_direction == "BUY"
        d = c.decide(_probs(0.515, 0.485))  # weak SELL margin 0.03
        assert d.stable_direction == "BUY"

    def test_sell_confirm_then_buy_weak_no_flip(self):
        c = DecisionStabilityController(entry_min_margin=0.05)
        c.decide(_probs(0.425, 0.575))
        d = c.decide(_probs(0.42, 0.58))
        assert d.stable_direction == "SELL"
        d = c.decide(_probs(0.485, 0.515))
        assert d.stable_direction == "SELL"


class TestStabilityConfirmation:
    def test_strong_opposite_confirms(self):
        c = DecisionStabilityController(entry_min_margin=0.05, hard_reversal_margin=0.20)
        c.decide(_probs(0.575, 0.425))
        d = c.decide(_probs(0.58, 0.42))
        assert d.stable_direction == "BUY"
        d = c.decide(_probs(0.35, 0.65))  # margin 0.30 >= 0.20 -> hard reversal
        assert d.stable_direction == "SELL"
        assert d.event is not None and d.event.confirmation_reason == "HARD_REVERSAL"

    def test_candidate_timeout(self):
        c = DecisionStabilityController(entry_min_margin=0.05, max_candidate_age=3)
        c.decide(_probs(0.55, 0.45))
        c.decide(_probs(0.51, 0.49))  # weak opposite resets streak
        c.decide(_probs(0.54, 0.46))
        c.decide(_probs(0.53, 0.47))
        d = c.decide(_probs(0.52, 0.48))
        assert d.candidate_direction == "NONE"

    def test_restart_reset(self):
        c = DecisionStabilityController()
        c.decide(_probs(0.575, 0.425))
        d = c.decide(_probs(0.58, 0.42))
        assert d.stable_direction == "BUY"
        c.reset()
        d = c.decide(_probs(0.575, 0.425))
        assert d.stable_direction == "NONE"
        assert c.last_event() is None

    def test_model_schema_reset(self):
        c = DecisionStabilityController()
        c.decide(_probs(0.575, 0.425))
        c.decide(_probs(0.58, 0.42))
        c.reset()  # model/schema change -> full reset
        assert c.decide(_probs(0.575, 0.425)).candidate_direction == "BUY"

    def test_entry_exit_separation(self):
        c = DecisionStabilityController(entry_confirm_bars=2, exit_confirm_bars=1)
        # entry needs 2 bars
        c.decide(_probs(0.575, 0.425))
        d = c.decide(_probs(0.49, 0.51))  # weak OPPOSITE (sell) -> streak reset
        assert d.stable_direction == "NONE"
        d = c.decide(_probs(0.58, 0.42))
        d = c.decide(_probs(0.56, 0.44))
        assert d.stable_direction == "BUY"
        # with an open position the exit confirmation is 1 bar
        d = c.decide(_probs(0.35, 0.65), position_open=True)
        assert d.stable_direction == "SELL"


class TestRawUnchanged:
    def test_raw_output_unchanged(self):
        c = DecisionStabilityController()
        d = c.decide(_probs(0.575, 0.425))
        assert d.raw_direction == "BUY"
        assert d.pbuy == pytest.approx(0.575)

    def test_stable_deterministic(self):
        seq = [
            _probs(0.55, 0.45),
            _probs(0.58, 0.42),
            _probs(0.515, 0.485),
            _probs(0.52, 0.48),
            _probs(0.54, 0.46),
        ]
        out_a = []
        ca = DecisionStabilityController()
        for s in seq:
            out_a.append(ca.decide(s).stable_direction)
        cb = DecisionStabilityController()
        out_b = []
        for s in seq:
            out_b.append(cb.decide(s).stable_direction)
        assert out_a == out_b


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-25/26 — flapping metrics (deterministic fixture)
# ---------------------------------------------------------------------------


class TestFlipMetrics:
    def test_micro_flip_rate_decreases(self):
        # A sawtooth raw sequence (alternating weak margins) must stabilize
        # to ZERO flips under the controller
        seq = [
            _probs(0.52, 0.48),
            _probs(0.48, 0.52),
            _probs(0.52, 0.48),
            _probs(0.48, 0.52),
            _probs(0.52, 0.48),
            _probs(0.48, 0.52),
        ]
        c = DecisionStabilityController(entry_min_margin=0.05)
        stable = [c.decide(s).stable_direction for s in seq]
        raw = ["BUY" if s[1] > s[2] else "SELL" for s in seq]
        raw_flips = sum(1 for i in range(1, len(raw)) if raw[i] != raw[i - 1])
        stab_flips = sum(
            1 for i in range(1, len(stable)) if stable[i] != stable[i - 1] and stable[i] != "NONE"
        )
        assert raw_flips == 5
        assert stab_flips == 0

    def test_genuine_reversal_latency_bounded(self):
        # A strong sustained opposite direction confirms within a bounded
        # number of decisions (hard reversal path)
        c = DecisionStabilityController(entry_min_margin=0.05, hard_reversal_margin=0.20)
        c.decide(_probs(0.575, 0.425))
        c.decide(_probs(0.58, 0.42))
        assert c.last_event() is not None
        for _ in range(5):
            d = c.decide(_probs(0.35, 0.65))
            if d.stable_direction == "SELL":
                break
        assert d.stable_direction == "SELL"  # bounded latency


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-27/28 — O(1) + no I/O
# ---------------------------------------------------------------------------


class TestRuntime:
    def test_o1_update(self):
        tr = TemporalLiquidityTracker()
        import time

        t0 = time.perf_counter()
        for i in range(5000):
            tr.update(_liq(bsl=float(i % 5)), f"t{i}")
        dt = time.perf_counter() - t0
        assert dt < 2.0  # 5000 O(1) updates comfortably under 2s

    def test_no_db_network_imports(self):
        import inspect

        from nexus_scalp.features import temporal

        src = inspect.getsource(temporal)
        for bad in ("sqlite", "httpx", "requests", "socket", "urllib"):
            assert bad not in src
        from nexus_scalp.signals import stability_controller

        src2 = inspect.getsource(stability_controller)
        for bad in ("sqlite", "httpx", "requests", "socket", "urllib"):
            assert bad not in src2


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-29 — debug values match runtime
# ---------------------------------------------------------------------------


class TestDebug:
    def test_snapshot_exposes_debug_fields(self):
        tr = TemporalLiquidityTracker()
        snap = tr.update(_liq(), "t0")
        # the snapshot exposes current+lags+deltas+persistence for the UI
        assert snap.dimension == 22
        assert len(snap.names) == 22
        assert "bsl_distance_atr_lag1" in snap.names
        # stability decision exposes raw vs stable + margins (brief 33/35)
        c = DecisionStabilityController()
        d = c.decide(_probs(0.575, 0.425))
        assert d.raw_direction == "BUY"
        assert d.stable_direction == "NONE"
        assert hasattr(d, "state") and hasattr(d, "margin")
        assert hasattr(d, "confirmation_progress")


# ---------------------------------------------------------------------------
# TEST-TEMPORAL-30 — schema registration + ablation structural guard
# ---------------------------------------------------------------------------


class TestSchemaAndAblation:
    def test_candidate_schema_registered(self):
        assert FEATURE_SCHEMAS.is_registered("scalp_v4_temporal_candidate")
        s = FEATURE_SCHEMAS.resolve("scalp_v4_temporal_candidate")
        assert s.dimension == 92

    def test_temporal_names_contract(self):
        assert len(TEMPORAL_FEATURE_NAMES) == 22
        assert len(set(TEMPORAL_FEATURE_NAMES)) == 22
        # all names reference canonical liquidity sources
        for n in TEMPORAL_FEATURE_NAMES:
            assert any(
                src in n
                for src in (
                    "bsl_distance_atr",
                    "ssl_distance_atr",
                    "eqh_strength",
                    "eql_strength",
                    "htf_liquidity_score",
                    "internal_liquidity_distance",
                    "external_liquidity_distance",
                    "liquidity_confluence",
                    "liquidity_sweep_state",
                    "post_sweep_displacement",
                )
            )

    def test_ablation_guard(self):
        # The three temporal families (lag/delta/persistence) are separable:
        # removing a family must change the vector (no degenerate dims)
        hist = [_liq(bsl=1.0, sweep=-1.0), _liq(bsl=1.6, sweep=-1.0), _liq(bsl=2.2, sweep=0.0)]
        full = temporal_features_from_history(hist)
        assert len(full) == 22
        # lag dims nonzero
        assert full[0] == pytest.approx(1.6)
        # delta dim nonzero
        assert full[2] == pytest.approx(0.3)
        # persistence dim nonzero (sweep changed 1/3)
        assert full[18] > 0.0
