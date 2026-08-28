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

import hashlib
import json
from typing import Any

#: Contract keys that activate sample filtering when present and non-empty.
_ACTIVE_KEYS = ("sessions", "trend_states", "volatility_regimes")

#: Canonical session token mapping from DSL hypothesis text / context keys.
_SESSION_TOKENS: dict[str, str] = {
    "ASIAN": "ASIAN",
    "LONDON": "LONDON",
    "NY": "NEW_YORK",
    "NEW_YORK": "NEW_YORK",
    "LONDON_NY_OVERLAP": "LONDON_NY_OVERLAP",
    "OVERLAP": "LONDON_NY_OVERLAP",
}

_VOLATILITY_TOKENS: dict[str, str] = {
    "VOLATILITY_EXPANSION": "EXPANSION",
    "EXPANSION": "EXPANSION",
    "VOLATILITY_CONTRACTION": "CONTRACTION",
    "CONTRACTION": "CONTRACTION",
}


def extract_context_contract(
    dsl_context: dict[str, Any] | None, hypothesis: dict[str, Any] | None
) -> dict[str, list[str]]:
    """Derives the CANONICAL evaluation contract from a strategy's DSL.

    Reads only explicit declarations; absent dimensions resolve to empty
    lists (= wildcards). The returned dict is the single canonical shape:
    {"sessions": [...], "trend_states": [...], "volatility_regimes": [...]}.
    """
    ctx = dict(dsl_context or {})
    hyp = dict(hypothesis or {})

    sessions: set[str] = set()
    sf = ctx.get("session_filter") or ctx.get("session") or ctx.get("sessions")
    if isinstance(sf, str) and sf.strip():
        tok = _SESSION_TOKENS.get(sf.strip().upper())
        if tok:
            sessions.add(tok)
    elif isinstance(sf, dict):
        named = sf.get("name") or sf.get("session") or ""
        tok = _SESSION_TOKENS.get(str(named).strip().upper())
        if tok:
            sessions.add(tok)
    elif isinstance(sf, (list, tuple)):
        for item in sf:
            tok = _SESSION_TOKENS.get(str(item).strip().upper())
            if tok:
                sessions.add(tok)

    trend_states: set[str] = set()
    rq = ctx.get("regime") or ctx.get("market_regime")
    if isinstance(rq, dict):
        want = str(rq.get("require") or rq.get("name") or "").strip().upper()
        if want and want != "ALL":
            trend_states.add(want)
    elif isinstance(rq, str) and rq.strip() and rq.strip().upper() not in ("ALL",):
        trend_states.add(rq.strip().upper())

    volatility_regimes: set[str] = set()
    vf = ctx.get("volatility_filter") or ctx.get("volatility_regime")
    if isinstance(vf, dict):
        want = str(vf.get("require") or vf.get("name") or "").strip().upper()
        if want and want in _VOLATILITY_TOKENS:
            volatility_regimes.add(_VOLATILITY_TOKENS[want])
    elif isinstance(vf, str) and vf.strip():
        tok = _VOLATILITY_TOKENS.get(vf.strip().upper())
        if tok:
            volatility_regimes.add(tok)

    # Hypothesis market_condition string may carry tokens ("LONDON | TRENDING |
    # VOLATILITY_EXPANSION"). Only fills dimensions NOT already explicitly set.
    mc = str(hyp.get("market_condition", "") or "").upper()
    if mc:
        if not sessions:
            for token, canonical in _SESSION_TOKENS.items():
                if token in mc:
                    sessions.add(canonical)
                    break
        if not trend_states:
            for t in ("TRENDING", "RANGING", "TREND"):
                if t in mc:
                    trend_states.add("TRENDING" if t != "RANGING" else "RANGING")
                    break
        if not volatility_regimes:
            for token, canonical in _VOLATILITY_TOKENS.items():
                if token in mc:
                    volatility_regimes.add(canonical)
                    break

    return {
        "sessions": sorted(sessions),
        "trend_states": sorted(trend_states),
        "volatility_regimes": sorted(volatility_regimes),
    }


def contract_hash(contract: dict[str, Any] | None) -> str:
    """Deterministic SHA-256 fingerprint (16 hex) of a canonical contract."""
    payload = json.dumps(contract or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def has_active_contract(contract: dict[str, Any] | None) -> bool:
    """True when the contract actually constrains the evaluation population.

    A missing/empty contract, or one whose scoping lists are all empty,
    leaves the population untouched.
    """
    if not isinstance(contract, dict):
        return False
    return any(contract.get(k) for k in _ACTIVE_KEYS)


class ContextContractError(ValueError):
    """Raised when a strategy's context contract is missing/invalid/inconsistent.

    PHASE 27 fail-loud discipline: an evaluator must NEVER silently fall back
    to global evaluation when a strategy declared a hypothesis context —
    that silent fallback is exactly how false validations happened before.
    """


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
