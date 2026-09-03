"""Momentum pack — persistence / continuation variants."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "momentum"
PACK_VERSION_DEFAULT = "1.0.0"

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "momentum_continuation",
        "confirmations": ["consecutive_momentum_count", "tk_cross_signal"],
        "filter_f": "lag_1_log_return",
        "family": StrategyFamily.MOMENTUM,
        "exit": {"mode": "chandelier", "factor": 3.0},
    },
    {
        "logic": "momentum_continuation",
        "confirmations": ["norm_displacement", "lag_2_log_return"],
        "filter_f": "norm_displacement",
        "family": StrategyFamily.MOMENTUM,
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
    {
        "logic": "combined_conditions",
        "confirmations": ["choch_sig", "consecutive_momentum_count"],
        "filter_f": "lag_1_atr_ratio",
        "family": StrategyFamily.HYBRID,
        "exit": {"mode": "trailing", "factor": 2.0},
    },
    {
        "logic": "htf_aligned_entry",
        "confirmations": ["htf_h1_momentum", "htf_m15_confirmation"],
        "filter_f": "htf_h1_momentum",
        "family": StrategyFamily.MULTI_TIMEFRAME,
        "exit": {"mode": "trailing", "factor": 1.8},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt = spec["filter_f"]
        if filt == "htf_h1_momentum":
            filters = [{"feature": filt, "op": "gt", "value": round(0.0 + (i % 5) * 0.05, 2)}]
        elif filt == "norm_displacement":
            filters = [{"feature": filt, "op": "gt", "value": round(0.1 + (i % 6) * 0.05, 2)}]
        else:
            filters = [
                {
                    "feature": filt,
                    "op": "gt" if i % 2 == 0 else "lt",
                    "value": round(0.0 + (i % 5) * 0.04 * (1 if i % 2 == 0 else -1), 2),
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
                name_prefix="Momentum",
                description=f"Momentum {spec['logic']} variant",
                expected_regimes=["trending", "expansion"],
                unsupported_regimes=["ranging"],
            )
        )
    return out
