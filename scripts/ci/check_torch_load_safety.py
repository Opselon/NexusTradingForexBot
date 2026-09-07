#!/usr/bin/env python3
"""P2 SOURCE GUARD: unsafe torch.load in production model-loading paths.

AST-based (no naive grep): finds real ``torch.load(...)`` CALLS in ``src/``
whose ``weights_only`` keyword is absent or explicitly False. Comments, doc
strings and strings are invisible to the AST walk, eliminating the false
positive class of text-based scans.

Production model/bundle loading MUST use ``weights_only=True``: arbitrary
pickle deserialization from a model file is arbitrary code execution.

An allowlist is intentionally SMALL and every entry documents why it
cannot be converted; new entries fail the review conversation, not just CI.

Exit codes: 0 = clean, 1 = unsafe call(s) found.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCAN_ROOT = REPO / "src"

# relpath -> reason the call cannot carry weights_only=True today.
# Every entry must be a REAL, reviewed exception — never a silent pass.
ALLOWLIST: dict[str, str] = {
    # (empty today: every production torch.load is weights_only=True)
}


def _is_torch_load(node: ast.Call) -> bool:
    f = node.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr == "load"
        and isinstance(f.value, ast.Name)
        and f.value.id == "torch"
    )


def _weights_only_kwarg(node: ast.Call) -> ast.keyword | None:
    for kw in node.keywords:
        if kw.arg == "weights_only":
            return kw
    return None


def check_file(path: Path) -> list[tuple[int, int, str]]:
    problems: list[tuple[int, int, str]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return problems  # unparseable files are caught by compile gates
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_torch_load(node):
            continue
        kw = _weights_only_kwarg(node)
        unsafe = kw is None or (isinstance(kw.value, ast.Constant) and kw.value.value is False)
        if unsafe:
            rel = path.relative_to(REPO).as_posix()
            problems.append((node.lineno, node.col_offset, rel))
    return problems


def main() -> int:
    violations: list[tuple[int, int, str]] = []
    for py in sorted(SCAN_ROOT.rglob("*.py")):
        rel = py.relative_to(REPO).as_posix()
        if rel in ALLOWLIST:
            continue
        for lineno, col, relpath in check_file(py):
            violations.append((lineno, col, relpath))
    if violations:
        print("UNSAFE torch.load CALLS (production sources must use weights_only=True):")
        for lineno, _col, rel in sorted(violations):
            print(f"  {rel}:{lineno}")
        print(
            "\nPolicy: model/bundle loading requires weights_only=True. Convert the "
            "call, or (rare, justified) add an ALLOWLIST entry with a reason in "
            "scripts/ci/check_torch_load_safety.py."
        )
        return 1
    print("torch.load guard OK: every torch.load in src/ is weights_only=True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
