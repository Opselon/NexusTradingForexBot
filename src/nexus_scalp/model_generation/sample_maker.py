"""Sample Maker v2 — Hunter-Quality Sample Construction (PHASE 15D).

Upgrades the Phase 13 SampleFactory pipeline with the hunter layer:

    RAW BAR (with 50D features)
        -> SetupDetector (12+ setups, quality score)
        -> StrategyFactory (entry decision GO/NO_GO + RR + direction)
        -> quality-tiered label: TIER_A / TIER_B / TIER_C / NO_TRADE
        -> SampleContract (richer price_context + hunter metadata)

Key idea (accuracy): the model only sees SAMPLES where a qualified setup +
strategy agree (hunter filter). NO_TRADE samples carry NO setup context (they
are pure noise rows the model learns to reject). This gives the model a sharp
"hunter" decision boundary instead of learning from every chaotic bar.

Labels remain 3-class (NO_TRADE / BUY / SELL) for the neural contract; the
setup quality + strategy decision are stored as metadata/features so the model
can condition on them and the validation layer can audit per-setup accuracy.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from nexus_scalp.model_generation.setup_detector import SetupDetector
from nexus_scalp.model_generation.strategy_factory import (
    StrategyFactory,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.sample_maker")

#: Absolute quality floors — a setup must clear the HUNTER floor to exist at
#: all; tiers are then assigned RELATIVE to the recent quality distribution
#: (a hunter only takes the best shots the market offers this week).
HUNTER_MIN_QUALITY: float = 0.55
TIER_A_PCT: float = 0.10  # top 10% of recent setups
TIER_B_PCT: float = 0.35  # next 25% (top 10-35%)
TIER_C_MIN: float = 0.55  # absolute floor for any tradeable setup


def quality_tier(quality: float, percentile: float | None = None) -> str:
    """Maps a quality score to a tier.

    When ``percentile`` (0..1 rank within the recent distribution) is given,
    tiers are RELATIVE: TIER_A = top 10%, TIER_B = top 10-35%. When omitted,
    falls back to the absolute floors (TIER_A >= 0.80, TIER_B >= 0.70,
    TIER_C >= 0.55).
    """
    if percentile is not None:
        # percentile: 0 = worst, 1 = elite (fraction of pool below this quality)
        if percentile >= 1.0 - TIER_A_PCT:
            return "TIER_A"
        if percentile >= 1.0 - TIER_B_PCT:
            return "TIER_B"
        return "TIER_C"
    if quality >= TIER_A_MIN:
        return "TIER_A"
    if quality >= TIER_B_MIN:
        return "TIER_B"
    if quality >= TIER_C_MIN:
        return "TIER_C"
    return "NO_TRADE"


# Legacy absolute floors (kept for the absolute-tier path above).
TIER_A_MIN = 0.80
TIER_B_MIN = 0.70


class HunterSampleMaker:
    """Builds hunter-quality samples from a labeled feature frame."""

    def __init__(
        self,
        detector: SetupDetector | None = None,
        strategy_factory: StrategyFactory | None = None,
        default_strategy: str | None = None,
    ) -> None:
        self.detector = detector or SetupDetector()
        self.strategy_factory = strategy_factory or StrategyFactory()
        self.default_strategy = default_strategy

    # ------------------------------------------------------------------
    # Per-row hunter analysis
    # ------------------------------------------------------------------

    def analyze_row(
        self,
        row: dict[str, Any],
        timestamp: Any = None,
        quality_reference: list[float] | None = None,
    ) -> dict[str, Any]:
        """Runs the full hunter pipeline for one row.

        ``quality_reference`` is an optional list of recent setup qualities used
        for RELATIVE tiering (percentile rank). When omitted, absolute tier
        floors apply.

        Returns a dict: {setup, strategy, decision, tier, quality, direction,
        stop_distance, tp_distance, reasons, factors} — all None-safe.
        """
        dets = self.detector.detect(row, timestamp)
        if not dets:
            return self._empty("NO_SETUP")

        setup = dets[0]  # best setup (highest quality)
        decision = self.strategy_factory.evaluate(setup, row, self.default_strategy)
        if quality_reference:
            import bisect

            sorted_ref = sorted(quality_reference)
            n = len(sorted_ref)
            # percentile = fraction of the reference STRICTLY BELOW this quality
            # (0 = worst, 1 = elite/top of the pool).
            below = bisect.bisect_left(sorted_ref, setup.quality)
            percentile = below / n if n else 0.0
            tier = quality_tier(setup.quality, percentile=percentile)
        else:
            tier = quality_tier(setup.quality)

        return {
            "setup": setup,
            "setup_id": setup.setup_id,
            "setup_type": setup.setup_type,
            "quality": setup.quality,
            "tier": tier,
            "strategy_id": decision.strategy_id,
            "decision": decision.decision,
            "direction": decision.direction,
            "stop_distance": decision.stop_distance,
            "tp_distance": decision.tp_distance,
            "reasons": decision.reasons,
            "risk_fraction": decision.risk_fraction,
            "factors": setup.factors,
        }

    @staticmethod
    def _empty(reason: str) -> dict[str, Any]:
        return {
            "setup": None,
            "setup_id": "",
            "setup_type": "",
            "quality": 0.0,
            "tier": "NO_TRADE",
            "strategy_id": "",
            "decision": "NO_GO",
            "direction": None,
            "stop_distance": None,
            "tp_distance": None,
            "reasons": (reason,),
            "risk_fraction": 0.0,
            "factors": {},
        }

    # ------------------------------------------------------------------
    # Frame-level builder (polars)
    # ------------------------------------------------------------------

    def build_hunter_frame(
        self,
        df: pl.DataFrame,
    ) -> pl.DataFrame:
        """Adds hunter columns to the labeled frame:
            setup_id, setup_type, setup_quality, setup_tier, strategy_id,
            entry_decision, direction, stop_distance, tp_distance, reasons_json
        Pure + causal (row-local). Returns a NEW frame (input untouched).
        """
        if df.is_empty():
            return df.with_columns(
                [
                    pl.lit("").alias("setup_id"),
                    pl.lit("").alias("setup_type"),
                    pl.lit(0.0).alias("setup_quality"),
                    pl.lit("NO_TRADE").alias("setup_tier"),
                    pl.lit("").alias("strategy_id"),
                    pl.lit("NO_GO").alias("entry_decision"),
                    pl.lit(None, dtype=pl.Utf8).alias("direction_out"),
                    pl.lit(None, dtype=pl.Float64).alias("stop_distance"),
                    pl.lit(None, dtype=pl.Float64).alias("tp_distance"),
                    pl.lit("").alias("entry_reasons"),
                ]
            )

        import json

        rows = df.to_dicts()
        out: list[dict[str, Any]] = []
        for row in rows:
            ts = row.get("timestamp") or row.get("time")
            hunter = self.analyze_row(row, ts)
            out.append(
                {
                    **row,
                    "setup_id": hunter["setup_id"],
                    "setup_type": hunter["setup_type"],
                    "setup_quality": round(hunter["quality"], 4),
                    "setup_tier": hunter["tier"],
                    "strategy_id": hunter["strategy_id"],
                    "entry_decision": hunter["decision"],
                    "direction_out": hunter["direction"],
                    "stop_distance": hunter["stop_distance"],
                    "tp_distance": hunter["tp_distance"],
                    "entry_reasons": json.dumps(list(hunter["reasons"])),
                }
            )
        return pl.DataFrame(out)

    # ------------------------------------------------------------------
    # Sample-tightening: only keep rows a hunter would trade
    # ------------------------------------------------------------------

    def hunter_gate_frame(
        self,
        df: pl.DataFrame,
        min_tier: str = "TIER_C",
        require_go: bool = True,
    ) -> pl.DataFrame:
        """Filters a hunter frame to TRADEABLE rows only.

        Default: only rows with a setup tier >= TIER_C AND a GO entry
        decision survive; everything else becomes NO_TRADE context rows.
        Returns the filtered frame.
        """
        if df.is_empty() or "setup_tier" not in df.columns:
            return df
        tier_order = {"NO_TRADE": 0, "TIER_C": 1, "TIER_B": 2, "TIER_A": 3}
        min_t = tier_order.get(min_tier, 1)

        cond = pl.col("setup_tier").is_in([t for t, v in tier_order.items() if v >= min_t])
        if require_go:
            cond = cond & (pl.col("entry_decision") == "GO")
        return df.filter(cond)


#: Compatibility: the SampleFactory can use this to produce hunter metadata rows.
def attach_hunter_metadata(
    row: dict[str, Any],
    hunter: dict[str, Any],
) -> dict[str, Any]:
    """Merges hunter analysis into a sample's metadata dict."""
    m = dict(row.get("metadata", {}))
    m["setup_type"] = hunter.get("setup_type", "")
    m["setup_quality"] = hunter.get("quality", 0.0)
    m["setup_tier"] = hunter.get("tier", "NO_TRADE")
    m["hunter_strategy_id"] = hunter.get("strategy_id", "")
    m["entry_decision"] = hunter.get("decision", "NO_GO")
    m["direction"] = hunter.get("direction")
    m["stop_distance"] = hunter.get("stop_distance")
    m["tp_distance"] = hunter.get("tp_distance")
    m["entry_reasons"] = hunter.get("reasons", ())
    return m
