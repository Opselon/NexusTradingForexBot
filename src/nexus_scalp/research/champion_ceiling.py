"""Phase 0: champion reachable-confidence ceiling + inference contract check.

Random-search probe over the post-scaled space to find the MAX directional
confidence the CURRENT champion artifact can emit, plus the exact scaled-input
contract it applies. Read-only on the production artifact (load only).
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import numpy as np
import torch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
OUT = REPO / "artifacts" / "research" / "phase0_20260921"

CHAMP = REPO / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity"
MODEL = CHAMP / "model.pt"
SCALER = CHAMP / "model.scaler.npz"
META = CHAMP / "model.meta.json"

# effective live gate components (read from the live settings DB, no writes)
LIVE_DB = Path("C:/Users/Capsizer/AppData/Local/NexusScalpEngine/databases/app_settings.db")


def read_live_gate() -> dict:
    import sqlite3

    out: dict = {}
    try:
        con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        con.execute("PRAGMA query_only=1")
        for k in ("model.confidence_threshold", "algo.ai_zone_confidence_threshold",
                  "algo.min_risk_reward_ratio", "model.liquidity_features_enabled",
                  "model.model_artifact_path", "execution.mode"):
            row = con.execute("select value from application_settings where key=?", (k,)).fetchone()
            out[k] = row[0] if row else None
        con.close()
    except Exception as exc:  # pragma: no cover
        out["error"] = str(exc)
    return out


def main() -> None:
    from nexus_scalp.models.scalp_net import ScalpNet

    state = torch.load(MODEL, map_location="cpu", weights_only=True)
    z = np.load(SCALER)
    mean = z["mean"].reshape(-1).astype(np.float32)
    std = z["std"].reshape(-1).astype(np.float32)
    meta = json.loads(META.read_text(encoding="utf-8"))

    report: dict = {
        "artifact": str(MODEL),
        "state_keys": sorted(state.keys()),
        "classifier_weight_shape": list(state["classifier.weight"].shape),
        "classifier_weight_absmean_per_class": [
            round(float(state["classifier.weight"][c].abs().mean()), 6) for c in range(3)
        ],
        "classifier_bias": [round(float(b), 6) for b in state["classifier.bias"].tolist()],
        "input_projection_shape": list(state["input_projection.weight"].shape),
        "meta_num_features": meta.get("num_features"),
        "meta_feature_schema_id": meta.get("feature_schema_id"),
        "meta_feature_schema_hash": meta.get("feature_schema_hash"),
        "meta_label_mapping": meta.get("label_mapping"),
        "meta_seq_len": meta.get("seq_len"),
        "meta_train_ratio": meta.get("train_ratio"),
        "meta_dataset_id": meta.get("dataset_id"),
        "scaler_mean_dim": int(mean.shape[0]),
        "scaler_std_dim": int(std.shape[0]),
        "scaler_std_min": round(float(std.min()), 8),
        "scaler_std_argmin": int(std.argmin()),
        "scaler_dims_std_le_1e-3": [int(i) for i in np.where(std <= 1e-3)[0]],
        "scaler_mean_50_59": [round(float(mean[i]), 8) for i in range(50, 60)],
        "scaler_std_50_59": [round(float(std[i]), 8) for i in range(50, 60)],
    }

    net = ScalpNet(num_features=int(state["input_projection.weight"].shape[1]), num_classes=3)
    net.load_state_dict(state)
    net.eval()

    # The trainer clips scaled features to [-5, +5]; the 70D contract bounds the
    # RAW vector to [-3,+3]. Probe BOTH spaces and report per-space ceilings.
    ceilings: dict[str, dict] = {}
    for space, lo, hi, label in [
        ("contract_raw_space_scaled", -3.0, 3.0, "raw vector in [-3,+3] then scaler-transformed"),
        ("clip_space_direct", -5.0, 5.0, "directly in post-clip [-5,+5] (trainer's clip band)"),
    ]:
        best = {"max_prob": -1.0, "probs": None, "x": None}
        worst = {"max_prob": 2.0}
        gen = torch.Generator().manual_seed(1234)
        BATCH = 4000
        for _ in range(400):
            X = torch.empty(BATCH, 70).uniform_(lo, hi, generator=gen)
            with torch.inference_mode():
                logits = net(X, return_logits=True)
                probs = torch.softmax(logits, dim=-1)
            mp, am = probs.max(dim=-1)
            i = int(torch.argmax(mp))
            if float(mp[i]) > best["max_prob"]:
                best = {"max_prob": float(mp[i]), "probs": probs[i].tolist(),
                        "x": X[i].tolist(), "pred_class": int(am[i])}
            j = int(torch.argmin(mp))
            if float(mp[j]) < worst["max_prob"]:
                worst = {"max_prob": float(mp[j]), "probs": probs[j].tolist()}
        # apply the shipped scaler to the raw-space probe as well (the real path)
        ceilings[label] = {
            "space": space,
            "samples_drawn": 400 * BATCH,
            "max_directional_confidence": round(best["max_prob"], 6),
            "argmax_probs": [round(v, 6) for v in best["probs"]],
            "argmax_pred_class": best.get("pred_class"),
            "min_max_probability_seen": round(worst["max_prob"], 6),
            "min_probs": [round(v, 6) for v in worst["probs"]],
        }

    report["reachable_ceiling"] = ceilings

    # real-path: raw vectors in [-3,+3] passed through the shipped scaler
    gen = torch.Generator().manual_seed(7)
    best_scaled = {"max_prob": -1.0, "probs": None}
    for _ in range(400):
        Xr = torch.empty(4000, 70).uniform_(-3.0, 3.0, generator=gen).numpy().astype(np.float32)
        Xs = np.clip((Xr - mean) / std, -5.0, 5.0)
        with torch.inference_mode():
            probs = torch.softmax(net(torch.tensor(Xs), return_logits=True), dim=-1)
        mp, am = probs.max(dim=-1)
        i = int(torch.argmax(mp))
        if float(mp[i]) > best_scaled["max_prob"]:
            best_scaled = {"max_prob": float(mp[i]), "probs": probs[i].tolist(),
                           "pred_class": int(am[i])}
    report["reachable_ceiling_scaled_path"] = {
        "space": "raw [-3,+3] -> shipped scaler -> clip [-5,+5]",
        "samples_drawn": 400 * 4000,
        "max_directional_confidence": round(best_scaled["max_prob"], 6),
        "argmax_probs": [round(v, 6) for v in best_scaled["probs"]],
        "argmax_pred_class": best_scaled.get("pred_class"),
    }

    gate = read_live_gate()
    report["live_runtime_gate"] = gate
    ct = float(gate.get("model.confidence_threshold", "0.4"))
    report["effective_gate_components"] = {
        "model.confidence_threshold": ct,
        "range_confidence_penalty (SignalPolicy default)": 0.10,
        "effective_gate_low": round(ct + 0.10, 4),
    }

    (OUT / "champion_ceiling_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in
                      ("classifier_weight_shape", "classifier_weight_absmean_per_class",
                       "classifier_bias", "reachable_ceiling",
                       "reachable_ceiling_scaled_path",
                       "scaler_dims_std_le_1e-3", "live_runtime_gate",
                       "effective_gate_components")}, indent=2))


if __name__ == "__main__":
    main()
