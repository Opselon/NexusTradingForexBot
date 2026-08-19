"""STEP-07 — temporal experiment matrix + stability controller evaluation.

Fair comparison (brief 21/22) of feature representations on the SAME real
XAUUSD M1 causal frame, SAME labels, SAME model architecture, SAME training
budget:

    A = 70D canonical (scalp_v3)                          [baseline]
    B = A + lag1/lag2 (temporal dims subset: lags)
    C = A + lag + delta1
    D = A + lag + persistence
    E = A + lag + delta + persistence + tsc (full 92D candidate)

Only the feature representation differs (brief 22). Each cell is trained
with CandidateTrainer on the same frame (feature columns selected per cell),
then evaluated on the same validation split. Additionally, the RAW vs
STABILIZED decision sequences are compared on the full frame using the
DecisionStabilityController (flip metrics before/after).

Output: artifacts/forensics/temporal_experiment_matrix.json
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, "src")

from nexus_scalp.features.temporal import (
    TEMPORAL_FEATURE_NAMES,
    TemporalLiquidityTracker,
)
from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.experiment_factory import ExperimentFactory
from nexus_scalp.model_generation.model_factory import ModelFactory
from nexus_scalp.model_generation.training import CandidateTrainer
from nexus_scalp.signals.stability_controller import (
    DecisionStabilityController,
)

REPO = Path(__file__).resolve().parents[1]
FRAME = REPO / "artifacts/forensics/temporal_frame_4000.parquet"
OUT = REPO / "artifacts/forensics/temporal_experiment_matrix.json"

#: Temporal dim name groups (indices into TEMPORAL_FEATURE_NAMES).
LAG_NAMES = [n for n in TEMPORAL_FEATURE_NAMES if "lag" in n]
DELTA_NAMES = [n for n in TEMPORAL_FEATURE_NAMES if "delta" in n]
PERSIST_NAMES = [n for n in TEMPORAL_FEATURE_NAMES if "persistence" in n]
TSC_NAMES = [n for n in TEMPORAL_FEATURE_NAMES if "time_since" in n or "state_duration" in n]

TRAIN_CFG = {"epochs": 8, "batch_size": 256, "learning_rate": 0.001, "seed": 42}


def build_temporal_frame() -> pl.DataFrame:
    """Adds the 22 temporal dims (feat_70..feat_91) to the canonical 70D frame."""
    df = pl.read_parquet(FRAME)
    if df.height == 0:
        raise RuntimeError("temporal frame missing — run temporal_step01b first")
    tracker = TemporalLiquidityTracker()
    liq_cols = [f"feat_{i}" for i in range(60, 70)]
    liq_np = df.select(liq_cols).to_numpy()
    ts_col = df["timestamp"].to_list()
    rows: list[list[float]] = []
    for i in range(df.height):
        liq = [float(x) for x in liq_np[i]]
        snap = tracker.update(liq, str(ts_col[i]))
        rows.append(list(snap.values))
    temp_np = np.asarray(rows, dtype=np.float64)
    out = df.clone()
    for j, _name in enumerate(TEMPORAL_FEATURE_NAMES):
        out = out.with_columns(pl.Series(f"feat_{70 + j}", temp_np[:, j]))
    return out


def softmax(v: np.ndarray) -> np.ndarray:
    e = np.exp(v - v.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def main() -> None:
    df = build_temporal_frame()
    print(f"[EXP] frame rows={df.height} cols={len(df.columns)}")

    # labels (already in frame? rebuild deterministically)
    if "label" not in df.columns:
        labeler = TripleBarrierLabeler()
        labeled = labeler.label_dataframe(df)
        from nexus_scalp.model_generation.models import default_label_schema

        ls = default_label_schema()
        lbl = [ls.encode(s) for s in labeled["label"].to_list()]
        df = labeled.with_columns(pl.Series("label", lbl))
    print("[EXP] labels:", {int(k): int(v) for k, v in zip(
        *np.unique(df["label"].to_numpy(), return_counts=True), strict=False)})

    cells = {
        "A_70d": [],
        "B_70d_lag": LAG_NAMES,
        "C_70d_lag_delta": LAG_NAMES + DELTA_NAMES,
        "D_70d_lag_persist": LAG_NAMES + PERSIST_NAMES,
        "E_70d_full": LAG_NAMES + DELTA_NAMES + PERSIST_NAMES + TSC_NAMES,
    }

    store = ArtifactStore()
    results: dict[str, dict] = {}
    models: dict[str, tuple] = {}
    for cell, extra in cells.items():
        feat_cols = [f"feat_{i}" for i in range(70)]
        if extra:
            temp_idx = [TEMPORAL_FEATURE_NAMES.index(n) for n in extra]
            feat_cols += [f"feat_{70 + j}" for j in temp_idx]
        exp = ExperimentFactory(store=store).create(
            "ds_temporal_matrix",
            template="baseline_scalpnet_v1",
            experiment_id=f"temporal_matrix_{cell}",
            overrides={"training": TRAIN_CFG},
        )
        mid = f"temporal_matrix_{cell}"
        t0 = time.perf_counter()
        res = CandidateTrainer(store=store).train_candidate(
            exp, df, feature_cols=feat_cols, model_id=mid, epochs=int(TRAIN_CFG["epochs"])
        )
        dt = round(time.perf_counter() - t0, 1)
        if res.get("status") != "COMPLETED":
            results[cell] = {"status": "FAILED", "error": res.get("error")}
            print(f"[EXP] {cell}: FAILED {res.get('error')}")
            continue
        model = ModelFactory().build(
            architecture=exp.architecture,
            num_classes=3,
            parameters={"input_dim": len(feat_cols), **(exp.architecture_parameters or {})},
        )
        model.load_state_dict(torch_load(store, mid))
        model.eval()
        mean, std = store.read_scaler(mid)
        X = df.select(feat_cols).to_numpy().astype(np.float32)
        Xs = (X - mean) / std
        import torch

        with torch.inference_mode():
            logits = model(torch.from_numpy(Xs)).numpy()
        probs = softmax(logits)
        preds = probs.argmax(axis=1)
        # validation split = last 20% (chronological, same for every cell)
        n = len(preds)
        val = slice(int(n * 0.8), n)
        acc = float(np.mean(preds[val] == df["label"].to_numpy()[val]))
        # flip metrics on directional preds (full frame)
        seq = ["BUY" if p == 1 else "SELL" if p == 2 else "NONE" for p in preds]
        flips = sum(1 for i in range(1, n) if seq[i] != seq[i - 1] and seq[i] != "NONE" and seq[i - 1] != "NONE")
        directional = sum(1 for s in seq if s != "NONE")
        results[cell] = {
            "status": "COMPLETED",
            "val_accuracy": round(acc, 4),
            "train_seconds": dt,
            "n_features": len(feat_cols),
            "flips": flips,
            "directional": directional,
            "flips_per_1000_bars": round(flips / n * 1000, 2),
        }
        models[cell] = (probs, seq)
        print(f"[EXP] {cell}: acc={acc:.4f} flips={flips} dir={directional} ({dt}s)")

    # ---- stability controller on the baseline (A) raw sequence -----------
    ctrl = DecisionStabilityController(
        entry_min_margin=0.05, hard_reversal_margin=0.20,
        entry_confirm_bars=2, exit_confirm_bars=1,
    )
    probs_a = models["A_70d"][0]
    stable_seq: list[str] = []
    events = []
    for i in range(len(probs_a)):
        d = ctrl.decide(list(probs_a[i]), timestamp=f"t{i}")
        stable_seq.append(d.stable_direction)
        if d.event:
            events.append(d.event)
    stable_flips = sum(1 for i in range(1, len(stable_seq))
                       if stable_seq[i] != stable_seq[i - 1] and stable_seq[i] != "NONE" and stable_seq[i - 1] != "NONE")
    raw_flips = results["A_70d"]["flips"]
    # signal duration stats
    def durations(seq):
        durs = []
        cur, cnt = None, 0
        for s in seq:
            if s == "NONE":
                if cur is not None:
                    durs.append(cnt); cur, cnt = None, 0
                continue
            if s == cur:
                cnt += 1
            else:
                if cur is not None:
                    durs.append(cnt)
                cur, cnt = s, 1
        if cur is not None:
            durs.append(cnt)
        return durs
    raw_dur = durations(["BUY" if int(p.argmax()) == 1 else "SELL" if int(p.argmax()) == 2 else "NONE" for p in probs_a])
    stab_dur = durations(stable_seq)
    stability = {
        "raw_flips": raw_flips,
        "stable_flips": stable_flips,
        "flip_reduction_pct": round((1 - stable_flips / raw_flips) * 100, 2) if raw_flips else 0.0,
        "raw_median_signal_duration_bars": statistics.median(raw_dur) if raw_dur else None,
        "stable_median_signal_duration_bars": statistics.median(stab_dur) if stab_dur else None,
        "confirmed_events": len(events),
        "event_sample": [
            {"previous": e.previous, "new": e.new_direction, "margin": e.margin,
             "reason": e.confirmation_reason, "candidate_age": e.candidate_age}
            for e in events[:20]
        ],
        "parameters": {
            "entry_min_margin": ctrl.entry_min_margin,
            "hard_reversal_margin": ctrl.hard_reversal_margin,
            "entry_confirm_bars": ctrl.entry_confirm_bars,
            "exit_confirm_bars": ctrl.exit_confirm_bars,
            "max_candidate_age": ctrl.max_candidate_age,
        },
    }
    print(f"[EXP] stability: raw_flips={raw_flips} stable_flips={stable_flips} "
          f"reduction={stability['flip_reduction_pct']}% events={len(events)}")

    payload = {
        "experiment": "STEP-07 temporal experiment matrix",
        "frame": str(FRAME),
        "cells": cells,
        "training_budget": TRAIN_CFG,
        "results": results,
        "stability_controller": stability,
        "note": "same frame/labels/split/architecture/budget; only feature "
                "representation differs; stability controller applied to the "
                "baseline A raw sequence (research instrument, never ACTIVE)",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    print(f"[EXP] wrote {OUT}")


def torch_load(store, mid):
    import torch

    return torch.load(store.model_weights_path(mid), map_location="cpu")


if __name__ == "__main__":
    main()