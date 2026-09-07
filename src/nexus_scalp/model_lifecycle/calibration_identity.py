"""Calibration artifact identity binding (mission phase 7).

A calibration artifact calibrates ONE EXACT model artifact. This module adds
the missing binding check WITHOUT touching the fail-closed sizing behavior:

* ``artifact_matches_serving`` — compares the artifact_fingerprint recorded
  in the calibration provenance against the sha256[:16] of the artifact the
  RiskEngine is actually serving.
* ``resolve_serving_fingerprint`` — derives the serving fingerprint from the
  engine's configured ``model.model_artifact_path`` (the same path the
  RiskEngine's lazy calibrator default uses), so the check cannot drift
  between "what we calibrate" and "what we serve".

Contract: on mismatch (or unknown serving fingerprint) the calibrator must
be treated as NOT_CALIBRATED by the consumer — flat sizing, never a stale
model's curve silently applied to a new champion. The check lives next to
the calibrator so every consumer shares ONE definition of identity match.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from nexus_scalp.model_lifecycle.confidence_calibration import (
    CalibrationProvenance,
    ConfidenceCalibrator,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.calibration_identity")

#: How many hex chars of the sha256 the provenance carries (engine parity).
FINGERPRINT_WIDTH: int = 16

#: The canonical serving artifact — single definition, reused by RiskEngine's
#: lazy default and by the identity check (no divergent path literals).
SERVING_ARTIFACT_PATH: str = (
    "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
)
SERVING_CALIBRATION_PATH: str = (
    "artifacts/models/scalp/XAUUSD/70d_liquidity/confidence_calibration.json"
)


def sha256_file(path: str | Path, width: int = FINGERPRINT_WIDTH) -> str | None:
    """sha256 prefix of a file, or None when the file is missing/unreadable."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:width]
    except OSError:
        return None


def resolve_serving_fingerprint(
    artifact_path: str | Path = SERVING_ARTIFACT_PATH,
) -> str | None:
    """Fingerprint of the artifact the engine is (configured to be) serving."""
    return sha256_file(artifact_path)


def artifact_matches_serving(
    provenance: CalibrationProvenance | dict[str, Any] | None,
    serving_fingerprint: str | None = None,
    serving_artifact_path: str | Path = SERVING_ARTIFACT_PATH,
) -> bool:
    """True ONLY when the calibration's artifact fingerprint equals the
    fingerprint of the currently-serving artifact file.

    * provenance without a fingerprint -> False (NOT_RECORDED is not a match)
    * serving artifact missing/unreadable -> False (fail-closed)
    * explicit serving_fingerprint overrides the file read (tests / engines
      that already hold the verified hash).
    """
    if provenance is None:
        return False
    cal_fp = (
        provenance.get("artifact_fingerprint", "")
        if isinstance(provenance, dict)
        else getattr(provenance, "artifact_fingerprint", "")
    )
    cal_fp = str(cal_fp or "")
    if not cal_fp:
        return False
    fp = (
        serving_fingerprint
        if serving_fingerprint is not None
        else resolve_serving_fingerprint(serving_artifact_path)
    )
    if not fp:
        return False
    return cal_fp == fp


def load_bound_calibrator(
    calibration_path: str | Path = SERVING_CALIBRATION_PATH,
    serving_artifact_path: str | Path = SERVING_ARTIFACT_PATH,
    serving_fingerprint: str | None = None,
) -> ConfidenceCalibrator:
    """Loads the calibration artifact and ENFORCES the identity binding.

    Returns the loaded calibrator when: artifact parses, provenance complete,
    state != NOT_CALIBRATED, AND fingerprint matches the serving artifact.
    Anything else returns a NOT_CALIBRATED calibrator (flat sizing) —
    never raises, never silently reuses a foreign model's curve.

    ``serving_fingerprint`` lets an engine that already holds the verified
    artifact hash pass it in (skipping the file re-read); by default the
    fingerprint is derived from ``serving_artifact_path`` on disk.
    """
    calibrator = ConfidenceCalibrator.from_artifact(calibration_path)
    if calibrator.state == "NOT_CALIBRATED":
        return calibrator
    prov = calibrator._provenance
    if not artifact_matches_serving(
        prov,
        serving_fingerprint=serving_fingerprint,
        serving_artifact_path=serving_artifact_path,
    ):
        logger.warning(
            "[CALIBRATION] identity mismatch -> NOT_CALIBRATED",
            calibrated_fingerprint=(
                getattr(prov, "artifact_fingerprint", "") if prov else ""
            ),
            serving_fingerprint=(
                serving_fingerprint
                if serving_fingerprint is not None
                else resolve_serving_fingerprint(serving_artifact_path)
            ),
            artifact_path=str(serving_artifact_path),
        )
        return ConfidenceCalibrator()
    return calibrator


def observability_block(calibrator: ConfidenceCalibrator) -> dict[str, Any]:
    """Bounded, secret-free calibration evidence for APIs/doctor (phase 13)."""
    ident = calibrator.identity()
    prov = calibrator._provenance
    return {
        "calibration_status": ident.get("state", "NOT_CALIBRATED"),
        "required_sample_count": 30,
        "eligible_sample_count": ident.get("sample_count", 0),
        "calibration_model_version": ident.get("model_version", ""),
        "calibration_artifact_fingerprint": (
            getattr(prov, "artifact_fingerprint", "") if prov else ""
        ),
        "calibration_method": ident.get("method", "none"),
        "ece": ident.get("ece"),
        "brier": ident.get("brier"),
        "matches_serving": artifact_matches_serving(prov),
    }
