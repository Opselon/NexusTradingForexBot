"""Capture real response contracts for Phase A endpoints.

Starts BOTH surfaces in-process with TestClient:
  - create_v1_app()  (/api/v1/*)
  - create_app()     (dashboard: /api/health, /api/status, static shells)

Dumps for each probe: status, ALL response headers, and the body parsed.
Writes api/migration/contracts/phase_a_contracts.json.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["NSE_WEB_AUTH_DISABLE"] = "1"
os.environ.setdefault("NSE_RUNTIME_MODE", "paper")
sys.path.insert(0, "src")

WT = Path.cwd()
OUT = WT / "api" / "migration" / "contracts" / "phase_a_contracts.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

probes: list[dict] = []


def probe(client, method: str, path: str, surface: str, note: str = "") -> None:
    try:
        r = getattr(client, method.lower())(path)
        try:
            body = r.json()
        except Exception:
            body = r.text[:2000]
        headers = {k: v for k, v in r.headers.items()
                   if k.lower() in ("content-type", "x-request-id", "x-correlation-id",
                                    "content-length", "cache-control", "server",
                                    "date", "set-cookie")}
        probes.append({
            "surface": surface, "method": method, "path": path,
            "note": note,
            "status": r.status_code,
            "headers": headers,
            "body": body,
        })
        print(f"  {surface:10s} {method:5s} {path:46s} -> {r.status_code}")
    except Exception as e:
        probes.append({"surface": surface, "method": method, "path": path,
                        "note": note, "error": f"{type(e).__name__}: {e}"})
        print(f"  {surface:10s} {method:5s} {path:46s} -> ERROR {type(e).__name__}")


# ---------------- v1 surface ----------------
from fastapi.testclient import TestClient  # noqa: E402

from nexus_scalp.web.api_v1_wiring import create_v1_app  # noqa: E402

print("== v1 surface ==")
v1 = TestClient(create_v1_app())
for path in ("/api/v1/system/health", "/api/v1/system/status", "/api/v1/system/version",
             "/api/v1/system/info", "/api/v1/system/runtime",
             "/api/v1/system", "/api/v1/does-not-exist"):
    probe(v1, "GET", path, "v1")
probe(v1, "POST", "/api/v1/system/health", "v1", "method-not-allowed")
probe(v1, "GET", "/api/v1/system/health", "v1", "headers-only")
# v1 validation error shape (422)
probe(v1, "GET", "/api/v1/market/candles?limit=abc", "v1", "validation-error")
# engine-unavailable style (engine not attached)
probe(v1, "GET", "/api/v1/runtime/engine", "v1", "no-engine")

# ---------------- dashboard surface ----------------
print("== dashboard surface ==")
from nexus_scalp.web.server import create_app  # noqa: E402

app = create_app(engine_ref=None)
d = TestClient(app)
for path in ("/api/health", "/api/status", "/", "/app.js", "/api_client.js"):
    probe(d, "GET", path, "dashboard")
# auth disabled probe: confirm 200 (auth is on by default in prod)
probe(d, "GET", "/api/mt5/status", "dashboard", "no-engine")

OUT.write_text(json.dumps(probes, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
print(f"\ncaptured {len(probes)} probes -> {OUT}")

# --- sanity: report whether X-Request-ID was present ---
with_id = [p for p in probes if p.get("headers", {}).get("X-Request-ID") or p.get("headers", {}).get("x-request-id")]
print(f"probes with X-Request-ID response header: {len(with_id)}/{len(probes)}")
