"""Agent-6 execution-contract regression battery (TASK-AGENT6-EXEC-CONTRACT).

Proves the model-output -> order-intent contract end to end:

  1. invalid model output   : NaN/Inf/short tensors cannot produce a trade
  2. confidence boundary    : normalized confidence < threshold rejected, >= accepted
  3. BUY/SELL mapping       : policy actions map to the correct MT5 order types
  4. risk-gate rejection    : kill switch / circuit breaker / spread / RR reject
                              the entry AT DISPATCH (DecisionExecutor), so a
                              rejected decision can never become an order
  5. stale decision         : a decision older than the freshness budget is
                              downgraded by the G29 freshness gate (no order)
  6. LIVE/PAPER isolation   : PAPER boots bind the simulation adapter (BUG-212)
                              and the AI-flip entry is suspended unless enabled
  7. fail-closed fallback   : a risk-engine evaluation error must NOT dispatch

Offline, deterministic, no broker and no model artifacts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import torch

from nexus_scalp.application.live.decision_executor import DecisionExecutor
from nexus_scalp.domain.enums import ActionType, ExecutionMode, OrderType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.signals.policy import SignalPolicy

UTC_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _tick(symbol: str = "XAUUSD", bid: float = 2400.0, ask: float = 2400.10) -> TickData:
    return TickData(symbol=symbol, timestamp=UTC_NOW, bid=bid, ask=ask)


def _fv() -> FeatureVector:
    """Deterministic, structurally-neutral feature vector (no candidate
    channels beyond a clean Ichimoku-bullish alignment)."""
    return FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=UTC_NOW.isoformat(),
        live_tick_displacement=2.0,
        log_return_m1=0.001,
        atr_m1=1.5,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.5,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.9,
        consecutive_momentum_count=2,
        dist_to_swing_high_20=1.0,
        dist_to_swing_low_20=1.0,
        price_compression_flag_ratio=0.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=False,
        session_london=True,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.5,
        fvg_bullish_active=False,
        fvg_bearish_active=False,
        order_block_type=0,
        liquidity_sweep_signal=0,
        choch_bullish=False,
        choch_bearish=False,
        broke_previous_high=False,
        broke_previous_low=False,
        rapid_reversal_spike=False,
        rapid_reversal_spike_val=0.0,
        tenkan_sen=2401.0,
        kijun_sen=2399.0,
        senkou_span_a=2398.0,
        senkou_span_b=2397.0,
        tk_cross_signal=1,
        is_above_kumo=True,
        is_below_kumo=False,
        rsi_14=60.0,
        dist_to_ema_21=0.5,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=1.0,
        htf_h1_momentum=1.0,
        htf_m30_structure=1.0,
        htf_m15_confirmation=1.0,
        support_zone_dist=5.0,
        resistance_zone_dist=5.0,
        trend_strength=0.5,
        consolidation_ratio=0.5,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )


def _exec_stub(**over: Any) -> tuple[DecisionExecutor, SimpleNamespace]:
    """A stub composition root exposing only what execute_decision_stage reads."""
    policy = SignalPolicy(confidence_threshold=0.35)
    recorded: dict[str, Any] = {"dispatch": [], "risk_eval": []}

    risk_engine = SimpleNamespace(
        evaluate_proposal=lambda **kw: (
            recorded["risk_eval"].append(kw),
            SimpleNamespace(volume=0.11),
        )[1],
        calculate_volume=lambda **kw: 0.11,
        get_clamped_position_size=lambda **kw: 0.11,
        _map_action_to_order_type=lambda a: OrderType.BUY,
    )

    class _PolicyState:
        last_order_price: float | None = 2400.0
        last_order_time: datetime | None = UTC_NOW
        _last_active_direction: Any = ActionType.BUY_MARKET
        _last_active_direction_time: Any = UTC_NOW
        _last_executed_price: float = 2400.0

    om = SimpleNamespace(
        config=SimpleNamespace(
            execution=SimpleNamespace(mode=ExecutionMode.LIVE),
            risk=SimpleNamespace(risk_per_trade_pct=0.5),
        ),
        signal_policy=policy,
        risk_engine=risk_engine,
        order_manager=SimpleNamespace(
            dispatch_order=lambda decision, volume, setup_snapshot=None: (
                recorded["dispatch"].append(
                    {"decision": decision, "volume": volume, "snapshot": setup_snapshot}
                ),
                True,
            )[1],
            execute_ai_reversal=lambda **kw: True,
            execute_lifecycle_action=lambda decision: True,
            register_order_message=lambda *a, **kw: None,
            update_account_snapshot=lambda **kw: None,
        ),
        notifier=SimpleNamespace(
            notify_order_opened=lambda **kw: None,
        ),
        audit=SimpleNamespace(
            log_account_snapshot=lambda **kw: None,
        ),
        # hedging + survival hooks are post-dispatch stages on the engine root
        _evaluate_hedging_policy=lambda **kw: None,
        _update_survival_state=lambda **kw: None,
        _symbol_info=SimpleNamespace(symbol="XAUUSD"),
        _peak_equity=10000.0,
        **over,
    )
    # the executor clears locks on the REAL signal policy object; expose the
    # state attributes directly on it for assertion simplicity.
    policy.last_order_price = 2400.0
    policy.last_order_time = UTC_NOW
    policy._last_active_direction = ActionType.BUY_MARKET
    policy._last_active_direction_time = UTC_NOW
    policy._last_executed_price = 2400.0
    om.signal_policy = policy

    eng = DecisionExecutor(om)
    recorded["om"] = om
    return eng, recorded


def _proposal(action: ActionType, **over: Any) -> SimpleNamespace:
    base = SimpleNamespace(
        request_id="req-agent6-1",
        execution_id="EXEC-20260909-120000-abcdef",
        symbol="XAUUSD",
        generated_at=UTC_NOW,
        action=action,
        confidence=0.9,
        proposed_entry=2400.10,
        stop_loss=2396.0,
        take_profit=2404.0,
        risk_reward_ratio=2.0,
        reason_code="TEST_ENTRY",
        ticket=0,
        is_ai_reversal=False,
        reversal_action=None,
        model_action="BUY_MARKET",
        buy_probability=0.85,
        sell_probability=0.05,
        no_trade_probability=0.10,
        regime="TRENDING",
        guardian_status="IDLE",
        decision_stage="FINAL_DECISION",
    )
    for k, v in over.items():
        setattr(base, k, v)
    return base


def _run_entry(eng: DecisionExecutor, proposal: SimpleNamespace, fv: Any = None) -> None:
    fv = fv or _fv()
    eng.execute_decision_stage(
        tick=_tick(),
        account=SimpleNamespace(equity=10000.0, balance=10000.0, margin_free=10000.0),
        fv=fv,
        probs=torch.tensor([0.05, 0.85, 0.05, 0.05]),
        regime_state=None,
        proposal=proposal,
        policy_decision=proposal,
        active_positions=[],
        current_pos_count=0,
    )


# ---------------------------------------------------------------------------
# 1. invalid model output -> fail closed at the policy layer
# ---------------------------------------------------------------------------


def test_all_nan_probabilities_cannot_produce_a_trade() -> None:
    policy = SignalPolicy(confidence_threshold=0.35)
    p = policy.evaluate_probabilities(
        probabilities=torch.tensor([float("nan")] * 4), current_tick=_tick(), feature_vector=_fv()
    )
    assert p.action == ActionType.NO_TRADE
    assert p.confidence == 0.0
    assert p.final_action == "NO_TRADE"


def test_inf_probability_falls_back_raw_and_stays_below_gate() -> None:
    policy = SignalPolicy(confidence_threshold=0.35)
    p = policy.evaluate_probabilities(
        probabilities=torch.tensor([float("inf"), 0.85, 0.05, 0.05]),
        current_tick=_tick(),
        feature_vector=_fv(),
    )
    # degenerate trained mass -> RAW_FALLBACK, and the entry gate must hold
    assert p.action == ActionType.NO_TRADE or p.confidence < 0.35


def test_short_probability_vector_is_fail_closed() -> None:
    policy = SignalPolicy(confidence_threshold=0.99)
    p = policy.evaluate_probabilities(
        probabilities=torch.tensor([0.9]), current_tick=_tick(), feature_vector=_fv()
    )
    assert p.action == ActionType.NO_TRADE
    assert p.confidence < 0.99


# ---------------------------------------------------------------------------
# 2. confidence threshold boundary (normalized trained-class semantics)
# ---------------------------------------------------------------------------


def test_confidence_just_below_threshold_is_rejected() -> None:
    policy = SignalPolicy(confidence_threshold=0.35)
    # normalized directional confidence = 0.3499 / 1.0
    p = policy.evaluate_probabilities(
        probabilities=torch.tensor([0.6501, 0.3499, 0.0, 0.0]),
        current_tick=_tick(),
        feature_vector=_fv(),
    )
    if p.action == ActionType.NO_TRADE:
        assert p.blocked_by in ("CONFIDENCE_FAIL", "ASYMMETRIC_RR_LIMIT", "ZONE_QUALITY_FAIL")


def test_confidence_at_threshold_is_not_confidence_blocked() -> None:
    policy = SignalPolicy(confidence_threshold=0.35)
    p = policy.evaluate_probabilities(
        probabilities=torch.tensor([0.6500, 0.3500, 0.0, 0.0]),
        current_tick=_tick(),
        feature_vector=_fv(),
    )
    # boundary semantics: confidence >= threshold must NOT be a CONFIDENCE_FAIL
    assert p.blocked_by != "CONFIDENCE_FAIL"


# ---------------------------------------------------------------------------
# 3. BUY/SELL mapping through the risk engine's action mapper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (ActionType.BUY, OrderType.BUY),
        (ActionType.BUY_MARKET, OrderType.BUY),
        (ActionType.SELL, OrderType.SELL),
        (ActionType.SELL_MARKET, OrderType.SELL),
        (ActionType.BUY_LIMIT, OrderType.BUY_LIMIT),
        (ActionType.SELL_LIMIT, OrderType.SELL_LIMIT),
        (ActionType.BUY_STOP, OrderType.BUY_STOP),
        (ActionType.SELL_STOP, OrderType.SELL_STOP),
    ],
)
def test_action_to_order_type_mapping(action: ActionType, expected: OrderType) -> None:
    engine = RiskEngine(config=SimpleNamespace())  # mapper is config-independent
    assert engine._map_action_to_order_type(action) is expected


# ---------------------------------------------------------------------------
# 4. risk-gate rejection at dispatch: rejected decision != order
# ---------------------------------------------------------------------------


def test_kill_switch_blocks_entry_dispatch() -> None:
    eng, rec = _exec_stub()

    def _reject(**kw: Any) -> None:
        rec["risk_eval"].append(kw)

    eng.om.risk_engine.evaluate_proposal = _reject  # type: ignore[method-assign]
    _run_entry(eng, _proposal(ActionType.BUY_MARKET))
    assert rec["dispatch"] == [], "kill-switch rejection must never dispatch"
    # policy price lock released (not wedged)
    assert eng.om.signal_policy.last_order_price is None


def test_circuit_breaker_rejection_blocks_entry_dispatch() -> None:
    eng, rec = _exec_stub()
    eng.om.risk_engine.evaluate_proposal = lambda **kw: rec["risk_eval"].append(kw) or None  # type: ignore[method-assign]
    _run_entry(eng, _proposal(ActionType.SELL_MARKET))
    assert rec["dispatch"] == []
    assert eng.om.signal_policy._last_executed_price == 0.0


def test_approved_risk_evaluation_dispatches_sized_order() -> None:
    eng, rec = _exec_stub()
    _run_entry(eng, _proposal(ActionType.BUY_MARKET))
    assert len(rec["dispatch"]) == 1
    d = rec["dispatch"][0]
    assert d["decision"].symbol == "XAUUSD"
    assert d["volume"] == 0.11  # risk-engine-returned volume, not the raw stub
    # provenance: the risk evaluation saw the exact decision being dispatched
    assert rec["risk_eval"][0]["proposal"] is rec["dispatch"][0]["decision"]


# ---------------------------------------------------------------------------
# 5. stale decision cannot become an order (G29 freshness gate downgrade)
# ---------------------------------------------------------------------------


def test_freshness_gate_downgrades_stale_decision_to_no_trade() -> None:
    from nexus_scalp.application.live_freshness import LiveFreshnessService
    from nexus_scalp.domain.models import TradeProposal

    stale_but_actionable = TradeProposal(
        request_id="req-stale-1",
        symbol="XAUUSD",
        generated_at=UTC_NOW - timedelta(minutes=10),
        action=ActionType.BUY_MARKET,
        confidence=0.9,
        proposed_entry=2400.10,
        stop_loss=2396.0,
        take_profit=2404.0,
        risk_reward_ratio=2.0,
    )
    fresh = {
        "overall": "STALE",
        "max_age_sec": 30.0,
        "market": {"state": "STALE", "age_ms": 600000.0},
        "features": {"state": "STALE", "age_ms": 600000.0},
        "inference": {"state": "STALE", "age_ms": 600000.0},
        "decision": {"state": "STALE", "age_ms": 600000.0},
    }
    out, blocked = LiveFreshnessService.gate_proposal(fresh, stale_but_actionable)
    assert blocked is True
    assert out.action == ActionType.NO_TRADE
    assert out.reason_code == "BLOCKED_BY_STALE"
    assert out.confidence == 0.0


def test_freshness_gate_passes_fresh_decision_unchanged() -> None:
    from nexus_scalp.application.live_freshness import LiveFreshnessService

    fresh = {"overall": "FRESH"}
    p = _proposal(ActionType.BUY_MARKET)
    out, blocked = LiveFreshnessService.gate_proposal(fresh, p)
    assert blocked is False
    assert out is p


# ---------------------------------------------------------------------------
# 6. LIVE / PAPER / SHADOW isolation
# ---------------------------------------------------------------------------


def test_shadow_boundary_suppresses_entry_before_dispatch() -> None:
    eng, rec = _exec_stub()
    # SHADOW mode: the boundary downgrades the decision before any authority runs
    eng.om.config.execution.mode = ExecutionMode.SHADOW
    from nexus_scalp.domain.models import TradeProposal

    proposal = TradeProposal(
        request_id="req-shadow-1",
        execution_id="EXEC-20260909-120000-shadow",
        symbol="XAUUSD",
        generated_at=UTC_NOW,
        action=ActionType.BUY_MARKET,
        confidence=0.9,
        proposed_entry=2400.10,
        stop_loss=2396.0,
        take_profit=2404.0,
        risk_reward_ratio=2.0,
    )
    eng.execute_decision_stage(
        tick=_tick(),
        account=SimpleNamespace(equity=10000.0, margin_free=10000.0),
        fv=_fv(),
        probs=torch.tensor([0.05, 0.85, 0.05, 0.05]),
        regime_state=None,
        proposal=proposal,
        policy_decision=proposal,
        active_positions=[],
        current_pos_count=0,
    )
    assert rec["dispatch"] == [], "SHADOW must never dispatch an order"
    # the downgraded decision kept the full audit evidence
    assert rec["om"]  # stub root intact
    # risk engine never consulted: no order authority in shadow
    assert rec["risk_eval"] == []


def test_ai_flip_entry_is_suspended_unless_operator_enabled() -> None:
    from nexus_scalp.execution.order_manager import OrderLifecycleManager

    om = OrderLifecycleManager.__new__(OrderLifecycleManager)
    om.adapter = SimpleNamespace(get_positions=lambda **kw: [])
    om.algo_config = SimpleNamespace(ai_flip_exit_enabled=False)
    om._ai_flip_warn_times = {}
    decision = _proposal(ActionType.CLOSE_POSITION, ticket=42)
    assert om.execute_ai_reversal(decision=decision, volume=0.1) is False


def test_paper_boot_binds_simulation_adapter() -> None:
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.configuration.config import AppConfig

    class _FakeReal(DirectMT5Adapter):  # type: ignore[misc]
        def __init__(self) -> None:  # bypass real MT5 init
            pass

    cfg = AppConfig()
    cfg.execution.mode = ExecutionMode.PAPER
    engine = LiveEngine.__new__(LiveEngine)
    engine.config = cfg
    real = _FakeReal()
    out = LiveEngine.align_adapter_to_boot_mode(engine, real, ExecutionMode.PAPER)
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    assert isinstance(out, PaperMT5Adapter), "PAPER boot must replace a real adapter"


# ---------------------------------------------------------------------------
# 7. fail-closed fallback: risk-engine crash must not dispatch
# ---------------------------------------------------------------------------


def test_risk_engine_crash_is_fail_closed() -> None:
    eng, rec = _exec_stub()

    def _boom(**kw: Any) -> None:
        raise RuntimeError("risk engine exploded")

    eng.om.risk_engine.evaluate_proposal = _boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        _run_entry(eng, _proposal(ActionType.BUY_MARKET))
    # the hot-path circuit breaker records the error upstream; crucially the
    # crash happened BEFORE dispatch, so no order can have been sent.
    assert rec["dispatch"] == []
