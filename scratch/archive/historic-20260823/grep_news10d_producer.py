"""Forensic scan: who produces the News-10D (50..59) in the 70D path?"""

PATHS = [
    "src/nexus_scalp/features/liquidity_runtime.py",
    "src/nexus_scalp/model_generation/schema_v2.py",
    "src/nexus_scalp/model_generation/news_bridge.py",
    "src/nexus_scalp/shadow/shadow70/liq_provider.py",
    "src/nexus_scalp/shadow/shadow70/runtime.py",
]
for p in PATHS:
    try:
        src = open(p, encoding="utf-8").read()
    except OSError as e:
        print("MISSING", p, e)
        continue
    print("=== ", p, f"({len(src.splitlines())} lines)")
    for i, ln in enumerate(src.splitlines(), 1):
        low = ln.lower()
        if "news" in low and ("feature" in low or "10d" in low or "50..59" in low or "slot" in low or "context" in low):
            print(f"  {i:4}| {ln.strip()[:160]}")