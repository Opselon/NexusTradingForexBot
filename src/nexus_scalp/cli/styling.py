"""CLI visual identity & shared output primitives.

WHERE/WHY: owns the Cinematic palette (MODE_* / GRADIENT_* globals), the shared rich
``Console`` instance, and the small output helpers every command module renders through
(``_emit`` JSON/plain parity, ``_banner``/``_error_panel``/``_success_panel``,
``_welcome_panel``/``_animated_intro``). Extracted verbatim from cli/main.py
(CHG-0032 Step 1) so the visual language has ONE home.

BOUNDARY: pure presentation — no Typer command registration, no engine imports, no I/O
beyond stdout via rich. Every other cli/* module may import from here; this module
imports nothing from cli siblings (import-graph leaf).

USED BY: cli.app_factory, cli.doctor, cli.update_cli, cli.wizard, cli.engine_boot,
and (via the facade) the historical ``from nexus_scalp.cli.main import console`` importers.

DO-NOT-PUT-HERE: command definitions, exit-code policy, engine boot logic.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from rich import box
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from nexus_scalp.domain.enums import ExecutionMode
from nexus_scalp.release import exit_codes as xc
from nexus_scalp.release.metadata import PRODUCT_DISPLAY, get_version_info

console = Console()

# Faster output when --json (no rich truncation, see exit-code contract)
_json_mode_global = False

MODE_ALIASES = {
    "paper": ExecutionMode.PAPER,
    "shadow": ExecutionMode.SHADOW,
    "live": ExecutionMode.LIVE,
}

# ----------------------------------------------------------------------------
# Cinematic palette — gradient + mode-aware styles
# ----------------------------------------------------------------------------
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


# ----------------------------------------------------------------------------
# Small output helpers (no-ANSI JSON mode for CI / automation)
# ----------------------------------------------------------------------------
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


# ----------------------------------------------------------------------------
# Cinematic boot — animated gradient frames for the .exe launch
# ----------------------------------------------------------------------------
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
