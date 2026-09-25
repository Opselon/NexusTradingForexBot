"""Generate the Go route registry from the live FastAPI route dump.

This is the anti-drift guarantee: Go's ServeMux is seeded from the SAME
resolved route table Python actually serves, so a route present on one side
and missing on the other is a detected mismatch rather than a silent 404.

Excludes HEAD/OPTIONS (FastAPI auto-generates those per GET) and MOUNT
(static file mounts, handled separately).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent
SRC = Path("C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/routes_ground_truth.json")
OUT = REPO / "go-api" / "internal" / "api" / "routes" / "table_gen.go"

# Already implemented by hand (Phase A/B); keep their dedicated handlers.
HANDWRITTEN = {
    ("GET", "/api/v1/system/health"),
    ("GET", "/api/v1/system/status"),
    ("GET", "/api/v1/system/readiness"),
    ("GET", "/api/v1/system/version"),
    ("GET", "/api/v1/system/runtime"),
    ("GET", "/api/v1/system/capabilities"),
    ("GET", "/api/v1/system/workers"),
    ("GET", "/api/v1/system/diagnostics"),
    ("POST", "/api/v1/system/diagnostics/run"),
    ("POST", "/api/v1/system/refresh"),
    ("GET", "/api/v1/research/status"),
    ("GET", "/api/v1/research/strategies"),
    ("GET", "/api/v1/research/strategies/{strategy_id}"),
    ("GET", "/api/v1/research/runs"),
    ("GET", "/api/v1/research/datasets"),
    ("GET", "/api/v1/risk/status"),
    ("GET", "/api/v1/risk/summary"),
    ("GET", "/api/v1/runtime/mode"),
    ("GET", "/api/v1/runtime/freshness"),
    ("GET", "/api/v1/runtime/shutdown"),
}


def go_path_pattern(path: str) -> str:
    """FastAPI {x} -> Go ServeMux {x}; {x:path} is multi-segment and must be
    handled with a trailing-wildcard pattern instead."""
    if "{path}" in path or ":path}" in path:
        return path  # registered as a prefix pattern by the caller
    return path


def main() -> None:
    recs = json.loads(SRC.read_text(encoding="utf-8"))
    if isinstance(recs, dict):
        recs = recs.get("routes", recs)

    rows = []
    seen = set()
    for r in recs:
        method, path, surface = r["method"], r["path"], r.get("surface", "")
        if method in ("HEAD", "OPTIONS", "MOUNT"):
            continue
        if (method, path) in HANDWRITTEN:
            continue
        if (method, path) in seen:
            continue
        seen.add((method, path))
        # collapse to Go-safe identifiers for the v1 flag
        rows.append({"method": method, "path": path, "surface": surface,
                     "isV1": path.startswith("/api/v1")})

    print(f"generated rows: {len(rows)}")

    def ident(p: str) -> str:
        s = re.sub(r"[^0-9a-zA-Z]+", "_", p)
        s = s.strip("_")
        if s and s[0].isdigit():
            s = "r_" + s
        return s

    # Group by method for readable output.
    lines = [
        "// Code generated from the live FastAPI route dump. DO NOT EDIT.",
        "//",
        "// Source: routes_ground_truth.json (create_app + create_v1_app).",
        "// HEAD/OPTIONS are omitted (FastAPI auto-generates them per GET and",
        "// net/http serves HEAD for free); MOUNT entries are static file mounts.",
        "//",
        "// Every entry is a pure pass-through to the Python runtime. This table",
        "// exists so that the Go surface cannot silently drift from Python's:",
        "// a route present on one side and absent on the other is caught by the",
        "// parity harness, which enumerates exactly this table.",
        "package routes",
        "",
        "// routeEntry is one proxied operation.",
        "type routeEntry struct {",
        "\tMethod string",
        "\tPath   string",
        "\tIsV1   bool",
        "}",
        "",
        "// generatedRoutes is the full proxied surface minus the hand-written",
        "// Phase A/B handlers (see table_handwritten.go).",
        "var generatedRoutes = []routeEntry{",
    ]
    for r in rows:
        lines.append(f'\t{{Method: "{r["method"]}", Path: "{r["path"]}", IsV1: {str(r["isV1"]).lower()}}},')
    lines.append("}")
    lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
