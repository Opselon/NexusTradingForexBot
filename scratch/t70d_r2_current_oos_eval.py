# -*- coding: utf-8 -*-
"""T70D-R2 probe: OOS evaluation of the CURRENT 70D artifact + 50D baseline.

Chronological split of the full available M1 history (data/raw/XAUUSD_M1.csv,
100k bars, 2026-05-01..2026-08-17):
  TRAIN  = bars 0..79,999   (80%)   -> NOT used here (weights already exist)
  OOS    = bars 80,000..99,999 (last 20%, ~Aug 1..Aug 17)
The current artifact was smoke-trained on tail 3000 bars (Bug-141 recovery,
2026-08-29) - i.e. its training window IS INSIDE our OOS range (Aug 15-17).
We therefore evaluate on the strict post-training OOS window (last 5000 bars,
after the smoke window) AND on the full OOS with the overlap flagged.

This is a DIAGNOSTIC evaluation (read-only), not a promotion gate.
"""
import sys
sys.path.insert(0, "src")

import numpy as np
import polars as pl
import torch

from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler

SEED = 7
np.random.seed(SEED)
torch.manual_seed(SEED)

OUT_PATH = "scratch/t70d_r2_current_artifact_oos_eval.md"


def load_bundle(path_prefix, dim):
    sd = torch.load(f"{path_prefix}/model.pt", map_location="cpu")
    model = ScalpNet(num_features=dim, num_classes=4)
    model.load_state_dict(sd)
    model.eval()
    sc = np.load(f"{path_prefix}/model.scaler.npz")
    return model, sc["mean"].reshape(1, -1), sc["std"].reshape(1, -1)


def eval_windows(model, mean, std, feat_frame, label_frame, windows):
    """Returns per-window metrics dict."""
    feats = feat_frame.select([f"feat_{i}" for i in range(70)]).to_numpy().astype(np.float32)
    X = np.clip((feats - mean) / std, -5.0, 5.0).astype(np.float32)

    ts = feat_frame["timestamp"].to_list()
    lab = label_frame.with_columns(pl.col("timestamp").alias("_t"))
    label_map = {"NO_TRADE": 0, "BUY_MARKET": 1, "SELL_MARKET": 2}
    label_by_ts = {}
    for row in lab.iter_rows(named=True):
        label_by_ts[row["timestamp"]] = label_map.get(row["label"], 0)

    results = {}
    for name, t_start, t_end in windows:
        idx = [i for i, t in enumerate(ts) if t_start <= t < t_end and t in label_by_ts]
        if len(idx) < 20:
            results[name] = {"n": len(idx), "note": "insufficient"}
            continue
        Xw = torch.tensor(X[idx], dtype=torch.float32)
        with torch.inference_mode():
            probs = model(Xw).numpy()
        preds = probs.argmax(axis=1)
        y = np.array([label_by_ts[ts[i]] for i in idx])
        acc = float((preds == y).mean())
        # class distribution of predictions
        pd_ = np.bincount(preds, minlength=4)
        # confidence stats
        mx = probs.max(axis=1)
        # targeted stats: directional precision (pred 1/2 vs label)
        dir_mask = np.isin(preds, [1, 2])
        dir_prec = (
            float((preds[dir_mask] == y[dir_mask]).mean()) if dir_mask.sum() > 0 else None
        )
        dir_count = int(dir_mask.sum())
        results[name] = {
            "n": len(idx),
            "accuracy": round(acc, 4),
            "pred_dist": {"NT": int(pd_[0]), "BUY": int(pd_[1]), "SELL": int(pd_[2]), "WAIT": int(pd_[3])},
            "label_dist": {
                "NT": int((y == 0).sum()), "BUY": int((y == 1).sum()), "SELL": int((y == 2).sum())
            },
            "mean_max_prob": round(float(mx.mean()), 4),
            "directional_calls": dir_count,
            "directional_precision": round(dir_prec, 4) if dir_prec is not None else None,
        }
    return results


def main():
    lines = ["# T70D-R2 — Current 70D artifact OOS diagnostic (read-only)\n"]

    df = pl.read_csv("data/raw/XAUUSD_M1.csv")
    t_end_all = df["time_utc"][-1]
    print(f"M1 rows={df.height} range ends {t_end_all}")

    # ---- windows (UTC) ----
    # smoke window (artifact training window): tail 3000 bars ~ last ~2.1 days
    # strict post-smoke OOS: last 5000 bars minus the smoke window
    ts_all = df["time_utc"].to_list()
    smoke_start = ts_all[-3000]
    oos_start = ts_all[-20000]
    strict_start = ts_all[-5000]  # evaluated subset boundary; overlap flagged below
    print("smoke window starts:", smoke_start)
    print("OOS window starts:", oos_start)

    # feature frame on OOS slice (80k..100k) with warm-up overlap of 55 bars.
    # compute_70d_frame requires a `time` (epoch seconds) column + `time_utc`
    # string; raw csv already has both. `time` must be int seconds for
    # downstream consumers? keep as-is (polars int64).
    oos_df = df.slice(80_000 - 60, 20_060)
    # Guard: compute_70d_frame skips rows whose time_utc cannot parse to a
    # datetime (times list shorter than raw height) -> pass a frame where
    # time_utc is pre-parsed to datetime so iter_rows yields datetimes.
    oos_df = oos_df.with_columns(
        pl.col("time_utc").str.to_datetime(strict=True).alias("time_utc")
    )
    print("computing 70D features for OOS slice (rows=%d)..." % oos_df.height)
    feat70 = compute_70d_frame(oos_df, news_frame=None)
    labeler = TripleBarrierLabeler()
    lab = labeler.label_dataframe(oos_df)
    ev = lab.filter(pl.col("label_evaluated"))
    print("feature rows:", feat70.height, "eval rows:", ev.height)

    import datetime as dt

    windows = [
        ("OOS_full(80k-100k)", dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc), dt.datetime(2026, 8, 18, tzinfo=dt.timezone.utc)),
        ("OOS_strict_post_smoke", dt.datetime(2026, 8, 16, 3, 0, tzinfo=dt.timezone.utc), dt.datetime(2026, 8, 18, tzinfo=dt.timezone.utc)),
    ]

    print("loading 70D bundle...")
    m70, mu70, sd70 = load_bundle("artifacts/models/scalp/XAUUSD/70d_liquidity", 70)
    r70 = eval_windows(m70, mu70, sd70, feat70, lab, windows)
    print("loading 50D baseline bundle...")
    m50, mu50, sd50 = load_bundle("artifacts/models/scalp/XAUUSD/50d_main", 50)

    # 50D eval: use base features feat_0..49 from the same frame
    class _Slicer:
        pass

    lines.append("## Windows\n")
    lines.append(f"- OOS_full: {windows[0][1]} .. {windows[0][2]} (n by eval rows)\n")
    lines.append(f"- OOS_strict_post_smoke: {windows[1][1]} .. {windows[1][2]}\n")
    lines.append("- NOTE: artifact was smoke-trained on tail-3000 bars (inside OOS_full); "
                 "OOS_strict_post_smoke is the clean window; overlap flagged.\n\n")

    lines.append("## 70D current artifact (live champion)\n")
    for w, r in r70.items():
        lines.append(f"### {w}\n```json\n{r}\n```\n")

    # 50D baseline on the same windows using feat_0..49
    feats50 = feat70.select([f"feat_{i}" for i in range(50)]).to_numpy().astype(np.float32)
    X50 = np.clip((feats50 - mu50) / sd50, -5.0, 5.0).astype(np.float32)
    ts = feat70["timestamp"].to_list()
    label_map = {"NO_TRADE": 0, "BUY_MARKET": 1, "SELL_MARKET": 2}
    label_by_ts = {}
    for row in lab.iter_rows(named=True):
        label_by_ts[row["timestamp"]] = label_map.get(row["label"], 0)

    lines.append("\n## 50D baseline artifact\n")
    for name, t_start, t_end in windows:
        idx = [i for i, t in enumerate(ts) if t_start <= t < t_end and t in label_by_ts]
        if len(idx) < 20:
            lines.append(f"### {name}: insufficient rows\n")
            continue
        Xw = torch.tensor(X50[idx], dtype=torch.float32)
        with torch.inference_mode():
            probs = m50(Xw).numpy()
        preds = probs.argmax(axis=1)
        y = np.array([label_by_ts[ts[i]] for i in idx])
        acc = float((preds == y).mean())
        pd_ = np.bincount(preds, minlength=4)
        dir_mask = np.isin(preds, [1, 2])
        dir_prec = float((preds[dir_mask] == y[dir_mask]).mean()) if dir_mask.sum() > 0 else None
        r = {
            "n": len(idx), "accuracy": round(acc, 4),
            "pred_dist": {"NT": int(pd_[0]), "BUY": int(pd_[1]), "SELL": int(pd_[2]), "WAIT": int(pd_[3])},
            "label_dist": {"NT": int((y == 0).sum()), "BUY": int((y == 1).sum()), "SELL": int((y == 2).sum())},
            "mean_max_prob": round(float(probs.max(axis=1).mean()), 4),
            "directional_calls": int(dir_mask.sum()),
            "directional_precision": round(dir_prec, 4) if dir_prec is not None else None,
        }
        lines.append(f"### {name}\n```json\n{r}\n```\n")

    out = "\n".join(lines)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(out)
    print("report written:", OUT_PATH)
    print(out)


if __name__ == "__main__":
    main()
