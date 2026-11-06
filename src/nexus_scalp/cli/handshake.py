"""Local-model handshake — the red every-start notice (2026-11 wizard wave).

Contract (operator directive): EVERY CLI engine start — including the bare
desktop-shortcut launch (`NexusTraderBot` → EXE → `start --mode paper`) —
renders a RED handshake line stating that the active model was trained on the
user's own machine and may not have verified edge or win-rate, and that the
Official Models (benchmark · edge · winrate verified) unlock by starring the
repository. The FIRST start after setup renders the same message in the full
variant (steps + a one-time connect offer).

Design rules:
- ZERO network: star state comes from the platform-link CACHE only
  (``PlatformLinkService.status()``) — the handshake can never slow a boot.
- Disk-cheap provenance: install-state sidecar + starter-note override, never
  a SHA256 recompute on the hot path.
- ANTI-CRASH: every seam is failure-isolated; the handshake can never break
  or delay the engine start (same contract as windows_identity branding).
- Machine-clean: ``--json`` mode and non-TTY contexts render nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from rich.panel import Panel

from nexus_scalp.cli.styling import _banner, console
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.cli.handshake")

REPO = "Opselon/NexusTradingForexBot"
REPO_URL = "https://github.com/Opselon/NexusTradingForexBot"

#: origins that mean "this bundle did NOT come from the signed official chain"
_LOCAL_ORIGINS = {"USER_TRAINED", "GOVERNED", "DEV_STARTER", "UNKNOWN", ""}


def _tty_interactive() -> bool:
    """True when both console streams are a real interactive terminal.

    Injectable for tests (pytest replaces sys.stdout with a capture object
    whose isatty() is False — patching global sys.stdout fights the capture
    machinery, patching THIS is the clean seam).
    """
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception as exc:  # anti-crash: allow (probe must never break a start)
        logger.debug("[HANDSHAKE] tty probe failed", error_type=type(exc).__name__)
        return False


def _boot_handshake() -> None:
    """Bounded, throttled boot handshake (failure-isolated, never stalls)."""
    try:
        from nexus_scalp.platform_link import get_platform_link_service

        service = get_platform_link_service()
        if service.store.client_token() is None:
            return  # nothing to ask yet — the banner handles the unlinked case
        service.handshake(force=False, min_interval_secs=300.0, timeout=8.0)
    except Exception as exc:  # anti-crash: allow (boot must never wait on this)
        logger.debug("[HANDSHAKE] boot handshake skipped", error_type=type(exc).__name__)


def _serving_origin_fast() -> str:
    """Origin WITHOUT recomputing SHA256 (sidecar + starter-note override).

    Conservative: a missing/unparsable sidecar reports UNKNOWN (the handshake
    shows for UNKNOWN too — silence is never claimed for an unproven bundle).
    """
    from nexus_scalp.model_provisioning.service import (
        _looks_like_starter,
        read_install_state,
        serving_model_path,
    )

    try:
        model_path = serving_model_path()
        origin = str(read_install_state(model_path).get("origin", "") or "")
        if _looks_like_starter(model_path):
            origin = "DEV_STARTER"  # manifest note wins over any sidecar claim
        if not origin:
            origin = "UNKNOWN"  # unproven bundle never claims silence
        return origin
    except Exception as exc:  # anti-crash: allow (handshake must never break a start)
        logger.debug("[HANDSHAKE] origin probe failed", error_type=type(exc).__name__)
        return "UNKNOWN"


def _star_verified() -> bool:
    """Cache-only star verdict (never a network call on the start path)."""
    try:
        from nexus_scalp.platform_link import VerificationState, get_platform_link_service

        return get_platform_link_service().status().state is VerificationState.STARRED_VERIFIED
    except Exception as exc:  # anti-crash: allow (handshake must never break a start)
        logger.debug("[HANDSHAKE] star status probe failed", error_type=type(exc).__name__)
        return False


def _first_start_marker() -> Path:
    from nexus_scalp.release import paths as rpaths

    return Path(rpaths.get_runtime_workspace()) / ".handshake-full-v1"


def render_handshake(*, full: bool = False) -> Panel:
    """The RED handshake panel (compact every-start / full first-start)."""
    if full:
        body = "\n".join(
            [
                "[bold red]⚠ LOCAL MODEL — trained on YOUR machine[/]",
                "",
                "The active model was [bold]trained locally[/bold]. It may [bold]not have "
                "verified edge or win-rate[/bold] — profits are [bold]NOT guaranteed[/bold].",
                "",
                "[bold]⭐ Star the project to unlock the OFFICIAL MODELS[/] "
                "([bold]benchmark · edge · winrate[/bold] verified):",
                f"  [bold]1.[/] Open [link={REPO_URL}]{REPO_URL}[/link]",
                "  [bold]2.[/] Give the project a Star [bold yellow]⭐[/bold yellow]",
                "  [bold]3.[/] Verify — may take a few seconds",
                "",
                "  [cyan]nexus platform connect[/cyan]   ·   official download: "
                "[cyan]nexus model-official[/cyan]",
            ]
        )
        return Panel(body, title="Local Model Handshake", border_style="red")
    return Panel(
        "\n".join(
            [
                "[bold red]⚠ LOCAL MODEL[/bold red] — trained on YOUR machine; may "
                "[bold]not have verified edge or win-rate[/bold]. Profits are "
                "[bold]NOT guaranteed[/bold].",
                f"[bold]⭐ Star [link={REPO_URL}]{REPO}[/link] to unlock OFFICIAL MODELS "
                "(benchmark · edge · winrate) — "
                "[cyan]nexus platform connect[/cyan] · [cyan]nexus model-official[/cyan]",
            ]
        ),
        border_style="red",
    )


def maybe_show_handshake(*, json_mode: bool = False) -> None:
    """Render the handshake on engine start when the serving model is local.

    Called from ``nexus start`` (which covers the desktop-shortcut launch).
    Never raises, never touches the network, never renders in JSON mode.
    """
    if json_mode or not _tty_interactive():
        return
    try:
        from nexus_scalp.model_provisioning.service import ORIGIN_OFFICIAL

        # LIVE BOOT HANDSHAKE (Handshake.md §5: handshake at boot): ask the
        # server for the authoritative verdict — throttled to one call per
        # 5 minutes (the server cooldown collapses bursts) and bounded to a
        # short timeout so an unreachable platform can never stall a start.
        # On UNAVAILABLE the cached state is kept (never revoked by a network
        # error) and the banner renders from that.
        _boot_handshake()
        origin = _serving_origin_fast()
        verified = _star_verified()
        if origin == ORIGIN_OFFICIAL and verified:
            return  # official model + verified star: nothing to warn about
        full = not _first_start_marker().exists()
        console.print()
        console.print(render_handshake(full=full))
        console.print()
        if full:
            _offer_connect_once(verified, origin == ORIGIN_OFFICIAL)
            try:
                _first_start_marker().parent.mkdir(parents=True, exist_ok=True)
                _first_start_marker().write_text("seen\n", encoding="utf-8")
            except Exception as exc:  # anti-crash: allow (marker is best-effort UX)
                logger.debug("[HANDSHAKE] marker write failed", error_type=type(exc).__name__)
    except Exception as exc:  # anti-crash: allow (handshake must never break a start)
        logger.debug("[HANDSHAKE] render failed", error_type=type(exc).__name__)


def _offer_connect_once(verified: bool, official_bundle: bool) -> None:
    """First-start one-time offer: connect/verify right here (TTY only)."""
    if not _tty_interactive():
        return
    try:
        if verified:
            console.print(
                "[dim]Star verified — download the official model with 'nexus model-official'.[/dim]"
            )
            return
        from nexus_scalp.cli.platform_commands import _interactive, run_connect_flow
        from nexus_scalp.platform_link import get_platform_link_service

        svc = get_platform_link_service()
        status = svc.status()
        if status.state.connected:
            answer = input("Verify your star now (server check)? [Y/n]: ").strip().lower()
            if answer not in ("n", "no"):
                with console.status("Verifying your star… (may take a few seconds)"):
                    final = svc.reverify()
                from nexus_scalp.cli.platform_commands import render_status_panel

                console.print(render_status_panel(final, svc))
            return
        answer = (
            input("Continue with GitHub now to unlock the official model? [Y/n]: ").strip().lower()
        )
        if answer not in ("n", "no") and _interactive():
            run_connect_flow(svc)
        elif answer not in ("n", "no"):
            console.print(
                f"[dim]Open [link={REPO_URL}]{REPO_URL}[/link], star the project, then run 'nexus platform connect'.[/dim]"
            )
    except Exception as exc:  # anti-crash: allow (the offer must never break a start)
        logger.debug("[HANDSHAKE] connect offer skipped", error_type=type(exc).__name__)


def render_handshake_banner_pair(*, full: bool = False) -> list[Any]:
    """Banner + handshake as one render unit (for callers that own layout)."""
    return [_banner(), render_handshake(full=full)]


__all__ = [
    "REPO",
    "REPO_URL",
    "maybe_show_handshake",
    "render_handshake",
    "render_handshake_banner_pair",
]
