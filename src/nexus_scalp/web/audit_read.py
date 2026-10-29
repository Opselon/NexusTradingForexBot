"""Audit-domain READ resolution for the forensic trace probes (OBS-001).

``/api/debug/trace/{execution_id}`` is the flagship "one id across the whole
pipeline" join, and it used to resolve its database with
``db_path_for_domain("audit")`` — a bare ``<cwd>/artifacts/audit.db`` that
ignores every seam the rest of the audit domain honours:

  * the ``NEXUS_AUDIT_DB`` test-isolation seam (BUG-223 / CHG-0067), so a
    probe or test that pinned an audit database was silently read by nobody;
  * the persisted provider switch (``nexus db-portability``), so on a
    PostgreSQL box the route opened a SQLite file that either did not exist
    or held stale data.

Live evidence (OBS-001, audit 2026-08-31): the endpoint returned
``{available: false, reason: "TRACE_LOOKUP_ERROR: ..."}`` while ``/health``
reported READY — a dead forensic surface that reported itself healthy.

This module owns ONLY the resolution decision. The SQLite reads live in
:mod:`nexus_scalp.database.audit_read`, because the fabric guards (Phase 32)
correctly forbid domain code from depending on sqlite3; keeping the connection
in the infrastructure layer is the architecture, not a workaround.

Precedence mirrors :func:`nexus_scalp.database.ops_provider.resolve_audit_db_url`
exactly:

  1. the ``NEXUS_AUDIT_DB`` isolation seam (SQLite, wins over everything);
  2. the ACTIVE persisted provider — PostgreSQL reads go through the fabric's
     pooled READ plane (never the write plane, never an ad-hoc connection);
  3. the SQLite default.

Every read is read-only and bounded: a diagnostic probe can never mutate or
manufacture audit evidence.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from nexus_scalp.database import audit_read as _sqlite_infra
from nexus_scalp.database.audit_read import AuditReadError
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.web.audit_read")

#: Re-exported so callers import one symbol for "the audit read failed".
__all__ = [
    "FORENSIC_ROW_CAP",
    "AuditReadError",
    "audit_database_label",
    "audit_read_plane",
    "audit_table_columns",
    "query_audit_rows",
]

FORENSIC_ROW_CAP = _sqlite_infra.FORENSIC_ROW_CAP


def _sqlite_path_from_seam() -> Path | None:
    """The ``NEXUS_AUDIT_DB`` isolation seam, when set (BUG-223)."""
    seam = os.environ.get("NEXUS_AUDIT_DB", "").strip()
    return Path(seam) if seam else None


def _postgresql_active() -> bool:
    """Is the ACTIVE persisted audit provider PostgreSQL?

    Deliberately tolerant: a settings-store failure must degrade to the SQLite
    default rather than take the diagnostic surface down.
    """
    try:
        from nexus_scalp.database.ops_provider import active_provider_is_postgresql

        return bool(active_provider_is_postgresql("audit"))
    except Exception as exc:
        logger.debug(
            "[WEB-AUDIT] event=WEB_AUDIT_ACTIVE_PROVIDER_PROBE_FAILED "
            "error_type=%s (settings store unreadable; audit reads stay on "
            "the SQLite default)",
            type(exc).__name__,
        )
        return False


def audit_read_plane() -> Any:
    """The fabric's pooled READ plane for ``audit``, or ``None`` on SQLite.

    ``None`` when SQLite is active (callers use the SQLite infrastructure
    reader). Raises :class:`AuditReadError` when PostgreSQL is configured but
    no read plane can be resolved, so the caller degrades with a real reason
    instead of inventing a connection path.

    A read plane is only ever *read*: a backend that cannot ``query`` is the
    write-shaped plane and is refused outright.
    """
    if not _postgresql_active():
        return None
    from nexus_scalp.database.ops_provider import ensure_read_plane

    plane = ensure_read_plane("audit")
    if plane is None:
        raise AuditReadError("no audit read plane could be resolved for PostgreSQL")
    if not hasattr(plane, "query"):
        raise AuditReadError("resolved audit backend is write-shaped; refusing to read")
    return plane


def _sqlite_target() -> Path:
    """The SQLite database the probes should read (seam first, then default)."""
    seam = _sqlite_path_from_seam()
    if seam is not None:
        return seam
    from nexus_scalp.database.engine import db_path_for_domain

    return db_path_for_domain("audit")


def audit_database_label() -> str:
    """A public display label for the database a forensic read is served from."""
    seam = _sqlite_path_from_seam()
    if seam is not None:
        return str(seam)
    if _postgresql_active():
        return "postgresql://audit"
    return str(_sqlite_target())


def query_audit_rows(
    sql: str,
    args: tuple[Any, ...] = (),
    *,
    cap: int = FORENSIC_ROW_CAP,
) -> list[dict[str, Any]]:
    """Run one bounded READ-ONLY forensic query against the audit domain.

    ``sql`` must be a single ``SELECT``; the probes build their statements
    internally and always bind ids as parameters, so a trace lookup cannot
    become an injection vector.

    Raises :class:`AuditReadError` with a public-safe reason when the database
    cannot be read.
    """
    lowered = sql.lstrip().lower()
    if not lowered.startswith("select"):
        raise AuditReadError("forensic reads must be SELECT statements")

    plane = audit_read_plane()
    if plane is None:
        return _sqlite_infra.query_rows(_sqlite_target(), sql, args, cap=cap)
    rows = plane.query(sql, tuple(args))
    return [dict(r) for r in list(rows or [])[:cap]]


def audit_table_columns(table: str) -> list[str]:
    """Column names of an audit table, resolved for the ACTIVE provider.

    Used to schema-guard optional columns (``audit_signals.execution_id`` only
    exists after the trace-chain instrumentation) without issuing a query that
    fails on a provider whose schema predates it.
    """
    plane = audit_read_plane()
    if plane is None:
        return _sqlite_infra.table_columns(_sqlite_target(), table)
    # `?` qmark: the PostgreSQL driver translates placeholders itself
    # (`translate_sql_for_execution`), so both providers share one statement.
    rows = plane.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        (table,),
    )
    return [str(r.get("column_name")) for r in rows or [] if r.get("column_name")]
