"""
Seedable Built-in Strategies
=============================
PHASE 15C: deterministic, testable, bar-based strategy engines that plug into
the research pipeline (deterministic candidates) and later the AI alignment.

Importing this package registers the built-in strategies, so
`from nexus_scalp.strategies import builtin_candidates()` yields the seeded
candidates for research/backtesting.
"""

from __future__ import annotations

# Import the strategy modules so their import-time registration runs.
from nexus_scalp.strategies import (
    base,  # noqa: F401
    ichimoku,  # noqa: F401
)
from nexus_scalp.strategies.base import (
    BUILTIN_STRATEGIES,
    BarLike,
    Strategy,
    StrategySignal,
    builtin_candidates,
    make_candidate,
    register_strategy,
)
from nexus_scalp.strategies.ichimoku import (
    STRATEGY_ID_FINAL,
    STRATEGY_ID_SPACED,
    IchimiliFinalStrategy,
    IchimiliSpacedStrategy,
)

__all__ = [
    "BUILTIN_STRATEGIES",
    "STRATEGY_ID_FINAL",
    "STRATEGY_ID_SPACED",
    "BarLike",
    "IchimiliFinalStrategy",
    "IchimiliSpacedStrategy",
    "Strategy",
    "StrategySignal",
    "builtin_candidates",
    "make_candidate",
    "register_strategy",
]
