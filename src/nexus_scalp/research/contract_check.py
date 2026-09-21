"""Phase 0 item 7: feature-order / schema mismatch check across
training dataset, shipped scaler, shipped model tensors, model metadata,
the live registry and the live inference assembly path. Read-only everywhere.
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
REG_DB = REPO / "artifacts" / "audit.db"
LIVE_DB = Path("C:/Users/Capsizer/AppData/Local/NexusScalpEngine/databases/app_settings.db")


def main() -> None:
    import sqlite3

    import polars as pl

    from nexus_scalp.features.schema import ACTIVE_SCHEMA_ID, FEATURE_SCHEMAS
    from nexus_scalp.features.schema_contract import (
        DIMENSION,
        SCHEMA_ID,
        canonical_feature_names,
        feature_schema_hash,
    )

    names = list(canonical_feature_names())
    report: dict = {
        "canonical_schema_id": SCHEMA_ID,
        "canonical_dimension": DIMENSION,
        "canonical_schema_hash_computed": feature_schema_hash(),
        "canonical_names_50_59": names[50:60],
        "canonical_names_60_69": names[60:70],
        "registry_active_schema_id": ACTIVE_SCHEMA_ID,
        "registry_scalp_v3_dimension": FEATURE_SCHEMAS.resolve("scalp_v3").dimension,
        "registry_scalp_v1_dimension": FEATURE_SCHEMAS.resolve("scalp_v1").dimension,
    }

    ds = REPO / "artifacts" / "model_generation" / "datasets" / "ds_70d_clean_m1_20260904"
    man = json.loads((ds / "dataset_manifest.json").read_text(encoding="utf-8"))
    df_cols: list[str] = []
    try:
        df_cols = [
            c for c in pl.read_parquet(ds / "dataset.parquet").columns if c.startswith("feat_")
        ]
    except Exception as exc:
        report["dataset_column_error"] = str(exc)
    report["dataset"] = {
        "dataset_id": man.get("dataset_id"),
        "dataset_sha256": man.get("dataset_hash") or man.get("dataset_sha256"),
        "feature_schema_id": man.get("feature_schema_id"),
        "feature_schema_hash": man.get("feature_schema_hash"),
        "label_schema_id": man.get("label_schema_id"),
        "feat_column_count": len(df_cols),
        "feat_columns_match_index_order": df_cols == [f"feat_{i}" for i in range(len(df_cols))],
        "news_schema_id": man.get("news_schema_id"),
        "news_data_range": man.get("news_data_range"),
        "news_version": man.get("news_version"),
        "sequence_windows_max_gap_us": (man.get("sequence_windows") or {}).get("max_gap_us"),
        "contract_temporal_max_gap_us": (man.get("contract") or {}).get("temporal_max_gap_us"),
    }

    z = np.load(CHAMP / "model.scaler.npz")
    mean, std = z["mean"].reshape(-1), z["std"].reshape(-1)
    dead_dims = [int(i) for i in np.where(std <= 1e-3)[0]]
    report["scaler"] = {
        "path": str(CHAMP / "model.scaler.npz"),
        "mean_dim": int(mean.shape[0]),
        "std_dim": int(std.shape[0]),
        "dims_std_le_1e-3": dead_dims,
        "dims_std_le_1e-3_names": [names[i] for i in dead_dims],
        "std_min": round(float(std.min()), 8),
        "news_50_59_std": [round(float(std[i]), 8) for i in range(50, 60)],
        "news_50_59_mean": [round(float(mean[i]), 8) for i in range(50, 60)],
    }

    state = torch.load(CHAMP / "model.pt", map_location="cpu", weights_only=True)
    meta = json.loads((CHAMP / "model.meta.json").read_text(encoding="utf-8"))
    ip = state["input_projection.weight"]
    cw = state["classifier.weight"]
    meta_names = list(meta.get("canonical_feature_names") or [])
    report["model_tensors"] = {
        "input_projection_shape": list(ip.shape),
        "input_dim_from_tensor": int(ip.shape[1]),
        "classifier_shape": list(cw.shape),
        "head_classes": int(cw.shape[0]),
        "meta_num_features": meta.get("num_features"),
        "meta_feature_schema_id": meta.get("feature_schema_id"),
        "meta_feature_schema_hash": meta.get("feature_schema_hash"),
        "meta_dataset_id": meta.get("dataset_id"),
        "meta_seq_len": meta.get("seq_len"),
        "meta_max_gap_us": meta.get("max_gap_us"),
        "meta_label_mapping": meta.get("label_mapping"),
        "meta_news_names_50_59": meta_names[50:60],
        "meta_names_match_canonical": meta_names == names,
    }

    try:
        con = sqlite3.connect(f"file:{REG_DB}?mode=ro", uri=True)
        con.execute("PRAGMA query_only=1")
        cols = [d[0] for d in con.execute("select * from experience_model_registry limit 0").description]
        report["experience_model_registry"] = [
            dict(zip(cols, r))
            for r in con.execute("select * from experience_model_registry")
        ]
        cols2 = [d[0] for d in con.execute("select * from model_runtime_health limit 0").description]
        report["model_runtime_health"] = [
            dict(zip(cols2, r)) for r in con.execute("select * from model_runtime_health")
        ]
        report["audit_experiences_feature_dimension_counts"] = [
            dict(zip(["feature_dimension", "n"], r))
            for r in con.execute(
                "select feature_dimension, count(*) as n from audit_experiences "
                "group by feature_dimension order by feature_dimension"
            )
        ]
        report["audit_experiences_schema_counts"] = [
            dict(zip(["schema_id", "n"], r))
            for r in con.execute(
                "select feature_schema_id as schema_id, count(*) as n from audit_experiences "
                "group by feature_schema_id order by n desc"
            )
        ]
        con.close()
    except Exception as exc:
        report["registry_error"] = str(exc)

    try:
        con = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        con.execute("PRAGMA query_only=1")
        report["live_settings"] = {
            k: v
            for k, v in con.execute(
                "select key, value from application_settings where key like 'model.%' "
                "or key like 'algo.ai_zone%' or key like 'algo.min_risk%' "
                "or key like 'execution.mode'"
            )
        }
        con.close()
    except Exception as exc:
        report["live_settings_error"] = str(exc)

    try:
        from nexus_scalp.governance.alignment import vectorize_news_context
        from nexus_scalp.shadow.shadow70 import news_provider

        v = vectorize_news_context(None)
        live10, ver = news_provider.build_news_10(v)
        report["live_news_10_when_unavailable"] = live10
        report["live_news_10_projection_version"] = ver
        report["live_news_10_slot_names"] = list(news_provider.NEWS_FAMILY_SLOT_NAMES)
    except Exception as exc:
        report["live_news_projection_error"] = str(exc)

    mismatches: list[str] = []
    if df_cols != [f"feat_{i}" for i in range(70)]:
        mismatches.append(f"dataset feat columns not canonical 70 in index order (got {len(df_cols)})")
    if man.get("feature_schema_hash") != feature_schema_hash():
        mismatches.append("dataset manifest schema hash != canonical hash")
    if int(mean.shape[0]) != 70 or int(std.shape[0]) != 70:
        mismatches.append(f"scaler width {mean.shape[0]}/{std.shape[0]} != 70")
    if int(ip.shape[1]) != 70:
        mismatches.append(f"model tensor input dim {ip.shape[1]} != 70")
    if int(ip.shape[1]) != int(mean.shape[0]):
        mismatches.append("model tensor input dim != scaler width")
    if int(cw.shape[0]) != 3:
        mismatches.append(f"model head {cw.shape[0]} != canonical 3 classes")
    if meta.get("feature_schema_hash") != feature_schema_hash():
        mismatches.append("model.meta.json schema hash != canonical hash")
    if meta_names != names:
        mismatches.append("model.meta.json canonical_feature_names != canonical tuple")
    if int(meta.get("num_features", -1)) != int(ip.shape[1]):
        mismatches.append("meta num_features != tensor input dim")
    if ACTIVE_SCHEMA_ID != "scalp_v1":
        mismatches.append(f"registry ACTIVE_SCHEMA_ID is {ACTIVE_SCHEMA_ID}, not scalp_v1")
    if set(range(50, 60)).issubset(set(dead_dims)):
        mismatches.append(
            "shipped scaler std<=1e-3 (the 0.001 floor) on ALL NEWS dims 50..59: the scaler "
            "cannot differentiate news inputs because the training data was constant zero"
        )
    reg = report.get("experience_model_registry") or []
    champ_reg_rows = [r for r in reg if str(r.get("lifecycle_status")) == "CHAMPION"]
    if champ_reg_rows and not any(
        "70d_liquidity" in str(r.get("artifact_path") or "").replace("\\", "/")
        for r in champ_reg_rows
    ):
        mismatches.append(
            "experience_model_registry CHAMPION rows point at a 50D artifact "
            + str({r.get("model_id"): r.get("artifact_path") for r in champ_reg_rows})
            + " while live settings model.model_artifact_path points at the 70d_liquidity artifact"
        )
    if (man.get("sequence_windows") or {}).get("max_gap_us") != meta.get("max_gap_us"):
        mismatches.append(
            "dataset manifest sequence_windows.max_gap_us "
            f"{(man.get('sequence_windows') or {}).get('max_gap_us')} != model.meta.json max_gap_us "
            f"{meta.get('max_gap_us')} (two-value gap contract)"
        )
    report["mismatches"] = mismatches
    report["contract_mismatch_found"] = bool(mismatches)

    (OUT / "contract_check_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    digest = {
        "canonical_schema_id": report["canonical_schema_id"],
        "canonical_schema_hash_computed": report["canonical_schema_hash_computed"],
        "dataset": report["dataset"],
        "scaler": report["scaler"],
        "model_tensors": report["model_tensors"],
        "registry_champion_rows": champ_reg_rows,
        "audit_experiences_feature_dimension_counts": report.get(
            "audit_experiences_feature_dimension_counts"
        ),
        "mismatches": mismatches,
        "contract_mismatch_found": report["contract_mismatch_found"],
    }
    print(json.dumps(digest, indent=2, default=str))


if __name__ == "__main__":
    main()
