"""Opt-in diagnostics consent.

The user is asked ONCE, explicitly, whether Warning/Error logs may be sent
to the Nexus server for end-to-end debugging. The decision is persisted; the
shipping pipeline is dead unless the answer was an explicit yes, and can be
revoked anytime via ``nexus diagnostics disable``.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any

from nexus_scalp.settings.service import SettingsService

SETTING_CONSENT = "diagnostics.ship_logs"  # "on" | "off" | "" (not asked)
SETTING_ASKED_AT = "diagnostics.asked_at"


def consent_state(settings: SettingsService | None = None) -> str:
    """Returns "on", "off", or "" (never asked)."""
    svc = settings or SettingsService()
    raw = svc.get(SETTING_CONSENT)
    value = str(raw.value) if raw is not None else ""
    return value if value in ("on", "off") else ""


def set_consent(enabled: bool, settings: SettingsService | None = None) -> None:
    svc = settings or SettingsService()
    svc.set(SETTING_CONSENT, "on" if enabled else "off", value_type="string")
    svc.set(SETTING_ASKED_AT, str(int(time.time())), value_type="string")


def mark_asked(settings: SettingsService | None = None) -> None:
    svc = settings or SettingsService()
    svc.set(SETTING_ASKED_AT, str(int(time.time())), value_type="string")


def is_enabled(settings: SettingsService | None = None) -> bool:
    return consent_state(settings) == "on"


def prompt_for_consent(
    *,
    ask: Callable[[str], bool] | None = None,
    settings: SettingsService | None = None,
    non_interactive: bool = False,
) -> bool:
    """Ask the user once for diagnostics consent. Returns current enabled state.

    ``ask`` is injectable for tests; default is a stdin prompt. Non-interactive
    contexts (piped stdio, --json, automation) never prompt and never change
    the stored decision.
    """
    current = consent_state(settings)
    if current:
        return current == "on"
    # An injected `ask` callable marks a scripted context: the TTY gate is the
    # caller's responsibility there. Otherwise only real terminals prompt.
    if non_interactive or (ask is None and not sys.stdin.isatty()):
        return False
    mark_asked(settings)

    def _default_ask(question: str) -> bool:
        try:
            answer = input(question).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")

    ask_fn = ask or _default_ask
    enabled = bool(
        ask_fn(
            "Help improve Nexus: send ONLY Warning and Error logs to the Nexus "
            "server for fixing? No trading data, no source code. [y/N]: "
        )
    )
    set_consent(enabled, settings)
    return enabled


def status_snapshot(settings: SettingsService | None = None) -> dict[str, Any]:
    svc = settings or SettingsService()
    asked = svc.get(SETTING_ASKED_AT)
    return {
        "consent": consent_state(svc) or "not_asked",
        "asked_at": str(asked.value) if asked is not None else None,
    }
