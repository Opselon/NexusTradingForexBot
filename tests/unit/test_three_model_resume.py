"""train_all resume behavior tests (first-run GAP 3).

Pins: with resume=True, a variant with prior PASS evidence (benchmark report
with gate PASS/READY/COMPLETED + artifact on disk) is SKIPPED (prior report
marked skipped=True); missing evidence or a non-PASS gate means it retrains.
resume=False (default) keeps the historical always-retrain behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from nexus_scalp.model_generation.three_model import _prior_pass, train_all


def _plant_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Plant a PASS benchmark report + a fake artifact for 50d_main."""
    import nexus_scalp.model_generation.three_model as tm

    report_dir = tmp_path / "artifacts" / "model_generation" / "three_model"
    report_dir.mkdir(parents=True)
    (report_dir / "benchmark_50d_main.json").write_text(
        json.dumps({"variant": "50d_main", "gate": "PASS", "status": "PASS"}),
        encoding="utf-8",
    )
    artifact = tmp_path / "art" / "50d_main" / "model.pt"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"fake")
    monkeypatch.chdir(tmp_path)
    # redirect the module-level canonical artifact path
    monkeypatch.setattr(tm, "MODEL_BASE_DIR", tmp_path / "art")


def test_prior_pass_detected_with_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _plant_pass(tmp_path, monkeypatch)
    prior = _prior_pass("50d_main")
    assert prior is not None
    assert prior["skipped"] is True


def test_no_artifact_means_retrain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report_dir = tmp_path / "artifacts" / "model_generation" / "three_model"
    report_dir.mkdir(parents=True)
    (report_dir / "benchmark_50d_main.json").write_text(
        json.dumps({"variant": "50d_main", "gate": "PASS"}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    assert _prior_pass("50d_main") is None


def test_non_pass_gate_means_retrain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _plant_pass(tmp_path, monkeypatch)
    (
        tmp_path / "artifacts" / "model_generation" / "three_model" / "benchmark_50d_main.json"
    ).write_text(json.dumps({"variant": "50d_main", "gate": "EVIDENCE_WRITTEN"}), encoding="utf-8")
    assert _prior_pass("50d_main") is None


def test_resume_false_never_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default resume=False preserves the historical always-retrain behavior."""
    _plant_pass(tmp_path, monkeypatch)
    calls: list[str] = []

    def fake_train_variant(variant, bars, **kwargs):
        calls.append(variant)
        return {"variant": variant, "gate": "PASS"}

    import nexus_scalp.model_generation.three_model as tm

    monkeypatch.setattr(tm, "train_variant", fake_train_variant)
    df = pl.DataFrame({"time": [1], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]})
    tm.train_all(df, variants=["50d_main"], resume=False)
    assert calls == ["50d_main"]

    # resume=True skips the PASS variant entirely
    calls.clear()
    tm.train_all(df, variants=["50d_main"], resume=True)
    assert calls == []
