"""Inference Preprocessing & Scaler Latency SLA (< 10ms) Verification.

Task: ML-INF-001 (Stream H - Real-Time Inference)
Agent: AGENT-INFERENCE
SLA: Complete live inference path (feature assembly, shape validation,
scaler transform, model forward pass, masked softmax) must execute
within p99 < 10.0ms on CPU across 50,000 tick evaluations.
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
import torch

from nexus_scalp.application.live.inference import InferenceService
from nexus_scalp.models.scalp_net import ScalpNet


class _MockScaler:
    """Production-compatible scaler sidecar double."""

    def __init__(self, dim: int = 50, corrupt: bool = False) -> None:
        self.mean = np.zeros(dim, dtype=np.float32)
        self.std = np.ones(dim, dtype=np.float32)
        self.corrupt = corrupt

    def transform(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.std


class _MockBundle:
    """Production-compatible model bundle double."""

    def __init__(
        self, num_features: int = 50, num_classes: int = 3, corrupt_scaler: bool = False
    ) -> None:
        self.model = ScalpNet(num_features=num_features, num_classes=num_classes)
        self.model.eval()
        self.scaler = _MockScaler(dim=num_features, corrupt=corrupt_scaler)
        self.artifact_path = "mock_model.pt"


def _create_mock_engine(bundle: Any, dim: int = 50, schema_id: str = "scalp_v1") -> SimpleNamespace:
    """Constructs a production-contract engine mock for InferenceService."""
    return SimpleNamespace(
        _bundle=bundle,
        _bundle_lock=threading.RLock(),
        _build_live_feature_vector=lambda fv: (fv.to_tensor_input(), {}),
        _validate_50d_tensor=lambda fv, context="": list(fv),
        _last_live_tensor_dim=dim,
        _last_live_tensor_schema=schema_id,
        _last_70d_assembly_timings={},
        _inference_failures_total=0,
        _inference_count=0,
        _latency_dbg_every=64,
        _latency_regression=None,
        _last_model_input_tensor=None,
        emit_incident_telemetry=MagicMock(),
        effective_feature_dim=dim,
        effective_feature_schema_id=schema_id,
        _news_enabled=False,
        news_engine=None,
        liquidity_governor=None,
        _maybe_build_live_sequence_tensor=lambda **kw: None,
    )


class _MockFeatureVector:
    """Feature vector mock returning a canonical float vector."""

    def __init__(self, dim: int = 50) -> None:
        self.dim = dim
        self._vec = [float(i % 10) * 0.1 for i in range(dim)]

    def to_tensor_input(self) -> list[float]:
        return list(self._vec)


# =============================================================================
# 1. Full 50,000-Tick Latency SLA Benchmark (ML-INF-001 Primary Acceptance)
# =============================================================================


def test_inference_latency_sla_50d_50k_benchmark() -> None:
    """Profiles live inference latency over 50,000 iterations against < 10ms P99 SLA.

    Measures:
      1. Vector validation
      2. Scaler transformation
      3. PyTorch tensor preparation & nan_to_num guarding
      4. ScalpNet model forward pass (single-threaded CPU)
      5. Masked softmax probability calibration
    """
    dim = 50
    bundle = _MockBundle(num_features=dim, num_classes=3)
    engine = _create_mock_engine(bundle, dim=dim, schema_id="scalp_v1")
    fv = _MockFeatureVector(dim=dim)

    # 1. Warm up model with 1,000 forward passes
    warmup_passes = 1000
    probs: torch.Tensor | None = None
    for _ in range(warmup_passes):
        probs = InferenceService.infer_probabilities(engine, fv)
    assert probs is not None
    assert probs.shape == (1, 3)

    # 2. Time N sequential single-tick inference calls
    n_ticks = int(os.environ.get("NSE_INFERENCE_BENCHMARK_TICKS", "50000"))
    latencies_ns: list[int] = []

    t_bench_start = time.perf_counter()
    for _ in range(n_ticks):
        t0 = time.perf_counter_ns()
        probs = InferenceService.infer_probabilities(engine, fv)
        t1 = time.perf_counter_ns()
        latencies_ns.append(t1 - t0)
    t_bench_end = time.perf_counter()

    elapsed_sec = t_bench_end - t_bench_start
    throughput = n_ticks / elapsed_sec if elapsed_sec > 0 else float("inf")

    latencies_ms = np.array(latencies_ns, dtype=np.float64) / 1e6
    p50_ms = float(np.percentile(latencies_ms, 50))
    p90_ms = float(np.percentile(latencies_ms, 90))
    p95_ms = float(np.percentile(latencies_ms, 95))
    p99_ms = float(np.percentile(latencies_ms, 99))
    min_ms = float(np.min(latencies_ms))
    max_ms = float(np.max(latencies_ms))
    mean_ms = float(np.mean(latencies_ms))
    std_ms = float(np.std(latencies_ms))

    # Print benchmark report table
    print("\n" + "=" * 68)
    print(f"ML-INF-001: INFERENCE LATENCY BENCHMARK REPORT ({n_ticks:,} TICKS)")
    print("=" * 68)
    print("  Target SLA:           p99 < 10.000 ms (10,000 us)")
    print(f"  Total Wall Time:      {elapsed_sec:.3f} seconds")
    print(f"  Throughput:           {throughput:.1f} inferences/sec")
    print(f"  Min Latency:          {min_ms:.4f} ms")
    print(f"  p50 (Median):         {p50_ms:.4f} ms")
    print(f"  p90:                  {p90_ms:.4f} ms")
    print(f"  p95:                  {p95_ms:.4f} ms")
    print(f"  p99 (SLA Gate):       {p99_ms:.4f} ms")
    print(f"  Max Latency:          {max_ms:.4f} ms")
    print(f"  Mean Latency:         {mean_ms:.4f} ms (+/- {std_ms:.4f} ms)")
    sla_verdict = "PASSED" if p99_ms < 10.0 else "FAILED"
    print(f"  SLA Verdict:          {sla_verdict} (p99 = {p99_ms:.4f} ms < 10.0 ms)")
    print("=" * 68)

    # Acceptance Assertions
    assert p99_ms < 10.0, f"SLA VIOLATION: p99 latency {p99_ms:.3f}ms exceeds 10.0ms threshold"
    assert p50_ms < 5.0, f"Excessive median latency: p50={p50_ms:.3f}ms"
    assert probs is not None
    assert probs.shape == (1, 3), f"Unexpected output shape: {probs.shape}"
    prob_sum = float(probs.sum().item())
    assert abs(prob_sum - 1.0) < 1e-4, f"Probabilities do not sum to 1.0: {prob_sum}"
    assert not bool(torch.isnan(probs).any()), "Probabilities contain NaN"
    assert not bool(torch.isinf(probs).any()), "Probabilities contain Inf"


# =============================================================================
# 2. 70D Feature Vector Path Latency SLA
# =============================================================================


def test_inference_latency_sla_70d_path() -> None:
    """Verifies that 70D live inference (50D Base + 10D News + 10D Liquidity) satisfies < 10ms SLA."""
    dim = 70
    bundle = _MockBundle(num_features=dim, num_classes=3)
    engine = _create_mock_engine(bundle, dim=dim, schema_id="scalp_v3")
    fv = _MockFeatureVector(dim=dim)

    # Warmup
    probs: torch.Tensor | None = None
    for _ in range(500):
        probs = InferenceService.infer_probabilities(engine, fv)
    assert probs is not None
    assert probs.shape == (1, 3)

    # Benchmark 5,000 ticks
    n_ticks = 5000
    latencies_ns: list[int] = []
    for _ in range(n_ticks):
        t0 = time.perf_counter_ns()
        probs = InferenceService.infer_probabilities(engine, fv)
        t1 = time.perf_counter_ns()
        latencies_ns.append(t1 - t0)

    latencies_ms = np.array(latencies_ns, dtype=np.float64) / 1e6
    p99_ms = float(np.percentile(latencies_ms, 99))
    p50_ms = float(np.percentile(latencies_ms, 50))

    assert p99_ms < 10.0, f"70D SLA VIOLATION: p99 latency {p99_ms:.3f}ms exceeds 10.0ms"
    assert p50_ms < 5.0, f"70D excessive median latency: p50={p50_ms:.3f}ms"
    assert probs is not None
    assert probs.shape == (1, 3)


# =============================================================================
# 3. Fail-Closed Resilience & Error Contract Checks
# =============================================================================


def test_inference_service_fail_closed_on_corrupt_scaler() -> None:
    """InferenceService must fail-closed (RuntimeError) when scaler sidecar is corrupt."""
    bundle = _MockBundle(num_features=50, num_classes=3, corrupt_scaler=True)
    engine = _create_mock_engine(bundle, dim=50)
    fv = _MockFeatureVector(dim=50)

    with pytest.raises(RuntimeError, match="Scaler sidecar corrupt"):
        InferenceService.infer_probabilities(engine, fv)


def test_inference_service_uninitialized_bundle() -> None:
    """InferenceService must raise RuntimeError when model bundle is None."""
    engine = _create_mock_engine(bundle=None, dim=50)
    fv = _MockFeatureVector(dim=50)

    with pytest.raises(RuntimeError, match="Model bundle not initialized"):
        InferenceService.infer_probabilities(engine, fv)
