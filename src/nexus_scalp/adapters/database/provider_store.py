"""Provider-aware persistence for the operational stores (DB-FABRIC-001/002).

The model_lifecycle / experience / intelligence stores all write OPERATIONAL
data to the canonical audit database. Their tables are part of the audit
domain's schema (they were provisioned on PostgreSQL together with it — see
``tests/unit/test_pg_schema_convergence.py``), so under PostgreSQL these
stores route through the EXISTING ``audit`` domain's pooled fabric backends.
No new fabric domain is registered: a second domain would point at a second
pool against the same database for tables the audit domain already owns.

The shadow / shadow70 / governance / hygiene / quarantine stores (DB-FABRIC-002)
are the other case: their tables are NOT part of the audit domain's schema
(the live nexusdb audit schema does not hold them), so they route through
their OWN domains (``ops_shadow`` / ``ops_hygiene``), which the fabric
bootstraps — schema included — on first use. The ``ops_*`` helpers below are
that path; they name the domain because a store's tables determine which pool
owns them, not the repository it was constructed with.

SQLite keeps the historical shape exactly: a short-lived connection to the
repository's own ``_db_path`` for reads, and the repository's background
write queue for writes. Nothing changes byte-for-byte on the SQLite path —
the helpers below are the SQLite path under SQLite, and the fabric path only
under PostgreSQL.

Writes
------
``queue_write`` is the one write entry point. On SQLite it puts on the
repository's background queue (``_queue.put_nowait``) as before; on
PostgreSQL it goes through the audit domain's pooled WRITE backend
(``PgWritePlane.execute``), which translates the ``?`` placeholders itself
and commits inside one borrowed pooled transaction.

Reads
-----
``query_rows`` / ``query_one`` / ``query_scalar`` are the read entry points.
On SQLite they open the repository's ``_connect_sqlite`` seam (the single
owner of the ``file:``-URI contract); on PostgreSQL they go through the
audit domain's pooled READ backend — a separate read-only pool, never a
writer connection.

Failure contract
----------------
Nothing here raises into a caller on a provider failure: a write failure is
logged and returns ``False`` (the store's documented contract), and a read
failure logs and returns the caller's documented empty default. A missing
backend (the domain is not provisioned) is OBSERVABLE — the store returns
its documented default and logs once — never a silent wrong-data path, and
never an invented ad-hoc connection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from nexus_scalp.adapters.database.audit_repository import AuditRepository

logger = get_logger("nexus_scalp.adapters.database.provider_store")

#: The fabric domain every operational store routes through under PostgreSQL.
#: Its schema already contains the model_lifecycle / experience / intelligence
#: tables, so this is the pooled backend those stores must resolve.
AUDIT_DOMAIN = "audit"

#: The fabric domain for the shadow / shadow70 / governance event tables
#: (DB-FABRIC-002). Those tables are NOT part of the audit domain's schema
#: (the live nexusdb audit schema does not hold them), so they need a domain
#: of their own for the fabric to provision them.
OPS_SHADOW_DOMAIN = "ops_shadow"

#: The fabric domain for the hygiene state + quarantine tables (DB-FABRIC-002),
#: which the audit domain's schema does not hold either.
OPS_HYGIENE_DOMAIN = "ops_hygiene"

#: The fabric domain for the News subsystem's own tables (news_articles,
#: news_sources, ... + calendar_events, which the news store provisions). The
#: news store resolves its pooled backend through the fabric directly; it is
#: declared here so the shared ``resolve_backend`` seam recognises the domain
#: and can bootstrap it the same way it bootstraps the ``ops_*`` domains.
NEWS_DOMAIN = "news"

#: Every domain an operational store may route through. ``resolve_backend``
#: bootstraps the domain the first time a process touches a pooled provider.
_KNOWN_DOMAINS: frozenset[str] = frozenset(
    {AUDIT_DOMAIN, OPS_SHADOW_DOMAIN, OPS_HYGIENE_DOMAIN, NEWS_DOMAIN}
)

#: Minimum spacing between repeated "not provisioned" warnings for one store.
#: The first occurrence always logs; later repeats are rate-limited so a hot
#: loop cannot flood the log while ``provider_write_degraded_total`` keeps
#: counting every degraded write.
_LOG_INTERVAL_SEC: float = 60.0

#: Per-process state for the rate limiter above: {key: last emission epoch}.
_log_state: dict[str, float] = {}


def _rate_limited(key: str) -> bool:
    """True when a warning for ``key`` may emit now (rate-limited)."""
    import time

    now = time.monotonic()
    last = _log_state.get(key)
    if last is not None and (now - float(last)) < _LOG_INTERVAL_SEC:
        return False
    _log_state[key] = now
    return True


def _is_sqlite(repo: Any) -> bool:
    """True when the repository is bound to the SQLite provider."""
    return bool(getattr(repo, "_is_sqlite", False))


def _write_backend(repo: Any, domain: str = AUDIT_DOMAIN, *, provision: bool = True) -> Any:
    """Resolve a domain's pooled WRITE backend, or None.

    Never invents a connection: when the domain is not provisioned for a
    pooled provider this returns None and the caller returns its documented
    default instead of writing through an ad-hoc path.

    For a domain with authored DDL (``ops_shadow`` / ``ops_hygiene``) the
    backend is bootstrapped on first use — without that, the first store on a
    PostgreSQL box would see an empty registry forever, since nothing else
    registers those domains (RTF-002, the audit domain's own pattern). The
    ``audit`` domain is provisioned by its repository instead, so it is only
    *read* here.
    """
    try:
        from nexus_scalp.database.fabric import get_domain_backend

        backend = get_domain_backend(domain, readonly=False)
        if backend is not None:
            return backend
    except Exception as exc:  # pragma: no cover - fabric import failure
        logger.warning(
            "[DB-FABRIC] %s write backend resolve failed: %s", domain, type(exc).__name__
        )
        return None
    if not provision or domain == AUDIT_DOMAIN or domain not in _KNOWN_DOMAINS:
        return None
    # A domain the fabric knows how to bootstrap: provision it from the
    # resolved DSN (the ACTIVE provider's connection, secret injected).
    try:
        from nexus_scalp.database.ops_provider import domain_dsn, resolve_pooled_backend

        return resolve_pooled_backend(domain, domain_dsn(domain))
    except Exception as exc:
        logger.error("[DB-FABRIC] %s domain provisioning failed: %s", domain, exc)
        return None


def _read_backend(repo: Any, domain: str = AUDIT_DOMAIN) -> Any:
    """Resolve a domain's pooled READ backend, or None.

    Reads never consume a writer connection: the read plane is a separate
    read-only pool (``SET default_transaction_read_only = on``). A
    write-shaped backend is refused outright, exactly like the audit
    repository's own read guard.
    """
    try:
        from nexus_scalp.database.fabric import get_domain_backend

        backend = get_domain_backend(domain, readonly=True)
    except Exception as exc:  # pragma: no cover - fabric import failure
        logger.warning("[DB-FABRIC] %s read backend resolve failed: %s", domain, type(exc).__name__)
        return None
    if backend is None:
        return None
    if hasattr(backend, "execute") or not hasattr(backend, "query"):
        return None
    return backend


def _degrade(operation: str, kind: str, detail: str, domain: str = AUDIT_DOMAIN) -> None:
    """Emit one observable degradation warning (rate-limited per operation)."""
    if _rate_limited(f"{operation}|{kind}"):
        logger.warning(
            "[DB-FABRIC] operational store %s degraded kind=%s: %s "
            "(domain %r is not provisioned for a pooled provider — the store "
            "returns its documented default; never a silent loss of data)",
            operation,
            kind,
            detail,
            domain,
        )


def _not_provisioned(operation: str, domain: str = AUDIT_DOMAIN) -> None:
    _degrade(operation, "not_provisioned", "no pooled write backend registered", domain)


def _read_not_provisioned(operation: str, domain: str = AUDIT_DOMAIN) -> None:
    _degrade(operation, "read_not_provisioned", "no pooled read backend registered", domain)


# ---------------------------------------------------------------------------
# Transactional writes (multi-statement state machines)
# ---------------------------------------------------------------------------


def queue_write_batch(
    repo: Any, statements: Sequence[tuple[str, Sequence[Any]]], *, operation: str = ""
) -> bool:
    """Apply several statements as ONE transaction on the ACTIVE provider.

    For state machines that must never expose a half-applied transition: the
    whole batch commits atomically, and any exception leaves the transaction
    rolled back so the caller's state stays consistent.

    SQLite: one ``with conn:`` block on the repository's writer connection.
    PostgreSQL: the audit domain's pooled write backend
    (``PgWritePlane.execute_batch``), which translates placeholders and
    commits one borrowed transaction.
    """
    if not statements:
        return True
    if _is_sqlite(repo):
        conn = None
        try:
            conn = sqlite_connection(repo, 5.0)
            with conn:  # transaction: commit on success, rollback on raise
                for query, args in statements:
                    conn.execute(query, tuple(args))
            return True
        except Exception as exc:
            logger.error(
                "[DB-FABRIC] operational batch write failed op=%s error=%s",
                operation or "?",
                exc,
            )
            return False
        finally:
            if conn is not None:
                with _Suppress():
                    conn.close()
    backend = _write_backend(repo, domain=AUDIT_DOMAIN)
    if backend is None:
        _not_provisioned(operation or "queue_write_batch")
        return False
    try:
        backend.execute_batch(list(statements))
        return True
    except Exception as exc:
        logger.error(
            "[DB-FABRIC] operational batch write failed op=%s error=%s",
            operation or "?",
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def queue_write(repo: Any, query: str, args: tuple[Any, ...], *, operation: str = "") -> bool:
    """Route one operational write to the ACTIVE provider.

    SQLite: the repository's background queue (unchanged historical path).
    PostgreSQL: the audit domain's pooled write backend — one translated,
    committed statement on a borrowed pooled connection.

    Returns ``True`` when the write was accepted, ``False`` on any failure
    (the store's documented contract — the caller never sees an exception).
    """
    if _is_sqlite(repo):
        queue = getattr(repo, "_queue", None)
        if queue is None:
            return False
        try:
            queue.put_nowait((query, args))
            return True
        except Exception as exc:
            logger.error(
                "[DB-FABRIC] operational write queue failed op=%s error=%s",
                operation or "?",
                exc,
            )
            return False

    backend = _write_backend(repo, domain=AUDIT_DOMAIN)
    if backend is None:
        _not_provisioned(operation or "queue_write")
        return False
    try:
        # The pooled write plane translates the ? placeholders itself and
        # commits inside the borrowed transaction.
        backend.execute(query, args)
        return True
    except Exception as exc:
        logger.error(
            "[DB-FABRIC] operational write failed op=%s error=%s",
            operation or "?",
            exc,
        )
        return False


def ops_queue_write(
    repo: Any,
    domain: str,
    query: str,
    args: tuple[Any, ...],
    *,
    operation: str = "",
) -> bool:
    """Route one operational write through a NAMED domain (DB-FABRIC-002).

    For tables that are NOT part of the audit domain's schema (the shadow /
    shadow70 / governance / hygiene / quarantine stores): the write goes
    through that domain's own pooled backend, which the fabric bootstraps
    (schema included) on first use.

    SQLite keeps the repository's background queue exactly as before — those
    tables all live in the audit database, so the single writer is still the
    correct queue.
    """
    if _is_sqlite(repo):
        return queue_write(repo, query, args, operation=operation)
    backend = _write_backend(repo, domain=domain)
    if backend is None:
        _not_provisioned(operation or "ops_queue_write", domain)
        return False
    try:
        backend.execute(query, args)
        return True
    except Exception as exc:
        logger.error(
            "[DB-FABRIC] operational write failed domain=%s op=%s error=%s",
            domain,
            operation or "?",
            exc,
        )
        return False


def ops_queue_write_batch(
    repo: Any,
    domain: str,
    statements: Sequence[tuple[str, Sequence[Any]]],
    *,
    operation: str = "",
) -> bool:
    """Apply several statements as ONE transaction through a NAMED domain.

    Same atomicity contract as :func:`queue_write_batch`, for stores whose
    state machine spans tables the audit domain does not own (e.g. an
    incident's quarantine move: ``INSERT OR REPLACE`` + ``UPDATE`` on
    quarantine tables that only the ``ops_hygiene`` domain provisions).
    """
    if not statements:
        return True
    if _is_sqlite(repo):
        return queue_write_batch(repo, statements, operation=operation)
    backend = _write_backend(repo, domain=domain)
    if backend is None:
        _not_provisioned(operation or "ops_queue_write_batch", domain)
        return False
    try:
        backend.execute_batch(list(statements))
        return True
    except Exception as exc:
        logger.error(
            "[DB-FABRIC] operational batch write failed domain=%s op=%s error=%s",
            domain,
            operation or "?",
            exc,
        )
        return False


def ops_ensure_schema(
    repo: Any,
    domain: str,
    *,
    operation: str = "",
) -> bool:
    """Provision a NAMED domain's schema on the ACTIVE provider (DB-FABRIC-002).

    Under SQLite this is a no-op: the store's own ``ensure_schema`` already
    created the tables in the audit database. Under PostgreSQL the fabric
    bootstraps the domain (``register_domain_backend`` provisions its
    translated DDL), so by the time a store reads or writes, its tables
    exist. Idempotent: the domain's statements are all ``IF NOT EXISTS``.
    """
    if _is_sqlite(repo):
        return True
    backend = _write_backend(repo, domain=domain, provision=True)
    if backend is None:
        _not_provisioned(operation or "ops_ensure_schema", domain)
        return False
    return True


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _sqlite_rows(repo: Any, sql: str, args: Sequence[Any]) -> list[dict[str, Any]]:
    """Run a read through the repository's one SQLite connection seam.

    Never imports ``sqlite3``: domain code routes through the repository's own
    ``_connect_sqlite`` seam (Phase 32's portability contract — see
    tests/unit/test_fabric_guards.py). Rows come back as dicts via
    ``dict(row)``, which the seam's ``sqlite3.Row`` factory already supports.
    """
    conn: Any = None
    try:
        conn = sqlite_connection(repo, 5.0)
        with conn:
            return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
    finally:
        if conn is not None:
            with _Suppress():
                conn.close()


def query_rows(
    repo: Any, sql: str, args: Sequence[Any] = (), *, operation: str = ""
) -> list[dict[str, Any]]:
    """Bounded row read on the ACTIVE provider (SQLite seam or PG read pool)."""
    if _is_sqlite(repo):
        try:
            return _sqlite_rows(repo, sql, args)
        except Exception as exc:
            logger.error(
                "[DB-FABRIC] operational read failed op=%s error=%s",
                operation or "?",
                exc,
            )
            return []
    backend = _read_backend(repo, domain=AUDIT_DOMAIN)
    if backend is None:
        _read_not_provisioned(operation or "query_rows")
        return []
    try:
        return list(backend.query(sql, args))
    except Exception as exc:
        logger.error(
            "[DB-FABRIC] operational read failed op=%s error=%s",
            operation or "?",
            exc,
        )
        return []


def query_one(
    repo: Any, sql: str, args: Sequence[Any] = (), *, operation: str = ""
) -> dict[str, Any] | None:
    """One row on the ACTIVE provider, or None when absent/unavailable."""
    rows = query_rows(repo, sql, args, operation=operation)
    return rows[0] if rows else None


def ops_query_rows(
    repo: Any,
    domain: str,
    sql: str,
    args: Sequence[Any] = (),
    *,
    operation: str = "",
) -> list[dict[str, Any]]:
    """Bounded row read through a NAMED domain (DB-FABRIC-002).

    For tables the audit domain does not own: the read goes through that
    domain's pooled READ backend (a separate read-only pool). SQLite keeps
    the repository's own connection seam, byte-identical to before.
    """
    if _is_sqlite(repo):
        return query_rows(repo, sql, args, operation=operation)
    backend = _read_backend(repo, domain=domain)
    if backend is None:
        _read_not_provisioned(operation or "ops_query_rows", domain)
        return []
    try:
        return list(backend.query(sql, args))
    except Exception as exc:
        logger.error(
            "[DB-FABRIC] operational read failed domain=%s op=%s error=%s",
            domain,
            operation or "?",
            exc,
        )
        return []


def ops_query_scalar(
    repo: Any,
    domain: str,
    sql: str,
    args: Sequence[Any] = (),
    *,
    operation: str = "",
) -> Any:
    """Single scalar through a NAMED domain, or None (DB-FABRIC-002)."""
    if _is_sqlite(repo):
        return query_scalar(repo, sql, args, operation=operation)
    backend = _read_backend(repo, domain=domain)
    if backend is None:
        _read_not_provisioned(operation or "ops_query_scalar", domain)
        return None
    try:
        return backend.scalar(sql, args)
    except Exception as exc:
        logger.error(
            "[DB-FABRIC] operational scalar read failed domain=%s op=%s error=%s",
            domain,
            operation or "?",
            exc,
        )
        return None


def query_scalar(repo: Any, sql: str, args: Sequence[Any] = (), *, operation: str = "") -> Any:
    """Single scalar on the ACTIVE provider (COUNT(*)/SUM/…), or None."""
    if _is_sqlite(repo):
        try:
            conn: Any = None
            try:
                conn = sqlite_connection(repo, 5.0)
                row = conn.execute(sql, tuple(args)).fetchone()
                return row[0] if row is not None else None
            finally:
                if conn is not None:
                    with _Suppress():
                        conn.close()
        except Exception as exc:
            logger.error(
                "[DB-FABRIC] operational scalar read failed op=%s error=%s",
                operation or "?",
                exc,
            )
            return None
    backend = _read_backend(repo, domain=AUDIT_DOMAIN)
    if backend is None:
        _read_not_provisioned(operation or "query_scalar")
        return None
    try:
        return backend.scalar(sql, args)
    except Exception as exc:
        logger.error(
            "[DB-FABRIC] operational scalar read failed op=%s error=%s",
            operation or "?",
            exc,
        )
        return None


class _Suppress:
    """contextlib.suppress(Exception) — inlined to keep the module dependency-free."""

    def __enter__(self) -> _Suppress:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return True


def sqlite_connection(repo: AuditRepository, timeout: float = 5.0) -> Any:
    """The repository's SQLite connection seam (for stores that borrow it).

    Raises ``RuntimeError`` for a non-SQLite repository: callers gate on
    ``_is_sqlite`` first, and a SQLite connection on a PostgreSQL box is a
    provider violation this must never paper over.
    """
    if not _is_sqlite(repo):
        raise RuntimeError(
            "sqlite_connection called on a non-SQLite repository "
            "(provider-wrong connection — route through the fabric instead)"
        )
    connect = getattr(repo, "_connect_sqlite", None)
    if callable(connect):
        return connect(timeout)
    raise RuntimeError(
        "SQLite repository has no _connect_sqlite seam — the repository owns "
        "the sqlite3 surface; a domain module opening sqlite3 directly is the "
        "Phase 32 portability violation this module exists not to be"
    )


def provider_name(repo: Any) -> str:
    """A short provider label for telemetry/diagnostics."""
    return "sqlite" if _is_sqlite(repo) else "postgresql"
