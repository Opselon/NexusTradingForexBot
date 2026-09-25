"""Capture Phase B contracts: v1:research + v1:risk + v1:runtime(READ part).

Records live Python responses so the Go implementation is written against
MEASURED contracts, never against my reading of the source. Also captures
the FastAPI-generated 422 for a bad page/page_size, which is the
parse_pagination contract.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
TOKEN = "contract-token-456"
PORT = int(os.environ.get("NSE_CONTRACT_PORT", "8101"))
PY = "C:/Users/Capsizer/source/repos/NexusTradingForexBot/.venv/Scripts/python.exe"

PROBES = [
    # research
    ("GET", "/api/v1/research/status", None),
    ("GET", "/api/v1/research/strategies", None),
    ("GET", "/api/v1/research/strategies?page=1&page_size=2", None),
    ("GET", "/api/v1/research/strategies?lifecycle=stable", None),
    ("GET", "/api/v1/research/strategies/does-not-exist", None),
    ("GET", "/api/v1/research/runs", None),
    ("GET", "/api/v1/research/runs?page=1&page_size=3", None),
    ("GET", "/api/v1/research/datasets", None),
    # pagination validation contract
    ("GET", "/api/v1/research/runs?page=0", None),
    ("GET", "/api/v1/research/runs?page_size=0", None),
    ("GET", "/api/v1/research/runs?page_size=201", None),
    ("GET", "/api/v1/research/runs?lifecycle=x", None),
    # risk
    ("GET", "/api/v1/risk/status", None),
    ("GET", "/api/v1/risk/summary", None),
    # runtime read part
    ("GET", "/api/v1/runtime/mode", None),
    ("GET", "/api/v1/runtime/freshness", None),
    ("GET", "/api/v1/runtime/shutdown", None),
    # error surface
    ("GET", "/api/v1/risk/nope", None),
    ("POST", "/api/v1/risk/status", None),
]


def _request(method: str, url: str, body: bytes | None):
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as exc:
        return -1, {}, str(exc).encode()


def wait_for(port: int, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/system/health")
            req.add_header("Authorization", f"Bearer {TOKEN}")
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status in (200, 401, 403, 404, 405, 503):
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    env = dict(os.environ)
    env.update({
        "NSE_WEB_AUTH_TOKEN": TOKEN,
        "NSE_RUNTIME_MODE": "paper",
        "PYTHONPATH": "src",
        "NSE_WEB_PORT": str(PORT),
        "NSE_ENGINE_DISABLE": "1",
        "NSE_MT5_DISABLE": "1",
        "NSE_MODEL_DISABLE": "1",
    })
    cmd = [PY, "-m", "uvicorn", "nexus_scalp.web.server:create_app",
           "--factory", "--host", "127.0.0.1", "--port", str(PORT),
           "--log-level", "warning"]
    proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        if not wait_for(PORT):
            print("FATAL: server never ready")
            return 1
        out = []
        for method, path, body in PROBES:
            status, headers, raw = _request(method, f"http://127.0.0.1:{PORT}{path}", body)
            try:
                parsed = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                parsed = None
            out.append({
                "method": method, "path": path, "status": status,
                "request_id": headers.get("X-Request-ID", ""),
                "content_type": headers.get("Content-Type", ""),
                "body": parsed,
                "raw_head": raw.decode("utf-8", "replace")[:800],
            })
            print(f"  {status:4d} {method:4s} {path}")
        dest = REPO / "api" / "migration" / "contracts" / "phase_b_contracts.json"
        dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {dest} ({len(out)} probes)")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
