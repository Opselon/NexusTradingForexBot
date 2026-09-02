"""NEXUS-ANTIGOD baseline census probe (read-only).

God-module ranking across src/nexus_scalp using multi-dimensional evidence:
LOC, class count, method count, max method LOC, self.* attribute count,
branch count, fan-in (importers across src), fan-out (imports), plus a
package-level module dependency cycle report (smallest cycles).

Director baseline tool for the AntiGod program. No edits, no side effects.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "nexus_scalp")


def module_name_for(path: str) -> str | None:
    rel = os.path.relpath(path, SRC).replace("\\", "/")
    if rel.endswith("__init__.py"):
        rel = rel[: -len("__init__.py")]
    else:
        rel = rel[: -len(".py")]
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    return "nexus_scalp." + rel.replace("/", ".") if rel else "nexus_scalp"


def analyze_file(path: str):
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    lines = src.count("\n") + 1
    classes = []
    top_funcs = []
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
        elif isinstance(node, ast.ClassDef):
            methods = [
                n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            class_lines = (node.end_lineno or node.lineno) - node.lineno + 1
            branches = sum(
                isinstance(n, (ast.If, ast.For, ast.While, ast.Try, ast.IfExp))
                for n in ast.walk(node)
            )
            classes.append(
                {
                    "name": node.name,
                    "loc": class_lines,
                    "methods": len(methods),
                    "branches": branches,
                }
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_funcs.append(node)

    def func_metrics(fn):
        span = (fn.end_lineno or fn.lineno) - fn.lineno + 1
        branches = sum(
            isinstance(n, (ast.If, ast.For, ast.While, ast.Try, ast.IfExp)) for n in ast.walk(fn)
        )
        attrs = {
            n.attr
            for n in ast.walk(fn)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == "self"
        }
        return {"name": fn.name, "loc": span, "branches": branches, "self_attrs": len(attrs)}

    all_methods = [func_metrics(f) for f in top_funcs]

    # method details per class (re-walk to attach)
    class_details = []
    for node in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        ms = [
            func_metrics(n)
            for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self_attrs = set()
        for n in ast.walk(node):
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "self"
            ):
                self_attrs.add(n.attr)
        class_details.append(
            {
                "name": node.name,
                "loc": (node.end_lineno or node.lineno) - node.lineno + 1,
                "methods": len(ms),
                "max_method_loc": max((m["loc"] for m in ms), default=0),
                "branches": sum(
                    isinstance(x, (ast.If, ast.For, ast.While, ast.Try, ast.IfExp))
                    for x in ast.walk(node)
                ),
                "self_attrs": len(self_attrs),
                "top_methods": sorted(ms, key=lambda m: -m["loc"])[:5],
            }
        )

    return {
        "module": module_name_for(path),
        "path": os.path.relpath(path, ROOT).replace("\\", "/"),
        "loc": lines,
        "n_classes": len(classes),
        "classes": class_details,
        "n_top_funcs": len(top_funcs),
        "imports": sorted(i for i in imports if i.startswith("nexus_scalp")),
        "n_imports_internal": len([i for i in imports if i.startswith("nexus_scalp")]),
        "max_method": max(all_methods, key=lambda m: m["loc"]) if all_methods else None,
    }


def main():
    files = []
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(os.path.join(dirpath, fn))

    results = [r for r in (analyze_file(p) for p in files) if r]
    results.sort(key=lambda r: -r["loc"])

    # module-level dependency graph for cycles
    mod_deps = defaultdict(set)
    for r in results:
        mod_deps[r["module"]] = set()
    for r in results:
        for imp in r["imports"]:
            # map import to a known module or its package prefix
            if imp in mod_deps and imp != r["module"]:
                mod_deps[r["module"]].add(imp)
            else:
                parts = imp.split(".")
                while len(parts) > 1:
                    cand = ".".join(parts[:-1])
                    if cand in mod_deps and cand != r["module"]:
                        mod_deps[r["module"]].add(cand)
                        break
                    parts = parts[:-1]

    # fan-in
    fan_in = defaultdict(int)
    for _m, deps in mod_deps.items():
        for d in deps:
            fan_in[d] += 1

    # cycle detection ( Tarjan SCC )
    index_counter = [0]
    stack, lowlink, index, on_stack = [], {}, {}, {}
    sccs = []

    def strongconnect(v):
        index[v] = lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack[v] = True
        for w in mod_deps.get(v, ()):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif on_stack.get(w):
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(comp)

    sys.setrecursionlimit(10000)
    for v in list(mod_deps):
        if v not in index:
            strongconnect(v)

    def god_score(r):
        cls = max(r["classes"], key=lambda c: c["loc"]) if r["classes"] else None
        return (
            min(r["loc"] / 6000.0, 1.5)
            + min(r["n_classes"] / 12.0, 1.0)
            + min(r["n_imports_internal"] / 30.0, 1.0)
            + min(fan_in.get(r["module"], 0) / 25.0, 1.0)
            + (min(cls["methods"] / 60.0, 1.5) if cls else 0)
            + (min(cls["self_attrs"] / 70.0, 1.5) if cls else 0)
            + (min(cls["branches"] / 400.0, 1.0) if cls else 0)
        )

    ranked = sorted(results, key=god_score, reverse=True)

    print("=" * 100)
    print("TOP 30 FILES BY LOC")
    print("=" * 100)
    for r in results[:30]:
        print(
            f"{r['loc']:6d}L  {r['path']}  classes={r['n_classes']} fan_in={fan_in.get(r['module'], 0):3d} imports={r['n_imports_internal']:3d}"
        )

    print()
    print("=" * 100)
    print("TOP 20 GOD CANDIDATES (composite: loc/class/methods/self-attrs/branches/fan-in/fan-out)")
    print("=" * 100)
    for r in ranked[:20]:
        cls = max(r["classes"], key=lambda c: c["loc"]) if r["classes"] else None
        cinfo = (
            f" | {cls['name']}: {cls['loc']}L {cls['methods']}m {cls['self_attrs']}attrs {cls['branches']}br max_m={cls['max_method_loc']}L"
            if cls
            else ""
        )
        print(f"score={god_score(r):.2f} {r['loc']:6d}L {r['path']}{cinfo}")

    print()
    print("=" * 100)
    print("TOP 15 FAN-IN MODULES (most depended-upon)")
    print("=" * 100)
    for m, c in sorted(fan_in.items(), key=lambda kv: -kv[1])[:15]:
        print(f"{c:4d}  {m}")

    print()
    print("=" * 100)
    print(f"DEPENDENCY CYCLES (module-level SCCs > 1): {len(sccs)}")
    print("=" * 100)
    for comp in sccs[:20]:
        if len(comp) <= 8:
            print(" -> ".join(sorted(comp)))
        else:
            print(f"[{len(comp)} modules] e.g. {' -> '.join(sorted(comp)[:6])} ...")

    print()
    print("=" * 100)
    print("TOP 25 LARGEST METHODS (anywhere)")
    print("=" * 100)
    biggest = []
    for r in results:
        for c in r["classes"]:
            for m in c["top_methods"]:
                biggest.append(
                    (
                        m["loc"],
                        m["branches"],
                        m["self_attrs"],
                        f"{r['path']}::{c['name']}.{m['name']}",
                    )
                )
    for loc, br, at, name in sorted(biggest, reverse=True)[:25]:
        print(f"{loc:6d}L branches={br:4d} self_attrs={at:3d}  {name}")

    with open(os.path.join(ROOT, "scratch", "ns_antigod_census.json"), "w", encoding="utf-8") as f:
        json.dump({"ranked": [r["path"] for r in ranked[:40]], "cycles": sccs}, f)


if __name__ == "__main__":
    main()
