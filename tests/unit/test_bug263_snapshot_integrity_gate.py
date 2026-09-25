"""BUG-263 (previously tracked as BUG-263 during the recovery audit): the
snapshot-integrity gate behind ``RollbackEngine.restore_application``.

Companion battery to ``test_agent9_rollback_integrity.py`` (the spec file):
pins the packaging-level verifier itself — both PRODUCTION manifest shapes
(``artifacts`` records from ``packaging.generate_manifest`` /
``scripts/build/update_helpers.py``, release-root AND portable-root spellings),
the fail-closed verdicts (malformed JSON, malformed digests, empty entry set,
traversal/absolute records, planted shadow files), the SHA256SUMS.txt
cross-check, and the orchestrator's refusal wiring (rollback reports
``FAILED_SAFE``/refusal, never a fake ``ROLLED_BACK`` over an activated
tampered tree). No spec assertion is modified or weakened anywhere.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.release import packaging
from nexus_scalp.release.update_engine.backup_migrate import ApplicationInstaller
from nexus_scalp.release.update_engine.constants import STATE_FAILED_SAFE
from nexus_scalp.release.update_engine.orchestrator import UpdateOrchestrator
from nexus_scalp.release.update_engine.rollback_state import (
    ERROR_SNAPSHOT_INTEGRITY_FAILED,
    ERROR_SNAPSHOT_NO_MANIFEST,
    RollbackEngine,
)
from tests.helpers.rollback_fixtures import seed_release_contract


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _payload_zip(tmp_path: Path, version: str = "9.1.0") -> Path:
    z = tmp_path / f"payload-{version}.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("build-info.json", json.dumps({"version": version}))
        zf.writestr("NexusScalpEngine.exe", b"MZ-PAYLOAD-EXE")
        zf.writestr("configs/base.yaml", "execution:\n  mode: PAPER\n")
    return z


def _installed_snapshot(
    tmp_path: Path, tamper: str | None = None
) -> tuple[Path, Path, dict[str, bytes]]:
    """Install a payload over a seeded tree; embed a production-shaped
    (``artifacts`` records, portable-root-relative) release manifest in the
    resulting .previous-* snapshot; optionally tamper the snapshot."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "NexusScalpEngine.exe").write_bytes(b"MZ-PAYLOAD-EXE")
    # an installed portable tree always carries its embedded build identity
    (app_root / "build-info.json").write_text(
        json.dumps({"version": "9.0.0", "channel": "stable"}), encoding="utf-8"
    )
    (app_root / "configs").mkdir()
    (app_root / "configs" / "base.yaml").write_text("execution:\n  mode: PAPER\n", encoding="utf-8")
    inst = ApplicationInstaller(app_root=app_root)
    res = inst.install_portable(_payload_zip(tmp_path), expected_version="9.1.0")
    snapshot = Path(res["previous"])
    seed_release_contract(snapshot, version="9.0.0")
    before = {
        str(p.relative_to(app_root)): p.read_bytes()
        for p in sorted(app_root.rglob("*"))
        if p.is_file()
    }
    if tamper == "corrupt-exe":
        (snapshot / "NexusScalpEngine.exe").write_bytes(b"MZ-TAMPERED")
    elif tamper == "planted-shadow":
        # attacker plants a portable/ twin matching the manifest while the
        # ROOT file that actually gets restored is tampered (BUG-160 remap
        # must not be steerable).
        exe_hash = json.loads((snapshot / "release-manifest.json").read_text())["artifacts"][0][
            "sha256"
        ]
        root_file = snapshot / "NexusScalpEngine.exe"
        root_file.write_bytes(b"MZ-TAMPERED")
        (snapshot / "release-manifest.json").write_text(
            json.dumps(
                {
                    "artifacts": [
                        {
                            "name": "portable-exe",
                            "relative_path": "portable/NexusScalpEngine.exe",
                            "sha256": exe_hash,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    elif tamper == "sums-mismatch":
        (snapshot / "NexusScalpEngine.exe").write_bytes(b"MZ-TAMPERED")
        seed_release_contract(snapshot, version="9.0.0")  # re-covers tampered bytes
        (snapshot / "SHA256SUMS.txt").write_text(
            f"{_sha(b'MZ-PAYLOAD-EXE')}  NexusScalpEngine.exe\n", encoding="ascii"
        )
    elif tamper == "identity-forgery":
        (snapshot / "NexusScalpEngine.exe").write_bytes(b"MZ-OTHER-RELEASE")
        seed_release_contract(snapshot, version="9.0.0")  # hashes the swapped bytes
        manifest = json.loads((snapshot / "release-manifest.json").read_text())
        manifest["version"] = "8.0.0"  # != build-info.json's 9.0.0-era stamp
        (snapshot / "release-manifest.json").write_text(json.dumps(manifest), "utf-8")
    return snapshot, app_root, before


def _tree_state(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): _sha(p.read_bytes())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# packaging.verify_snapshot_integrity — the canonical gate
# ---------------------------------------------------------------------------
def test_production_artifacts_manifest_passes(tmp_path: Path) -> None:
    snapshot, _app, _before = _installed_snapshot(tmp_path)
    res = packaging.verify_snapshot_integrity(snapshot)
    assert res["valid"] is True, res
    assert res["verified_files"] >= 2


def test_release_root_spelling_missing_external_entries_tolerated(tmp_path: Path) -> None:
    """A real embedded copy is the release-root manifest (cli/... + payload
    zip records that never land in the app tree): their absence is a layout
    fact, reported but never a refusal."""
    snapshot, _app, _before = _installed_snapshot(tmp_path)
    exe = snapshot / "NexusScalpEngine.exe"
    (snapshot / "release-manifest.json").write_text(
        json.dumps(
            {
                "version": "9.0.0",
                "artifacts": [
                    {
                        "name": "NexusScalpEngine.exe",
                        "relative_path": "portable/NexusScalpEngine.exe",
                        "sha256": _sha(exe.read_bytes()),
                    },
                    {
                        "name": "NexusScalpEngine-CLI.exe",
                        "relative_path": "cli/NexusScalpEngine-CLI.exe",
                        "sha256": _sha(b"CLI"),
                    },
                    {
                        "name": "NexusScalpEngine-9.0.0-win-x64.zip",
                        "relative_path": "NexusScalpEngine-9.0.0-win-x64.zip",
                        "sha256": _sha(b"ZIP"),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    res = packaging.verify_snapshot_integrity(snapshot)
    assert res["valid"] is True, res
    assert sorted(res["external_absent"]) == [
        "NexusScalpEngine-9.0.0-win-x64.zip",
        "cli/NexusScalpEngine-CLI.exe",
    ]


def test_release_root_spelling_tampered_portable_refused(tmp_path: Path) -> None:
    snapshot, _app, _before = _installed_snapshot(tmp_path)
    exe = snapshot / "NexusScalpEngine.exe"
    (snapshot / "release-manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "name": "NexusScalpEngine.exe",
                        "relative_path": "portable/NexusScalpEngine.exe",
                        "sha256": "0" * 64,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert exe.is_file()
    res = packaging.verify_snapshot_integrity(snapshot)
    assert res["valid"] is False
    assert res["reason"] == "HASH_MISMATCH"


def test_planted_shadow_cannot_steal_the_remap(tmp_path: Path) -> None:
    """Manifest claims portable/x; BOTH spellings must match. A good
    portable/x twin must not vouch for a tampered root x."""
    snapshot, _app, _before = _installed_snapshot(tmp_path, tamper="planted-shadow")
    (snapshot / "portable").mkdir(exist_ok=True)
    (snapshot / "portable" / "NexusScalpEngine.exe").write_bytes(b"MZ-PAYLOAD-EXE")
    res = packaging.verify_snapshot_integrity(snapshot)
    assert res["valid"] is False
    assert res["reason"] == "HASH_MISMATCH"


@pytest.mark.parametrize(
    ("raw", "expected_reason"),
    [
        (b"not json at all {", "MANIFEST_UNREADABLE"),
        (b'{"artifacts": []}', "MANIFEST_MALFORMED"),
        (b'{"files": {}}', "MANIFEST_MALFORMED"),
        (b'{"version": "1.0"}', "MANIFEST_MALFORMED"),
        (b"[1, 2, 3]", "MANIFEST_MALFORMED"),
        (b'{"artifacts": {"x": "not-a-list"}}', "MANIFEST_MALFORMED"),
        (
            json.dumps({"artifacts": [{"name": "a.txt", "sha256": "zz" + "00" * 31}]}).encode(),
            "MANIFEST_MALFORMED",
        ),
        (
            json.dumps(
                {
                    "artifacts": [
                        {
                            "name": "win",
                            "relative_path": "C:/Windows/x",
                            "sha256": "0" * 64,
                        }
                    ]
                }
            ).encode(),
            "MANIFEST_TRAVERSAL",
        ),
        (
            json.dumps({"files": {"a/../../outside.txt": "0" * 64}}).encode(),
            "MANIFEST_TRAVERSAL",
        ),
    ],
)
def test_malformed_manifests_fail_closed(tmp_path: Path, raw: bytes, expected_reason: str) -> None:
    (tmp_path / "a.txt").write_bytes(b"A")
    (tmp_path / "release-manifest.json").write_bytes(raw)
    res = packaging.verify_snapshot_integrity(tmp_path)
    assert res["valid"] is False
    assert res["reason"] == expected_reason, res


def test_absent_manifest_reason_missing(tmp_path: Path) -> None:
    res = packaging.verify_snapshot_integrity(tmp_path)
    assert res["valid"] is False
    assert res["reason"] == "MANIFEST_MISSING"


def test_verifier_is_pure_read(tmp_path: Path) -> None:
    snapshot, _app, _before = _installed_snapshot(tmp_path)
    listing = sorted(
        (str(p.relative_to(snapshot)), p.stat().st_mtime_ns)
        for p in snapshot.rglob("*")
        if p.is_file()
    )
    res = packaging.verify_snapshot_integrity(snapshot)
    assert res["valid"] is True
    after = sorted(
        (str(p.relative_to(snapshot)), p.stat().st_mtime_ns)
        for p in snapshot.rglob("*")
        if p.is_file()
    )
    assert after == listing


def test_sums_cross_check_refuses(tmp_path: Path) -> None:
    snapshot, _app, _before = _installed_snapshot(tmp_path, tamper="sums-mismatch")
    res = packaging.verify_snapshot_integrity(snapshot)
    assert res["valid"] is False
    assert res["reason"] == "SUMS_MISMATCH"


def test_identity_forgery_refused(tmp_path: Path) -> None:
    snapshot, _app, _before = _installed_snapshot(tmp_path, tamper="identity-forgery")
    res = packaging.verify_snapshot_integrity(snapshot)
    assert res["valid"] is False
    assert res["reason"] == "IDENTITY_MISMATCH"


# ---------------------------------------------------------------------------
# RollbackEngine — verdict mapping + byte-unchanged live tree
# ---------------------------------------------------------------------------
def test_restore_refusal_keeps_live_tree_byte_identical(tmp_path: Path) -> None:
    snapshot, app_root, _before = _installed_snapshot(tmp_path, tamper="corrupt-exe")
    state_before = _tree_state(app_root)
    rb = RollbackEngine(app_root=app_root, backup_dir=snapshot)
    res = rb.restore_application(reason="probe")
    assert res["restored"] is False
    assert res["error_code"] == ERROR_SNAPSHOT_INTEGRITY_FAILED
    assert res["restored_items"] == 0
    assert _tree_state(app_root) == state_before


def test_restore_missing_manifest_verdict(tmp_path: Path) -> None:
    snapshot, app_root, _before = _installed_snapshot(tmp_path)
    (snapshot / "release-manifest.json").unlink()
    rb = RollbackEngine(app_root=app_root, backup_dir=snapshot)
    res = rb.restore_application(reason="probe")
    assert res["restored"] is False
    assert res["error_code"] == ERROR_SNAPSHOT_NO_MANIFEST


def test_no_partial_restore_on_last_file_tamper(tmp_path: Path) -> None:
    """Tampering ONLY the last-iterated snapshot file must still refuse the
    whole restore (verification completes before the first move)."""
    snapshot, app_root, _before = _installed_snapshot(tmp_path)
    (snapshot / "configs" / "base.yaml").write_text("evil: true\n", encoding="utf-8")
    state_before = _tree_state(app_root)
    rb = RollbackEngine(app_root=app_root, backup_dir=snapshot)
    res = rb.restore_application(reason="probe")
    assert res["restored"] is False
    assert res["error_code"] == ERROR_SNAPSHOT_INTEGRITY_FAILED
    assert _tree_state(app_root) == state_before
    # the snapshot itself is also untouched (pure-read gate)
    assert (snapshot / "configs" / "base.yaml").read_text(encoding="utf-8") == "evil: true\n"


def test_valid_production_shaped_snapshot_restores(tmp_path: Path) -> None:
    snapshot, app_root, _before = _installed_snapshot(tmp_path)
    rb = RollbackEngine(app_root=app_root, backup_dir=snapshot)
    res = rb.restore_application(reason="probe")
    assert res["restored"] is True
    assert res["integrity_verified_files"] >= 2
    assert (app_root / "NexusScalpEngine.exe").read_bytes() == b"MZ-PAYLOAD-EXE"


# ---------------------------------------------------------------------------
# Orchestrator wiring — a refusal is never reported as a completed rollback
# ---------------------------------------------------------------------------
def _orchestrator(tmp_path: Path) -> UpdateOrchestrator:
    app = tmp_path / "app"
    app.mkdir()
    (app / "NexusScalpEngine.exe").write_bytes(b"MZ-CURRENT")
    return UpdateOrchestrator(
        app_root=app,
        user_root=tmp_path / "user",
        update_home=tmp_path / "home",
        installed_version="9.1.0",
    )


def test_orchestrator_rollback_refuses_tampered_snapshot(tmp_path: Path) -> None:
    orch = _orchestrator(tmp_path)
    snapshot = orch.app_root / ".previous-1"
    snapshot.mkdir()
    (snapshot / "NexusScalpEngine.exe").write_bytes(b"MZ-PREV")
    seed_release_contract(snapshot)
    (snapshot / "NexusScalpEngine.exe").write_bytes(b"MZ-EVIL")
    live_before = _tree_state(orch.app_root)
    report = orch.rollback(reason="probe")
    assert report["state"] == STATE_FAILED_SAFE
    assert report["restored"] is False
    assert report["error_code"] == ERROR_SNAPSHOT_INTEGRITY_FAILED
    assert (orch.app_root / "NexusScalpEngine.exe").read_bytes() == b"MZ-CURRENT"
    assert (snapshot / "NexusScalpEngine.exe").read_bytes() == b"MZ-EVIL"
    rows = orch.history_store.list()
    assert rows[-1]["result"] == "ROLLBACK_REFUSED"
    # the live tree never received the refused snapshot's bytes
    assert (
        _tree_state(orch.app_root)
        == {k: v for k, v in live_before.items() if k != "release-manifest.json"}
        or _tree_state(orch.app_root) == live_before
    )


def test_orchestrator_rollback_restores_verified_snapshot(tmp_path: Path) -> None:
    from nexus_scalp.release.update_engine.constants import STATE_ROLLED_BACK

    orch = _orchestrator(tmp_path)
    snapshot = orch.app_root / ".previous-1"
    snapshot.mkdir()
    (snapshot / "NexusScalpEngine.exe").write_bytes(b"MZ-PREV")
    seed_release_contract(snapshot)
    report = orch.rollback(reason="probe")
    assert report["state"] == STATE_ROLLED_BACK
    assert report["restored"] is True
    assert (orch.app_root / "NexusScalpEngine.exe").read_bytes() == b"MZ-PREV"


def test_manifest_records_reader_accepts_both_shapes() -> None:
    arts: Any = {"artifacts": [{"name": "x", "relative_path": "a/x", "sha256": "0" * 64}]}
    assert packaging.manifest_records(arts) == [{"rel": "a/x", "sha256": "0" * 64}]
    files: Any = {"files": {"a/x": "0" * 64}}
    assert packaging.manifest_records(files) == [{"rel": "a/x", "sha256": "0" * 64}]
    assert packaging.manifest_records({"artifacts": "nope"}) is None
    assert packaging.manifest_records({}) is None
    assert packaging.manifest_records(None) is None


def test_escape_detector_cases() -> None:
    assert packaging.manifest_entry_escape_reason("../x") == "TRAVERSAL"
    assert packaging.manifest_entry_escape_reason("a/../../x") == "TRAVERSAL"
    assert packaging.manifest_entry_escape_reason("C:/Windows/x") == "ABSOLUTE_PATH"
    assert packaging.manifest_entry_escape_reason("/etc/passwd") == "ABSOLUTE_PATH"
    assert packaging.manifest_entry_escape_reason("portable/a/b.exe") is None
    assert packaging.manifest_entry_escape_reason("configs\\base.yaml") is None
