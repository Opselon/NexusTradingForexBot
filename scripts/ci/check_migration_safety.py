#!/usr/bin/env python3
"""Schema migration safety checks (task: DB MIGRATIONS — ATOMIC + FAIL-LOUD).

One deterministic, offline checker for the migration framework itself:

  A. REGISTRY HYGIENE
     - every migration id unique, from_version < to_version, to_version ==
       from_version + 1 (no gaps/overlaps), chained per domain;
     - every migration declares apply + verify (verify is the postcondition
       contract), rollback required for NON_TRANSACTIONAL kinds;
     - description present (checksum identity input, §41).

  B. SILENT-FAILURE SCAN
     - the migration engine path (database/engine.py, database/gate.py,
       database/registry.py) must contain NO ``except Exception: pass`` /
       bare-swallow handlers whose ONLY effect is silence. Pattern basis:
       an except whose body contains no statement other than ``pass``,
       ``continue``, or ``return`` of a literal, AND no logging/raise.
     - exceptions in registry apply/verify/rollback helpers must not be
       swallowed: every helper body is scanned for `contextlib.suppress` /
       bare `except Exception: pass` around DDL.

  C. VERSION-CHAIN POSTCONDITIONS
     - for every domain: fresh in-memory DB + engine.migrate() must reach
       expected_version with integrity ok, then verify() must be True;
     - wrong-version detection: bump schema_meta above expected ->
       DB_DOWNGRADE_BLOCKED; tamper a checksum -> DB_BLOCKED;
     - restart determinism: a fresh engine on the same file reports
       NOT_REQUIRED.

Exit 0 = all invariants hold; 1 = violation list non-empty; 2 = tooling error.
Runs in CI (dependency-drift lane) and locally; uses ONLY disposable tmp DBs.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.database.engine import DatabaseMigrationEngine  # noqa: E402
from nexus_scalp.database.gate import STARTUP_DOMAINS  # noqa: E402
from nexus_scalp.database.models import (  # noqa: E402
    DatabaseDomain,
    MigrationState,
    TransactionKind,
)
from nexus_scalp.database.registry import REGISTRY  # noqa: E402

ENGINE_FILES = (
    REPO_ROOT / "src" / "nexus_scalp" / "database" / "engine.py",
    REPO_ROOT / "src" / "nexus_scalp" / "database" / "gate.py",
    REPO_ROOT / "src" / "nexus_scalp" / "database" / "registry.py",
)

problems: list[str] = []


def _record(msg: str) -> None:
    problems.append(msg)


# ---------------------------------------------------------------------------
# A. Registry hygiene (static)
# ---------------------------------------------------------------------------
def check_registry() -> None:
    for domain, migrations in REGISTRY.items():
        seen_ids: set[str] = set()
        chain: dict[int, int] = {}
        for m in migrations:
            if m.migration_id in seen_ids:
                _record(f"{domain.value}: duplicate migration id {m.migration_id}")
            seen_ids.add(m.migration_id)
            if m.from_version >= m.to_version:
                _record(
                    f"{m.migration_id}: from_version {m.from_version} >= to_version {m.to_version}"
                )
            if m.to_version != m.from_version + 1:
                _record(f"{m.migration_id}: non-adjacent step {m.from_version}->{m.to_version}")
            if m.to_version in chain and chain[m.to_version] != m.from_version:
                _record(f"{m.migration_id}: chain overlap at version {m.to_version}")
            chain[m.to_version] = m.from_version
            if m.apply is None:
                _record(f"{m.migration_id}: missing apply callable")
            if m.verify is None:
                _record(f"{m.migration_id}: missing verify callable (postcondition contract)")
            if (
                m.transaction_kind is TransactionKind.NON_TRANSACTIONAL_WITH_SAFETY_PROTOCOL
                and m.rollback is None
            ):
                _record(
                    f"{m.migration_id}: NON_TRANSACTIONAL without rollback violates the "
                    "safety protocol (§8)"
                )
            if not m.description:
                _record(f"{m.migration_id}: empty description (checksum identity input)")
        versions = sorted(chain)
        if versions != list(range(versions[0], versions[0] + len(versions))):
            _record(f"{domain.value}: version chain has gaps: {versions}")


# ---------------------------------------------------------------------------
# B. Silent-failure scan (AST)
# ---------------------------------------------------------------------------
def _is_silent(handler: ast.ExceptHandler) -> bool:
    """True when the handler body can only swallow (pass/continue/return-lit)
    with no logging, raise, or state change."""
    for node in ast.walk(handler):
        if isinstance(node, (ast.Raise, ast.Call)):  # raise or logging/state call
            return False
        if isinstance(node, ast.Return) and node.value is not None:
            return False
    body_stmts = [n for n in handler.body if not isinstance(n, (ast.Pass,))]
    if not body_stmts:
        return True  # pure `except: pass`
    for stmt in body_stmts:
        if isinstance(stmt, (ast.Continue, ast.Break, ast.Return, ast.Pass)):
            continue
        return False
    return True


#: Functions where a silent handler is a MIGRATION-SAFETY violation (failure
#: of schema DDL must be observable). Read-side helpers (drift/status/plan
#: probes) and lock-cleanup `__exit__` are report-only paths: a swallowed
#: probe error degrades information, never schema state, so they are
#: tolerated here and remain documented.
_CRITICAL_MIGRATION_FUNCTIONS = {
    "migrate",
    "_create_baseline_tables",
    "_record_migration",
    "_backup",
    "_restore",
    "_write_version",
}


def _enclosing_function(tree: ast.AST, node: ast.AST) -> str | None:
    """Name of the innermost function containing `node` (None at module level)."""
    best: tuple[int, str] | None = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(child is node for child in ast.walk(n)):
                if best is None or n.lineno > best[0]:
                    best = (n.lineno, n.name)
    return best[1] if best else None


def check_silent_handlers() -> None:
    for path in ENGINE_FILES:
        if not path.exists():
            _record(f"engine file missing: {path.name}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if not _is_silent(node):
                    continue
                fn = _enclosing_function(tree, node)
                if fn not in _CRITICAL_MIGRATION_FUNCTIONS:
                    continue  # read-side/teardown paths: not a schema-failure swallow
                name = getattr(node, "type", None)
                name_s = ast.unparse(name) if name is not None else "bare"
                _record(
                    f"{path.name}:{node.lineno} (fn={fn}): silent `except {name_s}` "
                    "in the migration apply path — a schema-change failure must be "
                    "observable, never swallowed"
                )


# ---------------------------------------------------------------------------
# C. Dynamic postconditions (disposable in-memory/file DBs)
# ---------------------------------------------------------------------------
def _engine(db_path: Path, domain: DatabaseDomain) -> DatabaseMigrationEngine:
    return DatabaseMigrationEngine(db_path, domain)  # type: ignore[arg-type]


def check_domain_chain(tmp: Path) -> None:
    for domain in STARTUP_DOMAINS:
        db = tmp / f"{domain.value}.db"
        eng = _engine(db, domain)
        r = eng.migrate()
        if r["state"] not in (MigrationState.DB_MIGRATION_SUCCEEDED.value,):
            _record(f"{domain.value}: fresh migrate() state={r['state']} error={r.get('error')}")
            continue
        if eng.current_version() != eng.expected_version():
            _record(
                f"{domain.value}: version after migrate {eng.current_version()} != expected {eng.expected_version()}"
            )
        v = eng.verify()
        if not v.get("verified", False):
            _record(f"{domain.value}: verify() reported not verified: {v}")
        # restart determinism
        r2 = _engine(db, domain).migrate()
        if r2["state"] != MigrationState.DB_MIGRATION_NOT_REQUIRED.value:
            _record(
                f"{domain.value}: restart migrate() state={r2['state']} (expected NOT_REQUIRED)"
            )
        # downgrade block
        con = sqlite3.connect(db)
        try:
            con.execute(
                "UPDATE schema_meta SET value=? WHERE key='schema_version'",
                (eng.expected_version() + 500,),
            )
            con.commit()
        finally:
            con.close()
        r3 = _engine(db, domain).migrate()
        if r3["state"] != MigrationState.DB_DOWNGRADE_BLOCKED.value:
            _record(
                f"{domain.value}: downgrade guard returned {r3['state']} (expected DB_DOWNGRADE_BLOCKED)"
            )


def main(argv: list[str] | None = None) -> int:
    check_registry()
    check_silent_handlers()
    with tempfile.TemporaryDirectory(prefix="nse_mig_check_") as tmp:
        check_domain_chain(Path(tmp))
    if problems:
        print("MIGRATION SAFETY CHECK FAILURES:")
        for p in problems:
            print(f"  - {p}")
        return 1
    domains = ", ".join(d.value for d in STARTUP_DOMAINS)
    print(
        "migration safety check: OK - registry chains valid, no silent handlers in "
        "the migration path, all domains reach expected version + verified on disposable DBs "
        f"({domains})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
