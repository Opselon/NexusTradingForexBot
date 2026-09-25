"""Regenerate the ground-truth route dump from the CURRENT origin/main.

Instantiates both live FastAPI apps (dashboard + v1) and dumps every resolved
route. Routes are registered imperatively via register_*(app, ...) factories,
so this is the only authoritative source — AST/regex passes over source miss
mount-time registration and prefix resolution.

Writes routes_ground_truth.json in the scratch dir.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# The ground truth MUST come from THIS worktree, not the shared checkout.
# The shared checkout sits on a different feature branch and lacks files this
# branch has (trace_routes.py), which silently truncates the route dump.
HERE = Path(__file__).resolve().parent
MAIN = HERE
OUT = Path("C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/routes_ground_truth.json")

os.environ.setdefault("NSE_RUNTIME_MODE", "paper")
os.environ.setdefault("NSE_ENGINE_DISABLE", "1")
os.environ.setdefault("NSE_MT5_DISABLE", "1")
os.environ.setdefault("NSE_MODEL_DISABLE", "1")
os.environ.setdefault("NSE_WEB_AUTH_DISABLE", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")

sys.path.insert(0, str(MAIN / "src"))


def dump(app, surface: str):
    # FastAPI 0.141+ wraps include_router results in a _IncludedRouter proxy
    # whose own .routes are the real leaf routes; iterating app.routes
    # directly sees the wrappers and nothing else (the v1 surface appears
    # empty). api_v1_wiring._iter_effective_routes flattens them — use it.
    from nexus_scalp.web.api_v1_wiring import _iter_effective_routes

    out = []
    for r in _iter_effective_routes(getattr(app, "router", app).routes):
        if r.__class__.__name__ == "Mount":
            out.append({"surface": surface, "method": "MOUNT", "path": r.path,
                        "handler": getattr(r, "name", "mount"), "async": False})
            continue
        for m in sorted(getattr(r, "methods", []) or []):
            out.append({
                "surface": surface,
                "method": m,
                "path": r.path,
                "handler": getattr(r, "name", getattr(r.endpoint, "__name__", "?")),
                "async": True,
            })
    return out


def main() -> int:
    from nexus_scalp.web.server import create_app
    from nexus_scalp.web.api_v1_wiring import create_v1_app

    rows = dump(create_app(), "dashboard") + dump(create_v1_app(), "v1")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=1), encoding="utf-8")

    from collections import Counter
    print(f"total route records: {len(rows)}")
    print("by surface:", dict(Counter(r["surface"] for r in rows)))
    print("by method :", dict(Counter(r["method"] for r in rows)))
    paths = {r["path"] for r in rows}
    print(f"unique paths: {len(paths)}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
