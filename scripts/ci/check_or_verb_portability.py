#!/usr/bin/env python3
"""OR-verb portability gate.

Lane O task 4 — the coverage gap that hid the empty tables.

The parity lanes proved the suite was green because it never exercised the
broken path: several tests assert the ABSENCE of the SQLite OR-verb
(``INSERT OR REPLACE`` / ``INSERT OR IGNORE``) on the paths they observe,
while the producers those paths feed DO emit it and the generic
execute()/executemany() path had to learn to translate it (root cause #1 of
the empty live tables, per PG_VERIFIED_STATE.md).

A blanket "no OR-verb anywhere" rule would be wrong: the OR-verb is LEGAL
SQLite dialect on the SQLite path, and the audit write plane's
``translate_sql`` seam is *where* the rewrite belongs. The gate therefore
classifies each assertion site by what it is actually pinning:

  * ALLOWED  — an assertion that a TRANSLATING seam removed the OR-verb
               before it reached the provider (the path now needs the
               verb translated, and the test pins that it was).
  * ALLOWED  — an assertion scoped to the SQLite provider's own spellings.
  * FORBIDDEN — an assertion that the OR-verb is absent on a path that
               EMITS it and has no translating seam between the emitter
               and the provider (a real write went to a real PG and died
               silently, exactly the empty-table class).

This is a STATIC gate (AST + call-graph-ish source scan, no DB, no network):
it must run in CI on every push because a new test asserting the verb's
absence on a now-translating path would otherwise re-hide the same defect.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"
SRC_DIR = REPO_ROOT / "src"

OR_VERBS = ("INSERT OR REPLACE", "INSERT OR IGNORE")

#: Modules whose generic execute()/executemany() path a PG driver must
#: translate the OR-verb on (root cause #1 of the empty live tables).
#: Keyed by the dotted module so the scan can resolve cross-calls.
TRANSLATING_SEAMS: tuple[str, ...] = (
    # audit write plane: translate_sql() strips the OR-verb for the pooled
    # providers (adapters/database/audit_write_plane.py)
    "nexus_scalp.adapters.database.audit_write_plane",
    # PostgreSQLDriver.translate_sql / proxy rewrite the verbs for the
    # generic execute()/executemany() path (database/drivers/*)
    "nexus_scalp.database.drivers.postgres_driver",
    "nexus_scalp.database.drivers.proxy",
    "nexus_scalp.database.drivers.sqlite_driver",
    "nexus_scalp.database.review_lock",
)

#: Source modules that EMIT the OR-verb on a path a PG driver serves.
_EMITTER_RE = re.compile(r'["\']INSERT OR (REPLACE|IGNORE)[ "\']', re.I)


@dataclass
class Site:
    path: Path
    lineno: int
    col: int
    verb: str
    context: str = ""


@dataclass
class Finding:
    severity: str  # ERROR | ALLOWED | INFO
    path: Path
    lineno: int
    message: str


@dataclass
class State:
    findings: list[Finding] = field(default_factory=list)

    def error(self, path: Path, lineno: int, msg: str) -> None:
        self.findings.append(Finding("ERROR", path, lineno, msg))

    def allowed(self, path: Path, lineno: int, msg: str) -> None:
        self.findings.append(Finding("ALLOWED", path, lineno, msg))

    def info(self, path: Path, lineno: int, msg: str) -> None:
        self.findings.append(Finding("INFO", path, lineno, msg))


# ---------------------------------------------------------------------------
# 1. Find every test assertion site that pins the OR-verb's absence.
# ---------------------------------------------------------------------------

_ABSENCE_RE = re.compile(
    r'(?P<assert>assert(?:\s+not)?\s*)'
    r'(?P<q>["\'])(?P<verb>INSERT OR (?:REPLACE|IGNORE))(?P=q)'
    r'\s*(?P<op>not\s+in|in)\s*'
)


def _absence_sites(test_file: Path) -> list[Site]:
    """Literal ``assert "INSERT OR x" not in <expr>`` (and the reverse).

    Handles the two spellings the suite actually uses:
        assert "INSERT OR REPLACE" not in query
        assert "INSERT OR IGNORE" not in sql, sql
    """
    sites: list[Site] = []
    try:
        text = test_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return sites
    for m in _ABSENCE_RE.finditer(text):
        lineno = text.count("\n", 0, m.start()) + 1
        # Only "not in" is an absence assertion; "in" is a presence assertion.
        if "not in" not in m.group("op"):
            continue
        sites.append(
            Site(
                path=test_file,
                lineno=lineno,
                col=m.start() - (text.rfind("\n", 0, m.start()) + 1),
                verb=m.group("verb"),
                context=text.splitlines()[lineno - 1].strip() if lineno - 1 < len(text.splitlines()) else "",
            )
        )
    return sites


def _ast_absence_sites(test_file: Path) -> list[Site]:
    """AST view: catches ``assert x not in "INSERT OR ..."`` too."""
    sites: list[Site] = []
    try:
        tree = ast.parse(test_file.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return sites

    class _Visitor(ast.NodeVisitor):
        def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
            self.generic_visit(node)
            # `a not in b`  ->  Compare(left=a, ops=[NotIn], comparators=[b])
            if not any(isinstance(op, ast.NotIn) for op in node.ops):
                return
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    up = comparator.value.strip().upper()
                    if up in OR_VERBS:
                        sites.append(
                            Site(
                                path=test_file,
                                lineno=node.lineno,
                                col=node.col_offset,
                                verb=up,
                                context=test_file.read_text(encoding="utf-8", errors="replace")
                                .splitlines()[node.lineno - 1]
                                .strip(),
                            )
                        )

    _Visitor().visit(tree)
    return sites


# ---------------------------------------------------------------------------
# 2. Classify each site.
# ---------------------------------------------------------------------------

_SQLITE_SCOPE_RE = re.compile(
    r"sqlite|_is_sqlite|sqlite3|dialect_spellings|sqlite_path|direct_connection",
    re.I,
)
_TRANSLATING_SCOPE_RE = re.compile(
    r"translate_sql|on_conflict|ON CONFLICT|postgres|pg_plane|write_plane|"
    r"portable|provider|fabric|executemany|generic_execute",
    re.I,
)


def _module_emits_or_verb(mod_path: Path) -> bool:
    """Does this source module emit an OR-verb string literal?"""
    try:
        return bool(_EMITTER_RE.search(mod_path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


def _enclosing_scope(text: str, lineno: int) -> str:
    """The function/docstring context around a line (best-effort)."""
    lines = text.splitlines()
    start = max(0, lineno - 12)
    return "\n".join(lines[start:lineno + 3])


def classify(site: Site, state: State) -> None:
    text = site.path.read_text(encoding="utf-8", errors="replace")
    scope = _enclosing_scope(text, site.lineno)

    # A presence-adjacent assertion: the test asserts the *translated* shape
    # landed (ON CONFLICT) and that the OR-verb is gone — that is the CORRECT
    # pin for a path that now needs the verb translated.
    if re.search(r"ON CONFLICT", scope, re.I):
        state.allowed(
            site.path,
            site.lineno,
            f'assert "{site.verb}" not in ... on a path asserted to emit ON CONFLICT '
            "(translating seam is pinned — correct)",
        )
        return

    # Explicitly scoped to the SQLite provider's own spellings.
    if _SQLITE_SCOPE_RE.search(scope) and not _TRANSLATING_SCOPE_RE.search(scope):
        state.allowed(
            site.path,
            site.lineno,
            f'assert "{site.verb}" not in ... scoped to SQLite spellings (legal dialect there)',
        )
        return

    # DDL-only carve-out: schema/heal statements carry the SQLite spellings
    # the governed migration translates on the real plane (the same carve-out
    # the test itself documents — see test_pg_store_paths.py:409-414).
    if re.search(r"CREATE|DDL|ensure_schema|schema_heal|lstrip\(\)\.upper\(\)\.startswith\(\"CREATE\"\)", scope, re.I):
        state.allowed(
            site.path,
            site.lineno,
            f'assert "{site.verb}" not in ... DDL-scoped carve-out (governed migration translates it)',
        )
        return

    # The risky class: no translating seam named in the scope at all.
    state.error(
        site.path,
        site.lineno,
        f'assert "{site.verb}" not in ... on a path with NO translating seam in scope. '
        "Producers emit this verb and the generic PG execute() path must translate "
        "it; asserting its absence here means a real write to a real PostgreSQL "
        "was observed as silent-dead-letter (the empty-table class). Pin the "
        "TRANSLATION (ON CONFLICT), not the absence.",
    )


# ---------------------------------------------------------------------------
# 3. Cross-check: do the emitting source paths actually reach a seam?
# ---------------------------------------------------------------------------

def check_emitter_coverage(state: State) -> None:
    """Every source module emitting the OR-verb must reach a translating seam.

    This is the structural half of the gate: if a store emits
    ``INSERT OR REPLACE`` and no seam sits between it and the PG driver,
    a live PG write dies with a syntax error and the row count stays 0 —
    the exact defect that hid behind a green suite.
    """
    src_root = SRC_DIR
    if not src_root.is_dir():
        return
    for mod in src_root.rglob("*.py"):
        if not _module_emits_or_verb(mod):
            continue
        rel = mod.relative_to(src_root)
        dotted = "nexus_scalp." + ".".join(rel.with_suffix("").parts)
        try:
            text = mod.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Does this module route its writes through a translating seam?
        routed = any(seam in text for seam in TRANSLATING_SEAMS) or any(
            re.search(rf"\b{re.escape(seam.rsplit('.', 1)[-1])}\b", text)
            for seam in TRANSLATING_SEAMS
        )
        # The driver layer itself IS the seam.
        is_seam = dotted in TRANSLATING_SEAMS
        if routed or is_seam:
            continue
        state.info(
            mod,
            0,
            f"{dotted} emits an OR-verb literal and names no translating seam "
            "(audit_write_plane / drivers.proxy / drivers.postgres_driver). "
            "Verify its generic execute() path is covered.",
        )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_or_verb_portability")
    ap.add_argument("--tests-dir", type=Path, default=TESTS_DIR)
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--quiet", action="store_true", help="only ERROR findings")
    args = ap.parse_args(argv)

    state = State()

    if not args.tests_dir.is_dir():
        print(f"ERROR: tests dir not found: {args.tests_dir}", file=sys.stderr)
        return 2

    files = sorted(p for p in args.tests_dir.rglob("*.py") if p.is_file())

    for tf in files:
        sites = _absence_sites(tf)
        if not sites:
            sites = _ast_absence_sites(tf)
        for site in sites:
            classify(site, state)

    check_emitter_coverage(state)

    errors = [f for f in state.findings if f.severity == "ERROR"]
    allowed = [f for f in state.findings if f.severity == "ALLOWED"]
    infos = [f for f in state.findings if f.severity == "INFO"]

    if args.json:
        import json

        payload = {
            "errors": [
                {"file": str(f.path.relative_to(REPO_ROOT)), "line": f.lineno, "message": f.message}
                for f in errors
            ],
            "allowed": len(allowed),
            "emitter_warnings": [
                {"file": str(f.path.relative_to(REPO_ROOT)), "message": f.message} for f in infos
            ],
            "summary": {
                "error": len(errors),
                "allowed": len(allowed),
                "emitter_warnings": len(infos),
            },
        }
        print(json.dumps(payload, indent=2))
    else:
        for f in errors:
            print(f"ERROR {f.path.relative_to(REPO_ROOT)}:{f.lineno} — {f.message}")
        if not args.quiet:
            for f in allowed:
                print(f"  ok  {f.path.relative_to(REPO_ROOT)}:{f.lineno} — {f.message}")
            for f in infos:
                print(f"  ..  {f.path.relative_to(REPO_ROOT)} — {f.message}")
        print(
            f"\nSUMMARY: {len(errors)} ERROR, {len(allowed)} ALLOWED, "
            f"{len(infos)} emitter-coverage note(s)"
        )
        if errors:
            print(
                "\nOR-VERB PORTABILITY GATE FAILED — a test asserts the OR-verb's "
                "absence on a path that now needs it translated. Pin the "
                "translation, not the absence (see PG_VERIFIED_STATE.md root cause #1)."
            )

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
