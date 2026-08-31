"""First-run setup wizard + uninstall CLI.

WHERE/WHY: the interactive ``nexus install``/``nexus setup`` wizard flow
(_wizard_flow: compatibility → repair → mode selection (never silently LIVE) →
health), its config-persistence helpers and the ``nexus uninstall`` data-safety
command. Extracted verbatim from cli/main.py (CHG-0032 Step 1).

BOUNDARY: setup/uninstall ceremony only. Engine boot lives in engine_boot.py;
safety contract (PAPER/SHADOW defaults, LIVE needs explicit confirmation) is
preserved byte-identically.

USED BY: cli.main facade (registers setup/install/uninstall), cli.engine_boot
(_get_network_endpoints).

DO-NOT-PUT-HERE: start/stop commands, config validation commands (doctor.py).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from nexus_scalp.cli.app_factory import app
from nexus_scalp.cli.styling import (
    MODE_ALIASES,
    _banner,
    _emit,
    _error_panel,
    _success_panel,
    _verdict_style,
    console,
)
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.release import environment as renv
from nexus_scalp.release import evaluate as reval
from nexus_scalp.release import exit_codes as xc
from nexus_scalp.release import health as rhealth
from nexus_scalp.release import paths as rpaths
from nexus_scalp.release import repair as rrepair
from nexus_scalp.release.metadata import get_version_info


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
