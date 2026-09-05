"""Fair Model Benchmark Runner (PHASE 13B).

Runs the experiment matrix:

                  NO NEWS     NEWS
    LEGACY           A           B
    TCN_ATTENTION    C           D

on the SAME dataset artifact (same labels / splits / purge / embargo /
friction inherited from the labeler). Only the model architecture (and the
intentional news input) differ — spec 2 of the benchmark task.

Outputs:
    - per-experiment candidate artifacts in the ArtifactStore
    - model_benchmark_report.json (machine-readable)
    - model_benchmark_report.md  (human-readable)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.dataset_factory import DatasetFactory
from nexus_scalp.model_generation.experiment_factory import ExperimentFactory
from nexus_scalp.model_generation.sample_factory import SampleFactory
from nexus_scalp.model_generation.sequence_training import SequenceCandidateTrainer
from nexus_scalp.model_generation.training import CandidateTrainer
from nexus_scalp.model_generation.validation import (
    ValidationFactory,
    confusion_and_class_metrics,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.benchmark")

#: Matrix: (template, seq_arch, news) -> experiment kind
MATRIX: list[dict[str, Any]] = [
    # TASK-5 controlled matrix: same dataset/split/labels/purge/embargo/friction,
    # ONLY the schema dimension (50D vs 60D) and news input differ (spec 12).
    {
        "kind": "A",
        "template": "baseline_scalpnet_v1",
        "seq": False,
        "news": False,
        "schema": "scalp_v1",
    },
    {
        "kind": "B",
        "template": "baseline_scalpnet_v1_news",
        "seq": False,
        "news": True,
        "schema": "scalp_v1",
    },
    {
        "kind": "C",
        "template": "baseline_scalpnet_v1",
        "seq": False,
        "news": False,
        "schema": "scalp_v2",
    },
    {
        "kind": "D",
        "template": "baseline_scalpnet_v1_news",
        "seq": False,
        "news": True,
        "schema": "scalp_v2",
    },
    {"kind": "E", "template": "tcn_attention_v1", "seq": True, "news": False, "schema": "scalp_v1"},
    {
        "kind": "F",
        "template": "tcn_attention_v1_news",
        "seq": True,
        "news": True,
        "schema": "scalp_v1",
    },
    {"kind": "G", "template": "tcn_attention_v1", "seq": True, "news": False, "schema": "scalp_v2"},
    {
        "kind": "H",
        "template": "tcn_attention_v1_news",
        "seq": True,
        "news": True,
        "schema": "scalp_v2",
    },
]


class BenchmarkRunner:
    """Builds one shared dataset + runs the 4-experiment matrix fairly."""

    def __init__(
        self,
        store: ArtifactStore | None = None,
        seq_len: int = 16,
        seed: int = 42,
        report_dir: Path | str | None = None,
    ) -> None:
        self.store = store or ArtifactStore()
        self.seq_len = seq_len
        self.seed = seed
        self.report_dir = Path(report_dir) if report_dir else Path("artifacts/model_generation")
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        df: pl.DataFrame,
        *,
        symbol: str = "XAUUSD",
        timeframe: str = "M5",
        news_frame: pl.DataFrame | None = None,
        strategy_id: str = "scalp_default",
        strategy_version: str = "1.0.0",
        epochs: int | None = None,
        enforce_readiness: bool = True,
    ) -> dict[str, Any]:
        """Full fair benchmark. Returns the report dict + persists artifacts.

        ``enforce_readiness`` (default True): when ``news_frame`` is provided
        it MUST pass the real-data readiness gate (spec 20/22) — the old
        synthetic-fixture benchmark is impossible to reproduce by accident.
        Pass False only for benchmark unit tests that exercise the matrix
        mechanics with a fixture.
        """
        if news_frame is not None and enforce_readiness:
            from nexus_scalp.model_generation.news_bridge import news_benchmark_readiness

            gate = news_benchmark_readiness(news_frame)
            if not gate["ready"]:
                raise ValueError(
                    "BenchmarkRunner: news readiness gate FAILED — refusing a "
                    f"synthetic/no-data news benchmark. Checks: {gate['checks']}"
                )
        # ---- datasets per schema (same bars/splits/labels/purge/embargo/friction).
        # 50D cells share one scalp_v1 dataset; 60D cells share one scalp_v2
        # dataset, built from the SAME raw bars + the causal 10D augmenter
        # (schema_v2.compute_60d_frame). The comparison is therefore fair:
        # only the schema dimension (and the intentional news input) differ.
        base_samples = SampleFactory(feature_schema_id="scalp_v1")
        schema_datasets: dict[str, str] = {}
        schema_frames: dict[str, Any] = {}
        for cell in MATRIX:
            sid = cell["schema"]
            if sid in schema_datasets:
                continue
            if sid == "scalp_v2":
                from nexus_scalp.model_generation.schema_v2 import compute_60d_frame

                feat_frame = compute_60d_frame(df)
                if feat_frame.is_empty():
                    raise ValueError("BenchmarkRunner: 60D frame empty")
                dh = DatasetFactory(
                    store=self.store,
                    sample_factory=SampleFactory(feature_schema_id="scalp_v2"),
                ).build(
                    feat_frame,
                    symbol=symbol,
                    timeframe=timeframe,
                    news_frame=news_frame,
                    strategy_id=strategy_id,
                    strategy_version=strategy_version,
                )
            else:
                dh = DatasetFactory(store=self.store, sample_factory=base_samples).build(
                    df,
                    symbol=symbol,
                    timeframe=timeframe,
                    news_frame=news_frame,
                    strategy_id=strategy_id,
                    strategy_version=strategy_version,
                )
            schema_datasets[sid] = dh["dataset_id"]
            schema_frames[sid] = self.store.read_dataset(dh["dataset_id"])
            logger.info(
                "[BENCH] schema=%s dataset=%s rows=%d",
                sid,
                dh["dataset_id"],
                dh.get("counts", {}).get("total", 0),
            )

        results: dict[str, Any] = {}
        for cell in MATRIX:
            kind = cell["kind"]
            template = cell["template"]
            dataset_id = schema_datasets[cell["schema"]]
            frame = schema_frames[cell["schema"]]
            exp = ExperimentFactory(store=self.store).create(
                dataset_id,
                template=template,
                experiment_id=f"bench_{kind}_{template.lower()}",
                strategy_id=strategy_id,
                strategy_version=strategy_version,
            )
            mid = f"bench_{kind.lower()}_v1"

            if cell["seq"]:
                res = SequenceCandidateTrainer(
                    store=self.store, seq_len=self.seq_len
                ).train_candidate(exp, frame, model_id=mid, epochs=epochs)
            else:
                res = CandidateTrainer(store=self.store).train_candidate(
                    exp, frame, model_id=mid, epochs=epochs
                )

            if res["status"] == "FAILED":
                results[kind] = {"status": "FAILED", "error": res.get("error", "")}
                logger.error("[BENCH] %s FAILED: %s", kind, res.get("error"))
                continue

            # ---- validation + per-class metrics on the SAME split ----
            vf = ValidationFactory()
            labels = frame["label"].to_numpy().astype(np.int64)
            probs = _predict_probs(self.store, mid, frame, cell["seq"], res)
            vr = vf.validate(mid, exp.experiment_id, frame, probs, labels)
            if probs is not None:
                preds = np.argmax(probs, axis=1)
                if preds.shape[0] != len(labels):
                    # AGENT-16 (CHG-0064): previous fallback replaced the
                    # model predictions with the ground-truth labels, so the
                    # benchmark macro-F1/confusion table could silently
                    # report a perfect classifier when sequence windows
                    # misaligned with dataset rows. That is fabricated
                    # evidence; metrics are now honestly NOT_COMPUTABLE.
                    cm = {
                        "macro_f1": None,
                        "per_class": {},
                        "error": "PREDICTION_ROW_MISMATCH",
                        "n_preds": int(preds.shape[0]),
                        "n_labels": len(labels),
                    }
                else:
                    cm = confusion_and_class_metrics(labels, preds)
            else:
                cm = {"macro_f1": None, "per_class": {}}
            uniq, counts = np.unique(labels, return_counts=True)
            results[kind] = {
                "status": "COMPLETED",
                "model_id": mid,
                "experiment_id": exp.experiment_id,
                "architecture": exp.architecture,
                "news_enabled": exp.news_enabled,
                "val_accuracy": res.get("val_accuracy"),
                "ece": res.get("ece"),
                "validation_verdict": vr.verdict,
                "validation_gates": vr.gates,
                "macro_f1": cm.get("macro_f1"),
                "per_class": cm.get("per_class"),
                "class_distribution": {
                    str(int(k)): int(v) for k, v in zip(uniq, counts, strict=False)
                },
                "artifact": res.get("artifact", {}),
            }
            logger.info(
                "[BENCH] %s COMPLETED acc=%s ece=%s", kind, res.get("val_accuracy"), res.get("ece")
            )

        report = self._build_report(dataset_id, results, df, news_frame)
        self._write_report(report)
        return report

    # ------------------------------------------------------------------

    def _build_report(
        self,
        dataset_id: str,
        results: dict[str, Any],
        df: pl.DataFrame,
        news_frame: pl.DataFrame | None,
    ) -> dict[str, Any]:
        manifest = self.store.read_dataset_manifest(dataset_id) or {}
        pairs = {
            "legacy_50d": results.get("A", {}),
            "legacy_50d_news": results.get("B", {}),
            "legacy_60d": results.get("C", {}),
            "legacy_60d_news": results.get("D", {}),
            "tcn_50d": results.get("E", {}),
            "tcn_50d_news": results.get("F", {}),
            "tcn_60d": results.get("G", {}),
            "tcn_60d_news": results.get("H", {}),
        }
        conclusion = _conclude(pairs)
        return {
            "benchmark_id": f"bench_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
            "dataset_id": dataset_id,
            "dataset_manifest": manifest,
            "rows_input": df.height,
            "rows_news": news_frame.height if news_frame is not None else 0,
            "results": results,
            "comparison": pairs,
            "conclusion": conclusion,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    def _write_report(self, report: dict[str, Any]) -> None:
        import json

        jp = self.report_dir / "model_benchmark_report.json"
        jp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        md = self.report_dir / "model_benchmark_report.md"
        md.write_text(_render_md(report), encoding="utf-8")
        logger.info("[BENCH] report written json=%s md=%s", jp, md)


def _predict_probs(
    store: ArtifactStore, mid: str, frame: pl.DataFrame, seq: bool, res: dict[str, Any]
) -> Any:
    """Replays predictions with the candidate artifact (probs per sample)."""
    import numpy as np

    from nexus_scalp.model_generation.runtime import LocalModelRuntime

    try:
        rt = LocalModelRuntime(store=store).load(mid)
    except Exception:
        return None
    if not seq:
        feat_cols = [c for c in frame.columns if c.startswith("feat_")]
        mm = store.read_model_manifest(mid) or {}
        news_enabled = bool(mm.get("news_enabled", False))
        news_cols = (
            [c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"]
            if news_enabled
            else []
        )
        X = frame.select(feat_cols + news_cols).to_numpy().astype(np.float32)
        if rt._scaler is not None:
            mean, std = rt._scaler
            X = (X - mean) / (std + 1e-8)
        with __import__("torch").inference_mode():
            logits = rt._model(__import__("torch").from_numpy(X))
            return __import__("torch").softmax(logits, dim=-1).numpy()
    # sequence path: reuse SequenceBuilder for the same windows
    from nexus_scalp.model_generation.sequence import SequenceBuilder

    builder = SequenceBuilder(seq_len=16)
    mm = store.read_model_manifest(mid) or {}
    seqdata = builder.build(frame, news_enabled=bool(mm.get("news_enabled", False)))
    valid = seqdata["valid"]
    X = seqdata["X"][valid]
    if rt._scaler is not None:
        mean, std = rt._scaler
        X = ((X - mean) / (std + 1e-8)).astype(np.float32)
    with __import__("torch").inference_mode():
        logits = rt._model(__import__("torch").from_numpy(X))
        probs = __import__("torch").softmax(logits, dim=-1).numpy()
    # align probs to the FULL frame (invalid windows get a zero row) so the
    # per-class comparison uses the SAME sample set as the 2D path
    full = np.zeros((frame.height, probs.shape[1]), dtype=np.float32)
    rows = np.where(valid)[0]
    full[rows] = probs
    return full


def _conclude(pairs: dict[str, Any]) -> dict[str, Any]:
    def acc(p: dict[str, Any]) -> float | None:
        return p.get("val_accuracy") if p.get("status") == "COMPLETED" else None

    a = acc(pairs.get("legacy_50d", {}))
    b = acc(pairs.get("legacy_50d_news", {}))
    c = acc(pairs.get("legacy_60d", {}))
    d = acc(pairs.get("legacy_60d_news", {}))

    def verdict(new_val: float | None, old_val: float | None) -> str:
        if new_val is None or old_val is None:
            return "INCONCLUSIVE" if new_val is None and old_val is None else "LOW_EVIDENCE"
        if new_val - old_val > 0.02:
            return "BETTER"
        if old_val - new_val > 0.02:
            return "WORSE"
        return "INCONCLUSIVE"

    return {
        "architecture_verdict": verdict(
            max(filter(None, [c, d]), default=None), max(filter(None, [a, b]), default=None)
        ),
        "news_off": {"legacy": a, "new": c},
        "news_on": {"legacy": b, "new": d},
        "news_helpful": verdict(
            max(filter(None, [b, d]), default=None), max(filter(None, [a, c]), default=None)
        ),
        "note": (
            "Point estimates only; statistical significance requires larger "
            "samples (see report.remaining_risks). No auto-promotion."
        ),
    }


def _render_md(report: dict[str, Any]) -> str:
    lines = [
        "# Model Generation Benchmark Report",
        "",
        f"- benchmark_id: `{report['benchmark_id']}`",
        f"- dataset_id: `{report['dataset_id']}`",
        f"- input rows: {report['rows_input']} (news events: {report['rows_news']})",
        "",
        "## Results",
        "",
        "| Kind | Arch | News | Status | val_acc | ECE | macro-F1 | Verdict |",
        "|------|------|------|--------|---------|-----|----------|---------|",
    ]
    kind_info = {
        "A": ("LEGACY_SCALPNET_V1 50D", "Off"),
        "B": ("LEGACY_SCALPNET_V1 50D", "On"),
        "C": ("LEGACY_SCALPNET_V1 60D", "Off"),
        "D": ("LEGACY_SCALPNET_V1 60D", "On"),
        "E": ("TCN_ATTENTION_V1 50D", "Off"),
        "F": ("TCN_ATTENTION_V1 50D", "On"),
        "G": ("TCN_ATTENTION_V1 60D", "Off"),
        "H": ("TCN_ATTENTION_V1 60D", "On"),
    }
    for kind, (arch, news) in kind_info.items():
        r = report["results"].get(kind, {})
        if r.get("status") != "COMPLETED":
            lines.append(
                f"| {kind} | {arch} | {news} | FAILED | — | — | — | {r.get('error', '')} |"
            )
            continue
        lines.append(
            f"| {kind} | {arch} | {news} | {r['status']} | {r.get('val_accuracy')} "
            f"| {r.get('ece')} | {r.get('macro_f1')} | {r.get('validation_verdict')} |"
        )
    lines.append("")
    lines.append("## Conclusion")
    for k, v in report["conclusion"].items():
        lines.append(f"- **{k}**: `{v}`")
    lines.append("")
    lines.append("_Report generated by Nexus Model Generation benchmark runner._")
    return "\n".join(lines) + "\n"
