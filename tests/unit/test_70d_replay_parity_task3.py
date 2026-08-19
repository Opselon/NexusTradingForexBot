"""TASK-03-70D-PARITY — dataset==replay parity + anti-leakage + golden corpus
(TEST-70D-PARITY-05/07/08/13/18/30).

Covers:
  TEST-70D-PARITY-05  replay produces identical 70D (canonical engine)
  TEST-70D-PARITY-08  dataset == replay (same causal window -> bit-exact)
  TEST-70D-PARITY-18  future data cannot change a historical vector
  TEST-70D-PARITY-30  golden corpus exact/parity (all scenarios 70D + valid)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from nexus_scalp.features.schema_contract import validate_70d_vector
from nexus_scalp.model_generation.replay import replay_70d_vector
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
from tests.helpers.golden70d import GOLDEN_CORPUS, _to_rows
from tests.helpers.liquidity_fixtures import steady_bars


def _frame_from_bars(bars, t0=None):
    rows = _to_rows(bars, t0 or datetime(2026, 8, 1, 0, 0, tzinfo=UTC))
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-05 — replay produces identical 70D via canonical engine
# ---------------------------------------------------------------------------


def test_p05_replay_dimension_and_schema() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = steady_bars(200, price=3300.0, step=0.1, t0=t0)
    df = _frame_from_bars(bars, t0)
    r = replay_70d_vector(df, timestamp=t0 + timedelta(minutes=199))
    assert r["dimension"] == 70
    assert r["schema_id"] == "scalp_v3"
    assert r["news_status"] == "FEATURE_DISABLED"
    validate_70d_vector(r["feature_vector"], context="replay")


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-08 — dataset == replay bit-exact
# ---------------------------------------------------------------------------


def test_p08_dataset_row_equals_replay() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = steady_bars(200, price=3300.0, step=0.1, t0=t0)
    df = _frame_from_bars(bars, t0)
    frame = compute_70d_frame(df)
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    assert len(feat_cols) == 70

    # last dataset row (timestamp == last bar)
    row = frame.tail(1).row(0, named=True)
    ds_vec = [float(row[c]) for c in feat_cols]
    ds_ts = row["timestamp"]

    r = replay_70d_vector(df, timestamp=ds_ts)
    rp_vec = r["feature_vector"]

    assert len(rp_vec) == 70
    assert ds_vec == rp_vec, "dataset vector != replay vector (same causal window)"


def test_p08_dataset_row_equals_replay_with_news() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = steady_bars(200, price=3300.0, step=0.1, t0=t0)
    df = _frame_from_bars(bars, t0)
    news_df = pl.DataFrame(
        {
            "published_at": [t0 + timedelta(minutes=10)],
            "active_high_impact_events": [1.0],
            "xauusd_relevance": [0.8],
            "usd_relevance": [0.5],
            "bullish_pressure": [0.4],
            "bearish_pressure": [0.1],
            "conflict_score": [0.2],
            "novelty": [0.0],
            "freshness": [1.0],
            "confidence": [0.9],
            "source_consensus": [0.7],
            "news_state": [2.0],
            "time_since_event_sec": [300.0],
        }
    )
    frame = compute_70d_frame(df, news_frame=news_df)
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    row = frame.tail(1).row(0, named=True)
    ds_vec = [float(row[c]) for c in feat_cols]
    r = replay_70d_vector(df, timestamp=row["timestamp"], news_frame=news_df)
    # news enabled -> the news block must reflect the prior event
    assert r["news_status"] == "FEATURE_AVAILABLE"
    assert r["feature_vector"] == ds_vec


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-18 — future data cannot change a historical vector
# ---------------------------------------------------------------------------


def test_p18_future_bars_do_not_change_historical_vector() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    base = steady_bars(150, price=3300.0, step=0.1, t0=t0)
    decision_ts = t0 + timedelta(minutes=149)

    df_base = _frame_from_bars(base, t0)
    v_base = replay_70d_vector(df_base, timestamp=decision_ts)["feature_vector"]

    # append 50 future bars (different regime)
    future = steady_bars(50, price=3400.0, step=0.5, t0=t0 + timedelta(minutes=150))
    df_full = _frame_from_bars(base + future, t0)
    v_full = replay_70d_vector(df_full, timestamp=decision_ts)["feature_vector"]

    assert v_base == v_full, "future bars changed a historical 70D vector"


def test_p18_future_news_does_not_change_historical_vector() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = steady_bars(150, price=3300.0, step=0.1, t0=t0)
    decision_ts = t0 + timedelta(minutes=149)
    df = _frame_from_bars(bars, t0)

    news_past = pl.DataFrame(
        {"published_at": [t0 + timedelta(minutes=5)], "active_high_impact_events": [1.0]}
    )
    r_no_future = replay_70d_vector(df, timestamp=decision_ts, news_frame=news_past)[
        "feature_vector"
    ]

    # a FUTURE news event (after decision) must be invisible
    news_with_future = pl.concat(
        [
            news_past,
            pl.DataFrame(
                {
                    "published_at": [decision_ts + timedelta(minutes=5)],
                    "active_high_impact_events": [9.0],
                }
            ),
        ]
    )
    r_with_future = replay_70d_vector(df, timestamp=decision_ts, news_frame=news_with_future)[
        "feature_vector"
    ]

    assert r_no_future == r_with_future, "future news changed a historical 70D vector"
    # sanity: the past event itself must be visible
    assert r_no_future[50] == 1.0


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-30 — golden corpus exact/parity
# ---------------------------------------------------------------------------


def test_p30_corpus_all_70d_and_valid() -> None:
    assert len(GOLDEN_CORPUS) >= 10
    for name, entry in GOLDEN_CORPUS.items():
        assert entry["dimension"] == 70, f"{name} not 70D"
        validate_70d_vector(entry["feature_vector"], context=f"golden:{name}")
        assert entry["schema_id"] == "scalp_v3"


def test_p30_corpus_covers_required_scenarios() -> None:
    names = set(GOLDEN_CORPUS.keys())
    for req in (
        "trending_up",
        "ranging",
        "high_volatility",
        "low_volatility",
        "news_on_ramp",
        "news_off_ramp",
        "liquidity_bsl_ssl",
        "eqh_cluster",
        "sweep",
        "no_sweep",
        "htf_confluence",
    ):
        assert req in names, f"missing golden scenario {req}"


def test_p30_corpus_deterministic() -> None:
    import importlib

    from tests.helpers import golden70d

    c2 = importlib.reload(golden70d).GOLDEN_CORPUS
    for name in GOLDEN_CORPUS:
        assert GOLDEN_CORPUS[name]["feature_vector"] == c2[name]["feature_vector"], (
            f"golden {name} drifted across rebuilds"
        )


def test_p30_corpus_news_off_is_explicit() -> None:
    assert GOLDEN_CORPUS["news_off_ramp"]["news_status"] == "FEATURE_DISABLED"
    assert GOLDEN_CORPUS["news_on_ramp"]["news_status"] == "FEATURE_AVAILABLE"
    # news ON must actually change the news block vs OFF
    n_on = GOLDEN_CORPUS["news_on_ramp"]["feature_vector"][50:60]
    n_off = GOLDEN_CORPUS["news_off_ramp"]["feature_vector"][50:60]
    assert n_on != n_off
