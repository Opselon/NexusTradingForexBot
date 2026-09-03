import ast
import py_compile

path = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nexus_scalp\execution\order_manager.py"
with open(path, encoding="utf-8", newline="") as f:
    raw = f.read()

E = "\r\n"

old_block = (
    "                        else:\r\n"
    "                            reason_str = (\r\n"
    '                                "Manual Close via Terminal"\r\n'
    '                                if (matched_deal and matched_deal.get("reason", 0) == 1)\r\n'
    "                                else f\"MT5 Reason Code {matched_deal.get('reason', 0) if matched_deal else 'Unknown'}\"\r\n"
    "                            )\r\n"
    '                            if matched_deal and matched_deal.get("comment", ""):\r\n'
    "                                reason_str += f\" ({matched_deal.get('comment', '')})\"\r\n"
    "                            self.notifier.notify_manual_close(\r\n"
    "                                ticket=dead_ticket,\r\n"
    "                                symbol=symbol,\r\n"
    "                                entry=entry,\r\n"
    "                                exit_price=exit_price,\r\n"
    "                                profit_usd=total_net_profit,\r\n"
    "                                duration_sec=duration_sec,\r\n"
    "                                reason=reason_str,\r\n"
    "                                reply_to_message_id=msg_id,\r\n"
    "                            )\r\n"
)
assert raw.count(old_block) == 1, f"old block count: {raw.count(old_block)}"

new_block = (
    "                        else:\r\n"
    "                            # BUG-081: Telegram consumes the CANONICAL outcome.\r\n"
    "                            # The exit label/evidence come from the same classifier\r\n"
    "                            # result written to the ledger (AccountingCore /\r\n"
    "                            # ExperienceLedger) — never re-inferred from the broker\r\n"
    "                            # reason code, and never defaulted to MANUAL.\r\n"
    '                            evidence_src = "BROKER_DEAL_REASON"\r\n'
    "                            if was_sl_modified:\r\n"
    '                                evidence_src = "ENGINE_SL_MODIFICATION"\r\n'
    '                            elif matched_deal and matched_deal.get("comment", ""):\r\n'
    '                                evidence_src = "BROKER_DEAL_COMMENT"\r\n'
    "                            self.notifier.notify_canonical_close(\r\n"
    "                                ticket=dead_ticket,\r\n"
    "                                symbol=symbol,\r\n"
    "                                entry=entry,\r\n"
    "                                exit_price=exit_price,\r\n"
    "                                profit_usd=total_net_profit,\r\n"
    "                                duration_sec=duration_sec,\r\n"
    "                                exit_reason=exit_mechanism,\r\n"
    "                                evidence=evidence_src,\r\n"
    "                                initial_sl=initial_sl_val,\r\n"
    "                                final_sl=final_sl_val,\r\n"
    '                                strategy=self._entry_reasons.get(dead_ticket, ""),\r\n'
    '                                regime=self._entry_regimes.get(dead_ticket, ""),\r\n'
    "                                confidence=self._entry_confidences.get(dead_ticket, 0.0),\r\n"
    "                                realized_r=orig_risk / max(abs(entry - initial_sl_val), 1e-9)\r\n"
    "                                if (orig_risk and initial_sl_val > 0.0 and entry > 0.0)\r\n"
    "                                else 0.0,\r\n"
    "                                mfe_usd=mfe_usd,\r\n"
    "                                mae_usd=mae_usd,\r\n"
    "                                reply_to_message_id=msg_id,\r\n"
    "                            )\r\n"
)
raw = raw.replace(old_block, new_block)

with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(raw)

py_compile.compile(path, doraise=True)
print("else-branch wired to notify_canonical_close; py_compile OK")

tree = ast.parse(raw)
count = 0
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "notify_canonical_close":
            count += 1
print("notify_canonical_close call sites:", count)
print("notify_manual_close still called:", raw.count("notify_manual_close"))
