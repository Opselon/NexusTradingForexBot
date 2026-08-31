"""BUG-164 regression: model-validate ghost dataset crashed with a raw traceback.

`ArtifactStore.read_dataset` returns None for an absent artifact (its
documented convention), so `model-validate`'s `except` never fired and
`frame["label"]` surfaced a raw "'NoneType' object is not subscriptable"
traceback (exit 1). The command now fails fast with the clean
Dataset-not-found panel and EXIT_USAGE (2), matching the BUG-159 contract
on model-experiment-create. Ghost-model (model_id) side already handled by
store verification; pinned here as the full ghost-input contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexus_scalp.cli.main import app
from nexus_scalp.release import exit_codes as xc

runner = CliRunner()


def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh CWD so artifacts/ and logs never touch the repo (same helper as TASK-CLI-E2E)."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_bug164_model_validate_ghost_dataset_fails_fast_exit_usage(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent dataset id -> Dataset not found panel, exit 2, no traceback."""
    res = runner.invoke(
        app,
        ["model-validate", "--model", "cand_ghost_zz", "--dataset", "ds_ghost_zz"],
    )
    assert res.exit_code == xc.EXIT_USAGE
    assert "Traceback" not in (res.stdout or "") + (res.stderr or "")
    assert "Dataset not found" in res.stdout + res.stderr


def test_bug164_model_train_ghost_experiment_contract_unchanged(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """model-train ghost experiment -> clean runtime failure, exit 1 (BUG-159 era)."""
    res = runner.invoke(app, ["model-train", "--experiment", "exp_ghost_zz"])
    assert res.exit_code == 1
    assert "Could not load experiment/dataset" in res.stdout + res.stderr
    assert "Traceback" not in res.stdout + res.stderr
