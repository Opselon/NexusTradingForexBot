"""Diff the regenerated route table against the committed table_gen.go.

Reports routes Python serves that Go does NOT (drift = future 404s) and routes
Go registers that Python no longer serves (dead surface).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent
GROUND = Path("C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/routes_ground_truth.json")
TABLE = REPO / "go-api" / "internal" / "api" / "routes" / "table_gen.go"

HANDWRITTEN = {
    ("GET", "/api/v1/system/health"), ("GET", "/api/v1/system/status"),
    ("GET", "/api/v1/system/readiness"), ("GET", "/api/v1/system/version"),
    ("GET", "/api/v1/system/runtime"), ("GET", "/api/v1/system/capabilities"),
    ("GET", "/api/v1/system/workers"), ("GET", "/api/v1/system/diagnostics"),
    ("POST", "/api/v1/system/diagnostics/run"), ("POST", "/api/v1/system/refresh"),
    ("GET", "/api/v1/research/status"), ("GET", "/api/v1/research/strategies"),
    ("GET", "/api/v1/research/strategies/{strategy_id}"),
    ("GET", "/api/v1/research/runs"), ("GET", "/api/v1/research/datasets"),
    ("GET", "/api/v1/risk/status"), ("GET", "/api/v1/risk/summary"),
    ("GET", "/api/v1/runtime/mode"), ("GET", "/api/v1/runtime/freshness"),
    ("GET", "/api/v1/runtime/shutdown"),
}


def main() -> None:
    recs = json.loads(GROUND.read_text(encoding="utf-8"))
    py = {(r["method"], r["path"]) for r in recs
          if r["method"] not in ("HEAD", "OPTIONS", "MOUNT")}

    src = TABLE.read_text(encoding="utf-8")
    go = {tuple(m) for m in re.findall(
        r'\{Method: "([A-Z]+)", Path: "([^"]+)"', src)}

    missing = sorted(py - go - HANDWRITTEN)
    dead = sorted(go - py)
    print(f"python operations: {len(py)}")
    print(f"go table entries  : {len(go)} (+{len(HANDWRITTEN)} handwritten)")
    print(f"\n=== DRIFT: python serves, go does not: {len(missing)} ===")
    for m, p in missing:
        print(f"   {m:6s} {p}")
    print(f"\n=== DEAD: go registers, python no longer serves: {len(dead)} ===")
    for m, p in dead:
        print(f"   {m:6s} {p}")


if __name__ == "__main__":
    main()
