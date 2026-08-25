"""PHASE 26: strategy-aware validation context contracts.

Reconstructed 2026-08-25 by Nexus-Coder after an accidental working-tree
loss during parallel-agent commit absorption. Interface matches the call
sites in research.oos / research.walkforward:

    filtered, diag = filter_samples_by_contract(samples, contract)
    active = has_active_contract(contract)

A contract scopes the validation population to the strategy's DECLARED
market conditions (session / trend / volatility). Gate thresholds are
never touched here - only which samples are eligible for evaluation.
"""

from __future__ import annotations

from typing import Any

#: Contract keys that activate sample filtering when present and non-empty.
_ACTIVE_KEYS = ("sessions", "trend_states", "volatility_regimes")


def has_active_contract(contract: dict[str, Any] | None) -> bool:
    """True when the contract actually constrains the evaluation population.

    A missing/empty contract, or one whose scoping lists are all empty,
    leaves the population untouched.
    """
    if not isinstance(contract, dict):
        return False
    return any(contract.get(k) for k in _ACTIVE_KEYS)


def filter_samples_by_contract(
    samples: list[Any], contract: dict[str, Any]
) -> tuple[list[Any], dict[str, Any]]:
    """Return (matching_samples, diagnostics) for the given context contract.

    Matching rules per sample attribute:
      * session        -> membership in contract["sessions"]
      * trend_state    -> membership in contract["trend_states"]
      * volatility_regime -> membership in contract["volatility_regimes"]

    Empty/missing scope lists are wildcards. Diagnostics always carry the
    original/matched counts so callers can record honest evidence; when no
    sample matches, ``sufficient_evidence`` is set False so upstream gates
    can abstain instead of validating on an empty population.
    """
    sessions = {str(s).upper() for s in (contract.get("sessions") or [])}
    trends = {str(t).upper() for t in (contract.get("trend_states") or [])}
    vols = {str(v).upper() for v in (contract.get("volatility_regimes") or [])}

    matched: list[Any] = []
    for s in samples:
        if sessions and str(getattr(s, "session", "")).upper() not in sessions:
            continue
        if trends and str(getattr(s, "trend_state", "")).upper() not in trends:
            continue
        if vols and str(getattr(s, "volatility_regime", "")).upper() not in vols:
            continue
        matched.append(s)

    diag: dict[str, Any] = {
        "total_samples": len(samples),
        "matched_samples": len(matched),
        "sufficient_evidence": bool(matched),
    }
    if matched:
        wins = sum(1 for m in matched if float(getattr(m, "realized_r", 0.0)) > 0)
        diag["matched_win_rate"] = round(wins / len(matched), 6)
    return matched, diag
