"""PostgreSQL read/write planes with psycopg v3 pooling.

Phase 6 of the DATABASE FABRIC mission (DB-FABRIC-001).

  * separate read pool and write pool (``psycopg_pool.ConnectionPool``);
  * read connections are read-only by construction:
    ``SET default_transaction_read_only = on`` — the server rejects
    mutations, not just our convention;
  * every connection receives session configuration (statement_timeout,
    ``jit=off`` for stable p99, ``application_name`` for observability);
  * configurable min/max size, connection timeout, idle timeout, health
    checks, pool-exhaustion metrics, graceful shutdown;
  * optional read-replica pool for EVENTUAL reads only.

The pool is what turns a ~104ms cold connect into a sub-millisecond
checkout.  Connections are never held across requests, and cursors are
always closed (``with conn.cursor()``).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

logger = get_logger("nexus_scalp.database.fabric.pg_planes")

#: Session settings applied to EVERY pooled connection (no secrets).
_SESSION_BASE_SQL = "SET jit = off"

#: Session settings for READ connections.
_READ_SESSION_SQL = "SET default_transaction_read_only = on"


@dataclass(frozen=True)
class PoolLimits:
    """Pool sizing + timeouts.  Zero means "use the driver default"."""

    min_size: int = 2
    max_size: int = 10
    connect_timeout_sec: int = 10
    statement_timeout_ms: int = 0
    idle_timeout_sec: int = 0
    max_lifetime_sec: int = 0
    health_check_interval_sec: int = 30
    #: psycopg_pool 3.3's default ``reconnect_timeout`` is 300s: a background
    #: worker keeps retrying an unreachable host for five minutes before it
    #: gives up. On a dead DSN (or a server that died mid-run) that is a
    #: 300s+ hang on every pool the fabric opens, and the pool survives long
    #: enough to be re-provisioned on top of itself. Bound it to the same
    #: order as the connection timeout so an unreachable provider fails fast.
    reconnect_timeout_sec: float = 30.0

    def __post_init__(self) -> None:
        if self.max_size < 1:
            object.__setattr__(self, "max_size", 1)
        if self.min_size < 0:
            object.__setattr__(self, "min_size", 0)
        if self.min_size > self.max_size:
            object.__setattr__(self, "min_size", self.max_size)


class PgPool:
    """One psycopg_pool connection pool over a PostgreSQL DSN.

    Wraps ``psycopg_pool.ConnectionPool`` so the fabric owns lifecycle,
    session configuration, metrics and masking.  The raw DSN is never logged.
    """

    __slots__ = ("_closed", "_dsn", "_limits", "_name", "_pool", "_readonly")

    def __init__(
        self,
        dsn: str,
        limits: PoolLimits,
        *,
        readonly: bool = False,
        name: str = "",
    ) -> None:
        self._dsn = dsn
        self._limits = limits
        self._readonly = readonly
        self._name = name or ("pg-read" if readonly else "pg-write")
        self._pool: Any = None
        self._closed = False

    # -- lifecycle --------------------------------------------------------

    @property
    def available(self) -> bool:
        """True when psycopg_pool is importable (optional dependency)."""
        try:
            import psycopg_pool  # noqa: F401

            return True
        except ImportError:
            return False

    def open(self) -> None:
        if self._pool is not None and not self._closed:
            return  # already open
        psycopg_pool = self._require_psycopg_pool()
        # A pool that was closed before may be reopened: clear the sentinel so
        # the fresh psycopg_pool is accepted (close() sets it permanently).
        self._closed = False
        kwargs: dict[str, Any] = {
            "min_size": self._limits.min_size,
            "max_size": self._limits.max_size,
            "timeout": float(self._limits.connect_timeout_sec),
        }
        if self._limits.idle_timeout_sec:
            kwargs["max_idle"] = float(self._limits.idle_timeout_sec)
        if self._limits.max_lifetime_sec:
            kwargs["max_lifetime"] = float(self._limits.max_lifetime_sec)
        if self._limits.health_check_interval_sec:
            # psycopg_pool 3.3: `check` is a callback invoked before lending a
            # connection; raising there makes the pool discard and replace it.
            # The probe interval is the pool's own scheduling concern.
            kwargs["check"] = self._pool_check
        if self._limits.reconnect_timeout_sec:
            # psycopg_pool 3.3 defaults reconnect_timeout to 300s, which makes
            # a dead DSN hang a background worker for five minutes per pool.
            kwargs["reconnect_timeout"] = float(self._limits.reconnect_timeout_sec)
        # The password is NEVER embedded in the DSN the pool holds: psycopg
        # takes it as a separate connection parameter, resolved from the
        # OS-backed secret store exactly as the existing driver does.  The
        # pool's conninfo therefore carries only the non-secret parts and the
        # credential is injected per connection attempt.
        conninfo, connect_kwargs = _split_dsn_secret(self._dsn)
        self._pool = psycopg_pool.ConnectionPool(
            conninfo=conninfo,
            name=self._name,
            configure=self._configure_connection,
            # psycopg_pool forwards ``kwargs`` to psycopg.connect, which is
            # how the password reaches the auth exchange: it must NOT live in
            # the conninfo the pool retains.
            kwargs=connect_kwargs,
            **kwargs,
        )
        logger.debug(
            "pg pool opened name=%s readonly=%s",
            self._name,
            self._readonly,
        )

    def close(self) -> None:
        pool = self._pool
        self._pool = None
        self._closed = True
        if pool is not None:
            with contextlib_suppress():
                pool.close(wait=2.0)

    # -- connection configuration -----------------------------------------

    def _configure_connection(self, conn: Any) -> None:
        """Apply session settings to a freshly checked-out connection.

        psycopg's pool `configure` hook runs while the connection is still in
        its initial state: autocommit is OFF, so an unexecuted ``SET`` leaves
        an open transaction that makes the pool mark the connection INERROR.
        Enable autocommit first, then every statement is a one-shot command.

        ``SET`` accepts no parameters in PostgreSQL, so application_name is
        set through the standard literal form.  ``self._name`` is a fabric
        constant (never user input), and it is validated to a conservative
        identifier shape before use.
        """

        try:
            conn.autocommit = True
            conn.execute(_SESSION_BASE_SQL)
            if self._readonly:
                conn.execute(_READ_SESSION_SQL)
            if self._limits.statement_timeout_ms:
                conn.execute(f"SET statement_timeout = {int(self._limits.statement_timeout_ms)}")
            app_name = _sanitize_app_name(self._name)
            conn.execute(f"SET application_name = '{app_name}'")
        except Exception:
            # A connection we cannot configure is useless; let the pool
            # discard and replace it rather than lending a bad handle.
            with contextlib_suppress():
                conn.close()
            raise

    # -- health check -----------------------------------------------------

    @property
    def name(self) -> str:
        """Pool identity for logs/metrics (never the DSN)."""
        return self._name

    @staticmethod
    def _pool_check(conn: Any) -> None:
        """Readiness probe: raise if the connection is dead.

        psycopg_pool calls this before lending a connection; an exception
        makes the pool discard and replace the connection.  The probe runs
        BEFORE ``configure`` on a fresh connection, so the connection is
        still in its default (autocommit-off) state: an executed statement
        opens a transaction the pool then has to roll back.  Run the probe
        in an explicit autocommit context to leave the connection clean.
        """
        try:
            was_autocommit = conn.autocommit
            if not was_autocommit:
                conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            if not was_autocommit:
                conn.autocommit = False
        except Exception:
            # The pool treats a raised check as "unhealthy" and reconnects.
            with contextlib_suppress():
                conn.close()
            raise

    # -- checkout ---------------------------------------------------------

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Check out a configured connection; always returned to the pool.

        A checkout failure is classified and logged here (pool name, the
        failure class — checkout timeout vs server-side — and the pool stats)
        so a dead pooled backend is traceable instead of surfacing as a
        generic exception. Re-raises: observability never changes the
        failure contract.
        """
        from nexus_scalp.database.query_logging import log_pool_failure

        if self._pool is None:
            # Not-open is a programming/ordering error, not a pool condition:
            # classify and log it the same way so it is traceable in the log
            # instead of appearing as a bare RuntimeError to the caller.
            err = RuntimeError(f"pg pool {self._name!r} is not open")
            with contextlib_suppress():
                log_pool_failure(
                    pool_name=self._name,
                    operation="checkout",
                    exc=err,
                    stats=self.stats(),
                )
            raise err
        try:
            conn = self._pool.getconn()
        except BaseException as exc:
            with contextlib_suppress():
                log_pool_failure(
                    pool_name=self._name,
                    operation="checkout",
                    exc=exc,
                    stats=self.stats(),
                )
            raise
        try:
            yield conn
        finally:
            with contextlib_suppress():
                self._pool.putconn(conn)

    # -- query ------------------------------------------------------------

    def query(self, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver
        from nexus_scalp.database.query_logging import query_timer

        translated = PostgreSQLDriver.translate_sql(sql)
        with (
            query_timer("query", sql, domain=self._name) as timer,
            self.connection() as conn,
            conn.cursor() as cur,
        ):
            cur.execute(translated, tuple(args))
            cols = [d.name for d in cur.description] if cur.description else []
            rows = [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
            # Row count set INSIDE the block: the timer renders it on __exit__.
            timer.rows = len(rows)
        return rows

    def query_one(self, sql: str, args: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: Sequence[Any] = ()) -> Any:
        from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver
        from nexus_scalp.database.query_logging import query_timer

        translated = PostgreSQLDriver.translate_sql(sql)
        with (
            query_timer("scalar", sql, domain=self._name),
            self.connection() as conn,
            conn.cursor() as cur,
        ):
            cur.execute(translated, tuple(args))
            row = cur.fetchone()
            return row[0] if row is not None else None

    # -- stats ------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        pool = self._pool
        if pool is None:
            return {"name": self._name, "open": False}
        with contextlib_suppress():
            return {
                "name": self._name,
                "open": True,
                "size": getattr(pool, "size", lambda: -1)(),
                "available": getattr(pool, "get_stats", lambda: {})(),
            }
        return {"name": self._name, "open": True}

    # -- internals --------------------------------------------------------

    @staticmethod
    def _require_psycopg_pool() -> Any:
        try:
            import psycopg_pool

            return psycopg_pool
        except ImportError as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "PostgreSQL pooling requires psycopg_pool. Run: pip install 'nexus[postgres]'"
            ) from exc


class PgReadPlane:
    """Read plane for one PostgreSQL database: read pool + optional replica."""

    __slots__ = ("_limits", "_primary", "_replica")

    def __init__(
        self,
        primary_dsn: str,
        limits: PoolLimits,
        *,
        replica_dsn: str = "",
    ) -> None:
        self._limits = limits
        self._primary = PgPool(primary_dsn, limits, readonly=True, name="pg-read")
        self._replica = (
            PgPool(replica_dsn, limits, readonly=True, name="pg-read-replica")
            if replica_dsn
            else None
        )

    @property
    def has_replica(self) -> bool:
        return self._replica is not None

    def open(self) -> None:
        self._primary.open()
        if self._replica is not None:
            with contextlib_suppress():
                self._replica.open()

    def close(self) -> None:
        self._primary.close()
        if self._replica is not None:
            self._replica.close()

    def connection(self, *, allow_replica: bool = False) -> Any:
        """Pick a read connection: replica only for EVENTUAL reads."""
        pool = self._replica if (allow_replica and self._replica is not None) else self._primary
        return pool.connection()

    def query(
        self, sql: str, args: Sequence[Any] = (), *, allow_replica: bool = False
    ) -> list[dict[str, Any]]:
        pool = self._replica if (allow_replica and self._replica is not None) else self._primary
        return pool.query(sql, args)

    def query_one(
        self, sql: str, args: Sequence[Any] = (), *, allow_replica: bool = False
    ) -> dict[str, Any] | None:
        rows = self.query(sql, args, allow_replica=allow_replica)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: Sequence[Any] = (), *, allow_replica: bool = False) -> Any:
        pool = self._replica if (allow_replica and self._replica is not None) else self._primary
        return pool.scalar(sql, args)


class PgWritePlane:
    """Write plane for one PostgreSQL database: pooled writes + batching.

    Reuses the fabric's write-item queue semantics so SQLite and PostgreSQL
    share the same backpressure/overflow/idempotency contract.  Unlike
    SQLite, PostgreSQL accepts concurrent writers; the pool is the boundary,
    so writes still batch into bounded transactions.
    """

    __slots__ = ("_limits", "_pool")

    def __init__(self, write_dsn: str, limits: PoolLimits) -> None:
        self._limits = limits
        self._pool = PgPool(write_dsn, limits, readonly=False, name="pg-write")

    @property
    def pool(self) -> PgPool:
        return self._pool

    def open(self) -> None:
        # PgPool.open() is idempotent (no-op when already open or closed) and
        # constructs the psycopg_pool lazily, so callers must open before any
        # checkout or the pool has nothing to lend.
        self._pool.open()

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            yield conn

    def execute(self, sql: str, args: Sequence[Any] = ()) -> None:
        from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver
        from nexus_scalp.database.query_logging import pool_failure_guard

        translated = PostgreSQLDriver.translate_sql(sql)
        pool_failure_guard(
            lambda: self._execute_translated(translated, args),
            pool_name=self._pool.name,
            operation="execute",
            domain=self._pool.name,
            stats=self._pool.stats,
            sql=sql,
            args=args,
        )

    def _execute_translated(self, translated: str, args: Sequence[Any]) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(translated, tuple(args))
            # The pool lends connections with autocommit OFF (the psycopg
            # default); without an explicit commit the write is discarded when
            # the connection is returned. Financial rows must never be
            # silently dropped, so commit before returning to the pool.
            conn.commit()

    def execute_batch(self, statements: Sequence[tuple[str, Sequence[Sequence[Any]]]]) -> None:
        """Apply one batched transaction: grouped statements, one commit.

        Implements the audit write plane's backend contract: the whole batch
        commits atomically, and any exception leaves the connection rolled
        back so the caller's row-level salvage starts from a clean slate.
        """
        from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver
        from nexus_scalp.database.query_logging import pool_failure_guard

        translated = [
            (query, rows, PostgreSQLDriver.translate_sql(query)) for query, rows in statements
        ]
        pool_failure_guard(
            lambda: self._apply_batch(translated),
            pool_name=self._pool.name,
            operation="execute_batch",
            domain=self._pool.name,
            stats=self._pool.stats,
            sql="; ".join(q for q, _r, _t in translated),
            args=[rows for _q, rows, _t in translated],
        )

    def _apply_batch(
        self,
        statements: Sequence[tuple[str, Sequence[Sequence[Any]], str]],
    ) -> None:
        with self._pool.connection() as conn:
            try:
                for _query, rows, translated in statements:
                    with conn.cursor() as cur:
                        if len(rows) == 1:
                            cur.execute(translated, tuple(rows[0]))
                        else:
                            cur.executemany(translated, [tuple(r) for r in rows])
                conn.commit()
            except Exception:
                with contextlib_suppress():
                    conn.rollback()
                raise

    def execute_one(self, query: str, args: Sequence[Any]) -> None:
        from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver
        from nexus_scalp.database.query_logging import pool_failure_guard

        translated = PostgreSQLDriver.translate_sql(query)
        pool_failure_guard(
            lambda: self._apply_one(translated, args),
            pool_name=self._pool.name,
            operation="execute_one",
            domain=self._pool.name,
            stats=self._pool.stats,
            sql=query,
            args=args,
        )

    def _apply_one(self, translated: str, args: Sequence[Any]) -> None:
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(translated, tuple(args))
                conn.commit()
            except Exception:
                with contextlib_suppress():
                    conn.rollback()
                raise

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> None:
        from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver
        from nexus_scalp.database.query_logging import pool_failure_guard

        translated = PostgreSQLDriver.translate_sql(sql)
        pool_failure_guard(
            lambda: self._apply_many(translated, seq),
            pool_name=self._pool.name,
            operation="executemany",
            domain=self._pool.name,
            stats=self._pool.stats,
            sql=sql,
            args=seq,
        )

    def _apply_many(self, translated: str, seq: Sequence[Sequence[Any]]) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(translated, [tuple(a) for a in seq])
            conn.commit()


@contextmanager
def contextlib_suppress() -> Iterator[None]:
    """Suppress any exception in a with-block (pool best-effort paths)."""
    import contextlib

    with contextlib.suppress(Exception):
        yield


def _split_dsn_secret(dsn: str) -> tuple[str, dict[str, Any]]:
    """Split a DSN into a password-free conninfo + a psycopg ``password`` kwarg.

    psycopg v3 rejects a password inside ``conninfo`` for pooled connections
    (it never reaches the auth exchange), so the credential must be supplied
    as its own parameter.  A DSN with no password yields an empty kwarg dict.
    """
    if not dsn:
        return "", {}
    if "=" in dsn and "://" not in dsn:
        # libpq key=value form (host=... port=... dbname=... user=...).
        # psycopg refuses a password inside ``conninfo`` for pooled
        # connections, so lift it out into its own kwarg and leave the
        # rest of the string untouched.
        import shlex

        try:
            tokens = shlex.split(dsn)
        except ValueError:
            tokens = dsn.split()
        pw = None
        kept = []
        for part in tokens:
            if part.startswith("password="):
                pw = part[len("password=") :]
            else:
                kept.append(part)
        return " ".join(kept), ({"password": pw} if pw else {})
    if "://" in dsn:
        from urllib.parse import urlparse, urlunparse

        p = urlparse(dsn)
        password = p.password or ""
        if not password:
            return dsn, {}
        # Rebuild netloc WITHOUT the password, keeping host + port: libpq
        # needs the host to dial, and urlunparse drops a hostname that is
        # only present in the original netloc string.
        host = p.hostname or ""
        netloc = p.username or ""
        if host:
            netloc = f"{netloc}@{host}"
        if p.port:
            netloc = f"{netloc}:{p.port}"
        clean = urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
        return clean, {"password": password}
    # keyword form: "host=... password=[REDACTED]"
    parts: list[str] = []
    password = ""
    for pair in dsn.split():
        if "=" not in pair:
            parts.append(pair)
            continue
        key, value = pair.split("=", 1)
        if key.strip().lower() == "password":
            password = value.strip()
        else:
            parts.append(pair)
    return " ".join(parts), ({"password": password} if password else {})


def _sanitize_app_name(name: str) -> str:
    """Reduce a pool name to a safe PostgreSQL ``application_name`` literal.

    ``SET application_name`` accepts no bound parameters, so the value must be
    embedded literally; it is a fabric constant (never user input) and is
    restricted here to ``[A-Za-z0-9_-]`` so it cannot break out of the string
    literal.
    """
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", (name or "nse-pool")).strip("-")
    return cleaned or "nse-pool"
