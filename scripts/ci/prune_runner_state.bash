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
# It never touches the CURRENT venv or anything younger than the
# thresholds. The uv wheel cache is managed with uv's own tool
# (`uv cache prune` below — never rm inside the cache: uv forbids
# touching it directly): prune removes unused entries and keeps what
# the current lock needs. Safe to run concurrently: every
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
# CORRECTION (2026-11-10): this rule did NOT unblock Dubai — the disk
# was freed by hand (16 GB uv cache + 3.6 GB pytest temp dirs), because
# the prune never touched the uv cache and the pytest temp top dir
# stayed fresh. The obsolete-venv rule is still correct hygiene, but
# the real unblocker was the uv-cache prune below. A runner executes
# one job at a time, so nothing is using them.
if [ -d "$HOME/.cache/nse-venvs" ]; then
  find "$HOME/.cache/nse-venvs" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*\.venv-ci-[0-9a-f]{16}-.*' -exec rm -rf -- {} + 2>/dev/null || true
fi

# uv wheel cache (2026-11-10): the cache grew to 16 GB on Dubai and
# filled the disk — the prune never touched it before, and every
# --refresh retry re-downloaded the torch/nvidia wheels into it.
# `uv cache prune` is the supported tool (never rm inside the cache:
# uv forbids it) and removes unused entries while keeping what the
# current lock needs. Runs at the start of the setup step, when no uv
# process is running (a runner executes one job at a time), so no
# --force. Failure-proof: if uv is not on PATH yet, skip silently.
if command -v uv >/dev/null 2>&1; then
  uv cache prune 2>/dev/null || true
fi

# Disk-space guard (2026-11-10): if the disk is nearly full, wipe the
# uv wheel cache outright with uv's own tool (`uv cache clean` —
# never rm inside the cache). The cache is regenerable and the venv
# is intact, so the next sync downloads only packages missing from
# the venv (a delta, never a full reinstall) — wiping the cache can
# never force a full re-download while the venv is warm. This is the
# emergency valve that keeps a long-lived runner from hitting the
# ceiling again after many runs: `uv cache prune` handles ordinary
# growth, this handles the pathological case (a 16 GB cache filled
# the 40 GB Dubai disk, 2026-11-10). 5 GB floor: a full install needs
# ~2.5 GB of downloads plus extraction headroom. Space-gated only —
# it never fires on a healthy disk — and failure-proof.
if command -v uv >/dev/null 2>&1; then
  FREE_KB="$(df -k --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')"
  if [ -n "${FREE_KB}" ] && [ "${FREE_KB}" -lt 5242880 ]; then
    echo "disk nearly full (${FREE_KB} KB free < 5 GB): wiping the uv wheel cache (venv intact, next sync is a delta)"
    uv cache clean 2>/dev/null || true
  fi
fi

# Other venvs (the live stable one) and warm linter/type caches older
# than 30 days.
prune_older_than "$HOME/.cache/nse-venvs" 30 ".venv-ci-*"
prune_older_than "$HOME/.cache/nse-ci" 30 "*"

# /tmp test leftovers: tmp* dirs (pytest/tmp_path + engine clones) and
# pytest session dirs. 24h, not 30d: these are per-run artifacts, and a
# live job's files are minutes old. NOTE: /tmp/pytest-of-github-runner
# itself stays fresh (every session touches it), so its SESSION subdirs
# must be pruned by age too — the top-dir rule alone never fired, and
# the dir held 3.6 GB of stale Nov-9 sessions on Dubai (2026-11-10).
prune_older_than "/tmp" 1 "tmp*"
prune_older_than "/tmp" 1 "pytest-*"
prune_older_than "/tmp" 1 ".pytest_cache*"
prune_older_than "/tmp/pytest-of-github-runner" 1 "*"

echo "runner state pruned (obsolete hash-bound venvs, uv cache prune, disk guard if <5 GB free, venvs/caches >30d, /tmp artifacts + pytest sessions >24h)"
