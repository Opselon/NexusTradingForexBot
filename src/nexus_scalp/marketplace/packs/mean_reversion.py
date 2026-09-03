"""Mean Reversion pack — overshoot reversion / squeeze reversion."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "mean_reversion"
PACK_VERSION_DEFAULT = "1.0.0"

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "overshoot_reversion",
        "confirmations": ["extreme_sig", "rapid_reversal_spike_val"],
        "filter_f": "norm_rsi",
        "family": StrategyFamily.MEAN_REVERSION,
        "exit": {"mode": "target", "rr": 2.0},
    },
    {
        "logic": "squeeze_reversion",
        "confirmations": ["price_compression_flag_ratio", "extreme_sig"],
        "filter_f": "price_compression_flag_ratio",
        "family": StrategyFamily.VOLATILITY_CONTRACTION,
        "exit": {"mode": "target", "rr": 2.0},
    },
    {
        "logic": "exhaustion_reversal",
        "confirmations": ["pinbar_sig", "norm_displacement"],
        "filter_f": "upper_wick_ratio",
        "family": StrategyFamily.REVERSAL,
        "exit": {"mode": "target", "rr": 2.0},
    },
    {
        "logic": "overshoot_reversion",
        "confirmations": ["rapid_reversal_spike_val", "norm_rsi"],
        "filter_f": "norm_rsi",
        "family": StrategyFamily.MEAN_REVERSION,
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt = spec["filter_f"]
        if filt == "norm_rsi":
            val = round(0.5 if i % 2 == 0 else -0.5, 2)
            filters = [{"feature": filt, "op": "gt" if i % 2 == 0 else "lt", "value": val}]
        elif filt == "upper_wick_ratio":
            filters = [{"feature": filt, "op": "gt", "value": round(0.35 + (i % 5) * 0.05, 2)}]
        else:
            filters = [{"feature": filt, "op": "gt", "value": round(0.5 + (i % 5) * 0.06, 2)}]
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
                name_prefix="Mean Reversion",
                description=f"Reversion {spec['logic']} variant",
                expected_regimes=["ranging", "contraction"],
                unsupported_regimes=["trending", "expansion"],
            )
        )
    return out
