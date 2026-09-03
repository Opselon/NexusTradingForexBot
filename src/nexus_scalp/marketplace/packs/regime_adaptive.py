"""Regime Adaptive (Hybrid) pack — multi-regime wrappers + stacked filters."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "regime_adaptive"
PACK_VERSION_DEFAULT = "1.0.0"

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "combined_conditions",
        "confirmations": ["choch_sig", "consecutive_momentum_count"],
        "filter_f": "lag_1_atr_ratio",
        "family": StrategyFamily.HYBRID,
        "exit": {"mode": "trailing", "factor": 2.0},
    },
    {
        "logic": "htf_aligned_entry",
        "confirmations": ["htf_h4_trend", "htf_h1_momentum"],
        "filter_f": "dist_to_ema_50",
        "family": StrategyFamily.MULTI_TIMEFRAME,
        "exit": {"mode": "trailing", "factor": 1.8},
    },
    {
        "logic": "adaptive_break_or_reversion",
        "confirmations": ["breakout_sig", "extreme_sig"],
        "filter_f": "lag_1_atr_ratio",
        "family": StrategyFamily.HYBRID,
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
    {
        "logic": "liquidity_and_momentum",
        "confirmations": ["liquidity_sweep_signal", "consecutive_momentum_count"],
        "filter_f": "liquidity_sweep_state",
        "family": StrategyFamily.HYBRID,
        "exit": {"mode": "fixed_rr", "rr": 2.5},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt = spec["filter_f"]
        if filt == "liquidity_sweep_state":
            filters = [{"feature": filt, "op": "eq", "value": 1.0}]
        elif filt == "dist_to_ema_50":
            filters = [
                {
                    "feature": filt,
                    "op": "gt" if i % 2 == 0 else "lt",
                    "value": round(0.0 + (i % 5) * 0.05 * (1 if i % 2 == 0 else -1), 2),
                }
            ]
        else:
            filters = [
                {
                    "feature": filt,
                    "op": "gt" if i % 2 == 0 else "lt",
                    "value": round(0.0 + (i % 6) * 0.04 * (1 if i % 2 == 0 else -0.3), 2),
                }
            ]
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
                name_prefix="Regime Adaptive",
                description=f"Regime-adaptive hybrid: {spec['logic']}",
                expected_regimes=["trending", "ranging", "expansion", "contraction"],
                unsupported_regimes=[],
            )
        )
    return out
