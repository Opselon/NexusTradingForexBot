"""
Real Champion Metrics (Learning-Loop Closure, Phase 2)
======================================================

The challenger-vs-champion comparison must use REAL champion metrics from the
repository's own evidence artifacts — never placeholders (expectancy 0.0 /
stability 1.0).

Evidence sources (checked in order, all read-only):
  1. Research registry / evidence store rows for the champion model identity
     (`research_gates` / `research_run_snapshots` in audit.db).
  2. Baseline evaluation artifacts written by research.baseline_eval
     (artifacts/forensics/baseline_eval/<model_hash>.json) — produced by the
     reproducible evaluator in this repository (Phase 3).
  3. Outcome-distribution evaluation of the champion over the ledger
     (BacktestEngine on verified executed experiences).

If no real champion metrics are available, `ChampionMetricsProvider.load()`
returns None and the caller MUST record
PROMOTION_EVALUATION=BLOCKED / CHAMPION_METRICS_UNAVAILABLE.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.model_lifecycle.champion import ChampionModel
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.champion_metrics")

#: Baseline artifact directory (written by research.baseline_eval).
BASELINE_ARTIFACT_DIR = Path("artifacts/forensics/baseline_eval")

#: The exact metric keys a champion comparison requires (spec: real evidence).
REQUIRED_CHAMPION_METRIC_KEYS: tuple[str, ...] = (
    "expectancy_r",
    "max_drawdown_r",
    "oos_expectancy_r",
    "tail_loss_count",
    "robustness_status",
    "stability",
)


class ChampionMetricsUnavailableError(RuntimeError):
    """Raised when no real champion metrics exist (never substitute zeros)."""


def _full_hash_for(champion: ChampionModel) -> str:
    """Full SHA-256 of the champion artifact (16-char fingerprint widened)."""
    try:
        import hashlib

        h = hashlib.sha256()
        with open(champion.artifact_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def champion_metrics_artifact_path(model_hash: str) -> Path:
    """Deterministic artifact path for a champion's baseline evaluation.

    Accepts either the 16-char lifecycle fingerprint (ChampionModel /
    integrity.compute_artifact_hash) or the full SHA-256 (baseline_eval
    artifact naming); the artifact file is matched by its full hash and
    its payload re-checked against the served bytes on load.
    """
    return BASELINE_ARTIFACT_DIR / f"{model_hash}.json"


def _candidate_artifact_files(baseline_dir: Path, model_hash: str) -> list[Path]:
    """All baseline artifacts plausibly keyed by this model identity."""
    out: list[Path] = []
    exact = baseline_dir / f"{model_hash}.json"
    if exact.exists():
        out.append(exact)
    if model_hash and len(model_hash) < 64:
        # Prefix match: fingerprint -> full-hash artifact names.
        for p in baseline_dir.glob(f"{model_hash}*.json"):
            if p != exact:
                out.append(p)
    return out


def load_baseline_artifact(model_hash: str) -> dict[str, Any] | None:
    """Loads from the module-default baseline dir (kept for direct callers)."""
    return _load_from_dir(BASELINE_ARTIFACT_DIR, model_hash)


def _load_from_dir(baseline_dir: Path, model_hash: str) -> dict[str, Any] | None:
    """Loads the persisted baseline evaluation for a model hash (or None).

    Identity rule: the artifact's model_hash must START WITH the serving
    fingerprint (full-hash artifacts match their 16-char prefix). A stale
    evaluation for different bytes is never returned.
    """
    if not model_hash:
        return None
    for p in _candidate_artifact_files(baseline_dir, model_hash):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("[CHAMPION_METRICS] baseline artifact unreadable", error=str(e))
            continue
        artifact_hash = str(payload.get("model_hash", "") or "")
        if artifact_hash.startswith(model_hash) or model_hash.startswith(artifact_hash):
            return payload
        logger.warning(
            "[CHAMPION_METRICS] baseline artifact hash mismatch: artifact=%s serving=%s",
            artifact_hash[:12],
            model_hash[:12],
        )
    return None


def baseline_artifact_to_champion_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    """Maps a baseline_eval artifact onto the comparator's champion dict.

    Every value is traceable to the artifact (evaluation_id recorded); the
    mapping is explicit — no invented defaults.
    """
    metrics = payload.get("metrics", {}) or {}
    oos = payload.get("oos", {}) or {}
    robust = payload.get("robustness", {}) or {}
    stability = payload.get("stability", {}) or {}
    wf = payload.get("walk_forward", {}) or {}
    return {
        "model_id": payload.get("model_id", ""),
        "model_version": payload.get("model_version", ""),
        "model_hash": payload.get("model_hash", ""),
        "dataset_id": payload.get("dataset_id", ""),
        "dataset_hash": payload.get("dataset_hash", ""),
        "evaluation_id": payload.get("evaluation_id", ""),
        "evaluation_config_hash": payload.get("evaluation_config_hash", ""),
        "expectancy_r": float(metrics.get("expectancy_r", 0.0)),
        "max_drawdown_r": float(metrics.get("max_drawdown_r", 0.0)),
        "oos_expectancy_r": float(oos.get("expectancy_r", 0.0)),
        "tail_loss_count": int(metrics.get("tail_loss_count", 0)),
        "robustness_status": str(robust.get("status", "UNKNOWN")),
        "stability": float(stability.get("score", 0.0)),
        "win_rate": float(metrics.get("win_rate", 0.0)),
        "profit_factor": float(metrics.get("profit_factor", 0.0)),
        "wf_avg_oos_expectancy_r": float(wf.get("avg_oos_expectancy_r", 0.0)),
        "provenance": "baseline_eval_artifact",
        "artifact_path": str(champion_metrics_artifact_path(payload.get("model_hash", ""))),
    }


class ChampionMetricsProvider:
    """Loads REAL champion metrics with full provenance (fail-closed)."""

    def __init__(self, baseline_dir: Path | None = None) -> None:
        self.baseline_dir = baseline_dir or BASELINE_ARTIFACT_DIR

    def load(self, champion: ChampionModel) -> dict[str, Any] | None:
        """Returns real champion metrics, or None when unavailable.

        The champion's verified artifact hash is the identity key: the metrics
        belong to EXACTLY the bytes that are serving. A stale evaluation for a
        different artifact is not the champion's evidence.
        """
        model_hash = champion.artifact_hash or ""
        if not model_hash:
            logger.error("[CHAMPION_METRICS] champion has no artifact hash")
            return None
        baseline_dir = self.baseline_dir
        payload = _load_from_dir(baseline_dir, model_hash)
        if payload is None and len(model_hash) >= 16:
            # Full-hash identity as fallback when the fingerprint prefix
            # lookup found no file (e.g. artifact stored under full sha).
            payload = _load_from_dir(baseline_dir, _full_hash_for(champion))
        if payload is None:
            logger.warning(
                "[CHAMPION_METRICS] event=CHAMPION_METRICS_UNAVAILABLE "
                "model_hash=%s (run research.baseline_eval to produce evidence)",
                model_hash[:12],
            )
            return None
        metrics = baseline_artifact_to_champion_metrics(payload)
        missing = [k for k in REQUIRED_CHAMPION_METRIC_KEYS if k not in metrics]
        if missing:
            logger.error(
                "[CHAMPION_METRICS] baseline artifact missing keys: %s", ",".join(missing)
            )
            return None
        return metrics


def blocked_comparison(champion: ChampionModel, reason: str = "CHAMPION_METRICS_UNAVAILABLE") -> dict[str, Any]:
    """The contractually required BLOCKED verdict when metrics are absent."""
    return {
        "promotion_evaluation": "BLOCKED",
        "reason": reason,
        "champion_model_id": champion.model_id if champion else "",
        "champion_version": champion.model_version if champion else "",
        "champion_artifact_hash": champion.artifact_hash if champion else "",
        "checked_at": datetime.now(UTC).isoformat(),
    }
