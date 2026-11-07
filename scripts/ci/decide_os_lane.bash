#!/usr/bin/env bash
# Shared OS-lane decision (CI runner-routing architecture, 2026-11).
#
# Extracted from the old tests-os.yml inline step so the SELF-HOSTED and the
# GITHUB-HOSTED Linux legs run byte-identical lane logic. Reports the same
# mode vocabulary the workflow has always used:
#
#   FULL         = full critical_suite.txt
#   SKEW         = OS-skewed subset (PR fast lane)
#   SKIP-REPORT  = classifier decided nothing to run (required-context
#                  always-report green; installs nothing, runs no tests)
#
# Usage:   scripts/ci/decide_os_lane.bash <platform>      (linux|windows|macos)
# Outputs: mode=<FULL|SKEW|SKIP-REPORT> into $GITHUB_OUTPUT
set -uo pipefail

PLATFORM="${1:-linux}"
MODE="FULL"

case "${GITHUB_EVENT_NAME:-}" in
  pull_request)
    # PR fast shape: every platform uses the skewed subset.
    MODE="SKEW" ;;
  push)
    # CI quota diet (2026-11): the FULL suite is semi-monthly-schedule and
    # full-dispatch only. A main push takes the fast skewed subset on the
    # tiered self-hosted pool, exactly like a PR.
    MODE="SKEW" ;;
  schedule)
    # Semi-monthly health sweep: the only AUTOMATIC FULL run.
    MODE="FULL" ;;
  workflow_dispatch)
    if [ "${NSE_FORCE_FULL_SUITE:-false}" = "true" ]; then MODE="FULL"; else MODE="SKEW"; fi ;;
esac

if [ "$MODE" != "SKIP-REPORT" ]; then
  # Canonical classifier: no Python in the diff -> nothing to run.
  # Fail-open on uncertainty (empty diff / classifier error => run).
  # Every git probe below is ||-guarded: the default checkout has no local
  # origin/<base> ref, and bash -e/pipefail made an unresolvable
  # `git diff main...HEAD` (exit 128) kill this step BEFORE mode was written.
  PY=python; command -v python >/dev/null 2>&1 || PY=python3
  BASE="${GITHUB_BASE_REF:-}"
  NAMES=""
  if [ -n "$BASE" ]; then
    git fetch -q --no-tags origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" || true
    NAMES=$(git diff --name-only "origin/$BASE...HEAD" 2>/dev/null | tr '\n' ' ' || true)
  fi
  if [ -z "${NAMES// /}" ]; then
    BEFORE="${GITHUB_EVENT_BEFORE:-}"
    for REF in "$BEFORE...HEAD" "HEAD~1...HEAD" "HEAD^...HEAD"; do
      case "$REF" in "...HEAD") continue;; esac
      NAMES=$(git diff --name-only "$REF" 2>/dev/null | tr '\n' ' ' || true)
      [ -n "${NAMES// /}" ] && break
    done
  fi
  if [ -z "${NAMES// /}" ]; then
    # every discovery failed => fail OPEN (force the python lane)
    NAMES="src/force_python_lane.py"
  fi
  if command -v "$PY" >/dev/null 2>&1 && [ -f scripts/ci/classify_changes.py ]; then
    LANES=$("$PY" scripts/ci/classify_changes.py --files "$NAMES" 2>/dev/null || echo '{}')
    if ! echo "$LANES" | grep -Eq '"python": *true'; then
      MODE="SKIP-REPORT"
    fi
  fi
fi

# Optional input passthrough for the dispatch shape (tests-os.yml).
if [ "${GITHUB_EVENT_NAME:-}" = "workflow_dispatch" ] && [ "${NSE_FORCE_FULL_SUITE:-false}" = "true" ]; then
  MODE="FULL"
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "mode=$MODE" >> "$GITHUB_OUTPUT"
fi
echo "os-lane: platform=$PLATFORM mode=$MODE event=${GITHUB_EVENT_NAME:-unknown}"
