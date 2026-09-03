"""Volume / VWAP pack — volume-z, VWAP deviation, volume-implied liquidity."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "volume_vwap"
PACK_VERSION_DEFAULT = "1.0.0"

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "range_break",
        "confirmations": ["lag_1_volume_z", "breakout_sig"],
        "filter_f": "lag_1_volume_z",
        "family": StrategyFamily.BREAKOUT,
        "exit": {"mode": "fixed_rr", "rr": 2.5},
    },
    {
        "logic": "overshoot_reversion",
        "confirmations": ["lag_1_clv", "lag_1_volume_z"],
        "filter_f": "lag_1_clv",
        "family": StrategyFamily.MEAN_REVERSION,
        "exit": {"mode": "target", "rr": 2.0},
    },
    {
        "logic": "feature_combination",
        "confirmations": ["lag_1_volume_z", "lag_1_atr_ratio"],
        "filter_f": "lag_1_volume_z",
        "family": StrategyFamily.HYBRID,
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
    {
        "logic": "feature_combination",
        "confirmations": ["lag_1_volume_z", "price_compression_flag_ratio"],
        "filter_f": "price_compression_flag_ratio",
        "family": StrategyFamily.VOLATILITY_CONTRACTION,
        "exit": {"mode": "target", "rr": 2.0},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt = spec["filter_f"]
        if filt == "lag_1_volume_z":
            filters = [
                {
                    "feature": filt,
                    "op": "gt" if i % 2 == 0 else "lt",
                    "value": round(1.0 + (i % 5) * 0.2 * (1 if i % 2 == 0 else -1), 2),
                }
            ]
        elif filt == "price_compression_flag_ratio":
            filters = [{"feature": filt, "op": "gt", "value": round(0.4 + (i % 5) * 0.06, 2)}]
        else:
            filters = [{"feature": filt, "op": "gt", "value": round(0.2 + (i % 5) * 0.05, 2)}]
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
                name_prefix="Volume / VWAP",
                description=f"Volume {spec['logic']} variant",
                expected_regimes=["trending", "expansion"],
                unsupported_regimes=[],
            )
        )
    return out
