#!/usr/bin/env python3
"""
Nexus Scalp Engine (NSE) - Main Orchestrated Launcher
====================================================
Primary one-command entry point launching the quantitative trading engine,
integrated AI decision pipelines, and modern web-based control panel on
a dynamically detected available port.
"""

import argparse
import asyncio
from pathlib import Path
import shutil
import socket
import sys
from typing import Optional

# Register `src` directory in sys.path
CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rich.console import Console
from rich.panel import Panel
import uvicorn

from nexus_scalp.adapters.mt5.mt5_adapter import HAS_NATIVE_MT5, DirectMT5Adapter
from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.observability.logging import configure_logging, get_logger
from nexus_scalp.web.server import create_app

console = Console()
logger = get_logger("nexus_scalp.launcher.main")


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
            console.print("[yellow]configs/live.yaml not found. Copied template from configs/live.yaml.example[/yellow]")
        elif base_config.exists():
            shutil.copy(base_config, live_config)
            console.print("[yellow]configs/live.yaml not found. Copied template from configs/base.yaml[/yellow]")
        else:
            # Create a basic default file
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
            console.print("[yellow]configs/live.yaml not found. Generated a default live configuration.[/yellow]")

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
    console.print(Panel(banner_text, border_style="cyan", title="Nexus Control panel", subtitle="System Initialized"))


async def main_orchestrator(config_path: Path, force_gateway: bool) -> None:
    """Concurrently executes the FastAPI Web Server and the Live Trading Engine."""
    # 1. Load System configuration
    config = AppConfig.load_from_yaml(config_path)

    # 2. Configure Logger
    configure_logging(
        log_level="INFO",
        json_format=(config.execution.mode == ExecutionMode.LIVE and False),
        log_to_file=True,
    )

    # 3. Bind execution Adapter
    if force_gateway or sys.platform != "win32" or not HAS_NATIVE_MT5:
        logger.info("Binding Remote MT5 Gateway adapter.")
        adapter = RemoteMT5GatewayAdapter()
    else:
        logger.info("Binding Native MetaTrader 5 Win32 IPC adapter.")
        adapter = DirectMT5Adapter(
            account=config.mt5.account,
            password=config.mt5.password,
            server=config.mt5.server,
            timeout=config.mt5.timeout_ms,
        )

    # 4. Instantiate Live Trading Engine
    engine = LiveEngine(config=config, adapter=adapter)

    # Perform pre-flight validations
    engine._preflight_or_raise()

    # 5. Find available web dashboard port
    web_port = find_available_port(start_port=8080)

    # 6. Create Web Dashboard FastAPI app with engine reference
    app = create_app(engine_ref=engine)

    # 7. Print startup status
    print_startup_banner(
        port=web_port,
        mode=config.execution.mode.value,
        symbol=config.execution.symbol
    )

    # 8. Setup Uvicorn server configuration
    uvicorn_config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=web_port,
        log_level="warning",
        ws_max_size=16*1024*1024
    )
    server = uvicorn.Server(uvicorn_config)

    # 9. Concurrently run both services in the active async event loop
    try:
        await asyncio.gather(
            server.serve(),
            engine.run_loop(),
            return_exceptions=False
        )
    except asyncio.CancelledError:
        logger.info("Lifecycle tasks cancelled cleanly.")
    except Exception as e:
        logger.critical("Fatal exception in main system execution thread.", error=str(e), exc_info=True)
    finally:
        logger.info("Initiating system shutdown and closing core resources.")
        await engine._shutdown_async()


def run() -> None:
    """Parser entry point resolving configuration settings and starting the main loop."""
    parser = argparse.ArgumentParser(description="Nexus Scalp Engine Main Launcher")
    parser.add_argument(
        "--config", "-c", type=str, default=None, help="Path to config file"
    )
    parser.add_argument(
        "--gateway", "-g", action="store_true", help="Force Gateway Mode"
    )
    args = parser.parse_args()

    # Ensure configs/live.yaml exists
    live_yaml_path = ensure_config_files()
    config_path = Path(args.config) if args.config else live_yaml_path

    try:
        asyncio.run(main_orchestrator(config_path, args.gateway))
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Keyboard interrupt received (Ctrl+C). Shutting down...[/bold yellow]")
    finally:
        console.print("[bold cyan]System execution complete.[/bold cyan]")


if __name__ == "__main__":
    run()
