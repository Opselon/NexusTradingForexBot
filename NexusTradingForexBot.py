#!/usr/bin/env python3
"""
Nexus Trading Forex Bot — Main Application Production Launcher
===============================================================
Primary entry point and bootstrapper for the Nexus Scalp Engine (NSE).

This module serves as the main executable script for launching the real-time
trading engine from Visual Studio, Windows PowerShell, or production deployment tasks via:
    `python NexusTradingForexBot.py`

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
from pathlib import Path

# ==============================================================================
# Path Bootstrapping: Register `src` directory in sys.path BEFORE importing core
# ==============================================================================
CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nexus_scalp.adapters.mt5.mt5_adapter import HAS_NATIVE_MT5, DirectMT5Adapter
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.observability.logging import configure_logging, get_logger
from nexus_scalp.ports.mt5_port import IMT5Port
from nexus_scalp.web.server import create_app

# Initialize Rich terminal output console
console = Console()
logger = get_logger("nexus_scalp.launcher")


def display_startup_banner() -> None:
    """
    Renders high-visibility startup banner with engine branding and mode warnings.
    """
    banner_content = (
        "[bold cyan]NEXUS SCALP ENGINE (NSE)[/bold cyan]\n"
        "[dim]Production-Grade High-Performance Quantitative Scalping System[/dim]\n"
        "[bold red]EXECUTION TARGET: LIVE METATRADER 5 TERMINAL EXECUTION[/bold red]"
    )
    console.print(Panel(banner_content, title="System Initialization", border_style="cyan"))


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
                "[yellow]configs/live.yaml not found. Copied template from configs/live.yaml.example[/yellow]"
            )
        elif base_config.exists():
            shutil.copy(base_config, live_config)
            console.print(
                "[yellow]configs/live.yaml not found. Copied template from configs/base.yaml[/yellow]"
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
model:
  confidence_threshold: 0.35
  feature_schema_version: "v1.0"
  model_artifact_path: "artifacts/models/scalp/XAUUSD/v1.0.0/model.pt"
"""
            with open(live_config, "w", encoding="utf-8") as f:
                f.write(default_content)
            console.print(
                "[yellow]configs/live.yaml not found. Generated a default live configuration.[/yellow]"
            )

    return live_config


def print_startup_banner(port: int, mode: str, symbol: str) -> None:
    """Prints a beautiful Bloomberg/Terminal styled system status overview."""
    banner_text = (
        f"[bold cyan]=================================[/bold cyan]\n"
        f"[bold white]AI Trading System Started[/bold white]\n\n"
        f"[bold white]Web Dashboard:[/bold white]\n"
        f"[bold green]http://localhost:{port}[/bold green]\n\n"
        f"[bold white]Status:[/bold white]\n"
        f"[bold green]Running[/bold green]\n\n"
        f"[bold white]AI Engine:[/bold white]\n"
        f"[bold cyan]Active[/bold cyan]\n\n"
        f"[bold white]Bot Mode / Symbol:[/bold white]\n"
        f"[{'red animate-pulse' if mode == 'LIVE' else 'yellow'}]{mode}[/{'red animate-pulse' if mode == 'LIVE' else 'yellow'}] / [bold white]{symbol}[/bold white]\n"
        f"[bold cyan]=================================[/bold cyan]"
    )
    console.print(
        Panel(
            banner_text,
            border_style="cyan",
            title="Nexus Control panel",
            subtitle="System Initialized",
        )
    )


def run_infrastructure_doctor(config_path: Path) -> bool:
    """
    Executes pre-flight infrastructure checks and prints health diagnostics summary.

    Args:
        config_path: Path to configuration YAML file to validate.

    Returns:
        bool: True if all critical diagnostic checks pass.
    """
    console.print(
        "\n[bold yellow]Executing Infrastructure Pre-Flight Diagnostics Check...[/bold yellow]\n"
    )

    table = Table(title="System Runtime Diagnostic Summary")
    table.add_column("Subsystem", style="bold white")
    table.add_column("Status", style="bold")
    table.add_column("Operational Details", style="dim")

    # 1. Check Python Version Invariant
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    table.add_row("Python Runtime", "[green]PASS[/green]", f"Python {py_ver}")

    # 2. Check Host Operating System & IPC Driver
    table.add_row("Host Platform", "OK", sys.platform)

    if HAS_NATIVE_MT5:
        table.add_row(
            "Native MT5 IPC Driver",
            "[green]AVAILABLE[/green]",
            "Direct Win32 IPC Available for Local Terminal Process",
        )
    else:
        table.add_row(
            "Native MT5 IPC Driver",
            "[yellow]UNAVAILABLE[/yellow]",
            "Platform non-Windows or 'MetaTrader5' module missing (Requires Remote Gateway)",
        )

    # 3. Check Configuration File Validity
    if config_path.exists():
        try:
            cfg = AppConfig.load_from_yaml(config_path)
            table.add_row(
                "Configuration File",
                "[green]VALID[/green]",
                f"{config_path} (Symbol: {cfg.execution.symbol})",
            )
        except Exception as err:
            table.add_row("Configuration File", "[red]INVALID[/red]", f"Parse Error: {err}")
            console.print(table)
            return False
    else:
        table.add_row("Configuration File", "[red]MISSING[/red]", f"File not found: {config_path}")
        console.print(table)
        return False

    console.print(table)
    console.print(
        "[bold green]All Infrastructure Pre-Flight Checks Passed Successfully![/bold green]\n"
    )
    return True


def main() -> None:
    """
    Primary application entry point and engine orchestrator launcher.
    """
    display_startup_banner()

    # 1. Parse Command Line Arguments
    parser = argparse.ArgumentParser(
        description="Nexus Trading Forex Bot — Production Scalping Launcher"
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

    args = parser.parse_args()

    config_path = Path(args.config)

    # 2. Execute Doctor Diagnostic Check if explicitly requested
    if args.doctor:
        success = run_infrastructure_doctor(config_path)
        sys.exit(0 if success else 1)

    # Always execute mandatory pre-flight health checks
    if not run_infrastructure_doctor(config_path):
        console.print(
            "[bold red]Pre-flight infrastructure diagnostics failed! Halting launch.[/bold red]"
        )
        sys.exit(1)

    # 3. Load & Validate System Configuration
    console.print(
        f"[bold green]Loading Configuration File:[/bold green] [yellow]{config_path}[/yellow]"
    )
    config = AppConfig.load_from_yaml(config_path)

    # Allow CLI symbol override
    if args.symbol:
        config.execution.symbol = args.symbol.upper()
        console.print(
            f"[bold yellow]Symbol override applied from CLI:[/bold yellow] [bold white]{config.execution.symbol}[/bold white]"
        )

    # 4. Configure System Observability Logging Engine
    configure_logging(
        log_level="INFO",
        json_format=False,
        log_to_file=True,
        log_file_path=Path("logs"),
    )

    logger.info(
        "Bootstrapping Engine Subsystems",
        symbol=config.execution.symbol,
        mode=config.execution.mode.value,
        magic_number=config.execution.magic_number,
        max_drawdown=config.risk.max_account_drawdown_pct,
    )

    # 5. Dynamically Bind Execution Adapter

    adapter: IMT5Port
    if args.gateway or sys.platform != "win32" or not HAS_NATIVE_MT5:
        console.print(
            "[bold yellow]Binding Execution Adapter: Remote MT5 Gateway Client[/bold yellow]"
        )
        from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

        adapter = RemoteMT5GatewayAdapter()
    else:
        console.print(
            "[bold green]Binding Execution Adapter: Direct Native MetaTrader 5 (Win32 IPC)[/bold green]"
        )
        adapter = DirectMT5Adapter(
            account=config.mt5.account,
            password=config.mt5.password,
            server=config.mt5.server,
            timeout=config.mt5.timeout_ms,
        )

    # 6. Instantiate & Launch Live Trading Engine Event Loop Concurrently with Web Server
    web_port = find_available_port(start_port=8080)
    try:
        engine = LiveEngine(config=config, adapter=adapter)
        engine._preflight_or_raise()

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
            "\n[bold yellow]Keyboard interrupt received (Ctrl+C). Initiating clean shutdown...[/bold yellow]"
        )
    except Exception as e:
        logger.critical(
            "Fatal unhandled execution error in launcher thread", error=str(e), exc_info=True
        )
        console.print(f"\n[bold red]FATAL ENGINE EXECUTION ERROR:[/bold red] {e}")
        sys.exit(1)
    finally:
        console.print("[bold cyan]Nexus Scalp Engine lifecycle terminated cleanly.[/bold cyan]")


if __name__ == "__main__":
    main()
