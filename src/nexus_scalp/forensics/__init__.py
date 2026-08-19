"""Forensic monitoring package (TASK-11 POST-70D continuous protection)."""

from nexus_scalp.forensics.engine import ForensicHealthEngine
from nexus_scalp.forensics.models import (
    CheckResult,
    ForensicCheckError,
    HealthStatus,
    worst_status,
)
from nexus_scalp.forensics.references import (
    FEATURE_REFERENCES,
    FeatureReferenceRegistry,
    FeatureReferenceStats,
    compute_reference_stats,
)

__all__ = [
    "FEATURE_REFERENCES",
    "CheckResult",
    "FeatureReferenceRegistry",
    "FeatureReferenceStats",
    "ForensicCheckError",
    "ForensicHealthEngine",
    "HealthStatus",
    "compute_reference_stats",
    "worst_status",
]
