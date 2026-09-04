"""Characterization of LiveEngine freshness — locks current semantics before extraction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.application.live_freshness import LiveFreshnessService, LiveFreshnessSnapshot


def _snapshot(**overrides):
    base = dict(
        freshness_max_age_sec=30.0,
        last_tick_timestamp=datetime.now(UTC),
        last_feature_update=datetime.now(UTC),
        last_inference_timestamp=datetime.now(UTC),
        last_decision_timestamp=datetime.now(UTC),
        tick_sequence=1,
        feature_sequence=1,
        inference_sequence=1,
        decision_sequence=1,
        monotonic_tick_ms=1000,
        last_raw_market_hash="a" * 16,
        last_feature_hash="b" * 16,
        last_model_input_hash="c" * 16,
        last_model_output_hash="d" * 16,
        market_updates_total=1,
        feature_builds_total=1,
        inference_runs_total=1,
        inference_failures_total=0,
        decision_updates_total=1,
        stale_state_detected_total=0,
    )
    base.update(overrides)
    return LiveFreshnessSnapshot(**base)


# === GOLDEN 1: stage freshness boundaries ===

class TestStageFreshness:
    def test_none_is_unknown(self):
        svc = LiveFreshnessService()
        state, age = svc.stage_freshness(None, 30.0)
        assert state == "UNKNOWN"
        assert age is None

    def test_fresh_within_window(self):
        svc = LiveFreshnessService()
        stamp = datetime.now(UTC) - timedelta(seconds=5)
        state, age = svc.stage_freshness(stamp, 30.0)
        assert state == "FRESH"
        assert age is not None and age >= 4000  # ~5000ms, allow jitter

    def test_stale_beyond_window(self):
        svc = LiveFreshnessService()
        stamp = datetime.now(UTC) - timedelta(seconds=900)
        state, age = svc.stage_freshness(stamp, 30.0)
        assert state == "STALE"
        assert age is not None and age >= 890_000

    def test_slightly_below_boundary_is_fresh(self):
        svc = LiveFreshnessService()
        stamp = datetime.now(UTC) - timedelta(seconds=29.5)
        state, _ = svc.stage_freshness(stamp, 30.0)
        assert state == "FRESH"

    def test_slightly_above_boundary_is_stale(self):
        svc = LiveFreshnessService()
        stamp = datetime.now(UTC) - timedelta(seconds=30.5)
        state, _ = svc.stage_freshness(stamp, 30.0)
        assert state == "STALE"

    def test_future_stamp_clamped_to_zero_and_fresh(self):
        svc = LiveFreshnessService()
        stamp = datetime.now(UTC) + timedelta(seconds=60)
        state, age = svc.stage_freshness(stamp, 30.0)
        assert state == "FRESH"
        assert age == 0.0


# === GOLDEN 2: compute aggregation ===

class TestComputeFreshness:
    def test_all_fresh_overall_fresh(self):
        svc = LiveFreshnessService()
        fresh = svc.compute_freshness(_snapshot())
        assert fresh["overall"] == "FRESH"

    def test_any_stale_overall_stale(self):
        svc = LiveFreshnessService()
        stale = _snapshot(last_tick_timestamp=datetime.now(UTC) - timedelta(seconds=900))
        fresh = svc.compute_freshness(stale)
        assert fresh["overall"] == "STALE"
        assert fresh["market"]["state"] == "STALE"

    def test_unknown_when_missing_stamp_and_no_stale(self):
        svc = LiveFreshnessService()
        unk = _snapshot(last_tick_timestamp=None)
        fresh = svc.compute_freshness(unk)
        assert fresh["overall"] == "UNKNOWN"
        assert fresh["market"]["state"] == "UNKNOWN"

    def test_stale_beats_unknown(self):
        svc = LiveFreshnessService()
        mixed = _snapshot(
            last_tick_timestamp=datetime.now(UTC) - timedelta(seconds=900),
            last_feature_update=None,
        )
        fresh = svc.compute_freshness(mixed)
        assert fresh["overall"] == "STALE"

    def test_return_shape(self):
        svc = LiveFreshnessService()
        fresh = svc.compute_freshness(_snapshot())
        assert set(fresh.keys()) == {
            "market", "features", "inference", "decision",
            "overall", "max_age_sec", "sequences", "monotonic_tick_ms",
            "hashes", "telemetry",
        }
        assert fresh["max_age_sec"] == 30.0
        assert fresh["sequences"]["tick"] == 1
        assert fresh["hashes"]["raw_market"] == "a" * 16

    def test_telemetry_reflects_snapshot(self):
        svc = LiveFreshnessService()
        snap = _snapshot(market_updates_total=7, stale_state_detected_total=3)
        fresh = svc.compute_freshness(snap)
        assert fresh["telemetry"]["market_updates_total"] == 7
        assert fresh["telemetry"]["stale_state_detected_total"] == 3

    def test_compute_does_not_mutate_snapshot(self):
        svc = LiveFreshnessService()
        snap = _snapshot(last_tick_timestamp=datetime.now(UTC) - timedelta(seconds=900))
        before = snap.stale_state_detected_total
        svc.compute_freshness(snap)
        assert snap.stale_state_detected_total == before


# === GOLDEN 3: gate downgrade ===

class TestGateProposal:
    def _proposal(self, action="BUY", confidence=0.5):
        from nexus_scalp.domain.enums import ActionType
        from nexus_scalp.domain.models import TradeProposal
        amap = {"BUY": ActionType.BUY, "SELL": ActionType.SELL, "BUY_MARKET": ActionType.BUY_MARKET}
        return TradeProposal(
            request_id="test",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=amap.get(action, ActionType.BUY),
            confidence=confidence,
            proposed_entry=4628.0,
            stop_loss=4620.0,
            take_profit=4640.0,
            risk_reward_ratio=1.5,
            reason_code="TEST",
        )

    def test_fresh_not_blocked(self):
        svc = LiveFreshnessService()
        proposal = self._proposal()
        fresh = svc.compute_freshness(_snapshot())
        out, blocked = svc.gate_proposal(fresh, proposal)
        assert blocked is False
        assert out.action == proposal.action

    def test_stale_blocked_to_no_trade(self):
        from nexus_scalp.domain.enums import ActionType
        svc = LiveFreshnessService()
        proposal = self._proposal()
        stale = svc.compute_freshness(_snapshot(last_tick_timestamp=datetime.now(UTC) - timedelta(seconds=900)))
        out, blocked = svc.gate_proposal(stale, proposal)
        assert blocked is True
        assert out.action == ActionType.NO_TRADE
        assert out.confidence == 0.0
        assert out.reason_code == "BLOCKED_BY_STALE"

    def test_gate_preserves_proposal_on_not_copyable(self):
        svc = LiveFreshnessService()
        fresh = {"overall": "STALE"}
        obj = object()
        out, blocked = svc.gate_proposal(fresh, obj)
        assert blocked is True
        assert out is obj


# === GOLDEN 4: diagnose localization ===

class TestDiagnoseFreshness:
    def _engine_snapshot(self, **overrides):
        return _snapshot(**overrides)

    def test_market_none_frozen_at_market(self):
        svc = LiveFreshnessService()
        class FakeAdapter:
            def get_tick(self, symbol): return None
        class FakeAgg:
            def get_completed_bars(self): return []
        class FakeFE:
            def compute_from_bars(self, completed_bars=None, current_tick=None):  # noqa: ARG002
                raise AssertionError("should not be called when tick is None")
        snap = self._engine_snapshot()
        res = svc.diagnose(
            snap,
            adapter=FakeAdapter(), aggregator=FakeAgg(), feature_engine=FakeFE(),
            build_vector_fn=lambda fv: ([0.0]*50, {}),
            get_bundle_fn=lambda: None,
            run_inference_fn=lambda x: None,
            symbol="XAUUSD",
        )
        assert res["frozen_at"] == "MARKET"
        assert "adapter.get_tick returned None" in (res["error"] or "")

    def test_exception_falls_back_to_unknown(self):
        svc = LiveFreshnessService()
        class BadAdapter:
            def get_tick(self, symbol): raise RuntimeError("boom")
        snap = self._engine_snapshot()
        res = svc.diagnose(
            snap,
            adapter=BadAdapter(), aggregator=None, feature_engine=None,
            build_vector_fn=lambda fv: ([0.0]*50, {}),
            get_bundle_fn=lambda: None,
            run_inference_fn=lambda x: None,
            symbol="XAUUSD",
        )
        assert res["frozen_at"] == "UNKNOWN"
        assert "RuntimeError" in (res["error"] or "")

    def test_frozen_market_hash_localizes(self):
        import hashlib
        from dataclasses import dataclass

        @dataclass
        class FakeTick:
            bid: float = 4628.0
            ask: float = 4628.5
            last: float = 4628.25

        tick = FakeTick()
        mkt_hash = hashlib.sha1(f"{tick.bid:.5f}|{tick.ask:.5f}|{tick.last:.5f}".encode()).hexdigest()[:16]

        class FakeAdapter:
            def get_tick(self, symbol): return tick
        class FakeAgg:
            def get_completed_bars(self): return []
        class FakeFV:
            def to_tensor_input(self): return [0.1] * 50
        class FakeFE:
            def compute_from_bars(self, completed_bars=None, current_tick=None):  # noqa: ARG002
                return FakeFV()

        snap = self._engine_snapshot(last_raw_market_hash=mkt_hash)
        res = svc = LiveFreshnessService().diagnose(
            snap,
            adapter=FakeAdapter(), aggregator=FakeAgg(), feature_engine=FakeFE(),
            build_vector_fn=lambda fv: ([0.0]*50, {}),
            get_bundle_fn=lambda: None,
            run_inference_fn=lambda x: None,
            symbol="XAUUSD",
        )
        assert res["frozen_at"] == "MARKET"
