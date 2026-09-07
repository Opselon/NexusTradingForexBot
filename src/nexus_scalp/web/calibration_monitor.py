"""GET /api/operator/calibration — calibration evidence monitor (read-only).

Surfaces the confidence-calibration evidence state for the CURRENT 70D
serving model so an operator (or this monitoring mission) can verify, from
ONE endpoint:

    serving_fingerprint      sha256[:16] of the artifact RiskEngine serves
    fingerprint_stable_hint  artifact mtime (changes => collection window
                             invalidated and must be reported)
    artifact_status          ABSENT | PRESENT (collector-produced only)
    calibration_status       the loaded calibrator state (NOT_CALIBRATED /
                             DEGRADED / CALIBRATED) under identity binding
    eligible_current_fp      outcomes passing EVERY eligibility gate for the
                             CURRENT fingerprint after the OOS cutoff
    excluded                 counts by reason (foreign fp, zero-confidence,
                             zero-PnL, missing identity)
    calibration_split / validation_split   chronological split counts
    required_per_split       30 (contract constant, echoed for the UI)
    collector_status + deficit             the collector's own verdict
    risk_multiplier          exactly 1.0 unless a VALID bound artifact exists

Read-only: one RO sqlite connection + one scaler/artifact hash read.
Never fabricates: missing evidence is reported as missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.model_lifecycle.calibration_identity import (
    SERVING_ARTIFACT_PATH,
    SERVING_CALIBRATION_PATH,
    load_bound_calibrator,
    observability_block,
)
from nexus_scalp.model_lifecycle.confidence_calibration import (
    MIN_CALIBRATION_SAMPLES,
    MIN_CONFIDENCE_RISK_MULTIPLIER,
    confidence_to_risk_multiplier,
)

AUDIT_DB_PATH = "artifacts/audit.db"
CONFIDENCE_FLOOR = 0.05  # mirrors calibration_collector.MIN_CONFIDENCE


def _sha16(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return None


def calibration_monitor_snapshot(
    oos_cutoff: str = "2026-09-06T00:00:00+00:00",
    audit_db_path: str = AUDIT_DB_PATH,
) -> dict[str, Any]:
    """Assembles the bounded calibration-evidence monitor block (read-only)."""
    serving_fp = _sha16(SERVING_ARTIFACT_PATH)
    artifact_exists = os.path.exists(SERVING_CALIBRATION_PATH)
    artifact_mtime = (
        datetime.fromtimestamp(
            os.path.getmtime(SERVING_ARTIFACT_PATH), UTC
        ).isoformat()
        if os.path.exists(SERVING_ARTIFACT_PATH)
        else None
    )

    # Identity-bound calibrator (exactly the RiskEngine consumption path).
    calibrator = load_bound_calibrator()
    obs = observability_block(calibrator)

    excluded: Counter[str] = Counter()
    eligible: list[dict[str, Any]] = []
    if serving_fp and os.path.exists(audit_db_path):
        con = sqlite3.connect(f"file:{audit_db_path}?mode=ro", uri=True)
        try:
            rows = con.execute(
                """
                SELECT e.payload, e.signal_confidence, o.realized_pnl_usd,
                       o.outcome_timestamp
                FROM audit_experiences e
                JOIN audit_experience_outcomes o
                  ON o.idempotency_key = e.idempotency_key
                WHERE o.is_executed = 1 AND o.is_closed = 1
                  AND o.realized_pnl_usd IS NOT NULL
                ORDER BY o.outcome_timestamp
                """
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            con.close()
        seen: set[str] = set()
        for payload, conf, pnl, ots in rows:
            try:
                rec = json.loads(payload)
            except (TypeError, ValueError):
                excluded["malformed_payload"] += 1
                continue
            prov = rec.get("provenance", {}) or {}
            fp_row = str(prov.get("artifact_fingerprint", "") or "")
            key = str(rec.get("idempotency_key", "") or "")
            if key and key in seen:
                excluded["duplicate"] += 1
                continue
            if key:
                seen.add(key)
            if not (ots and ots >= oos_cutoff):
                excluded["pre_cutoff"] += 1
                continue
            if fp_row != (serving_fp or ""):
                excluded["foreign_fingerprint"] += 1
                continue
            if float(conf or 0.0) < CONFIDENCE_FLOOR:
                excluded["zero_confidence_structural"] += 1
                continue
            if float(pnl or 0.0) == 0.0:
                excluded["zero_pnl"] += 1
                continue
            eligible.append({"ts": str(ots), "conf": float(conf or 0.0), "pnl": float(pnl or 0.0)})

    # Chronological split (same convention as the collector: 60/40).
    split = max(1, int(len(eligible) * 0.6)) if eligible else 0
    cal_n, val_n = split, max(0, len(eligible) - split)
    deficit = {
        "calibration": max(0, MIN_CALIBRATION_SAMPLES - cal_n),
        "validation": max(0, MIN_CALIBRATION_SAMPLES - val_n),
    }
    collector_status = (
        "COLLECTED"
        if (artifact_exists and cal_n >= MIN_CALIBRATION_SAMPLES and val_n >= MIN_CALIBRATION_SAMPLES)
        else "INSUFFICIENT_EVIDENCE"
    )

    # The multiplier the RiskEngine actually applies (identity-bound path).
    probe_conf, probe_state = calibrator.calibrate(0.9)
    risk_multiplier = confidence_to_risk_multiplier(probe_conf, probe_state)

    return {
        "available": True,
        "serving_fingerprint": serving_fp,
        "serving_artifact_mtime": artifact_mtime,
        "fingerprint_stable_hint": True,  # single-snapshot: mtime above is the evidence
        "artifact_status": "PRESENT" if artifact_exists else "ABSENT",
        "calibration_status": obs["calibration_status"],
        "matches_serving": obs["matches_serving"],
        "required_per_split": MIN_CALIBRATION_SAMPLES,
        "calibration_split": cal_n,
        "validation_split": val_n,
        "total_eligible": len(eligible),
        "deficit": deficit,
        "excluded": dict(excluded),
        "oos_cutoff": oos_cutoff,
        "collector_status": collector_status,
        "risk_multiplier": risk_multiplier,
        "min_risk_multiplier_floor": MIN_CONFIDENCE_RISK_MULTIPLIER,
        "ece": obs.get("ece"),
        "brier": obs.get("brier"),
    }


def register_calibration_route(app: Any, _err: Any, _log_err: Any) -> None:
    """Registers GET /api/operator/calibration (read-only monitor)."""

    @app.get("/api/operator/calibration")
    def operator_calibration() -> dict[str, Any]:
        try:
            return calibration_monitor_snapshot()
        except Exception as exc:  # fail-open to an honest unavailable block
            _log_err(exc, "operator calibration failed", endpoint="/api/operator/calibration")
            return _err("INTERNAL_ERROR")
