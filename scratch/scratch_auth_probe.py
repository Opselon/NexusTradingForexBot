"""Probe the AUTH REJECTION contract with auth ENABLED (no token supplied).

Covers both surfaces so the Go port matches byte-for-byte:
  - legacy  /api/status        -> legacy safe_error_payload shape?
  - v1      /api/v1/system/health -> v1 error envelope? which code?
  - public  /health            -> 200 (allowlisted, no token)
  - traversal /../etc/passwd   -> 404/401, never public
Plus: what a VALID token returns, and header casing.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# AUTH ENABLED: do NOT set NSE_WEB_AUTH_DISABLE
os.environ.pop("NSE_WEB_AUTH_DISABLE", None)
os.environ["NSE_WEB_AUTH_TOKEN"] = "probe-token-123"
os.environ.setdefault("NSE_RUNTIME_MODE", "paper")
sys.path.insert(0, "src")

from fastapi.testclient import TestClient  # noqa: E402

results: list[dict] = []


def probe(client, method: str, path: str, surface: str, tok: str | None) -> None:
    h = {}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    try:
        r = getattr(client, method.lower())(path, headers=h)
        try:
            body = r.json()
        except Exception:
            body = r.text[:600]
        results.append({
            "surface": surface, "path": path, "method": method,
            "token": bool(tok),
            "status": r.status_code,
            "headers": dict(r.headers),
            "body": body,
        })
        print(f"{surface:10s} {path:34s} tok={str(bool(tok)):5s} -> {r.status_code}")
    except Exception as e:
        results.append({"surface": surface, "path": path, "method": method,
                         "token": bool(tok), "error": f"{type(e).__name__}: {e}"})
        print(f"{surface:10s} {path:34s} tok={str(bool(tok)):5s} -> ERROR {type(e).__name__}")


# ---- v1 surface ----
from nexus_scalp.web.api_v1_wiring import create_v1_app  # noqa: E402

v1 = TestClient(create_v1_app())
probe(v1, "GET", "/api/v1/system/health", "v1", None)
probe(v1, "GET", "/api/v1/system/health", "v1", "probe-token-123")
probe(v1, "GET", "/api/v1/system/health", "v1", "WRONG-token")
probe(v1, "GET", "/health", "v1", None)
probe(v1, "GET", "/nope", "v1", None)

# ---- dashboard surface ----
from nexus_scalp.web.server import create_app  # noqa: E402

d = TestClient(create_app(engine_ref=None))
probe(d, "GET", "/api/status", "dashboard", None)
probe(d, "GET", "/api/status", "dashboard", "probe-token-123")
probe(d, "GET", "/api/status", "dashboard", "WRONG-token")
probe(d, "GET", "/health", "dashboard", None)
probe(d, "GET", "/healthz", "dashboard", None)
probe(d, "GET", "/", "dashboard", None)
probe(d, "GET", "/app.js", "dashboard", None)
probe(d, "GET", "/alt", "dashboard", None)
probe(d, "GET", "/../etc/passwd", "dashboard", None)
probe(d, "GET", "/%2e%2e/etc/passwd", "dashboard", None)
probe(d, "GET", "/api/v1/system/health", "dashboard", None)
probe(d, "POST", "/api/positions/close", "dashboard", None)

out = Path("api/migration/contracts/auth_contracts.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
print(f"\ncaptured {len(results)} auth probes -> {out}")

# ---- summarise the shapes ----
print("\n== body shape by surface ==")
seen = set()
for r in results:
    if "error" in (r.get("body") or {}) if isinstance(r.get("body"), dict) else False:
        key = (r["surface"], tuple(sorted(r["body"].keys())))
        if key not in seen:
            seen.add(key)
            print(f"{r['surface']:10s} status={r['status']} keys={list(r['body'].keys())} "
                  f"error={r['body'].get('error')}")
    elif isinstance(r.get("body"), dict):
        key = (r["surface"], tuple(sorted(r["body"].keys())), r["status"])
        if key not in seen:
            seen.add(key)
            print(f"{r['surface']:10s} status={r['status']} keys={list(r['body'].keys())}")
