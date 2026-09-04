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
from typing import Any

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

    def _fingerprint(self) -> tuple[int, int]:
        """(file_count, max_mtime_ns) over scanned .py files - cache key.

        Deliberately cheap (os.scandir walk, no parsing): the whole point is
        to avoid the 9-13s full AST rescan when nothing changed.
        """
        files = 0
        newest = 0
        for path in self.root.rglob("*.py"):
            rel = path.relative_to(self.root)
            if any(part in {"__pycache__", ".venv", "node_modules"} for part in rel.parts):
                continue
            if rel.parts and rel.parts[0] == "scratch":
                continue
            files += 1
            try:
                m = path.stat().st_mtime_ns
            except OSError:
                continue
            newest = max(newest, m)
        return files, newest

    def _cache_path(self) -> Path:
        return Path("artifacts/dependency_intelligence/cache.json")

    def analyze(self, use_cache: bool = True) -> AnalysisResult:
        started = time.time()
        stats = AnalysisStats()

        key = None
        if use_cache:
            try:
                key = (self.pkg_root, ANALYZER_VERSION, *self._fingerprint())
            except OSError:
                key = None
            cached = self._load_cache(key)
            if cached is not None:
                graph, files_analyzed, parse_errors = cached
                stats.files_analyzed = files_analyzed
                stats.parse_errors = len(parse_errors)
                stats.cache_hits = 1
                stats.nodes = len(graph.nodes)
                stats.edges = len(graph.edges)
                stats.duration_ms = round((time.time() - started) * 1000.0, 2)
                return AnalysisResult(graph=graph, stats=stats)
            stats.cache_misses = 1

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

        # Store AFTER DI enrichment: the cached graph must equal the fresh
        # AnalysisResult.graph (REGISTERS/FACTORY_CREATES edges included).
        if use_cache and key is not None:
            self._store_cache(key, graph, scan.files_analyzed, scan.parse_errors)

        stats.nodes = len(graph.nodes)
        stats.edges = len(graph.edges)
        stats.duration_ms = round((time.time() - started) * 1000.0, 2)
        return AnalysisResult(graph=graph, stats=stats)

    # -- result cache -----------------------------------------------------
    # The dependency CLI (scan/graph/validate/impact/explain/path) rebuilds a
    # full 437-file AST graph on EVERY invocation (~10-14s each); the critical
    # suite's CLI e2e tests invoke it 7x per run. A fingerprint-keyed result
    # cache collapses those to one build per tree state. Non-trading module;
    # behavior identical for identical trees (verified by test_dependency_cache).

    def _load_cache(self, key) -> tuple[DependencyGraph, int, list[str]] | None:
        path = self._cache_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if payload.get("key") != list(key):
            return None
        try:
            graph = DependencyGraph.from_dict(payload["graph"])
        except Exception:
            return None
        return graph, int(payload["files_analyzed"]), list(payload.get("parse_errors", []))

    def _store_cache(
        self, key, graph: DependencyGraph, files_analyzed: int, parse_errors: list[Any]
    ) -> None:
        path = self._cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "key": list(key),
                "graph": graph.to_dict(),
                "files_analyzed": files_analyzed,
                "parse_errors": parse_errors,
            }
            path.write_text(json.dumps(payload, default=str), encoding="utf-8")
        except OSError:
            # Cache is best-effort; a read-only tree must never fail analysis.
            pass

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
