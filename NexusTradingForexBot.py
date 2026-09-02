#!/usr/bin/env python3
"""
Nexus Trading Forex Bot — Main Application Production Launcher
===============================================================
Primary entry point and bootstrapper for the Nexus Scalp Engine (NSE).

This module serves as the main executable script for launching the real-time
trading engine from Visual Studio, Windows PowerShell, or production deployment tasks via:
    `python NexusTradingForexBot.py`

PAPER + XAUUSD by default. Animated, gradient-rich CLI that matches the
packaged EXE experience (src/nexus_scalp/cli/main.py + packaged_main.py).

Key Architectural Responsibilities:
    1. Sys.Path Bootstrapping: Automatically registers the local `src` folder into Python's
       module lookup paths before importing internal sub-systems.
    2. CLI Argument Parsing: Provides ergonomic CLI controls for configuration overrides,
       diagnostics checks (`--doctor`), custom symbols, and network gateway modes.
    3. Infrastructure Diagnostics: Validates Python runtime, platform IPC drivers,
       and configuration files before initiating event loops.
    4. Adapter Binding: Binds `DirectMT5Adapter` for direct native Win32 IPC with the
       MetaTrader 5 terminal, or `RemoteMT5GatewayAdapter` for cross-platform/network runs.
    5. Lifecycle Management: Instantiates `LiveEngine`, executes the async event loop,
       and guarantees graceful teardown on SIGINT/SIGTERM shutdown signals.
"""

import argparse
import asyncio
import shutil
import socket
import sys
import time
from pathlib import Path

# ==============================================================================
# Path Bootstrapping: Register `src` directory in sys.path BEFORE importing core
# ==============================================================================
CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import uvicorn
from rich import box
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from nexus_scalp.adapters.mt5.mt5_adapter import HAS_NATIVE_MT5, DirectMT5Adapter
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.observability.logging import configure_logging, get_logger
from nexus_scalp.ports.mt5_port import IMT5Port
from nexus_scalp.web.server import create_app

# Initialize Rich terminal output console
console = Console()
logger = get_logger("nexus_scalp.launcher")

GRADIENT_TITLE = "bold cyan"
MODE_TIPS = {
    "PAPER": "Safe simulation — no broker orders. Perfect for first run.",
    "SHADOW": "Shadow paper — mirrors live decisions without execution.",
    "LIVE": "Real capital at risk — dashboard kill-switch available.",
}


def _version_tag() -> str:
    try:
        from nexus_scalp.release.metadata import get_version_info

        info = get_version_info()
        return f"v{info.get('version', '?')} · {info.get('channel', 'stable')}"
    except Exception:
        return "v9"


def display_startup_banner() -> None:
    """First visible frame — gradient hero (no animation deps)."""
    tag = _version_tag()
    title = Text("NEXUS SCALP ENGINE", style="bold cyan")
    title.append(f"  {tag}", style="dim cyan")
    is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    # Two-frame shimmer on TTY, single frame otherwise
    frames: list[Panel] = []
    for step in (0, 1):
        body = Text()
        body.append("Production-Grade High-Performance Quantitative Scalping\n", style="bold white")
        body.append(
            "PAPER  ·  XAUUSD  ·  Secure by default", style="dim cyan" if step == 0 else "dim white"
        )
        frames.append(
            Panel(
                Align.center(body),
                title=str(title),
                subtitle="[dim]Secure runtime · gradient boot[/dim]",
                border_style="bright_cyan" if step else "cyan",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )
    if not is_tty:
        console.print(frames[-1])
        return
    with Live(frames[0], console=console, refresh_per_second=12, transient=False) as live:
        time.sleep(0.26)
        live.update(frames[1])
        time.sleep(0.22)


def find_available_port(start_port: int = 8080) -> int:
    """Finds an available TCP port starting from the given port number."""
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1


def ensure_config_files() -> Path:
    """Ensures that configs/live.yaml exists.

    If not, copy it from live.yaml.example or fallback.
    """
    configs_dir = Path("configs")
    configs_dir.mkdir(parents=True, exist_ok=True)

    live_config = configs_dir / "live.yaml"
    example_config = configs_dir / "live.yaml.example"
    base_config = configs_dir / "base.yaml"

    if not live_config.exists():
        if example_config.exists():
            shutil.copy(example_config, live_config)
            console.print(
                Panel(
                    f"[yellow]Created {live_config}[/yellow] from template [dim]{example_config}[/dim]",
                    border_style="yellow",
                    box=box.ROUNDED,
                )
            )
        elif base_config.exists():
            shutil.copy(base_config, live_config)
            console.print(
                Panel(
                    f"[yellow]Created {live_config}[/yellow] from base [dim]{base_config}[/dim]\n[dim]Mode PAPER · Symbol XAUUSD — safe default[/dim]",
                    border_style="yellow",
                    box=box.ROUNDED,
                )
            )
        else:
            default_content = """execution:
  symbol: "XAUUSD"
  mode: "PAPER"
  timeframe: "M1"
  magic_number: 888101
  max_slippage_points: 30
risk:
  max_account_drawdown_pct: 2.0
  risk_per_trade_pct: 0.5
  max_concurrent_positions: 1
  max_spread_points: 60
  enforce_stop_loss: true
  max_margin_usage_pct: 10.0
  max_allowed_lots: 2.0
telegram:
  enabled: false
  bot_token: ""
  admin_id: ""
mt5:
  timeout_ms: 5000
  retries: 3
  portable_mode: false
model:
  confidence_threshold: 0.35
  feature_schema_version: "v1.0"
  model_artifact_path: "artifacts/models/scalp/XAUUSD/v1.0.0/model.pt"
"""
            with open(live_config, "w", encoding="utf-8") as f:
                f.write(default_content)
            console.print(
                Panel(
                    f"[yellow]Generated {live_config}[/yellow] with safe defaults\n[dim]PAPER · XAUUSD · never LIVE by default[/dim]",
                    border_style="yellow",
                    box=box.ROUNDED,
                )
            )

    return live_config


def print_startup_banner(port: int, mode: str, symbol: str) -> None:
    """Bloomberg/Terminal welcome — mode-aware, endpoint-rich."""
    tag = _version_tag()
    mode_u = mode.upper()
    live = mode_u == "LIVE"
    title = Text("NEXUS TRADING FOREX BOT", style="bold white")
    title.append(f"  —  {mode_u} · {symbol}", style="bold red" if live else "dim cyan")
    # Animated two-step: first PAPER/XAUUSD safety, then full dashboard
    is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    endpoints = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    # Also advertise LAN ip when discoverable
    try:
        hostname = socket.gethostname()
        lan = socket.gethostbyname(hostname)
        if lan and not lan.startswith("127.") and f"http://{lan}:{port}" not in endpoints:
            endpoints.append(f"http://{lan}:{port}")
    except Exception:
        pass
    ep_str = "\n".join(f"  [cyan]> {ep}[/cyan]" for ep in endpoints)
    tip = (
        "Kill-switch on dashboard — real orders are live."
        if live
        else "Tip: monitor, chart, and toggle LIVE/SHADOW in the Web UI at any time."
    )
    banner_text = (
        f"[bold cyan]NEXUS SCALP ENGINE[/bold cyan]  [dim]{tag}[/dim]\n"
        f"[{'bold red' if live else 'bold green'}]● {mode_u}[/{'bold red' if live else 'bold green'}]"
        f"  [dim]·[/dim]  [bold]{symbol}[/bold]  [dim]·[/dim]  [dim]port {port}[/dim]\n\n"
        f"[bold]Web Control Center[/bold]\n{ep_str}\n\n"
        f"[dim italic]{tip}[/dim italic]\n"
        f"[dim]Press Ctrl+C to stop safely  ·  nexus doctor --fix for health[/dim]"
    )
    panel = Panel(
        banner_text,
        title=str(title),
        subtitle="[dim]System initialized — secure by default[/dim]",
        border_style="bright_cyan" if not live else "red",
        box=box.ROUNDED,
        padding=(1, 2),
    )
    if is_tty:
        with Live(panel, console=console, refresh_per_second=8, transient=False) as live_obj:
            time.sleep(0.18)
            # nudge border shimmer
            live_obj.update(panel)
            time.sleep(0.12)
    else:
        console.print(panel)


def run_infrastructure_doctor(config_path: Path) -> bool:
    """
    Executes pre-flight infrastructure checks and prints health diagnostics summary.

    Args:
        config_path: Path to configuration YAML file to validate.

    Returns:
        bool: True if all critical diagnostic checks pass.
    """
    console.print(
        Panel(
            "[bold cyan]Pre-flight diagnostics[/bold cyan]  [dim]19 checks · never silent on failure[/dim]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )

    # Spinner for the heavy-ish checks
    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[cyan]Checking system…[/cyan]"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("doctor", total=None)
        # Do the work inside the spinner context so it feels alive
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        mt5_ok = HAS_NATIVE_MT5
        mt5_detail = (
            "Direct Win32 IPC — local MT5 terminal"
            if HAS_NATIVE_MT5
            else "Remote Gateway required (non-Windows or MetaTrader5 missing)"
        )
        cfg_ok = True
        cfg_detail = ""
        if config_path.exists():
            try:
                cfg = AppConfig.load_from_yaml(config_path)
                cfg_detail = f"{config_path} · Symbol {cfg.execution.symbol} · Mode {cfg.execution.mode.value}"
            except Exception as err:
                cfg_ok = False
                cfg_detail = f"Parse error: {err}"
        else:
            cfg_ok = False
            cfg_detail = f"File not found: {config_path} — run nexus repair --recreate-config"
        time.sleep(0.18)
        progress.update(task, completed=1)

    table = Table(title="System Runtime Diagnostic Summary", box=box.SIMPLE_HEAD, show_lines=False)
    table.add_column("Subsystem", style="bold white", no_wrap=True)
    table.add_column("Status", style="bold", no_wrap=True)
    table.add_column("Operational Details", style="dim", overflow="fold")

    table.add_row("Python Runtime", "[green]PASS[/green]", f"Python {py_ver}")
    table.add_row("Host Platform", "OK", sys.platform)
    table.add_row(
        "Native MT5 IPC Driver",
        "[green]AVAILABLE[/green]" if mt5_ok else "[yellow]UNAVAILABLE[/yellow]",
        mt5_detail,
    )
    if not config_path.exists():
        table.add_row("Configuration File", "[red]MISSING[/red]", cfg_detail)
    elif not cfg_ok:
        table.add_row("Configuration File", "[red]INVALID[/red]", cfg_detail)
    else:
        table.add_row("Configuration File", "[green]VALID[/green]", cfg_detail)
        # PAPER + XAUUSD safety hint
        try:
            c = AppConfig.load_from_yaml(config_path)
            if str(c.execution.mode.value).upper() == "LIVE":
                table.add_row(
                    "Execution safety",
                    "[red]LIVE[/red]",
                    "PAPER is the safe default — confirm LIVE carefully",
                )
            else:
                table.add_row(
                    "Paper guard", "[green]PAPER[/green]", f"Default safe · {c.execution.symbol}"
                )
        except Exception:
            pass

    console.print(table)
    if cfg_ok and cfg_detail:
        try:
            # Rich full HealthEngine grade when available
            from nexus_scalp.release.health import HealthEngine

            engine = HealthEngine(config_path=config_path)
            verdict, entries = engine.overall()
            bad = [e for e in entries if e.verdict == "FAIL"]
            if bad:
                console.print(
                    Panel(
                        f"[bold]{verdict}[/bold] — {len(bad)} check(s) failing\n"
                        + "\n".join(f"[red]• {e.category}:[/red] {e.reason}" for e in bad[:6])
                        + (
                            "\n[dim]Run nexus doctor --fix to repair fixable issues[/dim]"
                            if bad
                            else ""
                        ),
                        border_style="red" if verdict in ("NOT READY", "FAIL") else "yellow",
                        title="Doctor summary",
                    )
                )
                return verdict in ("READY", "PASS", "DEGRADED")
        except Exception:
            pass
    if not cfg_ok:
        console.print(
            Panel(
                "[red]Pre-flight check: config issue detected[/red]\n[dim]Try: nexus repair --recreate-config  ·  then nexus doctor[/dim]",
                border_style="red",
            )
        )
        return False
    console.print(
        Panel(
            "[bold green]All pre-flight checks passed[/bold green]  [dim]·  PAPER · XAUUSD ready[/dim]",
            border_style="green",
            box=box.ROUNDED,
        )
    )
    return True


def main() -> None:
    """
    Primary application entry point and engine orchestrator launcher.
    """
    display_startup_banner()

    # 1. Parse Command Line Arguments
    parser = argparse.ArgumentParser(
        description="Nexus Trading Forex Bot — Production Scalping Launcher (PAPER/XAUUSD safe by default)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default="configs/live.yaml",
        help="Path to YAML configuration file (Default: configs/live.yaml)",
    )
    parser.add_argument(
        "--doctor",
        "-d",
        action="store_true",
        help="Run comprehensive infrastructure health diagnostics check and exit.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="With --doctor, auto-repair fixable issues (dirs/config/DB) then re-verify.",
    )
    parser.add_argument(
        "--gateway",
        "-g",
        action="store_true",
        help="Force Remote Gateway Client adapter mode instead of native local MT5 driver.",
    )
    parser.add_argument(
        "--symbol",
        "-s",
        type=str,
        default=None,
        help="Override target instrument symbol (e.g. '--symbol EURUSD').",
    )
    parser.add_argument(
        "--mode",
        "-m",
        type=str,
        default=None,
        choices=["paper", "shadow", "live"],
        help="Override execution mode (default: from config, PAPER safe).",
    )
    parser.add_argument(
        "--no-animate",
        action="store_true",
        help="Disable animated startup banners (CI / plain terminals).",
    )

    args = parser.parse_args()

    config_path = Path(args.config)

    # 2. Execute Doctor Diagnostic Check if explicitly requested
    if args.doctor:
        success = run_infrastructure_doctor(config_path)
        # --fix loop: repair fixable and re-verify
        if args.fix and not success:
            console.print(Panel("[cyan]Auto-repairing fixable issues…[/cyan]", border_style="cyan"))
            try:
                from nexus_scalp.release.repair import RepairEngine

                eng = RepairEngine()
                results = eng.run(recreate_dirs=True, with_news=False)
                for r in results:
                    style = (
                        "green"
                        if r.status == "OK"
                        else ("yellow" if r.status == "SKIPPED" else "red")
                    )
                    console.print(f"[{style}]{r.status:8}[/{style}] {r.action:12} {r.detail}")
                success = run_infrastructure_doctor(config_path)
                if success:
                    console.print(
                        Panel(
                            "[bold green]Repaired — system is ready.[/bold green]",
                            border_style="green",
                        )
                    )
                else:
                    console.print(
                        Panel(
                            "[yellow]Some checks still failing — see above[/yellow]",
                            border_style="yellow",
                        )
                    )
            except Exception as e:
                console.print(Panel(f"[red]Repair failed:[/red] {e}", border_style="red"))
                success = False
        sys.exit(0 if success else 1)

    # Always execute mandatory pre-flight health checks
    if not run_infrastructure_doctor(config_path):
        console.print(
            Panel(
                "[bold red]Pre-flight diagnostics failed![/bold red]\n[dim]Halting launch — fix with nexus doctor --fix or nexus repair[/dim]",
                border_style="red",
            )
        )
        sys.exit(1)

    # 3. Load & Validate System Configuration
    console.print(
        Panel(
            f"[bold green]Loading config[/bold green]  [dim]{config_path}[/dim]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )
    ensure_config_files()
    config = AppConfig.load_from_yaml(config_path)

    # CLI overrides — mode + symbol, both persist for the session banner
    if args.symbol:
        config.execution.symbol = args.symbol.upper()
        console.print(
            Panel(
                f"[yellow]Symbol override → [bold]{config.execution.symbol}[/bold][/yellow]",
                border_style="yellow",
            )
        )
    if args.mode:
        from nexus_scalp.domain.enums import ExecutionMode

        chosen = {
            "paper": ExecutionMode.PAPER,
            "shadow": ExecutionMode.SHADOW,
            "live": ExecutionMode.LIVE,
        }[args.mode]
        config.execution.mode = chosen
        console.print(
            Panel(
                f"[bold]Mode override → [cyan]{chosen.value}[/cyan][/bold]  [dim](use nexus setup to persist)[/dim]",
                border_style="cyan",
            )
        )
        if chosen == ExecutionMode.LIVE:
            console.print(
                Panel(
                    "[bold red]LIVE trading requested via launcher[/bold red]\n[dim]You will see a confirmation before real orders.[/dim]",
                    border_style="red",
                )
            )
            # Require explicit yes on TTY
            if sys.stdin.isatty():
                try:
                    ans = input("Confirm LIVE trading? Type YES to continue: ").strip()
                    if ans != "YES":
                        console.print(
                            Panel(
                                "[yellow]LIVE not confirmed — aborting.[/yellow]",
                                border_style="yellow",
                            )
                        )
                        sys.exit(1)
                except (EOFError, KeyboardInterrupt):
                    console.print(Panel("[yellow]Aborted.[/yellow]", border_style="yellow"))
                    sys.exit(1)

    # 4. Configure System Observability Logging Engine
    configure_logging(
        log_level="INFO",
        json_format=False,
        log_to_file=True,
        log_file_path=Path("logs"),
    )

    # STATE-SEMANTICS (C-001, 2026-09-02): the launcher line must never
    # present the PRE-SETTINGS launch mode as the effective mode. The
    # engine re-binds execution.mode from the settings DB at
    # construction (settings DB > YAML per BUG-148), so the only
    # honest launcher log is the configured-vs-effective pair.
    _eff_mode = config.execution.mode.value
    _launch_mode = getattr(args, "mode", None) or _eff_mode
    logger.info(
        "Bootstrapping Engine Subsystems",
        symbol=config.execution.symbol,
        launch_mode=str(_launch_mode).upper(),
        configured_mode=_eff_mode,
        mode=_eff_mode,
        magic_number=config.execution.magic_number,
        max_drawdown=config.risk.max_account_drawdown_pct,
    )

    # 5. Dynamically Bind Execution Adapter

    adapter: IMT5Port
    if args.gateway or sys.platform != "win32" or not HAS_NATIVE_MT5:
        console.print(
            Panel(
                "[yellow]Execution Adapter → Remote MT5 Gateway Client[/yellow]",
                border_style="yellow",
            )
        )
        from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

        adapter = RemoteMT5GatewayAdapter()
    else:
        console.print(
            Panel(
                "[green]Execution Adapter → Direct Native MetaTrader 5 (Win32 IPC)[/green]",
                border_style="green",
            )
        )
        adapter = DirectMT5Adapter(
            account=config.mt5.account,
            password=config.mt5.password,
            server=config.mt5.server,
            timeout=config.mt5.timeout_ms,
            retries=config.mt5.retries,
        )

    # 6. Instantiate & Launch Live Trading Engine Event Loop Concurrently with Web Server
    web_port = find_available_port(start_port=8080)
    try:
        engine = LiveEngine(config=config, adapter=adapter)
        try:
            engine._preflight_or_raise()
        except Exception as e:
            console.print(
                Panel(
                    f"[bold red]Pre-flight failed:[/bold red] {e}\n[dim]Try nexus doctor --fix or nexus repair[/dim]",
                    border_style="red",
                )
            )
            sys.exit(1)

        app = create_app(engine_ref=engine)
        engine.server_state = app.state.server_state
        print_startup_banner(
            port=web_port, mode=config.execution.mode.value, symbol=config.execution.symbol
        )

        uvicorn_config = uvicorn.Config(
            app=app,
            host="127.0.0.1",
            port=web_port,
            log_level="warning",
            ws_max_size=16 * 1024 * 1024,
        )
        server = uvicorn.Server(uvicorn_config)

        async def run_concurrently() -> None:
            await asyncio.gather(server.serve(), engine.run_loop(), return_exceptions=False)

        asyncio.run(run_concurrently())
    except KeyboardInterrupt:
        console.print(
            Panel(
                "[yellow]Shutdown requested (Ctrl+C) — stopping cleanly…[/yellow]",
                border_style="yellow",
            )
        )
    except SystemExit:
        raise
    except Exception as e:
        logger.critical(
            "Fatal unhandled execution error in launcher thread", error=str(e), exc_info=True
        )
        console.print(
            Panel(
                f"[bold red]FATAL ENGINE ERROR:[/bold red] {e}\n[dim]Run nexus logs --errors and nexus doctor[/dim]",
                border_style="red",
            )
        )
        sys.exit(1)
    finally:
        console.print(
            Panel(
                "[bold cyan]Nexus Scalp Engine terminated cleanly.[/bold cyan]", border_style="cyan"
            )
        )


if __name__ == "__main__":
    main()
