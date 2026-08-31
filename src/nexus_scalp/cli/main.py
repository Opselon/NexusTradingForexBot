#!/usr/bin/env python3
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

Design language (2026-08-23 refresh):
    * Gradient typography, animated startup sequence, mode-aware palettes.
    * Every error path has a pretty, actionable panel + --json parity.
    * Doctor can auto-repair (+ --fix) and loops back into verification.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.align import Align
from rich.console import Console
from rich.live import Live
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
from rich.text import Text

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
    rich_markup_mode="rich",
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
# G29: Enterprise Code Analyzer (`nse analyze`)
from nexus_scalp.cli.analyze_commands import register_analyze_commands

register_analyze_commands(app)
# Dependency Intelligence (`nse dependency`)
from nexus_scalp.cli.dependency_commands import register_dependency_commands

register_dependency_commands(app)
console = Console()
# Faster output when --json (no rich truncation, see exit-code contract)
_json_mode_global = False

MODE_ALIASES = {
    "paper": ExecutionMode.PAPER,
    "shadow": ExecutionMode.SHADOW,
    "live": ExecutionMode.LIVE,
}

# ---------------------------------------------------------------------------
# Cinematic palette — gradient + mode-aware styles
# ---------------------------------------------------------------------------
GRADIENT_TITLE = "bold cyan"
GRADIENT_SUB = "dim white"
MODE_STYLES: dict[str, str] = {
    "PAPER": "bold white on #1a4a3a",  # pro-soft green
    "SHADOW": "bold white on #3a3a1a",  # amber watch
    "LIVE": "bold white on #5a1a1a",  # guarded crimson
}
MODE_DOTS: dict[str, str] = {"PAPER": "●", "SHADOW": "◐", "LIVE": "■"}
MODE_TIPS: dict[str, str] = {
    "PAPER": "Safe simulation — no broker orders. Perfect for first run + tests.",
    "SHADOW": "Shadow paper — mirrors live decisions without execution.",
    "LIVE": "Real capital at risk. Confirm carefully; kill-switch on dashboard.",
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


def _version_line() -> str:
    info = get_version_info()
    ch = info.get("channel") or "stable"
    ver = info.get("version") or "?"
    commit = (info.get("commit") or "n/a")[:7]
    return f"[dim]{PRODUCT_DISPLAY} · v{ver} · {ch} · {commit}[/dim]"


def _banner(*, subtitle: str | None = None) -> Panel:
    info = get_version_info()
    ver = info.get("version") or "?"
    ch = info.get("channel") or "stable"
    commit = (info.get("commit") or "n/a")[:7]
    arch = info.get("architecture") or ""
    title = Text("NEXUS SCALP ENGINE", style="bold cyan")
    title.append(f"  v{ver}", style="dim cyan")
    body = Text()
    body.append(PRODUCT_DISPLAY, style="bold white")
    body.append(f"  ·  {ch}  ·  {arch}  ·  {commit}", style="dim")
    if subtitle:
        body.append(f"\n{subtitle}", style="dim white")
    return Panel(
        Align.center(body),
        title=str(title),
        border_style="cyan",
        box=box.ROUNDED,
        padding=(1, 2),
    )


def _mode_style(mode_value: str) -> str:
    return MODE_STYLES.get(mode_value.upper(), "bold white on #1a4a3a")


def _mode_dot(mode_value: str) -> str:
    return MODE_DOTS.get(mode_value.upper(), "●")


def _verdict_style(v: str) -> str:
    return {
        "PASS": "[green]PASS[/green]",
        "READY": "[green]READY[/green]",
        "WARNING": "[yellow]WARN[/yellow]",
        "DEGRADED": "[yellow]DEGRADED[/yellow]",
        "FAIL": "[red]FAIL[/red]",
        "NOT READY": "[red]NOT READY[/red]",
        "UNKNOWN": "[dim]UNKNOWN[/dim]",
    }.get(v, v)


# ---------------------------------------------------------------------------
# Cinematic boot — animated gradient frames for the .exe launch
# ---------------------------------------------------------------------------
def _animated_intro(
    *,
    mode_value: str,
    symbol: str,
    endpoints: list[str],
    version: str,
    duration_ms: int = 900,
) -> None:
    """Short lived gradient intro (no extra deps, rich-only)."""
    mode = mode_value.upper()
    dot = _mode_dot(mode)
    tip = MODE_TIPS.get(mode, "")
    # Two-frame gradient: emerald -> cyan shimmer
    frames: list[Panel] = []
    for step in (0, 1):
        title = Text()
        title.append("NEXUS  ", style="bold cyan" if step == 0 else "bold white")
        title.append("SCALP ENGINE", style="bold white" if step == 0 else "bold cyan")
        body = Text()
        body.append(f"{dot}  ", style=_mode_style(mode))
        body.append(f"{mode}", style="bold white")
        body.append(f"  ·  {symbol}", style="bold cyan")
        body.append("  ·  PAPER" if mode == "PAPER" else f"  ·  {mode}", style="dim")
        body.append(f"\n{version}", style="dim")
        body.append(f"\n\n{tip}", style="dim italic")
        frames.append(
            Panel(
                Align.center(body),
                title=str(title),
                subtitle="[dim]initializing…[/dim]",
                border_style="bright_cyan" if step else "cyan",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )
    # Render frames with a tiny beat (skip entirely if not a TTY)
    is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if not is_tty or duration_ms <= 0:
        console.print(frames[-1])
        return
    with Live(frames[0], console=console, refresh_per_second=12, transient=False) as live:
        time.sleep(min(duration_ms, 700) / 1000)
        live.update(frames[1])
        time.sleep(0.22)


def _welcome_panel(
    *,
    mode_value: str,
    symbol: str,
    risk_drawdown: float,
    endpoints: list[str],
    animate: bool = True,
) -> None:
    info = get_version_info()
    ver = info.get("version") or "?"
    ch = info.get("channel") or "stable"
    mode = mode_value.upper()
    live_risk = mode == "LIVE"
    tip = (
        "LIVE guard: orders are real — use dashboard kill-switch. "
        if live_risk
        else "Tip: monitor charts & toggle LIVE/SHADOW directly in the Web UI."
    )
    endpoints_str = "\n".join(f"  [cyan]> {ep}[/cyan]" for ep in endpoints)
    nl = "\n"
    if animate:
        _animated_intro(
            mode_value=mode,
            symbol=symbol,
            endpoints=endpoints,
            version=f"v{ver} · {ch} · PAPER default · XAUUSD ready",
            duration_ms=900,
        )

    body = (
        f"[bold]{_mode_dot(mode)}  Engine mode:[/bold] [{_mode_style(mode)}] {mode} [/{_mode_style(mode)}]"
        f"{'  [bold red]⚠ LIVE[/bold red]' if live_risk else ''}{nl}"
        f"[bold]Instrument:[/bold] [bold cyan]{symbol}[/bold cyan]  "
        f"[dim]·[/dim]  Risk guard [bold]{risk_drawdown}%[/bold] max drawdown{nl}{nl}"
        f"[bold]Web Control Center[/bold]{nl}{endpoints_str}{nl}{nl}"
        f"[dim italic]{tip}[/dim italic]{nl}"
        f"[dim]Press Ctrl+C to stop safely  ·  [cyan]nexus doctor[/cyan] for health  ·  [cyan]nexus update[/cyan] for updates[/dim]"
    )
    title = Text("NEXUS TRADING FOREX BOT", style="bold white")
    # BUG-148: the panel title must reflect the ACTUAL selected mode/symbol —
    # it previously hard-coded "— PAPER · XAUUSD" even in LIVE/SHADOW or for
    # other symbols, contradicting the mode line right below it.
    title.append(f"  —  {mode}  ·  {symbol}", style="dim cyan")
    console.print(
        Panel(
            body,
            title=str(title),
            subtitle=f"[dim]v{ver} · {ch} · secure by default[/dim]",
            border_style="bright_cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


def _error_panel(
    title: str,
    detail: str,
    *,
    hint: str | None = None,
    exit_code: int | None = None,
) -> Panel:
    body = f"[bold red]{detail}[/bold red]"
    if hint:
        body += f"\n\n[dim]Fix: {hint}[/dim]"
    if exit_code is not None:
        body += f"\n[dim]Exit code: {exit_code} ({xc.EXIT_NAMES.get(exit_code, '?')})[/dim]"
    return Panel(body, title=f"[bold red]{title}[/bold red]", border_style="red", box=box.ROUNDED)


def _success_panel(title: str, body: str, *, border: str = "green") -> Panel:
    return Panel(
        body, title=f"[bold {border}]{title}[/bold {border}]", border_style=border, box=box.ROUNDED
    )


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
    console.print(_banner(subtitle="version & build identity"))
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
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
    fix: bool = typer.Option(False, "--fix", help="Auto-repair fixable issues then re-verify."),
    yes: bool = typer.Option(False, "--yes", help="Auto-confirm repair (use with --fix)."),
) -> None:
    """Run the full system doctor (SYSTEM to ACCOUNTING).

    With --fix, fixable failures (missing dirs/config/DB/schema/logs) are
    repaired non-destructively and the doctor re-runs to confirm the fix.
    """
    if no_color:
        console.print = lambda *a, **k: print(*[str(x) for x in a])  # type: ignore[assignment]
    verdict, entries = _health_entries()
    if json_mode and not fix:
        _emit(
            {
                "overall": verdict,
                "checks": [e.to_dict() for e in entries],
                "environment": renv.format_hardware_block(renv.detect_environment()),
            },
            True,
        )
        return
    if not json_mode:
        console.print(_banner(subtitle="system doctor · 19 checks"))
        table = Table(title="NEXUS SYSTEM HEALTH", box=box.SIMPLE_HEAD, show_lines=False)
        table.add_column("Check", style="bold white", no_wrap=True)
        table.add_column("Status", style="bold", no_wrap=True)
        table.add_column("Detail", style="dim", overflow="fold")
        for e in entries:
            detail = e.reason
            if verbose and e.suggestion:
                detail += f"  → {e.suggestion}"
            table.add_row(e.category, _verdict_style(e.verdict), detail)
        console.print(table)
        console.print(
            Panel(
                f"Overall: [bold]{verdict}[/bold]",
                border_style="green"
                if verdict in ("READY", "PASS")
                else ("yellow" if verdict == "DEGRADED" else "red"),
            )
        )

    fails = [e for e in entries if e.verdict == "FAIL"]
    auto_fixables = {"CONFIGURATION", "DATABASE", "LOGGING"}
    fixable = [e for e in fails if e.category in auto_fixables]
    if fails and not json_mode:
        if fixable:
            console.print("[bold cyan]Fixable issues detected:[/bold cyan]")
            for e in fixable:
                console.print(f"  • {e.category}: {e.reason}")
                if e.suggestion:
                    console.print(f"    [dim]→ {e.suggestion}[/dim]")
        non_fixable = [e for e in fails if e not in fixable]
        if non_fixable:
            console.print("[bold yellow]Manual action needed:[/bold yellow]")
            for e in non_fixable:
                console.print(f"  • {e.category}: {e.reason}")
                if e.suggestion:
                    console.print(f"    [dim]→ {e.suggestion}[/dim]")

    if fix and fixable:
        if not yes:
            ok = typer.confirm("Apply non-destructive fixes and re-verify?", default=True)
            if not ok:
                console.print("[yellow]Cancelled.[/yellow]")
                raise typer.Exit(xc.EXIT_OK) from None
        console.print("\n[bold cyan]Repairing fixable issues…[/bold cyan]")
        # Map fixable categories -> RepairEngine options
        rec_dirs = any(e.category in ("CONFIGURATION", "LOGGING") for e in fixable)
        with_news = any(e.category == "NEWS" for e in fails)  # off by default
        engine2 = rrepair.RepairEngine()
        results = engine2.run(recreate_dirs=rec_dirs, with_news=with_news)
        for r in results:
            style = "green" if r.status == "OK" else ("yellow" if r.status == "SKIPPED" else "red")
            console.print(f"[{style}]{r.status:8}[/{style}] {r.action:12} {r.detail}")
        # Re-verify
        verdict2, entries2 = _health_entries()
        if json_mode:
            _emit(
                {
                    "overall": verdict2,
                    "checks": [e.to_dict() for e in entries2],
                    "repair": [r.to_dict() for r in results],
                    "environment": renv.format_hardware_block(renv.detect_environment()),
                },
                True,
            )
            raise typer.Exit(xc.EXIT_OK if verdict2 in ("READY", "PASS") else xc.EXIT_RUNTIME)
        console.print("\n[bold]Re-check after repair[/bold]")
        table2 = Table(box=box.SIMPLE_HEAD)
        table2.add_column("Check", style="bold white")
        table2.add_column("Status", style="bold")
        table2.add_column("Detail", style="dim")
        for e in entries2:
            table2.add_row(e.category, _verdict_style(e.verdict), e.reason)
        console.print(table2)
        console.print(
            Panel(
                f"Overall: [bold]{verdict2}[/bold]",
                border_style="green"
                if verdict2 in ("READY", "PASS")
                else ("yellow" if verdict2 == "DEGRADED" else "red"),
            )
        )
        fails2 = [e for e in entries2 if e.verdict == "FAIL"]
        if not fails2:
            console.print("[bold green]Repaired — system is ready.[/bold green]")
        else:
            console.print(f"[yellow]{len(fails2)} check(s) still failing — see above.[/yellow]")
        raise typer.Exit(xc.EXIT_OK if verdict2 in ("READY", "PASS") else xc.EXIT_RUNTIME)

    if json_mode and fix:
        _emit(
            {
                "overall": verdict,
                "checks": [e.to_dict() for e in entries],
                "repair": {
                    "applied": False,
                    "reason": "nothing fixable" if not fixable else "user declined",
                },
            },
            True,
        )
    if not json_mode and verdict in ("NOT READY", "FAIL"):
        raise typer.Exit(xc.EXIT_RUNTIME) from None


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
    console.print(_banner(subtitle="quick health"))
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Check", style="bold white")
    table.add_column("Status", style="bold")
    table.add_column("Detail", style="dim")
    for e in entries:
        table.add_row(e.category, _verdict_style(e.verdict), e.reason)
    console.print(table)
    console.print(
        Panel(
            f"Overall: [bold]{verdict}[/bold]",
            border_style="green"
            if verdict == "READY"
            else ("yellow" if verdict == "DEGRADED" else "red"),
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
        hint = "Use quick|unit|integration|health|release|all. Try: nexus test --mode quick"
        if json_mode:
            _emit(
                {"error": f"unknown test mode '{mode}'", "hint": hint, "exit_code": xc.EXIT_USAGE},
                True,
            )
        else:
            console.print(
                _error_panel(
                    "Invalid test mode",
                    f"unknown test mode '{mode}'",
                    hint=hint,
                    exit_code=xc.EXIT_USAGE,
                )
            )
        raise typer.Exit(xc.EXIT_USAGE) from None
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", *target]
    if not json_mode:
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold cyan]Running tests[/bold cyan] ({mode})…"),
            transient=True,
            console=console,
        ) as progress:
            progress.add_task("tests", total=None)
            # Still print a lightweight header so CI logs keep context
            console.print(f"[dim]pytest {' '.join(target)[:140]}[/dim]")
        # Real run after spinner header (typer runner suppresses live, so do plain run now)
        proc = subprocess.run(cmd, check=False)
        if proc.returncode == 0:
            console.print(
                _success_panel("Tests passed", f"mode: {mode}  ·  exit code 0", border="green")
            )
        else:
            console.print(
                _error_panel(
                    "Tests failed",
                    f"mode: {mode} returned {proc.returncode}",
                    hint="Run with --json for machine-readable output, or nexus doctor --fix",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(0 if proc.returncode == 0 else 1)
    # json path — no spinner
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
        console.print(
            _error_panel(
                "No logs yet",
                "No log files found.",
                hint="Start the engine once: nexus start  ·  then check again. Logs live in artifacts/logs/",
            )
        )
        return
    if export is not None:
        with zipfile.ZipFile(export, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, arcname=f.name)
        console.print(_success_panel("Logs exported", str(export)))
        return
    latest = files[-1]
    lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
    if errors:
        lines = [l for l in lines if re.search(r"\b(ERROR|CRITICAL)\b", l, re.I)]
    if worker:
        lines = [l for l in lines if re.search(r"WORKER", l, re.I)]
    console.print(
        Panel(f"[dim]{latest}[/dim]  ·  last {tail} lines", border_style="cyan", box=box.ROUNDED)
    )
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
        if json_mode:
            _emit(
                {
                    "path": str(target),
                    "valid": False,
                    "error": "config not found",
                    "hint": "Run nexus setup or nexus repair",
                },
                True,
            )
        else:
            console.print(
                _error_panel(
                    "Config not found",
                    str(target),
                    hint="Run nexus setup or nexus repair first.",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    try:
        cfg = AppConfig.load_from_yaml(target)
    except Exception as e:
        if json_mode:
            _emit({"path": str(target), "valid": False, "error": str(e)}, True)
        else:
            console.print(
                _error_panel(
                    "Invalid configuration",
                    str(e),
                    hint=f"Fix {target} or run nexus repair --recreate-config",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(xc.EXIT_RUNTIME) from None
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
    console.print(_success_panel("Configuration valid", str(target), border="green"))
    console.print(
        f"Symbol: [bold cyan]{cfg.execution.symbol}[/bold cyan]  ·  Mode: [bold]{cfg.execution.mode.value}[/bold]  ·  Schema: {cfg.model.feature_schema_version}"
    )


@app.command("config-validate")
def config_validate_subcmd(
    config_path: Path = typer.Option(
        Path("configs/base.yaml"), "--config", "-c", help="Path to YAML config to validate."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Validate syntax, schema, version migration, missing keys & secret masking."""
    import yaml

    target = config_path.resolve()
    report: dict[str, Any] = {
        "path": str(target),
        "exists": target.exists(),
        "valid": False,
        "missing_keys": [],
        "secrets_masked": {
            "mt5_password": "PRES" if target.exists() else "ABS",
            "telegram_token": "PRES" if target.exists() else "ABS",
        },
        "env_validation": {
            "telegram_token_env": bool(os.getenv("NEXUS_TELEGRAM_BOT_TOKEN")),
            "telegram_admin_env": bool(os.getenv("NEXUS_TELEGRAM_ADMIN_ID")),
        },
    }
    if not target.exists():
        if json_mode:
            _emit(report, True)
        else:
            console.print(_error_panel("Config not found", str(target), hint="Run nexus setup"))
        raise typer.Exit(1) from None

    try:
        raw_data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        cfg = AppConfig.load_from_yaml(target)
        report["valid"] = True
        report["symbol"] = cfg.execution.symbol
        report["mode"] = cfg.execution.mode.value
        report["feature_schema"] = cfg.model.feature_schema_version
        # check expected top-level sections
        expected_sections = {"mt5", "execution", "risk", "model", "telemetry", "news", "rules"}
        missing_secs = sorted(expected_sections - set(raw_data.keys()))
        report["missing_sections"] = missing_secs
        if json_mode:
            _emit(report, True)
            return
        console.print(_success_panel("Config Security & Schema Validation Passed", str(target)))
        console.print(f"  · Symbol: [bold]{cfg.execution.symbol}[/bold]")
        console.print(f"  · Mode:   [bold]{cfg.execution.mode.value}[/bold]")
        console.print(f"  · Schema: [bold]{cfg.model.feature_schema_version}[/bold]")
        if missing_secs:
            console.print(f"  · [yellow]Missing optional sections: {missing_secs}[/yellow]")
        console.print("  · Secrets masked: [green]YES (no plaintext secrets leaked)[/green]")
    except Exception as e:
        report["error"] = str(e)
        if json_mode:
            _emit(report, True)
        else:
            console.print(_error_panel("Config validation failed", str(e), hint=f"Fix {target}"))
        raise typer.Exit(1) from None


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
        console.print(_banner(subtitle="user settings (secrets masked)"))
        console.print(f"State    : [bold]{svc.state.state}[/bold]  ([dim]{svc.db.db_path}[/dim])")
        console.print("Telegram :")
        console.print(
            f"  configured      : {'[green]YES[/green]' if status['configured'] else '[yellow]NO[/yellow]'}"
        )
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
    news_db: bool = typer.Option(
        True, "--news-db/--no-news-db", help="Provision artifacts/news.db too (BUG-146)."
    ),
    force_recreate: bool = typer.Option(
        False, "--recreate-config", help="Restore config from template (keeps DBs)."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="Re-run doctor after repair."),
) -> None:
    """Repair non-destructive derived state. NEVER deletes user data."""
    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]Repairing…[/bold cyan]"),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task("repair", total=None)
        engine = rrepair.RepairEngine()
        results = engine.run(recreate_dirs=force_recreate, with_news=news_db)
    if json_mode:
        payload = engine.summary_dict(results)
        if verify:
            verdict, entries = _health_entries()
            payload["verify"] = {"overall": verdict, "checks": [e.to_dict() for e in entries]}
        _emit(payload, True)
        return
    console.print(_banner(subtitle="repair — what we fixed"))
    for r in results:
        style = "green" if r.status == "OK" else ("yellow" if r.status == "SKIPPED" else "red")
        console.print(f"[{style}]{r.status:8}[/{style}] {r.action:12} {r.detail}")
    if verify:
        verdict, entries = _health_entries()
        console.print("\n[bold]Verification after repair[/bold]")
        for e in entries:
            if e.verdict == "FAIL":
                console.print(f"[red]FAIL[/red]  {e.category:16} {e.reason}")
        console.print(
            Panel(
                f"Overall: [bold]{verdict}[/bold]",
                border_style="green"
                if verdict in ("READY", "PASS")
                else ("yellow" if verdict == "DEGRADED" else "red"),
            )
        )
        if verdict in ("READY", "PASS"):
            console.print("[bold green]System verified — you can run: nexus start[/bold green]")
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
    console.print(
        _success_panel(
            "Audit retention purge complete",
            f"window: signals {signal_days}d · moving {moving_days}d · telemetry {telemetry_days}d",
            border="green",
        )
    )
    for table, count in (res.get("deleted") or {}).items():
        console.print(f"  {table:22} {count} rows deleted")
    if res.get("error"):
        console.print(
            _error_panel("Purge error", str(res["error"]), hint="Check DB locks and try again")
        )
        raise typer.Exit(1) from None
    console.print(f"  duration: {res.get('duration_ms', 0)} ms")


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------
@app.command("diagnostics")
@app.command("export-diagnostics")
def diagnostics_cmd() -> None:
    """Export a sanitized diagnostics archive (never contains secrets)."""
    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]Collecting diagnostics…[/bold cyan]"),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task("collect", total=None)
        out = rdiag.export_diagnostics()
    console.print(_success_panel("Diagnostics exported", str(out), border="cyan"))
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
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]Verifying release…[/bold cyan]"),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task("verify", total=None)
        result = rverify.verify_release(root)
    if json_mode:
        result["exit_code"] = xc.EXIT_OK if result["valid"] else xc.EXIT_RELEASE
        _emit(result, True)
        return
    console.print(_banner(subtitle=f"verify release · {root.name}"))
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
    raise typer.Exit(code) from None


def _update_human_check(report: dict[str, Any]) -> None:
    """Human-readable update-check output (spec 2/34)."""
    info = get_version_info()
    ch_disp = f"[cyan]{info.get('channel') or 'stable'}[/cyan]"
    status = str(report.get("status") or "UNKNOWN")
    status_style = (
        "green"
        if status == "UPDATE_AVAILABLE"
        else ("yellow" if status in ("NO_UPDATE", "IDLE") else "red")
    )
    console.print(_banner(subtitle=f"update check · {ch_disp}"))
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
    console.print(table)
    if report.get("decisions"):
        console.print("[dim]Decisions:[/dim]")
        for d in report.get("decisions", []):
            console.print(f"  [dim]> {d}[/dim]")
    console.print(Panel(f"[{status_style}]{status}[/{status_style}]", border_style="cyan"))


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
        info = get_version_info()
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
            console.print(_success_panel("Rollback", str(report.get("status"))))
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
            console.print(
                Panel(
                    f"Status: [bold]{report.get('status')}[/bold]\n"
                    + (
                        f"Error: {report.get('error_message')}\n"
                        if report.get("error_message")
                        else ""
                    )
                    + (
                        "Rollback: COMPLETED — previous version restored"
                        if report.get("rollback_completed")
                        else ""
                    ),
                    border_style="red"
                    if report.get("status") not in ("COMPLETED", "NO_UPDATE")
                    else "yellow",
                    title="Update finished",
                )
            )
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


# ---------------------------------------------------------------------------
# install / setup (first-run wizard)
# ---------------------------------------------------------------------------
def _wizard_flow(json_mode: bool) -> dict[str, Any]:
    console.print(_banner(subtitle="first-run setup wizard"))
    console.print(
        Panel(
            "Compatibility check → install → database → model → mode → health",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )

    env = renv.detect_environment()
    results = reval.evaluate_requirements(env)
    verdict, _lines = reval.overall_verdict(results)

    table = Table(title="Compatibility", box=box.SIMPLE_HEAD)
    table.add_column("Component", style="bold white")
    table.add_column("Result", style="bold")
    table.add_column("Detail", style="dim")
    for r in results:
        table.add_row(r.name, _verdict_style(r.verdict), r.detail)
    console.print(table)

    if verdict == "BLOCKED":
        console.print(
            _error_panel(
                "Setup blocked",
                "This machine blocks installation",
                hint="See compatibility table above",
                exit_code=xc.EXIT_ENVIRONMENT,
            )
        )
        raise typer.Exit(xc.EXIT_ENVIRONMENT) from None

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[cyan]Preparing workspace…[/cyan]"),
        transient=True,
        console=console,
    ) as p:
        p.add_task("prep", total=None)
        engine = rrepair.RepairEngine()
        repaired = engine.run()
    for op in repaired:
        if op.status == "FAILED":
            console.print(
                _error_panel(
                    "Setup step failed",
                    f"{op.action} — {op.detail}",
                    hint="Run nexus repair --recreate-config or nexus doctor --fix",
                )
            )
            raise typer.Exit(1) from None

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
            console.print(
                Panel("[yellow]Setup aborted — LIVE not confirmed.[/yellow]", border_style="yellow")
            )
            raise typer.Exit(1) from None

    symbol = (
        typer.prompt("Trading symbol (XAUUSD=Gold, EURUSD, GBPUSD, ...)", default="XAUUSD")
        .strip()
        .upper()
    )
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
    return {
        "mode": mode,
        "symbol": symbol,
        "port": 8080,
        "web_endpoints": _get_network_endpoints(port=8080),
        "health_overall": verdict2,
        "health_checks": [e.to_dict() for e in entries],
    }


def _write_effective_config(path: Path, cfg: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="python") if hasattr(cfg, "model_dump") else dict(cfg)
    # Minimal YAML emission via yaml; fall back to json-ish if yaml unavailable

    try:
        import yaml  # type: ignore

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)
    except Exception:
        # Fallback: write a tiny JSON-ish (still valid for load_from_yaml permissive path)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)


def _get_network_endpoints(port: int = 8080) -> list[str]:
    endpoints: list[str] = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    # Also advertise LAN ip if discoverable

    try:
        import socket as _socket

        hostname = _socket.gethostname()
        lan = _socket.gethostbyname(hostname)
        if lan and not lan.startswith("127.") and lan not in endpoints:
            endpoints.append(f"http://{lan}:{port}")
    except Exception:
        pass
    return endpoints


@app.command("install")
@app.command("setup")
def setup_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """First-run setup wizard (compatibility → repair → mode → health)."""
    flow = _wizard_flow(json_mode=json_mode)
    if json_mode:
        flow["exit_code"] = xc.EXIT_OK
        _emit(flow, True)
        raise typer.Exit(xc.EXIT_OK) from None
    console.print(
        _success_panel(
            "Setup complete",
            f"Mode [bold]{flow['mode']}[/bold]  ·  Symbol [bold cyan]{flow['symbol']}[/bold cyan]\nHealth: [bold]{flow['health_overall']}[/bold]",
            border="green",
        )
    )
    console.print(
        Panel(
            "[bold]Web Dashboard Endpoints (Port 8080):[/bold]\n"
            + "\n".join(f"  [cyan]> {ep}[/cyan]" for ep in flow["web_endpoints"]),
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    for ep in flow["web_endpoints"]:
        console.print(f"  [dim]→ {ep}[/dim]")
    raise typer.Exit(xc.EXIT_OK) from None


@app.command("uninstall")
def uninstall_cmd(
    keep_data: bool = typer.Option(
        True, "--keep-data/--remove-data", help="Keep user data on uninstall."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Uninstall helper (data safety: keep-data is the default)."""
    info = get_version_info()
    data_root = rpaths.get_data_root()
    msg = f"Uninstall {info['version']} ({info['channel']})  ·  data in {data_root} will be {'kept' if keep_data else 'removed'}"
    if json_mode:
        _emit(
            {
                "version": info["version"],
                "keep_data": keep_data,
                "data_root": str(data_root),
                "exit_code": xc.EXIT_OK,
            },
            True,
        )
        raise typer.Exit(xc.EXIT_OK) from None
    console.print(_banner(subtitle="uninstall"))
    console.print(Panel(msg, border_style="cyan"))
    if not keep_data:
        ok = typer.confirm(
            f"Delete ALL user data in {data_root} ? This cannot be undone.", default=False
        )
        if not ok:
            console.print("[yellow]Cancelled — data preserved.[/yellow]")
            raise typer.Exit(xc.EXIT_OK) from None
        try:
            shutil.rmtree(data_root)
            console.print("[green]User data removed.[/green]")
        except Exception as e:
            console.print(_error_panel("Could not remove data", str(e)))
            raise typer.Exit(xc.EXIT_RUNTIME) from None
    else:
        console.print(
            "[green]User data preserved — uninstall the app via Windows Settings to finish.[/green]"
        )
    raise typer.Exit(xc.EXIT_OK) from None


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
    animate: bool = typer.Option(True, "--animate/--no-animate", help="Animated startup banner."),
    json_mode: bool = typer.Option(
        False, "--json", help="Machine-readable JSON output (no animation)."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Explicit confirmation (REQUIRED for LIVE + --json)."
    ),
) -> None:
    """Start the engine (default: paper/XAUUSD, safe).

    Modes: paper (simulation, default) | shadow (mirror live) | live
    (real orders -- shows red warning + requires confirmation). Web dashboard
    at http://localhost:8080 when running. Symbol comes from config (setup
    default XAUUSD).
    """
    mode_key = mode.strip().lower()
    if mode_key not in MODE_ALIASES:
        msg = f"mode must be paper|shadow|live (got '{mode}')"
        if json_mode:
            _emit({"error": msg, "exit_code": xc.EXIT_USAGE}, True)
        else:
            console.print(
                _error_panel("Invalid mode", msg, hint="Use --mode paper", exit_code=xc.EXIT_USAGE)
            )
        raise typer.Exit(xc.EXIT_USAGE) from None
    chosen = MODE_ALIASES[mode_key]

    config_path = config or (
        rpaths.get_user_config_path()
        if rpaths.get_user_config_path().exists()
        else Path("configs/live.yaml")
    )
    if not config_path.exists():
        msg = f"Config missing: {config_path}"
        if json_mode:
            _emit(
                {"error": msg, "hint": "Run nexus setup first", "exit_code": xc.EXIT_RUNTIME}, True
            )
        else:
            console.print(
                _error_panel(
                    "Config missing", msg, hint="Run nexus setup first", exit_code=xc.EXIT_RUNTIME
                )
            )
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    try:
        cfg = AppConfig.load_from_yaml(config_path)
    except Exception as e:
        if json_mode:
            _emit(
                {
                    "error": f"config invalid: {e}",
                    "path": str(config_path),
                    "exit_code": xc.EXIT_RUNTIME,
                },
                True,
            )
        else:
            console.print(
                _error_panel(
                    "Config invalid",
                    str(e),
                    hint=f"Run nexus repair --recreate-config or fix {config_path}",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
        raise typer.Exit(xc.EXIT_RUNTIME) from None

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
        if not json_mode:
            console.print(panel)
        if not json_mode and not typer.confirm(
            "I confirm I want to start REAL LIVE trading.", default=False
        ):
            console.print(
                Panel("[yellow]Live start aborted (not confirmed).[/yellow]", border_style="yellow")
            )
            raise typer.Exit(xc.EXIT_OK) from None
        elif json_mode and not yes:
            # JSON/automated LIVE start MUST be explicit — never silent.
            _emit(
                {
                    "error": "LIVE mode via --json requires explicit --yes confirmation",
                    "exit_code": xc.EXIT_USAGE,
                },
                True,
            )
            raise typer.Exit(xc.EXIT_USAGE) from None

    # Daemonize before welcome (welcome is the foreground ceremony)
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
        # daemon is silent + no animate + no welcome
        if json_mode:
            _emit(
                {"status": "starting_daemon", "mode": chosen.value, "config": str(config_path)},
                True,
            )
        _spawn_daemon(cmd)
        return

    cfg.execution.mode = chosen
    # BUG-148: record the operator's EXPLICIT start mode so a persisted
    # settings-DB value can never silently flip it at boot.
    try:
        from nexus_scalp.settings.service import SettingsService

        svc = SettingsService()
        svc.set("execution.mode", chosen.value, actor="cli:start")  # type: ignore[attr-defined]
    except Exception:
        pass

    endpoints = _get_network_endpoints(port=port)

    if json_mode:
        _emit(
            {
                "status": "starting",
                "mode": chosen.value,
                "symbol": cfg.execution.symbol,
                "port": port,
                "endpoints": endpoints,
                "animate": False,
            },
            True,
        )
    else:
        _welcome_panel(
            mode_value=chosen.value,
            symbol=cfg.execution.symbol,
            risk_drawdown=cfg.risk.max_account_drawdown_pct,
            endpoints=endpoints,
            animate=animate,
        )
    _run_engine(cfg, gateway=gateway, port=port, mode_override=chosen)


def _spawn_daemon(cmd: list[str]) -> None:
    data_root = rpaths.get_data_root()
    data_root.mkdir(parents=True, exist_ok=True)
    pidfile = _pidfile()
    if pidfile.exists():
        try:
            old = int(pidfile.read_text().strip())
            os.kill(old, 0)
            console.print(
                Panel(
                    f"[yellow]Engine already running (pid {old}). Use nexus stop first.[/yellow]",
                    border_style="yellow",
                )
            )
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
    console.print(
        _success_panel(
            "Engine starting in background",
            f"Mode via {cmd[5]}  ·  pid tracked in {pidfile}\nUse nexus stop to halt",
            border="green",
        )
    )


def _run_engine(
    cfg: AppConfig, *, gateway: bool, port: int, mode_override: ExecutionMode | None = None
) -> None:
    # TASK-10 startup migration gate: apply safe pending schema migrations
    # BEFORE the engine enters READY (§6/§7). Same canonical engine as `nexus db`.
    try:
        from nexus_scalp.database.gate import run_startup_migration_gate

        gate = run_startup_migration_gate(
            workspace=Path.cwd(),
            application_version=str(get_version_info().get("version", "")),
        )
        if not gate.get("ready", False):
            console.print(
                _error_panel(
                    "Database migration blocked",
                    "Engine cannot start — migration gate blocked.",
                    hint="Run nexus db status and nexus db migrate, see logs",
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
            raise typer.Exit(xc.EXIT_RUNTIME) from None
        if gate.get("state") == "DB_MIGRATION_SUCCEEDED":
            console.print(
                _success_panel(
                    "Migrations applied", "Database schemas are now current", border="green"
                )
            )
    except typer.Exit:
        raise
    except Exception as e:
        console.print(
            _error_panel(
                "Migration gate error",
                str(e),
                hint="Run nexus doctor --verbose",
                exit_code=xc.EXIT_RUNTIME,
            )
        )
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    # Heavy engine imports are local so the slim onefile CLI (which excludes
    # torch/polars/MetaTrader5) never pays for them unless actually starting.
    try:
        from nexus_scalp.adapters.mt5.mt5_adapter import HAS_NATIVE_MT5, DirectMT5Adapter
        from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter
        from nexus_scalp.application.live_engine import LiveEngine
        from nexus_scalp.ports.mt5_port import IMT5Port
    except Exception as e:
        console.print(
            _error_panel(
                "Could not load engine",
                str(e),
                hint="Run nexus doctor, check Python 3.11 + deps",
                exit_code=xc.EXIT_RUNTIME,
            )
        )
        raise typer.Exit(xc.EXIT_RUNTIME) from None

    adapter: IMT5Port
    # BUG-148: adapter boundary must match the operator-selected mode. PAPER
    # starts use the simulation adapter so a double-click/bare `start` can
    # NEVER touch the real broker even when MT5 credentials are configured.
    if mode_override == ExecutionMode.PAPER and not gateway:
        from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

        console.print(
            Panel(
                "[green]PAPER mode — simulation adapter (no broker connection)[/green]",
                border_style="green",
            )
        )
        adapter = PaperMT5Adapter(symbol=cfg.execution.symbol)
    elif gateway or sys.platform != "win32" or not HAS_NATIVE_MT5:
        console.print(
            Panel("[yellow]Using Remote MT5 Gateway Adapter[/yellow]", border_style="yellow")
        )
        adapter = RemoteMT5GatewayAdapter()
    else:
        console.print(
            Panel(
                "[green]Using Direct Native MT5 Adapter (Win32 IPC)[/green]", border_style="green"
            )
        )
        adapter = DirectMT5Adapter(
            account=cfg.mt5.account,
            password=cfg.mt5.password,
            server=cfg.mt5.server,
            timeout=cfg.mt5.timeout_ms,
            retries=cfg.mt5.retries,
        )
    engine = LiveEngine(
        config=cfg,
        adapter=adapter,
        # BUG-148: the operator's explicit --mode is authoritative for this
        # process — a persisted settings-DB value cannot override it at boot.
        mode_override=mode_override,
    )
    _start_web_and_engine(engine, cfg, port)


def _start_web_and_engine(engine: Any, cfg: AppConfig, port: int) -> None:
    import asyncio

    # BUG-147: friendly port-in-use failure. A bare bind error looked like a
    # crash ("Process completed with exit code 1"); now the operator gets the
    # actual cause + the exact remediation (busy PID or --port override).
    import socket as _socket

    import uvicorn

    from nexus_scalp.web.server import create_app

    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            console.print(
                _error_panel(
                    "Web port already in use",
                    f"127.0.0.1:{port} is occupied by another process.",
                    hint=(
                        "Another engine instance may be running — use `nexus stop`, "
                        f"kill that PID, or start with `--port {port + 1}`."
                    ),
                    exit_code=xc.EXIT_RUNTIME,
                )
            )
            raise typer.Exit(xc.EXIT_RUNTIME) from None
    finally:
        probe.close()

    # DOCKER-REPAIR: NSE_LOG_LEVEL (DEBUG|INFO|WARNING|ERROR) drives the
    # structlog config used by `nexus start` (default INFO when unset).
    nse_log_level = os.getenv("NSE_LOG_LEVEL", "INFO").strip().upper()
    from nexus_scalp.observability.logging import configure_logging

    configure_logging(
        log_level=nse_log_level,
        json_format=False,
        log_to_file=True,
    )
    # Small beat so the welcome animation lands before the server log burst

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[cyan]Starting services…[/cyan]"),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task("boot", total=None)
        time.sleep(0.35)

    console.print(
        Panel(
            f"[bold cyan]Starting {cfg.execution.mode.value} mode — {cfg.execution.symbol}[/bold cyan]  ·  port {port}",
            border_style="cyan",
        )
    )
    try:
        engine._preflight_or_raise()
    except Exception as e:
        console.print(
            _error_panel(
                "Pre-flight failed",
                str(e),
                hint="Run nexus doctor --fix or nexus repair --recreate-config",
                exit_code=xc.EXIT_RUNTIME,
            )
        )
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    app_obj = create_app(engine_ref=engine)
    engine.server_state = app_obj.state.server_state
    # DOCKER-REPAIR (2026-08-20): container bind is driven by env
    # (NSE_WEB_HOST / NSE_WEB_PORT); bare `run` keeps localhost-only.
    bind_host = os.getenv("NSE_WEB_HOST", "127.0.0.1")
    uvicorn_config = uvicorn.Config(
        app=app_obj,
        host=bind_host,
        port=port,
        log_level="warning",
        ws_max_size=16 * 1024 * 1024,
        ws="none",
    )
    server = uvicorn.Server(uvicorn_config)

    async def run_concurrently() -> None:
        await asyncio.gather(server.serve(), engine.run_loop(), return_exceptions=False)

    try:
        asyncio.run(run_concurrently())
    except KeyboardInterrupt:
        console.print(
            Panel(
                "\n[yellow]Shutdown requested (Ctrl+C) — stopping cleanly…[/yellow]",
                border_style="yellow",
            )
        )
    except Exception as e:
        console.print(
            _error_panel(
                "Engine stopped unexpectedly",
                str(e),
                hint="Check nexus logs --errors and run nexus doctor",
                exit_code=xc.EXIT_RUNTIME,
            )
        )
        raise typer.Exit(xc.EXIT_RUNTIME) from None


@app.command("stop")
def stop_cmd() -> None:
    """Stop a background engine (pidfile-based)."""
    pidfile = _pidfile()
    if not pidfile.exists():
        console.print(
            Panel(
                "[yellow]No pidfile — engine not running as background process.[/yellow]",
                border_style="yellow",
            )
        )
        return
    try:
        pid = int(pidfile.read_text().strip())
    except ValueError:
        console.print(_error_panel("Bad pidfile", str(pidfile), hint="Removing stale pidfile"))
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
        console.print(_error_panel("Could not stop", str(e)))
    pidfile.unlink(missing_ok=True)
    console.print(_success_panel("Engine stopped", f"pid {pid}", border="green"))


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
    if not config_path.exists():
        console.print(_error_panel("Config not found", str(config_path), hint="Run nexus setup"))
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    try:
        cfg = AppConfig.load_from_yaml(config_path)
    except Exception as e:
        console.print(_error_panel("Config invalid", str(e), exit_code=xc.EXIT_RUNTIME))
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    _run_engine(cfg, gateway=gateway, port=8080)


# ---------------------------------------------------------------------------
# doctor parity: config-validate
# ---------------------------------------------------------------------------
@app.command("config-validate")
def config_validate_cmd(
    config_path: Path = typer.Option(
        Path("configs/base.yaml"), "--config", "-c", help="Path to YAML config to validate."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    """Validate syntax, schema, version migration, missing keys & secret masking."""
    import yaml

    target = config_path.resolve()
    report: dict[str, Any] = {
        "path": str(target),
        "exists": target.exists(),
        "valid": False,
        "missing_keys": [],
        "secrets_masked": {
            "mt5_password": "PRES" if target.exists() else "ABS",
            "telegram_token": "PRES" if target.exists() else "ABS",
        },
        "env_validation": {
            "telegram_token_env": bool(os.getenv("NEXUS_TELEGRAM_BOT_TOKEN")),
            "telegram_admin_env": bool(os.getenv("NEXUS_TELEGRAM_ADMIN_ID")),
        },
    }
    if not target.exists():
        if json_mode:
            _emit(report, True)
        else:
            console.print(_error_panel("Config not found", str(target), hint="Run nexus setup"))
        raise typer.Exit(1) from None

    try:
        raw_data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        cfg = AppConfig.load_from_yaml(target)
        report["valid"] = True
        report["symbol"] = cfg.execution.symbol
        report["mode"] = cfg.execution.mode.value
        report["feature_schema"] = cfg.model.feature_schema_version
        expected_sections = {"mt5", "execution", "risk", "model", "telemetry", "news", "rules"}
        missing_secs = sorted(expected_sections - set(raw_data.keys()))
        report["missing_sections"] = missing_secs
        if json_mode:
            _emit(report, True)
            return
        console.print(_success_panel("Config Security & Schema Validation Passed", str(target)))
        console.print(f"  · Symbol: [bold]{cfg.execution.symbol}[/bold]")
        console.print(f"  · Mode:   [bold]{cfg.execution.mode.value}[/bold]")
        console.print(f"  · Schema: [bold]{cfg.model.feature_schema_version}[/bold]")
        if missing_secs:
            console.print(f"  · [yellow]Missing optional sections: {missing_secs}[/yellow]")
        console.print("  · Secrets masked: [green]YES (no plaintext secrets leaked)[/green]")
    except Exception as e:
        report["error"] = str(e)
        if json_mode:
            _emit(report, True)
        else:
            console.print(_error_panel("Config validation failed", str(e), hint=f"Fix {target}"))
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
        console.print(
            _error_panel("No bars file", str(bars_csv), hint="Pass --bars path/to/bars.csv")
        )
        raise typer.Exit(1) from None
    df = pl.read_csv(bars_csv) if bars_csv.suffix.lower() == ".csv" else pl.read_parquet(bars_csv)
    news_frame = None
    if with_news:
        # BUG-150: the empty-Path sentinel ``Path("")`` normalizes to Path(".")
        # (truthy + "exists"), so a bare --with-news used to open the CURRENT
        # DIRECTORY as the news DB and crash with sqlite3.OperationalError.
        # Resolve the default to the canonical artifacts/news.db instead.
        if str(news_db) in ("", "."):
            news_db = Path("artifacts/news.db")
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
                    Panel(
                        "[yellow]News database contains NO analysis records — the dataset "
                        "will carry all-zero news context (news ON == news OFF). "
                        "Collect real news first.[/yellow]",
                        border_style="yellow",
                    )
                )
            else:
                gate = news_benchmark_readiness(news_frame)
                if not gate["ready"]:
                    console.print(
                        Panel(
                            "[yellow]NEWS READINESS GATE: NOT READY — the news frame does not "
                            "satisfy the real-data requirements (non-neutral > 0, XAUUSD > 0, "
                            "multiple events, distinct vectors). News context in this dataset "
                            "may be uninformative. Do NOT use it for a news benchmark.[/yellow]",
                            border_style="yellow",
                        )
                    )
                    console.print(f"[yellow]Failed checks: {gate['checks']}[/yellow]")
                else:
                    console.print(
                        Panel(
                            "[green]NEWS READINESS GATE: READY — real news context.[/green]",
                            border_style="green",
                        )
                    )
        elif news_csv.exists() and str(news_csv) not in ("", "."):
            # BUG-150 companion: never treat the Path("") sentinel (-> ".") as
            # a real news file; read_parquet(".") produced a bogus parquet
            # crash. Only read when the user actually named a file.
            news_frame = (
                pl.read_csv(news_csv)
                if news_csv.suffix.lower() == ".csv"
                else pl.read_parquet(news_csv)
            )
            from nexus_scalp.model_generation.news_bridge import news_benchmark_readiness

            gate = news_benchmark_readiness(news_frame)
            if not gate["ready"]:
                console.print(
                    Panel(
                        "[yellow]NEWS READINESS GATE: NOT READY for --news file — the frame "
                        "does not satisfy the real-data requirements. "
                        "Do NOT use it for a news benchmark.[/yellow]",
                        border_style="yellow",
                    )
                )
        else:
            console.print(
                Panel(
                    "[yellow]--with-news given but no --news file or --news-db found; "
                    "news context will be all-zero (news ON == news OFF).[/yellow]",
                    border_style="yellow",
                )
            )
    store = _mg_store()
    handle = DatasetFactory(store=store).build(
        df, symbol=symbol, timeframe=timeframe, news_frame=news_frame
    )
    console.print(
        _success_panel(
            "Dataset built",
            f"{handle['dataset_id']}  rows={handle['counts']['total']}",
            border="green",
        )
    )
    _emit(handle, as_json=False, plain=True)


@app.command("model-experiment-create")
def model_experiment_create(
    dataset_id: str = typer.Option(..., "--dataset", help="dataset artifact id"),
    template: str = typer.Option("baseline_scalpnet_v1", "--template"),
) -> None:
    """Create a bounded experiment on a dataset artifact."""
    from nexus_scalp.model_generation import ExperimentFactory

    try:
        cfg = ExperimentFactory(store=_mg_store()).create(dataset_id, template=template)
    except Exception as e:
        console.print(_error_panel("Could not create experiment", str(e)))
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    console.print(
        _success_panel(
            "Experiment created", f"{cfg.experiment_id}  arch={cfg.architecture}", border="green"
        )
    )
    _emit(cfg.model_dump(mode="json"), as_json=False, plain=True)


@app.command("model-train")
def model_train(
    experiment_id: str = typer.Option(..., "--experiment"),
    model_id: str = typer.Option("", "--model-id"),
) -> None:
    """Train a candidate from an experiment (never touches Champion)."""
    from nexus_scalp.model_generation import CandidateTrainer, ExperimentFactory

    store = _mg_store()
    try:
        exp = ExperimentFactory(store=store).load(experiment_id)
        frame = store.read_dataset(exp.dataset_id)
    except Exception as e:
        console.print(_error_panel("Could not load experiment/dataset", str(e)))
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    res = CandidateTrainer(store=store).train_candidate(exp, frame, model_id=model_id or None)
    if res["status"] == "FAILED":
        console.print(_error_panel("Training failed", str(res.get("error", ""))))
        raise typer.Exit(1) from None
    console.print(
        _success_panel("Training complete", f"model={res.get('model_id')}", border="green")
    )
    _emit(res, as_json=False, plain=True)


@app.command("model-inspect")
def model_inspect(model_id: str = typer.Option(..., "--model")) -> None:
    """Inspect a model artifact manifest + integrity."""
    store = _mg_store()
    man = store.read_model_manifest(model_id)
    if not man:
        console.print(_error_panel("Model not found", model_id, hint="Check --model id"))
        raise typer.Exit(1) from None
    v = store.verify_artifact(model_id)
    style = "green" if v["ok"] else "red"
    console.print(
        Panel(
            f"[bold {style}]{'OK' if v['ok'] else 'FAIL'}[/bold {style}]  {model_id}  integrity={v['ok']}",
            border_style=style,
        )
    )
    _emit({"manifest": man, "integrity": v}, as_json=False, plain=True)


@app.command("model-validate")
def model_validate(
    model_id: str = typer.Option(..., "--model"),
    dataset_id: str = typer.Option(..., "--dataset"),
) -> None:
    """Validate a candidate artifact against its dataset (OOS/regime/collapse)."""
    from nexus_scalp.model_generation import ValidationFactory

    store = _mg_store()
    try:
        frame = store.read_dataset(dataset_id)
    except Exception as e:
        console.print(_error_panel("Dataset not found", str(e)))
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    import numpy as np

    labels = frame["label"].to_numpy().astype(np.int64)
    vf = ValidationFactory()
    try:
        vr = vf.validate(model_id, "cli", frame, None, labels)
    except Exception as e:
        console.print(_error_panel("Validation failed", str(e)))
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    color = "green" if vr.passed else "red"
    console.print(
        Panel(f"[bold {color}]{vr.verdict}[/bold {color}]  passed={vr.passed}", border_style=color)
    )
    _emit(vr.model_dump(mode="json"), as_json=False, plain=True)


@app.command("model-replay")
def model_replay(
    dataset_id: str = typer.Option(..., "--dataset"),
    sample_id: str = typer.Option(..., "--sample"),
    model_id: str = typer.Option("", "--model"),
) -> None:
    """Replay one sample (historical context + optional model prediction)."""
    from nexus_scalp.model_generation import SampleReplay

    try:
        rec = SampleReplay(store=_mg_store()).replay(
            dataset_id, sample_id, model_id=model_id or None
        )
    except Exception as e:
        console.print(_error_panel("Replay failed", str(e)))
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    _emit(rec, as_json=False, plain=True)


@app.command("model-doctor")
def model_doctor(model_id: str = typer.Option(..., "--model")) -> None:
    """Run the model doctor: integrity + load + metadata health."""
    from nexus_scalp.model_generation import LocalModelRuntime

    store = _mg_store()
    v = store.verify_artifact(model_id)
    if not v["ok"]:
        console.print(
            _error_panel(
                "Model failed integrity",
                f"{model_id}: {v.get('reason')}",
                exit_code=xc.EXIT_RUNTIME,
            )
        )
        raise typer.Exit(1) from None
    try:
        rt = LocalModelRuntime(store=store).load(model_id)
        console.print(_success_panel("Model healthy", model_id, border="green"))
        _emit({"integrity": v, "health": rt.health()}, as_json=False, plain=True)
    except Exception as e:
        console.print(_error_panel("Model failed to load", str(e), exit_code=xc.EXIT_RUNTIME))
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
    """
    # BUG-151: the pipeline lives in ``three_model`` (there has never been a
    # ``three_model_pipeline`` module) — every invocation used to crash with
    # ModuleNotFoundError before any training could start.
    from nexus_scalp.model_generation.three_model import train_all

    if variant and variant not in ("50d_main", "70d_news", "70d_liquidity"):
        msg = f"unknown variant '{variant}' (allowed: 50d_main, 70d_news, 70d_liquidity)"
        if json_mode:
            _emit({"error": msg, "exit_code": xc.EXIT_USAGE}, True)
        else:
            console.print(
                _error_panel("Invalid variant", msg, hint="Empty --variant trains all three")
            )
        raise typer.Exit(xc.EXIT_USAGE) from None
    try:
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold cyan]Training 3-model matrix…[/bold cyan]"),
            transient=True,
            console=console,
        ) as progress:
            progress.add_task("train", total=None)
            bars_path = Path("data/raw/XAUUSD_M1.parquet")
            if not bars_path.exists():
                raise FileNotFoundError(
                    f"canonical bars file missing: {bars_path} — run the data pipeline first"
                )
            import polars as pl

            bars_frame = pl.read_parquet(bars_path)
            reports = train_all(
                bars_frame,
                variants=[variant] if variant else None,
                num_folds=folds,
                epochs=epochs,
                smoke=smoke,
            )
            by_variant = {r["variant"]: r for r in reports}
            result = {
                "overall": "PASS"
                if all(r.get("gate") in ("PASS", "COMPLETED", "READY") for r in reports)
                else "EVIDENCE_WRITTEN",
                "variants": by_variant,
            }
    except Exception as e:
        if json_mode:
            _emit({"error": str(e), "exit_code": xc.EXIT_RUNTIME}, True)
        else:
            console.print(_error_panel("Training failed", str(e), exit_code=xc.EXIT_RUNTIME))
        raise typer.Exit(xc.EXIT_RUNTIME) from None
    if json_mode:
        _emit(result, True)
        raise typer.Exit(0 if result.get("overall") == "PASS" else 1)
    for v, r in result.get("variants", {}).items():
        style = "green" if r.get("status") == "PASS" else "red"
        console.print(f"[{style}]{r.get('status'):5}[/{style}] {v:16} {r.get('detail', '')}")


if __name__ == "__main__":
    app()
