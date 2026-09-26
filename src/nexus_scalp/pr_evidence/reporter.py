"""Report orchestrator — collect → render → publish ONE comment; watch loop.

Spec §19 watch mode: collect → publish → wait → detect new CI/commit/review
state → collect again → update the SAME comment. The comment is only rewritten
when the evidence actually changed, and the loop is bounded by a timeout.

BOUNDARY: orchestration only. Collection lives in ``collectors.py``, rendering
in ``renderer.py``, API I/O in ``github_client.py``.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus_scalp.pr_evidence.collectors import EvidenceOptions, collect_evidence
from nexus_scalp.pr_evidence.github_client import (
    COMMENT_MARKER,
    GitHubClient,
    HttpxTransport,
    PRCommentManager,
    Transport,
)
from nexus_scalp.pr_evidence.models import EvidenceCollection, Status
from nexus_scalp.pr_evidence.renderer import render_report

__all__ = ["DEFAULT_POLL_SEC", "DEFAULT_WATCH_TIMEOUT_SEC", "ReportResult", "Reporter"]


DEFAULT_POLL_SEC = 60
DEFAULT_WATCH_TIMEOUT_SEC = 1800


@dataclass
class ReportResult:
    """What the reporter actually did — the CLI's evidence for its own claim."""

    pr: int
    status: Status
    failure_count: int
    affected_files: list[str] = field(default_factory=list)
    comment_id: int | None = None
    comment_action: str = "none"  # created | updated | unchanged | not_published
    published: bool = False
    body_sha: str = ""
    errors: list[str] = field(default_factory=list)
    collection: EvidenceCollection | None = None


class Reporter:
    """Generic PR evidence reporter: any PR, any check, any failure shape."""

    def __init__(
        self,
        client: GitHubClient,
        *,
        repo_root: str | None = None,
        repo_dir: Path | None = None,
        options: EvidenceOptions | None = None,
        marker: str = COMMENT_MARKER,
    ) -> None:
        self._client = client
        self._comments = PRCommentManager(client)
        self._repo_root = repo_root
        self._repo_dir = repo_dir
        self._options = options or EvidenceOptions()
        self._marker = marker

    # ------------------------------------------------------------------
    def report_once(self, pr: int, *, publish: bool = True, local_python: str = "") -> ReportResult:
        """Collect + render + (optionally) publish one report (spec §§1-18)."""
        evidence = collect_evidence(
            client=self._client,
            repo=self._client._repo,
            pr=pr,
            options=self._options,
            repo_root=self._repo_root,
            repo_dir=self._repo_dir,
            local_python=local_python,
        )
        body = render_report(evidence, marker=self._marker)
        result = ReportResult(
            pr=pr,
            status=evidence.status,
            failure_count=len(evidence.all_failures()),
            affected_files=_affected_files(evidence),
            errors=list(evidence.errors),
            collection=evidence,
            body_sha=_body_sha(body),
        )
        if publish:
            outcome = self._publish(pr, body)
            result.published = outcome["published"]
            result.comment_id = outcome.get("comment_id")
            result.comment_action = outcome["action"]
            # A publish failure must be DISCLOSED, never swallowed: an empty
            # error list next to ``action: error`` told the operator nothing
            # (spec §16/§26: what remains unresolved must be stated).
            if outcome.get("error"):
                result.errors = [*result.errors, f"comment publish failed: {outcome['error']}"]
        return result

    def _publish(self, pr: int, body: str) -> dict[str, Any]:
        """Upsert exactly one comment (spec §18: never duplicate)."""
        try:
            existing = self._comments.find_existing(pr, marker=self._marker)
        except Exception as exc:
            return {"published": False, "action": "error", "error": str(exc)}
        try:
            if existing is not None and self._body_unchanged(pr, existing, body):
                return {
                    "published": True,
                    "action": "unchanged",
                    "comment_id": existing,
                }
            payload = self._comments.upsert(pr, body, marker=self._marker)
        except Exception as exc:
            return {"published": False, "action": "error", "error": str(exc)}
        if "_error" in payload:
            return {
                "published": False,
                "action": "error",
                "error": str(payload.get("_error")),
            }
        return {
            "published": True,
            "action": "updated" if existing is not None else "created",
            "comment_id": _comment_id(payload),
        }

    def _body_unchanged(self, pr: int, comment_id: int, body: str) -> bool:
        """Only rewrite the comment when the evidence changed (spec §19)."""
        for comment in self._client.list_pr_comments(pr):
            if int(comment.get("id") or 0) != comment_id:
                continue
            return _body_sha(str(comment.get("body") or "")) == _body_sha(body)
        return False

    # ------------------------------------------------------------------
    def watch(
        self,
        pr: int,
        *,
        poll_sec: int = DEFAULT_POLL_SEC,
        timeout_sec: int = DEFAULT_WATCH_TIMEOUT_SEC,
        max_iterations: int = 60,
        on_cycle: Callable[[ReportResult], None] | None = None,
        stop_event: Any = None,
        local_python: str = "",
    ) -> ReportResult:
        """Spec §19 watch loop. Terminates on timeout, stop event, or PASS."""
        deadline = time.monotonic() + max(timeout_sec, 1)
        last: ReportResult | None = None
        iteration = 0
        while True:
            iteration += 1
            if iteration > max_iterations:
                break
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                break
            result = self.report_once(pr, publish=True, local_python=local_python)
            if on_cycle is not None:
                on_cycle(result)
            last = result
            if result.status == Status.PASS:
                break
            if time.monotonic() + poll_sec > deadline:
                break
            self._sleep(poll_sec)
        assert last is not None  # loop runs at least once
        return last

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(max(0, seconds))


def _affected_files(evidence: EvidenceCollection) -> list[str]:
    seen: list[str] = []
    for failure in evidence.all_failures():
        for path in failure.affected_paths():
            if path not in seen:
                seen.append(path)
    return seen


def _body_sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()[:16]


def _comment_id(payload: dict[str, Any]) -> int | None:
    try:
        return int(payload.get("id"))
    except (TypeError, ValueError):
        return None


def build_transport(
    *,
    token: str | None = None,
    use_gh: bool = False,
    api_base: str | None = None,
    cwd: str | Path | None = None,
) -> Transport:
    """Pick the transport: explicit token → httpx; else the local ``gh`` CLI.

    The reporter must work in CI (``GITHUB_TOKEN``) and on a developer's
    machine with only ``gh`` authenticated — without inventing a second auth
    path (the repo convention is ``gh api``).
    """
    resolved = token or _env_token()
    if use_gh or not resolved:
        return _GhOrHttpx(use_gh=use_gh, token=resolved, cwd=cwd, api_base=api_base)
    return HttpxTransport(token=resolved, api_base=api_base or "https://api.github.com")


def _env_token() -> str | None:
    import os

    return os.environ.get("NSE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")


@dataclass
class _GhOrHttpx:
    """Prefer ``gh api`` when a token is absent; httpx when one is present."""

    use_gh: bool
    token: str | None
    cwd: str | Path | None
    api_base: str | None

    def __post_init__(self) -> None:
        if self.use_gh or not self.token:
            from nexus_scalp.pr_evidence.github_client import SubprocessTransport

            self._impl: Transport = SubprocessTransport(cwd=self.cwd)
        else:
            self._impl = HttpxTransport(
                token=self.token, api_base=self.api_base or "https://api.github.com"
            )

    def get(self, url: str, *, timeout: float = 30.0) -> dict[str, Any]:
        return self._impl.get(url, timeout=timeout)

    def post(self, url: str, body: Any, *, timeout: float = 30.0) -> dict[str, Any]:
        return self._impl.post(url, body, timeout=timeout)

    def patch(self, url: str, body: Any, *, timeout: float = 30.0) -> dict[str, Any]:
        return self._impl.patch(url, body, timeout=timeout)


def make_client(
    repo: str | None = None,
    *,
    transport: Transport | None = None,
    token: str | None = None,
    cwd: str | Path | None = None,
) -> GitHubClient:
    """Assemble a client for any repo+PR (used by the CLI and tests)."""
    resolved_repo = GitHubClient.resolve_repo(repo, cwd=str(cwd) if cwd else None)
    if transport is None:
        transport = build_transport(token=token, cwd=cwd)
    return GitHubClient(resolved_repo, transport)
