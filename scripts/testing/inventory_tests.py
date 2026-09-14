#!/usr/bin/env python3
"""Forensic test inventory builder (NSE radical test reconstruction).

AST-parses every test file under tests/ and emits machine facts per file:
test counts (functions + classes), production modules imported, mock usage,
subprocess/network/sqlite usage, sleeps, markers, fixtures, platform deps.

Usage:
    python scripts/testing/inventory_tests.py --out scratch/recon/inventory_raw.json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"

MOCK_HINT = re.compile(r"\b(MagicMock|AsyncMock|Mock|patch|monkeypatch\.(setattr|setenv))\b")
SLEEP_HINT = re.compile(r"(asyncio\.sleep|time\.sleep|Event\.wait|threading\.Timer)")
NET_HINT = re.compile(r"(requests\.|httpx\.|urllib\.request|socket\.|aiohttp)")
SUBPROC_HINT = re.compile(r"(subprocess\.|Popen\(|os\.system)")
SQLITE_HINT = re.compile(
    r"(sqlite3|create_engine\(['\"]sqlite|AuditRepository\(|RuntimeConfigStore\()"
)
MT5_HINT = re.compile(r"(MetaTrader5|DirectMT5Adapter|\bmt5\.)")
ENVWRITE_HINT = re.compile(r"os\.environ\[")


def _deco_markers(fn: ast.FunctionDef | ast.ClassDef) -> list[str]:
    out = []
    for d in fn.decorator_list:
        s = ast.dump(d)
        m = re.findall(r"Name\(id='(\w+)'\)|attr='(\w+)'", s)
        for a, b in m:
            name = a or b
            if name in {"mark", "parametrize", "skip", "skipif", "xfail", "fixture"}:
                continue
        # simpler: read source-ish from Attribute chains like pytest.mark.slow
        if isinstance(d, ast.Attribute):
            chain = []
            cur: ast.AST = d
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                chain.append(cur.id)
            joined = ".".join(reversed(chain))
            if joined.startswith("pytest.mark."):
                out.append(joined[len("pytest.mark.") :])
            elif joined.endswith(".slow"):
                out.append("slow")
        elif isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
            chain = []
            cur2: ast.AST = d.func
            while isinstance(cur2, ast.Attribute):
                chain.append(cur2.attr)
                cur2 = cur2.value
            if isinstance(cur2, ast.Name):
                chain.append(cur2.id)
            joined = ".".join(reversed(chain))
            if joined.startswith("pytest.mark."):
                out.append(joined[len("pytest.mark.") :])
    return out


def scan_file(path: Path) -> dict:
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"file": str(path.relative_to(REPO)), "parse_error": str(e)}

    tests: list[dict] = []
    fixtures: list[str] = []
    imports_prod: set[str] = set()
    mock_uses = 0
    sleep_uses = 0
    net_uses = 0
    subproc_uses = 0
    sqlite_uses = 0
    mt5_uses = 0
    env_writes = 0
    skips = 0
    xfails = 0
    tmp_path_uses = 0
    real_classes: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ""
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                names = [f"{mod}.{a.name}" for a in node.names]
            for n in names:
                if n.startswith("nexus_scalp"):
                    imports_prod.add(n)
        elif isinstance(node, ast.Name) and node.id.startswith("Test"):
            real_classes.add(node.id)

    for _match in MOCK_HINT.finditer(src):
        mock_uses += 1
    sleep_uses = len(SLEEP_HINT.findall(src))
    net_uses = len(NET_HINT.findall(src))
    subproc_uses = len(SUBPROC_HINT.findall(src))
    sqlite_uses = len(SQLITE_HINT.findall(src))
    mt5_uses = len(MT5_HINT.findall(src))
    env_writes = len(ENVWRITE_HINT.findall(src))
    skips = len(re.findall(r"(pytest\.skip|@pytest\.mark\.skip|skipif|skipIf)", src))
    xfails = len(re.findall(r"xfail", src))
    tmp_path_uses = len(re.findall(r"\btmp_path\b", src))

    for top in tree.body:
        if isinstance(top, ast.ClassDef) and top.name.startswith("Test"):
            markers = _deco_markers(top)
            for sub in top.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith(
                    "test"
                ):
                    tests.append(
                        {
                            "name": f"{top.name}::{sub.name}",
                            "markers": sorted(set(_deco_markers(sub) + markers)),
                            "doc": (ast.get_docstring(sub) or "")[:200],
                        }
                    )
        elif isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if top.name.startswith("test"):
                tests.append(
                    {
                        "name": top.name,
                        "markers": _deco_markers(top),
                        "doc": (ast.get_docstring(top) or "")[:200],
                    }
                )
            elif any(
                (isinstance(d, ast.Attribute) and d.attr == "fixture")
                or (
                    isinstance(d, ast.Call)
                    and isinstance(getattr(d, "func", None), ast.Attribute)
                    and d.func.attr == "fixture"
                )
                for d in top.decorator_list
            ):
                fixtures.append(top.name)

    rel = str(path.relative_to(REPO)).replace("\\", "/")
    return {
        "file": rel,
        "dir": rel.split("/")[1] if len(rel.split("/")) > 2 else "root",
        "loc": src.count("\n") + 1,
        "n_tests": len(tests),
        "tests": tests,
        "fixtures": fixtures,
        "imports_prod": sorted(imports_prod),
        "mock_uses": mock_uses,
        "sleep_uses": sleep_uses,
        "net_uses": net_uses,
        "subproc_uses": subproc_uses,
        "sqlite_uses": sqlite_uses,
        "mt5_uses": mt5_uses,
        "env_writes": env_writes,
        "skips": skips,
        "xfails": xfails,
        "tmp_path_uses": tmp_path_uses,
        "in_critical_suite": None,  # filled by main
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="scratch/recon/inventory_raw.json")
    args = ap.parse_args()

    crit = set()
    crit_path = TESTS / "critical_suite.txt"
    if crit_path.exists():
        crit = {
            l.strip().replace("\\", "/")
            for l in crit_path.read_text().splitlines()
            if l.strip() and not l.startswith("#")
        }
    slow = set()
    slow_path = TESTS / "slow_suite.txt"
    if slow_path.exists():
        slow = {
            l.strip().replace("\\", "/")
            for l in slow_path.read_text().splitlines()
            if l.strip() and not l.startswith("#")
        }

    files = sorted(p for p in TESTS.rglob("test_*.py") if "__pycache__" not in str(p))
    entries = []
    for p in files:
        e = scan_file(p)
        e["in_critical_suite"] = e["file"] in crit
        e["in_slow_suite"] = e["file"] in slow
        entries.append(e)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    total_tests = sum(e.get("n_tests", 0) for e in entries)
    print(f"files={len(entries)} tests={total_tests} -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
