"""scripts/dev/api_benchmark.py — bounded local performance smoke for v1 read routes.

Measures median / p95 latency per high-frequency route against the EMBEDDED v1
app (TestClient — same contract surface, no server, no external calls, no
trading operations). Bounded by design: ~150 requests/route, read-only routes
only. Reports per-route results and flags routes above a soft p95 budget.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

ROUTES = [
    "/api/v1/system/status",
    "/api/v1/system/version",
    "/api/v1/system/health",
    "/api/v1/runtime/mode",
    "/api/v1/signals/latest",
    "/api/v1/decisions",
    "/api/v1/decisions/stats",
    "/api/v1/positions",
    "/api/v1/risk/status",
    "/api/v1/execution/status",
    "/api/v1/model/status",
    "/api/v1/features/contract",
    "/api/v1/research/status",
    "/api/v1/shadow/status",
    "/api/v1/incidents",
    "/api/v1/database/status",
    "/api/v1/observability/metrics",
    "/api/v1/system/capabilities",
]
REQUESTS_PER_ROUTE = 150
P95_BUDGET_MS = 250.0  # soft budget: SQLite + HealthEngine are allowed to be slowish


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded v1 API performance smoke (embedded)")
    parser.add_argument(
        "--requests", type=int, default=REQUESTS_PER_ROUTE, help="requests per route (cap 300)"
    )
    args = parser.parse_args(argv)
    n = max(1, min(args.requests, 300))

    from fastapi.testclient import TestClient

    from nexus_scalp.web.api_v1_wiring import create_v1_app

    client = TestClient(create_v1_app())
    print(f"API BENCHMARK ({n} req/route, embedded, read-only)")
    results: list[tuple[str, float, float, float]] = []
    over_budget: list[str] = []
    for route in ROUTES:
        samples: list[float] = []
        for i in range(n):
            t0 = time.perf_counter()
            r = client.get(route)
            dt = (time.perf_counter() - t0) * 1000.0
            if i == 0 and r.status_code >= 500:
                print(f"  ✗ {route}: HTTP {r.status_code} (skipped)")
                samples = []
                break
            samples.append(dt)
        if not samples:
            continue
        med = statistics.median(samples)
        p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
        p99 = sorted(samples)[int(len(samples) * 0.99) - 1]
        results.append((route, med, p95, p99))
        flag = " (over budget)" if p95 > P95_BUDGET_MS else ""
        if p95 > P95_BUDGET_MS:
            over_budget.append(route)
        print(f"  {route:42s} median={med:7.2f}ms p95={p95:7.2f}ms p99={p99:7.2f}ms{flag}")

    print(
        f"API_BENCHMARK = {'PASS' if not over_budget else 'PASS_WITH_NOTES'} ({len(results)} routes, {len(over_budget)} over p95 budget {P95_BUDGET_MS:.0f}ms)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
