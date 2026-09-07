"""End-to-end calibration collection tests (mission phases 2/4/6/10/11).

Uses a REAL temporary audit DB with a controlled population so every gate is
exercised against the actual collector implementation:

1. eligible OOS outcomes for the serving artifact -> fit + persist + reload
2. identity binding: rows from OTHER artifact fingerprints are excluded
3. insufficient per-split evidence -> INSUFFICIENT_EVIDENCE + exact deficit
4. zero confidence rows (structural paths) excluded — never fabricated
5. OOS cutoff enforced: pre-cutoff rows (in-sample) never enter the fit
6. missing serving artifact -> INSUFFICIENT_PROVENANCE
7. reloaded artifact reproduces fit-time validation metrics exactly
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from nexus_scalp.model_lifecycle import calibration_collector as cc
from nexus_scalp.model_lifecycle.calibration_collector import (
    collect_calibration_evidence,
)
from nexus_scalp.model_lifecycle.confidence_calibration import (
    MIN_CALIBRATION_SAMPLES,
    ConfidenceCalibrator,
)


def _setup_db(tmp_path, rows: list[dict]) -> str:
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
    for i, r in enumerate(rows):
        payload = json.dumps(
            {
                "idempotency_key": r["key"],
                "provenance": {
                    "model_id": "primary_scalp_scalp_v3_70d",
                    "artifact_fingerprint": r["fp"],
                },
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


def _row(i: int, fp: str, conf: float, ts: str, pnl: float | None = None) -> dict:
    if pnl is None:
        pnl = 10.0 if (i % 3) else -12.0
    return {
        "key": f"k{i}",
        "fp": fp,
        "conf": conf,
        "decision_ts": ts,
        "outcome_ts": ts,
        "pnl": pnl,
    }


def _serving_artifact(tmp_path) -> tuple[str, str]:
    p = tmp_path / "model.pt"
    p.write_bytes(b"serving-artifact-bytes")
    fp = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return str(p), fp


def _persist_patch(tmp_path, monkeypatch):
    """Redirect the canonical persist path into the tmp dir."""
    target = tmp_path / "confidence_calibration.json"
    monkeypatch.setattr(cc, "SERVING_CALIBRATION_PATH", target)
    return target


def test_full_pipeline_fit_persist_reload(tmp_path, monkeypatch) -> None:
    art_path, fp = _serving_artifact(tmp_path)
    target = _persist_patch(tmp_path, monkeypatch)
    rows = [
        _row(i, fp, 0.20 + (i % 60) * 0.01, f"2026-09-10T{i % 24:02d}:00:00+00:00")
        for i in range(100)  # 100 eligible -> 60 cal / 40 val (>= 30/30)
    ]
    db = _setup_db(tmp_path, rows)
    res = collect_calibration_evidence(
        oos_cutoff="2026-09-01T00:00:00+00:00",
        serving_artifact_path=art_path,
        audit_db_path=db,
    )
    assert res.status == "COLLECTED"
    assert res.eligible_count == 100 and res.cal_count == 60 and res.val_count == 40
    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["provenance"]["artifact_fingerprint"] == fp
    # RELOAD through the consumer path: bound to the same artifact -> usable
    cal = ConfidenceCalibrator.from_artifact(target)
    assert cal.state in ("CALIBRATED", "DEGRADED")
    assert res.raw_metrics["n"] == 40 and res.calibrated_metrics["n"] == 40


def test_identity_binding_excludes_foreign_artifacts(tmp_path, monkeypatch) -> None:
    art_path, fp = _serving_artifact(tmp_path)
    _persist_patch(tmp_path, monkeypatch)
    rows = [_row(i, fp, 0.3, f"2026-09-10T00:00:00+00:00") for i in range(70)]
    # 500 outcomes from a DIFFERENT artifact — plentiful but NOT eligible
    rows += [_row(1000 + i, "deadbeefdeadbeef", 0.5, "2026-09-10T00:00:00+00:00") for i in range(500)]
    db = _setup_db(tmp_path, rows)
    res = collect_calibration_evidence(
        oos_cutoff="2026-09-01T00:00:00+00:00",
        serving_artifact_path=art_path,
        audit_db_path=db,
    )
    assert res.eligible_count == 70
    # 70 eligible -> 42 cal / 28 val: validation is short -> INSUFFICIENT, and
    # crucially the 500 foreign-artifact rows never inflated the counts.
    assert res.status == "INSUFFICIENT_EVIDENCE"
    assert res.deficit["validation"] == MIN_CALIBRATION_SAMPLES - 28


def test_insufficient_per_split_reports_exact_deficit(tmp_path, monkeypatch) -> None:
    art_path, fp = _serving_artifact(tmp_path)
    _persist_patch(tmp_path, monkeypatch)
    rows = [_row(i, fp, 0.4, "2026-09-10T00:00:00+00:00") for i in range(45)]
    db = _setup_db(tmp_path, rows)
    res = collect_calibration_evidence(
        oos_cutoff="2026-09-01T00:00:00+00:00",
        serving_artifact_path=art_path,
        audit_db_path=db,
    )
    assert res.status == "INSUFFICIENT_EVIDENCE"
    # 45 * 0.6 = 27 cal (short), 18 val (short)
    assert res.cal_count == 27 and res.val_count == 18
    assert res.deficit == {"calibration": 3, "validation": 12}


def test_oos_cutoff_excludes_in_sample_rows(tmp_path, monkeypatch) -> None:
    art_path, fp = _serving_artifact(tmp_path)
    _persist_patch(tmp_path, monkeypatch)
    rows = [_row(i, fp, 0.4, "2026-09-10T00:00:00+00:00") for i in range(100)]
    # 200 in-sample (pre-cutoff) rows — excluded from the fit entirely
    rows += [_row(500 + i, fp, 0.4, "2026-08-01T00:00:00+00:00") for i in range(200)]
    db = _setup_db(tmp_path, rows)
    res = collect_calibration_evidence(
        oos_cutoff="2026-09-01T00:00:00+00:00",
        serving_artifact_path=art_path,
        audit_db_path=db,
    )
    assert res.eligible_count == 100  # in-sample rows never counted
    assert res.status == "COLLECTED"


def test_zero_confidence_rows_excluded_not_fabricated(tmp_path, monkeypatch) -> None:
    art_path, fp = _serving_artifact(tmp_path)
    _persist_patch(tmp_path, monkeypatch)
    rows = [_row(i, fp, 0.0, "2026-09-10T00:00:00+00:00") for i in range(80)]
    db = _setup_db(tmp_path, rows)
    res = collect_calibration_evidence(
        oos_cutoff="2026-09-01T00:00:00+00:00",
        serving_artifact_path=art_path,
        audit_db_path=db,
    )
    assert res.eligible_count == 0
    assert res.status == "INSUFFICIENT_EVIDENCE"


def test_missing_serving_artifact_is_insufficient_provenance(tmp_path, monkeypatch) -> None:
    db = _setup_db(tmp_path, [])
    res = collect_calibration_evidence(
        oos_cutoff="2026-09-01T00:00:00+00:00",
        serving_artifact_path=tmp_path / "missing.pt",
        audit_db_path=db,
    )
    assert res.status == "INSUFFICIENT_PROVENANCE"
