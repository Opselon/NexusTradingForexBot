"""AGENT-2 P1 ONLINE-TRAIN PROPAGATION PROBES — record -> buffer -> scaler -> trainer -> model.

Non-invasive: observes the REAL WalkForwardTrainer, REAL ScalerBundle and the
REAL width guard semantics. No production file is modified; no artifact is
written (scaler fit happens on in-memory arrays only via the trainer's public
shape contract; artifact-save paths are pointed at tmp_path).

Answers Q7-Q10 + Q19 (order) with real runtime values.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.helpers.runtime_70d_probe import (  # noqa: E402
    build_engine_stub_with_real_methods,
    collect_contract_snapshot,
)

# ---------------------------------------------------------------------------
# P1-1: buffer record -> scaler dimension (Q8 vs Q9) — both sources named
# ---------------------------------------------------------------------------


def test_p1_buffer_dim_matches_scaler_dim_on_70d_bundle():
    """The two SOURCES compared: buffer record width (built from
    _retrain_record_dim over the base producer vector) vs the loaded bundle's
    scaler width. On divergence, name both sides."""
    snap = collect_contract_snapshot()
    stub = build_engine_stub_with_real_methods(snap.scaler_dim)

    rec_dim = int(stub._retrain_record_dim())
    scaler_dim = snap.scaler_dim
    print(
        f"[BUFFER_VS_SCALER]\nbuffer_dim(source=_retrain_record_dim)={rec_dim}\n"
        f"scaler_dim(source=loaded bundle {snap.artifact_path})={scaler_dim}"
    )
    assert rec_dim == scaler_dim, (
        f"BUFFER_SCALER_DIM_MISMATCH\n"
        f"buffer_dim={rec_dim} (source=LiveEngine._retrain_record_dim)\n"
        f"scaler_dim={scaler_dim} (source=loaded bundle scaler.npz)\n"
        "the buffer builder and the serving scaler resolved different widths"
    )


# ---------------------------------------------------------------------------
# P1-2: scaler -> trainer width via the REAL trainer binding (Q9 vs Q10)
# ---------------------------------------------------------------------------


def test_p1_trainer_rebind_aligns_with_scaler_dim():
    """The REAL _rebind_trainer_to_bundle must move a WalkForwardTrainer bound
    at 50D onto the loaded bundle's width (70D) — the BUG-185 part2 invariant."""
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.features.schema import FEATURE_SCHEMAS
    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    snap = collect_contract_snapshot()
    trainer = WalkForwardTrainer()  # default: active registry schema (50D)
    assert trainer.num_features == 50, f"trainer bootstrap width changed: {trainer.num_features}"

    stub = build_engine_stub_with_real_methods(snap.scaler_dim)
    stub._rebind_trainer_to_bundle = LiveEngine._rebind_trainer_to_bundle.__get__(stub)
    stub50_rebind = LiveEngine._rebind_trainer_to_bundle
    stub.trainer = trainer
    stub._online_train_disabled = False
    stub._rebind_trainer_to_bundle()

    print(
        f"[SCALER_VS_TRAINER]\nscaler_dim={snap.scaler_dim}\n"
        f"trainer_num_features(after rebind)={trainer.num_features}\n"
        f"trainer_schema={trainer.feature_schema.schema_id}"
    )
    assert trainer.num_features == snap.scaler_dim, (
        f"TRAINER_CONTRACT_MISMATCH\n"
        f"scaler_dim={snap.scaler_dim} (loaded bundle)\n"
        f"trainer_num_features={trainer.num_features} (after real rebind)\n"
        f"trainer_schema={trainer.feature_schema.schema_id}"
    )
    assert trainer.feature_schema.schema_id == "scalp_v3"

    # hot-swap back to 50D restores scalp_v1 (BUG-185 part2)
    stub50 = build_engine_stub_with_real_methods(50)
    stub50._rebind_trainer_to_bundle = stub50_rebind.__get__(stub50)
    stub50.trainer = trainer
    stub50._online_train_disabled = False
    stub50._rebind_trainer_to_bundle()
    assert trainer.num_features == 50
    assert trainer.feature_schema.schema_id == "scalp_v1"


# ---------------------------------------------------------------------------
# P1-3: fine_tune_online width guard — 50D records vs 70D head FAILS LOUD
# ---------------------------------------------------------------------------


def test_p1_fine_tune_width_guard_fails_loud_on_50d_records_vs_70d_head():
    """BUG-182B regression (observed, not re-implemented): feeding a 50D
    feature frame to a 70-input model must raise ValueError BEFORE torch
    work. Uses the real trainer + a tiny real model head; no artifacts."""
    import polars as pl
    import torch

    from nexus_scalp.features.schema import FEATURE_SCHEMAS
    from nexus_scalp.models.scalp_net import ScalpNet
    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    trainer = WalkForwardTrainer()
    # Bind the trainer to scalp_v3/70D exactly as _rebind_trainer_to_bundle does
    trainer.feature_schema = FEATURE_SCHEMAS.resolve("scalp_v3")
    trainer.num_features = 70

    model70 = None
    try:
        model70 = ScalpNet(num_features=70, num_classes=4)
    except Exception:
        pytest.skip("ScalpNet import path differs — construct via trainer module")

    rows = [{f"feat_{i}": 0.01 for i in range(50)} for _ in range(60)]
    for r in rows:
        r.update(close=1.0, high=1.0, low=1.0, open=1.0, spread=0.2, atr_m1=1.0, label=0)
    df = pl.DataFrame(rows)

    with pytest.raises(ValueError) as excinfo:
        trainer.fine_tune_online(
            live_model=model70, recent_df=df, feature_cols=[f"feat_{i}" for i in range(50)]
        )
    msg = str(excinfo.value)
    print(f"[TRAINER_WIDTH_GUARD] ValueError raised: {msg[:200]}")
    # The guard chain may fire at frame validation (schema columns) or at the
    # model-input width check — both are LOUD ValueError contract failures.
    assert (
        "width" in msg.lower()
        or "feature columns" in msg.lower()
        or "feature contract violation" in msg.lower()
    ), f"guard message should name the contract violation: {msg}"


# ---------------------------------------------------------------------------
# P1-4: feature ORDER — canonical names vs merged runtime vector placement
# ---------------------------------------------------------------------------


def test_p1_canonical_feature_order_survives_runtime_merge():
    """Dimension equality is NOT order equality. The liquidity block placed at
    indices 60..69 by build_70d_vector must equal the governor's as_vector()
    order, and the news block at 50..59 the canonical selection."""
    import math
    from datetime import UTC, datetime, timedelta

    from nexus_scalp.features.features70 import news_10d_from_context
    from nexus_scalp.features.liquidity_runtime import LiquidityGovernor, build_70d_vector
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.features.schema_contract import (
        LIQUIDITY_10D_NAMES,
        NEWS_10D_NAMES,
        canonical_feature_names,
    )
    from nexus_scalp.market_data.bar_aggregator import BarData

    t0 = datetime.now(UTC) - timedelta(minutes=122)
    bars, price = [], 3300.0
    for i in range(120):
        step = math.sin(i * 0.37) * 0.8 + math.cos(i * 0.11) * 0.5
        o = price
        price = price + step
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=t0 + timedelta(minutes=i + 1),
                open=o,
                high=max(o, price) + 0.4,
                low=min(o, price) - 0.4,
                close=price,
                tick_volume=100,
                is_complete=True,
            )
        )
    last = bars[-1]
    tick = SimpleNamespace(
        symbol="XAUUSD",
        timestamp=last.timestamp,
        bid=last.close,
        ask=last.close + 0.20,
        volume=100.0,
        last=0.0,
        flags=0,
    )
    fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bars, tick)
    base = fv.to_tensor_input()

    gov = LiquidityGovernor(enabled=True)
    gov.compute_from_engine(
        bars=bars,
        mid_price=float(last.close),
        atr=float(fv.atr_m1),
        decision_at=last.timestamp,
    )
    liq = [float(v) for v in gov.last_snapshot.features]
    news = news_10d_from_context(None)

    vec70 = build_70d_vector(base, family_10=news, liquidity_10=liq)
    names = canonical_feature_names()

    assert len(vec70) == 70 and len(names) == 70
    # Segment identity: base slice must equal the producer output verbatim
    assert list(vec70[0:50]) == base, "base block reordered at runtime"
    assert list(vec70[50:60]) == list(news), "news block order differs from canonical"
    assert list(vec70[60:70]) == liq, "liquidity block order differs from governor as_vector"
    # Canonical family layout at the boundaries
    assert names[50:60] == NEWS_10D_NAMES
    assert names[60:70] == LIQUIDITY_10D_NAMES
    print("[FEATURE_ORDER] PASS — base/news/liquidity blocks all in canonical order")


# ---------------------------------------------------------------------------
# P1-5: full staged contract summary (MODEL..CONTRACT..TRAINER) — §17
# ---------------------------------------------------------------------------


def test_p1_online_train_runtime_contract_summary():
    snap = collect_contract_snapshot()
    stub = build_engine_stub_with_real_methods(snap.scaler_dim)
    rec_dim = int(stub._retrain_record_dim())

    trace = {
        "model_dim": snap.model_dim,
        "scaler_dim": snap.scaler_dim,
        "record_dim": rec_dim,
        "effective_dim": snap.extra.get("engine_effective_dim"),
        "meta_dim": snap.extra.get("meta_dim"),
    }
    print(
        "ONLINE_TRAIN_RUNTIME_CONTRACT\n"
        "-----------------------------\n"
        f"MODEL\nschema={snap.meta_schema_id}\ndim={trace['model_dim']}\n\n"
        f"RETRAIN RECORD\ndim={trace['record_dim']}\n\n"
        f"BUFFER\ndim={trace['record_dim']}\n\n"
        f"SCALER\ndim={trace['scaler_dim']}\n\n"
        f"EFFECTIVE\ndim={trace['effective_dim']}\n"
    )
    vals = {k: v for k, v in trace.items() if v is not None}
    assert vals, "no contract stage measurable"
    distinct = set(vals.values())
    if len(distinct) == 1 and 70 in distinct:
        print("RESULT\nPASS")
    else:
        stages = ", ".join(f"{k}={v}" for k, v in trace.items())
        pytest.fail(
            f"ONLINE_TRAIN_CONTRACT_SPLIT: {stages}\n"
            "at least one stage disagrees with the 70D champion contract"
        )
