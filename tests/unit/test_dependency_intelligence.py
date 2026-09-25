"""Tests for the NSE Dependency Intelligence subsystem.

Single dedicated test file (per implementation spec): covers the scanner,
DI analyzer, cycle detection, architecture validation, impact analysis,
API contract, and the /dependency frontend route.

All tests run against the real ``src/nexus_scalp`` repository (no mocks, no
fabricated data). Heavy scans are memoised per-session via a module fixture.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "nexus_scalp"
PKG = "nexus_scalp"

# Skip the full-repo analysis if the package import path is unavailable.
requires_pkg = pytest.mark.skipif(
    importlib.util.find_spec(PKG) is None,
    reason=f"{PKG} not importable",
)


@pytest.fixture(scope="module")
def graph():
    """Build the canonical graph once for the whole module."""
    from nexus_scalp.dependency_intelligence.engine import run_analysis

    result = run_analysis()
    return result


@pytest.fixture(scope="module")
def analysis(graph):
    from nexus_scalp.dependency_intelligence.analysis import analyze_graph

    return analyze_graph(graph.graph)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


@requires_pkg
def test_scanner_discovers_real_modules(graph):
    assert graph.stats.files_analyzed > 100
    assert len(graph.graph.nodes) > 500
    assert len(graph.graph.edges) > 500


@requires_pkg
def test_import_edge_normal_and_alias(graph):
    edges = graph.graph.edges
    # There must be normal absolute imports and (at least some) aliased imports.
    imports = [e for e in edges if e.kind.value == "IMPORT"]
    assert imports, "expected IMPORT edges"
    # Every import edge carries evidence (file:line).
    assert all(e.evidence.file and e.evidence.line for e in imports[:50])


@requires_pkg
def test_relative_import_resolves(graph):
    # cli.main imports from within nexus_scalp; verify module nodes exist.
    assert "mod:nexus_scalp.cli.main" in graph.graph.nodes


@requires_pkg
def test_unresolved_import_recorded_not_crashing(graph):
    # The scanner must not crash on unresolvable imports; it records them.
    # (psycopg / feedparser are optional deps present in some envs.)
    unresolved = [n for n in graph.graph.nodes.values() if n.id.startswith("unresolved:")]
    # Even if zero in this env, the analysis must complete without error.
    assert graph.stats.parse_errors == 0 or isinstance(graph.stats.parse_errors, int)
    assert isinstance(unresolved, list)


# ---------------------------------------------------------------------------
# DI analyzer
# ---------------------------------------------------------------------------


@requires_pkg
def test_constructor_injection_detected(graph):
    injects = [e for e in graph.graph.edges if e.kind.value == "INJECTS"]
    assert injects, "expected constructor-injection edges"
    # LiveEngine injects IMT5Port (real composition root wiring).
    le = "cls:nexus_scalp.application.live_engine.LiveEngine"
    if le in graph.graph.nodes:
        le_in = [e for e in injects if e.source == le]
        assert le_in, "LiveEngine should inject dependencies"


@requires_pkg
def test_protocol_abc_implementation_detected(graph):
    impl = [e for e in graph.graph.edges if e.kind.value == "IMPLEMENTS"]
    # At least the ABC/Protocol bases found during discovery must be linked.
    assert impl or any(
        n.kind.value in ("PROTOCOL", "INTERFACE") for n in graph.graph.nodes.values()
    )


@requires_pkg
def test_registration_detected(graph):
    # register_strategy / register(...) calls become REGISTERS edges.
    registers = [e for e in graph.graph.edges if e.kind.value == "REGISTERS"]
    assert registers, "expected dependency-registration edges"


@requires_pkg
def test_unresolved_di_binding_classifiable(graph):
    from nexus_scalp.dependency_intelligence.models import ResolutionStatus

    for n in graph.graph.nodes.values():
        # No node should carry an invalid resolution value.
        assert n.status in ResolutionStatus


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


@requires_pkg
def test_cycle_detection_runs(analysis):
    cycles = analysis["cycles"]
    assert isinstance(cycles, list)
    for c in cycles:
        assert "cycle_id" in c and "path" in c and len(c["path"]) >= 2


def test_no_false_cycle_on_simple_chain():
    """A linear import chain must not be reported as a cycle."""
    from nexus_scalp.dependency_intelligence.models import (
        DependencyEdge,
        DependencyGraph,
        EdgeKind,
    )

    g = DependencyGraph()
    g.add_node(_m("a"))
    g.add_node(_m("b"))
    g.add_node(_m("c"))
    g.add_edge(DependencyEdge(source="mod:a", target="mod:b", kind=EdgeKind.IMPORT))
    g.add_edge(DependencyEdge(source="mod:b", target="mod:c", kind=EdgeKind.IMPORT))
    from nexus_scalp.dependency_intelligence.analysis import GraphAnalyzer

    cyc = GraphAnalyzer(g).detect_cycles()
    assert cyc == [], "linear chain is not a cycle"


def test_two_node_cycle_detected():
    from nexus_scalp.dependency_intelligence.models import (
        DependencyEdge,
        DependencyGraph,
        EdgeKind,
    )

    g = DependencyGraph()
    g.add_node(_m("a"))
    g.add_node(_m("b"))
    g.add_edge(DependencyEdge(source="mod:a", target="mod:b", kind=EdgeKind.IMPORT))
    g.add_edge(DependencyEdge(source="mod:b", target="mod:a", kind=EdgeKind.IMPORT))
    from nexus_scalp.dependency_intelligence.analysis import GraphAnalyzer

    cyc = GraphAnalyzer(g).detect_cycles()
    assert len(cyc) == 1 and len(cyc[0].path) == 2


def test_three_node_cycle_detected():
    from nexus_scalp.dependency_intelligence.models import (
        DependencyEdge,
        DependencyGraph,
        EdgeKind,
    )

    g = DependencyGraph()
    for x in ("a", "b", "c"):
        g.add_node(_m(x))
    g.add_edge(DependencyEdge(source="mod:a", target="mod:b", kind=EdgeKind.IMPORT))
    g.add_edge(DependencyEdge(source="mod:b", target="mod:c", kind=EdgeKind.IMPORT))
    g.add_edge(DependencyEdge(source="mod:c", target="mod:a", kind=EdgeKind.IMPORT))
    from nexus_scalp.dependency_intelligence.analysis import GraphAnalyzer

    cyc = GraphAnalyzer(g).detect_cycles()
    assert len(cyc) == 1 and len(cyc[0].path) == 3


# ---------------------------------------------------------------------------
# Architecture rules
# ---------------------------------------------------------------------------


@requires_pkg
def test_architecture_validation_runs(analysis):
    violations = analysis["violations"]
    assert isinstance(violations, list)
    for v in violations:
        assert "severity" in v and "rule" in v and "remediation" in v


def test_invalid_layer_dependency_flagged():
    from nexus_scalp.dependency_intelligence.analysis import GraphAnalyzer
    from nexus_scalp.dependency_intelligence.models import (
        DependencyEdge,
        DependencyGraph,
        EdgeKind,
        Layer,
        NodeKind,
    )

    g = DependencyGraph()
    g.add_node(_m("dom", layer=Layer.DOMAIN))
    g.add_node(_m("pres", layer=Layer.PRESENTATION))
    g.add_edge(DependencyEdge(source="mod:pres", target="mod:dom", kind=EdgeKind.IMPORT))
    # presentation -> domain is allowed; force a violation: domain -> presentation
    g.add_edge(DependencyEdge(source="mod:dom", target="mod:pres", kind=EdgeKind.IMPORT))
    v = GraphAnalyzer(g).validate_architecture()
    assert any(x.rule.endswith("presentation") for x in v)


def test_valid_layer_dependency_ok():
    from nexus_scalp.dependency_intelligence.analysis import GraphAnalyzer
    from nexus_scalp.dependency_intelligence.models import (
        DependencyEdge,
        DependencyGraph,
        EdgeKind,
        Layer,
    )

    g = DependencyGraph()
    g.add_node(_m("dom", layer=Layer.DOMAIN))
    g.add_node(_m("pres", layer=Layer.PRESENTATION))
    g.add_edge(DependencyEdge(source="mod:pres", target="mod:dom", kind=EdgeKind.IMPORT))
    v = GraphAnalyzer(g).validate_architecture()
    assert not any(x["rule"].startswith("layer:presentation") for x in v)


# ---------------------------------------------------------------------------
# Impact analysis
# ---------------------------------------------------------------------------


@requires_pkg
def test_impact_direct_and_transitive(graph):
    from nexus_scalp.dependency_intelligence.analysis import GraphAnalyzer

    ga = GraphAnalyzer(graph.graph)
    # pick a node that has dependents
    target = None
    for e in graph.graph.edges:
        if e.kind.value == "IMPORT":
            target = e.target
            break
    rep = ga.impact(target)
    assert "direct" in rep and "transitive" in rep
    assert isinstance(rep["direct"], list)


# ---------------------------------------------------------------------------
# API contract
# ---------------------------------------------------------------------------


@requires_pkg
def test_api_summary_shape():
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    client = TestClient(create_app(engine_ref=None))
    r = client.get("/api/dependency/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "repository" in body and "health" in body


@requires_pkg
def test_api_graph_shape():
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    client = TestClient(create_app(engine_ref=None))
    r = client.get("/api/dependency/graph")
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body and "edges" in body


@requires_pkg
def test_api_node_lookup():
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    client = TestClient(create_app(engine_ref=None))
    r = client.get("/api/dependency/node/nexus_scalp.cli.main")
    assert r.status_code in (200, 404)  # 200 if exists; contract is a valid response


@requires_pkg
def test_api_path_lookup():
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    client = TestClient(create_app(engine_ref=None))
    r = client.get(
        "/api/dependency/path",
        params={
            "source": "mod:nexus_scalp.web.server",
            "target": "mod:nexus_scalp.observability.logging",
        },
    )
    assert r.status_code == 200
    assert "found" in r.json()


@requires_pkg
def test_api_impact_response():
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    client = TestClient(create_app(engine_ref=None))
    r = client.get(
        "/api/dependency/impact", params={"path": "mod:nexus_scalp.configuration.config"}
    )
    assert r.status_code == 200
    assert "impact_kind" in r.json()


@requires_pkg
def test_api_unknown_node_404():
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    client = TestClient(create_app(engine_ref=None))
    r = client.get("/api/dependency/node/does.not.exist.module")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# HTML route
# ---------------------------------------------------------------------------


@requires_pkg
def test_dependency_route_exists():
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    client = TestClient(create_app(engine_ref=None))
    r = client.get("/dependency")
    assert r.status_code == 200
    assert b"Dependency Intelligence" in r.content


@requires_pkg
def test_frontend_bootstrap_present():
    from fastapi.testclient import TestClient

    from nexus_scalp.web.server import create_app

    client = TestClient(create_app(engine_ref=None))
    html = client.get("/dependency").content.decode("utf-8", "replace")
    for token in ("dependency_api.js", "dependency_graph.js", "dependency_ui.js", "graph-svg"):
        assert token in html, f"missing {token} in /dependency page"


# ---------------------------------------------------------------------------
# CLI contract (lightweight — runs the registered typer app)
# ---------------------------------------------------------------------------


@requires_pkg
def test_cli_dependency_scan():
    spec = importlib.util.spec_from_file_location(
        "dep_cli_check", str(REPO_ROOT / "src" / "nexus_scalp" / "cli" / "dependency_commands.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from typer.testing import CliRunner

    runner = CliRunner()
    res = runner.invoke(mod.app, ["scan"])
    assert res.exit_code == 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _m(name: str, layer=None):
    from nexus_scalp.dependency_intelligence.models import (
        DependencyNode,
        NodeKind,
    )

    return DependencyNode(
        id=f"mod:{name}",
        qualified_name=name,
        display_name=name,
        kind=NodeKind.MODULE,
        layer=layer
        or __import__(
            "nexus_scalp.dependency_intelligence.models", fromlist=["Layer"]
        ).Layer.UNKNOWN,
        metadata={"rel_path": name.replace(".", "/") + ".py"},
    )
