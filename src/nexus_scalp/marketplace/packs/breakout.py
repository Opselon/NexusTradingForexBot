"""Breakout pack — range/compression/ATR break variants."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "breakout"
PACK_VERSION_DEFAULT = "1.0.0"

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "range_break",
        "confirmations": ["breakout_sig", "lag_1_volume_z"],
        "filter_f": "lag_1_atr_ratio",
        "family": StrategyFamily.BREAKOUT,
        "exit": {"mode": "fixed_rr", "rr": 2.5},
    },
    {
        "logic": "squeeze_break",
        "confirmations": ["price_compression_flag_ratio", "breakout_sig"],
        "filter_f": "price_compression_flag_ratio",
        "family": StrategyFamily.VOLATILITY_CONTRACTION,
        "exit": {"mode": "target", "rr": 2.0},
    },
    {
        "logic": "volatility_expansion_break",
        "confirmations": ["norm_displacement", "breakout_sig"],
        "filter_f": "lag_1_atr_ratio",
        "family": StrategyFamily.VOLATILITY_EXPANSION,
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
    {
        "logic": "structure_bos_break",
        "confirmations": ["choch_sig", "breakout_sig"],
        "filter_f": "feat_ob_valid_bos",
        "family": StrategyFamily.BREAKOUT,
        "exit": {"mode": "trailing", "factor": 1.8},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt = spec["filter_f"]
        if filt == "feat_ob_valid_bos":
            filters = [{"feature": filt, "op": "eq", "value": 1.0}]
        elif filt == "price_compression_flag_ratio":
            filters = [{"feature": filt, "op": "gt", "value": round(0.5 + (i % 5) * 0.06, 2)}]
        else:
            filters = [
                {
                    "feature": filt,
                    "op": "gt",
                    "value": round(0.0 + (i % 6) * 0.04 * (1 if i % 3 < 2 else -0.5), 2),
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
                name_prefix="Breakout",
                description=f"Breakout {spec['logic']} variant",
                expected_regimes=["expansion", "trending"],
                unsupported_regimes=["ranging"],
            )
        )
    return out
