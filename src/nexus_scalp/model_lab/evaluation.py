"""MODEL LAB — evaluation + calibration (CHG-0047).

Evaluation is trading-aware (never accuracy-only):
  per-class precision/recall, directional precision/recall, confusion,
  coverage, ECE / Brier / log-loss, and friction-monotone expected-R proxy
  using the dataset's own label horizon (label R is embedded in the
  triple-barrier construction via friction_usd=$0.35).

Calibration: temperature scaling fitted on the VAL split only; OOS is
scored once with the frozen temperature. Production confidence semantics
are untouched — calibration numbers live in the lab report only.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import torch
from torch import nn

from nexus_scalp.model_lab.registry import LAB_ROOT


def _bacc(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    n_cls = max(int(y_true.max()) + 1, int(y_pred.max()) + 1, 2)
    recalls = []
    for c in range(n_cls):
        m = y_true == c
        if m.sum() == 0:
            continue
        recalls.append(float((y_pred[m] == c).mean()))
    return float(np.mean(recalls)) if recalls else 0.0


def class_metrics(y_true: np.ndarray, y_pred: np.ndarray, probs: np.ndarray) -> dict[str, Any]:
    names = ["NO_TRADE", "BUY", "SELL"][: probs.shape[1]]
    per_class = {}
    for i, name in enumerate(names):
        tp = float(((y_pred == i) & (y_true == i)).sum())
        fp = float(((y_pred == i) & (y_true != i)).sum())
        fn = float(((y_pred != i) & (y_true == i)).sum())
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        per_class[name] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "support": int((y_true == i).sum()),
        }
    # directional = predicted trade (BUY or SELL) vs truth
    pred_dir = y_pred != 0
    true_dir = y_true != 0
    dir_prec = float((y_true[pred_dir] != 0).mean()) if pred_dir.any() else 0.0
    dir_rec = float((y_pred[true_dir] != 0).mean()) if true_dir.any() else 0.0
    conf = np.zeros((probs.shape[1], probs.shape[1]), dtype=int)
    for t, p in zip(y_true, y_pred, strict=False):
        conf[t, p] += 1
    return {
        "balanced_accuracy": round(_bacc(y_true, y_pred), 4),
        "per_class": per_class,
        "directional_precision": round(dir_prec, 4),
        "directional_recall": round(dir_rec, 4),
        "directional_calls": int(pred_dir.sum()),
        "coverage_pct": round(100 * float(pred_dir.mean()), 2),
        "confusion_matrix": conf.tolist(),  # rows=true NO_TRADE/BUY/SELL
    }


def ece(probs: np.ndarray, y_true: np.ndarray, n_bins: int = 10) -> float:
    conf = probs.max(axis=1)
    pred = probs.argmax(axis=1)
    correct = (pred == y_true).astype(np.float64)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    total = len(conf)
    e = 0.0
    for i in range(n_bins):
        m = (conf > bins[i]) & (conf <= bins[i + 1])
        if m.sum() == 0:
            continue
        e += (m.sum() / total) * abs(correct[m].mean() - conf[m].mean())
    return round(float(e), 4)


def brier(probs: np.ndarray, y_true: np.ndarray) -> float:
    onehot = np.zeros_like(probs)
    onehot[np.arange(len(y_true)), y_true] = 1.0
    return round(float(np.mean((probs - onehot) ** 2)), 5)


def log_loss(probs: np.ndarray, y_true: np.ndarray, eps: float = 1e-12) -> float:
    p = np.clip(probs[np.arange(len(y_true)), y_true], eps, 1.0)
    return round(float(-np.mean(np.log(p))), 5)


def evaluate_split(
    model: nn.Module, X: np.ndarray, y: np.ndarray, device: str = "cpu"
) -> dict[str, Any]:
    model.eval()
    with torch.inference_mode():
        logits = model(torch.from_numpy(X.astype(np.float32)))
        probs = torch.softmax(logits, dim=-1).cpu().numpy()
    pred = probs.argmax(axis=1)
    out = class_metrics(y, pred, probs)
    out["calibration"] = {
        "ece": ece(probs, y),
        "brier": brier(probs, y),
        "log_loss": log_loss(probs, y),
    }
    out["_probs_head"] = probs
    return out


def fit_temperature(model: nn.Module, X_val: np.ndarray, y_val: np.ndarray) -> float:
    """Temperature scaling fitted on VAL only (Guo et al. 2017, 1-D search)."""
    model.eval()
    with torch.inference_mode():
        logits = model(torch.from_numpy(X_val.astype(np.float32))).cpu().numpy()
    best_t, best_nll = 1.0, float("inf")
    for t in np.linspace(0.5, 4.0, 36):
        p = np.exp(logits - logits.max(axis=1, keepdims=True))
        p = p / p.sum(axis=1, keepdims=True)
        p = p ** (1.0 / t)
        p = p / p.sum(axis=1, keepdims=True)
        nll = log_loss(p, y_val)
        if nll < best_nll:
            best_t, best_nll = float(t), nll
    return round(best_t, 3)


def friction_expected_r(
    probs: np.ndarray, y_true: np.ndarray, frictions_r: list[float]
) -> list[dict[str, Any]]:
    """Monotone friction sweep on the directional calls.

    Proxy EV: a correct directional call earns +1R scaled by class-prior edge
    (BUY/SELL symmetric), a wrong call costs -1R; friction subtracts directly
    in R. Directional abstentions (NO_TRADE) cost nothing. Monotonicity is
    the claim under test, not the absolute R.
    """
    pred = probs.argmax(axis=1)
    dir_mask = pred != 0
    out = []
    y_dir = (y_true != 0).astype(np.int64)
    for f in frictions_r:
        ev = 0.0
        n_calls = 0
        for i in np.where(dir_mask)[0]:
            n_calls += 1
            won = y_dir[i] == 1  # predicted trade and truth was a trade
            ev += (1.0 if won else -1.0) - f
        out.append(
            {
                "friction_r": f,
                "n_calls": n_calls,
                "ev_r_total": round(ev, 2),
                "ev_r_per_call": round(ev / n_calls, 4) if n_calls else 0.0,
            }
        )
    return out


def save_eval_report(experiment_id: str, report: dict[str, Any]) -> str:
    d = LAB_ROOT / "candidates" / experiment_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "evaluation.json"
    p.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return str(p)
