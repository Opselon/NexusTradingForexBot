"""AGENT-2 P0 RUNTIME CONTRACT PROBES — current 70D feature generation.

Non-invasive runtime evidence for the 70D pipeline incident (Agent 1 owns
the production repair; this suite only OBSERVES real runtime objects):

  Q1 canonical contract          -> test_p0_canonical_contract_is_scalp_v3_70d
  Q2/Q3/Q4/Q5 feature dims       -> test_p0_real_feature_pipeline_dimensions
  Q14 70D model feed             -> test_p0_70d_model_receives_canonical_70d
  contract snapshot of artifact  -> test_p0_configured_artifact_contract_consistent

Every test renders a grep-friendly trace block so failures localise the
divergence (producer vs merge vs gate) instead of a bare assert.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.helpers.runtime_70d_probe import (  # noqa: E402
    build_engine_stub_with_real_methods,
    build_feature_pipeline_trace,
    collect_contract_snapshot,
    render_feature_trace,
)

# ---------------------------------------------------------------------------
# P0-1: canonical contract (SSOT) — Q1
# ---------------------------------------------------------------------------


def test_p0_canonical_contract_is_scalp_v3_70d(capsys):
    from nexus_scalp.features.schema_contract import (
        DIMENSION,
        SCHEMA_ID,
        canonical_feature_names,
        feature_schema_hash,
    )

    assert SCHEMA_ID == "scalp_v3", f"canonical schema id drifted: {SCHEMA_ID}"
    assert DIMENSION == 70, f"canonical dimension drifted: {DIMENSION}"
    names = canonical_feature_names()
    assert len(names) == 70
    # Contract families at the documented offsets
    assert names[0] and names[49] and names[50] == "active_high_impact_events"
    assert names[60] == "bsl_distance_atr"
    assert names[69] == "post_sweep_displacement"
    h1 = feature_schema_hash()
    h2 = feature_schema_hash()
    assert h1 == h2 and len(h1) == 16  # deterministic content hash
    out = capsys.readouterr().out
    assert out == ""  # no stray prints in SSOT import path


# ---------------------------------------------------------------------------
# P0-2: the REAL feature pipeline dimensions (base/liquidity/news/merged) — Q2..Q5
# ---------------------------------------------------------------------------


def test_p0_real_feature_pipeline_dimensions():
    trace = build_feature_pipeline_trace(bars_count=240)
    print(render_feature_trace(trace, expected_dim=70))

    # Base producer must be exactly 50D (protected scalp_v1 block)
    assert trace["base_dim"] == 50, (
        f"FEATURE_PIPELINE_BASE_WIDTH_MISMATCH: base_dim={trace['base_dim']} (expected 50); "
        f"producer=ScalpFeatureEngine.compute_from_bars().to_tensor_input()"
    )
    assert trace["base_all_finite"], "base vector contains non-finite values"

    # Liquidity subsystem must produce a usable 10D block
    assert trace["liquidity_status"] == "LIQUIDITY_CALCULATION_OK", (
        f"LIQUIDITY_SUBSYSTEM_NOT_OK: {trace['liquidity_status']}"
    )
    assert trace["liquidity_dim"] == 10, (
        f"LIQUIDITY_DIM_MISMATCH: {trace['liquidity_dim']} (expected 10)"
    )
    assert trace["liquidity_causal_state"] == "VALID", (
        f"LIQUIDITY_CAUSAL_STATE={trace['liquidity_causal_state']} (expected VALID)"
    )

    # Canonical merge must assemble 70 from 50+10+10
    assert trace["merge_fn"] == "liquidity_runtime.build_70d_vector", (
        f"CANONICAL_MERGE_NOT_REACHED: merge_fn={trace['merge_fn']}"
    )
    assert trace["merged_dim"] == 70, (
        f"FEATURE_PIPELINE_CONTRACT_MISMATCH\n"
        f"schema=scalp_v3\nexpected_dim=70\nactual_dim={trace['merged_dim']}\n"
        f"base_dim={trace['base_dim']}\nliquidity_dim={trace['liquidity_dim']}\n"
        f"news_dim={trace['news_dim']}\nproducer={trace['merge_fn']}"
    )

    # Both validation gates accept their respective vectors
    assert trace["validate_50d_ok"] is True
    assert trace["validate_70d_ok"] is True, (
        "70D canonical vector REJECTED by LiveEngine._validate_feature_vector"
    )


# ---------------------------------------------------------------------------
# P0-3: the configured artifact is internally consistent 70D — Q26/Q14
# ---------------------------------------------------------------------------


def test_p0_configured_artifact_contract_consistent():
    snap = collect_contract_snapshot()
    print(snap.render("ARTIFACT_CONTRACT"))

    assert snap.model_dim is not None, "checkpoint not loadable (model_dim=NOT_PRESENT)"
    assert snap.scaler_dim is not None, "scaler not loadable (scaler_dim=NOT_PRESENT)"
    assert snap.model_dim == 70, f"MODEL_ARTIFACT_DIM_MISMATCH: model_dim={snap.model_dim}"
    assert snap.scaler_dim == 70, f"SCALER_ARTIFACT_DIM_MISMATCH: scaler_dim={snap.scaler_dim}"
    assert snap.meta_schema_id == "scalp_v3", f"META_SCHEMA_ID_MISMATCH: {snap.meta_schema_id}"
    meta_dim = snap.extra.get("meta_dim")
    assert meta_dim == 70, f"META_DIM_MISMATCH: {meta_dim}"
    assert meta_dim == snap.model_dim == snap.scaler_dim, (
        f"ARTIFACT_INTERNAL_SPLIT: meta={meta_dim} checkpoint={snap.model_dim} "
        f"scaler={snap.scaler_dim}"
    )


# ---------------------------------------------------------------------------
# P0-4: 70D model never receives non-canonical input (engine contract) — Q14
# ---------------------------------------------------------------------------


def test_p0_engine_effective_contract_follows_70d_bundle():
    """With a 70D bundle loaded, the REAL engine properties must resolve to
    the scalp_v3/70D contract (BUG-125 artifact-driven contract)."""
    stub = build_engine_stub_with_real_methods(70)
    eff_dim = int(stub.effective_feature_dim)
    eff_schema = str(stub.effective_feature_schema_id)
    eff_cols = tuple(stub.effective_feature_cols)
    rec_dim = int(stub._retrain_record_dim())

    print(
        f"[ENGINE_EFFECTIVE_CONTRACT]\neffective_dim={eff_dim}\n"
        f"effective_schema={eff_schema}\neffective_cols={len(eff_cols)}\n"
        f"retrain_record_dim={rec_dim}"
    )
    assert eff_dim == 70, f"EFFECTIVE_DIM_MISMATCH: {eff_dim} (expected 70)"
    assert eff_schema == "scalp_v3", f"EFFECTIVE_SCHEMA_MISMATCH: {eff_schema}"
    assert len(eff_cols) == 70 and eff_cols[-1] == "feat_69"
    assert rec_dim == 70, f"RETRAIN_RECORD_DIM_MISMATCH: {rec_dim} (expected 70)"

    # And with a 50D bundle the contract follows back (no stale 70D lock-in)
    stub50 = build_engine_stub_with_real_methods(50)
    assert int(stub50.effective_feature_dim) == 50
    assert int(stub50._retrain_record_dim()) == 50


# ---------------------------------------------------------------------------
# P0-5: 70D merge output passes the engine's own schema gate — Q5/Q14
# ---------------------------------------------------------------------------


def test_p0_70d_model_receives_canonical_70d():
    """End-to-end single-snapshot check: base 50 + liquidity 10 + news 10
    merged via the REAL runtime builder must pass the REAL engine gate."""
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.features.features70 import news_10d_from_context
    from nexus_scalp.features.liquidity_runtime import build_70d_vector

    trace = build_feature_pipeline_trace(bars_count=240)
    assert trace["liquidity_status"] == "LIQUIDITY_CALCULATION_OK"
    assert trace["merged_dim"] == 70

    # Gate with the REAL schema-gated validator (70D branch)
    stub = build_engine_stub_with_real_methods(70)
    # Rebuild the merged vector through the real producers (trace keeps dims only)
    from nexus_scalp.features.liquidity_runtime import build_70d_vector as _b70

    base = None
    # Deterministic reconstruction: run the trace, capture the merged vector
    # by re-deriving it from the same real producers.
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from tests.helpers.runtime_70d_probe import (
        build_feature_pipeline_trace as _t,
    )

    trace = _t(bars_count=240)
    assert trace["liquidity_status"] == "LIQUIDITY_CALCULATION_OK"
    # Rebuild: the helper ran build_70d_vector internally; re-run with the same
    # real functions to obtain the concrete vector.
    from datetime import UTC, datetime, timedelta

    from nexus_scalp.features.liquidity_runtime import LiquidityGovernor
    from nexus_scalp.market_data.bar_aggregator import BarData

    t0 = datetime.now(UTC) - timedelta(minutes=242)
    bars = []
    price = 3300.0
    for i in range(240):
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
                tick_volume=100 + (i % 7) * 5,
                is_complete=True,
            )
        )
    last = bars[-1]
    tick = SimpleNamespace(
        symbol="XAUUSD",
        timestamp=last.timestamp,
        bid=last.close,
        ask=last.close + 0.20,
        volume=last.tick_volume,
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
    vec70 = _b70(base, family_10=news_10d_from_context(None), liquidity_10=liq)
    validated = LiveEngine._validate_feature_vector(stub, vec70, context="p0_gate_probe")
    assert len(validated) == 70
    news10 = news_10d_from_context(None)
    assert len(news10) == 10  # documented neutral news block is 10D


# ---------------------------------------------------------------------------
# P0-6: retrain record stage exposes the exact divergence (if any) — §32
# ---------------------------------------------------------------------------


def test_p0_warmup_record_construction_uses_matching_widths():
    """The warmup record comprehension {feat_i: x50[i] for i in range(dim)}
    must NEVER index beyond the producer width. This reproduces the exact
    production statement shape (live_engine.py:3043-3044) against the REAL
    producer output and the REAL _retrain_record_dim resolution — without
    touching production code."""
    trace = build_feature_pipeline_trace(bars_count=240)
    print(
        f"[WARMUP_RECORD_PROBE]\nproducer_width={trace['retrain_record_producer_width']}\n"
        f"record_contract_dim={trace['retrain_record_dim']}\n"
        f"built={trace['retrain_record_built_dim']}\nerror={trace['retrain_record_error']}"
    )
    if trace["retrain_record_error"]:
        pytest.fail(
            "WARMUP_RECORD_WIDTH_DIVERGENCE_REPRODUCED\n"
            f"producer_width={trace['retrain_record_producer_width']} "
            f"(fv.to_tensor_input, base 50D)\n"
            f"record_contract_dim={trace['retrain_record_dim']} "
            f"(_retrain_record_dim, bundle contract)\n"
            f"error={trace['retrain_record_error']}\n"
            "diagnosis: record builder iterates the BUNDLE width over the BASE "
            "producer vector — liquidity/news are not part of the record source"
        )
    assert trace["retrain_record_built_dim"] == trace["retrain_record_dim"]
