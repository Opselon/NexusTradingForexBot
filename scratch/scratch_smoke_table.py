"""Smoke test: does the Go binary serve the generated route table?

Starts the Go API alone (Python NOT running — every request should reach the
circuit-breaker path, which is the perfect probe for ROUTE REGISTRATION:

  - route registered           -> Go calls Python -> 503 DEPENDENCY_UNAVAILABLE
  - route MISSING              -> Go's own 404 (a route-table defect)
  - route registered, wrong verb -> Go's own 405

So the status code alone tells us the mux state without needing Python.
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
GO_API = REPO / "go-api"
BIN = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "nexus-api-smoke.exe"

PORT = 0


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> int:
    port = free_port()
    env = dict(os.environ)
    env["PYTHON_ORIGIN"] = f"http://127.0.0.1:{port + 1}"  # nothing listens
    env["NSE_WEB_AUTH_DISABLE"] = "1"
    env["NEXUS_API_LISTEN_ADDR"] = f"127.0.0.1:{port}"

    proc = subprocess.Popen(
        [str(BIN), "-addr", f"127.0.0.1:{port}",
         "-python-origin", f"http://127.0.0.1:{port + 1}"],
        cwd=str(GO_API), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # wait for listen
        for _ in range(60):
            try:
                with socket.create_connection(("127.0.0.1", port), 0.3):
                    break
            except OSError:
                time.sleep(0.15)
        time.sleep(0.4)

        table = json.loads(
            (GO_API / "internal" / "api" / "routes" / "table_gen.go").read_text(
                encoding="utf-8")
        ) if False else None
        # parse the table from the Go source (simple line scan)
        rows = []
        src = (GO_API / "internal" / "api" / "routes" / "table_gen.go").read_text(
            encoding="utf-8")
        for line in src.splitlines():
            line = line.strip()
            if line.startswith("{Method:"):
                parts = line.split(", ")
                m = parts[0].split('"')[1]
                p = parts[1].split('"')[1]
                rows.append((m, p))
        print(f"table rows: {len(rows)}")

        def hit(method: str, path: str) -> tuple[int, str]:
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                         method=method)
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, r.read()[:200].decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                return e.code, e.read()[:200].decode("utf-8", "replace")
            except Exception as e:  # noqa: BLE001
                return -1, str(e)[:120]

        # --- does each registered route reach the proxy (not Go 404)? ---
        registered = 0
        missing: list[str] = []
        for m, p in rows:
            # substitute a plausible value for path params so the request
            # actually matches the template
            probe = p
            for tok in ("{strategy_id}", "{node_id:path}", "{node_id}",
                        "{incident_id}", "{run_id}", "{model_id}",
                        "{generation_id}", "{candidate_id}", "{ticket}",
                        "{trade_id}", "{snapshot_id}", "{execution_id}",
                        "{article_id}", "{name}", "{kind}"):
                probe = probe.replace(tok, "1")
            probe = probe.replace("{rest...}", "a/b")
            st, body = hit(m, probe)
            if st == 404:
                missing.append(f"{m} {p} -> {st} {body[:80]}")
            else:
                registered += 1
        print(f"\nreachable (not Go-404): {registered}/{len(rows)}")
        if missing:
            print(f"MISSING {len(missing)}:")
            for x in missing[:15]:
                print("   ", x)

        # --- wrong verb on a known path => 405, not 404 ---
        bad_verb: list[str] = []
        for m, p in rows[:40]:
            other = "POST" if m != "POST" else "GET"
            st, _ = hit(other, p.replace("{node_id:path}", "1"))
            if st not in (405, 503):
                bad_verb.append(f"{other} {p} -> {st}")
        print(f"\nwrong-verb probes (expect 405 or proxied): "
              f"{len(bad_verb)} anomalies")
        for x in bad_verb[:8]:
            print("   ", x)

        # --- truly unknown path => 404 ---
        for path, surface in (("/api/v1/does-not-exist", "v1"),
                              ("/api/does-not-exist", "legacy")):
            st, body = hit("GET", path)
            print(f"\nunknown {surface} path {path}: {st}  {body[:100]}")

        return 1 if missing else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
