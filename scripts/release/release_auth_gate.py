#!/usr/bin/env python3
"""Release authorization gate (Finding 3, release wave).

NO MAIN-CI EVIDENCE == NO RELEASE.  The GitHub Actions release workflow calls
this BEFORE publishing anything; it refuses to authorize a release unless
every REQUIRED MAIN-CI check for the EXACT release commit SHA has completed
successfully on that same SHA.

Evidence source: the GitHub Checks API (combined check-runs for the commit).
The required-check list mirrors the repository's branch-protection
``required_status_checks.contexts`` (verified live at build time via the
API when a token is available; the workflow supplies GITHUB_TOKEN).

Fail-closed semantics — the gate exits non-zero (release blocked) when:

  * a required check is missing for this SHA
  * a required check is in a non-terminal state (in_progress/queued)
  * a required check concluded anything other than success
  * skipped / neutral / cancelled are NOT accepted (skipped-by-design
    heavy lanes are not part of the required list)
  * the API is unreachable, errors, or returns ambiguous data
  * the GITHUB_REPOSITORY/GITHUB_SHA environment is absent (local runs must
    pass --repo/--sha explicitly and STILL get the same verdict logic)

Exit codes:
    0  authorized  (all required checks succeeded on this exact SHA)
    1  NOT authorized (missing/failed/pending evidence, API failure)

This gate is EVIDENCE-BINDING, not test re-running: release.yml runs its own
gates too, but publication additionally requires independent main-CI proof
for the same commit, so a red main CI can never be published around.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

#: Required MAIN-CI checks for release authorization. Mirrors the live
#: branch-protection required_status_checks for main (verified 2026-09-11 via
#: GET /repos/{repo}/branches/main/protection):
#:   ['Dependency drift (lock vs pyproject)', 'Migration safety (fail-loud +
#:   version postconditions)', 'Frontend JS Unit Tests', 'CodeQL Analysis',
#:   'Trivy Vulnerability Scan', 'OSV Scanner / osv-scan',
#:   'Py Tests (macos-latest)', 'Py Tests (windows-latest)',
#:   'Validate documentation', 'CI Integrity and Change Classification',
#:   'Code Quality & Tests']
#: Aggregate/Heavy-CI legs are NOT required on main pushes (they run on the
#: ci-tests branch / manual dispatch) and are intentionally absent here.
REQUIRED_MAIN_CI_CHECKS: tuple[str, ...] = (
    "CI Integrity and Change Classification",
    "Code Quality & Tests",
    "Dependency drift (lock vs pyproject)",
    "Migration safety (fail-loud + version postconditions)",
    "Frontend JS Unit Tests",
    "CodeQL Analysis",
    "Trivy Vulnerability Scan",
    "OSV Scanner / osv-scan",
    "Py Tests (macos-latest)",
    "Py Tests (windows-latest)",
    "Validate documentation",
)

_API_RE = re.compile(r"^https://api\.github\.com/repos/([^/]+/[^/]+)$")


def _fetch_json(url: str, token: str, timeout: int) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "nse-release-gate",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def collect_check_runs(repo: str, sha: str, token: str, timeout: int = 30) -> list[dict]:
    """All combined check-runs for the commit (paged). Raises on API failure."""
    runs: list[dict] = []
    page = 1
    while True:
        data = _fetch_json(
            f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs"
            f"?per_page=100&page={page}",
            token,
            timeout,
        )
        batch = data.get("check_runs", [])
        if not isinstance(batch, list):
            raise ValueError("check-runs payload is not a list (ambiguous API state)")
        runs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        if page > 10:
            raise ValueError("check-runs pagination exceeded 10 pages (ambiguous)")
    return runs


#: terminal conclusions that authorize a release
_SUCCESS = "success"
_PENDING = ("queued", "in_progress", "waiting", "pending")


def authorize(
    runs: list[dict], required: tuple[str, ...] = REQUIRED_MAIN_CI_CHECKS
) -> tuple[bool, list[str], list[str]]:
    """Return (authorized, failures, missing). Fail-closed on every gap.

    A check name can appear more than once (re-runs). The verdict for a name
    is: any completed attempt counts (a completed FAILURE cannot be masked by
    a later queued re-run), and if any attempt is pending with no completed
    attempt, the name is pending.
    """
    verdicts: dict[str, str] = {}
    for run in runs:
        name = str(run.get("name", ""))
        status = str(run.get("status", ""))
        conclusion = run.get("conclusion")
        if status != "completed" or conclusion is None:
            if name not in verdicts:
                verdicts[name] = "pending"
            continue
        if verdicts.get(name) in (None, "pending"):
            verdicts[name] = str(conclusion)
        # a completed conclusion never downgrades an existing one

    failures: list[str] = []
    missing: list[str] = []
    for name in required:
        state = verdicts.get(name)
        if state is None:
            missing.append(name)
        elif state == "pending":
            failures.append(f"{name}: pending (not completed)")
        elif state != _SUCCESS:
            failures.append(f"{name}: {state}")
    return (not failures and not missing), failures, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Main-CI evidence gate for releases")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN", os.environ.get("GH_TOKEN", "")),
        help="GitHub token (workflow supplies secrets.GITHUB_TOKEN)",
    )
    args = parser.parse_args(argv)

    if not _API_RE.match(f"https://api.github.com/repos/{args.repo}" if args.repo else ""):
        print("::error::RELEASE_AUTH_GATE: repository identity unavailable — refusing to authorize")
        return 1
    if not re.match(r"^[0-9a-f]{40}$", args.sha or ""):
        print(
            "::error::RELEASE_AUTH_GATE: full 40-hex commit SHA required "
            f"(got {args.sha!r}) — refusing to authorize"
        )
        return 1

    try:
        runs = collect_check_runs(args.repo, args.sha, args.token)
        ok, failures, missing = authorize(runs)
    except urllib.error.HTTPError as e:
        print(f"::error::RELEASE_AUTH_GATE: GitHub API HTTP {e.code} — fail closed (NO RELEASE)")
        return 1
    except Exception as e:  # ambiguous or unavailable evidence = NO RELEASE
        print(f"::error::RELEASE_AUTH_GATE: evidence unavailable ({e}) — fail closed (NO RELEASE)")
        return 1

    print(f"RELEASE AUTH EVIDENCE for {args.sha}: {len(runs)} check-runs observed")
    if missing:
        print(f"::error::RELEASE_AUTH_GATE: MISSING main-CI evidence: {', '.join(missing)}")
    if failures:
        print(f"::error::RELEASE_AUTH_GATE: main-CI NOT green: {', '.join(failures)}")
    if not ok:
        print("::error::NO MAIN-CI EVIDENCE == NO RELEASE (fail closed)")
        return 1

    print(
        "RELEASE AUTHORIZED: all required main-CI checks succeeded on this exact SHA "
        f"({', '.join(REQUIRED_MAIN_CI_CHECKS)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
