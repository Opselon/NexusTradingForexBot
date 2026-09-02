"""BUG-217 regression - the live news STATE encoding must stay inside [-3,+3].

FAILS BEFORE the fix: vectorize_news_context() maps NewsState.BREAKING to
4.0 and STALE to 5.0; build_news_10 preserves that encoding at news slot 59,
so validate_70d_vector rejects the whole 70D vector the moment a live news
state reaches BREAKING/STALE -> ALL live 70D inference blocked (the exact
client-stale P0 class proven live by BUG-197's count overflow).

The canonical semantics are defined by the dataset builder: the news family
is passed through features70.clamp_neutral_family (neutral 0.0) BEFORE the
contract boundary, i.e. training rows carry encodings clamped to 3.0. The
live projection must emit the SAME in-distribution value.

Also pins:
  * the 43.0 active_event_count incident case (BUG-197) end-to-end
  * NaN/Inf/malformed producer fields degrade to the documented defaults
  * exact canonical vector output for a fully-specified context
  * the 50D live path is untouched by the news-family fix
"""

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from nexus_scalp.features.schema_contract import (  # noqa: E402
    feature_schema_hash,
    validate_70d_vector,
)
from nexus_scalp.governance.alignment import (  # noqa: E402
    vectorize_news_context,
)
from nexus_scalp.shadow.shadow70.news_provider import (  # noqa: E402
    build_news_10,
    verify_news_family,
)

STATE_ENCODINGS = {
    "NORMAL": 0.0,
    "ELEVATED": 1.0,
    "HIGH_IMPACT": 2.0,
    "CONFLICTED": 3.0,
    "BREAKING": 3.0,  # table says 4.0 - clamped to the training distribution
    "STALE": 3.0,  # table says 5.0 - clamped to the training distribution
    "UNKNOWN_STATE": 0.0,
}


def _ctx(count=3, **overrides):
    ctx = {
        "state": "HIGH_IMPACT",
        "active_event_count": count,
        "xauusd_relevance": 0.8,
        "usd_relevance": 0.5,
        "bullish_score": 0.21,
        "bearish_score": 0.09,
        "confidence": 0.6,
        "conflict_score": 0.03,
        "freshness": 0.95,
        "source_consensus": 0.7,
    }
    ctx.update(overrides)
    return ctx


def _slot59(state: str) -> float:
    news10, _ = build_news_10(vectorize_news_context(_ctx(state=state)))
    return news10[9]


# ---------------------------------------------------------------------------
# 1. the latent overflow itself (BREAKING / STALE / unknown)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["BREAKING", "STALE"])
def test_state_encoding_stays_inside_family_bound(state: str) -> None:
    news10, _ = build_news_10(vectorize_news_context(_ctx(state=state)))
    assert all(-3.0 <= v <= 3.0 for v in news10), (
        f"{state}: news10={news10} violates the 70D family bounds"
    )


def test_breaking_clamps_to_training_distribution_max() -> None:
    """The clamp target is the value TRAINING rows carry (3.0), not 0."""
    assert _slot59("BREAKING") == 3.0


def test_stale_clamps_to_training_distribution_max() -> None:
    assert _slot59("STALE") == 3.0


def test_unbounded_legal_states_keep_their_table_value() -> None:
    """The repair must not alter encodings that were already in-bounds."""
    for state in ("NORMAL", "ELEVATED", "HIGH_IMPACT", "CONFLICTED"):
        assert _slot59(state) == STATE_ENCODINGS[state]


def test_unknown_state_defaults_to_zero() -> None:
    assert _slot59("NOT_A_REAL_STATE") == 0.0


# ---------------------------------------------------------------------------
# 2. the original 43.0 incident (BUG-197) end-to-end through the contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [18, 24, 31, 43, 500])
def test_incident_count_values_pass_full_70d_validation(count: int) -> None:
    """The exact values observed in the 43.0 incident must validate."""
    news10, _ = build_news_10(vectorize_news_context(_ctx(count=count)))
    vector = [0.0] * 50 + news10 + [0.0] * 10
    out = validate_70d_vector(vector, schema_hash=feature_schema_hash(), context="live_70d")
    assert out[50] == 1.0  # bounded flag, not the raw count
    assert verify_news_family(out[50:60])


# ---------------------------------------------------------------------------
# 3. malformed / non-finite producer data
#
# DOCUMENTED CONTRACT: vectorize_news_context RAISES on non-numeric producer
# fields (ValueError/TypeError) — it never fabricates a number. Every live
# call site wraps the projection in try/except and degrades to the documented
# zero vector (news-disabled ablation, INV discipline: refuse > fabricate).
# The pydantic CurrentNewsContext (ge/le constrained) makes the primary live
# path safe; these tests pin the defense-in-depth behavior.
# ---------------------------------------------------------------------------


def _raw_context(bad) -> dict:
    """A dict-shaped context (the dict branch of vectorize) with one bad field."""
    return {
        "state": "HIGH_IMPACT",
        "active_event_count": 1,
        "xauusd_relevance": bad,
        "usd_relevance": 0.5,
        "bullish_score": 0.2,
        "bearish_score": 0.1,
        "confidence": 0.6,
        "conflict_score": 0.0,
        "freshness": 0.9,
        "source_consensus": 0.5,
    }


@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), float("-inf"), "not-a-number", None, object()],
)
def test_non_numeric_producer_values_raise_never_fabricate(bad) -> None:
    """A non-numeric producer value must RAISE (never become a number)."""
    with pytest.raises((TypeError, ValueError)):
        vectorize_news_context(_raw_context(bad))


@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), "not-a-number", None, object()],
)
def test_engine_caller_degrades_malformed_news_to_zero_vector(bad) -> None:
    """The live call-site contract: exception -> documented zero vector."""
    try:
        v12 = vectorize_news_context(_raw_context(bad))
    except (TypeError, ValueError):
        v12 = [0.0] * 12  # the engine's documented ablation fallback
    assert len(v12) == 12
    assert all(v == 0.0 or math.isfinite(v) for v in v12)


def test_infinity_from_producer_raises_not_silently_clipped() -> None:
    """Inf must raise here (fail-closed), NOT become 3.0 — the bound clamp
    applies only to the static encoding tables, never to producer data."""
    with pytest.raises(ValueError):
        vectorize_news_context(_raw_context(float("inf")))


# ---------------------------------------------------------------------------
# 4. exact canonical vector output (contract pin)
# ---------------------------------------------------------------------------


def test_exact_canonical_vector_output() -> None:
    """Pins the full 12->10 projection, including the clamped state slot."""
    ctx = {
        "state": "STALE",  # 5.0 -> clamped 3.0
        "active_event_count": 7,  # -> 1.0
        "xauusd_relevance": 0.9,
        "usd_relevance": 0.4,
        "bullish_score": 0.6,
        "bearish_score": 0.2,
        "confidence": 0.8,
        "conflict_score": 0.1,
        "freshness": 0.7,
        "source_consensus": 0.5,
    }
    v12 = vectorize_news_context(ctx)
    assert v12 == [
        1.0,  # active bounded flag
        0.9,
        0.4,
        0.6,
        0.2,
        0.1,
        0.0,  # novelty (absent -> NEW=0.0)
        0.7,
        0.8,
        0.5,  # source_consensus (12-field context, NOT in the family)
        3.0,  # STALE clamped from 5.0
        0.0,  # time_since_event_sec (absent)
    ]
    news10, version = build_news_10(v12)
    assert version == "news_family_v1"
    assert news10 == [1.0, 0.9, 0.4, 0.6, 0.2, 0.1, 0.0, 0.7, 0.8, 3.0]


# ---------------------------------------------------------------------------
# 5. 50D live contract untouched by the news-family repair
# ---------------------------------------------------------------------------


def test_50d_contract_independent_of_news_projection() -> None:
    """The ACTIVE 50D contract must not involve the news family at all."""
    from nexus_scalp.features.schema_contract import (
        ACTIVE_SCHEMA_ID,
        DIMENSION,
        SCHEMA_ID,
    )
    from nexus_scalp.features.schema_contract import (
        assert_canonical_registry as _acr,
    )

    _acr()  # raises if registry/ACTIVE drift
    assert ACTIVE_SCHEMA_ID == "scalp_v1"
    assert SCHEMA_ID == "scalp_v3" and DIMENSION == 70
