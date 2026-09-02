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


def checksums_file(paths: list[Path], out: Path, *, base_dir: Path | None = None) -> Path:
    """Write SHA256SUMS.txt (path relative to base_dir)."""
    base_dir = base_dir or out.parent
    lines: list[str] = []
    for p in paths:
        try:
            rel = p.relative_to(base_dir).as_posix()
        except ValueError:
            rel = p.name
        lines.append(f"{sha256_file(p)}  {rel}")
    out.parent.mkdir(parents=True, exist_ok=True)
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


def _manifest_feature_schema(info: dict[str, Any]) -> str:
    """Canonical active feature schema id (registry-derived, brief 37).

    Precedence: stamped build-info feature_schema -> active schema id in
    the registry -> scalar fallback.  Never invent a schema id.
    """
    stamped = str(info.get("feature_schema") or "").strip()
    if stamped:
        try:
            from nexus_scalp.features.schema import FEATURE_SCHEMAS

            FEATURE_SCHEMAS.resolve(stamped)
            return stamped
        except Exception:
            pass
    from nexus_scalp.features.schema import ACTIVE_SCHEMA_ID

    return ACTIVE_SCHEMA_ID


def _manifest_feature_dimension(info: dict[str, Any]) -> int:
    """Registered dimension of the manifest feature schema (0 when unknown)."""
    sid = _manifest_feature_schema(info)
    try:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        return FEATURE_SCHEMAS.resolve(sid).dimension
    except Exception:
        return 0


def _manifest_supported_model_schemas() -> list[str]:
    """Every REGISTERED schema id — the set of model schemas this release
    can load without conversion (brief 37: scalp_v1..scalp_v4 all included)."""
    try:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        return sorted(s.schema_id for s in FEATURE_SCHEMAS.list_schemas())
    except Exception:
        return ["scalp_v1"]


def _manifest_db_schema_version(info: dict[str, Any]) -> int:
    """Highest expected schema version across managed DB domains.

    Falls back to the stamped build-info (db_schema_version) when the
    migration registry is not importable in the build environment.
    """
    try:
        from nexus_scalp.database.models import DatabaseDomain
        from nexus_scalp.database.registry import expected_version_for_domain

        return max(
            expected_version_for_domain(d)
            for d in (
                DatabaseDomain.AUDIT,
                DatabaseDomain.NEWS,
                DatabaseDomain.CANDLE_INTEL,
            )
        )
    except Exception:
        return int(info.get("db_schema_version") or 0)


def _manifest_required_migrations() -> list[str]:
    """All migration ids the release carries (ordered, brief 37)."""
    try:
        from nexus_scalp.database.models import DatabaseDomain
        from nexus_scalp.database.registry import all_migration_ids

        out: list[str] = []
        for d in (
            DatabaseDomain.AUDIT,
            DatabaseDomain.NEWS,
            DatabaseDomain.CANDLE_INTEL,
        ):
            out.extend(all_migration_ids(d))
        return out
    except Exception:
        return []


def _manifest_model_compatibility(info: dict[str, Any]) -> str:
    """Human-readable model compatibility line (never hardcoded)."""
    schemas = _manifest_supported_model_schemas()
    return " / ".join(f"{s} ({_schema_dim(s)}D)" for s in schemas) or "none"


def _schema_dim(schema_id: str) -> int:
    try:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        return FEATURE_SCHEMAS.resolve(schema_id).dimension
    except Exception:
        return 0


def generate_manifest(
    artifacts: list[Path],
    out: Path,
    *,
    channel: str = "stable",
    build_mode: str = "Release",
    installer_version: str = "1.0.0",
    base_dir: Path | None = None,
) -> Path:
    """Write release-manifest.json for a set of artifacts.

    Architecture/channel/version come from the canonical build identity
    (build-info.json when present, else runtime platform) so the manifest
    never disagrees with the packaged bundle.
    """
    info = get_version_info()
    base_dir = base_dir or out.parent
    # Prefer the STAMPED build-info.json at the release root (the canonical
    # build identity for this exact artifact set) over runtime introspection,
    # so the manifest never disagrees with the packaged bundle.
    stamped = base_dir / "portable" / "build-info.json"
    if not stamped.exists():
        stamped = base_dir / "build-info.json"
    if stamped.exists():
        try:
            info = {**info, **json.loads(stamped.read_text(encoding="utf-8"))}
        except Exception:
            pass
    manifest: dict[str, Any] = {
        "product": info["product"],
        "product_display": info["product_display"],
        "version": info.get("version") or get_version(),
        "git_commit": info.get("git_commit") or info.get("commit"),
        # CHG-0043: idempotent truth — emit the timestamp ONLY when recorded.
        # A datetime.now() fallback fabricated a fresh value per call (breaking
        # manifest idempotency) and lied about the actual build time.
        **({"build_timestamp": info["build_timestamp"]} if info.get("build_timestamp") else {}),
        "channel": channel or info.get("channel") or "stable",
        "platform": "windows",
        "architecture": info.get("architecture") or platform.machine(),
        "build_mode": build_mode or info.get("build_mode") or "Release",
        "python_compatibility": "3.11.x",
        # Schema coverage (TASK-9): feature_schema derives from the CANONICAL
        # registry (never hardcoded) — a future 70D release cannot silently
        # drift the manifest; web_bundle_version + supported_model_schemas +
        # db_schema_version + required_migrations make the manifest the
        # release contract (brief section 37).
        "feature_schema": _manifest_feature_schema(info),
        "feature_schema_dimension": _manifest_feature_dimension(info),
        "supported_model_schemas": _manifest_supported_model_schemas(),
        "web_bundle_version": str(info.get("web_bundle_version") or ""),
        "db_schema_version": _manifest_db_schema_version(info),
        "required_migrations": _manifest_required_migrations(),
        "model_compatibility": _manifest_model_compatibility(info),
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
                "relative_path": a.relative_to(base_dir).as_posix() if base_dir else a.name,
                "size_bytes": a.stat().st_size,
                "sha256": sha256_file(a),
            }
            for a in artifacts
            if a.exists()
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
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


def generate_sbom(
    dependencies: dict[str, str] | None = None, out: Path | None = None
) -> dict[str, Any]:
    """SPDX-lite SBOM (dependency inventory). Not a security guarantee."""
    deps = dependencies or _installed_versions()
    sbom: dict[str, Any] = {
        "bomFormat": "SPDX",
        "spdxVersion": "SPDX-2.3",
        "name": f"nexus-scalp-engine-{get_version()}",
        "created": datetime.now(UTC).isoformat(),
        "packages": [{"name": name, "versionInfo": ver} for name, ver in sorted(deps.items())],
    }
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    return sbom


def _installed_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for mod in (
        "pydantic",
        "pydantic_settings",
        "yaml",
        "structlog",
        "typer",
        "rich",
        "polars",
        "pyarrow",
        "numpy",
        "torch",
        "fastapi",
        "uvicorn",
        "httpx",
        "pytest",
        "ruff",
        "mypy",
    ):
        try:
            m = __import__(mod)
            out[mod] = str(getattr(m, "__version__", "unknown"))
        except Exception:
            continue
    return out
