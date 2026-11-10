#!/usr/bin/env bash
# Prune stale runner-persistent state (automatic, age-based — with one
# exception, below).
#
# A self-hosted runner accumulates state across jobs: venvs
# (~/.cache/nse-venvs), warm linter/type caches (~/.cache/nse-ci), and
# /tmp test leftovers (tmp* dirs, pytest sessions). None of it is created
# by hand — tools create it on demand — but nothing ever removed it, so a
# long-lived runner grows unbounded and a stale venv can shadow a fresh
# one. This script is the automatic counterpart: it runs at the start of
# every lane (via the setup action / OS lane) and removes only state that
# cannot belong to a live job:
#   * OBSOLETE hash-bound venv dirs (any age — see below);
#   * other venvs and nse-ci caches older than 30 days (a runner
#     executes one job at a time, and a venv in current use is touched
#     by the install step, so its mtime is fresh);
#   * /tmp tmp* dirs and pytest sessions older than 24h (a concurrent
#     job's files are minutes old; the Dubai box held 107 tmp* dirs,
#     1687 model.pt files and 3.5G of pytest sessions, 2026-11-09).
# It never touches the CURRENT venv, the uv wheel cache (a proper cache
# with its own eviction — pruning it would only slow installs), or
# anything younger than the thresholds. Safe to run concurrently: every
# deletion is age-gated (except the obsolete-venv rule, which matches
# only dirs no job can reference), and failures are warnings, never
# fatal.
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

# OBSOLETE hash-bound venvs (2026-11-10): the setup action switched to a
# stable venv (`...-.venv-ci-<python>`) synced with `uv pip sync`, so the
# old lock-hash dirs (`...-.venv-ci-<16 hex>-<python>`, one ~6 GB dir per
# lock regen) are dead weight at ANY age — a new lock no longer mints a
# new dir, and no job references them. Delete them unconditionally: the
# 16-hex pattern cannot match the live stable venv (`...-.venv-ci-3.11`).
# This is what unblocked Dubai (2026-11-10): its disk filled up with one
# venv per lock regen while the sync into the new stable venv needed the
# same ~6 GB again, and both Quality jobs died with "No space left on
# device". A runner executes one job at a time, so nothing is using them.
if [ -d "$HOME/.cache/nse-venvs" ]; then
  find "$HOME/.cache/nse-venvs" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*\.venv-ci-[0-9a-f]{16}-.*' -exec rm -rf -- {} + 2>/dev/null || true
fi

# Other venvs (the live stable one) and warm linter/type caches older
# than 30 days.
prune_older_than "$HOME/.cache/nse-venvs" 30 ".venv-ci-*"
prune_older_than "$HOME/.cache/nse-ci" 30 "*"

# /tmp test leftovers: tmp* dirs (pytest/tmp_path + engine clones) and
# pytest session dirs. 24h, not 30d: these are per-run artifacts, and a
# live job's files are minutes old.
prune_older_than "/tmp" 1 "tmp*"
prune_older_than "/tmp" 1 "pytest-*"
prune_older_than "/tmp" 1 ".pytest_cache*"

echo "runner state pruned (obsolete hash-bound venvs, venvs/caches >30d, /tmp artifacts >24h)"
