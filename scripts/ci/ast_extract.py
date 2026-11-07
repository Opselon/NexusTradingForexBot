#!/usr/bin/env python3
"""Minimal Python AST metadata extractor — the ONLY Python analysis allowed.

The C17 engine (nse-ci-engine ast-graph) consumes this JSON and performs ALL
dependency analysis, fixture mapping, selection and planning. Python's role is
strictly limited to extracting structural metadata via the official `ast`
module (multiline imports, decorators and call shapes that a line scanner
cannot see reliably).

Output schema (nse-ast-meta-v1), one entry per scanned file:
  module           dotted module name (src/ tree stripped)
  imports[]        {m: module-or-empty, rel: level, symbols: [names]}
  classes[]        {name, bases[]}
  functions[]      names
  fixtures[]       {name, autouse, params}   (@pytest.fixture decorated)
  tests[]          test functions / test classes
  test_args{}      test function -> positional args (fixture usage)
  dynamic_imports[] importlib.import_module / __import__ targets (or <dynamic>)
  mock_refs[]      patch/Mock/MagicMock/monkeypatch references
  monkeypatch      bool
  unparseable      true when ast.parse failed (C expands conservatively)
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOTS = ("src/nexus_scalp", "tests")


def module_name(rel: str) -> str:
    parts = rel[:-3].split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(p for p in parts if p)


def _decorator_name(dec) -> str:
    if isinstance(dec, ast.Call):
        dec = dec.func
    if isinstance(dec, ast.Attribute):
        return dec.attr
    if isinstance(dec, ast.Name):
        return dec.id
    return ""


def extract(rel: str, path: Path) -> dict:
    info: dict = {
        "module": module_name(rel),
        "imports": [],
        "classes": [],
        "functions": [],
        "fixtures": [],
        "tests": [],
        "test_args": {},
        "dynamic_imports": [],
        "mock_refs": [],
        "monkeypatch": False,
    }
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError, ValueError):
        info["unparseable"] = True
        return info

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                info["imports"].append({"m": a.name, "rel": 0, "symbols": []})
        elif isinstance(node, ast.ImportFrom):
            syms = [a.name for a in node.names if a.name != "*"]
            info["imports"].append(
                {"m": node.module or "", "rel": node.level or 0, "symbols": syms})
        elif isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                try:
                    bases.append(ast.unparse(b))
                except Exception:
                    pass
            info["classes"].append({"name": node.name, "bases": bases})
            if node.name.startswith("Test"):
                info["tests"].append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info["functions"].append(node.name)
            is_test = node.name.startswith("test_")
            if is_test:
                info["tests"].append(node.name)
                info["test_args"][node.name] = [
                    a.arg for a in node.args.args
                    if a.arg not in ("self", "cls")
                ]
            for dec in node.decorator_list:
                dname = _decorator_name(dec)
                if dname == "fixture":
                    autouse = False
                    if isinstance(dec, ast.Call):
                        for kw in dec.keywords:
                            if kw.arg == "autouse":
                                try:
                                    autouse = bool(ast.literal_eval(kw.value))
                                except Exception:
                                    autouse = True
                    info["fixtures"].append(
                        {"name": node.name, "autouse": autouse, "params": []})
        elif isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", "")
            if name in ("import_module", "__import__"):
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    info["dynamic_imports"].append(arg.value)
                else:
                    info["dynamic_imports"].append("<dynamic>")
            if name in ("patch", "Mock", "MagicMock", "monkeypatch", "MockFixture"):
                info["mock_refs"].append(name)
            if isinstance(fn, ast.Name) and fn.id == "monkeypatch":
                info["monkeypatch"] = True
    return info


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    out = {"schema": "nse-ast-meta-v1", "files": {}}
    for base in ROOTS:
        base_dir = root / base
        if not base_dir.is_dir():
            continue
        for p in sorted(base_dir.rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            if "__pycache__" in rel:
                continue
            out["files"][rel] = extract(rel, p)
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
