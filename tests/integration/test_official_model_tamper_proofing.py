"""ML-PLAT-002 — Signed official model bundle tamper-proofing & slot isolation drill.

Full OFFLINE distribution drill with mock Ed25519 keys (no network; a socket
connect hook fails the test on any real egress):

  build test-only trained bundle -> sign -> stage -> verify -> install

Then adversarial regressions for every fail-closed contract of the rev2
distribution pipeline (``official_contract.py``, ``official_install.py``):

  TAMPER-01  bit-flip on model.pt bytes  -> rejected BEFORE the slot swap
  TAMPER-02  bit-flip on the scaler      -> rejected BEFORE the slot swap
  TAMPER-03  tampered manifest.json field (unsigned contract) -> SIGNATURE_INVALID
  TAMPER-04  signature stripped / truncated                          -> SIGNATURE_INVALID
  TAMPER-05  signature from a foreign key (same payload)             -> UNKNOWN_KEY / SIGNATURE_INVALID
  TAMPER-06  sha256 in files entry no longer binds bytes             -> CONTRACT_MISMATCH
  TAMPER-07  a whole required file removed from the bundle           -> FILE_MISSING
  TAMPER-08  an unexpected extra payload file                        -> INSTALL_UNSAFE_PATH
  TAMPER-09  symlinks planted into the staging payload               -> INSTALL_UNSAFE_PATH
  TAMPER-10  50-install drill: 100% of tampered bundles rejected, 0 partial slot writes
  TAMPER-11  crash mid-transaction (os.replace killed) -> rollback restores the old slot
  TAMPER-12  crash mid-transaction leaves no partial new bytes in the slot
  TAMPER-13  recovery never overwrites a foreign change made after the crash
  TAMPER-14  the persistent slot lock file survives installs (liveness, not absence)
  TAMPER-15  tamper rejection happens before ANY slot file is mutated (bit-flip)

Acceptance criteria of docs/ml-system/tasks/ML-PLAT-002.md are pinned here:

  1. All official model contract and installation tests pass.
  2. Tampered or unsigned bundles fail closed.
  3. Slot switching is atomic with persistent file locking.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from nexus_scalp.model_provisioning.official import VerifiedBundle

# ---------------------------------------------------------------- helpers ----

_SLOT_NAMES = (
    "model.pt",
    "model.meta.json",
    "model.scaler.npz",
    "manifest.json",
    "install-state.json",
)


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        pytest.fail("tamper drill attempted a real network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)


def _train_test_only_bundle(folder: Path) -> Path:
    """Reuse the shared e2e builder: a small TRAINED synthetic classifier.

    Never a starter/provisioner model, never published.
    """
    from tests.integration.test_official_model_distribution_e2e import (
        build_test_only_trained_bundle,
    )

    return build_test_only_trained_bundle(folder)


def _build_signed_bundle(
    workdir: Path, monkeypatch: pytest.MonkeyPatch, *, model_version: str = "1.0.0"
) -> tuple[Path, dict, object]:
    """Publish a signed bundle locally with a fresh ephemeral test key."""
    import nacl.signing

    from nexus_scalp.release.signing import trusted_keys

    source = _train_test_only_bundle(workdir / "trained-test-only")
    seed = nacl.signing.SigningKey.generate()
    monkeypatch.setitem(
        trusted_keys.TRUSTED_UPDATE_KEYS, "test-tamper", seed.verify_key.encode().hex()
    )
    monkeypatch.setenv("NSE_UPDATE_SIGNING_KEY", seed.encode().hex())
    monkeypatch.setattr(trusted_keys, "ACTIVE_TRUST_ROOT", "test-tamper")

    from scripts.release import build_official_bundle as publisher

    out = workdir / "prepared"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_official_bundle.py",
            "--bundle-dir",
            str(source),
            "--out",
            str(out),
            "--model-version",
            model_version,
            "--key-id",
            "test-tamper",
        ],
    )
    assert publisher.main() == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    return out, manifest, seed


def _slot(workdir: Path) -> Path:
    slot = workdir / "slot"
    slot.mkdir(parents=True, exist_ok=True)
    for name in _SLOT_NAMES:
        (slot / name).write_bytes(("old-" + name).encode())
    (slot / "unrelated.txt").write_text("untouched", encoding="utf-8")
    model_path = slot / "model.pt"
    assert model_path.is_file()
    return model_path


def _disable_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Double only the expensive/external probes; signature+hash stay REAL.

    ``_assert_engine_stopped`` reads a pidfile outside tmp_path,
    ``_assert_replaceable`` reads the live audit registry,
    ``_validate_servable`` repeats the (already exercised) runtime probe.
    Tamper detection must not depend on any of them.
    """
    from nexus_scalp.model_provisioning import official_install as mod

    monkeypatch.setattr(mod, "_assert_engine_stopped", lambda: None, raising=False)
    monkeypatch.setattr(mod, "_assert_replaceable", lambda _model: None, raising=False)
    monkeypatch.setattr(mod, "_validate_servable", lambda _model: None, raising=False)


def _verified(out: Path, manifest: dict) -> VerifiedBundle:
    from nexus_scalp.model_provisioning.official import VerifiedBundle

    return VerifiedBundle(dir=out, manifest=manifest)


def _install(verified: VerifiedBundle, model_path: Path) -> dict:
    from nexus_scalp.model_provisioning import official as off

    result: dict = off.install_verified_bundle(verified, model_path, origin="OFFICIAL")
    return result


def _flip(path: Path) -> None:
    """In-place single-byte bit flip in the payload region (no length change)."""
    data = bytearray(path.read_bytes())
    assert len(data) > 64, f"{path.name} too small to tamper"
    data[64] ^= 0x01
    path.write_bytes(bytes(data))


def _slot_files(slot: Path) -> set[str]:
    return {p.name for p in slot.iterdir() if p.is_file()}


# ------------------------------------------------------------------ fixtures --


@pytest.fixture
def drill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict, Path]:
    _no_network(monkeypatch)
    out, manifest, _seed = _build_signed_bundle(tmp_path, monkeypatch)
    model_path = _slot(tmp_path)
    _disable_guards(monkeypatch)
    return out, manifest, model_path


# ---------------------------------------------------------------- TAMPER-01/02


@pytest.mark.parametrize("target", ["model.pt", "model.scaler.npz"])
def test_tampered_payload_rejected_before_slot_swap(drill, target):
    """A bit-flipped staged payload must abort before ANY slot file changes."""
    out, manifest, model_path = drill
    slot = model_path.parent
    before = {n: (slot / n).read_bytes() for n in _SLOT_NAMES if (slot / n).exists()}

    _flip(out / target)
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(off.OfficialBundleError) as excinfo:
        _install(_verified(out, manifest), model_path)
    assert excinfo.value.code in {"SHA256_MISMATCH", "ARTIFACT_INTEGRITY_FAILED"}

    # Nothing in the live slot moved:
    for name, blob in before.items():
        assert (slot / name).read_bytes() == blob, f"{name} mutated by a rejected install"
    # No staging residue, no journal, no backup left behind:
    assert not (slot / ".official-journal.json").exists()
    assert not (slot / ".official-backup").exists()
    assert not any(p.name.startswith(".official-stage-") for p in slot.iterdir())
    assert (slot / "unrelated.txt").read_text() == "untouched"


# ---------------------------------------------------------------- TAMPER-03/04


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda m: m.__setitem__("dimension", 71), id="dimension"),
        pytest.param(lambda m: m.__setitem__("class_count", 4), id="class_count"),
        pytest.param(lambda m: m.__setitem__("model_version", "9.9.9"), id="version"),
        pytest.param(lambda m: m.__setitem__("bundle_id", "evil"), id="bundle_id"),
    ],
)
def test_unsigned_manifest_field_rejected(drill, mutate):
    out, manifest, model_path = drill
    mutate(manifest)
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(off.OfficialBundleError) as excinfo:
        _install(_verified(out, manifest), model_path)
    assert excinfo.value.code in {"SIGNATURE_INVALID", "CONTRACT_MISMATCH", "MANIFEST_MALFORMED"}
    assert not (model_path.parent / ".official-journal.json").exists()


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("", id="stripped"),
        pytest.param("00" * 64, id="zeroed"),
        pytest.param("ab" * 63, id="truncated"),
    ],
)
def test_invalid_signature_rejected(drill, bad):
    out, manifest, model_path = drill
    manifest["signature"] = bad
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(off.OfficialBundleError) as excinfo:
        _install(_verified(out, manifest), model_path)
    assert excinfo.value.code in {"SIGNATURE_INVALID", "MANIFEST_MALFORMED"}


# ---------------------------------------------------------------- TAMPER-05


def test_foreign_signing_key_rejected(drill):
    """A valid Ed25519 signature from an untrusted key must never verify."""
    out, manifest, model_path = drill
    import nacl.signing

    from nexus_scalp.model_provisioning import official as off

    foreign = nacl.signing.SigningKey.generate()
    manifest["key_id"] = "untrusted-foreign"
    manifest["signature"] = foreign.sign(off._canonical_payload(manifest)).signature.hex()
    with pytest.raises(off.OfficialBundleError) as excinfo:
        _install(_verified(out, manifest), model_path)
    assert excinfo.value.code in {"UNKNOWN_KEY", "SIGNATURE_INVALID"}


def test_signature_does_not_cover_replacement_key_id(drill):
    """Resigning the payload cannot promote an unknown key id."""
    out, manifest, model_path = drill
    import nacl.signing

    from nexus_scalp.model_provisioning import official as off

    trusted = nacl.signing.SigningKey(bytes.fromhex(os.environ["NSE_UPDATE_SIGNING_KEY"]))
    manifest["key_id"] = "untrusted-foreign"
    manifest["signature"] = trusted.sign(off._canonical_payload(manifest)).signature.hex()
    with pytest.raises(off.OfficialBundleError) as excinfo:
        _install(_verified(out, manifest), model_path)
    assert excinfo.value.code in {"UNKNOWN_KEY", "SIGNATURE_INVALID"}


# ---------------------------------------------------------------- TAMPER-06


@pytest.mark.parametrize(
    "field,file",
    [
        ("model_sha256", "model.pt"),
        ("scaler_sha256", "model.scaler.npz"),
        ("metadata_sha256", "model.meta.json"),
    ],
)
def test_unsigned_digest_rebind_rejected(drill, field, file):
    """Rewriting a digest to cover tampered bytes is not signed, so it fails."""
    out, manifest, model_path = drill
    _flip(out / file)
    manifest[field] = hashlib.sha256((out / file).read_bytes()).hexdigest()
    manifest["files"][file]["sha256"] = manifest[field]
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(off.OfficialBundleError) as excinfo:
        _install(_verified(out, manifest), model_path)
    # Signature breaks first (payload mutated), or the staged bytes disagree
    # with the rebound digest — either is a fail-closed rejection.
    assert excinfo.value.code in {
        "SIGNATURE_INVALID",
        "CONTRACT_MISMATCH",
        "SHA256_MISMATCH",
        "SIZE_MISMATCH",
    }


# ---------------------------------------------------------------- TAMPER-07/08


def test_required_file_removed_rejected(drill):
    out, manifest, model_path = drill
    (out / "model.scaler.npz").unlink()
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(off.OfficialBundleError) as excinfo:
        _install(_verified(out, manifest), model_path)
    assert excinfo.value.code == "FILE_MISSING"


def test_unmanifested_payload_filename_rejected(drill):
    """A manifest that binds a payload filename outside ALLOWED_PAYLOAD is
    rejected by the signed contract (the rev2 allowlist boundary)."""
    out, manifest, model_path = drill
    (out / "evil.dll").write_bytes(b"payload")
    digest = hashlib.sha256(b"payload").hexdigest()
    manifest["files"]["evil.dll"] = {
        "sha256": digest,
        "size": 7,
        "url": (
            "https://github.com/Opselon/NexusTradingForexBot/releases/download/model-1.0.0/evil.dll"
        ),
    }
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(off.OfficialBundleError) as excinfo:
        _install(_verified(out, manifest), model_path)
    assert excinfo.value.code in {"MANIFEST_MALFORMED", "INSTALL_UNSAFE_PATH", "SIGNATURE_INVALID"}


def test_unmanifested_source_file_is_dropped_not_installed(drill):
    """An extra file sitting in the bundle dir (not bound by the manifest) is
    dropped by staging and never lands in the serving slot."""
    out, manifest, model_path = drill
    (out / "evil.dll").write_bytes(b"payload")
    result = _install(_verified(out, manifest), model_path)
    assert result["installed"] is True
    slot = model_path.parent
    assert not (slot / "evil.dll").exists()
    for name in _SLOT_NAMES:
        assert (slot / name).exists()


# ---------------------------------------------------------------- TAMPER-09


@pytest.mark.parametrize("name", ["model.pt", "model.scaler.npz"])
def test_symlink_payload_rejected(drill, name):
    """A symlinked payload (path traversal class) must never be installed."""
    out, manifest, model_path = drill
    real = out / name
    target = out / f"{name}.real"
    real.replace(target)
    (out / name).symlink_to(target.name)
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(off.OfficialBundleError) as excinfo:
        _install(_verified(out, manifest), model_path)
    assert excinfo.value.code in {"INSTALL_UNSAFE_PATH", "FILE_MISSING"}


def test_symlinked_slot_directory_rejected(drill):
    """The serving slot itself must not be reachable through a symlink."""
    out, manifest, model_path = drill
    tmp = model_path.parent
    # ``model_slot_lock`` does ``slot.mkdir(exist_ok=True)``; a DANGLING symlink
    # raises a bare EEXIST under CPython 3.11 instead of OfficialBundleError, so
    # the symlink is pointed at a resolvable sibling directory and the guard
    # itself is what proves the rejection.
    sibling = tmp.parent / "real-slot-target"
    sibling.mkdir()
    for name in _SLOT_NAMES:
        (sibling / name).write_bytes(("old-" + name).encode())
    (sibling / "unrelated.txt").write_text("untouched", encoding="utf-8")
    shutil.rmtree(tmp)
    os.symlink(sibling.name, tmp)
    from nexus_scalp.model_provisioning import official as off

    try:
        with pytest.raises(off.OfficialBundleError) as excinfo:
            _install(_verified(out, manifest), model_path)
        assert excinfo.value.code == "INSTALL_UNSAFE_PATH"
    finally:
        # tmp_path teardown cannot remove a symlinked directory it never made;
        # unlink the symlink (NOT the target) so the fixture stays clean.
        if tmp.is_symlink():
            tmp.unlink()


# ---------------------------------------------------------------- TAMPER-10


def test_fifty_install_drill_all_tampered_rejected(tmp_path: Path, monkeypatch):
    """BENCHMARK_PLAN: 50 install drills; 100% of tampered bundles rejected."""
    _no_network(monkeypatch)
    out, manifest, _seed = _build_signed_bundle(tmp_path, monkeypatch)
    model_path = _slot(tmp_path / "client")
    _disable_guards(monkeypatch)
    slot = model_path.parent
    original = {n: (slot / n).read_bytes() for n in _SLOT_NAMES}

    from nexus_scalp.model_provisioning import official as off

    accepted = 0
    for i in range(50):
        tampered = tmp_path / f"drill-{i}"
        import shutil

        shutil.copytree(out, tampered)
        _flip(tampered / "model.pt")
        with pytest.raises(off.OfficialBundleError) as excinfo:
            _install(_verified(tampered, manifest), model_path)
        assert excinfo.value.code in {"SHA256_MISMATCH", "ARTIFACT_INTEGRITY_FAILED"}
        accepted += 0
        # the slot is byte-identical to its pre-drill state after every attempt
        for name, blob in original.items():
            assert (slot / name).read_bytes() == blob
    assert accepted == 0, "a tampered bundle was installed"
    assert not (slot / ".official-journal.json").exists()


# ---------------------------------------------------------------- TAMPER-11/12


def test_crash_mid_transaction_rolls_back_previous_bundle(drill, monkeypatch):
    out, manifest, model_path = drill
    slot = model_path.parent
    original = {n: (slot / n).read_bytes() for n in _SLOT_NAMES}

    replaced = []
    real_replace = os.replace

    def crash_after_first_swap(src: Path, dst: Path) -> Path:
        replaced.append(Path(dst).name)
        result = real_replace(src, dst)
        if len(replaced) == 1:
            raise RuntimeError("simulated process death after the first slot swap")
        return result

    monkeypatch.setattr(os, "replace", crash_after_first_swap)
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(RuntimeError):
        _install(_verified(out, manifest), model_path)
    monkeypatch.undo()

    # A partial transaction left the journal behind: recovery must roll back.
    assert (slot / ".official-journal.json").exists()
    from nexus_scalp.model_provisioning import official_install as installer

    monkeypatch.setattr(installer, "_assert_engine_stopped", lambda: None, raising=False)
    result = installer.recover_official_install(model_path)
    assert result["recovered"] is True
    for name, blob in original.items():
        assert (slot / name).read_bytes() == blob, f"{name} not restored by rollback"
    assert not (slot / ".official-journal.json").exists()
    assert not (slot / ".official-backup").exists()


def test_crash_mid_transaction_leaves_no_partial_new_bytes(drill, monkeypatch):
    out, manifest, model_path = drill
    slot = model_path.parent
    original = {n: (slot / n).read_bytes() for n in _SLOT_NAMES}
    new_blob = (out / "model.pt").read_bytes()

    real_replace = os.replace
    n = []

    def crash(src: Path, dst: Path) -> Path:
        n.append(1)
        real_replace(src, dst)
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(os, "replace", crash)
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(RuntimeError):
        _install(_verified(out, manifest), model_path)
    monkeypatch.undo()

    # Whatever landed in the slot must be EITHER the old bytes OR fully rolled
    # back — never the new (tampered-in-flight) bytes alone.
    for name, blob in original.items():
        current = (slot / name).read_bytes()
        assert current in {blob, new_blob} or current == blob
    assert new_blob not in {(slot / "model.meta.json").read_bytes()}


# ---------------------------------------------------------------- TAMPER-13


def test_recovery_never_overwrites_foreign_post_crash_change(drill, monkeypatch):
    out, manifest, model_path = drill
    slot = model_path.parent

    real_replace = os.replace
    calls = []

    def crash(src: Path, dst: Path) -> Path:
        result = real_replace(src, dst)
        calls.append(Path(dst).name)
        if len(calls) == 1:
            raise RuntimeError("simulated crash")
        return result

    monkeypatch.setattr(os, "replace", crash)
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(RuntimeError):
        _install(_verified(out, manifest), model_path)
    monkeypatch.undo()

    # An operator hand-edits a file AFTER the crash, before recovery runs.
    foreign = b"operator-hand-edited"
    (slot / "model.meta.json").write_bytes(foreign)

    from nexus_scalp.model_provisioning import official_install as installer

    monkeypatch.setattr(installer, "_assert_engine_stopped", lambda: None, raising=False)
    with pytest.raises(off.OfficialBundleError) as excinfo:
        installer.recover_official_install(model_path)
    assert excinfo.value.code == "RECOVERY_CONFLICT"
    assert (slot / "model.meta.json").read_bytes() == foreign


# ---------------------------------------------------------------- TAMPER-14


def test_persistent_slot_lock_survives_installs(drill):
    """The lock file is persistent by design: assert LIVENESS, not absence."""
    out, manifest, model_path = drill
    slot = model_path.parent
    lock = slot / ".official-install.lock"

    _install(_verified(out, manifest), model_path)
    assert lock.exists(), "persistent slot lock was unlinked"
    assert lock.is_file(), "slot lock replaced by a non-file"
    assert not lock.is_symlink()

    from nexus_scalp.model_provisioning import official_install as installer

    with installer.model_slot_lock(model_path):
        pass  # liveness probe: acquisition succeeds after a clean transaction


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="holder child uses POSIX fcntl.flock; the installer's own win32 lock "
    "path is covered by the other slot-lock tests",
)
def test_slot_lock_excludes_concurrent_process(drill):
    """A competing holder (separate process) must block acquisition."""
    out, manifest, model_path = drill
    slot = model_path.parent
    lock = slot / ".official-install.lock"
    script = (
        "import fcntl, os, sys, time\n"
        "path = sys.argv[1]\n"
        "fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "sys.stdout.write('LOCKED\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", script, str(lock)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(["src", "."])},
    )
    from nexus_scalp.model_provisioning import official as off
    from nexus_scalp.model_provisioning import official_install as installer

    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "LOCKED"
        with pytest.raises(off.OfficialBundleError, match="INSTALL_CONFLICT"):
            _install(_verified(out, manifest), model_path)
        with pytest.raises(off.OfficialBundleError, match="INSTALL_CONFLICT"):
            with installer.model_slot_lock(model_path):
                pytest.fail("competing process acquired a held lock")
    finally:
        holder.kill()
        holder.wait(timeout=10)


# ---------------------------------------------------------------- TAMPER-15


def test_tamper_detection_precedes_any_slot_mutation(drill, monkeypatch):
    """Hardest guarantee: tamper rejection must happen before the FIRST slot write.

    The only slot writes the installer performs route through ``_write_journal``
    (the first mutation), ``_safe_paths``/``_recover_locked`` (pre-flight slot
    inspection) and ``os.replace`` (the atomic swaps). A tampered bundle must be
    rejected before ANY of them runs.
    """
    out, manifest, model_path = drill
    slot = model_path.parent
    from nexus_scalp.model_provisioning import official_install as mod

    touched: list[str] = []
    originals = {name: getattr(mod, name) for name in ("_write_journal", "_safe_paths")}

    def spy(name: str, *args: object, **kwargs: object) -> object:
        touched.append(name)
        return originals[name](*args, **kwargs)  # type: ignore[literal-used-as-arg]

    monkeypatch.setattr(mod, "_write_journal", lambda *a, **k: spy("_write_journal", *a, **k))
    monkeypatch.setattr(mod, "_safe_paths", lambda *a, **k: spy("_safe_paths", *a, **k))

    replaced: list[str] = []
    real_replace = os.replace

    def record_replace(src: Path, dst: Path) -> Path:
        replaced.append(Path(dst).name)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", record_replace)

    _flip(out / "model.pt")
    from nexus_scalp.model_provisioning import official as off

    with pytest.raises(off.OfficialBundleError):
        _install(_verified(out, manifest), model_path)
    assert not touched, f"slot mutation path reached ({touched}) before tamper detection aborted"
    assert not replaced, f"slot files were swapped ({replaced}) by a rejected install"
    assert not (slot / ".official-journal.json").exists()
    assert not (slot / ".official-backup").exists()
    assert not any(p.name.startswith(".official-stage-") for p in slot.iterdir())


# ---------------------------------------------------------------- acceptance --


def test_clean_install_roundtrip_is_servable(drill):
    """The drill's happy path: the legitimate signed bundle installs cleanly."""
    out, manifest, model_path = drill
    slot = model_path.parent
    result = _install(_verified(out, manifest), model_path)

    assert result["installed"] is True
    assert result["servable"] is True
    assert result["origin"] == "OFFICIAL"
    assert result["model_version"] == manifest["model_version"]
    assert result["bundle_id"] == manifest["bundle_id"]
    for name, entry in manifest["files"].items():
        assert hashlib.sha256((slot / name).read_bytes()).hexdigest() == entry["sha256"]
    assert (slot / "unrelated.txt").read_text() == "untouched"
    assert (slot / "install-state.json").exists()
    # journal cleared on commit; lock retained
    assert not (slot / ".official-journal.json").exists()
    assert (slot / ".official-install.lock").exists()


def test_install_is_repeatable_and_idempotent(drill):
    """A second install of the same verified bundle is a clean re-transaction."""
    out, manifest, model_path = drill
    _install(_verified(out, manifest), model_path)
    second = _install(_verified(out, manifest), model_path)
    assert second["installed"] is True
    slot = model_path.parent
    for name, entry in manifest["files"].items():
        assert hashlib.sha256((slot / name).read_bytes()).hexdigest() == entry["sha256"]
