"""
Institutional Dynamic Fail-Closed Risk Management Engine (v5.0 HFT Scalper Calibrated)
======================================================================================
Calculates optimal position lot sizing dynamically using real-time account equity,
free margin, leverage, contract size, and strict broker validation.

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
        max_allowed_lots: float = 10.0,  # HARD_MAX_LOTS parity (order_manager)
        eta_coefficient: float = 200.0,  # HFT Calibrated Almgren-Chriss coefficient for Gold micro-lots
        max_impact_reward_ratio: float = 0.45,  # Allow up to 45% slippage/reward ratio on tight M1 targets
        min_risk_reward_ratio: float = 1.8,
        min_rr_high_confidence: float = 1.2,
        high_confidence_threshold: float = 0.95,  # = config default (AlgoConfig)
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
        """Clamps the proposed position volume to dynamic safety ceilings based on account size."""
        vol = volume if volume is not None else raw_volume
        if vol is None:
            vol = 0.0

        if account is not None:
            equity = getattr(account, "equity", 0.0)
            if equity < 100.0:
                tier_max = 0.02
            elif equity < 1000.0:
                tier_max = 0.10
            elif equity < 10000.0:
                tier_max = 1.00
            else:
                tier_max = 10.0
        else:
            tier_max = 10.0  # Fallback ceiling

        if symbol_info is not None:
            tier_max = min(tier_max, symbol_info.volume_max)

        return min(vol, tier_max)

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
        if (
            sl_distance_price <= 0.0
            or math.isnan(sl_distance_price)
            or math.isinf(sl_distance_price)
        ):
            return 0.0
        if account.equity <= 0.0 or math.isnan(account.equity) or math.isinf(account.equity):
            return 0.0
        if (
            symbol_info.trade_contract_size <= 0.0
            or math.isnan(symbol_info.trade_contract_size)
            or math.isinf(symbol_info.trade_contract_size)
        ):
            return 0.0
        risk_amount_usd = account.equity * (risk_pct / 100.0)
        return risk_amount_usd / (sl_distance_price * symbol_info.trade_contract_size)

    def _floor_to_step(self, val: float, step: float) -> float:
        """Floors a value to the nearest step, avoiding floating-point precision issues."""
        if step <= 0.0 or math.isnan(step) or math.isinf(step):
            return 0.0
        if val <= 0.0 or math.isnan(val) or math.isinf(val):
            return 0.0
        eps = 1e-9
        steps = math.floor((val + eps) / step)
        return round(steps * step, 4)

    def calculate_dynamic_volume(
        self,
        entry: float,
        sl: float,
        account: AccountInfo,
        symbol_info: SymbolInfo,
        risk_pct: float,
    ) -> tuple[float, str]:
        """
        Centralized Dynamic position-sizing engine (v5.0 Enterprise Calibrated).
        Implements a mathematically correct, broker-aware, multi-stage risk-sizing pipeline.
        """
        # Step 1: Validate Inputs
        inputs = [
            entry,
            sl,
            account.equity,
            account.margin_free,
            account.leverage,
            symbol_info.trade_contract_size,
            symbol_info.volume_step,
        ]
        for val in inputs:
            if val is None or math.isnan(val) or math.isinf(val):
                return 0.0, "INVALID_INPUT_NAN_INF_NONE"

        if entry <= 0.0 or sl <= 0.0:
            return 0.0, "INVALID_PRICING"
        if account.equity <= 0.0:
            return 0.0, "INVALID_EQUITY"
        if account.margin_free <= 0.0:
            return 0.0, "INVALID_FREE_MARGIN"
        if account.leverage <= 0:
            return 0.0, "INVALID_LEVERAGE"
        if symbol_info.trade_contract_size <= 0.0:
            return 0.0, "INVALID_CONTRACT_SIZE"
        if symbol_info.volume_step <= 0.0:
            return 0.0, "INVALID_VOLUME_STEP"

        sl_distance = abs(entry - sl)
        if sl_distance <= 0.0:
            return 0.0, "INVALID_SL_DISTANCE"

        # Step 2: Calculate Equity Risk $
        risk_amount_usd = account.equity * (risk_pct / 100.0)
        if risk_amount_usd < 0.0 or math.isnan(risk_amount_usd) or math.isinf(risk_amount_usd):
            return 0.0, "INVALID_RISK_AMOUNT"

        # Step 3: Calculate Raw Risk-Based Lots
        contract_size = symbol_info.trade_contract_size
        raw_lots = risk_amount_usd / (sl_distance * contract_size)
        if math.isnan(raw_lots) or math.isinf(raw_lots) or raw_lots < 0.0:
            return 0.0, "INVALID_RAW_LOTS"

        # Step 4: Floor to Broker Volume Step
        step = symbol_info.volume_step
        volume = self._floor_to_step(raw_lots, step)

        # Step 5: Apply Broker Maximum
        volume = min(volume, symbol_info.volume_max)

        # Step 6: Apply Account Safety Ceiling
        if account.equity < 100.0:
            tier_max = 0.02
        elif account.equity < 1000.0:
            tier_max = 0.10
        elif account.equity < 10000.0:
            tier_max = 1.00
        else:
            tier_max = min(10.0, symbol_info.volume_max)

        volume = min(volume, tier_max)

        # Step 7: Calculate Required Margin & Apply 20% Free-Margin Clamp
        maximum_allowed_margin = account.margin_free * 0.20
        if contract_size > 0 and entry > 0 and account.leverage > 0:
            max_margin_volume = (maximum_allowed_margin * account.leverage) / (
                contract_size * entry
            )
        else:
            max_margin_volume = 0.0

        volume = min(volume, max_margin_volume)

        # Floor to Step AGAIN
        volume = self._floor_to_step(volume, step)

        # Step 8: Check Broker Minimum & Apply Micro-Account Exception
        if volume < symbol_info.volume_min:
            if account.equity < 50.0:
                volume = symbol_info.volume_min
                volume = min(volume, symbol_info.volume_max)
                reason = "MICRO_ACCOUNT_MIN_LOT_EXCEPTION"
            else:
                volume = 0.0
                reason = "INSUFFICIENT_EQUITY_FOR_MIN_LOT"
        else:
            reason = "SUCCESS"

        return volume, reason

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
            return float("inf")

        # HFT MARKET MICROSTRUCTURE RULE: Limit orders are Makers and have ZERO taker slippage impact
        if order_type in (OrderType.BUY_LIMIT, OrderType.SELL_LIMIT):
            return 0.0

        contract_size = (
            symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 100.0
        )
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
        is_hedge = (
            "HEDGE" in getattr(proposal, "reason_code", "")
            or "hedge" in getattr(proposal, "reason_code", "").lower()
        )
        if not is_hedge:
            has_opposite_exposure = False
            for p in symbol_positions:
                if is_buy_proposal and p.type == OrderType.SELL:
                    has_opposite_exposure = True
                elif not is_buy_proposal and p.type == OrderType.BUY:
                    has_opposite_exposure = True

            if has_opposite_exposure and proposed_order_type in (
                OrderType.BUY_LIMIT,
                OrderType.SELL_LIMIT,
            ):
                logger.warning(
                    "Portfolio Context Blocked: Opposing exposure present, limit order rejected."
                )
                return None

        current_directional_exposure = sum(
            p.volume
            for p in symbol_positions
            if (is_buy_proposal and p.type == OrderType.BUY)
            or (not is_buy_proposal and p.type == OrderType.SELL)
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
            active_min_rr = min(active_min_rr, min_rr_high_conf)

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

        if proposed_order_type in (
            OrderType.BUY_LIMIT,
            OrderType.SELL_LIMIT,
            OrderType.BUY_STOP,
            OrderType.SELL_STOP,
        ):
            if proposed_order_type == OrderType.BUY_LIMIT:
                entry_market_dist = current_tick.ask - proposal.proposed_entry
            elif proposed_order_type == OrderType.SELL_LIMIT:
                entry_market_dist = proposal.proposed_entry - current_tick.bid
            elif proposed_order_type == OrderType.BUY_STOP:
                entry_market_dist = proposal.proposed_entry - current_tick.ask
            else:  # SELL_STOP
                entry_market_dist = current_tick.bid - proposal.proposed_entry

            if entry_market_dist < min_dist_price:
                logger.warning(
                    "Proposal rejected: Pending entry price is too close to market (Stops Level violation)"
                )
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
                drawdown_penalty = max(0.2, 1.0 - (drawdown_pct * 0.2))  # Scale down up to 80%
                risk_pct *= drawdown_penalty
                logger.info(
                    f"Drawdown Penalty Active: Scaling trade risk % by {drawdown_penalty:.2f}x due to {drawdown_pct:.2f}% drawdown."
                )

        # Confidence scaled risk sizing
        if hasattr(proposal, "confidence"):
            confidence_scalar = max(0.5, min(1.2, proposal.confidence / 0.85))
            risk_pct *= confidence_scalar
            logger.info(f"Confidence Scaling Active: Scaling risk % by {confidence_scalar:.2f}x.")

        risk_amount_usd = account.equity * (risk_pct / 100.0)

        # ----------------------------------------------------------------------
        # 5. DYNAMIC LOT SIZING CALCULATION
        # ----------------------------------------------------------------------
        final_volume, _size_reason = self.calculate_dynamic_volume(
            entry=proposal.proposed_entry,
            sl=proposal.stop_loss,
            account=account,
            symbol_info=symbol_info,
            risk_pct=risk_pct,
        )

        remaining_exposure_cap = self.max_allowed_lots - current_directional_exposure
        if final_volume > remaining_exposure_cap:
            final_volume = remaining_exposure_cap
            final_volume = self._floor_to_step(final_volume, symbol_info.volume_step)

        # Re-check minimum after clamping to exposure cap
        if final_volume < symbol_info.volume_min:
            if account.equity < 50.0:
                final_volume = symbol_info.volume_min
            else:
                final_volume = 0.0

        # Verify free margin before dispatching. If margin is insufficient, set final_volume to 0.0
        contract_size = (
            symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 100.0
        )
        leverage = account.leverage if account.leverage > 0 else 100
        required_margin = (contract_size * proposal.proposed_entry * final_volume) / leverage
        if required_margin > account.margin_free:
            final_volume = 0.0

        # ----------------------------------------------------------------------
        # 6. ALMGREN-CHRISS MARKET IMPACT & SLIPPAGE GUARD (Order-Type Aware)
        # ----------------------------------------------------------------------
        # BUG-239: the slippage variable must exist even when the impact loop
        # never runs (final_volume already below the broker minimum), otherwise
        # the success log below raises UnboundLocalError instead of safely
        # rejecting the proposal.
        slippage_usd = 0.0
        step = symbol_info.volume_step if symbol_info.volume_step > 0 else 0.01
        tick_val = symbol_info.tick_value if symbol_info.tick_value > 0 else 1.0
        while final_volume >= symbol_info.volume_min:
            expected_reward_usd = (tp_dist_price / symbol_info.point) * tick_val * final_volume
            slippage_usd = self._estimate_market_impact(
                final_volume, symbol_info, current_tick, atr, order_type=proposed_order_type
            )

            if (
                expected_reward_usd > 0
                and (slippage_usd / expected_reward_usd) <= self.max_impact_reward_ratio
            ):
                break  # Impact bounds respected

            final_volume = self._floor_to_step(final_volume - step, step)

        if final_volume < symbol_info.volume_min:
            if account.equity < 50.0 and final_volume > 0.0:
                # BUG-239: the micro exception may only RESCUE a volume that was
                # reduced by the impact model. A volume zeroed by the margin /
                # exposure checks above must stay zero: re-applying the broker
                # minimum here would dispatch an order the free-margin guard
                # already refused. Re-verify the margin rescue keeps it legal.
                rescue_margin = (contract_size * proposal.proposed_entry * symbol_info.volume_min) / leverage
                if rescue_margin > account.margin_free:
                    logger.warning(
                        "MICRO_ACCOUNT_MARGIN_REJECTED: Proposal aborted (broker minimum not affordable)",
                        symbol=proposal.symbol,
                        required_margin=round(rescue_margin, 2),
                        free_margin=round(account.margin_free, 2),
                    )
                    return None
                final_volume = symbol_info.volume_min
                logger.info(
                    "Micro-account exception: bypassing market impact reduction to allow broker minimum lot."
                )
            elif account.equity < 50.0 and final_volume <= 0.0:
                # Margin/exposure checks zeroed the volume: the micro exception
                # must not resurrect it (BUG-239 fail-closed).
                logger.warning(
                    "MICRO_ACCOUNT_ZERO_VOLUME_REJECTED: Proposal aborted",
                    symbol=proposal.symbol,
                    reason="volume zeroed by margin/exposure guard before impact gate",
                )
                return None
            else:
                logger.warning(
                    "EXCESSIVE_MARKET_IMPACT_REJECTED: Proposal aborted",
                    symbol=proposal.symbol,
                    calculated_vol=final_volume,
                    broker_min=symbol_info.volume_min,
                    reason=f"Estimated execution slippage consumes > {self.max_impact_reward_ratio * 100}% of gross reward",
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
        risk_pct = self.config.risk_per_trade_pct
        volume, _reason = self.calculate_dynamic_volume(
            entry=entry,
            sl=sl,
            account=account,
            symbol_info=symbol_info,
            risk_pct=risk_pct,
        )
        return volume

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

    # ------------------------------------------------------------------
    # BROKER-AWARE CALCULATION PROVENANCE (Phase 14)
    # ------------------------------------------------------------------
    # mt5.order_calc_margin() / mt5.order_calc_profit() are broker-native and
    # depend on the CURRENT trading environment (leverage, contract, tick
    # value, currency conversion). These helpers use them when the adapter
    # supports the snapshot API, and fall back to the mathematical estimate
    # with EXPLICIT provenance - never claiming broker-exactness (task 24).
    # They are OPTIONAL (never on the safety-critical path) and failure-
    # isolated: a broker-calc failure degrades to FALLBACK_ESTIMATE.
    # ------------------------------------------------------------------

    def verify_margin_with_broker(
        self,
        *,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        adapter: Any = None,
        fallback_estimate: float | None = None,
    ) -> dict[str, Any]:
        """Broker-native margin verification via mt5.order_calc_margin().

        Returns:
            {
                "margin_required": float | None,
                "source": "BROKER_NATIVE" | "FALLBACK_ESTIMATE" | "UNAVAILABLE",
                "available": bool,
                "error": {...} | None,
            }
        """
        result: dict[str, Any] = {
            "margin_required": fallback_estimate,
            "source": "FALLBACK_ESTIMATE" if fallback_estimate is not None else "UNAVAILABLE",
            "available": fallback_estimate is not None,
            "error": None,
        }
        if adapter is None or not hasattr(adapter, "order_calc_margin_snapshot"):
            return result
        try:
            # OrderType has BUY/SELL/LIMIT/STOP members only (no *_MARKET).
            mt5_type = 0 if "BUY" in str(getattr(order_type, "value", order_type)).upper() else 1
            snap = adapter.order_calc_margin_snapshot(
                symbol=symbol, order_type=mt5_type, volume=float(volume), price=float(price)
            )
            if snap.available and snap.value is not None:
                result = {
                    "margin_required": float(snap.value),
                    "source": "BROKER_NATIVE",
                    "available": True,
                    "error": None,
                }
            elif snap.error_code is not None:
                result["source"] = "UNAVAILABLE"
                result["available"] = False
                result["error"] = {"code": snap.error_code, "message": snap.error_message}
        except Exception as exc:
            logger.warning(
                "[RISK] broker margin calc failed (fallback estimate kept)",
                error=str(exc),
                symbol=symbol,
            )
            result["source"] = (
                "FALLBACK_ESTIMATE" if fallback_estimate is not None else "UNAVAILABLE"
            )
            result["error"] = {"code": "EXCEPTION", "message": type(exc).__name__}
        return result

    def verify_profit_with_broker(
        self,
        *,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price_open: float,
        price_close: float,
        adapter: Any = None,
        fallback_estimate: float | None = None,
    ) -> dict[str, Any]:
        """Broker-native profit verification via mt5.order_calc_profit().

        Returns the same provenance contract as verify_margin_with_broker.
        """
        result: dict[str, Any] = {
            "profit": fallback_estimate,
            "source": "FALLBACK_ESTIMATE" if fallback_estimate is not None else "UNAVAILABLE",
            "available": fallback_estimate is not None,
            "error": None,
        }
        if adapter is None or not hasattr(adapter, "order_calc_profit_snapshot"):
            return result
        try:
            mt5_type = 0 if "BUY" in str(getattr(order_type, "value", order_type)).upper() else 1
            snap = adapter.order_calc_profit_snapshot(
                symbol=symbol,
                order_type=mt5_type,
                volume=float(volume),
                price_open=float(price_open),
                price_close=float(price_close),
            )
            if snap.available and snap.value is not None:
                result = {
                    "profit": float(snap.value),
                    "source": "BROKER_NATIVE",
                    "available": True,
                    "error": None,
                }
            elif snap.error_code is not None:
                result["source"] = "UNAVAILABLE"
                result["available"] = False
                result["error"] = {"code": snap.error_code, "message": snap.error_message}
        except Exception as exc:
            logger.warning(
                "[RISK] broker profit calc failed (fallback estimate kept)",
                error=str(exc),
                symbol=symbol,
            )
            result["source"] = (
                "FALLBACK_ESTIMATE" if fallback_estimate is not None else "UNAVAILABLE"
            )
            result["error"] = {"code": "EXCEPTION", "message": type(exc).__name__}
        return result
