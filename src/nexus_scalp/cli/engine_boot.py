"""Engine lifecycle commands — start / stop / restart / run.

WHERE/WHY: ``nexus start`` (PAPER default, LIVE needs explicit confirmation —
safety contract sections 17/31/59), the BUG-170 atomic-pidfile daemon spawn
(O_EXCL claim + loser grace window, BUG-179), the migration-gated engine
construction (_run_engine: DB gate → adapter selection → LiveEngine), the
uvicorn co-boot (_start_web_and_engine, BUG-147 port probe) and the pidfile-based
stop/restart/run legacy-parity commands. Extracted verbatim from cli/main.py
(CHG-0032 Step 1).

BOUNDARY: engine PROCESS lifecycle only. No update logic, no wizard, no diagnostic
commands. Heavy imports stay function-local (slim onefile CLI must not pay for
torch/polars/MT5 unless actually starting).

USED BY: cli.main facade (registers start/stop/restart/run), tests
(test_cli_end_to_end start/stop guards monkeypatch ``_run_engine``/``_spawn_daemon``
through the facade; test_user_hunt_bug170_171 drives the daemon race directly).

DO-NOT-PUT-HERE: model commands, update commands, setup wizard.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from nexus_scalp.cli.app_factory import _resolve_facade_seam, app
from nexus_scalp.cli.styling import (
    MODE_ALIASES,
    _emit,
    _error_panel,
    _success_panel,
    _welcome_panel,
    console,
)
from nexus_scalp.cli.wizard import _get_network_endpoints
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.release import exit_codes as xc
from nexus_scalp.release import paths as rpaths
from nexus_scalp.release.metadata import get_version_info


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
        _resolve_facade_seam("_spawn_daemon", _spawn_daemon)(cmd)
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
    _resolve_facade_seam("_run_engine", _run_engine)(
        cfg, gateway=gateway, port=port, mode_override=chosen
    )


def _spawn_daemon(cmd: list[str]) -> None:
    data_root = rpaths.get_data_root()
    data_root.mkdir(parents=True, exist_ok=True)
    pidfile = _pidfile()
    # BUG-170: atomic claim. The old check-then-write let two concurrent
    # `nexus start` invocations both pass the liveness check and both
    # spawn an engine (web-bind crash / duplicate sessions). os.open with
    # O_CREAT|O_EXCL makes exactly ONE racer own the pidfile; losers then
    # re-read it and report the winner as the running engine.
    claimed = False
    try:
        fd = os.open(str(pidfile), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        claimed = True
    except FileExistsError:
        claimed = False
    except OSError:
        # Fall back to legacy behavior only when O_EXCL itself is
        # unsupported (never on CPython/Windows or POSIX) — keep failing
        # loudly rather than silently spawning twice.
        raise

    if not claimed:
        # Someone else owns the pidfile: liveness-check THEIR pid.
        # BUG-170-hardening: between O_EXCL creation and the pid write the
        # file is briefly EMPTY. Reading it then yields ValueError -> the
        # old code treated a live claim as stale, unlinked the winner's
        # pidfile, re-claimed and spawned a SECOND engine (CI flake,
        # run 33433361894). Give the winner a short grace window to write
        # the pid before declaring the file stale.
        pid_text: str | None = None
        for _ in range(25):  # ~0.5s total
            try:
                pid_text = pidfile.read_text().strip()
            except OSError:
                pid_text = None
            if pid_text:
                break
            time.sleep(0.02)
        try:
            old = int(pid_text or "")
            os.kill(old, 0)
            console.print(
                Panel(
                    f"[yellow]Engine already running (pid {old}). Use nexus stop first.[/yellow]",
                    border_style="yellow",
                )
            )
            return
        except (OSError, ValueError):
            # Dead pid (OSError) or stale empty file after the grace window
            # (ValueError): remove it and retry the atomic claim ONCE. A
            # pid we just read is re-checked with kill() above, so this
            # path can no longer race a live claim's write.
            pidfile.unlink(missing_ok=True)
            try:
                fd = os.open(str(pidfile), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                claimed = True
            except FileExistsError:
                console.print(
                    Panel(
                        "[yellow]Another nexus start is spawning right now. Use nexus stop first.[/yellow]",
                        border_style="yellow",
                    )
                )
                return
    if claimed:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
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
            application_version=str(
                _resolve_facade_seam("get_version_info", get_version_info)().get("version", "")
            ),
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
    stopped = False
    already_gone = False
    error_text = ""
    try:
        if sys.platform == "win32":
            # BUG-172: the taskkill result was discarded, so a DEAD pid
            # (rc=128 process-not-found) printed a green success panel and
            # a PID-reuse kill of the WRONG process went unreported.
            kill = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False
            )
            out = ((kill.stdout or b"") + (kill.stderr or b"")).decode(errors="replace")
            if kill.returncode == 0:
                stopped = True
            elif kill.returncode == 128 or "not found" in out.lower():
                already_gone = True
            else:
                error_text = out.strip()[:200] or f"taskkill rc={kill.returncode}"
        else:
            os.kill(pid, 15)
            stopped = True
    except ProcessLookupError:  # POSIX: pid already gone
        already_gone = True
    except OSError as e:
        error_text = str(e)
    pidfile.unlink(missing_ok=True)
    if stopped:
        console.print(_success_panel("Engine stopped", f"pid {pid}", border="green"))
    elif already_gone:
        console.print(
            Panel(
                f"[yellow]Engine already stopped (stale pidfile, pid {pid}).[/yellow]",
                border_style="yellow",
            )
        )
    else:
        console.print(
            _error_panel(
                "Could not stop",
                error_text or f"unknown failure stopping pid {pid}",
                hint=f'Verify the process manually: tasklist /FI "PID eq {pid}"',
                exit_code=xc.EXIT_RUNTIME,
            )
        )
        raise typer.Exit(xc.EXIT_RUNTIME) from None


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
    _resolve_facade_seam("_run_engine", _run_engine)(cfg, gateway=gateway, port=8080)
