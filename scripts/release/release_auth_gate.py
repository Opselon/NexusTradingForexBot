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
#:
#: 2026-09-14 test reconstruction NOTE: the required-context SET is
#: deliberately UNCHANGED (protection untouched). What changed is what the
#: contexts actually EXECUTE on each event (tests-os.yml):
#:   * 'Py Tests (windows-latest)' — PR: OS-skewed subset (fast);
#:     main push: FULL critical suite (product runtime).
#:   * 'Py Tests (macos-latest)' — PR: always-report green-with-reason
#:     (DEC-0008 pattern, ~1min, no torch install); main push / Monday
#:     schedule / platform-path changes: FULL critical suite (advisory truth;
#:     nothing ships for macOS, but a real regression still colors the context
#:     red on main where it is re-required for the NEXT merge — honest drift
#:     detection without paying 5x per PR).
#:   * Tier 2 (tests/extended_suite.txt) runs on main push as the
#:     'Extended Validation (Tier 2)' job of CI and is executed ON THE RELEASE
#:     SHA by release.yml's gates job (replacing the duplicated ruff/mypy/
#:     critical re-run there). A red Tier-2 on the release SHA blocks the tag.
#: If the macOS PR-lane report is ever made non-green by design, protection
#: must be edited atomically with this tuple (drift test:
#: tests/unit/test_release_auth_gate.py::
#: test_required_list_matches_live_branch_protection).
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

#: Runtime certification contexts that are NOT branch-protection-required on
#: PRs but MUST be green on the exact release SHA anyway (DevOps audit P4,
#: 2026-11): a release may never bypass the real-process SQLite/PostgreSQL
#: runtime matrix + MT5 terminal runtime verdict, or the DB Fabric
#: SQLite<->PostgreSQL certification lane. These run on every main push via
#: ci.yml, so the evidence exists for any releasable commit; a missing or red
#: runtime context fails the release authorization exactly like a failed
#: required check (fail-closed).
#:
#: CI runner-routing architecture (2026-11): Windows is excluded from
#: automatic CI, so the "MetaTrader 5 Native Windows IPC" context is
#: expected to be SKIPPED on PR/push events. A skipped check is not
#: evidence, so the release boundary instead requires the MANUAL Windows
#: lane contexts below (produced by tests-windows.yml / the scheduled MT5
#: runs). They are not required on PRs; they are required HERE. Both are
#: absent-or-green on a PR-only SHA, which is exactly the case this gate
#: must refuse: a release may not be cut from a commit whose Windows
#: evidence does not exist.
REQUIRED_RELEASE_RUNTIME_CHECKS: tuple[str, ...] = (
    "Full Runtime Overall Verdict",
    "DB Fabric Certification (SQLite ↔ PostgreSQL)",
)

#: Windows manual-lane contexts whose evidence the release boundary accepts
#: for the release SHA (any one satisfied is enough). Produced by
#: .github/workflows/tests-windows.yml (workflow_dispatch) and by the
#: scheduled mt5-native-windows runs. A release whose SHA has none of these
#: has NO Windows evidence and is refused.
REQUIRED_RELEASE_WINDOWS_EVIDENCE: tuple[str, ...] = (
    "Py Tests (windows-latest, hosted)",
    "Py Tests (windows-latest, self-hosted)",
    "MetaTrader 5 Native Windows IPC / MetaTrader 5 Native Windows IPC",
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
    last_exc: Exception | None = None
    import time

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            time.sleep(1.0 * (attempt + 1))

    # Fallback to curl.exe if Python SSL handshake fails on Windows
    import shutil
    import subprocess

    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        cmd = [
            curl,
            "-s",
            "--max-time",
            str(timeout),
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "User-Agent: nse-release-gate",
            url,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            try:
                return json.loads(res.stdout)
            except Exception as j_exc:
                last_exc = j_exc
    raise last_exc or RuntimeError("fetch failed")


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

    ``required`` defaults to the full release boundary: branch-protection
    contexts PLUS the runtime certification contexts
    (REQUIRED_RELEASE_RUNTIME_CHECKS). A check name can appear more than
    once (re-runs). The verdict for a name is: any completed attempt counts
    (a completed FAILURE cannot be masked by a later queued re-run), and if
    any attempt is pending with no completed attempt, the name is pending.
    """
    required = tuple(required) + REQUIRED_RELEASE_RUNTIME_CHECKS
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

    # Windows manual-lane evidence (CI runner-routing architecture, 2026-11).
    # The automatic Windows contexts are expected to be SKIPPED on PR/push;
    # a skipped context is not evidence. The release must instead carry at
    # least one green Windows manual-lane context for this exact SHA, and no
    # Windows manual-lane context may have FAILED (a failure is never masked
    # by a green sibling, mirroring the per-name rule above). This branch
    # cannot turn a verdict green; it can only refuse to authorize.
    windows_ok = False
    for name in REQUIRED_RELEASE_WINDOWS_EVIDENCE:
        state = verdicts.get(name)
        if state == _SUCCESS:
            windows_ok = True
        elif state == "pending":
            failures.append(f"{name}: pending (Windows evidence not completed)")
        elif state is not None and state not in {"skipped", "neutral"}:
            failures.append(f"{name}: {state} (a red Windows lane refuses the release)")
    if not windows_ok and not any(name in verdicts for name in REQUIRED_RELEASE_WINDOWS_EVIDENCE):
        missing.append(
            "Windows lane evidence (none of: "
            + ", ".join(REQUIRED_RELEASE_WINDOWS_EVIDENCE)
            + ") — run tests-windows.yml on this SHA"
        )

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
        f"({', '.join((*REQUIRED_MAIN_CI_CHECKS, *REQUIRED_RELEASE_RUNTIME_CHECKS))})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
