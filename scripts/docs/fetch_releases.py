"""Fetch GitHub release metadata into site/cache/releases.json (build-time
release synchronization — deterministic, no client-side remote fetches).

Nexus-Docs owns this script. Called by docs.yml on every docs build and on
release publication. Offline-safe: when the GitHub API is unreachable the
existing cache is kept (build then uses the last known releases; never
fabricates data).

Usage: python scripts/docs/fetch_releases.py [--repo OWNER/REPO]
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

CACHE = REPO_ROOT / "site" / "cache" / "releases.json"
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


def fetch(repo: str) -> list[dict] | None:
    req = urllib.request.Request(
        f"{API}/repos/{repo}/releases?per_page=20",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "nexus-docs-build",
            **({"Authorization": f"token {token()}"} if token() else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [
            {
                "tag_name": r.get("tag_name", ""),
                "name": r.get("name", ""),
                "published_at": r.get("published_at", ""),
                "draft": bool(r.get("draft")),
                "prerelease": bool(r.get("prerelease")),
                "html_url": r.get("html_url", ""),
                "body": r.get("body", ""),
            }
            for r in data
        ]
    except Exception as exc:  # offline-safe
        print(f"WARN: release fetch failed ({exc}); keeping cache", file=sys.stderr)
        return None


def main() -> int:
    repo = cfg.OWNER + "/" + cfg.REPO
    if "--repo" in sys.argv:
        repo = sys.argv[sys.argv.index("--repo") + 1]
    releases = fetch(repo)
    if releases is None:
        print(f"cache kept: {CACHE}")
        return 0  # not an error — deterministic offline build
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(releases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"releases cached: {len(releases)} -> {CACHE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
