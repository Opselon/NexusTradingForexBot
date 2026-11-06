"""Easy Setup lifecycle state machine (lifecycle spec §2/§2.1/§4).

The wizard can be cancelled at any point, the app can close mid-download,
and an upgrade reinstall must NEVER lose identity. This module is the
persistent memory of WHERE the user stopped and WHAT the correct next action
is — so no journey dead-ends and no user is forced through another OAuth
login when their token is already valid.

Local state is a RECOVERY + UX mechanism only. Authorization is always
re-proven against the platform (the startup handshake / live gate).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from nexus_scalp.platform_link.states import VerificationState

SETTING_SETUP_STAGE = "platform.setup_stage"
SETTING_SETUP_STAGE_AT = "platform.setup_stage_at"

SETTING_STAGE_PREFIX = "platform.setup_stage."


class SetupStage(StrEnum):
    """Persistent installation-lifecycle stages (spec §2)."""

    NOT_STARTED = "NOT_STARTED"
    INSTALLED = "INSTALLED"
    SETUP_IN_PROGRESS = "SETUP_IN_PROGRESS"
    AUTH_PENDING = "AUTH_PENDING"
    ENTITLEMENT_PENDING = "ENTITLEMENT_PENDING"
    MODEL_PENDING = "MODEL_PENDING"
    COMPLETED = "COMPLETED"
    USER_CANCELLED = "USER_CANCELLED"


@dataclass(frozen=True)
class RecoveryHint:
    """What the UI should show at next launch after an interrupted setup."""

    stage: SetupStage
    headline: str
    actions: tuple[str, ...]  # e.g. ("install_model",) / ("continue_setup",)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "headline": self.headline,
            "actions": list(self.actions),
        }


def load_stage(settings: Any) -> SetupStage:
    row = settings.get(SETTING_STAGE_PREFIX + "current") or settings.get(SETTING_SETUP_STAGE)
    if row is None or not str(row.value):
        return SetupStage.NOT_STARTED
    try:
        return SetupStage(str(row.value))
    except ValueError:
        return SetupStage.NOT_STARTED


def mark_stage(settings: Any, stage: SetupStage) -> None:
    settings.set(SETTING_STAGE_PREFIX + "current", stage.value, value_type="string")
    settings.set(SETTING_SETUP_STAGE_AT, str(time.time()), value_type="string")


def clear_stage(settings: Any) -> None:
    settings.set(SETTING_STAGE_PREFIX + "current", "", value_type="string")


def resume_hint(
    settings: Any,
    link_state: VerificationState,
    *,
    official_model_installed: bool | None = None,
) -> RecoveryHint | None:
    """The correct next action after ANY interruption (spec §2.1/§4).

    ``official_model_installed`` may be None when the caller cannot inspect
    the slot (e.g. CLI without the model modules); the hint degrades safely.
    """
    stage = load_stage(settings)

    if stage in (SetupStage.NOT_STARTED, SetupStage.INSTALLED, SetupStage.COMPLETED):
        return None

    token_connected = link_state.connected
    verified = link_state.allows_official_download

    # GitHub already connected but the model step never finished (spec example):
    # "Your GitHub account is already connected. Official model setup is not
    # finished." — offer continuation, NEVER another OAuth login.
    if stage in (
        SetupStage.AUTH_PENDING,
        SetupStage.ENTITLEMENT_PENDING,
        SetupStage.MODEL_PENDING,
        SetupStage.SETUP_IN_PROGRESS,
    ):
        if verified and official_model_installed is False:
            return RecoveryHint(
                stage=stage,
                headline=(
                    "Your GitHub account is already connected. "
                    "Official model setup is not finished."
                ),
                actions=("install_model",),
            )
        if verified:
            return RecoveryHint(
                stage=stage,
                headline="Setup is almost complete — finish the remaining steps.",
                actions=("continue_setup",),
            )
        if token_connected:
            return RecoveryHint(
                stage=stage,
                headline=(
                    "Your GitHub account is already connected. "
                    "Official model setup is not finished."
                ),
                actions=("continue_setup",),
            )
        # Cancelled before any credential existed: restartable, nothing to clean.
        return RecoveryHint(
            stage=stage,
            headline="Setup was not finished. You can start it again at any time.",
            actions=("continue_setup", "not_now"),
        )

    if stage is SetupStage.USER_CANCELLED:
        return RecoveryHint(
            stage=stage,
            headline="Setup was cancelled. Your progress was saved — continue anytime.",
            actions=("continue_setup", "not_now"),
        )

    return None
