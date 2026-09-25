"""Transactional official installation: all writes confined to tmp_path."""

import hashlib
import json
from pathlib import Path

import pytest

from nexus_scalp.model_provisioning.official import OfficialBundleError, VerifiedBundle


def bundle(tmp_path, monkeypatch):
    from nacl.signing import SigningKey

    from nexus_scalp.model_provisioning import official
    from nexus_scalp.model_provisioning import official_install as mod
    from nexus_scalp.release.signing import trusted_keys

    source = tmp_path / "source"
    source.mkdir()
    for name in ("model.pt", "model.meta.json", "model.scaler.npz"):
        (source / name).write_bytes(("new-" + name).encode())
    manifest = {
        "schema": official.BUNDLE_MANIFEST_SCHEMA,
        "bundle_id": "test",
        "model_version": "1.0.0",
        "architecture": "ScalpNet",
        "architecture_version": "1.0.0",
        "feature_schema_id": "scalp_v3",
        "dimension": 70,
        "class_count": 3,
        "files": {
            p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "size": p.stat().st_size}
            for p in source.iterdir()
        },
        "key_id": "ephemeral-test",
    }
    key = SigningKey.generate()
    monkeypatch.setattr(trusted_keys, "trusted_public_key", lambda _: key.verify_key.encode().hex())
    import platform
    import sys

    from nexus_scalp.features.schema_contract import DIMENSION, SCHEMA_ID, feature_schema_hash

    manifest.update(
        contract_version=2,
        dimension=DIMENSION,
        feature_schema_id=SCHEMA_ID,
        feature_schema_hash=feature_schema_hash(),
        architecture_parameters={
            "num_features": DIMENSION,
            "num_classes": 3,
            "hidden_dim": 128,
            "num_heads": 4,
            "dropout_rate": 0.25,
        },
        input_layout="batch_features",
        class_labels=["NO_TRADE", "BUY", "SELL"],
        symbol="XAUUSD",
        timeframe="M1",
        producer={"python": "3.11.1", "pytorch": "2.14.0+cpu", "git_commit": "a" * 40},
        consumer={
            "python": ">=3.11,<3.15",
            "pytorch": ">=2.14,<2.15",
            "platforms": [f"{sys.platform}-{platform.machine().lower()}"],
        },
        training={
            "command": "test-only",
            "seed": 1,
            "dataset": {"id": "test-only", "sha256": "a" * 64},
        },
    )
    for field, name in [
        ("model_sha256", "model.pt"),
        ("scaler_sha256", "model.scaler.npz"),
        ("metadata_sha256", "model.meta.json"),
    ]:
        manifest[field] = manifest["files"][name]["sha256"]
    for name, entry in manifest["files"].items():
        entry["url"] = (
            f"https://github.com/Opselon/NexusTradingForexBot/releases/download/model-1.0.0/{name}"
        )
    manifest["signature"] = key.sign(official._canonical_payload(manifest)).signature.hex()
    (source / "manifest.json").write_text(json.dumps(manifest))
    slot = tmp_path / "slot"
    slot.mkdir()
    for name in (
        "model.pt",
        "model.meta.json",
        "model.scaler.npz",
        "manifest.json",
        "install-state.json",
    ):
        (slot / name).write_bytes(("old-" + name).encode())
    (slot / "unrelated.txt").write_text("untouched")
    monkeypatch.setattr(mod, "_assert_engine_stopped", lambda: None, raising=False)
    monkeypatch.setattr(mod, "_assert_replaceable", lambda _: None, raising=False)
    # Only expensive tensor/schema/behavior probe is doubled; signature/hash are real.
    monkeypatch.setattr(mod, "_validate_servable", lambda _: None, raising=False)
    return VerifiedBundle(source, manifest), slot / "model.pt"


def snapshot(model):
    return {
        p.name: p.read_bytes()
        for p in model.parent.iterdir()
        if p.is_file() and not p.name.startswith(".official")
    }


def test_tampered_download_is_reverified_before_mutation(tmp_path, monkeypatch):
    from nexus_scalp.model_provisioning import official_install as mod

    verified, model = bundle(tmp_path, monkeypatch)
    before = snapshot(model)
    verified.model_path().write_bytes(b"tampered-after-verification")
    with pytest.raises(OfficialBundleError, match="SHA256_MISMATCH"):
        mod.install_verified_bundle(verified, model)
    assert snapshot(model) == before


def test_success_preserves_unrelated_files_and_signed_manifest(tmp_path, monkeypatch):
    from nexus_scalp.model_provisioning import official_install as mod

    verified, model = bundle(tmp_path, monkeypatch)
    result = mod.install_verified_bundle(verified, model)
    assert result["installed"] is True
    assert model.read_bytes() == verified.model_path().read_bytes()
    assert (model.parent / "manifest.json").read_bytes() == (
        verified.dir / "manifest.json"
    ).read_bytes()
    assert (model.parent / "unrelated.txt").read_text() == "untouched"
    state = json.loads((model.parent / "install-state.json").read_text())
    assert state["origin"] == "OFFICIAL"
    assert state["model_sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()


@pytest.mark.parametrize("failure", ["stage", "final", "replace", "cancel"])
def test_failed_transaction_restores_entire_previous_bundle(tmp_path, monkeypatch, failure):
    from nexus_scalp.model_provisioning import official_install as mod

    verified, model = bundle(tmp_path, monkeypatch)
    before = snapshot(model)
    calls = 0

    def validate(path):
        nonlocal calls
        calls += 1
        if failure == "stage" or (failure == "final" and path.parent == model.parent):
            raise OfficialBundleError("ARTIFACT_INTEGRITY_FAILED", "probe rejected")

    monkeypatch.setattr(mod, "_validate_servable", validate)
    replace = mod.os.replace

    def broken_replace(src, dst):
        if Path(dst) == model.parent / "model.meta.json" and ".official-stage" in str(src):
            if failure == "cancel":
                raise KeyboardInterrupt()
            if failure == "replace":
                raise OSError("disk error")
        return replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", broken_replace)
    with pytest.raises((OfficialBundleError, KeyboardInterrupt, OSError)):
        mod.install_verified_bundle(verified, model)
    assert snapshot(model) == before


@pytest.mark.parametrize("foreign", [False, True])
def test_recovery_after_interrupted_mutation_never_overwrites_foreign_change(
    tmp_path, monkeypatch, foreign
):
    from nexus_scalp.model_provisioning import official_install as mod

    verified, model = bundle(tmp_path, monkeypatch)
    before = snapshot(model)
    original = mod._rollback
    monkeypatch.setattr(mod, "_rollback", lambda *_: None)  # process death: no exception cleanup

    def reject(path):
        if path == model:
            raise OfficialBundleError("FINAL_FAILED", "interrupted before rollback")

    monkeypatch.setattr(mod, "_validate_servable", reject)
    with pytest.raises(OfficialBundleError):
        mod.install_verified_bundle(verified, model)
    monkeypatch.setattr(mod, "_rollback", original)
    if foreign:
        model.write_bytes(b"newer-uncoordinated-attempt")
        changed = snapshot(model)
        with pytest.raises(OfficialBundleError, match="RECOVERY_CONFLICT"):
            mod.recover_official_install(model)
        assert snapshot(model) == changed
    else:
        assert mod.recover_official_install(model)["recovered"] is True
        assert snapshot(model) == before
        assert mod.recover_official_install(model)["recovered"] is False


@pytest.mark.parametrize("state", ["LIVE", "PAPER", "SHADOW", "RUNNING", "UNKNOWN"])
def test_running_engine_refuses_before_mutation(tmp_path, monkeypatch, state):
    from nexus_scalp.model_provisioning import official_install as mod
    from nexus_scalp.release import paths
    from nexus_scalp.release.update_engine.safety_guards import EngineGuard

    real_guard = getattr(mod, "_assert_engine_stopped", None)
    verified, model = bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "_assert_engine_stopped", real_guard)
    monkeypatch.setattr(paths, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(EngineGuard, "engine_state", lambda self: state)
    before = snapshot(model)
    with pytest.raises(OfficialBundleError, match="ENGINE_RUNNING"):
        mod.install_verified_bundle(verified, model)
    assert snapshot(model) == before


@pytest.mark.parametrize("point", ["before", "during"])
def test_cooperative_cancel_restores_previous_bundle(tmp_path, monkeypatch, point):
    import threading

    from nexus_scalp.model_provisioning import official_install as mod

    verified, model = bundle(tmp_path, monkeypatch)
    before = snapshot(model)
    event = threading.Event()
    if point == "before":
        event.set()
    replace = mod.os.replace

    def cancel_after_first(src, dst):
        value = replace(src, dst)
        if Path(dst) == model:
            event.set()
        return value

    monkeypatch.setattr(mod.os, "replace", cancel_after_first)
    with pytest.raises(OfficialBundleError, match="CANCELLED"):
        mod.install_verified_bundle(verified, model, cancel_event=event)
    assert snapshot(model) == before


@pytest.mark.parametrize(
    "unsafe", ["target_symlink", "backup_symlink", "journal_path", "state", "basename", "origin"]
)
def test_unsafe_install_inputs_fail_closed(tmp_path, monkeypatch, unsafe):
    from nexus_scalp.model_provisioning import official_install as mod
    from nexus_scalp.model_provisioning.states import LifecycleState

    verified, model = bundle(tmp_path, monkeypatch)
    external = tmp_path / "external"
    external.mkdir()
    (external / "model.pt").write_bytes(b"external")
    origin = "OFFICIAL"
    if unsafe == "target_symlink":
        model.unlink()
        model.symlink_to(external / "model.pt")
    elif unsafe == "backup_symlink":
        (model.parent / ".official-backup").symlink_to(external, target_is_directory=True)
    elif unsafe == "journal_path":
        (model.parent / ".official-journal.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "state": "INSTALLING",
                    "old": {"../external/model.pt": None},
                    "new": {"../external/model.pt": "a" * 64},
                }
            )
        )
    elif unsafe == "state":
        verified.state = LifecycleState.REJECTED
    elif unsafe == "basename":
        model = model.with_name("custom.pt")
    else:
        origin = "GOVERNED"
    before = snapshot(model)
    with pytest.raises(OfficialBundleError):
        mod.install_verified_bundle(verified, model, origin=origin)
    assert snapshot(model) == before
    assert (external / "model.pt").read_bytes() == b"external"


def test_existing_servable_foreign_bundle_requires_governed_promotion(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from nexus_scalp.database import config
    from nexus_scalp.database.provider import DatabaseProvider
    from nexus_scalp.model_provisioning import official_install as mod
    from nexus_scalp.release import bootstrap, model_bootstrap

    real = getattr(mod, "_assert_replaceable", None)
    verified, model = bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "_assert_replaceable", real)
    monkeypatch.setattr(
        config,
        "load_database_config",
        lambda _: SimpleNamespace(
            provider=DatabaseProvider.SQLITE, sqlite_path=str(tmp_path / "absent.db")
        ),
    )
    monkeypatch.setattr(bootstrap, "bundle_status", lambda _: {"state": bootstrap.STATE_OK})
    monkeypatch.setattr(model_bootstrap, "_is_declared_starter", lambda _: False)
    before = snapshot(model)
    with pytest.raises(OfficialBundleError, match="GOVERNED_PROMOTION_REQUIRED"):
        mod.install_verified_bundle(verified, model)
    assert snapshot(model) == before


@pytest.mark.parametrize("db_state", ["champion", "locked_or_corrupt", "empty"])
def test_registry_guard_is_read_only_and_fails_closed(tmp_path, monkeypatch, db_state):
    import sqlite3
    from types import SimpleNamespace

    from nexus_scalp.database import config
    from nexus_scalp.database.provider import DatabaseProvider
    from nexus_scalp.model_provisioning import official_install as mod
    from nexus_scalp.release import bootstrap

    real = mod._assert_replaceable
    verified, model = bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "_assert_replaceable", real)
    monkeypatch.setattr(bootstrap, "bundle_status", lambda _: {"state": bootstrap.STATE_UNSERVABLE})
    db = tmp_path / "audit.db"
    if db_state == "locked_or_corrupt":
        db.write_bytes(b"not-sqlite")
    else:
        with sqlite3.connect(db) as con:
            con.execute(
                "CREATE TABLE experience_model_registry(model_id, artifact_fingerprint, artifact_path, lifecycle_status, registered_at)"
            )
            if db_state == "champion":
                con.execute(
                    "INSERT INTO experience_model_registry VALUES (?, ?, ?, ?, ?)",
                    ("champ", "other-digest", str(model), "CHAMPION", "now"),
                )
    monkeypatch.setattr(
        config,
        "load_database_config",
        lambda _: SimpleNamespace(provider=DatabaseProvider.SQLITE, sqlite_path=str(db)),
    )
    before = snapshot(model)
    db_before = db.read_bytes()
    if db_state == "empty":
        assert mod.install_verified_bundle(verified, model)["installed"] is True
    else:
        with pytest.raises(OfficialBundleError, match="GOVERN"):
            mod.install_verified_bundle(verified, model)
        assert snapshot(model) == before
    assert db.read_bytes() == db_before


def test_engine_startup_recovers_before_pid_and_blocks_install(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    from nexus_scalp.cli import engine_boot
    from nexus_scalp.model_provisioning import official_install as mod
    from nexus_scalp.release import bootstrap, paths

    verified, model = bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(bootstrap, "canonical_starter_path", lambda _: model)
    monkeypatch.setattr(paths, "get_data_root", lambda: tmp_path)
    pid = tmp_path / "nexus.pid"
    events = []

    def recover(path, **kw):
        assert not pid.exists()
        events.append("recovery")
        return {"recovered": False}

    monkeypatch.setattr(mod, "recover_official_install", recover)

    def run(*args, **kwargs):
        assert pid.read_text() == str(os.getpid())
        events.append("engine")

    monkeypatch.setattr(
        engine_boot, "run_startup_migration_gate", lambda **_: {"ready": True}, raising=False
    )
    monkeypatch.setattr(engine_boot, "anchor_workspace", lambda: None, raising=False)
    monkeypatch.setattr(engine_boot, "ensure_packaged_config_dir", lambda: None, raising=False)
    mode = SimpleNamespace(value="PAPER")
    monkeypatch.setattr(engine_boot, "ExecutionMode", SimpleNamespace(PAPER=mode, SHADOW=None))
    config = SimpleNamespace(
        execution=SimpleNamespace(mode=mode), model=SimpleNamespace(model_artifact_path=str(model))
    )

    def coordinator(**kwargs):
        def ensure(mode):
            events.append("slot")
            return {"action": "none", "slot": {}}

        return SimpleNamespace(ensure_serving_model=ensure)

    monkeypatch.setattr("nexus_scalp.model_provisioning.FirstRunCoordinator", coordinator)

    def load_adapter(*a, **k):
        raise RuntimeError("stop before heavy engine path")

    monkeypatch.setattr(engine_boot, "_build_adapter", load_adapter, raising=False)
    monkeypatch.setattr(engine_boot, "_run_engine_locked", run, raising=False)
    engine_boot._run_engine(config, gateway=False, port=8080)
    assert events == ["recovery", "engine"]
    assert not pid.exists()


def test_slot_lock_allows_nested_recovery_but_excludes_other_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from nexus_scalp.model_provisioning.official_install import model_slot_lock

    model = tmp_path / "model.pt"

    def other():
        with pytest.raises(OfficialBundleError, match="INSTALL_CONFLICT"):
            with model_slot_lock(model):
                pytest.fail("other thread acquired")

    with model_slot_lock(model):
        with model_slot_lock(model):
            with ThreadPoolExecutor(1) as pool:
                pool.submit(other).result()


def test_slot_lock_excludes_another_installer(tmp_path: Path) -> None:
    from nexus_scalp.model_provisioning.official import OfficialBundleError
    from nexus_scalp.model_provisioning.official_install import model_slot_lock

    model = tmp_path / "slot" / "model.pt"
    import subprocess
    import sys

    script = "from pathlib import Path; from nexus_scalp.model_provisioning.official_install import model_slot_lock; from nexus_scalp.model_provisioning.official import OfficialBundleError; import sys\ntry:\n with model_slot_lock(Path(sys.argv[1])): sys.exit(2)\nexcept OfficialBundleError: sys.exit(0)"
    with model_slot_lock(model):
        result = subprocess.run(
            [sys.executable, "-c", script, str(model)], capture_output=True, check=False
        )
        assert result.returncode == 0, result.stderr
    with model_slot_lock(model):
        pass
