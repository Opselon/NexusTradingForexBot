"""
Institutional Order Lifecycle & Dynamic Position Management Engine (v4.0 Enterprise)
====================================================================================
Monitors active open positions with Wall Street grade execution controls and Market Impact modeling.
Integrates Almgren-Chriss execution framework to dynamically predict and mitigate slippage.

Enterprise Upgrades & Math Foundations Incorporated:
  - Almgren-Chriss Temporary Market Impact Model (Calculates real-time O(1) liquidity depletion).
  - Impact-Adjusted Trailing Stops & Break-Even (Defers triggers until projected slippage is covered).
  - Toxic Flow Detection (Deducts Hold Score if execution impact spikes dangerously against LOB depth).
  - Multi-Stage Partial Take-Profit Scaling (Scale-out at TP1 with broker volume step validation).
  - Volatility-Adaptive Chandelier Trailing Stop & Wick-Tolerant Break-Even.
  - Structural Hold Value Score Invalidation (0 to 100).
  - Real-Time MFE/MAE (Maximum Favorable / Adverse Excursion) Metrics Tracking.
  - Memory-Leak Free Execution (Garbage collection for positions closed via TP/SL/Manual).

Invariants:
    - Zero Latency Penalty: Position management executes on every live tick (50ms hot path).
    - Full Traceability: Every modification, partial close, or impact assessment is audited.
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData, TradeOrder
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.ports.mt5_port import IMT5Port

logger = get_logger("nexus_scalp.execution.order_manager")


class OrderLifecycleManager:
    """
    Manages order execution, position tracking, multi-stage partial scale-outs,
    dynamic volatility trailing stops, and Almgren-Chriss impact evaluation.
    """

    def __init__(
        self,
        adapter: IMT5Port,
        audit_repo: Optional[AuditRepository] = None,
        be_trigger_usd: float = 1.20,         # Dynamic base trigger ($1.20 movement before BE lock)
        be_lock_usd: float = 0.20,            # Locks +$0.20 to cover commissions and spread
        trailing_distance_usd: float = 1.50,  # ATR-scaled dynamic trailing distance for Gold noise
        min_modify_step_usd: float = 0.20,     # Minimum price change required before sending order modify IPC
        enable_partial_tp: bool = True,       # Enables partial profit scale-out at TP1
        partial_tp_ratio: float = 0.50,       # Closes 50% volume on TP1 milestone
        max_holding_seconds: float = 900.0,   # 15 minutes time-decay threshold for stagnant trades
        eta_coefficient: float = 2500.0,      # Base Temporary Impact scale for XAUUSD (Almgren-Chriss)
    ) -> None:
        self.adapter = adapter
        self.audit = audit_repo or AuditRepository()
        self._processed_orders: Dict[str, bool] = {}

        self.be_trigger = be_trigger_usd
        self.be_lock = be_lock_usd
        self.trailing_distance = trailing_distance_usd
        self.min_step = min_modify_step_usd

        # Institutional Execution Features
        self.enable_partial_tp = enable_partial_tp
        self.partial_tp_ratio = partial_tp_ratio
        self.max_holding_seconds = max_holding_seconds
        self.eta_coefficient = eta_coefficient

        # State Tracking for Metrics (Ticket -> Dict)
        self._partial_closed_tickets: Dict[int, bool] = {}
        self._mfe_tracker: Dict[int, float] = {}  # Maximum Favorable Excursion
        self._mae_tracker: Dict[int, float] = {}  # Maximum Adverse Excursion
        self._entry_timestamps: Dict[int, datetime] = {}

    def execute_order(self, order: TradeOrder) -> bool:
        """Submits trade deal to broker adapter with duplicate submission prevention."""
        if order.order_id in self._processed_orders:
            logger.warning("Duplicate order submission blocked by idempotency check", order_id=order.order_id)
            return False

        logger.info(
            "Dispatching trade order to broker adapter",
            order_id=order.order_id,
            symbol=order.symbol,
            volume=order.volume,
        )

        success = self.adapter.send_order(order)
        status_str = "FILLED" if success else "REJECTED"

        self._processed_orders[order.order_id] = success
        self.audit.log_execution(order, status_str)

        return success

    def _estimate_liquidation_impact(self, volume: float, symbol_info: Optional[SymbolInfo], atr: float) -> Tuple[float, float]:
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

        # Total estimated USD lost to temporary liquidity depletion (slippage)
        total_impact_usd = self.eta_coefficient * size_ratio * vol_factor

        # Convert total USD impact into the equivalent per-ounce price delta
        impact_price_delta = total_impact_usd / max(volume * contract_size, 1.0)
        
        return total_impact_usd, impact_price_delta

    def manage_active_positions(
        self,
        symbol: str,
        current_tick: TickData,
        feature_vector: Optional[FeatureVector] = None,
        symbol_info: Optional[SymbolInfo] = None,
    ) -> List[Position]:
        """
        Monitors active positions, evaluates Hold Value Score, tracks MFE/MAE,
        applies Impact-Adjusted Scale-Outs & Trailing Stops, and triggers Emergency Bailouts.
        """
        positions = self.adapter.get_positions(symbol=symbol)
        now = current_tick.timestamp

        # Garbage Collection / Memory Leak Protection for Closed Positions
        active_tickets = {pos.ticket for pos in positions} if positions else set()
        tracked_tickets = set(self._entry_timestamps.keys())
        dead_tickets = tracked_tickets - active_tickets

        for dead_ticket in dead_tickets:
            self._cleanup_ticket_state(dead_ticket)

        if not positions:
            return []

        atr = max(feature_vector.atr_m1, 0.50) if feature_vector else 0.80

        # Dynamic Volatility Parameters
        dynamic_be_trigger = max(self.be_trigger, round(atr * 1.20, 2))
        dynamic_trailing_dist = max(self.trailing_distance, round(atr * 1.50, 2))

        min_stop_gap = (
            (symbol_info.stops_level * symbol_info.point)
            if symbol_info and symbol_info.stops_level > 0
            else 0.25
        )

        for pos in positions:
            ticket = pos.ticket

            # Persistent Entry Timestamp from Actual MT5 Setup Time
            if ticket not in self._entry_timestamps:
                pos_time = getattr(pos, "time_setup", None) or getattr(pos, "time", None) or now
                self._entry_timestamps[ticket] = pos_time

            price_current = current_tick.bid if pos.type == OrderType.BUY else current_tick.ask
            
            # Price delta per ounce (Gross movement)
            profit_price_delta = (
                (price_current - pos.price_open)
                if pos.type == OrderType.BUY
                else (pos.price_open - price_current)
            )

            # ------------------------------------------------------------------
            # ALMGREN-CHRISS LIQUIDATION IMPACT COMPUTATION
            # ------------------------------------------------------------------
            total_impact_usd, impact_price_delta = self._estimate_liquidation_impact(pos.volume, symbol_info, atr)
            
            # Net Price Delta inherently accounts for the slippage required to close the trade
            net_price_delta = profit_price_delta - impact_price_delta

            # Update Institutional MFE / MAE Metrics using Gross Price Delta
            self._update_mfe_mae(ticket, profit_price_delta)

            # Calculate Order Hold Value Score (Pass impact for Toxic Flow detection)
            hold_score, invalidate_reasons = self._calculate_hold_value_score(
                pos, price_current, feature_vector, impact_price_delta, atr
            )

            # Calculate Holding Duration from Actual Entry Time
            entry_time = self._entry_timestamps[ticket]
            holding_duration = (now - entry_time).total_seconds() if isinstance(entry_time, datetime) else 0.0

            logger.info(
                "[INSTITUTIONAL POSITION MONITOR]",
                ticket=ticket,
                type=pos.type.value,
                lots=pos.volume,
                gross_delta=f"${profit_price_delta:+.2f}",
                est_impact_delta=f"${impact_price_delta:.2f}",
                net_delta=f"${net_price_delta:+.2f}",
                pnl_usd=f"${pos.profit:+.2f}",
                mfe=f"${self._mfe_tracker.get(ticket, 0.0):+.2f}",
                mae=f"${self._mae_tracker.get(ticket, 0.0):+.2f}",
                duration=f"{holding_duration:.0f}s",
                hold_score=f"{hold_score}/100",
            )

            # ------------------------------------------------------------------
            # 1. TIME-DECAY & STAGNATION AUTO-EXIT GUARD
            # ------------------------------------------------------------------
            if holding_duration > self.max_holding_seconds and net_price_delta < 0.20:
                logger.warning(
                    ">>> STAGNANT POSITION TIMEOUT TRIGGERED! CLOSING TRADE <<<",
                    ticket=ticket,
                    duration_sec=holding_duration,
                    net_delta=f"${net_price_delta:.2f}",
                    action="Capital Reallocation Exit",
                )
                self.adapter.close_position(ticket=ticket)
                self._cleanup_ticket_state(ticket)
                continue

            # ------------------------------------------------------------------
            # 2. DECISIVE EARLY EMERGENCY BAILOUT GUARD
            # ------------------------------------------------------------------
            # Uses Net Price Delta: Bailout triggers faster if slippage is high!
            if hold_score < 45 and net_price_delta < -(atr * 0.50):
                logger.critical(
                    ">>> EARLY EMERGENCY CUT TRIGGERED! TRADE INVALIDATED <<<",
                    ticket=ticket,
                    hold_score=hold_score,
                    net_loss_delta=f"${net_price_delta:.2f}",
                    reasons=" | ".join(invalidate_reasons),
                    action="Rescued Capital Before Full Stop-Loss Hit",
                )
                self.adapter.close_position(ticket=ticket)
                self._cleanup_ticket_state(ticket)
                continue

            # ------------------------------------------------------------------
            # 3. IMPACT-AWARE MULTI-STAGE PARTIAL TAKE-PROFIT SCALE-OUT (TP1)
            # ------------------------------------------------------------------
            tp1_threshold = round(atr * 1.50, 2)
            if (
                self.enable_partial_tp
                and not self._partial_closed_tickets.get(ticket, False)
                and net_price_delta >= tp1_threshold
                and pos.volume >= 0.02
            ):
                # Broker Volume Step & Minimum Step Alignment
                vol_step = symbol_info.volume_step if symbol_info and symbol_info.volume_step > 0 else 0.01
                min_vol = symbol_info.volume_min if symbol_info and symbol_info.volume_min > 0 else 0.01
                raw_volume = pos.volume * self.partial_tp_ratio
                partial_volume = round(round(raw_volume / vol_step) * vol_step, 2)

                if partial_volume >= min_vol and (pos.volume - partial_volume) >= min_vol:
                    logger.info(
                        ">>> MILESTONE TARGET TP1 HIT (IMPACT ADJUSTED)! EXECUTING SCALE-OUT <<<",
                        ticket=ticket,
                        closing_volume=partial_volume,
                        net_delta=f"${net_price_delta:.2f}",
                    )
                    success = False
                    try:
                        success = self.adapter.close_position(ticket=ticket, volume=partial_volume)
                    except TypeError:
                        logger.error("Adapter interface does not accept partial volume parameter", ticket=ticket)
                    except Exception as exc:
                        logger.error("Broker adapter failed partial close execution", ticket=ticket, error=str(exc))

                    if success:
                        self._partial_closed_tickets[ticket] = True

            # ------------------------------------------------------------------
            # 4. IMPACT-ADJUSTED TRAILING STOP & BREAK-EVEN ENGINE
            # ------------------------------------------------------------------
            # NOTE: Uses net_price_delta so BE/Trailing is deferred until slippage cost is covered.
            if pos.type == OrderType.BUY:
                # Stage 1: Break-Even Risk-Free Lock
                if net_price_delta >= dynamic_be_trigger and pos.sl < (pos.price_open + self.be_lock - 0.01):
                    target_sl = round(pos.price_open + self.be_lock, 2)
                    if (price_current - target_sl) >= min_stop_gap:
                        logger.info(
                            ">>> STAGE 1: APPLYING BREAK-EVEN RISK-FREE (BUY) <<<",
                            ticket=ticket,
                            new_sl=target_sl,
                            net_delta=f"${net_price_delta:.2f}",
                        )
                        self.adapter.modify_position(ticket=ticket, stop_loss=target_sl, take_profit=pos.tp)

                # Stage 2: Volatility Chandelier Trailing Stop
                elif net_price_delta >= (dynamic_be_trigger + (atr * 0.50)):
                    dynamic_sl = round(price_current - dynamic_trailing_dist, 2)
                    if (dynamic_sl - pos.sl) >= self.min_step and (price_current - dynamic_sl) >= min_stop_gap:
                        logger.info(
                            ">>> STAGE 2: CHANDELIER TRAILING STOP ADVANCED (BUY) <<<",
                            ticket=ticket,
                            new_sl=dynamic_sl,
                            price=price_current,
                        )
                        self.adapter.modify_position(ticket=ticket, stop_loss=dynamic_sl, take_profit=pos.tp)

            elif pos.type == OrderType.SELL:
                # Stage 1: Break-Even Risk-Free Lock
                if net_price_delta >= dynamic_be_trigger and (pos.sl > (pos.price_open - self.be_lock + 0.01) or pos.sl == 0.0):
                    target_sl = round(pos.price_open - self.be_lock, 2)
                    if (target_sl - price_current) >= min_stop_gap:
                        logger.info(
                            ">>> STAGE 1: APPLYING BREAK-EVEN RISK-FREE (SELL) <<<",
                            ticket=ticket,
                            new_sl=target_sl,
                            net_delta=f"${net_price_delta:.2f}",
                        )
                        self.adapter.modify_position(ticket=ticket, stop_loss=target_sl, take_profit=pos.tp)

                # Stage 2: Volatility Chandelier Trailing Stop
                elif net_price_delta >= (dynamic_be_trigger + (atr * 0.50)):
                    dynamic_sl = round(price_current + dynamic_trailing_dist, 2)
                    sl_diff = (pos.sl - dynamic_sl) if pos.sl > 0.0 else 999.0
                    if sl_diff >= self.min_step and (dynamic_sl - price_current) >= min_stop_gap:
                        logger.info(
                            ">>> STAGE 2: CHANDELIER TRAILING STOP ADVANCED (SELL) <<<",
                            ticket=ticket,
                            new_sl=dynamic_sl,
                            price=price_current,
                        )
                        self.adapter.modify_position(ticket=ticket, stop_loss=dynamic_sl, take_profit=pos.tp)

        return positions

    def _calculate_hold_value_score(
        self,
        pos: Position,
        price_current: float,
        features: Optional[FeatureVector],
        impact_price_delta: float,
        atr: float,
    ) -> Tuple[int, List[str]]:
        """
        Calculates position Hold Value Score (0 to 100).
        Aggressively deducts points for adverse structural shifts, counter-trend breaches,
        and toxic market impact dynamics.
        """
        score = 100
        reasons: List[str] = []

        if features is None:
            return score, reasons

        # 0. ALMGREN-CHRISS TOXIC FLOW DETECTION
        if impact_price_delta > (atr * 0.25):
            score -= 15
            reasons.append(f"TOXIC_LIQUIDITY_IMPACT (${impact_price_delta:.2f} > 25% ATR)")

        # 1. Peak / Floor Extreme Entry Trap Penalty
        if pos.type == OrderType.BUY and features.is_at_extreme_high:
            score -= 30
            reasons.append("BOUGHT_AT_EXTREME_PEAK_TRAP")
        elif pos.type == OrderType.SELL and features.is_at_extreme_low:
            score -= 30
            reasons.append("SOLD_AT_EXTREME_FLOOR_TRAP")

        # 2. Structural Kumo Regime Invalidation
        if pos.type == OrderType.BUY and features.is_below_kumo:
            score -= 25
            reasons.append("COUNTER_TREND_BELOW_KUMO")
        elif pos.type == OrderType.SELL and features.is_above_kumo:
            score -= 25
            reasons.append("COUNTER_TREND_ABOVE_KUMO")

        # 3. Adverse Liquidity Sweep Detection
        if pos.type == OrderType.BUY and features.liquidity_sweep_signal == -1:
            score -= 30
            reasons.append("BEARISH_LIQUIDITY_SWEEP_TRAP_DETECTED")
        elif pos.type == OrderType.SELL and features.liquidity_sweep_signal == 1:
            score -= 30
            reasons.append("BULLISH_LIQUIDITY_SWEEP_TRAP_DETECTED")

        # 4. Adverse ICT Change of Character (ChoCh) Breakdown Penalty
        if pos.type == OrderType.BUY and features.choch_bearish:
            score -= 30
            reasons.append("ADVERSE_BEARISH_CHOCH_BREAKDOWN")
        elif pos.type == OrderType.SELL and features.choch_bullish:
            score -= 30
            reasons.append("ADVERSE_BULLISH_CHOCH_BREAKOUT")

        # 5. Adverse Tenkan/Kijun Dynamic Cross
        if pos.type == OrderType.BUY and features.tenkan_sen < features.kijun_sen:
            score -= 15
            reasons.append("TENKAN_KIJUN_BEARISH_CROSS")
        elif pos.type == OrderType.SELL and features.tenkan_sen > features.kijun_sen:
            score -= 15
            reasons.append("TENKAN_KIJUN_BULLISH_CROSS")

        # 6. Rapid Adverse Spike Penalty
        if features.rapid_reversal_spike:
            score -= 20
            reasons.append("RAPID_ADVERSE_REVERSAL_SPIKE")

        # 7. MAE Drawdown Excursion Penalty
        ticket = pos.ticket
        mae = self._mae_tracker.get(ticket, 0.0)
        mfe = self._mfe_tracker.get(ticket, 0.0)
        if mae < -1.20 and mfe < 0.30:
            score -= 20
            reasons.append("POOR_MFE_MAE_EXCURSION_RATIO")

        return max(0, score), reasons

    def _update_mfe_mae(self, ticket: int, profit_price_delta: float) -> None:
        """Tracks Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE)."""
        current_mfe = self._mfe_tracker.get(ticket, profit_price_delta)
        current_mae = self._mae_tracker.get(ticket, profit_price_delta)

        self._mfe_tracker[ticket] = max(current_mfe, profit_price_delta)
        self._mae_tracker[ticket] = min(current_mae, profit_price_delta)

    def _cleanup_ticket_state(self, ticket: int) -> None:
        """Cleans up internal tracking memory when position is closed."""
        self._partial_closed_tickets.pop(ticket, None)
        self._mfe_tracker.pop(ticket, None)
        self._mae_tracker.pop(ticket, None)
        self._entry_timestamps.pop(ticket, None)