"""Price Action pack (CHG-0056) — deterministic over catalog ids + templates."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "price_action"
PACK_VERSION_DEFAULT = "1.0.0"
# draw features from bar-shape / structure slots
_FEATURES = [
    "pinbar_sig",
    "engulfing_sig",
    "body_to_range_ratio",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "close_location_value",
    "choch_sig",
]

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "pullback_in_trend",
        "confirmations": ["pinbar_sig", "choch_sig"],
        "filter_f": "body_to_range_ratio",
        "family": StrategyFamily.TREND_FOLLOWING,
        "exit": {"mode": "trailing", "factor": 2.0},
        "tf": "M15",
    },
    {
        "logic": "exhaustion_reversal",
        "confirmations": ["pinbar_sig", "upper_wick_ratio"],
        "filter_f": "upper_wick_ratio",
        "family": StrategyFamily.REVERSAL,
        "exit": {"mode": "target", "rr": 2.0},
        "tf": "M15",
    },
    {
        "logic": "feature_combination",
        "confirmations": ["engulfing_sig", "close_location_value"],
        "filter_f": "close_location_value",
        "family": StrategyFamily.MOMENTUM,
        "exit": {"mode": "fixed_rr", "rr": 2.5},
        "tf": "H1",
    },
    {
        "logic": "feature_combination",
        "confirmations": ["pinbar_sig", "lower_wick_ratio"],
        "filter_f": "lower_wick_ratio",
        "family": StrategyFamily.REVERSAL,
        "exit": {"mode": "target", "rr": 2.2},
        "tf": "M15",
    },
]

_OPS = ["gt", "lt"]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        # threshold diversity by index (deterministic)
        threshold = round(0.15 + (i % 7) * 0.05 * (1 if i % 2 == 0 else -1), 2)
        filt_value = (
            threshold if spec["filter_f"] != "upper_wick_ratio" else round(0.35 + (i % 5) * 0.05, 2)
        )
        filters = [{"feature": spec["filter_f"], "op": _OPS[i % 2], "value": filt_value}]
        # confirmations jitter across filter features for per-slot diversity
        confirmations = list(spec["confirmations"])
        if i >= len(_CANDIDATES):
            # cycle which bar feature anchors the confirmations
            confirmations[0] = _FEATURES[i % len(_FEATURES)]
        dsl = build_dsl(
            spec["family"],
            logic=spec["logic"],
            confirmations=confirmations,
            filters=filters,
            exit_spec=spec["exit"],
            timeframe=spec["tf"],
        )
        if not validate_dsl(dsl):
            # template-derived DSLs never fail structurally - adapter bug: skip rather than emit broken seed
            continue
        out.append(
            make_seed_spec(
                PACK_ID,
                version,
                i,
                dsl,
                name_prefix="Price Action",
                description=f"Price-action {spec['logic']} variant (filters={filters[0]['feature']})",
                expected_regimes=["trending"]
                if spec["family"] == StrategyFamily.TREND_FOLLOWING
                else ["ranging", "reversal"],
                unsupported_regimes=["contraction"]
                if spec["family"] == StrategyFamily.TREND_FOLLOWING
                else [],
            )
        )
    return out
