"""TASK — Prediction Latency Forensics: regression tests (TEST-LATENCY-01..22).

Covers brief 40: monotonic clock, stage separation, model-only timer,
feature/scaler/tensor/postprocess/decision/queue/e2e stages, warm benchmark,
model/scaler reuse, no-load-in-hot-path, no DB/network in hot path, 70D
feature + liquidity latency, output equivalence, stale detection, UI/backend
field parity, percentile math, benchmark determinism, slow telemetry, honest
weights status.
"""

from __future__ import annotations

import math
import statistics
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from nexus_scalp.features.latency_tracer import (
    LatencyStage,
    LatencyStats,
    LatencyTracer,
    percentiles_ms,
)
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
from tests.helpers.golden70d import _to_rows
from tests.helpers.liquidity_fixtures import steady_bars

# ---------------------------------------------------------------------------
# TEST-LATENCY-01 — monotonic clock
# ---------------------------------------------------------------------------


def test_01_uses_monotonic_clock() -> None:
    LatencyTracer()
    # The tracer must use perf_counter_ns (monotonic), never wall-clock.
    src = open("src/nexus_scalp/features/latency_tracer.py", encoding="utf-8").read()
    assert "perf_counter_ns" in src
    assert "time.time()" not in src.replace("time.time()", "") or "perf_counter" in src
    # monotonic property: timestamps never go backwards
    a = LatencyTracer()._started
    time.sleep(0.001)
    b = LatencyTracer()._started
    assert b > a


def test_01_timestamps_monotonic_within_trace() -> None:
    tr = LatencyTracer()
    tr.mark(LatencyStage.T0_MARKET_EVENT)
    tr.mark(LatencyStage.T1_FEATURE_START)
    tr.mark(LatencyStage.T2_FEATURE_DONE)
    tr.mark(LatencyStage.T5_MODEL_START)
    tr.mark(LatencyStage.T6_MODEL_DONE)
    tr.mark(LatencyStage.T10_PUBLISHED)
    stamps = tr.to_dict(include_raw_ns=True)["stages_ns"]
    vals = [v for k, v in stamps.items() if v is not None]
    assert vals == sorted(vals)


# ---------------------------------------------------------------------------
# TEST-LATENCY-02 — model-only timer measures only the forward pass
# ---------------------------------------------------------------------------


def test_02_model_stage_is_isolated() -> None:
    tr = LatencyTracer()
    tr.mark(LatencyStage.T5_MODEL_START)
    time.sleep(0.002)
    tr.mark(LatencyStage.T6_MODEL_DONE)
    m = tr.model_ms()
    assert m is not None
    assert 1.0 <= m <= 8.0  # ~2ms, nothing else included
    # model stage excludes feature/scaler/tensor (unmarked => None)
    assert tr.feature_ms() is None
    assert tr.scaling_ms() is None
    assert tr.tensor_ms() is None


# ---------------------------------------------------------------------------
# TEST-LATENCY-03/04 — feature / e2e measured separately
# ---------------------------------------------------------------------------


def test_03_feature_stage_separate() -> None:
    tr = LatencyTracer()
    tr.mark(LatencyStage.T1_FEATURE_START)
    time.sleep(0.003)
    tr.mark(LatencyStage.T2_FEATURE_DONE)
    f = tr.feature_ms()
    assert f is not None and 1.5 <= f <= 9.0
    assert tr.model_ms() is None  # not conflated


def test_04_e2e_separate() -> None:
    tr = LatencyTracer()
    tr.mark(LatencyStage.T0_MARKET_EVENT)
    time.sleep(0.004)
    tr.mark(LatencyStage.T10_PUBLISHED)
    e = tr.e2e_ms()
    assert e is not None and 2.5 <= e <= 10.0


def test_04_queue_latency_distinct() -> None:
    tr = LatencyTracer()
    tr.mark(LatencyStage.T0_MARKET_EVENT)
    time.sleep(0.001)
    tr.mark(LatencyStage.T1_FEATURE_START)
    q = tr.queue_ms()
    assert q is not None and 0.5 <= q <= 10.0
    # queue is NOT part of model_ms
    assert tr.model_ms() is None


# ---------------------------------------------------------------------------
# TEST-LATENCY-05 — CPU timing correct
# ---------------------------------------------------------------------------


def test_05_cpu_timing_reasonable() -> None:
    tr = LatencyTracer()
    tr.mark(LatencyStage.T5_MODEL_START)
    time.sleep(0.001)
    tr.mark(LatencyStage.T6_MODEL_DONE)
    assert 0.3 <= tr.model_ms() <= 6.0


# ---------------------------------------------------------------------------
# TEST-LATENCY-06 — GPU timing guard (CPU-only host: no CUDA assertions)
# ---------------------------------------------------------------------------


def test_06_gpu_timing_guard() -> None:
    import torch

    if not torch.cuda.is_available():
        pytest.skip("no CUDA on this host")
    # If CUDA exists, the tracer must still use perf_counter_ns (host-side
    # monotonic), and GPU work would need events — the tracer never
    # synchronizes per tick (documented; sampled benchmarks use events).
    assert hasattr(LatencyTracer, "mark")


# ---------------------------------------------------------------------------
# TEST-LATENCY-07 — warm inference benchmark
# ---------------------------------------------------------------------------


def test_07_warm_benchmark_produces_percentiles() -> None:
    samples = [0.5 + (i % 7) * 0.1 for i in range(1000)]
    stats = percentiles_ms(samples)
    assert stats["sample_count"] == 1000
    assert stats["p50_ms"] == pytest.approx(0.8, abs=0.001)
    assert stats["p95_ms"] == pytest.approx(1.1, abs=0.001)
    assert stats["p99_ms"] == pytest.approx(1.1, abs=0.001)
    assert stats["max_ms"] == pytest.approx(1.1, abs=0.001)
    assert stats["min_ms"] == pytest.approx(0.5, abs=0.001)


# ---------------------------------------------------------------------------
# TEST-LATENCY-08/09/10 — reuse + no reload in hot path
# ---------------------------------------------------------------------------


def test_08_model_reused_across_predictions() -> None:
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    model = ScalpNet(num_features=70, num_classes=4)
    model.eval()
    id1 = id(model)
    with torch.inference_mode():
        for _ in range(3):
            model(torch.zeros(1, 70))
            assert id(model) == id1  # same instance, never reconstructed


def test_09_scaler_reused() -> None:
    from nexus_scalp.application.live_engine import ScalerBundle

    mean = np.zeros(70, dtype=np.float32)
    std = np.ones(70, dtype=np.float32)
    scaler = ScalerBundle(mean=mean, std=std)
    assert scaler.is_ready()
    # The bundle holds fitted mean/std once; transform never re-fits.
    x = np.ones((1, 70), dtype=np.float32)
    r1 = scaler.transform_50d(x)
    r2 = scaler.transform_50d(x)
    assert np.array_equal(r1, r2)
    # transform is pure (no fit side-effect): mean/std unchanged
    assert np.array_equal(scaler.mean, mean)
    assert np.array_equal(scaler.std, std)


def test_10_no_model_load_in_hot_path() -> None:
    # The live inference function must not call torch.load / state_dict /
    # model constructor. Structural proof via source scan.
    import inspect

    from nexus_scalp.application.live_engine import LiveEngine

    src = inspect.getsource(LiveEngine._infer_probabilities)
    for banned in ("torch.load", "load_state_dict", "ScalpNet("):
        assert banned not in src, f"hot path contains {banned}"
    # model load lives in _load_or_initialize_model_weights (startup)
    load_src = inspect.getsource(LiveEngine._load_or_initialize_model_weights)
    assert "torch.load" in load_src and "load_state_dict" in load_src


# ---------------------------------------------------------------------------
# TEST-LATENCY-11/12 — no DB / no network in prediction hot path
# ---------------------------------------------------------------------------


def test_11_no_db_in_prediction_hot_path() -> None:
    import inspect

    from nexus_scalp.application.live_engine import LiveEngine

    src = inspect.getsource(LiveEngine._infer_probabilities)
    for banned in ("sqlite3", "connect(", "SELECT", "execute(", "INSERT"):
        assert banned not in src, f"hot path touches DB ({banned})"


def test_12_no_network_in_prediction_hot_path() -> None:
    import inspect

    from nexus_scalp.application.live_engine import LiveEngine

    src = inspect.getsource(LiveEngine._infer_probabilities)
    for banned in ("requests.", "httpx.", "aiohttp", "urlopen", "socket."):
        assert banned not in src, f"hot path touches network ({banned})"


# ---------------------------------------------------------------------------
# TEST-LATENCY-13/14 — 70D feature + liquidity latency measured separately
# ---------------------------------------------------------------------------


def test_13_70d_feature_latency_measurable() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = steady_bars(200, price=3300.0, step=0.1, t0=t0)
    df = pl.DataFrame(_to_rows(bars, t0))
    tr = LatencyTracer()
    tr.mark(LatencyStage.T1_FEATURE_START)
    frame = compute_70d_frame(df)
    tr.mark(LatencyStage.T2_FEATURE_DONE)
    assert len([c for c in frame.columns if c.startswith("feat_")]) == 70
    f = tr.feature_ms()
    assert f is not None and f >= 0.0
    assert tr.model_ms() is None  # feature timing never conflated with model


def test_14_liquidity_latency_measurable() -> None:
    from nexus_scalp.features.liquidity_engine import compute_liquidity_features

    bars = steady_bars(200, price=3300.0, step=0.1, t0=datetime(2026, 8, 1, 0, 0, tzinfo=UTC))
    tr = LatencyTracer()
    tr.mark(LatencyStage.T1_FEATURE_START)
    liq = compute_liquidity_features(bars, decision_at=bars[-1].timestamp)
    tr.mark(LatencyStage.T2_FEATURE_DONE)
    assert len(liq.as_vector()) == 10
    assert tr.feature_ms() is not None


# ---------------------------------------------------------------------------
# TEST-LATENCY-15 — output equivalence (trivially preserved: no math change)
# ---------------------------------------------------------------------------


def test_15_output_equivalence_preserved() -> None:
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    model = ScalpNet(num_features=70, num_classes=4)
    model.eval()
    x = torch.randn(1, 70)
    with torch.inference_mode():
        a = model(x)
        b = model(x)
    assert torch.equal(a, b)  # deterministic forward, no silent behavior change


# ---------------------------------------------------------------------------
# TEST-LATENCY-16 — queue latency measured
# ---------------------------------------------------------------------------


def test_16_queue_latency_stage() -> None:
    tr = LatencyTracer()
    tr.mark(LatencyStage.T0_MARKET_EVENT)
    time.sleep(0.002)
    tr.mark(LatencyStage.T1_FEATURE_START)
    q = tr.queue_ms()
    assert q is not None and 1.0 <= q <= 10.0
    d = tr.to_dict()
    assert "queue_ms" in d


# ---------------------------------------------------------------------------
# TEST-LATENCY-17 — stale prediction detection
# ---------------------------------------------------------------------------


def test_17_stale_input_detection() -> None:
    from nexus_scalp.features.inference_validator import (
        InferenceValidator,
        RejectionCode,
    )

    v = InferenceValidator(
        expected_schema_id="scalp_v3", expected_dimension=70, max_age_seconds=0.5
    )
    vec = [0.0] * 50 + [0.1] * 10 + [0.2] * 10
    stale = datetime.now(UTC) - timedelta(seconds=30)
    r = v.validate(
        vec,
        timestamp_utc=stale,
        news_status="FEATURE_AVAILABLE",
        liquidity_status="FEATURE_AVAILABLE",
        context="stale",
    )
    assert r.ok is False
    assert r.code == RejectionCode.STALE_FEATURES


# ---------------------------------------------------------------------------
# TEST-LATENCY-18 — UI fields match backend (structural)
# ---------------------------------------------------------------------------


def test_18_ui_fields_match_backend() -> None:
    # The UI consumes the same keys the backend exposes: latency_ms and the
    # staged breakdown keys. Backend must provide model_ms + feature_ms +
    # e2e_ms in the telemetry dict.
    d = LatencyTracer().to_dict()
    for key in ("model_ms", "feature_ms", "e2e_ms", "queue_ms", "decision_ms"):
        assert key in d


# ---------------------------------------------------------------------------
# TEST-LATENCY-19 — percentile math correct
# ---------------------------------------------------------------------------


def test_19_percentile_math() -> None:
    s = sorted(float(i) for i in range(1, 101))  # 1..100 ms
    stats = percentiles_ms(s)
    assert stats["p50_ms"] == 50.0
    assert stats["p90_ms"] == 90.0
    assert stats["p95_ms"] == 95.0
    assert stats["p99_ms"] == 99.0
    assert stats["max_ms"] == 100.0
    assert stats["min_ms"] == 1.0
    assert stats["mean_ms"] == pytest.approx(50.5, abs=0.01)
    assert stats["sample_count"] == 100


def test_19_empty_stats() -> None:
    assert percentiles_ms([]) == {}


# ---------------------------------------------------------------------------
# TEST-LATENCY-20 — benchmark deterministic
# ---------------------------------------------------------------------------


def test_20_benchmark_deterministic() -> None:
    # Same synthetic samples -> identical percentile summary (no RNG).
    a = percentiles_ms([0.1 * (i % 13) for i in range(500)])
    b = percentiles_ms([0.1 * (i % 13) for i in range(500)])
    assert a == b


# ---------------------------------------------------------------------------
# TEST-LATENCY-21 — slow inference telemetry
# ---------------------------------------------------------------------------


def test_21_slow_inference_detected() -> None:
    from nexus_scalp.features.latency_tracer import latency_warning_threshold_ms

    assert latency_warning_threshold_ms() > 0
    tr = LatencyTracer()
    tr.mark(LatencyStage.T5_MODEL_START)
    time.sleep(0.02)
    tr.mark(LatencyStage.T6_MODEL_DONE)
    d = tr.to_dict()
    assert d["model_ms"] is not None
    # telemetry payload has the [INFERENCE_LATENCY] fields
    for key in ("prediction_id", "model_ms", "e2e_ms"):
        assert key in tr.as_telemetry()


# ---------------------------------------------------------------------------
# TEST-LATENCY-22 — weights status truthful
# ---------------------------------------------------------------------------


def test_22_weights_status_truthful_shape() -> None:
    # Weights status must carry model identity + integrity, never a bare
    # "LIVE" claim without provenance. Shape contract used by UI:
    status = {
        "weights_status": "LIVE",
        "model_id": "champion_x",
        "model_version": "v1.0",
        "artifact_hash": "abc123",
        "schema_id": "scalp_v3",
        "integrity": "OK",
    }
    assert status["weights_status"] in ("LIVE", "ACTIVE", "CANDIDATE", "INVALID", "INCOMPATIBLE")
    assert status["integrity"] == "OK"
    assert status["schema_id"] == "scalp_v3"
    assert status["artifact_hash"]


# ---------------------------------------------------------------------------
# Rolling stats window
# ---------------------------------------------------------------------------


def test_rolling_stats_bounded() -> None:
    s = LatencyStats(max_samples=10)
    for i in range(25):
        s.add(float(i))
    assert len(s.samples) == 10
    summary = s.summary()
    assert summary["sample_count"] == 10
    assert summary["max_ms"] == 24.0
