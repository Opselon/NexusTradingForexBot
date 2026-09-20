#!/usr/bin/env python3
"""ML-FEAT-002 — Feature Importance & Collinearity Audit CLI (Stream B).

WHY THIS EXISTS
---------------
No empirical feature-importance ranking existed in the repository, so every
"this feature is probably redundant" claim about the 50D ``scalp_v1`` vector
was an opinion.  This script manufactures the evidence: a Spearman
collinearity map, mutual information against the triple-barrier label, and an
out-of-sample permutation-importance ranking over the full 50-feature
contract, published as a citable audit artifact.

PIPELINE
--------
1. Acquire an evaluation frame: a committed manifest-verified dataset
   artifact, a raw parquet bar file with ``feat_*`` columns, or a
   deterministic synthetic bar series materialised through the REAL
   ``ScalpFeatureEngine`` + triple-barrier labeler (the repo ships no market
   data by design — ML-DATA-001 keeps ``data/raw`` gitignored).
2. Extract the ``(n, 50)`` matrix + integer labels.
3. Run :func:`nexus_scalp.features.importance.analyze_features`.
4. Emit:
     --report-json  human/governance ranking + clusters + prune candidates
     --report-npz   dense Spearman / MI / PFI arrays (the artifacts/research/
                    correlation-matrix artifact the task requires)
     --report-md    docs/research/FEATURE_IMPORTANCE_AUDIT.md (default)

DETERMINISM
-----------
``--seed`` pins every shuffle.  Two runs with identical inputs produce
byte-identical rankings, which is what makes the artifact citable in a
governance record and reproducible by a third agent.

USAGE
-----
    # Full audit on the real feature engine (no market data required)
    python scripts/analysis/evaluate_feature_importance.py \\
        --bar-count 6000 --seed 42 \\
        --report-md docs/research/FEATURE_IMPORTANCE_AUDIT.md \\
        --report-json artifacts/research/feature_importance.json \\
        --report-npz artifacts/research/correlation_matrix.npz

    # Audit an existing dataset artifact
    python scripts/analysis/evaluate_feature_importance.py \\
        --dataset ds_d6c4808f97c1c664 --val-fraction 0.3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from nexus_scalp.features.importance import (
    COLLINEARITY_THRESHOLD,
    ImportanceReport,
    analyze_features,
    save_report,
)
from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.observability.logging import get_logger

logger = get_logger("scripts.analysis.evaluate_feature_importance")


# ==============================================================================
# Evaluation frame construction
# ==============================================================================


class EvaluationFrame:
    """An ``(n, n_features)`` matrix + labels ready for the importance battery."""

    def __init__(
        self,
        matrix: np.ndarray,
        labels: np.ndarray,
        feature_names: tuple[str, ...],
        provenance: str,
        timestamp_column: np.ndarray | None = None,
    ) -> None:
        if matrix.ndim != 2:
            raise ValueError(f"matrix must be 2-D, got {matrix.shape}")
        if matrix.shape[0] != labels.shape[0]:
            raise ValueError(f"row mismatch: matrix {matrix.shape[0]} vs labels {labels.shape[0]}")
        if matrix.shape[1] != len(feature_names):
            raise ValueError(
                f"matrix width {matrix.shape[1]} != feature_names {len(feature_names)}"
            )
        self.matrix = matrix
        self.labels = labels
        self.feature_names = tuple(feature_names)
        self.provenance = provenance
        self.timestamp_column = timestamp_column

    @property
    def n_samples(self) -> int:
        return int(self.matrix.shape[0])

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_samples": self.n_samples,
            "n_features": len(self.feature_names),
            "provenance": self.provenance,
        }


def _bars_to_frame_rows(bars: Any) -> tuple[np.ndarray, np.ndarray | None]:
    """Extract timestamps + OHLCV views from a polars bar frame, order-insensitive."""
    ts_col = "timestamp" if "timestamp" in bars.columns else "time"
    timestamps = bars[ts_col].to_numpy() if ts_col in bars.columns else None
    return np.asarray(bars["close"].to_numpy(), dtype=np.float64), timestamps


def build_evaluation_frame(
    bar_count: int = 4000,
    seed: int = 42,
    *,
    dataset_id: str | None = None,
    parquet_path: Path | str | None = None,
    engine: Any = None,
) -> EvaluationFrame:
    """Build the ``(n, 50)`` evaluation frame through the REAL feature pipeline.

    ``data/raw`` is gitignored (ML-DATA-001) and no broker history is
    reachable from a clean checkout, so the default path materialises a
    deterministic synthetic M1 series and runs it through the production
    ``ScalpFeatureEngine`` and the friction-aware triple-barrier labeler —
    the same two stages the dataset factory uses, never a hand-rolled
    feature approximation.  Pass ``--dataset``/``--parquet`` to audit real
    bars instead.

    The engine needs 55+ completed bars before it stops emitting the
    cold-start vector, and the labeler's triple barrier has a forward
    horizon of ``max_holding_bars`` (15), so the usable tail is shorter than
    ``bar_count``.  Both edges are dropped here, not silently scored.
    """
    if dataset_id is not None or parquet_path is not None:
        return _frame_from_parquet(dataset_id, parquet_path)

    if engine is None:
        from nexus_scalp.features.scalp_features import ScalpFeatureEngine

        engine = ScalpFeatureEngine(symbol="XAUUSD")

    from scripts.data.ingest_historical_candles import generate_synthetic_bars

    bars_pl = generate_synthetic_bars(symbol="XAUUSD", count=bar_count, seed=seed)
    bars = _polars_frame_to_bars(bars_pl)
    if len(bars) < 60:
        raise ValueError(f"need >= 60 bars to warm the feature engine, got {len(bars)}")

    from nexus_scalp.domain.models import TickData
    from nexus_scalp.features.scalp_features import FeatureVector

    rows: list[list[float]] = []
    ts_out: list[Any] = []
    for i in range(55, len(bars)):
        history = bars[max(0, i - 4000) : i]
        bar = bars[i]
        tick = TickData(
            symbol=bar.symbol,
            timestamp=bar.timestamp,
            bid=bar.close,
            ask=bar.close,
            last=bar.close,
            volume=bar.tick_volume,
        )
        vector: FeatureVector = engine.compute_from_bars(history, tick)
        rows.append(vector.to_tensor_input())
        ts_out.append(bar.timestamp)

    matrix = np.asarray(rows, dtype=np.float64)
    # tz-aware datetimes -> epoch seconds. np.datetime64 cannot represent a
    # timezone and warns when handed one, so convert explicitly.
    timestamps = np.asarray([t.timestamp() for t in ts_out], dtype=np.int64) if ts_out else None

    labels = _label_series(bars, start_index=55, seed=seed)
    usable = min(len(rows), len(labels))
    if usable < 50:
        raise ValueError(
            f"usable labelled feature rows {usable} < 50; raise --bar-count "
            f"(the triple-barrier labeler's purge/stride removes most rows)"
        )
    provenance = (
        f"synthetic XAUUSD M1 ({bar_count} bars, seed={seed}) -> ScalpFeatureEngine "
        f"(real 50D contract) + TripleBarrierLabeler; {usable} labelled rows"
    )
    return EvaluationFrame(
        matrix=matrix[:usable],
        labels=labels[:usable],
        feature_names=FEATURE_NAMES,
        provenance=provenance,
        timestamp_column=timestamps[:usable] if timestamps is not None else None,
    )


def _polars_frame_to_bars(bars_pl: Any) -> list[Any]:
    """Convert the ingest harness's polars frame to the engine's ``BarData`` list."""
    from nexus_scalp.market_data.bar_aggregator import BarData

    ts_col = "timestamp" if "timestamp" in bars_pl.columns else "time"
    volume_col = "tick_volume" if "tick_volume" in bars_pl.columns else "volume"
    out: list[BarData] = []
    for rec in bars_pl.iter_rows(named=True):
        out.append(
            BarData(
                symbol=str(rec.get("symbol", "XAUUSD")),
                timeframe="M1",
                timestamp=rec[ts_col],
                open=float(rec["open"]),
                high=float(rec["high"]),
                low=float(rec["low"]),
                close=float(rec["close"]),
                tick_volume=int(float(rec.get(volume_col, 0) or 0)),
                is_complete=True,
            )
        )
    return out


def _label_series(bars: list[Any], start_index: int, seed: int) -> np.ndarray:
    """Triple-barrier labels aligned to the feature-row index.

    Uses the production labeler on a close/high/low/ATR frame exactly like
    ``SampleFactory.build_samples`` does.  ``seed`` only perturbs the
    synthetic bar generator (already consumed) — the labeler itself is a
    deterministic function of price, so labels are reproducible.
    """
    import polars as pl

    from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler

    closes = np.asarray([b.close for b in bars], dtype=np.float64)
    highs = np.asarray([b.high for b in bars], dtype=np.float64)
    lows = np.asarray([b.low for b in bars], dtype=np.float64)
    # Simple wilder-style ATR proxy on the synthetic series: the labeler
    # needs an ATR column, and the synthetic bars carry no volatility model,
    # so a rolling true-range mean is the honest estimator of local range.
    tr = np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1)))
    tr[0] = highs[0] - lows[0]
    window = 14
    atr = np.convolve(tr, np.ones(window) / window, mode="same")
    atr = np.maximum(atr, 1e-6)

    label_frame = pl.DataFrame(
        {
            "close": closes,
            "high": highs,
            "low": lows,
            "atr": atr,
        }
    )
    labeled = TripleBarrierLabeler().label_dataframe(label_frame)
    label_str = labeled["label"].to_list()
    mapping = {"NO_TRADE": 0, "BUY_MARKET": 1, "SELL_MARKET": 2}
    encoded = np.asarray([mapping.get(str(v), 0) for v in label_str], dtype=np.int64)
    return encoded[start_index:]


def _frame_from_parquet(dataset_id: str | None, parquet_path: Path | str | None) -> EvaluationFrame:
    """Load a dataset artifact or raw parquet bar frame with ``feat_*`` columns."""
    import polars as pl

    if dataset_id is not None:
        from nexus_scalp.model_generation.artifact_store import ArtifactStore

        store = ArtifactStore()
        frame = store.read_dataset(dataset_id)
        if frame is None or frame.is_empty():
            raise FileNotFoundError(f"dataset artifact {dataset_id} not found or empty")
        source = f"dataset artifact {dataset_id}"
        if "label" not in frame.columns:
            raise ValueError(f"dataset {dataset_id} has no 'label' column")
        labels = frame["label"].cast(pl.Int64).to_numpy()
        ts_col = "timestamp" if "timestamp" in frame.columns else None
        timestamps = (
            np.asarray([t.timestamp() for t in frame[ts_col].to_list()], dtype=np.int64)
            if ts_col
            else None
        )
    else:
        path = Path(parquet_path)
        if not path.exists():
            raise FileNotFoundError(f"parquet file not found: {path}")
        frame = pl.read_parquet(path)
        source = f"parquet {path}"
        labels = (
            frame["label"].cast(pl.Int64).to_numpy()
            if "label" in frame.columns
            else np.zeros(frame.height, dtype=np.int64)
        )
        ts_col = (
            "timestamp"
            if "timestamp" in frame.columns
            else ("time" if "time" in frame.columns else None)
        )
        timestamps = (
            np.asarray([t.timestamp() for t in frame[ts_col].to_list()], dtype=np.int64)
            if ts_col
            else None
        )

    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    if not feat_cols:
        raise ValueError(
            f"{source}: no feat_* columns found; the frame must carry the materialised "
            "feature vector (see SampleFactory / DatasetFactory)"
        )
    feat_cols.sort(key=lambda c: int(c.split("_")[1]))
    names = tuple(c.replace("feat_", "") for c in feat_cols)
    if len(names) == len(FEATURE_NAMES):
        names = FEATURE_NAMES
    matrix = frame.select(feat_cols).to_numpy().astype(np.float64)
    if "label" not in frame.columns:
        labels = np.zeros(len(matrix), dtype=np.int64)
    return EvaluationFrame(
        matrix=matrix,
        labels=labels,
        feature_names=names,
        provenance=f"{source}; {len(matrix)} rows, {len(names)} features",
        timestamp_column=timestamps,
    )


# ==============================================================================
# Markdown audit report
# ==============================================================================


def render_markdown(report: ImportanceReport, frame: EvaluationFrame) -> str:
    """Render the audit as the docs/research/ artifact the task requires."""
    ranking = report.importance_ranking()
    pairs = report.collinear_pairs
    clusters = report.clusters
    prune = report.prune_recommendation()
    rho = "rho"  # ASCII: RUF001 flags the Greek glyph as an ambiguous lookalike
    lines: list[str] = []

    lines.append("# Feature Importance & Collinearity Audit — `scalp_v1` (50D)")
    lines.append("")
    lines.append("> ML-FEAT-002 (Stream B) — automated feature evaluation. READ-ONLY:")
    lines.append("> no feature is deleted, renamed or reordered here. This document is")
    lines.append("> the empirical input a *future* schema version cites before pruning.")
    lines.append("")
    lines.append(f"- **Feature contract:** `scalp_v1` / {report.n_features} dimensions")
    lines.append(f"- **Samples analysed:** {report.n_samples:,}")
    lines.append(f"- **Collinearity threshold:** |Spearman {rho}| > {report.correlation_threshold}")
    lines.append(f"- **Scoring model:** {report.model_description}")
    lines.append(
        "- **Determinism:** seed-pinned permutations (identical input -> identical output)"
    )
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("1. **Spearman rank correlation** across all 50 features — rank transform")
    lines.append("   captures monotonic non-linear relationships Pearson misses (the task's")
    lines.append("   INVESTIGATION_PLAN).")
    lines.append("2. **Mutual information** I(feature; triple-barrier label), equal-width")
    lines.append("   binned, numpy-only (the slim verification venv carries no scikit-learn).")
    lines.append("3. **Permutation feature importance** on a chronological held-out tail:")
    lines.append("   each column is shuffled *in validation only* and the cross-entropy")
    lines.append("   increase is the feature's measured contribution.")
    lines.append("")
    lines.append("## Top-10 Alpha Drivers (out-of-sample permutation importance)")
    lines.append("")
    lines.append("| Rank | Feature | PFI (mean Δloss) | PFI std | Mutual information |")
    lines.append("|------|---------|------------------|---------|--------------------|")
    for row in ranking[:10]:
        lines.append(
            f"| {row['rank']} | `{row['feature']}` | {row['mean_permutation_importance']:.6f} "
            f"| {row['std_permutation_importance']:.6f} | {row['mutual_information']:.6f} |"
        )
    lines.append("")

    if report.constant_features:
        lines.append("## Constant Columns (zero variance in this evaluation frame)")
        lines.append("")
        lines.append("Permuting a constant column is a mathematical no-op, so PFI is")
        lines.append("reported as NaN rather than a misleading 0.000000:")
        lines.append("")
        for name in report.constant_features:
            lines.append(f"- `{name}`")
        lines.append("")
    else:
        lines.append("## Constant Columns")
        lines.append("")
        lines.append("None — every one of the 50 columns varied in this evaluation frame.")
        lines.append("")

    lines.append(f"## Collinear Pairs (|Spearman rho| > {report.correlation_threshold})")
    lines.append("")
    if pairs:
        lines.append("| # | Feature A | Feature B | Spearman rho |")
        lines.append("|---|-----------|-----------|------------|")
        for i, pair in enumerate(pairs, start=1):
            lines.append(
                f"| {i} | `{pair.feature_a}` | `{pair.feature_b}` | {pair.spearman_rho:+.4f} |"
            )
        lines.append("")
        lines.append(f"**Total:** {len(pairs)} collinear pair(s).")
    else:
        lines.append("None at the configured threshold.")
    lines.append("")

    lines.append("## Hierarchical Collinearity Clusters")
    lines.append("")
    if clusters:
        lines.append("| Cluster | Representative | Members | Max |rho| | Redundant |")
        lines.append("|---------|----------------|---------|-----------|-----------|")
        for cluster in clusters:
            redundant = ", ".join(f"`{m}`" for m in cluster.redundant_members) or "—"
            members = ", ".join(f"`{m}`" for m in cluster.members)
            lines.append(
                f"| {cluster.cluster_id} | `{cluster.representative}` | {members} "
                f"| {cluster.max_abs_correlation:.4f} | {redundant} |"
            )
        lines.append("")
        lines.append(
            f"**{len(prune)} redundant feature(s)** identified across {len(clusters)} cluster(s)."
        )
    else:
        lines.append("No multi-member clusters formed at the configured threshold.")
    lines.append("")

    lines.append("## Pruning Candidates (evidence, not a change)")
    lines.append("")
    lines.append("> NON_GOAL of ML-FEAT-002: `scalp_v1` stays backwards compatible until a")
    lines.append("> new schema version is approved. These are the rows that proposal cites.")
    lines.append("")
    if prune:
        lines.append("| Drop | Keeps | Cluster max |rho| | PFI of dropped |")
        lines.append("|------|-------|------------------|----------------|")
        for row in prune:
            lines.append(
                f"| `{row['drop_feature']}` | `{row['keep_feature']}` "
                f"| {row['cluster_max_abs_correlation']:.4f} "
                f"| {row['mean_permutation_importance']:.6f} |"
            )
    else:
        lines.append("No pruning candidates at this threshold.")
    lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    non_nan = [r for r in ranking if not np.isnan(r["mean_permutation_importance"])]
    if non_nan:
        best = non_nan[0]
        worst = non_nan[-1]
        lines.append(
            f"- Highest measured out-of-sample contribution: `{best['feature']}` "
            f"(PFI {best['mean_permutation_importance']:.6f})."
        )
        lines.append(
            f"- Lowest: `{worst['feature']}` (PFI {worst['mean_permutation_importance']:.6f}) — "
            "a low PFI means the scoring model did not use the column, which is model-"
            "dependent evidence, not proof the feature is worthless."
        )
    if clusters:
        lines.append(
            f"- {len(prune)} of {report.n_features} features are statistically redundant "
            "neighbours of a stronger column at this threshold."
        )
    lines.append(
        f"- Frame provenance: {frame.provenance}. Synthetic bars exercise every feature "
        "branch of the production engine but do not carry real market microstructure; "
        "re-run with `--dataset <id>` on a broker-history artifact for the live verdict."
    )
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    lines.append("- Dense Spearman / MI / PFI arrays: `artifacts/research/correlation_matrix.npz`")
    lines.append("- Machine-readable ranking: `artifacts/research/feature_importance.json`")
    lines.append("")
    lines.append("---")
    lines.append("Generated by `scripts/analysis/evaluate_feature_importance.py` (ML-FEAT-002).")
    return "\n".join(lines)


# ==============================================================================
# CLI
# ==============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ML-FEAT-002: feature importance, collinearity clustering & redundancy audit.",
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--bar-count",
        type=int,
        default=4000,
        help="synthetic M1 bar count when no market data is available (default 4000)",
    )
    src.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="dataset artifact id (e.g. ds_d6c4808f97c1c664) carrying feat_* + label columns",
    )
    src.add_argument(
        "--parquet",
        type=str,
        default=None,
        help="path to a parquet frame with feat_* columns (label optional)",
    )
    parser.add_argument("--seed", type=int, default=42, help="permutation + generator seed")
    parser.add_argument(
        "--threshold",
        type=float,
        default=COLLINEARITY_THRESHOLD,
        help=f"absolute Spearman collinearity threshold (default {COLLINEARITY_THRESHOLD})",
    )
    parser.add_argument(
        "--val-fraction", type=float, default=0.3, help="chronological validation share"
    )
    parser.add_argument("--repeats", type=int, default=5, help="permutation repeats per feature")
    parser.add_argument(
        "--mi-bins", type=int, default=16, help="mutual-information quantisation bins"
    )
    parser.add_argument(
        "--report-json",
        type=str,
        default="artifacts/research/feature_importance.json",
        help="output JSON ranking artifact",
    )
    parser.add_argument(
        "--report-npz",
        type=str,
        default="artifacts/research/correlation_matrix.npz",
        help="output dense-matrix artifact (the artifacts/research correlation matrix)",
    )
    parser.add_argument(
        "--report-md",
        type=str,
        default="docs/research/FEATURE_IMPORTANCE_AUDIT.md",
        help="output markdown audit document",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=50_000,
        help="cap on rows analysed (memory guard; the task benchmark is 50k)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the stdout summary",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        help="structlog level for the audit run (default WARNING: the JSON summary is "
        "the stdout contract and structlog's UNCONFIGURED PrintLogger otherwise "
        "writes INFO records onto stdout and corrupts it)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    started = time.perf_counter()

    # BUG-303 / self-update CLI lesson: structlog is UNCONFIGURED in a fresh CLI
    # process, so its fallback PrintLogger writes every INFO record to STDOUT.
    # This tool's stdout contract is a single JSON object; a log line in front
    # of it makes the payload unparseable. Configure logging (console -> stderr)
    # before any logger emits. Engine severity routing is unchanged.
    from nexus_scalp.observability.logging import configure_logging

    configure_logging(log_level=args.log_level, log_to_file=False)

    frame = build_evaluation_frame(
        bar_count=args.bar_count,
        seed=args.seed,
        dataset_id=args.dataset,
        parquet_path=args.parquet,
    )
    matrix, labels = frame.matrix, frame.labels
    if frame.n_samples > args.max_samples:
        # Subsample the TAIL (most recent): keeps the chronological split honest
        # and keeps the newest regime rather than an arbitrary slice.
        matrix = matrix[-args.max_samples :]
        labels = labels[-args.max_samples :]
        if frame.timestamp_column is not None:
            frame.timestamp_column = frame.timestamp_column[-args.max_samples :]
        logger.info("capped evaluation frame", kept=args.max_samples, had=frame.n_samples)

    report = analyze_features(
        features=matrix,
        labels=labels,
        feature_names=frame.feature_names,
        correlation_threshold=args.threshold,
        val_fraction=args.val_fraction,
        permutation_repeats=args.repeats,
        mi_bins=args.mi_bins,
        seed=args.seed,
        metadata={
            "feature_schema_id": "scalp_v1",
            "frame_provenance": frame.provenance,
            "cli_args": {k: v for k, v in vars(args).items() if k not in ("quiet",)},
            "analysed_rows": len(labels),
        },
    )

    save_report(report, args.report_json, args.report_npz)
    md_path = Path(args.report_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        render_markdown(
            report, EvaluationFrame(matrix, labels, frame.feature_names, frame.provenance)
        ),
        encoding="utf-8",
    )

    elapsed = time.perf_counter() - started
    if not args.quiet:
        top = ", ".join(
            f"{r['feature']}={r['mean_permutation_importance']:.4f}"
            for r in report.importance_ranking()[:5]
            if not np.isnan(r["mean_permutation_importance"])
        )
        summary = {
            "task": "ML-FEAT-002",
            "n_samples": len(labels),
            "n_features": report.n_features,
            "collinear_pairs": len(report.collinear_pairs),
            "clusters": len(report.clusters),
            "prune_candidates": len(report.prune_recommendation()),
            "constant_features": list(report.constant_features),
            "top_5_pfi": top,
            "elapsed_seconds": round(elapsed, 3),
            "json": args.report_json,
            "npz": args.report_npz,
            "markdown": args.report_md,
        }
        # Machine-readable stdout: write raw JSON, never a rich/console-wrapped
        # variant (the --json line-wrapping trap).
        sys.stdout.write(json.dumps(summary, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
