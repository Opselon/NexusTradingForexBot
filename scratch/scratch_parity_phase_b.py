"""Phase B parity harness — v1:research, v1:risk, v1:runtime(read).

Same paired design as Phase A (py -> go per request, same machine/config) so
a shared backend state change cannot contaminate the comparison.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
TOKEN = "parity-token-777"
GO_EXE = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Capsizer\AppData\Local")) / "Temp" / "nexus-api-test.exe"
PY = "C:/Users/Capsizer/source/repos/NexusTradingForexBot/.venv/Scripts/python.exe"
PY_PORT = 0
GO_PORT = 0

MASKED_KEYS = {
    "request_id", "generated_at", "at", "snapshot_timestamp",
    "probed_at",
    "build_timestamp", "commit", "version", "python", "architecture",
    "platform", "commit_source", "commit_status", "dirty_tree",
    "feature_schema", "installer_version", "channel", "build_mode",
    "product_display",
    # HealthEngine live OS readings drift between two probe calls.
    "reason",
}

PROBES = [
    ("GET", "/api/v1/research/status", None),
    ("GET", "/api/v1/research/strategies", None),
    ("GET", "/api/v1/research/strategies?page=1&page_size=2", None),
    ("GET", "/api/v1/research/strategies?lifecycle=stable", None),
    ("GET", "/api/v1/research/strategies/does-not-exist", None),
    ("GET", "/api/v1/research/runs", None),
    ("GET", "/api/v1/research/runs?page=2&page_size=5", None),
    ("GET", "/api/v1/research/datasets", None),
    # pagination validation (Go validates locally -> must match FastAPI's 422)
    ("GET", "/api/v1/research/runs?page=0", None),
    ("GET", "/api/v1/research/runs?page_size=0", None),
    ("GET", "/api/v1/research/runs?page_size=201", None),
    ("GET", "/api/v1/research/runs?lifecycle=x", None),
    # risk (503 with no engine — envelope must match)
    ("GET", "/api/v1/risk/status", None),
    ("GET", "/api/v1/risk/summary", None),
    # runtime read part
    ("GET", "/api/v1/runtime/mode", None),
    ("GET", "/api/v1/runtime/freshness", None),
    ("GET", "/api/v1/runtime/shutdown", None),
    # error surface
    ("GET", "/api/v1/risk/nope", None),
    ("POST", "/api/v1/risk/status", None),
    ("POST", "/api/v1/research/runs", None),
    ("GET", "/api/v1/research/runs?page=abc", None),
]


def _request(method: str, url: str, body):
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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _short(v):
    s = json.dumps(v)
    return s if len(s) <= 160 else s[:160] + "..."


def leaf_name(key: str) -> str:
    key = key.split("[]")[0]
    return key.rsplit(".", 1)[-1] if "." in key else key


def _deep_compare(a, b, prefix: str, out_diffs):
    missing, extra = [], []
    diffs = out_diffs
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            ka = f"{prefix}.{k}" if prefix else k
            if k not in b:
                missing.append(ka)
                continue
            _deep_compare(a[k], b[k], ka, diffs)
        for k in b:
            kb = f"{prefix}.{k}" if prefix else k
            if k not in a:
                extra.append(kb)
        for k in a:
            ka = f"{prefix}.{k}" if prefix else k
            if k in b and not isinstance(a[k], (dict, list)) and not isinstance(b[k], (dict, list)):
                if a[k] != b[k]:
                    diffs.append({"key": ka, "py": _short(a[k]), "go": _short(b[k])})
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append({"key": prefix + "[]len", "py": len(a), "go": len(b)})
        for i in range(min(len(a), len(b))):
            _deep_compare(a[i], b[i], f"{prefix}[{i}]", diffs)
    elif a != b:
        diffs.append({"key": prefix, "py": _short(a), "go": _short(b)})
    return not (missing or extra or diffs), missing, extra, diffs


def diff_entry(p, g):
    d = {"method": p["method"], "path": p["path"],
         "py_status": p["status"], "go_status": g["status"],
         "request_id_py": p["request_id"], "request_id_go": g["request_id"]}
    try:
        pj = json.loads(p["raw"])
    except Exception:
        pj = None
    try:
        gj = json.loads(g["raw"])
    except Exception:
        gj = None
    d["py_json"], d["go_json"] = pj, gj
    if pj is None or gj is None:
        d["verdict"] = "JSON_PARSE_FAIL"
        d["reason"] = "unparseable body"
        return d
    ok_status = p["status"] == g["status"]
    _, missing, extra, diffs = _deep_compare(pj, gj, "", [])
    d["keys_missing_in_go"] = missing
    d["keys_extra_in_go"] = extra
    d["value_diffs"] = diffs
    unexplained = [v for v in diffs if leaf_name(v["key"]) not in MASKED_KEYS]
    d["verdict"] = "PASS" if (ok_status and not missing
                             and not extra and not unexplained) else "FAIL"
    if not ok_status:
        d["reason"] = f"status {p['status']} != {g['status']}"
    elif missing:
        d["reason"] = f"missing keys {missing[:6]}"
    elif extra:
        d["reason"] = f"extra keys {extra[:6]}"
    elif unexplained:
        d["reason"] = f"value diffs on {len(unexplained)} non-masked keys"
    return d


def probe_both():
    out = []
    for method, path, body in PROBES:
        ps, ph, praw = _request(method, f"http://127.0.0.1:{PY_PORT}{path}", body)
        gs, gh, graw = _request(method, f"http://127.0.0.1:{GO_PORT}{path}", body)
        out.append(diff_entry(
            {"method": method, "path": path, "status": ps, "raw": praw.decode("utf-8", "replace"),
             "request_id": ph.get("X-Request-ID", "")},
            {"method": method, "path": path, "status": gs, "raw": graw.decode("utf-8", "replace"),
             "request_id": gh.get("X-Request-ID", "")}))
    return out


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


def start_python():
    env = dict(os.environ)
    env.update({"NSE_WEB_AUTH_TOKEN": TOKEN, "NSE_RUNTIME_MODE": "paper",
                "PYTHONPATH": "src", "NSE_WEB_PORT": str(PY_PORT),
                "NSE_ENGINE_DISABLE": "1", "NSE_MT5_DISABLE": "1", "NSE_MODEL_DISABLE": "1"})
    cmd = [PY, "-m", "uvicorn", "nexus_scalp.web.server:create_app", "--factory",
           "--host", "127.0.0.1", "--port", str(PY_PORT), "--log-level", "warning"]
    return subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def start_go():
    env = dict(os.environ)
    env.update({"NSE_WEB_AUTH_TOKEN": TOKEN, "NSE_GO_ADDR": f"127.0.0.1:{GO_PORT}",
                "NSE_PYTHON_ORIGIN": f"http://127.0.0.1:{PY_PORT}",
                "NSE_RUNTIME_MODE": "paper"})
    return subprocess.Popen([str(GO_EXE)], cwd=REPO, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _terminate(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def stragglers():
    out = []
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-Process nexus-api-test -ErrorAction SilentlyContinue | "
                            "ForEach-Object { $_.Id }"],
                           capture_output=True, text=True, timeout=30)
        for line in (r.stdout or "").split():
            try:
                out.append(int(line))
            except ValueError:
                pass
    except Exception:
        pass
    return out


def main() -> int:
    global PY_PORT, GO_PORT
    if not GO_EXE.exists():
        print(f"FATAL: {GO_EXE} missing")
        return 1
    for pid in stragglers():
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=30)
    PY_PORT, GO_PORT = free_port(), free_port()
    print(f"[1/4] python reference on {PY_PORT}")
    py = start_python()
    try:
        if not wait_for(PY_PORT):
            print("FATAL: python never ready")
            return 1
        print("      ready")
        go = start_go()
        try:
            if not wait_for(GO_PORT):
                print("FATAL: go never ready")
                return 1
            print(f"[2/4] go candidate on {GO_PORT} — ready")
            print("[3/4] paired probing")
            diffs = probe_both()
            n_pass = sum(1 for d in diffs if d["verdict"] == "PASS")
            print(f"[4/4] {n_pass}/{len(diffs)} probes at parity")
            for d in diffs:
                mark = "OK " if d["verdict"] == "PASS" else "XX "
                line = f"  {mark}{d['method']:4s} {d['path']}"
                if d["verdict"] != "PASS":
                    line += f"  {d.get('reason','')}"
                print(line)
                for v in d.get("value_diffs", []):
                    if leaf_name(v["key"]) not in MASKED_KEYS:
                        print(f"        key {v['key']}: py={v['py']!r} go={v['go']!r}")
            out = REPO / "api" / "migration" / "parity" / "phase_b_parity.json"
            out.write_text(json.dumps(diffs, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"      wrote {out}")
            return 0 if n_pass == len(diffs) else 2
        finally:
            _terminate(go)
    finally:
        _terminate(py)


if __name__ == "__main__":
    sys.exit(main())
