"""Phase 0: aggregate the A/B/C ablation table + verdicts into one report."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "artifacts" / "research" / "phase0_20260921"


def main() -> None:
    res = json.loads((OUT / "training_probe_results.json").read_text(encoding="utf-8"))
    ds = json.loads((OUT / "dataset_stats.json").read_text(encoding="utf-8"))
    champ = json.loads((OUT / "champion_ceiling_report.json").read_text(encoding="utf-8"))
    contract = json.loads((OUT / "contract_check_report.json").read_text(encoding="utf-8"))
    news = json.loads((OUT / "news_frame_report.json").read_text(encoding="utf-8"))

    # dataset signal ceiling: max standardized class separation over non-const dims
    X = None
    import polars as pl

    df = pl.read_parquet(
        REPO
        / "artifacts"
        / "model_generation"
        / "datasets"
        / "ds_70d_clean_m1_20260904"
        / "dataset.parquet"
    )
    cols = [f"feat_{i}" for i in range(70)]
    ev = df.filter(pl.col("is_eval_sample") & ~pl.col("is_purged"))
    X = ev.select(cols).to_numpy().astype(np.float64)
    y = np.asarray(ev["label"].to_list(), dtype=np.int64)
    std_all = X.std(axis=0)
    const_dims = [int(i) for i in np.where(std_all <= 1e-9)[0]]
    sep = []
    for i in range(70):
        if std_all[i] <= 1e-9:
            continue
        means = [X[y == c, i].mean() for c in range(3)]
        md = max(abs(means[a] - means[b]) for a in range(3) for b in range(a + 1, 3))
        sep.append({"dim": i, "column": cols[i], "sep": round(float(md / std_all[i]), 6)})
    sep.sort(key=lambda r: -r["sep"])
    signal_ceiling = {
        "constant_dims": const_dims,
        "constant_dim_names": [cols[i] for i in const_dims],
        "max_standardized_class_separation": sep[0]["sep"] if sep else None,
        "best_dim": sep[0] if sep else None,
        "top5_separation": sep[:5],
        "n_nonconstant_dims": len(sep),
    }

    table = {}
    for name, runs in res.items():
        ok = [r for r in runs if "FAILED" not in r]
        table[name] = {
            "seeds": [r["seed"] for r in runs],
            "runs_ok": len(ok),
            "runs_failed": len(runs) - len(ok),
            "holdout_accuracy": [r["holdout"]["accuracy"] for r in ok],
            "holdout_balanced_accuracy": [r["holdout"]["balanced_accuracy"] for r in ok],
            "holdout_macro_f1": [r["holdout"]["macro_f1"] for r in ok],
            "majority_baseline_acc": ok[0]["holdout"]["majority_class_baseline_accuracy"]
            if ok
            else None,
            "holdout_mean_max_prob": [r["holdout"]["mean_max_probability"] for r in ok],
            "holdout_max_confidence": [r["holdout"]["max_max_probability"] for r in ok],
            "pct_rows_over_gate": [r["holdout"]["pct_rows_over_effective_gate"] for r in ok],
            "pct_directional_over_gate": [r["holdout"]["pct_directional_over_gate"] for r in ok],
            "reachable_ceiling": [r["reachable_ceiling"]["max_directional_confidence"] for r in ok],
            "holdout_mean_entropy_normalized": [
                r["holdout"]["mean_entropy_normalized"] for r in ok
            ],
            "mean_prob_per_class_seed42": ok[0]["holdout"]["mean_probability_per_class"]
            if ok
            else None,
            "news_sensitivity": [
                (r.get("block_sensitivity") or {}).get("news_50_59_shift_1") for r in ok
            ],
            "liq_sensitivity": [
                (r.get("block_sensitivity") or {}).get("liquidity_60_69_shift_1") for r in ok
            ],
            "oos_accuracy_pooled_folds": [r["pooled_oos"]["oos_accuracy"] for r in ok],
            "in_sample_accuracy": [r["in_sample"]["accuracy"] for r in ok],
            "holdout_pctiles_p50_p90_p99_max": [
                {
                    k: r["holdout"]["max_probability_percentiles"][k]
                    for k in ("p50", "p90", "p99", "p100")
                }
                for r in ok
            ],
            "dir_conf_p50_p90_p99": [
                {
                    k: r["holdout"]["directional_confidence_stats"][k]
                    for k in ("p50", "p90", "p99", "p100")
                }
                for r in ok
            ],
        }

    def mean(v):
        return round(float(np.mean(v)), 6) if v else None

    summary = {
        "effective_gate": 0.50,
        "champion_reachable_ceiling": champ["reachable_ceiling_scaled_path"][
            "max_directional_confidence"
        ],
        "champion_ceiling_space": champ["reachable_ceiling_scaled_path"]["space"],
        "champion_classifier": {
            "shape": champ["classifier_weight_shape"],
            "absmean_per_class": champ["classifier_weight_absmean_per_class"],
            "bias": champ["classifier_bias"],
        },
        "dataset": {
            "dataset_id": ds["dataset_id"],
            "dataset_sha256": ds["dataset_sha256"],
            "rows_total": ds["rows_total"],
            "trainable_rows": ds["trainable_rows"],
            "label_distribution_all_rows": ds["label_distribution_all_rows"],
            "label_distribution_eval": ds["label_distribution"],
            "temporal_range": ds["temporal_range"],
            "sequence_windows": ds["sequence_windows"],
        },
        "signal_ceiling": signal_ceiling,
        "news_coverage": news.get("coverage_vs_dataset_window"),
        "news_quality": {
            k: v for k, v in (news.get("quality_diagnostics") or {}).items() if k != "per_field"
        },
        "ablation_means": {
            n: {
                "acc": mean(v["holdout_accuracy"]),
                "bal_acc": mean(v["holdout_balanced_accuracy"]),
                "macro_f1": mean(v["holdout_macro_f1"]),
                "mean_max_prob": mean(v["holdout_mean_max_prob"]),
                "max_confidence": mean(v["holdout_max_confidence"]),
                "pct_over_gate": mean(v["pct_rows_over_gate"]),
                "pct_directional_over_gate": mean(v["pct_directional_over_gate"]),
                "reachable_ceiling": mean(v["reachable_ceiling"]),
                "entropy_normalized": mean(v["holdout_mean_entropy_normalized"]),
                "oos_pooled_acc": mean(v["oos_accuracy_pooled_folds"]),
                "in_sample_acc": mean(v["in_sample_accuracy"]),
            }
            for n, v in table.items()
        },
        "ablation_table": table,
        "contract_mismatch_found": contract["contract_mismatch_found"],
        "contract_mismatches": contract["mismatches"],
    }

    (OUT / "phase0_final_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
