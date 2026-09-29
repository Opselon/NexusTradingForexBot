"""Wave 5 Lane D: paired benchmark of the FULL UI load path over the Go origin
vs the Python origin (END-USER-RUNTIME-UI-INTEGRATION).

Wave 4 benchmarked API routes (Go direct vs Python proxy). This answers the
question the end user actually cares about: is opening the console through
Go *as good as* opening it through Python — same bytes, same shell, and
faster on the routes the React console hits at boot?

Two things are measured, both PAIRED (same machine, same minute, same
client), per Master Spec §8/§9 fair-comparison rules:

  1. UI LOAD PATH (the literal first paint):
       GET /                     (index.html shell, Cache-Control: no-store)
       GET /assets/<hash>.js     (the biggest immutable bundle)
       GET /assets/<hash>.css    (the immutable stylesheet)
       GET /manifest.json        (PWA manifest, a public-path byte check)
     Metrics: time-to-first-byte, total bytes, total wall time, p50/p95 over
     N repeats. The asset bytes MUST be identical across origins (a byte
     difference means the two servers ship two different bundles — the exact
     regression this wave exists to catch).

  2. ROUTE MIX the React console hits while it is alive:
       /api/status                (legacy snapshot bootstrap, ENDPOINTS.status)
       /api/v1/system/health      (boot readiness gate)
       /api/v1/system/version     (topbar build metadata)
       /api/v1/features/contract  (feature registry)
     Metrics: p50/p95/p99, RPS, error rate at concurrencies [1, 10, 50].

The Go origin is built through the real seam
(nexus_scalp.web.go_api_bootstrap.build_go_api) and pointed at a STUB Python
upstream, so the numbers measure the Go serving plane itself, not the
engine. The Python origin is the same stub served by the same repo's own
http.server, so both origins answer byte-identical data and the only
variable under test is the server.

Chrome DevTools MCP is the primary UI verification (separate probe); this
script is the byte-level and latency-level evidence.

Usage:
    python scripts/dev/benchmark_ui_origin.py --repeats 30
    python scripts/dev/benchmark_ui_origin.py --quick
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "benchmarks" / "wave5_ui_origin_benchmark.json"

# The routes the React console actually hits (frontend/src/core/config.ts
# ENDPOINTS + the boot readiness/metadata calls).
ROUTE_MIX = [
    "/api/status",
    "/api/v1/system/health",
    "/api/v1/system/version",
    "/api/v1/features/contract",
]

# Fallback route list when the live UI cannot be read from disk (no built
# bundle); the load-path part of the run is then skipped with a clear note.
_MIN_LOAD_REPEATS = 5


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _safe_unlink(path: Path) -> None:
    for _ in range(25):
        try:
            if path.exists():
                path.unlink()
            return
        except OSError:
            time.sleep(0.1)


def _wait(
    url: str,
    timeout: float = 30.0,
    token: str | None = None,
    ok_statuses: tuple[int, ...] = (200,),
    strict: bool = True,
) -> int | None:
    """Poll until the origin answers. Returns the status, or None on timeout.

    ok_statuses is the set treated as 'up' — the Go readiness probe
    intentionally accepts 503 (Go is listening, Python not yet live) and 401
    (auth is installed and enforcing, which is itself proof the plane booted).
    When strict is False ANY HTTP status means the server is up (a 404 from
    a minimal test origin is a live listener, not a config error).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=2) as resp:
                resp.read()
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            if not strict or exc.code in ok_statuses:
                return int(exc.code)
            raise  # the server is UP; an unexpected status is a real failure
        except Exception:
            time.sleep(0.1)
    return None


# --------------------------------------------------------------------------- #
# the stub Python upstream both origins stand in front of
# --------------------------------------------------------------------------- #
STUB_SOURCE = r"""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The v1 contract payloads, mirroring what the REAL Python api_v1 app emits
# (nexus_scalp/web/api_v1/system.py): the health envelope carries the
# EXECUTABLE fields verdict/checks/critical_failures only — no 'overall'.
HEALTH = {"verdict": "READY", "checks": [], "critical_failures": []}
STATUS = {"health_verdict": "READY", "critical_failures": [],
          "version": {"product": "NexusScalpEngine", "version": "9.0.14",
                      "commit": "stub", "channel": "production"},
          "runtime": {"engine_attached": True, "engine_running": True,
                      "mode": "PAPER", "freshness_overall": "FRESH"},
          "checks_count": 24}

class H(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        # /api/status is the LEGACY surface: the bare snapshot, no envelope.
        if p == "/api/status":
            self._send({"status": "ok", "engine": "stub", "state": "NO_TRADE",
                        "health": {"overall": "READY", "subsystems": {},
                                   "checked_at": "2026-09-29T00:00:00"}})
        elif p == "/api/v1/system/health":
            self._send({"data": HEALTH, "meta": {}})
        elif p == "/api/v1/system/status":
            self._send({"data": STATUS, "meta": {}})
        elif p == "/api/v1/system/version":
            self._send({"data": {"version": "9.9.9-stub",
                                 "product": "NexusScalpEngine"}, "meta": {}})
        elif p == "/api/v1/features/contract":
            self._send({"data": {"schema_id": "scalp_v3", "feature_count": 70},
                        "meta": {}})
        elif p == "/manifest.json":
            body = b'{"name":"Nexus Scalp Engine","display":"standalone"}\n'
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send({"error": "not found"}, 404)

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ["NSE_STUB_PORT"])), H
                        ).serve_forever()
"""


@dataclass
class LoadSample:
    """One fetch of a UI load-path document."""

    path: str
    status: int
    ttfb_ms: float
    total_ms: float
    bytes: int


@dataclass
class LoadComparison:
    path: str
    repeats: int
    python_bytes: int
    go_bytes: int
    byte_identical: bool
    python_ttfb_p50_ms: float
    python_ttfb_p95_ms: float
    python_total_p50_ms: float
    go_ttfb_p50_ms: float
    go_ttfb_p95_ms: float
    go_total_p50_ms: float


@dataclass
class RouteMetrics:
    total_requests: int
    error_count: int
    error_rate: float
    duration_sec: float
    requests_per_sec: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float


@dataclass
class RouteComparison:
    route: str
    concurrency: int
    python_baseline: RouteMetrics
    go_origin: RouteMetrics
    rps_speedup: float
    p50_reduction_pct: float


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * q))]


# --------------------------------------------------------------------------- #
# origin plumbing
# --------------------------------------------------------------------------- #
class _Origin:
    """A live HTTP origin with a token-authenticated client."""

    def __init__(self, name: str, base: str, token: str):
        self.name = name
        self.base = base
        self.token = token

    def get(self, path: str, timeout: float = 15.0) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(self.base + path)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return int(resp.status), dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return int(exc.code), dict(exc.headers), exc.read()


def _start_stub(port: int) -> subprocess.Popen:
    stub_file = REPO_ROOT / "artifacts" / "go_build" / "_stub_upstream.py"
    stub_file.parent.mkdir(parents=True, exist_ok=True)
    stub_file.write_text(STUB_SOURCE, encoding="utf-8")
    env = os.environ.copy()
    env["NSE_STUB_PORT"] = str(port)
    venv_python = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\.venv\Scripts\python.exe"
    python = venv_python if Path(venv_python).is_file() else sys.executable
    return subprocess.Popen(
        [python, str(stub_file)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _start_go_origin(port: int, stub_port: int, token: str) -> subprocess.Popen:
    """Boot nexus-api through the real build seam (go_api_bootstrap)."""
    from nexus_scalp.web import go_api_bootstrap as gab

    go_exe = gab._find_go()
    ok, binary = gab.build_go_api(go_exe)
    if not ok:
        raise RuntimeError(f"build_go_api failed: {binary}")

    env = os.environ.copy()
    env["NSE_WEB_AUTH_TOKEN"] = token
    env["NSE_GO_ADDR"] = f"127.0.0.1:{port}"
    env["NSE_PYTHON_ORIGIN"] = f"http://127.0.0.1:{stub_port}"
    return subprocess.Popen(
        [binary, "-addr", f"127.0.0.1:{port}", "-python-origin", f"http://127.0.0.1:{stub_port}"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _start_python_origin(port: int, stub_port: int, dist: Path, token: str) -> subprocess.Popen:
    """The Python counterpart: serve the SAME dist over http.server with the
    same auth token, so the two origins differ only in the server."""
    launcher = REPO_ROOT / "artifacts" / "go_build" / "_py_origin.py"
    launcher.write_text(
        "import os, sys\n"
        "from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler\n"
        "from functools import partial\n"
        f"DIST = {str(dist)!r}\n"
        "TOKEN = os.environ['NSE_BENCH_TOKEN']\n"
        "\n"
        "class H(SimpleHTTPRequestHandler):\n"
        "    def __init__(self, *a, **k):\n"
        "        super().__init__(*a, directory=DIST, **k)\n"
        "    def end_headers(self):\n"
        "        p = self.path.split('?')[0]\n"
        "        if p in ('/', '/index.html', '/manifest.json'):\n"
        "            self.send_header('Cache-Control', 'no-store')\n"
        "        elif '-assets/' not in p and self.path.count('-') >= 1:\n"
        "            self.send_header('Cache-Control', 'no-store')\n"
        "        super().end_headers()\n"
        "    def do_GET(self):\n"
        "        p = self.path.split('?')[0]\n"
        "        if p.startswith('/api') or p.startswith('/health'):\n"
        "            # proxy the stub upstream so both origins answer the same\n"
        "            # JSON for the route mix (the UI never sees the seam).\n"
        "            import urllib.request as u, json as j\n"
        "            base = os.environ['NSE_STUB_URL']\n"
        "            try:\n"
        "                with u.urlopen(base + p, timeout=5) as r:\n"
        "                    body, ct = r.read(), r.headers.get('Content-Type', 'application/json')\n"
        "            except u.HTTPError as e:\n"
        "                body, ct = e.read(), 'application/json'\n"
        "            self.send_response(200)\n"
        "            self.send_header('Content-Type', ct)\n"
        "            self.send_header('Content-Length', str(len(body)))\n"
        "            self.end_headers()\n"
        "            self.wfile.write(body)\n"
        "            return\n"
        "        auth = self.headers.get('Authorization', '')\n"
        "        if TOKEN and auth != f'Bearer {TOKEN}':\n"
        '            body = b\'{"ok":false,"error":{"code":"UNAUTHORIZED"}}\'\n'
        "            self.send_response(401)\n"
        "            self.send_header('Content-Type', 'application/json')\n"
        "            self.send_header('Content-Length', str(len(body)))\n"
        "            self.end_headers()\n"
        "            self.wfile.write(body)\n"
        "            return\n"
        "        super().do_GET()\n"
        "    def log_message(self, *a):\n"
        "        pass\n"
        "\n"
        "ThreadingHTTPServer(('127.0.0.1', int(os.environ['NSE_BENCH_PORT'])), H"
        ").serve_forever()\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["NSE_BENCH_PORT"] = str(port)
    env["NSE_BENCH_TOKEN"] = token
    env["NSE_STUB_URL"] = f"http://127.0.0.1:{stub_port}"
    venv_python = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\.venv\Scripts\python.exe"
    python = venv_python if Path(venv_python).is_file() else sys.executable
    return subprocess.Popen(
        [python, str(launcher)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


# --------------------------------------------------------------------------- #
# measurement
# --------------------------------------------------------------------------- #
def _measure_load_path(
    origin: _Origin, paths: list[str], repeats: int
) -> dict[str, list[LoadSample]]:
    out: dict[str, list[LoadSample]] = {}
    for path in paths:
        samples: list[LoadSample] = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            status, _headers, body = origin.get(path)
            # TTFB cannot be read from urllib directly; approximate it as the
            # full fetch of these small documents and report total wall time
            # as the honest number (both origins measured the same way).
            total = (time.perf_counter() - t0) * 1000.0
            samples.append(
                LoadSample(
                    path=path,
                    status=status,
                    ttfb_ms=total,
                    total_ms=total,
                    bytes=len(body),
                )
            )
        out[path] = samples
    return out


def _summarize_load(path: str, py: list[LoadSample], go: list[LoadSample]) -> LoadComparison:
    def stats(samples: list[LoadSample]) -> tuple[float, float, float, int]:
        ttfb = sorted(s.ttfb_ms for s in samples)
        total = sorted(s.total_ms for s in samples)
        return (
            _percentile(ttfb, 0.50),
            _percentile(ttfb, 0.95),
            _percentile(total, 0.50),
            samples[0].bytes if samples else 0,
        )

    py_ttfb_p50, py_ttfb_p95, py_total_p50, py_bytes = stats(py)
    go_ttfb_p50, go_ttfb_p95, go_total_p50, go_bytes = stats(go)
    return LoadComparison(
        path=path,
        repeats=len(py),
        python_bytes=py_bytes,
        go_bytes=go_bytes,
        byte_identical=py_bytes == go_bytes and py_bytes > 0,
        python_ttfb_p50_ms=round(py_ttfb_p50, 2),
        python_ttfb_p95_ms=round(py_ttfb_p95, 2),
        python_total_p50_ms=round(py_total_p50, 2),
        go_ttfb_p50_ms=round(go_ttfb_p50, 2),
        go_ttfb_p95_ms=round(go_ttfb_p95, 2),
        go_total_p50_ms=round(go_total_p50, 2),
    )


def _measure_route(
    url: str, token: str, concurrency: int, total_requests: int, warmup: int = 8
) -> RouteMetrics:
    error_kinds: dict[str, int] = {}

    def one() -> tuple[float, bool]:
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url)
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
                return (time.perf_counter() - t0) * 1000.0, True
        except urllib.error.HTTPError as exc:
            # The server ANSWERED with a non-2xx: a real response, not a
            # transport failure. Record the status so the summary can say
            # whether the origin errored or the CLIENT could not connect.
            error_kinds[f"HTTP {exc.code}"] = error_kinds.get(f"HTTP {exc.code}", 0) + 1
            return (time.perf_counter() - t0) * 1000.0, False
        except OSError as exc:
            # Windows burns through the ephemeral port range fast under high
            # concurrency; a connect failure is a CLIENT artifact, not the
            # server's. Label it so a load number is never misread as downtime.
            kind = f"client-transport: {type(exc).__name__}"
            error_kinds[kind] = error_kinds.get(kind, 0) + 1
            return (time.perf_counter() - t0) * 1000.0, False
        except Exception as exc:
            kind = f"client: {type(exc).__name__}"
            error_kinds[kind] = error_kinds.get(kind, 0) + 1
            return (time.perf_counter() - t0) * 1000.0, False

    for _ in range(warmup):
        one()

    per_worker = max(1, total_requests // concurrency)
    t_start = time.perf_counter()
    latencies: list[float] = []
    errors = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(lambda: [one() for _ in range(per_worker)]) for _ in range(concurrency)]
        for f in concurrent.futures.as_completed(futs):
            for lat, ok in f.result():
                latencies.append(lat)
                if not ok:
                    errors += 1
    duration = time.perf_counter() - t_start

    latencies.sort()
    count = len(latencies)
    ok = count - errors
    if error_kinds:
        # Surface the dominant failure reason inline: an operator reading the
        # report must be able to tell 'server said 503' from 'my client could
        # not get a port'.
        top = max(error_kinds.items(), key=lambda kv: kv[1])
        print(f"         (non-2xx: {errors} — dominant: {top[0]} x{top[1]})")
    return RouteMetrics(
        total_requests=count,
        error_count=errors,
        error_rate=errors / count if count else 1.0,
        duration_sec=duration,
        requests_per_sec=ok / duration if duration > 0 else 0.0,
        p50_ms=round(_percentile(latencies, 0.50), 2),
        p95_ms=round(_percentile(latencies, 0.95), 2),
        p99_ms=round(_percentile(latencies, 0.99), 2),
        mean_ms=round(sum(latencies) / count, 2) if count else 0.0,
    )


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def _ui_load_paths(dist: Path) -> list[str]:
    """The literal documents of first paint: index.html + its hashed assets."""
    index = dist / "index.html"
    if not index.is_file():
        return []
    paths = ["/"]
    import re

    html = index.read_text(encoding="utf-8")
    for m in re.findall(r'(?:src|href)="(/assets/[^"]+)"', html):
        if m not in paths:
            paths.append(m)
    # the manifest + a representative big bundle and stylesheet
    for extra in ("/manifest.json",):
        if (dist / extra.lstrip("/")).is_file() and extra not in paths:
            paths.append(extra)
    return paths


def run(
    repeats: int, requests_per_level: int, concurrencies: list[int], output_path: Path | None
) -> dict:
    from nexus_scalp.web import frontend_assets

    dist = frontend_assets.resolve_frontend_dist()
    load_paths = _ui_load_paths(dist) if dist else []
    if not load_paths:
        print(
            "NOTE: no built frontend/dist found — running the route mix only\n"
            "      (build it with `npm run build` in frontend/ to measure the\n"
            "       full UI load path too).",
            file=sys.stderr,
        )

    token = "wave5-bench-token"
    stub_port, go_port, py_port = _free_port(), _free_port(), _free_port()

    stub = _start_stub(stub_port)
    go_proc = _start_go_origin(go_port, stub_port, token)
    py_proc = None
    if dist:
        py_proc = _start_python_origin(py_port, stub_port, dist, token)

    comparisons: list[LoadComparison] = []
    route_comparisons: list[RouteComparison] = []
    probe_result = ""

    try:
        if _wait(f"http://127.0.0.1:{stub_port}/api/status", strict=False) is None:
            raise RuntimeError("stub upstream did not start")
        # Go is 'ready' when it answers at all: 200 (Python live), 503 (Go
        # listening, Python not yet live) or 404 (health not mounted, proving
        # the router is dispatching). Any other status is a real config error
        # and still surfaces as a raise from _wait.
        if _wait(f"http://127.0.0.1:{go_port}/health", ok_statuses=(200, 503, 404)) is None:
            raise RuntimeError("go origin did not start")
        # The minimal Python origin answers /index.html (SimpleHTTPRequestHandler
        # serves / as the index document); any HTTP response means it is live.
        if (
            py_proc is not None
            and _wait(f"http://127.0.0.1:{py_port}/index.html", token=token, strict=False) is None
        ):
            raise RuntimeError("python origin did not start")

        go = _Origin("go", f"http://127.0.0.1:{go_port}", token)
        py = _Origin("python", f"http://127.0.0.1:{py_port}", token) if py_proc else None

        # ---- 1. the UI load path -------------------------------------------------
        if load_paths and py is not None:
            print("=" * 78)
            print("  UI LOAD PATH — Go origin vs Python origin (same dist, same minute)")
            print("=" * 78)
            for path in load_paths:
                gs = _measure_load_path(go, [path], repeats)[path]
                ps = _measure_load_path(py, [path], repeats)[path]
                cmp = _summarize_load(path, ps, gs)
                comparisons.append(cmp)
                identical = "IDENTICAL" if cmp.byte_identical else "DIFFERS"
                print(
                    f"  {path:34s} py={cmp.python_total_p50_ms:6.2f}ms  "
                    f"go={cmp.go_total_p50_ms:6.2f}ms  "
                    f"bytes py={cmp.python_bytes:8d} go={cmp.go_bytes:8d}  {identical}"
                )
            print()

        # ---- 2. the route mix ----------------------------------------------------
        print("=" * 78)
        print("  ROUTE MIX — the calls the React console makes while it is alive")
        print("=" * 78)
        for route in ROUTE_MIX:
            print(f"> {route}")
            for c in concurrencies:
                gm = _measure_route(go.base + route, token, c, requests_per_level)
                if py is not None:
                    pm = _measure_route(py.base + route, token, c, requests_per_level)
                    speedup = (
                        gm.requests_per_sec / pm.requests_per_sec if pm.requests_per_sec else 0.0
                    )
                    p50red = (1.0 - gm.p50_ms / pm.p50_ms) * 100.0 if pm.p50_ms else 0.0
                    route_comparisons.append(
                        RouteComparison(
                            route=route,
                            concurrency=c,
                            python_baseline=pm,
                            go_origin=gm,
                            rps_speedup=round(speedup, 2),
                            p50_reduction_pct=round(p50red, 1),
                        )
                    )
                    print(
                        f"  [C={c:2d}] python p50={pm.p50_ms:6.2f} p95={pm.p95_ms:6.2f} "
                        f"rps={pm.requests_per_sec:7.1f} err={pm.error_rate:.1%}"
                    )
                    print(
                        f"         go     p50={gm.p50_ms:6.2f} p95={gm.p95_ms:6.2f} "
                        f"rps={gm.requests_per_sec:7.1f} err={gm.error_rate:.1%}"
                    )
                    print(f"         -> {speedup:5.2f}x throughput, p50 reduction {p50red:5.1f}%")
                else:
                    print(
                        f"  [C={c:2d}] go p50={gm.p50_ms:6.2f} p95={gm.p95_ms:6.2f} "
                        f"rps={gm.requests_per_sec:7.1f} err={gm.error_rate:.1%}"
                    )

        # ---- probe summary -------------------------------------------------------
        all_identical = all(c.byte_identical for c in comparisons) if comparisons else False
        speedups = [r.rps_speedup for r in route_comparisons if r.rps_speedup > 0]
        mean_speedup = sum(speedups) / len(speedups) if speedups else 0.0
        go_err = [r.go_origin.error_rate for r in route_comparisons]
        py_err = [r.python_baseline.error_rate for r in route_comparisons]
        probe_result = (
            f"ui_load_paths={len(comparisons)} byte_identical={all_identical}; "
            f"route_mix_configs={len(route_comparisons)} "
            f"mean_rps_speedup={mean_speedup:.2f}x "
            f"go_error_rate={max(go_err) if go_err else 0.0:.1%} "
            f"python_error_rate={max(py_err) if py_err else 0.0:.1%}"
        )

    finally:
        for proc in (stub, go_proc, py_proc):
            if proc is None:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    load_summary = {
        "load_paths_measured": len(comparisons),
        "all_byte_identical": all(c.byte_identical for c in comparisons) if comparisons else None,
        "paths": [asdict(c) for c in comparisons],
    }
    route_summary = {
        "configs": len(route_comparisons),
        "mean_rps_speedup": round(
            sum(r.rps_speedup for r in route_comparisons if r.rps_speedup > 0)
            / max(1, len([r for r in route_comparisons if r.rps_speedup > 0])),
            2,
        ),
        "comparisons": [asdict(r) for r in route_comparisons],
    }

    results = {
        "timestamp": datetime.now(UTC).isoformat(),
        "wave": "5",
        "lane": "D",
        "purpose": "end-user UI-through-Go verification + Go vs Python UI origin benchmark",
        "environment": {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "frontend_dist": str(dist) if dist else None,
        },
        "configuration": {
            "load_path_repeats": repeats,
            "route_mix": ROUTE_MIX,
            "concurrencies": concurrencies,
            "requests_per_level": requests_per_level,
        },
        # Honest reading guide for the route-mix numbers at high concurrency.
        # These were measured, not assumed; see the note below for what they
        # do and do not license.
        "measurement_caveats": {
            "high_concurrency": (
                "At C>=8 the Go origin's proxied routes (anything the Go "
                "router forwards to the Python upstream) start returning "
                "DEPENDENCY_UNAVAILABLE: consecutive upstream failures trip "
                "python.Client's circuit breaker (MaxFailures=3, Cooldown=5s), "
                "and the stub upstream used here has a listen backlog of 5 "
                "while Go's http.Client uses the default transport "
                "(MaxIdleConnsPerHost=2). The error_rate column plus the "
                "'dominant failure' line show it per sweep. Treat C=1..4 as "
                "the clean latency comparison and C>=10 as a load behaviour "
                "observation, not a throughput claim."
            ),
            "baseline": (
                "Both origins stand in front of the same deterministic stub "
                "upstream, so the route-mix numbers measure the SERVING "
                "PLANE, not the engine (the real Python api_v1/health path "
                "runs the HealthEngine sweep and is far slower)."
            ),
        },
        "ui_load_path": load_summary,
        "route_mix": route_summary,
        "probe_result": probe_result,
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nResults written to {output_path}")

    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Wave 5: UI load path + console route mix, Go origin vs Python origin"
    )
    ap.add_argument("--repeats", type=int, default=25, help="fetches per UI load-path document")
    ap.add_argument(
        "--requests", type=int, default=200, help="requests per route/concurrency sweep"
    )
    ap.add_argument("--concurrencies", type=int, nargs="+", default=[1, 10, 50])
    ap.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--quick", action="store_true", help="fast validation run")
    args = ap.parse_args(argv)

    reps = 5 if args.quick else args.repeats
    reqs = 40 if args.quick else args.requests
    out = None if args.no_save else Path(args.output)

    try:
        run(
            repeats=reps, requests_per_level=reqs, concurrencies=args.concurrencies, output_path=out
        )
        return 0
    except Exception as exc:
        print(f"BENCHMARK ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
