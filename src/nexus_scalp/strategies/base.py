"""
Strategy Framework — Base Contracts & Registry
================================================
PHASE 15C: seedable, testable trading strategies that plug into the
research/experience pipeline and can later be aligned with the AI model.

A `Strategy` is a PURE signal generator: it consumes completed bars and
produces directional signals (BUY / SELL / NONE) with an optional confidence.
It holds no adapter, no risk engine, and NEVER places orders — exactly like
the research layer's safety contract. Registration produces deterministic
`StrategyCandidate` objects (content-addressed versions) so the research
pipeline, Experience `StrategyContext`, and future AI-vs-strategy alignment
can all reference the same stable identities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from nexus_scalp.research.candidates import StrategyCandidate

#: Registry of all built-in strategies. Keys are stable strategy ids.
BUILTIN_STRATEGIES: dict[str, Strategy] = {}


class BarLike(Protocol):
    """Minimal OHLCV bar contract accepted by strategies."""

    timestamp: Any
    open: float
    high: float
    low: float
    close: float
    tick_volume: int


@dataclass(frozen=True)
class StrategySignal:
    """A single directional signal from a strategy over one bar."""

    strategy_id: str
    direction: str  # "BUY" | "SELL" | "NONE"
    bar_index: int
    timestamp: datetime | None = None
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class Strategy(Protocol):
    """Pure signal-generator protocol (no I/O, no order authority)."""

    strategy_id: str
    version: str
    display_name: str

    def evaluate(self, bars: list[BarLike]) -> list[StrategySignal]: ...

    def context_definition(self) -> dict[str, Any]: ...

    def entry_logic(self) -> dict[str, Any]: ...

    def exit_logic(self) -> dict[str, Any]: ...

    def risk_assumptions(self) -> dict[str, Any]: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def make_candidate(strategy: Strategy) -> StrategyCandidate:
    """Builds the deterministic, content-addressed candidate for a strategy."""
    candidate = StrategyCandidate(
        strategy_id=strategy.strategy_id,
        strategy_version="",
        context_definition=strategy.context_definition(),
        entry_logic=strategy.entry_logic(),
        exit_logic=strategy.exit_logic(),
        risk_assumptions=strategy.risk_assumptions(),
        parent_strategy_ids=[],
        discovery_method=f"builtin:{strategy.display_name.lower().replace(' ', '_')}",
        lifecycle="DISCOVERED",  # type: ignore[arg-type]
        discovery_evidence={
            "source": "builtin_seed",
            "definition": strategy.entry_logic(),
        },
    )
    return candidate.model_copy(update={"strategy_version": candidate.canonical_version()})


def register_strategy(strategy: Strategy) -> Strategy:
    """Registers a built-in strategy so `builtin_candidates()` can seed research."""
    BUILTIN_STRATEGIES[strategy.strategy_id] = strategy
    return strategy


def builtin_candidates() -> list[StrategyCandidate]:
    """All registered built-in strategies as deterministic candidates."""
    return [make_candidate(s) for s in BUILTIN_STRATEGIES.values()]


def _bars_to_lists(
    bars: list[BarLike],
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Extracts OHLC lists from BarLike objects (never mutates)."""
    return (
        [float(b.open) for b in bars],
        [float(b.high) for b in bars],
        [float(b.low) for b in bars],
        [float(b.close) for b in bars],
    )


def donchian_mid(highs: list[float], lows: list[float], length: int, end: int) -> float:
    """Donchian midpoint over the trailing `length` bars ending at `end`."""
    lo = max(0, end - length + 1)
    window_highs = highs[lo : end + 1]
    window_lows = lows[lo : end + 1]
    if not window_highs:
        return 0.0
    return (max(window_highs) + min(window_lows)) / 2.0
