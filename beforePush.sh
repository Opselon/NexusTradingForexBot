#!/usr/bin/env python3
"""Canonical local pre-push validation gate (Linux/macOS/Bash wrapper).

ONE canonical implementation lives in scripts/ci/check_local.py; the
platform scripts (this file and beforePush.ps1) are thin wrappers that
resolve the repository Python interpreter and delegate. The validation
LOGIC (ruff lint/format, mypy, critical-suite manifest, push contract)
is intentionally NOT duplicated here — see docs/LOCAL_QUALITY_GATE.md.

Optional passthrough args are forwarded verbatim:
    ./beforePush.sh --all      # whole-tree scope
    ./beforePush.sh --staged   # staged files only
    ./beforePush.sh --fix      # safe mechanical fixes
    ./beforePush.sh --json     # machine-readable envelope
    ./beforePush.sh --prepush  # canonical push-time contract (default
                               # behavior is already push-scope)
Exit codes mirror check_local.py: 0 pass, 1 failure, 2 usage/config error.
"""

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve repo root = directory containing this script (portable; no
# platform-specific venv path is hard-coded).
# ---------------------------------------------------------------------------
SCRIPT_PATH="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_PATH" ]; do
    SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
REPO_ROOT="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"

# On MSYS/git-bash, $REPO_ROOT is a POSIX path (/c/Users/...). When the
# resolved interpreter is a native Windows executable, hand it a native
# path instead — MSYS path conversion is disabled for command arguments
# that already look like paths, and C:/... form is valid for both worlds.
if [ -n "${MSYSTEM:-}" ] && [ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]; then
    REPO_ROOT="$(cygpath -m "$REPO_ROOT")"
fi

# ---------------------------------------------------------------------------
# Resolve the repository Python: repo venv (POSIX layout first, then the
# Windows layout for WSL/git-bash venvs), else python3 from PATH. The
# canonical runner picks the right tool modules from whichever interpreter
# hosts it (scripts/ci/check_local.py::_tool_cmd).
# ---------------------------------------------------------------------------
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    for candidate in \
        "$REPO_ROOT/.venv/bin/python" \
        "$REPO_ROOT/.venv/Scripts/python.exe" \
        "$(command -v python3 || true)"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
fi
if [ -z "$PYTHON_BIN" ]; then
    echo "beforePush.sh: no repository Python found (.venv missing, python3 absent)" >&2
    exit 2
fi

echo "[beforePush] repo: $REPO_ROOT"
echo "[beforePush] python: $PYTHON_BIN"
echo "[beforePush] delegating to canonical gate: scripts/ci/check_local.py --prepush $*"
exec "$PYTHON_BIN" "$REPO_ROOT/scripts/ci/check_local.py" --prepush "$@"
