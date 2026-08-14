"""
Nexus Scalp Engine CLI Management Interface
===========================================
Console application providing operational controls, system status diagnostics,
and live engine controls.
"""

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from nexus_scalp.adapters.mt5.mt5_adapter import HAS_NATIVE_MT5, DirectMT5Adapter
from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter
from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.ports.mt5_port import IMT5Port

app = typer.Typer(
    name="nse",
    help="Nexus Scalp Engine (NSE) Operational Management Console",
    add_completion=False,
)
console = Console()


@app.command("doctor")
def system_doctor() -> None:
    """
    Runs diagnostic checks on host environment, Python runtime, and MT5 connectivity options.
    """
    console.print("\n[bold cyan]Nexus Scalp Engine Diagnostic System Check[/bold cyan]\n")

    table = Table(title="System Component Diagnostics")
    table.add_column("Component", style="bold white")
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")

    table.add_row("Host OS Platform", "OK", sys.platform)

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    table.add_row("Python Version", "[green]PASS[/green]", py_ver)

    if HAS_NATIVE_MT5:
        table.add_row(
            "Native MetaTrader5 API",
            "[green]AVAILABLE[/green]",
            "Direct Win32 IPC Available",
        )
    else:
        table.add_row(
            "Native MetaTrader5 API",
            "[yellow]UNAVAILABLE[/yellow]",
            "Platform non-Windows (Use Remote Gateway)",
        )

    console.print(table)


@app.command("config-validate")
def validate_config(
    config_path: Path = typer.Option(
        Path("configs/base.yaml"),
        "--config",
        "-c",
        help="Path to YAML configuration file to validate.",
    ),
) -> None:
    """
    Validates syntax and structural invariants of a specified configuration file.
    """
    console.print(f"Validating configuration file: [bold yellow]{config_path}[/bold yellow]")
    try:
        config = AppConfig.load_from_yaml(config_path)
        console.print("[bold green]Configuration is valid and successfully parsed![/bold green]")
        console.print(f"Symbol: {config.execution.symbol} | Mode: {config.execution.mode.value}")
    except Exception as e:
        console.print(f"[bold red]Configuration validation failed![/bold red]\nError: {e}")
        raise typer.Exit(code=1) from e


@app.command("run")
def run_live_engine(
    config_path: Path = typer.Option(
        Path("configs/live.yaml"),
        "--config",
        "-c",
        help="Path to execution configuration YAML.",
    ),
    use_gateway: bool = typer.Option(
        False,
        "--gateway",
        "-g",
        help="Force remote gateway client mode.",
    ),
) -> None:
    """
    Starts the live scalping trading engine.
    """
    console.print(
        f"[bold green]Starting Live Scalp Engine using config:[/bold green] {config_path}"
    )
    config = AppConfig.load_from_yaml(config_path)

    adapter: IMT5Port
    if use_gateway or sys.platform != "win32":
        console.print(
            "[bold yellow]Using Remote MT5 Gateway Adapter (Cross-Platform / Container)[/bold yellow]"
        )
        adapter = RemoteMT5GatewayAdapter()
    else:
        console.print("[bold green]Using Direct Native MT5 Adapter (Windows Local)[/bold green]")
        adapter = DirectMT5Adapter(
            account=config.mt5.account,
            password=config.mt5.password,
            server=config.mt5.server,
        )

    engine = LiveEngine(config=config, adapter=adapter)
    engine.start()


if __name__ == "__main__":
    app()
