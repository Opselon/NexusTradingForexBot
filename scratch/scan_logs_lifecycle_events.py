"""Scan rotated live logs for engine lifecycle events (raw substring search)."""

import os

files = [
    "artifacts/logs/nse_live.log",
    "artifacts/logs/nse_live.log.1",
    "artifacts/logs/nse_live.log.2",
    "artifacts/logs/nse_live.log.3",
    "artifacts/logs/nse_live.log.4",
    "artifacts/logs/nse_live.log.5",
]
needles = [
    "connected to MT5",
    "runtime_mode=",
    "Starting",
    "preflight",
    "PAPER",
    "Using Remote",
    "Using Direct",
]
for f in files:
    if not os.path.exists(f):
        continue
    hits = []
    with open(f, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            low = line.lower()
            for nd in needles:
                if nd.lower() in low:
                    hits.append(line.rstrip()[:150])
                    break
    print(f"=== {f}: {len(hits)} hits ===")
    for h in hits[:15]:
        print("  ", h)
