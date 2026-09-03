"""MLFIX-T7: Training-data lineage — classification + production hard guard.

The context §15 lineage risk: online fine-tune labels come from paper/live
fills of a DEGENERATE model — self-fulfilling loop. A CLEAN historical dataset
must not be trainable from those rolling records without an explicit governance
override.

This module tags every dataset/retrain artifact with its label provenance and
blocks training a PRODUCTION-eligible candidate from tainted labels.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class LabelOrigin(StrEnum):
    CLEAN_HISTORICAL = "CLEAN_HISTORICAL"  # offline historical triple-barrier / verified imports
    PAPER_GENERATED = "PAPER_GENERATED"  # derived from paper-sim fills
    LIVE_GENERATED = "LIVE_GENERATED"  # derived from live broker fills
    SYNTHETIC = "SYNTHETIC"  # replay / simulation / augmentation
    UNKNOWN = "UNKNOWN"  # caller did not declare


# Allowlist for PRODUCTION-eligible training datasets (no override needed).
CLEAN_ORIGINS: frozenset[LabelOrigin] = frozenset(
    {LabelOrigin.CLEAN_HISTORICAL, LabelOrigin.SYNTHETIC}
)

# Origins that are NEVER production-eligible without an explicit operator
# governance token (CHG governing model promotion still applies on top).
TAINTED_ORIGINS: frozenset[LabelOrigin] = frozenset(
    {LabelOrigin.PAPER_GENERATED, LabelOrigin.LIVE_GENERATED, LabelOrigin.UNKNOWN}
)


def classify_source(
    *,
    is_paper: bool | None = None,
    is_live: bool | None = None,
    synthetic: bool | None = None,
    label_origin: str | LabelOrigin | None = None,
    is_degenerate_model_derived: bool | None = None,
) -> LabelOrigin:
    """Canonical source classifier — single place that names the lineage.

    Pass an already-known ``label_origin`` string directly when available
    (e.g. from a rolling buffer or a dataset manifest). The (is_paper/is_live)
    flags are kept as an affordance for LiveEngine-style call sites that only
    know their execution mode.

    Returns a LabelOrigin. UNKNOWN when the caller declares nothing.
    """
    if label_origin is not None:
        s = str(label_origin).strip().upper()
        for member in LabelOrigin:
            if member.value == s:
                return member
        # degenerate flag always upgrades to TAINTED regardless of spelling
        return LabelOrigin.UNKNOWN
    if synthetic:
        return LabelOrigin.SYNTHETIC
    if is_live:
        return LabelOrigin.LIVE_GENERATED
    if is_paper:
        return LabelOrigin.PAPER_GENERATED
    # Online path that knows it was derived from a degenerate / epsilon-init
    # model is always tainted even before execution mode is considered.
    if is_degenerate_model_derived:
        return LabelOrigin.PAPER_GENERATED
    # default when the caller claims no provenance: UNKNOWN (tainted)
    return LabelOrigin.UNKNOWN


def requires_governance_override(origin: LabelOrigin | str) -> bool:
    """True iff this origin needs an explicit operator token for CHAMPION.

    CLEAN_HISTORICAL and SYNTHETIC are safe. PAPER/LIVE/UNKNOWN are not.
    """
    if isinstance(origin, str):
        try:
            origin = LabelOrigin(origin.strip().upper())
        except ValueError:
            return True
    return origin in TAINTED_ORIGINS


def assert_production_eligible(
    origin: LabelOrigin | str,
    *,
    governance_override: bool = False,
) -> None:
    """Hard guard: dataset with tainted label origin cannot train a
    PRODUCTION-eligible candidate without an explicit governance_override.

    Used by the training/orchestrator path right before it would mint a
    candidate that could become CHALLENGER -> CHAMPION. Non-production,
    research-only runs should pass governance_override=False and still
    raise; those callers must route through a non-production role.

    Raises:
        LineageGovernanceError when the guard blocks.
    """
    if origin in (None, ""):
        origin = LabelOrigin.UNKNOWN
    if not requires_governance_override(origin):
        return
    if governance_override:
        return
    raise LineageGovernanceError(
        f"Lineage guard: label_origin={origin!s} is not production-eligible without "
        "governance_override=True (operator token). Use a CLEAN_HISTORICAL dataset for "
        "any CHAMPION-eligible run, or pass an explicit governance token."
    )


class LineageGovernanceError(RuntimeError):
    """Training blocked: tainted label lineage without operator override."""


def manifest_is_production_eligible(manifest: dict[str, Any]) -> tuple[bool, str]:
    """Inspect a persisted dataset manifest / training-dataset envelope.

    Looks for the canonical keys we stamp (and for the legacy key
    ``label_origin`` with fallback to ``source_classification``). Returns
    (eligible, reason). Invalid/missing manifests are ineligible.
    """
    origin_raw = (
        manifest.get("label_origin")
        or manifest.get("source_classification")
        or manifest.get("provenance_extra", {}).get("label_origin")
        or manifest.get("provenance_extra", {}).get("source_classification")
        or ""
    )
    if not origin_raw:
        # Legacy manifests without any lineage stamp are treated as UNKNOWN
        # and therefore NOT production-eligible until re-issued with a stamp.
        return False, "missing label_origin/source_classification (legacy manifest) -> UNKNOWN"
    try:
        ov = LabelOrigin(str(origin_raw).strip().upper())
    except ValueError:
        return False, f"unknown label_origin={origin_raw!r} -> UNKNOWN"
    if ov in TAINTED_ORIGINS:
        return False, f"label_origin={ov.value} requires governance_override"
    return True, f"label_origin={ov.value} is production-eligible"


def stamp_manifest(manifest: dict[str, Any], origin: LabelOrigin | str) -> dict[str, Any]:
    """Returns a shallow copy of *manifest* stamped with canonical lineage.

    Does not mutate the input. Stamp shape:

        label_origin: <LabelOrigin>
        label_origin_stamped_at: ISO-8601 UTC
        governance_override_required: bool
    """
    if isinstance(origin, str):
        origin = classify_source(label_origin=origin)
    out = dict(manifest)
    out["label_origin"] = origin.value
    out["label_origin_stamped_at"] = datetime.now(UTC).isoformat()
    out["governance_override_required"] = bool(requires_governance_override(origin))
    return out
