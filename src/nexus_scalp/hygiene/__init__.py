"""
TASK-11 Database Hygiene Worker — safety contract (no-op until implemented).

The hygiene worker's FULL safety contract lives in
`docs/DATABASE_HYGIENE.md`. This module defines the TIER constants used by
the worker and the registry of databases the worker manages.
"""

from __future__ import annotations

from enum import StrEnum


class DataTier(StrEnum):
    """Spec §3 data classification tiers."""

    TIER_0_BROKER_TRUTH = "TIER-0"
    TIER_1_CANONICAL_AUDIT = "TIER-1"
    TIER_2_RESEARCH_EVIDENCE = "TIER-2"
    TIER_3_MODEL_METADATA = "TIER-3"
    TIER_4_NEWS_INTELLIGENCE = "TIER-4"
    TIER_5_DERIVED_ANALYTICS = "TIER-5"
    TIER_6_CACHE = "TIER-6"
    TIER_7_TEMPORARY_STATE = "TIER-7"
    TIER_8_LEGACY_ARTIFACT = "TIER-8"


class Confidence(StrEnum):
    """Spec §7 duplicate/cleanup identity confidence."""

    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    LIKELY_DUPLICATE = "LIKELY_DUPLICATE"
    NOT_DUPLICATE = "NOT_DUPLICATE"
    UNKNOWN = "UNKNOWN"


class WorkerMode(StrEnum):
    """Spec §2 worker modes. Production default SAFE_CLEAN; first-run AUDIT_ONLY."""

    AUDIT_ONLY = "AUDIT_ONLY"
    DRY_RUN = "DRY_RUN"
    SAFE_CLEAN = "SAFE_CLEAN"
    AGGRESSIVE_CLEAN = "AGGRESSIVE_CLEAN"


class WorkerState(StrEnum):
    """Spec §51 worker state."""

    DISABLED = "DISABLED"
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    PLANNING = "PLANNING"
    CLEANING = "CLEANING"
    VERIFYING = "VERIFYING"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    DEGRADED = "DEGRADED"


class OrphanClass(StrEnum):
    """Spec §9 orphan classification."""

    EXPECTED_ORPHAN = "EXPECTED_ORPHAN"
    RECOVERABLE = "RECOVERABLE"
    REBUILDABLE = "REBUILDABLE"
    CORRUPTION = "CORRUPTION"
    UNKNOWN = "UNKNOWN"
