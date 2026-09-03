"""Liquidity pack — sweep / stop-hunt semantics."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "liquidity"
PACK_VERSION_DEFAULT = "1.0.0"

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "liquidity_sweep_reversal",
        "confirmations": ["liquidity_sweep_signal", "stop_hunt_depth"],
        "filter_f": "liquidity_sweep_state",
        "family": StrategyFamily.LIQUIDITY_SWEEP,
        "exit": {"mode": "target", "rr": 2.5},
    },
    {
        "logic": "liquidity_sweep_reversal",
        "confirmations": ["stop_hunt_depth", "post_sweep_displacement"],
        "filter_f": "stop_hunt_depth",
        "family": StrategyFamily.LIQUIDITY_SWEEP,
        "exit": {"mode": "target", "rr": 2.5},
    },
    {
        "logic": "feature_combination",
        "confirmations": ["bsl_distance_atr", "ssl_distance_atr"],
        "filter_f": "liquidity_sweep_state",
        "family": StrategyFamily.LIQUIDITY_SWEEP,
        "exit": {"mode": "target", "rr": 2.0},
    },
    {
        "logic": "liquidity_sweep_reversal",
        "confirmations": ["external_liquidity_distance", "liquidity_confluence"],
        "filter_f": "external_liquidity_distance",
        "family": StrategyFamily.LIQUIDITY_SWEEP,
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt = spec["filter_f"]
        if filt == "liquidity_sweep_state":
            filters = [{"feature": filt, "op": "eq", "value": 1.0}]
        elif filt == "stop_hunt_depth":
            filters = [{"feature": filt, "op": "gt", "value": round(0.6 + (i % 5) * 0.08, 2)}]
        else:
            filters = [{"feature": filt, "op": "gt", "value": round(0.2 + (i % 5) * 0.06, 2)}]
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
                name_prefix="Liquidity",
                description=f"Liquidity {spec['logic']} variant",
                expected_regimes=["liquidity_sweep", "reversal", "expansion"],
                unsupported_regimes=[],
            )
        )
    return out
