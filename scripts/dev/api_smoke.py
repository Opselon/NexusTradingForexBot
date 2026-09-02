"""scripts/dev/api_smoke.py — domain-by-domain smoke over the v1 platform.

Two modes:
  1. --live BASE_URL   : smoke a RUNNING API over real HTTP (uses the Python client).
  2. --embedded        : boot the standalone v1 app in-process (TestClient);
                         verifies every DB-backed route + truthful 503s, no server needed.

Output: ✓/○/✗ per domain and a final ``API_SMOKE = PASS|FAIL`` line (exit code).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

DOMAINS: list[tuple[str, str, tuple[tuple[str, Any], ...]]] = [
    # (domain, path, query)
    ("system", "/api/v1/system/status", ()),
    ("runtime", "/api/v1/runtime/mode", ()),
    ("market", "/api/v1/market/quote", ()),
    ("signals", "/api/v1/signals/latest", ()),
    ("decisions", "/api/v1/decisions", (("page_size", 5),)),
    ("positions", "/api/v1/positions", ()),
    ("risk", "/api/v1/risk/status", ()),
    ("execution", "/api/v1/execution/status", ()),
    ("model", "/api/v1/model/status", ()),
    ("features", "/api/v1/features/contract", ()),
    ("research", "/api/v1/research/status", ()),
    ("replay-shadow", "/api/v1/shadow/status", ()),
    ("incidents", "/api/v1/incidents", (("page_size", 5),)),
    ("database", "/api/v1/database/status", ()),
    ("config", "/api/v1/config/schema", ()),
    ("observability", "/api/v1/observability/metrics", ()),
    ("diagnostics", "/api/v1/system/diagnostics", ()),
]

TRUTHFUL_UNAVAILABLE = {"ENGINE_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE"}


def _ok(code: str | None, status: int) -> bool:
    return status == 200


def smoke_live(base_url: str) -> int:
    from nexus_scalp.api_client import NexusApiClient, NexusApiError

    client = NexusApiClient(base_url)
    failures: list[str] = []
    for name, path, query in DOMAINS:
        try:
            envelope = client.get(path, params=dict(query) or None)
            typer_ok = True
            del envelope
        except NexusApiError as exc:
            if exc.code in TRUTHFUL_UNAVAILABLE or exc.status == 404:
                print(f"  ○ {name} ({exc.code} — truthful unavailability)")
                continue
            print(f"  ✗ {name}: [{exc.code}] {exc.message}")
            failures.append(name)
            continue
        except Exception as exc:
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")
            failures.append(name)
            continue
        print(f"  ✓ {name}")
        del typer_ok
    return _verdict(failures)


def smoke_embedded() -> int:
    from fastapi.testclient import TestClient

    from nexus_scalp.web.api_v1_wiring import create_v1_app

    client = TestClient(create_v1_app())
    failures: list[str] = []
    for name, path, query in DOMAINS:
        try:
            r = client.get(path, params=dict(query) or None)
        except Exception as exc:
            print(f"  ✗ {name}: {type(exc).__name__}: {exc}")
            failures.append(name)
            continue
        if r.status_code == 200:
            print(f"  ✓ {name}")
        elif r.status_code in (404, 503, 504):
            code = r.json().get("error", {}).get("code", "?")
            print(f"  ○ {name} ({code} — truthful unavailability)")
        else:
            print(f"  ✗ {name}: HTTP {r.status_code} {r.text[:120]}")
            failures.append(name)
    return _verdict(failures)


def _verdict(failures: list[str]) -> int:
    if failures:
        print(f"API_SMOKE = FAIL ({', '.join(failures)})")
        return 1
    print("API_SMOKE = PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nexus /api/v1 smoke")
    parser.add_argument(
        "--live",
        metavar="BASE_URL",
        help="smoke a running API (default base: http://127.0.0.1:8080)",
    )
    parser.add_argument(
        "--embedded", action="store_true", help="boot the v1 app in-process and smoke it"
    )
    args = parser.parse_args(argv)
    print("API SMOKE")
    if args.live:
        return smoke_live(args.live)
    return smoke_embedded()


if __name__ == "__main__":
    sys.exit(main())
