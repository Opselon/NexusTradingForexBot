"""Official-model invitation preference memory (lifecycle spec §6.5).

Modal fatigue is not a licensing strategy: the SAME invitation is never
re-shown after an explicit decision for the SAME release. Rules:

- ``decline``  -> remembered for that model release (re-offered only when a
  genuinely NEW official version is published, entitlement newly activates,
  or the user explicitly opens model selection).
- ``later``    -> bounded reminder cooldown (default 3 days), then re-offer.
- A user already running the newest official model is never re-prompted.
"""

from __future__ import annotations

import time
from typing import Any

DECISION_DECLINED = "declined"
DECISION_LATER = "later"

DEFAULT_LATER_COOLDOWN_SECS = 3 * 24 * 3600.0

_KEY_DECISION = "platform.model_invite.{version}.decision"
_KEY_LATER_UNTIL = "platform.model_invite.{version}.later_until"


class ModelInvitationPrefs:
    """Thin, testable preference store over the settings service."""

    def __init__(self, settings: Any, *, later_cooldown_secs: float = DEFAULT_LATER_COOLDOWN_SECS) -> None:
        self.settings = settings
        self.later_cooldown_secs = later_cooldown_secs

    # ------------------------------------------------------------------ reads
    def decision_for(self, version: str) -> str | None:
        row = self.settings.get(_KEY_DECISION.format(version=version))
        if row is None:
            return None
        value = str(row.value)
        return value if value in (DECISION_DECLINED, DECISION_LATER) else None

    def should_offer(
        self,
        version: str,
        *,
        installed_version: str | None = None,
        access_allowed: bool | None = None,
        now: float | None = None,
    ) -> bool:
        """Whether the invitation may be shown for ``version`` right now."""
        if not version:
            return False
        if installed_version is not None and str(installed_version) == str(version):
            return False  # already running the newest official model
        if access_allowed is False:
            # Nothing to adopt yet — the account/status panel handles it;
            # nagging the un-entitled is hostile UX.
            return False
        decision = self.decision_for(version)
        if decision is None:
            return True
        if decision == DECISION_DECLINED:
            return False
        # "later": re-offer after the cooldown expires.
        row = self.settings.get(_KEY_LATER_UNTIL.format(version=version))
        try:
            until = float(str(row.value)) if row is not None else 0.0
        except (TypeError, ValueError):
            until = 0.0
        return (now if now is not None else time.time()) >= until

    # ----------------------------------------------------------------- writes
    def record(self, version: str, decision: str, *, now: float | None = None) -> None:
        if decision not in (DECISION_DECLINED, DECISION_LATER):
            raise ValueError(f"unknown invitation decision: {decision!r}")
        self.settings.set(
            _KEY_DECISION.format(version=version), decision, value_type="string"
        )
        if decision == DECISION_LATER:
            until = (now if now is not None else time.time()) + self.later_cooldown_secs
            self.settings.set(
                _KEY_LATER_UNTIL.format(version=version), str(until), value_type="string"
            )

    def forget(self, version: str) -> None:
        """Explicit re-offer reset (user opened model selection)."""
        self.settings.set(_KEY_DECISION.format(version=version), "", value_type="string")
        self.settings.set(_KEY_LATER_UNTIL.format(version=version), "", value_type="string")
