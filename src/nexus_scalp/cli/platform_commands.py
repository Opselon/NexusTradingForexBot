"""``nexus platform`` — GitHub star verification via the trusted platform.

Commands:
- ``nexus platform status``     pretty green/red verification panel
- ``nexus platform connect``    one-click flow: [Y/n] open browser -> done
- ``nexus platform verify``     force a fresh platform verification
- ``nexus platform disconnect`` drop the local link

The same panel and the [Y/n] browser prompt are reused by ``nexus setup``,
``nexus model-official`` (gate denial recovery) and engine boot, so the
official-model experience is identical everywhere.
"""

from __future__ import annotations

import os
import sys

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


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("NSE_NO_BROWSER") != "1"


def _ask_open_browser(url: str) -> bool:
    """The [Y/n] echo prompt for opening the system default browser."""
    try:
        answer = input("Open GitHub in your default browser? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if answer in ("n", "no"):
        console.print(f"[dim]Open this URL when ready:[/] {url}")
        return False
    return True


# ---------------------------------------------------------------------------
# Pretty verification panel — green ✓ / red ✗ lines
# ---------------------------------------------------------------------------


def _ok(text: str) -> str:
    return f"[green]✓[/green] {text}"


def _bad(text: str) -> str:
    return f"[red]✗[/red] {text}"


def _info(text: str) -> str:
    return f"[dim]i {text}[/dim]"


def _model_benchmark_line(svc: PlatformLinkService) -> str:
    """'Official model 9.0.14 · win rate 58% · max drawdown 7%' when known.

    Display-only truth cached from /api/client/me (spec §6.1); absent info
    renders nothing — the panel never invents numbers."""
    try:
        info = svc.model_info()
    except Exception:  # anti-crash: allow (display-only)
        return ""
    version = str(info.get("version") or "")
    if not version:
        return ""
    parts = [f"[bold]Official model {version}[/bold]"]
    win_rate = info.get("win_rate")
    max_dd = info.get("max_drawdown")
    if isinstance(win_rate, (int, float)):
        parts.append(f"win rate {win_rate * 100:.0f}%")
    if isinstance(max_dd, (int, float)):
        parts.append(f"max drawdown {max_dd * 100:.0f}%")
    return " · ".join(parts)


def _token_line(svc: PlatformLinkService) -> str:
    """Masked token status: nsc_ab12…ef90 · expires <date|unknown>."""
    meta = svc.token_status()
    if meta is None:
        return _info("Token: (none — not connected)")
    masked, expires = meta
    tail = f" · expires {expires}" if expires else ""
    return _info(f"Token {masked}{tail}")


def render_status_panel(status: LinkStatus, svc: PlatformLinkService) -> Panel:
    repo = status.repository or "Opselon/NexusTradingForexBot"
    state = status.state
    who = f"@{status.github_login or '?'}"
    lines: list[str] = []

    if state is VerificationState.STARRED_VERIFIED:
        lines.append(_ok(f"[bold]Connected[/] as {who}"))
        lines.append(_ok(f"Star verified: {repo}"))
        lines.append(_ok("[bold]Official models UNLOCKED[/]"))
        # Platform API §6.1 model.benchmark — show WHAT the star unlocks.
        bench = _model_benchmark_line(svc)
        if bench:
            lines.append(_ok(bench))
    elif state is VerificationState.NOT_STARRED:
        lines.append(_ok(f"Connected as {who}"))
        lines.append(_bad(f"NOT starred: {repo}"))
        lines.append(_bad("Official models LOCKED"))
    elif state is VerificationState.AUTHENTICATION_PENDING:
        lines.append(_info("Connecting securely to GitHub — finish in your browser"))
    elif state is VerificationState.VERIFICATION_PENDING:
        lines.append(_ok(f"Connected as {who}"))
        lines.append(_bad("Star verification pending — star the repo, then Verify Again"))
    elif state is VerificationState.VERIFICATION_EXPIRED:
        lines.append(_bad("Verification EXPIRED — reconnect GitHub to renew"))
    elif state is VerificationState.VERIFICATION_UNAVAILABLE:
        lines.append(_bad("Verification platform unreachable — official downloads paused"))
    else:  # NOT_CONNECTED
        lines.append(_bad("Not connected — official models LOCKED"))
    lines.append(_token_line(svc))
    if status.reason:
        lines.append(_info(status.reason))

    border = "cyan"
    if state is VerificationState.STARRED_VERIFIED:
        border = "green"
    elif state in (VerificationState.NOT_STARRED, VerificationState.VERIFICATION_EXPIRED):
        border = "red"
    return Panel("\n".join(lines), title="GitHub Verification", border_style=border)


# ---------------------------------------------------------------------------
# Star promo — the ONE shared "Continue with GitHub" pitch (setup wizard,
# boot offer, download-gate recovery all render the same copy).
# ---------------------------------------------------------------------------

_REPO_URL = "https://github.com/Opselon/NexusTradingForexBot"


def render_star_promo(repo: str | None = None, url: str | None = None) -> Panel:
    """The pretty star pitch shown before the connect flow.

    Copy contract (2026-11 Easy Setup Wizard upgrade): open repository →
    give a Star ⭐ → write Y to confirm → verification (may take time) →
    official models with benchmark / edge / winrate unlock for download.
    """
    repo = repo or "Opselon/NexusTradingForexBot"
    url = url or _REPO_URL
    body = "\n".join(
        [
            "[bold cyan]⭐ Continue with GitHub — unlock the Official Models[/]",
            "",
            "Official models ship with [bold]benchmark, edge and winrate[/] stats —",
            "you will be able to download them once your star is verified.",
            "",
            "[bold]Steps:[/]",
            f"  [bold]1.[/] Open our repository: [link={url}]{url}[/link]",
            "  [bold]2.[/] Give the project a Star [bold yellow]⭐[/bold yellow]",
            "  [bold]3.[/] Verify — may take a few seconds",
            "",
            "After starring, write [bold green]Y[/] and press Enter to confirm your star.",
        ]
    )
    return Panel(body, title="Continue with GitHub", border_style="cyan")


def _ask_retry_after_star(url: str) -> bool:
    """The post-not-starred question: star first, then Y re-verifies."""
    try:
        answer = input("After starring, write Y to verify again [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer not in ("n", "no")


def _service() -> PlatformLinkService:
    return PlatformLinkService()


@platform_app.command("status")
def platform_status() -> None:
    """Show the current platform verification state (no network)."""
    svc = _service()
    console.print(render_status_panel(svc.status(), svc))


def run_connect_flow(svc: PlatformLinkService, timeout: float = 600.0) -> LinkStatus:
    """The ONE standard connect flow, shared by CLI / wizard / boot / gate.

    - Renders the star promo (open repo → Star ⭐ → write Y → verify).
    - Prints the generated link directly (the user may self-open it).
    - Asks exactly ONE question: open the browser? [Y/n]
    - Polls resiliently either way until verified / unstarred / expired.
    - A NOT_STARRED verdict gets the recovery loop: the steps are shown again
      and the user's Y re-verifies (bounded, never an infinite nag).
    """
    status = svc.start_connect()
    console.print(render_star_promo(status.repository, status.verification_url))
    if status.verification_url:
        console.print(
            Panel(
                "GitHub's own authorization page opens — no codes, no tokens:\n"
                "this app only receives your verification result.\n\n"
                f"[bold]Link:[/] [link={status.verification_url}]{status.verification_url}[/link]",
                title="Secure GitHub authorization",
                border_style="cyan",
            )
        )
    if status.verification_url and _interactive() and _ask_open_browser(status.verification_url):
        if not svc.open_verification_browser():
            console.print(f"[yellow]Open this URL manually:[/] {status.verification_url}")
    else:
        console.print("[dim]Open the link above — this stays polling until done.[/]")

    final = _verification_loop(svc, timeout)
    console.print(render_status_panel(final, svc))
    _after_verification(final)
    return final


def _verification_loop(svc: PlatformLinkService, timeout: float) -> LinkStatus:
    """Poll to a terminal state; recover from NOT_STARRED with a bounded,
    polite star→verify-again loop (the star may land seconds after starring)."""
    final = LinkStatus(state=VerificationState.NOT_CONNECTED)
    for _attempt in range(3):
        with console.status("Verifying your star… (this may take time; Ctrl+C to cancel)"):
            final = svc.await_verification(timeout=timeout)
        if final.state is not VerificationState.NOT_STARRED:
            return final
        console.print(
            "[red]✗ You have not starred the project yet.[/] Official models stay locked.\n"
        )
        console.print(render_star_promo(final.repository))
        if not (_interactive() and _ask_retry_after_star(final.verification_url or "")):
            return final
        with console.status("Re-checking your star…"):
            final = svc.reverify()
        if final.state is VerificationState.STARRED_VERIFIED:
            return final
    return final


@platform_app.command("connect")
def platform_connect(
    timeout: float = typer.Option(
        600.0, "--timeout", help="How long to wait for the GitHub flow (seconds)."
    ),
) -> None:
    """Connect GitHub: ONE click — the official GitHub page opens, done."""
    run_connect_flow(_service(), timeout=timeout)


@platform_app.command("verify")
def platform_verify() -> None:
    """Force a fresh verification against the platform."""
    svc = _service()
    final = svc.reverify()
    console.print(render_status_panel(final, svc))
    _after_verification(final)


@platform_app.command("disconnect")
def platform_disconnect() -> None:
    """Drop the local platform link (revoke server-side via the dashboard)."""
    svc = _service()
    console.print(render_status_panel(svc.disconnect(), svc))
    console.print("[dim]Revoke the token server-side from the platform dashboard.[/]")


def _after_verification(final: LinkStatus) -> None:
    """Post-verification UX: unlocked message + one-time diagnostics consent."""
    if final.state is VerificationState.STARRED_VERIFIED:
        console.print(
            "[bold green]⭐ Star verified — Official Models UNLOCKED.[/] "
            "Download them with [bold]nexus model-official[/] "
            "(benchmark · edge · winrate included)."
        )
        _maybe_ask_diagnostics_consent()
    elif final.state is VerificationState.NOT_STARRED:
        console.print(
            "[red]You have not starred the project yet.[/] One more step to unlock "
            "official models:\n"
            f"  1. Star [link={final.repository or _REPO_URL}]{final.repository or _REPO_URL}[/link] on GitHub\n"
            "  2. Run [bold]nexus platform verify[/] (server-checked, no browser needed)\n"
            "     or [bold]nexus platform connect[/] to reopen the link."
        )


def _maybe_ask_diagnostics_consent() -> None:
    """One-time, explicit, interactive-only consent ask (user decides)."""
    if not _interactive():
        return
    try:
        from nexus_scalp.log_shipping import ensure_consent

        ensure_consent()
    except Exception:  # pragma: no cover - consent must never break the flow
        pass


# ---------------------------------------------------------------------------
# Boot / first-setup / download-gate recovery (shared interactive helpers)
# ---------------------------------------------------------------------------


def offer_boot_connect(svc: PlatformLinkService, status: LinkStatus) -> None:
    """TTY-only offer at engine boot / setup wizard: the SAME standard flow."""
    console.print(render_star_promo(status.repository))
    console.print(render_status_panel(status, svc))
    connected = status.state.connected
    question = (
        "Verify now (server check, no browser)? [Y/n]"
        if connected
        else "Continue with GitHub now to unlock the official model? [Y/n]"
    )
    try:
        answer = input(question).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if answer in ("n", "no"):
        console.print("[dim]Skipping — run 'nexus platform connect' anytime.[/]")
        return
    if connected:
        with console.status("Verifying your star… (may take a few seconds)"):
            final = svc.reverify()
        if final.state is VerificationState.NOT_STARRED:
            final = _verification_loop(svc, timeout=600.0)
        console.print(render_status_panel(final, svc))
        _after_verification(final)
    else:
        run_connect_flow(svc)


def guard_official_download_interactive(svc: PlatformLinkService) -> None:
    """Gate for official downloads with interactive recovery.

    On denial (TTY only): show the pretty panel, offer the one-click connect,
    re-run the gate once. Non-interactive: exit exactly like the strict gate.
    """
    try:
        svc.require_download_authorization()
        return
    except PlatformAuthorizationRequiredError as exc:
        console.print(render_status_panel(exc.status, svc))
        if not _interactive():
            console.print(f"[red]{exc.message}[/]")
            raise typer.Exit(code=2) from exc
        try:
            answer = input("Connect GitHub now and retry the download? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer in ("n", "no"):
            raise typer.Exit(code=2) from exc
        final = run_connect_flow(svc)
        if final.state is VerificationState.STARRED_VERIFIED:
            svc.require_download_authorization()
            return
        console.print("[red]Official model download is still locked.[/]")
        raise typer.Exit(code=2) from None


def guard_official_download() -> None:
    """Non-interactive gate (scripted callers); use the interactive variant in CLIs."""
    try:
        _service().require_download_authorization()
    except PlatformAuthorizationRequiredError as exc:
        console.print(render_status_panel(exc.status, _service()))
        console.print(f"[red]{exc.message}[/]")
        raise typer.Exit(code=2) from exc


__all__ = [
    "PlatformLinkConfig",
    "guard_official_download",
    "guard_official_download_interactive",
    "offer_boot_connect",
    "platform_app",
    "render_status_panel",
    "run_connect_flow",
]
