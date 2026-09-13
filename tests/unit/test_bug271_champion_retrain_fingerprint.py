"""BUG-271 regression: a GOVERNED in-place replacement of the serving artifact
(async retrain persist / collapse recovery / promotion+rollback activation)
re-registers provenance under a NEW fingerprint while the lifecycle CHAMPION
stamp stays on the OLD row. Without supersession the P0-2 boot trust anchor
(application/live/model_bundle_store._verify_champion_registry_binding) then
compares the serving bytes against the ORPHANED champion fingerprint and
permanently refuses the next cold boot — trading dies after a legitimate
retrain, with no self-heal path.

Pins:
  1. governed replace -> CHAMPION row follows the new fingerprint, anchor PASSES
  2. foreign (unauthorized) rewrite -> anchor still REFUSES (security intact)
  3. no champion row -> no-op
  4. champion on a DIFFERENT artifact path -> left alone (champion_sync owns it)
  5. empty fingerprint -> refused with an explicit reason
  6. append-only history: superseded row goes ARCHIVED, never deleted
  7. idempotent: second supersession call is a NO_SUPERSESSION_NEEDED
  8. wiring contract: LiveEngine._register_active_model calls the seam on
     replaced=True (the single choke point all governed replace paths cross)
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.application.live.model_bundle_store import ModelBundleStore
from nexus_scalp.experience.provenance import ModelRegistry, fingerprint_artifact
from nexus_scalp.model_lifecycle.load_integrity import ArtifactIntegrityError
from nexus_scalp.model_lifecycle.models import ModelStatus
from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry

NEW_FP = "governed in-place artifact replacement (test)"


@pytest.fixture
def env(tmp_path: Path):
    db_file = tmp_path / "bug271.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    registry = ModelRegistry(repo)
    lifecycle = ModelLifecycleRegistry(audit_repo=repo, model_registry=registry)
    yield repo, registry, lifecycle, db_file
    repo.close()


def _flush(repo: AuditRepository) -> None:
    assert repo.flush(timeout_sec=10.0)


def _rows(db_file: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT model_id, model_version, artifact_path, artifact_fingerprint,"
                " lifecycle_status FROM experience_model_registry"
                " ORDER BY registered_at, rowid;"
            )
        ]
    finally:
        conn.close()


def _register(registry: ModelRegistry, artifact: Path, *, replaced: bool):
    return registry.register_model(
        artifact_path=str(artifact),
        model_version="v1.0",
        feature_schema_id="scalp_v3",
        feature_dimension=70,
        model_role="PRIMARY_SCALP",
        replaced=replaced,
    )


def _make_champion(lifecycle: ModelLifecycleRegistry, prov) -> None:
    ok = lifecycle.set_status(
        model_id=prov.model_id,
        model_version=prov.model_version,
        status=ModelStatus.CHAMPION,
        reason="promoted (test)",
    )
    assert ok, f"set_status no-op for {prov.model_id}"


def _anchor(repo: AuditRepository, artifact: Path) -> None:
    """Runs the REAL P0-2 boot trust anchor against the on-disk artifact.

    Raises ArtifactIntegrityError on drift (that is the boot refusal).
    """

    class _Surface:
        om = None
        audit = repo

    ModelBundleStore._verify_champion_registry_binding(
        _Surface(),  # type: ignore[arg-type]  # unbound seam: engine IS the surface
        artifact,
        None,
    )


def _supersede(lifecycle: ModelLifecycleRegistry, prov, artifact: Path, fp: str):
    return lifecycle.supersede_champion_on_governed_replace(
        model_id=prov.model_id,
        model_version=prov.model_version,
        artifact_path=str(artifact),
        new_fingerprint=fp,
        reason=NEW_FP,
    )


def test_governed_replace_repoints_champion_and_boot_anchor_passes(env, tmp_path: Path) -> None:
    repo, registry, lifecycle, db = env
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"WEIGHTS-V1" * 40)
    prov1 = _register(registry, artifact, replaced=False)
    _flush(repo)
    _make_champion(lifecycle, prov1)
    _flush(repo)
    _anchor(repo, artifact)  # boot 1 serves

    # Governed persist: same path, new bytes, provenance re-registered
    # (exactly what the async-retrain success path does), THEN supersession.
    artifact.write_bytes(b"WEIGHTS-V2-RETRAINED" * 40)
    prov2 = _register(registry, artifact, replaced=True)
    _flush(repo)
    outcome = _supersede(lifecycle, prov2, artifact, prov2.artifact_fingerprint)
    _flush(repo)
    assert outcome["ok"] is True
    assert outcome["reason"] == "SUPERSEDED"
    # The next cold boot serves the governed retrain instead of refusing.
    _anchor(repo, artifact)


def test_foreign_rewrite_still_refuses_after_fix(env, tmp_path: Path) -> None:
    """Security invariant: the fix must NOT weaken the trust anchor."""
    repo, registry, lifecycle, _db = env
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"WEIGHTS-V1" * 40)
    prov = _register(registry, artifact, replaced=False)
    _flush(repo)
    _make_champion(lifecycle, prov)
    _flush(repo)
    _anchor(repo, artifact)

    artifact.write_bytes(b"ATTACKER-BYTES" * 40)
    with pytest.raises(ArtifactIntegrityError):
        _anchor(repo, artifact)


def test_no_champion_row_is_noop(env, tmp_path: Path) -> None:
    repo, registry, lifecycle, _db = env
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"W" * 64)
    prov = _register(registry, artifact, replaced=True)
    _flush(repo)
    outcome = _supersede(lifecycle, prov, artifact, prov.artifact_fingerprint)
    assert outcome["ok"] is True
    assert outcome["reason"] == "NO_CHAMPION_ROW"


def test_hot_swap_replaces_champion_path_and_fingerprint(env, tmp_path: Path) -> None:
    """Hot-swap shape (governed operator/API path): the same model identity is
    re-registered from ANOTHER artifact path with new bytes. The champion stamp
    must follow BOTH path and fingerprint — otherwise the next cold boot's
    trust anchor (which runs BEFORE champion_sync can repair) refuses a
    legitimately swapped model."""
    repo, registry, lifecycle, db = env
    old_artifact = tmp_path / "old" / "model.pt"
    old_artifact.parent.mkdir(parents=True)
    old_artifact.write_bytes(b"OLD-CHAMPION" * 40)
    prov_old = _register(registry, old_artifact, replaced=False)
    _flush(repo)
    _make_champion(lifecycle, prov_old)
    _flush(repo)

    new_artifact = tmp_path / "new" / "model.pt"
    new_artifact.parent.mkdir(parents=True)
    new_artifact.write_bytes(b"SWAPPED-BYTES" * 40)
    prov_new = _register(registry, new_artifact, replaced=True)
    _flush(repo)
    outcome = _supersede(lifecycle, prov_new, new_artifact, prov_new.artifact_fingerprint)
    _flush(repo)
    assert outcome["reason"] == "SUPERSEDED"
    champs = [r for r in _rows(db) if r["lifecycle_status"] == ModelStatus.CHAMPION.value]
    assert len(champs) == 1
    assert champs[0]["artifact_path"] == str(new_artifact)
    assert champs[0]["artifact_fingerprint"] == prov_new.artifact_fingerprint
    # boot after the swap serves
    _anchor(repo, new_artifact)


def test_cross_identity_champion_is_left_alone(env, tmp_path: Path) -> None:
    """A champion of ANOTHER identity (different schema/dimension row) is a
    promotion/registry-truth decision — never hijacked by a persist."""
    repo, registry, lifecycle, db = env
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"OTHER" * 40)
    other = registry.register_model(
        artifact_path=str(artifact),
        model_version="v9.9",
        feature_schema_id="scalp_v1",
        feature_dimension=50,
        model_role="PRIMARY_SCALP",
    )
    _flush(repo)
    assert lifecycle.set_status(
        model_id=other.model_id,
        model_version="v9.9",
        status=ModelStatus.CHAMPION,
        reason="other identity",
    )
    _flush(repo)

    mine = _register(registry, artifact, replaced=True)
    _flush(repo)
    assert mine.model_id != other.model_id
    outcome = _supersede(lifecycle, mine, artifact, mine.artifact_fingerprint)
    _flush(repo)
    assert outcome["reason"] == "NO_SUPERSESSION_NEEDED"
    champs = [r for r in _rows(db) if r["lifecycle_status"] == ModelStatus.CHAMPION.value]
    assert [r["model_id"] for r in champs] == [other.model_id]


def test_rejected_row_is_never_resurrected_by_a_persist(env, tmp_path: Path) -> None:
    """Security/lifecycle invariant: the new-fingerprint row must be in a
    promotable status. A REJECTED candidate cannot become champion by merely
    being persisted. (The successor status is stamped with raw SQL because the
    public set_status is identity-scoped and would also de-throne the
    champion row — a rejected *successor row* is what a gate produces.)"""
    repo, registry, lifecycle, db = env
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"V1" * 40)
    prov1 = _register(registry, artifact, replaced=False)
    _flush(repo)
    _make_champion(lifecycle, prov1)
    _flush(repo)

    artifact.write_bytes(b"REJECTED-BYTES" * 40)
    prov2 = _register(registry, artifact, replaced=True)
    _flush(repo)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE experience_model_registry SET lifecycle_status=? "
        "WHERE model_id=? AND model_version=? AND artifact_fingerprint=?;",
        (
            ModelStatus.REJECTED.value,
            prov2.model_id,
            prov2.model_version,
            prov2.artifact_fingerprint,
        ),
    )
    conn.commit()
    conn.close()

    outcome = _supersede(lifecycle, prov2, artifact, prov2.artifact_fingerprint)
    _flush(repo)
    assert outcome["reason"] == "NO_SUPERSESSION_NEEDED"
    assert outcome["successor_status"] == ModelStatus.REJECTED.value
    rows = _rows(db)
    champs = [r for r in rows if r["lifecycle_status"] == ModelStatus.CHAMPION.value]
    assert len(champs) == 1
    assert champs[0]["artifact_fingerprint"] == prov1.artifact_fingerprint
    # and the boot anchor fails closed exactly as before the fix
    with pytest.raises(ArtifactIntegrityError):
        _anchor(repo, artifact)


def test_empty_fingerprint_refused_with_reason(env, tmp_path: Path) -> None:
    repo, registry, lifecycle, _db = env
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"W" * 64)
    prov = _register(registry, artifact, replaced=False)
    _flush(repo)
    _make_champion(lifecycle, prov)
    _flush(repo)
    outcome = lifecycle.supersede_champion_on_governed_replace(
        model_id=prov.model_id,
        model_version=prov.model_version,
        artifact_path=str(artifact),
        new_fingerprint="",
        reason=NEW_FP,
    )
    assert outcome["ok"] is False
    assert outcome["reason"] == "EMPTY_FINGERPRINT"


def test_superseded_history_is_archived_never_deleted(env, tmp_path: Path) -> None:
    repo, registry, lifecycle, db = env
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"V1" * 40)
    prov1 = _register(registry, artifact, replaced=False)
    _flush(repo)
    _make_champion(lifecycle, prov1)
    _flush(repo)
    old_fp = prov1.artifact_fingerprint

    artifact.write_bytes(b"V2" * 40)
    prov2 = _register(registry, artifact, replaced=True)
    _flush(repo)
    _supersede(lifecycle, prov2, artifact, prov2.artifact_fingerprint)
    _flush(repo)

    rows = _rows(db)
    by_fp = {r["artifact_fingerprint"]: r["lifecycle_status"] for r in rows}
    assert by_fp[old_fp] == ModelStatus.ARCHIVED.value  # evidence preserved
    assert by_fp[prov2.artifact_fingerprint] == ModelStatus.CHAMPION.value
    assert len(rows) == 2  # append-only: nothing deleted


def test_supersession_is_idempotent(env, tmp_path: Path) -> None:
    repo, registry, lifecycle, db = env
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"V1" * 40)
    prov1 = _register(registry, artifact, replaced=False)
    _flush(repo)
    _make_champion(lifecycle, prov1)
    _flush(repo)
    artifact.write_bytes(b"V2" * 40)
    prov2 = _register(registry, artifact, replaced=True)
    _flush(repo)
    first = _supersede(lifecycle, prov2, artifact, prov2.artifact_fingerprint)
    _flush(repo)
    second = _supersede(lifecycle, prov2, artifact, prov2.artifact_fingerprint)
    _flush(repo)
    assert first["reason"] == "SUPERSEDED"
    assert second["reason"] == "NO_SUPERSESSION_NEEDED"
    champs = [r for r in _rows(db) if r["lifecycle_status"] == ModelStatus.CHAMPION.value]
    assert len(champs) == 1
    assert champs[0]["artifact_fingerprint"] == prov2.artifact_fingerprint


def test_engine_replace_path_wires_supersession() -> None:
    """Wiring contract: _register_active_model (the single choke point every
    governed replace persist crosses: async retrain, collapse recovery,
    promotion/rollback activation, hot-swap) must call the supersession seam
    ONLY for replaced=True. Boot registration (replaced=False) stays inert."""
    from nexus_scalp.application.live_engine import LiveEngine

    src = inspect.getsource(LiveEngine._register_active_model)
    assert "supersede_champion_on_governed_replace" in src
    assert "if not replaced:" in src


def test_engine_wiring_end_to_end_governed_replace(env, tmp_path: Path) -> None:
    """Execution proof of the wiring (not just source): call the REAL
    LiveEngine._register_active_model on a minimal engine surface after a
    governed in-place replace; the champion fingerprint must follow and the
    boot anchor must pass — the exact sequence the async-retrain success path
    performs (live_engine.py persist -> _register_active_model(replaced=True)).
    """
    from nexus_scalp.application.live_engine import LiveEngine

    repo, registry, lifecycle, db = env
    artifact = tmp_path / "model.pt"
    artifact.write_bytes(b"WEIGHTS-V1" * 40)
    prov1 = _register(registry, artifact, replaced=False)
    _flush(repo)
    _make_champion(lifecycle, prov1)
    _flush(repo)

    class _ExpEngine:
        def __init__(self) -> None:
            self.provenance: Any = None

        def set_provenance(self, p: Any) -> None:
            self.provenance = p

    class _EngineSurface:
        # attributes _register_active_model reads off the composition root
        audit = repo
        model_registry = registry
        effective_feature_schema_id = "scalp_v3"
        effective_feature_dim = 70
        config = SimpleNamespace(model=SimpleNamespace(feature_schema_version="v1.0"))
        runtime_config = SimpleNamespace(get_version=lambda: 3)

        def __init__(self) -> None:
            self.experience_engine = _ExpEngine()

    surface = _EngineSurface()

    # Governed persist: bytes replaced in place, then the engine's re-register.
    artifact.write_bytes(b"WEIGHTS-V2-RETRAINED" * 40)
    LiveEngine._register_active_model(surface, artifact, True)  # type: ignore[arg-type]
    _flush(repo)

    expected_fp = fingerprint_artifact(artifact)
    rows = _rows(db)
    champs = [r for r in rows if r["lifecycle_status"] == ModelStatus.CHAMPION.value]
    assert len(champs) == 1
    assert champs[0]["artifact_fingerprint"] == expected_fp
    # next cold boot serves (before the fix this raised ArtifactIntegrityError)
    _anchor(repo, artifact)

    # Boot registration (replaced=False) must stay INERT: no supersession, so
    # a foreign artifact registered at boot cannot re-point the governed stamp.
    artifact.write_bytes(b"WEIGHTS-V3" * 40)
    LiveEngine._register_active_model(surface, artifact, False)  # type: ignore[arg-type]
    _flush(repo)
    with pytest.raises(ArtifactIntegrityError):
        _anchor(repo, artifact)
