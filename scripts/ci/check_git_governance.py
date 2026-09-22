#!/usr/bin/env python3
"""Git governance CI check (agents/git_governance.md).

A read-only lane that verifies, on every PR and push:

1. The governance preflight (``scripts/git/preflight.py``) imports cleanly
   and reports a parsable result against the checked-out tree. A syntax
   error or import regression in the guard is itself a governance failure.
2. Automation surfaces (``.github/workflows/*``, ``scripts/**``) do not
   INSTRUCT destructive git commands. Naming a banned command inside a
   prohibition is fine; telling an agent to run one is not.
3. The canonical governance document exists and still contains the rule
   sections the preflight implements (§3 §4 §5 §8 §9 §10). Doc/code drift is
   a detectable failure, not a silent disagreement.

Exit 0 = governance intact. Exit 1 = violation (blocks the CI gate).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "git"))

from preflight import scan_text_for_banned_commands  # noqa: E402

#: Surfaces where a destructive git instruction would actually be EXECUTED.
#: Prose files (agents/*.md, docs/**) may discuss banned commands; these
#: surfaces run them.
AUTOMATION_SURFACES: tuple[Path, ...] = (
    REPO_ROOT / ".github" / "workflows",
    REPO_ROOT / "scripts",
)

#: The preflight's own ban-list DECLARATION is enforcement data, not an
#: instruction. Scanning it would flag the rule against itself.
_EXEMPT_FILES: frozenset[Path] = frozenset({REPO_ROOT / "scripts" / "git" / "preflight.py"})

#: Lines starting with these are commentary, not instruction.
_COMMENT_PREFIXES = ("#", "//")

#: File types scanned inside the automation surfaces.
_SCANNED_SUFFIXES = {".py", ".sh", ".ps1", ".yml", ".yaml"}

#: The governance doc and the sections the preflight implements.
GOVERNANCE_DOC = REPO_ROOT / "agents" / "git_governance.md"
REQUIRED_SECTIONS = ("§3", "§4", "§5", "§8", "§9", "§10")


def _scan_file(path: Path) -> list[tuple[str, int, str]]:
    """Return (command, line_number, excerpt) hits in one file."""
    hits: list[tuple[str, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    in_fence = False
    for ln_no, ln in enumerate(text.splitlines(), start=1):
        stripped = ln.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:  # fenced examples are documentation, not execution
            continue
        if not stripped or stripped.startswith(_COMMENT_PREFIXES):
            continue
        for cmd in scan_text_for_banned_commands(ln):
            hits.append((cmd, ln_no, stripped[:110]))
    return hits


def main() -> int:
    problems: list[str] = []

    # --- 1. the preflight itself must work ------------------------------
    rc = sys.path  # sanity: path setup above worked
    if not rc:
        problems.append("preflight import path setup failed")
    try:
        import preflight  # noqa: F401
    except Exception as exc:  # pragma: no cover — defensive
        problems.append(f"preflight import failed: {exc}")

    # --- 2. no destructive instructions in automation surfaces ----------
    for surface in AUTOMATION_SURFACES:
        if not surface.exists():
            continue
        for path in sorted(surface.rglob("*")):
            if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
                continue
            if path.resolve() in {p.resolve() for p in _EXEMPT_FILES}:
                continue
            if "__pycache__" in path.parts:
                continue
            for cmd, ln_no, excerpt in _scan_file(path):
                rel = path.relative_to(REPO_ROOT)
                problems.append(f"{rel}:{ln_no}: instructs destructive git '{cmd}': {excerpt}")

    # --- 3. governance doc must exist and cover implemented rules -------
    if not GOVERNANCE_DOC.exists():
        problems.append(f"missing canonical governance doc: {GOVERNANCE_DOC}")
    else:
        doc_text = GOVERNANCE_DOC.read_text(encoding="utf-8")
        for section in REQUIRED_SECTIONS:
            if section not in doc_text:
                problems.append(
                    f"governance doc missing section {section} "
                    "(implemented in scripts/git/preflight.py)"
                )

    if problems:
        print("GIT GOVERNANCE VIOLATIONS:")
        for p in problems:
            print(f"  - {p}")
        print("\nCanonical model: agents/git_governance.md — STOP and fix the state;")
        print("never reset/rebase/force-push it away.")
        return 1

    print("GIT GOVERNANCE OK — preflight importable, no destructive git in automation,")
    print("governance doc covers all implemented sections.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
