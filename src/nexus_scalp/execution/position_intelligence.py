"""Position intelligence: deterministic SmartMetrics kernel (Agent-5 P0 S4).

Extracted VERBATIM from execution/order_manager.py (behavior-preserving;
formulas untouched). PURE computation only: zero broker/audit/notifier
I/O, zero state writes — every input is explicit (SmartMetricsInputs), so
identical outputs are guaranteed for identical inputs.

Units (documented, NOT normalized): prices in quote currency; ATR/spread
in price units; USD via contract-size math; holding_duration in seconds;
tick pressures are ratios; scores are 0..100 unless noted.

USED BY: execution/order_manager.py (facade delegates; the manager keeps
ownership of all execution decisions and state mutation).
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass
from typing import Any

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo
from nexus_scalp.features.scalp_features import FeatureVector


@dataclass
class SmartMetricsInputs:
    """Explicit immutable inputs for calculate_smart_metrics."""

    pos: Position
    price_current: float
    mid_price: float
    spread: float
    atr: float
    net_price_delta: float
    gross_price_delta: float
    impact_price_delta: float
    total_impact_usd: float
    holding_duration: float
    features: FeatureVector | None
    symbol_info: SymbolInfo | None
    be_trigger: float
    trailing_distance: float
    max_holding_seconds: float
    atr_sl_buffer_multiplier: float
    rescue_registered: bool
    lsf_desync_score: float
    mfe: float
    mae: float
    adverse_ticks: int
    favorable_ticks: int
    stagnation_ticks: int


def _safe_feature_float(
    self, features: FeatureVector | None, attr_name: str, default: float
) -> float:
    """Safely extracts a floating point attribute from FeatureVector with fallback."""
    if features is None:
        return default
    val = getattr(features, attr_name, default)
    try:
        fval = float(val)
        if math.isnan(fval) or math.isinf(fval):
            return default
        return fval
    except (TypeError, ValueError):
        return default


def _current_regime_str(
    regime_state: Any | None, ticket: int, entry_regimes: dict[int, str]
) -> str:
    """
    Resolves the CURRENT market-regime label for a ticket.

    Prefers the live `regime_state` threaded from the engine (Phase 15 exit
    audit); falls back to the entry snapshot when the live state is absent
    (e.g. unit tests, warmup-gated ticks), so regime-aware exit logic never
    crashes on a missing input.
    """
    if regime_state is not None:
        with contextlib.suppress(Exception):
            regime = getattr(regime_state, "regime_type", None)
            if regime is not None:
                return str(getattr(regime, "value", regime))
            return str(regime_state)
    return (entry_regimes or {}).get(ticket, "")


def _estimate_liquidation_impact(
    volume: float,
    symbol_info: SymbolInfo | None,
    atr: float,
    eta_coefficient: float,
) -> tuple[float, float]:
    """
    Almgren-Chriss Temporary Market Impact Model (Strict O(1) Math).
    Calculates the expected slippage incurred if the position were to be liquidated via Market Order.
    Returns: (Total Impact in USD, Impact Price Delta per Oz)
    """
    contract_size = 100.0
    if symbol_info and symbol_info.trade_contract_size > 0:
        contract_size = symbol_info.trade_contract_size

    vol_factor = max(atr, 0.50)
    size_ratio = volume / contract_size

    total_impact_usd = eta_coefficient * size_ratio * vol_factor
    impact_price_delta = total_impact_usd / max(volume * contract_size, 1.0)

    return total_impact_usd, impact_price_delta


# =========================================================================
# LSF: LOCAL STATE FEATURES & DESYNC METRICS ENGINE
# =========================================================================


def calculate_smart_metrics(inputs: SmartMetricsInputs) -> dict[str, Any]:
    """Calculates 57 derived O(1) position metrics."""
    pos = inputs.pos
    spread = inputs.spread
    atr = inputs.atr
    net_price_delta = inputs.net_price_delta
    gross_price_delta = inputs.gross_price_delta
    impact_price_delta = inputs.impact_price_delta
    holding_duration = inputs.holding_duration
    features = inputs.features
    symbol_info = inputs.symbol_info
    be_trigger = inputs.be_trigger
    trailing_distance = inputs.trailing_distance
    max_holding_seconds = inputs.max_holding_seconds
    atr_sl_buffer_multiplier = inputs.atr_sl_buffer_multiplier
    rescue_registered = inputs.rescue_registered
    lsf_desync_score = inputs.lsf_desync_score
    mfe = inputs.mfe
    mae = inputs.mae
    adverse_ticks = inputs.adverse_ticks
    favorable_ticks = inputs.favorable_ticks
    stagnation_ticks = inputs.stagnation_ticks

    eps = 1e-9

    total_ticks = max(adverse_ticks + favorable_ticks + stagnation_ticks, 1)

    spread_to_atr_ratio = spread / max(atr, eps)
    impact_to_atr_ratio = impact_price_delta / max(atr, eps)
    net_to_atr_ratio = net_price_delta / max(atr, eps)
    gross_to_atr_ratio = gross_price_delta / max(atr, eps)
    mae_to_atr_ratio = abs(min(mae, 0.0)) / max(atr, eps)
    mfe_to_atr_ratio = max(mfe, 0.0) / max(atr, eps)

    mfe_mae_efficiency = max(mfe, 0.0) / max(abs(mae), 0.10)
    mfe_giveback = max(mfe - gross_price_delta, 0.0)
    mfe_giveback_ratio = mfe_giveback / max(abs(mfe), 0.10)

    adverse_tick_pressure = adverse_ticks / total_ticks
    favorable_tick_pressure = favorable_ticks / total_ticks
    stagnation_pressure = stagnation_ticks / total_ticks

    time_decay_ratio = holding_duration / max(max_holding_seconds, 1.0)
    position_age_bucket = math.floor(holding_duration / 60.0)

    contract_size = (
        symbol_info.trade_contract_size
        if symbol_info and symbol_info.trade_contract_size > 0
        else 100.0
    )
    position_size_pressure = pos.volume / 1.0
    volume_step = symbol_info.volume_step if symbol_info and symbol_info.volume_step > 0 else 0.01
    volume_step_pressure = (pos.volume % volume_step) / volume_step
    contract_pressure = pos.volume * contract_size / 100.0
    liquidity_depletion_score = impact_to_atr_ratio * position_size_pressure

    # -------------------------------------------------------------
    # FIXED ASYMPTOTE BUG: Bounded toxicity calculation
    # -------------------------------------------------------------
    impact_to_net_profit_ratio = min(
        5.0, impact_price_delta / max(abs(net_price_delta), atr * 0.5, 0.10)
    )
    impact_to_gross_ratio = min(
        5.0, impact_price_delta / max(abs(gross_price_delta), atr * 0.5, 0.10)
    )

    spread_impact_combo = spread_to_atr_ratio + impact_to_atr_ratio

    breakeven_quality = max(0.0, net_price_delta - be_trigger)
    trailing_quality = max(0.0, net_price_delta - (be_trigger + trailing_distance))

    risk_reward_decay = mae_to_atr_ratio / max(mfe_to_atr_ratio, 0.10)
    unrealized_recovery_ratio = (gross_price_delta - mae) / max(mfe - mae, eps)
    adverse_excursion_velocity = abs(min(mae, 0.0)) / max(holding_duration, 1.0)
    favorable_excursion_velocity = max(mfe, 0.0) / max(holding_duration, 1.0)

    missed_position_rescue_mode = bool(holding_duration > 120.0 and not rescue_registered)
    no_sl_risk = bool(pos.sl <= 0.0)

    entry_to_sl_dist = abs(pos.price_open - pos.sl) if pos.sl > 0 else (atr * 1.5)
    entry_to_tp_dist = abs(pos.tp - pos.price_open) if pos.tp > 0 else (atr * 2.5)
    sl_distance_to_atr = entry_to_sl_dist / max(atr, eps)
    tp_distance_to_atr = entry_to_tp_dist / max(atr, eps)
    sl_tp_asymmetry = tp_distance_to_atr / max(sl_distance_to_atr, eps)

    min_stop_gap = (
        (symbol_info.stops_level * symbol_info.point)
        if symbol_info and symbol_info.stops_level > 0
        else 0.25
    )
    stop_gap_pressure = min_stop_gap / max(atr, eps)
    wick_tolerance_pressure = (atr * 0.50) / max(atr, eps)

    volatility_regime_score = atr / 1.50
    trend_conflict_score = 0.0
    kumo_conflict_score = 0.0
    choch_conflict_score = 0.0
    liquidity_sweep_conflict_score = 0.0
    tenkan_kijun_conflict_score = 0.0
    extreme_entry_conflict_score = 0.0
    rapid_reversal_conflict_score = 0.0

    if features:
        if (pos.type == OrderType.BUY and features.is_below_kumo) or (
            pos.type == OrderType.SELL and features.is_above_kumo
        ):
            kumo_conflict_score = 1.0
        if (pos.type == OrderType.BUY and features.choch_bearish) or (
            pos.type == OrderType.SELL and features.choch_bullish
        ):
            choch_conflict_score = 1.0
        if (pos.type == OrderType.BUY and features.liquidity_sweep_signal == -1) or (
            pos.type == OrderType.SELL and features.liquidity_sweep_signal == 1
        ):
            liquidity_sweep_conflict_score = 1.0
        if (pos.type == OrderType.BUY and features.tenkan_sen < features.kijun_sen) or (
            pos.type == OrderType.SELL and features.tenkan_sen > features.kijun_sen
        ):
            tenkan_kijun_conflict_score = 1.0
        if (pos.type == OrderType.BUY and features.is_at_extreme_high) or (
            pos.type == OrderType.SELL and features.is_at_extreme_low
        ):
            extreme_entry_conflict_score = 1.0
        if features.rapid_reversal_spike:
            rapid_reversal_conflict_score = 1.0

    directional_conflict_score = (
        (kumo_conflict_score * 0.25)
        + (choch_conflict_score * 0.25)
        + (liquidity_sweep_conflict_score * 0.20)
        + (tenkan_kijun_conflict_score * 0.15)
        + (extreme_entry_conflict_score * 0.15)
    )

    desync_score = lsf_desync_score
    desync_risk_flag = bool(desync_score > 15.0)

    danger_tier = "NORMAL"
    kill_switch_required = False
    time_decay_exit_required = False
    soft_rescue_exit_required = False

    if (
        directional_conflict_score >= 0.70 and net_price_delta < -(atr * 0.40)
    ) or desync_score > 30.0:
        danger_tier = "CRITICAL_KILL"
        kill_switch_required = True
    elif directional_conflict_score >= 0.50 or mae_to_atr_ratio > 1.20 or desync_risk_flag:
        danger_tier = "HIGH_DANGER"
    elif directional_conflict_score >= 0.30 or stagnation_pressure > 0.60:
        danger_tier = "ELEVATED_RISK"
    elif spread_to_atr_ratio > 0.20:
        danger_tier = "MODERATE_WARN"

    if holding_duration > max_holding_seconds and net_price_delta < 0.10:
        time_decay_exit_required = True

    if mae < -(atr * 0.80) and mfe < (atr * 0.20) and net_price_delta < 0.0:
        soft_rescue_exit_required = True

    defer_stop_management = bool(impact_to_net_profit_ratio > 0.60 or desync_risk_flag)
    defer_scale_out = bool(spread_to_atr_ratio > 0.25 or desync_risk_flag)

    # Dynamically scale triggers based on the dynamic AlgoConfig ATR stop multiplier
    atr_multiplier = atr_sl_buffer_multiplier
    smart_be_trigger = max(be_trigger, round(atr * 0.30 * atr_multiplier + impact_price_delta, 2))
    smart_trailing_distance = max(
        trailing_distance, round(atr * 0.80 * atr_multiplier + impact_price_delta, 2)
    )
    tp1_atr_multiplier = 1.50 + impact_to_atr_ratio

    rescue_quality_score = max(
        0.0, 100.0 - (desync_score * 2.0) - (directional_conflict_score * 40.0)
    )

    return {
        "spread_to_atr_ratio": spread_to_atr_ratio,
        "impact_to_atr_ratio": impact_to_atr_ratio,
        "net_to_atr_ratio": net_to_atr_ratio,
        "gross_to_atr_ratio": gross_to_atr_ratio,
        "mae_to_atr_ratio": mae_to_atr_ratio,
        "mfe_to_atr_ratio": mfe_to_atr_ratio,
        "mfe_mae_efficiency": mfe_mae_efficiency,
        "mfe_giveback_ratio": mfe_giveback_ratio,
        "adverse_tick_pressure": adverse_tick_pressure,
        "favorable_tick_pressure": favorable_tick_pressure,
        "stagnation_pressure": stagnation_pressure,
        "time_decay_ratio": time_decay_ratio,
        "position_age_bucket": position_age_bucket,
        "position_size_pressure": position_size_pressure,
        "volume_step_pressure": volume_step_pressure,
        "contract_pressure": contract_pressure,
        "liquidity_depletion_score": liquidity_depletion_score,
        "impact_to_net_profit_ratio": impact_to_net_profit_ratio,
        "impact_to_gross_ratio": impact_to_gross_ratio,
        "spread_impact_combo": spread_impact_combo,
        "breakeven_quality": breakeven_quality,
        "trailing_quality": trailing_quality,
        "risk_reward_decay": risk_reward_decay,
        "unrealized_recovery_ratio": unrealized_recovery_ratio,
        "adverse_excursion_velocity": adverse_excursion_velocity,
        "favorable_excursion_velocity": favorable_excursion_velocity,
        "stagnation_ticks": stagnation_ticks,
        "adverse_ticks": adverse_ticks,
        "favorable_ticks": favorable_ticks,
        "missed_position_rescue_mode": missed_position_rescue_mode,
        "no_sl_risk": no_sl_risk,
        "sl_distance_to_atr": sl_distance_to_atr,
        "tp_distance_to_atr": tp_distance_to_atr,
        "sl_tp_asymmetry": sl_tp_asymmetry,
        "stop_gap_pressure": stop_gap_pressure,
        "wick_tolerance_pressure": wick_tolerance_pressure,
        "volatility_regime_score": volatility_regime_score,
        "trend_conflict_score": trend_conflict_score,
        "kumo_conflict_score": kumo_conflict_score,
        "choch_conflict_score": choch_conflict_score,
        "liquidity_sweep_conflict_score": liquidity_sweep_conflict_score,
        "tenkan_kijun_conflict_score": tenkan_kijun_conflict_score,
        "extreme_entry_conflict_score": extreme_entry_conflict_score,
        "rapid_reversal_conflict_score": rapid_reversal_conflict_score,
        "directional_conflict_score": directional_conflict_score,
        "desync_score": desync_score,
        "desync_risk_flag": desync_risk_flag,
        "danger_tier": danger_tier,
        "kill_switch_required": kill_switch_required,
        "time_decay_exit_required": time_decay_exit_required,
        "soft_rescue_exit_required": soft_rescue_exit_required,
        "defer_stop_management": defer_stop_management,
        "defer_scale_out": defer_scale_out,
        "smart_be_trigger": smart_be_trigger,
        "smart_trailing_distance": smart_trailing_distance,
        "tp1_atr_multiplier": tp1_atr_multiplier,
        "rescue_quality_score": rescue_quality_score,
    }
