"""
Evolution Engine — Mutation, Crossover, Exploration
====================================================
STRATEGY FACTORY (2026-08-20).

Evolutionary operators constrained by semantic validity (spec 7):
  * MUTATION   — bounded single-axis change to an existing strategy while
                 preserving DSL validity (add/remove filter, replace
                 indicator, change threshold, change condition).
  * CROSSOVER  — combine compatible parts of two validated strategies;
                 rejects semantic contradictions and excessive complexity.
  * EXPLORATION — controlled random exploration reserved for genuinely new
                 strategy families (prevents local-optimum convergence).

Every operator re-validates its output through the structural gates before
the candidate is accepted; a failed operator produces nothing (it never
silently degrades into an invalid strategy).

Operator success metrics (generated/survived/elite) are tracked by the
orchestrator; the engine exposes `adapt_probabilities` for adaptive evolution
(spec 98 / 99 / 100).
"""

from __future__ import annotations

import random
from typing import Any

from nexus_scalp.strategies.factory.dsl import (
    RANDOM_SEED,
    SUPPORTED_TIMEFRAMES,
    candidate_id_from_hash,
    dsl_hash,
    feature_ids,
)
from nexus_scalp.strategies.factory.models import (
    CandidateSource,
    EvolutionOperator,
    FactoryCandidate,
    StrategyDsl,
    StrategyFamily,
)
from nexus_scalp.strategies.factory.validators import (
    validate_complexity,
    validate_features,
    validate_schema,
)

#: Mutation action pool (spec 7). Each maps a DSL raw dict -> mutated raw dict.
_MUTATION_ACTIONS = (
    "add_filter",
    "remove_filter",
    "replace_indicator",
    "change_threshold",
    "change_timeframe",
    "change_condition",
    "simplify",
)


def _raw(dsl: StrategyDsl) -> dict[str, Any]:
    return dsl.model_dump()


def _rebuild(raw: dict[str, Any]) -> StrategyDsl:
    return StrategyDsl(**raw)


def _mutate_add_filter(
    raw: dict[str, Any], rng: random.Random, feature_pool: list[str]
) -> dict[str, Any]:
    filters = list(raw.get("filters", []))
    existing = {f.get("feature") for f in filters if isinstance(f, dict)}
    candidates = [f for f in feature_pool if f not in existing]
    if not candidates:
        return raw
    chosen = rng.choice(candidates)
    filters.append(
        {
            "feature": chosen,
            "op": rng.choice(["gt", "lt"]),
            "value": round(rng.uniform(-0.3, 0.3), 2),
        }
    )
    raw["filters"] = filters
    return raw


def _mutate_remove_filter(raw: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    filters = list(raw.get("filters", []))
    if len(filters) <= 1:
        return raw
    del filters[rng.randrange(len(filters))]
    raw["filters"] = filters
    return raw


def _mutate_replace_indicator(
    raw: dict[str, Any], rng: random.Random, feature_pool: list[str]
) -> dict[str, Any]:
    filters = list(raw.get("filters", []))
    if not filters:
        return raw
    idx = rng.randrange(len(filters))
    new_feature = rng.choice(feature_pool)
    filters[idx] = {**filters[idx], "feature": new_feature}
    raw["filters"] = filters
    return raw


def _mutate_change_threshold(raw: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    filters = list(raw.get("filters", []))
    mutable = [
        i
        for i, f in enumerate(filters)
        if isinstance(f, dict) and isinstance(f.get("value"), (int, float))
    ]
    if not mutable:
        return raw
    idx = rng.choice(mutable)
    delta = round(rng.uniform(-0.15, 0.15), 2)
    filters[idx] = {**filters[idx], "value": round(float(filters[idx]["value"]) + delta, 2)}
    raw["filters"] = filters
    return raw


def _mutate_change_timeframe(raw: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    tfs = list((raw.get("market") or {}).get("timeframes") or [])
    if not tfs:
        return raw
    idx = rng.randrange(len(tfs))
    alternatives = [t for t in SUPPORTED_TIMEFRAMES if t != tfs[idx]]
    tfs[idx] = rng.choice(alternatives)
    raw.setdefault("market", {})["timeframes"] = tfs
    return raw


def _mutate_change_condition(
    raw: dict[str, Any], rng: random.Random, feature_pool: list[str]
) -> dict[str, Any]:
    entry = dict(raw.get("entry") or {})
    confirmations = list(entry.get("confirmation") or [])
    if not confirmations:
        confirmations = [rng.choice(feature_pool)]
    else:
        idx = rng.randrange(len(confirmations))
        confirmations[idx] = rng.choice(feature_pool)
    entry["confirmation"] = confirmations
    raw["entry"] = entry
    return raw


def _mutate_simplify(raw: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Simplify: drop the last filter + trim confirmation list (spec 7)."""
    filters = list(raw.get("filters", []))
    if len(filters) > 1:
        filters.pop()
        raw["filters"] = filters
    entry = dict(raw.get("entry") or {})
    confirmations = list(entry.get("confirmation") or [])
    if len(confirmations) > 1:
        entry["confirmation"] = confirmations[:-1]
        raw["entry"] = entry
    return raw


def mutate(
    candidate: FactoryCandidate,
    rng: random.Random | None = None,
    feature_pool: list[str] | None = None,
    action: str | None = None,
    budgets: dict[str, int] | None = None,
) -> FactoryCandidate | None:
    """Mutates a candidate under the semantic validity constraint.

    Returns None when the mutation would violate a structural gate (the
    operator fails cleanly — it never produces an invalid strategy).
    """
    rng = rng or random.Random(RANDOM_SEED)
    pool = feature_pool or feature_ids()
    raw = _raw(candidate.dsl)
    action = action or rng.choice(_MUTATION_ACTIONS)

    if action == "add_filter":
        raw = _mutate_add_filter(raw, rng, pool)
    elif action == "remove_filter":
        raw = _mutate_remove_filter(raw, rng)
    elif action == "replace_indicator":
        raw = _mutate_replace_indicator(raw, rng, pool)
    elif action == "change_threshold":
        raw = _mutate_change_threshold(raw, rng)
    elif action == "change_timeframe":
        raw = _mutate_change_timeframe(raw, rng)
    elif action == "change_condition":
        raw = _mutate_change_condition(raw, rng, pool)
    elif action == "simplify":
        raw = _mutate_simplify(raw, rng)
    else:
        return None

    try:
        dsl = _rebuild(raw)
    except Exception:
        return None

    # A mutation that produces no definition change is a NO-OP: reject it so
    # the population never carries silent duplicates (spec 7 / 13).
    if dsl_hash(dsl) == candidate.definition_hash:
        return None

    # Re-validate the mutated DSL structurally before accepting.
    checks = [
        validate_schema(dsl),
        validate_features(dsl),
        validate_complexity(dsl, budgets or {}),
    ]
    if any(not c.passed for c in checks):
        return None

    digest = dsl_hash(dsl)
    return FactoryCandidate(
        candidate_id=candidate_id_from_hash(digest),
        definition_hash=digest,
        generation_id=candidate.generation_id,
        source=CandidateSource.MUTATION,
        operator=EvolutionOperator.MUTATION,
        parent_ids=[candidate.candidate_id, candidate.definition_hash[:12]],
        dsl=dsl,
        family=dsl.family,
        population_index=candidate.population_index,
    )


def _compatible(a: StrategyDsl, b: StrategyDsl) -> bool:
    """Crossover compatibility: same symbol universe and shared market context.

    Crossovers across incompatible symbols / wildly different timeframes are
    semantic contradictions and are rejected (spec 7).
    """
    sa = set((a.market or {}).get("symbols") or [])
    sb = set((b.market or {}).get("symbols") or [])
    if sa and sb and not (sa & sb):
        return False
    return True


def _merge_confirmation(a: list[str], b: list[str]) -> list[str]:
    """Merges entry confirmations preserving order, capped at 4 clauses."""
    out: list[str] = []
    for item in [*a, *b]:
        if item not in out:
            out.append(item)
        if len(out) >= 4:
            break
    return out


def crossover(
    parent_a: FactoryCandidate,
    parent_b: FactoryCandidate,
    rng: random.Random | None = None,
    budgets: dict[str, int] | None = None,
) -> FactoryCandidate | None:
    """Combines compatible parts of two validated candidates.

    Child = context/setup of A + entry of B merged with A + unified filters +
    exit of B. Rejected when the parents are incompatible or the child exceeds
    the complexity budget.
    """
    rng = rng or random.Random(RANDOM_SEED + 11)
    if not _compatible(parent_a.dsl, parent_b.dsl):
        return None

    a_raw = _raw(parent_a.dsl)
    b_raw = _raw(parent_b.dsl)

    child_raw: dict[str, Any] = {}
    child_raw["schema_version"] = a_raw.get("schema_version", "1.0")
    # Hypothesis: keep parent A's statement, annotate crossover origin.
    hyp = dict(a_raw.get("hypothesis") or {})
    hyp["statement"] = f"{hyp.get('statement', '')} [crossover with {parent_b.candidate_id}]"
    child_raw["hypothesis"] = hyp
    child_raw["family"] = StrategyFamily.HYBRID.value
    child_raw["market"] = dict(a_raw.get("market") or {})

    context = dict(a_raw.get("context") or {})
    child_raw["context"] = context

    entry_a = a_raw.get("entry") or {}
    entry_b = b_raw.get("entry") or {}
    child_raw["setup"] = dict(a_raw.get("setup") or {})

    merged_conf = _merge_confirmation(
        list(entry_a.get("confirmation") or []),
        list(entry_b.get("confirmation") or []),
    )
    child_raw["entry"] = {
        "logic": f"{entry_b.get('logic', 'combined')}_plus_{entry_a.get('logic', 'context')}",
        "confirmation": merged_conf,
    }

    # Unified filters: A's filters, then B's filters not already present (cap 4).
    filters: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in [*(a_raw.get("filters") or []), *(b_raw.get("filters") or [])]:
        if not isinstance(f, dict):
            continue
        feat = f.get("feature")
        if feat in seen:
            continue
        seen.add(feat)
        filters.append(dict(f))
        if len(filters) >= 4:
            break
    child_raw["filters"] = filters

    child_raw["exit"] = dict(
        b_raw.get("exit") or a_raw.get("exit") or {"mode": "fixed_rr", "rr": 2.0}
    )
    child_raw["risk"] = dict(a_raw.get("risk") or {})
    child_raw["constraints"] = {
        **(a_raw.get("constraints") or {}),
        "crossover": True,
        "max_conditions": min(
            int((a_raw.get("constraints") or {}).get("max_conditions", 9)),
            int((b_raw.get("constraints") or {}).get("max_conditions", 9)),
        ),
    }

    try:
        dsl = _rebuild(child_raw)
    except Exception:
        return None

    checks = [
        validate_schema(dsl),
        validate_features(dsl),
        validate_complexity(dsl, budgets or {}),
    ]
    if any(not c.passed for c in checks):
        return None

    digest = dsl_hash(dsl)
    return FactoryCandidate(
        candidate_id=candidate_id_from_hash(digest),
        definition_hash=digest,
        generation_id=parent_a.generation_id,
        source=CandidateSource.CROSSOVER,
        operator=EvolutionOperator.CROSSOVER,
        parent_ids=[
            parent_a.candidate_id,
            parent_a.definition_hash[:12],
            parent_b.candidate_id,
            parent_b.definition_hash[:12],
        ],
        dsl=dsl,
        family=StrategyFamily.HYBRID,
        population_index=parent_a.population_index,
    )


def explore(
    base: FactoryCandidate,
    rng: random.Random | None = None,
    feature_pool: list[str] | None = None,
    budgets: dict[str, int] | None = None,
) -> FactoryCandidate | None:
    """Controlled exploration: a genuinely new family direction derived from a
    base candidate (spec 7 Exploration). Uses a fresh family template and
    randomizes the feature wiring — semantic validity is re-checked."""
    rng = rng or random.Random(RANDOM_SEED + 23)
    pool = feature_pool or feature_ids()
    from nexus_scalp.strategies.factory.dsl import _template_dsl

    new_family = rng.choice(
        [
            StrategyFamily.TREND_FOLLOWING,
            StrategyFamily.MEAN_REVERSION,
            StrategyFamily.BREAKOUT,
            StrategyFamily.VOLATILITY_EXPANSION,
            StrategyFamily.LIQUIDITY_SWEEP,
        ]
    )
    tpl = _template_dsl(new_family, rng)
    raw = _raw(tpl)
    # Inject a fresh feature combination into the template.
    filters = list(raw.get("filters", []))
    extra = rng.sample(pool, min(2, len(pool)))
    for feat in extra:
        if feat not in {f.get("feature") for f in filters if isinstance(f, dict)}:
            filters.append({"feature": feat, "op": rng.choice(["gt", "lt"]), "value": 0.0})
    raw["filters"] = filters[:4]
    raw["context"] = {**raw.get("context", {}), "regime": {"use": True}}

    try:
        dsl = _rebuild(raw)
    except Exception:
        return None

    checks = [
        validate_schema(dsl),
        validate_features(dsl),
        validate_complexity(dsl, budgets or {}),
    ]
    if any(not c.passed for c in checks):
        return None

    digest = dsl_hash(dsl)
    return FactoryCandidate(
        candidate_id=candidate_id_from_hash(digest),
        definition_hash=digest,
        generation_id=base.generation_id,
        source=CandidateSource.RANDOM,
        operator=EvolutionOperator.NONE,
        parent_ids=[base.candidate_id, base.definition_hash[:12]],
        dsl=dsl,
        family=new_family,
        population_index=base.population_index,
    )


def mutate_with_action(
    candidate: FactoryCandidate,
    rng: random.Random | None = None,
    feature_pool: list[str] | None = None,
    budgets: dict[str, int] | None = None,
) -> tuple[FactoryCandidate | None, str]:
    """Per-action-attribution wrapper around mutate() (G28 TARGET 2).

    Returns ``(child_or_None, action)`` where action is one of the 7 mutation
    actions, so the orchestrator can attribute outcomes per action without
    changing the DSL representation or the structural gates.
    """
    rng = rng or random.Random(RANDOM_SEED)
    action = rng.choice(_MUTATION_ACTIONS)
    child = mutate(
        candidate,
        rng=rng,
        feature_pool=feature_pool,
        action=action,
        budgets=budgets,
    )
    return child, action


def adapt_probabilities(
    base: dict[str, float],
    operator_success: dict[str, float],
    diversity: float,
    diversity_floor: float,
) -> dict[str, float]:
    """Adaptive evolution (spec 99): bounded adjustment of operator
    probabilities from historical operator success + diversity pressure.

    Bounds every change to +/- 0.05 per step to avoid unstable feedback
    loops; probabilities always normalize to 1.0.
    """
    keys = ("mutation_rate", "crossover_rate", "exploration_rate")
    out = {k: max(0.0, min(1.0, float(base.get(k, 0.0)))) for k in keys}

    total_ok = sum(operator_success.values()) or 1.0
    for op, prob_key in (
        ("MUTATION", "mutation_rate"),
        ("CROSSOVER", "crossover_rate"),
        ("RANDOM", "exploration_rate"),
    ):
        rate = operator_success.get(op, 0.0) / total_ok
        expected = 1.0 / 3.0
        delta = (rate - expected) * 0.05
        out[prob_key] = max(0.0, min(1.0, out[prob_key] + delta))

    # Diversity pressure: when diversity collapses, boost exploration.
    if diversity < diversity_floor:
        out["exploration_rate"] = min(0.6, out["exploration_rate"] + 0.05)
        out["mutation_rate"] = max(0.0, out["mutation_rate"] - 0.025)
        out["crossover_rate"] = max(0.0, out["crossover_rate"] - 0.025)

    total = sum(out.values()) or 1.0
    return {k: round(v / total, 4) for k, v in out.items()}


__all__ = [
    "EvolutionOperator",
    "adapt_probabilities",
    "crossover",
    "explore",
    "mutate",
]
