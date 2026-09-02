"""Fix broken indentation in broker_history.py (CRLF-safe deterministic edit)."""

from pathlib import Path

p = Path(
    r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nexus_scalp\adapters\database\broker_history.py"
)
text = p.read_text(encoding="utf-8")

# Find the broken region and rebuild it with correct indentation.
broken = """    trades = reconstruct_trades(orders=orders, deals=deals, symbol=symbol)
        now_iso = datetime.now(UTC).isoformat()
        for t in trades:"""
fixed = """    trades = reconstruct_trades(orders=orders, deals=deals, symbol=symbol)
    now_iso = datetime.now(UTC).isoformat()
    for t in trades:"""

if broken in text:
    text = text.replace(broken, fixed)
    print("fixed indentation")
else:
    print("broken pattern NOT found; current region:")
    idx = text.find("trades = reconstruct_trades")
    print(text[idx : idx + 220])

p.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
print("written")
