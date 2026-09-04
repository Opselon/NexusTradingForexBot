"""CLI command module for ``nse dependency`` — Dependency Intelligence.

Thin Typer wrapper over the dependency-intelligence engine. Reuses the repo's
CLI conventions (typer app registered in ``cli/main.py``).

Commands:
    nse dependency scan            Run the full AST + DI analysis
    nse dependency graph           Print the canonical graph (JSON)
    nse dependency validate        Run architecture + cycle validation
    nse dependency impact <path>   Show direct/transitive impact of a change
    nse dependency explain <node>  Explain a node's dependencies + dependents
    nse dependency path <a> <b>    Shortest verified dependency path
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from nexus_scalp.dependency_intelligence.analysis import GraphAnalyzer, analyze_graph
from nexus_scalp.dependency_intelligence.engine import DependencyIntelligenceEngine
from nexus_scalp.dependency_intelligence.models import EdgeKind

app = typer.Typer(help="NSE Dependency Intelligence (imports + DI + architecture).")
console = Console()

ROOT = Path("src/nexus_scalp")


_CACHED_RESULT: Any = None


def _run() -> Any:
    global _CACHED_RESULT
    if _CACHED_RESULT is not None:
        return _CACHED_RESULT
    engine = DependencyIntelligenceEngine(ROOT)
    result = engine.analyze(use_cache=True)
    _CACHED_RESULT = result
    return result


@app.command("scan")
def scan(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON only."),
    refresh: bool = typer.Option(False, "--refresh", help="Force re-scan."),
) -> None:
    """Run the full dependency + DI analysis."""
    engine = DependencyIntelligenceEngine(ROOT)
    result = engine.analyze(use_cache=not refresh)
    an = analyze_graph(result.graph)
    if json_output:
        payload = {
            "status": "ok",
            "stats": result.stats.__dict__,
            "analysis": {k: an[k] for k in ("summary",)},
        }
        sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
        raise typer.Exit(code=0)
    console.print("[bold cyan]Dependency Intelligence scan[/bold cyan]")
    console.print(f"  files analyzed : {result.stats.files_analyzed}")
    console.print(f"  nodes          : {result.stats.nodes}")
    console.print(f"  edges          : {result.stats.edges}")
    console.print(f"  DI registrations: {result.stats.registers}")
    console.print(f"  cycles         : {an['summary']['cycles']}")
    console.print(f"  violations     : {an['summary']['violations']}")
    console.print(f"  unresolved imps: {an['summary']['unresolved_imports']}")
    console.print(f"  duration_ms    : {result.stats.duration_ms}")


@app.command("graph")
def graph_cmd(
    out: str | None = typer.Option(None, "--out", help="Write graph JSON to path."),
) -> None:
    """Print the canonical dependency graph (JSON)."""
    result = _run()
    data = result.graph.to_dict()
    if out:
        Path(out).write_text(json.dumps(data, indent=2), encoding="utf-8")
        console.print(f"[green]Wrote graph to {out}[/green]")
    else:
        sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
    raise typer.Exit(code=0)


@app.command("validate")
def validate() -> None:
    """Run architecture-rule and cycle validation."""
    result = _run()
    analyzer = GraphAnalyzer(result.graph)
    cycles = analyzer.detect_cycles()
    violations = analyzer.validate_architecture()
    table = Table(title="Validation")
    table.add_column("Kind")
    table.add_column("Count")
    table.add_row("cycles", str(len(cycles)))
    table.add_row("architecture violations", str(len(violations)))
    console.print(table)
    if cycles:
        for c in cycles[:10]:
            console.print(f"  [red]{c.cycle_id}[/red] {c.severity}: {' -> '.join(c.path)}")
    if violations:
        for v in violations[:10]:
            console.print(f"  [yellow]{v.rule}[/yellow]: {v.source} -> {v.target}")
    raise typer.Exit(code=0 if not (cycles or violations) else 1)


@app.command("impact")
def impact(path: str) -> None:
    """Show direct/transitive impact of changing a node id or qualified name."""
    result = _run()
    analyzer = GraphAnalyzer(result.graph)
    nid = _resolve(result.graph, path)
    if nid is None:
        console.print(f"[red]Unknown node: {path}[/red]")
        raise typer.Exit(code=2)
    rep = analyzer.impact(nid)
    console.print(f"[bold]Impact of {path}[/bold] ({rep['impact_kind']})")
    console.print(f"  direct    : {len(rep['direct'])}")
    console.print(f"  transitive: {len(rep['transitive'])}")
    console.print(f"  tests     : {len(rep['tests_likely_affected'])}")
    console.print(f"  api       : {len(rep['api_impact'])}")
    console.print(f"  runtime   : {len(rep['runtime_impact'])}")


@app.command("explain")
def explain(node: str) -> None:
    """Explain a node: its dependencies, dependents, and key edges."""
    result = _run()
    graph = result.graph
    nid = _resolve(graph, node)
    if nid is None:
        console.print(f"[red]Unknown node: {node}[/red]")
        raise typer.Exit(code=2)
    n = graph.nodes[nid]
    console.print(f"[bold]{n.qualified_name}[/bold] ({n.kind.value}, {n.layer.value})")
    deps = [e for e in graph.edges if e.source == nid and e.kind in _DEP_SET]
    deps_inv = [e for e in graph.edges if e.target == nid and e.kind in _DEP_SET]
    console.print(f"  dependencies ({len(deps)}):")
    for e in deps[:20]:
        console.print(f"    - {e.kind.value} -> {e.target}  ({e.evidence.file}:{e.evidence.line})")
    console.print(f"  dependents ({len(deps_inv)}):")
    for e in deps_inv[:20]:
        console.print(f"    - {e.source} {e.kind.value}")


@app.command("path")
def path_cmd(source: str, target: str) -> None:
    """Show the shortest verified dependency path between two nodes."""
    result = _run()
    analyzer = GraphAnalyzer(result.graph)
    s = _resolve(result.graph, source)
    t = _resolve(result.graph, target)
    if s is None or t is None:
        console.print("[red]Unknown source/target[/red]")
        raise typer.Exit(code=2)
    rep = analyzer.shortest_path(s, t)
    if not rep.get("found"):
        console.print(f"[yellow]No path from {source} to {target}[/yellow]")
        raise typer.Exit(code=0)
    console.print(" -> ".join(rep["path"]))
    for e in rep["edges"]:
        console.print(f"  {e['source']} --{e['kinds']}--> {e['target']}")


_DEP_SET = {
    EdgeKind.IMPORT,
    EdgeKind.INHERITS,
    EdgeKind.IMPLEMENTS,
    EdgeKind.INJECTS,
    EdgeKind.CONSTRUCTS,
    EdgeKind.CALLS,
    EdgeKind.USES,
    EdgeKind.RESOLVES,
    EdgeKind.REGISTERS,
    EdgeKind.FACTORY_CREATES,
    EdgeKind.CONFIG_DEPENDS_ON,
    EdgeKind.CONSUMES,
}


def _resolve(graph: Any, key: str) -> str | None:
    if key in graph.nodes:
        return key
    for nid, n in graph.nodes.items():
        if n.qualified_name == key:
            return nid
    return None


def register_dependency_commands(app_main: typer.Typer) -> None:
    """Wire the dependency subcommands into the main CLI app."""
    app_main.add_typer(app, name="dependency", help="Dependency Intelligence toolkit.")
