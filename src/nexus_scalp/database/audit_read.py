"""Read-only SQLite access for forensic audit probes (infrastructure layer).

The fabric guards (``tests/unit/test_fabric_guards.py``, Phase 32) forbid
domain code from depending on sqlite3: a raw connection, a PRAGMA or a
``connect`` call outside ``database/`` / ``adapters/database/`` is a
regression. The forensic probes in ``web/`` therefore must not open the audit
database themselves — they resolve *which* database to read and delegate the
reading here, where SQLite-specific behaviour is allowed to live.

This module is deliberately narrow: it serves strictly read-only, bounded row
reads for diagnostics, and it never creates a database. ``mode=ro`` means a
missing file raises instead of materialising an empty one — a forensic probe
must not manufacture the evidence it was asked to read.

Provider selection is NOT made here. ``web/audit_read.py`` owns that
precedence (the ``NEXUS_AUDIT_DB`` isolation seam, then the persisted
provider, then the SQLite default) and calls in only once it has decided the
read is a SQLite read.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

#: Hard cap on rows a single forensic read may return.
FORENSIC_ROW_CAP = 5


class AuditReadError(RuntimeError):
    """A forensic read could not be served; ``str()`` is the public reason."""


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open an audit database strictly read-only (never creates the file)."""
    if not path.exists():
        raise AuditReadError(f"audit database not found: {path}")
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def query_rows(
    path: Path,
    sql: str,
    args: tuple[Any, ...] = (),
    *,
    cap: int = FORENSIC_ROW_CAP,
) -> list[dict[str, Any]]:
    """Run one bounded read-only query and return plain dicts."""
    con = open_readonly(path)
    try:
        return [dict(row) for row in con.execute(sql, args).fetchmany(cap)]
    except sqlite3.Error as exc:
        raise AuditReadError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        con.close()


def table_columns(path: Path, table: str) -> list[str]:
    """Column NAMES of a table.

    ``PRAGMA table_info`` yields ``(cid, name, type, notnull, dflt_value, pk)``
    — index 0 is the cid, so the name must be read by column key. Reading
    ``row[0]`` returns ``['0', '1', ...]`` and makes every schema guard
    conclude the column is absent.
    """
    con = open_readonly(path)
    try:
        return [str(row["name"]) for row in con.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error as exc:
        raise AuditReadError(f"{type(exc).__name__}: {exc}") from exc
    finally:
        con.close()


__all__ = [
    "FORENSIC_ROW_CAP",
    "AuditReadError",
    "open_readonly",
    "query_rows",
    "table_columns",
]
