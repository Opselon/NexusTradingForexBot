"""Layer-2 Position Decision Adviser (TASK-POSA-001).

Optional, opt-in advisory model for the keep/close decide system. OFF by
default; can only ever lower a hold score, never raise one, never open/size/
extend a position, and never weaken a protection verdict.

Public surface:
    PositionAdviserService   — the fail-closed runtime the decide system calls.
    train_position_adviser   — trains a Layer-2 adviser from a position dataset.
    PositionAdviserNet       — the decision head itself.

The re-exports are LAZY (PEP 562 ``__getattr__``): this package's
``__init__`` runs on every ``from nexus_scalp.position_adviser.paths
import ...`` that create_app()'s provisioning routes performs, and the
previous eager imports pulled ``service``/``trainer`` — and therefore
torch — into every web-serving worker at boot. Importing a submodule
directly (``nexus_scalp.position_adviser.service``) is unaffected.
"""

from typing import Any

_EXPORTS: dict[str, str] = {
    "ADVISER_ACTIONS": "nexus_scalp.position_adviser.models",
    "AdviserActivation": "nexus_scalp.position_adviser.models",
    "PositionAdvisory": "nexus_scalp.position_adviser.models",
    "AdviserConfig": "nexus_scalp.position_adviser.service",
    "AdviserState": "nexus_scalp.position_adviser.service",
    "PositionAdviserService": "nexus_scalp.position_adviser.service",
    "AdviserScaler": "nexus_scalp.position_adviser.trainer",
    "AdviserTrainingResult": "nexus_scalp.position_adviser.trainer",
    "PositionAdviserNet": "nexus_scalp.position_adviser.trainer",
    "train_position_adviser": "nexus_scalp.position_adviser.trainer",
}

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


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module), name)
    globals()[name] = value  # cache: one import per name per process
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
