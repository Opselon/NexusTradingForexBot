#!/usr/bin/env python3
"""Reproducible Tailwind CSS build for the Nexus Scalp Engine (NSE) Control Center.

WHY THIS FILE EXISTS
--------------------
The NSE Web UI is a *buildless* vanilla-JS SPA (see ``agents/skill.md`` and the
``js-tests.yml`` workflow). There is **no bundler** (no webpack / vite / esbuild /
parcel), and at runtime the browser loads the already-compiled ``Web/tailwind.css``
directly from the FastAPI server (route ``/tailwind.css`` in
``src/nexus_scalp/web/server.py``).

Node.js is therefore NOT a runtime dependency of the engine or the Web UI. It is
used only at **build/dev/test time** to:

  1. Compile Tailwind (this script).
  2. Run the ``node --check`` syntax gate + ``tests/js/*.test.js`` unit tests
     (see ``.github/workflows/js-tests.yml``).

The previous "build" recipe lived only in a BUG-047 commit message and prose docs,
which made it non-reproducible and drift-prone. This script makes it canonical and
idempotent, and rebuilds the exact same artifact the runtime serves.

WHAT THIS SCRIPT DOES
---------------------
Locates ``node`` / ``npx`` on ``PATH`` and runs the Tailwind CLI to compile
``Web/tailwind_input.css`` -> ``Web/tailwind.css`` using ``tailwind.config.js``.
If Tailwind is not installed locally it is fetched on the fly via ``npx`` (which
downloads ``tailwindcss`` into an ephemeral cache, NOT a committed ``node_modules``).

Exit codes mirror the engine convention:
  0  success
  3  environment error (Node.js not available)
  1  build/compile failure
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TAILWIND_CONFIG = REPO_ROOT / "tailwind.config.js"
TAILWIND_INPUT = REPO_ROOT / "Web" / "tailwind_input.css"
TAILWIND_OUTPUT = REPO_ROOT / "Web" / "tailwind.css"
NODE_VERSION_REQUIRED = (18, 0)

EXIT_OK = 0
EXIT_RUNTIME_ENV = 3
EXIT_BUILD_FAILED = 1


def find_executable(name: str) -> str | None:
    return shutil.which(name)


def node_version(node: str) -> tuple[int, int, int] | None:
    try:
        out = subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    cleaned = "".join(ch if ch.isdigit() or ch == "." else " " for ch in out)
    parts: list[int] = []
    for part in cleaned.split():
        for sub in part.split("."):
            if not sub:
                continue
            try:
                parts.append(int(sub))
            except ValueError:
                break
        break  # only the first dotted group (e.g. 24.18.0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])  # type: ignore[return-value]


def build(minify: bool = True) -> int:
    if not TAILWIND_CONFIG.exists():
        print(f"[tailwind] MISSING config: {TAILWIND_CONFIG}", file=sys.stderr)
        return EXIT_BUILD_FAILED
    if not TAILWIND_INPUT.exists():
        print(f"[tailwind] MISSING input: {TAILWIND_INPUT}", file=sys.stderr)
        return EXIT_BUILD_FAILED

    node = find_executable("node")
    if node is None:
        print(
            "[tailwind] Node.js is required to build the frontend stylesheet.\n"
            "  Required: Node.js >= 18\n"
            "  Detected: Node.js not installed / not on PATH\n"
            "  Next action: install Node.js (https://nodejs.org) or run the engine "
            "without rebuilding (the committed Web/tailwind.css is used as-is).",
            file=sys.stderr,
        )
        return EXIT_RUNTIME_ENV

    ver = node_version(node)
    if ver is None or ver < NODE_VERSION_REQUIRED:
        got = ".".join(map(str, ver)) if ver else "unknown"
        print(
            f"[tailwind] Node.js >= 18 required to build, detected {got}.",
            file=sys.stderr,
        )
        return EXIT_RUNTIME_ENV

    # Build the CLI command. Prefer a locally installed tailwindcss binary; fall
    # back to `npx` which fetches it ephemerally (no committed node_modules).
    # Pin to v3 (the BUG-047 recipe uses the v3 CLI: -c/-i/-o/--minify). Tailwind
    # v4 changed the CLI surface, so an unpinned `npx tailwindcss` would break.
    TAILWIND_VERSION = "3"
    cmd: list[str]
    local_bin = REPO_ROOT / "node_modules" / ".bin" / "tailwindcss"
    if local_bin.exists():
        cmd = [str(local_bin)]
    else:
        npx = find_executable("npx")
        if npx is None:
            print("[tailwind] `npx` not found; cannot fetch tailwindcss.", file=sys.stderr)
            return EXIT_RUNTIME_ENV
        cmd = [npx, "--yes", f"tailwindcss@{TAILWIND_VERSION}"]

    cmd += [
        "-c",
        str(TAILWIND_CONFIG),
        "-i",
        str(TAILWIND_INPUT),
        "-o",
        str(TAILWIND_OUTPUT),
    ]
    if minify:
        cmd.append("--minify")

    print(f"[tailwind] running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    except OSError as exc:
        print(f"[tailwind] failed to launch build: {exc}", file=sys.stderr)
        return EXIT_BUILD_FAILED

    if result.returncode != 0:
        print("[tailwind] build failed (see output above).", file=sys.stderr)
        return EXIT_BUILD_FAILED

    size = TAILWIND_OUTPUT.stat().st_size if TAILWIND_OUTPUT.exists() else -1
    print(f"[tailwind] OK -> {TAILWIND_OUTPUT} ({size} bytes)")
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Web/tailwind.css")
    parser.add_argument("--no-minify", action="store_true", help="emit an unminified stylesheet")
    args = parser.parse_args()
    return build(minify=not args.no_minify)


if __name__ == "__main__":
    raise SystemExit(main())
