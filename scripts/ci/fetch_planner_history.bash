#!/usr/bin/env bash
# Fetch planner history: make a shallow checkout merge-base-ready.
#
# The C planner diffs base...head, so both SHAs must be objects with a
# reachable merge-base. A depth-1 checkout does NOT guarantee that: on a
# virgin runner the blobless SHA fetch can exit 0 yet bring NOTHING (the
# server excludes objects reachable from the shallow merge commit's
# parents, so base/head never arrive), and `git merge-base` then fails —
# tier-1, 2026-11-09: Quality job died in 31s with "merge base
# unreachable after fetch" while tier-2 (deep accumulated history) worked.
# The failure does NOT reproduce on a local virgin clone with the same
# commands and git version, so the step does not trust the fetch's exit
# code: it VERIFIES merge-base and ESCALATES deterministically until the
# graph is present. Unshallowing is the guarantee (it fetches the full
# commit graph); it is a one-time cost per runner because _work persists.
#
# Usage: fetch_planner_history.bash <base-sha> <head-sha>
#   <base-sha> may be empty (dispatch without an explicit base) — then
#   HEAD~1 is used, matching the OS lane's old fallback (the last
#   commit's diff). Exits non-zero only if merge-base is unreachable
#   after every escalation (fail-closed: the planner would rc=3).
set -uo pipefail

BASE="${1:-}"
HEAD="${2:-}"

if [ -z "$HEAD" ]; then
  echo "::error::fetch_planner_history: head SHA is required"
  exit 1
fi
if [ -z "$BASE" ] || [ "$BASE" = "0000000000000000000000000000000000000000" ]; then
  BASE="$(git rev-parse HEAD~1 2>/dev/null || echo "")"
  if [ -z "$BASE" ]; then
    echo "::error::fetch_planner_history: no base SHA and HEAD~1 unresolvable"
    exit 1
  fi
  echo "fetch_planner_history: no base given; using HEAD~1 ($BASE)"
fi

# 1. Cheap path: blobless fetch of the two SHAs (commits+trees, no blobs).
if git fetch --filter=blob:none --no-tags origin "$BASE" "$HEAD" 2>/dev/null; then
  echo "blobless history fetched"
else
  echo "::warning::blobless fetch unsupported; falling back to full fetch"
  git fetch --no-tags origin "$BASE" "$HEAD"
fi

# 2. Verify. If merge-base is unreachable, the fetch was a no-op (see
#    header) — escalate to unshallow, which fetches the full commit graph
#    and therefore guarantees the merge-base exists.
if ! git merge-base "$BASE" "$HEAD" >/dev/null 2>&1; then
  echo "::warning::merge base unreachable after blobless fetch; unshallowing (blobless)"
  git fetch --filter=blob:none --no-tags --unshallow origin "$BASE" "$HEAD" 2>/dev/null \
    || git fetch --filter=blob:none --no-tags --unshallow origin 2>/dev/null \
    || git fetch --no-tags --unshallow origin
fi

if ! git merge-base "$BASE" "$HEAD" >/dev/null 2>&1; then
  echo "::error::merge base unreachable after fetch; planner would rc=3"
  exit 1
fi
echo "planner history ready (base=$(git rev-parse --short "$BASE") head=$(git rev-parse --short "$HEAD"))"
