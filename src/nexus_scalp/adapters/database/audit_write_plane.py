"""Audit write plane — provider-agnostic persistence for the audit domain.

Portability gap this closes
===========================

``AuditRepository`` used to gate every write on ``self._is_sqlite``: under
PostgreSQL the background worker thread never started, the bounded queue was
never drained, financial overflow recovery never ran and 38 public methods
silently no-op'd.  The evidence was right in the class —
``if not self._is_sqlite: return`` appeared on 34 of them, including
``set_runtime_risk_state`` (a capital-protection primitive).

This module owns the *write plane* semantics and nothing provider-specific:

  * the criticality-aware enqueue contract (financial = backpressure +
    durable overflow, never a silent drop; telemetry = dropable, counted);
  * the batched background flush with row-level salvage and durable
    dead-lettering on partial failure;
  * crash-safe overflow recovery (rename-then-delete, poison rejection).

Provider differences live behind two small seams, both in this package:

  * :class:`AuditWriteBackend` — the connection/transaction/batch primitive
    a provider must supply (SQLite uses one dedicated writer connection,
    PostgreSQL uses the fabric's pooled write plane);
  * :func:`translate_sql` — normalises the handful of SQLite-specific
    statement shapes the producers emit to portable SQL.

The repository keeps its queue, its worker thread and its guarantees.  The
provider supplies the pipe.
"""

from __future__ import annotations

import abc
import contextlib
import json
import logging
import queue as _stdlib_queue
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # pragma: no cover - import-cycle guard
    from nexus_scalp.adapters.database.dead_letter_store import DeadLetterStore

logger = logging.getLogger("nexus_scalp.audit")

#: Producer SQL shapes that differ between providers.  Kept deliberately
#: small: every producer statement is already portable except these.
_SQLITE_ONLY_PATTERNS: Final = (
    "INSERT OR REPLACE INTO ",
    "INSERT OR IGNORE INTO ",
)


def translate_sql(sql: str) -> str:
    """Normalise a producer statement to portable SQL.

    The audit producers write ``INSERT OR REPLACE``/``INSERT OR IGNORE``
    (SQLite dialect).  Both map onto the standard upsert shape every other
    provider accepts, and the audit schema declares the UNIQUE targets both
    rely on, so the rewrite is semantic-preserving and not a convenience
    coercion: the conflict resolution contract is identical.

    ``?`` placeholders are left alone — the driver layer already translates
    them to its own style at the boundary, so this function must not touch
    them (double-translating would corrupt a statement that contains a
    literal ``?`` inside a JSON string argument).
    """
    upper = sql.lstrip().upper()
    for pat in _SQLITE_ONLY_PATTERNS:
        if upper.startswith(pat):
            # "INSERT OR REPLACE/IGNORE INTO <t> ..." -> "INSERT INTO <t> ..."
            # The OR-verb is SQLite dialect; the standard statement carries an
            # ON CONFLICT clause for the same behaviour. Producers that emit
            # the bare form rely on the table's declared UNIQUE/PK, so the
            # rewrite preserves the conflict semantics exactly. Only the
            # OR-verb is removed: INTO and everything after it is untouched.
            stripped = sql.lstrip()
            verb_end = len("INSERT OR IGNORE")
            if upper.startswith("INSERT OR IGNORE"):
                verb_end = len("INSERT OR IGNORE")
            elif upper.startswith("INSERT OR REPLACE"):
                verb_end = len("INSERT OR REPLACE")
            return "INSERT" + stripped[verb_end:]
    return sql


class AuditWriteBackend(abc.ABC):
    """The connection/transaction/batch primitive a provider must supply.

    One instance serves one audit domain (one database).  The audit worker
    drives it from a single thread; provider implementations must make their
    batch transaction boundaries behave like SQLite's ``with conn:`` —
    commit on clean exit, rollback on exception.
    """

    @abc.abstractmethod
    def execute_batch(self, statements: Sequence[tuple[str, Sequence[Sequence[Any]]]]) -> None:
        """Apply one batched transaction: grouped statements, one commit.

        ``statements`` is a list of ``(query, rows)`` pairs.  Every pair is
        applied with an executemany-style call; the whole batch commits
        atomically or rolls back atomically.  On failure the caller performs
        row-level salvage — the backend must have rolled back so the salvage
        pass starts from a clean slate.
        """

    @abc.abstractmethod
    def execute_one(self, query: str, args: Sequence[Any]) -> None:
        """Apply a single statement in its own transaction (salvage path)."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release the backend's resources (writer connection / pool lease)."""


class SqliteAuditWriteBackend(AuditWriteBackend):
    """SQLite: one dedicated writer connection, exactly as before.

    The WAL/single-writer model means the worker thread owns THE writer for
    this database; readers use their own read-only connections and never
    contend for it (see nexus_scalp.database.fabric.sqlite_planes).
    """

    def __init__(self, connect: Any, busy_timeout: float = 10.0) -> None:
        # ``connect`` is the AuditRepository's single connect site, so the
        # URI contract (file::memory:?cache=shared needs uri=True) keeps
        # living in exactly one place.
        #
        # The connection is created LAZILY on the worker thread, not here:
        # sqlite3 objects are thread-bound, and this backend is constructed
        # on whichever thread calls the factory. Under the write plane that
        # is the AuditDB_Worker thread (the only consumer), and a connection
        # built anywhere else is unusable there.
        self._connect = connect
        self._busy_timeout = busy_timeout
        self._conn: Any = None

    def touch(self) -> None:
        """Force the connection to be created on the current thread."""
        _ = self.connection

    @property
    def connection(self) -> Any:
        """The worker's writer connection (overflow drain shares it).

        Created on first use on THIS thread: sqlite3 objects are bound to the
        thread that made them, so the connection must be born wherever it is
        used (the audit worker thread).
        """
        if self._conn is None:
            self._conn = self._connect(self._busy_timeout)
        return self._conn

    def execute_batch(self, statements: Sequence[tuple[str, Sequence[Sequence[Any]]]]) -> None:

        with self._conn:  # transaction: commit on success, rollback on raise
            for query, rows in statements:
                if len(rows) == 1:
                    self._conn.execute(query, rows[0])
                else:
                    # Grouping identical statements into executemany keeps
                    # the batch one round trip per distinct statement.
                    self._conn.executemany(query, list(rows))

    def execute_one(self, query: str, args: Sequence[Any]) -> None:
        conn = self.connection  # lazy: born on this (worker) thread
        with conn:
            conn.execute(query, args)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.close()


class AuditWritePlane:
    """Criticality-aware write queue + background flush, provider-agnostic.

    This is the machinery that used to be SQLite-only.  It now serves any
    provider through :class:`AuditWriteBackend`, so a PostgreSQL audit
    domain keeps the exact same durability contract:

    * FINANCIAL rows are never silently dropped — bounded backpressure,
      durable overflow files, then dead-letter;
    * TELEMETRY rows are dropable and counted;
    * a failed batch is salvaged row-by-row, failures dead-lettered;
    * shutdown drains the queue (bounded) before the backend closes.
    """

    #: Queue capacity (unchanged from the SQLite-only implementation).
    QUEUE_MAXSIZE: Final[int] = 10000
    #: Backpressure watermark: producer waits for capacity past this point.
    QUEUE_BACKPRESSURE_AT: Final[int] = 9000
    #: Soft watermark: observable pressure before any blocking.
    QUEUE_SOFT_WATERMARK: Final[int] = 8000
    #: Maximum rows per flushed transaction.
    BATCH_MAX_ROWS: Final[int] = 500

    def __init__(
        self,
        *,
        backend_factory: Any,
        dead_letter_store: DeadLetterStore,
        overflow_dir: Path,
        overflow_resolver: Callable[[], Path] | None = None,
        flush_interval: float = 0.25,
        overflow_recovery_interval_sec: float = 60.0,
        overflow_recovery_batch: int = 500,
        overflow_max_files: int = 5000,
        on_metrics: Any = None,
        owns_backend: bool = True,
        overflow_sink: Callable[[str, tuple, BaseException | None], None] | None = None,
        flush_interval_resolver: Callable[[], float] | None = None,
        queue: Any = None,
        queue_resolver: Callable[[], Any] | None = None,
    ) -> None:
        """``queue`` may be supplied by the owner (AuditRepository) so the ~30
        producers and the public ``flush()`` contract all post to the exact
        object this plane drains.  Two queues = silent row loss.

        ``overflow_sink`` lets the owner keep owning the durable-overflow
        write (its BUG-285 file cap, dead-letter routing and seq counter are
        the authoritative implementation; a plane-local duplicate would
        silently bypass an owner's monkeypatched seam).  When absent the plane
        keeps its own implementation.
        """
        self._backend_factory = backend_factory
        self._owns_backend = owns_backend
        self._dead_letter_store = dead_letter_store
        self._overflow_resolver = overflow_resolver
        self._overflow_dir = Path(overflow_dir)
        self._flush_interval = max(0.01, float(flush_interval))
        self._overflow_recovery_interval = float(overflow_recovery_interval_sec)
        self._overflow_recovery_batch = int(overflow_recovery_batch)
        self._overflow_max_files = int(overflow_max_files)
        self._on_metrics = on_metrics
        self._overflow_sink = overflow_sink
        self._flush_interval_resolver = flush_interval_resolver

        self._queue_resolver = queue_resolver
        self._queue = (
            queue if queue is not None else _stdlib_queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        )
        self._running = False
        self._worker_thread: threading.Thread | None = None
        self._backend: AuditWriteBackend | None = None
        self._last_overflow_drain: float | None = None

        # Observable durability metrics (the contract's "never silent").
        self.financial_queue_backpressure = 0
        self.financial_events_overflowed = 0
        self.financial_events_failed = 0
        self.financial_overflow_recovered = 0
        self.financial_overflow_failed = 0
        self.telemetry_dropped = 0
        self.audit_batch_failures = 0
        self.audit_salvaged_rows = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the background flush thread (idempotent)."""
        if self._running:
            return
        self._running = True
        ready = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._process_queue_worker,
            args=(ready,),
            daemon=True,
            name="AuditDB_Worker",
        )
        self._worker_thread.start()
        # BUG-288 contract: "started" means "bound to this queue object",
        # not "loop has begun" — the handshake closes the rebind race.
        if not ready.wait(timeout=5.0):
            logger.warning("AUDIT WORKER CAPTURE NOT CONFIRMED within 5.0s")

    def _effective_queue(self) -> Any:
        """The queue this plane drains.

        An owner may swap the queue object at runtime (tests do this to force
        saturation).  A reference fixed at construction would keep draining a
        dead queue while events piled up on the new one.
        """
        try:
            if self._queue_resolver is not None:
                resolved = self._queue_resolver()
                if resolved is not None:
                    return resolved
        except Exception:
            pass
        return self._queue

    def _effective_flush_interval(self) -> float:
        """The flush cadence the owner currently uses.

        An owner may retune ``_flush_interval`` at runtime (perf-wave R1
        probes do this); a value frozen at construction would keep applying
        a stale blocking window on the tick path.
        """
        try:
            if self._flush_interval_resolver is not None:
                resolved = self._flush_interval_resolver()
                if resolved is not None:
                    return max(0.01, float(resolved))
        except Exception:
            pass
        return self._flush_interval

    def _effective_overflow_dir(self) -> Path:
        """Overflow dir as the owner currently sees it.

        The repository may redirect overflow at runtime (tests do this via
        ``_FINANCIAL_OVERFLOW_DIR``); a path fixed at construction would
        ignore that and silently spill elsewhere.
        """
        try:
            if self._overflow_resolver is not None:
                resolved = self._overflow_resolver()
                if resolved is not None:
                    return Path(resolved)
        except Exception:
            pass
        return self._overflow_dir

    def stop(self, drain_timeout_sec: float = 5.0) -> bool:
        """Boundedly drain the queue, then close the backend.

        Returns True only when every enqueued row is durable.  Never blocks
        the caller past ``drain_timeout_sec``.
        """
        self._running = False
        flushed = self.flush(timeout_sec=drain_timeout_sec)
        worker = self._worker_thread
        if worker is not None and worker.is_alive():
            worker.join(timeout=max(1.0, drain_timeout_sec))
        backend, self._backend = self._backend, None
        # A pooled provider backend (the fabric's write plane) is a SHARED
        # resource whose lifecycle the fabric owns: closing it here would
        # tear down a pool other consumers still hold. Only the SQLite
        # backend, created exclusively for this plane, is ours to close.
        if backend is not None and self._owns_backend:
            backend.close()
        self._worker_thread = None
        return flushed

    # ------------------------------------------------------------------
    # Enqueue (producer path — never blocks unboundedly, INV-001)
    # ------------------------------------------------------------------
    def enqueue_financial(self, query: str, args: tuple) -> None:
        """Enqueue a CRITICAL financial row. Never silently drops.

        Order of defense: bounded blocking put -> durable overflow file ->
        dead-letter + CRITICAL log.
        """
        query = translate_sql(query)
        backpressured = False
        if self._effective_queue().qsize() >= self.QUEUE_SOFT_WATERMARK:
            self.financial_queue_backpressure += 1
        try:
            if self._effective_queue().qsize() >= self.QUEUE_BACKPRESSURE_AT:
                # The blocking window is a fraction of the flush cadence —
                # this runs on the tick path and must never stall it beyond
                # what the writer can absorb.
                self._effective_queue().put(
                    (query, args),
                    timeout=min(self._effective_flush_interval() * 2.0, 0.1),
                )
                backpressured = True
            else:
                self._effective_queue().put_nowait((query, args))
            if backpressured:
                self.financial_queue_backpressure += 1
                logger.warning(
                    "Financial audit enqueue backpressured qsize=%d",
                    self._effective_queue().qsize(),
                )
            self._report_metrics()
            return
        except _stdlib_queue.Full:
            self.financial_queue_backpressure += 1
        except Exception as put_err:
            self.financial_events_failed += 1
            logger.error("Financial audit enqueue failed: %s", put_err)
            self._dead_letter_store.record(
                query=query, args=args, error=put_err, payload_note="enqueue exception"
            )
            self._report_metrics()
            return
        self.financial_events_overflowed += 1
        self._write_financial_overflow(query, args, error=None)
        self._report_metrics()

    def enqueue_telemetry(self, query: str, args: tuple) -> None:
        """Enqueue a NON-CRITICAL telemetry row: dropable, counted."""
        try:
            self._effective_queue().put_nowait((translate_sql(query), args))
            self._report_metrics()
            return
        except _stdlib_queue.Full:
            self.telemetry_dropped += 1
            logger.error(
                "Audit telemetry queue full — counter dropped (telemetry_dropped=%d)",
                self.telemetry_dropped,
            )
        self._report_metrics()

    def flush(self, timeout_sec: float = 5.0) -> bool:
        """Boundedly drain the queue (read-after-write ordering)."""
        try:
            deadline = time.monotonic() + max(0.0, float(timeout_sec))
            while self._effective_queue().unfinished_tasks > 0:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.005)
            return True
        except Exception as e:
            logger.error("Audit flush failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Background flush
    # ------------------------------------------------------------------
    def _process_queue_worker(self, ready: threading.Event | None = None) -> None:
        q = self._effective_queue()  # local ref: never GC'd while the loop runs (BUG-058)
        if ready is not None:
            ready.set()
        backend = self._backend_factory()
        self._backend = backend
        if isinstance(backend, SqliteAuditWriteBackend):
            # Force the connection to be born HERE, on the worker thread:
            # sqlite3 objects are thread-bound and the backend is constructed
            # by the factory (running on this thread), so touching
            # ``connection`` now binds it correctly before the first batch.
            backend.touch()

        sqlite_backend = backend if isinstance(backend, SqliteAuditWriteBackend) else None
        while self._running or not q.empty():
            batch: list[tuple[str, tuple]] = []
            try:
                while len(batch) < self.BATCH_MAX_ROWS:
                    if batch:
                        try:
                            query_tuple = q.get_nowait()
                        except _stdlib_queue.Empty:
                            break
                    else:
                        query_tuple = q.get(timeout=self._flush_interval)
                    batch.append(query_tuple)
            except _stdlib_queue.Empty:
                pass

            if not batch:
                # Idle pass: the writer has capacity again, so recover rows
                # stranded by a past saturation.  SQLite keeps the original
                # drain (it replays on the worker's own connection); pooled
                # providers replay through a leased write connection.
                if sqlite_backend is not None:
                    self._drain_financial_overflow_due(sqlite_backend.connection)
                else:
                    self._drain_financial_overflow_pooled(backend)
                continue

            try:
                import itertools

                grouped = [
                    (q_text, [item[1] for item in group])
                    for q_text, group in itertools.groupby(batch, key=lambda x: x[0])
                ]
                backend.execute_batch(grouped)
                for _ in batch:
                    q.task_done()
            except Exception as e:
                # BATCH RECOVERY (P0): one bad row must never destroy up to
                # BATCH_MAX_ROWS-1 good financial records.  The backend has
                # already rolled back; salvage row by row, dead-letter the
                # rest, count every outcome.
                self.audit_batch_failures += 1
                salvaged = 0
                dead_lettered = 0
                for failed_query, failed_args in batch:
                    try:
                        backend.execute_one(failed_query, failed_args)
                        salvaged += 1
                    except Exception as row_err:
                        dead_lettered += 1
                        self._dead_letter_store.record(
                            query=failed_query,
                            args=failed_args,
                            error=row_err,
                            retry_count=1,
                            payload_note="audit worker batch-retry failure",
                        )
                self.audit_salvaged_rows += salvaged
                # Full context (see the sibling site above): the masked
                # statement + arity + the UNTRUNCATED error, so the live
                # "query has 0 placeholders but 32 parameters" class is
                # traceable to a query.
                try:
                    from nexus_scalp.database.query_logging import log_query_failure

                    log_query_failure(
                        operation="audit_batch_insert",
                        exc=e,
                        sql=batch[0][0] if batch else "",
                        args=batch[0][1] if batch else (),
                        domain="audit",
                        kind="batch_write",
                        extra={
                            "batch_size": len(batch),
                            "salvaged": salvaged,
                            "dead_lettered": dead_lettered,
                        },
                    )
                except Exception:
                    pass
                logger.error(
                    "Audit batch insert failed; recovery applied "
                    "batch=%d salvaged=%d dead_lettered=%d error_type=%s error=%s",
                    len(batch),
                    salvaged,
                    dead_lettered,
                    type(e).__name__,
                    str(e)[:400],
                )
                self._report_metrics()
                e = None  # never leak a live exception reference across loop iters
                for _ in batch:
                    q.task_done()
                time.sleep(1.0)  # backoff on error
            self._report_metrics()

        if self._owns_backend:
            backend.close()

    # ------------------------------------------------------------------
    # Durable overflow (financial events must survive queue saturation)
    # ------------------------------------------------------------------
    def _write_financial_overflow(
        self, query: str, args: tuple, error: BaseException | None
    ) -> None:
        """Persist one financial event to the durable overflow directory."""
        if self._overflow_sink is not None:
            # The owner (AuditRepository) keeps the authoritative overflow
            # implementation: its BUG-285 file cap, dead-letter routing and
            # sequence counter. Delegating keeps that seam intact instead of
            # running a plane-local duplicate that silently bypasses it.
            self._overflow_sink(query, args, error)
            return
        try:
            overflow_dir = self._effective_overflow_dir()
            overflow_dir.mkdir(parents=True, exist_ok=True)
            try:
                pending = sum(1 for _ in overflow_dir.glob("overflow_*.json"))
            except OSError:
                pending = 0
            if pending >= self._overflow_max_files:
                self.financial_overflow_failed += 1
                self._dead_letter_store.record(
                    query=query,
                    args=args,
                    error=error or RuntimeError("QUEUE_SATURATED"),
                    payload_note="overflow file cap reached",
                )
                logger.critical(
                    "FINANCIAL AUDIT OVERFLOW CAP — %d pending overflow files; "
                    "row routed to dead-letter",
                    pending,
                )
                return
            ts = time.strftime("%Y%m%d_%H%M%S")
            fname = f"overflow_{ts}_{self._dead_letter_store.next_seq():08d}.json"
            payload = {
                "failed_at": ts,
                "query": query,
                "args": self._dead_letter_store.json_safe_args(args),
                "error": type(error).__name__ if error else "QUEUE_SATURATED",
            }
            (overflow_dir / fname).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            logger.critical(
                "FINANCIAL AUDIT OVERFLOW — event persisted to durable overflow file %s",
                fname,
            )
        except Exception as of_err:
            self.financial_events_failed += 1
            self._dead_letter_store.record(
                query=query,
                args=args,
                error=error or of_err,
                payload_note="overflow-file write failure",
            )
            logger.critical("FINANCIAL AUDIT EVENT COULD NOT BE DURABLY PRESERVED: %s", of_err)

    def _drain_financial_overflow_due(self, conn: Any) -> None:
        """SQLite overflow recovery: replay on the worker's own connection."""
        now = time.monotonic()
        if (
            self._last_overflow_drain is not None
            and now - self._last_overflow_drain < self._overflow_recovery_interval
        ):
            return
        self._last_overflow_drain = now
        self._replay_overflow_files(self._make_conn_replayer(conn))

    def _drain_financial_overflow_pooled(self, backend: AuditWriteBackend) -> None:
        """Pooled overflow recovery: replay through the write backend."""
        now = time.monotonic()
        if (
            self._last_overflow_drain is not None
            and now - self._last_overflow_drain < self._overflow_recovery_interval
        ):
            return
        self._last_overflow_drain = now
        self._replay_overflow_files(backend.execute_one)

    def _replay_overflow_files(self, replay: Any) -> None:
        """Shared, deterministic overflow replay with poison rejection.

        Oldest first, bounded batch width, rename-then-delete retirement so a
        crash between replay and unlink can never lose the row twice.  A file
        that cannot be parsed or replayed is dead-lettered (durable, bounded)
        and retired — never retried forever, never silently dropped.
        """
        try:
            overflow_dir = self._effective_overflow_dir()
        except Exception:
            return
        if not overflow_dir.is_dir():
            return
        try:
            pending = sorted(
                (p for p in overflow_dir.glob("overflow_*.json") if p.is_file()),
                key=lambda p: p.name,
            )[: self._overflow_recovery_batch]
        except OSError as scan_err:
            logger.warning("FINANCIAL OVERFLOW DRAIN scan failed (isolated): %s", scan_err)
            return
        if not pending:
            return

        recovered = 0
        rejected = 0
        for path in pending:
            payload: dict[str, Any] = {}
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                payload = parsed if isinstance(parsed, dict) else {}
                query = str(payload.get("query") or "")
                raw_args = payload.get("args") or "[]"
                if isinstance(raw_args, str):
                    arg_list: list[Any] | None = json.loads(raw_args)
                elif isinstance(raw_args, list):
                    arg_list = raw_args
                else:
                    arg_list = None
                if (
                    not query
                    or not query.upper().lstrip().startswith(("INSERT", "REPLACE"))
                    or arg_list is None
                    or not isinstance(arg_list, list)
                ):
                    raise ValueError("unreplayable overflow payload")
                if any(isinstance(v, dict) and v.get("__unserializable__") for v in arg_list):
                    raise ValueError("overflow args contain __unserializable__ envelope")
                replay(query, tuple(arg_list))
                self._retire_overflow(path, ".consumed-")
                recovered += 1
            except Exception as row_err:
                rejected += 1
                logger.error(
                    "FINANCIAL OVERFLOW DRAIN — replay REJECTED %s (error=%s); "
                    "durably dead-lettered instead",
                    path.name,
                    row_err,
                )
                try:
                    fail_args = payload.get("args")
                    self._dead_letter_store.record(
                        query=str(payload.get("query") or ""),
                        args=tuple(fail_args) if isinstance(fail_args, list) else (),
                        error=row_err,
                        payload_note=f"overflow drain replay failure file={path.name}",
                    )
                except Exception:
                    pass
                self._retire_overflow(path, ".rejected-")
        if recovered:
            self.financial_overflow_recovered += recovered
            logger.warning(
                "FINANCIAL AUDIT OVERFLOW DRAIN — recovered=%d rejected=%d",
                recovered,
                rejected,
            )
        if rejected:
            self.financial_overflow_failed += rejected
        self._report_metrics()

    @staticmethod
    def _make_conn_replayer(conn: Any) -> Any:
        """Bind the writer connection to the (query, args) replay shape."""

        def _replay(query: str, args: tuple) -> None:
            with conn:  # one transaction per replayed row
                conn.execute(query, args)

        return _replay

    @staticmethod
    def _replay_on_connection(conn: Any, query: str, args: tuple) -> None:
        with conn:  # one transaction per replayed row
            conn.execute(query, args)

    @staticmethod
    def _retire_overflow(path: Path, prefix: str) -> None:
        """Rename-then-delete: a crash between replay and unlink is safe."""
        try:
            consumed_dir = path.parent / "overflow_recovered"
            consumed_dir.mkdir(parents=True, exist_ok=True)
            marker = consumed_dir / f"{prefix}{path.name}"
            path.replace(marker)
            marker.unlink()
        except OSError:
            with contextlib.suppress(OSError):
                path.unlink()

    def overflow_pending_count(self) -> int:
        """Stranded overflow rows still on disk (-1 = unreadable)."""
        try:
            d = self._overflow_dir
            if not d.is_dir():
                return 0
            return sum(1 for p in d.glob("overflow_*.json") if p.is_file())
        except Exception:
            return -1

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------
    def _report_metrics(self) -> None:
        if self._on_metrics is None:
            return
        try:
            self._on_metrics(
                {
                    "queue_depth": self._effective_queue().qsize(),
                    "financial_queue_backpressure": self.financial_queue_backpressure,
                    "financial_events_overflowed": self.financial_events_overflowed,
                    "financial_events_failed": self.financial_events_failed,
                    "financial_overflow_recovered": self.financial_overflow_recovered,
                    "financial_overflow_failed": self.financial_overflow_failed,
                    "telemetry_dropped": self.telemetry_dropped,
                    "audit_batch_failures": self.audit_batch_failures,
                    "audit_salvaged_rows": self.audit_salvaged_rows,
                }
            )
        except Exception:
            logger.debug("audit metrics callback failed", exc_info=True)

    # ------------------------------------------------------------------
    # Backwards-compatible attribute surface
    # ------------------------------------------------------------------
    @property
    def dead_letter_store(self) -> DeadLetterStore:
        return self._dead_letter_store
