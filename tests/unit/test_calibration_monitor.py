"""Calibration evidence monitor tests (collection-monitoring mission).

Verifies the /api/operator/calibration snapshot logic WITHOUT a live server:

* serving fingerprint + artifact mtime surfaced (fingerprint-stability
  evidence);
* excluded-by-reason accounting: foreign fingerprint, zero-confidence
  structural rows, zero-PnL rows, duplicates, pre-cutoff rows are EXCLUDED
  (never transformed into evidence);
* chronological split + exact deficit reporting;
* artifact ABSENT => collector INSUFFICIENT_EVIDENCE => multiplier 1.0;
* a foreign-fingerprint artifact on disk does NOT change the multiplier
  (identity binding holds through the monitor path).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from nexus_scalp.model_lifecycle import calibration_identity as ci
from nexus_scalp.web import calibration_monitor as cm
from nexus_scalp.web.calibration_monitor import calibration_monitor_snapshot


def _sha16_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _db(tmp_path, rows: list[dict]) -> str:
    db = tmp_path / "audit.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE audit_experiences (idempotency_key TEXT PRIMARY KEY, "
        "model_id TEXT, payload TEXT, signal_confidence REAL, decision_timestamp TEXT)"
    )
    con.execute(
        "CREATE TABLE audit_experience_outcomes (idempotency_key TEXT PRIMARY KEY, "
        "realized_pnl_usd REAL, outcome_timestamp TEXT, is_executed INTEGER, is_closed INTEGER)"
    )
    for r in rows:
        payload = json.dumps(
            {
                "idempotency_key": r["key"],
                "provenance": {"model_id": "primary_scalp_scalp_v3_70d",
                               "artifact_fingerprint": r["fp"]},
            }
        )
        con.execute(
            "INSERT INTO audit_experiences VALUES (?,?,?,?,?)",
            (r["key"], "primary_scalp_scalp_v3_70d", payload, r["conf"], r["decision_ts"]),
        )
        con.execute(
            "INSERT INTO audit_experience_outcomes VALUES (?,?,?,?,?)",
            (r["key"], r["pnl"], r["outcome_ts"], 1, 1),
        )
    con.commit()
    con.close()
    return str(db)


def _row(i: int, fp: str, conf: float, ts: str, pnl: float = 10.0, key: str = "") -> dict:
    return {
        "key": key or f"k{i}",
        "fp": fp,
        "conf": conf,
        "decision_ts": ts,
        "outcome_ts": ts,
        "pnl": pnl,
    }


def _serving_artifact(tmp_path) -> tuple[str, str]:
    p = tmp_path / "model.pt"
    p.write_bytes(b"serving-artifact-bytes")
    return str(p), _sha16_bytes(b"serving-artifact-bytes")


def test_monitor_reports_excluded_reasons_and_deficit(tmp_path, monkeypatch) -> None:
    art, fp = _serving_artifact(tmp_path)
    monkeypatch.setattr(cm, "SERVING_ARTIFACT_PATH", art)
    rows = [
        # 60 eligible rows for the CURRENT fingerprint
        *[_row(i, fp, 0.2 + (i % 40) * 0.01, "2026-09-10T00:00:00+00:00") for i in range(60)],
        # exclusions:
        _row(100, "deadbeefdeadbeef", 0.5, "2026-09-10T00:00:00+00:00"),   # foreign fp
        _row(101, fp, 0.0, "2026-09-10T00:00:00+00:00"),                   # zero conf
        _row(102, fp, 0.5, "2026-09-10T00:00:00+00:00", pnl=0.0),          # zero pnl
        _row(103, fp, 0.5, "2026-08-01T00:00:00+00:00"),                   # pre-cutoff
    ]
    db = _db(tmp_path, rows)
    snap = calibration_monitor_snapshot(
        oos_cutoff="2026-09-06T00:00:00+00:00", audit_db_path=db
    )
    assert snap["serving_fingerprint"] == fp
    assert snap["artifact_status"] == "ABSENT"
    assert snap["total_eligible"] == 60
    assert snap["excluded"]["foreign_fingerprint"] == 1
    assert snap["excluded"]["zero_confidence_structural"] == 1
    assert snap["excluded"]["zero_pnl"] == 1
    assert snap["excluded"]["pre_cutoff"] == 1
    # chronological split: 36 cal / 24 val -> validation short
    assert snap["calibration_split"] == 36
    assert snap["validation_split"] == 24
    assert snap["deficit"] == {"calibration": 0, "validation": 6}
    assert snap["collector_status"] == "INSUFFICIENT_EVIDENCE"
    # no artifact => identity-bound path must report NOT_CALIBRATED and 1.0
    assert snap["calibration_status"] == "NOT_CALIBRATED"
    assert snap["risk_multiplier"] == 1.0


def test_monitor_fingerprint_stability_field_changes_with_artifact(tmp_path, monkeypatch) -> None:
    art, fp = _serving_artifact(tmp_path)
    monkeypatch.setattr(cm, "SERVING_ARTIFACT_PATH", art)
    s1 = calibration_monitor_snapshot(oos_cutoff="2026-09-01T00:00:00+00:00",
                                      audit_db_path=str(tmp_path / "none.db"))
    assert s1["serving_fingerprint"] == fp
    assert s1["serving_artifact_mtime"] is not None
    # artifact bytes change => fingerprint changes (the stability evidence)
    p = tmp_path / "model.pt"
    p.write_bytes(b"CHANGED")
    s2 = calibration_monitor_snapshot(oos_cutoff="2026-09-01T00:00:00+00:00",
                                      audit_db_path=str(tmp_path / "none.db"))
    assert s2["serving_fingerprint"] != fp


def test_monitor_multiplier_stays_flat_with_foreign_artifact_present(tmp_path, monkeypatch) -> None:
    """A foreign calibration file on disk must NOT move the multiplier (the
    monitor reads through load_bound_calibrator, the RiskEngine path)."""
    art, fp = _serving_artifact(tmp_path)
    foreign_fp = _sha16_bytes(b"some-other-model")
    cal_path = tmp_path / "confidence_calibration.json"
    from nexus_scalp.model_lifecycle.confidence_calibration import (
        CalibrationProvenance,
        persist_calibration_artifact,
    )

    prov = CalibrationProvenance(
        model_version="foreign",
        calibration_dataset_id="ds",
        artifact_fingerprint=foreign_fp,
        calibration_period_start="s", calibration_period_end="e",
        validation_dataset_id="ds2",
        validation_period_start="s", validation_period_end="e",
        method="platt_logistic", created_at="now",
        feature_schema_version="scalp_v3",
        sample_count=60, validation_sample_count=40,
    )
    assert persist_calibration_artifact(cal_path, {"a": 3.0, "b": -1.0}, prov,
                                        {"ece": 0.05, "brier": 0.2})
    monkeypatch.setattr(cm, "SERVING_ARTIFACT_PATH", art)
    monkeypatch.setattr(cm, "SERVING_CALIBRATION_PATH", cal_path)
    snap = calibration_monitor_snapshot(oos_cutoff="2026-09-01T00:00:00+00:00",
                                        audit_db_path=str(tmp_path / "none.db"))
    assert snap["artifact_status"] == "PRESENT"
    assert snap["matches_serving"] is False
    assert snap["calibration_status"] == "NOT_CALIBRATED"
    assert snap["risk_multiplier"] == 1.0
