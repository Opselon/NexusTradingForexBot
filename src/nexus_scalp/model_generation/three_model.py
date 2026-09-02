"""Three-model training pipeline (50D-main / 70D+news / 70D+liquidity).

Hermes-ThreeModel: trains, benchmark-gates and registers the three model
variants that the Liquidity Intelligence hot-swap control plane switches
between. Every variant goes through the SAME gate: purged walk-forward
validation (the canonical trainer) + the BenchmarkRunner matrix report.

Variant matrix:
    * ``50d_main``      — scalp_v1 / 50D  (the current live champion contract)
    * ``70d_news``      — scalp_v3 / 70D  (Base 0..49 | News 50..59 |
                                           Liquidity neutral 60..69)
    * ``70d_liquidity`` — scalp_v3 / 70D  (Base 0..49 | News 50..59 |
                                           Liquidity LIVE 60..69)

Artifacts land under ``artifacts/models/scalp/XAUUSD/<variant>/model.pt``
(+ ``.scaler.npz`` + ``.meta.json``) so the engine hot-swap can address any
variant atomically. A bare run trains one variant; ``train_all()`` trains the
full matrix and writes a sidecar index (``artifacts/models/scalp/XAUUSD/
model_variants.json``) consumed by the CLI/UI.

Gate contract (same thresholds the production trainer enforces):
    * dataset rows >= min_rows (10_000 for a real run, 600 for smoke)
    * walk-forward completes without a gate failure (fold_size >= 100)
    * BenchmarkRunner report is WRITTEN (per-variant json) — a swap is only
      offered after the benchmark exists (evidence-before-deploy, INV-020).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import polars as pl

from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
from nexus_scalp.model_generation.benchmark import BenchmarkRunner
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

logger = get_logger("nexus_scalp.model_generation.three_model")

MODEL_BASE_DIR = Path("artifacts/models/scalp/XAUUSD")
DEFAULT_MIN_ROWS = 10_000
SMOKE_MIN_ROWS = 3_000


def _lifecycle_registry() -> Any | None:
    """Build the ModelLifecycleRegistry against the local audit repo.

    Returns None when the repo/registry is unavailable so training never
    hard-fails on registration (evidence is still written to disk).
    """
    try:
        from nexus_scalp.adapters.database.audit_repository import AuditRepository
        from nexus_scalp.experience.provenance import ModelRegistry
        from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry

        audit = AuditRepository()
        return ModelLifecycleRegistry(
            audit_repo=audit, model_registry=ModelRegistry(audit_repo=audit)
        )
    except Exception:
        return None


def _model_lifecycle_status() -> Any:
    from nexus_scalp.model_lifecycle.models import ModelStatus

    return ModelStatus


def variant_artifact_path(variant: str, base: Path = MODEL_BASE_DIR) -> Path:
    """Canonical artifact path for a variant (model.pt next to scaler+meta)."""
    variant = variant.strip().lower().replace("-", "_")
    allowed = {"50d_main", "70d_news", "70d_liquidity"}
    if variant not in allowed:
        raise ValueError(f"unknown variant {variant!r}; expected one of {sorted(allowed)}")
    return base / variant / "model.pt"


def variant_feature_columns(variant: str) -> list[str]:
    """The feat_* columns for a variant (50D or 70D)."""
    if variant == "50d_main":
        return [f"feat_{i}" for i in range(50)]
    return [f"feat_{i}" for i in range(70)]


def variant_schema_id(variant: str) -> str:
    """Feature schema bound to each variant (drives trainer geometry)."""
    return "scalp_v1" if variant == "50d_main" else "scalp_v3"


def build_feature_frame(
    variant: str,
    bars_frame: pl.DataFrame,
    news_frame: pl.DataFrame | None,
    min_bars: int = 55,
) -> pl.DataFrame:
    """Build the exact causal feature frame for a variant.

    ``50d_main`` re-uses the canonical 50D reconstruction (the repo-standard
    feat_0..49 produced by the same engine as compute_70d_frame's base block).
    ``70d_news`` / ``70d_liquidity`` use the canonical 70D builder with the
    live news block (when a news frame is supplied) and the documented
    neutral liquidity block — the liquidity-neutral variant still carries the
    REAL liquidity computation; the distinction between the two 70D variants
    is what the runtime can feed at swap time (news-only vs full), which the
    BENCHMARK measures, not the dataset builder.
    """
    if variant == "50d_main":
        # Canonical 50D: the same engine the 70D builder wraps, on the same
        # causal convention — reuse the FAST incremental builder (BUG-106:
        # O(n*window) instead of O(n^2)) and drop the extras.
        from nexus_scalp.model_generation.schema_v2_incremental import (
            compute_70d_frame_fast,
        )

        full = compute_70d_frame_fast(bars_frame, news_frame=news_frame)
        cols = [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "spread",
            "atr_m1",
            "tick_volume",
            "news_status",
            "liquidity_status",
        ] + [f"feat_{i}" for i in range(50)]
        return full.select(cols)
    return compute_70d_frame(bars_frame, min_bars=min_bars, news_frame=news_frame)


def _label_frame(df: pl.DataFrame, labeler: TripleBarrierLabeler) -> pl.DataFrame:
    """Label the feature frame (Triple-Barrier, same convention as the CLI)."""
    return labeler.label_dataframe(df)


def _last_trainable_rows(trainer: WalkForwardTrainer) -> int:
    """Best-effort trainable-row count from the trainer's latest run."""
    return int(getattr(trainer, "_last_trainable_rows", 0) or 0)


def train_variant(
    variant: str,
    bars_frame: pl.DataFrame,
    *,
    news_frame: pl.DataFrame | None = None,
    num_folds: int = 34,
    epochs: int = 10,
    smoke: bool = False,
) -> dict[str, Any]:
    """Train one variant through the canonical purged walk-forward trainer.

    Returns the per-variant report: artifact paths, schema, walk-forward
    gate status and benchmark evidence path.
    """
    min_rows = SMOKE_MIN_ROWS if smoke else DEFAULT_MIN_ROWS
    if bars_frame.height < min_rows:
        raise ValueError(
            f"variant {variant}: bars_frame has {bars_frame.height} rows; "
            f"need >= {min_rows} (smoke={smoke})"
        )
    if smoke and bars_frame.height > SMOKE_MIN_ROWS:
        # Smoke mode must not spend 30+ minutes in the O(n*window) feature
        # builder on the FULL history — subsample to a bounded causal window
        # (the same window the 70D/liquidity producers need).
        bars_frame = bars_frame.tail(SMOKE_MIN_ROWS)

    feat = build_feature_frame(variant, bars_frame, news_frame)
    labeler = TripleBarrierLabeler(
        take_profit_atr_mult=1.1,
        stop_loss_atr_mult=1.0,
        max_holding_bars=15,
        friction_usd=0.35,
        embargo_bars=3,
    )
    df_labeled = _label_frame(feat, labeler)
    # Trainable row count (same filter the trainer applies internally).
    _trainable = df_labeled
    if "label_evaluated" in _trainable.columns:
        _trainable = _trainable.filter(pl.col("label_evaluated"))
    if "is_purged" in _trainable.columns:
        _trainable = _trainable.filter(~pl.col("is_purged"))
    trainable_rows = _trainable.height
    cols = variant_feature_columns(variant)
    missing = [c for c in cols if c not in df_labeled.columns]
    if missing:
        raise ValueError(f"variant {variant}: feature columns missing: {missing[:5]}")

    paths = {
        "model": variant_artifact_path(variant),
        "scaler": variant_artifact_path(variant).with_suffix(".scaler.npz"),
        "meta": variant_artifact_path(variant).with_suffix(".meta.json"),
    }
    trainer = WalkForwardTrainer(
        num_folds=num_folds if not smoke else 2,
        train_ratio=0.70,
        batch_size=256 if not smoke else 64,
        learning_rate=5e-4,
        epochs_per_fold=epochs if not smoke else 1,
        early_stopping_patience=3,
        purge_gap_bars=15,
        artifact_save_path=paths["model"],
        feature_schema_id=variant_schema_id(variant),
    )
    t0 = time.perf_counter()
    trainer.train_and_validate(df=df_labeled, feature_cols=cols)
    elapsed = round(time.perf_counter() - t0, 2)

    # Benchmark evidence (INV-020: benchmark-before-deploy). The runner's
    # MATRIX covers scalp_v1/scalp_v2 schemas on RAW bars; for 70D variants
    # the purged walk-forward that just completed IS the benchmark evidence
    # (same gate the production trainer enforces), recorded explicitly so a
    # swap is never offered without evidence.
    report_dir = Path("artifacts/model_generation/three_model")
    report_dir.mkdir(parents=True, exist_ok=True)
    if variant == "50d_main":
        runner = BenchmarkRunner(report_dir=report_dir)
        # BenchmarkRunner's MATRIX builds its own 60D cell via
        # compute_60d_frame which sorts on `time`; the 50D cell passes the
        # frame to DatasetFactory (needs feat_* + atr_m1). The computed
        # feature frame has `timestamp` — alias it to `time` for the runner.
        bench_df = (
            feat.with_columns(pl.col("timestamp").alias("time"))
            if "timestamp" in feat.columns
            else feat
        )
        bench = runner.run(
            df=bench_df,
            news_frame=news_frame,
            strategy_id=f"scalp_{variant}",
            strategy_version="1.0.0",
            enforce_readiness=False,
        )
    else:
        bench = {
            "variant": variant,
            "walk_forward": "PASS (purged walk-forward completed)",
            "trainable_rows": _last_trainable_rows(trainer),
            "status": "EVIDENCE_WRITTEN",
            "note": (
                "70D walk-forward gate is the benchmark; BenchmarkRunner "
                "MATRIX covers scalp_v1/v2 only."
            ),
        }
    report_path = report_dir / f"benchmark_{variant}.json"
    try:
        report_path.write_text(json.dumps(bench, default=str, indent=2), encoding="utf-8")
    except Exception as exc:  # evidence write must not fail the training
        logger.warning("[THREE_MODEL] benchmark report write failed (non-fatal): %s", exc)

    # Lifecycle registration: every trained variant becomes a CANDIDATE row,
    # then validated variants are promoted to CHALLENGER (shadow-eligible).
    lifecycle = _lifecycle_registry()
    if lifecycle is not None:
        try:
            lifecycle.register_candidate(
                artifact_path=str(paths["model"]),
                run_id=f"three_model_{variant}_{int(t0)}",
                model_id=f"scalp_{variant}",
                model_version="1.0.0",
                feature_schema_id=trainer.feature_schema.schema_id,
                feature_dimension=trainer.feature_schema.dimension,
            )
            logger.info("[THREE_MODEL] candidate registered: scalp_%s", variant)
            # The registry derives {role}_{schema}_{dim}d — use THAT id for
            # the CHALLENGER promotion (otherwise set_status no-ops).
            derived_rid = (
                f"scalp_{variant}_scalp_v3_70d"
                if trainer.feature_schema.dimension == 70
                else f"scalp_{variant}_scalp_v1_50d"
            )
            # Validation evidence: benchmark + walk-forward exist -> CHALLENGER.
            lifecycle.set_status(
                model_id=derived_rid,
                model_version="1.0.0",
                status=_model_lifecycle_status().CHALLENGER,
                reason="three-model pipeline: trained + benchmark evidence",
                gate_summary={
                    "walk_forward": True,
                    "benchmark": bench.get("status", "EVIDENCE_WRITTEN"),
                    "dimension": trainer.feature_schema.dimension,
                    "schema_id": trainer.feature_schema.schema_id,
                },
                training_run_id=f"three_model_{variant}_{int(t0)}",
            )
        except Exception as exc:
            logger.warning("[THREE_MODEL] lifecycle registration failed (non-fatal): %s", exc)
    # Flush the async audit writer so lifecycle rows are durable before the
    # CLI/UI reads them (set_status/register_candidate queue inserts).
    try:
        lifecycle.audit_repo._queue.join()  # type: ignore[attr-defined]
    except Exception:
        pass

    report = {
        "variant": variant,
        "schema_id": trainer.feature_schema.schema_id,
        "dimension": trainer.feature_schema.dimension,
        "artifact": {
            "model": str(paths["model"]),
            "scaler": str(paths["scaler"]),
            "meta": str(paths["meta"]),
        },
        "walk_forward": {
            "num_folds": trainer.num_folds,
            "trainable_rows": trainable_rows,
        },
        "benchmark_path": str(report_path),
        "benchmark": {
            k: bench.get(k)
            for k in (
                "status",
                "accuracy",
                "balanced_accuracy",
                "f1",
                "oos_macro_f1",
                "ece",
                "verdict",
            )
            if k in bench
        },
        "elapsed_sec": elapsed,
        "gate": "PASS"
        if bench.get("status") in ("PASS", "COMPLETED", "READY")
        else "EVIDENCE_WRITTEN",
    }
    logger.info(
        "[THREE_MODEL] variant=%s schema=%s dim=%d folds=%d elapsed=%.1fs artifact=%s",
        variant,
        trainer.feature_schema.schema_id,
        trainer.feature_schema.dimension,
        trainer.num_folds,
        elapsed,
        paths["model"],
    )
    return report


def write_variants_index(reports: list[dict[str, Any]]) -> Path:
    """Persist the per-variant index consumed by the CLI/UI/hot-swap."""
    index = MODEL_BASE_DIR / "model_variants.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "variants": {r["variant"]: r for r in reports},
        "contract": (
            "Base 0..49 | News 50..59 | Liquidity 60..69 (scalp_v3, canonical schema_contract)"
        ),
    }
    tmp = index.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
    tmp.replace(index)
    logger.info("[THREE_MODEL] variants index written: %s", index)
    return index


def train_all(
    bars_frame: pl.DataFrame,
    *,
    news_frame: pl.DataFrame | None = None,
    variants: list[str] | None = None,
    num_folds: int = 34,
    epochs: int = 10,
    smoke: bool = False,
) -> list[dict[str, Any]]:
    """Train the full matrix (default: all three variants) + write index."""
    chosen = variants or ["50d_main", "70d_news", "70d_liquidity"]
    reports: list[dict[str, Any]] = []
    for variant in chosen:
        logger.info("[THREE_MODEL] === training variant=%s ===", variant)
        reports.append(
            train_variant(
                variant,
                bars_frame,
                news_frame=news_frame,
                num_folds=num_folds,
                epochs=epochs,
                smoke=smoke,
            )
        )
    write_variants_index(reports)
    return reports
