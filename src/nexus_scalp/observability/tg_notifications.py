"""Telegram domain notifications: the notify_* message builders.

Extracted VERBATIM from telegram_notifier.py (Agent-5 modularization,
CHG-0032-A1 program). Every ``notify_*`` keeps its exact message text,
emoji layout, severity and callback behavior — these strings are operator-
visible contract (BUG-118/BUG-121 lineage; telegram DEDUP logic depends on
exact text). ``NotificationsMixin`` is a stateless carrier delegating to
``self.send``/``self.send_message``/``self._escape`` in the core notifier.

USED BY: observability/telegram_notifier.py (facade).
DO-NOT-PUT-HERE: transport/DNS fallback (tg_transport.py), worker/queue.
"""

from __future__ import annotations

import time
from typing import Any

from nexus_scalp.domain.models import AccountInfo, TradeOrder
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability._tg_core_protocol import _TelegramCoreProto
from nexus_scalp.settings import new_correlation_id


class NotificationsMixin(_TelegramCoreProto):
    """Stateless carrier for notify_* builders (verbatim)."""

    def notify_startup(
        self,
        symbol: str,
        mode: str,
        balance: float,
        equity: float,
        callback: Any | None = None,
    ) -> int | None:
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

    def notify_info(self, title: str, message: str, callback: Any | None = None) -> int | None:
        msg = (
            f"ℹ️ <b>{self._escape(title)}</b>\n"  # noqa: RUF001
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{self._escape(message)}"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def send_message(
        self,
        text: str,
        callback: Any | None = None,
        severity: str = "INFO",
    ) -> int | None:
        return self.send(text, callback=callback, severity=severity)

    def notify_generic_message(
        self,
        title: str,
        message: str,
        severity: str = "INFO",
        callback: Any | None = None,
    ) -> int | None:
        msg = f"🔔 <b>{self._escape(title)}</b>\n━━━━━━━━━━━━━━━━━━━━━\n{self._escape(message)}"
        return self.send(msg, callback=callback, severity=severity)

    def notify_test_message(self, callback: Any | None = None) -> int | None:
        msg = (
            f"✅ <b>TELEGRAM CONNECTION OK</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧪 <b>Test message delivered.</b>\n"
            f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
            f"<i>Your bot token and admin chat ID are correctly configured.</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_engine_stopped(
        self, reason: str = "manual", callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🛑 <b>NEXUS SCALP ENGINE STOPPED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>Reason:</b> <code>{self._escape(reason)}</code>\n"
            f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_engine_error(
        self, error: str, context: str = "", callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🚨 <b>ENGINE ERROR</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <b>Context:</b> <code>{self._escape(context or 'engine')}</code>\n"
            f"❌ <b>Error:</b> <code>{self._escape(error)[:1500]}</code>\n"
            f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        )
        return self.send(msg, callback=callback, severity="CRITICAL")

    def notify_audit_purge(
        self, deleted: dict[str, Any], duration_ms: float, callback: Any | None = None
    ) -> int | None:
        parts = " | ".join(f"{k}: {v}" for k, v in (deleted or {}).items()) or "nothing deleted"
        msg = (
            f"🧹 <b>AUDIT RETENTION PURGE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🗑 <b>Deleted:</b> <code>{self._escape(parts)}</code>\n"
            f"⏱ <b>Duration:</b> <code>{duration_ms:.0f} ms</code>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_warmup(
        self, state: str, symbol: str, detail: str = "", callback: Any | None = None
    ) -> int | None:
        emoji = "✅" if state.upper() == "READY" else "⏳"
        msg = (
            f"{emoji} <b>WARMUP STATE: {self._escape(state.upper())}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📋 <b>Detail:</b> {self._escape(detail or '—')}"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_daily_summary(
        self, stats: dict[str, Any], callback: Any | None = None
    ) -> int | None:
        def _fmt(k: str, dflt: Any = "—") -> str:
            v = stats.get(k, dflt)
            return "—" if v is None or v == "" else str(v)

        msg = (
            f"📊 <b>DAILY PERFORMANCE SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 <b>Date:</b> <code>{_fmt('date')}</code>\n"
            f"💼 <b>Trades:</b> <code>{_fmt('trades')}</code>\n"
            f"✅ <b>Wins:</b> <code>{_fmt('wins')}</code> | ❌ <b>Losses:</b> <code>{_fmt('losses')}</code>\n"
            f"🎯 <b>Win Rate:</b> <code>{_fmt('win_rate')}</code>\n"
            f"💰 <b>Net PnL:</b> <code>{_fmt('net_pnl')}</code>\n"
            f"📉 <b>Max Drawdown:</b> <code>{_fmt('max_drawdown')}</code>\n"
            f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_order_opened(
        self, order: TradeOrder, risk_usd: float, callback: Any | None = None
    ) -> int | None:
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
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
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
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
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
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
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
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
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

    def notify_canonical_close(
        self,
        ticket: int,
        symbol: str,
        entry: float,
        exit_price: float,
        profit_usd: float,
        duration_sec: float,
        exit_reason: str,
        evidence: str = "",
        initial_sl: float = 0.0,
        final_sl: float = 0.0,
        strategy: str = "",
        regime: str = "",
        confidence: float = 0.0,
        realized_r: float = 0.0,
        mfe_usd: float = 0.0,
        mae_usd: float = 0.0,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        """POSITION CLOSED — consumes the CANONICAL outcome (BUG-081).

        The exit reason and evidence come from the same classifier result that
        feeds AccountingCore / ExperienceLedger. This method NEVER re-infers
        manual / SL / BE / trailing from the broker reason code. The label is
        derived from the canonical taxonomy (ExitReason) and shown with its
        evidence so the message is auditable.
        """
        emoji = "🎉" if profit_usd >= 0 else "🔻"
        label = self._exit_label(exit_reason)
        lines = [
            f"{emoji} <b>POSITION CLOSED</b>\n",
            "━━━━━━━━━━━━━━━━━━━━━\n",
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n",
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n",
            f"💵 <b>Entry:</b> <code>{entry:.2f}</code>  →  <b>Exit:</b> <code>{exit_price:.2f}</code>\n",
            f"💰 <b>Net PnL:</b> <code>{'+$' if profit_usd >= 0 else '-$'}{abs(profit_usd):,.2f}</code>\n",
        ]
        if realized_r:
            lines.append(f"📐 <b>R:</b> <code>{realized_r:+.2f}R</code>\n")
        lines.append(f"🚪 <b>Exit:</b> <code>{label}</code>\n")
        if evidence:
            lines.append(f"🧾 <b>Evidence:</b> <code>{self._escape(evidence)}</code>\n")
        if initial_sl > 0.0 or final_sl > 0.0:
            lines.append(f"🛡️ <b>SL:</b> <code>{initial_sl:.2f} → {final_sl:.2f}</code>\n")
        if duration_sec > 0:
            lines.append(f"⏱️ <b>Duration:</b> <code>{int(duration_sec)}s</code>\n")
        meta = []
        if strategy:
            meta.append(f"Strategy: {self._escape(strategy)}")
        if regime:
            meta.append(f"Regime: {self._escape(regime)}")
        if confidence:
            meta.append(f"Confidence: {confidence:.2f}")
        if mfe_usd or mae_usd:
            meta.append(f"MFE {mfe_usd:+.2f} / MAE {mae_usd:+.2f}")
        if meta:
            lines.append("🧠 " + " | ".join(meta) + "\n")
        return self.send(
            "".join(lines),
            reply_to_message_id=reply_to_message_id,
            callback=callback,
            severity="INFO",
        )

    def _exit_label(self, exit_reason: str) -> str:
        """Human label for the canonical ExitReason taxonomy (never re-classifies)."""
        r = (exit_reason or "").upper()
        mapping = {
            "TAKE_PROFIT_HIT": "TAKE PROFIT",
            "HARD_SL_HIT": "ORIGINAL STOP LOSS",
            "RISK_FREE_SL_HIT": "RISK-FREE STOP",
            "BREAK_EVEN_SL_HIT": "BREAK-EVEN STOP",
            "TRAILING_STOP_HIT": "TRAILING STOP",
            "MANUAL_CLOSE": "MANUAL CLOSE",
            "SYSTEM_CLOSE": "SYSTEM CLOSE",
            "RECONCILIATION_CLOSE": "RECONCILIATION CLOSE",
            "BROKER_CLOSE": "BROKER CLOSE",
            "AI_REVERSAL_EXIT": "STRATEGY EXIT (AI REVERSAL)",
            "HOLD_SCORE_DECAY": "STRATEGY EXIT (HOLD SCORE)",
            "PROFIT_GIVEBACK_PROTECTION": "EMERGENCY EXIT (GIVEBACK)",
            "UNKNOWN": "UNKNOWN",
        }
        return mapping.get(r, self._escape(exit_reason or "UNKNOWN"))

    def notify_early_emergency_cut(
        self,
        ticket: int,
        score: int,
        reasons: str,
        saved_usd: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"⚡ <b>EARLY EMERGENCY CUT (CAPITAL SAVED)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📉 <b>Hold Score:</b> <code>{score}/100</code>\n"
            f"⚠️ <b>Invalidation Reason:</b> <code>{self._escape(reasons)}</code>\n"
            f"🛡️ <b>Action:</b> <i>Closed Early to Avoid Full SL (Saved ~${saved_usd:.2f})</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="WARNING"
        )

    def notify_break_even_applied(
        self,
        ticket: int,
        new_sl: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"🛡️ <b>BREAK-EVEN APPLIED (RISK-FREE)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"🔒 <b>New Stop Loss:</b> <code>{new_sl:.2f}</code>\n"
            f"✨ <b>Status:</b> <i>Trade is now 100% Risk-Free!</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_trailing_stop_advanced(
        self,
        ticket: int,
        new_sl: float,
        current_price: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"📈 <b>TRAILING STOP ADVANCED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"🎯 <b>Current Price:</b> <code>{current_price:.2f}</code>\n"
            f"🔒 <b>Locked Stop Loss:</b> <code>{new_sl:.2f}</code>"
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
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
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

    def notify_trailing_stop_advanced_extended(
        self,
        ticket: int,
        old_sl: float,
        new_sl: float,
        current_price: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
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
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
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

    def notify_emergency_cut(
        self,
        ticket: int,
        score: int,
        reasons: str,
        saved_usd: float,
        trigger_source: str,
        drawdown_pct: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
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
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
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
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
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

    def notify_order_modification(
        self,
        ticket: int,
        symbol: str,
        field_modified: str,
        old_value: Any,
        new_value: Any,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
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

    def notify_survival_mode_changed(
        self, active: bool, drawdown_pct: float, callback: Any | None = None
    ) -> int | None:
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
        self, account: AccountInfo, drawdown_pct: float, callback: Any | None = None
    ) -> int | None:
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
        self, symbol: str, current_spread: float, max_allowed: float, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"⚠️ <b>SPREAD SPIKE DETECTED (TRADE BLOCKED)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📊 <b>Current Spread:</b> <code>{current_spread:.1f} pts</code>\n"
            f"🛑 <b>Max Permissible:</b> <code>{max_allowed:.1f} pts</code>"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_volume_anomaly(
        self, symbol: str, volume: float, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🌊 <b>SMART MONEY VOLUME ANOMALY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📊 <b>Tick Volume Spike:</b> <code>{volume} ticks/sec</code>"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_kill_switch_activated(self, reason: str, callback: Any | None = None) -> int | None:
        msg = (
            f"🚨 <b>EMERGENCY KILL SWITCH ACTIVATED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 <b>Action:</b> <i>All new execution halted immediately!</i>\n"
            f"📝 <b>Reason:</b> <code>{self._escape(reason)}</code>"
        )
        return self.send(msg, callback=callback, severity="CRITICAL")

    def notify_error(self, context: str, error_msg: str, callback: Any | None = None) -> int | None:
        msg = (
            f"⚠️ <b>SYSTEM OPERATIONAL ERROR</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 <b>Context:</b> <code>{self._escape(context)}</code>\n"
            f"❌ <b>Error:</b> <code>{self._escape(error_msg)}</code>"
        )
        return self.send(msg, callback=callback, severity="ERROR")

    def notify_market_summary(
        self, symbol: str, features: FeatureVector, callback: Any | None = None
    ) -> int | None:
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
        self, reason: str = "User Initiated", callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🛑 <b>NEXUS SCALP ENGINE SHUTTING DOWN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 <b>Reason:</b> <code>{self._escape(reason)}</code>\n"
            f"🕒 <b>Status:</b> <i>Engine Disconnected Cleanly</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_market_extremes(
        self,
        symbol: str,
        high_50: float,
        low_50: float,
        range_pos_pct: float,
        callback: Any | None = None,
    ) -> int | None:
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
        self, symbol: str, direction: str, callback: Any | None = None
    ) -> int | None:
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
        self, symbol: str, sweep_type: str, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🧹 <b>LIQUIDITY SWEEP / STOP HUNT DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"🎯 <b>Type:</b> <code>{self._escape(sweep_type)}</code>\n"
            f"⚡ <b>Action:</b> <i>Smart Money Swept Liquidity Pools</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_fvg_detected(
        self, symbol: str, fvg_type: str, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"📐 <b>ICT FAIR VALUE GAP (FVG) ACTIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📌 <b>Imbalance Type:</b> <code>{self._escape(fvg_type)}</code>\n"
            f"⌛ <b>Strategy:</b> <i>Waiting for Limit Retest Entry</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_order_block(
        self, symbol: str, ob_type: str, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🧱 <b>INSTITUTIONAL ORDER BLOCK DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"🔹 <b>Block Type:</b> <code>{self._escape(ob_type)}</code>"
        )
        return self.send(msg, callback=callback, severity="INFO")


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def record_placeholder() -> str:
    return new_correlation_id("nid")
