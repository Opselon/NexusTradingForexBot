"""Audit-domain READ access for the forensic trace probes.

``/api/debug/trace/{execution_id}`` is the flagship "one id across the whole
pipeline" join, and it used to resolve its database with
``db_path_for_domain("audit")`` — a bare ``<cwd>/artifacts/audit.db`` that
ignores every seam the rest of the audit domain honours:

  * the ``NEXUS_AUDIT_DB`` test-isolation seam (BUG-223 / CHG-0067), so a
    probe or test that pins an audit database was silently read by nobody;
  * the persisted provider switch (``nexus db-portability``), so on a
    PostgreSQL box the route opened a SQLite file that either did not exist
    or held stale data.

Live evidence (OBS-001, audit 2026-08-31): the endpoint returned
``{available: false, reason: "TRACE_LOOKUP_ERROR: No module named
nexus_scalp.adapters.audit_db"}`` while ``/health`` reported READY — a dead
forensic surface that reported itself healthy.

This module is the single resolution point for those probes. It mirrors
:func:`nexus_scalp.database.ops_provider.resolve_audit_db_url` precedence
exactly:

  1. the ``NEXUS_AUDIT_DB`` isolation seam (SQLite, wins over everything);
  2. the ACTIVE persisted provider — PostgreSQL reads go through the fabric's
     pooled READ plane (never the write plane, never an ad-hoc connection);
  3. the SQLite default.

Reads are strictly READ-ONLY: the SQLite branch opens ``mode=ro`` and the
PostgreSQL branch refuses a backend that is not read-shaped, so a diagnostic
probe can never mutate an audit database.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

#: Hard cap on rows any single forensic query may return. The probes are
#: operator-facing and never stream a whole audit table.
FORENSIC_ROW_CAP = 5


class AuditReadError(RuntimeError):
    """A forensic read could not be served; ``str()`` is the public reason."""


def _sqlite_path_from_seam() -> Path | None:
    """The ``NEXUS_AUDIT_DB`` isolation seam, when set (BUG-223)."""
    seam = os.environ.get("NEXUS_AUDIT_DB", "").strip()
    return Path(seam) if seam else None


def _open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    """Open an audit SQLite database strictly read-only.

    ``mode=ro`` (not ``rw``/``rwc``) means a missing file raises instead of
    silently creating an empty one — a forensic probe must never manufacture
    the evidence it is asked to read.
    """
    if not path.exists():
        raise AuditReadError(f"audit database not found: {path}")
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _sqlite_columns(con: sqlite3.Connection, table: str) -> list[str]:
    """Column NAMES of a table.

    ``PRAGMA table_info`` yields ``(cid, name, type, notnull, dflt_value, pk)``
    — index 0 is the cid, so the name must be read by column key. Reading
    ``row[0]`` returned ``['0', '1', ...]`` and made every schema guard report
    the column as absent.
    """
    return [str(row["name"]) for row in con.execute(f"PRAGMA table_info({table})")]


def audit_read_plane() -> Any:
    """The fabric's pooled READ plane for ``audit``, or ``None`` on SQLite.

    Returns ``None`` when the active provider is SQLite (the caller uses
    :func:`_open_sqlite_readonly`) and also when PostgreSQL is configured but
    no read plane can be resolved — the caller then degrades observably with a
    real reason instead of inventing a connection path.

    A read plane is only ever *read*: a backend exposing ``execute`` (the
    write-shaped plane) is refused outright.
    """
    from nexus_scalp.database.ops_provider import (
        active_provider_is_postgresql,
        ensure_read_plane,
    )

    if not active_provider_is_postgresql("audit"):
        return None
    plane = ensure_read_plane("audit")
    if plane is None:
        raise AuditReadError("no audit read plane could be resolved for PostgreSQL")
    if hasattr(plane, "execute") and not hasattr(plane, "query"):
        raise AuditReadError("resolved audit backend is write-shaped; refusing to read")
    return plane


def audit_database_label() -> str:
    """A display label for the database a forensic read is served from."""
    seam = _sqlite_path_from_seam()
    if seam is not None:
        return str(seam)
    try:
        from nexus_scalp.database.ops_provider import active_provider_is_postgresql

        if active_provider_is_postgresql("audit"):
            return "postgresql://audit"
    except Exception:
        pass
    from nexus_scalp.database.engine import db_path_for_domain

    return str(db_path_for_domain("audit"))


def query_audit_rows(
    sql: str,
    args: tuple[Any, ...] = (),
    *,
    cap: int = FORENSIC_ROW_CAP,
) -> list[dict[str, Any]]:
    """Run one bounded READ-ONLY forensic query against the audit domain.

    ``sql`` must be a single ``SELECT``; the probes build their statements
    internally and never interpolate caller input into the SQL text (ids are
    always bound parameters), so a trace lookup cannot become an injection
    vector.

    Raises :class:`AuditReadError` with a public-safe reason when the database
    cannot be read — the route turns that into ``available: false`` plus the
    reason, and never into a silent empty result.
    """
    lowered = sql.lstrip().lower()
    if not lowered.startswith("select"):
        raise AuditReadError("forensic reads must be SELECT statements")

    seam = _sqlite_path_from_seam()
    if seam is not None:
        con = _open_sqlite_readonly(seam)
        try:
            return [dict(r) for r in con.execute(sql, args).fetchmany(cap)]
        finally:
            con.close()

    plane = audit_read_plane()
    if plane is None:
        # SQLite default (no seam, no PostgreSQL): same read-only discipline.
        from nexus_scalp.database.engine import db_path_for_domain

        con = _open_sqlite_readonly(db_path_for_domain("audit"))
        try:
            return [dict(r) for r in con.execute(sql, args).fetchmany(cap)]
        finally:
            con.close()

    rows = plane.query(sql, tuple(args))
    return [dict(r) for r in list(rows or [])[:cap]]


def audit_table_columns(table: str) -> list[str]:
    """Column names of an audit table, resolved for the ACTIVE provider.

    Used to schema-guard optional columns (``audit_signals.execution_id`` only
    exists after the trace-chain instrumentation) without ever issuing a
    failing query on a provider whose schema predates it.
    """
    seam = _sqlite_path_from_seam()
    if seam is not None:
        con = _open_sqlite_readonly(seam)
        try:
            return _sqlite_columns(con, table)
        finally:
            con.close()

    plane = audit_read_plane()
    if plane is None:
        from nexus_scalp.database.engine import db_path_for_domain

        con = _open_sqlite_readonly(db_path_for_domain("audit"))
        try:
            return _sqlite_columns(con, table)
        finally:
            con.close()

    # `?` qmark: the PostgreSQL driver translates placeholders itself
    # (`translate_sql_for_execution`), so both providers share one statement.
    rows = plane.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        (table,),
    )
    return [str(r.get("column_name")) for r in rows or [] if r.get("column_name")]


__all__ = [
    "FORENSIC_ROW_CAP",
    "AuditReadError",
    "audit_database_label",
    "audit_read_plane",
    "audit_table_columns",
    "query_audit_rows",
]
