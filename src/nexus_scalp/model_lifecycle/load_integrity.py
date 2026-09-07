"""MODEL ARTIFACT LOAD INTEGRITY (P1 mission: verify BEFORE serving).

Independent, disk-truth verification of the exact artifact a serving loader
is about to load. The invariant:

    WHAT IS ON DISK  ==  WHAT THE MANIFEST SAYS  before  WHAT THE PROCESS SERVES

Integrity statuses (observability contract, P2):

    VERIFIED           manifest present, digest matches (trusted serving)
    LEGACY_UNVERIFIED  artifact present but carries NO integrity metadata
                       (pre-trust-chain bundle). Explicitly classified,
                       logged, and NEVER treated as verified. Eligible only
                       through the explicit legacy allowance below.
    MISSING_METADATA   bundle declares metadata that does not exist
    HASH_MISMATCH      on-disk bytes != manifest digest (fail closed)
    LOAD_REJECTED      unreadable/missing artifact

Classification policy for PRODUCTION serving:
    * VERIFIED          -> load
    * HASH_MISMATCH /
      MISSING_METADATA /
      LOAD_REJECTED     -> reject (fail closed, never cold-start silently)
    * LEGACY_UNVERIFIED -> allowed ONLY when the caller explicitly opts in
      (allow_legacy_unverified=True). The engine's default is NO opt-in:
      an unverified artifact cannot become the serving model silently.

Non-goals: this module never loads tensors (torch import stays in the
loader), never mutates files, and never logs secrets.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.load_integrity")


class ArtifactIntegrityStatus(StrEnum):
    """Observable integrity classification for every model load."""

    VERIFIED = "VERIFIED"
    LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"
    MISSING_METADATA = "MISSING_METADATA"
    HASH_MISMATCH = "HASH_MISMATCH"
    LOAD_REJECTED = "LOAD_REJECTED"


#: Manifest/sidecar files that may carry the weight-file digest, in priority
#: order. The FIRST record that declares a model digest is authoritative.
_INTEGRITY_SOURCES: tuple[str, ...] = (
    "manifest.json",  # trainer bundle manifest (model_sha256)
    "model.meta.json",  # per-artifact meta (model_sha256 when present)
)


@dataclass(frozen=True)
class IntegrityVerdict:
    """Result of verifying one artifact against its integrity metadata."""

    status: ArtifactIntegrityStatus
    reason: str
    artifact: str  # file NAME only (never absolute paths in logs)
    expected_sha256: str = ""
    actual_sha256: str = ""
    manifest_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "artifact": self.artifact,
            "expected_sha256": self.expected_sha256[:16],
            "actual_sha256": self.actual_sha256[:16],
            "manifest_version": self.manifest_version,
        }


class ArtifactIntegrityError(RuntimeError):
    """Raised when a NON-VERIFIED artifact may not be served (fail closed)."""

    def __init__(self, verdict: IntegrityVerdict) -> None:
        super().__init__(f"{verdict.status.value}: {verdict.reason} ({verdict.artifact})")
        self.verdict = verdict


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _declared_digest(model_path: Path) -> tuple[str, str, str] | None:
    """Returns (digest, source_name, manifest_version) or None when no
    integrity metadata declares a digest for this weight file."""
    for name in _INTEGRITY_SOURCES:
        p = model_path.parent / name
        if not p.exists():
            continue
        try:
            record = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        digest = str(record.get("model_sha256") or record.get("artifact_hash") or "")
        if digest:
            return digest.lower(), name, str(record.get("manifest_version") or "")
    return None


def verify_artifact_integrity(
    model_path: Path | str,
    *,
    allow_legacy_unverified: bool = False,
) -> IntegrityVerdict:
    """Verify the EXACT weight file against its integrity metadata.

    Classification (see module docstring). Raises ArtifactIntegrityError
    when the verdict is not servable under the requested policy — callers
    must surface the failure BEFORE weights become the serving model.
    """
    p = Path(model_path)
    artifact_name = p.name
    if not p.exists() or p.stat().st_size == 0:
        verdict = IntegrityVerdict(
            status=ArtifactIntegrityStatus.LOAD_REJECTED,
            reason="artifact missing or empty",
            artifact=artifact_name,
        )
        _log_verdict(verdict)
        raise ArtifactIntegrityError(verdict)

    actual = _sha256_file(p)
    declared = _declared_digest(p)
    if declared is None:
        verdict = IntegrityVerdict(
            status=ArtifactIntegrityStatus.LEGACY_UNVERIFIED,
            reason=(
                "no integrity metadata (manifest.json/model.meta.json digest) "
                "for this artifact — pre-trust-chain bundle, NOT verified"
            ),
            artifact=artifact_name,
            actual_sha256=actual,
        )
        _log_verdict(verdict)
        if allow_legacy_unverified:
            return verdict
        raise ArtifactIntegrityError(verdict)

    expected, source, manifest_version = declared
    if actual != expected:
        verdict = IntegrityVerdict(
            status=ArtifactIntegrityStatus.HASH_MISMATCH,
            reason=(
                f"on-disk sha256 {actual[:12]} != {source} digest {expected[:12]} "
                "(weights modified after manifest, corrupted, or swapped)"
            ),
            artifact=artifact_name,
            expected_sha256=expected,
            actual_sha256=actual,
            manifest_version=manifest_version,
        )
        _log_verdict(verdict)
        raise ArtifactIntegrityError(verdict)

    verdict = IntegrityVerdict(
        status=ArtifactIntegrityStatus.VERIFIED,
        reason=f"digest matches {source}",
        artifact=artifact_name,
        expected_sha256=expected,
        actual_sha256=actual,
        manifest_version=manifest_version,
    )
    _log_verdict(verdict)
    return verdict


def _log_verdict(verdict: IntegrityVerdict) -> None:
    """Every model-load integrity outcome is observable (P2)."""
    d = verdict.as_dict()
    if verdict.status is ArtifactIntegrityStatus.VERIFIED:
        logger.info("[ARTIFACT_INTEGRITY] status=VERIFIED", **d)
    elif verdict.status is ArtifactIntegrityStatus.LEGACY_UNVERIFIED:
        logger.warning("[ARTIFACT_INTEGRITY] status=LEGACY_UNVERIFIED (not verified)", **d)
    else:
        logger.error(
            "[ARTIFACT_INTEGRITY] status=%s LOAD_REJECTED_CLASS" % verdict.status.value, **d
        )
