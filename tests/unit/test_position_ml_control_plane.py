"""E2E tests for the ML Position Control Plane (spec §23, Tests A-H).

Exercises the REAL production chain: ownership gate → gated adapter →
broker mutation. Not mocks of the gate — the actual OrderManager wrapper.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

import pytest

from nexus_scalp.position_adviser.decision import (
    CloseMode,
    MLPositionDecision,
    PositionAction,
    SlTpAction,
)
from nexus_scalp.position_adviser.feature_schema import (
    POSITION_FEATURE_DIM,
    POSITION_FEATURE_SCHEMA_VERSION,
    build_timeframe_block,
    schema_contract,
)
from nexus_scalp.position_adviser.ml_lifecycle import (
    MLLifecycleState,
    MLPositionControllerLifecycle,
)
from nexus_scalp.position_adviser.ownership import (
    Controller,
    OwnershipViolation,
    PositionOwnershipGate,
)
from nexus_scalp.position_adviser.tensorizer import MLFeatureTensorizer, TensorizationError


# ---------------------------------------------------------------- fakes


class FakeSettings:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def set(self, key: str, value: Any, *, value_type: str | None = None, **kw: Any) -> None:
        self._store[key] = value

    def get(self, key: str) -> Any:
        v = self._store.get(key)
        if v is None:
            return None

        class _V:
            def __init__(self, value: Any) -> None:
                self.value = value

        return _V(v)


class FakeAdapter:
    def __init__(self) -> None:
        self.closed: list[int] = []
        self.modified: list[tuple[int, float, float]] = []
        self.fail_close = False

    def close_position(self, ticket: int, volume: float | None = None) -> bool:
        if self.fail_close:
            return False
        self.closed.append(ticket)
        return True

    def modify_position(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        self.modified.append((ticket, stop_loss, take_profit))
        return True

    def get_last_tick(self, symbol: str) -> None:  # delegate surface
        return None


# ------------------------------------------------------------- fixtures


@pytest.fixture()
def gate() -> PositionOwnershipGate:
    return PositionOwnershipGate()


@pytest.fixture()
def settings() -> FakeSettings:
    return FakeSettings()


def make_lifecycle(settings: FakeSettings, gate: PositionOwnershipGate) -> MLPositionControllerLifecycle:
    return MLPositionControllerLifecycle(
        settings_service=settings,
        gate=gate,
        schema_version=POSITION_FEATURE_SCHEMA_VERSION,
    )


# ================================================================ tests


def test_schema_v2_dimension_and_contract() -> None:
    c = schema_contract()
    assert c["d"] == POSITION_FEATURE_DIM
    assert c["schema_version"] == POSITION_FEATURE_SCHEMA_VERSION
    assert len(c["feature_names"]) == POSITION_FEATURE_DIM
    assert c["timeframes"] == ["M1", "M5", "M15"]


def test_schema_v2_feature_count_math() -> None:
    # 12 position-state + 3 timeframes × 104 indicator features
    assert POSITION_FEATURE_DIM == 12 + 3 * 104
    assert POSITION_FEATURE_DIM == 324


def test_gate_blocks_legacy_when_ml_active(gate: PositionOwnershipGate) -> None:
    gate.set_ml_active(True)
    with pytest.raises(OwnershipViolation):
        gate.authorize_or_raise(ticket=1, action="CLOSE", actor="legacy")
    with pytest.raises(OwnershipViolation):
        gate.authorize_or_raise(ticket=1, action="MODIFY_SL", actor="legacy")
    with pytest.raises(OwnershipViolation):
        gate.authorize_or_raise(ticket=1, action="TRAIL_STOP", actor="legacy")


def test_gate_allows_ml_and_emergency(gate: PositionOwnershipGate) -> None:
    gate.set_ml_active(True)
    assert gate.authorize_or_raise(ticket=1, action="CLOSE", actor="ml").allowed
    # emergency bypass always allowed
    assert gate.authorize_or_raise(ticket=1, action="CLOSE", actor="emergency").allowed


def test_gate_legacy_owner_allows_legacy(gate: PositionOwnershipGate) -> None:
    assert gate.controller_for(1) is Controller.LEGACY
    assert gate.authorize_or_raise(ticket=1, action="CLOSE", actor="legacy").allowed


def test_ml_failure_keeps_ownership_legacy_blocked(gate: PositionOwnershipGate) -> None:
    """§17: ML crash must NOT hand control back to legacy."""
    gate.set_ml_active(True)
    gate.set_ml_health(False, "inference crashed")
    with pytest.raises(OwnershipViolation):
        gate.authorize_or_raise(ticket=1, action="CLOSE", actor="legacy")
    assert gate.ml_active
    assert not gate.ml_healthy


def test_decision_keep_with_sl_tp_modify_is_legal() -> None:
    d = MLPositionDecision(
        ticket=7,
        position_action=PositionAction.KEEP,
        sl_action=SlTpAction.MODIFY,
        tp_action=SlTpAction.MODIFY,
        new_sl=4150.0,
        new_tp=4180.0,
    )
    assert not d.is_close
    assert d.modifies_sl and d.modifies_tp
    assert d.broker_mutations() == (
        ("MODIFY_SL_TP", {"ticket": 7, "stop_loss": 4150.0, "take_profit": 4180.0}),
    )


def test_decision_close_fast_is_legal() -> None:
    d = MLPositionDecision(
        ticket=7,
        position_action=PositionAction.CLOSE,
        close_mode=CloseMode.FAST,
    )
    assert d.is_fast_close
    assert d.broker_mutations()[0][0] == "FAST_CLOSE"


def test_decision_keep_with_close_mode_rejected() -> None:
    with pytest.raises(ValueError):
        MLPositionDecision(ticket=7, position_action=PositionAction.KEEP, close_mode=CloseMode.FAST)


def test_lifecycle_activate_persists_and_sets_owner(
    settings: FakeSettings, gate: PositionOwnershipGate
) -> None:
    lc = make_lifecycle(settings, gate)
    lc.wire_model_runtime(
        loader=lambda m, s: None, unloader=lambda: None
    )
    out = lc.activate(model_id="m1", model_path="a.pt", scaler_path="a.npz")
    assert out["status"] == "OK"
    assert lc.state is MLLifecycleState.ACTIVE
    assert gate.ml_active
    # persisted
    rec = lc.read_persisted()
    assert rec.enabled is True
    assert rec.model_id == "m1"
    assert rec.schema_version == POSITION_FEATURE_SCHEMA_VERSION
    assert rec.controller_mode == "ML"


def test_lifecycle_disable_unloads_and_restores_legacy(
    settings: FakeSettings, gate: PositionOwnershipGate
) -> None:
    lc = make_lifecycle(settings, gate)
    unloaded: list[bool] = []
    lc.wire_model_runtime(loader=lambda m, s: None, unloader=lambda: unloaded.append(True))
    lc.activate(model_id="m1", model_path="a.pt", scaler_path="a.npz")
    out = lc.disable()
    assert out["status"] == "OK"
    assert lc.state is MLLifecycleState.DISABLED
    assert unloaded == [True]
    assert not gate.ml_active
    assert gate.controller_for(1) is Controller.LEGACY
    assert lc.read_persisted().enabled is False


def test_lifecycle_restart_restores_ml(
    settings: FakeSettings, gate: PositionOwnershipGate
) -> None:
    lc = make_lifecycle(settings, gate)
    lc.wire_model_runtime(loader=lambda m, s: None, unloader=lambda: None)
    lc.activate(model_id="m1", model_path="a.pt", scaler_path="a.npz")
    # NEW lifecycle instance = process restart against the SAME settings DB
    lc2 = make_lifecycle(settings, gate)
    loaded: list[str] = []
    lc2.wire_model_runtime(
        loader=lambda m, s: loaded.append(m), unloader=lambda: None
    )
    out = lc2.restore_from_persistence()
    assert out["status"] == "OK" and out["restored"] is True
    assert lc2.state is MLLifecycleState.ACTIVE
    assert loaded == ["a.pt"]
    assert gate.ml_active


def test_lifecycle_schema_mismatch_rejected_loudly(
    settings: FakeSettings, gate: PositionOwnershipGate
) -> None:
    lc = make_lifecycle(settings, gate)
    rec = lc.read_persisted()
    rec.enabled = True
    rec.schema_version = "position_features_v1_STALE"
    rec.model_path = "a.pt"
    rec.scaler_path = "a.npz"
    rec.model_id = "stale"
    lc._persist(rec)
    out = lc.restore_from_persistence()
    assert out["status"] == "FAILED"
    assert "MODEL_LOAD_REJECTED" in lc.last_error
    # ownership stays ML (legacy NOT silently restored, §17)
    assert gate.ml_active
    assert not gate.ml_healthy


def test_tensorizer_rejects_lookahead() -> None:
    t = MLFeatureTensorizer()
    pos_state = {k: 1.0 for k in (
        "unrealized_pnl_r", "current_r_net", "current_return",
        "distance_to_stop_r", "distance_to_target_r", "position_age_bars",
        "atr", "spread", "estimated_slippage", "model_probability",
        "model_confidence", "signal_age",
    )}
    decision_time = datetime.now(UTC)

    class Snap:
        snapshot_time = None
        oscillators: list[Any] = []
        moving_averages: list[Any] = []
        pivots = None
        gauges: dict[str, Any] = {}
        last_close = None
        bar_count = 0

    snaps = {tf: Snap() for tf in ("M1", "M5", "M15")}
    # no timestamps -> fail loud
    with pytest.raises(TensorizationError):
        t.tensorize(
            position_state=pos_state,
            indicator_snapshots=snaps,
            atr=1.0,
            decision_time=decision_time,
        )
    # future snapshot -> look-ahead refused
    from datetime import timedelta

    for tf, s in snaps.items():
        s.snapshot_time = decision_time + timedelta(seconds=1)
    with pytest.raises(TensorizationError, match="look-ahead"):
        t.tensorize(
            position_state=pos_state,
            indicator_snapshots=snaps,
            atr=1.0,
            decision_time=decision_time,
        )


def test_tensorizer_full_vector_shape() -> None:
    from dataclasses import replace

    from nexus_scalp.indicators.service import IndicatorService

    t = MLFeatureTensorizer()
    pos_state = {k: 1.0 for k in (
        "unrealized_pnl_r", "current_r_net", "current_return",
        "distance_to_stop_r", "distance_to_target_r", "position_age_bars",
        "atr", "spread", "estimated_slippage", "model_probability",
        "model_confidence", "signal_age",
    )}
    now = datetime.now(UTC)
    svc = IndicatorService()
    snap = svc.snapshot("XAUUSD", [], "M1")  # empty bars -> None values
    # snapshot_time lives on the tensorizer input contract, not the frozen
    # snapshot — provenance is attached by the runtime wrapper (engine layer).
    class TimedSnap:
        def __init__(self, inner: Any, ts: datetime) -> None:
            self._inner = inner
            self.snapshot_time = ts
            self.oscillators = inner.oscillators
            self.moving_averages = inner.moving_averages
            self.pivots = inner.pivots
            self.gauges = inner.gauges
            self.last_close = inner.last_close
            self.bar_count = inner.bar_count

    timed = TimedSnap(snap, now)
    vec, prov = t.tensorize(
        position_state=pos_state,
        indicator_snapshots={"M1": timed, "M5": timed, "M15": timed},
        atr=1.0,
        decision_time=now,
    )
    assert len(vec) == POSITION_FEATURE_DIM
    assert prov["d"] == POSITION_FEATURE_DIM
    # indicator values/valids are all 0 (missing) — but the vote COUNTS are
    # the engine's real output (11 neutral oscillators, 15 neutral MAs), not
    # fabricated zeros. Only value/action/valid triples must be zeroed.
    non_zero = [(i, v) for i, v in enumerate(vec[12:], start=12) if v != 0.0]
    # expected nonzero: per tf, osc counts (neutral=11, total=11) + ma counts
    # (neutral=15, total=15) = 4 features per timeframe × 3 = 12
    assert len(non_zero) == 12, non_zero


def test_build_timeframe_block_preserves_engine_values() -> None:
    """The block encodes the SHARED engine's own actions (§7/§10)."""
    from nexus_scalp.indicators.ports import IndicatorResult

    class Snap:
        oscillators = [
            IndicatorResult("Relative Strength Index (14)", 30.0, "Sell"),
        ]
        moving_averages: list[IndicatorResult] = []
        pivots = None
        gauges: dict[str, Any] = {}
        last_close = 4157.0
        bar_count = 10

    block = build_timeframe_block("M1", Snap(), atr=1.0)
    # rsi14 block: value_norm (30->-0.4), action (Sell=-0.5), valid=1
    assert abs(block.values[0] - (-0.4)) < 1e-6
    assert block.values[1] == -0.5
    assert block.values[2] == 1.0
    # next oscillator (stoch_k) missing -> valid=0
    assert block.values[3:6] == (0.0, 0.0, 0.0)


def test_gated_adapter_blocks_legacy_close() -> None:
    """The REAL OrderManager wrapper blocks a legacy close on an ML-owned position."""
    from nexus_scalp.execution.order_manager import OrderLifecycleManager

    adapter = FakeAdapter()
    om = OrderLifecycleManager(adapter=adapter)  # type: ignore[arg-type]
    gate: PositionOwnershipGate = om._ownership_gate
    gate.set_ml_active(True)
    with pytest.raises(OwnershipViolation):
        om.adapter.close_position(ticket=1)
    assert adapter.closed == []  # broker NOT touched


def test_gated_adapter_allows_legacy_when_ml_disabled() -> None:
    from nexus_scalp.execution.order_manager import OrderLifecycleManager

    adapter = FakeAdapter()
    om = OrderLifecycleManager(adapter=adapter)  # type: ignore[arg-type]
    assert om.adapter.close_position(ticket=1) is True
    assert adapter.closed == [1]


def test_ml_controller_executes_through_raw_adapter() -> None:
    from nexus_scalp.position_adviser.controller import MLPositionController

    raw = FakeAdapter()
    gate = PositionOwnershipGate()
    gate.set_ml_active(True)
    ctrl = MLPositionController(
        raw_adapter=raw, gate=gate, model_version="m1", schema_version="v2"
    )
    d = MLPositionDecision(
        ticket=5,
        position_action=PositionAction.KEEP,
        sl_action=SlTpAction.MODIFY,
        tp_action=SlTpAction.MODIFY,
        new_sl=100.0,
        new_tp=110.0,
    )
    out = ctrl.execute(d)
    assert out["status"] == "OK"
    assert raw.modified == [(5, 100.0, 110.0)]
    assert raw.closed == []


def test_ml_controller_failure_surfaces_without_legacy_takeover() -> None:
    from nexus_scalp.position_adviser.controller import MLPositionController

    class FailingAdapter(FakeAdapter):
        def close_position(self, ticket: int, volume: float | None = None) -> bool:
            raise RuntimeError("torch CUDA OOM")

    raw = FailingAdapter()
    gate = PositionOwnershipGate()
    gate.set_ml_active(True)
    ctrl = MLPositionController(
        raw_adapter=raw, gate=gate, model_version="m1", schema_version="v2"
    )
    d = MLPositionDecision(ticket=5, position_action=PositionAction.CLOSE)
    with pytest.raises(Exception, match="CUDA OOM"):
        ctrl.execute(d)
    # §17: ML ownership persists; legacy remains blocked
    assert gate.ml_active
    assert not gate.ml_healthy
    with pytest.raises(OwnershipViolation):
        gate.authorize_or_raise(ticket=5, action="CLOSE", actor="legacy")
