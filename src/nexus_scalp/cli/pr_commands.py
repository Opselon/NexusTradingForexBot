"""``nse pr`` — PR Evidence Reporter CLI (spec §2).

    nse pr report                 # discover the current PR, report once
    nse pr report --pr 492        # report a specific PR
    nse pr report --watch         # collect → publish → wait → re-collect
    nse pr report --refresh       # force re-collection (ignore cached state)
    nse pr report --json          # machine-readable result
    nse pr report --dry-run       # render the report, do not publish it

Follows the repo's Typer sub-app convention: register on the shared ``app``
from ``nexus_scalp.cli.app_factory`` (see ``api_commands.py`` / ``risk_commands.py``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import typer
from rich.panel import Panel

from nexus_scalp.cli.styling import _emit, console
from nexus_scalp.pr_evidence.collectors import EvidenceOptions
from nexus_scalp.pr_evidence.models import Status
from nexus_scalp.pr_evidence.reporter import (
    DEFAULT_POLL_SEC,
    DEFAULT_WATCH_TIMEOUT_SEC,
    Reporter,
    build_transport,
    make_client,
)

pr_app = typer.Typer(
    name="pr",
    help="PR evidence reporting: file-level failure intelligence inside any pull request.",
    no_args_is_help=True,
)


def _repo_dir() -> Path:
    return Path.cwd()


def _result_payload(result: Any) -> dict[str, Any]:
    collection = getattr(result, "collection", None)
    payload: dict[str, Any] = {
        "pr": result.pr,
        "status": result.status.value if hasattr(result.status, "value") else str(result.status),
        "failure_count": result.failure_count,
        "affected_files": result.affected_files,
        "published": result.published,
        "comment_action": result.comment_action,
        "comment_id": result.comment_id,
        "errors": result.errors,
    }
    if collection is not None:
        payload["checks"] = [
            {
                "name": c.name,
                "workflow": c.workflow,
                "job": c.job,
                "status": c.status,
                "conclusion": c.conclusion,
                "url": c.url,
            }
            for c in collection.checks
        ]
        payload["failures"] = [
            {
                "test": f.test,
                "file": f.location.rendered(),
                "production_file": (
                    f.production_location.rendered() if f.production_location is not None else None
                ),
                "error_type": f.error_type,
                "message": (f.message or "")[:200],
                "category": f.category.value,
                "check": f.check,
                "workflow": f.workflow,
                "job": f.job,
            }
            for f in collection.all_failures()
        ]
        payload["head"] = {
            "pr": collection.meta.pr_head_sha,
            "local": collection.meta.local_head_sha,
            "ci": collection.meta.ci_head_sha,
            "heads_match": collection.meta.heads_match,
        }
    return payload


@pr_app.command("report")
def report(
    pr: int = typer.Option(
        None, "--pr", "-p", help="PR number. Discovered automatically when omitted."
    ),
    watch: bool = typer.Option(
        False, "--watch", help="Re-collect and update the same comment until PASS or timeout."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Force a fresh collection pass."),
    local: bool = typer.Option(
        False, "--local", help="Also run the local gates (pytest/ruff/mypy)."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Emit a machine-readable result."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Render the report without publishing it."
    ),
    repo: str = typer.Option(
        None, "--repo", help="owner/name (default: resolved from the git remote)."
    ),
    poll: int = typer.Option(DEFAULT_POLL_SEC, "--poll-sec", help="Watch-mode polling interval."),
    timeout_sec: int = typer.Option(
        DEFAULT_WATCH_TIMEOUT_SEC, "--timeout-sec", help="Watch-mode total timeout."
    ),
) -> None:
    """Publish ONE file-level evidence report for a pull request."""
    _emit(_banner_text(), json_mode)
    transport = build_transport(use_gh=_prefer_gh())
    client = make_client(repo, transport=transport, cwd=_repo_dir())
    reporter = Reporter(
        client,
        repo_root=str(_repo_dir()),
        repo_dir=_repo_dir(),
        options=EvidenceOptions(
            fetch_annotations=True,
            fetch_codeql=True,
            local_tests=local,
            reviews=True,
            fetch_logs=False,
        ),
    )

    resolved_pr = client.discover_pr(pr, branch=_current_branch())
    if resolved_pr is None:
        msg = "Could not discover a PR number. Pass --pr <N> or run from a PR branch."
        if json_mode:
            _emit({"error": msg}, True)
        else:
            console.print(Panel.fit(msg, style="bold red"))
        raise typer.Exit(code=3)

    if watch:
        result = reporter.watch(
            resolved_pr, poll_sec=poll, timeout_sec=timeout_sec, local_python=_venv_python()
        )
    else:
        result = reporter.report_once(resolved_pr, publish=not dry_run, local_python=_venv_python())

    if json_mode:
        _emit(_result_payload(result), True)
    else:
        console.print(_summary_panel(result))
    if result.errors and not json_mode:
        for err in result.errors[:5]:
            console.print(f"  [red]gap:[/red] {err}")
    if result.status == Status.FAIL:
        raise typer.Exit(code=1)
    if result.status in (Status.UNKNOWN, Status.BLOCKED):
        raise typer.Exit(code=3)


def _banner_text() -> str:
    return "NSE PR Evidence Reporter"


def _current_branch() -> str | None:
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None


def _prefer_gh() -> bool:
    """Use the authenticated gh CLI unless a token is explicitly available."""
    return not bool(os.environ.get("NSE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"))


def _venv_python() -> str:
    venv = _repo_dir() / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.exists() else ""


def _summary_panel(result: Any) -> Panel:
    status = getattr(result, "status", Status.UNKNOWN)
    dot = getattr(status, "dot", "⚪")
    lines = [
        f"{dot} Status: {getattr(status, 'value', status)}",
        f"PR #{result.pr} · failures: {result.failure_count}",
        f"Affected files: {len(result.affected_files)}",
        f"Comment: {result.comment_action} (published={result.published})",
    ]
    for path in result.affected_files[:5]:
        lines.append(f"  • {path}")
    return Panel.fit("\n".join(lines), title="PR Evidence Report", border_style="cyan")


def register_pr_commands(app: typer.Typer) -> None:
    """Attach the ``pr`` sub-app to the canonical CLI tree."""
    app.add_typer(
        pr_app,
        name="pr",
        help="PR evidence reporting: file-level failure intelligence in one comment.",
    )
