"""P0-E lineage tagging — dataset-side (MLFIX-T7).

Adds label_origin/source_classification onto research datasets derived from
the experience ledger. EXPERIENCE LEDGER INTERNALS ARE NOT MODIFIED — this is
a dataset-side wrapper only.

Data sources that can enter retraining:
    * CLEAN HISTORICAL LABELS  (offline triple-barrier over raw M1/M5 files)
    * PAPER-GENERATED LABELS   (paper-mode fills, incl. degenerate-model loops)
    * LIVE-GENERATED LABELS    (real broker fills)
    * SYNTHETIC                (replay/simulation)
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.model_generation.lineage import (
    CLEAN_ORIGINS,
    LabelOrigin,
    LineageGovernanceError,
    classify_source,
    manifest_is_production_eligible,
    requires_governance_override,
    stamp_manifest,
)


def ledger_origin(record: Any) -> LabelOrigin:
    """Deterministic origin classification for ONE ledger experience record.

    Reads only already-persisted fields. Unknown provenance is UNKNOWN (which
    the hard guard treats as tainted) — never fabricated as CLEAN.
    """
    exec_ctx = getattr(record, "execution", None)
    spread = getattr(exec_ctx, "spread_at_execution", None) if exec_ctx else None
    boot_source = getattr(record, "boot_account_source", "") or ""
    if str(boot_source).upper() == "PAPER":
        return LabelOrigin.PAPER_GENERATED
    if str(boot_source).upper() == "LIVE":
        return LabelOrigin.LIVE_GENERATED
    if spread is not None and float(spread) > 0:
        # A non-zero paper-spread marker is the strongest available signal
        # that this row was simulated (paper adapter always quotes a spread;
        # the live ledger may also carry one, so this is a HEURISTIC).
        return LabelOrigin.PAPER_GENERATED
    return LabelOrigin.UNKNOWN


def dataset_origin_from_records(records: list[Any]) -> LabelOrigin:
    """Worst-case (most-tainted) origin across a sample set.

    A dataset is only as clean as its dirtiest sample: one paper-derived row
    makes the dataset PAPER_GENERATED for production purposes.
    """
    if not records:
        return LabelOrigin.UNKNOWN
    origins = [ledger_origin(r) for r in records]
    for tainted in (LabelOrigin.LIVE_GENERATED, LabelOrigin.PAPER_GENERATED, LabelOrigin.UNKNOWN):
        if tainted in origins:
            return tainted
    return LabelOrigin.CLEAN_HISTORICAL


def annotate_research_dataset(dataset: Any) -> Any:
    """Attaches label_origin/source classification to a ResearchDataset's
    provenance_extra via an immutable copy. Never mutates the dataset."""

    extra = dict(getattr(dataset, "provenance_extra", {}) or {})
    if "label_origin" in extra:
        return dataset  # already tagged; idempotent
    origin = dataset_origin_from_records(list(getattr(dataset, "samples", []) or []))
    extra["label_origin"] = origin.value
    extra["governance_override_required"] = bool(requires_governance_override(origin))
    return dataset.model_copy(update={"provenance_extra": extra})


__all__ = [
    "CLEAN_ORIGINS",
    "LabelOrigin",
    "LineageGovernanceError",
    "annotate_research_dataset",
    "classify_source",
    "dataset_origin_from_records",
    "ledger_origin",
    "manifest_is_production_eligible",
    "requires_governance_override",
    "stamp_manifest",
]
