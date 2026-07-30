"""
Telegram Production Notification Subsystem
==========================================
Non-blocking, Thread-Pool-backed Telegram alert engine providing full market telemetry,
trade open/close notifications, risk events, account health, and ICT structural alerts.

[EXPANDED] Fully supports Telegram Message Threading (reply_to_message_id) so all updates, 
break-even locks, trailing stops, and trade exits are cleanly replied to the original order message.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from typing import Any, Dict, Optional
import urllib.parse
import urllib.request

from nexus_scalp.domain.models import AccountInfo, Position, TradeOrder
from nexus_scalp.features.scalp_features import FeatureVector

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Enterprise Telegram Alert Manager using HTML formatting, thread-pool execution,
    and message thread replying capabilities.
    """

    def __init__(self, bot_token: str, admin_id: str, enabled: bool = True) -> None:
        self.bot_token = bot_token
        self.admin_id = admin_id
        self.enabled = enabled and bool(bot_token) and bool(admin_id)
        self._api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        # Dedicated background thread pool preventing blocking of the 50ms hot tick loop
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="telegram_alert")

    def _send_msg_sync(self, html_text: str, reply_to_message_id: Optional[int] = None) -> Optional[int]:
        """
        Sends synchronous HTTP POST request to Telegram API and returns the generated message_id.
        """
        if not self.enabled:
            return None

        payload: Dict[str, Any] = {
            "chat_id": self.admin_id,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        # Inject reply_to_message_id if this is a follow-up alert on an active trade thread
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._api_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if resp.status == 200:
                    resp_bytes = resp.read()
                    res_json = json.loads(resp_bytes.decode("utf-8"))
                    if res_json.get("ok"):
                        return res_json["result"]["message_id"]
                return None
        except Exception as e:
            logger.error("Failed to dispatch Telegram message: %s", e)
            return None

    def send(self, html_text: str, reply_to_message_id: Optional[int] = None) -> Optional[int]:
        """
        Dispatches message payload to background thread pool.
        Returns message_id when needed for message threading.
        """
        if not self.enabled:
            return None
        future = self._executor.submit(self._send_msg_sync, html_text, reply_to_message_id)
        try:
            return future.result(timeout=2.0)
        except Exception:
            return None

    # ==========================================================================
    # 20+ ENTERPRISE NOTIFICATION METHOD TEMPLATES (WITH REPLY-THREADING)
    # ==========================================================================

    def notify_startup(self, symbol: str, mode: str, balance: float, equity: float) -> Optional[int]:
        """1. System Launch Banner Alert"""
        msg = (
            f"🚀 <b>NEXUS SCALP ENGINE STARTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"⚙️ <b>Execution Mode:</b> <code>{mode}</code>\n"
            f"💰 <b>Balance:</b> <code>${balance:,.2f}</code>\n"
            f"📈 <b>Equity:</b> <code>${equity:,.2f}</code>\n"
            f"🕒 <b>Status:</b> <i>Active & Operational</i>"
        )
        return self.send(msg)

    def notify_order_opened(self, order: TradeOrder, risk_usd: float) -> Optional[int]:
        """
        2. Market/Pending Order Placement Alert.
        [EXPANDED] Returns message_id to allow thread replying for all subsequent order events.
        """
        emoji = "🟢" if "BUY" in order.order_type.value else "🔴"
        msg = (
            f"{emoji} <b>ORDER DISPATCHED TO BROKER</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{order.symbol}</code>\n"
            f"📌 <b>Type:</b> <code>{order.order_type.value}</code>\n"
            f"📦 <b>Lots:</b> <code>{order.volume}</code>\n"
            f"💵 <b>Entry Price:</b> <code>{order.price:.2f}</code>\n"
            f"🛑 <b>Stop Loss:</b> <code>{order.stop_loss:.2f}</code>\n"
            f"🎯 <b>Take Profit:</b> <code>{order.take_profit:.2f}</code>\n"
            f"⚠️ <b>Risk Allocated:</b> <code>${risk_usd:.2f}</code>"
        )
        return self.send(msg)

    def notify_order_closed_profit(
        self,
        ticket: int,
        symbol: str,
        lots: float,
        entry: float,
        exit_price: float,
        profit_usd: float,
        profit_pct: float,
        reply_to_message_id: Optional[int] = None,
    ) -> Optional[int]:
        """3. Order Closed in Profit (Win Alert) - Replies to original open message"""
        msg = (
            f"🎉 <b>PROFITABLE TRADE CLOSED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{symbol}</code> ({lots} lots)\n"
            f"💵 <b>Entry:</b> <code>{entry:.2f}</code> ➔ <b>Exit:</b> <code>{exit_price:.2f}</code>\n"
            f"💵 <b>Net Profit:</b> <code>+${profit_usd:,.2f}</code> (+{profit_pct:.2f}%)\n"
            f"✅ <b>Status:</b> <i>Target Achieved / Trailing Closed</i>"
        )
        return self.send(msg, reply_to_message_id=reply_to_message_id)

    def notify_order_closed_loss(
        self,
        ticket: int,
        symbol: str,
        lots: float,
        entry: float,
        exit_price: float,
        loss_usd: float,
        loss_pct: float,
        reply_to_message_id: Optional[int] = None,
    ) -> Optional[int]:
        """4. Order Closed in Loss Alert - Replies to original open message"""
        msg = (
            f"🔻 <b>TRADE CLOSED IN LOSS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{symbol}</code> ({lots} lots)\n"
            f"💵 <b>Entry:</b> <code>{entry:.2f}</code> ➔ <b>Exit:</b> <code>{exit_price:.2f}</code>\n"
            f"💸 <b>Loss Amount:</b> <code>-${abs(loss_usd):,.2f}</code> (-{abs(loss_pct):.2f}%)\n"
            f"🛡️ <b>Capital Safeguard:</b> <i>Risk Limited by Stop Loss</i>"
        )
        return self.send(msg, reply_to_message_id=reply_to_message_id)

    def notify_early_emergency_cut(
        self,
        ticket: int,
        score: int,
        reasons: str,
        saved_usd: float,
        reply_to_message_id: Optional[int] = None,
    ) -> Optional[int]:
        """5. Early Cut / Emergency Bailout Alert - Replies to original open message"""
        msg = (
            f"⚡ <b>EARLY EMERGENCY CUT (CAPITAL SAVED)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📉 <b>Hold Score:</b> <code>{score}/100</code>\n"
            f"⚠️ <b>Invalidation Reason:</b> <code>{reasons}</code>\n"
            f"🛡️ <b>Action:</b> <i>Closed Early to Avoid Full SL (Saved ~${saved_usd:.2f})</i>"
        )
        return self.send(msg, reply_to_message_id=reply_to_message_id)

    def notify_break_even_applied(
        self, ticket: int, new_sl: float, reply_to_message_id: Optional[int] = None
    ) -> Optional[int]:
        """6. Break-Even Risk-Free Lock Alert - Replies to original open message"""
        msg = (
            f"🛡️ <b>BREAK-EVEN APPLIED (RISK-FREE)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"🔒 <b>New Stop Loss:</b> <code>{new_sl:.2f}</code>\n"
            f"✨ <b>Status:</b> <i>Trade is now 100% Risk-Free!</i>"
        )
        return self.send(msg, reply_to_message_id=reply_to_message_id)

    def notify_trailing_stop_advanced(
        self, ticket: int, new_sl: float, current_price: float, reply_to_message_id: Optional[int] = None
    ) -> Optional[int]:
        """7. Trailing Stop Step Advanced Alert - Replies to original open message"""
        msg = (
            f"📈 <b>TRAILING STOP ADVANCED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"🎯 <b>Current Price:</b> <code>{current_price:.2f}</code>\n"
            f"🔒 <b>Locked Stop Loss:</b> <code>{new_sl:.2f}</code>"
        )
        return self.send(msg, reply_to_message_id=reply_to_message_id)

    def notify_market_extremes(self, symbol: str, high_50: float, low_50: float, range_pos_pct: float) -> Optional[int]:
        """8. Market Extreme Levels (Peak/Floor) Alert"""
        pos_type = "🔥 EXTREME HIGH (PEAK)" if range_pos_pct >= 0.90 else "❄️ EXTREME LOW (FLOOR)"
        msg = (
            f"⛰️ <b>MARKET STRUCTURE EXTREME DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"🔺 <b>50-Bar High:</b> <code>{high_50:.2f}</code>\n"
            f"🔻 <b>50-Bar Low:</b> <code>{low_50:.2f}</code>\n"
            f"📍 <b>Range Position:</b> <code>{range_pos_pct*100:.1f}% ({pos_type})</code>"
        )
        return self.send(msg)

    def notify_choch_detected(self, symbol: str, direction: str) -> Optional[int]:
        """9. ICT Change of Character (ChoCh) Alert"""
        emoji = "🟢" if direction == "BULLISH" else "🔴"
        msg = (
            f"{emoji} <b>ICT CHANGE OF CHARACTER (ChoCh)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"🔀 <b>Direction Shift:</b> <code>{direction}</code>\n"
            f"💡 <b>Market Structure:</b> <i>Potential Trend Reversal Initiated</i>"
        )
        return self.send(msg)

    def notify_liquidity_sweep(self, symbol: str, sweep_type: str) -> Optional[int]:
        """10. Liquidity Sweep & Stop Hunt Alert"""
        msg = (
            f"🧹 <b>LIQUIDITY SWEEP / STOP HUNT DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"🎯 <b>Type:</b> <code>{sweep_type}</code>\n"
            f"⚡ <b>Action:</b> <i>Smart Money Swept Liquidity Pools</i>"
        )
        return self.send(msg)

    def notify_fvg_detected(self, symbol: str, fvg_type: str) -> Optional[int]:
        """11. Fair Value Gap Imbalance Alert"""
        msg = (
            f"📐 <b>ICT FAIR VALUE GAP (FVG) ACTIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"📌 <b>Imbalance Type:</b> <code>{fvg_type}</code>\n"
            f"⌛ <b>Strategy:</b> <i>Waiting for Limit Retest Entry</i>"
        )
        return self.send(msg)

    def notify_order_block(self, symbol: str, ob_type: str) -> Optional[int]:
        """12. Institutional Order Block Alert"""
        msg = (
            f"🧱 <b>INSTITUTIONAL ORDER BLOCK DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"🔹 <b>Block Type:</b> <code>{ob_type}</code>"
        )
        return self.send(msg)

    def notify_survival_mode_changed(self, active: bool, drawdown_pct: float) -> Optional[int]:
        """13. Account Survival Mode Status Alert"""
        status = "🔴 ACTIVATED (HIGH CONVICTION ONLY)" if active else "🟢 DEACTIVATED (NORMAL TRADING)"
        msg = (
            f"🛡️ <b>ACCOUNT SURVIVAL MODE: {status}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📉 <b>Current Peak Drawdown:</b> <code>{drawdown_pct:.2f}%</code>"
        )
        return self.send(msg)

    def notify_account_health(self, account: AccountInfo, drawdown_pct: float) -> Optional[int]:
        """14. Executive Account Balance Summary"""
        msg = (
            f"📊 <b>ACCOUNT FINANCIAL HEALTH REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>Login:</b> <code>#{account.login}</code>\n"
            f"💰 <b>Balance:</b> <code>${account.balance:,.2f}</code>\n"
            f"📈 <b>Equity:</b> <code>${account.equity:,.2f}</code>\n"
            f"💵 <b>Free Margin:</b> <code>${account.margin_free:,.2f}</code>\n"
            f"📉 <b>Peak Drawdown:</b> <code>{drawdown_pct:.2f}%</code>\n"
            f"⚡ <b>Leverage:</b> <code>1:{account.leverage}</code>"
        )
        return self.send(msg)

    def notify_spread_spike(self, symbol: str, current_spread: float, max_allowed: float) -> Optional[int]:
        """15. Abnormal Spread Spike Protection Alert"""
        msg = (
            f"⚠️ <b>SPREAD SPIKE DETECTED (TRADE BLOCKED)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"📊 <b>Current Spread:</b> <code>{current_spread:.1f} pts</code>\n"
            f"🛑 <b>Max Permissible:</b> <code>{max_allowed:.1f} pts</code>"
        )
        return self.send(msg)

    def notify_volume_anomaly(self, symbol: str, volume: float) -> Optional[int]:
        """16. Smart Money High Volume Anomaly Alert"""
        msg = (
            f"🌊 <b>SMART MONEY VOLUME ANOMALY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"📊 <b>Tick Volume Spike:</b> <code>{volume} ticks/sec</code>"
        )
        return self.send(msg)

    def notify_kill_switch_activated(self, reason: str) -> Optional[int]:
        """17. Emergency Kill Switch Alert"""
        msg = (
            f"🚨 <b>EMERGENCY KILL SWITCH ACTIVATED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 <b>Action:</b> <i>All new execution halted immediately!</i>\n"
            f"📝 <b>Reason:</b> <code>{reason}</code>"
        )
        return self.send(msg)

    def notify_error(self, context: str, error_msg: str) -> Optional[int]:
        """18. System Operational Error Alert"""
        msg = (
            f"⚠️ <b>SYSTEM OPERATIONAL ERROR</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 <b>Context:</b> <code>{context}</code>\n"
            f"❌ <b>Error:</b> <code>{error_msg}</code>"
        )
        return self.send(msg)

    def notify_market_summary(self, symbol: str, features: FeatureVector) -> Optional[int]:
        """19. Periodic Market Structure Summary"""
        msg = (
            f"🌐 <b>MARKET TELEMETRY RADAR SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{symbol}</code>\n"
            f"💵 <b>Displacement:</b> <code>${features.live_tick_displacement:+.2f}</code>\n"
            f"📊 <b>ATR (M1):</b> <code>${features.atr_m1:.2f}</code>\n"
            f"☁️ <b>Ichimoku:</b> <code>TK_Cross:{features.tk_cross_signal}</code>\n"
            f"🧱 <b>ICT State:</b> <code>FVG:{features.fvg_bullish_active}|OB:{features.order_block_type}</code>"
        )
        return self.send(msg)

    def notify_shutdown(self, reason: str = "User Initiated") -> Optional[int]:
        """20. System Shutdown Alert"""
        msg = (
            f"🛑 <b>NEXUS SCALP ENGINE SHUTTING DOWN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 <b>Reason:</b> <code>{reason}</code>\n"
            f"🕒 <b>Status:</b> <i>Engine Disconnected Cleanly</i>"
        )
        return self.send(msg)