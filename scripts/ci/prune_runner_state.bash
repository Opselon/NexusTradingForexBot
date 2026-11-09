#!/usr/bin/env bash
# Prune stale runner-persistent state (automatic, age-based).
#
# A self-hosted runner accumulates state across jobs: hash-bound venvs
# (~/.cache/nse-venvs), warm linter/type caches (~/.cache/nse-ci), and
# /tmp test leftovers (tmp* dirs, pytest sessions). None of it is created
# by hand — tools create it on demand — but nothing ever removed it, so a
# long-lived runner grows unbounded and a stale venv can shadow a fresh
# one. This script is the automatic counterpart: it runs at the start of
# every lane (via the setup action / OS lane) and removes only state that
# cannot belong to a live job:
#   * hash-bound venvs and nse-ci caches older than 30 days (a runner
#     executes one job at a time, and a venv in current use is touched
#     by the install step, so its mtime is fresh);
#   * /tmp tmp* dirs and pytest sessions older than 24h (a concurrent
#     job's files are minutes old; the Dubai box held 107 tmp* dirs,
#     1687 model.pt files and 3.5G of pytest sessions, 2026-11-09).
# It never touches the CURRENT venv, the uv wheel cache (a proper cache
# with its own eviction — pruning it would only slow installs), or
# anything younger than the thresholds. Safe to run concurrently: every
# deletion is age-gated, and failures are warnings, never fatal.
set -uo pipefail

prune_older_than() {
  # prune_older_than <dir> <days> <glob...>: delete matching entries
  # (files and dirs) whose mtime is older than <days> days.
  local dir="$1" days="$2"; shift 2
  [ -d "$dir" ] || return 0
  for pattern in "$@"; do
    # -mindepth 1: never delete $dir itself. -prune is unnecessary:
    # find deletes the matched dir and does not descend into it.
    find "$dir" -mindepth 1 -maxdepth 1 -name "$pattern" -mtime "+$days" -exec rm -rf -- {} + 2>/dev/null || true
  done
}

# Hash-bound venvs (keyed by lockfile hash; a new lock mints a new dir,
# so old ones are dead weight) and warm linter/type caches.
prune_older_than "$HOME/.cache/nse-venvs" 30 ".venv-ci-*"
prune_older_than "$HOME/.cache/nse-ci" 30 "*"

# /tmp test leftovers: tmp* dirs (pytest/tmp_path + engine clones) and
# pytest session dirs. 24h, not 30d: these are per-run artifacts, and a
# live job's files are minutes old.
prune_older_than "/tmp" 1 "tmp*"
prune_older_than "/tmp" 1 "pytest-*"
prune_older_than "/tmp" 1 ".pytest_cache*"

echo "runner state pruned (venvs/caches >30d, /tmp artifacts >24h)"
