"""Market Structure pack — BOS / CHoCH / structure."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "market_structure"
PACK_VERSION_DEFAULT = "1.0.0"

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "choch_continuation",
        "confirmations": ["choch_sig", "htf_h4_trend"],
        "filter_f": "dist_to_swing_high_20",
        "family": StrategyFamily.TREND_FOLLOWING,
        "exit": {"mode": "trailing", "factor": 2.0},
    },
    {
        "logic": "bos_failed_break",
        "confirmations": ["choch_sig", "feat_ob_valid_bos"],
        "filter_f": "feat_ob_valid_bos",
        "family": StrategyFamily.REVERSAL,
        "exit": {"mode": "target", "rr": 2.0},
    },
    {
        "logic": "htf_aligned_entry",
        "confirmations": ["htf_h4_trend", "htf_h1_momentum"],
        "filter_f": "htf_m30_structure",
        "family": StrategyFamily.MULTI_TIMEFRAME,
        "exit": {"mode": "trailing", "factor": 1.8},
    },
    {
        "logic": "structure_sweep",
        "confirmations": ["choch_sig", "dist_to_swing_low_20"],
        "filter_f": "support_zone_dist",
        "family": StrategyFamily.LIQUIDITY_SWEEP,
        "exit": {"mode": "target", "rr": 2.5},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt = spec["filter_f"]
        if filt == "feat_ob_valid_bos":
            filters = [{"feature": filt, "op": "eq", "value": 1.0}]
        elif filt == "htf_m30_structure":
            filters = [{"feature": filt, "op": "gt", "value": round(0.1 + (i % 5) * 0.05, 2)}]
        else:
            filters = [
                {
                    "feature": filt,
                    "op": "gt" if i % 2 == 0 else "lt",
                    "value": round(0.05 + (i % 6) * 0.04 * (1 if i % 2 == 0 else -1), 2),
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
                name_prefix="Market Structure",
                description=f"Structure {spec['logic']} variant",
                expected_regimes=["trending", "expansion"],
                unsupported_regimes=[],
            )
        )
    return out
