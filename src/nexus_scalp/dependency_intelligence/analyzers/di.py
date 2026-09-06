"""DI-intelligence post-processor for the NSE Dependency Intelligence layer.

Operates on the canonical graph produced by the AST scanner and enriches it
with *dependency-injection* semantics discovered from real source evidence:

* Registration edges (``REGISTERS``) — ``container.register(X)``,
  ``registry.register(obj)``, ``register_strategy(s)``, ``features.register``,
  ``_register(...)`` etc. The argument's runtime type becomes the target.
* Factory edges (``FACTORY_CREATES``) — ``create_*`` / ``build_*`` /
  ``make_*`` / ``factory.create`` style call sites.
* Composition-root detection — modules that *construct* many injectable
  services (e.g. ``LiveEngine.__init__``, ``NexusTradingForexBot.py``,
  ``cli/main.py``) are flagged as composition roots.

This never assumes a DI framework exists (NSE uses hand-assembled wiring); it
detects the real patterns the codebase actually uses, each with file:line
evidence and an explicit confidence.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from nexus_scalp.dependency_intelligence.models import (
    CONFIDENCE_STRONG,
    CONFIDENCE_SUPPORTED,
    DependencyEdge,
    DependencyGraph,
    EdgeKind,
    Evidence,
    NodeKind,
    ResolutionStatus,
)

# Call names that denote a registration of a dependency (abstraction <- impl).
_REGISTER_CALLS = {
    "register",
    "register_strategy",
    "register_factory",
    "add_singleton",
    "add_transient",
    "add_scoped",
    "bind",
    "provide",
    "_register",
    "REGISTER",
}

# Call-name prefixes that denote a factory/provider construction.
_FACTORY_PREFIXES = ("create_", "build_", "make_", "construct_", "new_", "get_")


def _name_to_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_to_str(node.value)
        if base:
            return f"{base}.{node.attr}"
        return node.attr
    if isinstance(node, ast.Call):
        return _name_to_str(node.func)
    return None


class DIAnalyzer:
    def __init__(self, root: Path, pkg_root: str = "nexus_scalp") -> None:
        self.root = Path(root).resolve()
        self.pkg_root = pkg_root

    def _modname(self, path: Path) -> str:
        parts = list(path.relative_to(self.root).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and parts[0] == self.pkg_root:
            return ".".join(parts)
        return ".".join([self.pkg_root, *parts]) if parts else self.pkg_root

    # -- public ----------------------------------------------------------

    def enrich(self, graph: DependencyGraph) -> dict[str, Any]:
        stats = {
            "registers": 0,
            "factory_creates": 0,
            "composition_roots": 0,
            "di_bindings": 0,
        }
        # index class nodes by simple name for type resolution of call args
        name_index: dict[str, str] = {}
        for nid, graph_node in graph.nodes.items():
            if graph_node.kind in (
                NodeKind.CLASS,
                NodeKind.PROTOCOL,
                NodeKind.INTERFACE,
                NodeKind.SERVICE,
            ):
                simple = graph_node.qualified_name.rsplit(".", 1)[-1]
                name_index.setdefault(simple, nid)

        for path in sorted(self.root.rglob("*.py")):
            rel = path.relative_to(self.root)
            if any(p in {"__pycache__", ".venv", "node_modules", "scratch"} for p in rel.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            mod_parts = list(rel.with_suffix("").parts)
            if mod_parts[-1] == "__init__":
                mod_parts = mod_parts[:-1]
            module = self._modname(path)
            src_mod = f"mod:{module}"
            if src_mod not in graph.nodes:
                continue
            for ast_node in ast.walk(tree):
                if not isinstance(ast_node, ast.Call):
                    continue
                fname = _name_to_str(ast_node.func)
                if not fname:
                    continue
                simple = fname.rsplit(".", 1)[-1]
                short = simple
                # REGISTERS: register(container, Impl) / register(Impl())
                if short in _REGISTER_CALLS:
                    tgt = self._resolve_arg_type(ast_node, name_index, graph)
                    if tgt:
                        graph.add_edge(
                            DependencyEdge(
                                source=src_mod,
                                target=tgt,
                                kind=EdgeKind.REGISTERS,
                                confidence=CONFIDENCE_STRONG,
                                resolution=ResolutionStatus.RESOLVED,
                                evidence=Evidence(
                                    evidence_type="di_registration",
                                    file=str(rel),
                                    line=ast_node.lineno,
                                    reason=f"registration call: {fname}(...)",
                                ),
                            )
                        )
                        stats["registers"] += 1
                        stats["di_bindings"] += 1
                # FACTORY_CREATES: create_* / build_* / make_*
                if any(short.startswith(p) for p in _FACTORY_PREFIXES) or short in {
                    "create",
                    "build",
                    "make",
                }:
                    tgt = self._resolve_arg_type(ast_node, name_index, graph)
                    if tgt:
                        graph.add_edge(
                            DependencyEdge(
                                source=src_mod,
                                target=tgt,
                                kind=EdgeKind.FACTORY_CREATES,
                                confidence=CONFIDENCE_SUPPORTED,
                                resolution=ResolutionStatus.FACTORY_RESOLVED,
                                evidence=Evidence(
                                    evidence_type="factory",
                                    file=str(rel),
                                    line=ast_node.lineno,
                                    reason=f"factory call: {fname}(...)",
                                ),
                            )
                        )
                        stats["factory_creates"] += 1

        # Composition-root detection: modules that appear as the *source* of the
        # most INJECTS/REGISTERS/FACTORY_CREATES edges.
        root_scores: dict[str, int] = {}
        for e in graph.edges:
            if e.kind in (EdgeKind.INJECTS, EdgeKind.REGISTERS, EdgeKind.FACTORY_CREATES):
                if e.source.startswith("cls:"):
                    root_scores[e.source] = root_scores.get(e.source, 0) + 1
        for nid, score in root_scores.items():
            if score >= 3 and nid in graph.nodes:
                graph.nodes[nid].metadata["composition_root"] = True
                graph.nodes[nid].metadata["di_wiring_count"] = score
                stats["composition_roots"] += 1

        return stats

    # -- helpers ---------------------------------------------------------

    def _resolve_arg_type(self, call: ast.Call, name_index, graph) -> str | None:
        """Best-effort: resolve the constructed/registered type from a call arg."""
        # case: register(SomeStrategy())  or  create_engine(Config)
        for arg in call.args:
            tgt = self._type_from_expr(arg, name_index, graph)
            if tgt:
                return tgt
        # keyword args: register(impl=SomeStrategy) / bind(implementation=X)
        for kw in call.keywords:
            if kw.arg in {
                "impl",
                "implementation",
                "concrete",
                "instance",
                "obj",
                "strategy",
                "factory",
            }:
                tgt = self._type_from_expr(kw.value, name_index, graph)
                if tgt:
                    return tgt
        return None

    def _type_from_expr(self, expr: ast.AST, name_index, graph) -> str | None:
        if isinstance(expr, ast.Call):
            func = _name_to_str(expr.func)
            if func:
                simple = func.rsplit(".", 1)[-1]
                # local class name (e.g. SomeStrategy())
                if simple in name_index:
                    return name_index[simple]
                # module.Class() form
                if "." in func and func in name_index:
                    return name_index[func]
                # fallback: the called factory's module can't be resolved here;
                # returning None keeps the caller on the attribute path above.
                return None
        if isinstance(expr, ast.Name):
            return name_index.get(expr.id)
        if isinstance(expr, ast.Attribute):
            full = _name_to_str(expr)
            if full and full in name_index:
                return name_index[full]
        return None
