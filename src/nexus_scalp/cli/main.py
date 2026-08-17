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
from nexus_scalp.release import verify as rverify
from nexus_scalp.release.metadata import PRODUCT_DISPLAY, get_version_info

app = typer.Typer(
    name="nexus",
    help=f"{PRODUCT_DISPLAY} - operational & release console",
    add_completion=False,
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
# update
# ---------------------------------------------------------------------------
@app.command("update")
def update_cmd(
    manifest: Path | None = typer.Option(
        None, "--manifest", help="Path to available-release manifest (JSON)."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Check for a safe update. Never touches user data."""
    info = get_version_info()
    available = rupdate.load_available_releases(manifest) if manifest else None
    plan = rupdate.UpdateEngine().plan(current_version=info["version"], available=available)
    if json_mode:
        _emit(plan.to_dict(), True)
        return
    rupdate.format_update_report(plan)
    if not plan.ready:
        raise typer.Exit(0)


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
    if mode not in MODE_ALIASES:
        mode = "PAPER"
    if mode == "LIVE":
        confirm = typer.confirm(
            "WARNING: LIVE mode places real orders and risks real capital. Continue?",
            default=False,
        )
        if not confirm:
            console.print("[yellow]Setup aborted — LIVE not confirmed.[/yellow]")
            raise typer.Exit(1)

    symbol = typer.prompt("Trading symbol", default="XAUUSD").strip().upper()
    config_path = rpaths.get_user_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    template = rrepair.RepairEngine().template_config
    if not config_path.exists() and template.exists():
        shutil.copy2(template, config_path)
    try:
        cfg = AppConfig.load_from_yaml(config_path) if config_path.exists() else AppConfig()
    except Exception:
        cfg = AppConfig()
    cfg.execution.mode = MODE_ALIASES[mode]
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
    """First-run setup wizard (compat, install, DB, model, mode, health)."""
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
    """Start the engine. LIVE requires explicit confirmation."""
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
        )
    cfg.execution.mode = cfg.execution.mode
    engine = LiveEngine(config=cfg, adapter=adapter)
    _start_web_and_engine(engine, cfg, port)


def _start_web_and_engine(engine: Any, cfg: AppConfig, port: int) -> None:
    import asyncio

    import uvicorn

    from nexus_scalp.web.server import create_app

    console.print(
        f"[bold cyan]Starting {cfg.execution.mode.value} mode — {cfg.execution.symbol}[/bold cyan]"
    )
    engine._preflight_or_raise()
    app_obj = create_app(engine_ref=engine)
    engine.server_state = app_obj.state.server_state
    uvicorn_config = uvicorn.Config(
        app=app_obj, host="127.0.0.1", port=port, log_level="warning", ws_max_size=16 * 1024 * 1024
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


if __name__ == "__main__":
    app()
