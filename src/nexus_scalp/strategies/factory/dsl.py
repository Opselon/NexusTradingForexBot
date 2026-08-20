"""
Strategy DSL — Schema, Feature Catalog, Canonicalization, Deterministic Generation
====================================================================================
STRATEGY FACTORY (2026-08-20).

The DSL is the ONLY strategy representation the factory (and the optional LLM
provider) may produce. Features come EXCLUSIVELY from the canonical 70D schema
contract (`features/schema_contract.py`) — the factory never invents features,
never changes the feature vector dimension (spec 10), and never allows the LLM
to hallucinate unsupported indicators (spec 9).

Deterministic generation: template + feature-combination + regime + controlled
random exploration built from the approved catalog. This is the DEFAULT path;
the LLM provider (provider.py) is an optional assisted generation source whose
output goes through the SAME canonicalization + validation pipeline.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
from typing import Any

from nexus_scalp.features.schema_contract import (
    FAMILY_BASE,
    FAMILY_LIQUIDITY,
    FAMILY_NEWS,
    canonical_feature_names,
    family_of,
)
from nexus_scalp.strategies.factory.models import (
    CandidateSource,
    EvolutionOperator,
    FactoryCandidate,
    FeatureCatalogEntry,
    StrategyDsl,
    StrategyFamily,
)

#: DSL schema version (spec 86) — bump when the DSL grammar changes.
DSL_SCHEMA_VERSION: str = "1.0"
#: Prompt / generator version — every candidate records which version produced it.
GENERATOR_VERSION: str = "deterministic-v1"

#: Timeframes supported by the data/feature pipeline (spec 65).
SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")

#: Symbols supported by configuration (XAUUSD is the initial universe; never hardcoded
#: into the factory logic — the orchestrator passes the configured universe).
DEFAULT_SYMBOLS: tuple[str, ...] = ("XAUUSD",)

#: Controlled-random exploration seed space for deterministic reproducibility.
RANDOM_SEED: int = 20260820

#: Family -> preferred structural template (deterministic hypothesis family map).
_FAMILY_TEMPLATES: dict[StrategyFamily, dict[str, Any]] = {
    StrategyFamily.TREND_FOLLOWING: {
        "context": {"htf_bias": {"use": True}, "trend_state": {"use": True}},
        "entry": {"logic": "pullback_in_trend", "confirmation": ["choch_sig", "htf_h4_trend"]},
        "filters": [{"feature": "dist_to_ema_21", "op": "gt", "value": 0.0}],
        "exit": {"mode": "trailing", "factor": 2.0},
    },
    StrategyFamily.MEAN_REVERSION: {
        "context": {"volatility_filter": {"use": True}, "range_state": {"use": True}},
        "entry": {"logic": "overshoot_reversion", "confirmation": ["extreme_sig", "rapid_reversal_spike_val"]},
        "filters": [{"feature": "norm_rsi", "op": "lt", "value": -0.5}],
        "exit": {"mode": "target", "rr": 2.0},
    },
    StrategyFamily.BREAKOUT: {
        "context": {"volatility_filter": {"use": True}, "session_filter": {"use": True}},
        "entry": {"logic": "range_break", "confirmation": ["breakout_sig", "lag_1_volume_z"]},
        "filters": [{"feature": "lag_1_atr_ratio", "op": "gt", "value": 0.0}],
        "exit": {"mode": "fixed_rr", "rr": 2.5},
    },
    StrategyFamily.REVERSAL: {
        "context": {"htf_bias": {"use": True}, "session_filter": {"use": True}},
        "entry": {"logic": "exhaustion_reversal", "confirmation": ["pinbar_sig", "norm_displacement"]},
        "filters": [{"feature": "upper_wick_ratio", "op": "gt", "value": 0.5}],
        "exit": {"mode": "target", "rr": 2.0},
    },
    StrategyFamily.MOMENTUM: {
        "context": {"trend_state": {"use": True}, "volatility_filter": {"use": True}},
        "entry": {"logic": "momentum_continuation", "confirmation": ["consecutive_momentum_count", "tk_cross_signal"]},
        "filters": [{"feature": "lag_1_log_return", "op": "gt", "value": 0.0}],
        "exit": {"mode": "chandelier", "factor": 3.0},
    },
    StrategyFamily.VOLATILITY_EXPANSION: {
        "context": {"volatility_filter": {"use": True}},
        "entry": {"logic": "volatility_expansion_break", "confirmation": ["norm_displacement", "breakout_sig"]},
        "filters": [{"feature": "lag_1_atr_ratio", "op": "gt", "value": 0.3}],
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
    StrategyFamily.VOLATILITY_CONTRACTION: {
        "context": {"volatility_filter": {"use": True}},
        "entry": {"logic": "squeeze_break", "confirmation": ["price_compression_flag_ratio", "breakout_sig"]},
        "filters": [{"feature": "price_compression_flag_ratio", "op": "gt", "value": 0.5}],
        "exit": {"mode": "target", "rr": 2.0},
    },
    StrategyFamily.LIQUIDITY_SWEEP: {
        "context": {"liquidity": {"use": True}, "session_filter": {"use": True}},
        "entry": {"logic": "liquidity_sweep_reversal", "confirmation": ["liquidity_sweep_signal", "stop_hunt_depth"]},
        "filters": [{"feature": "liquidity_sweep_state", "op": "eq", "value": 1.0}],
        "exit": {"mode": "target", "rr": 2.5},
    },
    StrategyFamily.SESSION: {
        "context": {"session_filter": {"use": True}, "regime": {"use": True}},
        "entry": {"logic": "session_break", "confirmation": ["session_london", "session_ny"]},
        "filters": [{"feature": "session_overlap_london_ny", "op": "eq", "value": 1.0}],
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
    StrategyFamily.MULTI_TIMEFRAME: {
        "context": {"htf_bias": {"use": True}, "timeframes": ["M1", "H1"]},
        "entry": {"logic": "htf_aligned_entry", "confirmation": ["htf_h4_trend", "htf_h1_momentum"]},
        "filters": [{"feature": "dist_to_ema_50", "op": "gt", "value": 0.0}],
        "exit": {"mode": "trailing", "factor": 1.8},
    },
    StrategyFamily.HYBRID: {
        "context": {
            "htf_bias": {"use": True},
            "volatility_filter": {"use": True},
            "session_filter": {"use": True},
        },
        "entry": {"logic": "combined_conditions", "confirmation": ["choch_sig", "consecutive_momentum_count"]},
        "filters": [{"feature": "lag_1_atr_ratio", "op": "gt", "value": 0.0}],
        "exit": {"mode": "trailing", "factor": 2.0},
    },
}


# ---------------------------------------------------------------------------
# Feature catalog (from the canonical 70D contract — never invented)
# ---------------------------------------------------------------------------


def build_feature_catalog() -> list[FeatureCatalogEntry]:
    """Builds the machine-readable catalog from the canonical 70D schema.

    Index, name and family come straight from schema_contract; category is
    derived from the family. This guarantees the catalog can NEVER drift from
    the real feature vector the model consumes (spec 9 / 10).
    """
    names = canonical_feature_names()
    categories = {
        FAMILY_BASE: "price_action",
        FAMILY_NEWS: "news",
        FAMILY_LIQUIDITY: "liquidity",
    }
    entries: list[FeatureCatalogEntry] = []
    for idx, name in enumerate(names):
        fam = family_of(idx)
        entries.append(
            FeatureCatalogEntry(
                feature_id=name,
                index=idx,
                family=fam,
                description=f"70D contract feature at index {idx} (family={fam})",
                category=categories.get(fam, "base"),
                causal=True,
                lookahead_safe=True,
            )
        )
    return entries


def feature_catalog_index() -> dict[str, FeatureCatalogEntry]:
    """feature_id -> entry (fast lookup for validation)."""
    return {e.feature_id: e for e in build_feature_catalog()}


def feature_ids() -> list[str]:
    """All approved feature ids (compact list for prompts / templates)."""
    return [e.feature_id for e in build_feature_catalog()]


# ---------------------------------------------------------------------------
# Canonicalization (spec 13 / 40 / 75)
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Deterministic JSON serialization (sorted keys, stable separators)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def dsl_hash(dsl: StrategyDsl) -> str:
    """Content hash of a DSL definition (dedup + identity key, spec 13)."""
    return hashlib.sha256(canonical_json(dsl.model_dump()).encode("utf-8")).hexdigest()


def candidate_id_from_hash(digest: str) -> str:
    """Stable human-scannable candidate id: SF-<first-10-hex-upper>."""
    return f"SF-{digest[:10].upper()}"


def canonicalize_dsl(dsl: StrategyDsl | dict[str, Any]) -> StrategyDsl:
    """Normalizes a DSL dict into the canonical StrategyDsl model.

    Unknown fields are structurally rejected (extra='forbid'); the canonical
    form is what gets hashed and persisted.
    """
    if isinstance(dsl, StrategyDsl):
        return dsl
    return StrategyDsl(**dsl)


# ---------------------------------------------------------------------------
# Deterministic candidate generation (default path — no LLM required)
# ---------------------------------------------------------------------------


def _feature_group(family: StrategyFamily) -> list[str]:
    """Feature ids relevant to a family (subset of the 70D catalog)."""
    all_ids = feature_ids()
    base_ids = [f for f in all_ids if family_of(_index_of(f)) == FAMILY_BASE]
    liq_ids = [f for f in all_ids if family_of(_index_of(f)) == FAMILY_LIQUIDITY]
    if family in (StrategyFamily.LIQUIDITY_SWEEP,):
        return liq_ids[:5] + base_ids[:8]
    if family in (StrategyFamily.SESSION, StrategyFamily.MULTI_TIMEFRAME):
        return base_ids[:12]
    return base_ids[:16]


def _index_of(feature_id: str) -> int:
    return feature_catalog_index()[feature_id].index


def _template_dsl(family: StrategyFamily, rng: random.Random) -> StrategyDsl:
    """Deterministic template strategy for a family with bounded randomization."""
    tpl = _FAMILY_TEMPLATES.get(family, _FAMILY_TEMPLATES[StrategyFamily.HYBRID])
    tfs = list(SUPPORTED_TIMEFRAMES[:5])  # M1..H1 for templates
    tf = rng.choice(tfs)
    market = {"symbols": list(DEFAULT_SYMBOLS), "timeframes": [tf]}
    hypothesis = {
        "statement": f"{family.value} hypothesis: exploit {family.value.lower().replace('_', ' ')} "
        "dynamics in the configured market.",
        "market_mechanism": _FAMILY_TEMPLATES[family].get("entry", {}).get("logic", "price action"),
        "expected_regime": _expected_regime(family),
        "invalidation": ["trend flattening", "regime mismatch"],
        "abstain_conditions": ["high spread", "news shock"],
    }
    return StrategyDsl(
        schema_version=DSL_SCHEMA_VERSION,
        hypothesis=hypothesis,
        family=family,
        market=market,
        context=tpl.get("context", {}),
        setup={"structure": {"use": True}},
        entry=tpl.get("entry", {}),
        filters=list(tpl.get("filters", [])),
        exit=tpl.get("exit", {}),
        risk={"risk_governance": "global_risk_authority", "max_risk_per_trade_pct": 0.5},
        constraints={"max_conditions": 6, "no_future_data": True},
    )


def _expected_regime(family: StrategyFamily) -> list[str]:
    mapping: dict[StrategyFamily, list[str]] = {
        StrategyFamily.TREND_FOLLOWING: ["trending", "expansion"],
        StrategyFamily.MEAN_REVERSION: ["ranging", "contraction"],
        StrategyFamily.BREAKOUT: ["expansion", "trending"],
        StrategyFamily.REVERSAL: ["exhaustion", "ranging"],
        StrategyFamily.MOMENTUM: ["trending", "expansion"],
        StrategyFamily.VOLATILITY_EXPANSION: ["expansion"],
        StrategyFamily.VOLATILITY_CONTRACTION: ["contraction"],
        StrategyFamily.LIQUIDITY_SWEEP: ["reversal", "expansion"],
        StrategyFamily.SESSION: ["session_bound", "trending"],
        StrategyFamily.MULTI_TIMEFRAME: ["trending", "ranging", "expansion"],
        StrategyFamily.HYBRID: ["trending", "ranging", "expansion", "contraction"],
    }
    return mapping.get(family, ["trending", "ranging", "expansion", "contraction"])


def generate_template_candidates(
    count: int,
    families: list[StrategyFamily] | None = None,
    seed: int = RANDOM_SEED,
) -> list[StrategyDsl]:
    """Deterministic template exploration (Generation 0 hypothesis-driven 30%)."""
    families = families or list(StrategyFamily)
    out: list[StrategyDsl] = []
    rng = random.Random(seed)
    i = 0
    while len(out) < count and i < count * 4:
        i += 1
        family = families[len(out) % len(families)]
        dsl = _template_dsl(family, rng)
        # rotate thresholds slightly per population slot for diversity
        dsl = _rotate_template(dsl, len(out), rng)
        out.append(dsl)
    return out[:count]


def _rotate_template(dsl: StrategyDsl, slot: int, rng: random.Random) -> StrategyDsl:
    """Applies a deterministic bounded variation to one template (parameter diversity)."""
    raw = dsl.model_dump()
    filters = raw.get("filters", [])
    for f in filters:
        if isinstance(f, dict) and "value" in f and isinstance(f["value"], (int, float)):
            delta = (slot % 5) * 0.05 * (1.0 if slot % 2 else -1.0)
            f["value"] = round(float(f["value"]) + delta, 2)
    raw["filters"] = filters
    raw["constraints"] = {**raw.get("constraints", {}), "generator_version": GENERATOR_VERSION}
    return StrategyDsl(**raw)


def generate_diversity_candidates(
    count: int,
    seed: int = RANDOM_SEED,
) -> list[StrategyDsl]:
    """Feature-combination exploration (Generation 0 20%): deterministic cartesian
    sampling of approved feature pairs with family-appropriate logical wiring."""
    fams = [
        StrategyFamily.TREND_FOLLOWING,
        StrategyFamily.MEAN_REVERSION,
        StrategyFamily.BREAKOUT,
        StrategyFamily.MOMENTUM,
    ]
    rng = random.Random(seed + 7)
    out: list[StrategyDsl] = []
    base_ids = [f for f in feature_ids() if family_of(_index_of(f)) == FAMILY_BASE]
    pairs = list(itertools.combinations(base_ids[:10], 2))
    rng.shuffle(pairs)
    for a, b in pairs[: max(1, count * 3)]:
        if len(out) >= count:
            break
        family = fams[len(out) % len(fams)]
        tf = rng.choice(SUPPORTED_TIMEFRAMES[:4])
        dsl = StrategyDsl(
            schema_version=DSL_SCHEMA_VERSION,
            hypothesis={
                "statement": f"Combination hypothesis: {a} x {b} interaction",
                "market_mechanism": "combined feature interaction",
                "expected_regime": _expected_regime(family),
                "invalidation": ["regime mismatch"],
                "abstain_conditions": ["high spread"],
            },
            family=family,
            market={"symbols": list(DEFAULT_SYMBOLS), "timeframes": [tf]},
            context={"volatility_filter": {"use": True}},
            setup={"structure": {"use": True}},
            entry={"logic": "feature_combination", "confirmation": [a, b]},
            filters=[
                {"feature": a, "op": "gt", "value": 0.0},
                {"feature": b, "op": "gt", "value": 0.0},
            ],
            exit={"mode": "fixed_rr", "rr": 2.0},
            risk={"risk_governance": "global_risk_authority"},
            constraints={"max_conditions": 5, "no_future_data": True},
        )
        out.append(dsl)
    return out[:count]


def generate_regime_candidates(
    count: int,
    regimes: list[str] | None = None,
    seed: int = RANDOM_SEED,
) -> list[StrategyDsl]:
    """Regime-specific strategies (Generation 0 10%, spec 6)."""
    regimes = regimes or ["TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY"]
    rng = random.Random(seed + 13)
    family_by_regime: dict[str, StrategyFamily] = {
        "TRENDING": StrategyFamily.TREND_FOLLOWING,
        "RANGING": StrategyFamily.MEAN_REVERSION,
        "HIGH_VOLATILITY": StrategyFamily.VOLATILITY_EXPANSION,
        "LOW_VOLATILITY": StrategyFamily.VOLATILITY_CONTRACTION,
    }
    out: list[StrategyDsl] = []
    i = 0
    while len(out) < count and i < count * 4:
        i += 1
        regime = regimes[len(out) % len(regimes)]
        family = family_by_regime.get(regime, StrategyFamily.HYBRID)
        dsl = _template_dsl(family, rng)
        raw = dsl.model_dump()
        raw["context"] = {**raw.get("context", {}), "regime": {"require": regime}}
        raw["hypothesis"]["expected_regime"] = [regime.lower()]
        raw["constraints"] = {
            **raw.get("constraints", {}),
            "regime_specialization": regime,
            "generator_version": GENERATOR_VERSION,
        }
        out.append(StrategyDsl(**raw))
    return out[:count]


def generate_random_candidates(
    count: int,
    seed: int = RANDOM_SEED,
) -> list[StrategyDsl]:
    """Controlled random exploration (Generation 0 10%, spec 6).

    Bounded: features come ONLY from the approved catalog, conditions are
    capped by the complexity budget, and every candidate still carries a
    coherent hypothesis. This is exploration, NOT free-form hallucination.
    """
    rng = random.Random(seed + 29)
    out: list[StrategyDsl] = []
    fams = list(StrategyFamily)
    base_ids = [f for f in feature_ids() if family_of(_index_of(f)) == FAMILY_BASE][:14]
    for _ in range(count):
        fam = rng.choice(fams)
        n_filters = rng.randint(1, 4)
        chosen = rng.sample(base_ids, min(n_filters, len(base_ids)))
        filters = [
            {"feature": f, "op": rng.choice(["gt", "lt"]), "value": round(rng.uniform(-0.5, 0.5), 2)}
            for f in chosen
        ]
        tf = rng.choice(SUPPORTED_TIMEFRAMES[:5])
        dsl = StrategyDsl(
            schema_version=DSL_SCHEMA_VERSION,
            hypothesis={
                "statement": f"Exploration hypothesis ({fam.value}): bounded feature combos",
                "market_mechanism": "exploratory feature interaction",
                "expected_regime": _expected_regime(fam),
                "invalidation": ["no observable mechanism"],
                "abstain_conditions": ["high spread", "low liquidity"],
            },
            family=fam,
            market={"symbols": list(DEFAULT_SYMBOLS), "timeframes": [tf]},
            context={"volatility_filter": {"use": bool(rng.randint(0, 1))}},
            setup={"structure": {"use": bool(rng.randint(0, 1))}},
            entry={"logic": "bounded_exploration", "confirmation": [f["feature"] for f in filters]},
            filters=filters,
            exit={"mode": rng.choice(["fixed_rr", "trailing", "target"]), "rr": 2.0},
            risk={"risk_governance": "global_risk_authority"},
            constraints={"max_conditions": len(filters) + 3, "no_future_data": True},
        )
        out.append(dsl)
    return out[:count]


def generate_generation_zero(
    population: int,
    seed: int = RANDOM_SEED,
) -> list[FactoryCandidate]:
    """Generation 0 mixture (spec 6): 30% templates, 20% diversity, 10%
    regime, 10% random, 30% LLM (when the provider is configured; the caller
    replaces a slack slice with LLM candidates, else templates fill it)."""
    import math

    n_tpl = max(1, int(population * 0.30))
    n_div = max(1, int(population * 0.20))
    n_reg = max(1, int(population * 0.10))
    n_rnd = max(1, int(population * 0.10))
    n_llm_slot = population - (n_tpl + n_div + n_reg + n_rnd)
    n_llm_slot = max(0, n_llm_slot)

    dsls: list[StrategyDsl] = []
    dsls += generate_template_candidates(n_tpl, seed=seed)
    dsls += generate_diversity_candidates(n_div, seed=seed)
    dsls += generate_regime_candidates(n_reg, seed=seed)
    dsls += generate_random_candidates(n_rnd, seed=seed)
    # LLM slice: if no provider is active the orchestrator substitutes more
    # templates (caller-side); here we leave the slot unfilled deterministically.
    dsls += generate_template_candidates(
        n_llm_slot, seed=seed + 5
    )  # placeholder source TEMPLATE

    candidates: list[FactoryCandidate] = []
    generation_id = "G0"
    for i, dsl in enumerate(dsls[:population]):
        digest = dsl_hash(dsl)
        source = CandidateSource.TEMPLATE
        if i < n_tpl:
            source = CandidateSource.TEMPLATE
        elif i < n_tpl + n_div:
            source = CandidateSource.DIVERSITY
        elif i < n_tpl + n_div + n_reg:
            source = CandidateSource.REGIME
        elif i < n_tpl + n_div + n_reg + n_rnd:
            source = CandidateSource.RANDOM
        else:
            source = CandidateSource.LLM  # slot; provider fills or falls back
        candidates.append(
            FactoryCandidate(
                candidate_id=candidate_id_from_hash(digest),
                definition_hash=digest,
                generation_id=generation_id,
                source=source,
                operator=EvolutionOperator.NONE,
                parent_ids=[],
                dsl=dsl,
                family=dsl.family,
                population_index=i,
            )
        )
    return candidates


__all__ = [
    "DSL_SCHEMA_VERSION",
    "GENERATOR_VERSION",
    "SUPPORTED_TIMEFRAMES",
    "DEFAULT_SYMBOLS",
    "StrategyFamily",
    "build_feature_catalog",
    "canonical_json",
    "canonicalize_dsl",
    "candidate_id_from_hash",
    "dsl_hash",
    "feature_catalog_index",
    "feature_ids",
    "generate_diversity_candidates",
    "generate_generation_zero",
    "generate_random_candidates",
    "generate_regime_candidates",
    "generate_template_candidates",
]