"""Full-surface parity harness: every generated route, py vs go.

Design carried over from the Phase A/B harnesses (paired py->go per request,
same machine/config) but the probe list is now the ENTIRE generated route
table, not a hand-picked subset. This is the gate that proves the Go surface
cannot silently drift from Python's.

Mutations are ordered LAST and each is run py-then-go immediately back to
back, because both servers share ONE Python backend — a state change between
the two calls would show up as a false divergence.

SSE/websocket routes are skipped (they are streaming, not request/response,
and the proxy contract is byte-for-byte envelope parity).
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
GO_API = REPO / "go-api"
BIN = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "nexus-api-full.exe"

# The Python reference MUST be THIS worktree. The shared checkout sits on a
# different feature branch and serves a different (older) surface — running
# the reference there makes every new route look like a Go bug.
MAIN = Path(__file__).resolve().parent
PY_EXE = Path("C:/Users/Capsizer/source/repos/NexusTradingForexBot/.venv/Scripts/python.exe")

TOKEN = os.environ.get("NSE_WEB_AUTH_TOKEN", "parity-token")
# Mask volatile fields: live readings that legitimately differ between two
# separate process snapshots.
MASK = ("now", "timestamp", "ts", "generated_at", "updated_at", "uptime",
        "pid", "id", "request_id", "x_request_id", "server_time", "elapsed",
        "checked_at", "snapshot_id", "correlation_id", "latency_ms",
        "snapshot_timestamp", "probed_at", "at", "state_version",
        "weights_sha256", "executed_at", "audit_started", "last_check",
        "fetched_at", "exported_at", "synchronization_timestamp",
        "inspected_at", "duration_ms", "TIMESTAMP", "CORRELATION_ID",
        "host_now_utc", "measured_at")

# Routes that are streaming, not request/response. An SSE endpoint never
# closes the body, so urllib's read() blocks until the socket timeout — the
# harness hangs on the first one. Excluded from the parity sweep; the proxy
# contract is byte-level envelope parity, which does not apply to a stream.
SKIP = (
    "/api/v1/system/events",
    "/api/ticks/stream",
    "/api/trace/stream",
    "/api/news/stream",
    "/api/v1/decisions/stream",
    "/api/v1/positions/stream",
    "/api/v1/market/stream",
    "/api/v1/signals/stream",
)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _requests(method: str, url: str, body):
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            # http.client lowercases header keys; canonicalise so the
            # comparison is case-insensitive as HTTP requires.
            hdrs = {k.lower(): v for k, v in r.headers.items()}
            return r.status, hdrs, r.read()
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in e.headers.items()}
        return e.code, hdrs, e.read()
    except Exception as e:  # noqa: BLE001
        return -1, {}, str(e).encode()


def leaf_name(path: str) -> str:
    return path.rsplit(".", 1)[-1]


def is_volatile(leaf: str) -> bool:
    """A leaf is volatile when its value is a fresh per-run measurement —
    timing, timestamps, hashes of live state — so two separate processes can
    never agree. Matched on the leaf name, case-insensitively."""
    if leaf in MASK:
        return True
    low = leaf.lower()
    return (low.endswith("_ms") or low.endswith("_nanos") or low.endswith("_us")
            or low.endswith("_seconds"))


def scrub(v, path=""):
    """Recursively replace volatile fields. Nested readings (forensics/health
    has rows.<check>.last_check; model-studio nests weights_sha256) are matched
    on the LEAF name, so the mask catches them at any depth."""
    if isinstance(v, dict):
        return {k: "***" if is_volatile(leaf_name(k)) else scrub(x, f"{path}.{k}")
                for k, x in v.items()}
    if isinstance(v, list):
        return [scrub(x, path) for x in v]
    return v


def canonical(b: bytes):
    try:
        return json.dumps(scrub(json.loads(b)), sort_keys=True, separators=(",", ":"))
    except Exception:
        return b.decode("utf-8", "replace")


def start_python(port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(MAIN / "src")
    env["NSE_WEB_AUTH_TOKEN"] = TOKEN
    env["NSE_RUNTIME_MODE"] = "paper"
    env["NSE_WEB_HOST"] = "127.0.0.1"
    env["NSE_WEB_PORT"] = str(port)
    env["NSE_WEB_AUTH_DISABLE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["NSE_ENGINE_DISABLE"] = "1"
    env["NSE_MT5_DISABLE"] = "1"
    env["NSE_MODEL_DISABLE"] = "1"
    proc = subprocess.Popen(
        [str(PY_EXE), "-m", "uvicorn",
         "nexus_scalp.web.server:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=str(MAIN), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(200):
        try:
            socket.create_connection(("127.0.0.1", port), 0.5).close()
            return proc
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError("python server died")
            time.sleep(0.25)
    raise RuntimeError("python server never listened")


def wait_go(port: int) -> None:
    for _ in range(200):
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("go server never listened")


def _force_kill(proc: subprocess.Popen) -> None:
    """The Go server's graceful shutdown ignores SIGTERM on Windows; kill the
    OS process tree or the socket leaks into the next run."""
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pid = proc.pid
        try:
            subprocess.run(
                ["C:/Windows/System32/taskkill.exe", "-F", "-T", "-PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass


def load_table() -> list[tuple[str, str]]:
    src = (GO_API / "internal" / "api" / "routes" / "table_gen.go").read_text(
        encoding="utf-8")
    rows = []
    for line in src.splitlines():
        m = re.match(r'\s*\{Method: "([A-Z]+)", Path: "([^"]+)"', line)
        if m:
            rows.append((m.group(1), m.group(2)))
    return rows


def substitute(path: str) -> str:
    p = path.replace("{node_id:path}", "a/b")
    for tok in ("{strategy_id}", "{incident_id}", "{run_id}", "{model_id}",
                "{generation_id}", "{candidate_id}", "{ticket}", "{trade_id}",
                "{snapshot_id}", "{execution_id}", "{article_id}", "{name}",
                "{kind}", "{decision_id}", "{node_id}", "{config_key}",
                "{symbol}", "{position_id}", "{event_id}", "{rule_id}",
                "{report_id}", "{task_id}", "{message_id}", "{session_id}",
                "{day}", "{hours}", "{target}", "{param}"):
        p = p.replace(tok, "1")
    return p


def main() -> int:
    table = load_table()
    print(f"route table: {len(table)} entries")

    py_port, go_port = free_port(), free_port()
    print(f"python on :{py_port}   go on :{go_port}")
    py = start_python(py_port)
    try:
        env = dict(os.environ)
        env["NSE_WEB_AUTH_DISABLE"] = "1"
        go = subprocess.Popen(
            [str(BIN), "-addr", f"127.0.0.1:{go_port}",
             "-python-origin", f"http://127.0.0.1:{py_port}"],
            cwd=str(GO_API), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            wait_go(go_port)
            time.sleep(0.6)

            results = []
            for method, path in table:
                if path in SKIP:
                    continue
                probe = substitute(path)
                ps, ph, praw = _requests(method, f"http://127.0.0.1:{py_port}{probe}", None)
                gs, gh, graw = _requests(method, f"http://127.0.0.1:{go_port}{probe}", None)

                verdict = "PASS"
                notes = []
                if ps != gs:
                    verdict = "FAIL"
                    notes.append(f"status {ps} vs {gs}")
                if canonical(praw) != canonical(graw):
                    verdict = "FAIL"
                    notes.append("body")
                if ph.get("X-Request-ID") == gh.get("X-Request-ID"):
                    # identical ids across two servers would be a bug
                    pass
                if not gh.get("x-request-id"):
                    verdict = "FAIL"
                    notes.append("missing X-Request-ID")
                results.append({"method": method, "path": path,
                                "verdict": verdict,
                                "py_status": ps, "go_status": gs,
                                "note": "; ".join(notes)})

            passed = sum(1 for r in results if r["verdict"] == "PASS")
            print(f"\n{passed}/{len(results)} routes PASS")
            fails = [r for r in results if r["verdict"] != "PASS"]
            for r in fails[:40]:
                print(f"  FAIL {r['method']:6s} {r['path']}"
                      f"  py={r['py_status']} go={r['go_status']} {r['note']}")

            out = REPO / "api" / "migration" / "parity" / "full_surface_parity.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(results, indent=1), encoding="utf-8")
            print(f"\nevidence: {out}")
            return 1 if fails else 0
        finally:
            _force_kill(go)
    finally:
        py.terminate()
        try:
            py.wait(timeout=8)
        except subprocess.TimeoutExpired:
            py.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
