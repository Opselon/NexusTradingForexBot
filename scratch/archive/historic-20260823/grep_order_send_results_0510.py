"""Find the engine's actual order_send results around 05:10-05:12 in the logs."""

import os
import re

files = [
    "artifacts/logs/nse_live.log",
    "artifacts/logs/nse_live.log.1",
    "artifacts/logs/nse_live.log.2",
    "artifacts/logs/nse_live.log.3",
    "artifacts/logs/nse_live.log.4",
    "artifacts/logs/nse_live.log.5",
]
pat = re.compile(r"^(2026-08-17 [\d:]+).*$")
for f in files:
    if not os.path.exists(f):
        continue
    hits = []
    with open(f, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = pat.match(line)
            if not m:
                continue
            ts = m.group(1)
            # keep lines 05:09-05:13 with order-related content
            if "05:09" <= ts[11:16] <= "05:13":
                low = line.lower()
                if any(
                    k in low
                    for k in [
                        "order",
                        "execut",
                        "dispatch",
                        "ticket",
                        "broker",
                        "send",
                        "position",
                        "pending",
                        "fill",
                        "retcode",
                    ]
                ):
                    hits.append(line.rstrip()[:220])
    if hits:
        print(f"=== {f}: {len(hits)} order-related lines 05:09-05:13 ===")
        for h in hits[:40]:
            print("  ", h)
