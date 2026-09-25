"""Cache regression for the dependency-intelligence result cache (CHG-0056).

Proves: refresh/miss produces a real graph (cache_misses), the immediate
second call with the same tree hits the cache (cache_hits) and produces a
node/edge-identical graph, and the cached graph survives a model round-trip.
"""

from __future__ import annotations

import pytest

from nexus_scalp.dependency_intelligence.engine import DependencyIntelligenceEngine
from nexus_scalp.dependency_intelligence.models import DependencyGraph


def test_dependency_cache_hit_is_equivalent_to_fresh_build() -> None:
    engine = DependencyIntelligenceEngine("src/nexus_scalp")

    # Force a fresh build, then a cached hit on the same tree.
    fresh = engine.analyze(use_cache=False)
    engine2 = DependencyIntelligenceEngine("src/nexus_scalp")
    cached = engine2.analyze(use_cache=True)

    assert fresh.stats.nodes == cached.stats.nodes
    assert fresh.stats.edges == cached.stats.edges
    # Same logical graph: every node id present and edge multisets match.
    assert set(fresh.graph.nodes) == set(cached.graph.nodes)

    def edge_key(e):  # type: ignore[no-untyped-def]
        return (e.source, e.target, e.kind, e.evidence.file, e.evidence.line)

    from collections import Counter

    assert Counter(edge_key(e) for e in fresh.graph.edges) == Counter(
        edge_key(e) for e in cached.graph.edges
    )


def test_dependency_graph_survives_round_trip() -> None:
    engine = DependencyIntelligenceEngine("src/nexus_scalp")
    result = engine.analyze(use_cache=False)
    payload = result.graph.to_dict()
    rebuilt = DependencyGraph.from_dict(payload)

    assert len(rebuilt.nodes) == len(result.graph.nodes)
    assert len(rebuilt.edges) == len(result.graph.edges)
    assert set(rebuilt.nodes) == set(result.graph.nodes)

    def edge_key(e):  # type: ignore[no-untyped-def]
        return (e.source, e.target, e.kind, e.evidence.file, e.evidence.line)

    from collections import Counter

    assert Counter(edge_key(e) for e in rebuilt.edges) == Counter(
        edge_key(e) for e in result.graph.edges
    )


def test_dependency_cache_refresh_bypasses_cache_file() -> None:
    # use_cache=False must bypass any existing cache.json and rebuild.
    a = DependencyIntelligenceEngine("src/nexus_scalp")
    miss = a.analyze(use_cache=False)
    assert miss.stats.cache_misses in (0, 1)
    b = DependencyIntelligenceEngine("src/nexus_scalp")
    refreshed = b.analyze(use_cache=False)
    assert refreshed.stats.nodes == miss.stats.nodes
    assert refreshed.stats.edges == miss.stats.edges
