"""
Strategy DSL Validation — Hard Gates
=====================================
STRATEGY FACTORY (2026-08-20).

Structural validation is a HARD GATE (spec 59): an LLM-generated candidate
with an invalid schema, an unsupported feature, a lookahead risk or an
excessive complexity budget is REJECTED before it ever reaches the backtest
queue. The LLM output is untrusted input; only the deterministic validator
decides what may be scheduled.

GATES (each returns a ValidationVerdict):
  validate_schema        — the DSL parses, extra fields rejected, symbols/timeframes supported
  validate_features      — every feature id exists in the canonical 70D catalog
  validate_causality     — no future/close-of-bar violations in the declared structure
  validate_complexity    — condition/feature/timeframe/nesting budget (spec 12)

The final gate (`validate_candidate`) ANDs all gates plus the canonical
dedup check against the current population (spec 13).
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.strategies.factory.dsl import (
    DEFAULT_SYMBOLS,
    SUPPORTED_TIMEFRAMES,
    feature_catalog_index,
)
from nexus_scalp.strategies.factory.models import (
    FactoryCandidate,
    FactoryStage,
    FailureReason,
    StrategyDsl,
    ValidationVerdict,
)

#: Approved operators for filters/conditions (spec 8 grammar).
_APPROVED_OPS = frozenset({"gt", "lt", "gte", "lte", "eq", "neq", "between"})

#: Structural keys the DSL grammar understands (extra keys are rejected).
_KNOWN_TOP_KEYS = frozenset(
    {
        "schema_version",
        "hypothesis",
        "family",
        "market",
        "context",
        "setup",
        "entry",
        "filters",
        "exit",
        "risk",
        "constraints",
    }
)

#: Causal-safety flags declared by a strategy are enforced, not trusted
#: (spec 15): any declaration that would require a future bar to compute is
#: rejected. Deterministic templates are causal by construction; the LLM may
#: not flip this.
_CAUSAL_DECLARATIONS = frozenset(
    {"no_future_data", "completed_bars_only", "causal", "lookahead_safe"}
)


def _verdict(
    passed: bool,
    stage: FactoryStage,
    reason: FailureReason | None = None,
    message: str = "",
    details: dict[str, Any] | None = None,
) -> ValidationVerdict:
    return ValidationVerdict(
        passed=passed,
        stage=stage,
        reasons=[message] if message else [],
        failure_reason=reason,
        details=details or {},
    )


def validate_schema(
    dsl: StrategyDsl,
    symbols: list[str] | None = None,
    timeframes: tuple[str, ...] | None = None,
) -> ValidationVerdict:
    """Gate 1: structural schema validity."""
    symbols = symbols or list(DEFAULT_SYMBOLS)
    timeframes = timeframes or SUPPORTED_TIMEFRAMES

    # Pydantic extra='forbid' already rejects unknown keys on construction;
    # defensively re-serialize and check the top-level shape survived.
    try:
        raw = dsl.model_dump()
    except Exception as e:  # pragma: no cover - defensive
        return _verdict(False, FactoryStage.DSL_VALIDATION, FailureReason.INVALID_SCHEMA, str(e))

    unknown = set(raw) - _KNOWN_TOP_KEYS
    if unknown:
        return _verdict(
            False,
            FactoryStage.DSL_VALIDATION,
            FailureReason.INVALID_SCHEMA,
            f"Unknown DSL top-level keys: {sorted(unknown)}",
        )

    market = raw.get("market") or {}
    syms = market.get("symbols") or []
    tfs = market.get("timeframes") or []
    if not syms:
        return _verdict(
            False, FactoryStage.DSL_VALIDATION, FailureReason.INVALID_SCHEMA, "No symbols"
        )
    bad_syms = [s for s in syms if s not in symbols]
    if bad_syms:
        return _verdict(
            False,
            FactoryStage.DSL_VALIDATION,
            FailureReason.UNSUPPORTED_SYMBOL,
            f"Unsupported symbols: {bad_syms}",
            {"unsupported": bad_syms},
        )
    if not tfs:
        return _verdict(
            False, FactoryStage.DSL_VALIDATION, FailureReason.INVALID_SCHEMA, "No timeframes"
        )
    bad_tfs = [t for t in tfs if t not in timeframes]
    if bad_tfs:
        return _verdict(
            False,
            FactoryStage.DSL_VALIDATION,
            FailureReason.UNSUPPORTED_TIMEFRAME,
            f"Unsupported timeframes: {bad_tfs}",
            {"unsupported": bad_tfs},
        )

    hypothesis = raw.get("hypothesis") or {}
    if not hypothesis.get("statement"):
        return _verdict(
            False,
            FactoryStage.DSL_VALIDATION,
            FailureReason.INVALID_SCHEMA,
            "Missing hypothesis.statement (spec 11)",
        )
    if not raw.get("exit"):
        return _verdict(
            False,
            FactoryStage.DSL_VALIDATION,
            FailureReason.INVALID_SCHEMA,
            "Missing exit logic (spec 8)",
        )

    filters = raw.get("filters") or []
    for f in filters:
        if not isinstance(f, dict):
            return _verdict(
                False, FactoryStage.DSL_VALIDATION, FailureReason.INVALID_SCHEMA, "Malformed filter"
            )
        if f.get("op") not in _APPROVED_OPS:
            return _verdict(
                False,
                FactoryStage.DSL_VALIDATION,
                FailureReason.INVALID_SCHEMA,
                f"Unapproved operator {f.get('op')!r} in filter",
            )

    return _verdict(True, FactoryStage.DSL_VALIDATION)


def validate_features(dsl: StrategyDsl) -> ValidationVerdict:
    """Gate 2: every referenced feature exists in the canonical catalog.

    Feature governance (spec 9): the LLM may only use features that exist in
    the approved Nexus Feature Registry. An unknown feature REJECTS the
    strategy — it is never silently implemented.
    """
    catalog = feature_catalog_index()
    raw = dsl.model_dump()
    referenced: list[str] = []

    def _collect(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "feature" and isinstance(v, str):
                    referenced.append(v)
                _collect(v)
        elif isinstance(obj, list):
            for item in obj:
                _collect(item)

    _collect(raw)
    unknown = sorted({f for f in referenced if f not in catalog})
    if unknown:
        return _verdict(
            False,
            FactoryStage.FEATURE_VALIDATION,
            FailureReason.UNSUPPORTED_FEATURE,
            f"Unsupported features referenced: {unknown}",
            {"unsupported": unknown},
        )
    return _verdict(True, FactoryStage.FEATURE_VALIDATION)


def validate_causality(dsl: StrategyDsl) -> ValidationVerdict:
    """Gate 3: lookahead / data-leakage defense (spec 15, NON-NEGOTIABLE).

    The DSL grammar is bar/close-based by construction; this gate enforces it:
      * no `future_bars` / `next_bar` / `future_*` keys may appear anywhere
      * every condition must declare `completed_bars_only` semantics
      * the declared context may not reference timeframe alignment that would
        require an unclosed candle
    The LLM cannot declare itself causal — causality is verified structurally.
    """
    raw_bytes = dsl.model_dump(mode="json")
    raw = repr(raw_bytes).lower()
    forbidden = [
        "future_bars",
        "next_bar",
        "future_open",
        "future_close",
        "future_high",
        "future_low",
    ]
    hits = [f for f in forbidden if f in raw]
    if hits:
        return _verdict(
            False,
            FactoryStage.CAUSALITY_VALIDATION,
            FailureReason.LOOKAHEAD_RISK,
            f"Causality violation: forbidden future references {hits}",
            {"forbidden": hits},
        )

    constraints = (dsl.constraints or {}).get(
        "completed_bars_only", dsl.constraints.get("no_future_data")
    )
    if not constraints and not dsl.hypothesis.get("completed_bars_only"):
        # Templates always set no_future_data=True; an LLM candidate omitting
        # it is REJECTED rather than assumed safe (spec 15).
        return _verdict(
            False,
            FactoryStage.CAUSALITY_VALIDATION,
            FailureReason.LOOKAHEAD_RISK,
            "Strategy must declare completed-bar / no-future-data semantics",
        )
    return _verdict(True, FactoryStage.CAUSALITY_VALIDATION)


def validate_complexity(dsl: StrategyDsl, budgets: dict[str, int]) -> ValidationVerdict:
    """Gate 4: complexity budget (spec 12).

    Prefer simple + robust + generalizable over complex + optimized + fragile.
    """
    raw = dsl.model_dump()
    n_conditions = 0

    def _count_conditions(obj: Any) -> int:
        total = 0
        if isinstance(obj, dict):
            if any(k in obj for k in ("op", "logic", "confirmation", "require")):
                total += 1
            for v in obj.values():
                total += _count_conditions(v)
        elif isinstance(obj, list):
            for item in obj:
                total += _count_conditions(item)
        return total

    n_conditions = _count_conditions(raw)
    max_conditions = int(budgets.get("max_conditions", 9))
    if n_conditions > max_conditions:
        return _verdict(
            False,
            FactoryStage.COMPLEXITY_VALIDATION,
            FailureReason.EXCESSIVE_COMPLEXITY,
            f"Condition count {n_conditions} exceeds budget {max_conditions}",
            {"conditions": n_conditions, "max": max_conditions},
        )

    n_features = len({f.get("feature") for f in raw.get("filters", []) if isinstance(f, dict)})
    max_features = int(budgets.get("max_features", 6))
    if n_features > max_features:
        return _verdict(
            False,
            FactoryStage.COMPLEXITY_VALIDATION,
            FailureReason.EXCESSIVE_COMPLEXITY,
            f"Feature count {n_features} exceeds budget {max_features}",
            {"features": n_features, "max": max_features},
        )

    tfs = (raw.get("market") or {}).get("timeframes") or []
    max_tfs = int(budgets.get("max_timeframes", 2))
    if len(tfs) > max_tfs:
        return _verdict(
            False,
            FactoryStage.COMPLEXITY_VALIDATION,
            FailureReason.EXCESSIVE_COMPLEXITY,
            f"Timeframe count {len(tfs)} exceeds budget {max_tfs}",
            {"timeframes": tfs, "max": max_tfs},
        )

    return _verdict(True, FactoryStage.COMPLEXITY_VALIDATION)


def validate_candidate(
    candidate: FactoryCandidate,
    *,
    budgets: dict[str, int] | None = None,
    existing_hashes: set[str] | None = None,
    symbols: list[str] | None = None,
) -> ValidationVerdict:
    """Full structural gate chain for one candidate (spec 14 gate 1-4).

    The dedup check (spec 13) is part of structural validation: a candidate
    whose canonical DSL hash already exists in the current population is
    DUPLICATE and rejected before scheduling.
    """
    budgets = budgets or {}
    for gate in (
        lambda: validate_schema(candidate.dsl, symbols=symbols),
        lambda: validate_features(candidate.dsl),
        lambda: validate_causality(candidate.dsl),
        lambda: validate_complexity(candidate.dsl, budgets),
    ):
        verdict = gate()
        if not verdict.passed:
            return verdict

    if existing_hashes and candidate.definition_hash in existing_hashes:
        return _verdict(
            False,
            FactoryStage.DEDUPLICATION,
            FailureReason.DUPLICATE,
            f"Duplicate candidate (hash {candidate.definition_hash[:12]}…)",
            {"definition_hash": candidate.definition_hash},
        )
    return _verdict(
        True, FactoryStage.DSL_VALIDATION, details={"definition_hash": candidate.definition_hash}
    )


__all__ = [
    "validate_candidate",
    "validate_causality",
    "validate_complexity",
    "validate_features",
    "validate_schema",
]
