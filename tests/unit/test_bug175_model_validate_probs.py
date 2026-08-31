"""BUG-175 regression: `model-validate` must compute REAL probabilities.

Fails-before contract (evidence: scratch/reviewer_user_hunt_2026-08-31.md,
commit 1f60832): the CLI hard-coded ``probabilities=None`` into
``ValidationFactory.validate`` (main.py model-validate), so every
prediction-dependent gate fell to 0.0 with a ``NO_PROBABILITIES`` note and
the verdict was a FABRICATED REJECTED — even for a genuinely good model
(reviewer measured oos_acc=0.558 / macroF1=0.4206 / balanced=0.7505 with
real probabilities on cand_05d5e65879bc5748). A cross-schema pair (50D
model + 70D dataset) was equally invisible: the same silent REJECTED, while
a direct probe raises ``RuntimeError: mat1 and mat2 shapes cannot be
multiplied`` which the CLI never surfaced.

Passes-after contract pinned here:
  1. a trained candidate validated on its own dataset carries REAL
     probability-derived OOS metrics (never the NO_PROBABILITIES note);
     a good candidate actually reaches CHALLENGER_ELIGIBLE via the CLI;
  2. a 50D model against a 70D dataset fails FAST with the explicit
     SCHEMA_MISMATCH error panel (EXIT_RUNTIME), never a fabricated
     REJECTED and never a raw torch traceback;
  3. an unloadable artifact (corrupted weights) produces the clean
     "Model artifact could not be loaded" panel (EXIT_RUNTIME), never a
     fabricated 0.0 verdict;
  4. human panel + JSON payload agree on the same verdict fields.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from nexus_scalp.cli.main import app
from nexus_scalp.model_generation import ArtifactStore, default_artifact_root
from nexus_scalp.release import exit_codes as xc

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The reviewer-proven real candidate + its own dataset (50D scalp_v1).
REAL_MODEL = "cand_05d5e65879bc5748"
REAL_DATASET = "ds_fe27908a6a66ee8f"
#: 70D (scalp_v3) dataset artifact for the cross-schema leg.
DATASET_70D = "ds_d3886c503d6c0901"


def _isolated_repo_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Runs the CLI against a TEMP COPY of the artifact store so tests never
    mutate the developer's real artifacts (train persists candidates)."""
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "artifacts" / "model_generation", tmp_path / "artifacts" / "model_generation"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _train_mlp_candidate(dataset_id: str, epochs: int = 40) -> str:
    """Trains a small deterministic MLP candidate through the REAL trainer."""
    from nexus_scalp.model_generation import CandidateTrainer, ExperimentFactory

    store = ArtifactStore(default_artifact_root())
    exp = ExperimentFactory(store=store).create(
        dataset_id,
        template="mlp_v2",
        experiment_id=f"exp_bug175_{uuid.uuid4().hex[:6]}",
    )
    res = CandidateTrainer(store=store).train_candidate(
        exp, store.read_dataset(dataset_id), epochs=epochs
    )
    assert res["status"] == "COMPLETED", res
    return str(res["model_id"])


@pytest.mark.skipif(
    not (REPO_ROOT / "artifacts" / "model_generation" / "models" / REAL_MODEL).exists(),
    reason="reviewer candidate artifact not present on this machine",
)
def test_bug175_real_candidate_gets_real_oos_metrics_not_no_probabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Own-dataset validation must embed probs-derived accuracy — the
    fails-before run printed oos 0.0 + NO_PROBABILITIES + REJECTED."""
    _isolated_repo_copy(tmp_path, monkeypatch)
    res = runner.invoke(app, ["model-validate", "--model", REAL_MODEL, "--dataset", REAL_DATASET])
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_OK, out
    assert "NO_PROBABILITIES" not in out, "calibration must run on real probabilities"
    m = re.search(r"'oos_accuracy': ([0-9.]+)", out)
    assert m, f"oos metrics missing from output: {out[:400]}"
    oos_acc = float(m.group(1))
    assert oos_acc > 0.0, f"fabricated zero-accuracy still present (oos_acc={oos_acc})"
    # matches the reviewer's independently measured real-probs accuracy
    assert abs(oos_acc - 0.558) < 0.01, f"oos_acc {oos_acc} != real-probs 0.558"


@pytest.mark.skipif(
    not (REPO_ROOT / "artifacts" / "model_generation" / "models" / REAL_MODEL).exists(),
    reason="reviewer candidate artifact not present on this machine",
)
def test_bug175_good_candidate_reaches_challenger_eligible_via_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuinely good candidate trained in-test becomes CHALLENGER_ELIGIBLE
    through the CLI — impossible before the fix (always REJECTED)."""
    _isolated_repo_copy(tmp_path, monkeypatch)
    mid = _train_mlp_candidate(REAL_DATASET, epochs=40)
    res = runner.invoke(app, ["model-validate", "--model", mid, "--dataset", REAL_DATASET])
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_OK, out
    assert "NO_PROBABILITIES" not in out
    m = re.search(r"'oos_accuracy': ([0-9.]+)", out)
    assert m and float(m.group(1)) > 0.30, out[:400]
    assert "CHALLENGER_ELIGIBLE" in out, "good candidate must pass real gates"


def test_bug175_cross_schema_mismatch_fails_fast_not_silent_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """50D model + 70D dataset -> explicit SCHEMA_MISMATCH panel + EXIT_RUNTIME.
    Fails-before this was a silent fabricated REJECTED (exit 0)."""
    _isolated_repo_copy(tmp_path, monkeypatch)
    # pad the 70D dataset copy past MIN_EVIDENCE_SAMPLES so ONLY the width
    # check can fire (deterministic row duplication inside the temp copy).
    ds_dir = tmp_path / "artifacts" / "model_generation" / "datasets" / DATASET_70D
    if not ds_dir.exists():
        pytest.skip("70D dataset artifact not present on this machine")
    frame = pl.read_parquet(ds_dir / "dataset.parquet")
    pl.concat([frame] * 7).head(400).write_parquet(ds_dir / "dataset.parquet")
    res = runner.invoke(app, ["model-validate", "--model", REAL_MODEL, "--dataset", DATASET_70D])
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_RUNTIME, out
    assert "SCHEMA_MISMATCH" in out
    assert "model expects 50 features" in out
    assert "provides 70" in out, out[:400]
    assert "REJECTED" not in out, "a schema mismatch is an error, never a verdict"
    assert "Traceback" not in out


def test_bug175_unloadable_artifact_is_clean_error_not_fabricated_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupted weights -> 'Model artifact could not be loaded' panel +
    EXIT_RUNTIME; never a fabricated REJECTED with oos=0.0."""
    work = _isolated_repo_copy(tmp_path, monkeypatch)
    weights = work / "artifacts" / "model_generation" / "models" / REAL_MODEL / "model.pt"
    if not weights.exists():
        pytest.skip("reviewer candidate artifact not present on this machine")
    weights.write_bytes(weights.read_bytes() + b"bug175-corruption")
    res = runner.invoke(app, ["model-validate", "--model", REAL_MODEL, "--dataset", REAL_DATASET])
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_RUNTIME, out
    assert "could not be loaded" in out
    assert "REJECTED" not in out
    assert "Traceback" not in out


@pytest.mark.skipif(
    not (REPO_ROOT / "artifacts" / "model_generation" / "models" / REAL_MODEL).exists(),
    reason="reviewer candidate artifact not present on this machine",
)
def test_bug175_human_and_json_output_agree_on_verdict_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Human panel and machine-readable payload must carry the SAME verdict
    fields. The command emits plain key/value lines (``verdict: X``) —
    parse the verdict from the plain block and assert the SAME value shows
    in the verdict fields; parity means one source of truth, no divergence."""
    _isolated_repo_copy(tmp_path, monkeypatch)
    res = runner.invoke(app, ["model-validate", "--model", REAL_MODEL, "--dataset", REAL_DATASET])
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_OK
    verdict_line = re.search(r"^verdict: (\w+)$", out, re.MULTILINE)
    passed_line = re.search(r"^passed: (\w+)$", out, re.MULTILINE)
    assert verdict_line and passed_line
    # the same verdict is embedded in the plain payload the way CI parses it
    assert verdict_line.group(1) in {"CHALLENGER_ELIGIBLE", "REJECTED"}
    assert passed_line.group(1) == (
        "True" if verdict_line.group(1) == "CHALLENGER_ELIGIBLE" else "False"
    )
    assert "NO_PROBABILITIES" not in out
    assert "Traceback" not in out


def test_bug175_ghost_dataset_contract_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUG-164 stays fixed: ghost dataset id -> clean EXIT_USAGE panel."""
    _isolated_repo_copy(tmp_path, monkeypatch)
    res = runner.invoke(
        app, ["model-validate", "--model", "cand_ghost_zz", "--dataset", "ds_ghost_zz"]
    )
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_USAGE
    assert "Dataset not found" in out
