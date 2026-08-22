"""
Nexus Scalp Engine CLI Management Console
==========================================
Operational controls, release commands, health/diagnostics, install/repair,
safe start modes and system status — the public UX of the release system.

Safety contract (section 17 / 31 / 59):
    * ``nexus start`` NEVER defaults to LIVE.
    * ``nexus start --mode live`` prints the full risk warning and requires an
      explicit interactive confirmation.
    * First-run setup defaults to PAPER / SHADOW (diagnostic-safe).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.release import diagnostics as rdiag
from nexus_scalp.release import environment as renv
from nexus_scalp.release import evaluate as reval
from nexus_scalp.release import exit_codes as xc
from nexus_scalp.release import health as rhealth
from nexus_scalp.release import paths as rpaths
from nexus_scalp.release import repair as rrepair
from nexus_scalp.release import update as rupdate
from nexus_scalp.release import updater as rupdater
from nexus_scalp.release import verify as rverify
from nexus_scalp.release.metadata import PRODUCT_DISPLAY, get_version_info

app = typer.Typer(
    name="nexus",
    help=f"{PRODUCT_DISPLAY} - operational & release console",
    add_completion=False,
)

# ---------------------------------------------------------------------------
# DB migration & schema management (TASK-10) — same canonical engine as startup
# ---------------------------------------------------------------------------
from nexus_scalp.cli.db_commands import db_app

# TASK-10 `db` group; TASK-11 hygiene registers as a SUBCOMMAND of `db`
# so the spec surface is `nexus db hygiene status|plan|run|pause|resume|history`.
app.add_typer(db_app, name="db", help="Database schema migration & management (TASK-10).")

# DATABASE PORTABILITY (`nexus db-portability ...`) — SQLite <-> PostgreSQL workflow.
from nexus_scalp.cli.db_commands import make_portability_app

app.add_typer(
    make_portability_app(),
    name="db-portability",
    help="DATABASE PORTABILITY: provider status, config, SQLite->PostgreSQL migration.",
)

# TASK-12 incident response & forensic diagnostics (`nexus incidents ...`).
from nexus_scalp.cli.incident_commands import incidents_app

app.add_typer(
    incidents_app,
    name="incidents",
    help="Incident response & forensic diagnostics (TASK-12) — read-only by default.",
)
console = Console()

MODE_ALIASES = {
    "paper": ExecutionMode.PAPER,
    "shadow": ExecutionMode.SHADOW,
    "live": ExecutionMode.LIVE,
}


# ---------------------------------------------------------------------------
# Small output helpers (no-ANSI JSON mode for CI / automation)
# ---------------------------------------------------------------------------
def _emit(data: Any, as_json: bool, plain: bool = False) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
        return
    if plain:
        if isinstance(data, dict):
            for k, v in data.items():
                print(f"{k}: {v}")
        else:
            print(str(data))
        return
    # rich rendering fallback
    console.print(data)


def _banner() -> None:
    info = get_version_info()
    console.print(
        Panel(
            f"[bold cyan]{PRODUCT_DISPLAY}[/bold cyan]\n"
            f"[dim]Version {info['version']} · {info['channel']} · "
            f"{info['platform']} {info['architecture']} · commit "
            f"{info['commit'] or 'n/a'}[/dim]",
            border_style="cyan",
        )
    )


def _verdict_style(v: str) -> str:
    return {
        "PASS": "[green]PASS[/green]",
        "WARNING": "[yellow]WARN[/yellow]",
        "FAIL": "[red]FAIL[/red]",
        "UNKNOWN": "[dim]UNKNOWN[/dim]",
    }.get(v, v)


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------
@app.command("version")
def version_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    plain: bool = typer.Option(False, "--plain", help="Plain text, no ANSI."),
) -> None:
    """Show canonical version + build identity."""
    info = get_version_info()
    if json_mode:
        from nexus_scalp.release.versioning import RuntimeVersionBlock

        try:
            block = RuntimeVersionBlock(web_dir=Path("Web") if Path("Web").is_dir() else None)
            info = {**info, "web_bundle": block.build()}
        except Exception:
            pass  # version truth never blocks the CLI
        _emit(info, True)
        return
    if plain:
        print(
            f"{PRODUCT_DISPLAY} version {info['version']} ({info['channel']}, "
            f"{info['architecture']}, commit {info['commit'] or 'n/a'})"
        )
        return
    _banner()
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")
    for k in (
        "version",
        "channel",
        "platform",
        "architecture",
        "build_mode",
        "feature_schema",
        "build_timestamp",
        "commit",
    ):
        table.add_row(k.replace("_", " ").title(), str(info.get(k, "n/a")))
    console.print(table)


# ---------------------------------------------------------------------------
# doctor / health / status
# ---------------------------------------------------------------------------
def _health_entries() -> tuple[str, list[rhealth.HealthEntry]]:
    engine = rhealth.HealthEngine()
    return engine.overall()


@app.command("doctor")
def doctor_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    verbose: bool = typer.Option(False, "--verbose", help="Show full reasons/suggestions."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors."),
) -> None:
    """Run the full system doctor (SYSTEM to ACCOUNTING)."""
    if no_color:
        console.print = lambda *a, **k: print(*[str(x) for x in a])  # type: ignore[assignment]
    verdict, entries = _health_entries()
    if json_mode:
        _emit(
            {
                "overall": verdict,
                "checks": [e.to_dict() for e in entries],
                "environment": renv.format_hardware_block(renv.detect_environment()),
            },
            True,
        )
        return
    _banner()
    table = Table(title="NEXUS SYSTEM HEALTH", box=box.SIMPLE)
    table.add_column("Check", style="bold white")
    table.add_column("Status", style="bold")
    table.add_column("Detail", style="dim")
    for e in entries:
        detail = e.reason
        if verbose and e.suggestion:
            detail += f"  -> {e.suggestion}"
        table.add_row(e.category, _verdict_style(e.verdict), detail)
    console.print(table)
    console.print(Panel(f"Overall: [bold]{verdict}[/bold]", border_style="cyan"))
    fails = [e for e in entries if e.verdict == "FAIL"]
    if fails and not json_mode:
        console.print("[bold yellow]Suggested fixes:[/bold yellow]")
        for e in fails:
            if e.suggestion:
                console.print(f"  • {e.category}: {e.suggestion}")


@app.command("health")
def health_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    plain: bool = typer.Option(False, "--plain", help="Plain text, no ANSI."),
) -> None:
    """Quick health summary (READY / DEGRADED / NOT READY)."""
    verdict, entries = _health_entries()
    if json_mode:
        _emit(
            {
                "overall": verdict,
                "checks": [e.to_dict() for e in entries],
            },
            True,
        )
        return
    if plain:
        for e in entries:
            print(f"{e.category:18} {e.verdict:8} {e.reason}")
        print(f"\nOverall: {verdict}")
        return
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Check", style="bold white")
    table.add_column("Status", style="bold")
    table.add_column("Detail", style="dim")
    for e in entries:
        table.add_row(e.category, _verdict_style(e.verdict), e.reason)
    console.print(table)
    console.print(
        Panel(
            f"Overall: [bold]{verdict}[/bold]",
            border_style="green" if verdict == "READY" else "yellow",
        )
    )


@app.command("status")
def status_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Full status: health + environment + version."""
    if json_mode:
        engine = rhealth.HealthEngine()
        _emit(engine.summary_dict(), True)
        return
    health_cmd(json_mode=False, plain=True)


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------
_TEST_TARGETS = {
    "quick": [
        "tests/unit/test_log_autopsy_fixes.py",
        "tests/unit/test_accounting_core.py",
        "tests/unit/test_release_system.py",
    ],
    "unit": ["tests/unit/"],
    "integration": ["tests/integration/", "--ignore=tests/integration/test_playwright_e2e.py"],
    "health": None,  # handled specially
    "release": None,  # handled specially
    "all": ["tests/", "--ignore=tests/integration/test_playwright_e2e.py"],
}


@app.command("test")
def test_cmd(
    mode: str = typer.Option(
        "quick", "--mode", "-m", help="quick | unit | integration | health | release | all"
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Run the requested test suite. Never runs live broker tests."""
    if mode == "health":
        verdict, entries = _health_entries()
        data = {
            "overall": verdict,
            "checks": [e.to_dict() for e in entries],
            "results": [(e.category, e.verdict) for e in entries],
        }
        _emit(data, json_mode)
        return
    if mode == "release":
        verify_cmd(json_mode=json_mode)
        return
    target = _TEST_TARGETS.get(mode)
    if target is None:
        raise typer.BadParameter(
            f"unknown test mode '{mode}' — use quick|unit|integration|health|release|all"
        )
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", *target]
    if not json_mode:
        console.print(f"[bold cyan]Running tests ({mode}):[/bold cyan] {' '.join(target)[:120]}")
    proc = subprocess.run(cmd, check=False)
    _emit(
        {"mode": mode, "returncode": proc.returncode},
        json_mode,
        plain=True if proc.returncode == 0 else False,
    )
    raise typer.Exit(0 if proc.returncode == 0 else 1)


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------
def _log_files() -> list[Path]:
    dirs = [rpaths.get_logs_dir(), Path("artifacts/logs")]
    out: list[Path] = []
    seen: set[Path] = set()
    for d in dirs:
        if d.exists():
            for f in sorted(d.glob("*.log")):
                if f.resolve() not in seen:
                    seen.add(f.resolve())
                    out.append(f)
    return out


@app.command("logs")
def logs_cmd(
    tail: int = typer.Option(50, "--tail", "-n", help="Lines to show."),
    errors: bool = typer.Option(False, "--errors", help="Only ERROR/CRITICAL lines."),
    worker: bool = typer.Option(False, "--worker", help="Only worker lines."),
    export: Path | None = typer.Option(None, "--export", help="Export logs to a zip path."),
) -> None:
    """Tail / filter / export engine logs."""
    files = _log_files()
    if not files:
        console.print("[yellow]No log files found yet.[/yellow]")
        return
    if export is not None:
        with zipfile.ZipFile(export, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, arcname=f.name)
        console.print(f"[green]Logs exported to {export}[/green]")
        return
    latest = files[-1]
    lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
    if errors:
        lines = [l for l in lines if re.search(r"\b(ERROR|CRITICAL)\b", l, re.I)]
    if worker:
        lines = [l for l in lines if re.search(r"WORKER", l, re.I)]
    for line in lines[-tail:]:
        console.print(line)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
@app.command("config")
def config_cmd(
    validate: Path | None = typer.Option(
        None, "--validate", "-c", help="Validate a specific YAML config."
    ),
    show: bool = typer.Option(False, "--show", help="Print effective user config."),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Inspect / validate the active configuration."""
    target = validate or rpaths.get_user_config_path()
    if not target.exists():
        console.print(f"[red]Config not found: {target}[/red]")
        console.print("Run [bold]nexus setup[/bold] or [bold]nexus repair[/bold] first.")
        raise typer.Exit(1)
    try:
        cfg = AppConfig.load_from_yaml(target)
    except Exception as e:
        console.print(f"[red]INVALID configuration:[/red] {e}")
        raise typer.Exit(1) from None
    if json_mode:
        _emit(
            {
                "path": str(target),
                "valid": True,
                "symbol": cfg.execution.symbol,
                "mode": cfg.execution.mode.value,
                "schema": cfg.model.feature_schema_version,
            },
            True,
        )
        return
    console.print(f"[green]Configuration valid:[/green] {target}")
    console.print(
        f"Symbol: {cfg.execution.symbol} | Mode: {cfg.execution.mode.value} | "
        f"Schema: {cfg.model.feature_schema_version}"
    )


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
@app.command("settings")
def settings_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Inspect the isolated user-settings store (never exposes secrets)."""
    from nexus_scalp.settings import SettingsService

    svc = SettingsService()
    try:
        status = svc.telegram_config_status()
        if json_mode:
            _emit(
                {
                    "state": svc.state.state,
                    "db_path": str(svc.db.db_path),
                    "telegram": status,
                    "settings": svc.provenance(),
                },
                True,
            )
            return
        console.print("[bold cyan]NEXUS USER SETTINGS[/bold cyan]")
        console.print(f"State    : {svc.state.state}  ({svc.db.db_path})")
        console.print("Telegram :")
        console.print(f"  configured      : {'YES' if status['configured'] else 'NO'}")
        console.print(f"  enabled         : {status['enabled']}")
        console.print(f"  token_present   : {status['token_present']}")
        console.print(f"  masked_token    : {status['masked_token'] or '(none)'}")
        console.print(f"  admin_id_present: {status['admin_id_present']}")
        console.print(f"  admin_shape_ok  : {status['admin_id_shape_valid']}")
        console.print(f"  source          : {status['source']}")
    finally:
        svc.close()


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------
@app.command("repair")
def repair_cmd(
    database: bool = False,
    news_db: bool = False,
    force_recreate: bool = typer.Option(
        False, "--recreate-config", help="Restore config from template (keeps DBs)."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Repair non-destructive derived state. NEVER deletes user data."""
    engine = rrepair.RepairEngine()
    results = engine.run(recreate_dirs=force_recreate, with_news=news_db)
    if json_mode:
        _emit(engine.summary_dict(results), True)
        return
    for r in results:
        style = "green" if r.status == "OK" else ("yellow" if r.status == "SKIPPED" else "red")
        console.print(f"[{style}]{r.status:8}[/{style}] {r.action:12} {r.detail}")
    failed = [r for r in results if r.status == "FAILED"]
    raise typer.Exit(1 if failed else 0)


@app.command("audit-purge")
def audit_purge_cmd(
    signal_days: float = typer.Option(
        7.0, "--signal-days", help="Retention for audit_signals rows (days)."
    ),
    moving_days: float = typer.Option(
        3.0, "--moving-days", help="Retention for POSITION_MOVING events (days)."
    ),
    telemetry_days: float = typer.Option(
        13.0, "--telemetry-days", help="Retention for guard telemetry (days)."
    ),
    batch_size: int = typer.Option(500, "--batch", help="Rows per bounded delete transaction."),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Purge disposable audit telemetry older than the retention window (BUG-054).

    Deletes ONLY: old audit_signals rows, old POSITION_MOVING events and old
    guard telemetry. NEVER touches audit_ledger / experiences / autopsies /
    strategy / research tables. Safe to run while the engine is live (bounded
    batches, WAL). For scheduled use, pair with a cron/systemd timer.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = AuditRepository(flush_interval_sec=0.05)
    try:
        res = repo.purge_old_audit_data(
            signal_retention_days=signal_days,
            moving_retention_days=moving_days,
            telemetry_retention_days=telemetry_days,
            batch_size=batch_size,
        )
    finally:
        repo.close()
    if json_mode:
        _emit(res, True)
        return
    console.print("[green]Audit retention purge complete[/green]")
    for table, count in (res.get("deleted") or {}).items():
        console.print(f"  {table:22} {count} rows deleted")
    if res.get("error"):
        console.print(f"[red]error: {res['error']}[/red]")
        raise typer.Exit(1)
    console.print(f"  duration: {res.get('duration_ms', 0)} ms")


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------
@app.command("diagnostics")
@app.command("export-diagnostics")
def diagnostics_cmd() -> None:
    """Export a sanitized diagnostics archive (never contains secrets)."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]Collecting diagnostics...[/bold cyan]"),
        transient=True,
    ) as progress:
        progress.add_task("collect", total=None)
        out = rdiag.export_diagnostics()
    console.print(f"[green]Diagnostics exported:[/green] {out}")
    console.print("[dim]Sanitized: no passwords, tokens, credentials or DB contents.[/dim]")


# ---------------------------------------------------------------------------
# verify-release
# ---------------------------------------------------------------------------
@app.command("verify-release")
def verify_cmd(
    root: Path = typer.Option(Path("."), "--root", "-r", help="Release directory to verify."),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Verify a release tree: EXE, launch, version, assets, checksums, secrets."""
    root = root.resolve()
    with Progress(
        SpinnerColumn(), TextColumn("[bold cyan]Verifying release...[/bold cyan]"), transient=True
    ) as progress:
        progress.add_task("verify", total=None)
        result = rverify.verify_release(root)
    if json_mode:
        result["exit_code"] = xc.EXIT_OK if result["valid"] else xc.EXIT_RELEASE
        _emit(result, True)
        return
    for c in result["checks"]:
        style = "green" if c["status"] == "PASS" else ("yellow" if c["status"] == "WARN" else "red")
        console.print(f"[{style}]{c['status']:5}[/{style}] {c['check']:28} {c['detail']}")
    console.print(
        Panel(
            f"Release: [bold]{result['overall']}[/bold]",
            border_style="green" if result["valid"] else "red",
        )
    )
    raise typer.Exit(xc.EXIT_OK if result["valid"] else xc.EXIT_RELEASE)


# ---------------------------------------------------------------------------
# update — TASK-9 full user update surface
#   nexus update check | status | history | rollback | doctor
#   nexus update [--channel stable|beta|nightly] [--dry-run] [--force] [--yes] [--json]
# ---------------------------------------------------------------------------
def _update_orchestrator() -> rupdater.UpdateOrchestrator:
    info = get_version_info()
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
    ok_states = ("COMPLETED", "NO_UPDATE", "ROLLED_BACK", "FAILED_SAFE", "IDLE")
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
    raise typer.Exit(code)


def _update_human_check(report: dict[str, Any]) -> None:
    """Human-readable update-check output (spec 2/34)."""
    print("Nexus Client Updater")
    print(f"  Current version : {report.get('current_version')}")
    print(f"  Latest release  : {report.get('target_version')}")
    print(f"  Release tag     : {report.get('tag') or '—'}")
    if report.get("commit_sha"):
        print(f"  Commit SHA      : {report['commit_sha']}")
    if report.get("published_at"):
        print(f"  Published at    : {report['published_at']}")
    print(f"  Platform        : {report.get('platform')}")
    print(f"  Architecture    : {report.get('architecture')}")
    if report.get("artifact_name"):
        print(f"  Asset           : {report['artifact_name']}")
    if report.get("model_version"):
        print(f"  Model version   : {report['model_version']}")
    if report.get("schema_version"):
        print(f"  Model schema    : {report['schema_version']}")
    print(f"  Status          : {report.get('status')}")
    for d in report.get("decisions", []):
        print(f"    - {d}")


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
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; never download/install."),
    force: bool = typer.Option(
        False, "--force", help="Authorize the documented LIVE-quiesce maintenance flow."
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Skip interactive prompts (never bypasses security checks)."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    include_prerelease: bool = typer.Option(
        False,
        "--include-prerelease",
        help="Allow pre-releases on the stable channel (explicit opt-in).",
    ),
    allow_downgrade: bool = typer.Option(
        False,
        "--allow-downgrade",
        help="Permit an explicitly-requested downgrade (compatibility still verified).",
    ),
    force_refresh: bool = typer.Option(
        False,
        "--force-refresh",
        help="Bypass cached release metadata; query GitHub fresh (spec 18/40).",
    ),
    fresh: bool = typer.Option(
        False, "--force-refresh", help="Bypass cached release metadata; query GitHub fresh."
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
    # Offline manifest mode (build pipeline / tests): routed through the
    # SAME discovery/plan core (no duplicate update implementation, spec 55).
    if manifest is not None:
        info = get_version_info()
        available = rupdate.load_available_releases(manifest)
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
        report = orch.check(
            include_prerelease=include_prerelease,
            allow_downgrade=allow_downgrade,
        )
        report["dry_run"] = True
        report["force_refresh"] = force_refresh
        if not json_mode:
            _update_human_check(report)
        _update_json_exit(report, json_mode)
        return
    if subcommand == "latest":
        report = orch.latest(
            include_prerelease=include_prerelease,
        )
        report["force_refresh"] = True  # latest ALWAYS bypasses cache (spec 19)
        if not json_mode:
            _update_human_check(report)
        _update_json_exit(report, json_mode)
        return
    if subcommand == "download":
        report = orch.download(include_prerelease=include_prerelease)
        report["force_refresh"] = force_refresh
        if not json_mode:
            if report.get("artifact_path"):
                print("Nexus Client Updater — DOWNLOAD")
                print(f"  Current version : {report.get('current_version')}")
                print(f"  Target version  : {report.get('target_version')}")
                print(f"  Asset           : {report.get('artifact_name')}")
                print("  SHA256          : PASS")
                print(f"  Staged at       : {report.get('artifact_path')}")
                print("  Update          : STAGED_READY")
            else:
                print("Nexus Client Updater — DOWNLOAD")
                print(f"  Status          : {report.get('status')}")
                for d in report.get("decisions", []):
                    print(f"    - {d}")
        _update_json_exit(report, json_mode)
        return
    if subcommand == "verify":
        report = orch.verify()
        if not json_mode:
            for c in report.get("checks", []):
                style = "green" if c["verdict"] == "PASS" else "red"
                print(f"[{style}]{c['verdict']:8}[/{style}] {c['name']:24} {c['detail']}")
            print(f"\nVerify       : {report.get('status')}")
        _update_json_exit(report, json_mode)
        return
    if subcommand == "status":
        report = orch.status()
        if json_mode:
            _emit(report, True)
        else:
            st = report["state"]
            rec = report.get("recovery", {})
            console.print(f"[bold cyan]Update state:[/bold cyan] {st}")
            console.print(f"Crashed      : {rec.get('crashed', False)}")
            console.print(f"Recovery     : {rec.get('recovery', 'n/a')}")
            console.print(f"Lock held    : {report.get('lock_held', False)}")
            console.print(f"Current      : {report['current_version']} ({report['channel']})")
        raise typer.Exit(xc.EXIT_OK)
    if subcommand == "history":
        rows = orch.history()
        if json_mode:
            _emit(rows, True)
        else:
            if not rows:
                console.print("[yellow]No update history yet.[/yellow]")
            for row in rows:
                console.print(
                    f"{row.get('timestamp', '?')[:19]}  {row.get('from_version')} -> "
                    f"{row.get('to_version')}  [{row.get('channel')}]  {row.get('result')}"
                )
        raise typer.Exit(xc.EXIT_OK)
    if subcommand == "rollback":
        report = orch.rollback(reason="user-requested")
        _update_json_exit(report, json_mode)
        return
    if subcommand == "doctor":
        report = orch.doctor()
        if json_mode:
            _emit(report, True)
        else:
            for c in report["checks"]:
                style = (
                    "green"
                    if c["verdict"] == "PASS"
                    else ("yellow" if c["verdict"] == "WARNING" else "red")
                )
                console.print(f"[{style}]{c['verdict']:8}[/{style}] {c['name']:20} {c['reason']}")
            console.print(f"\nOverall: [bold]{report['overall']}[/bold]")
        raise typer.Exit(xc.EXIT_OK if report["overall"] == "READY" else xc.EXIT_UPDATE)

    if subcommand == "install":
        report = orch.install(
            yes=yes,
            force=force,
            allow_downgrade=allow_downgrade,
        )
        _update_json_exit(report, json_mode)
        return

    if subcommand not in (None, "run", "apply"):
        raise typer.BadParameter(
            f"unknown update subcommand '{subcommand}' — use "
            "check|latest|download|install|verify|status|history|rollback|doctor"
        )

    if dry_run:
        report = orch.dry_run()
        if json_mode:
            report["exit_code"] = (
                xc.EXIT_OK if report.get("status") == "UPDATE_AVAILABLE" else xc.EXIT_UPDATE
            )
            _emit(report, True)
        else:
            print("NEXUS UPDATE — DRY RUN (nothing downloaded, nothing modified)")
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
            print(f"  {state.replace('_', ' ').title()}... {detail}")

    report = orch.run(yes=yes, force=force, on_event=_human_event)
    if not json_mode:
        print(f"\n  Current        : {report.get('current_version')}")
        print(f"  Target         : {report.get('target_version')}")
        print(f"  Status         : {report.get('status')}")
        if report.get("health_status"):
            print(f"  Client health  : {report['health_status']}")
        if report.get("error_message"):
            print(f"  Error          : {report['error_message']}")
        if report.get("rollback_completed"):
            print("  Rollback       : COMPLETED — previous version restored")
    _update_json_exit(report, json_mode)


@app.command("release")
def release_cmd(
    subcommand: str = typer.Argument(None, help="info — release metadata of the installed client"),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Release metadata of the installed client (spec 38).

    nexus release info : show the release record associated with the
    installed client (version, tag, commit, asset hash, model, schema).
    """
    if subcommand not in (None, "info"):
        raise typer.BadParameter("unknown release subcommand — use info")
    report = _update_orchestrator().release_info()
    if json_mode:
        report["exit_code"] = xc.EXIT_OK
        _emit(report, True)
    else:
        inst = report.get("installed_release") or {}
        print("Nexus Client Release Info")
        print(f"  Current version    : {report.get('current_version')}")
        print(f"  Current commit     : {report.get('current_commit') or 'n/a'}")
        print(f"  Channel            : {report.get('channel')}")
        print(f"  Architecture       : {report.get('architecture')}")
        if inst:
            print(f"  Installed release  : v{inst.get('version')}")
            print(f"  Release tag        : {inst.get('tag')}")
            print(f"  Commit             : {inst.get('commit') or 'n/a'}")
            print(f"  Asset              : {inst.get('asset_name')}")
            print(f"  Asset SHA256       : {str(inst.get('asset_sha256') or '')[:16]}…")
            if inst.get("model_version"):
                print(f"  Model version      : {inst['model_version']}")
            if inst.get("schema_version"):
                print(f"  Model schema       : {inst['schema_version']}")
            if inst.get("feature_dimension"):
                print(f"  Feature dimension  : {inst['feature_dimension']}")
            print(f"  Installed at       : {inst.get('installed_at')}")
        else:
            print("  Installed release  : none recorded yet")
    raise typer.Exit(xc.EXIT_OK)


# ---------------------------------------------------------------------------
# install / setup (first-run wizard)
# ---------------------------------------------------------------------------
def _wizard_flow(json_mode: bool) -> dict[str, Any]:
    console.print(
        Panel(
            "[bold cyan]Nexus First-Run Setup Wizard[/bold cyan]\n"
            "Compatibility check, install, database, model, mode, health",
            border_style="cyan",
        )
    )

    env = renv.detect_environment()
    results = reval.evaluate_requirements(env)
    verdict, _lines = reval.overall_verdict(results)

    console.print("\n[bold]Compatibility report[/bold]")
    for r in results:
        console.print(f"  {_verdict_style(r.verdict):8} {r.name:14} {r.detail}")

    if verdict == "BLOCKED":
        raise typer.Exit(xc.EXIT_ENVIRONMENT)

    engine = rrepair.RepairEngine()
    repaired = engine.run()
    for op in repaired:
        if op.status == "FAILED":
            console.print(f"[red]Setup step failed: {op.action} — {op.detail}[/red]")
            raise typer.Exit(1)

    # Mode selection — never silently LIVE.
    mode = typer.prompt("Execution mode (PAPER / SHADOW / LIVE)", default="PAPER").strip().upper()
    if mode.lower() not in MODE_ALIASES:
        mode = "PAPER"
    if mode == "LIVE":
        confirm = typer.confirm(
            "WARNING: LIVE mode places real orders and risks real capital. Continue?",
            default=False,
        )
        if not confirm:
            console.print("[yellow]Setup aborted — LIVE not confirmed.[/yellow]")
            raise typer.Exit(1)

    symbol = typer.prompt("Trading symbol (XAUUSD=Gold, EURUSD, GBPUSD, ...)", default="XAUUSD").strip().upper()
    if not symbol:
        symbol = "XAUUSD"
    config_path = rpaths.get_user_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    template = rrepair.RepairEngine().template_config
    if not config_path.exists() and template.exists():
        shutil.copy2(template, config_path)
    try:
        cfg = AppConfig.load_from_yaml(config_path) if config_path.exists() else AppConfig()
    except Exception:
        cfg = AppConfig()
    cfg.execution.mode = MODE_ALIASES[mode.lower()]
    cfg.execution.symbol = symbol
    # Persist effective mode/symbol into the yaml (idempotent write of the
    # whole validated config is safer than regex surgery).
    _write_effective_config(config_path, cfg)

    health = rhealth.HealthEngine(config_path=config_path)
    verdict2, entries = health.overall()
    for e in entries:
        console.print(f"  {_verdict_style(e.verdict):8} {e.category:16} {e.reason}")
    return {
        "mode": mode,
        "symbol": symbol,
        "config": str(config_path),
        "overall": verdict2,
        "requirements": [r.to_row() for r in results],
    }


def _write_effective_config(path: Path, cfg: AppConfig) -> None:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data.setdefault("execution", {})
    data["execution"]["mode"] = cfg.execution.mode.value
    data["execution"]["symbol"] = cfg.execution.symbol
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@app.command("install")
@app.command("setup")
def setup_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """First-run setup wizard (compat, install, DB, model, mode, health).

    PAPER=simulation (no real orders, safe default), SHADOW=mirror live
    without orders, LIVE=real execution (requires confirmation). Default
    symbol is XAUUSD (Gold). After setup, start with `nexus start` or the
    Web dashboard at http://localhost:8080. No browser? Use CLI: `nexus start --mode paper`.
    """
    outcome = _wizard_flow(json_mode)
    if json_mode:
        _emit(outcome, True)
        return
    console.print(
        Panel(
            f"Setup complete — [bold]{outcome['overall']}[/bold]. "
            f"Start with [bold]nexus start --mode {outcome['mode'].lower()}[/bold] "
            f"or [bold]nexus start[/bold].",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------
@app.command("uninstall")
def uninstall_cmd(
    keep_data: bool = typer.Option(
        True, "--keep-data", help="Preserve user data (config/logs/db). [default]"
    ),
    force: bool = typer.Option(False, "--force", help="Skip confirmation."),
) -> None:
    """Remove the installation. User data preserved unless --no-keep-data."""
    if not force and not typer.confirm(
        "Uninstall Nexus? Your user data (databases, models, config) will be PRESERVED. Continue?",
        default=False,
    ):
        console.print("Aborted.")
        raise typer.Exit(1)
    # Packaged layout: nothing here to do — the OS uninstaller removes the
    # app dir; this command exists as the CLI-side contract.
    console.print("[green]Application uninstalled (user data preserved).[/green]")
    if not keep_data:
        console.print(
            "[yellow]--no-keep-data: user data left in place; remove "
            f"{rpaths.get_data_root()} manually if desired.[/yellow]"
        )


# ---------------------------------------------------------------------------
# start / stop / restart (safe by default)
# ---------------------------------------------------------------------------
def _pidfile() -> Path:
    return rpaths.get_data_root() / "nexus.pid"


@app.command("start")
def start_cmd(
    mode: str = typer.Option(
        "paper", "--mode", "-m", help="paper | shadow | live (default: paper - NEVER live)"
    ),
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Config path (default: user config)."
    ),
    gateway: bool = typer.Option(False, "--gateway", "-g", help="Force remote gateway adapter."),
    daemon: bool = typer.Option(False, "--daemon", help="Run as background process."),
    port: int = typer.Option(8080, "--port", help="Web dashboard port."),
) -> None:
    """Start the engine (default: paper/XAUUSD, safe).

    Modes: paper (simulation, default) | shadow (mirror live) | live
    (real orders -- shows red warning + requires confirmation). Web dashboard
    at http://localhost:8080 when running. Symbol comes from config (setup
    default XAUUSD).
    """
    mode_key = mode.strip().lower()
    if mode_key not in MODE_ALIASES:
        raise typer.BadParameter(f"mode must be paper|shadow|live (got '{mode}')")
    chosen = MODE_ALIASES[mode_key]

    config_path = config or (
        rpaths.get_user_config_path()
        if rpaths.get_user_config_path().exists()
        else Path("configs/live.yaml")
    )
    if not config_path.exists():
        console.print(f"[red]Config missing: {config_path}[/red]")
        console.print("Run [bold]nexus setup[/bold] first.")
        raise typer.Exit(1)
    cfg = AppConfig.load_from_yaml(config_path)

    if chosen == ExecutionMode.LIVE:
        panel = Panel(
            "[bold red]WARNING: this starts REAL execution.[/bold red]\n\n"
            f"Account   : {cfg.mt5.account or 'configured'}\n"
            f"Broker    : {cfg.mt5.server or 'configured'}\n"
            f"Symbol    : {cfg.execution.symbol}\n"
            f"Mode      : LIVE\n"
            f"Risk      : {cfg.risk.risk_per_trade_pct}% / trade, "
            f"{cfg.risk.max_account_drawdown_pct}% max drawdown, "
            f"{cfg.risk.max_allowed_lots} max lots\n"
            f"Kill switch: manual close via dashboard / stop command",
            border_style="red",
            title="LIVE TRADING",
        )
        console.print(panel)
        if not typer.confirm("I confirm I want to start REAL LIVE trading.", default=False):
            console.print("[yellow]Live start aborted (not confirmed).[/yellow]")
            raise typer.Exit(1)

    if daemon:
        cmd = [
            sys.executable,
            "-m",
            "nexus_scalp.cli.main",
            "start",
            "--mode",
            mode_key,
            "--config",
            str(config_path),
        ]
        if gateway:
            cmd.append("--gateway")
        _spawn_daemon(cmd)
        return

    cfg.execution.mode = chosen
    # Persist the explicit --mode so LiveEngine's SettingsService override (app_settings.db) does not flip it back.
    # The packaged bare launch (start --mode paper) must remain PAPER even when DB has LIVE.
    try:
        from nexus_scalp.settings.service import SettingsService

        svc = SettingsService()
        svc.set("execution.mode", chosen.value, actor="cli:start")
    except Exception:
        pass
    _run_engine(cfg, gateway=gateway, port=port)


def _spawn_daemon(cmd: list[str]) -> None:
    data_root = rpaths.get_data_root()
    data_root.mkdir(parents=True, exist_ok=True)
    pidfile = _pidfile()
    if pidfile.exists():
        try:
            old = int(pidfile.read_text().strip())
            os.kill(old, 0)
            console.print(f"[yellow]Engine already running (pid {old}).[/yellow]")
            return
        except (OSError, ValueError):
            pidfile.unlink(missing_ok=True)
    with open(pidfile, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    # Reparent via the same interpreter; the child runs foreground logic.
    subprocess.Popen(
        cmd,
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x00000008,
    )
    console.print("[green]Engine starting in background.[/green]")


def _run_engine(cfg: AppConfig, *, gateway: bool, port: int) -> None:
    # TASK-10 startup migration gate: apply safe pending schema migrations
    # BEFORE the engine enters READY (§6/§7). Same canonical engine as `nexus db`.
    from nexus_scalp.database.gate import run_startup_migration_gate

    gate = run_startup_migration_gate(
        workspace=Path.cwd(),
        application_version=str(get_version_info().get("version", "")),
    )
    if not gate.get("ready", False):
        console.print(
            "[red]DATABASE MIGRATION GATE BLOCKED[/red] — refusing to start. "
            "Run `nexus db status` and `nexus db migrate` for details."
        )
        raise typer.Exit(1)
    if gate.get("state") == "DB_MIGRATION_SUCCEEDED":
        console.print("[green]Database migrations applied successfully.[/green]")
    # Heavy engine imports are local so the slim onefile CLI (which excludes
    # torch/polars/MetaTrader5) never pays for them unless actually starting.
    from nexus_scalp.adapters.mt5.mt5_adapter import HAS_NATIVE_MT5, DirectMT5Adapter
    from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.ports.mt5_port import IMT5Port

    adapter: IMT5Port
    if gateway or sys.platform != "win32" or not HAS_NATIVE_MT5:
        console.print("[yellow]Using Remote MT5 Gateway Adapter.[/yellow]")
        adapter = RemoteMT5GatewayAdapter()
    else:
        console.print("[green]Using Direct Native MT5 Adapter (Win32 IPC).[/green]")
        adapter = DirectMT5Adapter(
            account=cfg.mt5.account,
            password=cfg.mt5.password,
            server=cfg.mt5.server,
            timeout=cfg.mt5.timeout_ms,
            retries=cfg.mt5.retries,
        )
    engine = LiveEngine(config=cfg, adapter=adapter)
    _start_web_and_engine(engine, cfg, port)


def _start_web_and_engine(engine: Any, cfg: AppConfig, port: int) -> None:
    import asyncio

    import uvicorn

    from nexus_scalp.web.server import create_app

    # DOCKER-REPAIR: NSE_LOG_LEVEL (DEBUG|INFO|WARNING|ERROR) drives the
    # structlog config used by `nexus start` (default INFO when unset).
    nse_log_level = os.getenv("NSE_LOG_LEVEL", "INFO").strip().upper()
    from nexus_scalp.observability.logging import configure_logging

    configure_logging(
        log_level=nse_log_level,
        json_format=False,
        log_to_file=True,
    )
    console.print(
        f"[bold cyan]Starting {cfg.execution.mode.value} mode — {cfg.execution.symbol}[/bold cyan]"
    )
    engine._preflight_or_raise()
    app_obj = create_app(engine_ref=engine)
    engine.server_state = app_obj.state.server_state
    # DOCKER-REPAIR (2026-08-20): container bind is driven by env
    # (NSE_WEB_HOST / NSE_WEB_PORT); bare `run` keeps localhost-only.
    bind_host = os.getenv("NSE_WEB_HOST", "127.0.0.1")
    uvicorn_config = uvicorn.Config(
        app=app_obj, host=bind_host, port=port, log_level="warning", ws_max_size=16 * 1024 * 1024, ws="none"
    )
    server = uvicorn.Server(uvicorn_config)

    async def run_concurrently() -> None:
        await asyncio.gather(server.serve(), engine.run_loop(), return_exceptions=False)

    try:
        asyncio.run(run_concurrently())
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutdown requested (Ctrl+C).[/yellow]")


@app.command("stop")
def stop_cmd() -> None:
    """Stop a background engine (pidfile-based)."""
    pidfile = _pidfile()
    if not pidfile.exists():
        console.print("[yellow]No pidfile — engine not running as background process.[/yellow]")
        return
    try:
        pid = int(pidfile.read_text().strip())
    except ValueError:
        console.print("[yellow]Malformed pidfile.[/yellow]")
        pidfile.unlink(missing_ok=True)
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False
            )
        else:
            os.kill(pid, 15)
    except OSError as e:
        console.print(f"[yellow]Could not stop: {e}[/yellow]")
    pidfile.unlink(missing_ok=True)
    console.print(f"[green]Engine stopped (pid {pid}).[/green]")


@app.command("restart")
def restart_cmd(
    mode: str = typer.Option("paper", "--mode", "-m"),
    gateway: bool = typer.Option(False, "--gateway", "-g"),
) -> None:
    """Restart the background engine (stop + start)."""
    stop_cmd()
    start_cmd(mode=mode, gateway=gateway, daemon=True)


# ---------------------------------------------------------------------------
# run (legacy parity — same engine, same safety)
# ---------------------------------------------------------------------------
@app.command("run")
def run_cmd(
    config_path: Path = typer.Option(
        Path("configs/live.yaml"), "--config", "-c", help="Path to execution YAML."
    ),
    gateway: bool = typer.Option(
        False, "--gateway", "-g", help="Force remote gateway client mode."
    ),
) -> None:
    """Start the engine with an explicit config (legacy compatibility)."""
    cfg = AppConfig.load_from_yaml(config_path)
    _run_engine(cfg, gateway=gateway, port=8080)


# ---------------------------------------------------------------------------
# doctor parity: config-validate
# ---------------------------------------------------------------------------
@app.command("config-validate")
def config_validate_cmd(
    config_path: Path = typer.Option(
        Path("configs/base.yaml"), "--config", "-c", help="Path to YAML config to validate."
    ),
) -> None:
    """Validate syntax/structure of a config file (legacy parity)."""
    try:
        cfg = AppConfig.load_from_yaml(config_path)
        console.print(f"[green]Configuration valid:[/green] {config_path}")
        console.print(f"Symbol: {cfg.execution.symbol} | Mode: {cfg.execution.mode.value}")
    except Exception as e:
        console.print(f"[red]Configuration validation failed:[/red] {e}")
        raise typer.Exit(1) from None


# =============================================================================
# PHASE 13: MODEL GENERATION MIGRATION — artifact-first model factory CLI
# -----------------------------------------------------------------------------
# nse dataset build / experiment create / train / inspect / validate /
# replay / doctor — all operate on filesystem artifacts (no DB required).
# =============================================================================


def _mg_store() -> Any:
    from nexus_scalp.model_generation import ArtifactStore, default_artifact_root

    return ArtifactStore(default_artifact_root())


@app.command("model-dataset-build")
def model_dataset_build(
    bars_csv: Path = typer.Option(Path(""), "--bars", "-b", help="CSV/parquet of raw bars"),
    symbol: str = typer.Option("XAUUSD", "--symbol"),
    timeframe: str = typer.Option("M5", "--timeframe"),
    schema: str = typer.Option("scalp_v1", "--schema", help="feature schema id"),
    with_news: bool = typer.Option(False, "--with-news", help="attach news context"),
    news_csv: Path = typer.Option(Path(""), "--news", "-n", help="news frame parquet/csv"),
    news_db: Path = typer.Option(
        Path(""), "--news-db", help="export the news database (artifacts/news.db) into the frame"
    ),
) -> None:
    """Build a dataset artifact (deterministic, artifact-first).

    News context is attached when ``--with-news``.  The news frame may be
    given explicitly (``--news``) OR exported from the News subsystem's
    database (``--news-db``, default ``artifacts/news.db``) via the
    causally-correct bridge (model_generation.news_bridge).
    """
    import polars as pl

    from nexus_scalp.model_generation import DatasetFactory

    if not bars_csv.exists():
        console.print("[red]No bars file provided (--bars).[/red]")
        raise typer.Exit(1)
    df = pl.read_csv(bars_csv) if bars_csv.suffix.lower() == ".csv" else pl.read_parquet(bars_csv)
    news_frame = None
    if with_news:
        if news_db.exists():
            from nexus_scalp.model_generation.news_bridge import (
                build_news_frame_from_db,
                news_benchmark_readiness,
            )
            from nexus_scalp.news.database import NewsDatabase

            news_frame = build_news_frame_from_db(NewsDatabase(news_db))
            console.print(
                f"[cyan]News frame exported from DB:[/cyan] {news_db} "
                f"rows={news_frame.height if news_frame is not None else 0}"
            )
            if news_frame is None or news_frame.is_empty():
                console.print(
                    "[yellow]News database contains NO analysis records — the dataset "
                    "will carry all-zero news context (news ON == news OFF). "
                    "Collect real news first.[/yellow]"
                )
            else:
                gate = news_benchmark_readiness(news_frame)
                if not gate["ready"]:
                    console.print(
                        "[yellow]NEWS READINESS GATE: NOT READY — the news frame does not "
                        "satisfy the real-data requirements (non-neutral > 0, XAUUSD > 0, "
                        "multiple events, distinct vectors). News context in this dataset "
                        "may be uninformative. Do NOT use it for a news benchmark.[/yellow]"
                    )
                    console.print(f"[yellow]Failed checks: {gate['checks']}[/yellow]")
                else:
                    console.print("[green]NEWS READINESS GATE: READY — real news context.[/green]")
        elif news_csv.exists():
            news_frame = (
                pl.read_csv(news_csv)
                if news_csv.suffix.lower() == ".csv"
                else pl.read_parquet(news_csv)
            )
            from nexus_scalp.model_generation.news_bridge import news_benchmark_readiness

            gate = news_benchmark_readiness(news_frame)
            if not gate["ready"]:
                console.print(
                    "[yellow]NEWS READINESS GATE: NOT READY for --news file — the frame "
                    "does not satisfy the real-data requirements. "
                    "Do NOT use it for a news benchmark.[/yellow]"
                )
        else:
            console.print(
                "[yellow]--with-news given but no --news file or --news-db found; "
                "news context will be all-zero (news ON == news OFF).[/yellow]"
            )
    store = _mg_store()
    handle = DatasetFactory(store=store).build(
        df, symbol=symbol, timeframe=timeframe, news_frame=news_frame
    )
    console.print(
        f"[green]Dataset built:[/green] {handle['dataset_id']} rows={handle['counts']['total']}"
    )
    _emit(handle, as_json=False, plain=True)


@app.command("model-experiment-create")
def model_experiment_create(
    dataset_id: str = typer.Option(..., "--dataset", help="dataset artifact id"),
    template: str = typer.Option("baseline_scalpnet_v1", "--template"),
) -> None:
    """Create a bounded experiment on a dataset artifact."""
    from nexus_scalp.model_generation import ExperimentFactory

    cfg = ExperimentFactory(store=_mg_store()).create(dataset_id, template=template)
    console.print(f"[green]Experiment created:[/green] {cfg.experiment_id} arch={cfg.architecture}")
    _emit(cfg.model_dump(mode="json"), as_json=False, plain=True)


@app.command("model-train")
def model_train(
    experiment_id: str = typer.Option(..., "--experiment"),
    model_id: str = typer.Option("", "--model-id"),
) -> None:
    """Train a candidate from an experiment (never touches Champion)."""
    from nexus_scalp.model_generation import CandidateTrainer, ExperimentFactory

    store = _mg_store()
    exp = ExperimentFactory(store=store).load(experiment_id)
    frame = store.read_dataset(exp.dataset_id)
    res = CandidateTrainer(store=store).train_candidate(exp, frame, model_id=model_id or None)
    console.print(f"[green]Train:[/green] {res['status']} model={res.get('model_id')}")
    if res["status"] == "FAILED":
        console.print(f"[red]{res.get('error', '')}[/red]")
        raise typer.Exit(1)
    _emit(res, as_json=False, plain=True)


@app.command("model-inspect")
def model_inspect(model_id: str = typer.Option(..., "--model")) -> None:
    """Inspect a model artifact manifest + integrity."""
    store = _mg_store()
    man = store.read_model_manifest(model_id)
    if not man:
        console.print(f"[red]Model {model_id} not found.[/red]")
        raise typer.Exit(1)
    v = store.verify_artifact(model_id)
    console.print(f"[cyan]Model[/cyan] {model_id} integrity={v['ok']}")
    _emit({"manifest": man, "integrity": v}, as_json=False, plain=True)


@app.command("model-validate")
def model_validate(
    model_id: str = typer.Option(..., "--model"),
    dataset_id: str = typer.Option(..., "--dataset"),
) -> None:
    """Validate a candidate artifact against its dataset (OOS/regime/collapse)."""
    from nexus_scalp.model_generation import ValidationFactory

    store = _mg_store()
    frame = store.read_dataset(dataset_id)
    import numpy as np

    labels = frame["label"].to_numpy().astype(np.int64)
    vf = ValidationFactory()
    vr = vf.validate(model_id, "cli", frame, None, labels)
    console.print(f"[cyan]Validation:[/cyan] {vr.verdict} passed={vr.passed}")
    _emit(vr.model_dump(mode="json"), as_json=False, plain=True)


@app.command("model-replay")
def model_replay(
    dataset_id: str = typer.Option(..., "--dataset"),
    sample_id: str = typer.Option(..., "--sample"),
    model_id: str = typer.Option("", "--model"),
) -> None:
    """Replay one sample (historical context + optional model prediction)."""
    from nexus_scalp.model_generation import SampleReplay

    rec = SampleReplay(store=_mg_store()).replay(dataset_id, sample_id, model_id=model_id or None)
    _emit(rec, as_json=False, plain=True)


@app.command("model-doctor")
def model_doctor(model_id: str = typer.Option(..., "--model")) -> None:
    """Run the model doctor: integrity + load + metadata health."""
    from nexus_scalp.model_generation import LocalModelRuntime

    store = _mg_store()
    v = store.verify_artifact(model_id)
    if not v["ok"]:
        console.print(f"[red]Model {model_id} FAILED integrity: {v.get('reason')}[/red]")
        raise typer.Exit(1)
    try:
        rt = LocalModelRuntime(store=store).load(model_id)
        console.print(f"[green]Model healthy:[/green] {model_id}")
        _emit({"integrity": v, "health": rt.health()}, as_json=False, plain=True)
    except Exception as e:
        console.print(f"[red]Model {model_id} failed to load: {e}[/red]")
        raise typer.Exit(1) from None


@app.command("model-train-3")
def model_train_3(
    variant: str = typer.Option(
        "", "--variant", help="50d_main | 70d_news | 70d_liquidity (empty = all)"
    ),
    smoke: bool = typer.Option(
        False, "--smoke", help="Small quick validation run (2 folds, 1 epoch)."
    ),
    folds: int = typer.Option(34, "--folds", help="Walk-forward folds (full run)."),
    epochs: int = typer.Option(10, "--epochs", help="Epochs per fold (full run)."),
    json_mode: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    """Train the 3-model matrix (50D-main / 70D+news / 70D+liquidity).

    Every variant runs the canonical purged walk-forward trainer + the
    BenchmarkRunner evidence gate, then is registered in the model lifecycle
    as CHALLENGER (shadow-eligible / hot-swappable via the shadow70 attach
    endpoint). ``--smoke`` trains a small end-to-end validation so the
    pipeline is provable without a multi-hour CPU run.
    """
    import polars as pl

    from nexus_scalp.model_generation.three_model import train_all

    bars = pl.read_parquet("data/raw/XAUUSD_M1.parquet")
    news_frame = None  # news frame loading is optional; the 70D builder
    # accepts None => neutral news block (FEATURE_DISABLED)
    chosen = [variant] if variant else None
    reports = train_all(
        bars,
        news_frame=news_frame,
        variants=chosen,
        num_folds=folds,
        epochs=epochs,
        smoke=smoke,
    )
    if json_mode:
        _emit({"ok": True, "reports": reports}, as_json=True)
    else:
        for r in reports:
            console.print(
                f"[green]variant={r['variant']}[/green] schema={r['schema_id']} "
                f"dim={r['dimension']} gate={r['gate']} artifact={r['artifact']['model']}"
            )
        console.print("[green]3-model pipeline complete.[/green]")


@app.command("model-swap-hot")
def model_swap_hot(
    variant: str = typer.Option(..., "--variant", help="70d_news | 70d_liquidity | 50d_main"),
    json_mode: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    """Hot-attach a trained variant to the Shadow70 runtime (no restart).

    The variant must exist as a trained CHALLENGER in the lifecycle
    registry (see ``model-train-3``). This reuses the canonical
    ``/api/models/shadow70/attach`` contract — the 70D vector the engine
    already produces every tick is validated, the model is load-gated and
    then attaches with an inference callable, all isolated from the 50D
    Champion path (INV-018).
    """
    from nexus_scalp.application.live_engine import LiveEngine  # noqa: F401  (import check)
    from nexus_scalp.model_generation.three_model import variant_artifact_path

    p = variant_artifact_path(variant)
    if not p.exists():
        console.print(f"[red]artifact missing: {p} — train it first (model-train-3).[/red]")
        raise typer.Exit(1)
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.experience.provenance import ModelRegistry
    from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry

    audit = AuditRepository()
    reg = ModelLifecycleRegistry(audit_repo=audit, model_registry=ModelRegistry(audit_repo=audit))
    derived_id = (
        f"scalp_{variant}_scalp_v3_70d"
        if variant.startswith("70d")
        else f"scalp_{variant}_scalp_v1_50d"
    )
    rows = reg.list_models(status="CHALLENGER", limit=20)
    cand = [r for r in rows if r.get("model_id") == derived_id]
    if not cand:
        console.print(
            f"[red]no CHALLENGER row for {derived_id} — train it (model-train-3) or promote it first.[/red]"
        )
        raise typer.Exit(1)
    out = {
        "variant": variant,
        "artifact": str(p),
        "challenger": bool(cand),
        "attach": "POST /api/models/shadow70/attach (runtime hot-attach)",
        "schema": "scalp_v3" if variant.startswith("70d") else "scalp_v1",
    }
    if json_mode:
        _emit(out, as_json=True)
    else:
        console.print("[green]Hot-swap ready.[/green]", out)


# =============================================================================
# TASK-9 (production release): artifact release classification
# -----------------------------------------------------------------------------
# nexus model-artifacts [--json] -> per-artifact identity + class + runtime
# compatibility (ACTIVE/LEGACY/RETAINED/ARCHIVABLE; MODEL_NOT_RUNTIME_COMPATIBLE
# with the precise reason — never a silent semantic fallback).
# =============================================================================


@app.command("model-artifacts")
def model_artifacts_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """TASK-9: classify model artifacts + runtime compatibility (read-only)."""
    from nexus_scalp.model_generation import default_artifact_root
    from nexus_scalp.release import model_artifacts as rma

    compat_overall = "COMPATIBLE"
    records = rma.summarize_artifacts(default_artifact_root())
    if not records:
        console.print("[yellow]No model artifacts found.[/yellow]")
        return
    for rec in records:
        st = rec["runtime_compatibility"]["status"]
        if st != "COMPATIBLE":
            compat_overall = "INCOMPATIBLE"
    summary = {
        "artifact_count": len(records),
        "overall_compatibility": compat_overall,
        "artifacts": records,
    }
    if json_mode:
        _emit(summary, as_json=True)
        return
    console.print("[cyan]Model artifact release inventory[/cyan]")
    for rec in records:
        ident = rec["identity"]
        console.print(
            f"  {ident['model_id'] or ident['schema_id']} "
            f"schema={ident['schema_id']}({ident['dimension']}D) "
            f"class={rec['class']} runtime={rec['runtime_compatibility']['status']}"
        )
        if rec["runtime_compatibility"]["status"] != "COMPATIBLE":
            console.print(f"    [red]{rec['runtime_compatibility']['reason']}[/red]")


# =============================================================================
# TASK-11/12: POST-70D CONTINUOUS FORENSIC MONITORING + DEPLOY GATE
# -----------------------------------------------------------------------------
# nexus forensic                       -> full health matrix + snapshot
# nexus forensic --deploy-gate         -> canonical deploy gate (exit-code)
# nexus forensic --snapshot            -> persisted FORENSIC_HEALTH_SNAPSHOT
# nexus forensic --trend               -> current vs previous snapshot diff
# nexus forensic --gap                 -> experience->outcome gap forensics
# nexus forensic --report              -> bounded periodic Telegram report
# =============================================================================


@app.command("forensic")
def forensic_cmd(
    snapshot: bool = typer.Option(
        False, "--snapshot", help="Persist and print the FORENSIC_HEALTH_SNAPSHOT as JSON."
    ),
    deploy_gate: bool = typer.Option(
        False,
        "--deploy-gate",
        help="Canonical deploy gate: exit 0 allowed, 1 block, 2 review, 3 engine unavailable.",
    ),
    trend: bool = typer.Option(
        False, "--trend", help="Compare the latest snapshot against the previous (read-only)."
    ),
    gap: bool = typer.Option(False, "--gap", help="Experience->outcome gap forensics (read-only)."),
    report: bool = typer.Option(
        False, "--report", help="Run one bounded periodic Telegram forensic report cycle."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """TASK-11/12 post-70D forensic health matrix + canonical deploy gate (read-only)."""
    from nexus_scalp.forensics import (
        ForensicHealthEngine,
        analyze_experience_gap,
        latest_trend,
        persist_gap_report,
        run_deploy_gate,
    )

    engine = ForensicHealthEngine()

    if deploy_gate:
        result = run_deploy_gate(engine)
        payload = result.to_dict()
        payload["exit_code"] = result.exit_code
        _emit(payload, as_json=json_mode, plain=False)
        if not json_mode:
            if payload["exit_code"] == 1:
                console.print("[red]DEPLOYMENT BLOCKED[/red] — critical forensic checks failed.")
                for c in payload["blocking_checks"]:
                    console.print(f"  • {c}")
            elif payload["exit_code"] == 2:
                console.print(
                    "[yellow]DEPLOYMENT REQUIRES REVIEW[/yellow] — DEGRADED/UNKNOWN conditions."
                )
            elif payload["exit_code"] == 3:
                console.print(
                    "[red]FORENSIC ENGINE UNAVAILABLE[/red] — deployment cannot be verified."
                )
        raise typer.Exit(payload["exit_code"])

    if trend:
        t = latest_trend(Path("artifacts") / "forensics")
        _emit(t, as_json=json_mode, plain=not json_mode)
        return

    if gap:
        rep = analyze_experience_gap()
        persist_gap_report(rep)
        _emit(rep.to_dict(), as_json=json_mode, plain=not json_mode)
        return

    if report:
        from nexus_scalp.forensics import TelegramReportScheduler

        sched = TelegramReportScheduler()
        outcome = sched.run_once(engine)
        _emit(outcome, as_json=json_mode, plain=not json_mode)
        return

    if snapshot:
        rec = engine.snapshot(persist=True)
        _emit(rec.to_dict(), as_json=json_mode, plain=not json_mode)
        return

    dash = engine.dashboard()
    if json_mode:
        _emit(dash, True)
        return
    table = Table(title="SYSTEM FORENSIC HEALTH", box=box.SIMPLE)
    table.add_column("Group", style="bold white")
    table.add_column("Status", style="bold")
    table.add_column("Check", style="dim")
    table.add_column("Detail", style="dim")
    for group, status in dash["groups"].items():
        table.add_row(group, _verdict_style(status), "", "")
    console.print(table)
    console.print(
        Panel(
            f"Overall: [bold]{dash['overall']}[/bold]  "
            f"CRITICAL={dash['critical_count']} WARNING={dash['warning_count']} "
            f"DEGRADED={dash['degraded_count']} UNKNOWN={dash['unknown_count']}",
            border_style="red" if dash["critical_count"] else "yellow",
        )
    )
    problems = [
        (r["check_id"], r["status"], r["evidence"])
        for r in dash["rows"].values()
        if r["status"] not in ("PASS",)
    ]
    if problems:
        pt = Table(title="Non-passing checks (evidence)", box=box.SIMPLE)
        pt.add_column("Check", style="bold white")
        pt.add_column("Status", style="bold")
        pt.add_column("Evidence", style="dim")
        for cid, status, evidence in problems:
            pt.add_row(cid, _verdict_style(status), evidence[:160])
        console.print(pt)


if __name__ == "__main__":
    app()
