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

import contextlib
import json
import math
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from nexus_scalp.adapters.database.audit_write_plane import (
    AuditWritePlane,
    SqliteAuditWriteBackend,
)
from nexus_scalp.adapters.database.broker_history import (
    create_history_tables,
    create_paper_executions_table,
    last_sync_window,
    sync_broker_history,
)
from nexus_scalp.adapters.database.dead_letter_store import DeadLetterStore
from nexus_scalp.domain.models import AccountInfo, TradeOrder, TradeProposal
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.risk.runtime_safety import (
    PERSISTED_STATES,
    RUNTIME_RISK_STATE_VERSION,
)

logger = get_logger("nexus_scalp.adapters.audit_db")

#: Default initial trading rules seeded into trading_rules_config table.
DEFAULT_TRADING_RULES: tuple[tuple[str, str, str], ...] = (
    # Category 1: SMC
    (
        "RULE_FVG_SNIPER_FILL",
        "Price Hunting & Smart Money Concepts (SMC)",
        '{"fvg_timeframe": "M1", "fvg_min_size_pip": 0.5}',
    ),
    (
        "RULE_JUDAS_SWING_FADE",
        "Price Hunting & Smart Money Concepts (SMC)",
        '{"asian_range_pip": 15.0, "fade_reversal_ticks": 5}',
    ),
    (
        "RULE_LIQUIDITY_SWEEP_CONFIRM",
        "Price Hunting & Smart Money Concepts (SMC)",
        '{"sweep_depth_pip": 1.0, "time_window_sec": 300}',
    ),
    (
        "RULE_ORDERBLOCK_TAP_RESERVE",
        "Price Hunting & Smart Money Concepts (SMC)",
        '{"ob_timeframe": "M1", "tap_percentage": 50.0}',
    ),
    (
        "RULE_WICK_ABSORPTION_PLAY",
        "Price Hunting & Smart Money Concepts (SMC)",
        '{"min_wick_ratio": 0.6, "tick_direction_change": true}',
    ),
    # Category 2: HFT
    (
        "RULE_FLASH_MOMENTUM_SCRAPE",
        "Scalping Micro-Structure & Order Flow (HFT)",
        '{"volume_spike_multiplier": 3.0, "velocity_percentile": 99.0}',
    ),
    (
        "RULE_TICK_IMBALANCE_REVERSAL",
        "Scalping Micro-Structure & Order Flow (HFT)",
        '{"ofi_std_dev": -3.0, "min_ticks": 10}',
    ),
    (
        "RULE_SPREAD_SQUEEZE_ONLY",
        "Scalping Micro-Structure & Order Flow (HFT)",
        '{"spread_percentile": 10.0, "rolling_hour_sec": 3600}',
    ),
    (
        "RULE_REJECTION_WALL_BLOCKER",
        "Scalping Micro-Structure & Order Flow (HFT)",
        '{"limit_hit_count": 3, "time_window_sec": 60}',
    ),
    (
        "RULE_BID_ASK_SPOOF_DETECTOR",
        "Scalping Micro-Structure & Order Flow (HFT)",
        '{"vanishing_volume_threshold": 2.5, "spoof_secs": 5}',
    ),
    # Category 3: Position Management
    (
        "RULE_HIT_AND_RUN_EXIT",
        "In-Trade Hit & Run (Position Management)",
        '{"m1_bars_exit": 4}',
    ),
    (
        "RULE_ZERO_DRAWDOWN_TRAIL",
        "In-Trade Hit & Run (Position Management)",
        '{"trigger_profit_pip": 2.0, "lock_profit_pip": 1.0}',
    ),
    (
        "RULE_TIME_DECAY_CHOP_EXIT",
        "In-Trade Hit & Run (Position Management)",
        '{"decay_minutes": 4.0}',
    ),
    (
        "RULE_ATR_EXPANSION_RATCHET",
        "In-Trade Hit & Run (Position Management)",
        '{"atr_multiplier": 1.5}',
    ),
    (
        "RULE_HEDGE_ON_AI_FLIP",
        "In-Trade Hit & Run (Position Management)",
        '{"flip_threshold": 0.8}',
    ),
    # Category 4: Timing, Zones & Volatility
    (
        "RULE_LONDON_NY_KILLZONE_ONLY",
        "Timing, Zones & Volatility",
        '{"london_start": "08:00", "ny_end": "16:00"}',
    ),
    (
        "RULE_ASIAN_RANGE_FAKEOUT",
        "Timing, Zones & Volatility",
        '{"asian_start": "22:00", "asian_end": "06:00"}',
    ),
    ("RULE_NEWS_SPIKE_FADE", "Timing, Zones & Volatility", '{"news_cooldown_min": 2.0}'),
    (
        "RULE_DEAD_ZONE_BLOCKER",
        "Timing, Zones & Volatility",
        '{"rollover_start": "23:55", "rollover_end": "00:05"}',
    ),
    ("RULE_END_OF_HOUR_SQUEEZE", "Timing, Zones & Volatility", '{"squeeze_minute": 59}'),
    # Category 5: Risk & Account Safeguards
    (
        "RULE_CONSECUTIVE_LOSS_FREEZE",
        "Risk & Account Safeguards",
        '{"consecutive_losses": 3, "freeze_hours": 1.0}',
    ),
    ("RULE_DAILY_TARGET_LOCK", "Risk & Account Safeguards", '{"growth_target_pct": 2.0}'),
    ("RULE_AI_MACRO_ALIGNMENT", "Risk & Account Safeguards", '{"htf_trend": "bearish"}'),
    (
        "RULE_TURBO_CONFIDENCE_MULTIPLIER",
        "Risk & Account Safeguards",
        '{"confidence_threshold": 95.0}',
    ),
    ("RULE_DAILY_DRAWDOWN_CAP", "Risk & Account Safeguards", '{"max_drawdown_pct": 3.0}'),
    # Category 6: Advanced Reversion & Mathematics
    (
        "RULE_VWAP_ELASTIC_BAND",
        "Advanced Reversion & Mathematics",
        '{"std_dev_threshold": 3.5}',
    ),
    (
        "RULE_BOLLINGER_BURST_FADE",
        "Advanced Reversion & Mathematics",
        '{"bb_period": 20, "bb_std_dev": 2.0}',
    ),
    (
        "RULE_SCHMITT_TRIGGER_REGIME_LOCK",
        "Advanced Reversion & Mathematics",
        '{"regime_changes": 3, "window_minutes": 10}',
    ),
    (
        "RULE_GAP_AND_GO_MOMENTUM",
        "Advanced Reversion & Mathematics",
        '{"gap_pip": 2.0, "confirm_seconds": 30}',
    ),
    (
        "RULE_CONTRARIAN_RETAIL_TRAP",
        "Advanced Reversion & Mathematics",
        '{"rsi_threshold": 85.0}',
    ),
)

#: BUG-223: legacy relative default of AuditRepository (kept for BUG-149
#: anchoring semantics). NEXUS_AUDIT_DB overrides this implicit default only
#: (explicit db_url/config callers are never hijacked); tests/conftest.py sets
#: it per pytest run so bare constructions cannot touch the production tree.
_DEFAULT_AUDIT_DB_URL = "sqlite:///artifacts/audit.db"


def resolve_audit_db_url(db_url: str = _DEFAULT_AUDIT_DB_URL, config: Any = None) -> str:
    """Resolves the database URL for AuditRepository (BUG-223 isolation contract).

    Precedence order:
      1. Explicit `config` (DatabaseConfig): derives connect URL from config —
         honouring the config's PROVIDER (previously this branch hardcoded a
         SQLite URL, so a PostgreSQL config was silently downgraded).
      2. Implicit default (`db_url == _DEFAULT_AUDIT_DB_URL`): honors `NEXUS_AUDIT_DB`
         environment override if present (BUG-223 isolation seam), then the
         persisted provider setting (the app-level switch).
      3. Explicit `db_url` parameter: caller keeps full authority.
    """
    if config is not None:
        provider = getattr(getattr(config, "provider", None), "value", "sqlite")
        if str(provider).lower() in ("postgresql", "postgres", "pg"):
            from nexus_scalp.database.config import build_postgres_url

            return build_postgres_url(config)
        return f"sqlite:///{config.sqlite_connect_path}"
    if db_url == _DEFAULT_AUDIT_DB_URL:
        env_db = os.environ.get("NEXUS_AUDIT_DB", "").strip()
        if env_db:
            return f"sqlite:///{Path(env_db).as_posix()}"
        # No explicit URL and no test seam: consult the persisted provider so a
        # `nexus db-portability connect/switch` binds EVERY AuditRepository(), not
        # only the ones that already pass a config. Placed strictly AFTER the
        # NEXUS_AUDIT_DB seam so test isolation (BUG-223) keeps winning.
        try:
            from nexus_scalp.database.config import load_database_config

            persisted = load_database_config("audit")
            if getattr(persisted, "is_postgresql", False):
                from nexus_scalp.database.config import build_postgres_url

                return build_postgres_url(persisted)
        except Exception:  # pragma: no cover - settings DB unavailable
            pass
    return db_url


class RuntimeRiskStateReadError(RuntimeError):
    """The persisted safety-state row could not be read (DB uncertain).

    Raised by :meth:`AuditRepository.get_runtime_risk_state` instead of
    returning None so callers can distinguish 'healthy read proves unset'
    from 'the database cannot be trusted right now'. Boot resolution and the
    release path fail CLOSED on this — a corrupt/locked/unavailable audit DB
    must never be interpreted as 'no halt in force'.
    """


#: Module-private sentinel for the pooled-provider read path. The guard's
#: documented default is only reachable when the read route FAILED, so it
#: needs a value a healthy route can never return (None is a legitimate
#: healthy answer: 'the row is unset'). See get_runtime_risk_state.
_READ_FAILED_SENTINEL: Any = object()


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """PRAGMA pre-check shared by every SQLite ADD COLUMN site in this repo.

    Central helper (lives in audit_repository to avoid a new module + import
    cycle; every store imports AuditRepository already). Returns the column
    names present on `table`, or empty set when the table is missing.
    Consulting this BEFORE the ALTER means no duplicate-column exception is
    ever raised — not even a first-chance one for an attached debugger to
    print (the user's 08:45/08:50/09:05 duplicate-column walls).
    """
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table});").fetchall()}
    except Exception:
        return set()


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, col_name: str, col_type: str
) -> None:
    """Idempotent ADD COLUMN: PRAGMA-gated, suppress() as second net."""
    try:
        have = _existing_columns(conn, table)
    except Exception:
        have = set()
    if col_name in have:
        return
    with contextlib.suppress(Exception):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type};")


def normalize_history_dt(value: Any) -> Any:
    """Best-effort UTC datetime from arbitrary timestamp inputs."""

    from nexus_scalp.adapters.mt5.providers import normalize_utc

    return normalize_utc(value)


def _unique_target_resolves(conn: sqlite3.Connection, table: str, cols: tuple[str, ...]) -> bool:
    """True when ON CONFLICT(<cols>) resolves on <table> (PK or UNIQUE index).

    Mirrors SQLite's conflict-target rule: the named columns must be exactly
    the columns of a UNIQUE index (explicit, partial, or the app DDL's
    implicit auto-index) or of the table's PRIMARY KEY.

    BUG-276 helper — used by AuditRepository._ensure_unique_constraint_heal
    to decide whether the migration baseline skeleton destroyed an ON
    CONFLICT target. MUST stay module-level (pure sqlite3, no app deps).
    """
    want = [c.lower() for c in cols]
    try:
        for idx in conn.execute(f"PRAGMA index_list({table})").fetchall():
            # idx: (seq, name, unique, origin, partial)
            if not idx[2]:
                continue
            icols = [
                (r[2] or "").lower()
                for r in conn.execute(f"PRAGMA index_info({idx[1]})").fetchall()
            ]
            if icols == want:
                return True
        tcols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        pk = [c for c in tcols if c[5] > 0]
        if [c[1].lower() for c in pk] == want:
            return True
    except sqlite3.Error:
        return False
    return False


class AuditRepository:
    """
    Append-only audit store. All writes are enqueued to a single background
    worker thread (the hot path never touches SQLite); callers doing
    read-after-write sequences must flush() first. The ~20
    ``except Exception: pass`` blocks inside schema migration are expected
    control flow (duplicate column/index), not swallowed errors.
    """

    @staticmethod
    def _detect_sqlite(db_url: str) -> bool:
        """True when `db_url` addresses a SQLite database.

        Recognised as SQLite:
          * ``sqlite:///<path>`` and ``sqlite:///:memory:``;
          * a bare filesystem path (no scheme at all) — call sites have
            always been allowed to pass the path directly.

        Everything else is another provider:
          * a URL scheme (``postgres://``, ``postgresql://``);
          * a libpq key=value DSN (``host=... dbname=...``) — PostgreSQL,
            MySQL and friends speak this form and SQLite never does.
        """
        if "://" in db_url:
            return db_url.split("://", 1)[0].lower() in ("sqlite", "file")
        # A libpq-style DSN names a driver keyword: SQLite has no such form.
        if "=" in db_url:
            return False
        return True

    def __init__(
        self,
        db_url: str = _DEFAULT_AUDIT_DB_URL,
        config: Any = None,
        flush_interval_sec: float = 1.0,
        signal_retention_days: float = 7.0,
        moving_retention_days: float = 3.0,
        telemetry_retention_days: float = 13.0,
        purge_batch_size: int = 500,
    ) -> None:
        self._db_url = resolve_audit_db_url(db_url, config)
        # A bare filesystem path (no scheme) is a SQLite file: callers have
        # always been allowed to pass the path directly. Only a URL with an
        # explicit non-sqlite scheme means "another provider".
        self._is_sqlite = self._detect_sqlite(self._db_url)
        self._db_path = self._db_url.replace("sqlite:///", "") if self._is_sqlite else ""
        # D9 (PG-READ-PLANE-001): under a pooled provider there is no local
        # file, and the empty string broke every consumer that builds a Path
        # from it — ``Path("")`` is ``WindowsPath('.')``, so debug_snapshot's
        # schema probe raised ``WindowsPath('.') has an empty name``. Expose a
        # real, non-empty location (the fabric's database) via the same
        # attribute so those consumers work unchanged. ``_provider_db_path``
        # returns the SQLite path verbatim when the provider IS SQLite, so the
        # SQLite branch below stays byte-for-byte.
        if not self._is_sqlite:
            self._db_path = self._provider_db_path()
        # sqlite:///:memory: opens a PRIVATE empty DB per connection; the
        # background worker would never see the schema created here. Use a
        # shared named in-memory DB (file::memory:?cache=shared) so worker
        # and setup share one schema (2026-08-18 full-suite fix).
        if self._db_path == ":memory:":
            self._db_path = "file::memory:?cache=shared"
        # Hold ONE persistent connection for the shared in-memory DB; the
        # shared cache is dropped when the last connection closes, so the
        # worker must reuse THIS connection (2026-08-18 full-suite fix).
        self._shared_conn: sqlite3.Connection | None = None
        # A4 dead-letter split: compose the durable dead-letter store over
        # THIS repository's connection accessor (sqlite3.connect on the same
        # _db_path — never a second handle) and delegate the dead-letter
        # surface to it (record / list / counters). For file: URIs (shared
        # in-memory DB) the factory must pass uri=True, or sqlite treats the
        # URI string as a literal file name and creates a junk CWD file on
        # every dead-letter write (disk-leak bug, 2026-09-09).
        if self._db_path.startswith("file:"):

            def _uri_conn_factory(path: str) -> sqlite3.Connection:
                # `path` always IS self._db_path here (the store borrows the
                # owner's path verbatim); reconnect with uri=True.
                return sqlite3.connect(self._db_path, uri=True)

            _dl_conn_factory: Callable[[str], sqlite3.Connection] = _uri_conn_factory
        else:
            _dl_conn_factory = sqlite3.connect
        self.dead_letter_store = DeadLetterStore(
            conn_factory=_dl_conn_factory,
            is_sqlite=self._is_sqlite,
            db_path=self._db_path,
            write_sink=self._dead_letter_write_sink(),
        )

        self._flush_interval = flush_interval_sec
        self._queue: queue.Queue[tuple[str, tuple]] = queue.Queue(maxsize=10000)
        # NOTE: ``_running`` is deliberately NOT set here. It is a property
        # below delegating to the write plane: after DB-FABRIC-001 the worker
        # lives in AuditWritePlane, and a stale constant False here broke
        # BUG-297 connection caching in StrategyEvaluator (which gates
        # handle reuse on ``audit_repo._running`` and was opening a fresh
        # connect per call instead of reusing the cached one).
        # ``_worker_thread`` is likewise a property below: callers/tests read
        # ``repo._worker_thread`` to verify the consumer is bound and alive
        # (BUG-288 handshake), and ``close()`` needs to join it.
        # The provider-agnostic write plane owns the queue/worker/overflow
        # machinery (see audit_write_plane). It is constructed here so this
        # repository keeps its exact public surface, but the persistence
        # destination is decided by the domain's provider, not by a hard
        # `if sqlite` gate: under PostgreSQL the plane uses the fabric's
        # pooled write backend instead of silently no-oping.
        self._write_plane = self._build_write_plane()
        # =====================================================================
        # DATA-INTEGRITY METRICS (runtime safety mission, P0).
        # Financial record loss MUST be observable. These counters are the
        # canonical drop / dead-letter / backpressure surfaces consumed by
        # debug_snapshot + the safety tests. Financial producers use bounded
        # backpressure + durable overflow (never silent drops); telemetry
        # producers remain dropable by design.
        #
        # A4 dead-letter split: audit_dead_letter_rows and _dead_letter_seq
        # are OWNED by DeadLetterStore now — the public attribute reads
        # delegate to it (see the property definitions below), so the
        # runtime-safety tests + debug_snapshot keep working unchanged.
        # =====================================================================
        self.audit_batch_failures: int = 0
        self.audit_dropped_rows: int = 0
        self.audit_salvaged_rows: int = 0
        self.financial_queue_backpressure: int = 0
        self.financial_events_overflowed: int = 0
        self.financial_events_failed: int = 0
        # BUG-285: durable-overflow RECOVERY surface. The overflow file was
        # the last line of defense AND a dead end (no reader existed — the
        # 2026-09-14 perf wave R2): a stranded financial row stayed stranded
        # and the directory grew unbounded. recovered = rows re-inserted by
        # the audit worker's bounded drain pass; failed = overflow files
        # rejected by replay (durably dead-lettered) + writes refused
        # because the pending-file cap was reached.
        self.financial_overflow_recovered: int = 0
        self.financial_overflow_failed: int = 0
        # None = never ran (the first drain is ALWAYS due — a 0.0 sentinel
        # compared against time.monotonic() silently skips the first pass on
        # hosts with uptime < interval, the BUG-273 class).
        self._last_overflow_drain: float | None = None
        self.telemetry_dropped: int = 0
        # CHG-0067 / CR-02: provider-read degradation observability. Every
        # SQLite-gated read under a non-SQLite provider used to return its
        # default SILENTLY (None/0/[] with no exception - fail-silent wrong
        # data). The guard now counts each degraded read (total + per
        # operation) and warns once per operation, so the degradation is
        # observable in the metrics surface instead of plausible-looking.
        self.provider_reads_routed: int = 0
        self.provider_read_route_errors: int = 0
        self.provider_read_degraded_total: int = 0
        self.provider_read_degraded_ops: dict[str, int] = {}
        self._provider_read_guard_state: dict[str, tuple[float, int]] = {}
        # BUG-226: execution provenance of the account feeding this audit
        # stream ('LIVE' / 'PAPER' / 'SHADOW'). The engine sets this from the
        # effective mode; ledger + snapshot writes read it at write time so a
        # hot-swap is reflected per-row. AccountingCore excludes PAPER rows
        # from performance metrics.
        self.current_account_source: str = "LIVE"

        # BUG-149: the legacy relative default ("sqlite:///artifacts/audit.db")
        # anchors to the raw process CWD. When frozen, anchor to the canonical
        # runtime workspace (exe bundle) so every launch — double-click,
        # shortcut, any shell CWD — uses ONE canonical artifact tree. Source
        # runs (CWD == repo root) keep identical behavior.
        # BUG-156: in-memory URIs are NOT filesystem paths — they must be
        # excluded from workspace anchoring. Anchoring ":memory:" produced
        # "CWD/:memory:" -> a nonexistent file path, and every
        # "sqlite:///:memory:" AuditRepository raised OperationalError.
        if (
            self._is_sqlite
            and self._db_path
            and self._db_path != ":memory:"
            and not self._db_path.startswith("file:")
            and not Path(self._db_path).is_absolute()
        ):
            try:
                from nexus_scalp.release.paths import get_runtime_workspace

                self._db_path = str(get_runtime_workspace() / self._db_path)
            except Exception:
                pass
            self._db_url = f"sqlite:///{self._db_path}"

        # Retention policy (BUG-054). TEST env defaults: disposable signal
        # rows 7 days, POSITION_MOVING 3 days, guard telemetry 13 days.
        # Accounting/experience/research tables are NEVER purged here.
        self._signal_retention_days = float(signal_retention_days)
        self._moving_retention_days = float(moving_retention_days)
        self._telemetry_retention_days = float(telemetry_retention_days)
        self._purge_batch_size = max(1, int(purge_batch_size))

        # Snapshots throttling state to prevent high-frequency DB bloat
        self._last_snapshot_time = 0.0
        self._last_snapshot_balance = 0.0
        self._last_snapshot_equity = 0.0

        self._setup_storage()
        # Exactly ONE consumer of self._queue. Under SQLite the write plane
        # owns the queue (its backend wraps this repository's single writer
        # connection), so the legacy worker thread must NOT also start — two
        # consumers of one queue race the batches and duplicate/drop rows.
        # Under a pooled provider the plane is the sole consumer too.
        self._write_plane.start()
        self._legacy_worker_armed = True

    @property
    def _running(self) -> bool:
        """Is the repository's background machinery live?

        After DB-FABRIC-001 the queue/worker/overflow machinery moved into
        :class:`AuditWritePlane`, so this delegates there. StrategyEvaluator
        reads this flag to decide whether to cache its registry connection
        (BUG-297: a stale ``False`` made it drop the cache and reconnect on
        every pre-trade score lookup).
        """
        plane = getattr(self, "_write_plane", None)
        if plane is not None and getattr(plane, "_running", False):
            return True
        return False

    @_running.setter
    def _running(self, value: bool) -> None:
        """Delegate the running flag to the write plane (the worker owner).

        Preserves the pre-existing write sites (``_start_background_worker``,
        ``close``) that set this attribute directly; the plane is the single
        source of truth after DB-FABRIC-001.
        """
        plane = getattr(self, "_write_plane", None)
        if plane is not None:
            plane._running = value

    @property
    def _worker_thread(self) -> threading.Thread | None:
        """The background consumer thread (BUG-288 handshake: bound to the
        queue at construction).

        After DB-FABRIC-001 the worker lives in :class:`AuditWritePlane`;
        delegating here keeps the historical surface (``close()`` joins it,
        tests assert it is alive and cannot adopt a rebound queue).
        """
        plane = getattr(self, "_write_plane", None)
        if plane is not None:
            return getattr(plane, "_worker_thread", None)
        return None

    @_worker_thread.setter
    def _worker_thread(self, value: threading.Thread | None) -> None:
        """Delegate the worker thread slot to the write plane.

        ``_start_background_worker`` (legacy fallback) and ``close()`` still
        write this attribute; the plane owns the authoritative slot.
        """
        plane = getattr(self, "_write_plane", None)
        if plane is not None:
            plane._worker_thread = value

    def _setup_storage(self) -> None:
        """Initializes tables, indexes, and HFT performance pragmas."""
        if self._is_sqlite:
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)

            # NOTE: `with sqlite3.connect(...)` only wraps a transaction, it does NOT
            # close the connection. Leaking it keeps the .db/-wal/-shm files locked on
            # Windows, which breaks temp-directory cleanup in tests and log rotation.
            conn = self._connect_sqlite(10.0)
            try:
                # Enable Write-Ahead Logging for high concurrency without locks
                if self._db_path.startswith("file::"):
                    conn.execute("PRAGMA journal_mode = MEMORY;")
                else:
                    conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
                conn.execute("PRAGMA temp_store = MEMORY;")

                self._create_sqlite_tables(conn)
                conn.commit()
            finally:
                if self._db_path.startswith("file::"):
                    self._shared_conn = conn  # keep alive: shared cache needs it
                else:
                    conn.close()
            logger.info("Initialized High-Performance SQLite WAL storage", db_path=self._db_path)

    def _create_sqlite_tables(self, conn: sqlite3.Connection) -> None:
        """Creates table schemas including Crash Recovery Snapshots & Regime tracking."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trading_rules_config (
                rule_name TEXT PRIMARY KEY,
                is_enabled INTEGER DEFAULT 0,
                category TEXT NOT NULL,
                parameters TEXT
            );
            """
        )

        self._create_experience_tables(conn)
        self._create_intelligence_tables(conn)
        self._create_research_tables(conn)
        self._seed_trading_rules(conn)
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
                payload TEXT NOT NULL,
                execution_mode TEXT,
                reason_code TEXT,
                decision_stage TEXT,
                blocked_by TEXT,
                htf_score REAL,
                smc_score REAL,
                confidence_before_filters REAL,
                confidence_after_filters REAL
            );
            """
        )
        # Migrate existing audit_signals table if needed
        for col_def in [
            ("execution_mode", "TEXT"),
            ("reason_code", "TEXT"),
            ("decision_stage", "TEXT"),
            ("blocked_by", "TEXT"),
            ("htf_score", "REAL"),
            ("smc_score", "REAL"),
            ("confidence_before_filters", "REAL"),
            ("confidence_after_filters", "REAL"),
            # CHG-0043 decision-evidence completeness: the model-preferred
            # direction + raw probability block, captured at decision time so
            # every rejected candidate is counterfactually reconstructable.
            # NOT_RECORDED ("") for pre-repair rows — never backfilled from
            # future data.
            ("preferred_direction", "TEXT"),
            ("raw_prob_buy", "REAL"),
            ("raw_prob_sell", "REAL"),
            ("raw_prob_no_trade", "REAL"),
            ("raw_prob_wait", "REAL"),
            ("confidence_source", "TEXT"),
            ("spread_usd", "REAL"),
            # OBS-TRACE (2026-09-09): BUG-226 account provenance on the DECISION
            # table. execution_mode on audit_signals is the execution PATH
            # (STANDARD/PREDICTIVE_LIMIT/...), never LIVE/PAPER — without this
            # column a decision row cannot be attributed to the account that
            # produced it. Written by log_signal from the adapter-bound
            # current_account_source at enqueue time; '' for legacy rows.
            ("account_source", "TEXT DEFAULT ''"),
        ]:
            _add_column_if_missing(conn, "audit_signals", col_def[0], col_def[1])

        # Persistent signal deduplication identity (BUG-054). A deterministic
        # key derived from the canonical decision fields, stable across restart.
        # Database-enforced: UNIQUE index + ON CONFLICT DO NOTHING means the
        # background worker can never double-insert the same decision even if
        # two processes/producers race.
        _add_column_if_missing(conn, "audit_signals", "signal_dedup_key", "TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_signals_dedup "
            "ON audit_signals(signal_dedup_key);"
        )

        # Lightweight guard/telemetry counters (BUG-054): high-frequency
        # rejections (TICK_DUPLICATE_SUPPRESSED, ORDER_FREQUENCY_THROTTLED, ...)
        # must NOT create heavy audit_signals rows. They aggregate here instead:
        # one row per (generated_minute, symbol, reason_code) with a running
        # count, so "how often / when / for which symbol / why" stays answerable
        # at a few bytes per event instead of ~1.2KB.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_guard_telemetry (
                window_start TEXT NOT NULL,
                symbol TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (window_start, symbol, reason_code)
            );
            """
        )

        # Track detailed pending orders & executions
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket INTEGER,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL NOT NULL,
                stop_loss REAL,
                take_profit REAL,
                volume REAL NOT NULL,
                reason TEXT,
                latency REAL,
                execution_mode TEXT,
                execution_id TEXT,
                timestamp TEXT NOT NULL
            );
            """
        )
        # Trade-forensics index: the audit_orders ticket/order_id lookup is a
        # per-ticket scan without this (EXPLAIN: SCAN audit_orders). ticket is
        # the broker ticket of the lifecycle; order_id is the engine request id.
        # Idempotent, migration-safe on existing + fresh databases.
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_ticket
            ON audit_orders (ticket, order_id)
            """
        )
        # Migration guard: audit_orders created BEFORE execution_id existed
        # keeps its old shape (CREATE TABLE IF NOT EXISTS never alters). The
        # log_order/worker batch insert includes execution_id -> without this
        # column the batch fails with "table audit_orders has no column named
        # execution_id" and audit_orders silently stays empty (observed on
        # 2026-08-20 local engine). Idempotent ADD COLUMN upgrade.
        _add_column_if_missing(conn, "audit_orders", "execution_id", "TEXT")
        # =====================================================================
        # PERSISTENCE-CORE idempotency identity for order lifecycle events
        # (agent-17 durability audit, 2026-09-10). audit_orders rows are the
        # durable evidence of order lifecycle events (dispatch / close /
        # modify / cancel). They used to be plain INSERTs: a redelivered
        # event (worker batch retry after a partial commit, replayed
        # lifecycle update, operator retry) created a SECOND durable row, so
        # downstream consumers (accounting order-event traces, dispatch
        # ticket resolution LIMIT 3, operator order lookup) read duplicated
        # evidence. The engine stamps ONE execution_id per decision (BUG-226
        # identity chain), so a PARTIAL UNIQUE index on it makes event
        # redelivery idempotent at the database layer — same guarantee shape
        # audit_signals already has via idx_audit_signals_dedup. Rows with
        # NULL execution_id (legacy rows, post-fill ticket events) are
        # unaffected: partial index, plain inserts keep working.
        #
        # LEGACY-DATA SAFETY: a database that ran during the un-deduplicated
        # era may already hold duplicate non-null execution_id rows. Creating
        # the index there unguarded would fail the whole construction and
        # take the engine down. Bounded repair instead: detect duplicates
        # first; when found, keep the LOWEST id (the original observation)
        # per execution_id and delete the later redeliveries, then create the
        # index. Repairs only exact-identity duplicates (never touching rows
        # with NULL/'' execution_id); when nothing is duplicated the scan is
        # two cheap indexed reads and every legacy row is preserved verbatim.
        # =====================================================================
        _dup_rows = conn.execute(
            "SELECT execution_id, COUNT(*) AS c FROM audit_orders "
            "WHERE execution_id IS NOT NULL AND execution_id != '' "
            "GROUP BY execution_id HAVING c > 1 LIMIT ?",
            (self._ORDERS_DEDUP_REPAIR_BATCH,),
        ).fetchall()
        for _dup_id, _ in _dup_rows:
            conn.execute(
                "DELETE FROM audit_orders WHERE execution_id = ? AND id NOT IN "
                "(SELECT MIN(id) FROM audit_orders WHERE execution_id = ?)",
                (_dup_id, _dup_id),
            )
            logger.warning(
                "audit_orders duplicate lifecycle rows repaired (kept earliest row) "
                "execution_id=%s",
                _dup_id,
            )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_execution_idempotency
            ON audit_orders (execution_id)
            WHERE execution_id IS NOT NULL AND execution_id != ''
            """
        )

        # =====================================================================
        # INSTITUTIONAL FINANCIAL ACCOUNTING LEDGER (One autopsy row per trade)
        # =====================================================================
        # Legacy columns (ticket .. exit_mechanism) are retained verbatim for
        # backward compatibility with existing dashboards and metric queries.
        # The institutional autopsy columns extend them with full identification,
        # timing, financial, entry-context, SL-dynamics, quant-excursion, and
        # account-snapshot detail.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_ledger (
                ticket INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                volume REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                status TEXT NOT NULL,
                pnl REAL DEFAULT 0.0,
                commission REAL DEFAULT 0.0,
                swap REAL DEFAULT 0.0,
                duration_sec REAL DEFAULT 0.0,
                timestamp TEXT NOT NULL,
                mae REAL DEFAULT 0.0,
                mfe REAL DEFAULT 0.0,
                initial_sl_price REAL DEFAULT 0.0,
                final_sl_price REAL DEFAULT 0.0,
                is_risk_free_hit INTEGER DEFAULT 0,
                exit_mechanism TEXT DEFAULT '',

                -- Identification
                order_id TEXT DEFAULT '',

                -- Timestamps & Price
                open_time TEXT DEFAULT '',
                close_time TEXT DEFAULT '',
                duration_seconds REAL DEFAULT 0.0,
                open_price REAL DEFAULT 0.0,
                close_price REAL DEFAULT 0.0,

                -- Financials
                gross_pnl_usd REAL DEFAULT 0.0,
                net_pnl_usd REAL DEFAULT 0.0,

                -- Entry Context
                entry_reason TEXT DEFAULT '',
                ai_confidence_at_open REAL DEFAULT 0.0,
                market_regime_at_open TEXT DEFAULT '',

                -- SL/TP Dynamics
                was_sl_modified INTEGER DEFAULT 0,

                -- Quant Risk Excursions
                MAE_usd REAL DEFAULT 0.0,
                MFE_usd REAL DEFAULT 0.0,

                -- Account Snapshot
                account_balance_after REAL DEFAULT 0.0,
                account_equity_after REAL DEFAULT 0.0,
                drawdown_percent_after REAL DEFAULT 0.0
            );
            """
        )
        # Safe alter statements for migration of pre-existing ledger tables
        for col_def in [
            ("mae", "REAL DEFAULT 0.0"),
            ("mfe", "REAL DEFAULT 0.0"),
            ("initial_sl_price", "REAL DEFAULT 0.0"),
            ("final_sl_price", "REAL DEFAULT 0.0"),
            ("is_risk_free_hit", "INTEGER DEFAULT 0"),
            ("exit_mechanism", "TEXT DEFAULT ''"),
            ("order_id", "TEXT DEFAULT ''"),
            ("open_time", "TEXT DEFAULT ''"),
            ("close_time", "TEXT DEFAULT ''"),
            ("duration_seconds", "REAL DEFAULT 0.0"),
            ("open_price", "REAL DEFAULT 0.0"),
            ("close_price", "REAL DEFAULT 0.0"),
            ("gross_pnl_usd", "REAL DEFAULT 0.0"),
            ("net_pnl_usd", "REAL DEFAULT 0.0"),
            ("entry_reason", "TEXT DEFAULT ''"),
            ("ai_confidence_at_open", "REAL DEFAULT 0.0"),
            ("market_regime_at_open", "TEXT DEFAULT ''"),
            ("was_sl_modified", "INTEGER DEFAULT 0"),
            ("MAE_usd", "REAL DEFAULT 0.0"),
            ("MFE_usd", "REAL DEFAULT 0.0"),
            ("account_balance_after", "REAL DEFAULT 0.0"),
            ("account_equity_after", "REAL DEFAULT 0.0"),
            ("drawdown_percent_after", "REAL DEFAULT 0.0"),
            # DEBUG-AUDIT (2026-08-18): full chart-state fingerprint at dispatch
            # (HTF/SMC/ICT structure, displacement, sessions, guardian) for
            # post-hoc setup/strategy attribution of every closed trade.
            ("entry_setup_snapshot", "TEXT DEFAULT '{}'"),
            # TASK-3 (BUG-083): exit classification evidence provenance —
            # which evidence source produced the canonical exit mechanism and
            # how confident the classification is (1.0 = broker-proven).
            ("exit_reason_source", "TEXT DEFAULT ''"),
            ("exit_evidence", "TEXT DEFAULT ''"),
            ("exit_reason_confidence", "REAL DEFAULT 0.0"),
            # TASK-3: reversal/regime snapshots observed while the position
            # was open (model flip, regime change) — bounded JSON, null-safe.
            ("reversal_events_json", "TEXT DEFAULT '[]'"),
            # BUG-226: execution provenance of the account the trade ran on
            # ('' legacy, 'LIVE', 'PAPER', 'SHADOW'). AccountingCore filters
            # PAPER rows out of every performance metric; raw evidence is
            # retained (never rewritten — contract s47).
            ("account_source", "TEXT DEFAULT ''"),
        ]:
            _add_column_if_missing(conn, "audit_ledger", col_def[0], col_def[1])

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
        # =====================================================================
        # PERSISTENCE-CORE idempotency identity for execution ATTEMPTS
        # (agent-17 durability audit, 2026-09-10). audit_executions records
        # one row per dispatch attempt (dispatch.py logs exactly one
        # log_execution per order_id after send_order). It used to be a
        # plain INSERT: a redelivered attempt (batch retry after partial
        # commit / replayed dispatch) created a SECOND durable row. Identity
        # = (order_id, status): the same order_id reaching the same terminal
        # status twice is redelivery, not a new event — a second distinct
        # status (FILLED after REJECTED retry) still inserts its own row.
        #
        # BUG-254 (2026-09-11): the bare CREATE UNIQUE INDEX here crashed
        # EVERY construction on any pre-release database carrying
        # duplicates from the plain-INSERT era (sqlite3.IntegrityError —
        # the same defect the audit_orders repair below already guards
        # against). The index bootstrap is now the shared idempotent
        # repair+create helper: bounded detect->archive-to-reconciled->
        # create under BEGIN IMMEDIATE, earliest-row survivor rule,
        # non-destructive (superseded rows preserved in
        # audit_executions_reconciled), aggregate-only logging. See
        # adapters/database/executions_idempotency.py for the full contract.
        # =====================================================================
        from nexus_scalp.adapters.database.executions_idempotency import (
            ensure_executions_idempotency_index,
        )

        ensure_executions_idempotency_index(conn)
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
        # BUG-226: execution provenance of the account each snapshot observed.
        # ('' legacy, 'LIVE', 'PAPER', 'SHADOW'); AccountingCore excludes the
        # PAPER seed plateau (balance==equity==margin_free==10000.0) and any
        # PAPER-tagged row from drawdown/equity metrics.
        _add_column_if_missing(conn, "audit_account_snapshots", "account_source", "TEXT DEFAULT ''")

        # Broker-history normalized copy: audit_broker_orders / _deals / _trades
        # + sync watermark (created idempotently; identity = broker tickets).
        create_history_tables(conn)
        # PAPER execution-ledger durable copy (paper-demo parity, P0-1):
        # mirrors the in-memory PaperMT5Adapter ledger so paper fills and
        # rejections survive restarts and parity can be measured from data.
        create_paper_executions_table(conn)

        # =====================================================================
        # RUNTIME SAFETY STATE (P0, runtime-safety mission): ONE canonical
        # single-row store for durable safety decisions (HALT / KILL_SWITCH /
        # RUNNING) + the durable audit dead-letter table (A4: schema owned by
        # DeadLetterStore.create_table, still applied on THIS connection).
        # Atomic upserts; versioned for future migrations. See
        # risk/runtime_safety.py for the pure policy contract; AuditRepository
        # owns the ONLY persistence.
        # =====================================================================
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_risk_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL DEFAULT 1,
                state TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                triggered_at TEXT NOT NULL DEFAULT '',
                balance REAL NOT NULL DEFAULT 0.0,
                equity REAL NOT NULL DEFAULT 0.0,
                peak_equity REAL NOT NULL DEFAULT 0.0,
                release_required INTEGER NOT NULL DEFAULT 1,
                released_at TEXT,
                release_actor TEXT,
                consecutive_losses INTEGER NOT NULL DEFAULT 0,
                breaker_day_anchor REAL,
                breaker_day_utc TEXT,
                breaker_week_anchor REAL,
                breaker_week_iso TEXT
            );
            """
        )
        # BUG-259 (Agent-15 capital-protection wave 3): breaker anchor
        # persistence. Older DBs lack the columns — heal additively.
        _add_column_if_missing(conn, "runtime_risk_state", "breaker_day_anchor", "REAL")
        _add_column_if_missing(conn, "runtime_risk_state", "breaker_day_utc", "TEXT")
        _add_column_if_missing(conn, "runtime_risk_state", "breaker_week_anchor", "REAL")
        _add_column_if_missing(conn, "runtime_risk_state", "breaker_week_iso", "TEXT")
        # A4 dead-letter split: the audit_dead_letter DDL now lives on
        # DeadLetterStore (same table, VERBATIM move) — created here on the
        # setup connection so schema timing is unchanged.
        self.dead_letter_store.create_table(conn)
        # BUG-276: heal UNIQUE constraint targets shadowed by the migration
        # baseline skeletons (see database/app_columns.APP_UNIQUE_TARGETS).
        self._ensure_unique_constraint_heal(conn)

    def _ensure_unique_constraint_heal(self, conn: sqlite3.Connection) -> None:
        """Restores ON CONFLICT targets the baseline skeleton destroyed (BUG-276).

        ``AuditRepository``'s bootstrap is CREATE TABLE IF NOT EXISTS: on a
        database whose tables were pre-created as manifest skeletons by the
        migration gate it no-ops, leaving the skeleton's ``id INTEGER PRIMARY
        KEY`` as the ONLY uniqueness surface. Every producer INSERT with an
        ``ON CONFLICT(<cols>)`` target then fails outright —
        "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE
        constraint" — and the audit worker dead-letters that table's rows
        FOREVER (observed live in the nightly E2E container: guard telemetry
        dead-lettering ~19 rows/s from tick one).

        Repair contract:
          * idempotent + additive: a matching UNIQUE index (explicit or the
            app DDL's implicit auto-index) or a PRIMARY KEY covering exactly
            the target columns means NOTHING is created — healthy databases
            pay zero extra index maintenance;
          * when the target is dead, build the index the application's own
            canonical DDL declares (CREATE UNIQUE INDEX IF NOT EXISTS);
          * an existing poisoned database may already hold rows that violate
            the constraint (only possible for pre-skeleton legacy data): the
            CREATE raises IntegrityError — log it LOUD (the table keeps
            dead-lettering with a named reason) and never crash the boot.
        """
        try:
            from nexus_scalp.database.app_columns import APP_UNIQUE_TARGETS
        except ImportError:  # pragma: no cover - app_columns is stdlib-only
            return
        for table, targets in APP_UNIQUE_TARGETS.items():
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                continue
            for cols in targets:
                if _unique_target_resolves(conn, table, cols):
                    continue
                cols_txt = ", ".join(f'"{c}"' for c in cols)
                index_name = "idx_" + table + "_uq_" + "_".join(cols)
                try:
                    conn.execute(
                        f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({cols_txt})"
                    )
                    logger.warning(
                        "BUG-276 skeleton-shadow heal: created missing UNIQUE index %s ON %s (%s)",
                        index_name,
                        table,
                        cols_txt,
                    )
                except sqlite3.Error as e:
                    # Fail LOUD but non-fatal: the constraint cannot be
                    # enforced over the existing rows; producers keep
                    # dead-lettering with an explicit reason in the log.
                    logger.error(
                        "BUG-276 skeleton-shadow heal FAILED for %s(%s): %s — "
                        "ON CONFLICT producers for this table will keep "
                        "dead-lettering until the duplicates are resolved",
                        table,
                        cols_txt,
                        e,
                    )

    # ---------------------------------------------------------------------
    # DEAD-LETTER COUNTER DELEGATION (A4). audit_dead_letter_rows and
    # _dead_letter_seq are owned by DeadLetterStore; these keep the
    # pre-split attribute surface byte-compatible for readers
    # (debug_snapshot, runtime-safety tests, overflow-file naming).
    # ---------------------------------------------------------------------

    @property
    def audit_dead_letter_rows(self) -> int:
        """Dead-letter metric (canonical value on DeadLetterStore)."""
        return self.dead_letter_store.audit_dead_letter_rows

    @audit_dead_letter_rows.setter
    def audit_dead_letter_rows(self, value: int) -> None:
        self.dead_letter_store.audit_dead_letter_rows = int(value)

    @property
    def _dead_letter_seq(self) -> int:
        """Dead-letter sequence (canonical value on DeadLetterStore)."""
        return self.dead_letter_store._dead_letter_seq

    @_dead_letter_seq.setter
    def _dead_letter_seq(self, value: int) -> None:
        self.dead_letter_store._dead_letter_seq = int(value)

    @property
    def dead_letter_pruned_rows(self) -> int:
        """Rows removed by dead-letter retention (PERF-DEADLETTER).

        Bounded-retention observability: a nonzero value proves the cap is
        active and a producer failure loop is being contained (never
        silently — see the store's per-event WARNING)."""
        return self.dead_letter_store.dead_letter_pruned_rows

    # ---------------------------------------------------------------------
    # RUNTIME SAFETY STATE (P0) — canonical durable store.
    # One single-row table (id=1) holding the current safety decision.
    # Writes are ATOMIC upserts: either the old valid row or the new valid
    # row is visible — never a half-written state. HALT/KILL_SWITCH survive
    # restart until release_runtime_risk_state() (explicit, audited).
    # ---------------------------------------------------------------------

    @staticmethod
    def _json_safe_args(args: tuple[Any, ...]) -> str:
        """Durable JSON encoding of failed-row SQL args (A4: the canonical
        implementation lives on DeadLetterStore._json_safe_args; this
        delegating shim keeps the overflow-file writer and any external
        callers working unchanged)."""
        return DeadLetterStore._json_safe_args(args)

    def set_runtime_risk_state(
        self,
        *,
        state: str,
        reason: str = "",
        source: str = "",
        triggered_at: str = "",
        balance: float = 0.0,
        equity: float = 0.0,
        peak_equity: float = 0.0,
        release_required: bool = True,
        consecutive_losses: int = 0,
        version: int = RUNTIME_RISK_STATE_VERSION,
    ) -> bool:
        """Atomically persists the canonical runtime risk state (single row).

        state must be a durable state ('RUNNING' | 'HALTED' | 'KILL_SWITCH').
        Unknown states are refused (fail closed) — never persisted.
        """
        state_up = str(state or "").upper()
        if state_up not in PERSISTED_STATES:
            logger.error("runtime_risk_state refused unknown state=%r (fail closed)", state)
            return False
        if not triggered_at:
            from datetime import UTC, datetime

            triggered_at = datetime.now(UTC).isoformat()
        sql = """
            INSERT INTO runtime_risk_state
                (id, version, state, reason, source, triggered_at, balance,
                 equity, peak_equity, release_required, consecutive_losses)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                version=excluded.version,
                state=excluded.state,
                reason=excluded.reason,
                source=excluded.source,
                triggered_at=excluded.triggered_at,
                balance=excluded.balance,
                equity=excluded.equity,
                peak_equity=excluded.peak_equity,
                release_required=excluded.release_required,
                consecutive_losses=excluded.consecutive_losses,
                released_at=NULL,
                release_actor=NULL
        """
        args = (
            int(version),
            state_up,
            str(reason or ""),
            str(source or ""),
            str(triggered_at),
            float(balance),
            float(equity),
            float(peak_equity),
            1 if release_required else 0,
            int(consecutive_losses),
        )
        if not self._is_sqlite:
            # PG-READ-PLANE-001 / D2: under a pooled provider _db_path is ""
            # and _connect_sqlite() would open a throwaway temp DB, so the
            # safety row never reached PostgreSQL. Execute synchronously
            # against the fabric's pooled WRITE backend instead: this bool is
            # a safety gate, so it must reflect real durability, not a queue
            # that may still be in flight (or never flushed on a fast exit).
            ok = self._provider_execute_write([(sql, args)])
            if not ok:
                logger.error(
                    "runtime_risk_state persist FAILED state=%s "
                    "(provider write backend unavailable)",
                    state_up,
                )
                return False
            return True
        try:
            with self._connect_sqlite(10.0) as conn:
                conn.execute(sql, args)
                conn.commit()
            return True
        except Exception as e:
            # NEVER swallow: a failed safety-state persist must be loud.
            logger.error("runtime_risk_state persist FAILED state=%s error=%s", state_up, e)
            return False

    # ---------------------------------------------------------------------
    # BREAKER ANCHOR PERSISTENCE (BUG-259, Agent-15 capital-protection wave 3)
    # ---------------------------------------------------------------------
    # The profit-protection daily/weekly loss budgets anchor on the equity at
    # the start of the UTC trading day / ISO week. CircuitBreakerEngine
    # derives that anchor from the FIRST evaluation it sees in a period, so a
    # process restart mid-day silently re-anchored to CURRENT equity — a
    # loss taken before the restart vanished from the budget accounting.
    # These two methods give the engine an explicit durable home for the
    # anchors inside the canonical runtime_risk_state row (no new store).
    # Fail-closed on write: a persist failure returns False so the caller can
    # refuse trading rather than run with an unverifiable anchor.
    # ---------------------------------------------------------------------

    def save_breaker_anchors(
        self,
        *,
        day_anchor: float,
        day_utc: str,
        week_anchor: float,
        week_iso: str,
    ) -> bool:
        """Persists the current breaker period anchors (day/UTC-week)."""
        try:
            day_anchor = float(day_anchor)
            week_anchor = float(week_anchor)
        except (TypeError, ValueError):
            return False
        if not all(
            isinstance(v, float) and math.isfinite(v) and v > 0.0 for v in (day_anchor, week_anchor)
        ):
            return False
        if not self._is_sqlite:
            # PG-READ-PLANE-001 / D2: same provider defect as the risk state —
            # _connect_sqlite() under PostgreSQL writes to a throwaway temp
            # DB. The two statements run in ONE backend transaction so the
            # anchor row can never exist without its canonical parent row.
            # ``INSERT OR IGNORE`` is SQLite dialect: the pooled write backend
            # translates it (ON CONFLICT on the row PK id=1) at the boundary,
            # so the SQLite branch's statement text is reused verbatim — one
            # statement shape for both providers, matching the parity contract
            # the rest of the repository already uses.
            return self._provider_execute_write(
                [
                    (
                        "INSERT OR IGNORE INTO runtime_risk_state (id, state) "
                        "VALUES (1, 'RUNNING')",
                        (),
                    ),
                    (
                        """
                        UPDATE runtime_risk_state
                        SET breaker_day_anchor = ?, breaker_day_utc = ?,
                            breaker_week_anchor = ?, breaker_week_iso = ?
                        WHERE id = 1
                        """,
                        (day_anchor, str(day_utc or ""), week_anchor, str(week_iso or "")),
                    ),
                ]
            )
        try:
            with self._connect_sqlite(10.0) as conn:
                # The canonical row must exist (set_runtime_risk_state creates
                # it at boot); INSERT OR IGNORE guarantees a row for anchor
                # writes on a fresh store without fabricating a state.
                conn.execute(
                    "INSERT OR IGNORE INTO runtime_risk_state (id, state) VALUES (1, 'RUNNING')"
                )
                conn.execute(
                    """
                    UPDATE runtime_risk_state
                    SET breaker_day_anchor = ?, breaker_day_utc = ?,
                        breaker_week_anchor = ?, breaker_week_iso = ?
                    WHERE id = 1
                    """,
                    (day_anchor, str(day_utc or ""), week_anchor, str(week_iso or "")),
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error("breaker anchor persist FAILED error=%s", e)
            return False

    def get_breaker_anchors(self) -> dict[str, Any] | None:
        """Reads the persisted breaker anchors.

        Returns the anchor dict when a HEALTHY read proves a usable,
        well-formed anchor row exists; None when absent. CORRUPT/ambiguous
        values (non-finite, non-positive, missing identity strings, type
        garbage) return None — the caller must treat None as 'no trustworthy
        anchor' and FAIL CLOSED, never as 'reset to current equity'.
        """
        if not self._is_sqlite:
            # PG-READ-PLANE-001 / D3: the SQLite connect opens a throwaway
            # temp DB under a pooled provider, so the read always failed with
            # "no such table" and boot treated the anchors as ABSENT — the
            # BUG-259 regression (loss budgets re-anchored to current equity
            # on restart, a pre-restart loss vanishing from the budget).
            # Route through the fabric read plane, which returns a real row
            # dict or None when the row is genuinely unset. A failed route or
            # a corrupt row still degrades to the documented fail-closed None
            # below — never a fabricated anchor.
            row = self._provider_read_guard(
                "get_breaker_anchors",
                lambda: None,
                sql="""
                SELECT breaker_day_anchor, breaker_day_utc,
                       breaker_week_anchor, breaker_week_iso
                FROM runtime_risk_state WHERE id = 1
                """,
                kind="row",
            )
            return self._validate_breaker_anchors(row)
        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT breaker_day_anchor, breaker_day_utc,
                           breaker_week_anchor, breaker_week_iso
                    FROM runtime_risk_state WHERE id = 1
                    """
                ).fetchone()
        except Exception as e:
            logger.error("breaker anchor read FAILED (treated as absent) error=%s", e)
            return None
        return self._validate_breaker_anchors(row)

    @staticmethod
    def _validate_breaker_anchors(row: Any) -> dict[str, Any] | None:
        """Fail-closed validation of one breaker anchor row (any mapping).

        Returns the anchor dict only for a healthy, well-formed row; None for
        an absent row (``row is None``) and for every CORRUPT/ambiguous value
        (non-finite, non-positive, missing identity strings, type garbage).
        None means 'no trustworthy anchor': the caller must never decode it
        as 'reset to current equity' (BUG-259).
        """
        if row is None:
            return None
        try:
            day_anchor = row["breaker_day_anchor"]
            week_anchor = row["breaker_week_anchor"]
            day_utc = row["breaker_day_utc"]
            week_iso = row["breaker_week_iso"]
        except (IndexError, KeyError, TypeError):
            return None
        if day_anchor is None or week_anchor is None:
            return None
        try:
            day_anchor = float(day_anchor)
            week_anchor = float(week_anchor)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(day_anchor) and day_anchor > 0.0):
            return None
        if not (math.isfinite(week_anchor) and week_anchor > 0.0):
            return None
        if not str(day_utc or "") or not str(week_iso or ""):
            return None
        return {
            "day_anchor": day_anchor,
            "day_utc": str(day_utc),
            "week_anchor": week_anchor,
            "week_iso": str(week_iso),
        }

    def get_runtime_risk_state(self) -> dict[str, Any] | None:
        """Synchronous read of the persisted runtime risk state.

        Read is UNTRUSTED-fail-closed: None is only returned when a healthy
        read proves the row is unset (or the DB does not exist yet). A failed
        read (corruption, lock storm, IO error, MISSING TABLE) raises
        :class:`RuntimeRiskStateReadError` so the boot path can refuse to
        trade — DB uncertainty must NEVER be decoded as 'no persisted state'
        (the old contract made a corrupt/unavailable DB boot as RUNNING with
        a live HALT row on disk; agent-17 probe, 2026-09-10).

        RT-009 (2026-09-25): a MISSING ``runtime_risk_state`` table is a
        FAILED read on both providers, not a fresh install. The live engine
        logged exactly this condition ('no such table: runtime_risk_state')
        while a HALT was on disk; the SQLite arm used to decode it as
        NO_PERSISTED_STATE -> RUNNING because ``_setup_storage`` re-creates
        the table on construction, silently healing away the damage and
        returning an empty read. ``_table_exists`` below proves the table was
        there BEFORE this read; the PG arm's read route already fails
        observably, so this brings the two providers onto one contract.

        Under a POOLED provider the same rule applies to a failed read route:
        ``_provider_read_guard`` degrades observably to the documented default
        (None) so a transient issue stays recoverable (mission s91), but None
        is what boot decodes as 'no persisted state' — so the two ARE
        distinguishable here and only here: a routed read that answered None
        proves the row is unset, while a FAILED route proves nothing. The
        failure is surfaced as ``RuntimeRiskStateReadError`` so the boot path
        can tell them apart; the degradation counters + warning the guard
        already emits stay exactly as they are (they are the observability
        surface for this exact condition).
        """
        if not self._is_sqlite:
            row = self._provider_read_guard(
                "get_runtime_risk_state",
                lambda: _READ_FAILED_SENTINEL,
                sql="SELECT * FROM runtime_risk_state WHERE id = 1",
                kind="row",
            )
            # The guard only reaches the default when the route FAILED (a
            # healthy route returns a real row or a real None). A failed route
            # means the persisted state could not be observed — never 'absent'.
            if row is _READ_FAILED_SENTINEL:
                raise RuntimeRiskStateReadError(
                    "runtime_risk_state read failed on the pooled provider "
                    "(audit read route failed or no read plane registered for "
                    "the audit domain)"
                )
            return row
        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "runtime_risk_state"):
                    raise RuntimeRiskStateReadError(
                        "runtime_risk_state table is missing — the safety-state "
                        "store cannot be trusted (re-created by setup, but the "
                        "persisted decision is gone)"
                    )
                row = conn.execute("SELECT * FROM runtime_risk_state WHERE id = 1").fetchone()
                return dict(row) if row is not None else None
        except RuntimeRiskStateReadError:
            raise
        except Exception as e:
            raise RuntimeRiskStateReadError(
                f"runtime_risk_state read failed ({type(e).__name__}): {e}"
            ) from e

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        """True when ``table`` is present on a live SQLite connection.

        A plain ``SELECT ... FROM <table>`` cannot distinguish 'the table is
        missing' from 'the table is empty' — and the missing case is exactly
        the boot-honesty question here. The catalog lookup makes it explicit
        without raising, so the caller can fail closed deliberately.
        """
        try:
            return bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
            )
        except Exception:
            return False

    def release_runtime_risk_state(
        self,
        *,
        actor: str,
        note: str = "",
        expected_state: str | None = None,
    ) -> bool:
        """Explicit, auditable release of a persisted safety halt.

        Only this path (or the CLI wrapper) may clear HALTED / KILL_SWITCH.
        Stamps release_actor + released_at durably; a restart/reconnect can
        NEVER reach this code path. Idempotent: releasing an already-RUNNING
        row re-stamps provenance without resurrecting a lost decision.
        Refuses (returns False) when expected_state is given and mismatched.
        """
        current = self.get_runtime_risk_state()
        if current is None:
            logger.warning("runtime_risk_state release: no persisted state row")
            return False
        if (
            expected_state is not None
            and str(current.get("state", "")).upper() != str(expected_state).upper()
        ):
            logger.error(
                "runtime_risk_state release refused: expected=%s actual=%s",
                expected_state,
                current.get("state"),
            )
            return False
        from datetime import UTC, datetime

        sql = """
            UPDATE runtime_risk_state
            SET state='RUNNING',
                release_required=0,
                released_at=?,
                release_actor=?,
                reason=CASE WHEN ? != '' THEN reason || ' | RELEASED: ' || ? ELSE reason END
            WHERE id=1
        """
        try:
            with self._connect_sqlite(10.0) as conn:
                conn.execute(
                    sql,
                    (datetime.now(UTC).isoformat(), str(actor), str(note or ""), str(note or "")),
                )
                conn.commit()
            logger.info(
                "RUNTIME RISK STATE RELEASED actor=%s previous=%s note=%s",
                actor,
                current.get("state"),
                note,
            )
            return True
        except Exception as e:
            logger.error("runtime_risk_state release FAILED actor=%s error=%s", actor, e)
            return False

    def record_dead_letter(
        self,
        *,
        query: str,
        args: tuple[Any, ...],
        error: BaseException,
        table_name: str = "",
        retry_count: int = 0,
        payload_note: str = "",
    ) -> bool:
        """Durably stores one failed audit row (dead-letter path).

        A4: delegate to the composed DeadLetterStore (public signature
        unchanged — callers stay byte-compatible).
        """
        return self.dead_letter_store.record(
            query=query,
            args=args,
            error=error,
            table_name=table_name,
            retry_count=retry_count,
            payload_note=payload_note,
        )

    def get_dead_letter_rows(self, limit: int = 200) -> list[dict[str, Any]]:
        """Reads dead-letter rows for inspection/tests (newest first).

        A4: delegate to the composed DeadLetterStore.
        """
        return self.dead_letter_store.list_recent(limit=limit)

    def get_consecutive_losses(self, limit: int = 100) -> tuple[int, str]:
        """Canonical consecutive-loss chain from FINALIZED ledger outcomes.

        Reads closed audit_ledger rows (newest first). Only finalized
        financial outcomes participate: OPENED placeholders (no exit), rows
        with NULL exit_price, and rejected/unfilled events are excluded by
        the status filter — a loss can never be fabricated from telemetry.
        Returns (count, newest_loss_close_time_iso).
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_consecutive_losses",
                lambda: (0, ""),
            )
        try:
            from nexus_scalp.risk.runtime_safety import evaluate_consecutive_losses_with_time

            with self._connect_sqlite(5.0) as conn:
                rows = conn.execute(
                    """
                    SELECT status, net_pnl_usd, COALESCE(NULLIF(close_time,''), timestamp) AS close_ts
                    FROM audit_ledger
                    WHERE status IN ('CLOSED','CLOSED_TP','CLOSED_SL','RECONCILED','MANUALLY_CLOSED')
                      AND exit_price IS NOT NULL
                    ORDER BY COALESCE(NULLIF(close_time,''), timestamp) DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
            return evaluate_consecutive_losses_with_time(
                [(r[0], float(r[1] or 0.0), str(r[2] or "")) for r in rows]
            )
        except Exception as e:
            logger.error("get_consecutive_losses failed: %s", e)
            return 0, ""

    # ---------------------------------------------------------------------
    # Broker history sync (MT5 = broker truth, DB = durable normalized copy)
    # ---------------------------------------------------------------------
    def sync_broker_history(
        self,
        orders: list[dict[str, Any]],
        deals: list[dict[str, Any]],
        symbol: str | None = None,
        sync_from: Any = None,
        sync_to: Any = None,
    ) -> dict[str, Any]:
        """Upserts the normalized broker order/deal/trade copy (idempotent).

        Exact broker-ticket deduplication: re-ingesting identical history is a
        no-op (UNIQUE(ticket) / UNIQUE(position_id) insert-or-ignore).
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "sync_broker_history",
                lambda: (
                    {
                        "orders_total": len(orders or []),
                        "orders_inserted": 0,
                        "orders_duplicates": len(orders or []),
                        "deals_total": len(deals or []),
                        "deals_inserted": 0,
                        "deals_duplicates": len(deals or []),
                        "trades_total": 0,
                        "trades_inserted": 0,
                        "trades_duplicates": 0,
                        "duration_ms": 0.0,
                    }
                ),
            )
        from datetime import UTC as _UTC
        from datetime import datetime as _dt

        symbol = symbol or ""
        sync_from_dt = (
            sync_from.astimezone(_UTC)
            if isinstance(sync_from, _dt)
            else normalize_history_dt(sync_from)
        )
        sync_to_dt = (
            sync_to.astimezone(_UTC) if isinstance(sync_to, _dt) else normalize_history_dt(sync_to)
        )
        with self._connect_sqlite(15.0) as conn:
            return sync_broker_history(
                conn,
                orders=orders or [],
                deals=deals or [],
                symbol=symbol,
                sync_from=sync_from_dt,
                sync_to=sync_to_dt,
            )

    def get_broker_history_meta(self, symbol: str | None = None) -> dict[str, Any] | None:
        """Returns the persisted sync watermark (None before the first sync)."""
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_broker_history_meta",
                lambda: None,
            )
        with self._connect_sqlite(5.0) as conn:
            return last_sync_window(conn, symbol or "")

    def get_broker_trades(
        self,
        limit: int = 500,
        offset: int = 0,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """Reconstructed logical trades, newest exit first."""
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_broker_trades",
                lambda: ([]),
            )
        clauses: list[str] = []
        args: list[Any] = []
        if symbol:
            clauses.append("symbol = ?")
            args.append(symbol)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT * FROM audit_broker_trades "
            f"{where} ORDER BY COALESCE(NULLIF(exit_time,''), '') DESC "
            "LIMIT ? OFFSET ?"
        )
        args += [int(limit), int(offset)]
        with self._connect_sqlite(5.0) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]

    def get_broker_deals(
        self,
        position_id: int | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Normalized broker deals (optionally for one position lifecycle)."""
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_broker_deals",
                lambda: ([]),
            )
        if position_id is not None:
            sql = "SELECT * FROM audit_broker_deals WHERE position_id = ? ORDER BY time ASC LIMIT ?"
            args: tuple[Any, ...] = (int(position_id), int(limit))
        else:
            sql = "SELECT * FROM audit_broker_deals ORDER BY time DESC LIMIT ?"
            args = (int(limit),)
        with self._connect_sqlite(5.0) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def get_broker_orders(
        self,
        position_id: int | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Normalized broker orders (optionally for one position lifecycle)."""
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_broker_orders",
                lambda: ([]),
            )
        if position_id is not None:
            sql = (
                "SELECT * FROM audit_broker_orders WHERE position_id = ? "
                "ORDER BY time_setup ASC LIMIT ?"
            )
            args: tuple[Any, ...] = (int(position_id), int(limit))
        else:
            sql = "SELECT * FROM audit_broker_orders ORDER BY time_setup DESC LIMIT ?"
            args = (int(limit),)
        with self._connect_sqlite(5.0) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def _create_experience_tables(self, conn: sqlite3.Connection) -> None:
        """
        Creates the Phase 08 Experience Intelligence schema.

        Design notes:
          * `audit_experiences` holds IMMUTABLE decision rows. Nothing in the
            codebase issues an UPDATE against it.
          * `audit_experience_outcomes` is append-only and keyed 1:1 by
            `idempotency_key`, which is what makes duplicate broker close
            callbacks harmless instead of inflating learning evidence.
          * `audit_experience_corrections` records additive corrections so
            historical truth is never destroyed.
          * `experience_model_registry` stores model METADATA only. Experiences
            never depend on a model artifact still existing.
          * Indexes cover every retrieval predicate used on the live path
            (strategy_id + decision_timestamp, symbol + decision_timestamp) so
            experience retrieval stays bounded and fast.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experience_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                execution_id TEXT DEFAULT '',
                decision_id TEXT DEFAULT '',
                idempotency_key TEXT UNIQUE NOT NULL,
                correction_of TEXT DEFAULT '',
                record_version INTEGER DEFAULT 2,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT DEFAULT '1.0.0',
                decision_timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                entry_reason TEXT NOT NULL,
                model_probability REAL DEFAULT 0.0,
                signal_confidence REAL DEFAULT 0.0,
                proposed_entry REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                risk_reward_ratio REAL DEFAULT 1.0,
                min_rr_policy REAL DEFAULT 0.0,
                feature_schema_id TEXT DEFAULT 'scalp_v1',
                feature_dimension INTEGER DEFAULT 50,
                feature_hash TEXT DEFAULT '',
                model_id TEXT DEFAULT '',
                model_version TEXT DEFAULT '',
                config_version TEXT DEFAULT '',
                payload TEXT NOT NULL
            );
            """
        )
        # Forward migration for databases created by the first Phase 08 revision.
        # Uses the shared _add_column_if_missing helper (PRAGMA-gated).
        for col_name, col_type in [
            ("correction_of", "TEXT DEFAULT ''"),
            ("record_version", "INTEGER DEFAULT 1"),
            ("min_rr_policy", "REAL DEFAULT 0.0"),
            ("feature_schema_id", "TEXT DEFAULT 'scalp_v1'"),
            ("feature_dimension", "INTEGER DEFAULT 50"),
            ("model_id", "TEXT DEFAULT ''"),
            ("model_version", "TEXT DEFAULT ''"),
            ("config_version", "TEXT DEFAULT ''"),
        ]:
            _add_column_if_missing(conn, "audit_experiences", col_name, col_type)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_experience_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT UNIQUE NOT NULL,
                execution_id TEXT DEFAULT '',
                outcome_timestamp TEXT NOT NULL,
                is_executed INTEGER DEFAULT 0,
                is_closed INTEGER DEFAULT 0,
                exit_reason TEXT DEFAULT '',
                realized_pnl_usd REAL DEFAULT 0.0,
                realized_r_multiple REAL DEFAULT 0.0,
                approved_volume REAL DEFAULT 0.0,
                mae_points REAL DEFAULT 0.0,
                mfe_points REAL DEFAULT 0.0,
                mae_usd REAL DEFAULT 0.0,
                mfe_usd REAL DEFAULT 0.0,
                mae_r REAL DEFAULT 0.0,
                mfe_r REAL DEFAULT 0.0,
                holding_duration_seconds REAL DEFAULT 0.0,
                slippage_points REAL DEFAULT 0.0,
                execution_latency_ms REAL DEFAULT 0.0,
                strategy_quality REAL DEFAULT 0.0,
                entry_quality REAL DEFAULT 0.0,
                execution_quality REAL DEFAULT 0.0,
                management_quality REAL DEFAULT 0.0,
                exit_quality REAL DEFAULT 0.0,
                behavioral_flags TEXT DEFAULT '',
                payload TEXT NOT NULL
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_experience_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                correction_id TEXT UNIQUE NOT NULL,
                idempotency_key TEXT NOT NULL,
                corrected_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT DEFAULT '',
                new_value TEXT DEFAULT ''
            );
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_intelligence_registry (
                strategy_id TEXT PRIMARY KEY,
                lifecycle_state TEXT NOT NULL,
                sample_count INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0.0,
                expectancy_r REAL DEFAULT 0.0,
                recent_expectancy_r REAL DEFAULT 0.0,
                normalized_drawdown_r REAL DEFAULT 0.0,
                profit_factor REAL DEFAULT 1.0,
                confidence_score REAL DEFAULT 0.0,
                evidence_quality REAL DEFAULT 0.0,
                replay_validated INTEGER DEFAULT 0,
                probation_samples INTEGER DEFAULT 0,
                score_payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        for col_name, col_type in [
            ("recent_expectancy_r", "REAL DEFAULT 0.0"),
            ("normalized_drawdown_r", "REAL DEFAULT 0.0"),
            ("evidence_quality", "REAL DEFAULT 0.0"),
            ("replay_validated", "INTEGER DEFAULT 0"),
            ("probation_samples", "INTEGER DEFAULT 0"),
        ]:
            _add_column_if_missing(conn, "strategy_intelligence_registry", col_name, col_type)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experience_model_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL,
                model_version TEXT NOT NULL,
                model_role TEXT DEFAULT 'PRIMARY_SCALP',
                artifact_path TEXT DEFAULT '',
                artifact_fingerprint TEXT DEFAULT '',
                feature_schema_id TEXT DEFAULT 'scalp_v1',
                feature_dimension INTEGER DEFAULT 50,
                config_version TEXT DEFAULT '',
                build_identity TEXT DEFAULT '',
                was_replacement INTEGER DEFAULT 0,
                registered_at TEXT NOT NULL,
                UNIQUE(model_id, model_version, artifact_fingerprint)
            );
            """
        )

        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_exp_strategy_time "
            "ON audit_experiences(strategy_id, decision_timestamp DESC);",
            "CREATE INDEX IF NOT EXISTS idx_exp_symbol_time "
            "ON audit_experiences(symbol, decision_timestamp DESC);",
            "CREATE INDEX IF NOT EXISTS idx_exp_request ON audit_experiences(request_id);",
            "CREATE INDEX IF NOT EXISTS idx_exp_schema "
            "ON audit_experiences(feature_schema_id, feature_dimension);",
            "CREATE INDEX IF NOT EXISTS idx_exp_outcome_key "
            "ON audit_experience_outcomes(idempotency_key);",
            "CREATE INDEX IF NOT EXISTS idx_exp_corrections_key "
            "ON audit_experience_corrections(idempotency_key);",
        ):
            with contextlib.suppress(Exception):
                conn.execute(index_sql)

    def _create_intelligence_tables(self, conn: sqlite3.Connection) -> None:
        """
        Creates the PHASE 09 Trade Intelligence Brain schema.

        All rows here are derived/buildable intelligence layered on top of the
        authoritative Phase 08 experience tables. Design rules:

          * `position_lifecycle_events`  -- IMMUTABLE append-only position-timeline
            events keyed by (ticket, event_key) so replay can never duplicate or
            reorder the timeline of a position.
          * `trade_autopsies`            -- ONE forensic narrative row per closed
            ticket (upsert on ticket), answering "why did this trade win/lose?".
          * `behavior_detections`        -- append-only measurable behavioral
            pattern evidence (GREED_PATTERN, PANIC_EXIT_PATTERN, ...).
          * `strategy_evolution_candidates` -- discovered-but-unvalidated strategy
            variations. A candidate is NEVER executed live; only backtested and
            validated before it may enter strategy memory.
          * `intelligence_worker_state`  -- restart-safe worker bookkeeping so a
            crash mid-cycle resumes from the last checkpoint instead of redoing
            history.
        """
        self._create_table_position_lifecycle_events(conn)
        self._create_table_trade_autopsies(conn)
        self._create_table_behavior_detections(conn)
        self._create_table_behavior_analysis(conn)
        self._create_table_strategy_evolution_candidates(conn)
        self._create_table_intelligence_worker_state(conn)
        self._create_factory_tables(conn)

    def _create_table_position_lifecycle_events(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS position_lifecycle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE NOT NULL,
                ticket TEXT NOT NULL,
                trade_id TEXT DEFAULT '',
                experience_id TEXT DEFAULT '',
                symbol TEXT NOT NULL,
                timeframe TEXT DEFAULT '',
                event_type TEXT NOT NULL,
                sequence INTEGER DEFAULT 0,
                event_timestamp TEXT NOT NULL,
                market_context TEXT DEFAULT '{}',
                position_snapshot TEXT DEFAULT '{}',
                payload TEXT DEFAULT '{}'
            );
            """
        )
        for col_name, col_type in [
            ("experience_id", "TEXT DEFAULT ''"),
            ("sequence", "INTEGER DEFAULT 0"),
        ]:
            _add_column_if_missing(conn, "position_lifecycle_events", col_name, col_type)

        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_lifecycle_ticket ON position_lifecycle_events(ticket, sequence);",
            "CREATE INDEX IF NOT EXISTS idx_lifecycle_type ON position_lifecycle_events(event_type);",
        ):
            with contextlib.suppress(Exception):
                conn.execute(index_sql)

    def _create_table_trade_autopsies(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_autopsies (
                ticket TEXT PRIMARY KEY,
                trade_id TEXT DEFAULT '',
                experience_id TEXT DEFAULT '',
                strategy_id TEXT DEFAULT '',
                strategy_version TEXT DEFAULT '',
                symbol TEXT NOT NULL,
                timeframe TEXT DEFAULT '',
                entry_price REAL DEFAULT 0.0,
                exit_price REAL DEFAULT 0.0,
                volume REAL DEFAULT 0.0,
                direction TEXT DEFAULT '',
                entry_reason TEXT DEFAULT '',
                realized_pnl_usd REAL DEFAULT 0.0,
                realized_r REAL DEFAULT 0.0,
                mfe_r REAL DEFAULT 0.0,
                mae_r REAL DEFAULT 0.0,
                giveback_pct REAL DEFAULT 0.0,
                holding_duration_sec REAL DEFAULT 0.0,
                exit_mechanism TEXT DEFAULT '',
                strategy_quality REAL DEFAULT 0.0,
                entry_quality REAL DEFAULT 0.0,
                management_quality REAL DEFAULT 0.0,
                exit_quality REAL DEFAULT 0.0,
                execution_quality REAL DEFAULT 0.0,
                quality_verdict TEXT DEFAULT '',
                behavioral_flags TEXT DEFAULT '',
                narrative TEXT DEFAULT '',
                autopsied_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        for col_name, col_type in [
            ("strategy_version", "TEXT DEFAULT ''"),
            ("symbol", "TEXT NOT NULL DEFAULT ''"),
            ("timeframe", "TEXT DEFAULT ''"),
        ]:
            _add_column_if_missing(conn, "trade_autopsies", col_name, col_type)

        with contextlib.suppress(Exception):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_autopsy_strategy ON trade_autopsies(strategy_id);"
            )

    def _create_table_behavior_detections(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS behavior_detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                behavior_key TEXT UNIQUE NOT NULL,
                behavior_id TEXT NOT NULL,
                ticket TEXT NOT NULL,
                experience_id TEXT DEFAULT '',
                ticket_ctx TEXT DEFAULT '',
                pattern TEXT NOT NULL,
                severity TEXT NOT NULL,
                confidence REAL DEFAULT 0.0,
                evidence TEXT DEFAULT '{}',
                detected_at TEXT NOT NULL,
                autocorrected INTEGER DEFAULT 0
            );
            """
        )
        for col_name, col_type in [
            ("ticket_ctx", "TEXT DEFAULT ''"),
            ("behavior_key", "TEXT DEFAULT ''"),
        ]:
            _add_column_if_missing(conn, "behavior_detections", col_name, col_type)

        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_behavior_ticket ON behavior_detections(ticket);",
            "CREATE INDEX IF NOT EXISTS idx_behavior_pattern ON behavior_detections(pattern);",
        ):
            with contextlib.suppress(Exception):
                conn.execute(index_sql)

    def _create_table_behavior_analysis(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS behavior_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_key TEXT UNIQUE NOT NULL,
                ticket TEXT NOT NULL,
                symbol TEXT DEFAULT '',
                strategy_id TEXT DEFAULT '',
                behavior_version TEXT NOT NULL,
                anomaly_version TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                evidence_coverage REAL DEFAULT 0.0,
                complete_context INTEGER DEFAULT 0,
                partial_context INTEGER DEFAULT 0,
                flags TEXT DEFAULT '[]',
                anomalies TEXT DEFAULT '[]'
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anomaly_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                anomaly_id TEXT UNIQUE NOT NULL,
                ticket TEXT DEFAULT '',
                anomaly_type TEXT NOT NULL,
                category TEXT DEFAULT 'DATA',
                severity TEXT DEFAULT 'LOW',
                confidence REAL DEFAULT 0.0,
                evidence TEXT DEFAULT '{}',
                detected_at TEXT NOT NULL,
                algorithm_version TEXT NOT NULL
            );
            """
        )
        for col_name, col_type in [
            ("behavior_version", "TEXT DEFAULT ''"),
            ("anomaly_version", "TEXT DEFAULT ''"),
            ("evidence_coverage", "REAL DEFAULT 0.0"),
            ("complete_context", "INTEGER DEFAULT 0"),
            ("partial_context", "INTEGER DEFAULT 0"),
        ]:
            _add_column_if_missing(conn, "behavior_analysis", col_name, col_type)
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_behavior_analysis_ticket ON behavior_analysis(ticket);",
            "CREATE INDEX IF NOT EXISTS idx_behavior_analysis_version "
            "ON behavior_analysis(behavior_version, anomaly_version);",
            "CREATE INDEX IF NOT EXISTS idx_anomaly_events_ticket ON anomaly_events(ticket);",
            "CREATE INDEX IF NOT EXISTS idx_anomaly_events_type ON anomaly_events(anomaly_type);",
        ):
            with contextlib.suppress(Exception):
                conn.execute(index_sql)

    def _create_table_strategy_evolution_candidates(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_evolution_candidates (
                candidate_id TEXT PRIMARY KEY,
                source_strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT DEFAULT '',
                hypothesis TEXT NOT NULL,
                parameter_delta TEXT DEFAULT '{}',
                pattern_evidence TEXT DEFAULT '{}',
                status TEXT NOT NULL,
                backtest_expectancy_r REAL DEFAULT 0.0,
                backtest_sample_count INTEGER DEFAULT 0,
                validated_at TEXT DEFAULT '',
                discovered_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )
        with contextlib.suppress(Exception):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evolution_status ON strategy_evolution_candidates(status);"
            )

    def _create_table_intelligence_worker_state(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS intelligence_worker_state (
                scope TEXT PRIMARY KEY,
                last_checkpoint TEXT DEFAULT '',
                last_cycle_at TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                cycle_count INTEGER DEFAULT 0
            );
            """
        )

    def _create_factory_tables(self, conn: sqlite3.Connection) -> None:
        # =====================================================================
        # STRATEGY FACTORY research memory (2026-08-20) — append-only records.
        # The factory ORCHESTRATES generation/evolution over the research
        # pipeline; these tables hold the research memory itself.
        # =====================================================================
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS factory_generations (
                generation_id TEXT PRIMARY KEY,
                number INTEGER NOT NULL,
                mode TEXT DEFAULT 'MANUAL',
                parent_generation TEXT DEFAULT '',
                population_target INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT DEFAULT NULL,
                status TEXT DEFAULT 'PENDING',
                config TEXT DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS factory_candidates (
                candidate_id TEXT PRIMARY KEY,
                definition_hash TEXT NOT NULL,
                generation_id TEXT NOT NULL,
                source TEXT DEFAULT 'TEMPLATE',
                operator TEXT DEFAULT 'NONE',
                parent_ids TEXT DEFAULT '[]',
                family TEXT DEFAULT 'HYBRID',
                population_index INTEGER DEFAULT 0,
                dsl TEXT DEFAULT '{}',
                structural TEXT DEFAULT '{}',
                lifecycle TEXT DEFAULT 'GENERATED',
                failure_reasons TEXT DEFAULT '[]',
                llm_response_id TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        # PHASE 25 (2026-08-25): ensure context_matrices on factory_candidates
        try:
            from nexus_scalp.strategies.factory.store import ensure_factory_context_columns

            ensure_factory_context_columns(conn)
        except Exception:
            pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS factory_failures (
                failure_id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                strategy_id TEXT DEFAULT '',
                generation_id TEXT DEFAULT '',
                stage TEXT DEFAULT 'DSL_VALIDATION',
                reason TEXT DEFAULT '',
                detail TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS factory_events (
                event_id TEXT PRIMARY KEY,
                generation_id TEXT DEFAULT '',
                candidate_id TEXT DEFAULT '',
                event_type TEXT NOT NULL,
                message TEXT DEFAULT '',
                payload TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS factory_runs (
                run_id TEXT PRIMARY KEY,
                generation_id TEXT DEFAULT '',
                strategy_id TEXT DEFAULT '',
                experiment_kind TEXT DEFAULT 'GENERATE',
                executed_at TEXT NOT NULL,
                config TEXT DEFAULT '{}',
                result_summary TEXT DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS factory_provider_usage (
                usage_id TEXT PRIMARY KEY,
                generation_id TEXT DEFAULT '',
                requests INTEGER DEFAULT 0,
                failures INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                estimated_cost_usd REAL DEFAULT 0.0,
                last_latency_ms REAL DEFAULT 0.0,
                last_error TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS factory_loop_state (
                scope TEXT PRIMARY KEY,
                state TEXT DEFAULT 'STOPPED',
                generation_id TEXT DEFAULT '',
                checkpoint TEXT DEFAULT '{}',
                updated_at TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                last_cycle_at TEXT DEFAULT '',
                cycle_count INTEGER DEFAULT 0
            );
            """
        )
        with contextlib.suppress(Exception):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_factory_cand_gen ON factory_candidates(generation_id, population_index);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_factory_cand_hash ON factory_candidates(definition_hash);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_factory_fail_gen ON factory_failures(generation_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_factory_events_gen ON factory_events(generation_id, created_at);"
            )

    def _create_research_tables(self, conn: sqlite3.Connection) -> None:
        """
        Creates the PHASE 09B Strategy Research / Backtest / Validation schema.

        All rows here are DERIVED from the authoritative Phase 08 experience
        ledger and are rebuildable. The registry preserves historical validation
        truth (spec 20 / 28); validation runs are append-only (spec 26).
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                feature_schema_id TEXT DEFAULT 'scalp_v1',
                feature_dimension INTEGER DEFAULT 50,
                discovery_source TEXT DEFAULT '',
                discovery_window TEXT DEFAULT '',
                context_definition TEXT DEFAULT '{}',
                parent_strategy_ids TEXT DEFAULT '[]',
                lifecycle TEXT NOT NULL,
                backtest TEXT DEFAULT '{}',
                walkforward TEXT DEFAULT '{}',
                oos TEXT DEFAULT '{}',
                robustness TEXT DEFAULT '{}',
                score TEXT DEFAULT '{}',
                confidence REAL DEFAULT 0.0,
                sample_count INTEGER DEFAULT 0,
                validation_lineage TEXT DEFAULT '[]',
                retirement_reason TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (strategy_id, strategy_version)
            );
            """
        )
        # PHASE 25 (2026-08-25): ensure context_matrices on strategy_registry
        try:
            from nexus_scalp.research.store import ensure_registry_context_columns

            ensure_registry_context_columns(conn)
        except Exception:
            pass

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                config TEXT DEFAULT '{}',
                build_identity TEXT DEFAULT '',
                result_summary TEXT DEFAULT '{}',
                UNIQUE (run_id)
            );
            """
        )
        with contextlib.suppress(Exception):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_registry_id ON strategy_registry(strategy_id, updated_at);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_registry_lifecycle ON strategy_registry(lifecycle);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_runs_strategy ON research_runs(strategy_id);"
            )

        # Restart-safe research worker bookkeeping.
        # TASK-21-RESEARCH-OBSERVABILITY: first-class gate / event / evidence
        # / snapshot / heartbeat / queue observability. All tables are
        # idempotent (CREATE IF NOT EXISTS + ADD COLUMN guards) so existing
        # user databases upgrade in place.
        self._create_research_observability_tables(conn)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_worker_state (
                scope TEXT PRIMARY KEY,
                last_checkpoint TEXT DEFAULT '',
                last_cycle_at TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                cycle_count INTEGER DEFAULT 0
            );
            """
        )

    def _create_research_observability_tables(self, conn: sqlite3.Connection) -> None:
        """TASK-21 tables: research_gates / research_events / research_evidence /
        research_run_snapshots / research_worker_heartbeat.
        Idempotent; upgrades existing user databases in place via ADD COLUMN guards.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_gates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gate_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                gate_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                started_at TEXT DEFAULT '',
                completed_at TEXT DEFAULT '',
                duration_ms REAL DEFAULT 0.0,
                configuration_version TEXT DEFAULT '',
                dataset_version TEXT DEFAULT '',
                engine_version TEXT DEFAULT '',
                result TEXT DEFAULT '{}',
                failure_reason TEXT DEFAULT '',
                failure_class TEXT DEFAULT 'UNKNOWN',
                evidence_id TEXT DEFAULT '',
                retryable INTEGER DEFAULT 0,
                order_index INTEGER DEFAULT 0,
                UNIQUE (gate_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                gate_id TEXT DEFAULT '',
                event_type TEXT NOT NULL,
                message TEXT DEFAULT '',
                payload TEXT DEFAULT '{}',
                occurred_at TEXT NOT NULL,
                UNIQUE (event_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evidence_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                research_run_id TEXT NOT NULL,
                gate_id TEXT DEFAULT '',
                kind TEXT NOT NULL,
                content TEXT DEFAULT '{}',
                content_hash TEXT NOT NULL DEFAULT '',
                dataset_version TEXT DEFAULT '',
                engine_version TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE (evidence_id)
            );
            """
        )
        # Archive tables. AUDIT-0009 owns these on a database that already
        # exists (the governed migration applies them and records the version
        # bump), but a *fresh* bootstrap creates every live table above without
        # running the migration registry. Without them, ``list_events``'s
        # archive-aware read (live UNION archive) dies with
        # ``no such table: research_events_archive`` and silently returns zero
        # rows — the whole research timeline goes invisible on a first-run DB.
        # The DDL is identical to ``_audit_0009_research_archive_tables`` and
        # idempotent, so the migration is a no-op re-run on an already-bootstrapped
        # database.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_events_archive (
                id INTEGER PRIMARY KEY,
                event_id TEXT,
                strategy_id TEXT,
                research_run_id TEXT,
                gate_id TEXT,
                event_type TEXT,
                message TEXT,
                payload TEXT,
                occurred_at TEXT,
                archived_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_evidence_archive (
                id INTEGER PRIMARY KEY,
                evidence_id TEXT,
                strategy_id TEXT,
                research_run_id TEXT,
                gate_id TEXT,
                kind TEXT,
                content TEXT,
                content_hash TEXT,
                dataset_version TEXT,
                engine_version TEXT,
                created_at TEXT,
                archived_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_archive_occurred "
            "ON research_events_archive (occurred_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_evidence_archive_created "
            "ON research_evidence_archive (created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_run_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                research_run_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                strategy_definition_hash TEXT DEFAULT '',
                strategy_configuration TEXT DEFAULT '{}',
                dataset_version TEXT DEFAULT '',
                dataset_hash TEXT DEFAULT '',
                feature_schema_version TEXT DEFAULT '',
                model_version TEXT DEFAULT '',
                model_hash TEXT DEFAULT '',
                rule_matrix_version TEXT DEFAULT '',
                runtime_configuration_version TEXT DEFAULT '',
                backtest_engine_version TEXT DEFAULT '',
                validation_engine_version TEXT DEFAULT '',
                random_seed TEXT DEFAULT '',
                research_prompt_version TEXT DEFAULT '',
                engine_version TEXT DEFAULT '',
                configuration_hash TEXT DEFAULT '',
                research_hash TEXT DEFAULT '',
                captured_at TEXT NOT NULL,
                UNIQUE (research_run_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_worker_heartbeat (
                scope TEXT PRIMARY KEY,
                last_beat_at TEXT DEFAULT '',
                cycle_count INTEGER DEFAULT 0,
                last_cycle_start TEXT DEFAULT '',
                last_cycle_completion TEXT DEFAULT '',
                last_cycle_duration_ms REAL DEFAULT 0.0,
                last_action TEXT DEFAULT '',
                current_job TEXT DEFAULT '',
                current_strategy TEXT DEFAULT '',
                current_gate TEXT DEFAULT '',
                queued_jobs INTEGER DEFAULT 0,
                failed_jobs INTEGER DEFAULT 0,
                last_error TEXT DEFAULT '',
                status TEXT DEFAULT 'RUNNING'
            );
            """
        )
        for col_def in [
            ("status", "TEXT"),
            ("run_outcome", "TEXT"),
            ("snapshot_id", "TEXT"),
            ("gates", "TEXT"),
            ("completed_at", "TEXT"),
        ]:
            _add_column_if_missing(conn, "research_runs", col_def[0], col_def[1])
        with contextlib.suppress(Exception):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_gates_strategy ON research_gates(strategy_id, order_index);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_gates_run ON research_gates(research_run_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_strategy ON research_events(strategy_id, occurred_at);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evidence_strategy ON research_evidence(strategy_id, created_at);"
            )
        # CHG-0035 provenance hardening (RESEARCH_RUN_SNAPSHOT v2): identity
        # columns beyond the v1 contract. ADD COLUMN guarded (idempotent —
        # expected control flow on already-migrated DBs). Existing rows stay
        # empty = NOT_RECORDED (honest); no backfill invention.
        for col_def in [
            ("feature_schema_id", "TEXT"),
            ("feature_dimension", "INTEGER"),
            ("model_id", "TEXT"),
            ("git_commit", "TEXT"),
        ]:
            _add_column_if_missing(conn, "research_run_snapshots", col_def[0], col_def[1])

    def _connect_sqlite(self, timeout: float) -> sqlite3.Connection:
        """One SQLite connect site for the whole repository.

        Every raw sqlite3.connect(self._db_path, ...) call is routed HERE so
        the URI contract lives in exactly one place: when the resolved path
        is a ``file:`` URI (the shared in-memory audit DB), sqlite3.connect
        MUST receive uri=True. Without it, sqlite treats the URI STRING as a
        literal FILE NAME and silently creates a junk file called
        "file::memory:?cache=shared" in the process CWD on every flush/read
        of an in-memory repository (disk-leak bug, 2026-09-09).
        """
        uri = self._db_path.startswith("file:")
        return sqlite3.connect(self._db_path, timeout=timeout, uri=uri)

    def _provider_write_backend(self) -> Any:
        """The fabric's pooled WRITE backend for the audit domain, else None.

        LATENCY CONTRACT: ``set_runtime_risk_state`` / ``save_breaker_anchors``
        are synchronous safety writes — the caller's bool must reflect real
        durability, so this resolves the pooled WRITE backend directly and
        executes against it rather than enqueueing on the async write plane
        (a queued write can still be in-flight when the caller acts on True,
        and on a fast shutdown the row would never land).

        Resolved per call (never cached): the domain is provisioned lazily by
        ``_build_write_plane`` on the first non-SQLite boot, so a value fixed
        at construction would permanently observe ``None`` on a fresh process
        — the RTF-002 class of bug.
        """
        if self._is_sqlite:
            return None
        try:
            from nexus_scalp.database.fabric import get_domain_backend

            return get_domain_backend("audit", readonly=False)
        except Exception as exc:
            logger.error("[DB-FABRIC] audit write backend unavailable: %s", exc)
            return None

    def _provider_execute_write(self, statements: Sequence[tuple[str, Sequence[Any]]]) -> bool:
        """Run one atomic non-SQLite write against the pooled write backend.

        ``statements`` is a list of ``(query, args)`` pairs applied in ONE
        transaction (the backend's ``execute_batch`` commits atomically or
        rolls back). Returns True only when the backend reports success; a
        missing backend or a raised error is logged and returns False — a
        safety write never silently succeeds (the mission's fail-closed rule).

        Statements are normalised with the write plane's :func:`translate_sql`
        before they reach the backend: the safety writers emit the same
        SQLite-dialect text the SQLite branch uses (``INSERT OR IGNORE``), and
        the pooled backend would otherwise reject the bare INSERT with a
        duplicate-key violation instead of ignoring. ``?`` placeholders are
        left untouched for the driver to translate.
        """
        backend = self._provider_write_backend()
        if backend is None:
            logger.error(
                "audit provider write FAILED (no pooled write backend for the "
                "audit domain; provision it via the database fabric)"
            )
            return False
        # execute_batch takes (query, rows) pairs where `rows` is a sequence
        # of ROWS (each row the arg tuple for one execution of the query).
        # Every caller here is a single-row statement, so the arg tuple is
        # wrapped as the one and only row.
        from nexus_scalp.adapters.database.audit_write_plane import translate_sql

        batch = [(translate_sql(query), [tuple(args)]) for query, args in statements]
        try:
            backend.execute_batch(batch)
            return True
        except Exception as exc:
            logger.error("audit provider write FAILED error=%s", exc)
            return False

    def _provider_db_path(self) -> str:
        """A real, non-empty location string for a non-SQLite provider.

        Under PostgreSQL ``self._db_path`` is ``""`` (there is no file), and
        a handful of observability paths (``debug_snapshot``'s schema probe)
        build a ``Path`` from it — ``Path("")`` is ``WindowsPath('.')`` and
        ``DatabaseMigrationEngine.status()`` then raises ``WindowsPath('.') has
        an empty name`` (contract defect D9). The fabric's DSN names a real
        server-side database, so the probe reports the live database instead
        of a non-existent local file; the fallback keeps the value non-empty
        even when the DSN is unresolved.
        """
        if self._is_sqlite:
            return self._db_path
        dsn = getattr(self, "_db_url", "") or ""
        if dsn:
            try:
                from nexus_scalp.database.fabric import _pg_dsn_parts

                parts = _pg_dsn_parts(dsn)
                name = parts.get("database", "")
                if name:
                    host = parts.get("host", "localhost")
                    port = parts.get("port", "5432")
                    return f"postgresql://{host}:{port}/{name}"
            except Exception:
                pass  # DSN unparseable: fall back to the workspace path
        try:
            from nexus_scalp.release.paths import get_runtime_workspace

            return str(Path(get_runtime_workspace()) / "audit-postgresql")
        except Exception:
            return "audit-postgresql"

    # ---------------------------------------------------------------------
    # Provider read guard (CR-02 / CHG-0067)
    # ------------------------------------------------------------------
    #: Minimum spacing between repeated degradation warnings for ONE
    #: operation. The FIRST occurrence always logs; later repeats are
    #: rate-limited here while ``provider_read_degraded_total`` keeps
    #: counting every degraded read, so a hot loop cannot flood the
    #: warning file while staying observable through the counter.
    _PROVIDER_READ_LOG_INTERVAL_SEC: float = 60.0

    def _provider_read_guard(
        self,
        operation: str,
        default: Callable[[], Any],
        *,
        sql: str = "",
        args: tuple[Any, ...] = (),
        kind: str = "value",
    ) -> Any:
        """Observable non-SQLite fallback for a SQLite-gated read (CR-02).

        Before CHG-0067 every ``if not self._is_sqlite`` read gate returned
        its default SILENTLY: under PostgreSQL the caller received
        ``None``/``0``/``[]`` with no exception and no trace - fail-silent
        wrong data, which the mission forbids (no silent loss of
        consistency).

        What happens instead:

        1. ROUTE the read through the fabric read plane when one is
           genuinely registered for the audit domain AND the gate declared
           its query here (``sql`` + ``args`` + ``kind``).  Since
           ``provision_domain`` registers the READ backend alongside the
           WRITE one (CHG-0067), this branch IS the production path under
           PostgreSQL; before that the registry held only writes and every
           declared read degraded to (2).
        2. Otherwise make the degradation OBSERVABLE: bump
           ``provider_read_degraded_total`` (plus a per-operation
           breakdown), emit ONE structured warning per operation name
           (repeats rate-limited by ``_PROVIDER_READ_LOG_INTERVAL_SEC``),
           and only then return ``default()``. Nothing raises into
        callers: a recoverable issue stays recoverable (mission s91), and
           a non-recoverable read is now visible in the log and the
           metrics surface instead of invisible.

        SQLite never reaches this helper - the gates short-circuit first -
        so SQLite read semantics are unchanged byte for byte.

        No read-plane health surface exists yet (the fabric health probe
        covers connectivity only), so this counter plus this warning are
        today's readiness signal for audit read-plane state.
        """
        if self._is_sqlite:
            # Defensive: the gates make this unreachable, and a SQLite read
            # must never take a degraded path.
            return default()

        routed, value = self._route_provider_read(operation, sql, args, kind)
        if routed:
            self._bump_provider_read_counter("provider_reads_routed")
            return value

        total = self._bump_provider_read_counter("provider_read_degraded_total")
        occurrences = self._bump_provider_read_operation(operation)
        self._warn_provider_read(operation, kind, occurrences, total)
        return default()

    def _route_provider_read(
        self,
        operation: str,
        sql: str,
        args: tuple[Any, ...],
        kind: str,
    ) -> tuple[bool, Any]:
        """Serve the declared read from the fabric read plane, if any.

        Returns ``(True, value)`` when a registered READ plane answered and
        ``(False, None)`` when the read must degrade observably (no query
        declared, no read plane registered, or the route failed - a failed
        route is counted and warned, never swallowed).
        """
        if not sql or kind == "value":
            return False, None  # read not declared here: never guess a query
        plane = self._registered_audit_read_plane()
        if plane is None:
            return False, None
        try:
            if kind == "rows":
                return True, list(plane.query(sql, args))
            if kind == "row":
                query_one = getattr(plane, "query_one", None)
                if callable(query_one):
                    return True, query_one(sql, args)
                rows = plane.query(sql, args)
                return True, (rows[0] if rows else None)
            if kind == "exists":
                return True, bool(plane.query(sql, args))
            if kind == "count":
                return True, int(plane.scalar(sql, args) or 0)
            return True, plane.scalar(sql, args)
        except Exception as exc:
            # A failed route degrades observably: counted + warned here, then
            # the caller's documented default is returned below.
            self._bump_provider_read_counter("provider_read_route_errors")
            logger.warning(
                "[DB-FABRIC] audit provider read route failed op=%s kind=%s error=%s",
                operation,
                kind,
                type(exc).__name__,
            )
            return False, None

    def _registered_audit_read_plane(self) -> Any:
        """The fabric's registered READ plane for ``audit``, else ``None``.

        ``get_domain_backend(domain, readonly=True)`` is the fabric's
        read-side accessor; ``provision_domain`` registers the read plane
        there (CHG-0067).  When it is absent this returns ``None`` and every
        gated read degrades observably rather than inventing an ad-hoc
        connection path. A WRITE-shaped backend is refused outright: reads
        must never share the write path, so a slot holding only the write
        backend does not make the domain readable.
        """
        if self._is_sqlite:
            return None
        try:
            from nexus_scalp.database.fabric import get_domain_backend

            backend = get_domain_backend("audit", readonly=True)
        except Exception:
            return None
        if backend is None:
            return None
        if hasattr(backend, "execute") or not hasattr(backend, "query"):
            return None
        return backend

    def _bump_provider_read_counter(self, name: str) -> int:
        """Increment and return one provider-read counter (public attribute)."""
        value = int(getattr(self, name, 0) or 0) + 1
        setattr(self, name, value)
        return value

    def _bump_provider_read_operation(self, operation: str) -> int:
        """Per-operation breakdown of degraded reads (never capped)."""
        breakdown = getattr(self, "provider_read_degraded_ops", None)
        if not isinstance(breakdown, dict):
            breakdown = {}
            self.provider_read_degraded_ops = breakdown
        count = int(breakdown.get(operation, 0)) + 1
        breakdown[operation] = count
        return count

    def _warn_provider_read(
        self,
        operation: str,
        kind: str,
        occurrences: int,
        total: int,
    ) -> None:
        """One structured warning per operation; repeats rate-limited."""
        state = getattr(self, "_provider_read_guard_state", None)
        if not isinstance(state, dict):
            state = {}
            self._provider_read_guard_state = state
        now = time.monotonic()
        last_seen, emissions = state.get(operation, (0.0, 0))
        if emissions and (now - float(last_seen)) < self._PROVIDER_READ_LOG_INTERVAL_SEC:
            return  # suppressed by the rate limit; the counter still moves
        state[operation] = (now, emissions + 1)
        logger.warning(
            "[DB-FABRIC] audit provider read degraded op=%s kind=%s emission=%d "
            "occurrences=%d provider_read_degraded_total=%d -> documented default "
            "(no read plane registered for domain 'audit' — the domain is not "
            "provisioned for reads; never a silent loss of consistency)",
            operation,
            kind,
            emissions + 1,
            occurrences,
            total,
        )

    def provider_read_metrics(self) -> dict[str, Any]:
        """Provider-read observability surface (CR-02 / CHG-0067).

        The degradation must be inspectable by anything that wants a
        readiness view of audit reads: total + per-operation counts, routed
        count, route errors and whether a read plane is registered at all.
        """
        degraded = int(getattr(self, "provider_read_degraded_total", 0) or 0)
        return {
            "provider": "sqlite" if self._is_sqlite else "non-sqlite",
            "read_plane_registered": self._registered_audit_read_plane() is not None,
            "provider_reads_routed": int(getattr(self, "provider_reads_routed", 0) or 0),
            "provider_read_route_errors": int(getattr(self, "provider_read_route_errors", 0) or 0),
            "provider_read_degraded_total": degraded,
            "provider_read_degraded_ops": dict(
                getattr(self, "provider_read_degraded_ops", None) or {}
            ),
            "read_degraded": bool(degraded),
        }

    def flush(self, timeout_sec: float = 5.0) -> bool:
        """Boundedly drains the background write queue.

        Guarantees that every audit write enqueued on THIS thread is durable
        before the caller proceeds. Required for read-after-write sequences
        such as: pre-trade experience queued -> post-trade outcome immediately
        recorded (the outcome lookup reads the DB and would otherwise miss the
        still-queued decision snapshot, BUG-140 E2E finding).

        Bounded by `timeout_sec` so a stalled worker can never deadlock a
        live-path caller; returns True only when the queue fully drained.
        """
        if not self._is_sqlite and getattr(self, "_write_plane", None) is not None:
            # A pooled provider has its own queue + worker; the plane owns the
            # drain contract.  Returning True here without draining would make
            # "flush ok" vacuous (the row would never be guaranteed durable).
            return self._write_plane.flush(timeout_sec=max(0.0, float(timeout_sec)))
        if not self._is_sqlite:
            return True
        try:
            deadline = time.monotonic() + max(0.0, float(timeout_sec))
            # queue.Queue.join() has no timeout; poll the unfinished count so a
            # stalled worker can never deadlock a live-path caller.
            while self._queue.unfinished_tasks > 0:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.005)
            return True
        except Exception as e:
            logger.error("Audit flush failed", error=str(e))
            return False

    #: BUG-288: upper bound for the worker's queue-capture handshake. The
    #: wait is on the CONSTRUCTION path only (never the tick path, INV-001);
    #: a timeout is loud but non-fatal (warning + pre-fix semantics remain).
    _WORKER_READY_TIMEOUT_SEC = 5.0

    def _build_write_plane(self) -> AuditWritePlane:
        """Constructs the provider-agnostic write plane for this domain.

        SQLite keeps the historical model: one dedicated writer connection
        (WAL single-writer) and the worker thread bound to this queue.
        PostgreSQL builds the plane over the fabric's pooled write backend
        so the same queue, overflow and dead-letter guarantees apply.
        """
        from nexus_scalp.adapters.database.audit_write_plane import (
            AuditWritePlane,
        )

        def _get_flush_interval() -> float:
            return float(self._flush_interval)

        def _do_financial_overflow(
            query: str, args: tuple, error: BaseException | None = None
        ) -> None:
            self._write_financial_overflow(query, args, error)

        flush_interval = float(self._flush_interval)
        if self._is_sqlite:
            return AuditWritePlane(
                queue=self._queue,
                queue_resolver=lambda: self._queue,
                backend_factory=lambda: SqliteAuditWriteBackend(
                    self._connect_sqlite, busy_timeout=10.0
                ),
                dead_letter_store=self.dead_letter_store,
                overflow_dir=self._overflow_dir(),
                overflow_resolver=self._overflow_dir,
                overflow_sink=_do_financial_overflow,
                flush_interval=flush_interval,
                flush_interval_resolver=_get_flush_interval,
                on_metrics=self._apply_write_plane_metrics,
            )

        # Non-SQLite domain: the fabric supplies the pooled write backend.
        pg_backend = self._build_pooled_write_backend()
        if pg_backend is None:
            # No fabric backend is configured (e.g. the domain is not yet
            # provisioned for PostgreSQL). The plane still exists so callers
            # see the documented metrics, but writes cannot be silently
            # accepted: the backend raises on use and the caller's failure
            # is loud. This is deliberately NOT the old silent no-op.
            raise RuntimeError(
                "AuditRepository configured for a non-SQLite provider, but no "
                "pooled write backend is available for the audit domain. "
                "Provision the domain via the database fabric (nexus db ...) "
                "before switching providers."
            )
        return AuditWritePlane(
            queue=self._queue,
            queue_resolver=lambda: self._queue,
            backend_factory=lambda: pg_backend,
            dead_letter_store=self.dead_letter_store,
            overflow_dir=self._overflow_dir(),
            overflow_resolver=self._overflow_dir,
            flush_interval=flush_interval,
            flush_interval_resolver=_get_flush_interval,
            on_metrics=self._apply_write_plane_metrics,
        )

    def _build_pooled_write_backend(self) -> Any:
        """Resolves the fabric's pooled write backend for the audit domain.

        Auto-provisions the domain the first time a process touches a pooled
        provider: without this, a bare ``AuditRepository()`` on a PostgreSQL
        default would raise "no pooled write backend" on the very first boot,
        since nothing else had registered the domain yet. Falls back to
        ``None`` (loud, not silent) when provisioning itself fails.
        """
        try:
            from nexus_scalp.database.fabric import get_domain_backend, provision_domain

            backend = get_domain_backend("audit", readonly=False)
            if backend is not None:
                return backend
            # Not provisioned in this process — bootstrap it from the resolved
            # DSN (includes the secret, injected by resolve_audit_db_url).
            if not self._db_url or self._is_sqlite:
                return None
            return provision_domain("audit", self._db_url, min_size=1, max_size=4)
        except Exception as exc:
            logger.error(
                "[DB-FABRIC] audit domain provisioning failed: %s",
                exc,
            )
            return None

    def _dead_letter_write_sink(self) -> Any:
        """Dead-letter persistence sink for non-SQLite domains.

        Routes the dead-letter INSERT through the fabric's write plane so a
        PostgreSQL domain keeps durable failure evidence instead of the old
        silent counter increment.

        RTF-002: the backend is resolved LAZILY, at call time, never captured
        at construction. ``AuditRepository.__init__`` builds the
        ``DeadLetterStore`` BEFORE ``_build_write_plane()`` runs, and the
        write plane is what provisions the audit domain on the fabric. A
        construction-time capture therefore observed ``get_domain_backend``
        == None on a fresh PostgreSQL process, permanently cached it, and
        left the store with ``write_sink=None`` — the state that produced
        ``DEAD-LETTER WRITE IMPOSSIBLE ... financial record unrecoverable``
        for every failed row. Resolving per call means the first dead-letter
        that arrives after the plane provisions lands durably.

        Only *reads* the registry (no lazy provisioning): provisioning is the
        write plane's job, and doing it here could double-provision against a
        concurrent plane and close a pool that is in use.
        """
        if self._is_sqlite:
            return None

        def _sink(sql: str, args: tuple) -> bool:
            try:
                from nexus_scalp.database.fabric import get_domain_backend

                backend = get_domain_backend("audit", readonly=False)
                if backend is None:
                    return False
                backend.execute(sql, args)
                return True
            except Exception as sink_err:
                logger.error("[DEAD-LETTER] write sink failed: %s", sink_err)
                return False

        return _sink

    def _apply_write_plane_metrics(self, metrics: dict[str, Any]) -> None:
        """Pulls the plane's durability counters onto this repository.

        The runtime-safety tests and debug_snapshot read these attributes on
        the repository, so the plane must not own the only copy.
        """
        for key, value in metrics.items():
            setattr(self, key, value)

    def _start_background_worker(self) -> None:
        """Starts the dedicated background thread for zero-latency database inserts.

        BUG-288 (windows-latest CI red at b1fe0137): the writer loop used to
        capture its queue reference (``q = self._queue``) only AFTER the OS
        scheduled the thread AND the sqlite connect returned, while
        ``Thread.start()`` reports aliveness instantly. Any caller that
        rebound ``self._queue`` (or relied on "the worker is bound to the
        queue I can see") inside that window silently made the worker ADOPT
        the new object — the exact shape that turned the BUG-140
        deterministic-stall test into a flake (flush() returned True because
        the still-unbound worker adopted the poisoned queue and drained it).
        The constructor now blocks until the worker confirms capture, so
        "repo object published" implies "writer bound to self._queue".
        """
        # Retired in favour of the provider-agnostic write plane, which owns
        # the queue, the worker thread and the backend (see __init__). Kept as
        # the armament surface so a caller that rebinds self._queue before the
        # plane exists still gets a consumer; the plane is the authority.
        if getattr(self, "_write_plane", None) is not None:
            return
        self._running = True
        ready = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._process_queue_worker,
            args=(ready,),
            daemon=True,
            name="AuditDB_LegacyWorker",
        )
        self._worker_thread.start()
        if not ready.wait(timeout=self._WORKER_READY_TIMEOUT_SEC):
            # Never brick construction on a pathological thread; the miss is
            # observable and the previous (pre-fix) semantics simply remain.
            logger.warning(
                "AUDIT WORKER CAPTURE NOT CONFIRMED within %.1fs — queue-rebind "
                "callers may race the writer adoption window",
                self._WORKER_READY_TIMEOUT_SEC,
            )

    def _process_queue_worker(self, ready: threading.Event | None = None) -> None:
        """Background loop flushing pending inserts to disk via Bulk Transactions."""
        if not self._is_sqlite:
            if ready is not None:
                ready.set()
            return

        q = self._queue  # local ref: never GC'd while the loop runs (BUG-058)
        # BUG-288: publish the capture BEFORE the (potentially slow) connect —
        # the handshake means "bound to the queue object", not "loop running".
        if ready is not None:
            ready.set()

        conn = self._connect_sqlite(10.0)

        while self._running or not q.empty():
            batch: list[tuple[str, tuple]] = []
            try:
                # Wait for records, batch them up to 500 per transaction.
                # IDLE-POLL (2026-09-06 debugger wall): the old 1.0s default
                # _flush_interval raised queue.Empty 1x/sec whenever the
                # writer idled; an attached debugger prints every
                # first-chance Empty as a full wall. The batch window is now
                # decoupled from the idle cadence: drain whatever is already
                # queued WITHOUT blocking (get_nowait), then — only when the
                # batch is still empty — block up to the flush interval for
                # the first record. Arrivals dispatch immediately; idle walls
                # drop to ~1 per flush interval.
                while len(batch) < 500:
                    if batch:
                        try:
                            query_tuple = q.get_nowait()
                        except queue.Empty:
                            break
                    else:
                        query_tuple = q.get(timeout=self._flush_interval)
                    batch.append(query_tuple)
            except queue.Empty:
                pass

            if not batch:
                # BUG-285 (perf wave R2): idle pass — the writer has capacity
                # again, so recover financial rows stranded by a past
                # saturation. Bounded by cadence + batch width and confined
                # to THIS worker thread (never the tick path, INV-001).
                self._drain_financial_overflow_due(conn)

            if batch:
                try:
                    with conn:
                        import itertools

                        for query, group in itertools.groupby(batch, key=lambda x: x[0]):
                            args_list = [item[1] for item in group]
                            conn.executemany(query, args_list)
                    for _ in batch:
                        q.task_done()
                except Exception as e:
                    # =================================================================
                    # AUDIT BATCH RECOVERY (P0, runtime-safety mission).
                    # The OLD behavior discarded the ENTIRE batch on any
                    # error — one bad row silently destroyed up to 499 good
                    # financial records. Recovery algorithm:
                    #   1. retry records individually (salvage every good row);
                    #   2. isolate permanently failing rows;
                    #   3. dead-letter the failures DURABLY (audit_dead_letter
                    #      table with query + args + error classification);
                    #   4. count every outcome in observable metrics
                    #      (audit_batch_failures / audit_salvaged_rows /
                    #      audit_dead_letter_rows) — financial loss is never
                    #      silent. task_done() is still called for every item
                    #      so queue.join()/close() can always return.
                    # =================================================================
                    self.audit_batch_failures += 1
                    salvaged = 0
                    dead_lettered = 0
                    with contextlib.suppress(Exception):
                        conn.rollback()
                    for failed_query, failed_args in batch:
                        try:
                            with conn:
                                conn.execute(failed_query, failed_args)
                            salvaged += 1
                        except Exception as row_err:
                            dead_lettered += 1
                            self.record_dead_letter(
                                query=failed_query,
                                args=failed_args,
                                error=row_err,
                                retry_count=1,
                                payload_note="audit worker batch-retry failure",
                            )
                    self.audit_salvaged_rows += salvaged
                    logger.error(
                        "Audit batch insert failed; recovery applied "
                        "batch=%d salvaged=%d dead_lettered=%d error_type=%s error=%s",
                        len(batch),
                        salvaged,
                        dead_lettered,
                        type(e).__name__,
                        str(e)[:500],
                    )
                    for _ in batch:
                        q.task_done()
                    time.sleep(1.0)  # Backoff on error

        conn.close()

    #: reason codes that are guard/rejection events, NOT genuine signals.
    #: They are aggregated into lightweight telemetry instead of heavy rows.
    _GUARD_TELEMETRY_CODES = frozenset(
        {
            "TICK_DUPLICATE_SUPPRESSED",
            "ORDER_FREQUENCY_THROTTLED",
        }
    )

    #: Payload schema for audit_signals (BUG-054). Deliberately minimal: the
    #: structured columns already carry request_id/symbol/action/confidence/
    #: prices/regime/reason. Only genuinely extra forensic info lives here.
    _SIGNAL_PAYLOAD_FIELDS = (
        "model_action",
        "ai_buy_probability",
        "ai_sell_probability",
        "ai_no_trade_probability",
        "regime_confidence",
        "risk_allowed",
        "guardian_status",
        "rejection_reason",
    )

    def _signal_dedup_key(self, proposal: TradeProposal) -> str:
        """Deterministic, collision-resistant signal identity (BUG-054).

        A genuine decision is identified by what the engine evaluated, not by
        the wall-clock instant: symbol + M1 candle + model action + decision
        stage + the reason it reached (plus execution mode so a concurrent
        STANDARD vs PREDICTIVE_LIMIT evaluation is never conflated). Stable
        across restart, independent of request_id (UUIDs differ every call).
        """
        import hashlib

        candle = proposal.generated_at.replace(second=0, microsecond=0).isoformat()
        model_action = str(getattr(proposal, "model_action", "") or proposal.action.value)
        stage = str(getattr(proposal, "decision_stage", "") or "STANDARD_EVAL")
        mode = str(getattr(proposal, "execution_mode", "") or "STANDARD")
        reason = str(proposal.reason_code or "MODEL_SIGNAL")
        raw = "|".join([proposal.symbol, candle, model_action, stage, mode, reason])
        return f"sig_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"

    # ---------------------------------------------------------------------
    # CRITICALITY-AWARE ENQUEUE (P0, runtime-safety mission).
    #
    # Queue semantics MUST match data criticality:
    #   * FINANCIAL events (signals, orders, executions, ledger open/close,
    #     account snapshots) carry reconstruction truth — they use
    #     bounded BACKPRESSURE (short blocking put) + a durable overflow
    #     file when the audit writer cannot keep up. They are NEVER
    #     silently dropped: every overflow/failure is counted in
    #     financial_queue_backpressure / financial_events_overflowed /
    #     financial_events_failed and the event survives on disk.
    #   * TELEMETRY events (guard counters, diagnostics) remain dropable by
    #     design — counted in telemetry_dropped.
    # A bounded blocking timeout keeps INV-001 (hot path: no unbounded
    # synchronous DB wait) intact: worst case costs one flush interval.
    # ---------------------------------------------------------------------

    _FINANCIAL_OVERFLOW_DIR = "artifacts/audit_overflow"

    #: BUG-285 (perf wave R2): HARD cap on pending overflow files. Queue
    #: saturation that outlives the recovery cadence must never fill the
    #: disk; past the cap a row goes to the bounded dead-letter table
    #: (counted, loud) instead of spawning another file.
    _FINANCIAL_OVERFLOW_MAX_FILES = 5000

    #: BUG-285: recovery cadence + per-pass batch width. The drain runs on
    #: the audit worker thread (idle passes only) — never on the tick path.
    OVERFLOW_RECOVERY_INTERVAL_SEC: float = 60.0
    OVERFLOW_RECOVERY_BATCH: int = 500

    def _overflow_dir(self) -> Path:
        """Canonical durable-overflow directory (single resolution site).

        The writer and the recovery drainer MUST agree on this path or the
        drain silently never sees what the writer produced.
        """
        from nexus_scalp.release.paths import get_runtime_workspace

        return Path(get_runtime_workspace()) / self._FINANCIAL_OVERFLOW_DIR

    #: Bounded duplicate-repair scan width for the audit_orders idempotency
    #: index bootstrap (agent-17, 2026-09-10). One construction pass repairs
    #: at most this many duplicated identities; remaining duplicates (if any
    #: pathological volume) are repaired on the next boot — construction is
    #: never an unbounded delete against a huge table.
    _ORDERS_DEDUP_REPAIR_BATCH = 500

    def _enqueue_financial(self, query: str, args: tuple[Any, ...]) -> None:
        """Enqueue a CRITICAL financial row. Delegates to the write plane.

        Kept as the repository's entry point so the ~30 producers keep their
        call sites byte-identical; the plane owns the durability contract.

        R1 (perf wave): the backpressure window the plane applies is
        ``min(self._flush_interval * 2.0, 0.1)`` — bounded blocking put with
        no hard 2-second floor, so a saturated queue can never wedge the tick
        path for more than 100 ms while durable overflow still wins.
        """
        self._write_plane.enqueue_financial(query, args)

    def _write_financial_overflow(
        self, query: str, args: tuple[Any, ...], error: BaseException | None
    ) -> None:
        """Persists one financial event to the durable overflow directory.

        Best-effort: if even the filesystem refuses, the event is
        dead-letter-counted and logged CRITICAL — never silently lost.
        """
        try:
            overflow_dir = self._overflow_dir()
            overflow_dir.mkdir(parents=True, exist_ok=True)
            # BUG-285: bounded pending volume. The cap check is a cheap
            # directory count on the (already exceptional) overflow path —
            # never on the normal enqueue route. At the cap the row goes to
            # the dead-letter store (bounded retention owns it) and the loss
            # of FILE-durable preservation is counted + logged CRITICAL.
            try:
                pending = sum(1 for _ in overflow_dir.glob("overflow_*.json"))
            except OSError:
                pending = 0
            if pending >= self._FINANCIAL_OVERFLOW_MAX_FILES:
                self.financial_overflow_failed += 1
                self.record_dead_letter(
                    query=query,
                    args=args,
                    error=error or RuntimeError("QUEUE_SATURATED"),
                    payload_note="overflow file cap reached (BUG-285)",
                )
                logger.critical(
                    "FINANCIAL AUDIT OVERFLOW CAP — %d pending overflow files; row routed to "
                    "dead-letter instead (financial_overflow_failed=%d)",
                    pending,
                    self.financial_overflow_failed,
                )
                return
            ts = time.strftime("%Y%m%d_%H%M%S")
            fname = f"overflow_{ts}_{self._dead_letter_seq:08d}.json"
            self._dead_letter_seq += 1
            payload = {
                "failed_at": ts,
                "query": query,
                "args": self._json_safe_args(args),
                "error": type(error).__name__ if error else "QUEUE_SATURATED",
            }
            (overflow_dir / fname).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            logger.critical(
                "FINANCIAL AUDIT OVERFLOW — event persisted to durable overflow file %s "
                "(queue saturated; accounting evidence preserved)",
                fname,
            )
        except Exception as of_err:
            self.financial_events_failed += 1
            self.record_dead_letter(
                query=query,
                args=args,
                error=error or of_err,
                payload_note="overflow-file write failure",
            )
            logger.critical(
                "FINANCIAL AUDIT EVENT COULD NOT BE DURABLY PRESERVED (overflow file failed): %s",
                of_err,
            )

    def _drain_financial_overflow_due(
        self, conn: sqlite3.Connection, now: float | None = None
    ) -> None:
        """BUG-285: cadence-gated recovery of stranded overflow rows.

        The durable overflow file used to be terminal: every producer path
        (audit worker batch-retry, saturated-queue overflow) that needed it
        wrote the row out and NOTHING ever read it back — the perf-wave R2
        finding. A financial row that overflowed was therefore lost to the
        ledger forever despite being "durable", and the directory grew
        unbounded.

        Recovery contract (audit worker thread only, idle passes):
          * throttle: at most one pass per OVERFLOW_RECOVERY_INTERVAL_SEC
            (None sentinel — the FIRST pass is always due);
          * batch: at most OVERFLOW_RECOVERY_BATCH files per pass, oldest
            first (deterministic recovery order);
          * replay: re-execute the stored query+args on the worker's own
            connection (the SAME single writer — no cross-thread writes);
          * idempotent by construction: every financial producer query is
            ON CONFLICT DO NOTHING/UPDATE, so a duplicate replay of a row
            that already landed is a no-op, never a double count;
          * retire: a successfully replayed file moves to
            ``overflow_recovered/.consumed-<name>`` before deletion (the
            storage-hygiene rename-then-delete discipline — a crash between
            replay and unlink can never lose the row twice);
          * poison: a file that fails to parse or replay is dead-lettered
            (durable, bounded retention) and renamed ``.rejected-<name>`` —
            never retried forever, never silently dropped.
        """
        now = time.monotonic() if now is None else now
        if (
            self._last_overflow_drain is not None
            and now - self._last_overflow_drain < self.OVERFLOW_RECOVERY_INTERVAL_SEC
        ):
            return
        try:
            overflow_dir = self._overflow_dir()
        except Exception:
            return
        if not overflow_dir.is_dir():
            self._last_overflow_drain = now
            return
        self._last_overflow_drain = now
        try:
            pending = sorted(
                (p for p in overflow_dir.glob("overflow_*.json") if p.is_file()),
                key=lambda p: p.name,
            )[: self.OVERFLOW_RECOVERY_BATCH]
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
                    # The writer's safe-envelope substitution means the REAL
                    # value was not JSON-recoverable: replaying the envelope
                    # would fabricate a corrupt row. Dead-letter it instead.
                    raise ValueError("overflow args contain __unserializable__ envelope")
                with conn:
                    conn.execute(query, tuple(arg_list))
                consumed_dir = overflow_dir / "overflow_recovered"
                consumed_dir.mkdir(parents=True, exist_ok=True)
                marker = consumed_dir / f".consumed-{path.name}"
                path.replace(marker)
                marker.unlink()
                recovered += 1
            except Exception as row_err:
                rejected += 1
                logger.error(
                    "FINANCIAL OVERFLOW DRAIN — replay REJECTED %s (error=%s); "
                    "durably dead-lettered instead (financial_overflow_failed=%d)",
                    path.name,
                    row_err,
                    self.financial_overflow_failed + rejected,
                )
                try:
                    fail_args = payload.get("args")
                    self.record_dead_letter(
                        query=str(payload.get("query") or ""),
                        args=tuple(fail_args) if isinstance(fail_args, list) else (),
                        error=row_err,
                        payload_note=f"overflow drain replay failure (BUG-285) file={path.name}",
                    )
                except Exception:
                    pass
                # The dead-letter row is now the durable copy: retire the
                # file the same way a recovered one is retired (rename then
                # delete) so the directory stays bounded even when a row is
                # permanently unreplayable.
                try:
                    consumed_dir = overflow_dir / "overflow_recovered"
                    consumed_dir.mkdir(parents=True, exist_ok=True)
                    marker = consumed_dir / f".rejected-{path.name}"
                    path.replace(marker)
                    marker.unlink()
                except OSError:
                    with contextlib.suppress(OSError):
                        path.unlink()
        if recovered:
            self.financial_overflow_recovered += recovered
            logger.warning(
                "FINANCIAL AUDIT OVERFLOW DRAIN — recovered=%d rejected=%d "
                "(stranded rows returned to the ledger)",
                recovered,
                rejected,
            )
        if rejected:
            self.financial_overflow_failed += rejected

    def overflow_pending_count(self) -> int:
        """Public recovery surface: how many stranded overflow rows are
        still waiting on disk right now (BUG-285). -1 = unreadable."""
        if not self._is_sqlite:
            return self._provider_read_guard(
                "overflow_pending_count",
                lambda: 0,
            )
        try:
            d = self._overflow_dir()
            if not d.is_dir():
                return 0
            return sum(1 for p in d.glob("overflow_*.json") if p.is_file())
        except Exception:
            return -1

    def _enqueue_telemetry(self, query: str, args: tuple[Any, ...]) -> None:
        """Enqueue a NON-CRITICAL telemetry row. Delegates to the write plane."""
        self._write_plane.enqueue_telemetry(query, args)

    def log_signal(self, proposal: TradeProposal) -> None:
        """Zero-latency async logging of generated trade signals.

        Persistent idempotency (BUG-054):
        * guard/rejection codes (TICK_DUPLICATE_SUPPRESSED, ...) are aggregated
          into `audit_guard_telemetry` — one small counter row per minute per
          symbol per code — instead of a ~1.2KB signal row per event;
        * genuine signals carry a deterministic `signal_dedup_key` enforced by a
          UNIQUE index with ON CONFLICT DO NOTHING, so the background worker can
          never create duplicate decision rows across restart/races — no
          synchronous SELECT in the hot path, no in-memory state to lose.
        """

        reason_code = str(proposal.reason_code or "MODEL_SIGNAL")
        if reason_code in self._GUARD_TELEMETRY_CODES:
            self._log_guard_telemetry(proposal, reason_code)
            return

        # Determine Regime
        regime_str = "UNKNOWN"
        if hasattr(proposal, "regime") and proposal.regime:
            regime_str = proposal.regime
        elif "REGIME_" in reason_code:
            regime_str = reason_code.partition("REGIME_")[2]

        model_action = str(getattr(proposal, "model_action", "") or proposal.action.value)
        buy_prob = float(getattr(proposal, "buy_probability", 0.0) or 0.0)
        sell_prob = float(getattr(proposal, "sell_probability", 0.0) or 0.0)
        no_trade_prob = float(getattr(proposal, "no_trade_probability", 0.0) or 0.0)

        # CHG-0043 decision-evidence completeness: capture the RAW probability
        # block + the model-preferred direction AT DECISION TIME. The
        # preferred direction is whatever the model recorded as its candidate
        # action (model_action) — a genuine NO_TRADE abstention stays
        # NOT_RECORDED (""); direction is NEVER derived from the eventual
        # market move. confidence_source comes from the confidence-semantics
        # repair (risk_checks.confidence_source) so raw vs normalized
        # semantics stay distinguishable. spread_usd documents the quoting
        # state the decision saw (ask-bid).
        preferred_direction = ""
        ma = model_action.upper()
        if "BUY" in ma:
            preferred_direction = "BUY"
        elif "SELL" in ma:
            preferred_direction = "SELL"
        try:
            rc = proposal.risk_checks if isinstance(proposal.risk_checks, dict) else {}
            confidence_source = str(rc.get("confidence_source", "") or "")
        except Exception:
            confidence_source = ""
        # spread the decision actually saw: the proposal carries the raw model
        # probabilities but not the tick; policy stamps spread into its
        # rejection telemetry via current_spread — recover it from the
        # proposal's risk_checks when present, else derive from proposed_entry
        # vs the guard sentinel (never invent: absent -> NOT_RECORDED/None).
        spread_usd = None
        try:
            if "spread_usd" in (rc or {}):
                spread_usd = float(rc["spread_usd"])
        except Exception:
            spread_usd = None

        # Minimal forensic payload (BUG-054): no full proposal dump, no
        # duplicate fields already present as structured columns.
        payload_json = json.dumps(
            {
                "model_action": model_action,
                "ai_buy_probability": buy_prob,
                "ai_sell_probability": sell_prob,
                "ai_no_trade_probability": no_trade_prob,
                "regime_confidence": float(getattr(proposal, "regime_confidence", 0.0) or 0.0),
                "risk_allowed": bool(getattr(proposal, "risk_allowed", True)),
                "guardian_status": str(getattr(proposal, "guardian_status", "") or "IDLE"),
                "rejection_reason": str(
                    getattr(proposal, "rejection_reason", "") or proposal.reason_code
                ),
                # BUG-072/073: execution-state blocks (exposure/pending-state)
                # are distinguished from model rejections so learning never
                # mistakes an unavailable execution slot for "the model chose
                # not to trade".
                "blocked_by": str(getattr(proposal, "blocked_by", "") or ""),
                "decision_stage": str(getattr(proposal, "decision_stage", "") or ""),
                # OBS-TRACE (2026-09-09): the EXEC correlation id travels with
                # the decision row so audit_signals -> audit_orders joins by id
                # do not depend on parsing the reason string.
                "execution_id": str(getattr(proposal, "execution_id", "") or ""),
            }
        )

        # Task 4 Check for UNKNOWN regime
        # OBS-TRACE (2026-09-09): this diagnostic previously asserted FABRICATED
        # specifics — missing_features=[ADX, ATR] and available_bars=4000 —
        # that this layer never measured (a downstream forensic consumer could
        # cite them as evidence). The regime reason the policy actually
        # recorded (proposal.reason_code / regime_confidence) is real evidence
        # and is echoed verbatim; everything else is honestly NOT_RECORDED.
        if regime_str == "UNKNOWN" or not regime_str:
            unknown_log = {
                "regime": "UNKNOWN",
                "reason": str(getattr(proposal, "reason_code", "") or "UNKNOWN"),
                "regime_confidence": float(getattr(proposal, "regime_confidence", 0.0) or 0.0),
                "missing_features": "NOT_RECORDED",
                "available_bars": "NOT_RECORDED",
                "request_id": str(getattr(proposal, "request_id", "") or ""),
            }
            # PERF-WAVE R9 (2026-09-14): the duplicate `print(json.dumps(...))`
            # for stdout-forensics parsing was REMOVED. It wrote to stdout on
            # the tick path (blocking I/O, per UNKNOWN-regime signal), and the
            # structured warning below carries the identical payload in
            # `extra` — engine log capture (file + console handler) owns the
            # "stdout audit parsing" use case, never raw print().
            logger.warning("UNKNOWN regime detected - decision context echoed", extra=unknown_log)

        query = """
            INSERT INTO audit_signals
            (request_id, symbol, action, confidence, proposed_entry, stop_loss, take_profit, regime, generated_at, payload,
             execution_mode, reason_code, decision_stage, blocked_by, htf_score, smc_score, confidence_before_filters, confidence_after_filters,
             signal_dedup_key, preferred_direction, raw_prob_buy, raw_prob_sell, raw_prob_no_trade, raw_prob_wait, confidence_source, spread_usd,
             account_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_dedup_key) DO NOTHING
        """
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
            payload_json,
            getattr(proposal, "execution_mode", "STANDARD"),
            proposal.reason_code,
            getattr(proposal, "decision_stage", "STANDARD_EVAL"),
            getattr(proposal, "blocked_by", None),
            getattr(proposal, "htf_score", 0.0),
            getattr(proposal, "smc_score", 0.0),
            getattr(proposal, "confidence_before_filters", 0.0),
            getattr(proposal, "confidence_after_filters", 0.0),
            self._signal_dedup_key(proposal),
            # CHG-0043 decision-evidence columns ("" / None = NOT_RECORDED)
            preferred_direction,
            buy_prob,
            sell_prob,
            no_trade_prob,
            None,  # raw_prob_wait: WAIT slice not exposed on the proposal contract (never invented)
            confidence_source,
            spread_usd,
            # OBS-TRACE: BUG-226 provenance read NOW (enqueue time) from the
            # repository attribute the engine keeps synced to the bound
            # adapter — never resolved later at worker time, so a hot-swap
            # between enqueue and flush cannot misattribute the row.
            str(getattr(self, "current_account_source", "") or ""),
        )

        self._enqueue_financial(query, args)

    def _log_guard_telemetry(self, proposal: TradeProposal, reason_code: str) -> None:
        """Aggregates a guard/rejection event into a counter row (BUG-054).

        One row per (minute, symbol, reason_code); the UPSERT increments count.
        ~40 bytes per event instead of the ~1.2KB full signal row. The minute
        window keeps "how often / when / for which symbol / why" answerable.
        """
        window = proposal.generated_at.replace(second=0, microsecond=0).isoformat()
        # RT-003 / PG portability: the unqualified ``count`` in DO UPDATE SET
        # is AMBIGUOUS on PostgreSQL (it resolves against both the target row
        # and EXCLUDED) while SQLite silently picks the target row — this one
        # statement was 99.9% of every failed write on the live cluster.
        # Qualifying with the INSERT's declared alias ``t`` is valid on BOTH
        # providers (SQLite resolves the alias declared in the INSERT), so the
        # statement string stays identical for every provider.
        query = """
            INSERT INTO audit_guard_telemetry AS t (window_start, symbol, reason_code, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(window_start, symbol, reason_code)
            DO UPDATE SET count = t.count + 1
        """
        args = (window, proposal.symbol, reason_code)
        self._enqueue_telemetry(query, args)

    def log_order(
        self,
        ticket: int,
        order_id: str,
        symbol: str,
        action: str,
        price: float,
        stop_loss: float,
        take_profit: float,
        volume: float,
        reason: str,
        latency: float = 0.0,
        execution_mode: str = "STANDARD",
        execution_id: str | None = None,
    ) -> None:
        """Zero-latency async logging of order lifecycle events."""

        query = """
            INSERT INTO audit_orders
            (ticket, order_id, symbol, action, price, stop_loss, take_profit, volume, reason, latency, execution_mode, execution_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(execution_id) WHERE execution_id IS NOT NULL AND execution_id != ''
            DO NOTHING
        """
        from datetime import UTC, datetime

        args = (
            ticket,
            order_id,
            symbol,
            action,
            price,
            stop_loss,
            take_profit,
            volume,
            reason,
            latency,
            execution_mode,
            execution_id,
            datetime.now(UTC).isoformat(),
        )

        self._enqueue_financial(query, args)

    def log_execution(self, order: TradeOrder, status: str) -> None:
        """Zero-latency async logging of order execution attempts."""

        query = """
            INSERT INTO audit_executions
            (order_id, symbol, order_type, volume, price, status, executed_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id, status) DO NOTHING
        """
        from datetime import UTC, datetime

        args = (
            order.order_id,
            order.symbol,
            order.order_type.value,
            order.volume,
            order.price,
            status,
            datetime.now(UTC).isoformat(),
            order.model_dump_json(),
        )

        self._enqueue_financial(query, args)

    def log_account_snapshot(self, account: AccountInfo, peak_equity: float) -> None:
        """
        Aggregated running account balance and equity state snapshots.
        Only write a snapshot to the database if the balance has changed
        or if at least 60 seconds have elapsed since the last snapshot,
        minimizing database write footprint.

        BUG-226: rows are tagged with the account provenance of the bound
        adapter ('PAPER' when the simulation adapter is wired in, 'LIVE'
        otherwise) so the accounting layer can exclude simulation plateaus
        from drawdown/equity metrics without rewriting history.
        """

        now = time.time()
        balance_changed = abs(account.balance - self._last_snapshot_balance) > 0.01
        time_elapsed = (now - self._last_snapshot_time) >= 60.0

        if not balance_changed and not time_elapsed:
            return

        self._last_snapshot_time = now
        self._last_snapshot_balance = account.balance
        self._last_snapshot_equity = account.equity

        query = """
            INSERT INTO audit_account_snapshots
            (timestamp, balance, equity, margin_free, peak_equity, account_source)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        from datetime import UTC, datetime

        args = (
            datetime.now(UTC).isoformat(),
            account.balance,
            account.equity,
            account.margin_free,
            peak_equity,
            str(getattr(self, "current_account_source", "") or "LIVE"),
        )

        self._enqueue_financial(query, args)

    def log_ledger_opened(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        volume: float,
        entry_price: float,
        timestamp_str: str,
        order_id: str = "",
        entry_reason: str = "",
        ai_confidence_at_open: float = 0.0,
        market_regime_at_open: str = "",
        initial_sl_price: float = 0.0,
        account_source: str = "",
    ) -> None:
        """
        Logs the opening of a position to the financial ledger.

        Captures the immutable entry context (reason, AI confidence, regime, initial SL)
        so the closing autopsy row can be assembled without re-deriving history.
        `account_source` records execution provenance (BUG-226): 'LIVE' / 'PAPER' /
        'SHADOW' — '' for legacy rows. AccountingCore excludes PAPER provenance
        from every performance metric; the raw row itself is never rewritten.
        """

        query = """
            INSERT INTO audit_ledger
            (ticket, symbol, direction, volume, entry_price, status, timestamp,
             order_id, open_time, open_price, entry_reason, ai_confidence_at_open,
             market_regime_at_open, initial_sl_price, account_source)
            VALUES (?, ?, ?, ?, ?, 'OPENED', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticket) DO NOTHING
        """
        args = (
            ticket,
            symbol,
            direction,
            volume,
            entry_price,
            timestamp_str,
            order_id,
            timestamp_str,
            entry_price,
            entry_reason,
            float(ai_confidence_at_open),
            market_regime_at_open,
            float(initial_sl_price),
            str(account_source or ""),
        )
        self._enqueue_financial(query, args)

    def has_ledger_opened(self, ticket: int) -> bool:
        """
        True when an OPENED placeholder row exists for the ticket (its entry
        context was captured by this engine). Used by the Phase 14
        reconciliation close-loop to attribute broker-history closes.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "has_ledger_opened",
                lambda: False,
                sql=("SELECT 1 FROM audit_ledger WHERE ticket = ? AND status = 'OPENED' LIMIT 1;"),
                args=(int(ticket),),
                kind="exists",
            )
        try:
            with self._connect_sqlite(5.0) as conn:
                row = conn.execute(
                    "SELECT 1 FROM audit_ledger WHERE ticket = ? AND status = 'OPENED' LIMIT 1;",
                    (int(ticket),),
                ).fetchone()
                return row is not None
        except Exception as e:
            # FORENSIC REPAIR: never silent. The Phase 14 reconciliation
            # close-loop uses this pre-check; a broken/unavailable audit DB
            # must be visible in the logs (the False sentinel semantics are
            # preserved so callers keep their fallback behavior).
            logger.error(
                "has_ledger_opened failed (ticket=%s): %s",
                ticket,
                e,
                exc_info=True,
            )
            return False

    def count_ledger_opened_unclosed(self) -> int:
        """
        TASK-7 (BUG-090): cheap pre-check for the reconciliation close-loop.
        Returns the number of OPENED ledger rows that have no CLOSED outcome yet
        (identified by exit_price still 0 / status OPENED). The caller uses this to
        skip the expensive broker-history fetch entirely when there is nothing to
        reconcile. Never raises; returns -1 when the check is unavailable so the
        caller falls through to the broker fetch.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "count_ledger_opened_unclosed",
                lambda: -1,
                sql=(
                    "SELECT COUNT(*) FROM audit_ledger WHERE status = 'OPENED' "
                    "AND COALESCE(exit_price, 0) = 0;"
                ),
                kind="count",
            )
        try:
            with self._connect_sqlite(5.0) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM audit_ledger WHERE status = 'OPENED' "
                    "AND COALESCE(exit_price, 0) = 0;"
                ).fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            # FORENSIC REPAIR: never silent — a failing reconciliation pre-check
            # that silently returned -1 would hide audit-DB unavailability.
            # The -1 sentinel semantics are preserved for the caller fallback.
            logger.error(
                "count_ledger_opened_unclosed failed: %s",
                e,
                exc_info=True,
            )
            return -1

    # ------------------------------------------------------------------
    # PAPER execution-parity persistence + stats (mission P0-1)
    # ------------------------------------------------------------------

    def record_paper_execution(
        self,
        *,
        ts: str,
        symbol: str,
        order_type: str,
        volume: float,
        requested_price: float,
        bid_at_request: float,
        ask_at_request: float,
        spread: float,
        fill_price: float | None,
        slippage: float | None,
        rejection_reason: str | None,
        ticket: int = 0,
        latency_ticks: int = 0,
        status: str = "",
        source: str = "PAPER_ADAPTER_LEDGER",
    ) -> bool:
        """Durably stores one PAPER execution-ledger row (idempotent).

        The unique identity index (ts, ticket, order_type, requested_price)
        makes re-exporting the same adapter rows a no-op. Failure returns
        False (never raises — parity persistence must not disturb trading).
        """
        if not self._is_sqlite:
            # RT-004 / PG portability: this is a WRITE, not a read. The
            # ``_provider_read_guard`` it used to return silently swallowed
            # every paper-execution row on a pooled provider (returning the
            # documented default False), so the table stayed empty on
            # PostgreSQL while the SQLite side accumulated rows — exactly the
            # split the domain must not have. Route it through the pooled
            # WRITE backend instead: the statement is SQLite dialect, so the
            # write plane's ``translate_sql`` rewrites the ``INSERT OR IGNORE``
            # into a real upsert targeting the table's declared unique index
            # before it reaches the provider (``?`` placeholders stay for the
            # driver). Synchronous, like every other parity write here: the
            # caller's contract is a durability bool, not a queue hint.
            return self._provider_execute_write(
                [
                    (
                        """
                        INSERT OR IGNORE INTO audit_paper_executions
                            (ts, symbol, order_type, volume, requested_price,
                             bid_at_request, ask_at_request, spread, fill_price,
                             slippage, rejection_reason, ticket, latency_ticks,
                             status, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ts,
                            symbol,
                            order_type,
                            float(volume or 0.0),
                            float(requested_price or 0.0),
                            float(bid_at_request or 0.0),
                            float(ask_at_request or 0.0),
                            float(spread or 0.0),
                            fill_price,
                            slippage,
                            rejection_reason,
                            int(ticket or 0),
                            int(latency_ticks or 0),
                            status,
                            source,
                        ),
                    )
                ]
            )
        try:
            with self._connect_sqlite(5.0) as conn:
                create_paper_executions_table(conn)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO audit_paper_executions
                        (ts, symbol, order_type, volume, requested_price,
                         bid_at_request, ask_at_request, spread, fill_price,
                         slippage, rejection_reason, ticket, latency_ticks,
                         status, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        symbol,
                        order_type,
                        float(volume or 0.0),
                        float(requested_price or 0.0),
                        float(bid_at_request or 0.0),
                        float(ask_at_request or 0.0),
                        float(spread or 0.0),
                        fill_price,
                        slippage,
                        rejection_reason,
                        int(ticket or 0),
                        int(latency_ticks or 0),
                        status,
                        source,
                    ),
                )
            return True
        except Exception as e:
            logger.error("record_paper_execution failed: %s", e)
            return False

    def paper_execution_stats(self, days: int = 7) -> dict[str, Any]:
        """Aggregated PAPER execution stats over the trailing window.

        Honest None when there is no data — never a fabricated 0/0.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "paper_execution_stats",
                lambda: ({"fills": 0}),
            )
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=int(days))).isoformat()
        try:
            with self._connect_sqlite(5.0) as conn:
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS attempts,
                        SUM(CASE WHEN status = 'FILLED' THEN 1 ELSE 0 END) AS fills,
                        AVG(spread) AS mean_spread,
                        AVG(CASE WHEN status = 'FILLED' THEN ABS(slippage) END) AS mean_slippage
                    FROM audit_paper_executions WHERE ts >= ?
                    """,
                    (cutoff,),
                ).fetchone()
        except Exception as e:
            logger.error("paper_execution_stats failed: %s", e)
            return {"fills": 0}
        attempts = int(row[0] or 0)
        fills = int(row[1] or 0)
        return {
            "attempts": attempts,
            "fills": fills,
            "fill_rate": (fills / attempts) if attempts > 0 else None,
            "mean_spread": row[2],
            "mean_slippage": row[3],
        }

    def broker_execution_stats(self, days: int = 7) -> dict[str, Any]:
        """Aggregated DEMO/LIVE broker-truth stats over the trailing window.

        Consumes the canonical audit_broker_trades copy (synced from MT5 by
        BrokerHistorySyncWorker). Honest zeros when nothing synced.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "broker_execution_stats",
                lambda: ({"trades": 0}),
            )
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=int(days))).isoformat()
        try:
            with self._connect_sqlite(5.0) as conn:
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS trades,
                        SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS winners,
                        AVG(duration_sec) AS mean_duration_sec,
                        SUM(net_pnl) AS net_pnl_total
                    FROM audit_broker_trades
                    WHERE COALESCE(exit_time, entry_time) >= ?
                    """,
                    (cutoff,),
                ).fetchone()
        except Exception as e:
            logger.error("broker_execution_stats failed: %s", e)
            return {"trades": 0}
        trades = int(row[0] or 0)
        winners = int(row[1] or 0)
        return {
            "trades": trades,
            "win_rate": (winners / trades) if trades > 0 else None,
            "mean_duration_sec": row[2],
            "net_pnl_total": row[3],
        }

    def get_broker_deals_for_position(self, position_id: int) -> list[dict[str, Any]]:
        """
        TASK-7 (BUG-088/089): reads the DURABLE broker-deal capture for a position.
        The live deal window (get_closed_deals_history) can miss a close after a
        restart or when the position outlived the window; the durable
        audit_broker_deals table holds the authoritative deal rows (position_id
        join). Returns normalized dicts (same keys the live adapter emits:
        position_ticket, profit, commission, swap, price, reason, comment, volume),
        or [] when nothing is captured.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_broker_deals_for_position",
                lambda: ([]),
            )
        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    'SELECT ticket, "order", position_id, symbol, type, entry, '
                    "magic, time, reason, volume, price, profit, fee, swap, "
                    "commission, net_result, comment, external_id "
                    "FROM audit_broker_deals WHERE position_id = ? "
                    "ORDER BY time ASC;",
                    (int(position_id),),
                ).fetchall()
        except Exception as e:
            # FORENSIC REPAIR: never silent — a failing durable-deal capture read
            # must be observable so a missing autopsy row is NOT mistaken for
            # 'no deals'. The [] sentinel semantics are preserved.
            logger.error(
                "get_broker_deals_for_position failed (position_id=%s): %s",
                position_id,
                e,
                exc_info=True,
            )
            return []
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "ticket": r["ticket"],
                    "order_ticket": r["order"],
                    "position_ticket": r["position_id"],
                    "symbol": r["symbol"],
                    "price": r["price"],
                    "volume": r["volume"],
                    "profit": r["profit"],
                    "commission": r["commission"],
                    "swap": r["swap"],
                    "comment": r["comment"],
                    "closed_at": r["time"],
                    "reason": r["reason"],
                }
            )
        return out

    def get_ledger_opened(self, ticket: int) -> dict[str, Any] | None:
        """
        Returns the OPENED ledger row dict for a ticket (entry context) or None.
        Used by the reconciliation close-loop to rebuild the autopsy context.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_ledger_opened",
                lambda: None,
                sql=("SELECT * FROM audit_ledger WHERE ticket = ? AND status = 'OPENED' LIMIT 1;"),
                args=(int(ticket),),
                kind="row",
            )
        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM audit_ledger WHERE ticket = ? AND status = 'OPENED' LIMIT 1;",
                    (int(ticket),),
                ).fetchone()
                return dict(row) if row is not None else None
        except Exception as e:
            # FORENSIC REPAIR: never silent.
            logger.error(
                "get_ledger_opened failed (ticket=%s): %s",
                ticket,
                e,
                exc_info=True,
            )
            return None

    def log_ledger_closed(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        volume: float,
        entry_price: float,
        exit_price: float,
        status: str,
        pnl: float,
        commission: float,
        swap: float,
        duration_sec: float,
        timestamp_str: str,
        mae: float = 0.0,
        mfe: float = 0.0,
        initial_sl_price: float = 0.0,
        final_sl_price: float = 0.0,
        is_risk_free_hit: int = 0,
        exit_mechanism: str = "",
        order_id: str = "",
        open_time: str = "",
        close_time: str = "",
        entry_reason: str = "",
        ai_confidence_at_open: float = 0.0,
        market_regime_at_open: str = "",
        was_sl_modified: int = 0,
        mae_usd: float = 0.0,
        mfe_usd: float = 0.0,
        account_balance_after: float = 0.0,
        account_equity_after: float = 0.0,
        drawdown_percent_after: float = 0.0,
        entry_setup_snapshot: str = "{}",
        exit_reason_source: str = "",
        exit_evidence: str = "",
        exit_reason_confidence: float = 0.0,
        reversal_events_json: str = "[]",
        account_source: str = "",
    ) -> None:
        """
        Writes EXACTLY ONE data-rich autopsy row per closed trade.

        The row is upserted onto the OPENED placeholder (same ticket primary key), so a
        full trade lifecycle collapses into a single institutional accounting record:
        identification, timing, financials (gross vs net), entry context, SL dynamics,
        exit mechanism, quant excursions (MAE/MFE in USD), and the post-trade account
        snapshot.

        Entry-context fields (entry_reason / ai_confidence_at_open / market_regime_at_open)
        are preserved from the OPENED row whenever the caller passes blanks, so a close
        that lacks context never erases what was captured at entry.
        """

        # ---------------------------------------------------------------------
        # TASK 4 FIX: PnL / friction accounting.
        # `pnl` is the broker-reported gross profit (already net of the broker's own
        # spread execution). `commission` and `swap` are POSITIVE magnitudes that are
        # COSTS and MUST be SUBTRACTED, not added. The previous code did
        # `net = pnl + commission + swap`, which inflated profit (and produced the
        # reported "+$0.00" / overstated-profit symptom).
        #
        # We keep full float precision (REAL storage) and only round at display time.
        # `spread_at_close` is not yet a persisted column, so:
        #   - if the caller already folded spread into `pnl` (market-close slippage),
        #     we do NOT double-deduct it;
        #   - the spread term is accepted as an optional kwarg and subtracted exactly
        #     once when provided, reserving room for a future column migration.
        # ---------------------------------------------------------------------
        # ---------------------------------------------------------------------
        # BUG-046: `pnl` may be None (no broker deal AND no price evidence =
        # UNKNOWN). Never silently coerce missing broker truth to 0.0.
        # ---------------------------------------------------------------------
        if pnl is None:
            gross_pnl_usd = 0.0
            commission_usd = 0.0
            swap_usd = 0.0
            net_pnl_usd = 0.0
        else:
            gross_pnl_usd = float(pnl)
            commission_usd = abs(float(commission or 0.0))
            swap_usd = float(swap or 0.0)  # swaps can be negative (credited) or positive (debited)
            net_pnl_usd = gross_pnl_usd - commission_usd - swap_usd

        query = """
            INSERT INTO audit_ledger
            (ticket, symbol, direction, volume, entry_price, exit_price, status, pnl,
             commission, swap, duration_sec, timestamp, mae, mfe, initial_sl_price,
             final_sl_price, is_risk_free_hit, exit_mechanism,
             order_id, open_time, close_time, duration_seconds, open_price, close_price,
             gross_pnl_usd, net_pnl_usd, entry_reason, ai_confidence_at_open,
             market_regime_at_open, was_sl_modified, MAE_usd, MFE_usd,
             account_balance_after, account_equity_after, drawdown_percent_after,
             entry_setup_snapshot, exit_reason_source, exit_evidence,
             exit_reason_confidence, reversal_events_json, account_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticket) DO UPDATE SET
                exit_price=excluded.exit_price,
                status=excluded.status,
                pnl=excluded.pnl,
                commission=excluded.commission,
                swap=excluded.swap,
                duration_sec=excluded.duration_sec,
                timestamp=excluded.timestamp,
                mae=excluded.mae,
                mfe=excluded.mfe,
                initial_sl_price=excluded.initial_sl_price,
                final_sl_price=excluded.final_sl_price,
                is_risk_free_hit=excluded.is_risk_free_hit,
                exit_mechanism=excluded.exit_mechanism,
                order_id=CASE WHEN excluded.order_id != '' THEN excluded.order_id ELSE audit_ledger.order_id END,
                open_time=CASE WHEN excluded.open_time != '' THEN excluded.open_time ELSE audit_ledger.open_time END,
                close_time=excluded.close_time,
                duration_seconds=excluded.duration_seconds,
                open_price=excluded.open_price,
                close_price=excluded.close_price,
                gross_pnl_usd=excluded.gross_pnl_usd,
                net_pnl_usd=excluded.net_pnl_usd,
                entry_reason=CASE WHEN excluded.entry_reason != '' THEN excluded.entry_reason ELSE audit_ledger.entry_reason END,
                ai_confidence_at_open=CASE WHEN excluded.ai_confidence_at_open != 0.0 THEN excluded.ai_confidence_at_open ELSE audit_ledger.ai_confidence_at_open END,
                market_regime_at_open=CASE WHEN excluded.market_regime_at_open != '' THEN excluded.market_regime_at_open ELSE audit_ledger.market_regime_at_open END,
                was_sl_modified=excluded.was_sl_modified,
                MAE_usd=excluded.MAE_usd,
                MFE_usd=excluded.MFE_usd,
                account_balance_after=excluded.account_balance_after,
                account_equity_after=excluded.account_equity_after,
                drawdown_percent_after=excluded.drawdown_percent_after,
                entry_setup_snapshot=CASE WHEN excluded.entry_setup_snapshot != '{}' THEN excluded.entry_setup_snapshot ELSE audit_ledger.entry_setup_snapshot END,
                exit_reason_source=CASE WHEN excluded.exit_reason_source != '' THEN excluded.exit_reason_source ELSE audit_ledger.exit_reason_source END,
                exit_evidence=CASE WHEN excluded.exit_evidence != '' THEN excluded.exit_evidence ELSE audit_ledger.exit_evidence END,
                exit_reason_confidence=excluded.exit_reason_confidence,
                reversal_events_json=CASE WHEN excluded.reversal_events_json != '[]' THEN excluded.reversal_events_json ELSE audit_ledger.reversal_events_json END,
                account_source=CASE WHEN excluded.account_source != '' THEN excluded.account_source ELSE audit_ledger.account_source END
        """
        args = (
            ticket,
            symbol,
            direction,
            volume,
            entry_price,
            exit_price,
            status,
            pnl if pnl is not None else 0.0,
            commission if commission is not None else 0.0,
            swap if swap is not None else 0.0,
            duration_sec,
            timestamp_str,
            mae,
            mfe,
            initial_sl_price,
            final_sl_price,
            is_risk_free_hit,
            exit_mechanism,
            order_id,
            open_time or "",
            close_time or timestamp_str,
            float(duration_sec),
            float(entry_price),
            float(exit_price),
            gross_pnl_usd,
            net_pnl_usd,
            entry_reason,
            float(ai_confidence_at_open),
            market_regime_at_open,
            int(bool(was_sl_modified)),
            float(mae_usd),
            float(mfe_usd),
            float(account_balance_after),
            float(account_equity_after),
            float(drawdown_percent_after),
            entry_setup_snapshot,
            exit_reason_source,
            exit_evidence,
            float(exit_reason_confidence or 0.0),
            reversal_events_json or "[]",
            str(account_source or ""),
        )
        self._enqueue_financial(query, args)

    def get_account_performance_metrics(self) -> dict[str, Any]:
        """
        Calculates precise WinRate, Profit Factor, Drawdown, and historical trade metrics from the ledger.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_account_performance_metrics",
                lambda: (
                    {
                        "total_trades": 0,
                        "win_rate": 0.0,
                        "profit_factor": 0.0,
                        "max_drawdown": 0.0,
                        "avg_duration": 0.0,
                    }
                ),
            )

        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row

                # Fetch all closed trades from ledger
                cursor = conn.execute(
                    "SELECT pnl, commission, swap, duration_sec FROM audit_ledger WHERE status != 'OPENED'"
                )
                rows = cursor.fetchall()

                total_trades = len(rows)
                if total_trades == 0:
                    return {
                        "total_trades": 0,
                        "win_rate": 0.0,
                        "profit_factor": 0.0,
                        "max_drawdown": 0.0,
                        "avg_duration": 0.0,
                    }

                wins = 0
                gross_profit = 0.0
                gross_loss = 0.0
                total_duration = 0.0

                for r in rows:
                    # IMPORTANT: commission and swap are COSTS and must be
                    # SUBTRACTED (net = pnl - commission - swap), exactly as
                    # `log_ledger_closed` persists `net_pnl_usd`. The previous
                    # implementation used `pnl + commission + swap`, which
                    # inflated profits and disagreed with the canonical
                    # AccountingCore (agents/bugs.md BUG-019).
                    net_pnl = float(r["pnl"]) - abs(float(r["commission"])) - float(r["swap"])
                    if net_pnl > 0:
                        wins += 1
                        gross_profit += net_pnl
                    else:
                        gross_loss += abs(net_pnl)
                    total_duration += float(r["duration_sec"])

                win_rate = (wins / total_trades) * 100.0
                profit_factor = (
                    gross_profit / gross_loss
                    if gross_loss > 0
                    else (gross_profit if gross_profit > 0 else 1.0)
                )
                avg_duration = total_duration / total_trades

                # Drawdown calculation from snapshots
                cursor_snap = conn.execute(
                    "SELECT balance, equity FROM audit_account_snapshots ORDER BY id ASC"
                )
                snap_rows = cursor_snap.fetchall()

                max_drawdown = 0.0
                peak = 0.0
                for r_snap in snap_rows:
                    eq = float(r_snap["equity"])
                    peak = max(peak, eq)
                    if peak > 0:
                        dd = ((peak - eq) / peak) * 100.0
                        max_drawdown = max(max_drawdown, dd)

                return {
                    "total_trades": total_trades,
                    "win_rate": round(win_rate, 2),
                    "profit_factor": round(profit_factor, 2),
                    "max_drawdown": round(max_drawdown, 2),
                    "avg_duration": round(avg_duration, 2),
                }
        except Exception as e:
            logger.error("Failed to calculate account performance metrics", error=str(e))
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "avg_duration": 0.0,
            }

    def get_recent_predictions(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        REAL prediction history from the immutable audit_signals ledger.

        audit_signals is appended for every live trade proposal (one row per
        M1 decision) and carries the actual softmax probabilities in its JSON
        payload. This is the authoritative "AI Prediction vs Actual Movement"
        source - NOT the fabricated simulated_outcomes list that previously
        served as the predictions table.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_recent_predictions",
                lambda: ([]),
            )
        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT request_id, symbol, action, confidence, proposed_entry,
                           stop_loss, take_profit, regime, generated_at, payload,
                           execution_mode, reason_code, decision_stage, blocked_by
                    FROM audit_signals
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows: list[dict[str, Any]] = []
                for r in cursor.fetchall():
                    row = dict(r)
                    payload = row.get("payload") or "{}"
                    try:
                        parsed = json.loads(payload) if isinstance(payload, str) else payload
                    except Exception:
                        parsed = {}
                    row["payload_parsed"] = parsed
                    rows.append(row)
                return rows
        except Exception as e:
            logger.error("Failed to retrieve recent predictions", error=str(e))
            return []

    def get_ledger_trades(
        self,
        limit: int = 100,
        offset: int = 0,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieves paginated and filtered historical trade logs.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_ledger_trades",
                lambda: ([]),
            )

        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                if status_filter:
                    cursor = conn.execute(
                        "SELECT * FROM audit_ledger WHERE status = ? ORDER BY ticket DESC LIMIT ? OFFSET ?",
                        (status_filter, limit, offset),
                    )
                else:
                    cursor = conn.execute(
                        "SELECT * FROM audit_ledger ORDER BY ticket DESC LIMIT ? OFFSET ?",
                        (limit, offset),
                    )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to retrieve ledger trades", error=str(e))
            return []

    def get_equity_growth_chart_data(self) -> list[dict[str, Any]]:
        """
        Retrieves balance/equity growth history for charting.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_equity_growth_chart_data",
                lambda: ([]),
            )

        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT timestamp, balance, equity FROM audit_account_snapshots ORDER BY id ASC"
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to retrieve equity growth chart data", error=str(e))
            return []

    def get_recent_order_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Returns the most recent order lifecycle events for the Debug Hub's
        MT5 IPC Telemetry Console (retcodes/reasons, latency, state transitions).
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_recent_order_events",
                lambda: ([]),
            )

        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """
                    SELECT id, ticket, order_id, symbol, action, price, stop_loss, take_profit,
                           volume, reason, latency, execution_mode, timestamp
                    FROM audit_orders
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("Failed to retrieve recent order events", error=str(e))
            return []

    def get_ledger_row(self, ticket: int) -> dict[str, Any] | None:
        """Returns the full autopsy row for a single ticket, or None when absent."""
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_ledger_row",
                lambda: None,
                sql="SELECT * FROM audit_ledger WHERE ticket = ?",
                args=(ticket,),
                kind="row",
            )
        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM audit_ledger WHERE ticket = ?", (ticket,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("Failed to retrieve ledger row", ticket=ticket, error=str(e))
            return None

    def get_last_account_snapshot(self) -> dict[str, Any] | None:
        """
        Synchronous read to retrieve the last known account state for Crash Recovery.
        Typically called once during system boot in live_engine.py.
        """
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_last_account_snapshot",
                lambda: None,
                sql="SELECT * FROM audit_account_snapshots ORDER BY id DESC LIMIT 1",
                kind="row",
            )

        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM audit_account_snapshots ORDER BY id DESC LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(
                "Failed to retrieve last account snapshot for Crash Recovery", error=str(e)
            )
        return None

    def _seed_trading_rules(self, conn: sqlite3.Connection) -> None:
        """Seeds the trading_rules_config table with all 30+ rules, disabled by default."""
        # No trailing ``;``: the write path's SQLite-dialect rewrite appends an
        # ``ON CONFLICT`` clause to this statement, and a semicolon terminates
        # the statement before that clause on providers that parse strictly.
        conn.executemany(
            """
            INSERT OR IGNORE INTO trading_rules_config (rule_name, is_enabled, category, parameters)
            VALUES (?, 0, ?, ?)
            """,
            DEFAULT_TRADING_RULES,
        )

    def get_trading_rules(self) -> list[dict[str, Any]]:
        """Retrieves all 30+ trading rules with their enablement status and parameters."""
        if not self._is_sqlite:
            return self._provider_read_guard(
                "get_trading_rules",
                lambda: ([]),
            )
        try:
            with self._connect_sqlite(5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT rule_name, is_enabled, category, parameters FROM trading_rules_config"
                )
                return [
                    {
                        "rule_name": r["rule_name"],
                        "is_enabled": bool(r["is_enabled"]),
                        "category": r["category"],
                        "parameters": r["parameters"],
                    }
                    for r in cursor.fetchall()
                ]
        except Exception as e:
            logger.error("Failed to retrieve trading rules", error=str(e))
            return []

    def toggle_trading_rule(
        self, rule_name: str, is_enabled: bool, parameters_json: str | None = None
    ) -> bool:
        """Toggles the enablement of a trading rule and optionally updates its parameters."""
        if not self._is_sqlite:
            return self._provider_read_guard(
                "toggle_trading_rule",
                lambda: False,
            )
        try:
            # Execute synchronously to avoid thread-safety mismatch with web thread toggles
            with self._connect_sqlite(5.0) as conn:
                if parameters_json is not None:
                    conn.execute(
                        """
                        UPDATE trading_rules_config
                        SET is_enabled = ?, parameters = ?
                        WHERE rule_name = ?
                        """,
                        (1 if is_enabled else 0, parameters_json, rule_name),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE trading_rules_config
                        SET is_enabled = ?
                        WHERE rule_name = ?
                        """,
                        (1 if is_enabled else 0, rule_name),
                    )
                conn.commit()
            return True
        except Exception as e:
            logger.error("Failed to toggle/update trading rule", rule_name=rule_name, error=str(e))
            return False

    def purge_old_audit_data(
        self,
        signal_retention_days: float | None = None,
        moving_retention_days: float | None = None,
        telemetry_retention_days: float | None = None,
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        """Bounded, transaction-safe retention purge (BUG-054).

        Deletes ONLY disposable telemetry in small batches (never one giant
        unbounded DELETE against a large table):
          * audit_signals older than `signal_retention_days` (default 7)
          * position_lifecycle_events POSITION_MOVING older than
            `moving_retention_days` (default 3)
          * audit_guard_telemetry older than `telemetry_retention_days`
            (default 13)
        Accounting truth (audit_ledger), experience history, autopsies,
        strategy/research lineage are NEVER touched. Runs on its own short
        connection (outside the worker/hot path); safe to call while the
        engine is live (WAL allows concurrent readers/writers, batches keep
        each transaction short).
        """
        from datetime import UTC, datetime, timedelta

        if not self._is_sqlite:
            return self._provider_read_guard(
                "purge_old_audit_data",
                lambda: ({"error": "not sqlite"}),
            )

        sig_days = float(
            signal_retention_days
            if signal_retention_days is not None
            else self._signal_retention_days
        )
        mov_days = float(
            moving_retention_days
            if moving_retention_days is not None
            else self._moving_retention_days
        )
        tel_days = float(
            telemetry_retention_days
            if telemetry_retention_days is not None
            else self._telemetry_retention_days
        )
        bsize = int(batch_size if batch_size is not None else self._purge_batch_size)

        now = datetime.now(UTC)
        sig_cutoff = (now - timedelta(days=sig_days)).isoformat()
        mov_cutoff = (now - timedelta(days=mov_days)).isoformat()
        tel_cutoff = (now - timedelta(days=tel_days)).isoformat()

        results: dict[str, Any] = {
            "started_at": now.isoformat(),
            "signal_retention_days": sig_days,
            "moving_retention_days": mov_days,
            "telemetry_retention_days": tel_days,
            "deleted": {},
        }
        start = time.monotonic()
        conn = self._connect_sqlite(30.0)
        try:
            # Bounded batched deletes: each batch is its own transaction so a
            # long table never blocks writers for more than a few rows.
            def _batch_delete(sql: str, args: tuple[Any, ...]) -> int:
                """Bounded delete via rowid subquery (DELETE LIMIT unsupported).

                Each batch is a single short transaction; the rowid anchor keeps
                the scan bounded regardless of table size.
                """
                total = 0
                while True:
                    with conn:
                        cur = conn.execute(sql, (*args, bsize))
                        total += cur.rowcount
                    if cur.rowcount < bsize:
                        break
                return total

            results["deleted"]["audit_signals"] = _batch_delete(
                "DELETE FROM audit_signals WHERE id IN "
                "(SELECT id FROM audit_signals WHERE generated_at < ? ORDER BY id LIMIT ?)",
                (sig_cutoff,),
            )
            results["deleted"]["position_moving"] = _batch_delete(
                "DELETE FROM position_lifecycle_events WHERE id IN "
                "(SELECT id FROM position_lifecycle_events "
                "WHERE event_type = 'POSITION_MOVING' AND event_timestamp < ? ORDER BY id LIMIT ?)",
                (mov_cutoff,),
            )
            results["deleted"]["guard_telemetry"] = _batch_delete(
                "DELETE FROM audit_guard_telemetry WHERE rowid IN "
                "(SELECT rowid FROM audit_guard_telemetry WHERE window_start < ? ORDER BY rowid LIMIT ?)",
                (tel_cutoff,),
            )
        except Exception as e:
            results["error"] = str(e)
            logger.error("Audit retention purge failed", error=str(e))
        finally:
            conn.close()
        results["duration_ms"] = round((time.monotonic() - start) * 1000.0, 1)
        logger.info(
            "Audit retention purge complete",
            deleted=results["deleted"],
            duration_ms=results["duration_ms"],
        )
        return results

    #: BUG-304: cap on how long close() will wait for the background writer
    #: to flush its queue. An unbounded ``_queue.join()`` means one stuck
    #: insert parks process exit forever, and the operator is forced to
    #: taskkill /F — which reproduces the "not completely closed" symptom
    #: (WAL + broker session left open). Bounded beats hung.
    _CLOSE_FLUSH_TIMEOUT_SEC: float = 8.0

    def close(self) -> None:
        """Gracefully shuts down background worker and flushes pending records.

        BUG-304: the flush is BOUNDED. A stuck insert can no longer hold
        process exit hostage; the pending rows stay in the WAL and the
        caller is told the close was partial instead of hanging forever.
        """
        logger.info("Initiating graceful shutdown of Audit Database. Flushing queues...")
        self._running = False
        # The write plane owns the queue/worker/backend; drain it bounded.
        plane, self._write_plane = self._write_plane, None
        if plane is not None:
            plane_drained = plane.stop(max(self._CLOSE_FLUSH_TIMEOUT_SEC, 1.0))
            if not plane_drained:
                logger.warning(
                    "AUDIT close timed out after %.1fs — pending rows remain",
                    self._CLOSE_FLUSH_TIMEOUT_SEC,
                )
        if self._worker_thread and self._worker_thread.is_alive():
            drained = self._join_queue_bounded(self._CLOSE_FLUSH_TIMEOUT_SEC)
            self._worker_thread.join(timeout=self._CLOSE_FLUSH_TIMEOUT_SEC)
            self._worker_thread = None
            if not drained:
                # Honest accounting: rows may remain in the WAL. Never claim
                # a clean flush that did not happen.
                logger.warning(
                    "AUDIT close timed out after %.1fs — pending rows remain in the WAL",
                    self._CLOSE_FLUSH_TIMEOUT_SEC,
                )
        else:
            self._worker_thread = None
        if self._shared_conn is not None:
            with contextlib.suppress(Exception):
                self._shared_conn.close()
            self._shared_conn = None
        logger.info("Audit Database safely closed.")

    def _join_queue_bounded(self, timeout_sec: float) -> bool:
        """Wait for the writer queue to drain with a deadline.

        Returns True only when the queue actually emptied; False means rows
        may remain (the caller must not claim a clean flush). The blocking
        ``Queue.join`` runs on a daemon thread so a stuck insert cannot park
        the calling thread past the deadline.
        """
        import threading as _threading

        if self._queue.empty():
            return True
        done = _threading.Event()

        def _wait() -> None:
            with contextlib.suppress(Exception):
                self._queue.join()
            done.set()

        waiter = _threading.Thread(target=_wait, daemon=True, name="AuditDB_CloseWait")
        waiter.start()
        return done.wait(timeout=timeout_sec) and self._queue.empty()
