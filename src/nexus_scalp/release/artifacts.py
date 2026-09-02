"""Canonical release-artifact path and naming helpers.

Single source of truth for WHERE release artifacts live and WHAT they are
called. The GitHub Actions release workflow, the manifest generator, the
verifier and any future build script MUST resolve names through this module
instead of re-deriving them with ad-hoc string formatting.

Naming model (all targets/platforms follow the same shape):

    release root:   release/v<version>/<platform>/<architecture>/
    portable zip:   NexusScalpEngine-<version>-<platform>-<arch>.zip
    installer:      NexusScalpEngine-<version>-<platform>-<arch>-setup.exe
    portable exe:   portable/NexusScalpEngine.exe
    cli exe:        cli/NexusScalpEngine-CLI.exe
    checksums:      checksums/SHA256SUMS.txt
    manifest:       manifests/release-manifest.json
    sbom:           sbom/sbom.spdx.json

Every helper is deterministic: the same (version, platform, architecture)
always yields the same paths, on any CI run, on any machine.
"""

from __future__ import annotations

from pathlib import Path

RELEASE_PRODUCT = "NexusScalpEngine"


def release_root(
    repo_root: Path | str, version: str, platform: str = "windows", arch: str = "x64"
) -> Path:
    """Canonical per-target release directory: release/v<version>/<platform>/<arch>."""
    v = str(version).lstrip("v")
    return Path(repo_root) / "release" / f"v{v}" / platform / arch


def portable_dir(release_root_path: Path) -> Path:
    return release_root_path / "portable"


def cli_dir(release_root_path: Path) -> Path:
    return release_root_path / "cli"


def checksums_dir(release_root_path: Path) -> Path:
    return release_root_path / "checksums"


def manifests_dir(release_root_path: Path) -> Path:
    return release_root_path / "manifests"


def sbom_dir(release_root_path: Path) -> Path:
    return release_root_path / "sbom"


def portable_zip_name(version: str, platform: str = "win", arch: str = "x64") -> str:
    return f"{RELEASE_PRODUCT}-{str(version).lstrip('v')}-{platform}-{arch}.zip"


def installer_name(version: str, platform: str = "win", arch: str = "x64") -> str:
    return f"{RELEASE_PRODUCT}-{str(version).lstrip('v')}-{platform}-{arch}-setup.exe"


def portable_zip_path(
    release_root_path: Path, version: str, platform: str = "win", arch: str = "x64"
) -> Path:
    return release_root_path / portable_zip_name(version, platform, arch)


def installer_path(
    release_root_path: Path, version: str, platform: str = "win", arch: str = "x64"
) -> Path:
    return release_root_path / installer_name(version, platform, arch)


def release_manifest_path(release_root_path: Path) -> Path:
    return manifests_dir(release_root_path) / "release-manifest.json"


def embedded_manifest_path(release_root_path: Path) -> Path:
    """Manifest copy embedded INSIDE the portable bundle (shipped to users)."""
    return portable_dir(release_root_path) / "release-manifest.json"


def sha256_sums_path(release_root_path: Path) -> Path:
    return checksums_dir(release_root_path) / "SHA256SUMS.txt"


def sbom_path(release_root_path: Path) -> Path:
    return sbom_dir(release_root_path) / "sbom.spdx.json"


def manifest_artifacts_for(
    release_root_path: Path, version: str, platform: str = "win", arch: str = "x64"
) -> list[Path]:
    """The canonical, ordered artifact list a release manifest must cover."""
    root = release_root_path
    candidates = [
        portable_dir(root) / f"{RELEASE_PRODUCT}.exe",
        cli_dir(root) / f"{RELEASE_PRODUCT}-CLI.exe",
        portable_zip_path(root, version, platform, arch),
        installer_path(root, version, platform, arch),
    ]
    # Manifest lists only artifacts that actually exist; generate_manifest
    # itself also filters, but keeping the contract here makes the caller
    # self-documenting and testable.
    return [p for p in candidates if p.exists()]
