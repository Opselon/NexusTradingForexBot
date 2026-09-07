"""Calibration evidence collector (mission phase 10 — data collection loop).

ONE canonical, deterministic path from the experience ledger to a calibration
artifact. Replaces manual/scratch fitting: an operator (or the learning
worker) runs `collect` to assemble the eligible OOS sample, verify the
per-split evidence contract, and — only when the contract passes — fit,
validate on the disjoint slice, persist, and report.

Eligibility of one experience→outcome row (all REQUIRED, no exceptions):

  E1 model identity   provenance.model_id + provenance.artifact_fingerprint
                      == the SERVING artifact fingerprint (sha256[:16] of the
                      configured model file). A calibration calibrates ONE
                      exact artifact.
  E2 real confidence  signal_confidence >= 0.05 recorded at decision time
                      (structural paths emit 0.0 — they carry no model
                      confidence and are excluded, not fabricated).
  E3 resolved outcome is_executed AND is_closed AND realized_pnl_usd != 0
                      (unambiguous win/loss; zero-PnL rows are unresolvable).
  E4 causality        outcome_timestamp >= decision_timestamp (schema-enforced;
                      re-verified here).
  E5 no duplicates    outcomes are unique per idempotency_key (DB-enforced;
                      re-verified here).
  E6 holdout status   rows must postdate the artifact's training cutoff
                      (oos_cutoff, ISO). This is the OOS declaration —
                      rows observed while the model was SERVING, after its
                      training data window ended. The caller supplies the
                      cutoff from the training metadata; when unavailable,
                      collection reports INSUFFICIENT_PROVENANCE instead of
                      guessing.

Splitting: strictly chronological — oldest `cal_fraction` of eligible rows
fit, newest disjoint remainder validates. No shuffling (temporal data).

The per-split MINIMUM (30/30) is NOT relaxed here. If either side is short,
the collector returns status INSUFFICIENT_EVIDENCE with the exact deficit —
that is a SUCCESSFUL safe outcome, not a failure.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus_scalp.model_lifecycle.calibration_identity import (
    SERVING_ARTIFACT_PATH,
    SERVING_CALIBRATION_PATH,
    resolve_serving_fingerprint,
)
from nexus_scalp.model_lifecycle.confidence_calibration import (
    MIN_CALIBRATION_SAMPLES,
    CalibrationProvenance,
    ConfidenceCalibrator,
    build_calibration_artifact,
    evaluate_calibration,
    persist_calibration_artifact,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.calibration_collector")

AUDIT_DB_PATH = "artifacts/audit.db"
#: Confidence floor: structural paths (predictive-limit / tick-sweep) emit
#: confidence 0.0 — those rows carry NO model confidence and are excluded.
MIN_CONFIDENCE: float = 0.05


@dataclass
class CollectionResult:
    """Bounded, honest evidence summary (phase 13: nothing sensitive)."""

    status: str  # COLLECTED | INSUFFICIENT_EVIDENCE | INSUFFICIENT_PROVENANCE
    serving_fingerprint: str | None
    oos_cutoff: str
    eligible_count: int = 0
    cal_count: int = 0
    val_count: int = 0
    cal_wins: int = 0
    val_wins: int = 0
    date_range: list[str] = field(default_factory=list)
    deficit: dict[str, int] = field(default_factory=dict)
    raw_metrics: dict[str, Any] = field(default_factory=dict)
    calibrated_metrics: dict[str, Any] = field(default_factory=dict)
    artifact_path: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "serving_fingerprint": self.serving_fingerprint,
            "oos_cutoff": self.oos_cutoff,
            "eligible_count": self.eligible_count,
            "cal_count": self.cal_count,
            "val_count": self.val_count,
            "cal_wins": self.cal_wins,
            "val_wins": self.val_wins,
            "date_range": self.date_range,
            "deficit": self.deficit,
            "raw_metrics": self.raw_metrics,
            "calibrated_metrics": self.calibrated_metrics,
            "artifact_path": self.artifact_path,
            "notes": self.notes,
        }


def _outcomes(cur: sqlite3.Cursor, fingerprint: str) -> list[dict[str, Any]]:
    q = """
    SELECT e.payload, e.signal_confidence, o.realized_pnl_usd,
           e.decision_ts, o.outcome_ts
    FROM (
      SELECT idempotency_key, payload, signal_confidence,
             decision_timestamp AS decision_ts
      FROM audit_experiences
      WHERE model_id LIKE 'primary_scalp%'
    ) e
    JOIN (
      SELECT idempotency_key, realized_pnl_usd,
             outcome_timestamp AS outcome_ts, is_executed, is_closed
      FROM audit_experience_outcomes
      WHERE is_executed = 1 AND is_closed = 1
        AND realized_pnl_usd IS NOT NULL AND realized_pnl_usd != 0.0
    ) o ON o.idempotency_key = e.idempotency_key
    ORDER BY o.outcome_ts
    """
    rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for payload, conf, pnl, dec_ts, out_ts in cur.execute(q).fetchall():
        # E5 duplicate defense at read time (defense in depth)
        rec = json.loads(payload)
        key = str(rec.get("idempotency_key", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        # E1 exact artifact identity from the row's own provenance
        prov = rec.get("provenance", {})
        if str(prov.get("artifact_fingerprint", "")) != fingerprint:
            continue
        # E2 real model confidence
        c = float(conf or 0.0)
        if c < MIN_CONFIDENCE:
            continue
        # E4 causality (schema normally enforces; verify without trusting)
        try:
            if datetime.fromisoformat(str(out_ts)) < datetime.fromisoformat(str(dec_ts)):
                continue
        except (TypeError, ValueError):
            continue
        rows.append({"ts": str(out_ts), "decision_ts": str(dec_ts), "conf": c, "pnl": float(pnl)})
    return rows


def collect_calibration_evidence(
    *,
    oos_cutoff: str,
    serving_artifact_path: str | Path = SERVING_ARTIFACT_PATH,
    audit_db_path: str = AUDIT_DB_PATH,
    cal_fraction: float = 0.6,
) -> CollectionResult:
    """Assembles the eligible OOS sample for the SERVING artifact.

    Returns INSUFFICIENT_PROVENANCE when the serving artifact file is absent
    (no identity to bind), INSUFFICIENT_EVIDENCE with the exact deficit when
    fewer eligible rows exist than the contract requires — and a COLLECTED
    (fit+validated+persisted) result only when every gate passes.
    """
    fp = resolve_serving_fingerprint(serving_artifact_path)
    if not fp:
        return CollectionResult(
            status="INSUFFICIENT_PROVENANCE",
            serving_fingerprint=None,
            oos_cutoff=oos_cutoff,
            notes=[f"serving artifact missing/unreadable: {serving_artifact_path}"],
        )
    cutoff_dt = datetime.fromisoformat(oos_cutoff)
    con = sqlite3.connect(audit_db_path)
    try:
        rows = _outcomes(con.cursor(), fp)
    finally:
        con.close()
    # E6: holdout = observed while serving, AFTER the training data window
    eligible = [r for r in rows if datetime.fromisoformat(r["ts"]) >= cutoff_dt]
    result = CollectionResult(
        status="INSUFFICIENT_EVIDENCE",
        serving_fingerprint=fp,
        oos_cutoff=oos_cutoff,
        eligible_count=len(eligible),
        date_range=[eligible[0]["ts"][:10], eligible[-1]["ts"][:10]] if eligible else [],
    )
    if eligible:
        result.notes.append(
            f"{len(rows)} resolved rows for fingerprint {fp}; "
            f"{len(eligible)} at/after OOS cutoff {oos_cutoff}"
        )
    else:
        result.notes.append(
            f"0 resolved outcomes for serving fingerprint {fp} "
            f"(other artifacts' outcomes are NOT eligible — identity binding)"
        )
    split = max(1, int(len(eligible) * cal_fraction)) if eligible else 0
    cal, val = eligible[:split], eligible[split:]
    result.cal_count, result.val_count = len(cal), len(val)
    result.cal_wins = sum(1 for r in cal if r["pnl"] > 0)
    result.val_wins = sum(1 for r in val if r["pnl"] > 0)
    result.deficit = {
        "calibration": max(0, MIN_CALIBRATION_SAMPLES - len(cal)),
        "validation": max(0, MIN_CALIBRATION_SAMPLES - len(val)),
    }
    if len(cal) < MIN_CALIBRATION_SAMPLES or len(val) < MIN_CALIBRATION_SAMPLES:
        result.notes.append(
            f"per-split deficit: need {MIN_CALIBRATION_SAMPLES}/"
            f"{MIN_CALIBRATION_SAMPLES}, have {len(cal)}/{len(val)}"
        )
        return result

    # FIT (existing deterministic Platt implementation — unchanged)
    art = build_calibration_artifact(
        model_version=f"serving_{fp}",
        feature_schema_version="scalp_v3",
        cal_confidences=[r["conf"] for r in cal],
        cal_outcomes=[1 if r["pnl"] > 0 else 0 for r in cal],
        val_confidences=[r["conf"] for r in val],
        val_outcomes=[1 if r["pnl"] > 0 else 0 for r in val],
        calibration_dataset_id=f"experience_ledger_oos_cal_{fp}",
        validation_dataset_id=f"experience_ledger_oos_val_{fp}",
        cal_start=cal[0]["ts"],
        cal_end=cal[-1]["ts"],
        val_start=val[0]["ts"],
        val_end=val[-1]["ts"],
        artifact_fingerprint=fp,
        calibration_is_oos=True,
        validation_is_oos=True,
    )
    result.raw_metrics = art["validation_metrics"].get("raw", {})
    result.calibrated_metrics = art["validation_metrics"].get("calibrated", {})
    ok = persist_calibration_artifact(
        SERVING_CALIBRATION_PATH,
        art["params"],
        CalibrationProvenance.from_dict(art["provenance"]),
        art["validation_metrics"],
    )
    if not ok:
        result.notes.append("artifact persist FAILED — system stays uncalibrated")
        return result
    # RELOAD + VERIFY (phase 6): the persisted artifact must reproduce the
    # same validation metrics through the exact serving path consumers use.
    reloaded = ConfidenceCalibrator.from_artifact(SERVING_CALIBRATION_PATH)
    if reloaded.state == "NOT_CALIBRATED":
        result.notes.append("reloaded artifact NOT_CALIBRATED — persist schema drift")
        return result
    val_scores = [reloaded.calibrate(r["conf"])[0] for r in val]
    reloaded_metrics = evaluate_calibration(val_scores, [1 if r["pnl"] > 0 else 0 for r in val])
    if abs(float(reloaded_metrics["ece"]) - float(result.calibrated_metrics["ece"])) > 1e-6:
        result.notes.append("reloaded metrics diverge from fit-time metrics — REJECTED")
        return result
    result.status = "COLLECTED"
    result.artifact_path = str(SERVING_CALIBRATION_PATH)
    result.notes.append(
        f"artifact persisted+reloaded; calibrated ECE "
        f"{result.calibrated_metrics.get('ece')} vs raw "
        f"{result.raw_metrics.get('ece')} on the disjoint validation slice"
    )
    return result
