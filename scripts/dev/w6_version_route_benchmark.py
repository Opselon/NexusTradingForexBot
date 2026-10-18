"""scripts/dev/w6_version_route_benchmark.py — Wave 6 direct-route latency split.

Before/after harness for the /api/v1/system/version per-request git-exec fix.

The Go direct version route rebuilt its handler on every request
(handlers.SystemHandlers.VersionDirect -> NewSystemDirect(h.py)), which reset
the cached VersionData to nil each call, so EVERY request re-ran
`git rev-parse HEAD` (~43ms on Windows). Result: the Go "direct" route was
SLOWER than the Python proxy it replaced (48.73ms vs 10.11ms mean).

Run this against a live Go + Python reference pair:

    python scripts/dev/w6_version_route_benchmark.py \
        --go-port 8995 --py-port 8998 --token probe-xyz

Methodology notes (so the numbers stay honest on Windows):
- ONE keep-alive connection per origin: urllib's per-request connection
  churn pays a socket-setup cost that has nothing to do with the route, and
  triggers WinError 10054 resets on the proxied routes under load.
- per-sample retry with reconnect: a transient reset must not abort the run.
- the direct routes are compared against the same route on the Python
  origin, so the split is a like-for-like route comparison.
"""

from __future__ import annotations

import argparse
import http.client
import json
import statistics
import time

DIRECT = ["/api/v1/system/version", "/api/v1/system/capabilities", "/api/v1/config/schema"]
PROXIED = ["/api/v1/system/health", "/api/v1/system/status"]


class Origin:
    """Keep-alive HTTP client for one origin, resilient to socket resets."""

    def __init__(self, host: str, port: int, token: str, timeout: float = 30.0):
        self.host, self.port, self.token, self.timeout = host, port, token, timeout
        self._conn: http.client.HTTPConnection | None = None

    def _conn_or_new(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        return self._conn

    def _reconnect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def fetch_ms(self, path: str) -> float:
        last: Exception | str | None = None
        for _ in range(3):
            conn = self._conn_or_new()
            t0 = time.perf_counter()
            try:
                conn.request("GET", path, headers={"Authorization": f"Bearer {self.token}"})
                resp = conn.getresponse()
                resp.read()
                if 200 <= resp.status < 300:
                    return (time.perf_counter() - t0) * 1000.0
                last = f"HTTP {resp.status}"
                self._reconnect()
            except OSError as exc:
                last = exc
                self._reconnect()
        raise RuntimeError(f"fetch failed for {path}: {last}")

    def bench(self, path: str, n: int) -> dict:
        samples = sorted(self.fetch_ms(path) for _ in range(n))
        return {
            "path": path,
            "n": len(samples),
            "mean_ms": round(statistics.mean(samples), 2),
            "p50_ms": round(statistics.median(samples), 2),
            "p95_ms": round(samples[min(int(len(samples) * 0.95), len(samples) - 1)], 2),
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--go-port", type=int, default=8995, help="Go direct-origin port")
    ap.add_argument("--py-port", type=int, default=8998, help="Python origin port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token", default="probe-xyz", help="NSE_WEB_AUTH_TOKEN for the Go origin")
    ap.add_argument("--n", type=int, default=40, help="samples per route")
    args = ap.parse_args()

    go = Origin(args.host, args.go_port, args.token)
    py = Origin(args.host, args.py_port, args.token)

    out: dict[str, dict] = {"direct_candidates": {}, "proxied": {}}
    for path in DIRECT:
        out["direct_candidates"][path] = {
            "go": go.bench(path, args.n),
            "py": py.bench(path, args.n),
        }
    for path in PROXIED:
        out["proxied"][path] = {"go": go.bench(path, args.n), "py": py.bench(path, args.n)}

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
