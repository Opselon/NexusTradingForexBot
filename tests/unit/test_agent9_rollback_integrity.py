"""AGENT-9 P1: rollback integrity regression — tampered snapshot must be refused.

The .previous-* snapshots that RollbackEngine.restore_application() copies back
over the app tree are created by ApplicationInstaller.install_portable() from a
payload that ALREADY passed the full trust chain (Ed25519 signed manifest ->
SHA-256 -> zip-manifest -> build-info version).  The snapshot itself is
re-verified at restore time against the release-manifest.json that the CI
release pipeline embeds in the portable tree (same file the BUG-166 pre-stage
and `verify-release` gate produce); a snapshot whose EXE bytes no longer match
the embedded manifest hashes must be REFUSED, never activated.

Attacker model: an actor (malware, mistaken rsync, disk corruption) modified or
planted files inside a .previous-* snapshot directory.  Without the restore-time
verification, a plain `nexus update rollback` would silently activate those
bytes — rollback as an integrity bypass around the whole update trust chain.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from nexus_scalp.release.update_engine.backup_migrate import ApplicationInstaller
from nexus_scalp.release.update_engine.rollback_state import RollbackEngine


def _payload_zip(tmp_path: Path, version: str = "9.1.0") -> Path:
    z = tmp_path / f"payload-{version}.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("build-info.json", json.dumps({"version": version}))
        zf.writestr("NexusScalpEngine.exe", b"MZ-PAYLOAD-EXE")
        zf.writestr("configs/base.yaml", "execution:\n  mode: PAPER\n")
    return z


def _manifest_for(root: Path) -> dict:
    """Manifest of the same shape release.yml's generate_manifest produces."""
    files = {}
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.name != "release-manifest.json":
            files[str(f.relative_to(root)).replace("\\", "/")] = hashlib.sha256(
                f.read_bytes()
            ).hexdigest()
    return {"manifest_version": "1.0.0", "files": files}


def _verify_manifest(manifest_path: Path, base_dir: Path) -> dict:
    """Standalone verifier (mirrors packaging.verify_manifest contract)."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = []
    for rel, expected in (data.get("files") or {}).items():
        f = base_dir / rel
        if not f.is_file():
            failures.append(rel)
            continue
        actual = hashlib.sha256(f.read_bytes()).hexdigest()
        if actual != expected:
            failures.append(rel)
    return {"valid": not failures, "bad_files": failures[:5]}


def _make_snapshot(tmp_path: Path, tamper: str | None = None) -> tuple[Path, Path]:
    """Install a payload over a SEEDED OLD app tree (creating a .previous-*
    snapshot that contains the old exe + configs), write the embedded
    release-manifest over the snapshot, then optionally tamper."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    # seed the OLD (currently installed) tree — this is what the snapshot captures
    (app_root / "NexusScalpEngine.exe").write_bytes(b"MZ-PAYLOAD-EXE")
    (app_root / "configs").mkdir()
    (app_root / "configs" / "base.yaml").write_text("execution:\n  mode: PAPER\n", encoding="utf-8")

    inst = ApplicationInstaller(app_root=app_root)
    res = inst.install_portable(_payload_zip(tmp_path), expected_version="9.1.0")
    snapshot = Path(res["previous"])
    assert (snapshot / "NexusScalpEngine.exe").read_bytes() == b"MZ-PAYLOAD-EXE", (
        "test setup: snapshot must contain the pre-update exe"
    )
    manifest = _manifest_for(snapshot)
    (snapshot / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    if tamper == "corrupt-exe":
        (snapshot / "NexusScalpEngine.exe").write_bytes(b"MZ-TAMPERED")
    elif tamper == "missing-exe":
        (snapshot / "NexusScalpEngine.exe").unlink()
    elif tamper == "traversal":
        (snapshot.parent / "escaped.txt").write_bytes(b"outside")
        (snapshot / "release-manifest.json").write_text(
            json.dumps(
                {**manifest, "files": {**manifest["files"], "../escaped.txt": "0" * 64}}
            ),
            encoding="utf-8",
        )
    return snapshot, app_root


def test_valid_snapshot_restores(tmp_path: Path) -> None:
    snapshot, app_root = _make_snapshot(tmp_path)
    rb = RollbackEngine(app_root=app_root, backup_dir=snapshot)
    res = rb.restore_application(reason="test")
    assert res["restored"] is True
    assert (app_root / "NexusScalpEngine.exe").read_bytes() == b"MZ-PAYLOAD-EXE"


def test_corrupted_snapshot_exe_refused(tmp_path: Path) -> None:
    snapshot, app_root = _make_snapshot(tmp_path, tamper="corrupt-exe")
    rb = RollbackEngine(app_root=app_root, backup_dir=snapshot)
    res = rb.restore_application(reason="test")
    assert res["restored"] is False
    assert res["error_code"] == "SNAPSHOT_INTEGRITY_FAILED"
    # the current app tree must be untouched
    assert (app_root / "NexusScalpEngine.exe").read_bytes() == b"MZ-PAYLOAD-EXE"


def test_missing_snapshot_file_refused(tmp_path: Path) -> None:
    snapshot, app_root = _make_snapshot(tmp_path, tamper="missing-exe")
    rb = RollbackEngine(app_root=app_root, backup_dir=snapshot)
    res = rb.restore_application(reason="test")
    assert res["restored"] is False
    assert res["error_code"] == "SNAPSHOT_INTEGRITY_FAILED"


def test_traversal_manifest_entry_refused(tmp_path: Path) -> None:
    snapshot, app_root = _make_snapshot(tmp_path, tamper="traversal")
    rb = RollbackEngine(app_root=app_root, backup_dir=snapshot)
    res = rb.restore_application(reason="test")
    assert res["restored"] is False
    assert res["error_code"] in ("SNAPSHOT_INTEGRITY_FAILED", "SNAPSHOT_TRAVERSAL")


def test_missing_manifest_refused(tmp_path: Path) -> None:
    snapshot, app_root = _make_snapshot(tmp_path)
    (snapshot / "release-manifest.json").unlink()
    rb = RollbackEngine(app_root=app_root, backup_dir=snapshot)
    res = rb.restore_application(reason="test")
    assert res["restored"] is False
    assert res["error_code"] == "SNAPSHOT_NO_MANIFEST"
