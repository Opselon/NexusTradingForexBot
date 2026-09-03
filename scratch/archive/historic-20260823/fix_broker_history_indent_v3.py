"""Fix remaining indentation corruption in broker_history.py."""

from pathlib import Path

p = Path(
    r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src\nexus_scalp\adapters\database\broker_history.py"
)
text = p.read_text(encoding="utf-8")

fixes = [
    (
        """        if cur.rowcount == 0:
                    trades_dup += 1

            # The 2 fixture positions lacking an OUT deal stay OPEN; they are never
            # inserted. Reconstructed-but-open counts flow into the telemetry as-is.
            trades_persisted = len(trades) - still_open

    conn.execute(""",
        """        if cur.rowcount == 0:
            trades_dup += 1

    # The 2 fixture positions lacking an OUT deal stay OPEN; they are never
    # inserted. Reconstructed-but-open counts flow into the telemetry as-is.
    trades_persisted = len(trades) - still_open

    conn.execute(""",
    ),
]

for old, new in fixes:
    if old in text:
        text = text.replace(old, new)
        print("fixed:", new.splitlines()[0][:60])
    else:
        print("NOT FOUND:", old.splitlines()[0][:60])

p.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
print("written")
