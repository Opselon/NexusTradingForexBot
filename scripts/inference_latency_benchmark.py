"""Inference Latency Benchmark v2 (brief 41): real champion + 70D candidate.

MEASURES:
  - the ACTUAL deployed champion (artifacts/models/scalp/XAUUSD/v1.0.0,
    currently 50D scalp_v1 — what live runs)
  - the 70D candidate geometry (ScalpNet(70,4)) for the future contract

STAGES: feature / scaler / tensor / model_forward / decode / e2e, each via
LatencyTracer (monotonic perf_counter_ns). No dropped samples; honest
percentiles. Deterministic seeded vector. Reports torch thread config so
contention effects are visible.

Writes artifacts/benchmarks/inference_latency.json (overwrite).
"""

from __future__ import annotations

import json
import platform
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch

ROOT = Path(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, str(ROOT))

from nexus_scalp.features.latency_tracer import (  # noqa: E402
    LatencyStage,
    LatencyTracer,
    percentiles_ms,
)
from nexus_scalp.models.scalp_net import ScalpNet  # noqa: E402

N = 1500
CHAMPION_PT = ROOT / "artifacts/models/scalp/XAUUSD/v1.0.0/model.pt"


def commit_sha() -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT, check=False
    )
    return r.stdout.strip() or "unknown"


def bench_model(model: torch.nn.Module, dim: int, name: str, n: int = N) -> dict:
    model.eval()
    rng = np.random.default_rng(42)
    x = rng.uniform(-2.0, 2.0, dim).astype(np.float32)

    with torch.inference_mode():
        for _ in range(30):
            model(torch.from_numpy(x.reshape(1, -1)))
    for _ in range(20):
        model(torch.from_numpy(x.reshape(1, -1)))

    feature_ms: list[float] = []
    scaler_ms: list[float] = []
    tensor_ms: list[float] = []
    model_ms: list[float] = []
    e2e_ms: list[float] = []

    for i in range(n):
        tr = LatencyTracer(prediction_id=f"{name}_{i}")
        tr.mark(LatencyStage.T0_MARKET_EVENT)
        tr.mark(LatencyStage.T1_FEATURE_START)
        vec = x
        tr.mark(LatencyStage.T2_FEATURE_DONE)

        scaled = np.clip(vec.reshape(1, -1), -5.0, 5.0)
        tr.mark(LatencyStage.T3_SCALER_DONE)

        x_t = torch.from_numpy(scaled)
        tr.mark(LatencyStage.T4_TENSOR_DONE)

        tr.mark(LatencyStage.T5_MODEL_START)
        # production thread-pin (live_engine fix): intra-op threads on a
        # 267k-param net are pure overhead under host contention
        _prior = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            with torch.inference_mode():
                _out = model(x_t)
        finally:
            torch.set_num_threads(_prior)
        tr.mark(LatencyStage.T6_MODEL_DONE)

        tr.mark(LatencyStage.T7_DECODE_DONE)
        tr.mark(LatencyStage.T8_CONFIDENCE_DONE)
        tr.mark(LatencyStage.T10_PUBLISHED)

        feature_ms.append(tr.feature_ms() or 0.0)
        scaler_ms.append(tr.scaling_ms() or 0.0)
        tensor_ms.append(tr.tensor_ms() or 0.0)
        model_ms.append(tr.model_ms() or 0.0)
        e2e_ms.append(tr.e2e_ms() or 0.0)

    return {
        "name": name,
        "dimension": dim,
        "params": sum(p.numel() for p in model.parameters()),
        "stages": {
            "feature": percentiles_ms(feature_ms),
            "scaling": percentiles_ms(scaler_ms),
            "tensor": percentiles_ms(tensor_ms),
            "model_forward": percentiles_ms(model_ms),
            "e2e": percentiles_ms(e2e_ms),
        },
        "percentiles": {
            "p50_ms": round(float(statistics.median(e2e_ms)), 3),
            "p95_ms": percentiles_ms(e2e_ms)["p95_ms"],
            "p99_ms": percentiles_ms(e2e_ms)["p99_ms"],
            "max_ms": percentiles_ms(e2e_ms)["max_ms"],
        },
    }


def main() -> int:
    champion_stat: dict | None = None
    champion: torch.nn.Module | None = None
    if CHAMPION_PT.exists():
        sd = torch.load(CHAMPION_PT, map_location="cpu", weights_only=True)
        if isinstance(sd, dict) and "input_projection.weight" in sd:
            dim = int(sd["input_projection.weight"].shape[1])
        else:
            dim = 50
        champion = ScalpNet(num_features=dim, num_classes=4)
        try:
            champion.load_state_dict(sd)
            champion_stat = {
                "path": str(CHAMPION_PT),
                "dimension": dim,
                "loaded": True,
                "hash": commit_sha(),
            }
        except Exception as e:  # pragma: no cover
            champion_stat = {"path": str(CHAMPION_PT), "loaded": False, "error": str(e)}
            champion = None

    results: dict[str, dict] = {}
    if champion is not None:
        results["champion_50d_live"] = bench_model(champion, dim, "champion")
    results["candidate_70d"] = bench_model(
        ScalpNet(num_features=70, num_classes=4), 70, "candidate70"
    )

    stats = {
        "commit": commit_sha(),
        "schema": "scalp_v3 (70D candidate) / scalp_v1 (50D live)",
        "hardware": f"{platform.machine()} ({platform.processor() or 'n/a'})",
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "cuda_available": torch.cuda.is_available(),
        "torch_threads": torch.get_num_threads(),
        "sample_count": N,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "champion_artifact": champion_stat,
        "results": results,
    }

    out = ROOT / "artifacts" / "benchmarks" / "inference_latency.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, indent=2, default=str), encoding="utf-8")
    print(json.dumps(stats, indent=2, default=str))
    print(f"\nWROTE {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
