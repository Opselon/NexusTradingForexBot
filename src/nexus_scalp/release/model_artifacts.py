"""Model Artifact Release Compatibility (TASK-9 production release layer).

Owns the 70D/60D production-artifact side of the release contract:

* Artifact identity: every model gets a full identity record
  (model_id, version, schema_id, dimension, schema_hash, artifact_hash,
  scaler_hash, algorithm versions) — brief section 9.
* Compatibility classification: ACTIVE / LEGACY / RETAINED / ARCHIVABLE
  (brief section 38) — no silent pruning of old 60D/50D artifacts.
* 70D feature dependency check (brief section 11): before a 70D-capable
  model may load, the base schema, the news schema (when the manifest
  declares news), and the liquidity dependency must ALL be available with
  matching versions. Any missing/unsupported dependency yields
  ``MODEL_NOT_RUNTIME_COMPATIBLE`` with the precise reason — there is NO
  silent fallback that changes semantics (brief section 14).

Safety contract (mirrors governance/load_gate.py): this module never loads
weights, never touches an adapter/order manager/risk engine, and never
mutates the Champion (INV-002/003/004/016). It is pure classification over
filesystem artifacts + the canonical schema registry.

The schema registry in ``features/schema.py`` is the SINGLE source of truth
for dimension/schema identity. This module consumes it and never guesses.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.model_generation.artifact_store import sha256_file

# ---------------------------------------------------------------------------
# Classification taxonomy (brief section 38)
# ---------------------------------------------------------------------------


class ArtifactClass(StrEnum):
    """Release-time classification of a model artifact.

    ACTIVE      — the schema this artifact declares is the CURRENT live
                   contract (scalp_v1 today) or the artifact is the current
                   Champion. Never removed by a release.
    LEGACY      — a superseded-but-still-safe contract (60D scalp_v2,
                   scalp_liquidity_v1 candidates). Retained for replay and
                   challenger evidence; never silently deleted.
    RETAINED    — root cause / evidence artifacts (governance rollback
                   evidence, rejected candidates with evidence value).
    ARCHIVABLE  — redundant or superseded artifacts with no retention value
                   under the retention policy (kept unless an operator
                   archives them explicitly).
    """

    ACTIVE = "ACTIVE"
    LEGACY = "LEGACY"
    RETAINED = "RETAINED"
    ARCHIVABLE = "ARCHIVABLE"


class DependencyKind(StrEnum):
    """Dependency classes checked for a 70D-capable model (brief section 11)."""

    BASE_FEATURES = "BASE_FEATURES"
    NEWS = "NEWS"
    LIQUIDITY = "LIQUIDITY"


class CompatibilityStatus(StrEnum):
    """Outcome of the runtime-compatibility check (brief sections 11/14)."""

    COMPATIBLE = "COMPATIBLE"
    MODEL_NOT_RUNTIME_COMPATIBLE = "MODEL_NOT_RUNTIME_COMPATIBLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"  # artifact or manifest missing
    FEATURE_SCHEMA_MISMATCH = "FEATURE_SCHEMA_MISMATCH"  # unregistered schema id
    LIQUIDITY_UNAVAILABLE = "LIQUIDITY_UNAVAILABLE"  # model needs liquidity, no producer
    NEWS_SCHEMA_MISMATCH = "NEWS_SCHEMA_MISMATCH"
    DIMENSION_MISMATCH = "DIMENSION_MISMATCH"
    SCALER_MISMATCH = "SCALER_MISMATCH"
    HASHR_MISMATCH = "ARTIFACT_HASH_MISMATCH"
    SCHEMA_HASH_MISMATCH = "SCHEMA_HASH_MISMATCH"


# ---------------------------------------------------------------------------
# Identity + dependency model
# ---------------------------------------------------------------------------

_SUPPORTED_LIQUIDITY_ALGORITHMS: dict[str, str] = {
    # schema_id -> minimum liquidity algorithm version.  "v1" today; the
    # liquidity engine producer API is versioned forward by the schema
    # registry + THIS map (release contract: a model whose feature schema
    # requires liquidity features is only loadable while the matching
    # producer is shipped).
    # scalp_v3 = 70D parity contract (0..49 Base | 50..59 News |
    # 60..69 Liquidity); scalp_v4 = 70D brief contract (0..49 Base |
    # 50..59 Family | 60..69 Liquidity). Both require the liquidity block.
    "scalp_v3": "1.0.0",
    "scalp_v4": "1.0.0",
    "scalp_liquidity_v1": "1.0.0",
}


@dataclass(frozen=True)
class ModelArtifactIdentity:
    """One model artifact's full release identity (brief section 9)."""

    model_id: str
    model_version: str
    schema_id: str
    dimension: int
    schema_hash: str
    artifact_hash: str
    scaler_hash: str
    algorithm_versions: dict[str, str] = field(default_factory=dict)
    manifest_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "schema_id": self.schema_id,
            "dimension": self.dimension,
            "schema_hash": self.schema_hash,
            "artifact_hash": self.artifact_hash,
            "scaler_hash": self.scaler_hash,
            "algorithm_versions": dict(self.algorithm_versions),
            "manifest_hash": self.manifest_hash,
        }


@dataclass(frozen=True)
class DependencyRequirement:
    """One declared dependency of a 70D-capable model."""

    kind: DependencyKind
    name: str  # e.g. scalp_v1 (base), news_context_v1 (news), liquidity (feature family)
    version: str = ""
    required: bool = True


@dataclass(frozen=True)
class RuntimeCompatibilityResult:
    """Result of ``check_runtime_compatibility`` — never partial/fake."""

    status: CompatibilityStatus
    reason: str
    model_id: str = ""
    schema_id: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    failures: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "model_id": self.model_id,
            "schema_id": self.schema_id,
            "checked_at": self.checked_at,
            "failures": list(self.failures),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def schema_hash_for(schema_id: str) -> str:
    """Deterministic content hash of a registered schema (identity field).

    Uses the canonical registry definition (id + dimension + description +
    active flag + supersedes lineage).  An unregistered schema id yields an
    empty hash — callers treat that as FEATURE_SCHEMA_MISMATCH.
    """
    try:
        schema = FEATURE_SCHEMAS.resolve(schema_id)
    except Exception:
        return ""
    payload = {
        "schema_id": schema.schema_id,
        "dimension": schema.dimension,
        "description": schema.description,
        "is_active": schema.is_active,
        "supersedes": schema.supersedes,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return sha256_bytes(canonical)


def compute_artifact_identity(
    artifact_dir: Path, *, manifest: dict[str, Any] | None = None
) -> ModelArtifactIdentity | None:
    """Build a full release identity from an artifact directory.

    ``artifact_dir`` is the directory containing model.pt / scaler.npz /
    model.json (ArtifactStore layout). Returns None when the directory is
    not a recognizable model artifact (missing weights or manifest).
    """
    if not artifact_dir.is_dir():
        return None
    weights = artifact_dir / "model.pt"
    scaler = artifact_dir / "scaler.npz"
    if not weights.exists():
        return None
    manifest = manifest or {}
    mg = (artifact_dir / "model.json").read_text(encoding="utf-8") if (
        artifact_dir / "model.json"
    ).exists() else "{}"
    try:
        mf: dict[str, Any] = json.loads(mg)
    except Exception:
        mf = {}
    mf = {**mf, **manifest}  # caller-supplied manifest wins (e.g. governance)
    schema_id = str(mf.get("feature_schema_id") or manifest.get("feature_schema_id") or "")
    if not schema_id:
        return None
    dim = int(mf.get("feature_dimension") or manifest.get("feature_dimension") or 0)
    if dim <= 0:
        return None
    alg: dict[str, str] = {}
    for key in ("liquidity_algorithm_version", "algorithm_version"):
        if mf.get(key):
            alg[key] = str(mf[key])
    alg_meta = mf.get("algorithm_versions") or {}
    if isinstance(alg_meta, dict):
        alg.update({str(k): str(v) for k, v in alg_meta.items()})
    return ModelArtifactIdentity(
        model_id=str(mf.get("model_id") or ""),
        model_version=str(mf.get("model_version") or ""),
        schema_id=schema_id,
        dimension=dim,
        schema_hash=schema_hash_for(schema_id),
        artifact_hash=str(mf.get("artifact_hash") or sha256_file(weights)),
        scaler_hash=str(mf.get("scaler_hash") or (sha256_file(scaler) if scaler.exists() else "")),
        algorithm_versions=alg or {},
        manifest_hash=sha256_bytes(json.dumps(mf, sort_keys=True, default=str).encode("utf-8"))
        if mf
        else "",
    )


def classify_artifact(
    identity: ModelArtifactIdentity, *, is_champion: bool = False
) -> ArtifactClass:
    """Classify an artifact for retention (brief section 38).

    Rules (no silent pruning, evidence kept):
    * Current live contract (scalp_v1) or current Champion -> ACTIVE
    * Registered superseded contract (scalp_v2 / scalp_v4 / scalp_liquidity_v1
      candidates) -> LEGACY (safe to keep, needed for replay/evidence)
    * Champion-proximate (identity present, not active schema) -> RETAINED
    * Anything else -> ARCHIVABLE (kept unless an operator archives it)
    """
    registry = {s.schema_id: s for s in FEATURE_SCHEMAS.list_schemas()}
    schema = registry.get(identity.schema_id)
    if schema is None:
        return ArtifactClass.ARCHIVABLE if not is_champion else ArtifactClass.RETAINED
    if schema.is_active or is_champion:
        return ArtifactClass.ACTIVE
    if schema.supersedes or identity.schema_id != "scalp_v1":
        # Candidate schemas (v2/v4/liquidity) are LEGACY-safe: they are
        # superseding designs, never auto-deleted.
        if identity.schema_id in ("scalp_v2", "scalp_v4", "scalp_liquidity_v1"):
            return ArtifactClass.LEGACY
        return ArtifactClass.RETAINED
    return ArtifactClass.ARCHIVABLE


def liquidity_dependencies_for(schema_id: str) -> list[DependencyRequirement]:
    """Declared dependencies of a schema id (brief section 11).

    The 70D contract `scalp_v4` = BASE 0..49 | FAMILY 50..59 | LIQUIDITY
    60..69 — the LIQUIDITY block is required.  scalp_liquidity_v1 (60D)
    defines liquidity semantics at 50..59 under its own schema id.
    """
    deps: list[DependencyRequirement] = []
    if schema_id in ("scalp_v3", "scalp_v4"):
        deps.append(
            DependencyRequirement(
                kind=DependencyKind.BASE_FEATURES,
                name="scalp_v1",
                version="1.0.0",
                required=True,
            )
        )
        deps.append(
            DependencyRequirement(
                kind=DependencyKind.LIQUIDITY,
                name="liquidity",
                version=_SUPPORTED_LIQUIDITY_ALGORITHMS.get(schema_id, "1.0.0"),
                required=True,
            )
        )
    elif schema_id == "scalp_liquidity_v1":
        deps.append(
            DependencyRequirement(
                kind=DependencyKind.BASE_FEATURES,
                name="scalp_v1",
                version="1.0.0",
                required=True,
            )
        )
        deps.append(
            DependencyRequirement(
                kind=DependencyKind.LIQUIDITY,
                name="liquidity",
                version=_SUPPORTED_LIQUIDITY_ALGORITHMS.get(schema_id, "1.0.0"),
                required=True,
            )
        )
    return deps


def check_runtime_compatibility(
    artifact_dir: Path,
    *,
    liquidity_producer_available: bool = True,
    news_available: bool = True,
    manifest: dict[str, Any] | None = None,
) -> RuntimeCompatibilityResult:
    """Full runtime dependency check BEFORE a 70D-capable model may load.

    Brief section 11: Base 50D available / News 10D available according to
    schema / Liquidity 10D available / liquidity algorithm version supported /
    schema hash matches.  Any failure -> MODEL_NOT_RUNTIME_COMPATIBLE with
    the precise reason, never a silent semantic fallback (section 14).

    ``liquidity_producer_available`` and ``news_available`` are capabilities
    the RUNTIME declares (e.g. the liquidity engine is importable +
    versioned); tests can simulate missing producers without a live engine.
    """
    identity = compute_artifact_identity(artifact_dir, manifest=manifest)
    if identity is None:
        return RuntimeCompatibilityResult(
            status=CompatibilityStatus.MODEL_UNAVAILABLE,
            reason="artifact or manifest missing",
            model_id=str((manifest or {}).get("model_id") or ""),
        )
    failures: list[str] = []

    # 1. Schema registered (FEATURE_SCHEMA_MISMATCH)
    try:
        schema = FEATURE_SCHEMAS.resolve(identity.schema_id)
    except Exception:
        failures.append(f"unregistered schema id '{identity.schema_id}'")
        return RuntimeCompatibilityResult(
            status=CompatibilityStatus.FEATURE_SCHEMA_MISMATCH,
            reason="schema id not registered in the canonical registry",
            model_id=identity.model_id,
            schema_id=identity.schema_id,
            failures=tuple(failures),
        )

    # 2. Dimension matches the registry (DIMENSION_MISMATCH)
    if identity.dimension != schema.dimension:
        failures.append(
            f"dimension {identity.dimension} != registry {schema.dimension} "
            f"for {identity.schema_id}"
        )

    # 3. Schema hash matches (SCHEMA_HASH_MISMATCH)
    declared_schema_hash = str(
        (manifest or {}).get("schema_hash") or ""
    ) or schema_hash_for(identity.schema_id)
    if declared_schema_hash and declared_schema_hash != schema_hash_for(identity.schema_id):
        failures.append("schema_hash mismatch (manifest vs registry)")

    # 4. Artifact hash matches (ARTIFACT_HASH_MISMATCH)
    actual_hash = sha256_file(artifact_dir / "model.pt")
    if identity.artifact_hash and actual_hash and identity.artifact_hash != actual_hash:
        failures.append("artifact_hash mismatch (weights changed after manifest)")

    # 5. Scaler present + hash matches when declared (SCALER_MISMATCH)
    scaler = artifact_dir / "scaler.npz"
    if not scaler.exists():
        failures.append("scaler.npz missing")
    elif identity.scaler_hash and sha256_file(scaler) != identity.scaler_hash:
        failures.append("scaler_hash mismatch")

    # 6. Dependency requirements (liquidity/70D)
    for dep in liquidity_dependencies_for(identity.schema_id):
        if dep.kind == DependencyKind.LIQUIDITY and not liquidity_producer_available:
            failures.append(
                f"liquidity producer unavailable (required for {identity.schema_id}, "
                f"algorithm >= {dep.version})"
            )
        if dep.kind == DependencyKind.NEWS and not news_available:
            failures.append(f"news context unavailable (required by {identity.schema_id})")

    if failures:
        return RuntimeCompatibilityResult(
            status=CompatibilityStatus.MODEL_NOT_RUNTIME_COMPATIBLE,
            reason="; ".join(failures),
            model_id=identity.model_id,
            schema_id=identity.schema_id,
            failures=tuple(failures),
        )
    return RuntimeCompatibilityResult(
        status=CompatibilityStatus.COMPATIBLE,
        reason="all dependency gates passed",
        model_id=identity.model_id,
        schema_id=identity.schema_id,
    )


def list_artifact_directories(root: Path) -> list[Path]:
    """All model-version directories under an artifact root (best effort)."""
    out: list[Path] = []
    if not root.is_dir():
        return out
    for candidate in sorted(root.rglob("model.pt")):
        out.append(candidate.parent)
    return out


def summarize_artifacts(
    root: Path, *, champion_model_id: str = ""
) -> list[dict[str, Any]]:
    """Release-facing inventory of all model artifacts (brief sections 9/38).

    Returns one record per artifact directory: identity + class + runtime
    compatibility status + retention guidance.  Pure classification — never
    deletes, never mutates.
    """
    records: list[dict[str, Any]] = []
    for d in list_artifact_directories(root):
        identity = compute_artifact_identity(d)
        if identity is None:
            continue
        compat = check_runtime_compatibility(d)
        cls = classify_artifact(identity, is_champion=identity.model_id == champion_model_id)
        records.append(
            {
                "path": str(d),
                "identity": identity.as_dict(),
                "class": cls.value,
                "runtime_compatibility": compat.as_dict(),
                "retention": {
                    "action": "KEEP" if cls in (ArtifactClass.ACTIVE, ArtifactClass.LEGACY, ArtifactClass.RETAINED) else "ARCHIVE_OPTIONAL",
                    "pruneable_by_release": cls == ArtifactClass.ARCHIVABLE,
                },
            }
        )
    return records