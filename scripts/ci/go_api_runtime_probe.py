#!/usr/bin/env python3
"""Runtime probe for the NSE Go API route surface."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "ci-go-api-token"
REQUEST_TIMEOUT = 5.0
STARTUP_TIMEOUT = 30.0

ROUTE_RE = re.compile(
    r'\{Method:\s*"(?P<method>[A-Z]+)"\s*,\s*Path:\s*"(?P<path>[^"]+)"'
)
HANDLE_RE = re.compile(
    r'Handle(?:Func)?\(\s*"(?P<method>[A-Z]+)"\s*,\s*"(?P<path>[^"]+)"'
)


def collect_routes() -> list[tuple[str, str]]:
    files = (
        ROOT / "go-api/internal/api/routes/table_gen.go",
        ROOT / "go-api/internal/api/routes/table_handwritten.go",
        ROOT / "go-api/internal/api/routes/routes.go",
    )
    found: set[tuple[str, str]] = set()
    for path in files:
        source = path.read_text(encoding="utf-8")
        for rx in (ROUTE_RE, HANDLE_RE):
            for match in rx.finditer(source):
                found.add((match.group("method"), match.group("path")))
    return sorted((m, p) for m, p in found if p.startswith("/api/"))


def concrete_path(path: str) -> str:
    def replace(match: re.Match[str]) -> str:
        spec = match.group(1)
        if spec.endswith(":path"):
            return "probe/subpath"
        name = spec.split(":", 1)[0].lower()
        if "ticket" in name or "id" in name or "number" in name:
            return "1"
        return "probe"

    return re.sub(r"\{([^}]+)\}", replace, path)


def http_probe(base: str, method: str, path: str) -> dict[str, Any]:
    probe = concrete_path(path)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json, text/plain, */*",
    }
    if method in {"POST", "PUT", "PATCH"}:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base.rstrip("/") + probe,
        data=b"{}" if method in {"POST", "PUT", "PATCH"} else None,
        headers=headers,
        method=method,
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            status = int(response.status)
            body = response.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read(4096).decode("utf-8", "replace")
    except Exception as exc:
        return {
            "method": method,
            "path": path,
            "probe_path": probe,
            "status": None,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "transport_error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "method": method,
        "path": path,
        "probe_path": probe,
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "body_prefix": body[:1000],
    }


def wait_ready(base: str) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(base.rstrip("/") + "/health"),
                timeout=2,
            ) as response:
                # Any HTTP response proves the listener/runtime is alive.
                # The no-Python CI mode intentionally returns 503 for health.
                if 100 <= int(response.status) < 600:
                    return
        except urllib.error.HTTPError as exc:
            # A valid HTTP 4xx/5xx response still proves the Go server is
            # listening; 503 is the expected no-Python dependency state.
            if 100 <= int(exc.code) < 600:
                return
            last_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise RuntimeError(f"Go API did not become ready: {last_error}")


def stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            proc.wait(timeout=8)
            return
        except Exception:
            proc.terminate()
            proc.wait(timeout=5)
            return
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=5)


def check_frontend(base: str) -> dict[str, Any]:
    req = urllib.request.Request(
        base.rstrip("/") + "/",
        headers={"Accept": "text/html"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            status = int(response.status)
            content_type = response.headers.get("Content-Type", "")
            body = response.read(256 * 1024).decode("utf-8", "replace")
    except Exception as exc:
        return {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}

    lowered = body.lower()
    ok = (
        status == 200
        and "text/html" in content_type.lower()
        and ("<html" in lowered or 'id="root"' in lowered)
    )
    return {
        "status": "PASS" if ok else "FAIL",
        "http_status": status,
        "content_type": content_type,
        "has_html_marker": "<html" in lowered,
        "has_react_root": 'id="root"' in lowered,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--port", type=int, default=18787)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--frontend-dist", type=Path)
    args = parser.parse_args()

    binary = Path(args.binary).resolve()
    if not binary.is_file():
        raise SystemExit(f"Go API binary not found: {binary}")

    routes = collect_routes()
    if len(routes) < 400:
        raise SystemExit(
            f"Go route inventory unexpectedly small: {len(routes)}; "
            "refusing to certify an incomplete API surface"
        )

    env = os.environ.copy()
    env.update(
        {
            "NSE_GO_ADDR": f"127.0.0.1:{args.port}",
            "NSE_WEB_AUTH_TOKEN": TOKEN,
            "NSE_GO_LOG_LEVEL": "info",
            "NSE_PYTHON_ORIGIN": "",
        }
    )
    if args.frontend_dist:
        env["NEXUS_ALT_UI_DIR"] = str(args.frontend_dist.resolve())

    output_root = args.output.parent if args.output else ROOT / "ci-results"
    output_root.mkdir(parents=True, exist_ok=True)
    log_path = output_root / "go-api-runtime.log"

    with log_path.open("w", encoding="utf-8") as log:
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(
            [str(binary)],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )

    try:
        base = f"http://127.0.0.1:{args.port}"
        wait_ready(base)

        unauth_request = urllib.request.Request(base + "/api/status", method="GET")
        try:
            with urllib.request.urlopen(unauth_request, timeout=REQUEST_TIMEOUT) as response:
                unauth_status = int(response.status)
        except urllib.error.HTTPError as exc:
            unauth_status = int(exc.code)

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(
                pool.map(
                    lambda item: http_probe(base, item[0], item[1]),
                    routes,
                )
            )

        failures = [
            item for item in results
            if item.get("transport_error")
            or item.get("status") is None
            or item.get("status") in {404, 405}
            or (item.get("status", 0) >= 500 and item.get("status") != 503)
        ]

        result: dict[str, Any] = {
            "status": "PASS" if not failures and unauth_status == 401 else "FAIL",
            "route_count": len(routes),
            "api_probe_count": len(results),
            "unique_api_operations": len({(x["method"], x["path"]) for x in results}),
            "unauthenticated_status": unauth_status,
            "expected_dependency_unavailable_503": sum(
                1 for x in results if x.get("status") == 503
            ),
            "transport_failures": sum(1 for x in results if x.get("transport_error")),
            "failures": failures,
            "results": results,
            "binary": str(binary),
            "port": args.port,
            "python_origin": "",
        }

        if args.frontend_dist:
            result["frontend_runtime"] = check_frontend(base)
            if result["frontend_runtime"]["status"] != "PASS":
                result["status"] = "FAIL"

        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        print(
            json.dumps(
                {
                    "status": result["status"],
                    "route_count": result["route_count"],
                    "api_probe_count": result["api_probe_count"],
                    "unauthenticated_status": unauth_status,
                    "failures": len(failures),
                    "frontend_runtime": result.get("frontend_runtime", {}).get(
                        "status", "NOT_RUN"
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        if failures:
            print("=== FIRST RUNTIME FAILURES ===")
            print(json.dumps(failures[:25], indent=2, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    finally:
        stop_process(proc)


if __name__ == "__main__":
    raise SystemExit(main())
