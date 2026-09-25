"""
Candle Intelligence Store — Part 1: Schema
===========================================
Isolated local SQLite persistence for the candle-intelligence module
(BUG-061). No network, no cloud, no remote telemetry — a local-only database
layer. Owns 12 tables, all with the required audit columns.

This file holds the schema and connection helpers; part 2 holds the
read/write methods on the same class (appended via a second module import).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import queue
import sqlite3
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.candle_intelligence.store")

#: The 12 isolated tables required by spec §8.
TABLES = (
    "candles",
    "candle_closures",
    "candle_patterns",
    "market_regimes",
    "feature_vectors",
    "trade_proposals",
    "trade_decisions",
    "open_positions",
    "exit_signals",
    "risk_evaluations",
    "rule_vetoes",
    "audit_log",
)

#: Common audit columns appended to every table (spec §8).
#: Leading comma: the schema fragments end with a bare column/UNIQUE clause.
_COMMON = """
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    regime TEXT DEFAULT '',
    pattern_name TEXT DEFAULT '',
    pattern_score REAL DEFAULT 0.0,
    candle_close_classification TEXT DEFAULT '',
    decision_type TEXT DEFAULT '',
    risk_state TEXT DEFAULT '',
    reason_codes TEXT DEFAULT '[]',
    raw_payload TEXT DEFAULT '{}',
    computed_payload TEXT DEFAULT '{}'
"""

_SCHEMAS: dict[str, str] = {
    "candles": f"""
        CREATE TABLE IF NOT EXISTS candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL DEFAULT 0.0,
            is_complete INTEGER DEFAULT 1,
            {_COMMON}
            ,UNIQUE(bar_ts)
        )""",
    "candle_closures": f"""
        CREATE TABLE IF NOT EXISTS candle_closures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            range REAL DEFAULT 0.0,
            body REAL DEFAULT 0.0,
            upper_wick REAL DEFAULT 0.0,
            lower_wick REAL DEFAULT 0.0,
            body_ratio REAL DEFAULT 0.0,
            upper_wick_ratio REAL DEFAULT 0.0,
            lower_wick_ratio REAL DEFAULT 0.0,
            close_position_in_range REAL DEFAULT 0.0,
            open_to_close_direction TEXT DEFAULT '',
            close_strength REAL DEFAULT 0.0,
            rejection_score REAL DEFAULT 0.0,
            continuation_score REAL DEFAULT 0.0,
            reversal_score REAL DEFAULT 0.0,
            indecision_score REAL DEFAULT 0.0,
            momentum_decay_score REAL DEFAULT 0.0,
            close_quality TEXT DEFAULT '',
            {_COMMON}
            ,UNIQUE(bar_ts)
        )""",
    "candle_patterns": f"""
        CREATE TABLE IF NOT EXISTS candle_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            direction TEXT DEFAULT '',
            raw_score REAL DEFAULT 0.0,
            context_weight REAL DEFAULT 0.0,
            confidence_score REAL DEFAULT 0.0,
            requires_confirmation INTEGER DEFAULT 1,
            {_COMMON}
        )""",
    "market_regimes": f"""
        CREATE TABLE IF NOT EXISTS market_regimes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            volatility_state TEXT DEFAULT '',
            atr REAL DEFAULT 0.0,
            spread REAL DEFAULT 0.0,
            {_COMMON}
        )""",
    "feature_vectors": f"""
        CREATE TABLE IF NOT EXISTS feature_vectors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            feature_json TEXT DEFAULT '{{}}',
            {_COMMON}
        )""",
    "trade_proposals": f"""
        CREATE TABLE IF NOT EXISTS trade_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            proposal_json TEXT DEFAULT '{{}}',
            action TEXT DEFAULT '',
            confidence REAL DEFAULT 0.0,
            {_COMMON}
        )""",
    "trade_decisions": f"""
        CREATE TABLE IF NOT EXISTS trade_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            trade_bias TEXT DEFAULT '',
            confidence_score REAL DEFAULT 0.0,
            entry_allowed INTEGER DEFAULT 0,
            hold_allowed INTEGER DEFAULT 1,
            fast_exit_required INTEGER DEFAULT 0,
            exit_required INTEGER DEFAULT 0,
            modify_order INTEGER DEFAULT 0,
            cancel_pending INTEGER DEFAULT 0,
            no_trade_reason TEXT DEFAULT '',
            {_COMMON}
        )""",
    "open_positions": f"""
        CREATE TABLE IF NOT EXISTS open_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket TEXT NOT NULL,
            entry_price REAL DEFAULT 0.0,
            current_price REAL DEFAULT 0.0,
            stop_loss REAL DEFAULT 0.0,
            take_profit REAL DEFAULT 0.0,
            volume REAL DEFAULT 0.0,
            floating_pnl REAL DEFAULT 0.0,
            state TEXT DEFAULT '',
            snapshot_before TEXT DEFAULT '{{}}',
            snapshot_after TEXT DEFAULT '{{}}',
            {_COMMON}
        )""",
    "exit_signals": f"""
        CREATE TABLE IF NOT EXISTS exit_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            ticket TEXT DEFAULT '',
            exit_mechanism TEXT DEFAULT '',
            exit_reason TEXT DEFAULT '',
            {_COMMON}
        )""",
    "risk_evaluations": f"""
        CREATE TABLE IF NOT EXISTS risk_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            risk_allowed INTEGER DEFAULT 1,
            risk_notes TEXT DEFAULT '',
            {_COMMON}
        )""",
    "rule_vetoes": f"""
        CREATE TABLE IF NOT EXISTS rule_vetoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            veto_level INTEGER DEFAULT 0,
            veto_rule TEXT DEFAULT '',
            veto_reason TEXT DEFAULT '',
            {_COMMON}
        )""",
    "audit_log": f"""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bar_ts TEXT NOT NULL,
            event TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            {_COMMON}
        )""",
}


def _redirect_database(dsn: str, database: str) -> str:
    """Re-point a URL or libpq DSN at ``database``, leaving host/user/secret alone.

    The fabric derives the connection role from the DSN and its pools reconnect
    lazily, so the role and the credential must survive the redirect — a plain
    string replace of the last path segment does that for a URL DSN, and the
    keyword form needs ``dbname=`` rewritten in place.
    """
    if not database:
        return dsn
    if "://" in dsn:
        head, _, _old = dsn.rpartition("/")
        return f"{head}/{database}"
    parts = []
    seen = False
    for pair in dsn.split():
        if "=" not in pair:
            parts.append(pair)
            continue
        key, _, _unused_value = pair.partition("=")
        if key.strip().lower() in {"dbname", "database"}:
            parts.append(f"{key}={database}")
            seen = True
        else:
            parts.append(pair)
    if not seen:
        parts.append(f"dbname={database}")
    return " ".join(parts)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _j(obj: Any) -> str:
    """Safe JSON serialization: NaN/Inf -> None, deterministically."""

    def _clean(o: Any) -> Any:
        if isinstance(o, float):
            if math.isnan(o) or math.isinf(o):
                return None
            return o
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_clean(v) for v in o]
        if isinstance(o, datetime):
            return o.isoformat()
        return o

    return json.dumps(_clean(obj), sort_keys=True, separators=(",", ":"), default=str)


def _common_kwargs(
    ts: str,
    symbol: str,
    timeframe: str,
    regime: str = "",
    pattern_name: str = "",
    pattern_score: float = 0.0,
    candle_close_classification: str = "",
    decision_type: str = "",
    risk_state: str = "",
    reason_codes: list[str] | None = None,
    raw_payload: dict[str, Any] | None = None,
    computed_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "symbol": symbol,
        "timeframe": timeframe,
        "regime": regime,
        "pattern_name": pattern_name,
        "pattern_score": pattern_score,
        "candle_close_classification": candle_close_classification,
        "decision_type": decision_type,
        "risk_state": risk_state,
        "reason_codes": _j(reason_codes or []),
        "raw_payload": _j(raw_payload or {}),
        "computed_payload": _j(computed_payload or {}),
    }


class CandleIntelStore:
    """Isolated SQLite persistence layer for candle intelligence (BUG-061).

    PERFORMANCE (BUG-061 follow-up): the tick path must NEVER block on disk.
    All ``record_*`` methods enqueue onto an in-memory ring buffer and return
    in O(1) microseconds; a dedicated background worker thread drains the ring
    into SQLite in batched transactions (up to ``max_batch_size`` rows per
    commit, WAL mode). Reads resolve from RAM first, DB only for history.

    Record methods are attached from :mod:`store_writes` at module load;
    declared here as attributes for type checkers.
    """

    record_candle: Any
    record_candle_closure: Any
    record_patterns: Any
    record_regime: Any
    record_risk: Any
    record_decision: Any
    record_veto: Any
    record_audit_log: Any
    _insert: Any

    #: Ring buffer capacity per table (RAM bound; oldest rows evicted to keep
    #: memory flat under sustained load).
    RING_CAPACITY: int = 2000

    def __init__(
        self,
        config: CandleIntelligenceConfig | None = None,
        db_config: DatabaseConfig | None = None,
    ) -> None:
        """Provider-aware candle intelligence store (DATABASE PORTABILITY).

        `config` carries the SQLite path as before; `db_config` selects the
        provider explicitly (PostgreSQL).  SQLite remains the default.
        """
        self.config = config or CandleIntelligenceConfig()
        if db_config is not None:
            self._config = db_config
        else:
            # DATABASE PORTABILITY (the audit-domain contract): the ACTIVE
            # provider is the one the operator chose — resolved through
            # load_database_config('candle_intel'), never hard-coded. The
            # caller's explicit db_config still wins (tests + explicit paths).
            self._config = load_database_config("candle_intel")
            if self._config.is_sqlite and self.config.db_path:
                # Keep honoring an explicit SQLite path the caller passed; a
                # relative one is anchored to the runtime workspace below.
                self._config = DatabaseConfig.for_sqlite("candle_intel", path=self.config.db_path)
        if self._config.is_sqlite and not Path(self.config.db_path or "").is_absolute():
            # BUG-149: a relative default ("artifacts/candle_intel.db") anchors
            # to the canonical runtime workspace (bundle when frozen), never
            # the raw process CWD.
            from nexus_scalp.release.paths import get_runtime_workspace

            self.config.db_path = str(get_runtime_workspace() / self.config.db_path)
            self._config = DatabaseConfig.for_sqlite("candle_intel", path=self.config.db_path)
        if self._config.is_sqlite:
            self.config.db_path = self._config.sqlite_connect_path
        else:
            # Under PostgreSQL the SQLite path is meaningless: keep the config's
            # default populated (a later switch back still resolves the file)
            # but never CREATE it — an makedirs / a driver connect on a path the
            # provider does not use resurrects the "SQLite silently kept
            # writing" defect this store's resolution order exists to prevent.
            self.config.db_path = self.config.db_path or self._config.sqlite_path
        self._db_path = self.config.db_path
        self._driver = get_driver(self._config)
        if self._config.is_sqlite:
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)) or ".", exist_ok=True)

        # In-memory fast path: per-table ring buffers + a shared write queue.
        self._rings: dict[str, deque[dict[str, Any]]] = {
            t: deque(maxlen=self.RING_CAPACITY) for t in TABLES
        }
        self._write_queue: queue.Queue[tuple[str, list[str], list[Any]] | None] = queue.Queue(
            maxsize=20000
        )
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._write_lock = threading.Lock()

        # SQLite connection is OWNED by the worker thread only.
        self._conn: sqlite3.Connection | None = None
        self._reader_conn = self._connect_reader()
        self._init_schema()
        self._start_worker()
        logger.info(
            "Candle intelligence store initialized (RAM ring + async worker)",
            db_path=self._db_path if self._config.is_sqlite else None,
            provider=self._config.provider.value,
        )

    def _connect_reader(self) -> Any:
        """Portable reader connection (SQLite native; PostgreSQL pooled)."""
        if self._config.is_sqlite:
            conn = self._driver.connect(timeout=15.0)
            conn.row_factory = sqlite3.Row
            return conn
        # PostgreSQL: the fabric's pooled READ backend — a separate read-only
        # pool, exactly as the audit domain resolves reads. A borrowed pooled
        # connection is a context manager (never caller-owned), so it is held
        # for the store's lifetime and only used for the read-only fallback
        # path of query_recent(). Never used for writes (see _connect_writer).
        backend = self._resolve_read_backend()
        if backend is None:
            return _UnusableReadConnection(self._config)
        return _PooledReadConnection(backend)

    def _connect_writer(self) -> Any:
        """Portable writer connection (worker thread)."""
        if self._config.is_sqlite:
            return self._driver.connect(timeout=15.0)
        # PostgreSQL: the fabric's pooled WRITE backend, resolved lazily on
        # this worker thread (the pool is shared; a fresh lease per batch is
        # exactly what the pooled write plane is for). See _flush_batch.
        return _PooledWriteConnection(self)

    def _init_schema(self) -> None:
        """Schema bootstrap on the reader connection (safe; worker uses same DB
        file, WAL allows concurrent access)."""
        # Self-heal corrupted DB (candle_intel.db with bare candles(id) table) — see commit fde756b fix
        with contextlib.suppress(Exception):
            probe_cur = (
                self._conn.cursor() if hasattr(self, "_conn") and self._conn is not None else None
            )
            if probe_cur is not None:
                try:
                    probe_cur.execute("SELECT bar_ts FROM candles LIMIT 0")
                except Exception:
                    for _tbl in (
                        "candles",
                        "candle_closures",
                        "candle_patterns",
                        "market_regimes",
                        "feature_vectors",
                        "trade_proposals",
                        "trade_decisions",
                        "open_positions",
                        "exit_signals",
                        "risk_evaluations",
                        "rule_vetoes",
                        "audit_log",
                    ):
                        try:
                            probe_cur.execute(f"DROP TABLE IF EXISTS {_tbl}")
                        except Exception:
                            pass
                    try:
                        self._conn.commit()
                    except Exception:
                        pass
        if self._config.is_sqlite:
            # SQLite: schema bootstrap on the reader connection is safe — the
            # worker uses the same DB file and WAL allows concurrent access.
            with self._reader_conn:
                self._reader_conn.execute("PRAGMA journal_mode = WAL;")
                self._reader_conn.execute("PRAGMA synchronous = NORMAL;")
                for sql in _SCHEMAS.values():
                    self._reader_conn.execute(sql)
                for table in TABLES:
                    self._reader_conn.execute(
                        f"CREATE INDEX IF NOT EXISTS idx_{table}_ts ON {table}(ts);"
                    )
            return
        # PostgreSQL: the fabric's pooled write backend owns DDL (a pooled read
        # connection is read-only by construction — it would reject the DDL),
        # and the SQLite-dialect _SCHEMAS must go through translate_ddl first
        # (``AUTOINCREMENT`` is not PostgreSQL syntax). The translated
        # statements are re-applied idempotently, so a domain provisioned by
        # ``nexus db connect`` and a direct store construction converge on the
        # same physical schema.
        #
        # A placeholder reader (an unprovisioned domain at construction time)
        # must NOT short-circuit this: ``_init_schema_postgres`` resolves the
        # write backend — provisioning the domain, which is what creates the
        # tables — and a store built before ``nexus db connect`` would
        # otherwise silently converge on an empty database (the exact
        # model-but-table-missing defect the convergence test exists for).
        self._init_schema_postgres()

    def _init_schema_postgres(self) -> None:
        """Bootstrap the domain schema on the pooled PostgreSQL backend.

        ``provision_domain`` already migrates the domain's authored DDL through
        ``migrate_domain`` — re-running the translated ``_SCHEMAS`` here races
        with that pass for the same relation names (observed as
        ``duplicate key value violates unique constraint
        "pg_type_typname_nsp_index"`` when both passes create the same table
        inside overlapping transactions).  Resolve, which provisions, is
        enough; the store only owns the *choice* to provision, not the DDL.
        """
        backend = self._resolve_write_backend()
        if backend is None:
            raise RuntimeError(
                "CandleIntelStore resolved a PostgreSQL provider but the "
                "candle_intel domain has no pooled write backend. Provision it "
                "via the database fabric (`nexus db connect`) before switching "
                "providers — refusing to silently drop the schema bootstrap."
            )

    # ------------------------------------------------------------------
    # background worker
    # ------------------------------------------------------------------

    def _start_worker(self) -> None:
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="candle_intel_writer",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        """Drains the write queue into SQLite in batched transactions."""
        try:
            self._conn = self._connect_writer()
            if self._config.is_sqlite:
                self._conn.execute("PRAGMA journal_mode = WAL;")
                self._conn.execute("PRAGMA synchronous = NORMAL;")
        except Exception as e:
            logger.error("[CANDLE_INTEL] writer conn failed", error=str(e))
            return

        batch: list[tuple[str, list[str], list[Any]]] = []
        # IDLE-POLL (2026-09-06 debugger wall): the old 0.3s timeout raised
        # queue.Empty ~3x/sec whenever the writer idled; an attached debugger
        # prints every first-chance Empty as a full wall. 5s keeps the same
        # bounded-latency class for readers<->DB while cutting walls ~16x.
        # Items dispatch IMMEDIATELY on arrival (get returns at once).
        flush_interval = 5.0  # seconds; bounded latency for readers <-> DB
        while not self._stop.is_set() or not self._write_queue.empty():
            try:
                item = self._write_queue.get(timeout=flush_interval)
            except queue.Empty:
                item = None
            if item is not None:
                batch.append(item)
                # Drain everything already queued before flushing: a flush must
                # commit the whole visible batch in one transaction, and
                # ``flush()`` returns as soon as the QUEUE is empty — so unless
                # an emptied queue implies a committed batch, a caller that
                # enqueues 5 rows and waits for ``flush()`` would read 0 back
                # (the rows were dequeued into a worker-side buffer that had
                # not reached ``max_batch_size`` and would not until the idle
                # timer fired).
                while True:
                    try:
                        batch.append(self._write_queue.get_nowait())
                    except queue.Empty:
                        break
                self._flush_batch(batch)
                batch = []
            elif batch:
                self._flush_batch(batch)
                batch = []
        if batch:
            self._flush_batch(batch)
        with contextlib.suppress(Exception):
            if self._conn:
                self._conn.close()

    def _flush_batch(self, batch: list[tuple[str, list[str], list[Any]]]) -> None:
        if self._config.is_sqlite:
            self._flush_batch_sqlite(batch)
            return
        # PostgreSQL: batched through the fabric's pooled write plane —
        # `execute_batch` commits the whole batch atomically (or rolls it all
        # back), the same one-transaction-per-batch contract SQLite has.
        self._flush_batch_postgres(batch)

    def _flush_batch_sqlite(self, batch: list[tuple[str, list[str], list[Any]]]) -> None:
        if not self._conn:
            return
        try:
            with self._conn:
                for table, cols, vals in batch:
                    sql_placeholders = self._driver.qmarks(len(cols))
                    sql = (
                        f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) "
                        f"VALUES ({sql_placeholders})"
                    )
                    self._conn.execute(sql, list(vals))
        except Exception as e:
            logger.error("[CANDLE_INTEL] batch flush failed", error=str(e))

    def _flush_batch_postgres(self, batch: list[tuple[str, list[str], list[Any]]]) -> None:
        """Apply one batched transaction on the pooled PostgreSQL backend."""
        backend = self._resolve_write_backend()
        if backend is None:
            logger.error(
                "[CANDLE_INTEL] PostgreSQL batch dropped: candle_intel domain is "
                "not provisioned on the database fabric (run `nexus db connect`)"
            )
            return
        # Group by statement (same table + columns share one executemany call);
        # the pooled write plane translates the placeholders and commits once.
        grouped: list[tuple[str, list[tuple[Any, ...]]]] = []
        index: dict[str, int] = {}
        for table, cols, vals in batch:
            cols_csv = ", ".join(cols)
            sql = f"INSERT INTO {table} ({cols_csv}) VALUES ({self._driver.qmarks(len(cols))})"
            sql += " ON CONFLICT DO NOTHING"
            pos = index.get(sql)
            if pos is None:
                index[sql] = pos = len(grouped)
                grouped.append((sql, []))
            grouped[pos][1].append(tuple(vals))
        try:
            backend.execute_batch(grouped)
        except Exception as e:
            logger.error("[CANDLE_INTEL] PG batch flush failed", error=str(e))

    # ------------------------------------------------------------------
    # enqueue API (hot path — O(1), no disk)
    # ------------------------------------------------------------------

    def enqueue(self, table: str, cols: list[str], values: list[Any]) -> bool:
        """Non-blocking enqueue to RAM + background queue. Returns True if
        accepted (queue not full). NEVER touches disk on the caller's thread."""
        try:
            self._write_queue.put_nowait((table, cols, values))
        except queue.Full:
            return False
        # Mirror into the ring for instant reads.
        with contextlib.suppress(Exception):
            rec = dict(zip(cols, values, strict=False))
            ring = self._rings.get(table)
            if ring is not None:
                ring.append(rec)
        return True

    def pending_count(self) -> int:
        """Rows queued but not yet flushed to disk (for observability)."""
        return self._write_queue.qsize()

    def flush(self, timeout: float = 3.0) -> int:
        """Synchronously drain the queue (called at shutdown / checkpoints)."""
        deadline = time.monotonic() + timeout
        while self.pending_count() > 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.pending_count()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.flush(timeout=2.0)
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        with contextlib.suppress(Exception):
            if self._reader_conn:
                self._reader_conn.close()
        logger.info("Candle intelligence store closed")

    def __enter__(self) -> CandleIntelStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # bounded read facade
    # ------------------------------------------------------------------

    def query_recent(self, table: str, limit: int = 50) -> list[dict[str, Any]]:
        """Newest rows from one of the 12 tables.

        FAST PATH: reads the in-memory ring buffer (no disk I/O) — this is the
        hot-path query used by the engine. Falls back to SQLite only when the
        ring has no rows for the table (e.g. after restart or for deep history).
        """
        if table not in TABLES:
            return []
        limit = max(1, min(int(limit), 2000))

        # 1) RAM first (thread-safe snapshot).
        ring = self._rings.get(table)
        if ring:
            with self._write_lock:
                snapshot = list(ring)
            if snapshot:
                snapshot.reverse()  # newest first
                out: list[dict[str, Any]] = []
                for r in snapshot[-limit:]:
                    d = dict(r)
                    self._jsonify(d)
                    out.append(d)
                return out

        # 2) DB fallback (history/restart).
        if self._config.is_sqlite:
            rows = self._reader_conn.execute(
                f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            # Pooled read backend (read-only pool). The ring is empty here, so
            # this is a cold-start / deep-history path, never the hot path.
            read_backend = self._resolve_read_backend()
            if read_backend is None:
                return []
            rows = read_backend.query(f"SELECT * FROM {table} ORDER BY id DESC LIMIT %s", (limit,))
        out = []
        for r in rows:
            d = dict(r)
            self._jsonify(d)
            out.append(d)
        return out

    def _jsonify(self, d: dict[str, Any]) -> None:
        """Convert stored JSON strings back to objects in-place."""
        for k in (
            "reason_codes",
            "raw_payload",
            "computed_payload",
            "snapshot_before",
            "snapshot_after",
            "feature_json",
            "proposal_json",
            "detail",
        ):
            if k in d and isinstance(d[k], str):
                with contextlib.suppress(Exception):
                    d[k] = json.loads(d[k])

    def db_size_bytes(self) -> int:
        try:
            if not self._config.is_sqlite:
                size = self._driver.database_size_bytes()
                return int(size or 0)
            return int(
                self._reader_conn.execute("PRAGMA page_count").fetchone()[0]
                * self._reader_conn.execute("PRAGMA page_size").fetchone()[0]
            )
        except Exception:
            return 0

    def integrity_ok(self) -> bool:
        try:
            if not self._config.is_sqlite:
                return self._driver.ping()
            row = self._reader_conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row and row[0] == "ok")
        except Exception:
            return False

    # ------------------------------------------------------------------
    # fabric backend resolution (PostgreSQL only)
    # ------------------------------------------------------------------
    # The pooled backends are the fabric's shared resource: this store never
    # owns or closes them (the SQLite writer connection it owns is a different
    # contract — see close()). Resolution is LAZY and per call, never captured
    # at construction: ``__init__`` builds the schema on the write backend and
    # only then is the domain registered, so a construction-time capture would
    # observe ``None`` on a fresh PostgreSQL process and cache it forever.
    # Mirrors the audit domain's ``_build_pooled_write_backend`` contract.

    def _resolve_write_backend(self) -> Any:
        """The fabric's pooled write backend for candle_intel, or None."""
        try:
            from nexus_scalp.database.fabric import get_domain_backend, provision_domain

            backend = get_domain_backend("candle_intel", readonly=False)
            if backend is not None:
                return backend
            # Not provisioned in this process — bootstrap it from the resolved
            # DSN (provision_domain also creates the schema, idempotently).
            dsn = self._pg_dsn()
            if not dsn:
                return None
            return provision_domain("candle_intel", dsn, min_size=1, max_size=4)
        except Exception as exc:
            logger.error("[CANDLE_INTEL] fabric write backend resolve failed: %s", exc)
            return None

    def _resolve_read_backend(self) -> Any:
        """The fabric's pooled READ backend for candle_intel, or None.

        Only resolves the registry (never provisions): the write path owns
        provisioning, and doing it here could double-provision against a
        concurrent writer and close a pool that is in use.
        """
        try:
            from nexus_scalp.database.fabric import get_domain_backend

            return get_domain_backend("candle_intel", readonly=True)
        except Exception as exc:
            logger.error("[CANDLE_INTEL] fabric read backend resolve failed: %s", exc)
            return None

    def _pg_dsn(self) -> str:
        """The resolved PostgreSQL DSN for this domain (secret included).

        Prefers the environment override (``NSE_PG_TEST_URL`` / container DSN),
        which names the operator's real role; the persisted settings path is
        the production default and resolves the secret from the OS-backed
        store. Never logs the value.
        """
        try:
            env_dsn = os.environ.get("NSE_PG_TEST_URL", "").strip()
            if env_dsn:
                # Point the DSN at this store's database while keeping the
                # operator's role + credential (the fabric reconnects lazily,
                # so a mismatched role becomes a fatal auth failure).
                return _redirect_database(env_dsn, self._config.database)
            from nexus_scalp.database.config import build_postgres_url
            from nexus_scalp.settings.secret_store import SecureSecretStore

            return build_postgres_url(self._config, SecureSecretStore())
        except Exception as exc:
            logger.error("[CANDLE_INTEL] PostgreSQL DSN resolution failed: %s", exc)
            return ""


class _PooledWriteConnection:
    """Writer facade over the fabric's pooled PostgreSQL write backend.

    The store's worker loop calls ``self._conn`` for the SQLite WAL PRAGMAs and
    the batch flush. Under PostgreSQL neither applies (the pool configures each
    connection; batches go through ``execute_batch``), so this object exists to
    keep the worker's connection slot populated and to no-op its SQLite-shaped
    uses (``PRAGMA``/``commit``/``close``) instead of raising.
    """

    def __init__(self, store: CandleIntelStore) -> None:
        self._store = store

    def execute(self, sql: str, args: Any = ()) -> Any:  # pragma: no cover - unused on PG
        backend = self._store._resolve_write_backend()
        if backend is None:
            raise RuntimeError("candle_intel domain is not provisioned for PostgreSQL")
        backend.execute(sql, tuple(args) if args else ())

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None  # the pool owns the lifecycle


class _PooledReadConnection:
    """Reader facade over the fabric's pooled, read-only PostgreSQL backend.

    ``query_recent``'s DB fallback uses this only when the ring buffer is
    empty (cold start / deep history); the hot path never reaches it.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def query(self, sql: str, args: Any = ()) -> list[dict[str, Any]]:
        return self._backend.query(sql, tuple(args) if args else ())

    def close(self) -> None:
        return None  # the pool owns the lifecycle


class _UnusableReadConnection:
    """Reads against an unprovisioned PostgreSQL domain return no rows.

    The domain is resolved PostgreSQL but has no pooled read backend (not yet
    provisioned). Failing loudly here would break the engine's read facade on a
    misconfigured box; returning an empty result keeps the documented
    degradation observable (the resolver already logged the cause) — the same
    contract the audit domain's read guard has.
    """

    def __init__(self, config: Any) -> None:
        self._config = config

    def query(self, sql: str, args: Any = ()) -> list[dict[str, Any]]:
        return []

    def execute(self, sql: str, args: Any = ()) -> Any:
        raise RuntimeError("candle_intel PostgreSQL read backend is not provisioned")

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Wire the record methods (store_writes) onto the class so the engine's
# `self.store.record_*` calls resolve. Imported lazily to avoid a cycle:
# store_writes imports from store (types/helpers).
# ---------------------------------------------------------------------------
def _attach_writes() -> None:
    from nexus_scalp.candle_intelligence import store_writes as _w

    for _name in (
        "record_candle",
        "record_candle_closure",
        "record_patterns",
        "record_regime",
        "record_risk",
        "record_decision",
        "record_veto",
        "record_audit_log",
    ):
        if not hasattr(CandleIntelStore, _name):
            setattr(CandleIntelStore, _name, getattr(_w, _name))


_attach_writes()
