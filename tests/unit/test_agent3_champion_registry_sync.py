"""AGENT-3 regression: champion registry-truthfulness sync must be contract-aware."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.provenance import ModelRegistry
from nexus_scalp.model_lifecycle.models import ModelStatus
from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry


@pytest.fixture
def audit_env(tmp_path: Path):
    db_file = tmp_path / "agent3_sync.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    registry = ModelRegistry(repo)
    lifecycle = ModelLifecycleRegistry(audit_repo=repo, model_registry=registry)
    yield repo, registry, lifecycle, db_file
    repo.close()


def _flush(repo: AuditRepository) -> None:
    assert repo.flush(timeout_sec=10.0)


def _register_champion_row(registry, lifecycle, *, artifact_path: str, schema_id: str, dimension: int) -> str:
    prov = registry.register_model(
        artifact_path=artifact_path,
        model_version="v1.0",
        feature_schema_id=schema_id,
        feature_dimension=dimension,
        config_version="9.0.8-fix",
        model_role="PRIMARY_SCALP",
    )
    landed_id = prov.model_id
    assert registry.audit_repo.flush(timeout_sec=10.0)
    ok = lifecycle.set_status(
        model_id=landed_id,
        model_version="v1.0",
        status=ModelStatus.CHAMPION,
        reason="restore trained candidate as champion (historical out-of-band)",
    )
    assert ok, f"set_status no-op for {landed_id}"
    assert registry.audit_repo.flush(timeout_sec=10.0)
    return landed_id


def _sync(current_row, serving_path: str, serving_schema: str, serving_dim: int) -> dict:
    from nexus_scalp.application.live_engine import LiveEngine

    return LiveEngine._evaluate_champion_registry_sync(
        None,
        current_row=current_row,
        serving_artifact_path=serving_path,
        serving_schema_id=serving_schema,
        serving_dimension=serving_dim,
    )


class TestChampionRegistrySyncTruthfulness:
    def test_matching_champion_row_is_left_alone(self, audit_env):
        repo, registry, lifecycle, _ = audit_env
        _flush(repo)
        registry.register_model(
            artifact_path="artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
            model_version="v3.0",
            feature_schema_id="scalp_v3",
            feature_dimension=70,
        )
        _flush(repo)
        lifecycle.set_status(
            model_id="primary_scalp_scalp_v3_70d",
            model_version="v3.0",
            status=ModelStatus.CHAMPION,
            reason="truthful",
        )
        _flush(repo)
        current = lifecycle.champion()
        decision = _sync(
            current, "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt", "scalp_v3", 70
        )
        assert decision["action"] == "NOOP"
        assert decision["reason"] == "already_truthful"

    def test_foreign_champion_row_with_mismatched_contract_is_archived(self, audit_env, tmp_path):
        repo, registry, lifecycle, _ = audit_env
        _flush(repo)
        art = tmp_path / "serving_model.pt"
        art.write_bytes(b"fake-serving-bytes")
        serving_path = str(art)
        landed = _register_champion_row(
            registry, lifecycle, artifact_path=serving_path, schema_id="scalp_v3", dimension=70
        )
        _flush(repo)
        current = lifecycle.champion()
        assert current is not None and current["model_id"] == landed
        decision = _sync(current, serving_path, "scalp_v1", 50)
        assert decision["action"] == "REPAIR"
        assert decision["stale_row_model_id"] == "primary_scalp_scalp_v3_70d"
        assert decision["new_champion_model_id"] == "primary_scalp_scalp_v1_50d"
        assert decision["demote_stale_to"] == ModelStatus.ARCHIVED.value

    def test_no_champion_rows_bootstraps_truthful_row(self, audit_env, tmp_path):
        repo, registry, lifecycle, _ = audit_env
        _flush(repo)
        art = tmp_path / "boot_model.pt"
        art.write_bytes(b"cold-start-bytes")
        decision = _sync(None, str(art), "scalp_v1", 50)
        assert decision["action"] == "BOOTSTRAP"
        assert decision["new_champion_model_id"] == "primary_scalp_scalp_v1_50d"
