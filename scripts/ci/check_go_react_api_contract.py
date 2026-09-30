#!/usr/bin/env python3
"""Static React <-> Go API contract checker.

This gate does not execute business mutations. It verifies that every literal
or template API path used by the React transport facade maps to an actual Go
route operation with the same HTTP method.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

CALL_HEAD_RE = re.compile(
    r"\b(?P<fn>getV1|getLegacy|send|sendV1|drop|raw)\s*"
    r"(?:<[^;()\n]+>)?\s*\(",
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


def find_matching_paren(source: str, open_pos: int) -> int:
    """Find a call's closing parenthesis while respecting quoted arguments."""
    depth = 1
    quote: str | None = None
    escaped = False
    i = open_pos + 1
    while i < len(source):
        ch = source[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"" or ch == chr(96):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced transport call")


def split_top_level_args(args: str) -> list[str]:
    parts: list[str] = []
    start = 0
    paren = bracket = brace = 0
    quote: str | None = None
    escaped = False

    for i, ch in enumerate(args):
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "'\"" or ch == chr(96):
            quote = ch
        elif ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
        elif ch == "[":
            bracket += 1
        elif ch == "]":
            bracket -= 1
        elif ch == "{":
            brace += 1
        elif ch == "}":
            brace -= 1
        elif ch == "," and paren == bracket == brace == 0:
            parts.append(args[start:i].strip())
            start = i + 1
    parts.append(args[start:].strip())
    return parts


def first_literal_arg(args: str) -> str | None:
    args = args.lstrip()
    if not args or args[0] not in "'\"" and args[0] != chr(96):
        return None
    quote = args[0]
    escaped = False
    for i in range(1, len(args)):
        ch = args[i]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == quote:
            return args[1:i]
    return None


def normalize_template_path(path: str) -> str:
    """Normalize React template paths to Go route shapes."""
    out: list[str] = []
    i = 0
    while i < len(path):
        if path.startswith("$" + "{", i):
            depth = 1
            j = i + 2
            quote: str | None = None
            escaped = False
            while j < len(path):
                ch = path[j]
                if quote is not None:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == quote:
                        quote = None
                else:
                    if ch in "'\"" or ch == chr(96):
                        quote = ch
                    elif ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            break
                j += 1
            if depth != 0:
                return path

            # A template expression after a slash is a path parameter.
            # Other expressions are normally query builders/optional query
            # strings and are not part of the backend route shape.
            if out and out[-1] == "/":
                out.append("{param}")
            elif out and out[-1] == "?":
                out.pop()
            i = j + 1
            continue

        if path[i] == "?" and "$" + "{" in path[i:]:
            break
        out.append(path[i])
        i += 1

    normalized = "".join(out).rstrip("?") or "/"
    # Go route templates use {param_name}; React template literals use
    # ${expression}. Compare both as the same structural path parameter.
    return re.sub(r"\{[^}]+\}", "{param}", normalized)


def react_calls() -> list[dict[str, Any]]:
    root = ROOT / "frontend" / "src"
    calls: list[dict[str, Any]] = []

    for path in sorted(root.rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        source = path.read_text(encoding="utf-8")

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

        for match in CALL_HEAD_RE.finditer(source):
            close = find_matching_paren(source, match.end() - 1)
            args = source[match.end() : close]
            path_value = first_literal_arg(args)
            if path_value is None or not path_value.startswith("/api"):
                continue

            fn = match.group("fn")
            lower_fn = fn.lower()
            if lower_fn in {"getv1", "getlegacy"}:
                method = "GET"
            elif lower_fn == "drop":
                method = "DELETE"
            else:
                method = "POST"

            if lower_fn in {"send", "sendv1"}:
                args_list = split_top_level_args(args)
                if len(args_list) >= 3:
                    explicit = first_literal_arg(args_list[2])
                    if explicit in {"POST", "PUT", "PATCH", "DELETE", "GET"}:
                        method = explicit

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

    go_shapes = {(method, normalize_template_path(path)) for method, path in routes}
    missing = []
    for call in calls:
        key = (call["method"], normalize_template_path(call["path"]))
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
