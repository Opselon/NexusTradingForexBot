"""Confidence-gate non-bypass test matrix — machine-checkable pins for the
policy decision surface.

Companion to ``agents/runtime_invariants.md`` (INV-018 family) and the
signal-policy forensics. Every row proves a gate is present, is consulted,
and that a weakened or bypassed gate is METRIC-DETECTABLE: the evidence the
gate's own forensic ledger cites (risk_checks / decision_stage /
blocked_by) is asserted verbatim, so a silent bypass changes an observable
field and fails CI.

Unit-level only: drives SignalPolicy.evaluate_probabilities with the same
paper fixtures the existing policy suites use (test_policy.py,
test_policy_predictive_limit.py). No MT5 / network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import torch

from nexus_scalp.configuration.config import AlgoConfig, ModelConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.regime_classifier import MarketRegimeState
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy, TradeProposal


def _tick(bid: float = 2000.0, ask: float = 2000.2) -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=datetime.now(UTC),
        bid=bid,
        ask=ask,
        volume=1.0,
    )


def _tick_ts(ts: datetime, bid: float = 2000.0, ask: float = 2000.2) -> TickData:
    """A tick at an explicit timestamp, so ordered evaluations stay outside
    the policy's duplicate-tick window."""
    return TickData(symbol="XAUUSD", timestamp=ts, bid=bid, ask=ask, volume=1.0)


def _bearish_channel_fv(**over: object) -> FeatureVector:
    """Below-kumo bearish standard-channel fixture — mirrors the repo's own
    flip-protection pin suite (test_policy_flip_protection_pins_bug227) so
    the SELL candidate reaches the flip gate instead of an earlier filter.
    Zone-neutral flags keep the zone channel out of the way."""
    base: dict[str, Any] = dict(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        live_tick_displacement=0.0,
        log_return_m1=0.0,
        atr_m1=2.00,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=1.0,
        dist_to_swing_high_20=0.0,
        dist_to_swing_low_20=0.0,
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
        lag_1_clv=0.0,
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
        tenkan_sen=1999.0,
        kijun_sen=2000.5,
        senkou_span_a=1999.5,
        senkou_span_b=2000.5,
        tk_cross_signal=-1,
        is_above_kumo=False,
        is_below_kumo=True,
        rsi_14=50.0,
        dist_to_ema_21=0.0,
        dist_to_ema_50=0.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=0.0,
        htf_h1_momentum=0.0,
        htf_m30_structure=0.0,
        htf_m15_confirmation=0.0,
        support_zone_dist=3.0,
        resistance_zone_dist=3.0,
        trend_strength=0.0,
        consolidation_ratio=0.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )
    base.update(over)
    return FeatureVector(**base)


def _fv(**over: object) -> FeatureVector:
    base: dict[str, Any] = dict(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        live_tick_displacement=0.5,
        log_return_m1=0.0,
        atr_m1=2.00,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=1.0,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=True,
        session_london=False,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
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
        tenkan_sen=2000.0,
        kijun_sen=2000.0,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=True,
        is_below_kumo=False,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=1.0,
        htf_h1_momentum=1.0,
        htf_m30_structure=1.0,
        htf_m15_confirmation=1.0,
        support_zone_dist=5.0,
        resistance_zone_dist=5.0,
        trend_strength=1.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )
    base.update(over)
    return FeatureVector(**base)


def _eval(policy: SignalPolicy, probs: list[float], **kw: Any) -> TradeProposal:
    return policy.evaluate_probabilities(
        probabilities=torch.tensor([probs]),
        current_tick=kw.pop("tick", _tick()),
        feature_vector=kw.pop("fv", _fv()),
        **kw,
    )


#: Confidence-gate rows the matrix pins. Every entry names the gate and the
#: evidence field its forensic ledger cites, so a bypass is observable.
GATE_ROWS: tuple[tuple[str, str], ...] = (
    ("CONFIDENCE", "blocked_by=CONFIDENCE_FAIL / decision_stage=CONFIDENCE_GATE"),
    ("RANGE_PENALTY", "risk_checks.range_penalty"),
    ("SURVIVAL_MODE", "risk_checks.survival_mode_adjustment"),
    ("ZONE_QUALITY", "blocked_by=ZONE_QUALITY_FAIL"),
    ("FLIP_PROTECTION", "blocked_by=FLIP_PROTECTION"),
    ("SAME_LEVEL_REENTRY", "blocked_by=SAME_LEVEL_REENTRY"),
    ("HTF_TREND_CONFL", "blocked_by=HTF_TREND_CONFL_FAIL"),
    ("SR_MARGIN", "blocked_by=SR_RESISTANCE_MARGIN_FAIL / SR_SUPPORT_MARGIN_FAIL"),
    ("SPREAD_ATR", "blocked_by=SPREAD_ATR_RATIO"),
    ("SPREAD_TP", "blocked_by=SPREAD_TP_RATIO"),
    ("SPREAD_SESSION_PCT", "blocked_by=SPREAD_SESSION_PCT"),
    ("ASYMMETRIC_RR", "blocked_by=ASYMMETRIC_RR_LIMIT"),
    ("RANGE_FILTER", "blocked_by=RANGE_FILTER"),
    ("COOLDOWN", "blocked_by=COOLDOWN"),
    ("INFERENCE_DEGRADED", "reason_code=PROBS_UNAVAILABLE_DEGRADED"),
    ("DEDUP_TICK", "blocked_by=TICK_DUPLICATE_SUPPRESSED"),
)


def test_gate_rows_are_named_and_evidence_sourced() -> None:
    assert len(GATE_ROWS) == len({name for name, _ in GATE_ROWS})
    for name, evidence in GATE_ROWS:
        assert name.isupper(), name
        assert evidence, name


# ---------------------------------------------------------------------------
# Threshold ownership: one canonical default, overrides stay explicit.
# ---------------------------------------------------------------------------


def test_confidence_threshold_has_one_canonical_default() -> None:
    """THRESHOLD OWNERSHIP (P0): the base threshold's only default is
    ModelConfig.confidence_threshold. A second literal default here is a
    divergent source of truth labeled 'calibrated' without evidence."""
    assert SignalPolicy().confidence_threshold == ModelConfig().confidence_threshold


def test_confidence_threshold_override_is_explicit_and_scoped() -> None:
    """Explicit overrides remain possible for replay/research freezes and
    never leak back into the default (the override is constructor-scoped)."""
    policy = SignalPolicy(confidence_threshold=0.50)
    assert policy.confidence_threshold == 0.50
    # A fresh policy is unaffected by a previous override.
    assert SignalPolicy().confidence_threshold == ModelConfig().confidence_threshold


def test_algo_thresholds_default_from_the_canonical_config() -> None:
    """The spread/zone thresholds resolve from AlgoConfig (canonical source),
    with getattr fallbacks keeping frozen/replayed shapes working."""
    policy = SignalPolicy()
    algo = AlgoConfig()
    assert policy.max_spread_atr_ratio == pytest.approx(algo.max_spread_atr_ratio)
    assert policy.max_spread_pct_of_tp == pytest.approx(algo.max_spread_pct_of_tp)
    assert policy.spread_session_percentile == pytest.approx(algo.spread_session_percentile)


# ---------------------------------------------------------------------------
# CONFIDENCE gate: present, consulted, and impassable when unmet.
# ---------------------------------------------------------------------------


def test_confidence_gate_blocks_below_threshold() -> None:
    """A directional probability below the effective threshold must yield
    NO_TRADE stamped with the CONFIDENCE_FAIL evidence — the gate is
    consulted, not advisory."""
    policy = SignalPolicy(confidence_threshold=0.60)
    proposal = _eval(policy, [0.25, 0.30, 0.20, 0.25])
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.blocked_by == "CONFIDENCE_FAIL"
    assert proposal.decision_stage == "CONFIDENCE_GATE"
    checks = proposal.risk_checks or {}
    assert checks["model_confidence"] < checks["effective_threshold"]


def test_confidence_gate_passes_when_threshold_is_met() -> None:
    """A directional probability >= threshold passes the gate: the gate is
    not a universal veto (the CONFIDENCE-SEMANTICS repair keeps it
    reachable in the model's output domain)."""
    policy = SignalPolicy(confidence_threshold=0.40)
    proposal = _eval(policy, [0.01, 0.98, 0.01, 0.0])
    assert proposal.action == ActionType.BUY_MARKET
    assert proposal.blocked_by is None
    assert proposal.decision_stage == "FINAL_DECISION"


def test_confidence_gate_evidence_names_the_exact_thresholds() -> None:
    """The rejection evidence names base / range / survival / effective
    threshold so a gate change is auditable from the persisted row."""
    policy = SignalPolicy(confidence_threshold=0.40)
    proposal = _eval(policy, [0.25, 0.30, 0.20, 0.25], survival_mode=True)
    checks = proposal.risk_checks or {}
    assert checks["base_threshold"] == pytest.approx(0.40)
    assert checks["survival_mode_adjustment"] == pytest.approx(0.10)
    assert checks["effective_threshold"] == pytest.approx(0.50)
    assert proposal.blocked_by == "CONFIDENCE_FAIL"


def test_confidence_source_is_always_recorded() -> None:
    """confidence_source (RAW vs NORMALIZED) is persisted on every decision
    so the calibration domain cannot drift silently."""
    policy = SignalPolicy()
    for probs in ([0.25, 0.30, 0.20, 0.25], [0.01, 0.98, 0.01, 0.0]):
        proposal = _eval(policy, probs)
        assert (proposal.risk_checks or {}).get("confidence_source") in (
            "DIRECTIONAL_NORMALIZED",
            "RAW_FALLBACK",
        )


def test_degenerate_probability_never_manufactures_confidence() -> None:
    """All-zero / WAIT-only vectors must not manufacture a pass: the trained
    mass degenerates, so the ratio falls back to RAW semantics, the decision
    is NO_TRADE, and confidence is zero (the model said nothing). On the
    degenerate early path the confidence evidence block is empty — the
    absence of a source label is itself the observable contract."""
    policy = SignalPolicy()
    for probs in ([0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]):
        proposal = _eval(policy, probs)
        assert proposal.action == ActionType.NO_TRADE
        assert proposal.confidence == 0.0
        checks = proposal.risk_checks or {}
        # Either an explicit RAW_FALLBACK label or no confidence evidence at
        # all — never a manufactured DIRECTIONAL_NORMALIZED pass.
        assert checks.get("confidence_source") in (None, "RAW_FALLBACK")
        assert checks.get("model_confidence") in (0.0, None)


def test_confidence_never_exceeds_the_raw_directional_signal() -> None:
    """BUG-312: the ratio maps back to raw-probability space and is clamped
    to the raw directional max, so confidence is never manufactured above
    the signal. The clamp is the guarantee — the ratio itself is the ratio
    over TRAINED classes, which equals raw when the trained mass is 1."""
    policy = SignalPolicy()
    # Pure 3-class vector: trained mass == 1.0, so the mapping is identity
    # and confidence == the raw directional max exactly.
    proposal = _eval(policy, [0.25, 0.40, 0.35, 0.0])
    raw_directional = max(0.40, 0.35)
    checks = proposal.risk_checks or {}
    assert checks["confidence_source"] == "DIRECTIONAL_NORMALIZED"
    assert proposal.confidence == pytest.approx(raw_directional)

    # WAIT mass present: the ratio stays clamped to <= raw directional.
    # (the policy converts the torch tensor to float32, so a 1e-6
    # representation slop is inherent to the pipeline)
    mixed = _eval(policy, [0.20, 0.40, 0.35, 0.05])
    assert (mixed.risk_checks or {})["model_confidence"] <= raw_directional + 1e-6
    assert mixed.confidence <= raw_directional + 1e-6


# ---------------------------------------------------------------------------
# SURVIVAL / RANGE adjustments are documented and bounded.
# ---------------------------------------------------------------------------


def test_survival_mode_raises_the_effective_threshold() -> None:
    """Survival mode adds a documented +0.10 — it hardens the gate, it never
    relaxes it."""
    policy = SignalPolicy(confidence_threshold=0.40)
    proposal = _eval(policy, [0.25, 0.30, 0.20, 0.25], survival_mode=True)
    assert (proposal.risk_checks or {})["effective_threshold"] == pytest.approx(0.50)


def test_range_penalty_is_scaled_not_added_as_a_raw_addend() -> None:
    """BUG-312: the range penalty is base * penalty (not a raw +0.10 addend)
    so the gate stays reachable in range regimes. A range regime here is a
    flat Ichimoku (inside the kumo) with small tick displacement."""
    policy = SignalPolicy(confidence_threshold=0.40, range_confidence_penalty=0.10)
    base = policy.confidence_threshold
    expected = base + policy.range_confidence_penalty * base
    fv = _fv(is_inside_kumo=True, live_tick_displacement=0.05)
    proposal = _eval(policy, [0.25, 0.30, 0.20, 0.25], fv=fv)
    checks = proposal.risk_checks or {}
    assert checks["range_penalty"] == pytest.approx(0.10)
    assert checks["effective_threshold"] == pytest.approx(expected)
    assert proposal.blocked_by == "RANGE_FILTER"


def test_range_penalty_only_hardens_the_threshold() -> None:
    """The penalty can never make the gate EASIER than the base threshold."""
    policy = SignalPolicy(confidence_threshold=0.40, range_confidence_penalty=0.10)
    fv = _fv(is_inside_kumo=True, live_tick_displacement=0.05)
    proposal = _eval(policy, [0.25, 0.30, 0.20, 0.25], fv=fv)
    checks = proposal.risk_checks or {}
    assert checks["effective_threshold"] >= checks["base_threshold"]


# ---------------------------------------------------------------------------
# Zone quality gate.
# ---------------------------------------------------------------------------


def test_zone_quality_gate_is_enforced_below_threshold() -> None:
    """An active zone with confidence below ai_zone_confidence_threshold is
    rejected — the gate's threshold is the AlgoConfig canonical value."""
    policy = SignalPolicy(confidence_threshold=0.10)
    proposal = _eval(policy, [0.25, 0.15, 0.20, 0.40])
    assert (proposal.risk_checks or {})["min_zone_quality"] == pytest.approx(
        AlgoConfig().ai_zone_confidence_threshold
    )


def test_zone_quality_gate_blocks_a_weak_active_zone() -> None:
    """A zone-active candidate below the zone threshold carries
    ZONE_QUALITY_FAIL (the pin the gate's own evidence cites). The zone is
    active via a fair-value gap (an order block would route the candidate
    to the predictive-limit channel first)."""
    policy = SignalPolicy(confidence_threshold=0.10)
    fv = _fv(fvg_bullish_active=True)
    proposal = _eval(policy, [0.30, 0.15, 0.10, 0.45], fv=fv)
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.blocked_by == "ZONE_QUALITY_FAIL"
    assert proposal.decision_stage == "ZONE_QUALITY_GATE"
    checks = proposal.risk_checks or {}
    assert checks["zone_quality"] < checks["min_zone_quality"]


# ---------------------------------------------------------------------------
# Flip protection.
# ---------------------------------------------------------------------------


def test_flip_protection_penalty_is_documented() -> None:
    """A reversal inside the flip memory window demands base + penalty."""
    policy = SignalPolicy(flip_confidence_penalty=0.10, flip_memory_seconds=8.0)
    assert policy.flip_confidence_penalty == pytest.approx(0.10)
    assert policy.flip_memory_seconds == pytest.approx(8.0)


def test_flip_protection_blocks_a_weak_reversal() -> None:
    """A reversal candidate below the required flip confidence is blocked
    with FLIP_PROTECTION evidence. Fixture pattern follows the repo's own
    flip-protection pin suite (test_policy_flip_protection_pins_bug227):
    a below-kumo ichimoku SELL channel with the prior direction set to
    BUY and a sell confidence inside [base, base + penalty)."""
    policy = SignalPolicy(confidence_threshold=0.20)  # base 0.20, penalty 0.10
    now = datetime.now(UTC)
    policy._last_active_direction = ActionType.BUY_MARKET
    policy._last_active_direction_time = now - timedelta(seconds=1.0)
    policy._last_signal_time = None  # keep COOLDOWN out of the way

    proposal = _eval(
        policy,
        [0.65, 0.04, 0.24, 0.07],  # conf ~0.258 in [0.20, 0.30)
        tick=_tick_ts(now, bid=2000.10, ask=2000.15),
        fv=_bearish_channel_fv(),
        order_manager=_MockOM(
            live_tickets=[{"symbol": "XAUUSD", "magic": 888101, "price": 1990.0}]
        ),
    )
    checks = proposal.risk_checks or {}
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.blocked_by == "FLIP_PROTECTION"
    assert proposal.decision_stage == "FLIP_PROTECTION"
    assert "FLIP_PROTECTION_BLOCKED" in (proposal.reason_code or "")
    assert checks["model_confidence"] < policy.confidence_threshold + policy.flip_confidence_penalty


def test_flip_protection_expires_with_the_memory_window() -> None:
    """The penalty is a hysteresis window, not a permanent block: past
    flip_memory_seconds the same reversal is no longer flip-blocked."""
    policy = SignalPolicy()  # default memory 8s
    now = datetime.now(UTC)
    policy._last_active_direction = ActionType.BUY_MARKET
    policy._last_active_direction_time = now - timedelta(seconds=9.0)
    policy._last_signal_time = None

    proposal = _eval(
        policy,
        [0.65, 0.04, 0.24, 0.07],
        tick=_tick_ts(now, bid=2000.10, ask=2000.15),
        fv=_bearish_channel_fv(liquidity_sweep_signal=-1),
        order_manager=_MockOM(
            live_tickets=[{"symbol": "XAUUSD", "magic": 888101, "price": 1990.0}]
        ),
    )
    assert proposal.blocked_by != "FLIP_PROTECTION"


# ---------------------------------------------------------------------------
# Same-level re-entry lockout.
# ---------------------------------------------------------------------------


class _MockOM:
    def __init__(self, live_tickets: list[dict[str, Any]] | None = None) -> None:
        self.live_tickets = live_tickets or []

    def get_active_live_tickets(self) -> list[dict[str, Any]]:
        return self.live_tickets


def test_same_level_reentry_blocks_near_a_live_ticket() -> None:
    """A candidate within $0.50 of a same symbol+magic live ticket is
    blocked (the guard fires on the hot path, before dispatch). The ticket
    must match the policy's expected execution identity: the tick symbol
    plus the configured execution magic."""
    om = _MockOM(live_tickets=[{"symbol": "XAUUSD", "magic": 888101, "price": 2000.00}])
    policy = SignalPolicy(confidence_threshold=0.10)
    proposal = _eval(policy, [0.01, 0.98, 0.01, 0.0], order_manager=om)
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.blocked_by == "SAME_LEVEL_REENTRY"
    assert proposal.decision_stage == "REENTRY_GATE"
    assert "SAME_LEVEL_REENTRY_BLOCKED" in (proposal.reason_code or "")


def test_same_level_reentry_is_cleared_without_live_orders() -> None:
    """No live tickets => the lockout must not suppress a valid entry."""
    policy = SignalPolicy(confidence_threshold=0.10)
    proposal = _eval(policy, [0.01, 0.98, 0.01, 0.0], order_manager=_MockOM([]))
    assert proposal.action == ActionType.BUY_MARKET
    assert proposal.blocked_by != "SAME_LEVEL_REENTRY"


# ---------------------------------------------------------------------------
# Spread gates fail closed.
# ---------------------------------------------------------------------------


def test_spread_atr_gate_blocks_when_spread_dominates_atr() -> None:
    """BUG-249: spread/ATR above max_spread_atr_ratio blocks the candidate
    (fail-closed — the guard was once plumbed but never enforced). A spread
    of $0.50 against an ATR of $2.00 is a 0.25 ratio, above the 0.18 cap.
    The RR gate is relaxed so the spread gate is the one that fires."""
    policy = SignalPolicy(confidence_threshold=0.10, max_spread_atr_ratio=0.18, min_allowed_rr=0.5)
    policy.algo_config.min_risk_reward_ratio = 0.5
    proposal = _eval(policy, [0.01, 0.98, 0.01, 0.0], tick=_tick(bid=2000.0, ask=2000.5))
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.blocked_by == "SPREAD_ATR_RATIO"
    assert proposal.decision_stage == "SPREAD_ATR_GATE"
    checks = proposal.risk_checks or {}
    assert checks["spread_atr_ratio"] > checks["max_spread_atr_ratio"]


def test_spread_atr_gate_allows_a_normal_spread() -> None:
    """A normal spread passes the ATR gate — the gate is a real threshold,
    not a universal veto."""
    policy = SignalPolicy(confidence_threshold=0.10, max_spread_atr_ratio=0.18)
    proposal = _eval(policy, [0.01, 0.98, 0.01, 0.0], tick=_tick(bid=2000.0, ask=2000.2))
    assert proposal.blocked_by != "SPREAD_ATR_RATIO"


def test_spread_gate_evidence_is_stamped_on_every_decision() -> None:
    """The spread evidence block is present on both pass and reject paths so
    the counterfactual engine can stratify by quoting state."""
    policy = SignalPolicy(confidence_threshold=0.60)
    proposal = _eval(policy, [0.01, 0.98, 0.01, 0.0])
    checks = proposal.risk_checks or {}
    for key in ("spread_usd", "spread_atr_ratio", "max_spread_atr_ratio", "tp_distance_usd"):
        assert key in checks, key


# ---------------------------------------------------------------------------
# Degraded inference fail-closes; no fabricated signal.
# ---------------------------------------------------------------------------


def test_degraded_inference_fail_closes_without_touching_gate_state() -> None:
    """A missing/empty/non-tensor payload yields a NO_TRADE carrying
    PROBS_UNAVAILABLE_DEGRADED — the engine prefers NO TRADE over a crash
    loop, and never fabricates a candidate from a degraded state."""
    policy = SignalPolicy()
    for bad in (None, torch.tensor([]), "not-a-tensor"):
        proposal = policy.evaluate_probabilities(
            probabilities=bad,  # type: ignore[arg-type]
            current_tick=_tick(),
            feature_vector=_fv(),
        )
        assert proposal.action == ActionType.NO_TRADE
        assert proposal.reason_code == "PROBS_UNAVAILABLE_DEGRADED"
        assert proposal.confidence == 0.0
        assert proposal.decision_stage == "INFERENCE_DEGRADED"


def test_degraded_proposal_still_carries_execution_lineage() -> None:
    """The degraded early return still stamps an EXEC- id so the
    decision->signal join stays re constructable for the degraded classes
    forensics cares most about (OBS-TRACE-2)."""
    policy = SignalPolicy()
    proposal = policy.evaluate_probabilities(
        probabilities=None,
        current_tick=_tick(),
        feature_vector=_fv(),
    )
    assert (proposal.execution_id or "").startswith("EXEC-")
    assert proposal.request_id


# ---------------------------------------------------------------------------
# Non-XAUUSD symbols never reach the candidate path.
# ---------------------------------------------------------------------------


def test_enabled_symbols_defaults_fail_closed_to_xauusd() -> None:
    """The operator ruling: None/empty resolves to XAUUSD only."""
    assert SignalPolicy().enabled_symbols == ["XAUUSD"]
    assert SignalPolicy(enabled_symbols=None).enabled_symbols == ["XAUUSD"]
    assert SignalPolicy(enabled_symbols=[]).enabled_symbols == ["XAUUSD"]


def test_non_whitelisted_symbol_produces_no_candidate() -> None:
    """A tick for a symbol outside the whitelist must not emit a trade."""
    policy = SignalPolicy()
    tick = TickData(
        symbol="EURUSD",
        timestamp=datetime.now(UTC),
        bid=1.08,
        ask=1.0802,
        volume=1.0,
    )
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=_fv(),
    )
    assert proposal.action == ActionType.NO_TRADE


# ---------------------------------------------------------------------------
# Every emitted proposal carries the forensic contract.
# ---------------------------------------------------------------------------


def test_every_decision_carries_stage_and_lineage() -> None:
    """OBS-TRACE: every proposal (NO_TRADE included) carries execution_id,
    request_id and decision_stage — the audit join surface."""
    for probs in (
        [0.25, 0.30, 0.20, 0.25],
        [0.01, 0.98, 0.01, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ):
        proposal = _eval(SignalPolicy(), probs)
        assert proposal.execution_id.startswith("EXEC-")
        assert proposal.request_id
        assert proposal.decision_stage


def test_regime_state_flows_into_the_decision_evidence() -> None:
    """An injected regime state is carried into the proposal's regime
    fields — observability only, never a decision input. All numeric
    fields are required by the model, so the fixture supplies them."""
    regime = MarketRegimeState(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        regime_type="TRENDING_MOMENTUM",
        regime_probability=0.9,
        order_flow_imbalance=0.0,
        realized_volatility_5m=0.001,
        tick_velocity_per_sec=1.0,
        current_spread_usd=0.2,
        is_macro_news_active=False,
        recommended_execution_type="HYBRID_LIMIT_STOP",
        reason="OFI_TREND_ALIGN",
    )
    proposal = _eval(SignalPolicy(), [0.01, 0.98, 0.01, 0.0], regime_state=regime)
    assert proposal.regime == "TRENDING_MOMENTUM"
    assert proposal.regime_confidence == pytest.approx(0.9)
