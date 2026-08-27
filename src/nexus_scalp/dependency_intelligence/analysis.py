"""Analysis layer over the canonical dependency graph.

Computes (all from real graph evidence):

* node metrics: fan_in / fan_out / instability / centrality / cycle membership
* cycle detection (import + DI + mixed) via networkx
* architecture-rule validation (configurable layer constraints)
* impact analysis: direct / transitive / test / api / runtime impact of a change
* hotspots: composite diagnostics ranked by an explicit, explainable formula

No fabricated values: every metric traces back to edges in the canonical graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from nexus_scalp.dependency_intelligence.models import (
    Criticality,
    DependencyGraph,
    EdgeKind,
    Layer,
    NodeKind,
    ResolutionStatus,
)

# Edge kinds that represent a *dependency* (source depends on target).
_DEP_KINDS = {
    EdgeKind.IMPORT, EdgeKind.INHERITS, EdgeKind.IMPLEMENTS, EdgeKind.INJECTS,
    EdgeKind.CONSTRUCTS, EdgeKind.CALLS, EdgeKind.USES, EdgeKind.RESOLVES,
    EdgeKind.REGISTERS, EdgeKind.FACTORY_CREATES, EdgeKind.CONFIG_DEPENDS_ON,
    EdgeKind.CONSUMES, EdgeKind.EVENT_PUBLISHES, EdgeKind.EVENT_CONSUMES,
    EdgeKind.TESTS,
}

# Edge kinds that represent an *exposure* (source provides to target) — excluded
# from fan-out "depends-on" but included in fan-in as "depended on by".
_EXPOSE_KINDS = {EdgeKind.EXPOSES}


# -------------------------------------------------------------------------
# Architecture rules (configurable, evidence-based — not naming guesses)
# -------------------------------------------------------------------------

@dataclass
class LayerRule:
    layer: str
    can_depend_on: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)


DEFAULT_LAYER_RULES: list[LayerRule] = [
    LayerRule(Layer.PRESENTATION.value, can_depend_on=[
        Layer.APPLICATION.value, Layer.DOMAIN.value, Layer.PORTS.value,
        Layer.INFRASTRUCTURE.value,
    ]),
    LayerRule(Layer.APPLICATION.value, can_depend_on=[
        Layer.DOMAIN.value, Layer.PORTS.value, Layer.INFRASTRUCTURE.value,
        Layer.APPLICATION.value,
    ]),
    LayerRule(Layer.DOMAIN.value, can_depend_on=[Layer.PORTS.value]),
    LayerRule(Layer.PORTS.value, can_depend_on=[]),
    LayerRule(Layer.INFRASTRUCTURE.value, can_depend_on=[
        Layer.DOMAIN.value, Layer.PORTS.value,
    ]),
    LayerRule(Layer.RUNTIME.value, can_depend_on=[
        Layer.APPLICATION.value, Layer.INFRASTRUCTURE.value, Layer.DOMAIN.value,
    ]),
    LayerRule(Layer.TOOLING.value, can_depend_on=[Layer.UNKNOWN.value]),
]


@dataclass
class Violation:
    severity: str
    source: str
    target: str
    rule: str
    evidence: dict[str, Any]
    explanation: str
    remediation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "source": self.source,
            "target": self.target,
            "rule": self.rule,
            "evidence": self.evidence,
            "explanation": self.explanation,
            "remediation": self.remediation,
        }


@dataclass
class CycleRecord:
    cycle_id: str
    severity: str
    path: list[str]
    edge_types: list[str]
    source_locations: list[str]
    impact: str
    recommended_breakpoint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "severity": self.severity,
            "path": self.path,
            "edge_types": self.edge_types,
            "source_locations": self.source_locations,
            "impact": self.impact,
            "recommended_breakpoint": self.recommended_breakpoint,
        }


@dataclass
class Metrics:
    fan_in: int = 0
    fan_out: int = 0
    instability: float = 0.0
    centrality: float = 0.0
    in_cycle: bool = False
    violations: int = 0
    unresolved_deps: int = 0


class GraphAnalyzer:
    def __init__(self, graph: DependencyGraph) -> None:
        self.graph = graph
        self._nx: nx.MultiDiGraph | None = None
        self._metrics: dict[str, Metrics] = {}

    # -- networkx build --------------------------------------------------

    def _build_nx(self) -> nx.MultiDiGraph:
        if self._nx is not None:
            return self._nx
        g = nx.MultiDiGraph()
        for nid, node in self.graph.nodes.items():
            g.add_node(nid, **node.to_dict())
        for e in self.graph.edges:
            g.add_edge(e.source, e.target, kind=e.kind.value,
                       confidence=e.confidence, evidence=e.evidence.to_dict())
        self._nx = g
        return g

    # -- metrics ---------------------------------------------------------

    def compute_metrics(self) -> dict[str, Metrics]:
        if self._metrics:
            return self._metrics
        g = self._build_nx()
        out: dict[str, Metrics] = {}
        n = len(g.nodes)
        for nid in g.nodes:
            fan_out = sum(1 for _ in g.successors(nid))
            fan_in = sum(1 for _ in g.predecessors(nid))
            # instability I = FanOut / (FanIn + FanOut); 0 when both zero
            if fan_in + fan_out > 0:
                instability = fan_out / (fan_in + fan_out)
            else:
                instability = 0.0
            out[nid] = Metrics(fan_in=fan_in, fan_out=fan_out,
                               instability=round(instability, 4))
        # centrality (degree-based; explicit + bounded)
        for nid, m in out.items():
            m.centrality = round((m.fan_in + m.fan_out) / max(1, 2 * (n - 1)), 6)
        self._metrics = out
        return out

    # -- cycles ----------------------------------------------------------

    def detect_cycles(self) -> list[CycleRecord]:
        g = self._build_nx()
        cycles: list[CycleRecord] = []
        seen: set[tuple[str, ...]] = set()
        for cyc in nx.simple_cycles(g):
            if len(cyc) < 2:
                continue
            key = tuple(sorted(cyc))
            if key in seen:
                continue
            seen.add(key)
            edge_types: list[str] = []
            locs: list[str] = []
            for i in range(len(cyc)):
                a = cyc[i]
                b = cyc[(i + 1) % len(cyc)]
                edata = g.get_edge_data(a, b) or {}
                for _kk, d in edata.items():
                    edge_types.append(d["kind"])
                    ev = d.get("evidence") or {}
                    if ev.get("file"):
                        locs.append(f"{ev['file']}:{ev.get('line', 0)}")
            sev = "CRITICAL" if any(k in {"INJECTS", "REGISTERS", "FACTORY_CREATES"} for k in edge_types) else "HIGH"
            cycles.append(CycleRecord(
                cycle_id=f"CYC-{len(cycles)+1:03d}",
                severity=sev,
                path=list(cyc),
                edge_types=sorted(set(edge_types)),
                source_locations=sorted(set(locs)),
                impact=(
                    "Circular dependency can block construction / cause import-time "
                    "failures or non-deterministic wiring."
                ),
                recommended_breakpoint=(
                    "Extract an abstraction (Protocol/ABC) or move orchestration "
                    "to a composition root to break the cycle at its weakest edge."
                ),
            ))
        # mark cycle membership
        members = {n for c in cycles for n in c.path}
        for nid, m in self.compute_metrics().items():
            m.in_cycle = nid in members
        return cycles

    # -- architecture validation ----------------------------------------

    def validate_architecture(self, rules: list[LayerRule] | None = None) -> list[Violation]:
        rules = rules or DEFAULT_LAYER_RULES
        rule_map = {r.layer: r for r in rules}
        violations: list[Violation] = []
        for e in self.graph.edges:
            s = self.graph.nodes.get(e.source)
            t = self.graph.nodes.get(e.target)
            if s is None or t is None:
                continue
            if e.kind not in _DEP_KINDS:
                continue
            s_layer = s.layer.value
            t_layer = t.layer.value
            if s_layer in (Layer.UNKNOWN.value, Layer.TEST.value) or t_layer in (
                Layer.UNKNOWN.value, Layer.TEST.value,
            ):
                continue
            rule = rule_map.get(s_layer)
            if rule is None:
                continue
            allowed = set(rule.can_depend_on) | {s_layer}
            if t_layer not in allowed or t_layer in rule.forbidden:
                violations.append(Violation(
                    severity="MEDIUM",
                    source=e.source,
                    target=e.target,
                    rule=f"layer:{s_layer}->{t_layer}",
                    evidence=e.evidence.to_dict(),
                    explanation=(
                        f"{s_layer} module depends on {t_layer} module via "
                        f"{e.kind.value} (not in allowed set)."
                    ),
                    remediation=(
                        "Introduce an abstraction in the ports/domain layer or "
                        "invert the dependency."
                    ),
                ))
        # annotate violation counts
        for nid, m in self.compute_metrics().items():
            m.violations = sum(1 for v in violations if v.source == nid)
        return violations

    # -- impact analysis -------------------------------------------------

    def impact(self, node_id: str, max_depth: int = 12) -> dict[str, Any]:
        g = self._build_nx()
        if node_id not in g.nodes:
            return {"error": "unknown_node", "node_id": node_id}
        # BFS over successors (what depends-on transitively breaks)
        direct: list[str] = []
        transitive: list[str] = []
        visited: set[str] = {node_id}
        frontier = [node_id]
        depth = 0
        while frontier and depth < max_depth:
            nxt: list[str] = []
            for u in frontier:
                for v in g.successors(u):
                    if v in visited:
                        continue
                    if depth == 0:
                        direct.append(v)
                    else:
                        transitive.append(v)
                    visited.add(v)
                    nxt.append(v)
            frontier = nxt
            depth += 1
        # classify impact kinds
        test_impact = [n for n in (direct + transitive)
                       if self.graph.nodes.get(n) and self.graph.nodes[n].kind == NodeKind.TEST]
        api_impact = [n for n in (direct + transitive)
                      if self.graph.nodes.get(n) and self.graph.nodes[n].kind == NodeKind.API_ENDPOINT]
        runtime_impact = [
            n for n in (direct + transitive)
            if self.graph.nodes.get(n)
            and self.graph.nodes[n].criticality
            in (Criticality.CRITICAL, Criticality.HIGH)
        ]
        kind = "HIGH_RISK" if (test_impact or runtime_impact) else "TRANSITIVE"
        return {
            "changed": node_id,
            "direct": direct,
            "transitive": transitive,
            "tests_likely_affected": test_impact,
            "api_impact": api_impact,
            "runtime_impact": runtime_impact,
            "impact_kind": kind,
        }

    # -- shortest path ---------------------------------------------------

    def shortest_path(self, source: str, target: str) -> dict[str, Any]:
        g = self._build_nx()
        if source not in g.nodes or target not in g.nodes:
            return {"error": "unknown_node", "source": source, "target": target}
        try:
            path = nx.shortest_path(g, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return {"found": False, "source": source, "target": target}
        edges_detail: list[dict[str, Any]] = []
        for i in range(len(path) - 1):
            a, b = path[i], path[i + 1]
            edata = g.get_edge_data(a, b) or {}
            kinds = [d["kind"] for _kk, d in edata.items()]
            edges_detail.append({"source": a, "target": b, "kinds": kinds})
        return {
            "found": True,
            "source": source,
            "target": target,
            "path": path,
            "edges": edges_detail,
        }

    # -- hotspots --------------------------------------------------------

    def hotspots(self, top_n: int = 20) -> list[dict[str, Any]]:
        metrics = self.compute_metrics()
        scored: list[tuple[str, float, list[str]]] = []
        for nid, m in metrics.items():
            node = self.graph.nodes.get(nid)
            if node is None:
                continue
            # External / stdlib leaves are not NSE-architecture hotspots.
            if node.kind == NodeKind.EXTERNAL:
                continue
            flags: list[str] = []
            score = 0.0
            if m.fan_in >= 8:
                flags.append("HIGH_FAN_IN")
                score += m.fan_in * 1.0
            if m.fan_out >= 8:
                flags.append("HIGH_FAN_OUT")
                score += m.fan_out * 0.3
            if m.in_cycle:
                flags.append("CYCLE")
                score += 15.0
            if m.violations > 0:
                flags.append("ARCHITECTURE_VIOLATION")
                score += m.violations * 5.0
            if node.status == ResolutionStatus.UNRESOLVED:
                flags.append("UNRESOLVED_DI")
                score += 20.0
            if node.criticality == Criticality.CRITICAL:
                flags.append("RUNTIME_CRITICAL")
                score += 10.0
            if flags:
                scored.append((nid, round(score, 2), flags))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            {"node_id": nid, "risk_score": s, "flags": flags,
             "fan_in": metrics[nid].fan_in, "fan_out": metrics[nid].fan_out,
             "instability": metrics[nid].instability,
             "criticality": self.graph.nodes[nid].criticality.value}
            for nid, s, flags in scored[:top_n]
        ]


def analyze_graph(graph: DependencyGraph) -> dict[str, Any]:
    """One-shot full analysis returning a serialisable dict."""
    a = GraphAnalyzer(graph)
    metrics = a.compute_metrics()
    cycles = a.detect_cycles()
    violations = a.validate_architecture()
    hotspots = a.hotspots()
    return {
        "metrics": {nid: m.__dict__ for nid, m in metrics.items()},
        "cycles": [c.to_dict() for c in cycles],
        "violations": [v.to_dict() for v in violations],
        "hotspots": hotspots,
        "summary": {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "cycles": len(cycles),
            "violations": len(violations),
            "unresolved_imports": sum(
                1 for n in graph.nodes.values()
                if n.id.startswith("unresolved:")
            ),
            "di_registrations": sum(
                1 for e in graph.edges if e.kind.value == "REGISTERS"
            ),
        },
    }
