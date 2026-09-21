"""Phase 0 items 8-12: controlled retrain probe on the real 70D dataset.

Faithful REPLICATION of the production trainer's learning semantics
(WalkForwardTrainer.train_and_validate), as a self-contained probe so the
50D ablation C can run without the production emission gate's hard 70D
requirement aborting the diagnostic.

Replicated exactly from src/nexus_scalp/training/walk_forward_trainer.py:
  * blocked walk-forward geometry with per-fold purge + embargo
      _split_fold_with_embargo: train = floor(len*train_ratio),
      test starts after purge_gap_bars, test end = len - embargo_bars
  * per-fold z-score scaler, std floored at 1e-3, clip to [-5,+5]
  * class-balanced weights (beta=0.99) + active_class_boost 3.0 on BUY/SELL
  * FocalLossWithSmoothing(gamma=2.0, label_smoothing=0.08)
  * AdamW(lr, weight_decay=1e-4) + CosineAnnealingLR(T_max=epochs)
  * early stopping patience 3 on val loss; best state restored
  * final FULL_TRAIN refit on all trainable rows with the same recipe
  * torch.Generator seeded per DataLoader for shuffle determinism
  * the model checkpoint / scaler saved to an ISOLATED research path only.

ABLATIONS
  A = 70D as-shipped (Base + NEWS all-zero + Liquidity)
  B = 70D with NEWS 50..59 repopulated (documented diagnostic synthetic block
      derived causally from base features; the real news DB has ZERO overlap
      with the dataset window, so real news cannot be attached)
  C = 50D baseline (Base only; no NEWS, no LIQUIDITY). The train/serve
      news-coverage mismatch does NOT apply to C because NEWS is absent.

ISOLATION: outputs under artifacts/research/phase0_20260921/runs/.
The champion artifact is NEVER opened for write (load-only for the ceiling
probe in champion_ceiling.py). No registry / settings / guard mutation.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl
import torch
import torch.nn.functional as F
from torch import nn
from torch.utils.data import DataLoader, Dataset

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

OUT = REPO / "artifacts" / "research" / "phase0_20260921"
RUNS = OUT / "runs"
RUNS.mkdir(parents=True, exist_ok=True)

DATASET_ID = "ds_70d_clean_m1_20260904"
DATASET_SHA = "3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe"
SCHEMA_HASH = "235b8fccc96b7e0e"
EFFECTIVE_GATE = 0.50  # live model.confidence_threshold 0.40 + range_confidence_penalty 0.10

FEAT70 = [f"feat_{i}" for i in range(70)]
FEAT50 = [f"feat_{i}" for i in range(50)]


# ---------------------------------------------------------------------------
# ablation B: NEWS repopulation (diagnostic only)
# ---------------------------------------------------------------------------
def repopulate_news(frame: pl.DataFrame, seed: int) -> pl.DataFrame:
    """Honest, causal, seed-deterministic NEWS 50..59 repopulation.

    The real news DB has ZERO coverage over the dataset window
    (news_analysis starts 2026-08-21 08:52 UTC; the dataset ends 2026-08-17
    19:24 UTC). Attaching "real" values would require fabricating data, which
    is forbidden by contract. So ablation B uses a documented SYNTHETIC news
    block derived ONLY from causal in-frame base features (sessions, lag
    return, volume z). Purpose: measure whether a NON-CONSTANT news block
    changes model behavior at all (i.e. is the dead news block the limiter,
    or is the whole feature space uninformative). Never a production input.
    """
    rng = np.random.default_rng(seed)
    n = frame.height
    sess_ld = frame["feat_17"].to_numpy().astype(np.float64)  # session_london
    sess_ny = frame["feat_18"].to_numpy().astype(np.float64)  # session_ny
    mom = frame["feat_20"].to_numpy().astype(np.float64)  # lag_1_log_return
    vol = np.abs(frame["feat_24"].to_numpy().astype(np.float64))  # lag_1_volume_z
    intensity = np.clip(0.5 * (sess_ld + sess_ny) / 2.0 + 0.5 * np.clip(vol, 0, 1), 0.0, 1.0)
    drift = np.clip(np.abs(mom) * 2.0, 0.0, 1.0)
    cols = {
        "feat_50": (intensity > 0.55).astype(np.float64),
        "feat_51": np.clip(intensity, 0.0, 1.0),
        "feat_52": np.clip(intensity * 0.7, 0.0, 1.0),
        "feat_53": np.clip(drift * (mom > 0), 0.0, 1.0),
        "feat_54": np.clip(drift * (mom < 0), 0.0, 1.0),
        "feat_55": np.clip(np.abs(mom) * 1.5, 0.0, 1.0),
        "feat_56": np.round(intensity * 3.0) / 3.0,
        "feat_57": np.clip(1.0 - np.abs(mom), 0.0, 1.0),
        "feat_58": np.clip(0.3 + 0.5 * intensity, 0.0, 1.0),
        "feat_59": np.clip(np.round(intensity * 2.0), 0.0, 3.0),
    }
    out = frame.clone()
    for k, col in cols.items():
        jittered = np.clip(col + rng.normal(0, 0.02, n), 0.0, 3.0)
        out = out.with_columns(pl.Series(k, jittered.astype(np.float64)))
    return out


# ---------------------------------------------------------------------------
# faithful replicas of production training pieces
# ---------------------------------------------------------------------------
class FocalLossWithSmoothing(nn.Module):
    """Copy of WalkForwardTrainer.FocalLossWithSmoothing (identical math)."""

    def __init__(
        self, alpha=None, gamma: float = 2.0, label_smoothing: float = 0.08, reduction: str = "mean"
    ) -> None:
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.label_smoothing = label_smoothing
        self.reduction = reduction

    def forward(self, logits, targets, sample_weights=None):
        num_classes = logits.shape[1]
        log_probs = F.log_softmax(logits, dim=-1)
        probs = torch.exp(log_probs)
        with torch.no_grad():
            target_probs = torch.full_like(log_probs, self.label_smoothing / num_classes)
            target_probs.scatter_(
                1,
                targets.unsqueeze(1),
                1.0 - self.label_smoothing + (self.label_smoothing / num_classes),
            )
        p_t = probs.gather(1, targets.unsqueeze(1)).squeeze(1)
        focal_weight = (1.0 - p_t) ** self.gamma
        ce_loss = -(target_probs * log_probs).sum(dim=-1)
        loss = focal_weight * ce_loss
        if sample_weights is not None:
            loss = loss * sample_weights
        if self.alpha is not None:
            alpha = self.alpha.to(logits.device)
            at = alpha.gather(0, targets)
            loss = at * loss
        if self.reduction == "mean":
            return loss.mean()
        if self.reduction == "sum":
            return loss.sum()
        return loss


class _XY(Dataset):
    def __init__(self, X, y):
        self.X, self.y = X, y

    def __len__(self):
        return len(self.y)

    def __getitem__(self, i):
        return self.X[i], self.y[i]


def build_class_weights(y: np.ndarray, active_class_boost: float = 3.0) -> torch.Tensor:
    """Copy of WalkForwardTrainer._build_class_weights (non-online branch)."""
    num_classes = 3
    class_counts = np.bincount(y, minlength=num_classes)[:num_classes]
    beta = 0.99
    effective_num = np.maximum(1.0 - np.power(beta, class_counts), 1e-5)
    cb_weights = (1.0 - beta) / effective_num
    for idx in (1, 2):
        cb_weights[idx] *= active_class_boost
    mean_w = cb_weights.mean()
    weights = (cb_weights / mean_w if mean_w > 0 else cb_weights).astype(np.float32)
    return torch.tensor(weights, dtype=torch.float32)


def fit_scaler(X_raw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = np.mean(X_raw, axis=0, keepdims=True).astype(np.float32)
    std = np.maximum(np.std(X_raw, axis=0, keepdims=True).astype(np.float32), 1e-3)
    return mean, std


def transform(X_raw, mean, std, lo=-5.0, hi=5.0):
    return np.clip((X_raw - mean) / std, lo, hi).astype(np.float32)


def split_fold_with_embargo(n: int, train_ratio: float, purge_gap_bars: int, embargo_bars: int):
    """Copy of WalkForwardTrainer._split_fold_with_embargo."""
    train_end = int(n * train_ratio)
    test_start = train_end + purge_gap_bars
    test_end = n - embargo_bars
    return train_end, test_start, test_end


def train_one_epoch(model, loader, optimizer, criterion, device="cpu"):
    model.train()
    total, count = 0.0, 0
    for xb, yb in loader:
        optimizer.zero_grad()
        logits = model(xb, return_logits=True)
        loss = criterion(logits, yb)
        loss.backward()
        optimizer.step()
        total += float(loss.item()) * len(yb)
        count += len(yb)
    return total / max(1, count)


def eval_loss(model, loader, criterion):
    model.eval()
    total, count = 0.0, 0
    with torch.inference_mode():
        for xb, yb in loader:
            logits = model(xb, return_logits=True)
            loss = criterion(logits, yb)
            total += float(loss.item()) * len(yb)
            count += len(yb)
    return total / max(1, count)


def predict_classes(model, loader):
    model.eval()
    out = []
    with torch.inference_mode():
        for xb, _ in loader:
            out.extend(model(xb, return_logits=True).argmax(-1).tolist())
    return out


def make_loader(X, y, batch, shuffle, seed):
    g = torch.Generator()
    g.manual_seed(seed)
    return DataLoader(_XY(X, y), batch_size=batch, shuffle=shuffle, generator=g)


# ---------------------------------------------------------------------------
# the probe run
# ---------------------------------------------------------------------------
def run_one(
    name,
    seed,
    frame,
    feat_cols,
    folds,
    epochs,
    batch,
    holdout_frac,
    train_ratio=0.70,
    purge_gap_bars=15,
    embargo_bars=15,
):
    from nexus_scalp.models.scalp_net import ScalpNet

    run_dir = RUNS / f"{name}_seed{seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(seed)
    np.random.seed(seed)

    # chronological split: head = training pool (the probe trains inside this),
    # tail = untouched held-out set
    ts = np.array([t.timestamp() for t in frame["timestamp"].to_list()], dtype=np.float64)
    order = np.argsort(ts, kind="stable")
    n = len(order)
    ho_n = round(n * holdout_frac)
    tr_idx = order[: n - ho_n]
    ho_idx = order[n - ho_n :]
    train_pool = frame[list(tr_idx)]
    hold_frame = frame[list(ho_idx)]

    X_pool = train_pool.select(feat_cols).to_numpy().astype(np.float32)
    y_pool = np.asarray(train_pool["label"].to_list(), dtype=np.int64)
    assert np.all(np.isfinite(X_pool)), "non-finite feature in training pool"

    n_pool = len(y_pool)
    fold_size = n_pool // folds
    fold_meta = []
    oos_preds, oos_targets = [], []
    for fold in range(folds):
        s = fold * fold_size
        e = n_pool if fold == folds - 1 else (fold + 1) * fold_size
        fX, fy = X_pool[s:e], y_pool[s:e]
        if len(fX) < 10:
            continue
        tr_end, te_start, te_end = split_fold_with_embargo(
            len(fX), train_ratio, purge_gap_bars, embargo_bars
        )
        Xtr, ytr = fX[:tr_end], fy[:tr_end]
        Xte, yte = fX[te_start:te_end], fy[te_start:te_end]
        if len(Xtr) < 50 or len(Xte) < 20:
            continue
        mean, std = fit_scaler(Xtr)
        Xtr_s = transform(Xtr, mean, std)
        Xte_s = transform(Xte, mean, std)
        weights = build_class_weights(ytr)
        criterion = FocalLossWithSmoothing(alpha=weights, gamma=2.0, label_smoothing=0.08)
        model = ScalpNet(num_features=len(feat_cols), num_classes=3)
        optimizer = torch.optim.AdamW(model.parameters(), lr=5e-4, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
        best_val, best_state, patience, best_epoch = float("inf"), None, 0, 0
        tr_losses, va_losses = [], []
        for ep in range(epochs):
            tl = train_one_epoch(
                model,
                make_loader(Xtr_s, ytr, batch, True, seed + fold * 100 + ep),
                optimizer,
                criterion,
            )
            scheduler.step()
            vl = eval_loss(
                model, make_loader(Xte_s, yte, batch, False, seed + fold * 100 + ep), criterion
            )
            tr_losses.append(round(tl, 6))
            va_losses.append(round(vl, 6))
            if vl < best_val:
                best_val, best_state, best_epoch, patience = (
                    vl,
                    {k: v.clone() for k, v in model.state_dict().items()},
                    ep + 1,
                    0,
                )
            else:
                patience += 1
                if patience >= 3:
                    break
        if best_state is not None:
            model.load_state_dict(best_state)
        preds = predict_classes(model, make_loader(Xte_s, yte, batch, False, seed))
        oos_preds.extend(preds)
        oos_targets.extend(yte[: len(preds)].tolist())
        fold_meta.append(
            {
                "fold": fold + 1,
                "train_rows": len(Xtr),
                "test_rows": len(Xte),
                "purge_rows": int(te_start - tr_end),
                "embargo_rows": int(len(fX) - te_end),
                "best_epoch": best_epoch,
                "best_val_loss": round(float(best_val), 6),
                "train_losses": tr_losses,
                "val_losses": va_losses,
            }
        )

    # final FULL_TRAIN refit (production semantics: all trainable rows)
    full_mean, full_std = fit_scaler(X_pool)
    X_full = transform(X_pool, full_mean, full_std)
    final_model = ScalpNet(num_features=len(feat_cols), num_classes=3)
    opt = torch.optim.AdamW(final_model.parameters(), lr=5e-4, weight_decay=1e-4)
    sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    crit = FocalLossWithSmoothing(
        alpha=build_class_weights(y_pool), gamma=2.0, label_smoothing=0.08
    )
    final_losses = []
    for ep in range(epochs):
        fl = train_one_epoch(
            final_model, make_loader(X_full, y_pool, batch, True, seed + 9000 + ep), opt, crit
        )
        sch.step()
        final_losses.append(round(fl, 6))

    # persist the probe bundle in the ISOLATED research dir (never production)
    state = {k: v.detach().cpu() for k, v in final_model.state_dict().items()}
    torch.save(state, run_dir / "model.pt")
    np.savez(
        run_dir / "model.scaler.npz",
        mean=full_mean.astype(np.float32),
        std=full_std.astype(np.float32),
    )

    # ---- held-out evaluation ----
    Xh = hold_frame.select(feat_cols).to_numpy().astype(np.float32)
    yh = np.asarray(hold_frame["label"].to_list(), dtype=np.int64)
    Xhs = transform(Xh, full_mean, full_std)
    final_model.eval()
    parts = []
    with torch.inference_mode():
        for i in range(0, len(Xhs), 8192):
            parts.append(final_model(torch.tensor(Xhs[i : i + 8192]), return_logits=True).numpy())
    logits_ho = np.concatenate(parts, axis=0)
    probs_ho = softmax_np(logits_ho)

    holdout = compute_metrics(probs_ho, yh, gate=EFFECTIVE_GATE)
    ceiling = reachable_ceiling(final_model, seed=seed)

    # block sensitivities (70D only)
    sens = None
    if len(feat_cols) == 70:
        probes = {"news_50_59_shift_1": (50, 60), "liquidity_60_69_shift_1": (60, 70)}
        sens = {}
        for label, (a, b) in probes.items():
            Xp = Xhs.copy()
            Xp[:, a:b] = Xp[:, a:b] + 1.0
            with torch.inference_mode():
                pp = softmax_np(final_model(torch.tensor(Xp), return_logits=True).numpy())
            sens[label] = {
                "mean_abs_prob_change": round(float(np.abs(pp - probs_ho).mean()), 6),
                "max_abs_prob_change": round(float(np.abs(pp - probs_ho).max()), 6),
            }

    # in-sample metrics (overfit check)
    parts = []
    with torch.inference_mode():
        for i in range(0, len(X_full), 8192):
            parts.append(
                final_model(torch.tensor(X_full[i : i + 8192]), return_logits=True).numpy()
            )
    in_sample = compute_metrics(
        softmax_np(np.concatenate(parts, axis=0)), y_pool, gate=EFFECTIVE_GATE
    )

    # pooled OOS fold metrics (the production trainer's own OOS evidence)
    oos_p = np.asarray(oos_preds, dtype=np.int64)
    oos_t = np.asarray(oos_targets, dtype=np.int64)
    oos_metrics = {
        "n": len(oos_p),
        "oos_accuracy": round(float((oos_p == oos_t).mean()), 6) if len(oos_p) else None,
        "pred_class_counts": {str(c): int((oos_p == c).sum()) for c in range(3)},
        "true_class_counts": {str(c): int((oos_t == c).sum()) for c in range(3)},
    }

    report = {
        "ablation": name,
        "seed": seed,
        "training_config": {
            "trainer": "phase0 probe replica of WalkForwardTrainer.train_and_validate",
            "folds": folds,
            "epochs_per_fold": epochs,
            "batch_size": batch,
            "learning_rate": 5e-4,
            "optimizer": "AdamW(weight_decay=1e-4)",
            "scheduler": "CosineAnnealingLR(T_max=epochs)",
            "loss": "FocalLossWithSmoothing(gamma=2.0, label_smoothing=0.08, class-balanced alpha, active_class_boost=3.0)",
            "train_ratio_in_fold": train_ratio,
            "purge_gap_bars": purge_gap_bars,
            "embargo_bars": embargo_bars,
            "early_stopping_patience": 3,
            "feature_scaling": "z-score per fold, std floored 1e-3, clip [-5,+5]",
            "final_fit": "FULL_TRAIN on all trainable rows",
        },
        "feature_count": len(feat_cols),
        "feature_schema_id": "scalp_v3" if len(feat_cols) == 70 else "scalp_v1",
        "dataset_id": DATASET_ID,
        "dataset_sha256": DATASET_SHA,
        "train_pool_rows": int(train_pool.height),
        "holdout_rows": int(hold_frame.height),
        "train_pool_ts_range": [str(train_pool["timestamp"][0]), str(train_pool["timestamp"][-1])],
        "holdout_ts_range": [str(hold_frame["timestamp"][0]), str(hold_frame["timestamp"][-1])],
        "folds_meta": fold_meta,
        "final_fit_losses": final_losses,
        "pooled_oos": oos_metrics,
        "holdout": holdout,
        "in_sample": in_sample,
        "reachable_ceiling": ceiling,
        "block_sensitivity": sens,
        "effective_gate": EFFECTIVE_GATE,
        "output_dir": str(run_dir),
    }
    (run_dir / "report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    return report


def softmax_np(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def compute_metrics(probs: np.ndarray, y: np.ndarray, *, gate: float = EFFECTIVE_GATE) -> dict:
    n = len(y)
    pred = probs.argmax(axis=1)
    maxp = probs.max(axis=1)
    ent = -(probs * np.log(np.clip(probs, 1e-12, 1.0))).sum(axis=1)
    active = probs[:, 1:3]
    dir_max = active.max(axis=1)
    active_pred = np.where(active[:, 1] > active[:, 0], 1, 2)
    acc = float((pred == y).mean()) if n else None
    recs, f1s = [], []
    for c in range(3):
        m = y == c
        if int(m.sum()) == 0:
            recs.append(None)
            f1s.append(None)
            continue
        rec = float((pred[m] == c).mean())
        pp = pred == c
        prec = float((y[pp] == c).mean()) if int(pp.sum()) else 0.0
        recs.append(rec)
        f1s.append(2.0 * prec * rec / max(prec + rec, 1e-12))
    bal = float(np.mean([v for v in recs if v is not None])) if n else None
    mf1 = float(np.mean([v for v in f1s if v is not None])) if n else None
    dmask = pred != 0
    d_acc = float((active_pred[dmask] == y[dmask]).mean()) if int(dmask.sum()) else None
    return {
        "n": int(n),
        "class_distribution_pred": {str(c): int((pred == c).sum()) for c in range(3)},
        "class_distribution_true": {str(c): int((y == c).sum()) for c in range(3)},
        "accuracy": round(acc, 6) if acc is not None else None,
        "balanced_accuracy": round(bal, 6) if bal is not None else None,
        "macro_f1": round(mf1, 6) if mf1 is not None else None,
        "per_class_recall": [round(v, 6) if v is not None else None for v in recs],
        "per_class_f1": [round(v, 6) if v is not None else None for v in f1s],
        "majority_class_baseline_accuracy": round(float(np.bincount(y, minlength=3).max() / n), 6),
        "mean_max_probability": round(float(maxp.mean()), 6),
        "max_max_probability": round(float(maxp.max()), 6),
        "min_max_probability": round(float(maxp.min()), 6),
        "mean_entropy_nats": round(float(ent.mean()), 6),
        "mean_entropy_normalized": round(float(ent.mean() / np.log(2.0)), 6),
        "mean_probability_per_class": [round(float(probs[:, c].mean()), 6) for c in range(3)],
        "max_probability_percentiles": {
            f"p{p}": round(float(np.percentile(maxp, p)), 6)
            for p in (1, 5, 10, 25, 50, 75, 90, 95, 99, 100)
        },
        "directional_confidence_stats": {
            "mean": round(float(dir_max.mean()), 6),
            "max": round(float(dir_max.max()), 6),
            "p50": round(float(np.percentile(dir_max, 50)), 6),
            "p90": round(float(np.percentile(dir_max, 90)), 6),
            "p99": round(float(np.percentile(dir_max, 99)), 6),
            "p100": round(float(np.percentile(dir_max, 100)), 6),
        },
        "rows_over_effective_gate": int((maxp >= gate).sum()),
        "pct_rows_over_effective_gate": round(100.0 * float((maxp >= gate).mean()), 4),
        "rows_directional_over_gate": int((dir_max >= gate).sum()),
        "pct_directional_over_gate": round(100.0 * float((dir_max >= gate).mean()), 4),
        "directional_accuracy_on_active_preds": round(d_acc, 6) if d_acc is not None else None,
        "n_active_preds": int(dmask.sum()),
    }


def reachable_ceiling(
    net, seed: int = 1234, draws: int = 200, batch: int = 4000, lo: float = -3.0, hi: float = 3.0
) -> dict:
    g = torch.Generator().manual_seed(seed)
    best, best_probs = -1.0, None
    for _ in range(draws):
        X = torch.empty(batch, net.num_features).uniform_(lo, hi, generator=g)
        with torch.inference_mode():
            probs = torch.softmax(net(X, return_logits=True), dim=-1)
        mp, _ = probs.max(dim=-1)
        i = int(torch.argmax(mp))
        if float(mp[i]) > best:
            best, best_probs = float(mp[i]), probs[i].tolist()
    return {
        "max_directional_confidence": round(best, 6),
        "argmax_probs": [round(v, 6) for v in (best_probs or [])],
        "samples_drawn": draws * batch,
        "space": f"raw uniform [{lo},{hi}] (unscaled contract box)",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 7, 2024])
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--holdout-frac", type=float, default=0.25)
    ap.add_argument("--ablations", nargs="+", default=["A", "B", "C"])
    args = ap.parse_args()

    ds_path = REPO / "artifacts" / "model_generation" / "datasets" / DATASET_ID / "dataset.parquet"
    df = pl.read_parquet(ds_path).sort("timestamp")
    frames = {"A": df, "B": repopulate_news(df, seed=1234), "C": df}
    cols = {"A": FEAT70, "B": FEAT70, "C": FEAT50}

    results: dict[str, list] = {}
    for name in args.ablations:
        for seed in args.seeds:
            tag = f"{name}_seed{seed}"
            print(f"[PHASE0] === ablation {name} seed {seed} ===", flush=True)
            t0 = time.perf_counter()
            try:
                r = run_one(
                    name,
                    seed,
                    frames[name],
                    cols[name],
                    args.folds,
                    args.epochs,
                    args.batch,
                    args.holdout_frac,
                )
            except Exception as exc:
                import traceback

                traceback.print_exc()
                r = {"ablation": name, "seed": seed, "FAILED": str(exc)}
            r["total_seconds"] = round(time.perf_counter() - t0, 1)
            results.setdefault(name, []).append(r)
            (RUNS / f"{tag}_summary.json").write_text(
                json.dumps(r, indent=2, default=str), encoding="utf-8"
            )
            h = r.get("holdout", {})
            print(
                f"[PHASE0] {tag} done in {r['total_seconds']}s: "
                f"acc={h.get('accuracy')} bal={h.get('balanced_accuracy')} "
                f"f1={h.get('macro_f1')} pct_over_gate={h.get('pct_rows_over_effective_gate')} "
                f"ceil={r.get('reachable_ceiling', {}).get('max_directional_confidence')}",
                flush=True,
            )

    (OUT / "training_probe_results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    print("[PHASE0] ALL DONE ->", OUT / "training_probe_results.json")


if __name__ == "__main__":
    main()
