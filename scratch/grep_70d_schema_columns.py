"""Find how the 70D dataset producer builds the full 70D vector: schemas, columns, news handling."""

PATHS = [
    "src/nexus_scalp/model_generation/schema_v2.py",
    "src/nexus_scalp/features/liquidity_runtime.py",
]
for p in PATHS:
    src = open(p, encoding="utf-8").read()
    print("========", p)
    for i, ln in enumerate(src.splitlines(), 1):
        low = ln.lower()
        if any(k in low for k in ["feat_60", "feat_50", "liquidity_extra", "extra_start", "70", "scalp_v4", "news_context_at", "augment", "columns"]):
            print(f"  {i:4}| {ln.strip()[:170]}")