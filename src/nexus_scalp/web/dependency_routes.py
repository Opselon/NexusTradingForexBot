"""Dependency Intelligence — REST API routes for the NSE engineering dashboard.

Exposes the canonical dependency graph (imports + DI + architecture + runtime
semantics) produced by the :mod:`nexus_scalp.dependency_intelligence` engine.

Endpoints (all return real analysis results, never fabricated data):

  GET /api/dependency/summary
  GET /api/dependency/graph
  GET /api/dependency/node/{node_id}
  GET /api/dependency/path?source=&target=
  GET /api/dependency/impact?path=
  GET /api/dependency/cycles
  GET /api/dependency/violations
  GET /api/dependency/metrics
  GET /api/dependency/health

The analysis is computed lazily and cached in ``app.state`` so repeated calls
do not re-parse the repository (only the first call after a process start
scans). A ``?refresh=1`` query forces a re-scan.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from nexus_scalp.dependency_intelligence.analysis import (
    GraphAnalyzer,
    analyze_graph,
)
from nexus_scalp.dependency_intelligence.engine import (
    AnalysisResult,
    DependencyIntelligenceEngine,
)

router = APIRouter(prefix="/api/dependency", tags=["dependency"])

# Process-local cache for the (expensive) AST scan.
_cache: dict[str, Any] = {"result": None, "lock": threading.Lock()}


def _get_analysis(req: Request) -> AnalysisResult:
    """Return a cached analysis, scanning once per process unless refresh."""
    state = getattr(req.app.state, "dependency_analysis", None)
    refresh = str(req.query_params.get("refresh", "")) == "1"
    if state is None or refresh:
        root = Path("src/nexus_scalp")
        engine = DependencyIntelligenceEngine(root)
        result = engine.analyze(use_cache=True)
        req.app.state.dependency_analysis = result
        return result
    return state


def _serialize_node(node: Any) -> dict[str, Any]:
    return node.to_dict()


@router.get("/summary")
def dependency_summary(req: Request) -> dict[str, Any]:
    result = _get_analysis(req)
    graph = result.graph
    from nexus_scalp.dependency_intelligence.analysis import analyze_graph as _ag

    an = _ag(graph)
    summary = an["summary"]
    hotspots = an["hotspots"][:10]
    return {
        "status": "ok",
        "analyzer_version": graph.analyzer_version,
        "generated_at": graph.generated_at,
        "repository": {
            "files_analyzed": result.stats.files_analyzed,
            "modules": summary["nodes"] - _count_non_module(graph),
            "nodes": summary["nodes"],
            "edges": summary["edges"],
            "di_registrations": summary["di_registrations"],
        },
        "health": {
            "cycles": summary["cycles"],
            "unresolved_imports": summary["unresolved_imports"],
            "unresolved_di_bindings": _count_unresolved_di(graph),
            "architecture_violations": summary["violations"],
        },
        "hotspots": hotspots,
        "scan_duration_ms": result.stats.duration_ms,
    }


def _count_non_module(graph: Any) -> int:
    return sum(1 for n in graph.nodes.values() if n.kind.value != "MODULE")


def _count_unresolved_di(graph: Any) -> int:
    return sum(
        1 for n in graph.nodes.values() if n.status.value == "UNRESOLVED"
    )


@router.get("/graph")
def dependency_graph(req: Request) -> dict[str, Any]:
    result = _get_analysis(req)
    return result.graph.to_dict()


@router.get("/node/{node_id:path}")
def dependency_node(node_id: str, req: Request) -> dict[str, Any]:
    result = _get_analysis(req)
    graph = result.graph
    # Accept raw id (with mod:/cls: prefix) or qualified name.
    node = graph.nodes.get(node_id)
    if node is None:
        # try by qualified name
        for n in graph.nodes.values():
            if n.qualified_name == node_id:
                node = n
                break
    if node is None:
        raise HTTPException(status_code=404, detail=f"node not found: {node_id}")
    analyzer = GraphAnalyzer(graph)
    metrics = analyzer.compute_metrics().get(node.id, None)
    # dependents (who depends on this node)
    dependents = [
        e.source for e in graph.edges
        if e.target == node.id and _is_dep_edge(e.kind.value)
    ]
    dependencies = [
        e.target for e in graph.edges
        if e.source == node.id and _is_dep_edge(e.kind.value)
    ]
    return {
        "node": node.to_dict(),
        "metrics": metrics.__dict__ if metrics else None,
        "dependencies": dependencies,
        "dependents": dependents,
        "incident_edges": [
            e.to_dict() for e in graph.edges
            if e.source == node.id or e.target == node.id
        ],
    }


def _is_dep_edge(kind: str) -> bool:
    return kind in {
        "IMPORT", "INHERITS", "IMPLEMENTS", "INJECTS", "CONSTRUCTS",
        "CALLS", "USES", "RESOLVES", "REGISTERS", "FACTORY_CREATES",
        "CONFIG_DEPENDS_ON", "CONSUMES",
    }


@router.get("/path")
def dependency_path(source: str, target: str, req: Request) -> dict[str, Any]:
    result = _get_analysis(req)
    analyzer = GraphAnalyzer(result.graph)
    return analyzer.shortest_path(source, target)


@router.get("/impact")
def dependency_impact(path: str, req: Request) -> dict[str, Any]:
    result = _get_analysis(req)
    analyzer = GraphAnalyzer(result.graph)
    return analyzer.impact(path)


@router.get("/cycles")
def dependency_cycles(req: Request) -> dict[str, Any]:
    result = _get_analysis(req)
    analyzer = GraphAnalyzer(result.graph)
    cycles = analyzer.detect_cycles()
    return {"status": "ok", "count": len(cycles), "cycles": [c.to_dict() for c in cycles]}


@router.get("/violations")
def dependency_violations(req: Request) -> dict[str, Any]:
    result = _get_analysis(req)
    analyzer = GraphAnalyzer(result.graph)
    violations = analyzer.validate_architecture()
    return {
        "status": "ok",
        "count": len(violations),
        "violations": [v.to_dict() for v in violations],
    }


@router.get("/metrics")
def dependency_metrics(req: Request) -> dict[str, Any]:
    result = _get_analysis(req)
    analyzer = GraphAnalyzer(result.graph)
    metrics = analyzer.compute_metrics()
    return {
        "status": "ok",
        "nodes": len(metrics),
        "metrics": {nid: m.__dict__ for nid, m in metrics.items()},
    }


@router.get("/health")
def dependency_health(req: Request) -> dict[str, Any]:
    result = _get_analysis(req)
    an = analyze_graph(result.graph)
    s = an["summary"]
    healthy = s["cycles"] == 0 and s["violations"] == 0
    return {
        "status": "ok" if healthy else "degraded",
        "analyzer_version": result.graph.analyzer_version,
        "generated_at": result.graph.generated_at,
        "nodes": s["nodes"],
        "edges": s["edges"],
        "cycles": s["cycles"],
        "violations": s["violations"],
        "unresolved_imports": s["unresolved_imports"],
        "di_registrations": s["di_registrations"],
    }
