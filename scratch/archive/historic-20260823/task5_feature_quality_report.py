"""TASK-5 feature quality report generator (spec 5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import polars as pl

from nexus_scalp.features.schema_augment import feature_quality_report

RAW = Path("data/raw/XAUUSD_M5.parquet")


def main() -> int:
    from nexus_scalp.model_generation.schema_v2 import compute_60d_frame

    raw = pl.read_parquet(RAW)
    feat = compute_60d_frame(raw, min_bars=55)
    print(f"[Q] computed {feat.height} rows of 60D features")

    report = feature_quality_report(feat, schema_id="scalp_v2")
    # add the semantic names
    from nexus_scalp.features.schema_augment import FEATURE_NAMES_60D_EXTRA

    for i in range(50, 60):
        col = f"feat_{i}"
        if col in report["features"]:
            report["features"][col]["semantic_name"] = FEATURE_NAMES_60D_EXTRA[i - 50]

    out = Path("docs/task5_feature_quality_report.md")
    lines = [
        "# TASK-5 60D Feature Quality Report (real M5, 99,946 rows)",
        "",
        f"Computed {report['total_rows']} rows; {report['feature_count']} feat columns.",
        "",
        "| feat | semantic | missing% | NaN% | Inf% | unique | var | min | max | flags |",
        "|------|----------|----------|------|------|--------|-----|-----|-----|-------|",
    ]
    for i in range(50, 60):
        f = report["features"][f"feat_{i}"]
        lines.append(
            f"| feat_{i} ({f.get('semantic_name', '')}) | {f['missing_pct']} | {f['nan_pct']} "
            f"| {f['inf_pct']} | {f['unique_count']} | {f['variance']} | {f['min']} "
            f"| {f['max']} | {';'.join(f['flags']) or 'OK'} |"
        )
    lines += ["", "## Duplicate groups", ""]
    if report["duplicate_groups"]:
        for g in report["duplicate_groups"]:
            lines.append(f"- {g}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Base 50D scan (top flags)")
    base_flags = {
        c: f["flags"] for c, f in report["features"].items() if c.startswith("feat_") and f["flags"]
    }
    for c in sorted(base_flags)[:15]:
        lines.append(f"- {c}: {base_flags[c]}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report written: {out}")
    # json too
    jp = Path("artifacts/model_generation/task5_feature_quality.json")
    jp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"json written: {jp}")
    # print key extra-feature stats
    for i in range(50, 60):
        f = report["features"][f"feat_{i}"]
        print(
            f"  feat_{i} {f.get('semantic_name', '')}: unique={f['unique_count']} "
            f"var={f['variance']} flags={f['flags']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
