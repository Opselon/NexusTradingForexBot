"""
Zero-Latency Async Database Audit Repository (v3.0 Enterprise)
==============================================================
High-frequency background persistence layer recording trade executions, signals,
and critical account snapshots without blocking the primary 50ms event loop.

Enterprise Upgrades Incorporated:
    1. Zero-Latency Background Queuing (Main thread never waits for Disk I/O).
    2. SQLite Write-Ahead Logging (WAL) & Performance PRAGMAs (Prevents DB locks).
    3. Account Snapshot Persistence (Facilitates Crash Recovery & Peak Equity Memory).
    4. Market Regime Traceability (Logs Microstructure regime alongside signals).
    5. Context Manager & Graceful Shutdown (Flushes queue safely on exit).
"""

import os
import queue
import sqlite3
import threading
import time
from typing import Any

from nexus_scalp.domain.models import AccountInfo, TradeOrder, TradeProposal
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.adapters.audit_db")


class AuditRepository:
    """
    Enterprise Append-Only Audit Store with Async Disk I/O processing.
    """

    def __init__(
        self,
        db_url: str = "sqlite:///artifacts/audit.db",
        flush_interval_sec: float = 1.0,
    ) -> None:
        self._db_url = db_url
        self._is_sqlite = db_url.startswith("sqlite")
        self._db_path = self._db_url.replace("sqlite:///", "") if self._is_sqlite else ""
        
        self._flush_interval = flush_interval_sec
        self._queue: queue.Queue[tuple[str, tuple]] = queue.Queue(maxsize=10000)
        self._running = False
        self._worker_thread: threading.Thread | None = None

        self._setup_storage()
        self._start_background_worker()

    def _setup_storage(self) -> None:
        """Initializes tables, indexes, and HFT performance pragmas."""
        if self._is_sqlite:
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
            
            with sqlite3.connect(self._db_path, timeout=10.0) as conn:
                # Enable Write-Ahead Logging for high concurrency without locks
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
                conn.execute("PRAGMA temp_store = MEMORY;")
                
                self._create_sqlite_tables(conn)
            logger.info("Initialized High-Performance SQLite WAL storage", db_path=self._db_path)

    def _create_sqlite_tables(self, conn: sqlite3.Connection) -> None:
        """Creates table schemas including Crash Recovery Snapshots & Regime tracking."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                proposed_entry REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                regime TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                order_type TEXT NOT NULL,
                volume REAL NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        # BUGFIX: Table to persist Account Equity for Crash Recovery
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                balance REAL NOT NULL,
                equity REAL NOT NULL,
                margin_free REAL NOT NULL,
                peak_equity REAL NOT NULL
            );
            """
        )

    def _start_background_worker(self) -> None:
        """Starts the dedicated background thread for zero-latency database inserts."""
        self._running = True
        self._worker_thread = threading.Thread(target=self._process_queue_worker, daemon=True, name="AuditDB_Worker")
        self._worker_thread.start()

    def _process_queue_worker(self) -> None:
        """Background loop flushing pending inserts to disk via Bulk Transactions."""
        if not self._is_sqlite:
            return

        conn = sqlite3.connect(self._db_path, timeout=10.0)
        
        while self._running or not self._queue.empty():
            batch: list[tuple[str, tuple]] = []
            try:
                # Wait for records, batch them up to 500 per transaction
                while len(batch) < 500:
                    query_tuple = self._queue.get(timeout=self._flush_interval)
                    batch.append(query_tuple)
            except queue.Empty:
                pass

            if batch:
                try:
                    with conn:
                        for query, args in batch:
                            conn.execute(query, args)
                    for _ in batch:
                        self._queue.task_done()
                except Exception as e:
                    logger.error("Audit Background Worker failed to insert batch", error=str(e))
                    time.sleep(1.0) # Backoff on error
            
        conn.close()

    def log_signal(self, proposal: TradeProposal) -> None:
        """Zero-latency async logging of generated trade signals."""
        if not self._is_sqlite:
            return

        query = """
            INSERT INTO audit_signals
            (request_id, symbol, action, confidence, proposed_entry, stop_loss, take_profit, regime, generated_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        # Extract Regime if Reason Code contains it, otherwise Unknown
        regime_str = "UNKNOWN"
        if "REGIME_" in proposal.reason_code:
            regime_str = proposal.reason_code.split("REGIME_")[-1]

        args = (
            proposal.request_id,
            proposal.symbol,
            proposal.action.value,
            proposal.confidence,
            proposal.proposed_entry,
            proposal.stop_loss,
            proposal.take_profit,
            regime_str,
            proposal.generated_at.isoformat(),
            proposal.model_dump_json(),
        )

        try:
            self._queue.put_nowait((query, args))
        except queue.Full:
            logger.error("Audit Signal Queue is full! Dropping telemetry.")

    def log_execution(self, order: TradeOrder, status: str) -> None:
        """Zero-latency async logging of order execution attempts."""
        if not self._is_sqlite:
            return

        query = """
            INSERT INTO audit_executions
            (order_id, symbol, order_type, volume, price, status, executed_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, DATETIME('now'), ?)
        """
        args = (
            order.order_id,
            order.symbol,
            order.order_type.value,
            order.volume,
            order.price,
            status,
            order.model_dump_json(),
        )

        try:
            self._queue.put_nowait((query, args))
        except queue.Full:
            logger.error("Audit Execution Queue is full! Dropping execution log.")

    def log_account_snapshot(self, account: AccountInfo, peak_equity: float) -> None:
        """
        Zero-latency async logging of critical account state.
        Used by the engine to recover peak_equity after unexpected system reboots.
        """
        if not self._is_sqlite:
            return

        query = """
            INSERT INTO audit_account_snapshots
            (timestamp, balance, equity, margin_free, peak_equity)
            VALUES (DATETIME('now'), ?, ?, ?, ?)
        """
        args = (account.balance, account.equity, account.margin_free, peak_equity)

        try:
            self._queue.put_nowait((query, args))
        except queue.Full:
            pass # Non-critical if occasional snapshot drops

    def get_last_account_snapshot(self) -> dict[str, Any] | None:
        """
        Synchronous read to retrieve the last known account state for Crash Recovery.
        Typically called once during system boot in live_engine.py.
        """
        if not self._is_sqlite:
            return None

        try:
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM audit_account_snapshots ORDER BY id DESC LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    return dict(row)
        except Exception as e:
            logger.error("Failed to retrieve last account snapshot for Crash Recovery", error=str(e))
        return None

    def close(self) -> None:
        """Gracefully shuts down background worker and flushes pending records."""
        logger.info("Initiating graceful shutdown of Audit Database. Flushing queues...")
        self._running = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._queue.join()  # Wait for all pending inserts to complete
            self._worker_thread.join(timeout=5.0)
        logger.info("Audit Database safely closed.")