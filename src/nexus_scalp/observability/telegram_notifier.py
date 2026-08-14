# ruff: noqa: RUF012, UP045
"""
Telegram Production Notification Subsystem
==========================================
Non-blocking, Thread-Pool-backed Telegram alert engine providing full market telemetry,
trade open/close notifications, risk events, account health, and ICT structural alerts.

[EXPANDED] Fully supports Telegram Message Threading (reply_to_message_id) so all updates,
break-even locks, trailing stops, and trade exits are cleanly replied to the original order message.
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from nexus_scalp.domain.models import AccountInfo, TradeOrder
from nexus_scalp.features.scalp_features import FeatureVector

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Enterprise Telegram Alert Manager using HTML formatting, thread-pool execution,
    and message thread replying capabilities.
    """

    SEVERITY_WEIGHTS = {
        "INFO": 1,
        "WARNING": 2,
        "ERROR": 3,
        "CRITICAL": 4,
    }

    def __init__(
        self,
        bot_token: str,
        admin_id: str,
        enabled: bool = True,
        environment: str = "production",
        minimum_severity: str = "INFO",
        timeout_seconds: float = 4.0,
        maximum_retries: int = 3,
        retry_backoff: float = 2.0,
        queue_capacity: int = 100,
        rate_limit: int = 20,
        deduplication_window: float = 60.0,
        cooldown_seconds: float = 300.0,
        graceful_shutdown_timeout: float = 5.0,
    ) -> None:
        self.bot_token = bot_token
        self.admin_id = admin_id or ""
        self.enabled = enabled and bool(bot_token) and bool(self.admin_id)
        self.environment = environment
        self.minimum_severity = minimum_severity.upper()
        self.timeout_seconds = timeout_seconds
        self.maximum_retries = maximum_retries
        self.retry_backoff = retry_backoff
        self.queue_capacity = queue_capacity
        self.rate_limit = rate_limit
        self.deduplication_window = deduplication_window
        self.cooldown_seconds = cooldown_seconds
        self.graceful_shutdown_timeout = graceful_shutdown_timeout

        self._api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage" if bot_token else ""

        # Thread pool with bounded pending tasks limit
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="telegram_alert")
        self._lock = threading.Lock()
        self._pending_tasks_count = 0
        self._sent_timestamps: list[float] = []
        self._recent_messages: dict[str, float] = {}  # signature -> timestamp

    def _escape(self, text: Any) -> str:
        """HTML escapes text to prevent breaking parse_mode='HTML'."""
        return html.escape(str(text))

    def _truncate_message(self, text: str) -> str:
        """Safely truncates message to fit Telegram 4096 character limit."""
        if len(text) <= 4096:
            return text
        truncated = text[:4000] + "\n... [TRUNCATED] ..."
        # Safety closing tags to prevent breaking formatting
        return truncated + "</i></b></code>"

    def _redact_secrets(self, text: str) -> str:
        """Redacts sensitive credentials from the alert body."""
        # Redact Telegram bot tokens
        text = re.sub(r"\d{8,10}:[A-Za-z0-9_-]{35}", "[REDACTED_BOT_TOKEN]", text)
        # Redact generic credentials patterns
        text = re.sub(
            r"(?i)(password|secret|key|token|auth)\s*[:=]\s*[^\s]+", r"\1=[REDACTED]", text
        )
        return text

    def _is_duplicate_or_cooling_down(self, html_text: str) -> bool:
        """Suppress repeating messages within deduplication window."""
        now = time.time()
        # Clean expired records
        self._recent_messages = {
            k: t for k, t in self._recent_messages.items() if now - t < self.deduplication_window
        }

        # Normalize message by removing numbers/IDs
        sig = re.sub(r"\d+", "", html_text)[:150]
        if sig in self._recent_messages:
            return True

        self._recent_messages[sig] = now
        return False




    def notify_info(
        self,
        title: str,
        message: str,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """Generic Info Alert Method."""
        msg = (
            f"ℹ️ <b>{self._escape(title)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{self._escape(message)}"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def send_message(
        self,
        text: str,
        callback: Optional[Any] = None,
        severity: str = "INFO",
    ) -> Optional[int]:
        """Direct text dispatch wrapper."""
        return self.send(text, callback=callback, severity=severity)

    def notify_generic_message(
        self,
        title: str,
        message: str,
        severity: str = "INFO",
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """Custom generic alert with dynamic title and severity."""
        msg = (
            f"🔔 <b>{self._escape(title)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{self._escape(message)}"
        )
        return self.send(msg, callback=callback, severity=severity)


    def _send_msg_sync(
        self,
        html_text: str,
        reply_to_message_id: Optional[int] = None,
        severity: str = "INFO",
    ) -> Optional[int]:
        """
        Sends synchronous HTTP POST request to Telegram API and returns the generated message_id.
        """
        if not self.enabled or not self._api_url:
            return None

        # Build consistent message headers (STEP 4)
        header = f"<b>[{severity}]</b>"
        if self.environment:
            header += f" <b>({self.environment.upper()})</b>"
        header += "\n"

        full_text = self._truncate_message(header + self._redact_secrets(html_text))

        # Check deduplication & cooldown
        with self._lock:
            if self._is_duplicate_or_cooling_down(full_text):
                logger.debug("Deduplicated repeated Telegram alert.")
                return None

            # Rate Limiter
            now = time.time()
            self._sent_timestamps = [t for t in self._sent_timestamps if now - t < 60.0]
            if len(self._sent_timestamps) >= self.rate_limit:
                logger.warning("Telegram rate-limit exceeded (%d msgs/min).", self.rate_limit)
                return None
            self._sent_timestamps.append(now)

        payload: dict[str, Any] = {
            "chat_id": self.admin_id,
            "text": full_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

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

            retries = 0
            backoff = self.retry_backoff
            while retries <= self.maximum_retries:
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                        if resp.status == 200:
                            resp_bytes = resp.read()
                            res_json = json.loads(resp_bytes.decode("utf-8"))
                            if res_json.get("ok"):
                                msg_id = res_json["result"]["message_id"]
                                return int(msg_id) if msg_id is not None else None
                    break
                except Exception as e:
                    retries += 1
                    if retries > self.maximum_retries:
                        logger.error(
                            "Failed to dispatch Telegram message after %d retries: %s",
                            self.maximum_retries,
                            e,
                        )
                        return None
                    time.sleep(backoff)
                    backoff *= 2
            return None
        except Exception as e:
            logger.error("Failed to dispatch Telegram message: %s", e)
            return None

    def send(
        self,
        html_text: str,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
        severity: str = "INFO",
    ) -> Optional[int]:
        """
        Dispatches message payload to background thread pool.
        Returns message_id if resolved synchronously within timeout.
        """
        if not self.enabled:
            if callback:
                try:
                    callback(None)
                except Exception:
                    pass
            return None

        # Check minimum severity filter
        msg_weight = self.SEVERITY_WEIGHTS.get(severity.upper(), 1)
        min_weight = self.SEVERITY_WEIGHTS.get(self.minimum_severity.upper(), 1)
        if msg_weight < min_weight:
            return None

        # Guard queue capacity
        with self._lock:
            if self._pending_tasks_count >= self.queue_capacity:
                if severity.upper() != "CRITICAL":
                    logger.warning(
                        "Telegram queue capacity exceeded (%d). Dropping message.",
                        self.queue_capacity,
                    )
                    return None
            self._pending_tasks_count += 1

        def task() -> Optional[int]:
            try:
                msg_id = self._send_msg_sync(html_text, reply_to_message_id, severity)
                if callback:
                    try:
                        callback(msg_id)
                    except Exception as cb_err:
                        logger.error("Error in Telegram callback: %s", cb_err)
                return msg_id
            finally:
                with self._lock:
                    self._pending_tasks_count -= 1

        future = self._executor.submit(task)
        if callback is not None:
            return None

        # Synchronous fallback wait (keeps backward compatibility for simple scripts & tests)
        try:
            return future.result(timeout=0.05)
        except Exception:
            return None

    def shutdown(self, timeout: Optional[float] = None) -> None:
        """Awaits pending sends and shuts down executor cleanly."""
        self._executor.shutdown(wait=True, cancel_futures=True)

    # ==========================================================================
    # 20+ ENTERPRISE NOTIFICATION METHOD TEMPLATES (WITH REPLY-THREADING)
    # ==========================================================================

    def notify_startup(
        self,
        symbol: str,
        mode: str,
        balance: float,
        equity: float,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """1. System Launch Banner Alert"""
        msg = (
            f"🚀 <b>NEXUS SCALP ENGINE STARTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"⚙️ <b>Execution Mode:</b> <code>{self._escape(mode)}</code>\n"
            f"💰 <b>Balance:</b> <code>${balance:,.2f}</code>\n"
            f"📈 <b>Equity:</b> <code>${equity:,.2f}</code>\n"
            f"🕒 <b>Status:</b> <i>Active & Operational</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_order_opened(
        self,
        order: TradeOrder,
        risk_usd: float,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """
        2. Market/Pending Order Placement Alert.
        [EXPANDED] Returns message_id to allow thread replying for all subsequent order events.
        """
        emoji = "🟢" if "BUY" in order.order_type.value else "🔴"
        msg = (
            f"{emoji} <b>ORDER DISPATCHED TO BROKER</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(order.symbol)}</code>\n"
            f"📌 <b>Type:</b> <code>{self._escape(order.order_type.value)}</code>\n"
            f"📦 <b>Lots:</b> <code>{order.volume}</code>\n"
            f"💵 <b>Entry Price:</b> <code>{order.price:.2f}</code>\n"
            f"🛑 <b>Stop Loss:</b> <code>{order.stop_loss:.2f}</code>\n"
            f"🎯 <b>Take Profit:</b> <code>{order.take_profit:.2f}</code>\n"
            f"⚠️ <b>Risk Allocated:</b> <code>${risk_usd:.2f}</code>"
        )
        return self.send(msg, callback=callback, severity="INFO")

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
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """3. Order Closed in Profit (Win Alert) - Replies to original open message"""
        msg = (
            f"🎉 <b>PROFITABLE TRADE CLOSED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code> ({lots} lots)\n"
            f"💵 <b>Entry:</b> <code>{entry:.2f}</code> ➔ "
            f"<b>Exit:</b> <code>{exit_price:.2f}</code>\n"
            f"💵 <b>Net Profit:</b> <code>+${profit_usd:,.2f}</code> (+{profit_pct:.2f}%)\n"
            f"✅ <b>Status:</b> <i>Target Achieved / Trailing Closed</i>"
        )
        return self.send(
            msg,
            reply_to_message_id=reply_to_message_id,
            callback=callback,
            severity="INFO",
        )

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
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """4. Order Closed in Loss Alert - Replies to original open message"""
        msg = (
            f"🔻 <b>TRADE CLOSED IN LOSS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code> ({lots} lots)\n"
            f"💵 <b>Entry:</b> <code>{entry:.2f}</code> ➔ "
            f"<b>Exit:</b> <code>{exit_price:.2f}</code>\n"
            f"💸 <b>Loss Amount:</b> <code>-${abs(loss_usd):,.2f}</code> (-{abs(loss_pct):.2f}%)\n"
            f"🛡️ <b>Capital Safeguard:</b> <i>Risk Limited by Stop Loss</i>"
        )
        return self.send(
            msg,
            reply_to_message_id=reply_to_message_id,
            callback=callback,
            severity="WARNING",
        )

    def notify_early_emergency_cut(
        self,
        ticket: int,
        score: int,
        reasons: str,
        saved_usd: float,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """5. Early Cut / Emergency Bailout Alert - Replies to original open message"""
        msg = (
            f"⚡ <b>EARLY EMERGENCY CUT (CAPITAL SAVED)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📉 <b>Hold Score:</b> <code>{score}/100</code>\n"
            f"⚠️ <b>Invalidation Reason:</b> <code>{self._escape(reasons)}</code>\n"
            f"🛡️ <b>Action:</b> <i>Closed Early to Avoid Full SL (Saved ~${saved_usd:.2f})</i>"
        )
        return self.send(
            msg,
            reply_to_message_id=reply_to_message_id,
            callback=callback,
            severity="WARNING",
        )

    def notify_break_even_applied(
        self,
        ticket: int,
        new_sl: float,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """6. Break-Even Risk-Free Lock Alert - Replies to original open message"""
        msg = (
            f"🛡️ <b>BREAK-EVEN APPLIED (RISK-FREE)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"🔒 <b>New Stop Loss:</b> <code>{new_sl:.2f}</code>\n"
            f"✨ <b>Status:</b> <i>Trade is now 100% Risk-Free!</i>"
        )
        return self.send(
            msg,
            reply_to_message_id=reply_to_message_id,
            callback=callback,
            severity="INFO",
        )

    def notify_trailing_stop_advanced(
        self,
        ticket: int,
        new_sl: float,
        current_price: float,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """7. Trailing Stop Step Advanced Alert - Replies to original open message"""
        msg = (
            f"📈 <b>TRAILING STOP ADVANCED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"🎯 <b>Current Price:</b> <code>{current_price:.2f}</code>\n"
            f"🔒 <b>Locked Stop Loss:</b> <code>{new_sl:.2f}</code>"
        )
        return self.send(
            msg,
            reply_to_message_id=reply_to_message_id,
            callback=callback,
            severity="INFO",
        )

    def notify_market_extremes(
        self,
        symbol: str,
        high_50: float,
        low_50: float,
        range_pos_pct: float,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """8. Market Extreme Levels (Peak/Floor) Alert"""
        pos_type = "🔥 EXTREME HIGH (PEAK)" if range_pos_pct >= 0.90 else "❄️ EXTREME LOW (FLOOR)"
        msg = (
            f"⛰️ <b>MARKET STRUCTURE EXTREME DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"🔺 <b>50-Bar High:</b> <code>{high_50:.2f}</code>\n"
            f"🔻 <b>50-Bar Low:</b> <code>{low_50:.2f}</code>\n"
            f"📍 <b>Range Position:</b> <code>{range_pos_pct * 100:.1f}% ({pos_type})</code>"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_choch_detected(
        self,
        symbol: str,
        direction: str,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """9. ICT Change of Character (ChoCh) Alert"""
        emoji = "🟢" if direction == "BULLISH" else "🔴"
        msg = (
            f"{emoji} <b>ICT CHANGE OF CHARACTER (ChoCh)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"🔀 <b>Direction Shift:</b> <code>{self._escape(direction)}</code>\n"
            f"💡 <b>Market Structure:</b> <i>Potential Trend Reversal Initiated</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_liquidity_sweep(
        self,
        symbol: str,
        sweep_type: str,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """10. Liquidity Sweep & Stop Hunt Alert"""
        msg = (
            f"🧹 <b>LIQUIDITY SWEEP / STOP HUNT DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"🎯 <b>Type:</b> <code>{self._escape(sweep_type)}</code>\n"
            f"⚡ <b>Action:</b> <i>Smart Money Swept Liquidity Pools</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_fvg_detected(
        self,
        symbol: str,
        fvg_type: str,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """11. Fair Value Gap Imbalance Alert"""
        msg = (
            f"📐 <b>ICT FAIR VALUE GAP (FVG) ACTIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📌 <b>Imbalance Type:</b> <code>{self._escape(fvg_type)}</code>\n"
            f"⌛ <b>Strategy:</b> <i>Waiting for Limit Retest Entry</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_order_block(
        self,
        symbol: str,
        ob_type: str,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """12. Institutional Order Block Alert"""
        msg = (
            f"🧱 <b>INSTITUTIONAL ORDER BLOCK DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"🔹 <b>Block Type:</b> <code>{self._escape(ob_type)}</code>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_survival_mode_changed(
        self,
        active: bool,
        drawdown_pct: float,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """13. Account Survival Mode Status Alert"""
        status = (
            "🔴 ACTIVATED (HIGH CONVICTION ONLY)" if active else "🟢 DEACTIVATED (NORMAL TRADING)"
        )
        msg = (
            f"🛡️ <b>ACCOUNT SURVIVAL MODE: {status}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📉 <b>Current Peak Drawdown:</b> <code>{drawdown_pct:.2f}%</code>"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_account_health(
        self,
        account: AccountInfo,
        drawdown_pct: float,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
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
        return self.send(msg, callback=callback, severity="INFO")

    def notify_spread_spike(
        self,
        symbol: str,
        current_spread: float,
        max_allowed: float,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """15. Abnormal Spread Spike Protection Alert"""
        msg = (
            f"⚠️ <b>SPREAD SPIKE DETECTED (TRADE BLOCKED)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📊 <b>Current Spread:</b> <code>{current_spread:.1f} pts</code>\n"
            f"🛑 <b>Max Permissible:</b> <code>{max_allowed:.1f} pts</code>"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_volume_anomaly(
        self,
        symbol: str,
        volume: float,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """16. Smart Money High Volume Anomaly Alert"""
        msg = (
            f"🌊 <b>SMART MONEY VOLUME ANOMALY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📊 <b>Tick Volume Spike:</b> <code>{volume} ticks/sec</code>"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_kill_switch_activated(
        self,
        reason: str,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """17. Emergency Kill Switch Alert"""
        msg = (
            f"🚨 <b>EMERGENCY KILL SWITCH ACTIVATED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 <b>Action:</b> <i>All new execution halted immediately!</i>\n"
            f"📝 <b>Reason:</b> <code>{self._escape(reason)}</code>"
        )
        return self.send(msg, callback=callback, severity="CRITICAL")

    def notify_error(
        self,
        context: str,
        error_msg: str,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """18. System Operational Error Alert"""
        msg = (
            f"⚠️ <b>SYSTEM OPERATIONAL ERROR</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 <b>Context:</b> <code>{self._escape(context)}</code>\n"
            f"❌ <b>Error:</b> <code>{self._escape(error_msg)}</code>"
        )
        return self.send(msg, callback=callback, severity="ERROR")

    def notify_market_summary(
        self,
        symbol: str,
        features: FeatureVector,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """19. Periodic Market Structure Summary"""
        msg = (
            f"🌐 <b>MARKET TELEMETRY RADAR SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"💵 <b>Displacement:</b> <code>${features.live_tick_displacement:+.2f}</code>\n"
            f"📊 <b>ATR (M1):</b> <code>${features.atr_m1:.2f}</code>\n"
            f"☁️ <b>Ichimoku:</b> <code>TK_Cross:{features.tk_cross_signal}</code>\n"
            f"🧱 <b>ICT State:</b> <code>"
            f"FVG:{features.fvg_bullish_active}|OB:{features.order_block_type}</code>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_shutdown(
        self,
        reason: str = "User Initiated",
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """20. System Shutdown Alert"""
        msg = (
            f"🛑 <b>NEXUS SCALP ENGINE SHUTTING DOWN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 <b>Reason:</b> <code>{self._escape(reason)}</code>\n"
            f"🕒 <b>Status:</b> <i>Engine Disconnected Cleanly</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    # ==========================================================================
    # EXTENDED LIFECYCLE NOTIFICATION SCENARIOS (USER REQUESTED ADDITIONAL EVENTS)
    # ==========================================================================

    def notify_tp_touched(
        self,
        ticket: int,
        symbol: str,
        entry: float,
        tp_price: float,
        exit_price: float,
        profit_usd: float,
        profit_pct: float,
        duration_sec: float,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """🎯 Take Profit (TP) Touched Notification"""
        msg = (
            f"🎯 <b>TAKE PROFIT TOUCHED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"💵 <b>Entry Price:</b> <code>{entry:.2f}</code>\n"
            f"🎯 <b>TP Target:</b> <code>{tp_price:.2f}</code>\n"
            f"💵 <b>Exit Price:</b> <code>{exit_price:.2f}</code>\n"
            f"💰 <b>Profit/Loss:</b> <code>+${profit_usd:,.2f}</code>\n"
            f"📈 <b>Percentage Result:</b> <code>+{profit_pct:.2f}%</code>\n"
            f"⏱️ <b>Duration:</b> <code>{int(duration_sec)}s</code>\n"
            f"✨ <b>Status:</b> <i>Successfully hit TP target!</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_sl_touched(
        self,
        ticket: int,
        symbol: str,
        entry: float,
        sl_price: float,
        exit_price: float,
        loss_usd: float,
        loss_pct: float,
        duration_sec: float,
        risk_usd: float,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """🛑 Stop Loss (SL) Touched Notification"""
        msg = (
            f"🛑 <b>STOP LOSS TOUCHED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"💵 <b>Entry Price:</b> <code>{entry:.2f}</code>\n"
            f"🛑 <b>SL Level:</b> <code>{sl_price:.2f}</code>\n"
            f"💵 <b>Exit Price:</b> <code>{exit_price:.2f}</code>\n"
            f"💸 <b>Loss Amount:</b> <code>-${abs(loss_usd):,.2f}</code>\n"
            f"📉 <b>Percentage Result:</b> <code>-{abs(loss_pct):.2f}%</code>\n"
            f"⏱️ <b>Duration:</b> <code>{int(duration_sec)}s</code>\n"
            f"⚠️ <b>Allocated Risk:</b> <code>${risk_usd:.2f}</code>\n"
            f"🛡️ <b>Capital Safeguard:</b> <i>Position closed to protect equity</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="WARNING"
        )

    def notify_manual_close(
        self,
        ticket: int,
        symbol: str,
        entry: float,
        exit_price: float,
        profit_usd: float,
        duration_sec: float,
        reason: str,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """✍️ Manual Position Closure Notification"""
        emoji = "🎉" if profit_usd >= 0 else "🔻"
        msg = (
            f"{emoji} <b>MANUAL POSITION CLOSE DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"💵 <b>Entry Price:</b> <code>{entry:.2f}</code>\n"
            f"💵 <b>Exit Price:</b> <code>{exit_price:.2f}</code>\n"
            f"💰 <b>Net PnL:</b> <code>{'+$' if profit_usd >= 0 else '-$'}{abs(profit_usd):,.2f}</code>\n"
            f"⏱️ <b>Duration:</b> <code>{int(duration_sec)}s</code>\n"
            f"📝 <b>MT5 Closing Reason:</b> <code>{self._escape(reason)}</code>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_emergency_cut(
        self,
        ticket: int,
        score: int,
        reasons: str,
        saved_usd: float,
        trigger_source: str,
        drawdown_pct: float,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """⚡ Extended Emergency Cut Notification with pre-close risk metrics"""
        msg = (
            f"🚨 <b>EMERGENCY BAILOUT INITIATED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📉 <b>Hold Score:</b> <code>{score}/100</code>\n"
            f"⚠️ <b>Detailed Reason:</b> <code>{self._escape(reasons)}</code>\n"
            f"🛠️ <b>Trigger Source:</b> <code>{self._escape(trigger_source)}</code>\n"
            f"📉 <b>Pre-close Drawdown:</b> <code>{drawdown_pct:.2f}%</code>\n"
            f"🛡️ <b>Action:</b> <i>Emergency closed! (Saved ~${saved_usd:.2f})</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="WARNING"
        )

    def notify_trailing_stop_advanced_extended(
        self,
        ticket: int,
        old_sl: float,
        new_sl: float,
        current_price: float,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """📈 Enhanced Trailing Stop Advanced Event including SL Movement"""
        msg = (
            f"📈 <b>TRAILING STOP ADVANCED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"🎯 <b>Current Price:</b> <code>{current_price:.2f}</code>\n"
            f"🔒 <b>Stop Loss Step:</b> <code>{old_sl:.2f}</code> ➔ <b><code>{new_sl:.2f}</code></b>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_partial_close(
        self,
        ticket: int,
        symbol: str,
        closed_lots: float,
        remaining_lots: float,
        realized_profit_usd: float,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """🥞 Partial Position Close / Volume Reduction Alert"""
        emoji = "🎉" if realized_profit_usd >= 0 else "💸"
        msg = (
            f"🥞 <b>PARTIAL POSITION CLOSE (SCALE-OUT)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📦 <b>Scaled Volume:</b> <code>{closed_lots} lots</code>\n"
            f"Remaining Volume:</b> <code>{remaining_lots} lots</code>\n"
            f"{emoji} <b>Realized PnL:</b> <code>{'+$' if realized_profit_usd >= 0 else '-$'}{abs(realized_profit_usd):,.2f}</code>\n"
            f"🛡️ <b>Action:</b> <i>Scaled out part of the position to secure profits</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_break_even_applied_extended(
        self,
        ticket: int,
        new_sl: float,
        original_risk_usd: float,
        protected_amount_usd: float,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """🛡️ Enhanced Break-Even Applied Notification"""
        msg = (
            f"🛡️ <b>BREAK-EVEN LOCK ACTIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"🔒 <b>Protected Stop Loss:</b> <code>{new_sl:.2f}</code>\n"
            f"⚠️ <b>Original Risk:</b> <code>${original_risk_usd:.2f}</code>\n"
            f"🔒 <b>Protected PnL:</b> <code>${protected_amount_usd:.2f}</code>\n"
            f"✨ <b>Status:</b> <i>Trade is now 100% Risk-Free!</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_order_modification(
        self,
        ticket: int,
        symbol: str,
        field_modified: str,
        old_value: Any,
        new_value: Any,
        reply_to_message_id: Optional[int] = None,
        callback: Optional[Any] = None,
    ) -> Optional[int]:
        """⚙️ SL/TP / Volume / Parameter Order Modification Event"""
        msg = (
            f"⚙️ <b>POSITION CONTRACT MODIFIED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"⚙️ <b>Field Modified:</b> <code>{self._escape(field_modified)}</code>\n"
            f"📝 <b>Change:</b> <code>{self._escape(old_value)}</code> ➔ <b><code>{self._escape(new_value)}</code></b>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )
