"""``nexus platform`` — GitHub star verification via the trusted platform.

Commands:
- ``nexus platform status``     show the current link state
- ``nexus platform connect``    run the browser-based connect flow
- ``nexus platform verify``     force a fresh platform verification
- ``nexus platform disconnect`` drop the local link
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel

from nexus_scalp.platform_link import (
    LinkStatus,
    PlatformAuthorizationRequiredError,
    PlatformLinkService,
    VerificationState,
)
from nexus_scalp.platform_link.config import PlatformLinkConfig

platform_app = typer.Typer(
    help="GitHub star verification (official model access).",
    no_args_is_help=True,
)
console = Console()

_STATE_STYLES = {
    VerificationState.STARRED_VERIFIED: ("bold green", "STARRED & VERIFIED"),
    VerificationState.NOT_STARRED: ("bold red", "NOT STARRED"),
    VerificationState.NOT_CONNECTED: ("bold yellow", "NOT CONNECTED"),
    VerificationState.AUTHENTICATION_PENDING: ("bold cyan", "AWAITING BROWSER APPROVAL"),
    VerificationState.VERIFICATION_PENDING: ("bold yellow", "VERIFICATION PENDING"),
    VerificationState.VERIFICATION_EXPIRED: ("bold red", "VERIFICATION EXPIRED"),
    VerificationState.VERIFICATION_UNAVAILABLE: ("bold red", "PLATFORM UNAVAILABLE"),
}


def _render_status(status: LinkStatus) -> Panel:
    style, label = _STATE_STYLES.get(status.state, ("bold", status.state.value))
    lines = [f"[{style}]{label}[/]"]
    if status.github_login:
        lines.append(f"GitHub account: [bold]@{status.github_login}[/]")
    if status.repository:
        lines.append(f"Repository:     {status.repository}")
    if status.installation_id:
        lines.append(f"Installation:   {status.installation_id}")
    if status.reason:
        lines.append(f"Detail:         {status.reason}")
    if status.verification_url:
        lines.append(f"Verification:   {status.verification_url}")
    return Panel("\n".join(lines), title="Platform link", border_style="cyan")


def _service() -> PlatformLinkService:
    return PlatformLinkService()


@platform_app.command("status")
def platform_status() -> None:
    """Show the current platform verification state (no network)."""
    console.print(_render_status(_service().status()))


@platform_app.command("connect")
def platform_connect(
    timeout: float = typer.Option(
        600.0, "--timeout", help="How long to wait for browser approval (seconds)."
    )
) -> None:
    """Connect GitHub: opens the verification page and waits for approval."""
    svc = _service()
    status = svc.start_connect()
    console.print(
        Panel(
            "A verification page will open in your browser.\n"
            f"Verification code: [bold]{status.client_code}[/]\n"
            f"URL: {status.verification_url}",
            title="Connect GitHub",
            border_style="cyan",
        )
    )
    opened = svc.open_verification_browser()
    if not opened:
        console.print(
            "[yellow]Could not open a browser automatically.[/] "
            "Open the URL above manually, approve the code, then return here."
        )
    with console.status("Waiting for approval… (Ctrl+C to cancel)"):
        final = svc.await_verification(timeout=timeout)
    console.print(_render_status(final))
    if final.state is VerificationState.STARRED_VERIFIED:
        console.print("[green]Official model downloads are unlocked.[/]")
    elif final.state is VerificationState.NOT_STARRED:
        console.print(
            f"Star [link={final.repository}]{final.repository}[/link] on GitHub, "
            "then run [bold]nexus platform verify[/]."
        )


@platform_app.command("verify")
def platform_verify() -> None:
    """Force a fresh verification against the platform."""
    console.print(_render_status(_service().reverify()))


@platform_app.command("disconnect")
def platform_disconnect() -> None:
    """Drop the local platform link (revoke server-side via the dashboard)."""
    console.print(_render_status(_service().disconnect()))
    console.print("[dim]Revoke the token server-side from the platform dashboard.[/]")


def guard_official_download() -> None:
    """CLI seam: raise a rich-rendered denial when the gate refuses."""
    try:
        _service().require_download_authorization()
    except PlatformAuthorizationRequiredError as exc:
        console.print(_render_status(exc.status))
        console.print(f"[red]{exc.message}[/]")
        raise typer.Exit(code=2) from exc


__all__ = [
    "PlatformLinkConfig",
    "guard_official_download",
    "platform_app",
]
