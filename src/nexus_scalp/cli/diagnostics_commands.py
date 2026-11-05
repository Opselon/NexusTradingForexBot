"""``nexus diagnostics`` — opt-in Warning/Error log shipping controls.

The pipeline is OFF until the user explicitly answers yes to the one-time
consent ask (or runs ``enable`` here). Only WARNING+ records are collected,
secrets are redacted, and the payload is bounded; nothing is ever sent while
consent is off.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

from nexus_scalp.log_shipping.consent import (
    is_enabled,
    set_consent,
    status_snapshot,
)

diagnostics_app = typer.Typer(
    help="Opt-in diagnostics: send only Warning/Error logs to the Nexus server.",
    no_args_is_help=True,
)
console = Console()


@diagnostics_app.command("status")
def diagnostics_status() -> None:
    """Show the consent state and pipeline activity."""
    from nexus_scalp.log_shipping.shipper import get_session_shipper

    snap = status_snapshot()
    lines = [
        f"Consent: [bold]{snap['consent']}[/bold]",
    ]
    if snap.get("asked_at"):
        lines.append(f"Asked at: {snap['asked_at']}")
    if is_enabled():
        shipper = get_session_shipper(source="cli")
        lines.append(f"Collected (unsent): {len(shipper.collector)}")
        lines.append(f"Last sent: {shipper.last_sent_at or 'never'}")
        if shipper.last_error:
            lines.append(f"Last error: {shipper.last_error}")
    console.print(
        Panel(
            "\n".join(lines),
            title="Diagnostics",
            border_style="green" if is_enabled() else "yellow",
        )
    )


@diagnostics_app.command("enable")
def diagnostics_enable() -> None:
    """Turn ON Warning/Error log shipping (explicit user action)."""
    set_consent(True)
    console.print("[green]Diagnostics enabled — only Warning/Error logs are sent.[/]")


@diagnostics_app.command("disable")
def diagnostics_disable() -> None:
    """Turn OFF log shipping immediately (queued unsent records are dropped)."""
    set_consent(False)
    try:
        from nexus_scalp.log_shipping.shipper import reset_session_shipper

        reset_session_shipper()
    except Exception:  # pragma: no cover
        pass
    console.print("[yellow]Diagnostics disabled — nothing is sent.[/]")


@diagnostics_app.command("test")
def diagnostics_test() -> None:
    """Collect one synthetic Warning and attempt a delivery round now."""
    if not is_enabled():
        console.print("[red]Diagnostics are disabled — enable first.[/]")
        raise typer.Exit(code=1)
    import logging

    from nexus_scalp.log_shipping.shipper import get_session_shipper

    logging.getLogger("nexus.diagnostics.test").warning(
        "diagnostics self-test record (safe to ignore)"
    )
    shipper = get_session_shipper(source="cli")
    sent = shipper.flush_once()
    console.print(f"[green]Flush complete — {sent} record(s) delivered.[/]")
    if shipper.last_error:
        console.print(f"[yellow]Last error: {shipper.last_error}[/]")


__all__ = ["diagnostics_app"]
