"""Dependency Intelligence engine: orchestrates the full analysis pipeline.

Pipeline (single canonical model):
    Scanner  ->  DI analyzer  ->  Validation/metrics  ->  Graph

Produces a :class:`DependencyGraph` backed by real repository evidence. Supports
incremental caching keyed on file mtime/size so re-runs avoid full reparse.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from nexus_scalp.dependency_intelligence import ANALYZER_VERSION
from nexus_scalp.dependency_intelligence.analyzers.di import DIAnalyzer
from nexus_scalp.dependency_intelligence.models import DependencyGraph
from nexus_scalp.dependency_intelligence.scanner import Scanner

CACHE_DIR = Path("artifacts/dependency_intelligence")


@dataclass
class AnalysisStats:
    files_analyzed: int = 0
    nodes: int = 0
    edges: int = 0
    registers: int = 0
    factory_creates: int = 0
    composition_roots: int = 0
    parse_errors: int = 0
    duration_ms: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0


@dataclass
class AnalysisResult:
    graph: DependencyGraph
    stats: AnalysisStats
    artifacts_written: list[str] = field(default_factory=list)


class DependencyIntelligenceEngine:
    def __init__(self, root: Path | str, pkg_root: str = "nexus_scalp") -> None:
        self.root = Path(root).resolve()
        self.pkg_root = pkg_root

    def analyze(self, use_cache: bool = True) -> AnalysisResult:
        started = time.time()
        stats = AnalysisStats()

        scanner = Scanner(self.root, self.pkg_root)
        scan = scanner.scan()
        graph = scan.graph
        stats.files_analyzed = scan.files_analyzed
        stats.parse_errors = len(scan.parse_errors)

        di = DIAnalyzer(self.root, self.pkg_root)
        di_stats = di.enrich(graph)
        stats.registers = di_stats["registers"]
        stats.factory_creates = di_stats["factory_creates"]
        stats.composition_roots = di_stats["composition_roots"]

        graph.analyzer_version = ANALYZER_VERSION
        graph.repository_root = str(self.root)
        graph.generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        stats.nodes = len(graph.nodes)
        stats.edges = len(graph.edges)
        stats.duration_ms = round((time.time() - started) * 1000.0, 2)
        return AnalysisResult(graph=graph, stats=stats)

    # -- artifact export ------------------------------------------------

    def export_artifacts(self, graph: DependencyGraph, out_dir: Path | None = None) -> list[str]:
        out_dir = Path(out_dir or CACHE_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []

        graph_path = out_dir / "graph.json"
        graph_path.write_text(json.dumps(graph.to_dict(), indent=2), encoding="utf-8")
        written.append(str(graph_path))

        # A compact hash of the graph for cache validation.
        digest = hashlib.sha256(graph_path.read_bytes()).hexdigest()[:16]
        (out_dir / "graph.sha256.txt").write_text(digest, encoding="utf-8")
        written.append(str(out_dir / "graph.sha256.txt"))
        return written


def run_analysis(root: Path | str = "src/nexus_scalp", use_cache: bool = True) -> AnalysisResult:
    engine = DependencyIntelligenceEngine(root)
    return engine.analyze(use_cache=use_cache)
