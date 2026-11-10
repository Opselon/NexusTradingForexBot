#!/usr/bin/env bash
# Shared OS-lane decision (CI runner-routing architecture, 2026-11).
#
# Extracted from the old tests-os.yml inline step so the SELF-HOSTED and the
# GITHUB-HOSTED Linux legs run byte-identical lane logic. Reports the same
# mode vocabulary the workflow has always used:
#
#   FULL         = full critical_suite.txt
#   SKEW         = fast lane: C-planned targeted subset (dispatch-only
#                  since 2026-11-09 — see below; the C17 engine selects
#                  tests from the diff; no fixed list runs there).
#                  Windows still runs the fixed skew subset under the
#                  same mode name until its C-engine build is validated
#                  (step-by-step: Linux first).
#   SKIP-REPORT  = classifier decided nothing to run (required-context
#                  always-report green; installs nothing, runs no tests)
#
# OWNERSHIP (design correction, 2026-11-09 — simplify): ONE execution
# per PR. The Quality lane (ci.yml quality-tests) owns test execution on
# PRs: it runs the C-planned selection, with coverage, on the routed
# runner. The OS Linux legs used to re-run the SAME selection back-to-back
# (on this PR: the same 353 files in Quality AND in the OS lane — 24 min
# + 17 min for one PR). They now SKIP on pull_request / push / schedule.
# The expanded/full suite runs ONCE: on schedule it runs in Quality;
# on explicit dispatch it runs here (FULL). SKEW survives only as the
# manual dispatch shape (targeted subset on demand), never automatic.
#
# Windows / macOS (2026-11-09): those platforms have NO Quality lane —
# their workflows (tests-windows.yml, tests-macos.yml) are dispatch-only
# and never run on PRs (the `Py Tests (windows-latest)` /
# `Py Tests (macos-latest)` checks on a PR are routing notices emitted
# by tests-os.yml's ctx jobs on ubuntu-latest, not test execution — no
# Windows/macOS runner is borrowed on a PR). Their cross-platform
# coverage comes from a SEMI-MONTHLY schedule (1st & 15th, 07:17 UTC,
# the same cadence as tests-os.yml): on schedule they run FULL HERE,
# unconditionally — a periodic health sweep, not a change-driven run,
# so the classifier must not downgrade it.
#
# Usage:   scripts/ci/decide_os_lane.bash <platform>      (linux|windows|macos)
# Outputs: mode=<FULL|SKEW|SKIP-REPORT> into $GITHUB_OUTPUT
set -uo pipefail

PLATFORM="${1:-linux}"
MODE="FULL"

case "${GITHUB_EVENT_NAME:-}" in
  pull_request)
    # ONE execution per PR (2026-11-09): Quality owns test execution.
    # This leg must not re-run the same selection back-to-back.
    MODE="SKIP-REPORT" ;;
  push)
    # Same: Quality owns the push diff; this leg runs on dispatch only.
    MODE="SKIP-REPORT" ;;
  schedule)
    # Linux: the FULL suite runs ONCE on schedule — in the Quality lane.
    # This leg does not re-run it.
    # Windows / macOS: no Quality lane exists for those platforms, so
    # their semi-monthly sweep runs HERE (FULL, unconditional — a
    # periodic health sweep, not a change-driven run, so the classifier
    # below must not downgrade it; SCHEDULE_FULL bypasses it).
    if [ "$PLATFORM" = "linux" ]; then
      MODE="SKIP-REPORT"
    else
      MODE="FULL"
      SCHEDULE_FULL=true
    fi ;;
  workflow_dispatch)
    if [ "${NSE_FORCE_FULL_SUITE:-false}" = "true" ]; then MODE="FULL"; else MODE="SKEW"; fi ;;
esac

if [ "$MODE" != "SKIP-REPORT" ] && [ "${SCHEDULE_FULL:-false}" != "true" ]; then
  # Canonical classifier: no Python in the diff -> nothing to run.
  # Skipped for a scheduled Windows/macOS FULL sweep: that is a periodic
  # health sweep, not a change-driven run, and the schedule event has no
  # meaningful diff to classify (the last commit's files are not the
  # reason the sweep runs).
  # Fail-open on uncertainty (empty diff / classifier error => run).
  # Every git probe below is ||-guarded: the default checkout has no local
  # origin/<base> ref, and bash -e/pipefail made an unresolvable
  # `git diff main...HEAD` (exit 128) kill this step BEFORE mode was written.
  PY=python; command -v python >/dev/null 2>&1 || PY=python3
  BASE="${GITHUB_BASE_REF:-}"
  NAMES=""
  if [ -n "$BASE" ]; then
    # Shallow checkouts (actions/checkout fetch-depth 1) cannot compute the
    # PR merge-base, so the diff below would fail and the classifier would
    # fail OPEN (run everything). Fetch the base branch bloblessly first
    # (commits+trees only, ~5 MiB — the same trick ci.yml uses for the
    # planner); the SKEW step's own fetch covers dispatch, which has no base.
    git fetch -q --filter=blob:none --no-tags origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" 2>/dev/null \
      || git fetch -q --no-tags origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" 2>/dev/null || true
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
