#!/usr/bin/env python3
"""Runner availability preflight + runner-class selection for NSE CI.

This is the SINGLE place that decides which runner class a job uses. It runs
on GitHub-hosted infrastructure (a cheap ``ubuntu-latest`` preflight job)
BEFORE any real work is scheduled, so a job never sits in a queue waiting
for a self-hosted runner that is offline, busy, disabled or unconfigured.

Design decisions (validated against the real repository):

* The repository is owned by a USER account (``Opselon``), not an
  organization, so organization-level runner endpoints do not exist. The
  preflight queries ``GET /repos/{owner}/{repo}/actions/runners``, which
  requires the repository **Administration: read** permission for a GitHub
  App installation token, or the ``repo`` scope for a classic PAT. The
  workflow's own ``GITHUB_TOKEN`` does NOT have Administration permission,
  so a dedicated read-only credential (``NSE_RUNNER_STATUS_TOKEN``) is used
  when present and the preflight FAILS OPEN to the GitHub-hosted fallback
  whenever it is missing or the API errors. No token, no secrets and no
  runner registration data is ever printed.
* An availability check is NOT an atomic reservation: between the check and
  the job assignment another workflow may take the runner, or the runner may
  go offline. This is therefore a HINT that picks a runner class, and the
  routing is structured as two jobs with mutually exclusive conditions —
  the self-hosted leg can still be beaten to the runner, in which case
  GitHub re-queues it (bounded by ``timeout-minutes`` on the preflight, not
  on the job). See ``docs/CI_ARCHITECTURE.md`` "Race conditions".
* Fork-originated pull requests never execute on persistent self-hosted
  runners: the caller passes ``--untrusted`` and the answer is always the
  GitHub-hosted class, with NO credential consulted.

Exit code 0 in every path (a routing decision is always produced); a non-zero
exit would break the workflow instead of falling back.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API_VERSION = "2022-11-28"
TIMEOUT_SECONDS = 15

#: Conclusions emitted on stdout for the workflow (one JSON object).
DECISION_KEYS = (
    "platform",
    "runner_class",
    "self_hosted_enabled",
    "self_hosted_eligible",
    "reason",
    "labels",
)


def _gh_get(url: str, token: str) -> dict[str, Any] | None:
    """GET a GitHub API endpoint; return None on any failure."""
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            **({"Authorization": "Bearer " + token} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def list_runners(repository: str, token: str) -> list[dict[str, Any]] | None:
    """Return the repository self-hosted runner list, or None on API failure.

    ``None`` is the distinct "could not determine" signal that forces the
    hosted fallback. Pagination is handled up to a sane ceiling.
    """
    runners: list[dict[str, Any]] = []
    page = 1
    while page <= 20:
        url = f"https://api.github.com/repos/{repository}/actions/runners?per_page=100&page={page}"
        data = _gh_get(url, token)
        if data is None:
            return None
        batch = data.get("runners")
        if not isinstance(batch, list):
            return None
        runners.extend(r for r in batch if isinstance(r, dict))
        total = data.get("total_count")
        if not isinstance(total, int) or len(runners) >= total or not batch:
            return runners
        page += 1
    return runners


def eligible_runner(runners: list[dict[str, Any]], labels: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """A runner is eligible when it is online, idle, and has every label."""
    wanted = set(labels)
    out = []
    for runner in runners:
        if runner.get("status") != "online" or runner.get("busy"):
            continue
        have = {str(label.get("name")) for label in runner.get("labels") or [] if isinstance(label, dict)}
        if wanted <= have:
            out.append(runner)
    return out


def _flag(env: dict[str, str], name: str, default: bool) -> bool:
    """Read a boolean enablement flag from an environment mapping."""
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def select(
    platform: str,
    repository: str,
    labels: tuple[str, ...] | list[str],
    hosted: str,
    env: dict[str, str],
    untrusted: bool = False,
) -> dict[str, Any]:
    """Decide the runner class. Pure function — returns a decision dict."""
    flag_name = f"ENABLE_{platform.upper()}_SELF_HOSTED"
    enabled = _flag(env, flag_name, default=True) if platform == "linux" else _flag(env, flag_name, default=False)

    decision: dict[str, Any] = {
        "platform": platform,
        "runner_class": "github-hosted",
        "self_hosted_enabled": enabled,
        "self_hosted_eligible": False,
        "reason": "",
        "labels": list(labels),
    }

    # Trust boundary first: fork PRs never reach persistent runners.
    if untrusted:
        decision["reason"] = (
            f"untrusted (fork) pull request -> {FORK_REASON}; github-hosted class mandatory"
        )
        decision["runner_class"] = "github-hosted"
        return decision

    if not enabled:
        decision["reason"] = f"{flag_name} not enabled (default for this platform) -> github-hosted fallback"
        return decision

    token = env.get("NSE_RUNNER_STATUS_TOKEN", "")
    if not token:
        decision["reason"] = "NSE_RUNNER_STATUS_TOKEN absent -> fail open to github-hosted fallback"
        return decision

    runners = list_runners(repository, token)
    if runners is None:
        decision["reason"] = "runner status API unavailable/error -> fail open to github-hosted fallback"
        return decision

    matches = eligible_runner(runners, labels)
    decision["self_hosted_eligible"] = bool(matches)
    if not matches:
        decision["reason"] = (
            f"no online+idle runner matching labels {list(labels)} "
            f"(checked {len(runners)} runner(s)) -> github-hosted fallback"
        )
        return decision

    decision["runner_class"] = "self-hosted"
    decision["reason"] = (
        f"{len(matches)} online+idle runner(s) match labels {list(labels)} "
        f"(checked {len(runners)} runner(s)) -> self-hosted"
    )
    return decision


FORK_REASON = "untrusted code must not run on persistent trusted runners"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--platform", required=True, choices=("linux", "windows", "macos"))
    ap.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "Opselon/NexusTradingForexBot"))
    ap.add_argument("--labels", required=True, help="comma-separated self-hosted label set")
    ap.add_argument("--hosted", required=True, help="GitHub-hosted fallback image")
    ap.add_argument("--untrusted", action="store_true", help="fork PR: force github-hosted class")
    ap.add_argument("--env-prefix", default="", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    env = dict(os.environ)
    decision = select(
        platform=args.platform,
        repository=args.repository,
        labels=tuple(x for x in args.labels.split(",") if x),
        hosted=args.hosted,
        env=env,
        untrusted=args.untrusted,
    )

    # Emit the machine-readable decision (consumed via fromJson() in the
    # workflows) plus a compact, secret-free human line.
    print(json.dumps(decision, indent=2))
    print(
        f"::notice::runner-select: platform={decision['platform']} "
        f"class={decision['runner_class']} eligible={decision['self_hosted_eligible']} "
        f"reason={decision['reason']}",
        file=sys.stderr,
    )
    # GITHUB_OUTPUT support (preflight step output).
    gout = env.get("GITHUB_OUTPUT")
    if gout:
        with open(gout, "a", encoding="utf-8") as fh:
            fh.write(f"runner_class={decision['runner_class']}\n")
            fh.write(f"self_hosted_eligible={str(decision['self_hosted_eligible']).lower()}\n")
            fh.write(f"selection_reason={decision['reason']}\n")
            fh.write("decision=" + json.dumps(decision) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
