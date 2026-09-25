"""Empirically map WHICH paths enforce auth when the middleware IS installed.

Answers: is enforcement global, or route-by-route? Run with auth ENABLED.
"""
from __future__ import annotations

import json
import os
import sys

os.environ.pop("NSE_WEB_AUTH_DISABLE", None)
os.environ["NSE_WEB_AUTH_TOKEN"] = "probe-token-123"
os.environ.setdefault("NSE_RUNTIME_MODE", "paper")
sys.path.insert(0, "src")

from fastapi.testclient import TestClient  # noqa: E402
from nexus_scalp.web.server import create_app  # noqa: E402

app = create_app(engine_ref=None)
print("auth installed on app state:", getattr(app.state, "_web_auth_installed", "ABSENT"))
# what middleware classes are in the stack?
stack = getattr(app, "user_middleware", None) or []
print("user_middleware:", [(getattr(m, "cls", m), getattr(m, "kwargs", None)) for m in stack])

c = TestClient(app)

paths = [
    "/api/status",
    "/api/v1/system/health",
    "/api/v1/system/status",
    "/api/v1/system/version",
    "/api/v1/system/readiness",
    "/api/v1/system/runtime",
    "/api/v1/system/capabilities",
    "/api/v1/system/workers",
    "/api/v1/system/diagnostics",
    "/api/mt5/status",
    "/api/engine/toggle",
    "/api/positions/close",
    "/api/operator/summary",
    "/api/models/integrity",
    "/api/health",
    "/health",
    "/",
    "/app.js",
    "/api/ticks/stream",
    "/api/trace",
    "/api/debug/health",
    "/api/dependency/health",
    "/api/diagnostics/health",
    "/openapi.json",
    "/docs",
]

rows = []
for p in paths:
    try:
        r = c.get(p)
        st = r.status_code
        body = r.text[:120]
    except Exception as e:
        st, body = f"ERR {type(e).__name__}", str(e)[:120]
    kind = "BLOCKED-401" if st == 401 else ("OPEN" if st == 200 else str(st))
    rows.append({"path": p, "status": st, "kind": kind})
    print(f"{kind:12s} {st!s:5s} {p}")

with open("api/migration/contracts/auth_enforcement_map.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, indent=1)

blocked = sum(1 for r in rows if r["kind"] == "BLOCKED-401")
open_ = sum(1 for r in rows if r["kind"] == "OPEN")
print(f"\nBLOCKED-401: {blocked}   OPEN(200): {open_}   other: {len(rows)-blocked-open_}")
