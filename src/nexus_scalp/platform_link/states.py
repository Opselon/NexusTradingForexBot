"""Platform link verification states.

The desktop app never decides star status by itself: it reflects the state
attested by the trusted platform (NSE-Platform), which performs the real
GitHub identity + star verification. Network failures must NEVER collapse
into ``NOT_STARRED`` — they surface as ``VERIFICATION_UNAVAILABLE``.
"""

from __future__ import annotations

from enum import StrEnum


class VerificationState(StrEnum):
    """Full state machine for the GitHub star verification link.

    Required states (product security spec):
    - ``NOT_CONNECTED``            no verified link exists
    - ``AUTHENTICATION_PENDING``   a connect session exists; the user has not
                                   approved it in the browser yet
    - ``VERIFICATION_PENDING``     linked, but the platform has no positive
                                   star verification on record yet
    - ``STARRED_VERIFIED``         platform attests star + granted entitlement
    - ``NOT_STARRED``              platform attests the account exists but the
                                   star is missing / was revoked
    - ``VERIFICATION_EXPIRED``     the client token is expired or was rejected
    - ``VERIFICATION_UNAVAILABLE`` platform / network unreachable — unknown,
                                   fail-closed for protected operations
    """

    NOT_CONNECTED = "NOT_CONNECTED"
    AUTHENTICATION_PENDING = "AUTHENTICATION_PENDING"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    STARRED_VERIFIED = "STARRED_VERIFIED"
    NOT_STARRED = "NOT_STARRED"
    VERIFICATION_EXPIRED = "VERIFICATION_EXPIRED"
    VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"

    @property
    def connected(self) -> bool:
        """True when a client token link exists (any linked state)."""
        return self in {
            VerificationState.VERIFICATION_PENDING,
            VerificationState.STARRED_VERIFIED,
            VerificationState.NOT_STARRED,
            VerificationState.VERIFICATION_EXPIRED,
        }

    @property
    def allows_official_download(self) -> bool:
        return self is VerificationState.STARRED_VERIFIED


# Platform (NSE-Platform) verification_state vocabulary -> local state.
_PLATFORM_STATE_MAP: dict[str, VerificationState] = {
    "VerifiedStarred": VerificationState.STARRED_VERIFIED,
    "NotStarred": VerificationState.NOT_STARRED,
    "VerificationPending": VerificationState.VERIFICATION_PENDING,
    "VerificationFailed": VerificationState.VERIFICATION_PENDING,
    "AuthorizationExpired": VerificationState.VERIFICATION_EXPIRED,
}


def state_from_platform_payload(
    verification_state: str | None, entitlement_state: str | None
) -> VerificationState:
    """Map a platform /api/client/me payload onto the local state machine.

    The entitlement is the authority for access: a revoked entitlement with a
    stale positive verification means the star is gone (``NOT_STARRED``).
    """
    ent = (entitlement_state or "").strip()
    ver = (verification_state or "").strip()
    mapped = _PLATFORM_STATE_MAP.get(ver, VerificationState.VERIFICATION_PENDING)
    if mapped is VerificationState.STARRED_VERIFIED and ent != "VerifiedStarred":
        # Verification says starred but entitlement is not granted (yet).
        return VerificationState.VERIFICATION_PENDING
    if mapped in (VerificationState.NOT_STARRED, VerificationState.VERIFICATION_EXPIRED):
        return mapped
    if ent == "NotStarred":
        return VerificationState.NOT_STARRED
    return mapped


#: Server lease verdicts (canonical startup response, `entitlement.decision`)
#: -> local state. Preferred over the legacy two-string mapping whenever the
#: server publishes the decision — it is computed LIVE, not read from a row.
_DECISION_STATE_MAP: dict[str, VerificationState] = {
    "GrantedFresh": VerificationState.STARRED_VERIFIED,
    "GrantedStale": VerificationState.STARRED_VERIFIED,
    "Revoked": VerificationState.NOT_STARRED,
    "Pending": VerificationState.VERIFICATION_PENDING,
    "LeaseExpired": VerificationState.VERIFICATION_EXPIRED,
}


def state_from_startup_payload(payload: dict) -> VerificationState:
    """Map the canonical startup payload onto the local state machine.

    Uses the live lease decision when the server provides one and falls back
    to the legacy verification/entitlement string mapping for older servers.
    """
    decision = str(((payload.get("entitlement") or {}).get("decision")) or "").strip()
    if decision in _DECISION_STATE_MAP:
        return _DECISION_STATE_MAP[decision]
    return state_from_platform_payload(
        payload.get("verification_state"), payload.get("entitlement_state")
    )
