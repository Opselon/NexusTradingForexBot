"""Packaging helpers — release manifest, SHA-256 checksums, SBOM.

Used by the build scripts and by ``nexus verify-release`` to generate and
verify machine-readable release metadata, and by the CLI tests.
"""

from __future__ import annotations

import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .metadata import get_version, get_version_info


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk_size):
            h.update(block)
    return h.hexdigest()


def checksums_file(paths: list[Path], out: Path, *,
                   base_dir: Path | None = None) -> Path:
    """Write SHA256SUMS.txt (path relative to base_dir)."""
    base_dir = base_dir or out.parent
    lines: list[str] = []
    for p in paths:
        try:
            rel = p.relative_to(base_dir).as_posix()
        except ValueError:
            rel = p.name
        lines.append(f"{sha256_file(p)}  {rel}")
    out.write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    return out


def verify_checksums_file(sums_file: Path, base_dir: Path | None = None) -> dict[str, Any]:
    """Verify a SHA256SUMS.txt against its referenced files."""
    base_dir = base_dir or sums_file.parent
    results: list[dict[str, Any]] = []
    ok = True
    for line in sums_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            results.append({"line": line, "status": "MALFORMED"})
            ok = False
            continue
        expect, rel = parts
        f = (base_dir / rel).resolve()
        if not f.exists():
            results.append({"file": rel, "status": "MISSING"})
            ok = False
            continue
        actual = sha256_file(f)
        match = actual.lower() == expect.lower()
        results.append({"file": rel, "status": "OK" if match else "MISMATCH"})
        ok = ok and match
    return {"valid": ok, "files": results}


def generate_manifest(
    artifacts: list[Path],
    out: Path,
    *,
    channel: str = "stable",
    build_mode: str = "Release",
    installer_version: str = "1.0.0",
    base_dir: Path | None = None,
) -> Path:
    """Write release-manifest.json for a set of artifacts."""
    info = get_version_info()
    base_dir = base_dir or out.parent
    manifest: dict[str, Any] = {
        "product": info["product"],
        "product_display": info["product_display"],
        "version": info.get("version") or get_version(),
        "git_commit": info.get("commit"),
        "build_timestamp": info.get("build_timestamp")
        or datetime.now(UTC).isoformat(),
        "channel": channel,
        "platform": "windows",
        "architecture": info.get("architecture"),
        "build_mode": build_mode,
        "python_compatibility": "3.11.x",
        "feature_schema": info.get("feature_schema", "scalp_v1"),
        "model_compatibility": "scalp_v1 / 50D",
        "installer_version": installer_version,
        "build_environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "test_status": "not_run",
        "verification_status": "not_run",
        "artifacts": [
            {
                "name": a.name,
                "relative_path": a.relative_to(base_dir).as_posix()
                if base_dir else a.name,
                "size_bytes": a.stat().st_size,
                "sha256": sha256_file(a),
            }
            for a in artifacts
            if a.exists()
        ],
    }
    out.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return out


def verify_manifest(manifest_path: Path, base_dir: Path | None = None) -> dict[str, Any]:
    """Verify a manifest: every listed artifact exists and matches its hash."""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"valid": False, "error": str(e)}
    base_dir = base_dir or manifest_path.parent
    results: list[dict[str, Any]] = []
    ok = True
    for a in data.get("artifacts", []):
        rel = a.get("relative_path") or a.get("name")
        f = (base_dir / rel).resolve()
        if not f.exists():
            results.append({"name": rel, "status": "MISSING"})
            ok = False
            continue
        actual = sha256_file(f)
        match = actual.lower() == str(a.get("sha256", "")).lower()
        results.append({"name": rel, "status": "OK" if match else "MISMATCH"})
        ok = ok and match
    return {"valid": ok and bool(results), "files": results, "manifest": data}


def generate_sbom(dependencies: dict[str, str] | None = None, out: Path | None = None) -> dict[str, Any]:
    """SPDX-lite SBOM (dependency inventory). Not a security guarantee."""
    deps = dependencies or _installed_versions()
    sbom: dict[str, Any] = {
        "bomFormat": "SPDX",
        "spdxVersion": "SPDX-2.3",
        "name": f"nexus-scalp-engine-{get_version()}",
        "created": datetime.now(UTC).isoformat(),
        "packages": [
            {"name": name, "versionInfo": ver}
            for name, ver in sorted(deps.items())
        ],
    }
    if out is not None:
        out.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    return sbom


def _installed_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for mod in ("pydantic", "pydantic_settings", "yaml", "structlog", "typer",
                "rich", "polars", "pyarrow", "numpy", "torch", "fastapi",
                "uvicorn", "httpx", "pytest", "ruff", "mypy"):
        try:
            m = __import__(mod)
            out[mod] = str(getattr(m, "__version__", "unknown"))
        except Exception:
            continue
    return out