#!/usr/bin/env python
"""Fix indentation in broker_history.py sync_broker_history."""

import py_compile
from pathlib import Path

p = Path(
    r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nexus_scalp\adapters\database\broker_history.py"
)
src = p.read_text(encoding="utf-8")
old = (
    "    orders_dup = 0\n"
    "        deals_dup = 0\n"
    "        trades_dup = 0\n"
    "        still_open = 0  # positions with no OUT deal inside the fetched window\n"
    "        for o in orders_sorted:\n"
    "        row = normalize_order_row(o)"
)
new = (
    "    orders_dup = 0\n"
    "    deals_dup = 0\n"
    "    trades_dup = 0\n"
    "    still_open = 0  # positions with no OUT deal inside the fetched window\n"
    "    for o in orders_sorted:\n"
    "        row = normalize_order_row(o)"
)
assert old in src, "pattern not found"
p.write_text(src.replace(old, new), encoding="utf-8")
py_compile.compile(str(p), doraise=True)
print("broker_history.py OK")
