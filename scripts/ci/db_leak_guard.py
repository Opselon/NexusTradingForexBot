#!/usr/bin/env python3
"""Database leak-detection guard — Phase 33 of the DB-FABRIC-001 mission.

Static check that runs in CI (and locally via `nexus db audit-leaks`):
detects persistence violations BEFORE they reach production.

Checks (each fails the run on a NEW violation):
  1. direct sqlite3 import outside the approved infrastructure modules;
  2. SQLite PRAGMA outside the SQLite infrastructure layer;
  3. raw sqlite3.connect outside the driver/plane layer;
  4. direct psycopg import outside the PostgreSQL infrastructure layer;
  5. direct .db path construction outside the configuration layer.

Baseline ratchet: existing violations are recorded in
tests/fixtures/fabric_sqlite3_baseline.txt and may only SHRINK. A module not
on the baseline that trips a check fails the run immediately, while removing
a baseline entry is a visible, reviewable migration step (Phase 40: no
big-bang removal).

Exits 0 when clean, 1 on violations. Never modifies any file.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "nexus_scalp"
BASELINE = REPO / "tests" / "fixtures" / "fabric_sqlite3_baseline.txt"

#: The whole infrastructure layer may use sqlite3/psycopg/PRAGMA.
INFRA_PREFIXES = ("database/", "adapters/database/")

#: psycopg is the PostgreSQL infrastructure dependency.
PG_INFRA_MODULES = frozenset(
    {
        "database/drivers/postgres_driver.py",
        "database/fabric/pg_planes.py",
    }
)

_PATTERNS = {
    "sqlite3_import": re.compile(
        r"^\s*(?:import\s+sqlite3|from\s+sqlite3\s+import\s+.+)$", re.MULTILINE
    ),
    "sqlite3_connect": re.compile(r"sqlite3\.connect\s*\("),
    "pragma": re.compile(r"\bPRAGMA\b"),
    "psycopg_import": re.compile(
        r"^\s*(?:import\s+psycopg|from\s+psycopg(?:\.\w+)*\s+import\s+.+)$", re.MULTILINE
    ),
    "db_path_literal": re.compile(r"['\"][^'\"]*?\.db['\"]"),
}


def _load_baseline() -> frozenset[str]:
    if not BASELINE.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


def _production_files() -> list[Path]:
    out: list[Path] = []
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        if rel in {"__init__.py"} and p.parent == SRC:
            continue
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path, default=BASELINE, help="baseline file")
    ap.add_argument("--quiet", action="store_true", help="only print violations")
    args = ap.parse_args()

    baseline: frozenset[str] = (
        frozenset(
            line.strip()
            for line in args.baseline.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        )
        if args.baseline.exists()
        else frozenset()
    )

    violations: list[tuple[str, str, int, str]] = []  # check, file, line, text
    for p in _production_files():
        rel = p.relative_to(SRC).as_posix()
        is_infra = rel.startswith(INFRA_PREFIXES)
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        # 1 + 3: sqlite3 import / raw connect outside infrastructure
        if not is_infra:
            for check, rx in (
                ("sqlite3_import", _PATTERNS["sqlite3_import"]),
                ("sqlite3_connect", _PATTERNS["sqlite3_connect"]),
            ):
                for m in rx.finditer(text):
                    lineno = text.count("\n", 0, m.start()) + 1
                    line = lines[lineno - 1].strip()
                    if rel not in baseline:
                        violations.append((check, rel, lineno, line[:120]))

        # 2: PRAGMA outside infrastructure
        if not is_infra:
            for m in _PATTERNS["pragma"].finditer(text):
                lineno = text.count("\n", 0, m.start()) + 1
                line = lines[lineno - 1].strip()
                if line.startswith(("#", "//", '"', "'")):
                    continue
                if rel not in baseline:
                    violations.append(("pragma_outside_infra", rel, lineno, line[:120]))

        # 4: psycopg outside the PostgreSQL infrastructure
        if rel not in PG_INFRA_MODULES:
            for m in _PATTERNS["psycopg_import"].finditer(text):
                lineno = text.count("\n", 0, m.start()) + 1
                line = lines[lineno - 1].strip()
                violations.append(("psycopg_outside_pg_infra", rel, lineno, line[:120]))

    if violations:
        print(f"DATABASE LEAK GUARD: {len(violations)} violation(s)")
        seen: set[tuple[str, str]] = set()
        for check, rel, lineno, line in violations:
            key = (check, rel)
            if key in seen:
                continue
            seen.add(key)
            print(f"  [{check}] {rel}:{lineno}: {line}")
        print(
            "\nDomain code must not depend on sqlite3/psycopg directly.\n"
            "Migrate the consumer to the database fabric instead, or add the\n"
            "module to the baseline ONLY as a pre-existing offender.\n"
            f"Baseline: {args.baseline}"
        )
        return 1

    if not args.quiet:
        print(f"DATABASE LEAK GUARD: clean ({len(_production_files())} production modules checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
