"""STEP-07b — stability controller analysis on saved experiment models.

Reuses the 5 persisted temporal_matrix_* models and the saved temporal
frame to compute RAW vs STABILIZED flip metrics + decision-margin analysis.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, "src")

from nexus_scalp.features.temporal import (  # noqa: E402
    TEMPORAL_FEATURE_NAMES,
    TemporalLiquidityTracker,
)
from nexus_scalp.model_generation.artifact_store import ArtifactStore  # noqa: E402
from nexus_scalp.model_generation.model_factory import ModelFactory  # noqa: E402
from nexus_scalp.signals.stability_controller import (  # noqa: E402
    DecisionStabilityController,
)

REPO = Path(__file__).resolve().parents[1]
FRAME = REPO / "artifacts/forensics/temporal_frame_4000.parquet"
OUT = REPO / "artifacts/forensics/temporal_experiment_matrix.json"

LAG_NAMES = [n for n in TEMPORAL_FEATURE_NAMES if "lag" in n]
DELTA_NAMES = [n for n in TEMPORAL_FEATURE_NAMES if "delta" in n]
PERSIST_NAMES = [n for n in TEMPORAL_FEATURE_NAMES if "persistence" in n]
TSC_NAMES = [n for n in TEMPORAL_FEATURE_NAMES if "time_since" in n or "state_duration" in n]

CELLS = {
    "A_70d": [],
    "B_70d_lag": LAG_NAMES,
    "C_70d_lag_delta": LAG_NAMES + DELTA_NAMES,
    "D_70d_lag_persist": LAG_NAMES + PERSIST_NAMES,
    "E_70d_full": LAG_NAMES + DELTA_NAMES + PERSIST_NAMES + TSC_NAMES,
}


def softmax(v: np.ndarray) -> np.ndarray:
    e = np.exp(v - v.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def durations(seq: list[str]) -> list[int]:
    durs = []
    cur, cnt = None, 0
    for s in seq:
        if s == "NONE":
            if cur is not None:
                durs.append(cnt)
                cur, cnt = None, 0
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


def main() -> None:
    df = pl.read_parquet(FRAME)
    if df.height == 0:
        raise RuntimeError("temporal frame missing")

    # rebuild temporal dims (deterministic)
    tracker = TemporalLiquidityTracker()
    liq_np = df.select([f"feat_{i}" for i in range(60, 70)]).to_numpy()
    ts_col = df["timestamp"].to_list()
    temp_rows = []
    for i in range(df.height):
        snap = tracker.update([float(x) for x in liq_np[i]], str(ts_col[i]))
        temp_rows.append(list(snap.values))
    temp_np = np.asarray(temp_rows, dtype=np.float64)
    temps = {}
    for j, name in enumerate(TEMPORAL_FEATURE_NAMES):
        temps[name] = temp_np[:, j]
        df = df.with_columns(pl.Series(f"feat_{70 + j}", temp_np[:, j]))

    store = ArtifactStore()
    label_np = df["label"].to_numpy() if "label" in df.columns else None

    results: dict[str, dict] = {}
    seqs: dict[str, list[str]] = {}
    probs_by_cell: dict[str, np.ndarray] = {}
    for cell, extra in CELLS.items():
        mid = f"temporal_matrix_{cell}"
        man = store.read_model_manifest(mid)
        if not man:
            results[cell] = {"status": "SKIPPED", "error": "no artifact"}
            continue
        feat_cols = [f"feat_{i}" for i in range(70)]
        if extra:
            temp_idx = [TEMPORAL_FEATURE_NAMES.index(n) for n in extra]
            feat_cols += [f"feat_{70 + j}" for j in temp_idx]
        model = ModelFactory().build(
            architecture=man["architecture_id"],
            num_classes=3,
            parameters={
                "input_dim": man["feature_dimension"],
                **(man.get("architecture_parameters") or {}),
            },
        )
        import torch

        model.load_state_dict(torch.load(store.model_weights_path(mid), map_location="cpu"))
        model.eval()
        mean, std = store.read_scaler(mid)
        X = df.select(feat_cols).to_numpy().astype(np.float32)
        Xs = (X - mean) / std
        with torch.inference_mode():
            logits = model(torch.from_numpy(Xs)).numpy()
        probs = softmax(logits)
        preds = probs.argmax(axis=1)
        n = len(preds)
        val = slice(int(n * 0.8), n)
        acc = float(np.mean(preds[val] == label_np[val])) if label_np is not None else None
        seq = ["BUY" if int(p) == 1 else "SELL" if int(p) == 2 else "NONE" for p in preds]
        flips = sum(1 for i in range(1, n) if seq[i] != seq[i - 1] and seq[i] != "NONE" and seq[i - 1] != "NONE")
        directional = sum(1 for s in seq if s != "NONE")
        results[cell] = {
            "status": "COMPLETED",
            "val_accuracy": round(acc, 4) if acc is not None else None,
            "n_features": len(feat_cols),
            "flips": flips,
            "directional": directional,
            "flips_per_1000_bars": round(flips / n * 1000, 2),
            "median_signal_duration_bars": statistics.median(durations(seq)) if durations(seq) else None,
        }
        seqs[cell] = seq
        probs_by_cell[cell] = probs
        print(f"[EXP] {cell}: acc={acc} flips={flips} dir={directional}")

    # ---- stability controller on every cell's RAW sequence ----------------
    stability: dict[str, dict] = {}
    for cell in CELLS:
        if cell not in seqs:
            continue
        probs = probs_by_cell[cell]
        ctrl = DecisionStabilityController(
            entry_min_margin=0.05, hard_reversal_margin=0.20,
            entry_confirm_bars=2, exit_confirm_bars=1,
        )
        stable_seq: list[str] = []
        events = []
        for i in range(len(probs)):
            d = ctrl.decide(list(probs[i]), timestamp=f"t{i}")
            stable_seq.append(d.stable_direction)
            if d.event:
                events.append(d.event)
        stable_flips = sum(1 for i in range(1, len(stable_seq))
                           if stable_seq[i] != stable_seq[i - 1] and stable_seq[i] != "NONE" and stable_seq[i - 1] != "NONE")
        raw_flips = results[cell]["flips"]
        stab_dur = durations(stable_seq)
        stability[cell] = {
            "raw_flips": raw_flips,
            "stable_flips": stable_flips,
            "flip_reduction_pct": round((1 - stable_flips / raw_flips) * 100, 2) if raw_flips else 0.0,
            "raw_median_signal_duration_bars": results[cell]["median_signal_duration_bars"],
            "stable_median_signal_duration_bars": statistics.median(stab_dur) if stab_dur else None,
            "confirmed_events": len(events),
            "event_sample": [
                {"previous": e.previous, "new": e.new_direction, "margin": e.margin,
                 "reason": e.confirmation_reason, "candidate_age": e.candidate_age}
                for e in events[:10]
            ],
        }
        print(f"[EXP] {cell} stability: raw={raw_flips} stable={stable_flips} "
              f"reduction={stability[cell]['flip_reduction_pct']}% events={len(events)}")

    # ---- decision margin analysis (brief 25) -------------------------------
    margins_all = [abs(float(p[1]) - float(p[2])) for cell in CELLS if cell in probs_by_cell
                   for p in probs_by_cell[cell]]
    margin_stats = {
        "min": round(min(margins_all), 6),
        "median": round(statistics.median(margins_all), 6),
        "p95": round(sorted(margins_all)[int(len(margins_all) * 0.95) - 1], 6),
        "max": round(max(margins_all), 6),
    }

    payload = {
        "experiment": "STEP-07 temporal experiment matrix",
        "frame": str(FRAME),
        "results": results,
        "stability_controller": stability,
        "decision_margin_stats": margin_stats,
        "parameters": {
            "entry_min_margin": 0.05,
            "hard_reversal_margin": 0.20,
            "entry_confirm_bars": 2,
            "exit_confirm_bars": 1,
        },
        "note": "same frame/labels/split/architecture/budget; only feature "
                "representation differs; stability controller research-only",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    print(f"[EXP] wrote {OUT}")


if __name__ == "__main__":
    main()