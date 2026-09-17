"""CI heartbeat — AI endpoint health + configured model probe (stdlib + repo only).

Used as the *heartbeat* advisory step in CI lanes. Intentionally stdlib + repo
only: if this module itself fails to import, the lane-report step still fires
with a degraded verdict.
"""

from __future__ import annotations

import json
import os
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_HOST_ENV = "AI_HOST"
_KEY_ENV = "AI_KEY"
_MODEL_ENV = "AI_MODEL"
_TIMEOUT_S = 2.0


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def _tcp(host: str, port: int, timeout: float) -> dict[str, object]:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = time.monotonic() - start
            return {"ok": True, "tcp_ms": round(elapsed * 1000, 1)}
    except OSError as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}", "tcp_ms": None}


def _http(url: str, timeout: float) -> dict[str, object]:
    start = time.monotonic()
    try:
        req = Request(url, headers={"User-Agent": "nse-ci-heartbeat/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            elapsed = time.monotonic() - start
            body = resp.read(512)
            return {
                "ok": True,
                "status": resp.status,
                "http_ms": round(elapsed * 1000, 1),
                "body_preview": body.decode("utf-8", errors="replace")[:200],
            }
    except HTTPError as exc:
        return {
            "ok": True,
            "status": exc.code,
            "http_ms": round((time.monotonic() - start) * 1000, 1),
        }
    except URLError as exc:
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc.reason}"}


def heartbeat(
    host: str | None = None,
    port: int | None = None,
    model_env: str = _MODEL_ENV,
) -> dict[str, object]:
    """Return structured heartbeat payload. Never raises — failures are advisory."""
    configured_host = host or _env(_HOST_ENV)
    configured_model = _env(model_env)
    payload: dict[str, object] = {
        "category": "HEARTBEAT",
        "ai_host": configured_host or None,
        "configured_model": configured_model or None,
        "endpoint_reachable": False,
    }
    if not configured_host:
        payload["verdict"] = "NO_ENDPOINT_CONFIGURED"
        return payload

    host_part = configured_host.split("://", 1)[-1].rstrip("/")
    port = port or (443 if host_part.startswith("https") else 80)
    tcp = _tcp(host_part, port, _TIMEOUT_S)
    payload["tcp"] = tcp

    if tcp.get("ok"):
        try:
            url = f"{'https' if port == 443 else 'http'}://{host_part}/v1/models"
            http = _http(url, _TIMEOUT_S)
            payload["http"] = http
            payload["endpoint_reachable"] = http.get("ok", False)
            payload["verdict"] = (
                "REACHABLE" if payload["endpoint_reachable"] else "ENDPOINT_UNREACHABLE"
            )
        except Exception as exc:
            payload["verdict"] = f"HTTP_ERROR: {exc}"
    else:
        payload["verdict"] = "TCP_FAILED"
    return payload


if __name__ == "__main__":
    print(json.dumps(heartbeat(), indent=2, sort_keys=True))
