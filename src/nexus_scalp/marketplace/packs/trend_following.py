"""Trend Following pack — pullback + HTF-aligned trend continuations."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "trend_following"
PACK_VERSION_DEFAULT = "1.0.0"

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "pullback_in_trend",
        "confirmations": ["choch_sig", "htf_h4_trend"],
        "filter_f": "dist_to_ema_21",
        "family": StrategyFamily.TREND_FOLLOWING,
        "exit": {"mode": "trailing", "factor": 2.0},
    },
    {
        "logic": "htf_aligned_entry",
        "confirmations": ["htf_h4_trend", "htf_h1_momentum"],
        "filter_f": "dist_to_ema_50",
        "family": StrategyFamily.TREND_FOLLOWING,
        "exit": {"mode": "trailing", "factor": 1.8},
    },
    {
        "logic": "momentum_continuation",
        "confirmations": ["consecutive_momentum_count", "tk_cross_signal"],
        "filter_f": "lag_1_log_return",
        "family": StrategyFamily.MOMENTUM,
        "exit": {"mode": "chandelier", "factor": 3.0},
    },
    {
        "logic": "combined_conditions",
        "confirmations": ["choch_sig", "consecutive_momentum_count"],
        "filter_f": "lag_1_atr_ratio",
        "family": StrategyFamily.TREND_FOLLOWING,
        "exit": {"mode": "trailing", "factor": 2.0},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt = spec["filter_f"]
        val = round(0.0 + (i % 6) * 0.05 * (1 if i % 2 == 0 else -1), 2)
        filters = [{"feature": filt, "op": "gt" if i % 2 == 0 else "lt", "value": val}]
        dsl = build_dsl(
            spec["family"],
            logic=spec["logic"],
            confirmations=list(spec["confirmations"]),
            filters=filters,
            exit_spec=spec["exit"],
        )
        if not validate_dsl(dsl):
            continue
        out.append(
            make_seed_spec(
                PACK_ID,
                version,
                i,
                dsl,
                name_prefix="Trend Following",
                description=f"Trend {spec['logic']} variant",
                expected_regimes=["trending", "expansion"],
                unsupported_regimes=["ranging", "contraction"],
            )
        )
    return out
