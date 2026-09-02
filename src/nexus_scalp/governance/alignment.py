"""
Shadow Input Alignment & Same-State Parity
==========================================
TASK-6 / CHG-0003 (spec 5 / 6 / 7 / 8).

Guarantees what both models actually saw:

  1. Champion (50D) and Challenger (60D) receive EXACTLY the same market
     state at the same timestamp. The 60D Challenger's input is built by
     EXTENDING the Champion's 50D vector with the SAME canonical news
     context (the 10 reserved scalp_v2 slots stay zero, then 12 news
     fields), NEVER by truncating, reordering or padding the 50D.

  2. Every live comparison records feature parity (MAX_ABS_DIFF /
     MEAN_ABS_DIFF / MISMATCH_COUNT vs the offline/replay reference) and
     the canonical NEWS_CONTEXT_HASH.

  3. A parity failure invalidates the comparison — it is stored flagged,
     never used in promotion statistics.

  4. The Champion input is NEVER mutated: the challenger vector is a fresh
     list copy.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Any

from nexus_scalp.governance.models import ShadowParity
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.governance.alignment")

#: Feature schema contract (spec 5): NOTHING is silently truncated/padded.
ALLOWED_SCHEMA_IDS: tuple[str, ...] = ("scalp_v1", "scalp_v2", "scalp_v3")

#: Embedding contract for the 60D challenger (scalp_v2 = scalp_v1 + 10
#: reserved order-flow slots). The reserved slots are the LEADING 10 so the
#: first 50 features stay byte-identical to scalp_v1.
V2_RESERVED_SLOTS: int = 10

#: News context width appended after the reserved slots (scalp_v2 60D =
#: 50 + 10 reserved + 12 news when news_enabled).
NEWS_CONTEXT_DIM: int = 12


def sha256_json(payload: Any) -> str:
    """Deterministic content hash for canonical identity."""
    return hashlib.sha256(str(payload).encode("utf-8", errors="replace")).hexdigest()[:16]


def news_context_hash(context: dict[str, Any] | None) -> str:
    """Canonical NEWS_CONTEXT_HASH (spec 7): availability, relevance,
    direction, importance, freshness, consensus, state, decay,
    provenance — i.e. the whole snapshot, not just the vector."""
    if context is None:
        return sha256_json("no_news_context")
    keys = [
        "available",
        "state",
        "active_event_count",
        "xauusd_relevance",
        "usd_relevance",
        "bullish_score",
        "bearish_score",
        "confidence",
        "conflict_score",
        "freshness",
        "source_consensus",
        "stale",
        "active_high_impact",
        "timestamp",
    ]
    snapshot = {
        k: context.get(k) if isinstance(context, dict) else getattr(context, k, None) for k in keys
    }
    return sha256_json(snapshot)


def vectorize_news_context(context: dict[str, Any] | None) -> list[float]:
    """Maps the live CurrentNewsContext to the 12-field NewsContextSchema
    canonical order used by model_generation (spec 7 / 9). Zero vector when
    news is unavailable (A/B ablation semantics)."""
    if context is None:
        return [0.0] * NEWS_CONTEXT_DIM
    is_dict = isinstance(context, dict)

    def g(name: str, default: Any = 0.0) -> Any:
        return context.get(name, default) if is_dict else getattr(context, name, default)

    vla = g("bullish_score", 0.0)
    vle = g("bearish_score", 0.0)
    # news_state / novelty encodings mirror the model_generation bridge.
    state = str(g("news_state", g("state", "NORMAL"))).upper()

    def num(value: Any) -> float:
        """BUG-217 companion: producer value -> finite float, fail-closed.

        Numeric strings convert; non-numeric / non-finite (NaN/Inf) raise
        so the callers' documented try/except degrades to the zero vector.
        Never silently clips producer data to the family bounds — the bound
        repair applies ONLY to the static encoding tables below.
        """
        f = float(value)  # raises TypeError/ValueError on non-numeric
        if not math.isfinite(f):
            raise ValueError(f"non-finite news context value: {value!r}")
        return f

    vla = num(g("bullish_score", 0.0))
    vle = num(g("bearish_score", 0.0))
    state_enc = {
        "NORMAL": 0.0,
        "ELEVATED": 1.0,
        "HIGH_IMPACT": 2.0,
        "CONFLICTED": 3.0,
        "BREAKING": 4.0,
        "STALE": 5.0,
    }.get(state, 0.0)
    novelty = str(g("novelty", "NEW")).upper()
    novelty_enc = {
        "NEW": 0.0,
        "UPDATED": 1.0,
        "CONFIRMATION": 2.0,
        "REPETITION": 3.0,
        "STALE": 4.0,
    }.get(novelty, 0.0)
    # BUG-197: live CurrentNewsContext.active_event_count is an AGGREGATE
    # count while the training frame encodes a per-event 0/1 flag (max
    # 1.0). A raw count left the 70D bounds [-3,+3] and validate_70d_vector
    # blocked ALL live 70D inference whenever >=4 high-impact events were
    # active (client permanently STALE). Encode the bounded flag at the
    # training distribution maximum instead.
    _active_raw = num(g("active_event_count", g("active_high_impact_events", 0)))
    active = 1.0 if _active_raw >= 1.0 else max(0.0, _active_raw)
    # BUG-217: the state/novelty ordinal tables exceed the 70D NEWS-family
    # bound (BREAKING=4.0, STALE=5.0 > 3.0). The dataset builder passes the
    # family through clamp_neutral_family (neutral 0.0) BEFORE the contract
    # boundary, so training rows carry encodings clamped to 3.0; the live
    # projection must produce the SAME in-distribution value or
    # validate_70d_vector blocks all 70D inference the moment NewsState
    # reaches BREAKING/STALE (same client-stale class as BUG-197). The bound
    # is applied to the ENCODING TABLE (a static, defined transform), never
    # to arbitrary producer data — this mirrors training semantics, it does
    # not silently clip a bad producer value.
    _news_family_max = 3.0
    state_enc = min(state_enc, _news_family_max)
    novelty_enc = min(novelty_enc, _news_family_max)
    return [
        active,
        num(g("xauusd_relevance", 0.0)),
        num(g("usd_relevance", 0.0)),
        vla,
        vle,
        num(g("conflict_score", 0.0)),
        novelty_enc,
        num(g("freshness", 0.0)),
        num(g("confidence", 0.0)),
        num(g("source_consensus", 0.0)),
        state_enc,
        num(g("time_since_event_sec", 0.0)),
    ]


def challenger_input_for(
    champion_vector: list[float],
    *,
    champion_schema_id: str = "scalp_v1",
    challenger_schema_id: str,
    challenger_dimension: int,
    news_context: dict[str, Any] | None = None,
    extras_60d: list[float] | None = None,
) -> tuple[list[float], str]:
    """Builds the Challenger input from the Champion's live vector.

    Returns (vector, alignment) where alignment is:
        IDENTICAL      - same schema/dimension, byte-equal copy
        NEWS_EXTENDED  - 50D + 10 scalp_v2 extras (+ 12 news when the
                         model's neural width is 72)

    ``challenger_dimension`` is the manifest's NEURAL input width
    (`build_metadata.input_dimension`): for scalp_v2 this is 60 (50D + 10
    real extras, news disabled) or 72 (50D + 10 extras + 12 news).

    ``extras_60d`` MUST be supplied for a scalp_v2 challenger: the 10 extras
    are REAL features computed from the same causal bar window
    (features.schema_augment.compute_60d_extras, TASK-5 contract), never
    zeroed (zero-fill would feed the model a distribution it never trained
    on).
    """
    if not champion_vector:
        raise ValueError("champion vector empty")
    if (
        champion_schema_id not in ALLOWED_SCHEMA_IDS
        or challenger_schema_id not in ALLOWED_SCHEMA_IDS
    ):
        raise ValueError(
            f"unregistered schema in alignment: {champion_schema_id}/{challenger_schema_id}"
        )

    if challenger_schema_id == champion_schema_id:
        if int(challenger_dimension) != len(champion_vector):
            raise ValueError(
                f"schema {challenger_schema_id} dimension {challenger_dimension} != "
                f"champion vector width {len(champion_vector)}"
            )
        return list(champion_vector), "IDENTICAL"

    if challenger_schema_id == "scalp_v2":
        expected = int(challenger_dimension)
        if expected == 50 + V2_RESERVED_SLOTS + NEWS_CONTEXT_DIM:
            news = vectorize_news_context(news_context)
        elif expected == 50 + V2_RESERVED_SLOTS:
            news = []
        else:
            raise ValueError(
                f"scalp_v2 challenger declares unsupported input width {expected} "
                "(expected 60 news-disabled or 72 news-enabled)"
            )
        if extras_60d is None or len(extras_60d) != V2_RESERVED_SLOTS:
            raise ValueError(
                "scalp_v2 challenger requires the 10 live 60D extras "
                "(compute_60d_extras); refusing to zero-fill"
            )
        v = list(champion_vector) + list(extras_60d) + news
        if len(v) != expected:
            raise ValueError(
                f"scalp_v2 challenger built {len(v)}D but manifest declares {expected}D - "
                "refusing silent reshape"
            )
        return v, "NEWS_EXTENDED"

    raise ValueError(
        f"no documented compatibility path: {champion_schema_id} -> {challenger_schema_id}"
    )


def feature_parity(
    live_vector: list[float],
    reference_vector: list[float] | None,
    *,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """MAX_ABS_DIFF / MEAN_ABS_DIFF / MISMATCH_COUNT (spec 6).

    When no reference is available the parity is UNKNOWN (never assumed OK).
    """
    if reference_vector is None:
        return {
            "max_abs_diff": -1.0,
            "mean_abs_diff": -1.0,
            "mismatch_count": -1,
            "parity_ok": False,
            "state": "UNKNOWN",
        }
    n = min(len(live_vector), len(reference_vector))
    if n == 0:
        return {
            "parity_ok": False,
            "state": "EMPTY",
            "max_abs_diff": -1.0,
            "mean_abs_diff": -1.0,
            "mismatch_count": -1,
        }
    diffs = [abs(float(live_vector[i]) - float(reference_vector[i])) for i in range(n)]
    mismatches = sum(1 for d in diffs if d > tolerance)
    return {
        "max_abs_diff": max(diffs) if diffs else 0.0,
        "mean_abs_diff": sum(diffs) / len(diffs) if diffs else 0.0,
        "mismatch_count": mismatches,
        "parity_ok": mismatches == 0 and len(live_vector) == len(reference_vector),
        "state": "OK"
        if mismatches == 0 and len(live_vector) == len(reference_vector)
        else "MISMATCH",
    }


def build_shadow_parity(
    *,
    comparison_id: str,
    timestamp: datetime,
    feature_context_id: str,
    news_context_id: str,
    feature_schema_id: str,
    champion_input_dim: int,
    challenger_input_dim: int,
    parity: dict[str, Any],
    alignment: str,
    latency_champion_ms: float,
    latency_challenger_ms: float,
) -> ShadowParity:
    ok = bool(parity.get("parity_ok", False)) or parity.get("state") == "UNKNOWN"
    return ShadowParity(
        comparison_id=comparison_id,
        timestamp=timestamp,
        feature_context_id=feature_context_id,
        news_context_id=news_context_id,
        feature_schema_id=feature_schema_id,
        champion_input_dim=champion_input_dim,
        challenger_input_dim=challenger_input_dim,
        max_abs_diff=float(parity.get("max_abs_diff", -1.0)),
        mean_abs_diff=float(parity.get("mean_abs_diff", -1.0)),
        mismatch_count=int(parity.get("mismatch_count", -1)),
        parity_ok=ok,
        alignment=alignment,
        latency_champion_ms=latency_champion_ms,
        latency_challenger_ms=latency_challenger_ms,
    )
