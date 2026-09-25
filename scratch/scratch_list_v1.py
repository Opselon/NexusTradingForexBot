"""List exact v1 paths and read the real handler sources for the system domain."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

gt = json.load(open("C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/routes_ground_truth.json", encoding="utf-8"))

print("== ALL /api/v1/system paths ==")
for r in gt:
    if r["surface"] == "v1" and r["path"].startswith("/api/v1/system"):
        print(f"{r['method']:6s} {r['path']}   async={r['async']}")

print("\n== ALL v1 domain paths (grouped) ==")
by_dom: dict[str, list[str]] = {}
for r in gt:
    if r["surface"] != "v1" or not r["path"]:
        continue
    dom = r["path"].split("/")[2] if len(r["path"].split("/")) > 2 else "?"
    seg = "/".join(r["path"].split("/")[:3])
    by_dom.setdefault(seg, []).append(f"{r['method']} {r['path']}")
for seg in sorted(by_dom):
    print(f"\n[{seg}]  ({len(by_dom[seg])} ops)")
    for p in sorted(by_dom[seg]):
        print("   ", p)

print("\n== dashboard health-adjacent ==")
for r in gt:
    if r["surface"] == "dashboard" and any(k in (r["path"] or "") for k in ("health", "status", "version", "liveness", "ready")):
        print(f"{r['method']:6s} {r['path']}")
