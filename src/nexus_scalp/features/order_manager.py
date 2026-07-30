"""
Order Lifecycle & Hold Value Position Management Engine
======================================================
Monitors active open positions, calculates Hold Value Score (0 to 100), applies 
Break-Even & Trailing Stops with Wick Tolerance, triggers Early Emergency Cut 
if a trade encounters a Bull/Bear Trap, and dispatches Thread-Replied Telegram Alerts 
upon trade exit (TP/SL).
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData, TradeOrder
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.observability.telegram_notifier import TelegramNotifier
from nexus_scalp.ports.mt5_port import IMT5Port

logger = get_logger("nexus_scalp.execution.order_manager")


class OrderLifecycleManager:
    """
    Institutional position management engine handling smart executions, trailing stops, 
    time-decay bailouts, structural invalidation emergency cuts, and Telegram thread replying.
    """

    def __init__(
        self,
        adapter: IMT5Port,
        audit_repo: Optional[AuditRepository] = None,
        notifier: Optional[TelegramNotifier] = None, # [EXPANDED] Telegram Integration
        be_trigger_usd: float = 0.50,         # Trigger Break-Even at +$0.50 profit on Gold
        be_lock_usd: float = 0.10,            # Lock in +$0.10 profit when BE triggers
        trailing_distance_usd: float = 0.80,   # Maintain $0.80 trailing gap behind price
        min_modify_step_usd: float = 0.15,     # Minimum SL change to prevent MT5 spamming
        stale_trade_seconds: float = 300.0,    # 5 Minutes max hold without momentum
    ) -> None:
        self.adapter = adapter
        self.audit = audit_repo or AuditRepository()
        self.notifier = notifier
        self._processed_orders: Dict[str, bool] = {}

        self.be_trigger = be_trigger_usd
        self.be_lock = be_lock_usd
        self.trailing_distance = trailing_distance_usd
        self.min_step = min_modify_step_usd
        self.stale_trade_seconds = stale_trade_seconds
        
        # Tracks exact UTC timestamp when each position ticket was first detected open
        self._position_open_times: Dict[int, datetime] = {}
        
        # [EXPANDED] Maps position ticket -> Telegram message_id for Thread Replying
        self._order_message_ids: Dict[int, int] = {}
        
        # [EXPANDED] Set tracking active tickets to detect closed trades
        self._known_active_tickets: Set[int] = set()

    def register_telegram_message(self, ticket: int, message_id: Optional[int]) -> None:
        """[NEW] Associates a broker position ticket with its primary Telegram message_id."""
        if message_id is not None:
            self._order_message_ids[ticket] = message_id

    def execute_order(self, order: TradeOrder) -> bool:
        """
        Submits trade deal to broker adapter with duplicate submission prevention.
        """
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

    def manage_active_positions(
        self,
        symbol: str,
        current_tick: TickData,
        feature_vector: Optional[FeatureVector] = None,
        symbol_info: Optional[SymbolInfo] = None,
    ) -> List[Position]:
        """
        Monitors active positions, evaluates Hold Value Score, applies Wick-Tolerant Trailing Stops,
        detects closed trades to send Telegram Replies, and triggers Emergency Cut if needed.
        """
        positions = self.adapter.get_positions(symbol=symbol)
        current_tickets = {p.ticket for p in positions}

        # ----------------------------------------------------------------------
        # [EXPANDED] CLOSED TRADE DETECTION & TELEGRAM THREAD REPLIES
        # ----------------------------------------------------------------------
        closed_tickets = self._known_active_tickets - current_tickets
        if closed_tickets:
            # Query actual deal history from MT5 server
            history_deals = self.adapter.get_closed_deals_history(symbol=symbol, hours_back=1)
            for c_ticket in closed_tickets:
                msg_id = self._order_message_ids.get(c_ticket)
                matched_deal = next((d for d in history_deals if d.get("position_ticket") == c_ticket), None)

                if matched_deal and self.notifier:
                    profit_usd = matched_deal.get("profit", 0.0) + matched_deal.get("swap", 0.0) + matched_deal.get("commission", 0.0)
                    lots = matched_deal.get("volume", 0.0)
                    exit_price = matched_deal.get("price", 0.0)

                    if profit_usd >= 0:
                        logger.info("TRADE CLOSED IN PROFIT (TP/TRAILING)", ticket=c_ticket, net_pnl=profit_usd)
                        self.notifier.notify_order_closed_profit(
                            ticket=c_ticket,
                            symbol=symbol,
                            lots=lots,
                            entry=0.0,
                            exit_price=exit_price,
                            profit_usd=profit_usd,
                            profit_pct=0.0,
                            reply_to_message_id=msg_id,
                        )
                    else:
                        logger.warning("TRADE CLOSED IN LOSS (SL/CUT)", ticket=c_ticket, loss_usd=profit_usd)
                        self.notifier.notify_order_closed_loss(
                            ticket=c_ticket,
                            symbol=symbol,
                            lots=lots,
                            entry=0.0,
                            exit_price=exit_price,
                            loss_usd=profit_usd,
                            loss_pct=0.0,
                            reply_to_message_id=msg_id,
                        )

                # Clean up ticket trackers
                self._position_open_times.pop(c_ticket, None)
                self._order_message_ids.pop(c_ticket, None)

        self._known_active_tickets = current_tickets

        if not positions:
            return []

        min_stop_gap = (
            (symbol_info.stops_level * symbol_info.point)
            if symbol_info and symbol_info.stops_level > 0
            else 0.20
        )
        
        atr = feature_vector.atr_m1 if feature_vector else 1.50

        for pos in positions:
            if pos.ticket not in self._position_open_times:
                self._position_open_times[pos.ticket] = current_tick.timestamp
                
            open_time = self._position_open_times[pos.ticket]
            duration_sec = (current_tick.timestamp - open_time).total_seconds()

            price_current = current_tick.bid if pos.type == OrderType.BUY else current_tick.ask
            profit_usd = (
                (price_current - pos.price_open)
                if pos.type == OrderType.BUY
                else (pos.price_open - price_current)
            )

            # Calculate Hold Value Score (0 to 100)
            hold_score, invalidate_reasons = self._calculate_hold_value_score(
                pos, price_current, feature_vector
            )

            if duration_sec > self.stale_trade_seconds:
                hold_score -= 40
                invalidate_reasons.append(f"TIME_DECAY_STALE_TRADE ({int(duration_sec)}s)")

            logger.info(
                "[ACTIVE POSITION MONITOR]",
                ticket=pos.ticket,
                type=pos.type.value,
                lots=pos.volume,
                duration=f"{int(duration_sec)}s",
                current=f"{price_current:.2f}",
                profit_per_oz=f"${profit_usd:+.2f}",
                pnl_usd=f"${pos.profit:+.2f}",
                hold_score=f"{hold_score}/100",
            )

            # ------------------------------------------------------------------
            # 1. EARLY EMERGENCY BAILOUT GUARD (Survival Protection)
            # ------------------------------------------------------------------
            if hold_score < 40:
                msg_id = self._order_message_ids.get(pos.ticket)
                if profit_usd > 0.05:
                    logger.warning(">>> BAILING OUT AT BREAK-EVEN! Momentum lost. <<<", ticket=pos.ticket)
                    if self.adapter.close_position(ticket=pos.ticket) and self.notifier:
                        self.notifier.notify_early_emergency_cut(
                            ticket=pos.ticket,
                            score=hold_score,
                            reasons=" | ".join(invalidate_reasons),
                            saved_usd=profit_usd,
                            reply_to_message_id=msg_id,
                        )
                    continue
                elif profit_usd < -0.40:
                    logger.critical(">>> EARLY EMERGENCY CUT TRIGGERED! Rescuing Capital. <<<", ticket=pos.ticket)
                    if self.adapter.close_position(ticket=pos.ticket) and self.notifier:
                        self.notifier.notify_early_emergency_cut(
                            ticket=pos.ticket,
                            score=hold_score,
                            reasons=" | ".join(invalidate_reasons),
                            saved_usd=abs(profit_usd),
                            reply_to_message_id=msg_id,
                        )
                    continue

            # ------------------------------------------------------------------
            # 2. SMART TRAILING STOP & BREAK-EVEN ENGINE
            # ------------------------------------------------------------------
            smart_trailing_dist = max(self.trailing_distance, atr * 1.5)
            msg_id = self._order_message_ids.get(pos.ticket)

            if pos.type == OrderType.BUY:
                if profit_usd >= self.be_trigger and pos.sl < (pos.price_open + self.be_lock - 0.01):
                    target_sl = round(pos.price_open + self.be_lock, 2)
                    if (price_current - target_sl) >= min_stop_gap:
                        logger.info(">>> STAGE 1: APPLYING BREAK-EVEN RISK-FREE (BUY) <<<", ticket=pos.ticket, new_sl=target_sl)
                        if self.adapter.modify_position(ticket=pos.ticket, stop_loss=target_sl, take_profit=pos.tp) and self.notifier:
                            self.notifier.notify_break_even_applied(ticket=pos.ticket, new_sl=target_sl, reply_to_message_id=msg_id)

                elif profit_usd >= (self.be_trigger + 0.30):
                    dynamic_sl = round(price_current - smart_trailing_dist, 2)
                    if (dynamic_sl - pos.sl) >= self.min_step and (price_current - dynamic_sl) >= min_stop_gap:
                        logger.info(">>> STAGE 2: TRAILING STOP ADVANCED (BUY) <<<", ticket=pos.ticket, new_sl=dynamic_sl)
                        if self.adapter.modify_position(ticket=pos.ticket, stop_loss=dynamic_sl, take_profit=pos.tp) and self.notifier:
                            self.notifier.notify_trailing_stop_advanced(ticket=pos.ticket, new_sl=dynamic_sl, current_price=price_current, reply_to_message_id=msg_id)

            elif pos.type == OrderType.SELL:
                if profit_usd >= self.be_trigger and (pos.sl > (pos.price_open - self.be_lock + 0.01) or pos.sl == 0.0):
                    target_sl = round(pos.price_open - self.be_lock, 2)
                    if (target_sl - price_current) >= min_stop_gap:
                        logger.info(">>> STAGE 1: APPLYING BREAK-EVEN RISK-FREE (SELL) <<<", ticket=pos.ticket, new_sl=target_sl)
                        if self.adapter.modify_position(ticket=pos.ticket, stop_loss=target_sl, take_profit=pos.tp) and self.notifier:
                            self.notifier.notify_break_even_applied(ticket=pos.ticket, new_sl=target_sl, reply_to_message_id=msg_id)

                elif profit_usd >= (self.be_trigger + 0.30):
                    dynamic_sl = round(price_current + smart_trailing_dist, 2)
                    if (pos.sl - dynamic_sl) >= self.min_step and (dynamic_sl - price_current) >= min_stop_gap:
                        logger.info(">>> STAGE 2: TRAILING STOP ADVANCED (SELL) <<<", ticket=pos.ticket, new_sl=dynamic_sl)
                        if self.adapter.modify_position(ticket=pos.ticket, stop_loss=dynamic_sl, take_profit=pos.tp) and self.notifier:
                            self.notifier.notify_trailing_stop_advanced(ticket=pos.ticket, new_sl=dynamic_sl, current_price=price_current, reply_to_message_id=msg_id)

        return positions

    def _calculate_hold_value_score(
        self,
        pos: Position,
        price_current: float,
        features: Optional[FeatureVector],
    ) -> Tuple[int, List[str]]:
        score = 100
        reasons: List[str] = []

        if features is None:
            return score, reasons

        if pos.type == OrderType.BUY and features.is_at_extreme_high:
            score -= 30
            reasons.append("BOUGHT_AT_EXTREME_PEAK_TRAP")

        elif pos.type == OrderType.SELL and features.is_at_extreme_low:
            score -= 30
            reasons.append("SOLD_AT_EXTREME_FLOOR_TRAP")

        if pos.type == OrderType.BUY and features.is_below_kumo:
            score -= 25
            reasons.append("COUNTER_TREND_BELOW_KUMO")

        elif pos.type == OrderType.SELL and features.is_above_kumo:
            score -= 25
            reasons.append("COUNTER_TREND_ABOVE_KUMO")

        if pos.type == OrderType.BUY and features.liquidity_sweep_signal == -1:
            score -= 40
            reasons.append("BEARISH_LIQUIDITY_SWEEP_TRAP_DETECTED")

        elif pos.type == OrderType.SELL and features.liquidity_sweep_signal == 1:
            score -= 40
            reasons.append("BULLISH_LIQUIDITY_SWEEP_TRAP_DETECTED")

        if features.rapid_reversal_spike:
            score -= 20
            reasons.append("RAPID_ADVERSE_REVERSAL_SPIKE")

        return max(0, score), reasons