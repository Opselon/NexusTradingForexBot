"""Fix still_open init in broker_history.py via deterministic replace."""

from pathlib import Path

p = Path(
    r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nexus_scalp\adapters\database\broker_history.py"
)
text = p.read_text(encoding="utf-8")

old = """    orders_dup = 0
        deals_dup = 0
        trades_dup = 0
        still_open = 0  # positions with no OUT deal inside the fetched window
"""
new = """    orders_dup = 0
    deals_dup = 0
    trades_dup = 0
    still_open = 0  # positions with no OUT deal inside the fetched window
"""
if old in text:
    text = text.replace(old, new)
    print("fixed init")
else:
    print("pattern not found; region:")
    idx = text.find("orders_dup = 0")
    print(text[idx : idx + 200])

p.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
print("written")
