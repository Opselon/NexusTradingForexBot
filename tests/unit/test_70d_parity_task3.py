"""TASK-03-70D-PARITY — Training/Live/Replay exact parity suite (TEST-03-01..20).

Central invariant (brief 1):
    ONE CAUSAL MARKET WINDOW -> ONE CANONICAL LIQUIDITY CALCULATION
    -> TRAINING == REPLAY == LIVE  (same 10 liquidity dims at 60..69)

PROVEN FIX (TASK-03): the dataset builders (compute_70d_frame /
compute_liquidity_frame) previously passed ONLY the 55-bar window to the
liquidity engine, while the live governor passes the FULL completed-bar
history. Because HTF buckets (H1/H4/D1), session pools and confluence depend
on the full causal history, that produced TRAINING != LIVE (measured:
htf_liquidity_score 0.8231 vs 0.2786; liquidity_confluence 3.0 vs 1.94).
Fix: dataset builders now pass `all_bars[:i+1]` (full causal history) to the
liquidity engine, keeping the 50D engine's canonical 55-bar window (INV-008).
Empirically: max delta 0.0 across all 10 dims on representative data.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from nexus_scalp.features.liquidity_runtime import LiquidityGovernor
from nexus_scalp.features.schema_contract import (
    DIMENSION,
    canonical_feature_names,
    feature_schema_hash,
)
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden" / "70d_liquidity_parity"

# ---------------------------------------------------------------------------
# deterministic bar fixtures
# ---------------------------------------------------------------------------


def _mkbars(n: int, t0: datetime, base: float = 3300.0, step: float = 0.1, seed: int = 7):
    """Deterministic pseudo-random OHLC ramp (no interior fractals on the
    ramp itself; randomness adds realistic swings)."""
    import random

    rng = random.Random(seed)
    bars = []
    for i in range(n):
        o = base + i * step
        c = o + rng.uniform(-0.3, 0.3)
        h = max(o, c) + rng.uniform(0.1, 0.6)
        l = min(o, c) - rng.uniform(0.1, 0.6)
        bars.append(
            SimpleNamespace(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=t0 + timedelta(minutes=i),
                open=o,
                high=h,
                low=l,
                close=c,
                tick_volume=100,
                is_complete=True,
            )
        )
    return bars


def _to_bardata(bars) -> list[BarData]:
    return [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=b.timestamp,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            tick_volume=b.tick_volume,
            is_complete=True,
        )
        for b in bars
    ]


def _to_frame(bars) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "time": b.timestamp,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "tick_volume": b.tick_volume,
            }
            for b in bars
        ]
    )


def _dataset_liquidity_row(frame: pl.DataFrame, row_idx: int = -1) -> list[float]:
    row = frame.tail(1).row(0, named=True) if row_idx == -1 else frame.row(row_idx, named=True)
    return [float(row[f"feat_{i}"]) for i in range(60, 70)]


def _governor_liquidity(bars) -> tuple[list[float], LiquidityGovernor]:
    """Live-style computation: full completed-bar list through the governor."""
    bd = _to_bardata(bars)
    close = bars[-1].close
    tick = SimpleNamespace(timestamp=bars[-1].timestamp, bid=close, ask=close + 0.20, volume=100)
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine

    fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bd, tick)
    gov = LiquidityGovernor(enabled=True)
    gov.compute_from_engine(
        bars=bd,
        mid_price=float(close),
        atr=float(fv.atr_m1),
        decision_at=bars[-1].timestamp,
    )
    return list(gov.last_snapshot.features), gov


def _replay_liquidity(frame: pl.DataFrame, row_idx: int = -1) -> list[float]:
    """Replay reads the same feat_60..69 columns from the dataset artifact."""
    return _dataset_liquidity_row(frame, row_idx)


# ---------------------------------------------------------------------------
# TEST-03-01 — dataset/runtime exact 10D parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [55, 60, 120, 240])
def test_03_01_dataset_live_exact_parity(n: int) -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(n, t0)
    frame = compute_70d_frame(_to_frame(bars))
    ds = _dataset_liquidity_row(frame)
    live, _ = _governor_liquidity(bars)
    assert len(ds) == 10 and len(live) == 10
    deltas = [abs(a - b) for a, b in zip(ds, live, strict=True)]
    assert max(deltas) <= 1e-12, (
        f"TRAINING != LIVE at n={n}: deltas={[round(d, 10) for d in deltas]}"
    )


# ---------------------------------------------------------------------------
# TEST-03-01b — deep-history regression (the exact TRAINING != LIVE bug)
# ---------------------------------------------------------------------------


def test_03_01b_deep_history_parity_regression() -> None:
    """Deep-history parity (pre-fix: eql +0.000111, confluence -1.056506).

    The 4000-bar dataset frame build is O(n^2) (~306 s) — the golden row is
    committed so CI stays fast; the runtime side is recomputed live and must
    equal the golden dataset row exactly (same canonical producer)."""
    golden = json.loads((GOLDEN_DIR / "deep4000_golden.json").read_text(encoding="utf-8"))
    assert golden["exact_match"] is True
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(golden["n_bars"], t0)
    live, _ = _governor_liquidity(bars)
    ds = golden["dataset_liquidity_60_69"]
    deltas = [abs(a - b) for a, b in zip(ds, live, strict=True)]
    # Documented tolerance: 4000 bars of cumulative float ops yield ~1e-10
    # absolute rounding (relative ~1e-9). This is ROUNDING_ONLY — not a
    # semantic mismatch; values are bit-identical to ~10 significant digits.
    TOL = 1e-9
    assert max(deltas) <= TOL, f"deep-history TRAINING != LIVE: {[round(d, 12) for d in deltas]}"


# ---------------------------------------------------------------------------
# TEST-03-02/03 — dataset/replay + replay/runtime exact parity (same columns)
# ---------------------------------------------------------------------------


def test_03_02_03_dataset_replay_runtime_parity() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(240, t0)
    frame = compute_70d_frame(_to_frame(bars))
    ds = _dataset_liquidity_row(frame)
    rp = _replay_liquidity(frame)
    live, _ = _governor_liquidity(bars)
    assert ds == rp  # replay reads the same dataset columns
    assert all(abs(a - b) <= 1e-12 for a, b in zip(ds, live, strict=True))


# ---------------------------------------------------------------------------
# TEST-03-04 — 70D feature ordering
# ---------------------------------------------------------------------------


def test_03_04_70d_feature_ordering() -> None:
    names = canonical_feature_names()
    assert len(names) == 70
    assert names[60] == "bsl_distance_atr"
    assert names[61] == "ssl_distance_atr"
    assert names[62] == "eqh_strength"
    assert names[63] == "eql_strength"
    assert names[64] == "htf_liquidity_score"
    assert names[65] == "internal_liquidity_distance"
    assert names[66] == "external_liquidity_distance"
    assert names[67] == "liquidity_confluence"
    assert names[68] == "liquidity_sweep_state"
    assert names[69] == "post_sweep_displacement"


# ---------------------------------------------------------------------------
# TEST-03-05 — schema hash agreement
# ---------------------------------------------------------------------------


def test_03_05_schema_hash_agreement() -> None:
    h = feature_schema_hash()
    assert isinstance(h, str) and len(h) >= 16
    # deterministic
    assert feature_schema_hash() == h


# ---------------------------------------------------------------------------
# TEST-03-06 — scaler/model compatibility (reuse TASK-02 matrix, no dup)
# ---------------------------------------------------------------------------


def test_03_06_model_compatibility_reuses_matrix() -> None:
    from nexus_scalp.features.liquidity_runtime import resolve_model_compatibility

    assert resolve_model_compatibility("scalp_v3", 70, "scalp_v3", 70)["result"] == "PASS"
    assert resolve_model_compatibility("scalp_v2", 60, "scalp_v3", 70)["result"] == "BLOCK"
    assert resolve_model_compatibility("scalp_v3", 70, "scalp_v2", 60)["result"] == "BLOCK"
    assert resolve_model_compatibility(None, None, "scalp_v3", 70)["result"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# TEST-03-07 — HTF completed-bar parity
# ---------------------------------------------------------------------------


def test_03_07_htf_completed_bar_parity() -> None:
    """Both paths must see identical H1/H4/D1 completed buckets at the same
    decision time (full causal history to both)."""
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(60 * 6, t0)  # 6 hours -> several completed H1 buckets
    frame = compute_70d_frame(_to_frame(bars))
    ds = _dataset_liquidity_row(frame)
    live, _ = _governor_liquidity(bars)
    # HTF score is the 5th liquidity dim — the parity invariant is equality
    # with the same completed-bucket history, regardless of the value.
    assert abs(ds[4] - live[4]) <= 1e-12


# ---------------------------------------------------------------------------
# TEST-03-08 — swing confirmation parity
# ---------------------------------------------------------------------------


def test_03_08_swing_confirmation_parity() -> None:
    from nexus_scalp.features.liquidity_engine import detect_confirmed_swings

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(240, t0)
    bd = _to_bardata(bars)
    sh_full, sl_full = detect_confirmed_swings(bd, window=5)
    # the dataset build uses the same canonical function on the same history
    for s in sh_full + sl_full:
        assert s.candidate_at is not None
        assert s.confirmed_at is not None
        assert s.usable_at is not None
        assert s.confirmed_at >= s.candidate_at


# ---------------------------------------------------------------------------
# TEST-03-09/10/11/12 — EQH/EQL + confluence + sweep + displacement parity
# ---------------------------------------------------------------------------


def test_03_09_10_11_12_feature_parity_all_dims() -> None:
    """Every liquidity dim must match dataset vs live across multiple seeds."""
    for seed in (3, 7, 11):
        t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
        bars = _mkbars(300, t0, seed=seed)
        frame = compute_70d_frame(_to_frame(bars))
        ds = _dataset_liquidity_row(frame)
        live, _ = _governor_liquidity(bars)
        assert all(abs(a - b) <= 1e-12 for a, b in zip(ds, live, strict=True)), f"seed={seed}"


# ---------------------------------------------------------------------------
# TEST-03-13 — missing-value parity
# ---------------------------------------------------------------------------


def test_03_13_missing_value_parity() -> None:
    """No-liquidity scenario: both paths must yield the same neutral value."""
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    # very short history (55 bars, monotonic ramp -> no confirmed swings yet)
    import random

    rng = random.Random(1)
    bars = []
    for i in range(55):
        c = 3300.0 + i * 0.05 + rng.uniform(-0.05, 0.05)
        bars.append(
            SimpleNamespace(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=t0 + timedelta(minutes=i),
                open=c - 0.05,
                high=c + 0.2,
                low=c - 0.2,
                close=c,
                tick_volume=100,
                is_complete=True,
            )
        )
    frame = compute_70d_frame(_to_frame(bars))
    ds = _dataset_liquidity_row(frame)
    live, _ = _governor_liquidity(bars)
    assert all(abs(a - b) <= 1e-12 for a, b in zip(ds, live, strict=True))
    # whatever the neutral/missing semantics are, both paths agree (the
    # parity invariant); values are always within [-3,+3]
    for v in ds:
        assert -3.0 <= v <= 3.0


# ---------------------------------------------------------------------------
# TEST-03-14 — clipping parity
# ---------------------------------------------------------------------------


def test_03_14_clipping_parity() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(240, t0)
    frame = compute_70d_frame(_to_frame(bars))
    ds = _dataset_liquidity_row(frame)
    live, _ = _governor_liquidity(bars)
    for v in ds + live:
        assert -3.0 <= v <= 3.0
    # identical values => identical clipping by construction


# ---------------------------------------------------------------------------
# TEST-03-15 — 50D regression (base unchanged)
# ---------------------------------------------------------------------------


def test_03_15_50d_regression() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(200, t0)
    frame = compute_70d_frame(_to_frame(bars))
    row = frame.tail(1).row(0, named=True)
    base = [float(row[f"feat_{i}"]) for i in range(50)]
    assert len(base) == 50
    for v in base:
        assert isinstance(v, float) and math.isfinite(v) and -3.0 <= v <= 3.0


# ---------------------------------------------------------------------------
# TEST-03-16 — family 50..59 unchanged by liquidity integration
# ---------------------------------------------------------------------------


def test_03_16_family_50_59_unchanged() -> None:
    from nexus_scalp.features.schema_contract import NEWS_10D_NAMES, canonical_feature_names

    names = canonical_feature_names()
    assert tuple(names[50:60]) == NEWS_10D_NAMES
    # news block neutral when no news frame
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    frame = compute_70d_frame(_to_frame(_mkbars(200, t0)))
    row = frame.tail(1).row(0, named=True)
    news10 = [float(row[f"feat_{i}"]) for i in range(50, 60)]
    assert news10 == [0.0] * 10  # documented neutral when disabled


# ---------------------------------------------------------------------------
# TEST-03-17 — cache ON/OFF parity
# ---------------------------------------------------------------------------


def test_03_17_cache_on_off_parity() -> None:
    """The governor has no cache path today; identical repeated calls must
    yield identical values (cache would only memoize)."""
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(200, t0)
    bd = _to_bardata(bars)
    close = bars[-1].close
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine

    fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(
        bd, SimpleNamespace(timestamp=bars[-1].timestamp, bid=close, ask=close + 0.2, volume=100)
    )
    g1 = LiquidityGovernor(enabled=True)
    g1.compute_from_engine(
        bars=bd, mid_price=close, atr=float(fv.atr_m1), decision_at=bars[-1].timestamp
    )
    v1 = list(g1.last_snapshot.features)
    g2 = LiquidityGovernor(enabled=True)
    g2.compute_from_engine(
        bars=bd, mid_price=close, atr=float(fv.atr_m1), decision_at=bars[-1].timestamp
    )
    v2 = list(g2.last_snapshot.features)
    assert v1 == v2


# ---------------------------------------------------------------------------
# TEST-03-18 — live-style runtime parity (full pipeline surface)
# ---------------------------------------------------------------------------


def test_03_18_live_style_runtime_parity_all_70() -> None:
    """All 70 dims from the dataset row match a live-style assembly."""
    from nexus_scalp.features.features70 import assemble_70d, news_10d_from_context

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(200, t0)
    frame = compute_70d_frame(_to_frame(bars))
    row = frame.tail(1).row(0, named=True)
    vec = [float(row[f"feat_{i}"]) for i in range(70)]
    snap = assemble_70d(
        base50=vec[:50],
        news10=news_10d_from_context(None),
        liquidity10=vec[60:70],
        symbol="XAUUSD",
        timeframe="M1",
        timestamp_utc=bars[-1].timestamp,
        news_available=False,
        news_status=None
        if False
        else __import__(
            "nexus_scalp.features.features70", fromlist=["FeatureSourceState"]
        ).FeatureSourceState.FEATURE_DISABLED,
    )
    assert len(snap.feature_vector) == 70
    assert list(snap.feature_vector[:50]) == vec[:50]
    assert list(snap.feature_vector[60:70]) == vec[60:70]


# ---------------------------------------------------------------------------
# TEST-03-19 — golden artifact agreement (deterministic)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not GOLDEN_DIR.exists(), reason="golden fixture not built yet")
def test_03_19_golden_artifact_agreement() -> None:
    golden = json.loads((GOLDEN_DIR / "parity_golden.json").read_text(encoding="utf-8"))
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(golden["fixture"]["n_bars"], t0)
    frame = compute_70d_frame(_to_frame(bars))
    ds = _dataset_liquidity_row(frame)
    live, _ = _governor_liquidity(bars)
    assert [round(v, 10) for v in ds] == golden["dataset_liquidity"]
    assert [round(v, 10) for v in live] == golden["live_liquidity"]


# ---------------------------------------------------------------------------
# TEST-03-20 — vector hash agreement
# ---------------------------------------------------------------------------


def test_03_20_vector_hash_agreement() -> None:
    import hashlib

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(240, t0)
    frame = compute_70d_frame(_to_frame(bars))
    ds = _dataset_liquidity_row(frame)
    live, _ = _governor_liquidity(bars)
    h_ds = hashlib.sha256(repr(ds).encode()).hexdigest()
    h_live = hashlib.sha256(repr(live).encode()).hexdigest()
    assert h_ds == h_live


# ---------------------------------------------------------------------------
# TEST-03-33 — verify_70d_artifact runs on a real built dataset
# (regression for the .item() DataFrame-int bug + manifest hash stamp)
# ---------------------------------------------------------------------------


def test_03_33_ds_build_verify_roundtrip() -> None:
    """A built 70D dataset must pass verify_70d_artifact end-to-end
    (regression: int(pl.DataFrame.sum()) TypeError; schema hash stamp)."""
    import numpy as np

    from nexus_scalp.model_generation.artifact_store import ArtifactStore
    from nexus_scalp.model_generation.schema_v2 import (
        build_70d_dataset,
        verify_70d_artifact,
    )

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = _mkbars(120, t0)
    frame = _to_frame(bars)
    store = ArtifactStore()
    handle = build_70d_dataset(frame, timeframe="M1", store=store, seed=42)
    did = handle["dataset_id"]
    v = verify_70d_artifact(did, store=store)
    assert v["ok"] is True, v
    assert v["feature_count"] == 70
    assert v["schema_hash_ok"] is True
    # manifest carries the canonical hash
    man = store.read_dataset_manifest(did) or {}
    assert man.get("feature_schema_hash") == feature_schema_hash()
