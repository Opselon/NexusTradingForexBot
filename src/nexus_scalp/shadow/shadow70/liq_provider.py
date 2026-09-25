"""70D Liquidity Producer Bridge (TASK-05-70D-SHADOW).

CONTRACT-FIRST (INV-70D-003): indices 60..69 are the Liquidity family. This
module resolves the canonical liquidity producer WITHOUT importing the
parallel series' WIP directly at module import time: the producer is looked
up lazily by attribute/function name and version-stamped, so the shadow
runtime is correct whether the producer exists yet (returns neutral 10D +
empty version) or has landed (returns the real causal 10D features).

The bridging rules (documented in docs/70D_SHADOW_RUNTIME.md):

1. If ``features.liquidity_engine.compute_liquidity_features`` exists and is
   importable, call it with the SAME completed-bar window the 50D engine
   consumed (causal, decision_at <= bar close), then map its 10 named
   features in the canonical order to the 10 slots.
2. Any producer fault is ISOLATED: the 70D shadow falls back to the neutral
   constant vector (all 0.0) + liquidity_calculation_version="unavailable",
   and the observation still records (news/base families stay real). The
   shadow runtime MUST NOT fail because liquidity is unavailable (spec 38).
"""

from __future__ import annotations

import math
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.shadow.shadow70.liq_provider")

#: Canonical order of the 10 liquidity features (POST_70D contract).
LIQUIDITY_SLOT_NAMES: tuple[str, ...] = (
    "bsl_distance_atr",
    "ssl_distance_atr",
    "eqh_strength",
    "eql_strength",
    "htf_liquidity_score",
    "internal_liquidity_distance",
    "external_liquidity_distance",
    "liquidity_confluence",
    "liquidity_sweep_state",
    "post_sweep_displacement",
)

LIQUIDITY_CALC_VERSION_CURRENT: str = "shadow70-v1"


def _neutral_10() -> list[float]:
    return [0.0] * 10


#: Freshness window for REUSING the governor's cached snapshot (seconds).
#: The governor recomputes on the bar-close cadence; reusing it keeps the
#: 70D shadow hook at plumbing-level cost instead of a full liquidity
#: engine rebuild (BUG-112: measured 42ms @200 bars .. 1163ms @4000 bars).
GOVERNOR_SNAPSHOT_MAX_AGE_SEC: float = 90.0

#: Fallback recompute history bound (same semantics as the dataset builder's
#: LIQUIDITY_HISTORY_LIMIT — bounded causal tail; never unbounded).
LIQUIDITY_FALLBACK_HISTORY_LIMIT: int = 4000


def _governor_snapshot(engine: Any) -> tuple[list[float] | None, str]:
    """Reuse the live governor's cached liquidity snapshot when fresh.

    Returns (features_10, version) or (None, "") when unavailable/stale.
    The governor is updated by live_engine on the bar-close cadence
    (LiquidityGovernor.compute_from_engine) — reading its last snapshot is
    O(1) and preserves EXACT train==live semantics (same producer, same
    inputs).
    """
    import time

    try:
        gov = getattr(engine, "liquidity_governor", None)
        if gov is None:
            return None, ""
        snap = gov.last_snapshot
        if snap is None:
            return None, ""
        age = (
            time.monotonic() - gov._last_success_at
            if getattr(gov, "_last_success_at", None) is not None
            else float("inf")
        )
        if age > GOVERNOR_SNAPSHOT_MAX_AGE_SEC:
            return None, ""
        vec = list(snap.features)
        if len(vec) != 10:
            return None, ""
        return vec, "liquidity_governor:v1"
    except Exception:
        return None, ""


def build_liquidity_10(engine: Any, tick: Any) -> tuple[list[float], str]:
    """Returns (10 liquidity features, calculation version).

    REUSE-FIRST (BUG-112): the live governor already computes the canonical
    10 liquidity values on the bar-close cadence. Reusing a fresh governor
    snapshot costs ~µs; the full engine rebuild costs 42ms..1163ms per call
    (measured @200..4000 bars) and would blow the 50ms shadow latency budget
    on EVERY tick. Falls back to a BOUNDED recompute only when no fresh
    governor snapshot exists (liquidity disabled / cold start).

    NEVER raises: any producer fault yields the neutral vector + version
    "unavailable" (isolated; the observe() call still succeeds).
    """
    try:
        cached, version = _governor_snapshot(engine)
        if cached is not None:
            return cached, version

        from nexus_scalp.features.liquidity_engine import (
            compute_liquidity_features,
        )

        bars = None
        aggr = getattr(engine, "aggregator", None)
        if aggr is not None:
            try:
                bars = aggr.get_completed_bars()
            except Exception:
                bars = None
        if not bars:
            return _neutral_10(), "unavailable"
        # BOUNDED causal tail (matches dataset-builder semantics) — never the
        # full unbounded history (BUG-112 hot-path protection).
        bars = bars[-LIQUIDITY_FALLBACK_HISTORY_LIMIT:]
        try:
            feats = compute_liquidity_features(bars, use_htf=True)
        except TypeError:
            feats = compute_liquidity_features(bars)
        vec = _extract_named(feats)
        if vec is None:
            return _neutral_10(), "unavailable"
        return vec, "liquidity_engine:" + str(getattr(feats, "version", "v1")).split(":")[-1]
    except Exception as e:
        logger.warning("[SHADOW70] liquidity producer unavailable (isolated)", error=str(e))
        return _neutral_10(), "unavailable"


def _extract_named(feats: Any) -> list[float] | None:
    """Maps the producer's named features into the canonical 10 slots.

    Accepts either an object with attributes matching LIQUIDITY_SLOT_NAMES
    or a dict. Returns None when the shape cannot be resolved.
    """
    try:
        if hasattr(feats, "as_vector"):
            raw = list(feats.as_vector())
            if len(raw) == 10:
                return _sanitize(raw)
            if len(raw) == 60:
                return _sanitize(raw[50:60])
        if isinstance(feats, dict):
            out: list[float] = []
            for name in LIQUIDITY_SLOT_NAMES:
                if name not in feats:
                    return None
                out.append(float(feats[name]))
            return _sanitize(out)
        out2: list[float] = []
        for name in LIQUIDITY_SLOT_NAMES:
            if not hasattr(feats, name):
                return None
            out2.append(float(getattr(feats, name)))
        return _sanitize(out2)
    except Exception:
        return None


def _sanitize(vec: list[float]) -> list[float]:
    out: list[float] = []
    for v in vec:
        if not math.isfinite(v):
            out.append(0.0)
        else:
            out.append(max(-3.0, min(3.0, float(v))))
    return out
