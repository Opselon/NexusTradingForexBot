"""scripts/dev/api_openapi_diff.py — contract drift detection between snapshots.

Usage:
    python scripts/dev/api_openapi_diff.py OLD.json NEW.json [--fail-on-breaking]

Detected breaking changes:
  - endpoint removed
  - method removed from an endpoint
  - request schema added/changed on an existing method
Non-breaking (report only): new endpoints, summary/tag edits.

Exit codes: 0 = compatible (or no change), 2 = breaking changes found,
1 = usage/IO error. CI can gate on exit code 2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "paths" not in data:
        raise ValueError(f"{path} is not an api_openapi_snapshot artifact")
    return data


def diff(old: dict, new: dict) -> tuple[list[str], list[str]]:
    breaking: list[str] = []
    notes: list[str] = []
    old_paths: dict = old.get("paths", {})
    new_paths: dict = new.get("paths", {})
    for path in sorted(set(old_paths) - set(new_paths)):
        breaking.append(f"endpoint removed: {path}")
    for path in sorted(set(new_paths) - set(old_paths)):
        notes.append(f"endpoint added: {path}")
    for path in sorted(set(old_paths) & set(new_paths)):
        o, n = old_paths[path], new_paths[path]
        for method in sorted(set(o) - set(n)):
            breaking.append(f"method removed: {method.upper()} {path}")
        for method in sorted(set(n) - set(o)):
            notes.append(f"method added: {method.upper()} {path}")
        for method in sorted(set(o) & set(n)):
            if o[method].get("request_schema") != n[method].get("request_schema"):
                breaking.append(f"request schema changed: {method.upper()} {path}")
    return breaking, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff two api_openapi_snapshot artifacts")
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument(
        "--fail-on-breaking", action="store_true", help="exit 2 on breaking changes"
    )
    args = parser.parse_args(argv)
    try:
        old = load(args.old)
        new = load(args.new)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 1
    breaking, notes = diff(old, new)
    for item in breaking:
        print(f"BREAKING: {item}")
    for item in notes:
        print(f"note:     {item}")
    if not breaking and not notes:
        print("no contract changes")
    if breaking and args.fail_on_breaking:
        print(f"OPENAPI_DIFF = BREAKING ({len(breaking)})")
        return 2
    print("OPENAPI_DIFF = " + ("BREAKING" if breaking else "COMPATIBLE"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
