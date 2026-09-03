"""Hunters v2 — real-dataset setup distribution analysis (read-only)."""

from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")
sys.path.insert(0, "src")


from nexus_scalp.model_generation import (
    ArtifactStore,
    HunterSampleMaker,
    SetupDetector,
    default_artifact_root,
)

store = ArtifactStore(default_artifact_root())
frame = store.read_dataset("ds_cb30f87520e9e6a4")
print("dataset rows:", frame.height)

# Sample a subset (10k rows) for setup distribution analysis
sub = frame.head(10000)
rows = sub.to_dicts()
detector = SetupDetector()

setup_counter: Counter = Counter()
tier_counter: Counter = Counter()
go_counter: Counter = Counter()
samples_with_setup = 0
total = 0
qualities: list[float] = []

for r in rows:
    dets = detector.detect(r, r.get("timestamp"))
    total += 1
    if dets:
        best = dets[0]
        setup_counter[best.setup_type] += 1
        qualities.append(best.quality)
        samples_with_setup += 1

# second pass with RELATIVE tiering (percentile within this sample's distribution)
hm = HunterSampleMaker()
for r in rows:
    h = hm.analyze_row(r, r.get("timestamp"), quality_reference=qualities)
    tier_counter[h["tier"]] += 1
    if h["decision"] == "GO":
        go_counter[h["setup_type"]] += 1

print(f"\nrows analyzed: {total}")
print(f"rows with setup: {samples_with_setup} ({100.0 * samples_with_setup / total:.1f}%)")
print("\nsetup distribution:")
for k, v in setup_counter.most_common():
    print(f"  {k:24s} {v:6d} ({100.0 * v / total:.2f}%)")
print("\ntier distribution:")
for k, v in tier_counter.most_common():
    print(f"  {k:10s} {v:6d} ({100.0 * v / total:.2f}%)")
print("\nGO decisions by setup:")
for k, v in go_counter.most_common():
    print(f"  {k:24s} {v:6d}")
