"""Session / time-of-day pack — session breaks / overlap variants."""

from __future__ import annotations

from typing import Any

from nexus_scalp.marketplace.models import SeedSpec
from nexus_scalp.marketplace.packs._common import make_seed_spec
from nexus_scalp.marketplace.packs._template import build_dsl, validate_dsl
from nexus_scalp.strategies.factory.models import StrategyFamily

PACK_ID = "session"
PACK_VERSION_DEFAULT = "1.0.0"

_CANDIDATES: list[dict[str, Any]] = [
    {
        "logic": "session_break",
        "confirmations": ["session_london", "session_ny"],
        "filter_f": "session_overlap_london_ny",
        "family": StrategyFamily.SESSION,
        "exit": {"mode": "fixed_rr", "rr": 2.0},
    },
    {
        "logic": "session_break",
        "confirmations": ["session_tokyo", "choch_sig"],
        "filter_f": "session_tokyo",
        "family": StrategyFamily.SESSION,
        "exit": {"mode": "target", "rr": 2.0},
    },
    {
        "logic": "pullback_in_trend",
        "confirmations": ["choch_sig", "session_london"],
        "filter_f": "dist_to_ema_21",
        "family": StrategyFamily.TREND_FOLLOWING,
        "exit": {"mode": "trailing", "factor": 2.0},
    },
    {
        "logic": "liquidity_sweep_reversal",
        "confirmations": ["liquidity_sweep_signal", "session_london"],
        "filter_f": "liquidity_sweep_state",
        "family": StrategyFamily.LIQUIDITY_SWEEP,
        "exit": {"mode": "target", "rr": 2.5},
    },
]


def generate(count: int = 25, version: str = PACK_VERSION_DEFAULT) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for i in range(count):
        spec = _CANDIDATES[i % len(_CANDIDATES)]
        filt = spec["filter_f"]
        if filt == "session_overlap_london_ny":
            filters = [{"feature": filt, "op": "eq", "value": 1.0}]
        elif filt in ("session_tokyo", "liquidity_sweep_state"):
            filters = [{"feature": filt, "op": "eq", "value": 1.0}]
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
                name_prefix="Session",
                description=f"Session {spec['logic']} variant",
                expected_regimes=["session_bound", "trending"],
                unsupported_regimes=[],
            )
        )
    return out
