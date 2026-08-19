"""Shared fixtures for the 70D shadow test suites (TASK-05-70D-SHADOW)."""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from nexus_scalp.shadow.shadow70.models import (
    SHADOW70_DIMENSION,
    SHADOW70_SCHEMA_ID,
    Shadow70CandidateContract,
)
from nexus_scalp.shadow.shadow70.runtime import sha256_file


def make_contract(tmp: str, **overrides: object) -> Shadow70CandidateContract:
    """Creates a deterministic VALIDATED 70D candidate contract with real
    artifact + scaler files (validated-candidate fixture)."""
    artifact = os.path.join(tmp, "model.pt")
    scaler = os.path.join(tmp, "model.pt.scaler.npz")
    with open(artifact, "wb") as f:
        f.write(b"state-dict-bytes")
    with open(scaler, "wb") as f:
        f.write(b"scaler-bytes")
    base = dict(
        model_id="cand_70d_liquidity_v1",
        model_version="v1.0",
        schema_id=SHADOW70_SCHEMA_ID,
        dimension=SHADOW70_DIMENSION,
        feature_schema_hash="f" * 16,
        scaler_hash=sha256_file(scaler),
        training_dataset_id="ds_fixture",
        validation_result="VALIDATED_CANDIDATE",
        artifact_hash=sha256_file(artifact),
        artifact_path=artifact,
        scaler_path=scaler,
        num_classes=4,
    )
    base.update(overrides)
    return Shadow70CandidateContract(**base)


def vector70(liquidity: float = 0.2, news: float = 0.1) -> list[float]:
    """Deterministic 70D vector (50 base + 10 news + 10 liquidity)."""
    return [0.0] * 50 + [news] * 10 + [liquidity] * 10


@pytest.fixture()
def tmp_artifacts() -> str:
    d = tempfile.mkdtemp(prefix="s70_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def contract(tmp_artifacts: str) -> Shadow70CandidateContract:
    return make_contract(tmp_artifacts)
