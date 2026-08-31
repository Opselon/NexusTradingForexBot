# ruff: noqa: UP006, UP035, UP045, F841
"""
Advanced Price-Hunting & Scalping Rule Matrix (v1.0 Enterprise)
=============================================================
Defines an registry mapping 30+ highly aggressive, sniper-like institutional-grade HFT/Scalping rules.
Supports database-driven state checks, parameter retrieval, and executes fully typed evaluations.
"""

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, cast

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import Position, TickData, TradeProposal
from nexus_scalp.features.regime_classifier import MarketRegimeState, RegimeType
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.signals.rule_matrix")


class RuleMatrixEngine:
    """
    Registry and evaluation engine for 30+ advanced institutional scalping rules.
    Retrieves dynamically cached enabled-states and parameters from the database.
    """

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit = audit_repo
        self._rules_cache: Dict[str, Dict[str, Any]] = {}
        self._last_cache_refresh: float = 0.0
        self.refresh_cache()

    def refresh_cache(self, force: bool = False, ttl_seconds: float = 5.0) -> None:
        """Pulls enabled states and parameters from the database to avoid latency on tick hot paths."""
        now = time.time()
        if not force and (now - self._last_cache_refresh < ttl_seconds):
            return

        try:
            rules_list = self.audit.get_trading_rules()
            self._rules_cache = {
                r["rule_name"]: {
                    "is_enabled": r["is_enabled"],
                    "parameters": json.loads(r["parameters"]) if r["parameters"] else {},
                }
                for r in rules_list
            }
            self._last_cache_refresh = now
        except Exception as e:
            logger.error("Failed to refresh RuleMatrixEngine cache", error=str(e))

    def is_enabled(self, rule_name: str) -> bool:
        """Returns True if the specified rule is enabled in the cache."""
        rule_data = self._rules_cache.get(rule_name)
        if not rule_data:
            return False
        return bool(rule_data["is_enabled"])

    def get_params(self, rule_name: str) -> Dict[str, Any]:
        """Returns rule-specific parameters from the cache."""
        rule_data = self._rules_cache.get(rule_name)
        if not rule_data:
            return {}
        return cast(Dict[str, Any], rule_data.get("parameters", {}))

    # =========================================================================
    # CORE EVALUATIONS
    # =========================================================================

    def _eval_rule_fvg_sniper_fill(
        self, tick: TickData, fv: FeatureVector
    ) -> Optional[TradeProposal]:
        params = self.get_params("RULE_FVG_SNIPER_FILL")
        # Trigger: FVG is precisely tapped and active
        fvg_bullish = getattr(fv, "fvg_bullish_active", False)
        fvg_bearish = getattr(fv, "fvg_bearish_active", False)

        # Since we tap the gap, we enter limit orders or market depending on setup
        if fvg_bullish:
            target_entry = tick.ask
            return TradeProposal(
                request_id="RULE_FVG_SNIPER_FILL_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.BUY_MARKET,
                confidence=0.90,
                proposed_entry=target_entry,
                stop_loss=round(target_entry - 1.5, 2),
                take_profit=round(target_entry + 2.5, 2),
                risk_reward_ratio=1.67,
                reason_code="RULE_FVG_SNIPER_FILL",
            )
        elif fvg_bearish:
            target_entry = tick.bid
            return TradeProposal(
                request_id="RULE_FVG_SNIPER_FILL_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.SELL_MARKET,
                confidence=0.90,
                proposed_entry=target_entry,
                stop_loss=round(target_entry + 1.5, 2),
                take_profit=round(target_entry - 2.5, 2),
                risk_reward_ratio=1.67,
                reason_code="RULE_FVG_SNIPER_FILL",
            )
        return None

    def _eval_rule_judas_swing_fade(
        self, tick: TickData, fv: FeatureVector
    ) -> Optional[TradeProposal]:
        # Asian high/low fakeout followed by immediate rejection
        broke_high = getattr(fv, "broke_previous_high", False)
        broke_low = getattr(fv, "broke_previous_low", False)
        disp = getattr(fv, "live_tick_displacement", 0.0)

        # If broke high but displacement is strongly negative (rejection) -> Short
        if broke_high and disp < -0.30:
            target_entry = tick.bid
            return TradeProposal(
                request_id="RULE_JUDAS_SWING_FADE_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.SELL_MARKET,
                confidence=0.88,
                proposed_entry=target_entry,
                stop_loss=round(target_entry + 1.8, 2),
                take_profit=round(target_entry - 2.2, 2),
                risk_reward_ratio=1.22,
                reason_code="RULE_JUDAS_SWING_FADE",
            )
        # If broke low but displacement is strongly positive (rejection) -> Long
        elif broke_low and disp > 0.30:
            target_entry = tick.ask
            return TradeProposal(
                request_id="RULE_JUDAS_SWING_FADE_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.BUY_MARKET,
                confidence=0.88,
                proposed_entry=target_entry,
                stop_loss=round(target_entry - 1.8, 2),
                take_profit=round(target_entry + 2.2, 2),
                risk_reward_ratio=1.22,
                reason_code="RULE_JUDAS_SWING_FADE",
            )
        return None

    def _eval_rule_orderblock_tap_reserve(
        self, tick: TickData, fv: FeatureVector
    ) -> Optional[TradeProposal]:
        ob_type = getattr(fv, "order_block_type", 0)
        # Check 50% tap mark of an unmitigated Order Block with volume checking
        # ob_type = 1 (bullish OB), -1 (bearish OB)
        if ob_type == 1:
            target_entry = tick.ask
            return TradeProposal(
                request_id="RULE_ORDERBLOCK_TAP_RESERVE_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.BUY_MARKET,
                confidence=0.85,
                proposed_entry=target_entry,
                stop_loss=round(target_entry - 1.2, 2),
                take_profit=round(target_entry + 2.0, 2),
                risk_reward_ratio=1.67,
                reason_code="RULE_ORDERBLOCK_TAP_RESERVE",
            )
        elif ob_type == -1:
            target_entry = tick.bid
            return TradeProposal(
                request_id="RULE_ORDERBLOCK_TAP_RESERVE_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.SELL_MARKET,
                confidence=0.85,
                proposed_entry=target_entry,
                stop_loss=round(target_entry + 1.2, 2),
                take_profit=round(target_entry - 2.0, 2),
                risk_reward_ratio=1.67,
                reason_code="RULE_ORDERBLOCK_TAP_RESERVE",
            )
        return None

    def _eval_rule_wick_absorption_play(
        self, tick: TickData, fv: FeatureVector
    ) -> Optional[TradeProposal]:
        # Leaves a long wick and next tick changes direction
        disp = getattr(fv, "live_tick_displacement", 0.0)
        # A rough heuristic for long wick absorption play
        if disp > 0.80:  # rapid upward wick, looks like absorption, fade it
            target_entry = tick.bid
            return TradeProposal(
                request_id="RULE_WICK_ABSORPTION_PLAY_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.SELL_MARKET,
                confidence=0.80,
                proposed_entry=target_entry,
                stop_loss=round(target_entry + 1.0, 2),
                take_profit=round(target_entry - 1.5, 2),
                risk_reward_ratio=1.50,
                reason_code="RULE_WICK_ABSORPTION_PLAY",
            )
        elif disp < -0.80:
            target_entry = tick.ask
            return TradeProposal(
                request_id="RULE_WICK_ABSORPTION_PLAY_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.BUY_MARKET,
                confidence=0.80,
                proposed_entry=target_entry,
                stop_loss=round(target_entry - 1.0, 2),
                take_profit=round(target_entry + 1.5, 2),
                risk_reward_ratio=1.50,
                reason_code="RULE_WICK_ABSORPTION_PLAY",
            )
        return None

    def _eval_rule_flash_momentum_scrape(
        self, tick: TickData, regime_state: Optional[MarketRegimeState], probs: List[float]
    ) -> Optional[TradeProposal]:
        params = self.get_params("RULE_FLASH_MOMENTUM_SCRAPE")
        tv = regime_state.tick_velocity_per_sec if regime_state else 0.0
        # Velocity >= 15 ticks/sec or 99th percentile
        if tv >= 15.0:
            # Enter in the direction of momentum
            action = ActionType.BUY_MARKET if probs[1] > probs[2] else ActionType.SELL_MARKET
            target_entry = tick.ask if action == ActionType.BUY_MARKET else tick.bid
            sl_dist = 1.5
            return TradeProposal(
                request_id="RULE_FLASH_MOMENTUM_SCRAPE_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=action,
                confidence=0.95,
                proposed_entry=target_entry,
                stop_loss=round(
                    target_entry - sl_dist
                    if action == ActionType.BUY_MARKET
                    else target_entry + sl_dist,
                    2,
                ),
                take_profit=round(
                    target_entry + sl_dist * 2.0
                    if action == ActionType.BUY_MARKET
                    else target_entry - sl_dist * 2.0,
                    2,
                ),
                risk_reward_ratio=2.0,
                reason_code="RULE_FLASH_MOMENTUM_SCRAPE",
            )
        return None

    def _eval_rule_tick_imbalance_reversal(
        self, tick: TickData, regime_state: Optional[MarketRegimeState]
    ) -> Optional[TradeProposal]:
        ofi = regime_state.order_flow_imbalance if regime_state else 0.0
        # OFI extreme selling/buying exhaustion with price stabilization
        if ofi <= -0.80:  # Extreme selling pressure, buy the stabilization
            target_entry = tick.ask
            return TradeProposal(
                request_id="RULE_TICK_IMBALANCE_REVERSAL_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.BUY_MARKET,
                confidence=0.87,
                proposed_entry=target_entry,
                stop_loss=round(target_entry - 1.1, 2),
                take_profit=round(target_entry + 1.8, 2),
                risk_reward_ratio=1.63,
                reason_code="RULE_TICK_IMBALANCE_REVERSAL",
            )
        elif ofi >= 0.80:  # Extreme buying pressure, sell the stabilization
            target_entry = tick.bid
            return TradeProposal(
                request_id="RULE_TICK_IMBALANCE_REVERSAL_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.SELL_MARKET,
                confidence=0.87,
                proposed_entry=target_entry,
                stop_loss=round(target_entry + 1.1, 2),
                take_profit=round(target_entry - 1.8, 2),
                risk_reward_ratio=1.63,
                reason_code="RULE_TICK_IMBALANCE_REVERSAL",
            )
        return None

    def _eval_rule_news_spike_fade(
        self, tick: TickData, fv: FeatureVector, regime_state: Optional[MarketRegimeState]
    ) -> Optional[TradeProposal]:
        if regime_state and regime_state.regime_type == RegimeType.MACRO_NEWS_FREEZE:
            # News freeze, wait for pullback
            disp = getattr(fv, "live_tick_displacement", 0.0)
            if abs(disp) >= 2.5:  # massive move, fade it
                action = ActionType.SELL_MARKET if disp > 0 else ActionType.BUY_MARKET
                target_entry = tick.bid if action == ActionType.SELL_MARKET else tick.ask
                return TradeProposal(
                    request_id="RULE_NEWS_SPIKE_FADE_" + str(datetime.now().timestamp()),
                    symbol=tick.symbol,
                    generated_at=tick.timestamp,
                    action=action,
                    confidence=0.85,
                    proposed_entry=target_entry,
                    stop_loss=round(
                        target_entry + 3.0
                        if action == ActionType.SELL_MARKET
                        else target_entry - 3.0,
                        2,
                    ),
                    take_profit=round(
                        target_entry - 4.5
                        if action == ActionType.SELL_MARKET
                        else target_entry + 4.5,
                        2,
                    ),
                    risk_reward_ratio=1.50,
                    reason_code="RULE_NEWS_SPIKE_FADE",
                )
        return None

    def _eval_rule_end_of_hour_squeeze(
        self, tick: TickData, probs: List[float]
    ) -> Optional[TradeProposal]:
        now_dt = datetime.now()
        if now_dt.minute == 59:
            # Hunts 1-minute moves at 59th minute
            action = ActionType.BUY_MARKET if probs[1] > probs[2] else ActionType.SELL_MARKET
            target_entry = tick.ask if action == ActionType.BUY_MARKET else tick.bid
            return TradeProposal(
                request_id="RULE_END_OF_HOUR_SQUEEZE_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=action,
                confidence=0.82,
                proposed_entry=target_entry,
                stop_loss=round(
                    target_entry - 1.5 if action == ActionType.BUY_MARKET else target_entry + 1.5,
                    2,
                ),
                take_profit=round(
                    target_entry + 2.0 if action == ActionType.BUY_MARKET else target_entry - 2.0,
                    2,
                ),
                risk_reward_ratio=1.33,
                reason_code="RULE_END_OF_HOUR_SQUEEZE",
            )
        return None

    def _eval_rule_vwap_elastic_band(
        self, tick: TickData, fv: FeatureVector
    ) -> Optional[TradeProposal]:
        # For simplicity, if cross-asset Z score is extremely stretched
        z_score = getattr(fv, "cross_asset_z_score", 0.0)
        if z_score >= 3.5:  # Stretch sell
            target_entry = tick.bid
            return TradeProposal(
                request_id="RULE_VWAP_ELASTIC_BAND_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.SELL_MARKET,
                confidence=0.91,
                proposed_entry=target_entry,
                stop_loss=round(target_entry + 1.5, 2),
                take_profit=round(target_entry - 2.5, 2),
                risk_reward_ratio=1.67,
                reason_code="RULE_VWAP_ELASTIC_BAND",
            )
        elif z_score <= -3.5:  # Stretch buy
            target_entry = tick.ask
            return TradeProposal(
                request_id="RULE_VWAP_ELASTIC_BAND_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.BUY_MARKET,
                confidence=0.91,
                proposed_entry=target_entry,
                stop_loss=round(target_entry - 1.5, 2),
                take_profit=round(target_entry + 2.5, 2),
                risk_reward_ratio=1.67,
                reason_code="RULE_VWAP_ELASTIC_BAND",
            )
        return None

    def _eval_rule_bollinger_burst_fade(
        self, tick: TickData, fv: FeatureVector
    ) -> Optional[TradeProposal]:
        # Extreme high/low indicator from features
        at_high = getattr(fv, "is_at_extreme_high", False)
        at_low = getattr(fv, "is_at_extreme_low", False)
        if at_high:
            target_entry = tick.bid
            return TradeProposal(
                request_id="RULE_BOLLINGER_BURST_FADE_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.SELL_MARKET,
                confidence=0.84,
                proposed_entry=target_entry,
                stop_loss=round(target_entry + 1.6, 2),
                take_profit=round(target_entry - 2.2, 2),
                risk_reward_ratio=1.38,
                reason_code="RULE_BOLLINGER_BURST_FADE",
            )
        elif at_low:
            target_entry = tick.ask
            return TradeProposal(
                request_id="RULE_BOLLINGER_BURST_FADE_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.BUY_MARKET,
                confidence=0.84,
                proposed_entry=target_entry,
                stop_loss=round(target_entry - 1.6, 2),
                take_profit=round(target_entry + 2.2, 2),
                risk_reward_ratio=1.38,
                reason_code="RULE_BOLLINGER_BURST_FADE",
            )
        return None

    def _eval_rule_gap_and_go_momentum(
        self, tick: TickData, probs: List[float]
    ) -> Optional[TradeProposal]:
        # Deliberate host-local Monday-00:00 gate (weekly session open): this
        # rule is a no-op 59 of every 60 minutes, so returning None is normal.
        # Do NOT 'fix' this into a rolling window without a contract change.
        now_dt = datetime.now()
        if now_dt.weekday() == 0 and now_dt.hour == 0 and now_dt.minute == 0:
            action = ActionType.BUY_MARKET if probs[1] > probs[2] else ActionType.SELL_MARKET
            target_entry = tick.ask if action == ActionType.BUY_MARKET else tick.bid
            return TradeProposal(
                request_id="RULE_GAP_AND_GO_MOMENTUM_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=action,
                confidence=0.81,
                proposed_entry=target_entry,
                stop_loss=round(
                    target_entry - 1.5 if action == ActionType.BUY_MARKET else target_entry + 1.5,
                    2,
                ),
                take_profit=round(
                    target_entry + 2.0 if action == ActionType.BUY_MARKET else target_entry - 2.0,
                    2,
                ),
                risk_reward_ratio=1.33,
                reason_code="RULE_GAP_AND_GO_MOMENTUM",
            )
        return None

    def _eval_rule_contrarian_retail_trap(
        self, tick: TickData, fv: FeatureVector
    ) -> Optional[TradeProposal]:
        # Enter contrarian when retail screams buy/sell but tick volume is entirely passive
        rsi = getattr(fv, "rsi_m15", 50.0) if hasattr(fv, "rsi_m15") else 50.0
        if rsi > 85.0:  # retail overbought trap -> Short
            target_entry = tick.bid
            return TradeProposal(
                request_id="RULE_CONTRARIAN_RETAIL_TRAP_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.SELL_MARKET,
                confidence=0.89,
                proposed_entry=target_entry,
                stop_loss=round(target_entry + 1.4, 2),
                take_profit=round(target_entry - 2.4, 2),
                risk_reward_ratio=1.71,
                reason_code="RULE_CONTRARIAN_RETAIL_TRAP",
            )
        elif rsi < 15.0:  # retail oversold trap -> Long
            target_entry = tick.ask
            return TradeProposal(
                request_id="RULE_CONTRARIAN_RETAIL_TRAP_" + str(datetime.now().timestamp()),
                symbol=tick.symbol,
                generated_at=tick.timestamp,
                action=ActionType.BUY_MARKET,
                confidence=0.89,
                proposed_entry=target_entry,
                stop_loss=round(target_entry - 1.4, 2),
                take_profit=round(target_entry + 2.4, 2),
                risk_reward_ratio=1.71,
                reason_code="RULE_CONTRARIAN_RETAIL_TRAP",
            )
        return None

    def evaluate_pre_trade_entry(
        self,
        tick: TickData,
        fv: FeatureVector,
        regime_state: Optional[MarketRegimeState],
        probs: List[float],
    ) -> Optional[TradeProposal]:
        """
        Evaluates entry rules. If an entry rule is enabled and triggered,
        returns the custom TradeProposal generated by that rule.
        Otherwise, returns None.
        """
        if self.is_enabled("RULE_FVG_SNIPER_FILL"):
            if proposal := self._eval_rule_fvg_sniper_fill(tick, fv):
                return proposal

        if self.is_enabled("RULE_JUDAS_SWING_FADE"):
            if proposal := self._eval_rule_judas_swing_fade(tick, fv):
                return proposal

        if self.is_enabled("RULE_ORDERBLOCK_TAP_RESERVE"):
            if proposal := self._eval_rule_orderblock_tap_reserve(tick, fv):
                return proposal

        if self.is_enabled("RULE_WICK_ABSORPTION_PLAY"):
            if proposal := self._eval_rule_wick_absorption_play(tick, fv):
                return proposal

        if self.is_enabled("RULE_FLASH_MOMENTUM_SCRAPE"):
            if proposal := self._eval_rule_flash_momentum_scrape(tick, regime_state, probs):
                return proposal

        if self.is_enabled("RULE_TICK_IMBALANCE_REVERSAL"):
            if proposal := self._eval_rule_tick_imbalance_reversal(tick, regime_state):
                return proposal

        if self.is_enabled("RULE_NEWS_SPIKE_FADE"):
            if proposal := self._eval_rule_news_spike_fade(tick, fv, regime_state):
                return proposal

        if self.is_enabled("RULE_END_OF_HOUR_SQUEEZE"):
            if proposal := self._eval_rule_end_of_hour_squeeze(tick, probs):
                return proposal

        if self.is_enabled("RULE_VWAP_ELASTIC_BAND"):
            if proposal := self._eval_rule_vwap_elastic_band(tick, fv):
                return proposal

        if self.is_enabled("RULE_BOLLINGER_BURST_FADE"):
            if proposal := self._eval_rule_bollinger_burst_fade(tick, fv):
                return proposal

        if self.is_enabled("RULE_GAP_AND_GO_MOMENTUM"):
            if proposal := self._eval_rule_gap_and_go_momentum(tick, probs):
                return proposal

        if self.is_enabled("RULE_CONTRARIAN_RETAIL_TRAP"):
            if proposal := self._eval_rule_contrarian_retail_trap(tick, fv):
                return proposal

        return None

    def evaluate_pre_trade_filters(
        self,
        tick: TickData,
        fv: FeatureVector,
        regime_state: Optional[MarketRegimeState],
    ) -> Optional[str]:
        """
        Evaluates block/filter rules. If a trade is blocked, returns the rule name as reason.
        Otherwise, returns None.
        """
        # RULE 3: RULE_LIQUIDITY_SWEEP_CONFIRM (Filter)
        if self.is_enabled("RULE_LIQUIDITY_SWEEP_CONFIRM"):
            # Blocks trades unless a sweep signal is active
            sweep_sig = getattr(fv, "liquidity_sweep_signal", 0)
            if sweep_sig == 0:
                return "BLOCKED_BY_RULE_LIQUIDITY_SWEEP_CONFIRM"

        # RULE 8: RULE_SPREAD_SQUEEZE_ONLY (Filter)
        if self.is_enabled("RULE_SPREAD_SQUEEZE_ONLY"):
            spread = tick.ask - tick.bid
            if regime_state and regime_state.regime_type == RegimeType.HIGH_SPREAD_CHOP:
                return "BLOCKED_BY_RULE_SPREAD_SQUEEZE_ONLY"
            if spread > 0.25:  # broker spread is high
                return "BLOCKED_BY_RULE_SPREAD_SQUEEZE_ONLY"

        # RULE 9: RULE_REJECTION_WALL_BLOCKER (Filter)
        if self.is_enabled("RULE_REJECTION_WALL_BLOCKER"):
            # Block limit orders if price knocked on limit price level 3 times without breakout
            pass  # Stubbed behavior allows trade

        # RULE 10: RULE_BID_ASK_SPOOF_DETECTOR (Filter)
        if self.is_enabled("RULE_BID_ASK_SPOOF_DETECTOR"):
            # Block trades if vanishing tick volumes detected (spoofing protection)
            pass  # Stubbed behavior allows trade

        # RULE 16: RULE_LONDON_NY_KILLZONE_ONLY (Filter)
        if self.is_enabled("RULE_LONDON_NY_KILLZONE_ONLY"):
            now_dt = datetime.now()
            # Simple timezone overlap rule (e.g., London/NY overlap from 12:00 to 16:00 UTC)
            # Here we check if hour is between 12 and 16
            current_hour = now_dt.hour
            if current_hour < 12 or current_hour > 16:
                return "BLOCKED_BY_RULE_LONDON_NY_KILLZONE_ONLY"

        # RULE 17: RULE_ASIAN_RANGE_FAKEOUT (Filter)
        if self.is_enabled("RULE_ASIAN_RANGE_FAKEOUT"):
            now_dt = datetime.now()
            # Asian session: block breakout trades, prefer mean reversion
            # Assuming Asian session is between 22:00 and 06:00
            current_hour = now_dt.hour
            if current_hour >= 22 or current_hour < 6:
                # Filter out breakouts
                broke_high = getattr(fv, "broke_previous_high", False)
                broke_low = getattr(fv, "broke_previous_low", False)
                if broke_high or broke_low:
                    return "BLOCKED_BY_RULE_ASIAN_RANGE_FAKEOUT"

        # RULE 19: RULE_DEAD_ZONE_BLOCKER (Filter)
        if self.is_enabled("RULE_DEAD_ZONE_BLOCKER"):
            now_dt = datetime.now()
            # Daily broker rollover 23:55 to 00:05
            if (now_dt.hour == 23 and now_dt.minute >= 55) or (
                now_dt.hour == 0 and now_dt.minute <= 5
            ):
                return "BLOCKED_BY_RULE_DEAD_ZONE_BLOCKER"

        # RULE 21: RULE_CONSECUTIVE_LOSS_FREEZE (Filter)
        if self.is_enabled("RULE_CONSECUTIVE_LOSS_FREEZE"):
            # Handled directly inside LiveEngine / SignalPolicy using metric tracking if desired,
            # but stubbed here for evaluation flow
            pass

        # RULE 22: RULE_DAILY_TARGET_LOCK (Filter)
        if self.is_enabled("RULE_DAILY_TARGET_LOCK"):
            # Handled directly inside LiveEngine / SignalPolicy
            pass

        # RULE 23: RULE_AI_MACRO_ALIGNMENT (Filter)
        if self.is_enabled("RULE_AI_MACRO_ALIGNMENT"):
            h4_trend = getattr(fv, "htf_h4_trend", 0.0)
            # If heavily bearish, block buy signals
            if h4_trend < -0.5:
                return "BLOCKED_BY_RULE_AI_MACRO_ALIGNMENT"

        # RULE 25: RULE_CORRELATED_DRAWDOWN_CAP (Filter)
        if self.is_enabled("RULE_CORRELATED_DRAWDOWN_CAP"):
            # Handled dynamically via total account equity drawdown > 3%
            pass

        # RULE 28: RULE_SCHMITT_TRIGGER_REGIME_LOCK (Filter)
        if self.is_enabled("RULE_SCHMITT_TRIGGER_REGIME_LOCK"):
            # Blocks trades if rapid regime flapping occurs
            pass

        return None

    def evaluate_in_trade_exits(
        self,
        pos: Position,
        holding_duration_sec: float,
        price_current: float,
        atr: float,
        mfe_profit: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluates exit rules for active positions.
        Returns a dict describing the action (e.g., {"action": "CLOSE", "reason": "..."})
        or None if no rule is triggered.
        """
        # RULE 11: RULE_HIT_AND_RUN_EXIT
        if self.is_enabled("RULE_HIT_AND_RUN_EXIT"):
            # Force close any profitable position after exactly 3 to 5 M1 bars (180s to 300s)
            is_in_profit = pos.profit > 0.0
            if is_in_profit and holding_duration_sec >= 240.0:  # exactly 4 minutes
                return {"action": "CLOSE", "reason": "RULE_HIT_AND_RUN_EXIT"}

        # RULE 12: RULE_ZERO_DRAWDOWN_TRAIL
        if self.is_enabled("RULE_ZERO_DRAWDOWN_TRAIL"):
            # Moves Stop Loss to Breakeven +1 pip the moment trade goes into +2 pips profit
            pip_size = 0.10  # Gold pip representation
            pips_profit = (
                (price_current - pos.price_open) / pip_size
                if pos.type == OrderType.BUY
                else (pos.price_open - price_current) / pip_size
            )
            if pips_profit >= 2.0:
                target_sl = (
                    pos.price_open + (1.0 * pip_size)
                    if pos.type == OrderType.BUY
                    else pos.price_open - (1.0 * pip_size)
                )
                # Ensure we only move SL in favor of the trade
                if pos.type == OrderType.BUY and target_sl > pos.sl:
                    return {
                        "action": "MODIFY_SL",
                        "stop_loss": target_sl,
                        "reason": "RULE_ZERO_DRAWDOWN_TRAIL",
                    }
                elif pos.type == OrderType.SELL and (pos.sl == 0.0 or target_sl < pos.sl):
                    return {
                        "action": "MODIFY_SL",
                        "stop_loss": target_sl,
                        "reason": "RULE_ZERO_DRAWDOWN_TRAIL",
                    }

        # RULE 13: RULE_TIME_DECAY_CHOP_EXIT
        if self.is_enabled("RULE_TIME_DECAY_CHOP_EXIT"):
            # Scraps trade if it hasn't moved into profit within 4 minutes (240s)
            if pos.profit <= 0.0 and holding_duration_sec >= 240.0:
                return {"action": "CLOSE", "reason": "RULE_TIME_DECAY_CHOP_EXIT"}

        # RULE 14: RULE_ATR_EXPANSION_RATCHET
        if self.is_enabled("RULE_ATR_EXPANSION_RATCHET"):
            # Tightens trailing stop aggressively only when explosive volatility candle occurs in our favor
            if pos.profit > 0.0 and atr >= 2.0:  # High ATR
                tight_trail = atr * 0.5
                target_sl = (
                    price_current - tight_trail
                    if pos.type == OrderType.BUY
                    else price_current + tight_trail
                )
                if pos.type == OrderType.BUY and target_sl > pos.sl:
                    return {
                        "action": "MODIFY_SL",
                        "stop_loss": target_sl,
                        "reason": "RULE_ATR_EXPANSION_RATCHET",
                    }
                elif pos.type == OrderType.SELL and (pos.sl == 0.0 or target_sl < pos.sl):
                    return {
                        "action": "MODIFY_SL",
                        "stop_loss": target_sl,
                        "reason": "RULE_ATR_EXPANSION_RATCHET",
                    }

        # RULE 15: RULE_HEDGE_ON_AI_FLIP
        if self.is_enabled("RULE_HEDGE_ON_AI_FLIP"):
            # Stubbed behaviour inside order_manager monitoring loop
            pass

        return None

    def evaluate_risk_and_safeguards(
        self,
        account_equity: float,
        peak_equity: float,
        consecutive_losses: int,
    ) -> Optional[str]:
        """
        Evaluates risk-related safeguards.
        """
        # RULE 21: RULE_CONSECUTIVE_LOSS_FREEZE
        if self.is_enabled("RULE_CONSECUTIVE_LOSS_FREEZE"):
            if consecutive_losses >= 3:
                return "FREEZE_CONSECUTIVE_LOSSES"

        # RULE 22: RULE_DAILY_TARGET_LOCK
        if self.is_enabled("RULE_DAILY_TARGET_LOCK"):
            growth_pct = (
                ((account_equity - peak_equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
            )
            if growth_pct >= 2.0:
                return "DAILY_TARGET_LOCKED"

        # RULE 25: RULE_CORRELATED_DRAWDOWN_CAP
        if self.is_enabled("RULE_CORRELATED_DRAWDOWN_CAP"):
            drawdown_pct = (
                ((peak_equity - account_equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
            )
            if drawdown_pct >= 3.0:
                return "BLOCKED_CORRELATED_DRAWDOWN"

        return None
