"""Wire entry_setup_snapshot through log_ledger_closed (CRLF-safe, exact offsets)."""

import sys

path = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nexus_scalp\adapters\database\audit_repository.py"
with open(path, encoding="utf-8", newline="") as f:
    src = f.read()

nl = "\n" if "\r\n" not in src else "\r\n"

# 1. signature param
old_sig = "        drawdown_percent_after: float = 0.0,\n    ) -> None:"
new_sig = (
    "        drawdown_percent_after: float = 0.0,\n"
    '        entry_setup_snapshot: str = "{}",\n'
    "    ) -> None:"
)
i = src.find(old_sig)
if i == -1:
    print("SIG NOT FOUND")
    sys.exit(1)
src = src[:i] + new_sig + src[i + len(old_sig) :]

# 2. INSERT column list
old_ins = "             account_balance_after, account_equity_after, drawdown_percent_after)\n            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,\n                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
new_ins = (
    "             account_balance_after, account_equity_after, drawdown_percent_after,\n"
    "             entry_setup_snapshot)\n"
    "            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,\n"
    "                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
i = src.find(old_ins)
if i == -1:
    print("INSERT NOT FOUND")
    sys.exit(1)
src = src[:i] + new_ins + src[i + len(old_ins) :]

# 3. UPDATE SET
old_upd = '                drawdown_percent_after=excluded.drawdown_percent_after\n        """'
new_upd = (
    "                drawdown_percent_after=excluded.drawdown_percent_after,\n"
    "                entry_setup_snapshot=CASE WHEN excluded.entry_setup_snapshot != '{}' THEN excluded.entry_setup_snapshot ELSE audit_ledger.entry_setup_snapshot END\n"
    '        """'
)
i = src.find(old_upd)
if i == -1:
    print("UPD NOT FOUND")
    sys.exit(1)
src = src[:i] + new_upd + src[i + len(old_upd) :]

# 4. args tuple (exact)
old_args = "            float(drawdown_percent_after),\n        )"
new_args = (
    "            float(drawdown_percent_after),\n            entry_setup_snapshot,\n        )"
)
i = src.find(old_args)
if i == -1:
    print("ARGS NOT FOUND")
    sys.exit(1)
src = src[:i] + new_args + src[i + len(old_args) :]

with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src)
print("WIRE_OK")
