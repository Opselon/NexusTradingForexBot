"""ICT pack — DSL approximation (order block / FVG / liquidity-sweep semantics)."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "ict"
PACK_VERSION_DEFAULT = "1.0.0"
# order-block / fvg / liquidity sweep feature surface
_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "liquidity_sweep_reversal",
        "confirmations": ["order_block_type", "fvg_sig"],
        "filter_f": "order_block_type",
        "family": StrategyFamily.LIQUIDITY_SWEEP,
        "exit": {"mode": "target", "rr": 2.5},
    },
    {
        "logic": "feature_combination",
        "confirmations": ["feat_ob_valid_bos", "feat_ob_equilibrium_ratio"],
        "filter_f": "feat_ob_valid_bos",
        "family": StrategyFamily.LIQUIDITY_SWEEP,
        "exit": {"mode": "target", "rr": 2.0},
    },
    {
        "logic": "feature_combination",
        "confirmations": ["fvg_sig", "internal_liquidity_distance"],
        "filter_f": "feat_ob_fib_50_60_alignment",
        "family": StrategyFamily.HYBRID,
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
    {
        "logic": "liquidity_sweep_reversal",
        "confirmations": ["liquidity_sweep_signal", "feat_ob_liquidity_swept"],
        "filter_f": "liquidity_sweep_state",
        "family": StrategyFamily.LIQUIDITY_SWEEP,
        "exit": {"mode": "target", "rr": 2.5},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt_value = round(
            0.5
            if spec["filter_f"] in ("order_block_type", "feat_ob_valid_bos")
            else 0.2 + (i % 5) * 0.06,
            2,
        )
        op = "eq" if spec["filter_f"] in ("order_block_type",) else "gt"
        # order_block_type is categorical; use equality at 1.0 when filtered
        filters = [
            {"feature": spec["filter_f"], "op": op, "value": 1.0 if op == "eq" else filt_value}
        ]
        confirmations = list(spec["confirmations"])
        dsl = build_dsl(
            spec["family"],
            logic=spec["logic"],
            confirmations=confirmations,
            filters=filters,
            exit_spec=spec["exit"],
            timeframe="M15" if i % 2 == 0 else "M30",
        )
        if not validate_dsl(dsl):
            continue
        out.append(
            make_seed_spec(
                PACK_ID,
                version,
                i,
                dsl,
                name_prefix="ICT",
                description=f"ICT {spec['logic']} variant",
                expected_regimes=["liquidity_sweep", "reversal"],
                unsupported_regimes=[],
            )
        )
    return out
