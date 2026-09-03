"""Pack registry (CHG-0056, ARCH_SPEC §2 packs/__init__.py).

13 deterministic packs (price_action … regime_adaptive), each a function
generate(count=25, version="1.0.0") -> list[SeedSpec]. Same inputs => same
output. Every DSL is re-validated structurally before packaging.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nexus_scalp.marketplace.models import SeedSpec

PackGenerator = Callable[..., list[SeedSpec]]

REGISTRY: dict[str, dict[str, Any]] = {
    "price_action": {
        "id": "price_action",
        "name": "Price Action",
        "family": "PRICE_ACTION",
        "description": "Pure price-action entry variants: pullback, sweep reversal, doji/engulf confirmation over bar-shape filters.",
        "module": "nexus_scalp.marketplace.packs.price_action",
    },
    "ict": {
        "id": "ict",
        "name": "ICT / Smart Money (DSL approximation)",
        "family": "ICT",
        "description": "Inner-circle trader concepts as DSL features: order block, fair value gap, liquidity sweep / stop-hunt semantics.",
        "module": "nexus_scalp.marketplace.packs.ict",
    },
    "ichimoku": {
        "id": "ichimoku",
        "name": "Ichimoku (DSL approximation)",
        "family": "ICHIMOKU",
        "description": "Ichimoku approximation over trend/session DSL features. Real Ichimili code strategies are NOT replaced — this is a marketplace DSL family only.",
        "module": "nexus_scalp.marketplace.packs.ichimoku",
    },
    "market_structure": {
        "id": "market_structure",
        "name": "Market Structure",
        "family": "MARKET_STRUCTURE",
        "description": "BOS/CHoCH, HH/HL structure via momentum/displacement filters across HTF-aligned contexts.",
        "module": "nexus_scalp.marketplace.packs.market_structure",
    },
    "breakout": {
        "id": "breakout",
        "name": "Breakout",
        "family": "BREAKOUT",
        "description": "Range-break, compression-pop and ATR-expansion breakout variants with volume/displacement confirmation.",
        "module": "nexus_scalp.marketplace.packs.breakout",
    },
    "trend_following": {
        "id": "trend_following",
        "name": "Trend Following",
        "family": "TREND_FOLLOWING",
        "description": "Pullback-in-trend and H4-HTF-aligned pullbacks over the trend/logging category with regime filters.",
        "module": "nexus_scalp.marketplace.packs.trend_following",
    },
    "mean_reversion": {
        "id": "mean_reversion",
        "name": "Mean Reversion",
        "family": "MEAN_REVERSION",
        "description": "Overshoot reversion / squeeze reversion into a contraction / ranging market state.",
        "module": "nexus_scalp.marketplace.packs.mean_reversion",
    },
    "momentum": {
        "id": "momentum",
        "name": "Momentum",
        "family": "MOMENTUM",
        "description": "Momentum continuation and consecutive-bar persistence with displacement filters.",
        "module": "nexus_scalp.marketplace.packs.momentum",
    },
    "liquidity": {
        "id": "liquidity",
        "name": "Liquidity",
        "family": "LIQUIDITY_SWEEP",
        "description": "Liquidity-sweep reversal and stop-hunt depth variants over liquidity-sweep DSL signals.",
        "module": "nexus_scalp.marketplace.packs.liquidity",
    },
    "volume_vwap": {
        "id": "volume_vwap",
        "name": "Volume / VWAP",
        "family": "VOLUME_VWAP",
        "description": "Volume-z / VWAP-deviation and volume-implied liquidity variants built over vwap/volume family features.",
        "module": "nexus_scalp.marketplace.packs.volume_vwap",
    },
    "volatility": {
        "id": "volatility",
        "name": "Volatility",
        "family": "VOLATILITY_EXPANSION",
        "description": "ATR-expansion and ATR-contraction / squeeze-break variants over atr_ratio + volatility filters.",
        "module": "nexus_scalp.marketplace.packs.volatility",
    },
    "session": {
        "id": "session",
        "name": "Session / Time-of-Day",
        "family": "SESSION",
        "description": "Session-break, session-overlap and regime-by-session filter variants (London/NY/overlap).",
        "module": "nexus_scalp.marketplace.packs.session",
    },
    "regime_adaptive": {
        "id": "regime_adaptive",
        "name": "Regime Adaptive (Hybrid)",
        "family": "HYBRID",
        "description": "Composite multi-regime wrappers: CHoCH + momentum over stacked filters, HTF-aligned regime selectors.",
        "module": "nexus_scalp.marketplace.packs.regime_adaptive",
    },
}


def catalog() -> list[dict[str, Any]]:
    return [
        {"id": v["id"], "name": v["name"], "family": v["family"], "description": v["description"]}
        for v in REGISTRY.values()
    ]


def get_generator(pack_id: str) -> PackGenerator:
    import importlib

    mod_path = REGISTRY[pack_id]["module"]
    mod = importlib.import_module(mod_path)
    return mod.generate  # each pack exposes generate(...)


__all__ = ["REGISTRY", "catalog", "get_generator"]
