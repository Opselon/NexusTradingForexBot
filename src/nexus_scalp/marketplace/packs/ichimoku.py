"""
Ichimoku pack — DSL approximation (documented, CHG-0056 ARCH_SPEC §2).

The Ichimoku DSL variants are trend/session DSL approximations (tk cross,
kumo position, tenkan/kijun distances) over the factory feature catalog.
Real code Ichimili strategies remain untouched; these seeds are the market-
place family only.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "ichimoku"
PACK_VERSION_DEFAULT = "1.0.0"

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "tk_cross_continuation",
        "confirmations": ["tk_cross_signal", "kumo_sig"],
        "filter_f": "norm_tk_diff",
        "family": StrategyFamily.TREND_FOLLOWING,
        "exit": {"mode": "trailing", "factor": 2.0},
    },
    {
        "logic": "tk_cross_reversal",
        "confirmations": ["tk_cross_signal", "norm_dist_to_kijun"],
        "filter_f": "norm_dist_to_kijun",
        "family": StrategyFamily.REVERSAL,
        "exit": {"mode": "target", "rr": 2.0},
    },
    {
        "logic": "kumo_breakout",
        "confirmations": ["kumo_sig", "norm_kumo_width"],
        "filter_f": "norm_dist_to_tenkan",
        "family": StrategyFamily.BREAKOUT,
        "exit": {"mode": "fixed_rr", "rr": 2.5},
    },
    {
        "logic": "kumo_support_reversion",
        "confirmations": ["kumo_sig", "dist_to_ema_21"],
        "filter_f": "norm_kumo_width",
        "family": StrategyFamily.MEAN_REVERSION,
        "exit": {"mode": "target", "rr": 2.0},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt_value = round(0.05 + (i % 7) * 0.03 * (1 if i % 2 == 0 else -1), 2)
        op = "gt" if i % 2 == 0 else "lt"
        filters = [{"feature": spec["filter_f"], "op": op, "value": filt_value}]
        dsl = build_dsl(
            spec["family"],
            logic=spec["logic"],
            confirmations=list(spec["confirmations"]),
            filters=filters,
            exit_spec=spec["exit"],
            timeframe="M15" if i % 3 < 2 else "H1",
        )
        if not validate_dsl(dsl):
            continue
        out.append(
            make_seed_spec(
                PACK_ID,
                version,
                i,
                dsl,
                name_prefix="Ichimoku",
                description=f"Ichimoku DSL approximation: {spec['logic']}",
                expected_regimes=["trending", "expansion"],
                unsupported_regimes=["ranging"],
            )
        )
    return out
