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
# ---------------------------------------------------------------------------
# PHASE 25 hypothesis-driven discovery (2026-08-25) — Session & Family Premises
# ---------------------------------------------------------------------------
SESSION_REGIMES: dict[str, dict[str, Any]] = {
    "ASIAN": {
        "utc_hours": (0, 8),
        "dominant_regime": "RANGING",
        "volatility": "CONTRACTION",
        "liquidity_behavior": "liquidity builds at Asian range extremes",
    },
    "LONDON": {
        "utc_hours": (8, 13),
        "dominant_regime": "TRENDING",
        "volatility": "EXPANSION",
        "liquidity_behavior": "Asian-range liquidity is swept at the open",
    },
    "LONDON_NY_OVERLAP": {
        "utc_hours": (13, 17),
        "dominant_regime": "TRENDING",
        "volatility": "EXPANSION",
        "liquidity_behavior": "peak two-way liquidity; breakouts extend",
    },
    "NY": {
        "utc_hours": (17, 22),
        "dominant_regime": "TRENDING",
        "volatility": "EXPANSION",
        "liquidity_behavior": "US flows continue or exhaust the London move",
    },
}

_FAMILY_HYPOTHESES: dict[StrategyFamily, dict[str, Any]] = {
    StrategyFamily.TREND_FOLLOWING: {
        "statement": (
            "During London session after an Asian-range liquidity sweep, with "
            "HTF H4 trend alignment and volatility expansion, pullback-in-trend "
            "continuation shows positive expectancy"
        ),
        "market_condition": "LONDON | TRENDING | VOLATILITY_EXPANSION | liquidity swept at open",
        "entry_reason": (
            "Price pulls back into a higher-timeframe demand zone inside an H4 "
            "uptrend and prints a bullish CHoCH on the entry timeframe"
        ),
        "exit_reason": (
            "Trail behind structure at 2x ATR; exit when the entry-timeframe "
            "CHoCH flips against the position or HTF bias turns neutral"
        ),
        "expected_edge": (
            "London continuation flow extends the established direction after "
            "the sweep, targeting >= +0.3R average expectancy per trade"
        ),
        "failure_condition": (
            "HTF trend flattens or volatility contracts into range: pullbacks "
            "stop continuing and expectancy degrades below zero"
        ),
    },
    StrategyFamily.MEAN_REVERSION: {
        "statement": (
            "During the Asian session inside a neutral-trend contraction range, "
            "overshoot beyond the session extreme reverts to equilibrium "
            "(range midline) with positive expectancy"
        ),
        "market_condition": "ASIAN | RANGING | VOLATILITY_CONTRACTION | balanced two-way liquidity",
        "entry_reason": (
            "RSI-style oscillator undershoots while price spikes beyond the "
            "Asian range edge against a flat HTF bias"
        ),
        "exit_reason": (
            "Fixed target at the range midline (2R) or exit on range-boundary "
            "break with volume confirmation"
        ),
        "expected_edge": (
            "Asian-session inventory rebalancing snaps overshoots back to "
            "fair value, targeting >= +0.25R average expectancy"
        ),
        "failure_condition": (
            "Range resolves into a directional breakout (volatility expansion): "
            "reversion entries get run over and losses exceed the target"
        ),
    },
    StrategyFamily.BREAKOUT: {
        "statement": (
            "During London-NY overlap with volatility expansion and HTF trend "
            "alignment, a confirmed range break in the direction of HTF bias "
            "shows positive expectancy as released liquidity fuels continuation"
        ),
        "market_condition": "LONDON_NY_OVERLAP | TRENDING | VOLATILITY_EXPANSION | peak directional liquidity",
        "entry_reason": (
            "Breakout signal confirms a close beyond the compression range with "
            "above-average volume participation"
        ),
        "exit_reason": "Fixed 2.5R target; stop stays just inside the broken range boundary",
        "expected_edge": (
            "Overlap-session breakouts convert trapped-stop liquidity into "
            "momentum continuation, targeting >= +0.3R average expectancy"
        ),
        "failure_condition": (
            "False break / failure swing: price re-enters the range within a few "
            "bars and the stop absorbs the trap (expansion without follow-through)"
        ),
    },
    StrategyFamily.REVERSAL: {
        "statement": (
            "After a London-session liquidity sweep of the Asian range low/high "
            "into a marked exhaustion zone, displacement-backed reversal entries "
            "show positive expectancy"
        ),
        "market_condition": "LONDON | EXHAUSTION | VOLATILITY_EXPANSION | stop-hunt liquidity taken",
        "entry_reason": (
            "Pinbar/exhaustion candle prints at the swept extreme with "
            "displacement confirming rejection of the level"
        ),
        "exit_reason": "Target at the origin of the sweep (2R); exit on renewed sweep of the same extreme",
        "expected_edge": (
            "Swept-stop fuel plus displacement reversal recaptures the prior "
            "value area, targeting >= +0.3R average expectancy"
        ),
        "failure_condition": (
            "Genuine trend continuation through the level: the reversal fails "
            "and price extends past the exhaustion zone"
        ),
    },
    StrategyFamily.MOMENTUM: {
        "statement": (
            "During New York session with consecutive momentum bars aligned to "
            "HTF trend and volatility expansion, momentum-continuation entries "
            "show positive expectancy"
        ),
        "market_condition": "NY | TRENDING | VOLATILITY_EXPANSION | sustained US-flow direction",
        "entry_reason": (
            "Consecutive momentum count confirms persistence and the Tenkan/"
            "Kijun cross agrees with the HTF direction"
        ),
        "exit_reason": (
            "Chandelier trail at 3x ATR rides the momentum leg; exit on "
            "trail-out or opposing momentum burst"
        ),
        "expected_edge": (
            "US-session flow persistence extends intraday trends beyond random "
            "walk baseline, targeting >= +0.25R average expectancy"
        ),
        "failure_condition": (
            "Momentum stalls into chop (trend state flips NEUTRAL): trailing "
            "stops absorb repeated false starts"
        ),
    },
    StrategyFamily.VOLATILITY_EXPANSION: {
        "statement": (
            "When ATR expands sharply above its baseline during the London-NY "
            "overlap, volatility-expansion breaks in the expansion direction "
            "show positive expectancy"
        ),
        "market_condition": "LONDON_NY_OVERLAP | EXPANSION | VOLATILITY_EXPANSION | liquidity release event",
        "entry_reason": (
            "Normalized displacement exceeds threshold while ATR ratio confirms "
            "genuine expansion (not a single spike)"
        ),
        "exit_reason": "Fixed 2R target sized off the expanded ATR; stop below the expansion origin",
        "expected_edge": (
            "Expansion regimes persist short clusters of bars; entering on "
            "confirmed expansion captures the fat right tail, >= +0.25R expectancy"
        ),
        "failure_condition": (
            "One-bar volatility spike that immediately decays: entry buys the "
            "top of the burst and mean-reverts against the position"
        ),
    },
    StrategyFamily.VOLATILITY_CONTRACTION: {
        "statement": (
            "During Asian-session volatility contraction, a squeeze-break out of "
            "the compression range in the HTF direction shows positive expectancy"
        ),
        "market_condition": "ASIAN | CONTRACTION | VOLATILITY_CONTRACTION | compressed liquidity pockets",
        "entry_reason": (
            "Price-compression flag ratio exceeds threshold and the break "
            "confirms with a breakout signal"
        ),
        "exit_reason": "Target at 2R measured-move of the compressed box; stop inside the box",
        "expected_edge": (
            "Compression precedes expansion: energy stored in the squeeze "
            "resolves directionally, targeting >= +0.25R expectancy"
        ),
        "failure_condition": (
            "Compression deepens instead of resolving: repeated failed breaks "
            "chip away at equity inside dead liquidity"
        ),
    },
    StrategyFamily.LIQUIDITY_SWEEP: {
        "statement": (
            "During London session after an explicit stop-hunt sweep of resting "
            "liquidity below/above a structural level, sweep-reversal entries "
            "with displacement confirmation show positive expectancy"
        ),
        "market_condition": "LONDON | REVERSAL | VOLATILITY_EXPANSION | engineered liquidity sweep active",
        "entry_reason": (
            "Liquidity-sweep state flags a completed grab and stop-hunt depth "
            "exceeds the structural threshold"
        ),
        "exit_reason": "Target at 2.5R back toward pre-sweep value; hard stop beyond the sweep wick",
        "expected_edge": (
            "Post-sweep displacement reversals monetize trapped positioning, "
            "targeting >= +0.35R average expectancy"
        ),
        "failure_condition": (
            "The sweep IS the trend: price keeps running through successive "
            "levels and reversal stops cascade"
        ),
    },
    StrategyFamily.SESSION: {
        "statement": (
            "During the London-NY overlap, session-boundary breakouts with HTF "
            "regime alignment show positive expectancy as peak liquidity "
            "resolves the preceding range"
        ),
        "market_condition": "LONDON_NY_OVERLAP | SESSION_BOUND | VOLATILITY_EXPANSION | maximal participation window",
        "entry_reason": (
            "London/NY overlap flag is active and price breaks the pre-overlap "
            "session high/low"
        ),
        "exit_reason": "Fixed 2R target before session close; no overnight carry",
        "expected_edge": (
            "The overlap window carries the highest intraday liquidity share; "
            "session-resolved moves complete before close, >= +0.25R expectancy"
        ),
        "failure_condition": (
            "Low-participation anomaly day (holiday/thin book): the overlap "
            "breaks nothing and chops both boundaries"
        ),
    },
    StrategyFamily.MULTI_TIMEFRAME: {
        "statement": (
            "With H4 and H1 multi-timeframe alignment during London, HTF-aligned "
            "pullback entries show positive expectancy because cross-timeframe "
            "confirmation filters counter-trend noise"
        ),
        "market_condition": "LONDON | TRENDING | MIXED_VOLATILITY | HTF-aligned directional liquidity",
        "entry_reason": (
            "H4 trend state and H1 momentum agree and price retraces to value "
            "near the EMA50 anchor"
        ),
        "exit_reason": "Trailing stop at 1.8x ATR below structure; exit on H1 momentum flip",
        "expected_edge": (
            "Multi-timeframe agreement raises per-trade information content, "
            "targeting >= +0.3R expectancy with fewer false positives"
        ),
        "failure_condition": (
            "Timeframe disagreement (H4 vs H1 conflict): entries fire into "
            "transition zones and lose on both sides"
        ),
    },
    StrategyFamily.HYBRID: {
        "statement": (
            "Combining HTF bias, volatility-expansion and London-NY overlap "
            "filters, CHoCH-plus-momentum combined entries show positive "
            "expectancy across trending and expansion regimes"
        ),
        "market_condition": "LONDON_NY_OVERLAP | TRENDING+EXPANSION | regime-filtered composite",
        "entry_reason": (
            "CHoCH confirmation and consecutive-momentum agreement fire "
            "together only when all context filters pass"
        ),
        "exit_reason": "Trailing stop at 2x ATR; exit when any composite filter invalidates",
        "expected_edge": (
            "Condition stacking trades less but cleaner: filtered expectancy "
            "per trade targets >= +0.3R at reduced frequency"
        ),
        "failure_condition": (
            "Filter over-constraint starves the sample: too few qualifying "
            "setups leave expectancy statistically unproven"
        ),
    },
}

def _family_hypothesis(family: StrategyFamily) -> dict[str, Any]:
    base = _FAMILY_HYPOTHESES.get(family, _FAMILY_HYPOTHESES[StrategyFamily.HYBRID])
    return {
        "statement": str(base["statement"]),
        "market_condition": str(base["market_condition"]),
        "entry_reason": str(base["entry_reason"]),
        "exit_reason": str(base["exit_reason"]),
        "expected_edge": str(base["expected_edge"]),
        "failure_condition": str(base["failure_condition"]),
    }

_FAMILY_TEMPLATES: dict[StrategyFamily, dict[str, Any]] = {
    StrategyFamily.TREND_FOLLOWING: {
        "context": {"htf_bias": {"use": True}, "trend_state": {"use": True}},
        "entry": {"logic": "pullback_in_trend", "confirmation": ["choch_sig", "htf_h4_trend"]},
        "filters": [{"feature": "dist_to_ema_21", "op": "gt", "value": 0.0}],
        "exit": {"mode": "trailing", "factor": 2.0},
    },
    StrategyFamily.MEAN_REVERSION: {
        "context": {"volatility_filter": {"use": True}, "range_state": {"use": True}},
        "entry": {
            "logic": "overshoot_reversion",
            "confirmation": ["extreme_sig", "rapid_reversal_spike_val"],
        },
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
        "entry": {
            "logic": "exhaustion_reversal",
            "confirmation": ["pinbar_sig", "norm_displacement"],
        },
        "filters": [{"feature": "upper_wick_ratio", "op": "gt", "value": 0.5}],
        "exit": {"mode": "target", "rr": 2.0},
    },
    StrategyFamily.MOMENTUM: {
        "context": {"trend_state": {"use": True}, "volatility_filter": {"use": True}},
        "entry": {
            "logic": "momentum_continuation",
            "confirmation": ["consecutive_momentum_count", "tk_cross_signal"],
        },
        "filters": [{"feature": "lag_1_log_return", "op": "gt", "value": 0.0}],
        "exit": {"mode": "chandelier", "factor": 3.0},
    },
    StrategyFamily.VOLATILITY_EXPANSION: {
        "context": {"volatility_filter": {"use": True}},
        "entry": {
            "logic": "volatility_expansion_break",
            "confirmation": ["norm_displacement", "breakout_sig"],
        },
        "filters": [{"feature": "lag_1_atr_ratio", "op": "gt", "value": 0.3}],
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
    StrategyFamily.VOLATILITY_CONTRACTION: {
        "context": {"volatility_filter": {"use": True}},
        "entry": {
            "logic": "squeeze_break",
            "confirmation": ["price_compression_flag_ratio", "breakout_sig"],
        },
        "filters": [{"feature": "price_compression_flag_ratio", "op": "gt", "value": 0.5}],
        "exit": {"mode": "target", "rr": 2.0},
    },
    StrategyFamily.LIQUIDITY_SWEEP: {
        "context": {"liquidity": {"use": True}, "session_filter": {"use": True}},
        "entry": {
            "logic": "liquidity_sweep_reversal",
            "confirmation": ["liquidity_sweep_signal", "stop_hunt_depth"],
        },
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
        "entry": {
            "logic": "htf_aligned_entry",
            "confirmation": ["htf_h4_trend", "htf_h1_momentum"],
        },
        "filters": [{"feature": "dist_to_ema_50", "op": "gt", "value": 0.0}],
        "exit": {"mode": "trailing", "factor": 1.8},
    },
    StrategyFamily.HYBRID: {
        "context": {
            "htf_bias": {"use": True},
            "volatility_filter": {"use": True},
            "session_filter": {"use": True},
        },
        "entry": {
            "logic": "combined_conditions",
            "confirmation": ["choch_sig", "consecutive_momentum_count"],
        },
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
    hypothesis = _family_hypothesis(family)
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
            {
                "feature": f,
                "op": rng.choice(["gt", "lt"]),
                "value": round(rng.uniform(-0.5, 0.5), 2),
            }
            for f in chosen
        ]
        tf = rng.choice(SUPPORTED_TIMEFRAMES[:5])
        dsl = StrategyDsl(
            schema_version=DSL_SCHEMA_VERSION,
            hypothesis=_family_hypothesis(fam),
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
    dsls += generate_template_candidates(n_llm_slot, seed=seed + 5)  # placeholder source TEMPLATE

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
    "DEFAULT_SYMBOLS",
    "DSL_SCHEMA_VERSION",
    "GENERATOR_VERSION",
    "SUPPORTED_TIMEFRAMES",
    "StrategyFamily",
    "build_feature_catalog",
    "candidate_id_from_hash",
    "canonical_json",
    "canonicalize_dsl",
    "dsl_hash",
    "feature_catalog_index",
    "feature_ids",
    "generate_diversity_candidates",
    "generate_generation_zero",
    "generate_random_candidates",
    "generate_regime_candidates",
    "generate_template_candidates",
]
