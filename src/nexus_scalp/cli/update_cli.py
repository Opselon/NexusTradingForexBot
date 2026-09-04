"""Update & release-metadata CLI surface (TASK-9).

WHERE/WHY: ``nexus update`` (check|latest|download|install|verify|status|history|
rollback|doctor + --manifest offline mode), its exit-code mapping helpers, the
release-info command and the shared ``_update_orchestrator`` factory. MECHANICAL
MOVE ONLY (CHG-0032 Step 1): update_cmd interacts with release verification paths
(BUG-160/161/171/173 history) — logic untouched while moving.

BOUNDARY: CLI presentation + exit-code policy for the update domain only. The update
ENGINE (plan/download/verify/install) lives in release/updater.py and is NOT touched
by this module.

USED BY: cli.main facade (registers commands), tests/unit/test_cli_end_to_end.py
(update contract incl. BUG-155 drift guards).

DO-NOT-PUT-HERE: verification/atomic-write logic (release/), doctor/forensic commands.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from nexus_scalp.cli.app_factory import _resolve_facade_seam, app
from nexus_scalp.cli.styling import _banner, _emit, _error_panel, _success_panel, console
from nexus_scalp.release import exit_codes as xc
from nexus_scalp.release import update as rupdate
from nexus_scalp.release import updater as rupdater
from nexus_scalp.release.metadata import get_version_info


# ---------------------------------------------------------------------------
# update — TASK-9 full user update surface
#   nexus update check | status | history | rollback | doctor
#   nexus update [--channel stable|beta|nightly] [--dry-run] [--force] [--yes] [--json]
# ---------------------------------------------------------------------------
def _update_orchestrator() -> rupdater.UpdateOrchestrator:
    info = _resolve_facade_seam("get_version_info", get_version_info)()
    return rupdater.UpdateOrchestrator(
        channel=info.get("channel") or "stable",
        architecture=info.get("architecture"),
        installed_version=info["version"],
        installed_commit=info.get("commit"),
    )


def _update_exit_code(report: dict[str, Any]) -> int:
    """Stable exit-code mapping for update commands (spec 36, additive).

    0 SUCCESS (COMPLETED / NO_UPDATE / ROLLED_BACK / FAILED_SAFE / IDLE)
    1 runtime/validation failure
    4 release verification failure (SHA256 / manifest / tamper)
    5 update not applicable / network / incompatible / security
    8 rollback
    """
    status = str(report.get("status") or report.get("state") or "")
    # BUG-173: FAILED_SAFE means the operation did NOT succeed (e.g. a
    # rollback with no backup). It must never read as success to scripted
    # callers. ROLLED_BACK stays a controlled success (exit 0).
    if status == "FAILED_SAFE":
        return xc.EXIT_RUNTIME
    ok_states = ("COMPLETED", "NO_UPDATE", "ROLLED_BACK", "IDLE")
    if status in ok_states:
        return xc.EXIT_OK
    if status == "ROLLED_BACK":
        return xc.EXIT_OK
    if report.get("error_code") in ("SHA256_MISMATCH",) or "verification" in status.lower():
        return xc.EXIT_UPDATE
    if status in ("UPDATE_VERIFICATION_FAILED",):
        return xc.EXIT_UPDATE
    if status in (
        "RELEASE_NOT_FOUND",
        "NETWORK_UNAVAILABLE",
        "NETWORK_ERROR",
        "GITHUB_UNAVAILABLE",
        "INCOMPATIBLE",
        "SECURITY_BLOCKED",
        "UPDATE_REJECTED",
        "FAILED",
        "UPDATE_IN_PROGRESS",
        "UPDATE_BLOCKED_WHILE_LIVE",
        "UPDATE_AVAILABLE",
    ):
        return xc.EXIT_UPDATE
    return xc.EXIT_UPDATE


def _update_json_exit(report: dict[str, Any], json_mode: bool, code: int | None = None) -> int:
    """Emit the update report and raise the mapped exit code."""
    code = code if code is not None else _update_exit_code(report)
    if json_mode:
        report = dict(report)
        report["exit_code"] = code
        _emit(report, True)
    raise typer.Exit(code) from None


def _update_human_check(report: dict[str, Any]) -> None:
    """Human-readable update-check output (spec 2/34) — client-friendly copy."""
    info = _resolve_facade_seam("get_version_info", get_version_info)()
    ch_disp = f"[cyan]{info.get('channel') or 'stable'}[/cyan]"
    status = str(report.get("status") or "UNKNOWN")
    # Friendly labels so a non-technical client instantly knows what it means.
    friendly = {
        "UPDATE_AVAILABLE": "Update available",
        "NO_UPDATE": "Up to date",
        "IDLE": "No action needed",
        "GITHUB_UNAVAILABLE": "GitHub temporarily unavailable",
        "NETWORK_ERROR": "Network error",
        "SECURITY_BLOCKED": "Security check blocked the update",
    }.get(status, status.replace("_", " ").title())
    status_style = (
        "green"
        if status == "UPDATE_AVAILABLE"
        else ("yellow" if status in ("NO_UPDATE", "IDLE") else "red")
    )
    console.print(_banner(subtitle=f"update check  ·  {ch_disp}"))
    # One-line plain-English headline — the client never has to decode codes.
    console.print(f"[bold]Result:[/bold] [{status_style}]{friendly}[/{status_style}]")
    cur = report.get("current_version") or "?"
    tgt = report.get("target_version") or cur
    if status == "UPDATE_AVAILABLE":
        console.print(
            f"[dim]Installed: {cur}  →  Available: [bold]{tgt}[/bold] — run [cyan]nexus update[/cyan] to install[/dim]"
        )
    elif status in ("NO_UPDATE", "IDLE"):
        console.print(f"[dim]Installed: {cur}  ·  Latest: {tgt} — nothing to do[/dim]")
    else:
        console.print(f"[dim]Installed: {cur}[/dim]")
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    table.add_column("Field", style="bold white", no_wrap=True)
    table.add_column("Value", style="dim")
    for k, label in (
        ("current_version", "Current version"),
        ("target_version", "Latest release"),
        ("tag", "Release tag"),
        ("commit_sha", "Commit SHA"),
        ("published_at", "Published at"),
        ("platform", "Platform"),
        ("architecture", "Architecture"),
        ("artifact_name", "Asset"),
        ("model_version", "Model version"),
        ("schema_version", "Model schema"),
        ("status", "Status"),
    ):
        v = report.get(k)
        if v:
            table.add_row(label, str(v))

    # Update-awareness rows (2026-09-02 UX pass): commit distance + real
    # change summary + last-checked timestamp, from the offline-safe
    # release-status truth (never fabricated). Rows are added BEFORE the
    # table renders (the first pass added them after console.print - the
    # rows silently never appeared). Failure-isolated.
    try:
        from nexus_scalp.release.release_status import (
            STATUS_NO_UPDATE,
            STATUS_REVISION_AHEAD,
            build_release_status,
        )

        rs = build_release_status()
        behind, ahead = rs.get("commits_behind"), rs.get("commits_ahead")
        cur_commit = rs.get("current_commit")
        if cur_commit:
            table.add_row("Current commit", str(cur_commit))
        if behind is not None:
            if behind or ahead:
                rel = f"{behind} behind" + (f" / {ahead} ahead" if ahead else "")
            else:
                rel = "UP TO DATE"
            table.add_row("Commit distance", rel)
        awareness_status = rs.get("update_status")
    except Exception:
        rs = {}
        awareness_status = None  # extras never break the core check

    console.print(table)

    if rs.get("changes"):
        console.print("[bold]Changes since current:[/bold]")
        for c in rs["changes"][:5]:
            console.print(f"  • {c}")
    if status == STATUS_NO_UPDATE and awareness_status == STATUS_REVISION_AHEAD:
        console.print(
            f"[yellow]◎ Local revision is {ahead} commit(s) ahead of origin "
            "(development build — nothing to update to).[/yellow]"
        )
    elif status == STATUS_NO_UPDATE:
        console.print(
            "[green]✓ Up to date — nothing to do. Your install is up to date with the latest release.[/green]"
        )
    if rs.get("generated_at"):
        console.print(f"[dim]Last checked: {rs['generated_at']}[/dim]")
    if report.get("decisions"):
        console.print("[dim]What was checked:[/dim]")
        for d in report.get("decisions", []):
            console.print(f"  [dim]· {d}[/dim]")
    # Friendly next-step footer so the client knows what to do without reading decisions.
    if status == "UPDATE_AVAILABLE":
        console.print(
            "[bold cyan]Next:[/bold cyan] run [cyan]nexus update[/cyan] to install, or [cyan]nexus update --dry-run[/cyan] to preview"
        )
    elif status in ("NO_UPDATE", "IDLE"):
        console.print("[dim]No action needed — you're current.[/dim]")
    console.print(
        Panel(f"[{status_style}]{friendly}  ·  {status}[/{status_style}]", border_style="cyan")
    )


@app.command("update")
def update_cmd(
    subcommand: str = typer.Argument(
        None, help="check | status | history | rollback | doctor (default: run the update)"
    ),
    manifest: Path | None = typer.Option(
        None, "--manifest", help="Path to available-release manifest (JSON) — offline mode."
    ),
    channel: str = typer.Option(
        "stable", "--channel", help="stable | beta | nightly (never silently switches)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview what would happen — never downloads or changes anything."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Authorize the safety-checked LIVE maintenance flow (only needed for live trading).",
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Skip interactive prompts (security checks still run)."
    ),
    json_mode: bool = typer.Option(
        False, "--json", help="Machine-readable output (for scripts and tools)."
    ),
    include_prerelease: bool = typer.Option(
        False,
        "--include-prerelease",
        help="Also consider pre-release versions (beta builds) — off by default.",
    ),
    allow_downgrade: bool = typer.Option(
        False,
        "--allow-downgrade",
        help="Allow installing an older version (use with care).",
    ),
    force_refresh: bool = typer.Option(
        False,
        "--force-refresh",
        help="Skip the local cache and ask GitHub again for the latest version.",
    ),
    fetch: bool = typer.Option(
        False,
        "--fetch",
        help="Update the local Git history before checking how far you are from the latest.",
    ),
    fresh: bool = typer.Option(
        False, "--force-refresh", help="Same as --force-refresh (kept for compatibility)."
    ),
) -> None:
    """Check, download, verify, install and health-check the newest release.

    nexus update            : run the full safe update flow
    nexus update check      : discovery only (never fabricates latest)
    nexus update latest     : authoritative fresh latest (bypasses cache, spec 19)
    nexus update download   : check + download + verify to staging (not installed)
    nexus update install    : install the staged/latest package
    nexus update verify     : verify the INSTALLED client (no download)
    nexus update status     : observable state machine + crash recovery
    nexus update history    : persisted update log
    nexus update rollback   : restore the prior application (user data intact)
    nexus update doctor     : verify github/disk/mode/db/config/process/lock
    """
    # --- validation: unknown channel must be a pretty error, never silent ---
    if channel not in ("stable", "beta", "nightly"):
        msg = f"unknown channel '{channel}' — use stable|beta|nightly"
        if json_mode:
            _emit({"error": msg, "hint": "Add --channel stable", "exit_code": xc.EXIT_USAGE}, True)
        else:
            console.print(
                _error_panel(
                    "Invalid channel", msg, hint="Use --channel stable", exit_code=xc.EXIT_USAGE
                )
            )
        raise typer.Exit(xc.EXIT_USAGE) from None

    # Offline manifest mode (build pipeline / tests): routed through the
    # SAME discovery/plan core (no duplicate update implementation, spec 55).
    if manifest is not None:
        if not manifest.exists():
            msg = f"manifest not found: {manifest}"
            if json_mode:
                _emit({"error": msg, "exit_code": xc.EXIT_RUNTIME}, True)
            else:
                console.print(
                    _error_panel(
                        "Manifest not found",
                        msg,
                        hint="Pass a JSON file produced by the release pipeline",
                        exit_code=xc.EXIT_RUNTIME,
                    )
                )
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        info = _resolve_facade_seam("get_version_info", get_version_info)()
        try:
            available = rupdate.load_available_releases(manifest)
        except Exception as e:
            if json_mode:
                _emit({"error": f"invalid manifest: {e}", "exit_code": xc.EXIT_RUNTIME}, True)
            else:
                console.print(_error_panel("Invalid manifest", str(e), exit_code=xc.EXIT_RUNTIME))
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        if isinstance(available, dict):
            available = {
                "assets": available.get("assets") or [],
                "tag_name": available.get("tag_name") or f"v{info['version']}",
                "prerelease": bool(available.get("prerelease")),
                "body": str(available.get("body") or ""),
            }
        plan = rupdater.UpdatePlanBuilder(
            installed_version=info["version"],
            channel=channel,
            architecture=info.get("architecture"),
            installed_commit=info.get("commit"),
            include_prerelease=include_prerelease,
            allow_downgrade=allow_downgrade,
        ).build(available)
        plan["channel"] = channel
        _update_json_exit(plan, json_mode)
        return

    orch = _update_orchestrator()

    if subcommand == "check":
        try:
            report = orch.check(
                include_prerelease=include_prerelease,
                allow_downgrade=allow_downgrade,
            )
        except Exception as e:
            if json_mode:
                _emit(
                    {"error": str(e), "status": "NETWORK_ERROR", "exit_code": xc.EXIT_UPDATE}, True
                )
            else:
                console.print(
                    _error_panel(
                        "Update check failed",
                        str(e),
                        hint="Check your internet / nexus update doctor",
                        exit_code=xc.EXIT_UPDATE,
                    )
                )
            raise typer.Exit(xc.EXIT_UPDATE) from None
        report["dry_run"] = True
        report["force_refresh"] = force_refresh
        if fetch:
            # Bounded, opt-in repo fetch so commit distance reflects the real
            # remote tip (the offline default reads the last-fetched ref).
            # Failure-isolated: network problems degrade to stale counts,
            # never fabricate.
            fr = subprocess.run(
                ["git", "fetch", "origin", "--quiet"],
                cwd=str(Path.cwd()),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            report["git_fetch"] = "ok" if fr.returncode == 0 else f"failed (rc={fr.returncode})"
        if not json_mode:
            _update_human_check(report)
        _update_json_exit(report, json_mode)
        return
    if subcommand == "latest":
        try:
            report = orch.latest(
                include_prerelease=include_prerelease,
            )
        except Exception as e:
            if json_mode:
                _emit(
                    {"error": str(e), "status": "NETWORK_ERROR", "exit_code": xc.EXIT_UPDATE}, True
                )
            else:
                console.print(
                    _error_panel(
                        "Update latest failed",
                        str(e),
                        hint="Try nexus update doctor",
                        exit_code=xc.EXIT_UPDATE,
                    )
                )
            raise typer.Exit(xc.EXIT_UPDATE) from None
        report["force_refresh"] = True  # latest ALWAYS bypasses cache (spec 19)
        if not json_mode:
            _update_human_check(report)
        _update_json_exit(report, json_mode)
        return
    if subcommand == "download":
        try:
            report = orch.download(include_prerelease=include_prerelease)
        except Exception as e:
            if json_mode:
                _emit(
                    {"error": str(e), "status": "NETWORK_ERROR", "exit_code": xc.EXIT_UPDATE}, True
                )
            else:
                console.print(
                    _error_panel(
                        "Download failed",
                        str(e),
                        hint="nexus update doctor  ·  check disk & network",
                        exit_code=xc.EXIT_UPDATE,
                    )
                )
            raise typer.Exit(xc.EXIT_UPDATE) from None
        report["force_refresh"] = force_refresh
        if not json_mode:
            if report.get("artifact_path"):
                console.print(
                    _success_panel(
                        "Download staged",
                        f"Target: {report.get('target_version')}  ·  Asset: {report.get('artifact_name')}\nStaged at {report.get('artifact_path')}\nSHA256: PASS",
                        border="green",
                    )
                )
            else:
                console.print(
                    _error_panel(
                        "Download not ready",
                        str(report.get("status")),
                        hint=" ".join(report.get("decisions", [])[:2]),
                    )
                )
                for d in report.get("decisions", []):
                    console.print(f"  [dim]> {d}[/dim]")
        _update_json_exit(report, json_mode)
        return
    if subcommand == "verify":
        try:
            report = orch.verify()
        except Exception as e:
            if json_mode:
                _emit(
                    {"error": str(e), "status": "VERIFY_FAILED", "exit_code": xc.EXIT_RELEASE}, True
                )
            else:
                console.print(_error_panel("Verify failed", str(e), exit_code=xc.EXIT_RELEASE))
            raise typer.Exit(xc.EXIT_RELEASE) from None
        if not json_mode:
            console.print(_banner(subtitle="verify installed client"))
            for c in report.get("checks", []):
                style = (
                    "green"
                    if c["verdict"] == "PASS"
                    else ("yellow" if c["verdict"] == "WARNING" else "red")
                )
                console.print(f"[{style}]{c['verdict']:8}[/{style}] {c['name']:24} {c['detail']}")
            console.print(
                Panel(
                    f"Verify: [bold]{report.get('status')}[/bold]",
                    border_style="green" if report.get("status") == "PASS" else "red",
                )
            )
        _update_json_exit(report, json_mode)
        return
    if subcommand == "status":
        report = orch.status()
        if json_mode:
            _emit(report, True)
        else:
            st = report["state"]
            rec = report.get("recovery", {})
            table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
            table.add_column("Field", style="bold white")
            table.add_column("Value")
            table.add_row("State", f"[bold]{st}[/bold]")
            table.add_row("Crashed", str(rec.get("crashed", False)))
            table.add_row("Recovery", str(rec.get("recovery", "n/a")))
            table.add_row("Lock held", str(report.get("lock_held", False)))
            table.add_row("Current", f"{report['current_version']} ({report['channel']})")
            console.print(_banner(subtitle="update status · state machine"))
            console.print(table)
        raise typer.Exit(xc.EXIT_OK) from None
    if subcommand == "history":
        rows = orch.history()
        if json_mode:
            _emit(rows, True)
        else:
            console.print(_banner(subtitle="update history"))
            if not rows:
                console.print(
                    Panel(
                        "[dim]No update history yet — first update will log here.[/dim]",
                        border_style="cyan",
                    )
                )
            for row in rows:
                console.print(
                    f"[dim]{row.get('timestamp', '?')[:19]}[/dim]  {row.get('from_version')} → "
                    f"[bold]{row.get('to_version')}[/bold]  [{row.get('channel')}]  {row.get('result')}"
                )
        raise typer.Exit(xc.EXIT_OK) from None
    if subcommand == "rollback":
        try:
            report = orch.rollback(reason="user-requested")
        except Exception as e:
            if json_mode:
                _emit({"error": str(e), "exit_code": xc.EXIT_RUNTIME}, True)
            else:
                console.print(
                    _error_panel(
                        "Rollback failed",
                        str(e),
                        hint="Check logs and nexus diagnostics",
                        exit_code=xc.EXIT_RUNTIME,
                    )
                )
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        if not json_mode:
            # BUG-173: rollback reports carry `state`, not `status`; the old
            # panel printed None on every rollback. Surface the real state
            # and, on failure, the actionable error instead of a green panel.
            state = str(report.get("state") or report.get("status") or "UNKNOWN")
            error_message = str(report.get("error_message") or "").strip()
            if state in ("ROLLED_BACK", "COMPLETED") and report.get("restored", True):
                console.print(_success_panel("Rollback", state))
            else:
                console.print(
                    _error_panel(
                        f"Rollback not performed (state: {state})",
                        error_message
                        or "No previous application snapshot available; user data untouched.",
                        hint="Create a backup first: nexus db backup (then retry the update)",
                        exit_code=_update_exit_code(report),
                    )
                )
        _update_json_exit(report, json_mode)
        return
    if subcommand == "doctor":
        try:
            report = orch.doctor()
        except Exception as e:
            if json_mode:
                _emit({"error": str(e), "exit_code": xc.EXIT_UPDATE}, True)
            else:
                console.print(
                    _error_panel("Update doctor failed", str(e), exit_code=xc.EXIT_UPDATE)
                )
            raise typer.Exit(xc.EXIT_UPDATE) from None
        if json_mode:
            _emit(report, True)
        else:
            console.print(_banner(subtitle="update doctor · pre-flight for updates"))
            for c in report["checks"]:
                style = (
                    "green"
                    if c["verdict"] == "PASS"
                    else ("yellow" if c["verdict"] == "WARNING" else "red")
                )
                console.print(f"[{style}]{c['verdict']:8}[/{style}] {c['name']:20} {c['reason']}")
            console.print(
                Panel(
                    f"Overall: [bold]{report['overall']}[/bold]",
                    border_style="green" if report["overall"] == "READY" else "red",
                )
            )
        raise typer.Exit(xc.EXIT_OK if report["overall"] == "READY" else xc.EXIT_UPDATE)

    if subcommand == "install":
        try:
            report = orch.install(
                yes=yes,
                force=force,
                allow_downgrade=allow_downgrade,
            )
        except Exception as e:
            if json_mode:
                _emit({"error": str(e), "exit_code": xc.EXIT_UPDATE}, True)
            else:
                console.print(
                    _error_panel(
                        "Install failed",
                        str(e),
                        hint="Try nexus update download first, then install",
                        exit_code=xc.EXIT_UPDATE,
                    )
                )
            raise typer.Exit(xc.EXIT_UPDATE) from None
        if not json_mode and report.get("error_message"):
            console.print(_error_panel("Install error", str(report.get("error_message"))))
        _update_json_exit(report, json_mode)
        return

    if subcommand not in (None, "run", "apply"):
        msg = f"unknown update subcommand '{subcommand}'"
        hint = "Use check|latest|download|install|verify|status|history|rollback|doctor"
        if json_mode:
            _emit({"error": msg, "hint": hint, "exit_code": xc.EXIT_USAGE}, True)
        else:
            console.print(
                _error_panel("Invalid update command", msg, hint=hint, exit_code=xc.EXIT_USAGE)
            )
        raise typer.Exit(xc.EXIT_USAGE) from None

    if dry_run:
        try:
            report = orch.dry_run()
        except Exception as e:
            if json_mode:
                _emit({"error": str(e), "exit_code": xc.EXIT_UPDATE}, True)
            else:
                console.print(_error_panel("Dry run failed", str(e), exit_code=xc.EXIT_UPDATE))
            raise typer.Exit(xc.EXIT_UPDATE) from None
        if json_mode:
            report["exit_code"] = (
                xc.EXIT_OK if report.get("status") == "UPDATE_AVAILABLE" else xc.EXIT_UPDATE
            )
            _emit(report, True)
        else:
            console.print(_banner(subtitle="dry run — nothing downloaded, nothing touched"))
            print(f"  Current : {report.get('current_version')}")
            print(f"  Target  : {report.get('target_version')}")
            print(f"  Channel : {report.get('channel')}")
            print(f"  Status  : {report.get('status')}")
            for d in report.get("decisions", []):
                print(f"    - {d}")
            if report.get("status") == "UPDATE_AVAILABLE":
                compat = report.get("compatibility", {})
                print(f"  Compatibility : {compat.get('verdict')}")
                print(f"  Backup size   : {report.get('backup_estimate_bytes', 0) // 1024} KB")
                print(
                    f"  Migration     : {'REQUIRED' if report.get('migration_required') else 'none'}"
                )
                print("  Restart       : REQUIRED")
                print("  Rollback      : AVAILABLE")
        raise typer.Exit(
            xc.EXIT_OK if report.get("status") == "UPDATE_AVAILABLE" else xc.EXIT_UPDATE
        )

    # Pretty run with live step panel
    def _human_event(state: str, detail: str) -> None:
        if state in (
            "DOWNLOADING",
            "VERIFYING",
            "BACKING_UP",
            "MIGRATING",
            "INSTALLING",
            "VERIFYING_INSTALL",
            "HEALTH_CHECK",
            "COMPLETED",
            "QUIESCING",
            "REDIRECTING",
            "ROLLED_BACK",
            "ROLLING_BACK",
        ):
            console.print(f"  [dim]{state.replace('_', ' ').title()}…[/dim] {detail}")

    try:
        # Live progress for the long phases
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold cyan]{task.description}[/bold cyan]"),
            BarColumn(style="cyan", complete_style="green"),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task("Updating Nexus…", total=None)
            report = orch.run(yes=yes, force=force, on_event=_human_event)
            progress.update(task, completed=1)
    except Exception as e:
        if json_mode:
            _emit({"error": str(e), "status": "FAILED", "exit_code": xc.EXIT_UPDATE}, True)
        else:
            console.print(
                _error_panel(
                    "Update failed",
                    str(e),
                    hint="Run nexus update doctor and nexus logs --errors",
                    exit_code=xc.EXIT_UPDATE,
                )
            )
        raise typer.Exit(xc.EXIT_UPDATE) from None

    if not json_mode:
        if report.get("status") == "COMPLETED":
            console.print(
                _success_panel(
                    "Update complete",
                    f"Now on {report.get('target_version')} — safe to start: nexus start",
                    border="green",
                )
            )
        else:
            # Client-friendly finished states: plain English, what happened,
            # and where to look next — never just "FAILED" + raw message.
            raw_status = str(report.get("status") or "UNKNOWN")
            friendly = {
                "FAILED": "Something went wrong",
                "FAILED_SAFE": "Stopped safely — nothing was changed",
                "NO_UPDATE": "Already up to date",
                "SECURITY_BLOCKED": "Security check blocked the update",
                "NETWORK_ERROR": "Network error",
                "GITHUB_UNAVAILABLE": "GitHub is temporarily unavailable",
            }.get(raw_status, raw_status.replace("_", " ").title())
            detail = str(report.get("error_message") or "").strip()
            # For zip/artifact errors the raw detail is terse — add context.
            if "not a valid zip" in detail.lower():
                detail = (
                    f"{detail} — the downloaded file didn't look like the "
                    "expected update package. Check your network/proxy and try "
                    "again, or run: nexus update check --json"
                )
            hint = None
            if raw_status in ("FAILED", "FAILED_SAFE"):
                hint = (
                    "Run: nexus update doctor  ·  nexus logs --errors  ·  nexus update check --json"
                )
            elif raw_status in ("NETWORK_ERROR", "GITHUB_UNAVAILABLE"):
                hint = "Check your internet connection, then: nexus update check"
            rollback_note = (
                "Your previous version was restored — safe to keep working"
                if report.get("rollback_completed")
                else ""
            )
            body = f"[bold]{friendly}[/bold]  [dim]({raw_status})[/dim]"
            if detail:
                body += f"\n{detail}"
            if rollback_note:
                body += f"\n\n[green]{rollback_note}[/green]"
            console.print(
                Panel(
                    body,
                    border_style="red"
                    if raw_status not in ("COMPLETED", "NO_UPDATE", "IDLE")
                    else "yellow",
                    title="Update finished",
                    subtitle="[dim]Tip: nexus update --help for all options[/dim]"
                    if hint
                    else None,
                )
            )
            if hint:
                console.print(f"[dim]→ {hint}[/dim]")
    _update_json_exit(report, json_mode)


def release_cmd(
    subcommand: str = typer.Argument(None, help="info — release metadata of the installed client"),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Release metadata of the installed client (spec 38).

    nexus release info : show the release record associated with the
    installed client (version, tag, commit, asset hash, model, schema).
    """
    if subcommand not in (None, "info"):
        msg = "unknown release subcommand — use info"
        if json_mode:
            _emit({"error": msg, "exit_code": xc.EXIT_USAGE}, True)
        else:
            console.print(
                _error_panel(
                    "Invalid release command",
                    msg,
                    hint="Try: nexus release info --json",
                    exit_code=xc.EXIT_USAGE,
                )
            )
        raise typer.Exit(xc.EXIT_USAGE) from None
    report = _update_orchestrator().release_info()
    if json_mode:
        report["exit_code"] = xc.EXIT_OK
        _emit(report, True)
    else:
        inst = report.get("installed_release") or {}
        console.print(_banner(subtitle="release metadata"))
        table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
        table.add_column("Field", style="bold white")
        table.add_column("Value", style="dim")
        table.add_row("Current version", str(report.get("current_version")))
        table.add_row("Current commit", str(report.get("current_commit") or "n/a"))
        table.add_row("Channel", str(report.get("channel")))
        table.add_row("Architecture", str(report.get("architecture")))
        if inst:
            table.add_row("Installed release", f"v{inst.get('version')}")
            table.add_row("Release tag", str(inst.get("tag")))
            table.add_row("Commit", str(inst.get("commit") or "n/a"))
            table.add_row("Asset", str(inst.get("asset_name")))
            table.add_row("Asset SHA256", f"{str(inst.get('asset_sha256') or '')[:16]}…")
            if inst.get("model_version"):
                table.add_row("Model version", str(inst["model_version"]))
            if inst.get("schema_version"):
                table.add_row("Model schema", str(inst["schema_version"]))
            if inst.get("feature_dimension"):
                table.add_row("Feature dimension", str(inst["feature_dimension"]))
            table.add_row("Installed at", str(inst.get("installed_at")))
        else:
            table.add_row("Installed release", "[dim]none recorded yet[/dim]")
        console.print(table)
    raise typer.Exit(xc.EXIT_OK) from None


def _register_release_command() -> None:
    """CHG-0032-A1 help-order parity: the monolith registered release AFTER
    forensic (update 1140 -> forensic 1639 -> release 1767). update_cli import
    happens between verify-release and forensic, so ``release`` must be
    registered late by the doctor module once forensic_cmd exists.
    Idempotent; no-op after first call."""
    if "release" not in {c.name for c in app.registered_commands}:
        app.command("release")(release_cmd)
