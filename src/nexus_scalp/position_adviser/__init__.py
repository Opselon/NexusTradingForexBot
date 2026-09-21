"""Layer-2 Position Decision Adviser (TASK-POSA-001).

Optional, opt-in advisory model for the keep/close decide system. OFF by
default; can only ever lower a hold score, never raise one, never open/size/
extend a position, and never weaken a protection verdict.

Public surface:
    PositionAdviserService   — the fail-closed runtime the decide system calls.
    train_position_adviser   — trains a Layer-2 adviser from a position dataset.
    PositionAdviserNet       — the decision head itself.
"""

from nexus_scalp.position_adviser.models import (  # noqa: F401 (public re-export)
    ADVISER_ACTIONS,
    AdviserActivation,
    PositionAdvisory,
)
from nexus_scalp.position_adviser.service import (
    AdviserConfig,
    AdviserState,
    PositionAdviserService,
)
from nexus_scalp.position_adviser.trainer import (
    AdviserScaler,
    AdviserTrainingResult,
    PositionAdviserNet,
    train_position_adviser,
)

__all__ = [
    "ADVISER_ACTIONS",
    "AdviserActivation",
    "AdviserConfig",
    "AdviserScaler",
    "AdviserState",
    "AdviserTrainingResult",
    "PositionAdviser",
    "PositionAdviserNet",
    "PositionAdviserService",
    "train_position_adviser",
]
