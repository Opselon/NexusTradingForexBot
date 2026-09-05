"""ML-DATASET-RETRAIN-PREP — Clean 70D dataset regeneration (production contract).

Regenerates the XAUUSD M1 70D (scalp_v3) dataset artifact with ALL Wave-1
contract fixes applied:

    BUG-234 / MLPWR-06-02 : HTF_HISTORY_BARS=4000 shared causal HTF window
                            (compute_70d_frame_fast passes the live-equivalent
                            depth so feat_41/42 are real in every training row)
    MLFIX-T2 / FIX #1+#8  : temporal contract L=32, gap-safe windows
                            (SequenceBuilder max_gap_us, valid=False exclusion)
    MLFIX-T4              : 3-class label contract (NO_TRADE=0/BUY=1/SELL=2;
                            WAIT is policy-only, never a training target)
    MLFIX-T7              : lineage CLEAN_HISTORICAL stamp on the manifest
                            (production-eligible without governance override)
    MLFix §8 F5           : no_trade_stride_bars 3->2 RETRAIN-ONLY labeler
                            override (documented deviation; barriers identical)

Production build contract (smoke=False always here — this is the real artifact):
    builder   : compute_70d_frame_fast (BUG-106 incremental, byte-identical to
                the canonical builder; parity self-check embedded)
    verify    : build_70d_dataset(verify_parity=True) canonical-vs-fast
                equivalence + verify_70d_artifact gate (dim=70, schema hash,
                finite, [-3,+3], dup timestamps, sane timestamps)
    labels    : TripleBarrierLabeler stride 2 via SampleFactory(labeler=...)
    lineage   : DatasetFactory stamps label_origin=CLEAN_HISTORICAL
    identity  : deterministic_dataset_id (symbol|timeframe|schemas|strategy|
                config-hash|news-digest)
    split     : chronological 70/15/15 (train/val/test), purge/embargo preserved
    sequences : L=32 gap-safe window statistics recorded in the manifest
                (valid/invalid counts over the evaluated rows — the artifact
                stores 2D rows; sequences are derived by SequenceBuilder with
                the SAME builder live uses)

Usage (repo venv from repo root):
    .venv/Scripts/python.exe scripts/dev/regen_70d_clean_dataset.py \
        [--bars data/raw/XAUUSD_M1.csv] [--dataset-id ds_70d_clean_m1] \
        [--skip-parity]

Outputs (ArtifactStore layout, gitignored):
    artifacts/model_generation/datasets/<dataset_id>/dataset.parquet
    artifacts/model_generation/datasets/<dataset_id>/dataset_manifest.json
plus a verification report at
    artifacts/model_generation/datasets/<dataset_id>/verification.json
EXIT 0 only when every gate passes; any gate failure exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.schema_v2 import (
    build_70d_dataset,
    verify_70d_artifact,
)
from nexus_scalp.model_generation.sequence import SequenceBuilder
from nexus_scalp.model_generation.temporal_contract import CANONICAL_MAX_GAP_US
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.scripts.regen_70d_clean_dataset")

#: RETRAIN-ONLY documented labeler override (MLFix §8 F5): stride 3 -> 2.
NO_TRADE_STRIDE_BARS = 2
#: Canonical temporal contract (temporal_contract / sequence.py SEQUENCE_CONTRACT).
SEQ_LEN = 32
#: BUG-246 (Agent-2 dataset forensics 2026-09-05): bind the window gap
#: constant to the canonical SSoT instead of a local 15-minute literal. The
#: original build tree shipped MAX_GAP_US=900_000_000 here while
#: temporal_contract declares CANONICAL_MAX_GAP_US=600_000_000, so the
#: manifest recorded 900000000 while artifact metadata stamps 600000000 - a
#: silent two-value contract. No delta on the 100k M1 history falls in
#: (600s, 900s], so the authoritative artifact is unaffected; the SSoT import
#: prevents divergence on future rebuilds.
MAX_GAP_US = CANONICAL_MAX_GAP_US
PURGE_BARS = 15
EMBARGO_BARS = 15
SEED = 42
LABEL_SCHEMA_ID = "triple_barrier_3class_v1"
STRATEGY_ID = "scalp_70d_clean"
HTF_HISTORY_BARS = 4000  # BUG-234 shared contract (features/scalp_features.py)


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> tuple[str, bool]:
    """(commit, dirty) — honest provenance or ('unknown', True)."""
    try:
        import subprocess

        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = (
            subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
            ).stdout.strip()
            != ""
        )
        return commit, dirty
    except Exception:
        return "unknown", True


def load_bars(path: Path) -> pl.DataFrame:
    """Loads raw M1 bars (csv or parquet) with a UTC datetime column."""
    if path.suffix.lower() == ".parquet":
        df = pl.read_parquet(path)
    else:
        df = pl.read_csv(path)
        df = df.with_columns(pl.col("time_utc").str.to_datetime(strict=True).alias("time_utc"))
    df = df.sort("time")
    if df.height < 60_000:
        raise ValueError(
            f"production dataset requires >= 60,000 bars, got {df.height} from {path} "
            "(do NOT smoke-tail a production dataset; use --bars with the full file)"
        )
    return df


def gap_safe_window_stats(frame: pl.DataFrame) -> dict[str, Any]:
    """Builds L=32 sequences over evaluated rows with the canonical builder and
    reports gap-safe window statistics (never mutates the artifact rows)."""
    labeler_view = frame.filter(pl.col("is_eval_sample") & ~pl.col("is_purged"))
    if labeler_view.is_empty():
        return {"eval_rows": 0}
    builder = SequenceBuilder(seq_len=SEQ_LEN, max_gap_us=MAX_GAP_US)
    seq = builder.build(labeler_view, news_enabled=False)
    total = int(seq["valid"].shape[0])
    valid = int(seq["valid"].sum())
    return {
        "seq_len": SEQ_LEN,
        "max_gap_us": MAX_GAP_US,
        "windows_total": total,
        "windows_valid": valid,
        "windows_rejected_gap_or_boundary": total - valid,
        "tensor_shape": list(seq["X"].shape),
        "finite": bool(_is_finite(seq["X"])),
    }


def _is_finite(arr: Any) -> bool:
    import numpy as np

    return bool(np.isfinite(arr).all())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/raw/XAUUSD_M1.csv", help="raw M1 bars file")
    parser.add_argument(
        "--dataset-id", default="ds_70d_clean_m1", help="artifact id (stable across regens)"
    )
    parser.add_argument(
        "--skip-parity",
        action="store_true",
        help="skip the canonical-vs-fast parity self-check (NOT for production)",
    )
    parser.add_argument(
        "--news-frame",
        default=None,
        help="optional news parquet/csv (causal bridge). Default: no news frame -> "
        "documented neutral 10D news block (FEATURE_DISABLED).",
    )
    args = parser.parse_args(argv)

    bars_path = Path(args.bars)
    if not bars_path.exists():
        print(f"FAIL: bars file missing: {bars_path}")
        return 2

    t_start = time.time()
    commit, dirty = _git_commit()
    news_frame = None
    if args.news_frame:
        nf = Path(args.news_frame)
        news_frame = pl.read_parquet(nf) if nf.suffix == ".parquet" else pl.read_csv(nf)

    df = load_bars(bars_path)
    print(
        f"[REGEN] bars: {df.height} rows "
        f"{df['time'].min()} -> {df['time'].max()} (source={bars_path})"
    )

    store = ArtifactStore()

    # ---- features (BUG-234 HTF window + BUG-106 fast builder + parity self-check)
    # The labeler override (stride 2) rides through SampleFactory inside
    # build_70d_dataset -> DatasetFactory; lineage CLEAN_HISTORICAL is stamped
    # by DatasetFactory.build (MLFIX-T7).
    build_70d_dataset(
        df,
        timeframe="M1",
        news_frame=news_frame,
        strategy_id=STRATEGY_ID,
        strategy_version="1.0.0",
        store=store,
        seed=SEED,
        dataset_id=args.dataset_id,
        incremental=True,
        verify_parity=not args.skip_parity,
    )

    # ---- enrich the factory manifest with the ML-DATASET-RETRAIN-PREP contract
    frame = store.read_dataset(args.dataset_id)
    if frame is None or frame.is_empty():
        print("FAIL: dataset frame missing after build")
        return 3

    man = dict(store.read_dataset_manifest(args.dataset_id) or {})
    eval_rows = int(frame.filter(pl.col("is_eval_sample") & ~pl.col("is_purged")).height)
    label_counts = (
        frame.filter(pl.col("is_eval_sample") & ~pl.col("is_purged"))["label"]
        .value_counts()
        .sort("label")
        .to_dicts()
    )
    seq_stats = gap_safe_window_stats(frame)
    dataset_sha = _sha256_file(store.dataset_path(args.dataset_id))

    man["contract"] = {
        "task": "ML-DATASET-RETRAIN-PREP",
        "htf_history_bars": HTF_HISTORY_BARS,
        "temporal_seq_len": SEQ_LEN,
        "temporal_max_gap_us": MAX_GAP_US,
        "purge_gap_bars": PURGE_BARS,
        "embargo_bars": EMBARGO_BARS,
        "label_schema_id": LABEL_SCHEMA_ID,
        "class_count": 3,
        "class_names": ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"],
        "no_trade_stride_bars": NO_TRADE_STRIDE_BARS,
        "builder": "compute_70d_frame_fast (BUG-106, byte-identical to canonical)",
        "parity_self_check": (not args.skip_parity),
        "smoke": False,
        "production_eligible": True,
    }
    man["sequence_windows"] = seq_stats
    man["dataset_sha256"] = dataset_sha
    man["seed"] = SEED
    man["git_commit"] = commit
    man["git_dirty"] = dirty
    man["source_bars"] = {
        "path": str(bars_path),
        "rows": int(df.height),
        "start": str(df["time"].min()),
        "end": str(df["time"].max()),
    }
    man["label_distribution"] = {str(d["label"]): int(d["count"]) for d in label_counts}
    man["eval_rows"] = eval_rows
    man["regenerated_at"] = datetime.now(UTC).isoformat()

    # DatasetManifest-shaped purge/embargo parameters (canonical bars values,
    # matching walk_forward_trainer + temporal_contract).
    man["purge_parameters"] = {
        "purge_gap_bars": PURGE_BARS,
        "embargo_bars": EMBARGO_BARS,
        "labeler_embargo_bars": 3,
        "no_trade_stride_bars": NO_TRADE_STRIDE_BARS,
    }
    man["embargo_parameters"] = {"embargo_bars": EMBARGO_BARS}
    store.write_json(store.dataset_manifest_path(args.dataset_id), man)

    # ---- hard gates
    verify = verify_70d_artifact(args.dataset_id, store=store)
    lineage_ok = man.get("label_origin") == "CLEAN_HISTORICAL"
    contract_ok = bool(man.get("feature_schema_hash"))
    gates = {
        "verify_70d_artifact_ok": bool(verify.get("ok")),
        "gap_safe_windows_ok": seq_stats.get("windows_valid", 0) > 0
        and bool(seq_stats.get("finite")),
        "label_integrity_ok": sorted(man.get("label_distribution", {}).keys()) == ["0", "1", "2"]
        and eval_rows > 0,
        "lineage_clean_historical_ok": lineage_ok,
        "schema_hash_ok": contract_ok,
        "parity_self_check": (not args.skip_parity),
    }
    verification = {
        "dataset_id": args.dataset_id,
        "dataset_path": str(store.dataset_path(args.dataset_id)),
        "manifest_path": str(store.dataset_manifest_path(args.dataset_id)),
        "dataset_sha256": dataset_sha,
        "rows": int(frame.height),
        "eval_rows": eval_rows,
        "verify_70d_artifact": verify,
        "sequence_windows": seq_stats,
        "gates": gates,
        "all_gates_pass": all(gates.values()),
        "elapsed_sec": round(time.time() - t_start, 1),
    }
    report_path = store.dataset_dir(args.dataset_id) / "verification.json"
    store.write_json(report_path, verification)

    print(json.dumps(verification, indent=2, default=str))
    if not verification["all_gates_pass"]:
        print("[REGEN] FAIL: one or more gates failed — see verification.json")
        return 1
    print(
        f"[REGEN] PASS: dataset {args.dataset_id} ready for full walk-forward retrain "
        f"({verification['rows']} rows / {eval_rows} eval, sha {dataset_sha[:16]})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
