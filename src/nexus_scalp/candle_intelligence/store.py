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

import json
import math
import os
import queue
import sqlite3
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.drivers.proxy import PortableConnection
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
        elif not self.config.db_path:
            self._config = load_database_config("candle_intel")
            self.config.db_path = self._config.sqlite_connect_path
        else:
            self._config = DatabaseConfig.for_sqlite("candle_intel", path=self.config.db_path)
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
            db_path=self._db_path,
        )

    def _connect_reader(self) -> Any:
        """Portable reader connection (SQLite native; PostgreSQL proxied)."""
        if self._config.is_sqlite:
            conn = self._driver.connect(timeout=15.0)
            conn.row_factory = sqlite3.Row
            return conn
        return PortableConnection(self._driver, timeout=15.0)

    def _connect_writer(self) -> Any:
        """Portable writer connection (worker thread)."""
        if self._config.is_sqlite:
            return self._driver.connect(timeout=15.0)
        return PortableConnection(self._driver, timeout=15.0)

    def _init_schema(self) -> None:
        """Schema bootstrap on the reader connection (safe; worker uses same DB
        file, WAL allows concurrent access)."""
        # Self-heal corrupted DB (candle_intel.db with bare candles(id) table) — see commit fde756b fix
        try:
            import sqlite3 as _sqlite3  # noqa: F401

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
        except Exception:
            pass
        with self._reader_conn:
            if self._config.is_sqlite:
                self._reader_conn.execute("PRAGMA journal_mode = WAL;")
                self._reader_conn.execute("PRAGMA synchronous = NORMAL;")
            for sql in _SCHEMAS.values():
                self._reader_conn.execute(sql)
            for table in TABLES:
                self._reader_conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_ts ON {table}(ts);"
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
        flush_interval = 0.3  # seconds; bounded latency for readers <-> DB
        while not self._stop.is_set() or not self._write_queue.empty():
            try:
                item = self._write_queue.get(timeout=flush_interval)
            except queue.Empty:
                item = None
            if item is not None:
                batch.append(item)
                if len(batch) >= self.config.max_batch_size:
                    self._flush_batch(batch)
                    batch = []
            elif batch:
                self._flush_batch(batch)
                batch = []
        if batch:
            self._flush_batch(batch)
        try:
            if self._conn:
                self._conn.close()
        except Exception:
            pass

    def _flush_batch(self, batch: list[tuple[str, list[str], list[Any]]]) -> None:
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
        try:
            rec = dict(zip(cols, values, strict=False))
            ring = self._rings.get(table)
            if ring is not None:
                ring.append(rec)
        except Exception:
            pass
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
        try:
            self.flush(timeout=2.0)
        except Exception:
            pass
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        try:
            if self._reader_conn:
                self._reader_conn.close()
        except Exception:
            pass
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
        rows = self._reader_conn.execute(
            f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
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
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass

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
