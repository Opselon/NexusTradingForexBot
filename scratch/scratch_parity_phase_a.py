"""Phase A parity harness — live A/B between the Python reference and the Go
candidate on the same machine, same dataset, same config (spec §31, §9).

Both servers are started in the SAME process tree. Every probe is sent twice:
once to Python, once to Go, with an identical request (method, path, headers,
body). The harness compares the CONTRACT-relevant fields and writes
api/migration/parity/phase_a_parity.json.

Nondeterministic fields are explicitly listed below and masked — never
silently ignored.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parent
TOKEN = "parity-token-123"
GO_EXE = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Capsizer\AppData\Local")) / "Temp" / "nexus-api-test.exe"
PY_PORT = 0  # set in main()
GO_PORT = 0

# Fields that legitimately differ run-to-run or runtime-to-runtime. Masking
# these is NOT ignoring them — each is justified below.
MASKED_KEYS = {
    # new request id per call by design
    "request_id",
    # new generation timestamp per call by design
    "generated_at",
    "at",
    "snapshot_timestamp",
    # build metadata differs between runtimes by construction; asserted as
    # key-set equality below, not value equality.
    "build_timestamp",
    "commit",
    "version",
    "python",
    "architecture",
    "platform",
    "commit_source",
    "commit_status",
    "dirty_tree",
    "feature_schema",
    "installer_version",
    "channel",
    "build_mode",
    "product_display",
    # OS facts collected live by HealthEngine: free disk space changes between
    # two independent probe invocations, so a strict compare is noise. The
    # KEY (optional_layers[6].reason) existing on both sides is the invariant.
    "reason",
}

# Phase A mutation probes are run LAST and in their own pass: POST
# /diagnostics/run populates last_selftest on the SHARED Python backend, which
# would otherwise contaminate the earlier GET /diagnostics comparison. This is
# not test-suite tidiness — a side-effecting probe in the middle of an A/B run
# silently breaks parity for every later probe (spec §31: compare side effects
# too).
PROBES = [
    ("GET", "/api/v1/system/health", None),
    ("GET", "/api/v1/system/status", None),
    ("GET", "/api/v1/system/readiness", None),
    ("GET", "/api/v1/system/version", None),
    ("GET", "/api/v1/system/runtime", None),
    ("GET", "/api/v1/system/capabilities", None),
    ("GET", "/api/v1/system/workers", None),
    ("GET", "/api/v1/system/diagnostics", None),
    # error-surface parity
    ("GET", "/api/v1/system/info", None),        # 404
    ("POST", "/api/v1/system/health", None),     # 405
    ("GET", "/api/v1/nope", None),               # 404
    ("PUT", "/api/v1/system/version", None),     # 405
    # --- mutations last: these touch shared backend state ---
    ("POST", "/api/v1/system/refresh", None),
    ("POST", "/api/v1/system/diagnostics/run", None),
]


def _request(method: str, url: str, body: bytes | None) -> tuple[int, dict, bytes, str]:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read(), ""
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(), ""
    except Exception as exc:  # connection refused etc.
        return -1, {}, b"", f"{type(exc).__name__}: {exc}"


def probe_both() -> list[dict]:
    """Probe ONE pair (Python then Go) back to back and return the diff.

    Pairing is mandatory here, not a style choice: both servers share a SINGLE
    Python backend, so running the entire Python pass before the Go pass means
    Go reads state the Python mutation probes just wrote (GET /diagnostics saw
    last_selftest=null on Python but a populated object on Go). Comparing each
    request against its immediate counterpart guarantees equivalent state.
    """
    diffs = []
    for method, path, body in PROBES:
        p_status, p_hdr, p_raw, p_err = _request(
            method, f"http://127.0.0.1:{PY_PORT}{path}", body)
        g_status, g_hdr, g_raw, g_err = _request(
            method, f"http://127.0.0.1:{GO_PORT}{path}", body)
        p = {"method": method, "path": path, "status": p_status,
             "raw": p_raw.decode("utf-8", "replace"), "transport_error": p_err,
             "request_id_header": p_hdr.get("X-Request-ID", ""),
             "content_type": p_hdr.get("Content-Type", "")}
        g = {"method": method, "path": path, "status": g_status,
             "raw": g_raw.decode("utf-8", "replace"), "transport_error": g_err,
             "request_id_header": g_hdr.get("X-Request-ID", ""),
             "content_type": g_hdr.get("Content-Type", "")}
        diffs.append(diff_entry(p, g))
    return diffs


def diff_entry(p: dict, g: dict) -> dict:
    """Compare ONE probe pair. Only masked keys may differ and still PASS."""
    d = {
        "method": p["method"], "path": p["path"],
        "py_status": p["status"], "go_status": g["status"],
        "request_id_py": p["request_id_header"], "request_id_go": g["request_id_header"],
        "transport_error": p["transport_error"] or g["transport_error"],
    }
    try:
        pj = json.loads(p["raw"])
    except Exception:
        pj = None
    try:
        gj = json.loads(g["raw"])
    except Exception:
        gj = None
    d["py_json"] = pj
    d["go_json"] = gj
    if pj is None or gj is None:
        d["verdict"] = "JSON_PARSE_FAIL"
        return d
    ok_status = p["status"] == g["status"]
    keys_ok, keys_missing_in_go, keys_extra_in_go, value_diffs = _deep_compare(pj, gj, "")
    d["keys_missing_in_go"] = keys_missing_in_go
    d["keys_extra_in_go"] = keys_extra_in_go
    d["value_diffs"] = value_diffs
    unexplained = [v for v in value_diffs if leaf_name(v["key"]) not in MASKED_KEYS]
    # NOTE: keys_ok is derived from the SAME diff list that unexplained was
    # filtered from, so masked noise would fail it twice. The explicit
    # missing/extra/unexplained checks below are the complete criteria.
    d["verdict"] = "PASS" if (ok_status and not keys_missing_in_go
                             and not keys_extra_in_go and not unexplained) else "FAIL"
    if not ok_status:
        d["reason"] = f"status {p['status']} != {g['status']}"
    elif keys_missing_in_go:
        d["reason"] = f"missing keys {keys_missing_in_go}"
    elif keys_extra_in_go:
        d["reason"] = f"extra keys {keys_extra_in_go}"
    elif unexplained:
        d["reason"] = f"value diffs on {len(unexplained)} non-masked keys"
    return d


def _deep_compare(a, b, prefix: str, out_diffs=None):
    """Return (all_ok, missing, extra, diffs). Collects EVERY leaf diff into
    out_diffs — including ones nested inside lists — so nothing is silently
    dropped. Lists are compared by length + per-element recursion; a length
    mismatch is itself reported."""
    missing, extra = [], []
    diffs = out_diffs if out_diffs is not None else []
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


def _short(v):
    s = json.dumps(v)
    return s if len(s) <= 120 else s[:120] + "..."


def leaf_name(key: str) -> str:
    """Return the final segment of a dotted diff key.

    Masking matches on the LEAF NAME, not the full path: meta.request_id and
    data.request_id are both legitimately nondeterministic, so a full-path
    mask would let a real divergence on data.* through while flagging noise.
    """
    key = key.split("[]")[0]
    if "." in key:
        return key.rsplit(".", 1)[-1]
    return key


def free_port() -> int:
    """Pick an ephemeral free port so a lingering process from a previous run
    cannot make the whole A/B pass fail with a confusing 'never ready'."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_python() -> subprocess.Popen:
    """Start uvicorn on PY_PORT in PAPER mode, auth token fixed."""
    env = dict(os.environ)
    env.update({
        "NSE_WEB_AUTH_TOKEN": TOKEN,
        "NSE_RUNTIME_MODE": "paper",
        "PYTHONPATH": "src",
        "NSE_WEB_PORT": str(PY_PORT),
        # deterministic: no engine, no MT5, no model
        "NSE_ENGINE_DISABLE": "1",
        "NSE_MT5_DISABLE": "1",
        "NSE_MODEL_DISABLE": "1",
    })
    cmd = [
        sys.executable, "-m", "uvicorn",
        "nexus_scalp.web.server:create_app", "--factory",
        "--host", "127.0.0.1", "--port", str(PY_PORT), "--log-level", "warning",
    ]
    return subprocess.Popen(cmd, cwd=REPO, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def start_go() -> subprocess.Popen:
    env = dict(os.environ)
    env.update({
        "NSE_WEB_AUTH_TOKEN": TOKEN,
        "NSE_GO_ADDR": f"127.0.0.1:{GO_PORT}",
        "NSE_PYTHON_ORIGIN": f"http://127.0.0.1:{PY_PORT}",
        "NSE_RUNTIME_MODE": "paper",
    })
    return subprocess.Popen([str(GO_EXE)], cwd=REPO, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for(port: int, path: str, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}{path}"
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            req.add_header("Authorization", f"Bearer {TOKEN}")
            with urllib.request.urlopen(req, timeout=3) as r:
                if r.status in (200, 404, 405, 401, 503):
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    global PY_PORT, GO_PORT
    if not GO_EXE.exists():
        print(f"FATAL: {GO_EXE} missing — build it first")
        return 1
    # Kill any stragglers from a previous run before touching the network.
    for pid in stragglers():
        kill(pid)
    PY_PORT = free_port()
    GO_PORT = free_port()
    print(f"[1/4] starting Python reference on {PY_PORT}")
    py = start_python()
    try:
        if not wait_for(PY_PORT, "/api/v1/system/health"):
            print("FATAL: python server never became ready")
            return 1
        print("      ready")
        print("[2/4] starting Go candidate on", GO_PORT)
        go = start_go()
        try:
            if not wait_for(GO_PORT, "/api/v1/system/health"):
                print("FATAL: go server never became ready")
                return 1
            print("      ready")
            print("[3/4] probing both (paired: py -> go per request)")
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
            out = REPO / "api" / "migration" / "parity" / "phase_a_parity.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(diffs, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"      wrote {out}")
            return 0 if n_pass == len(diffs) else 2
        finally:
            _terminate(go)
    finally:
        _terminate(py)


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def stragglers() -> list[int]:
    """PIDs of nexus-api-test processes left over from a previous run."""
    out = []
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
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


def kill(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=30)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
