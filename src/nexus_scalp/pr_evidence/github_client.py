"""GitHub API client for PR discovery, checks/annotations, and comment upsert.

Spec §1 ownership: this module owns PR discovery + GitHub API calls. It is the
ONLY module in the package that talks to the GitHub API.

Design: a thin ``Transport`` abstraction (``HttpxTransport`` / ``SubprocessTransport``
/ ``FakeTransport``) so every call is mockable without network access, and the
reporter degrades gracefully — unreachable endpoints become structured evidence
gaps (``unknown``), never exceptions that kill the report (spec §21/§22).

BOUNDARY: transport + payload shaping only. Failure normalization lives in
``collectors.py``, rendering in ``renderer.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from nexus_scalp.pr_evidence.models import UNKNOWN

__all__ = [
    "DEFAULT_API_BASE",
    "FakeTransport",
    "GitHubClient",
    "HttpxTransport",
    "PRCommentManager",
    "SubprocessTransport",
    "Transport",
]

DEFAULT_API_BASE = "https://api.github.com"

#: The single comment marker (spec §18). The reporter upserts ONE comment.
COMMENT_MARKER = "<!-- NSE-EVIDENCE-REPORT -->"

#: Maximum retries for a transient network failure (spec: graceful degradation).
_MAX_RETRIES = 2

#: Per-request timeout — the reporter must never hang the CLI on a dead network.
_TIMEOUT_SEC = 30.0


class Transport(Protocol):
    """A JSON GET/POST/PATCH/PUT surface over the GitHub REST API."""

    def get(self, url: str, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]: ...

    def post(self, url: str, body: Any, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]: ...

    def patch(self, url: str, body: Any, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]: ...


class HttpxTransport:
    """``httpx``-backed transport. Reused because httpx is already a dependency."""

    def __init__(self, token: str | None = None, api_base: str = DEFAULT_API_BASE) -> None:
        self._api_base = api_base.rstrip("/")
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            # Never logged, never echoed into a report (sanitizer also masks tokens).
            self._headers["Authorization"] = f"Bearer {token}"

    def _client(self) -> Any:
        import httpx  # local import: CLI startup stays fast when offline

        return httpx.Client(timeout=30.0, follow_redirects=True, headers=self._headers)

    def _request(self, method: str, url: str, body: Any, timeout: float) -> dict[str, Any]:
        full = url if url.startswith("http") else f"{self._api_base}{url}"
        last_error: str = ""
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with self._client() as client:
                    resp = client.request(method, full, json=body, timeout=timeout)
                if resp.status_code in (200, 201):
                    payload: dict[str, Any] = resp.json()
                    return payload
                if resp.status_code == 204:
                    return {"ok": True, "status": 204}
                if resp.status_code in (403, 429):
                    last_error = f"HTTP {resp.status_code}: rate limited or forbidden"
                    break
                if 400 <= resp.status_code < 500:
                    last_error = f"HTTP {resp.status_code}"
                    break
                last_error = f"HTTP {resp.status_code}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < _MAX_RETRIES:
                time.sleep(0.5 * (attempt + 1))
        return {"_error": last_error or "request failed", "_status": "network_error"}

    def get(self, url: str, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]:
        return self._request("GET", url, None, timeout)

    def post(self, url: str, body: Any, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]:
        return self._request("POST", url, body, timeout)

    def patch(self, url: str, body: Any, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]:
        return self._request("PATCH", url, body, timeout)


class SubprocessTransport:
    """``gh api`` backed transport — used when no token is available locally.

    Reuses the locally-authenticated ``gh`` CLI instead of inventing a second
    auth path (the repo's own convention: ``gh api`` is the documented probe).
    """

    def __init__(self, cwd: str | Path | None = None) -> None:
        self._cwd = str(cwd) if cwd else None

    def _invoke(self, args: list[str], timeout: float, body: Any = None) -> dict[str, Any]:
        stdin_payload = None
        if body is not None:
            # ``gh api --input -`` reads the request body from stdin; without
            # this the POST/PATCH carries an EMPTY payload (GitHub answers
            # 422 "nil is not an object"). The body is passed as JSON text.
            stdin_payload = json.dumps(body)
        try:
            proc = subprocess.run(
                ["gh", "api", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._cwd,
                input=stdin_payload,
                check=False,
            )
        except FileNotFoundError:
            return {"_error": "gh CLI not found", "_status": "missing_tool"}
        except subprocess.TimeoutExpired:
            return {"_error": "gh api timed out", "_status": "timeout"}
        if proc.returncode != 0:
            return {
                "_error": (proc.stderr or "").strip()[:400] or f"gh exit {proc.returncode}",
                "_status": f"gh_exit_{proc.returncode}",
            }
        out = proc.stdout.strip()
        if not out:
            return {"ok": True}
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {"_error": "non-JSON gh output", "_status": "parse_error"}

    def get(self, url: str, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]:
        return self._invoke([url], timeout)

    def post(self, url: str, body: Any, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]:
        return self._invoke(["-X", "POST", url, "--input", "-"], timeout, body)

    def patch(self, url: str, body: Any, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]:
        return self._invoke(["-X", "PATCH", url, "--input", "-"], timeout, body)


@dataclass
class FakeTransport:
    """In-memory transport for tests: scripted responses + recorded requests.

    Recorded bodies let tests assert the reporter's upsert behavior without a
    network (spec §22 mandatory tests: duplicate prevention, update existing).

    Keys match the LONGEST scripted fragment present in a URL path, so a short
    generic key never shadows a specific one. Comment endpoints are STATEFUL:
    a created comment becomes visible to later ``list``/``patch`` calls, which
    is what makes the single-comment upsert contract genuinely testable.
    """

    responses: dict[str, Any] = field(default_factory=dict)
    recorded: list[tuple[str, str, Any]] = field(default_factory=list)
    # Optional per-URL failure injection (missing-environment / API-failure tests).
    failures: dict[str, str] = field(default_factory=dict)
    # Stateful store: PR number -> list of comment bodies (issue-comment API).
    comments: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    _next_comment_id: int = 1000

    # Re-declared so the @dataclass keeps them as METHODS (a Protocol's implicit
    # members are annotations-only and are otherwise dropped by dataclass).
    def get(self, url: str, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]:
        return self._lookup("GET", url, None)

    def post(self, url: str, body: Any, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]:
        return self._lookup("POST", url, body)

    def patch(self, url: str, body: Any, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]:
        return self._lookup("PATCH", url, body)

    def put(self, url: str, body: Any, *, timeout: float = _TIMEOUT_SEC) -> dict[str, Any]:
        return self._lookup("PUT", url, body)

    def _lookup(self, method: str, url: str, body: Any) -> dict[str, Any]:
        self.recorded.append((method, url, body))
        # Query strings are transport details, not part of the script key.
        key_url = url.split("?", 1)[0]
        for pattern, message in self.failures.items():
            if pattern in key_url or pattern in url:
                return {"_error": message, "_status": "injected"}
        payload = self._match(key_url)
        if payload is not None:
            return payload
        stateful = self._stateful(method, key_url, body)
        if stateful is not None:
            return stateful
        return {"_error": f"no scripted response for {url}", "_status": "not_found"}

    def _stateful(self, method: str, key_url: str, body: Any) -> Any:
        """Issue-comment create/list/patch over an in-memory store.

        Scripted responses take precedence in ``_lookup`` (called first), so
        this store only answers when no scripted key matched the URL. Returns a
        list payload for comment LISTING (GitHub's array shape), a dict for a
        created/updated comment, or ``None`` when it does not own the URL.
        """
        import re

        create = re.match(r".*/issues/(\d+)/comments$", key_url)
        if create:
            pr = int(create.group(1))
            if method == "POST":
                self._next_comment_id += 1
                record = {"id": self._next_comment_id, "body": dict(body).get("body", "")}
                self.comments.setdefault(pr, []).append(record)
                return record
            if method == "GET":
                return list(self.comments.get(pr, []))
            return None
        patch = re.match(r".*/issues/comments/(\d+)$", key_url)
        if patch and method == "PATCH":
            cid = int(patch.group(1))
            for rows in self.comments.values():
                for row in rows:
                    if row.get("id") == cid:
                        row["body"] = dict(body).get("body", "")
                        return row
            return {"_error": "comment not found", "_status": "not_found"}
        return None

    def _match(self, key_url: str) -> Any:
        """Longest scripted key wins, matched on PATH SEGMENTS.

        Keys are URL path fragments (``check-runs`` vs
        ``check-runs/1/annotations``). A key matches only when it is present in
        the URL *at a segment boundary*, so the short key ``check-runs`` cannot
        shadow the specific ``check-runs/1/annotations``. The longest matching
        key wins.
        """
        path = key_url.rstrip("/")
        best_key: str | None = None
        for key in self.responses:
            key_path = key.strip("/")
            if not key_path:
                continue
            if _segment_contains(path, key_path) and (
                best_key is None or len(key_path) > len(best_key)
            ):
                best_key = key_path
        if best_key is None:
            return None
        payload = self.responses[best_key]
        if callable(payload):
            return payload("GET", key_url, None)
        if isinstance(payload, list):
            if len(payload) > 1:
                return payload.pop(0)
            return list(payload)
        if isinstance(payload, dict):
            return dict(payload)
        return payload


def _segment_contains(path: str, key: str) -> bool:
    """True when ``key`` appears in ``path`` starting and ending at a ``/``."""
    if not key:
        return False
    idx = 0
    while True:
        at = path.find(key, idx)
        if at < 0:
            return False
        left_ok = at == 0 or path[at - 1] == "/"
        right = at + len(key)
        right_ok = right >= len(path) or path[right] == "/"
        if left_ok and right_ok:
            return True
        idx = at + 1


def _error_of(payload: dict[str, Any]) -> str:
    if isinstance(payload, dict):
        err = payload.get("_error")
        if isinstance(err, str) and err:
            return err
        msg = payload.get("message")
        if isinstance(msg, str) and msg:
            return msg
    return ""


class GitHubClient:
    """High-level GitHub evidence surface for one repo (generic, any PR)."""

    def __init__(
        self,
        repo: str,
        transport: Transport,
        *,
        api_base: str = DEFAULT_API_BASE,
    ) -> None:
        self._repo = repo.strip().strip("/")
        self._transport = transport
        self._api_base = api_base.rstrip("/")

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    @staticmethod
    def resolve_repo(repo: str | None = None, cwd: str | Path | None = None) -> str:
        """``owner/name`` from arg > env > the git remote of ``cwd``.

        Generic PR discovery (spec §2): never hardcoded, always resolved.
        """
        if repo and "/" in repo.strip().strip("/"):
            return repo.strip().strip("/")
        env_repo = os.environ.get("NSE_GITHUB_REPO") or os.environ.get("GITHUB_REPOSITORY")
        if env_repo and "/" in env_repo:
            return env_repo.strip()
        remote = GitHubClient._git_remote_origin(cwd)
        if remote:
            return remote
        return UNKNOWN

    @staticmethod
    def _git_remote_origin(cwd: str | Path | None) -> str:
        try:
            proc = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(cwd) if cwd else None,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return UNKNOWN
        if proc.returncode != 0:
            return UNKNOWN
        return GitHubClient._parse_remote_url(proc.stdout.strip())

    @staticmethod
    def _parse_remote_url(url: str) -> str:
        """``git@github.com:Opselon/NexusTradingForexBot.git`` → ``Opselon/NexusTradingForexBot``."""
        if not url:
            return UNKNOWN
        cleaned = url.strip()
        if cleaned.endswith(".git"):
            cleaned = cleaned[: -len(".git")]
        if cleaned.startswith("git@"):
            cleaned = cleaned.split(":", 1)[-1]
        elif "://" in cleaned:
            cleaned = cleaned.split("://", 1)[-1]
            if "@" in cleaned.split("/", 1)[0]:
                cleaned = cleaned.split("@", 1)[-1]
        parts = [p for p in cleaned.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
        return UNKNOWN

    # ------------------------------------------------------------------
    # PR discovery
    # ------------------------------------------------------------------
    def discover_pr(self, pr: int | None, *, branch: str | None = None) -> int | None:
        """Resolve the PR number: explicit > env > current branch > latest open.

        Spec §2: "The command must automatically discover the current PR".
        """
        if pr and pr > 0:
            return int(pr)
        env_pr = os.environ.get("NSE_PR_NUMBER") or os.environ.get("GITHUB_REF_NAME")
        if env_pr and env_pr.isdigit():
            return int(env_pr)
        if branch:
            found = self._pr_for_branch(branch)
            if found:
                return found
        current = self._current_branch()
        if current:
            found = self._pr_for_branch(current)
            if found:
                return found
        return self._latest_open_pr()

    def _current_branch(self) -> str:
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return UNKNOWN
        return proc.stdout.strip() if proc.returncode == 0 else UNKNOWN

    def _pr_for_branch(self, branch: str) -> int | None:
        payload = self._get(
            f"/repos/{self._repo}/pulls",
            params={"head": f"{self._repo.split('/')[0]}:{branch}", "state": "open"},
        )
        if isinstance(payload, list) and payload:
            number = payload[0].get("number")
            if isinstance(number, int):
                return number
        return None

    def _latest_open_pr(self) -> int | None:
        payload = self._get(
            f"/repos/{self._repo}/pulls",
            params={"state": "open", "sort": "created", "direction": "desc"},
        )
        if isinstance(payload, list) and payload:
            number = payload[0].get("number")
            if isinstance(number, int):
                return number
        return None

    # ------------------------------------------------------------------
    # Core API surface
    # ------------------------------------------------------------------
    def _get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        if path.startswith("http"):
            url = path
            base = self._api_base
            path = path[len(base) :] if path.startswith(base) else path.split("/", 3)[-1]
        else:
            url = f"{self._api_base}{path}"
        if params:
            import urllib.parse as up

            url = f"{url}?{up.urlencode(params)}"
        return self._transport.get(url)

    def get_pr(self, pr: int) -> dict[str, Any]:
        """PR metadata: number, title, head/base SHA, branch, author, state."""
        payload = self._get(f"/repos/{self._repo}/pulls/{pr}")
        if _error_of(payload):
            return {"_error": _error_of(payload)}
        return payload

    def get_pr_reviews(self, pr: int) -> list[dict[str, Any]]:
        payload = self._get(f"/repos/{self._repo}/pulls/{pr}/reviews")
        if isinstance(payload, list):
            return payload
        return []

    def get_branch_protection(self, branch: str) -> dict[str, Any]:
        """Branch-protection rules for ``branch`` (merge preconditions).

        Returns ``{}`` when protection is unreadable — an unprotected branch
        and an unreadable one must not be conflated, so the caller records a
        gap rather than assuming nothing is required.
        """
        if not branch or branch == UNKNOWN:
            return {}
        payload = self._get(f"/repos/{self._repo}/branches/{branch}/protection")
        if isinstance(payload, dict) and not _error_of(payload):
            return payload
        return {}

    def get_check_suites(self, sha: str) -> list[dict[str, Any]]:
        payload = self._get(f"/repos/{self._repo}/commits/{sha}/check-suites")
        if isinstance(payload, dict):
            suites = payload.get("check_suites")
            if isinstance(suites, list):
                return suites
        return []

    def get_check_runs_for_suite(self, suite_id: int) -> list[dict[str, Any]]:
        payload = self._get(f"/repos/{self._repo}/check-suites/{suite_id}/check-runs")
        if isinstance(payload, dict):
            runs = payload.get("check_runs")
            if isinstance(runs, list):
                return runs
        return []

    def get_check_runs_for_ref(self, sha: str) -> list[dict[str, Any]]:
        """All check runs for a ref — the generic CI evidence source (spec §10)."""
        all_runs: list[dict[str, Any]] = []
        url = f"/repos/{self._repo}/commits/{sha}/check-runs?per_page=100"
        page = 1
        while url and page <= 10:
            payload = self._get(url)
            if not isinstance(payload, dict) or _error_of(payload):
                break
            runs = payload.get("check_runs")
            if not isinstance(runs, list):
                break
            all_runs.extend(runs)
            if not runs or len(runs) < 100:
                break
            page += 1
            url = f"/repos/{self._repo}/commits/{sha}/check-runs?per_page=100&page={page}"
        return all_runs

    def get_code_scanning_alerts(
        self, sha: str, *, state: str = "open", pr: int | None = None
    ) -> list[dict[str, Any]]:
        """CodeQL / code-scanning alerts relevant to a PR (spec §11).

        PR-block alerts are indexed under the PR's MERGE ref, not the head SHA:
        CodeQL analyses the merged result, so `?ref=<head sha>` returns an empty
        list for exactly the findings that are failing the PR's gate. Query the
        head SHA and the PR merge ref and union them by alert number so neither
        source of findings is missed. The alerts API carries structured
        locations; when unreachable the caller falls back to annotations.
        """
        found: dict[Any, dict[str, Any]] = {}
        refs = [sha]
        if pr:
            refs.append(f"refs/pull/{pr}/merge")
        for ref in refs:
            if not ref or ref == UNKNOWN:
                continue
            payload = self._get(
                f"/repos/{self._repo}/code-scanning/alerts", params={"ref": ref, "state": state}
            )
            if isinstance(payload, list):
                for alert in payload:
                    if not isinstance(alert, dict):
                        continue
                    key = alert.get("number") or alert.get("html_url") or id(alert)
                    found[key] = alert
        return list(found.values())

    def get_annotations(self, check_run_id: int) -> list[dict[str, Any]]:
        """Annotations for one check run — file/line/column evidence (spec §4/§11)."""
        payload = self._get(f"/repos/{self._repo}/check-runs/{check_run_id}/annotations")
        if isinstance(payload, list):
            return payload
        return []

    def get_job_steps(self, job_id: int) -> list[dict[str, Any]]:
        """The steps of one CI job, with per-step conclusions (spec §10).

        When a job fails without a usable annotation — GitHub emits a synthetic
        ``.github`` path for some job-level failures — the failing STEP is the
        only location evidence that exists. It names what actually broke
        ("Build", "Tests"), which a bare "Process completed with exit code 1"
        never does.
        """
        payload = self._get(f"/repos/{self._repo}/actions/jobs/{job_id}")
        if isinstance(payload, dict) and not _error_of(payload):
            steps = payload.get("steps")
            if isinstance(steps, list):
                return [s for s in steps if isinstance(s, dict)]
        return []

    def get_workflow_run(self, run_id: int) -> dict[str, Any]:
        """Workflow run metadata — the authoritative workflow name (spec §10).

        A check run's payload carries no ``workflow_name``; the owning workflow
        name lives on the Actions run referenced by its ``details_url``. Missing
        it left every CI row rendered as ``unknown``, which is a fact the
        evidence DID support, so it must be resolved rather than guessed.
        """
        payload = self._get(f"/repos/{self._repo}/actions/runs/{run_id}")
        if isinstance(payload, dict) and not _error_of(payload):
            return payload
        return {}

    def list_pr_comments(self, pr: int) -> list[dict[str, Any]]:
        payload = self._get(f"/repos/{self._repo}/issues/{pr}/comments")
        if isinstance(payload, list):
            return payload
        return []

    def create_comment(self, pr: int, body: str) -> dict[str, Any]:
        return self._transport.post(
            f"{self._api_base}/repos/{self._repo}/issues/{pr}/comments", {"body": body}
        )

    def update_comment(self, comment_id: int, body: str) -> dict[str, Any]:
        return self._transport.patch(
            f"{self._api_base}/repos/{self._repo}/issues/comments/{comment_id}", {"body": body}
        )


class PRCommentManager:
    """Single-comment upsert (spec §18): find marker → update; never duplicate.

    Running ``nse pr report`` ten times must still produce ONE report comment.
    """

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def upsert(self, pr: int, body: str, *, marker: str = COMMENT_MARKER) -> dict[str, Any]:
        existing = self.find_existing(pr, marker=marker)
        if existing is not None:
            return self._client.update_comment(existing, body)
        return self._client.create_comment(pr, body)

    def find_existing(self, pr: int, *, marker: str = COMMENT_MARKER) -> int | None:
        for comment in self._client.list_pr_comments(pr):
            text = str(comment.get("body") or "")
            if marker in text:
                try:
                    return int(comment.get("id"))
                except (TypeError, ValueError):
                    continue
        return None

    def count_reports(self, pr: int, *, marker: str = COMMENT_MARKER) -> int:
        return sum(
            1
            for comment in self._client.list_pr_comments(pr)
            if marker in str(comment.get("body") or "")
        )
