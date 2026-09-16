"""Fetch live repository metadata into site/cache/repo.json (build-time
project synchronization for the landing page — deterministic, offline-safe).

Nexus-Docs owns this script. Called by docs.yml alongside fetch_releases.py.
When the GitHub API is unreachable the existing cache is kept (the build then
uses the last known values; never fabricates data). Client-side code may
refresh the numbers, but the page must render correctly without it.

Collected (all from public GitHub APIs, token only for rate limits):
  * repo stats     — stars, forks, open issues+PRs, pushed_at
  * latest commit  — sha, date, author on the default branch
  * CI state       — latest Actions run on the default branch (status/conclusion)
  * contributors   — contributor count (first page size, honest label)

Usage: python scripts/docs/fetch_repo.py [--repo OWNER/REPO]
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "docs"))
import site_config as cfg  # noqa: E402

CACHE = REPO_ROOT / "site" / "cache" / "repo.json"
API = "https://api.github.com"


def token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    try:  # local dev: reuse git credential store
        proc = __import__("subprocess").run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1]
    except Exception:
        pass
    return None


def get(path: str) -> object | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "nexus-docs-build",
        **({"Authorization": f"token {token()}"} if token() else {}),
    }
    req = urllib.request.Request(f"{API}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # offline-safe
        print(f"WARN: fetch {path} failed ({exc})", file=sys.stderr)
        return None


def main() -> int:
    repo = cfg.OWNER + "/" + cfg.REPO
    if "--repo" in sys.argv:
        repo = sys.argv[sys.argv.index("--repo") + 1]

    info = get(f"/repos/{repo}")
    if not isinstance(info, dict):
        print(f"cache kept: {CACHE}")
        return 0  # not an error — deterministic offline build
    branch = info.get("default_branch") or "main"

    commit = get(f"/repos/{repo}/commits?sha={branch}&per_page=1")
    latest_commit = {}
    if isinstance(commit, list) and commit:
        c = commit[0]
        latest_commit = {
            "sha": (c.get("sha") or "")[:7],
            "date": ((c.get("commit") or {}).get("committer") or {}).get("date", ""),
            "message": ((c.get("commit") or {}).get("message") or "").splitlines()[0][:120]
            if c.get("commit")
            else "",
        }

    runs = get(f"/repos/{repo}/actions/runs?branch={branch}&per_page=1")
    ci = {}
    workflow_runs = (runs or {}).get("workflow_runs") if isinstance(runs, dict) else None
    if workflow_runs:
        r = workflow_runs[0]
        ci = {
            "name": r.get("name", ""),
            "status": r.get("status", ""),
            "conclusion": r.get("conclusion") or "",
            "html_url": r.get("html_url", ""),
            "updated_at": r.get("updated_at", ""),
        }

    contributors = get(f"/repos/{repo}/contributors?per_page=100&anon=false")
    contributors_count = len(contributors) if isinstance(contributors, list) else None

    data = {
        "repo": repo,
        "stars": int(info.get("stargazers_count") or 0),
        "forks": int(info.get("forks_count") or 0),
        "open_issues": int(info.get("open_issues_count") or 0),
        "pushed_at": info.get("pushed_at", ""),
        "license": (info.get("license") or {}).get("spdx_id", ""),
        "latest_commit": latest_commit,
        "ci": ci,
        "contributors": contributors_count,
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"repo metadata cached -> {CACHE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
