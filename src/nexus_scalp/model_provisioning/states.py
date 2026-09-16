"""Provisioning lifecycle states (explicit, observable, never implied).

The states mirror the operator-directive vocabulary. They describe the
ACQUISITION side of the model pipeline and are deliberately separate from
the governance registry lifecycle (CANDIDATE/CHALLENGER/CHAMPION lives in
model_lifecycle — a provisioned bundle is still subject to it).
"""

from __future__ import annotations

from enum import StrEnum


class LifecycleState(StrEnum):
    """Acquisition state of the serving-slot model for one install."""

    MISSING = "missing"  # nothing in the serving slot
    DOWNLOADING = "downloading"  # PATH A fetch in flight
    VERIFYING = "verifying"  # signature / sha256 / schema / integrity chain
    TRAINING = "training"  # PATH B local walk-forward in flight
    VALIDATING = "validating"  # trained candidate under validation gates
    VALIDATION_FAILED = "validation_failed"  # honest red — never silently retried
    CANDIDATE = "candidate"  # locally valid bundle, NOT yet the governed champion
    VERIFIED = "verified"  # passed every installed-bundle gate
    READY = "ready"  # serving slot holds a verified bundle the engine can load
    REJECTED = "rejected"  # verification/integrity failed — install refused


#: States considered terminal-failure for UI reporting.
FAILED_STATES = frozenset({LifecycleState.VALIDATION_FAILED, LifecycleState.REJECTED})

#: States that mean "you can start the engine".
SERVABLE_STATES = frozenset({LifecycleState.VERIFIED, LifecycleState.READY})
