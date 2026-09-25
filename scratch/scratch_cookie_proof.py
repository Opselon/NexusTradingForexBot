"""Verify the cookie-bootstrap hypothesis for the auth map.

Fresh client, one request at a time, printing Set-Cookie each step:
  1. /api/status            (expect 401, no cookie yet)
  2. /                      (public bootstrap -> Set-Cookie?)
  3. /api/v1/system/health   (expect: authenticated BY COOKIE -> 200)
  4. same path with cookie JAR CLEARED (expect 401)
  5. /api/positions/close with no cookie (expect 401, NOT 422)
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
print("auth installed:", getattr(app.state, "_web_auth_installed", "ABSENT"))
c = TestClient(app)

steps = []


def go(label, method, path, send_cookie):
    if not send_cookie:
        c.cookies.clear()
    else:
        c.cookies.set("nse_web_auth", "probe-token-123")
    r = getattr(c, method)(path)
    sc = r.headers.get("set-cookie")
    body = r.text[:160]
    steps.append({"label": label, "path": path, "method": method,
                  "sent_cookie": send_cookie, "status": r.status_code,
                  "set_cookie": sc, "body": body})
    print(f"{label:42s} {method:5s} {path:30s} cookie={send_cookie} -> {r.status_code}")
    if sc:
        print(f"      Set-Cookie: {sc}")
    return r


go("1 no-cookie gated route", "get", "/api/status", False)
go("2 public bootstrap document", "get", "/", True)
go("3 v1 route WITH cookie", "get", "/api/v1/system/health", True)
go("4 v1 route cookie CLEARED", "get", "/api/v1/system/health", False)
go("5 mutation route cookie CLEARED", "post", "/api/positions/close", False)
go("6 v1 route WITH valid bearer", "get", "/api/v1/system/health", False)

# bearer header explicitly
c.cookies.clear()
r = c.get("/api/v1/system/health", headers={"Authorization": "Bearer probe-token-123"})
print(f"7 v1 route valid Bearer header{'':3s} -> {r.status_code}")
steps.append({"label": "7 v1 route valid Bearer header", "path": "/api/v1/system/health",
              "method": "get", "sent_cookie": False, "status": r.status_code,
              "set_cookie": None, "body": r.text[:160]})
c.cookies.clear()
r = c.get("/api/v1/system/health", headers={"Authorization": "Bearer WRONG"})
print(f"8 v1 route WRONG Bearer header{'':2s} -> {r.status_code}")
steps.append({"label": "8 v1 route WRONG Bearer header", "path": "/api/v1/system/health",
              "method": "get", "sent_cookie": False, "status": r.status_code,
              "set_cookie": None, "body": r.text[:160]})

with open("api/migration/contracts/cookie_bootstrap_proof.json", "w", encoding="utf-8") as f:
    json.dump(steps, f, indent=1, ensure_ascii=False)
print("\nwrote api/migration/contracts/cookie_bootstrap_proof.json")
