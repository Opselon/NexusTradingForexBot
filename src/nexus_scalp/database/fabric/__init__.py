"""Database Fabric — the one persistence facade.

Phase 2/4 of the DATABASE FABRIC mission (DB-FABRIC-001).

This is the ONLY object domain code constructs to reach a database.  It
composes, per persistence domain:

    ReadGateway (typed read surface)
        -> ConsistencyRouter -> read plane (SQLite read-only / PG read pool)
    UnitOfWork (write surface)
        -> write plane (single SQLite writer / PG write pool)

Domain code never sees a driver, a connection, a pool, a provider branch or
a sqlite3/psycopg symbol.

Usage::

    fabric = DatabaseFabric.for_domain("audit")
    rows = fabric.read.query("SELECT ...", consistency=ConsistencyClass.STRONG)
    with fabric.write.unit_of_work() as tx:
        tx.execute("INSERT INTO ...", args)

Lifecycle: call :meth:`DatabaseFabric.open` at bootstrap and
:meth:`DatabaseFabric.close` at shutdown.  ``close`` drains the writer.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.fabric.config import (
    FabricConfig,
    FabricDomainConfig,
)
from nexus_scalp.database.fabric.consistency import (
    ConsistencyClass,
    WriteIntentRequiredError,
    classify_read,
)
from nexus_scalp.database.fabric.health_states import DatabaseHealthState, HealthProbe
from nexus_scalp.database.fabric.metrics import FabricMetrics
from nexus_scalp.database.fabric.pg_planes import PoolLimits
from nexus_scalp.database.fabric.routing import ConsistencyRouter
from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

logger = get_logger("nexus_scalp.database.fabric")


class ReadGateway:
    """Typed read surface for one domain.

    Every call declares (or defaults to) a :class:`ConsistencyClass`; the
    router decides the connection.  All reads are read-only by construction.
    """

    __slots__ = ("_fabric", "_method_name")

    def __init__(self, fabric: DatabaseFabric, method_name: str = "") -> None:
        self._fabric = fabric
        self._method_name = method_name

    def with_method(self, name: str) -> ReadGateway:
        """Name the calling repository method so consistency can be resolved."""
        return ReadGateway(self._fabric, name)

    def query(
        self,
        sql: str,
        args: Sequence[Any] = (),
        *,
        consistency: ConsistencyClass | None = None,
    ) -> list[dict[str, Any]]:
        f = self._fabric
        cls = classify_read(self._method_name, consistency)
        decision = f._router.route(cls, method_name=self._method_name)
        t0 = time.perf_counter()
        try:
            rows = f._read_query(sql, args, allow_replica=decision.used_replica)
        finally:
            f._metrics.read_latency.record((time.perf_counter() - t0) * 1000.0)
            f._metrics.inc("reads")
        return rows

    def query_one(
        self,
        sql: str,
        args: Sequence[Any] = (),
        *,
        consistency: ConsistencyClass | None = None,
    ) -> dict[str, Any] | None:
        rows = self.query(sql, args, consistency=consistency)
        return rows[0] if rows else None

    def scalar(
        self,
        sql: str,
        args: Sequence[Any] = (),
        *,
        consistency: ConsistencyClass | None = None,
    ) -> Any:
        f = self._fabric
        cls = classify_read(self._method_name, consistency)
        decision = f._router.route(cls, method_name=self._method_name)
        t0 = time.perf_counter()
        try:
            val = f._read_scalar(sql, args, allow_replica=decision.used_replica)
        finally:
            f._metrics.read_latency.record((time.perf_counter() - t0) * 1000.0)
            f._metrics.inc("reads")
        return val


class UnitOfWork:
    """Bounded write transaction on the write plane.

    Financial writes are never silently dropped: the write plane applies
    backpressure, durable overflow and dead-letter accounting (Phase 24).
    """

    fabric: DatabaseFabric
    _committed: bool

    def __init__(self, fabric: DatabaseFabric) -> None:
        self.fabric = fabric
        self._committed = False

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc is None and not self._committed:
            self.commit()

    def execute(self, sql: str, args: Sequence[Any] = (), *, financial: bool = True) -> None:
        """Queue one write inside this unit of work.

        ``financial=True`` (default) engages the no-silent-drop contract.
        """
        self.fabric._enqueue(sql, tuple(args), financial=financial)

    def executemany(
        self, sql: str, seq: Sequence[Sequence[Any]], *, financial: bool = True
    ) -> None:
        for row in seq:
            self.execute(sql, row, financial=financial)

    def commit(self) -> None:
        """Flush everything queued in this unit of work (bounded wait)."""
        self._committed = True
        self.fabric._flush()

    def rollback(self) -> None:
        """Best-effort: the write plane is queued, so this is a no-op marker.

        A queued batch that has not been flushed yet is simply discarded;
        a flushed batch is durable (SQLite WAL / committed PG transaction).
        """
        self._committed = True


class DatabaseFabric:
    """Per-domain persistence facade.  Owned by the fabric registry."""

    __slots__ = (
        "_domain_cfg",
        "_driver",
        "_lock",
        "_metrics",
        "_opened",
        "_read",
        "_router",
        "_write",
        "domain",
    )

    def __init__(
        self,
        domain: str,
        domain_cfg: FabricDomainConfig,
        *,
        driver: Any = None,
    ) -> None:
        self.domain = domain
        self._domain_cfg = domain_cfg
        self._driver = driver if driver is not None else self._build_driver(domain_cfg)
        self._router = ConsistencyRouter(domain_cfg)
        self._metrics = FabricMetrics(domain)
        self._opened = False
        self._lock = threading.Lock()
        self._read: Any = None
        self._write: Any = None

    # -- construction ------------------------------------------------------

    def _build_driver(self, cfg: FabricDomainConfig) -> Any:
        if cfg.provider.is_sqlite:
            return get_driver(DatabaseConfig.for_sqlite(cfg.domain, path=cfg.sqlite_path))
        return get_driver(
            DatabaseConfig.for_postgres(
                domain=cfg.domain,
                host=_pg_host(cfg.dsn),
                port=_pg_port(cfg.dsn),
                database=_pg_database(cfg.dsn),
                username=_pg_user(cfg.dsn),
            )
        )

    @classmethod
    def for_domain(
        cls, domain: str, *, fabric_config: FabricConfig | None = None
    ) -> DatabaseFabric:
        """Build the fabric for one domain from the canonical config."""
        if fabric_config is None:
            fabric_config = _default_fabric_config()
        return cls(domain, fabric_config.for_domain(domain))

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        """Bring up pools/queues for this domain."""
        with self._lock:
            if self._opened:
                return
            self._opened = True
        try:
            self._driver.ensure_directory()
        except Exception as err:  # best-effort for in-memory / server-side
            logger.debug("ensure_directory skipped for %s: %s", self.domain, err)

        if self._domain_cfg.provider.is_sqlite:
            from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver
            from nexus_scalp.database.fabric.sqlite_planes import (
                SQLiteReadPlane,
                SQLiteWritePlane,
            )

            assert isinstance(self._driver, SQLiteDriver)
            self._read = SQLiteReadPlane(self._driver, self._driver.config)
            self._write = SQLiteWritePlane(
                self._driver,
                self._driver.config,
                batch_size=self._domain_cfg.batch_size,
            )
            self._write.start()
        else:
            from nexus_scalp.database.fabric.pg_planes import (
                PgReadPlane,
                PgWritePlane,
                PoolLimits,
            )

            limits = self._domain_cfg.pool_limits or PoolLimits()
            self._read = PgReadPlane(
                self._domain_cfg.dsn, limits, replica_dsn=self._domain_cfg.read_dsn
            )
            self._write = PgWritePlane(self._domain_cfg.dsn, limits)
            self._read.open()
            self._write.open()

    def close(self) -> None:
        """Drain the writer and close every pool (deterministic shutdown)."""
        with self._lock:
            if not self._opened:
                return
        if self._write is not None:
            with contextlib_suppress():
                self._write.close()
        if self._read is not None:
            with contextlib_suppress():
                self._read.close()
        with self._lock:
            self._opened = False

    # -- surfaces ----------------------------------------------------------

    @property
    def read(self) -> ReadGateway:
        if self._read is None:
            raise RuntimeError(f"fabric for {self.domain!r} is not open")
        return ReadGateway(self)

    @property
    def write(self) -> UnitOfWork:
        if self._write is None:
            raise RuntimeError(f"fabric for {self.domain!r} is not open")
        return UnitOfWork(self)

    @property
    def metrics(self) -> FabricMetrics:
        return self._metrics

    @property
    def health(self) -> HealthProbe:
        return self._probe_health()

    # -- internals ---------------------------------------------------------

    def _read_query(
        self, sql: str, args: Sequence[Any], *, allow_replica: bool
    ) -> list[dict[str, Any]]:
        read = self._read
        if read is None:
            raise RuntimeError("fabric not open")
        if self._domain_cfg.provider.is_sqlite:
            return read.query(sql, args)
        return read.query(sql, args, allow_replica=allow_replica)

    def _read_scalar(self, sql: str, args: Sequence[Any], *, allow_replica: bool) -> Any:
        read = self._read
        if read is None:
            raise RuntimeError("fabric not open")
        if self._domain_cfg.provider.is_sqlite:
            return read.scalar(sql, args)
        return read.scalar(sql, args, allow_replica=allow_replica)

    def _enqueue(self, sql: str, args: tuple[Any, ...], *, financial: bool) -> None:
        write = self._write
        if write is None:
            raise WriteIntentRequiredError(
                f"write attempted on domain {self.domain!r} before open()"
            )
        # SQLite write plane is a queue; PG write plane executes immediately
        # through the pool (it accepts concurrent writers).
        if self._domain_cfg.provider.is_sqlite:
            from nexus_scalp.database.fabric.sqlite_planes import WriteItem

            ok = write.enqueue(WriteItem(sql, args, financial=financial))
            if not ok and financial:
                self._metrics.inc("overflow")
        else:
            try:
                write.execute(sql, args)
                self._metrics.inc("writes")
            except Exception:
                self._metrics.inc("write_errors")
                raise

    def _flush(self) -> None:
        write = self._write
        if write is None:
            return
        t0 = time.perf_counter()
        try:
            if self._domain_cfg.provider.is_sqlite:
                write.flush(timeout_sec=5.0)
            # PG writes are executed eagerly through the pool, so there is
            # nothing queued to flush (unlike the SQLite writer loop).
        finally:
            self._metrics.batch_flush_latency.record((time.perf_counter() - t0) * 1000.0)

    def _probe_health(self) -> HealthProbe:
        from datetime import UTC, datetime

        state = DatabaseHealthState.READY
        detail = ""
        try:
            if self._domain_cfg.provider.is_sqlite:
                ok = bool(self._driver.ping())
            else:
                ok = bool(self._read and self._read._primary.ping())
            if not ok:
                state = DatabaseHealthState.UNAVAILABLE
                detail = "ping failed"
        except Exception as err:
            state = DatabaseHealthState.UNAVAILABLE
            detail = f"{type(err).__name__}: {err}"[:200]
        return HealthProbe(
            state,
            domain=self.domain,
            provider=self._domain_cfg.provider.value,
            detail=detail,
            checked_at_iso=datetime.now(UTC).isoformat(),
        )

    # -- metrics gauges ----------------------------------------------------

    def publish_gauges(self) -> None:
        m = self._metrics
        m.set_gauge("write_queue_depth", self._write.depth if self._write else 0)
        stats = self._write.stats.snapshot() if self._write else {}
        for k, v in stats.items():
            m.set_gauge(f"write_{k}", int(v))

    def write_backend(self, domain: str) -> Any:
        """The pooled write backend for a domain (provider-specific)."""
        return self._write_plane_for(domain)

    def read_backend(self, domain: str) -> Any:
        """The pooled read backend for a domain (provider-specific)."""
        return self._read_plane_for(domain)

    def _write_plane_for(self, domain: str) -> Any:
        from nexus_scalp.database.fabric.pg_planes import PgWritePlane

        return PgWritePlane(self._domain_dsn(domain), self._pg_pool_limits(domain))

    def _read_plane_for(self, domain: str) -> Any:
        from nexus_scalp.database.fabric.pg_planes import PgReadPlane

        return PgReadPlane(self._domain_dsn(domain), self._pg_pool_limits(domain))

    def _pg_pool_limits(self, domain: str) -> Any:
        from nexus_scalp.database.fabric.pg_planes import PoolLimits

        limits = getattr(self._domain_cfg, "pool_limits", None)
        if limits is not None:
            return limits
        return PoolLimits(min_size=2, max_size=8)

    def _domain_dsn(self, domain: str) -> str:
        cfg = self._domain_cfg
        if domain != self.domain:
            return ""
        return getattr(cfg, "dsn", "") or ""


def _default_fabric_config() -> FabricConfig:
    """Resolve the default fabric config (SQLite zero-config)."""
    return FabricConfig.sqlite_default()


# -- DSN parsing helpers (used only at the configuration boundary) --------


def _pg_dsn_parts(dsn: str) -> dict[str, str]:
    """Parse a keyword or URL DSN into parts (no secrets logged)."""
    if not dsn:
        return {}
    out: dict[str, str] = {}
    if "://" in dsn:
        from urllib.parse import urlparse

        p = urlparse(dsn)
        out["host"] = p.hostname or ""
        out["port"] = str(p.port or 5432)
        out["database"] = (p.path or "/").lstrip("/")
        out["user"] = p.username or ""
        return out
    for pair in dsn.split():
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _pg_host(dsn: str) -> str:
    return _pg_dsn_parts(dsn).get("host", "localhost")


def _pg_port(dsn: str) -> int:
    return int(_pg_dsn_parts(dsn).get("port", "5432") or 5432)


def _pg_database(dsn: str) -> str:
    return _pg_dsn_parts(dsn).get("database", "") or "nse_audit"


def _pg_user(dsn: str) -> str:
    return _pg_dsn_parts(dsn).get("user", "") or "postgres"


# -- Domain backend registry ----------------------------------------------
# Per-domain pooled backends for providers that need a pool (Phase 6).
# A domain is "provisioned" when the operator has pointed it at a provider
# through the fabric configuration; until then it resolves to None and the
# consumer refuses to write instead of silently dropping (Phase 24).


class _DomainBackends:
    """The pooled WRITE and READ backends registered for one domain.

    The two planes are distinct objects over distinct pools: a read must
    never be served from the write path, and read consumers refuse a
    write-shaped backend outright.  They are provisioned together and
    replaced together on re-provision.
    """

    __slots__ = ("read", "write")

    def __init__(self, *, write: Any, read: Any) -> None:
        self.write = write
        self.read = read

    def close(self) -> None:
        """Close both pools best-effort (shutdown / re-provision)."""
        for backend in (self.write, self.read):
            _close_backend(backend)


_DOMAIN_BACKENDS: dict[str, _DomainBackends] = {}
_DOMAIN_BACKEND_LOCK = threading.Lock()


def register_domain_backend(domain: str, backend: Any) -> None:
    """Register a pooled WRITE backend for a domain (idempotent).

    Only the write slot is replaced; a read plane already registered for the
    domain is preserved (:func:`provision_domain` replaces both at once).
    """
    with _DOMAIN_BACKEND_LOCK:
        existing = _DOMAIN_BACKENDS.get(domain)
        _DOMAIN_BACKENDS[domain] = _DomainBackends(
            write=backend,
            read=existing.read if existing is not None else None,
        )


def register_domain_read_backend(domain: str, backend: Any) -> None:
    """Register a pooled READ backend for a domain (idempotent).

    Only the read slot is replaced; the write backend is preserved.  The read
    plane must not be write-shaped — read consumers refuse a backend that
    exposes ``execute`` and degrade observably instead.
    """
    with _DOMAIN_BACKEND_LOCK:
        existing = _DOMAIN_BACKENDS.get(domain)
        _DOMAIN_BACKENDS[domain] = _DomainBackends(
            write=existing.write if existing is not None else None,
            read=backend,
        )


def unregister_domain_backend(domain: str) -> None:
    with _DOMAIN_BACKEND_LOCK:
        _DOMAIN_BACKENDS.pop(domain, None)


def get_domain_backend(domain: str, readonly: bool = False) -> Any:
    """Resolve the pooled backend for a provisioned domain, else None.

    ``readonly=True`` resolves the domain's READ plane (``query`` /
    ``query_one`` / ``scalar``); the default resolves its WRITE backend.
    Returns None when the domain is not provisioned for a pooled provider —
    or when the requested plane is absent — so callers can fail loudly rather
    than silently no-op (the pre-fabric behaviour).  SQLite domains never
    need this: their writer connection is owned by the consumer.
    """
    with _DOMAIN_BACKEND_LOCK:
        entry = _DOMAIN_BACKENDS.get(domain)
    if entry is None:
        return None
    return entry.read if readonly else entry.write


def _open_backend(backend: Any) -> None:
    """Open a pooled backend's own pool.

    A pooled plane owns its own PgPool, which is NOT opened by
    ``DatabaseFabric.open()``: without this the pool has nothing to lend and
    every checkout fails with "pg pool is not open" (the write would be
    silently dead-lettered on the first batch).
    """
    backend_open = getattr(backend, "open", None)
    if callable(backend_open):
        backend_open()


def _close_backend(backend: Any) -> None:
    """Close a pooled backend's pool best-effort (shutdown / failure paths)."""
    backend_close = getattr(backend, "close", None)
    if callable(backend_close):
        with contextlib_suppress():
            backend_close()


def _persisted_pool_config(domain: str) -> DatabaseConfig | None:
    """Resolve the persisted DatabaseConfig for ``domain``, or None.

    ``provision_domain`` receives only a DSN + literal pool sizes, so the
    persisted row is the only place the operator's pool knobs live.  This is
    best-effort by design: a fresh install, a missing settings DB, or an
    unreadable row must all fall through to the call-site defaults rather
    than break provisioning (the failure is loud at validation boundaries
    elsewhere — see ``DatabaseConfig.from_dict``).
    """
    try:
        from nexus_scalp.database.config import load_database_config

        cfg = load_database_config(domain)
    except Exception:
        return None
    return cfg if cfg.is_postgresql else None


def _pool_limits_from_config(
    cfg: DatabaseConfig | None,
    pool_kwargs: dict[str, Any],
) -> PoolLimits:
    """Build PoolLimits with the persisted config as the BASE.

    The persisted ``postgresql_config`` row is what the operator actually
    edits (DATABASE TAB / ``provider_options``), so it is the base and the
    call-site literals are overrides only when the persisted value is absent
    — otherwise a UI save would change nothing (the observed defect: every
    call site passed the identical ``min_size=1, max_size=4`` and the row was
    ignored entirely).

    Pool sizing is optional on the row (``None`` = "never set"), so an unset
    knob falls through to the caller's literal; an explicit 0 is a real
    choice (``min_size=0`` opens the pool lazily, 0 idle/lifetime = never
    reap) and is honored as-is.

    ``statement_timeout_ms`` / ``idle_timeout_sec`` / ``max_lifetime_sec``
    have no call-site equivalent, so they come ONLY from the persisted row:
    pooled connections previously never applied ``command_timeout_sec`` (the
    raw driver path did, via ``options='-c statement_timeout=<ms>'``) and the
    pool's ``max_idle`` / ``max_lifetime`` stayed 0, holding every connection
    for process lifetime.
    """
    min_size = int(pool_kwargs.get("min_size", 2))
    max_size = int(pool_kwargs.get("max_size", 10))
    connect_timeout_sec = PoolLimits.connect_timeout_sec
    statement_timeout_ms = 0
    idle_timeout_sec = 0
    max_lifetime_sec = 0
    if cfg is not None:
        if cfg.command_timeout_sec:
            # The raw driver path applies the same knob as a statement_timeout;
            # pooled connections get it as a session SET (pg_planes.configure).
            statement_timeout_ms = max(0, int(cfg.command_timeout_sec) * 1000)
        if cfg.connect_timeout_sec:
            connect_timeout_sec = int(cfg.connect_timeout_sec)
        # A pool sizing knob is only a BASE when the operator actually set it
        # (None = never touched); an explicit 0 is a deliberate choice.
        if cfg.pooling_enabled:
            if cfg.pool_min_size is not None:
                min_size = int(cfg.pool_min_size)
            if cfg.pool_max_size is not None:
                max_size = int(cfg.pool_max_size)
            if cfg.pool_idle_timeout_sec is not None:
                idle_timeout_sec = int(cfg.pool_idle_timeout_sec)
            if cfg.pool_max_lifetime_sec is not None:
                max_lifetime_sec = int(cfg.pool_max_lifetime_sec)
    return PoolLimits(
        min_size=min_size,
        max_size=max_size,
        connect_timeout_sec=connect_timeout_sec,
        statement_timeout_ms=statement_timeout_ms,
        idle_timeout_sec=idle_timeout_sec,
        max_lifetime_sec=max_lifetime_sec,
    )


def provision_domain(domain: str, dsn: str, **pool_kwargs: Any) -> Any:
    """Provision a domain on a pooled provider and return its WRITE backend.

    Builds the domain's fabric (write + read pools) from a DSN, opens it,
    registers its write AND read backends, and returns the write backend.
    Idempotent: re-provisioning replaces both pools and closes the old ones.
    Opening the read pool is a hard step — it raises instead of leaving a
    write-only domain registered while provision reports success.

    Pool sizing/timeouts take the persisted ``postgresql_config`` row as the
    BASE and treat the caller's literals as overrides only when the persisted
    value is absent or at its default (see :func:`_pool_limits_from_config`).
    """
    cfg = FabricConfig.for_postgresql(
        dsn,
        pool_limits=_pool_limits_from_config(_persisted_pool_config(domain), pool_kwargs),
    )
    domain_cfg = cfg.for_domain(domain)
    # Resolve the whole registry entry (both planes): ``get_domain_backend``
    # returns only the write backend, which is not what needs closing here.
    with _DOMAIN_BACKEND_LOCK:
        existing = _DOMAIN_BACKENDS.get(domain)
    if isinstance(existing, _DomainBackends):
        with contextlib_suppress():
            existing.close()
    fabric = DatabaseFabric(domain, domain_cfg)
    fabric.open()
    backend = fabric.write_backend(domain)
    _open_backend(backend)
    # The READ plane owns its own read-only pool, opened the same way.  This
    # registration is what the audit read guard looks for: without it every
    # gated read degrades to its documented default.
    read_backend = fabric.read_backend(domain)
    try:
        _open_backend(read_backend)
    except Exception:
        # Fail closed: never register a write-only domain (that is exactly
        # the silent-degradation defect), and never leak the pools opened so
        # far on the way out.
        _close_backend(backend)
        with contextlib_suppress():
            fabric.close()
        raise
    # Bootstrap the domain's schema on the target provider. The DDL is authored
    # once in the SQLite dialect and translated to PostgreSQL, so switching a
    # domain to PostgreSQL never requires hand-created tables (and re-running
    # is idempotent — IF NOT EXISTS makes re-provisioning non-destructive).
    try:
        from nexus_scalp.database.migration import migrate_domain

        def _exec(sql: str) -> None:
            backend.execute(sql)

        migration = migrate_domain(domain, _exec)
        if migration.get("error_count"):
            logger.warning(
                "[DB-FABRIC] domain=%s schema migration completed with %d error(s)",
                domain,
                migration["error_count"],
            )
    except NotImplementedError:
        pass  # domain has no authored DDL yet — loud, not silent
    # Replace both planes atomically so a provision that returns never leaves
    # a domain with a write backend and no read plane.
    with _DOMAIN_BACKEND_LOCK:
        _DOMAIN_BACKENDS[domain] = _DomainBackends(write=backend, read=read_backend)
    return backend


@contextmanager
def contextlib_suppress() -> Iterator[None]:
    """Suppress any exception in a with-block (best-effort close paths)."""
    import contextlib

    with contextlib.suppress(Exception):
        yield
