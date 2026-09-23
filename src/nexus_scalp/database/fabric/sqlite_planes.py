"""SQLite read/write planes — Phase 7 of the DATABASE FABRIC mission.

Concurrency model (deliberate, not incidental):

  * WAL journaling on file DBs (unlimited concurrent readers, one writer);
  * the READ PLANE uses true read-only connections: ``file:<db>?mode=ro``
    URI plus the SQLite C-level authorizer already implemented in
    :class:`~nexus_scalp.database.drivers.sqlite_driver.SQLiteDriver`
    (kernel-enforced: INSERT/UPDATE/DELETE/DDL are rejected, not merely
    discouraged);
  * the WRITE PLANE is exactly ONE writer connection per database/domain,
    fed by a bounded queue, drained in bounded transactions.  SQLite's
    process-wide write lock means a second writer can only ever lose;
  * ``busy_timeout`` bounded on every connection;
  * shared in-memory (``file::memory:?cache=shared``) semantics preserved:
    readers reuse the owner's persistent connection because the shared
    cache is dropped when the last connection closes.

A dashboard GET therefore never opens a write-capable connection, and the
trading writer never fights a reader for the write lock.
"""

from __future__ import annotations

import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver
from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

logger = get_logger("nexus_scalp.database.fabric.sqlite_planes")

#: SQLite busy_timeout in milliseconds (bounded wait for the write lock).
BUSY_TIMEOUT_MS = 5000

#: Default write-queue depth (matches the proven audit writer).
DEFAULT_QUEUE_DEPTH = 10000

#: Maximum rows committed in one writer transaction.
DEFAULT_BATCH_SIZE = 500


@dataclass(frozen=True)
class WriteItem:
    """One queued write: a SQL statement + its bound parameters."""

    sql: str
    args: tuple[Any, ...]
    #: True for financial/accounting rows (backpressure + durable overflow);
    #: False for telemetry (droppable by design, counted, never silent).
    financial: bool = False
    #: Optional idempotency key — when set, the writer de-duplicates on it.
    idempotency_key: str = ""


class _WriteQueueStats:
    """Atomic counters for the write plane."""

    __slots__ = (
        "_lock",
        "backpressured",
        "committed",
        "dead_lettered",
        "dropped_telemetry",
        "enqueued",
        "failed",
        "overflowed",
    )

    def __init__(self) -> None:
        self.enqueued = 0
        self.committed = 0
        self.failed = 0
        self.dead_lettered = 0
        self.overflowed = 0
        self.backpressured = 0
        self.dropped_telemetry = 0
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "enqueued": self.enqueued,
                "committed": self.committed,
                "failed": self.failed,
                "dead_lettered": self.dead_lettered,
                "overflowed": self.overflowed,
                "backpressured": self.backpressured,
                "dropped_telemetry": self.dropped_telemetry,
            }


class SQLiteReadPlane:
    """Read plane for one SQLite database.

    Opens read-only connections on demand.  Under WAL, readers never block
    the writer and the writer never blocks readers.
    """

    __slots__ = ("_closed", "_config", "_driver", "_lock", "_ro_conns")

    def __init__(self, driver: SQLiteDriver, config: DatabaseConfig) -> None:
        self._driver = driver
        self._config = config
        self._lock = threading.Lock()
        self._ro_conns: list[Any] = []
        self._closed = False

    # -- connection -------------------------------------------------------

    def _read_only_path(self) -> str:
        """Return the DB path in read-only URI form (or the shared-memory URI)."""
        path = self._driver.connect_path
        if self._driver.is_in_memory:
            # Shared cache: readers MUST reuse the shared connection — the
            # cache dies when the last connection closes.
            return path
        return f"file:{path}?mode=ro"

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Lend a read-only connection for the duration of the context.

        Raises :class:`~nexus_scalp.database.fabric.consistency.ReadOnlyViolationError`
        if a mutation is attempted (kernel-enforced).
        """
        if self._closed:
            raise RuntimeError("read plane closed")
        import sqlite3

        path = self._read_only_path()
        conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000.0, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            # READ-ONLY PRAGMAS ONLY.  The driver's configure_connection()
            # sets synchronous/temp_store/journal_mode, which are WRITES and
            # fail (or take an exclusive lock) on a mode=ro connection.
            # busy_timeout is a setting, not a write, so it is safe here.
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS};")
            # C-level read-only enforcement: the authorizer rejects any
            # write/DDL attempt at the kernel boundary.
            self._driver.set_read_authorizer(conn)
            with self._lock:
                self._ro_conns.append(conn)
            yield conn
        finally:
            with self._lock:
                try:
                    self._ro_conns.remove(conn)
                except ValueError:
                    pass
            conn.close()

    # -- query ------------------------------------------------------------

    def query(self, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Read-only SELECT -> rows as dicts. Enforced read-only."""
        from nexus_scalp.database.drivers._sql_guard import assert_safe_sql

        with self.connection() as conn:
            cur = conn.execute(assert_safe_sql(sql), tuple(args))
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, args: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: Sequence[Any] = ()) -> Any:
        from nexus_scalp.database.drivers._sql_guard import assert_safe_sql

        with self.connection() as conn:
            cur = conn.execute(assert_safe_sql(sql), tuple(args))
            row = cur.fetchone()
            return row[0] if row is not None else None

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self._closed = True
        with self._lock:
            for conn in list(self._ro_conns):
                conn.close()
            self._ro_conns.clear()


@contextmanager
def contextlib_suppress() -> Iterator[None]:
    """Suppress any exception in a with-block (close() best-effort paths)."""
    import contextlib

    with contextlib.suppress(Exception):
        yield


class SQLiteWritePlane:
    """Write plane for one SQLite database: one writer, bounded queue.

    Model: all writes enqueue onto a single queue; ONE background writer
    drains it in bounded transactions (``executemany`` grouped by statement,
    batch <= ``batch_size`` per commit).  This is the shape the production
    audit writer proved at 425MB/70-table scale; it is elevated here into
    the general fabric.

    Financial writes are never silently dropped (Phase 24 contract):
      1. bounded backpressure (producer waits for capacity);
      2. durable overflow file if the queue stays full;
      3. dead-letter + CRITICAL log + counters if even the file fails.
    Telemetry writes are droppable by design (counted, logged).
    """

    __slots__ = (
        "_batch_size",
        "_closed",
        "_cond",
        "_config",
        "_driver",
        "_flush_interval",
        "_lock",
        "_overflow_dir",
        "_queue",
        "_running",
        "_seen_idempotency",
        "_stats",
        "_thread",
    )

    def __init__(
        self,
        driver: SQLiteDriver,
        config: DatabaseConfig,
        *,
        queue_depth: int = DEFAULT_QUEUE_DEPTH,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_sec: float = 0.5,
        overflow_dir: Path | str | None = None,
    ) -> None:
        self._driver = driver
        self._config = config
        self._queue: queue.Queue[WriteItem | None] = queue.Queue(maxsize=max(1, queue_depth))
        self._batch_size = max(1, batch_size)
        self._flush_interval = max(0.01, float(flush_interval_sec))
        self._stats = _WriteQueueStats()
        self._running = False
        self._thread: threading.Thread | None = None
        self._overflow_dir = (
            Path(overflow_dir)
            if overflow_dir is not None
            else Path(config.sqlite_path or "artifacts").parent
            / "artifacts"
            / "db_overflow"
            / (config.domain or "audit")
        )
        self._seen_idempotency: set[str] = set()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._closed = False

    # -- properties -------------------------------------------------------

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def stats(self) -> _WriteQueueStats:
        return self._stats

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._writer_loop,
            name=f"db-write-{self._config.domain}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, drain_timeout_sec: float = 5.0) -> bool:
        """Stop accepting writes and drain the queue.  Returns True if drained."""
        if not self._running:
            return True
        self._running = False
        self._queue.put(None)  # sentinel to break the blocking get
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.1, drain_timeout_sec))
        drained = self._queue.unfinished_tasks == 0
        self._thread = None
        return drained

    # -- enqueue ----------------------------------------------------------

    def enqueue(self, item: WriteItem, *, timeout_sec: float | None = None) -> bool:
        """Enqueue one write.  Returns True on success.

        Financial items use bounded backpressure + durable overflow.
        Telemetry items drop (counted) when the queue is full.
        """
        if self._closed:
            return False
        if item.idempotency_key:
            with self._lock:
                if item.idempotency_key in self._seen_idempotency:
                    return True  # already queued/committed — idempotent
                self._seen_idempotency.add(item.idempotency_key)
        try:
            if item.financial:
                wait = (
                    timeout_sec if timeout_sec is not None else min(self._flush_interval * 2.0, 0.1)
                )
                self._queue.put(item, timeout=wait)
            else:
                self._queue.put_nowait(item)
            with self._cond:
                self._stats.enqueued += 1
            return True
        except queue.Full:
            with self._cond:
                if item.financial:
                    self._stats.backpressured += 1
                    self._stats.overflowed += 1
                else:
                    self._stats.dropped_telemetry += 1
            if item.financial:
                self._write_overflow(item)
            else:
                logger.warning(
                    "db write plane telemetry dropped (queue full) domain=%s qsize=%d",
                    self._config.domain,
                    self._queue.qsize(),
                )
            return item.financial  # overflowed items are durable; telemetry is not

    def flush(self, timeout_sec: float = 5.0) -> bool:
        """Wait until every queued write is durable (read-after-write helper)."""
        deadline = time.monotonic() + max(0.0, timeout_sec)
        while self._queue.unfinished_tasks > 0:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True

    # -- writer loop ------------------------------------------------------

    def _connect_writer(self, attempts: int = 60) -> Any:
        """Open the single write connection, tolerating transient lock contention.

        A read-only connection holds a SHARED lock while a query is in flight;
        switching the journal mode to WAL needs it briefly.  We retry with a
        bounded busy_timeout rather than crashing the writer thread (an
        unhandled thread exception leaves the queue un-drained).
        """
        import sqlite3

        path = self._driver.connect_path
        last_err: BaseException | None = None
        for _ in range(max(1, attempts)):
            if self._driver.is_in_memory:
                conn = self._driver.connect_shared()
            else:
                conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000.0)
                conn.row_factory = sqlite3.Row
            try:
                # WAL + performance PRAGMAs belong to the WRITE connection.
                # This is the single place a write-capable SQLite connection
                # is created for this domain, so the journal-mode switch
                # cannot contend with another writer.
                self._driver.configure_connection(conn)
                return conn
            except sqlite3.OperationalError as err:
                last_err = err
                try:
                    conn.close()
                except Exception:
                    pass
                time.sleep(0.05)
        raise RuntimeError(
            f"SQLite write connection could not be established for "
            f"{self._config.domain!r}: {last_err}"
        )

    def _writer_loop(self) -> None:
        q = self._queue
        conn = self._connect_writer()
        try:
            while self._running or (not q.empty() and q.unfinished_tasks > 0):
                batch: list[WriteItem] = []
                try:
                    while len(batch) < self._batch_size:
                        if batch:
                            try:
                                item = q.get_nowait()
                            except queue.Empty:
                                break
                        else:
                            item = q.get(timeout=self._flush_interval)
                        if item is None:  # stop sentinel
                            break
                        batch.append(item)
                except queue.Empty:
                    pass

                if batch:
                    self._commit_batch(conn, batch)
                    for _ in batch:
                        q.task_done()
                elif not self._running:
                    break
        finally:
            with contextlib_suppress():
                conn.close()

    def _commit_batch(self, conn: Any, batch: list[WriteItem]) -> None:
        """Commit one batch in a single transaction; salvage on failure."""
        import itertools

        t0 = time.perf_counter()
        try:
            with conn:  # commit on success, rollback on exception
                for sql, group in itertools.groupby(batch, key=lambda x: x.sql):
                    args_list = [item.args for item in group]
                    conn.executemany(sql, args_list)
            with self._cond:
                self._stats.committed += len(batch)
        except Exception as err:
            with self._cond:
                self._stats.failed += len(batch)
            logger.error(
                "db write plane batch failed domain=%s batch=%d error_type=%s error=%s",
                self._config.domain,
                len(batch),
                type(err).__name__,
                str(err)[:300],
            )
            # Salvage: retry each row individually; dead-letter the rest.
            for item in batch:
                try:
                    with conn:
                        conn.execute(item.sql, item.args)
                    with self._cond:
                        self._stats.committed += 1
                except Exception as row_err:
                    if item.financial:
                        with self._cond:
                            self._stats.dead_lettered += 1
                        logger.critical(
                            "db write plane FINANCIAL row dead-lettered domain=%s error=%s sql=%s",
                            self._config.domain,
                            str(row_err)[:200],
                            item.sql[:120],
                        )
                    else:
                        with self._cond:
                            self._stats.dropped_telemetry += 1
        finally:
            duration = (time.perf_counter() - t0) * 1000.0
            logger.debug(
                "db write plane commit domain=%s rows=%d ms=%.1f",
                self._config.domain,
                len(batch),
                duration,
            )

    # -- durable overflow --------------------------------------------------

    def _write_overflow(self, item: WriteItem) -> None:
        """Persist a stranded financial write to disk (never silently lost)."""
        try:
            self._overflow_dir.mkdir(parents=True, exist_ok=True)
            import json

            ts = time.strftime("%Y%m%d_%H%M%S")
            fname = f"overflow_{ts}_{self._stats.overflowed:08d}.json"
            (self._overflow_dir / fname).write_text(
                json.dumps(
                    {
                        "failed_at": ts,
                        "domain": self._config.domain,
                        "sql": item.sql,
                        "args": _json_safe_args(item.args),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            logger.critical(
                "db write plane FINANCIAL OVERFLOW persisted to disk domain=%s file=%s",
                self._config.domain,
                fname,
            )
        except OSError as err:
            with self._cond:
                self._stats.dead_lettered += 1
            logger.critical(
                "db write plane FINANCIAL WRITE COULD NOT BE PRESERVED domain=%s error=%s",
                self._config.domain,
                str(err)[:200],
            )

    def close(self) -> None:
        self._closed = True
        self.stop(drain_timeout_sec=0.0)


def _json_safe_args(args: tuple[Any, ...]) -> list[Any]:
    """Render bound parameters into a JSON-safe list (for overflow files)."""
    import json

    out: list[Any] = []
    for a in args:
        try:
            json.dumps(a)
            out.append(a)
        except (TypeError, ValueError):
            out.append(repr(a))
    return out
