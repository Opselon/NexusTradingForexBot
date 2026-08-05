"""
Institutional Dynamic Fail-Closed Risk Management Engine (v4.1 HFT Scalper Calibrated)
======================================================================================
Calculates optimal position lot sizing dynamically using real-time account equity,
free margin, leverage, tick value, and strict broker stops-level validation.

Enterprise Upgrades & Calibrations Incorporated:
    1. Calibrated Almgren-Chriss Slippage Tolerance (Allows up to 45% impact ratio for micro-scalps).
    2. Calibrated Impact Coefficient (eta=1500.0 tuned for HFT Gold market liquidity).
    3. Net Directional Exposure Squeeze Guard (Caps total lot size per direction).
    4. Triple Stops-Level Validation (Checks Entry vs Market, Entry vs SL, Entry vs TP).
    5. Strict Volume Floor Rejection (Aborts trade if risk/impact limits breach broker minimum).
"""

import math
from typing import Any

from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
    TradeProposal,
)
from nexus_scalp.features.regime_classifier import MarketRegimeState, RegimeType
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.risk.risk_engine")


class RiskEngine:
    """
    Independent Risk Management & Dynamic Lot Sizing Engine.
    Institutional implementation with regime-aware volatility scaling and Almgren-Chriss impact guards.
    """

    def __init__(
        self,
        config: RiskConfig,
        max_margin_usage_pct: float = 10.0,
        max_allowed_lots: float = 50.0,
        eta_coefficient: float = 200.0,        # HFT Calibrated Almgren-Chriss coefficient for Gold micro-lots
        max_impact_reward_ratio: float = 0.45, # Allow up to 45% slippage/reward ratio on tight M1 targets
        min_risk_reward_ratio: float = 1.8,
        min_rr_high_confidence: float = 1.2,
        high_confidence_threshold: float = 0.70,
    ) -> None:
        self.config = config
        self.max_margin_usage_pct = max_margin_usage_pct
        self.max_allowed_lots = max_allowed_lots
        self.eta_coefficient = eta_coefficient
        self.max_impact_reward_ratio = max_impact_reward_ratio
        self.min_risk_reward_ratio = min_risk_reward_ratio
        self.min_rr_high_confidence = min_rr_high_confidence
        self.high_confidence_threshold = high_confidence_threshold
        self._kill_switch_active = False

    def get_clamped_position_size(
        self,
        volume: float | None = None,
        raw_volume: float | None = None,
        account: Any = None,
        symbol_info: Any = None,
        current_directional_exposure: float = 0.0,
    ) -> float:
        """Clamps the proposed position volume to HARD_MAX_LOTS (2.0)."""
        vol = volume if volume is not None else raw_volume
        if vol is None:
            vol = 0.0
        return min(vol, 2.0)

    def calculate_position_size(
        self,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        sl_distance_price: float,
        risk_pct: float,
    ) -> float:
        """
        Dynamically adjusts position lot size based on variable structural SL distance (fixed dollar risk).
        If SL is wide, lot size scales down; if SL is tight, lot size scales up.
        """
        risk_amount_usd = account.equity * (risk_pct / 100.0)
        sl_distance_points = max(sl_distance_price, 1e-5) / symbol_info.point
        tick_val = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0

        sl_risk_volume = risk_amount_usd / (sl_distance_points * tick_val + 1e-8)
        return sl_risk_volume

    def enable_kill_switch(self) -> None:
        """Activates emergency hard-stop across all trading execution."""
        self._kill_switch_active = True
        logger.critical("EMERGENCY KILL SWITCH ACTIVATED! All execution rejected.")

    def disable_kill_switch(self) -> None:
        """Deactivates emergency hard-stop."""
        self._kill_switch_active = False
        logger.info("Kill switch deactivated.")

    def _estimate_market_impact(
        self, 
        volume: float, 
        symbol_info: SymbolInfo, 
        current_tick: TickData, 
        atr: float,
        order_type: OrderType | None = None,  # Passive Limit Order Awareness
    ) -> float:
        """
        Almgren-Chriss Temporary Market Impact Model (O(1) Hot-Path).
        Calculates expected USD slippage cost based on liquidity depletion and volume size.
        Passive Limit Orders (BUY_LIMIT / SELL_LIMIT) are Liquidity Makers and incur ZERO taker slippage!
        """
        if symbol_info is None or current_tick is None:
            return float('inf')

        # HFT MARKET MICROSTRUCTURE RULE: Limit orders are Makers and have ZERO taker slippage impact
        if order_type in (OrderType.BUY_LIMIT, OrderType.SELL_LIMIT):
            return 0.0

        contract_size = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 100.0
        size_ratio = volume / contract_size
        vol_factor = max(atr, 0.50)
        
        slippage_cost_usd = self.eta_coefficient * size_ratio * vol_factor
        return slippage_cost_usd

    def evaluate_proposal(
        self,
        proposal: TradeProposal,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        active_positions: list[Position],
        current_tick: TickData,
        regime_state: MarketRegimeState | None = None,
        atr: float = 1.50,
        pending_orders: list[Any] | None = None,
    ) -> TradeOrder | None:
        """
        Evaluates a TradeProposal against hard capital constraints, broker rules, and LOB friction.
        """
        if self._kill_switch_active:
            logger.warning("Proposal rejected: Emergency kill switch active.")
            return None

        if proposal.action in (ActionType.NO_TRADE, ActionType.WAIT):
            return None

        is_buy_proposal = "BUY" in proposal.action.value
        proposed_order_type = self._map_action_to_order_type(proposal.action)

        # ----------------------------------------------------------------------
        # PART 6: PORTFOLIO CONTEXT ENGINE
        # ----------------------------------------------------------------------
        symbol_positions = [p for p in active_positions if p.symbol == proposal.symbol]
        
        # Max concurrent positions check
        if len(symbol_positions) >= self.config.max_concurrent_positions:
            logger.warning(
                "Proposal rejected: Active position count limit reached",
                symbol=proposal.symbol,
                active_count=len(symbol_positions),
                max_limit=self.config.max_concurrent_positions,
            )
            return None

        # Max pending orders limit
        p_orders = pending_orders if pending_orders is not None else []
        max_pending_limit = getattr(self.config, "max_pending_orders", 5)
        if len(p_orders) >= max_pending_limit:
            logger.warning(
                "Proposal rejected: Max pending orders limit reached",
                pending_count=len(p_orders),
                max_limit=max_pending_limit,
            )
            return None

        # Conflicting limit orders check: Avoid large opposite BUY_LIMIT / SELL_LIMIT unless explicit hedge
        is_hedge = "HEDGE" in getattr(proposal, "reason_code", "") or "hedge" in getattr(proposal, "reason_code", "").lower()
        if not is_hedge:
            has_opposite_exposure = False
            for p in symbol_positions:
                if is_buy_proposal and p.type == OrderType.SELL:
                    has_opposite_exposure = True
                elif not is_buy_proposal and p.type == OrderType.BUY:
                    has_opposite_exposure = True

            if has_opposite_exposure and proposed_order_type in (OrderType.BUY_LIMIT, OrderType.SELL_LIMIT):
                logger.warning("Portfolio Context Blocked: Opposing exposure present, limit order rejected.")
                return None

        current_directional_exposure = sum(
            p.volume for p in symbol_positions 
            if (is_buy_proposal and p.type == OrderType.BUY) or (not is_buy_proposal and p.type == OrderType.SELL)
        )

        if current_directional_exposure >= self.max_allowed_lots:
            logger.warning(
                "Proposal rejected: Directional Exposure Squeeze limit reached",
                symbol=proposal.symbol,
                exposure_lots=current_directional_exposure,
                max_allowed=self.max_allowed_lots,
            )
            return None

        # ----------------------------------------------------------------------
        # 2. SPREAD GATE
        # ----------------------------------------------------------------------
        spread_points = (current_tick.ask - current_tick.bid) / symbol_info.point
        if spread_points > self.config.max_spread_points:
            logger.warning(
                "Proposal rejected: Spread exceeds maximum threshold",
                spread=round(spread_points, 1),
                max_allowed=self.config.max_spread_points,
            )
            return None

        # ----------------------------------------------------------------------
        # 2.5 RISK REWARD GATEKEEPER INTEGRATION (Bypassed for emergency hedging counter-positions)
        # ----------------------------------------------------------------------
        # Determine active min required RR based on confidence (normal vs high confidence)
        active_min_rr = self.min_risk_reward_ratio
        high_conf_thresh = getattr(self, "high_confidence_threshold", 0.95)
        min_rr_high_conf = getattr(self, "min_rr_high_confidence", 1.2)
        if hasattr(proposal, "confidence") and proposal.confidence >= high_conf_thresh:
            if min_rr_high_conf < active_min_rr:
                active_min_rr = min_rr_high_conf

        if not is_hedge and proposal.risk_reward_ratio < active_min_rr:
            logger.warning(
                "Proposal rejected: Risk reward ratio too low for risk engine",
                actual_rr=proposal.risk_reward_ratio,
                min_required=active_min_rr,
            )
            return None

        # ----------------------------------------------------------------------
        # 3. TRIPLE STOPS-LEVEL BROKER VALIDATION
        # ----------------------------------------------------------------------
        min_dist_price = symbol_info.stops_level * symbol_info.point
        
        if proposed_order_type in (OrderType.BUY_LIMIT, OrderType.SELL_LIMIT, OrderType.BUY_STOP, OrderType.SELL_STOP):
            if proposed_order_type == OrderType.BUY_LIMIT:
                entry_market_dist = current_tick.ask - proposal.proposed_entry
            elif proposed_order_type == OrderType.SELL_LIMIT:
                entry_market_dist = proposal.proposed_entry - current_tick.bid
            elif proposed_order_type == OrderType.BUY_STOP:
                entry_market_dist = proposal.proposed_entry - current_tick.ask
            else: # SELL_STOP
                entry_market_dist = current_tick.bid - proposal.proposed_entry

            if entry_market_dist < min_dist_price:
                logger.warning("Proposal rejected: Pending entry price is too close to market (Stops Level violation)")
                return None

        sl_dist_price = abs(proposal.proposed_entry - proposal.stop_loss)
        tp_dist_price = abs(proposal.proposed_entry - proposal.take_profit)

        if sl_dist_price < min_dist_price or tp_dist_price < min_dist_price:
            logger.warning("Proposal rejected: SL/TP distance smaller than broker stops level")
            return None

        # ----------------------------------------------------------------------
        # PART 7: DYNAMIC POSITION SIZING (Regime/Drawdown/Confidence scaled)
        # ----------------------------------------------------------------------
        risk_pct = self.config.risk_per_trade_pct

        if regime_state and regime_state.regime_type == RegimeType.VOLATILITY_EXPANSION:
            risk_pct *= 0.50
            logger.info("Volatility Scaling Active: Halved trade risk % due to market expansion.")

        # Additional Drawdown-aware penalty scaling
        peak_equity = getattr(account, "peak_equity", account.equity)
        if peak_equity > account.equity:
            drawdown_pct = ((peak_equity - account.equity) / peak_equity) * 100.0
            if drawdown_pct > 1.0:
                drawdown_penalty = max(0.2, 1.0 - (drawdown_pct * 0.2)) # Scale down up to 80%
                risk_pct *= drawdown_penalty
                logger.info(f"Drawdown Penalty Active: Scaling trade risk % by {drawdown_penalty:.2f}x due to {drawdown_pct:.2f}% drawdown.")

        # Confidence scaled risk sizing
        if hasattr(proposal, "confidence"):
            confidence_scalar = max(0.5, min(1.2, proposal.confidence / 0.85))
            risk_pct *= confidence_scalar
            logger.info(f"Confidence Scaling Active: Scaling risk % by {confidence_scalar:.2f}x.")

        risk_amount_usd = account.equity * (risk_pct / 100.0)

        # ----------------------------------------------------------------------
        # 5. DYNAMIC LOT SIZING CALCULATION
        # ----------------------------------------------------------------------
        tick_val = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0
        sl_risk_volume = self.calculate_position_size(
            account=account,
            symbol_info=symbol_info,
            sl_distance_price=sl_dist_price,
            risk_pct=risk_pct,
        )

        contract_size = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 100.0
        leverage = account.leverage if account.leverage > 0 else 100
        required_margin_per_lot = (contract_size * proposal.proposed_entry) / leverage
        
        max_allocatable_margin = account.margin_free * (self.max_margin_usage_pct / 100.0)
        margin_cap_volume = max_allocatable_margin / (required_margin_per_lot + 1e-8)

        raw_volume = min(sl_risk_volume, margin_cap_volume)
        remaining_exposure_cap = self.max_allowed_lots - current_directional_exposure
        raw_volume = min(raw_volume, remaining_exposure_cap)

        HARD_MAX_LOTS = 2.0
        raw_volume = min(raw_volume, HARD_MAX_LOTS)

        step = symbol_info.volume_step if symbol_info.volume_step > 0 else 0.01
        steps = math.floor(raw_volume / step)
        final_volume = round(steps * step, 2)
        final_volume = min(final_volume, HARD_MAX_LOTS, symbol_info.volume_max)

        # Verify free margin before dispatching. If margin is insufficient, set final_volume to 0.0
        contract_size = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 100.0
        leverage = account.leverage if account.leverage > 0 else 100
        required_margin = (contract_size * proposal.proposed_entry * final_volume) / leverage
        if required_margin > account.margin_free:
            final_volume = 0.0

        # ----------------------------------------------------------------------
        # 6. ALMGREN-CHRISS MARKET IMPACT & SLIPPAGE GUARD (Order-Type Aware)
        # ----------------------------------------------------------------------
        while final_volume >= symbol_info.volume_min:
            expected_reward_usd = (tp_dist_price / symbol_info.point) * tick_val * final_volume
            # BUGFIX: Pass proposed_order_type to grant zero slippage impact for Limit orders
            slippage_usd = self._estimate_market_impact(
                final_volume, symbol_info, current_tick, atr, order_type=proposed_order_type
            )
            
            if expected_reward_usd > 0 and (slippage_usd / expected_reward_usd) <= self.max_impact_reward_ratio:
                break  # Impact bounds respected
                
            final_volume = round(final_volume - step, 2)
            
        if final_volume < symbol_info.volume_min:
            logger.warning(
                "EXCESSIVE_MARKET_IMPACT_REJECTED: Proposal aborted",
                symbol=proposal.symbol,
                calculated_vol=final_volume,
                broker_min=symbol_info.volume_min,
                reason=f"Estimated execution slippage consumes > {self.max_impact_reward_ratio*100}% of gross reward",
            )
            return None

        logger.info(
            "HFT Scalper Dynamic Lot Sizing Computed",
            symbol=proposal.symbol,
            type=proposed_order_type.value,
            calculated_volume=final_volume,
            risk_usd=round(risk_amount_usd, 2),
            expected_slippage_usd=round(slippage_usd, 2),
            free_margin=round(account.margin_free, 2),
        )

        return TradeOrder(
            order_id=proposal.request_id,
            symbol=proposal.symbol,
            order_type=proposed_order_type,
            volume=final_volume,
            price=proposal.proposed_entry,
            stop_loss=proposal.stop_loss,
            take_profit=proposal.take_profit,
            magic_number=888101,
            comment="NSE_HFT_SIZED",
        )

    def calculate_volume(
        self,
        entry: float,
        sl: float,
        tp: float,
        account: AccountInfo,
        symbol_info: SymbolInfo,
    ) -> float:
        """
        Pass entry, SL, and TP prices to risk_engine.calculate_volume(...) for dynamic lot sizing based on account risk %.
        """
        sl_dist_price = abs(entry - sl)
        risk_pct = self.config.risk_per_trade_pct

        volume = self.calculate_position_size(
            account=account,
            symbol_info=symbol_info,
            sl_distance_price=sl_dist_price,
            risk_pct=risk_pct,
        )

        step = symbol_info.volume_step if symbol_info.volume_step > 0 else 0.01
        steps = math.floor(volume / step)
        final_volume = round(steps * step, 2)

        # Constrain to broker rules and hard limit
        HARD_MAX_LOTS = 2.0
        final_volume = min(final_volume, HARD_MAX_LOTS, symbol_info.volume_max)
        final_volume = max(final_volume, symbol_info.volume_min)

        # Verify free margin before dispatching. If margin is insufficient, return 0.0 volume.
        contract_size = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 100.0
        leverage = account.leverage if account.leverage > 0 else 100
        required_margin = (contract_size * entry * final_volume) / leverage
        if required_margin > account.margin_free:
            return 0.0

        return final_volume

    def _map_action_to_order_type(self, action: ActionType) -> OrderType:
        """Safely maps the domain ActionType to MT5 Execution OrderType."""
        if action in (ActionType.BUY, ActionType.BUY_MARKET):
            return OrderType.BUY
        elif action in (ActionType.SELL, ActionType.SELL_MARKET):
            return OrderType.SELL
        elif action == ActionType.BUY_LIMIT:
            return OrderType.BUY_LIMIT
        elif action == ActionType.SELL_LIMIT:
            return OrderType.SELL_LIMIT
        elif action == ActionType.BUY_STOP:
            return OrderType.BUY_STOP
        elif action == ActionType.SELL_STOP:
            return OrderType.SELL_STOP
        else:
            return OrderType.BUY if "BUY" in action.value else OrderType.SELL