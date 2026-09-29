"""Performance benchmark: Go direct serving vs Python proxy (Wave 4).

Measures and compares according to Master Spec §8 & §9:
- p50, p95, p99 latency (ms)
- Requests/sec (throughput)
- Total requests and error rate

Fair Benchmark Rules (Spec §9):
- Same machine: localhost paired execution
- Same endpoints: candidate routes for direct serving
- Same payload: GET requests with identical headers
- Concurrency levels: 1, 10, 50

Emits structured results to stdout and saves to artifacts/benchmarks/wave4_benchmark.json.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GO_API_DIR = REPO_ROOT / "go-api"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "benchmarks" / "wave4_benchmark.json"

CANDIDATE_ROUTES = [
    "/api/v1/features/contract",
    "/api/v1/features/groups",
    "/api/v1/config/schema",
    "/api/v1/system/capabilities",
    "/api/v1/system/version",
]

GO_SERVER_SOURCE = r"""package main

import (
	"flag"
	"fmt"
	"net/http"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/handlers"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/observability"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/web"
)

func main() {
	addr := flag.String("addr", ":8087", "listen address")
	flag.Parse()

	c := handlers.NewContracts(nil)
	sd := handlers.NewSystemDirect(nil)
	ps := web.NewPlatformStatic(nil)

	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/features/contract", c.FeatureContract)
	mux.HandleFunc("/api/v1/features/groups", c.FeatureGroups)
	mux.HandleFunc("/api/v1/config/schema", c.ConfigSchema)
	mux.HandleFunc("/api/v1/system/capabilities", sd.Capabilities)
	mux.HandleFunc("/api/v1/system/version", sd.Version)
	mux.HandleFunc("/openapi.json", ps.ServeOpenAPI)
	mux.HandleFunc("/docs", ps.ServeDocs)
	mux.HandleFunc("/redoc", ps.ServeReDoc)

	handler := observability.RequestIDMiddleware(mux)

	fmt.Printf("READY on %s\n", *addr)
	http.ListenAndServe(*addr, handler)
}
"""


@dataclass
class TargetMetrics:
    total_requests: int
    error_count: int
    error_rate: float
    duration_sec: float
    requests_per_sec: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    mean_ms: float


@dataclass
class BenchmarkComparison:
    route: str
    concurrency: int
    python_baseline: TargetMetrics
    go_direct: TargetMetrics
    rps_speedup: float
    p50_reduction_pct: float
    p95_reduction_pct: float


def _find_go_binary() -> str | None:
    candidates = [
        os.environ.get("GOROOT", "") + "/bin/go.exe",
        "C:/Users/Capsizer/go-toolchain/go/bin/go.exe",
        shutil.which("go.exe") or "",
        shutil.which("go") or "",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return str(Path(c).resolve())
    return None


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _safe_unlink(path: Path) -> None:
    for _ in range(25):
        try:
            if path.exists():
                path.unlink()
            return
        except OSError:
            time.sleep(0.1)


def _wait_for_server(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status in (200, 404):
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def _measure_target(
    url: str,
    concurrency: int,
    total_requests: int,
    warmup: int = 10,
) -> TargetMetrics:
    # Warmup phase
    for _ in range(warmup):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                resp.read()
        except Exception:
            pass

    reqs_per_worker = max(1, total_requests // concurrency)
    actual_total = reqs_per_worker * concurrency

    def worker() -> tuple[list[float], int]:
        latencies: list[float] = []
        errors = 0
        for _ in range(reqs_per_worker):
            t0 = time.perf_counter()
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
                    latencies.append((time.perf_counter() - t0) * 1000.0)
            except Exception:
                errors += 1
        return latencies, errors

    t_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker) for _ in range(concurrency)]
        all_latencies: list[float] = []
        total_errors = 0
        for f in futures:
            lats, errs = f.result()
            all_latencies.extend(lats)
            total_errors += errs
    duration = time.perf_counter() - t_start

    all_latencies.sort()
    count = len(all_latencies)
    if count == 0:
        return TargetMetrics(
            total_requests=actual_total,
            error_count=total_errors,
            error_rate=1.0,
            duration_sec=duration,
            requests_per_sec=0.0,
            p50_ms=0.0,
            p95_ms=0.0,
            p99_ms=0.0,
            min_ms=0.0,
            max_ms=0.0,
            mean_ms=0.0,
        )

    p50 = all_latencies[int(count * 0.50)]
    p95 = all_latencies[min(count - 1, int(count * 0.95))]
    p99 = all_latencies[min(count - 1, int(count * 0.99))]
    mean_val = sum(all_latencies) / count
    rps = count / duration if duration > 0 else 0.0
    err_rate = total_errors / actual_total if actual_total > 0 else 0.0

    return TargetMetrics(
        total_requests=count + total_errors,
        error_count=total_errors,
        error_rate=err_rate,
        duration_sec=duration,
        requests_per_sec=rps,
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        min_ms=all_latencies[0],
        max_ms=all_latencies[-1],
        mean_ms=mean_val,
    )


def run_benchmark(
    concurrencies: list[int],
    requests_per_level: int,
    routes: list[str],
    output_path: Path | None = None,
    warmup: int = 10,
) -> dict:
    go_bin = _find_go_binary()
    if not go_bin:
        raise RuntimeError("Go compiler not found")

    py_port = _get_free_port()
    go_port = _get_free_port()
    tmp_dir = Path(tempfile.gettempdir())
    tmp_exe = tmp_dir / f"wave4_bench_server_{go_port}.exe"
    src_file = GO_API_DIR / f"bench_server_{go_port}.go"

    print("=" * 80)
    print("  WAVE 4 PERFORMANCE BENCHMARK: GO DIRECT SERVING VS PYTHON BASELINE")
    print("  Master Spec §8 (Performance Discipline) & §9 (Fair Comparison Rules)")
    print("=" * 80)
    print(f"Platform: {platform.system()} | Python: {sys.version.split()[0]} | Go: {go_bin}")
    print(
        f"Concurrencies: {concurrencies} | Requests: {requests_per_level} | Routes: {len(routes)}\n"
    )

    # Start Python baseline server
    py_env = os.environ.copy()
    py_env.update(
        {
            "PYTHONPATH": "src",
            "NSE_WEB_AUTH_TOKEN": "bench-token",
            "NSE_WEB_PORT": str(py_port),
        }
    )
    py_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "nexus_scalp.web.api_v1_wiring:create_v1_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(py_port),
        "--log-level",
        "error",
    ]
    py_proc = subprocess.Popen(
        py_cmd,
        cwd=str(REPO_ROOT),
        env=py_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Build and start Go direct server
    src_file.write_text(GO_SERVER_SOURCE, encoding="utf-8")
    go_env = os.environ.copy()
    go_env.update(
        {
            "GOROOT": "C:/Users/Capsizer/go-toolchain/go",
            "GOPATH": "C:/Users/Capsizer/go",
            "GOMODCACHE": "C:/Users/Capsizer/go/pkg/mod",
            "GOFLAGS": "-mod=mod",
            "GOOS": "windows",
        }
    )
    build_res = subprocess.run(
        [go_bin, "build", "-o", str(tmp_exe), src_file.name],
        cwd=str(GO_API_DIR),
        env=go_env,
        capture_output=True,
        text=True,
        check=False,
    )
    _safe_unlink(src_file)

    if build_res.returncode != 0:
        py_proc.terminate()
        py_proc.wait()
        raise RuntimeError(f"Go direct server build failed: {build_res.stderr}")

    go_proc = subprocess.Popen(
        [str(tmp_exe), f"-addr=127.0.0.1:{go_port}"],
        cwd=str(REPO_ROOT),
        env=go_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    comparisons: list[BenchmarkComparison] = []

    try:
        # Wait for readiness
        py_probe_url = f"http://127.0.0.1:{py_port}/api/v1/system/capabilities"
        go_probe_url = f"http://127.0.0.1:{go_port}/api/v1/features/contract"
        if not _wait_for_server(py_probe_url):
            raise RuntimeError("Python baseline server failed to start")
        if not _wait_for_server(go_probe_url):
            raise RuntimeError("Go direct server failed to start")

        print("Servers ready. Running paired benchmark sweeps...\n")

        for route in routes:
            print(f"> Route: {route}")
            py_url = f"http://127.0.0.1:{py_port}{route}"
            go_url = f"http://127.0.0.1:{go_port}{route}"

            for c in concurrencies:
                py_metrics = _measure_target(py_url, c, requests_per_level, warmup=warmup)
                go_metrics = _measure_target(go_url, c, requests_per_level, warmup=warmup)

                py_rps, go_rps = py_metrics.requests_per_sec, go_metrics.requests_per_sec
                py_p50, go_p50 = py_metrics.p50_ms, go_metrics.p50_ms
                py_p95, go_p95 = py_metrics.p95_ms, go_metrics.p95_ms
                rps_speedup = go_rps / py_rps if py_rps > 0 else 0.0
                p50_red = (1.0 - (go_p50 / py_p50)) * 100.0 if py_p50 > 0 else 0.0
                p95_red = (1.0 - (go_p95 / py_p95)) * 100.0 if py_p95 > 0 else 0.0

                cmp = BenchmarkComparison(
                    route=route,
                    concurrency=c,
                    python_baseline=py_metrics,
                    go_direct=go_metrics,
                    rps_speedup=round(rps_speedup, 2),
                    p50_reduction_pct=round(p50_red, 1),
                    p95_reduction_pct=round(p95_red, 1),
                )
                comparisons.append(cmp)

                print(
                    f"  [C={c:2d}] Python:    p50={py_metrics.p50_ms:6.2f}ms  "
                    f"p95={py_metrics.p95_ms:6.2f}ms  p99={py_metrics.p99_ms:6.2f}ms  "
                    f"RPS={py_metrics.requests_per_sec:7.1f}  err={py_metrics.error_rate:.1%}"
                )
                print(
                    f"         Go Direct: p50={go_metrics.p50_ms:6.2f}ms  "
                    f"p95={go_metrics.p95_ms:6.2f}ms  p99={go_metrics.p99_ms:6.2f}ms  "
                    f"RPS={go_metrics.requests_per_sec:7.1f}  err={go_metrics.error_rate:.1%}"
                )
                print(
                    f"         --> Speedup: {rps_speedup:5.2f}x throughput  |  "
                    f"p50 latency reduction: {p50_red:5.1f}%\n"
                )

    finally:
        py_proc.terminate()
        go_proc.terminate()
        try:
            py_proc.wait(timeout=5)
            go_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            py_proc.kill()
            go_proc.kill()
        _safe_unlink(tmp_exe)

    # Compute overall statistics
    valid_speedups = [c.rps_speedup for c in comparisons if c.rps_speedup > 0]
    avg_speedup = sum(valid_speedups) / len(valid_speedups) if valid_speedups else 1.0

    print("=" * 80)
    print(
        f"BENCHMARK SUMMARY: {len(comparisons)} runs | Mean Speedup: {avg_speedup:.2f}x | Max: {max(valid_speedups or [1.0]):.2f}x"
    )
    print("=" * 80)

    structured_results = {
        "timestamp": datetime.now(UTC).isoformat(),
        "spec_compliance": {
            "master_spec_section_8": "Performance discipline (measured baseline vs target)",
            "master_spec_section_9": "Fair comparison (same machine, endpoint, payload, concurrency)",
        },
        "environment": {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "go_binary": go_bin,
        },
        "configuration": {
            "concurrencies": concurrencies,
            "requests_per_level": requests_per_level,
            "routes": routes,
        },
        "summary": {
            "configurations_count": len(comparisons),
            "mean_rps_speedup": round(avg_speedup, 2),
            "max_rps_speedup": round(max(valid_speedups or [1.0]), 2),
            "min_rps_speedup": round(min(valid_speedups or [1.0]), 2),
        },
        "comparisons": [asdict(c) for c in comparisons],
    }

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(structured_results, indent=2), encoding="utf-8")
        print(f"Structured results written to: {output_path}")

    return structured_results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wave 4 Go direct vs Python performance benchmark")
    parser.add_argument(
        "--concurrencies",
        type=int,
        nargs="+",
        default=[1, 10, 50],
        help="Concurrency levels (1 10 50)",
    )
    parser.add_argument("--requests", type=int, default=200, help="Requests per concurrency sweep")
    parser.add_argument(
        "--routes", type=str, nargs="+", default=CANDIDATE_ROUTES, help="Routes to benchmark"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_OUTPUT),
        help="Path to save JSON benchmark artifact",
    )
    parser.add_argument("--no-save", action="store_true", help="Do not save JSON artifact to disk")
    parser.add_argument(
        "--quick", action="store_true", help="Fast run with fewer requests for rapid validation"
    )
    args = parser.parse_args(argv)

    reqs = 50 if args.quick else args.requests
    out = None if args.no_save else Path(args.output)

    try:
        run_benchmark(
            concurrencies=args.concurrencies,
            requests_per_level=reqs,
            routes=args.routes,
            output_path=out,
        )
        return 0
    except Exception as exc:
        print(f"BENCHMARK ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
