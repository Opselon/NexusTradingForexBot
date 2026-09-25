"""BUG-237 — `nexus update` FAILED: "artifact is not a valid zip" on the Inno
setup.exe payload.

Root cause: _verify_payload_manifest() opened EVERY downloaded artifact with
zipfile.ZipFile. The GitHub release publishes TWO payloads: the portable zip
and the Inno setup.exe. The updater preferred the setup.exe, so every
setup-install user hit BadZipFile -> UpdateBlockedError("artifact is not a
valid zip") right after a clean SHA-256 pass.

Fix (two independent layers, both pinned here):
  1. _verify_payload_manifest() skips the zip-manifest pass for -setup.exe
     payloads (the Inno installer carries SHA256SUMS.txt + release-manifest
     itself; its integrity gate is the artifact SHA-256).
  2. _select_asset() prefers the PORTABLE ZIP over the setup.exe — the zip
     installs headless on every mode and carries build-info.json +
     release-manifest.json for full verification.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from nexus_scalp.release import updater as upd
from nexus_scalp.release.updater import SafeDownloader, UpdatePlanBuilder


def _asset(name: str, size: int = 100) -> dict[str, object]:
    return {
        "name": name,
        "size": size,
        "browser_download_url": f"https://example.invalid/{name}",
    }


def _release(assets: list[dict[str, object]]) -> dict[str, object]:
    return {
        "tag_name": "v9.0.8",
        "target_commitish": "abc1234",
        "draft": False,
        "prerelease": False,
        "html_url": "https://example.invalid/rel",
        "assets": assets,
    }


# ---------------------------------------------------------------------------
# Layer 2: asset selection prefers the portable zip
# ---------------------------------------------------------------------------


def test_select_asset_prefers_portable_zip_over_setup_exe() -> None:
    b = UpdatePlanBuilder(installed_version="9.0.7")
    decisions: list[str] = []
    asset = b._select_asset(
        _release(
            [
                _asset("NexusScalpEngine-9.0.8-win-x64-setup.exe"),
                _asset("NexusScalpEngine-9.0.8-win-x64.zip"),
            ]
        ),
        decisions,
    )
    assert asset is not None
    assert str(asset["name"]).endswith(".zip")


def test_select_asset_falls_back_to_setup_exe_when_no_zip() -> None:
    b = UpdatePlanBuilder(installed_version="9.0.7")
    asset = b._select_asset(
        _release([_asset("NexusScalpEngine-9.0.8-win-x64-setup.exe")]),
        [],
    )
    assert asset is not None
    assert str(asset["name"]).endswith("-setup.exe")


# ---------------------------------------------------------------------------
# Layer 1: manifest verification is payload-type aware
# ---------------------------------------------------------------------------


def _orchestrator(tmp_path: Path) -> object:
    """Minimal orchestrator wired to a tmp update home (no engine/lock I/O)."""
    return upd.UpdateOrchestrator(
        update_home=tmp_path / "update",
        app_root=tmp_path / "app",
        user_root=tmp_path / "user",
    )


def test_verify_manifest_skips_setup_exe(tmp_path: Path) -> None:
    """A setup.exe (Inno payload) must NOT be zopen for the manifest check."""
    orch = _orchestrator(tmp_path)
    exe = tmp_path / "NexusScalpEngine-9.0.8-win-x64-setup.exe"
    exe.write_bytes(b"MZ" + b"\x00" * 64)  # non-zip bytes on purpose
    plan = {"artifact_name": "NexusScalpEngine-9.0.8-win-x64-setup.exe"}
    # Pre-fix this raised UpdateBlockedError("artifact is not a valid zip").
    orch._verify_payload_manifest(exe, plan)  # type: ignore[attr-defined]


def test_verify_manifest_zip_without_manifest_passes(tmp_path: Path) -> None:
    """A portable zip WITHOUT an embedded manifest keeps the base SHA gate."""
    orch = _orchestrator(tmp_path)
    zp = tmp_path / "NexusScalpEngine-9.0.8-win-x64.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("build-info.json", json.dumps({"version": "9.0.8"}))
    plan = {"artifact_name": "NexusScalpEngine-9.0.8-win-x64.zip"}
    orch._verify_payload_manifest(zp, plan)  # type: ignore[attr-defined]


def test_verify_manifest_zip_with_bad_manifest_blocks(tmp_path: Path) -> None:
    """A zip whose embedded manifest lists a WRONG hash still blocks (no weakening)."""
    orch = _orchestrator(tmp_path)
    zp = tmp_path / "NexusScalpEngine-9.0.8-win-x64.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("build-info.json", json.dumps({"version": "9.0.8"}))
        zf.writestr("app.exe", b"binary")
        # Manifest claiming a hash the staged app.exe does NOT have.
        zf.writestr(
            "release-manifest.json",
            json.dumps(
                {
                    "channel": "stable",
                    "files": [{"name": "app.exe", "sha256": "00" * 32, "size": 6}],
                }
            ),
        )
    plan = {"artifact_name": "NexusScalpEngine-9.0.8-win-x64.zip"}
    with pytest.raises(upd.UpdateBlockedError) as ei:
        orch._verify_payload_manifest(zp, plan)  # type: ignore[attr-defined]
    assert "manifest" in str(ei.value).lower()


def test_verify_manifest_zip_corrupt_zip_blocks(tmp_path: Path) -> None:
    """A genuinely corrupt zip payload still fails with the clear message."""
    orch = _orchestrator(tmp_path)
    bad = tmp_path / "NexusScalpEngine-9.0.8-win-x64.zip"
    bad.write_bytes(b"PK\x03\x04" + b"truncated-garbage" * 8)
    plan = {"artifact_name": "NexusScalpEngine-9.0.8-win-x64.zip"}
    with pytest.raises(upd.UpdateBlockedError) as ei:
        orch._verify_payload_manifest(bad, plan)  # type: ignore[attr-defined]
    assert "not a valid zip" in str(ei.value)
