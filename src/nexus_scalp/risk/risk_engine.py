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
    ) -> None:
        self.config = config
        self.max_margin_usage_pct = max_margin_usage_pct
        self.max_allowed_lots = max_allowed_lots
        self.eta_coefficient = eta_coefficient
        self.max_impact_reward_ratio = max_impact_reward_ratio
        self._kill_switch_active = False

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
        # 1. CONCURRENT POSITIONS & DIRECTIONAL EXPOSURE SQUEEZE GUARD
        # ----------------------------------------------------------------------
        symbol_positions = [p for p in active_positions if p.symbol == proposal.symbol]
        
        if len(symbol_positions) >= self.config.max_concurrent_positions:
            logger.warning(
                "Proposal rejected: Active position count limit reached",
                symbol=proposal.symbol,
                active_count=len(symbol_positions),
                max_limit=self.config.max_concurrent_positions,
            )
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
        # 4. REGIME-ADJUSTED VOLATILITY SCALING (Kelly / Risk Attenuation)
        # ----------------------------------------------------------------------
        risk_pct = self.config.risk_per_trade_pct

        if regime_state and regime_state.regime_type == RegimeType.VOLATILITY_EXPANSION:
            risk_pct *= 0.50
            logger.info("Volatility Scaling Active: Halved trade risk % due to market expansion.")

        risk_amount_usd = account.equity * (risk_pct / 100.0)

        # ----------------------------------------------------------------------
        # 5. DYNAMIC LOT SIZING CALCULATION
        # ----------------------------------------------------------------------
        sl_distance_points = sl_dist_price / symbol_info.point
        tick_val = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0
        
        sl_risk_volume = risk_amount_usd / (sl_distance_points * tick_val + 1e-8)

        contract_size = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 100.0
        leverage = account.leverage if account.leverage > 0 else 100
        required_margin_per_lot = (contract_size * proposal.proposed_entry) / leverage
        
        max_allocatable_margin = account.margin_free * (self.max_margin_usage_pct / 100.0)
        margin_cap_volume = max_allocatable_margin / (required_margin_per_lot + 1e-8)

        raw_volume = min(sl_risk_volume, margin_cap_volume)
        remaining_exposure_cap = self.max_allowed_lots - current_directional_exposure
        raw_volume = min(raw_volume, remaining_exposure_cap)

        step = symbol_info.volume_step if symbol_info.volume_step > 0 else 0.01
        steps = math.floor(raw_volume / step)
        final_volume = round(steps * step, 2)
        final_volume = min(final_volume, symbol_info.volume_max)

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

    def _map_action_to_order_type(self, action: ActionType) -> OrderType:
        """Safely maps the domain ActionType to MT5 Execution OrderType."""
        if action == ActionType.BUY_MARKET:
            return OrderType.BUY
        elif action == ActionType.SELL_MARKET:
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