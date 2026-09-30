#!/usr/bin/env python3
"""Static React <-> Go API contract checker."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKTICK = chr(96)

CALL_RE = re.compile(
    r"\b(?P<fn>getV1|getLegacy|send)\s*(?:<[^;()\n]+>)?\s*\(\s*(?:"
    r"(?P<double>\"(?P<double_path>/api[^\"\n]*)\")|"
    r"(?P<single>'(?P<single_path>/api[^'\n]*)')|"
    + re.escape(BACKTICK)
    + r"(?P<template_path>/api[^\n]*)"
    + re.escape(BACKTICK)
    + r")",
    re.IGNORECASE,
)


def go_routes() -> set[tuple[str, str]]:
    patterns = (
        re.compile(r'\{Method:\s*"([A-Z]+)"\s*,\s*Path:\s*"([^"]+)"'),
        re.compile(r'Handle(?:Func)?\(\s*"([A-Z]+)"\s*,\s*"([^"]+)"'),
    )
    files = (
        ROOT / "go-api/internal/api/routes/table_gen.go",
        ROOT / "go-api/internal/api/routes/table_handwritten.go",
        ROOT / "go-api/internal/api/routes/routes.go",
    )
    routes: set[tuple[str, str]] = set()
    for path in files:
        source = path.read_text(encoding="utf-8")
        for rx in patterns:
            routes.update(rx.findall(source))
    return routes


def normalize_path(path: str) -> str:
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]+\}", "{param}", path)
    path = re.sub(r"\{[^}]+\}", "{param}", path)
    return path


def react_calls() -> list[dict[str, Any]]:
    root = ROOT / "frontend" / "src"
    calls: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        source = path.read_text(encoding="utf-8")

        # The realtime client consumes the centralized ENDPOINTS map rather
        # than calling getV1/getLegacy/send directly. Include those canonical
        # backend paths in the same contract set so SSE/API backbone routes
        # cannot drift away from Go.
        if path.as_posix().endswith("frontend/src/core/config.ts"):
            endpoint_re = re.compile(r'\b\w+\s*:\s*["\'](/api[^"\']+)["\']')
            for endpoint in endpoint_re.findall(source):
                calls.append(
                    {
                        "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                        "function": "ENDPOINTS",
                        "method": "GET",
                        "path": endpoint,
                    }
                )

        for match in CALL_RE.finditer(source):
            fn = match.group("fn").lower()
            path_value = (
                match.group("double_path")
                or match.group("single_path")
                or match.group("template_path")
            )
            method = "GET" if fn in {"getv1", "getlegacy"} else "POST"
            tail = source[match.end() : match.end() + 500]
            explicit = re.search(
                r",\s*['\"](GET|POST|PUT|PATCH|DELETE)['\"]",
                tail,
            )
            if explicit:
                method = explicit.group(1)
            calls.append(
                {
                    "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                    "function": fn,
                    "method": method,
                    "path": path_value,
                }
            )
    return calls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    routes = go_routes()
    calls = react_calls()
    if len(routes) < 400:
        raise SystemExit(
            f"Go route inventory unexpectedly small: {len(routes)}"
        )
    if not calls:
        raise SystemExit("No React API calls were discovered under frontend/src")

    go_shapes = {(method, normalize_path(path)) for method, path in routes}
    missing = []
    for call in calls:
        key = (call["method"], normalize_path(call["path"]))
        if key not in go_shapes:
            missing.append(call)

    result = {
        "status": "PASS" if not missing else "FAIL",
        "go_route_operations": len(routes),
        "react_api_calls": len(calls),
        "missing_from_go": missing,
        "go_route_shapes": len(go_shapes),
    }

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
