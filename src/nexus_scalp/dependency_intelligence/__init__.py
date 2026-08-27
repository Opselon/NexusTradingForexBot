"""NSE Dependency Intelligence subsystem.

Produces one canonical dependency graph (imports + DI + architecture +
runtime semantics) from real repository evidence, exposed via API, CLI, and
the /dependency developer dashboard.
"""

from __future__ import annotations

from nexus_scalp.dependency_intelligence.models import (
    CONFIDENCE_PROVEN,
    CONFIDENCE_STRONG,
    CONFIDENCE_SUPPORTED,
    CONFIDENCE_WEAK,
    Criticality,
    DependencyEdge,
    DependencyGraph,
    DependencyNode,
    EdgeKind,
    Evidence,
    Layer,
    NodeKind,
    ResolutionStatus,
)

ANALYZER_VERSION = "0.1.0"

__all__ = [
    "ANALYZER_VERSION",
    "CONFIDENCE_PROVEN",
    "CONFIDENCE_STRONG",
    "CONFIDENCE_SUPPORTED",
    "CONFIDENCE_WEAK",
    "Criticality",
    "DependencyEdge",
    "DependencyGraph",
    "DependencyNode",
    "EdgeKind",
    "Evidence",
    "Layer",
    "NodeKind",
    "ResolutionStatus",
]
