"""AST-based source scanner for the NSE Dependency Intelligence layer.

Two-pass design (correctness-critical):

Pass 1 — structure: parse every module, create all MODULE/PACKAGE/CLASS
nodes, record each class's base classes and constructor-injection type
hints. Imports are also resolved here (they never depend on class nodes).

Pass 2 — edges: now that every class node exists, emit IMPORT / INHERITS /
IMPLEMENTS / INJECTS edges with stable, order-independent resolution.

No runtime boot — the trading engine is never started. Every edge carries
file:line evidence and an explicit confidence.
"""

from __future__ import annotations

import ast
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus_scalp.dependency_intelligence.classify import (
    classify_criticality,
    classify_layer,
)
from nexus_scalp.dependency_intelligence.models import (
    CONFIDENCE_PROVEN,
    CONFIDENCE_STRONG,
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

_STDLIB_BASE = {
    "abc",
    "argparse",
    "asyncio",
    "base64",
    "collections",
    "concurrent",
    "contextlib",
    "copy",
    "csv",
    "datetime",
    "decimal",
    "enum",
    "functools",
    "hashlib",
    "http",
    "io",
    "itertools",
    "json",
    "logging",
    "math",
    "os",
    "pathlib",
    "pickle",
    "platform",
    "queue",
    "random",
    "re",
    "shutil",
    "signal",
    "socket",
    "sqlite3",
    "statistics",
    "string",
    "struct",
    "subprocess",
    "sys",
    "threading",
    "time",
    "traceback",
    "typing",
    "unittest",
    "urllib",
    "uuid",
    "warnings",
    "weakref",
    "xml",
    "yaml",
    "zlib",
    "dataclasses",
    "inspect",
    "types",
    "gc",
    "tempfile",
    "glob",
    "fnmatch",
    "textwrap",
    "pprint",
    "html",
    "email",
    "gzip",
    "bz2",
    "lzma",
    "codecs",
}

_THIRD_PARTY = {
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic_settings",
    "typer",
    "rich",
    "yaml",
    "numpy",
    "pandas",
    "polars",
    "torch",
    "sklearn",
    "scipy",
    "matplotlib",
    "networkx",
    "sqlalchemy",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    "structlog",
    "loguru",
    "pandas_ta",
    "ta",
    "pytest",
    "psutil",
    "pywin32",
    "MetaTrader5",
}

# Stdlib abstract bases that should NOT become graph nodes.
_ABSTRACT_STDLIB = {"Protocol", "ABC", "ABCMeta", "Generic", "object"}


@dataclass
class ScanResult:
    graph: DependencyGraph
    files_analyzed: int = 0
    parse_errors: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0


class _ModuleIndex:
    def __init__(self, root: Path, pkg_root: str) -> None:
        self.root = root
        self.pkg_root = pkg_root
        self.modules: dict[str, Path] = {}
        self.pkg_dirs: set[str] = set()

    def build(self) -> None:
        for path in self.root.rglob("*.py"):
            rel = path.relative_to(self.root).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            # prefix with pkg_root so module ids match import identifiers
            mod_parts = (
                parts[1:] if (parts and parts[0] == self.pkg_root) else [self.pkg_root, *parts]
            )
            mod = ".".join(mod_parts)
            self.modules[mod] = path
            for i in range(1, len(mod_parts)):
                self.pkg_dirs.add(".".join(mod_parts[:i]))
        for path in self.root.rglob("__init__.py"):
            rel_parts = list(path.relative_to(self.root).with_suffix("").parts[:-1])
            if rel_parts:
                pkg_parts = (
                    rel_parts[1:] if rel_parts[0] == self.pkg_root else [self.pkg_root, *rel_parts]
                )
                self.pkg_dirs.add(".".join(pkg_parts))

    def resolve(self, dotted: str) -> str | None:
        if dotted in self.modules or dotted in self.pkg_dirs:
            return dotted
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in self.modules or cand in self.pkg_dirs:
                return cand
        return None


def _is_stdlib(top: str) -> bool:
    try:
        return top in _STDLIB_BASE or top in sys.stdlib_module_names  # type: ignore[attr-defined]
    except Exception:
        return top in _STDLIB_BASE


def _is_third_party(top: str, pkg_root: str) -> bool:
    if top == pkg_root or top.startswith(pkg_root + "."):
        return False
    return top in _THIRD_PARTY


def _name_to_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_to_str(node.value)
        if base:
            return f"{base}.{node.attr}"
        return node.attr
    if isinstance(node, ast.Subscript):
        return _name_to_str(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _split_base(bname: str, current_module: str) -> tuple[str, str]:
    if "." in bname:
        mod, cls = bname.rsplit(".", 1)
        return mod, cls
    return current_module, bname


class Scanner:
    def __init__(self, root: Path, pkg_root: str = "nexus_scalp") -> None:
        self.root = Path(root).resolve()
        self.pkg_root = pkg_root
        self.index = _ModuleIndex(self.root, pkg_root)
        self.graph = DependencyGraph()
        self._ctors: dict[tuple[str, str], list[tuple[str, str, int]]] = {}
        self._bases: dict[tuple[str, str], list[tuple[str, int]]] = {}

    # -- node helpers ----------------------------------------------------

    def _modname(self, rel_path: Path) -> str:
        """Return the fully-qualified module name for a repo-relative path.

        The scan root is ``src/nexus_scalp`` (no ``nexus_scalp`` prefix in the
        relative path), but imports are written as ``nexus_scalp.observability.
        logging``. We always prefix with ``pkg_root`` so module identifiers and
        import identifiers share one namespace.
        """
        parts = list(rel_path.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and parts[0] == self.pkg_root:
            return ".".join(parts)
        return ".".join([self.pkg_root, *parts]) if parts else self.pkg_root

    def _mid(self, module: str) -> str:
        return f"mod:{module}"

    def _cid(self, module: str, cls: str) -> str:
        return f"cls:{module}.{cls}"

    def _ensure_module_node(self, module: str) -> DependencyNode:
        mid = self._mid(module)
        if mid in self.graph.nodes:
            return self.graph.nodes[mid]
        rel_path = module.replace(".", "/") + ".py"
        pkg = module.split(".", maxsplit=1)[0] if module else ""
        sub = module.rsplit(".", maxsplit=1)[-1] if module else ""
        node = DependencyNode(
            id=mid,
            qualified_name=module,
            display_name=module,
            kind=NodeKind.MODULE,
            module=module,
            package=pkg,
            file=rel_path,
            layer=classify_layer(pkg, sub),
            criticality=classify_criticality(pkg, sub),
            metadata={"rel_path": rel_path},
        )
        self.graph.add_node(node)
        return node

    def _ensure_class_node(
        self, module: str, cls: str, bases: list[str] | None = None
    ) -> DependencyNode:
        cid = self._cid(module, cls)
        if cid in self.graph.nodes:
            return self.graph.nodes[cid]
        pkg = module.split(".", maxsplit=1)[0] if module else ""
        kind = NodeKind.CLASS
        is_proto = bool(bases and ("Protocol" in bases))
        is_abc = bool(bases and ("ABC" in bases))
        if is_proto:
            kind = NodeKind.PROTOCOL
        elif is_abc:
            kind = NodeKind.INTERFACE
        node = DependencyNode(
            id=cid,
            qualified_name=f"{module}.{cls}",
            display_name=cls,
            kind=kind,
            module=module,
            package=pkg,
            file=module.replace(".", "/") + ".py",
            layer=classify_layer(pkg, cls),
            criticality=classify_criticality(pkg, cls),
            metadata={},
        )
        if bases:
            node.metadata["bases"] = bases
        self.graph.add_node(node)
        return node

    # -- main ------------------------------------------------------------

    def scan(self) -> ScanResult:
        started = time.time()
        self.index.build()
        files = sorted(self.root.rglob("*.py"))
        self._preindex_classes(files)
        for path in files:
            rel = path.relative_to(self.root)
            if any(p in {"__pycache__", ".venv", "node_modules"} for p in rel.parts):
                continue
            if rel.parts[0] == "scratch":
                continue
            self._pass1(path)
        for path in files:
            rel = path.relative_to(self.root)
            if any(p in {"__pycache__", ".venv", "node_modules"} for p in rel.parts):
                continue
            if rel.parts[0] == "scratch":
                continue
            self._pass2(path)
        res = ScanResult(graph=self.graph)
        res.files_analyzed = sum(1 for f in files if f.suffix == ".py" and f.parts[0] != "scratch")
        res.parse_errors = self.graph.metadata.get("parse_errors", [])
        res.duration_ms = round((time.time() - started) * 1000.0, 2)
        return res

    def _preindex_classes(self, files: list[Path]) -> None:
        """Global simple-name -> module index from real class definitions.

        Used to resolve bare-name bases / injection annotations (e.g.
        ``class X(Strategy)``) to the module that actually defines the class,
        so cross-module Protocol/ABC edges are correct.
        """
        self._class_by_name: dict[str, str] = {}
        for path in files:
            rel = path.relative_to(self.root)
            if rel.parts[0] in {"scratch", "__pycache__"}:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            mod_parts = list(path.relative_to(self.root).with_suffix("").parts)
            if mod_parts[-1] == "__init__":
                mod_parts = mod_parts[:-1]
            module = self._modname(path.relative_to(self.root))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = [_name_to_str(b) for b in node.bases]
                    is_abstract = any(
                        (b in _ABSTRACT_STDLIB) or (b or "").lower().endswith("protocol")
                        for b in bases
                    )
                    cur = self._class_by_name.get(node.name)
                    if cur is None or (is_abstract and not self._is_abstract_module(cur)):
                        # Prefer the definition in a base/ports/protocol package.
                        self._class_by_name[node.name] = module

    @staticmethod
    def _is_abstract_module(module: str) -> bool:
        return any(seg in {"base", "ports", "protocol"} for seg in module.split("."))

    # -- pass 1: structure ----------------------------------------------

    def _pass1(self, path: Path) -> None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            self._record_error(path, str(exc))
            return
        except Exception as exc:  # defensive
            self._record_error(path, f"{type(exc).__name__}: {exc}")
            return
        module = self._modname(path.relative_to(self.root))
        self._ensure_module_node(module)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [_name_to_str(b) for b in node.bases]
                bases = [b for b in bases if b]
                self._ensure_class_node(module, node.name, bases)
                self._bases[(module, node.name)] = [(b, node.lineno) for b in bases]
                # Register referenced base classes so edges resolve even when
                # the base is defined in another module.
                for b in bases:
                    tm, tc = _split_base(b, module)
                    if b in _ABSTRACT_STDLIB:
                        continue
                    # Resolve bare names against the global class-name index.
                    if "." not in b and b in getattr(self, "_class_by_name", {}):
                        tm = self._class_by_name[b]
                        tc = b
                    self._ensure_class_node(tm, tc)
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        sub.name == "__init__"
                    ):
                        for arg in sub.args.args:
                            if arg.arg == "self":
                                continue
                            if arg.annotation is None:
                                continue
                            tname = _name_to_str(arg.annotation)
                            if not tname:
                                continue
                            # Register referenced injected class so edges resolve
                            # across modules.
                            tm, tc = _split_base(tname, module)
                            # skip primitive / generic type hints
                            if tc.lower() in {
                                "int",
                                "float",
                                "str",
                                "bool",
                                "any",
                                "none",
                                "dict",
                                "list",
                                "tuple",
                                "set",
                                "optional",
                                "union",
                                "type",
                                "callable",
                                "object",
                            }:
                                continue
                            # Resolve bare names against the global class-name index.
                            if "." not in tname and tname in getattr(self, "_class_by_name", {}):
                                tm = self._class_by_name[tname]
                                tc = tname
                            self._ensure_class_node(tm, tc)
                            self._ctors.setdefault((module, node.name), []).append(
                                (arg.arg, tname, sub.lineno)
                            )

    # -- pass 2: edges --------------------------------------------------

    def _pass2(self, path: Path) -> None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception:
            return
        rel = str(path.relative_to(self.root))
        mod_parts = list(path.relative_to(self.root).with_suffix("").parts)
        if mod_parts[-1] == "__init__":
            mod_parts = mod_parts[:-1]
        module = self._modname(path.relative_to(self.root))
        src_mod = self._mid(module)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._emit_import(
                        src_mod, module, rel, alias.name, node.lineno, f"import {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                self._emit_import_from(src_mod, module, rel, node)

        # inheritance / implements
        for (m, c), bases in self._bases.items():
            if m != module:
                continue
            src = self._cid(m, c)
            for bname, lineno in bases:
                if bname in _ABSTRACT_STDLIB:
                    continue
                tgt_module, tgt_cls = _split_base(bname, m)
                if "." not in bname and bname in getattr(self, "_class_by_name", {}):
                    tgt_module = self._class_by_name[bname]
                    tgt_cls = bname
                tgt = self._cid(tgt_module, tgt_cls)
                if tgt not in self.graph.nodes:
                    continue
                tnode = self.graph.nodes[tgt]
                if tnode.kind in (NodeKind.PROTOCOL, NodeKind.INTERFACE):
                    kind = EdgeKind.IMPLEMENTS
                    conf = CONFIDENCE_STRONG
                else:
                    kind = EdgeKind.INHERITS
                    conf = CONFIDENCE_PROVEN
                self.graph.add_edge(
                    DependencyEdge(
                        source=src,
                        target=tgt,
                        kind=kind,
                        confidence=conf,
                        evidence=Evidence(
                            evidence_type="symbol",
                            file=rel,
                            line=lineno,
                            reason=f"class {c}({bname})",
                        ),
                    )
                )

        # constructor injection
        for (m, c), ctors in self._ctors.items():
            if m != module:
                continue
            src = self._cid(m, c)
            for _arg, tname, lineno in ctors:
                tgt_module, tgt_cls = _split_base(tname, m)
                if "." not in tname and tname in getattr(self, "_class_by_name", {}):
                    tgt_module = self._class_by_name[tname]
                    tgt_cls = tname
                tgt = self._cid(tgt_module, tgt_cls)
                if tgt in self.graph.nodes:
                    self.graph.add_edge(
                        DependencyEdge(
                            source=src,
                            target=tgt,
                            kind=EdgeKind.INJECTS,
                            confidence=CONFIDENCE_PROVEN,
                            resolution=ResolutionStatus.RESOLVED,
                            evidence=Evidence(
                                evidence_type="constructor",
                                file=rel,
                                line=lineno,
                                reason=f"constructor parameter '{_arg}' annotated as {tname}",
                            ),
                        )
                    )
                else:
                    self.graph.add_edge(
                        DependencyEdge(
                            source=src,
                            target=f"typehint:{tname}",
                            kind=EdgeKind.INJECTS,
                            confidence=CONFIDENCE_WEAK,
                            resolution=ResolutionStatus.UNKNOWN,
                            evidence=Evidence(
                                evidence_type="constructor",
                                file=rel,
                                line=lineno,
                                reason=f"constructor parameter '{_arg}' annotated as {tname} (class not in repo scope)",
                            ),
                        )
                    )

    # -- import emission -------------------------------------------------

    def _emit_import(self, src_mod, module, rel, name, lineno, raw) -> None:
        top = name.split(".")[0]
        if _is_stdlib(top):
            self._edge_external(src_mod, rel, lineno, raw, f"stdlib:{top}")
        elif _is_third_party(top, self.pkg_root):
            self._edge_external(src_mod, rel, lineno, raw, f"external:{top}")
        else:
            resolved = self.index.resolve(name)
            if resolved:
                self._ensure_module_node(resolved)
                self.graph.add_edge(
                    DependencyEdge(
                        source=src_mod,
                        target=self._mid(resolved),
                        kind=EdgeKind.IMPORT,
                        confidence=CONFIDENCE_PROVEN,
                        evidence=Evidence(
                            evidence_type="import", file=rel, line=lineno, reason=raw
                        ),
                    )
                )
            else:
                self._edge_unresolved(src_mod, rel, lineno, raw, name)

    def _emit_import_from(self, src_mod, module, rel, node) -> None:
        mod = node.module or ""
        raw = f"from {mod} import {', '.join(a.name for a in node.names)}"
        if node.level and node.level > 0:
            cur = module.rsplit(".", 1)[0] if "." in module else ""
            prefix = cur
            for _ in range(node.level - 1):
                if "." in prefix:
                    prefix = prefix.rsplit(".", 1)[0]
            target = f"{prefix}.{mod}".strip(".") if mod else prefix
            resolved = self.index.resolve(target)
            if resolved:
                self._ensure_module_node(resolved)
                self.graph.add_edge(
                    DependencyEdge(
                        source=src_mod,
                        target=self._mid(resolved),
                        kind=EdgeKind.IMPORT,
                        confidence=CONFIDENCE_PROVEN,
                        evidence=Evidence(
                            evidence_type="import", file=rel, line=node.lineno, reason=raw
                        ),
                    )
                )
            else:
                self._edge_unresolved(src_mod, rel, node.lineno, raw, target)
            return
        top = mod.split(".")[0] if mod else ""
        if _is_stdlib(top):
            self._edge_external(src_mod, rel, node.lineno, raw, f"stdlib:{top}")
        elif _is_third_party(top, self.pkg_root):
            self._edge_external(src_mod, rel, node.lineno, raw, f"external:{top}")
        else:
            resolved = self.index.resolve(mod)
            if resolved:
                self._ensure_module_node(resolved)
                self.graph.add_edge(
                    DependencyEdge(
                        source=src_mod,
                        target=self._mid(resolved),
                        kind=EdgeKind.IMPORT,
                        confidence=CONFIDENCE_PROVEN,
                        evidence=Evidence(
                            evidence_type="import", file=rel, line=node.lineno, reason=raw
                        ),
                    )
                )
            else:
                self._edge_unresolved(src_mod, rel, node.lineno, raw, mod)

    def _edge_external(self, src_mod, rel, lineno, raw, ext_id) -> None:
        ext_name = ext_id.split(":", 1)[1]
        self.graph.add_node(
            DependencyNode(
                id=ext_id,
                qualified_name=ext_name,
                display_name=ext_name,
                kind=NodeKind.EXTERNAL,
                file=rel,
                layer=Layer.UNKNOWN,
                criticality=Criticality.UNKNOWN,
                metadata={"external": True},
            )
        )
        self.graph.add_edge(
            DependencyEdge(
                source=src_mod,
                target=ext_id,
                kind=EdgeKind.IMPORT,
                confidence=CONFIDENCE_PROVEN,
                evidence=Evidence(evidence_type="import", file=rel, line=lineno, reason=raw),
            )
        )

    def _edge_unresolved(self, src_mod, rel, lineno, raw, name) -> None:
        uid = f"unresolved:{name}"
        self.graph.add_node(
            DependencyNode(
                id=uid,
                qualified_name=name,
                display_name=name,
                kind=NodeKind.EXTERNAL,
                file=rel,
                layer=Layer.UNKNOWN,
                status=ResolutionStatus.UNRESOLVED,
                confidence=CONFIDENCE_WEAK,
                metadata={"reason": "import not resolvable in repo"},
            )
        )
        self.graph.add_edge(
            DependencyEdge(
                source=src_mod,
                target=uid,
                kind=EdgeKind.IMPORT,
                confidence=CONFIDENCE_WEAK,
                resolution=ResolutionStatus.UNRESOLVED,
                evidence=Evidence(
                    evidence_type="import",
                    file=rel,
                    line=lineno,
                    reason=f"unresolved import: {raw}",
                ),
            )
        )

    def _record_error(self, path: Path, msg: str) -> None:
        self.graph.metadata.setdefault("parse_errors", []).append(
            {"file": str(path.relative_to(self.root)), "error": msg}
        )
