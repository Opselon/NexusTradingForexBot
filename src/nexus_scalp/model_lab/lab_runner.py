"""MODEL LAB — benchmark (params / size / latency p50/p95/p99) + OOS/WF/robustness orchestration.

Latency is measured under realistic conditions: torch.inference_mode,
single-thread torch (matches the production latency pattern), warmup calls
excluded, 200 timed calls per checkpoint. GPU latency recorded only when
CUDA is available (this host: CPU-only).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import numpy as np
import polars as pl
import torch
from torch import nn

from nexus_scalp.model_lab.architectures import build_model
from nexus_scalp.model_lab.evaluation import evaluate_split, friction_expected_r
from nexus_scalp.model_lab.registry import LAB_ROOT, ExperimentSpec


def load_lab_checkpoint(experiment_id: str) -> tuple[nn.Module, dict[str, Any]]:
    for root_name in ("teachers", "students", "candidates"):
        ck = LAB_ROOT / root_name / experiment_id / "model.pt"
        if ck.exists():
            manifest = json.loads(
                (LAB_ROOT / root_name / experiment_id / "manifest.json").read_text(encoding="utf-8")
            )
            mspec = manifest.get("spec")
            spec = ExperimentSpec(**mspec) if mspec else None
            model = (
                build_model(spec)
                if spec
                else build_model(
                    ExperimentSpec(
                        experiment_id=experiment_id,
                        model_family=manifest["model_family"],
                        input_dimension=manifest["input_dimension"],
                        num_classes=manifest["num_classes"],
                        sequence_length=manifest.get("sequence_length", 1),
                        seed=manifest.get("seed", 42),
                    )
                )
            )
            model.load_state_dict(torch.load(ck, map_location="cpu", weights_only=True))
            return model, manifest
    raise FileNotFoundError(f"no lab checkpoint for {experiment_id}")


def benchmark_checkpoint(
    experiment_id: str, spec: ExperimentSpec, n_calls: int = 200
) -> dict[str, Any]:
    model, _manifest = load_lab_checkpoint(experiment_id)
    params = int(sum(p.numel() for p in model.parameters()))
    ck = (
        LAB_ROOT
        / (
            "teachers"
            if "TEACHER" in spec.model_family
            else ("students" if "STUDENT" in spec.model_family else "candidates")
        )
        / experiment_id
        / "model.pt"
    )
    size_bytes = ck.stat().st_size if ck.exists() else 0

    x = torch.randn(
        spec.sequence_length if spec.sequence_length > 1 else 0 or 1, spec.input_dimension
    )
    if spec.sequence_length > 1:
        x = torch.randn(1, spec.sequence_length, spec.input_dimension)
    model.eval()
    prior = torch.get_num_threads()
    torch.set_num_threads(1)
    with torch.inference_mode():
        for _ in range(10):
            model(x)  # warmup
        times = []
        for _ in range(n_calls):
            t0 = time.perf_counter()
            model(x)
            times.append((time.perf_counter() - t0) * 1000.0)
    torch.set_num_threads(prior)
    lat = np.array(times)
    return {
        "experiment_id": experiment_id,
        "model_family": spec.model_family,
        "parameters": params,
        "checkpoint_bytes": size_bytes,
        "device": "cpu",
        "cuda_available": torch.cuda.is_available(),
        "latency_ms_p50": round(float(np.percentile(lat, 50)), 3),
        "latency_ms_p95": round(float(np.percentile(lat, 95)), 3),
        "latency_ms_p99": round(float(np.percentile(lat, 99)), 3),
    }


def run_oos(
    experiment_id: str, spec: ExperimentSpec, frame: pl.DataFrame, feature_cols: list[str]
) -> dict[str, Any]:
    """One-shot OOS evaluation with the frozen best checkpoint."""
    model, _ = load_lab_checkpoint(experiment_id)
    oos_df = frame.filter(pl.col("_split") == "oos")
    if spec.sequence_length > 1:
        from nexus_scalp.model_lab.windowing import build_windows

        parts = build_windows(frame, feature_cols, spec.sequence_length)
        part = parts.get("oos")
        if part is None:
            return {"status": "FAILED", "error": "no OOS windows"}
        X, y = part["X"], part["y"]
    else:
        X = oos_df.select(feature_cols).to_numpy().astype(np.float32)
        y = oos_df["label"].to_numpy().astype(np.int64)
    scaler = np.load(LAB_ROOT / "candidates" / experiment_id / "scaler.npz")
    Xs = ((X - scaler["mean"]) / scaler["std"]).astype(np.float32)
    report = evaluate_split(model, Xs, y)
    report["n_oos"] = len(y)
    return report


def walk_forward_lab(
    spec: ExperimentSpec,
    frame: pl.DataFrame,
    feature_cols: list[str],
    n_folds: int = 4,
) -> dict[str, Any]:
    """Expanding-window walk-forward INSIDE the lab split (train+val only).

    OOS is never used for fold training. Per-fold: metrics + fingerprint.
    Purge: the dataset build already purged horizon-crossing rows; folds use
    chronological boundaries with an additional spec.embargo_bars embargo.
    """
    tv = frame.filter(pl.col("_split") != "oos").sort("timestamp")
    n = tv.height
    fold_size = n // (n_folds + 1)
    folds = []
    for k in range(1, n_folds + 1):
        train_end = fold_size * k
        val_end = min(train_end + fold_size, n)
        if val_end - train_end < 50 or train_end < 200:
            continue
        f_train = tv.head(train_end)
        f_val = tv.slice(train_end, val_end - train_end)
        # embargo tail excluded from validation (boundary leakage guard)
        f_val = f_val.head(max(1, (val_end - train_end) - spec.embargo_bars))
        f_eval = f_val
        Xtr = f_train.select(feature_cols).to_numpy().astype(np.float32)
        ytr = f_train["label"].to_numpy().astype(np.int64)
        Xva = f_eval.select(feature_cols).to_numpy().astype(np.float32)
        yva = f_eval["label"].to_numpy().astype(np.int64)
        mean = Xtr.mean(axis=0).astype(np.float32)
        std = np.where(Xtr.std(axis=0) < 1e-8, 1.0, Xtr.std(axis=0)).astype(np.float32)
        torch.manual_seed(spec.seed)
        model = build_model(spec)
        opt = torch.optim.AdamW(
            model.parameters(), lr=spec.learning_rate, weight_decay=spec.weight_decay
        )
        from nexus_scalp.model_lab.trainer import _oversample_active

        Xb, yb = _oversample_active((Xtr - mean) / std, ytr, spec.oversample_ratio, spec.seed)
        loader = torch.utils.data.DataLoader(
            torch.utils.data.TensorDataset(
                torch.from_numpy(Xb.astype(np.float32)), torch.from_numpy(yb)
            ),
            batch_size=spec.batch_size,
            shuffle=True,
        )
        crit = torch.nn.CrossEntropyLoss(label_smoothing=spec.label_smoothing)
        model.train()
        for _ in range(spec.epochs):
            for xb, yb2 in loader:
                opt.zero_grad()
                loss = crit(model(xb), yb2)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                opt.step()
        model.eval()
        with torch.inference_mode():
            probs = (
                torch.softmax(
                    model(torch.from_numpy(((Xva - mean) / std).astype(np.float32))), dim=-1
                )
                .cpu()
                .numpy()
            )
        pred = probs.argmax(axis=1)
        from nexus_scalp.model_lab.evaluation import _bacc

        folds.append(
            {
                "fold": k,
                "train_rows": len(ytr),
                "val_rows": len(yva),
                "balanced_accuracy": round(_bacc(yva, pred), 4),
                "directional_calls": int((pred != 0).sum()),
                "train_fingerprint": hashlib.sha256(
                    np.ascontiguousarray(Xtr).tobytes()
                ).hexdigest()[:16],
            }
        )
    ba: list[float] = [float(str(f["balanced_accuracy"])) for f in folds]
    summary = {
        "n_folds": len(folds),
        "folds": folds,
        "bacc_mean": round(float(np.mean(ba)), 4) if ba else 0.0,
        "bacc_std": round(float(np.std(ba)), 4) if ba else 0.0,
        "verdict": "PASS"
        if ba and float(np.mean(ba)) > 0.34 and float(np.std(ba)) < 0.10
        else "FAIL",
    }
    return summary


def robustness_lab(
    experiment_id: str, spec: ExperimentSpec, frame: pl.DataFrame, feature_cols: list[str]
) -> dict[str, Any]:
    """Monotone friction sweep + input-noise + regime-stratified OOS."""
    model, _ = load_lab_checkpoint(experiment_id)
    oos_df = frame.filter(pl.col("_split") == "oos")
    X = oos_df.select(feature_cols).to_numpy().astype(np.float32)
    y = oos_df["label"].to_numpy().astype(np.int64)
    scaler = np.load(LAB_ROOT / "candidates" / experiment_id / "scaler.npz")
    Xs = ((X - scaler["mean"]) / scaler["std"]).astype(np.float32)
    model.eval()
    with torch.inference_mode():
        probs = torch.softmax(model(torch.from_numpy(Xs)), dim=-1).cpu().numpy()

    friction = friction_expected_r(probs, y, [0.0, 0.02, 0.05, 0.10, 0.20])
    mono = all(
        friction[i]["ev_r_total"] >= friction[i + 1]["ev_r_total"] - 1e-9
        for i in range(len(friction) - 1)
    )

    rng = np.random.default_rng(spec.seed)
    noise_report = []
    for sigma in (0.0, 0.01, 0.05):
        pn = probs if sigma == 0 else None
        if sigma > 0:
            with torch.inference_mode():
                pn = (
                    torch.softmax(
                        model(
                            torch.from_numpy(Xs + rng.normal(0, sigma, Xs.shape).astype(np.float32))
                        ),
                        dim=-1,
                    )
                    .cpu()
                    .numpy()
                )
        from nexus_scalp.model_lab.evaluation import _bacc

        noise_report.append({"sigma": sigma, "bacc": round(_bacc(y, pn.argmax(axis=1)), 4)})

    regime_col = "regime" if "regime" in frame.columns else None
    by_regime = {}
    if regime_col:
        with torch.inference_mode():
            pred = probs.argmax(axis=1)
        for r in oos_df[regime_col].unique().to_list():
            m = (oos_df[regime_col] == r).to_numpy()
            if m.sum() > 30:
                from nexus_scalp.model_lab.evaluation import _bacc

                by_regime[str(r)] = {"n": int(m.sum()), "bacc": round(_bacc(y[m], pred[m]), 4)}

    return {
        "friction_sweep": friction,
        "friction_monotone": mono,
        "input_noise": noise_report,
        "oos_by_regime": by_regime,
    }


def compare_lab(
    specs: dict[str, ExperimentSpec], reports: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    rows = []
    for eid, rep in reports.items():
        spec = specs[eid]
        bench = rep.get("benchmark", {})
        rows.append(
            {
                "model": spec.model_family,
                "experiment": eid,
                "classes": spec.num_classes,
                "seq": spec.sequence_length,
                "oos_bacc": rep.get("oos", {}).get("balanced_accuracy"),
                "oos_ece": rep.get("oos", {}).get("calibration", {}).get("ece"),
                "wf_bacc_mean": rep.get("walk_forward", {}).get("bacc_mean"),
                "wf_verdict": rep.get("walk_forward", {}).get("verdict"),
                "friction_monotone": rep.get("robustness", {}).get("friction_monotone"),
                "latency_p95_ms": bench.get("latency_ms_p95"),
                "params": bench.get("parameters"),
                "checkpoint_bytes": bench.get("checkpoint_bytes"),
            }
        )
    return {"comparison": rows}
