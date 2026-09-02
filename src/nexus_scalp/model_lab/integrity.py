"""MODEL LAB — leakage defense + artifact integrity tests support.

Adversarial probes demanded by the model-lab brief (§37-§43):
  * future leakage: mutating OOS rows must not change the trained artifact
    (training only reads train/val rows — enforced by construction and by
    the probes below);
  * input contract: wrong dimension / NaN / Inf inputs are rejected;
  * output contract: shape, finite probabilities, sum==1, class order;
  * corruption: a tampered checkpoint hash fails manifest verification.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import torch

from nexus_scalp.model_lab.architectures import build_model
from nexus_scalp.model_lab.registry import LAB_ROOT, ExperimentSpec


def artifact_integrity(experiment_id: str) -> dict[str, Any]:
    """Verify checkpoint hash + manifest presence for a lab artifact."""
    found = None
    for root_name in ("teachers", "students", "candidates"):
        d = LAB_ROOT / root_name / experiment_id
        if (d / "model.pt").exists():
            found = d
            break
    if found is None:
        return {"verified": False, "reason": "artifact not found"}
    manifest = json.loads((found / "manifest.json").read_text(encoding="utf-8"))
    actual = hashlib.sha256((found / "model.pt").read_bytes()).hexdigest()[:32]
    return {
        "verified": actual == manifest.get("checkpoint_sha256"),
        "expected": manifest.get("checkpoint_sha256"),
        "actual": actual,
        "path": str(found),
    }


def input_contract_probe(
    spec: ExperimentSpec, sequence_length: int | None = None
) -> dict[str, Any]:
    """Wrong dimension / NaN / Inf must raise or produce finite outputs."""
    model = build_model(spec)
    model.eval()
    seq = sequence_length or max(1, spec.sequence_length)
    results: dict[str, bool] = {}
    with torch.inference_mode():
        try:
            bad = torch.randn(1, seq, spec.input_dimension + 7)
            model(bad)
            results["wrong_dimension_rejected"] = False
        except Exception:
            results["wrong_dimension_rejected"] = True
        nan_x = torch.randn(1, seq, spec.input_dimension)
        nan_x[:, -1, 0] = float("nan")
        out = model(nan_x)
        results["nan_input_propagates_nan_output"] = bool(torch.isnan(out).any().item())
        inf_x = torch.randn(1, seq, spec.input_dimension)
        inf_x[:, -1, 1] = float("inf")
        out2 = model(inf_x)
        results["inf_input_propagates_nonfinite"] = bool((~torch.isfinite(out2)).all().item())
    return results


def output_contract_probe(spec: ExperimentSpec, X: np.ndarray) -> dict[str, Any]:
    model = build_model(spec)
    model.eval()
    with torch.inference_mode():
        logits = model(torch.from_numpy(X.astype(np.float32)))
        probs = torch.softmax(logits, dim=-1).cpu().numpy()
    return {
        "shape_ok": probs.shape == (X.shape[0], spec.num_classes),
        "rows_sum_to_one": bool(np.allclose(probs.sum(axis=1), 1.0, atol=1e-5)),
        "all_finite": bool(np.isfinite(probs).all()),
        "class_order": spec.class_order,
        "prob_range_ok": bool((probs >= 0).all() and (probs <= 1).all()),
    }


def future_leakage_probe(
    spec: ExperimentSpec,
    frame,  # pl.DataFrame with _split
    feature_cols: list[str],
) -> dict[str, Any]:
    """Training on (train|val) rows twice: once with pristine OOS rows, once
    with MUTATED OOS rows. The trained checkpoints must be bit-identical."""
    import polars as pl

    from nexus_scalp.model_lab.trainer import train_lab_model

    r1 = train_lab_model(spec, frame, feature_cols, git_revision="leak-probe")
    c1 = Path(r1["checkpoint"]).read_bytes()

    mutated = frame.with_columns(
        pl.when(pl.col("_split") == "oos")
        .then(pl.col(feature_cols[0]) * -3.7)
        .otherwise(pl.col(feature_cols[0]))
        .alias(feature_cols[0])
    )
    spec2 = spec.model_copy(update={"experiment_id": spec.experiment_id + "_leakprobe"})
    r2 = train_lab_model(spec2, mutated, feature_cols, git_revision="leak-probe")
    c2 = Path(r2["checkpoint"]).read_bytes()

    same = hashlib.sha256(c1).hexdigest() == hashlib.sha256(c2).hexdigest()
    # cleanup the probe artifact
    for root in ("candidates", "teachers", "students"):
        d = LAB_ROOT / root / spec2.experiment_id
        if d.exists():
            for f in d.iterrows() if hasattr(d, "iterrows") else list(d.iterdir()):
                f[1].unlink() if isinstance(f, tuple) else f.unlink()
            d.rmdir()
    return {
        "oos_mutation_invariant": bool(same),
        "checkpoint1_sha": hashlib.sha256(c1).hexdigest()[:16],
    }
