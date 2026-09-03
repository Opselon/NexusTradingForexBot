"""Canonical dependency-intelligence data model for NSE.

One normalized graph model. All views (API / HTML / JSON / CLI / metrics)
derive from this single source of truth. No separate import/DI/runtime models.

Every edge carries an evidence origin (file:line + reason) and a confidence
score so the UI can rank and explain relationships honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# -------------------------------------------------------------------------
# Node / Edge kinds
# -------------------------------------------------------------------------


class NodeKind(StrEnum):
    MODULE = "MODULE"
    PACKAGE = "PACKAGE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    INTERFACE = "INTERFACE"  # ABC / abstract base
    PROTOCOL = "PROTOCOL"  # typing.Protocol
    SERVICE = "SERVICE"
    REPOSITORY = "REPOSITORY"
    FACTORY = "FACTORY"
    CONTAINER = "CONTAINER"
    CONFIGURATION = "CONFIGURATION"
    EXTERNAL = "EXTERNAL"
    API_ENDPOINT = "API_ENDPOINT"
    WORKER = "WORKER"
    TEST = "TEST"


class EdgeKind(StrEnum):
    IMPORT = "IMPORT"
    INHERITS = "INHERITS"
    IMPLEMENTS = "IMPLEMENTS"
    CONSTRUCTS = "CONSTRUCTS"
    INJECTS = "INJECTS"
    CALLS = "CALLS"
    USES = "USES"
    RESOLVES = "RESOLVES"
    REGISTERS = "REGISTERS"
    FACTORY_CREATES = "FACTORY_CREATES"
    CONFIG_DEPENDS_ON = "CONFIG_DEPENDS_ON"
    EXPOSES = "EXPOSES"
    CONSUMES = "CONSUMES"
    EVENT_PUBLISHES = "EVENT_PUBLISHES"
    EVENT_CONSUMES = "EVENT_CONSUMES"
    TESTS = "TESTS"


class ResolutionStatus(StrEnum):
    """DI binding resolution outcome (only meaningful for DI edges)."""

    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    MULTIPLE_IMPLEMENTATIONS = "MULTIPLE_IMPLEMENTATIONS"
    STALE = "STALE"
    OPTIONAL = "OPTIONAL"
    FACTORY_RESOLVED = "FACTORY_RESOLVED"
    UNKNOWN = "UNKNOWN"


class Layer(StrEnum):
    PRESENTATION = "presentation"
    APPLICATION = "application"
    DOMAIN = "domain"
    PORTS = "ports"
    INFRASTRUCTURE = "infrastructure"
    RUNTIME = "runtime"
    TOOLING = "tooling"
    TEST = "test"
    UNKNOWN = "unknown"


class Criticality(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


# -------------------------------------------------------------------------
# Confidence semantics (explicit, not decoration)
#   1.00  directly proven by source syntax / explicit registration
#   0.90+ strong static inference
#   0.70+ supported inference
#   0.40+ weak inference
#   <0.40 heuristic / low confidence
# -------------------------------------------------------------------------

CONFIDENCE_PROVEN = 1.0
CONFIDENCE_STRONG = 0.9
CONFIDENCE_SUPPORTED = 0.7
CONFIDENCE_WEAK = 0.4


@dataclass
class Evidence:
    evidence_type: str = (
        "unknown"  # import | symbol | di_registration | constructor | factory | config | runtime
    )
    file: str = ""
    line: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_type": self.evidence_type,
            "file": self.file,
            "line": self.line,
            "reason": self.reason,
        }


@dataclass
class DependencyNode:
    id: str
    qualified_name: str
    display_name: str
    kind: NodeKind = NodeKind.MODULE
    module: str = ""
    package: str = ""
    file: str = ""
    layer: Layer = Layer.UNKNOWN
    status: ResolutionStatus = ResolutionStatus.UNKNOWN
    confidence: float = CONFIDENCE_PROVEN
    criticality: Criticality = Criticality.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "qualified_name": self.qualified_name,
            "display_name": self.display_name,
            "kind": self.kind.value,
            "module": self.module,
            "package": self.package,
            "file": self.file,
            "layer": self.layer.value,
            "status": self.status.value,
            "confidence": round(self.confidence, 3),
            "criticality": self.criticality.value,
            "metadata": self.metadata,
        }


@dataclass
class DependencyEdge:
    source: str
    target: str
    kind: EdgeKind
    confidence: float = CONFIDENCE_PROVEN
    resolution: ResolutionStatus = ResolutionStatus.UNKNOWN
    evidence: Evidence = field(default_factory=Evidence)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind.value,
            "confidence": round(self.confidence, 3),
            "resolution": self.resolution.value,
            "evidence": self.evidence.to_dict(),
            "metadata": self.metadata,
        }


@dataclass
class DependencyGraph:
    nodes: dict[str, DependencyNode] = field(default_factory=dict)
    edges: list[DependencyEdge] = field(default_factory=list)
    analyzer_version: str = "0.1.0"
    repository_root: str = ""
    generated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_node(self, node: DependencyNode) -> DependencyNode:
        existing = self.nodes.get(node.id)
        if existing is None:
            self.nodes[node.id] = node
        return self.nodes[node.id]

    def add_edge(self, edge: DependencyEdge) -> None:
        # De-duplicate identical source/target/kind/evidence edges.
        for e in self.edges:
            if (
                e.source == edge.source
                and e.target == edge.target
                and e.kind == edge.kind
                and e.evidence.file == edge.evidence.file
                and e.evidence.line == edge.evidence.line
            ):
                # Keep the higher-confidence evidence if duplicate.
                if edge.confidence > e.confidence:
                    e.confidence = edge.confidence
                    e.evidence = edge.evidence
                    e.resolution = edge.resolution
                    e.metadata.update(edge.metadata)
                return
        self.edges.append(edge)

    def to_dict(self) -> dict[str, Any]:
        return {
            "analyzer_version": self.analyzer_version,
            "repository_root": self.repository_root,
            "generated_at": self.generated_at,
            "metadata": self.metadata,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DependencyGraph:
        """Lossless inverse of to_dict() for the canonical node/edge fields.

        Unknown/extra keys are ignored (forward-compatible cache format);
        enum fields tolerate legacy raw values via NodeKind(value) fallthrough
        to the nearest safe default (MODULE / IMPORT / UNKNOWN).
        """
        graph = cls(
            analyzer_version=str(data.get("analyzer_version", "0.1.0")),
            repository_root=str(data.get("repository_root", "")),
            generated_at=str(data.get("generated_at", "")),
            metadata=dict(data.get("metadata", {})),
        )
        for nd in data.get("nodes", []):
            try:
                kind = NodeKind(nd.get("kind", "MODULE"))
            except ValueError:
                kind = NodeKind.MODULE
            try:
                layer = Layer(nd.get("layer", "unknown"))
            except ValueError:
                layer = Layer.UNKNOWN
            try:
                status = ResolutionStatus(nd.get("status", "UNKNOWN"))
            except ValueError:
                status = ResolutionStatus.UNKNOWN
            try:
                criticality = Criticality(nd.get("criticality", "UNKNOWN"))
            except ValueError:
                criticality = Criticality.UNKNOWN
            graph.add_node(
                DependencyNode(
                    id=str(nd.get("id", "")),
                    qualified_name=str(nd.get("qualified_name", "")),
                    display_name=str(nd.get("display_name", "")),
                    kind=kind,
                    module=str(nd.get("module", "")),
                    package=str(nd.get("package", "")),
                    file=str(nd.get("file", "")),
                    layer=layer,
                    status=status,
                    confidence=float(nd.get("confidence", CONFIDENCE_PROVEN)),
                    criticality=criticality,
                    metadata=dict(nd.get("metadata", {})),
                )
            )
        for ed in data.get("edges", []):
            try:
                edge_kind = EdgeKind(ed.get("kind", "IMPORT"))
            except ValueError:
                edge_kind = EdgeKind.IMPORT
            try:
                resolution = ResolutionStatus(ed.get("resolution", "UNKNOWN"))
            except ValueError:
                resolution = ResolutionStatus.UNKNOWN
            ev = ed.get("evidence", {}) or {}
            graph.add_edge(
                DependencyEdge(
                    source=str(ed.get("source", "")),
                    target=str(ed.get("target", "")),
                    kind=edge_kind,
                    confidence=float(ed.get("confidence", CONFIDENCE_PROVEN)),
                    resolution=resolution,
                    evidence=Evidence(
                        evidence_type=str(ev.get("evidence_type", "unknown")),
                        file=str(ev.get("file", "")),
                        line=int(ev.get("line", 0)),
                        reason=str(ev.get("reason", "")),
                    ),
                    metadata=dict(ed.get("metadata", {})),
                )
            )
        return graph
