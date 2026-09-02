"""Canonical operator-facing state vocabulary (CHG-0043, TASK-RUNTIME-TRUTH).

ONE module owns the truthful-state words the runtime shows an operator.
Every consumer (release/health.py, cli/doctor.py, web endpoints, snapshot)
must express subsystem truth through this vocabulary instead of collapsing
distinct meanings into PASS/WARN/N-A:

    AVAILABLE        component exists and can be used
    ENABLED          configured on (intent) -- NOT proof of runtime effect
    ACTIVE           provably participating in the live path right now
    DISABLED         configured off (user choice, not a defect)
    NOT_CONFIGURED   required configuration absent (no intent recorded)
    NOT_INITIALIZED  component exists but first-use setup has not run yet
    NOT_APPLICABLE   component has no meaning for this install/mode
    DEGRADED         usable but below expected quality/capability
    UNKNOWN          truth cannot be determined (never fabricate a value)
    MISSING          required for core operation and absent
    NOT_RECORDED     identity/telemetry fact absent (commit, build stamp)
    ERROR            component broken/raised
    UNSUPPORTED      component has no implementation for this install/platform
    HEALTHY          check-level all-good (legacy PASS)
    INFO             informational note, not a defect

Decision rules encoded by the sets below:
    * CRITICAL_STATES block the READY aggregate.
    * NEUTRAL_STATES never downgrade the aggregate (a disabled optional
      subsystem is an operator choice, not a health problem).
    * DEGRADED_STATES downgrade the aggregate without blocking.
    * OK_STATES contribute positively.

FEATURE ENABLED != FEATURE ACTIVE. MODEL REGISTERED != MODEL SERVING.
DATABASE EXISTS != DATABASE CAPABILITY AVAILABLE.
"""

from __future__ import annotations

# --- Canonical vocabulary (string constants; keep stable, they cross the API) ---
AVAILABLE = "AVAILABLE"
ENABLED = "ENABLED"
ACTIVE = "ACTIVE"
DISABLED = "DISABLED"
NOT_CONFIGURED = "NOT_CONFIGURED"
NOT_INITIALIZED = "NOT_INITIALIZED"
NOT_APPLICABLE = "NOT_APPLICABLE"
DEGRADED = "DEGRADED"
UNKNOWN = "UNKNOWN"
MISSING = "MISSING"
NOT_RECORDED = "NOT_RECORDED"
ERROR = "ERROR"
UNSUPPORTED = "UNSUPPORTED"
HEALTHY = "HEALTHY"
INFO = "INFO"

CRITICAL_STATES = frozenset({ERROR, MISSING})
OK_STATES = frozenset({HEALTHY, AVAILABLE, ACTIVE, ENABLED, INFO})
NEUTRAL_STATES = frozenset({DISABLED, NOT_CONFIGURED, NOT_APPLICABLE, NOT_INITIALIZED, UNSUPPORTED})
DEGRADED_STATES = frozenset({DEGRADED, UNKNOWN})

ALL_STATES = frozenset(
    {
        AVAILABLE,
        ENABLED,
        ACTIVE,
        DISABLED,
        NOT_CONFIGURED,
        NOT_INITIALIZED,
        NOT_APPLICABLE,
        DEGRADED,
        UNKNOWN,
        MISSING,
        NOT_RECORDED,
        ERROR,
        UNSUPPORTED,
        HEALTHY,
        INFO,
    }
)


def normalize_verdict(legacy: str) -> str:
    """Map a legacy check verdict onto the canonical taxonomy (aggregate use).

    PASS -> HEALTHY, WARNING -> DEGRADED, FAIL -> ERROR; anything else is
    UNKNOWN (never invent a state from an unrecognized string).
    """
    mapping = {"PASS": HEALTHY, "WARNING": DEGRADED, "FAIL": ERROR}
    return mapping.get(str(legacy or "").upper(), UNKNOWN)


def aggregate_verdict(states: list[str]) -> str:
    """Derive the aggregate from canonical entry states.

    ERROR/MISSING anywhere -> ERROR; DEGRADED/UNKNOWN anywhere -> DEGRADED;
    otherwise HEALTHY. Neutral states (disabled/not-configured/optional)
    never degrade the aggregate — the aggregate reflects what the system
    can actually do, not the operator's configuration choices.
    """
    states = [s for s in states if s]
    if any(s in CRITICAL_STATES for s in states):
        return ERROR
    if any(s in DEGRADED_STATES for s in states):
        return DEGRADED
    return HEALTHY
