"""Database Event & Query Error/Warning Persistent Log Store.

Enforces Sections 16, 17, 18 of the Dual Database Architecture:
    - Structured persistence for WARNING, ERROR, and CRITICAL database events.
    - INFO and DEBUG are excluded to prevent database bloat and performance loss.
    - Sensitive credentials and tokens are strictly redacted.
    - Configurable retention and automatic pruning.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import traceback
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.query_logging import mask_query_text
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.database.log_store")

LOG_TABLE = "db_operation_logs"
LOG_PERSISTENCE_ENABLED_SETTING_KEY = "database.log_persistence.enabled"

#: The app-wide log sink ships ON by default (the operator ask: every ERROR
#: and WARNING from the whole app, in the DB, not duplicated). The settings
#: key remains the explicit off-switch. ``log_persistence_enabled()``
#: resolves the operator's choice at activation time and on each record.
LOG_PERSISTENCE_DEFAULT_ENABLED = True

#: Record-level dedup window. Two records with the SAME provenance
#: (component/operation/event + level + error_code) within this many seconds
#: are one incident, not N rows. The first record is persisted immediately;
#: repeats bump ``repeat_count`` on that row instead of inserting again.
LOG_DEDUP_WINDOW_SEC = 60.0

#: Cap on the rendered full traceback (a pathological ``raise`` chain in a
#: recursive serializer can produce megabytes; the trace is for triage, and
#: one megabyte is already a very deep stack).
LOG_MAX_TRACE_CHARS = 4096

#: Cap on the JSON blob carried as ``context_json`` (the whole bound context
#: minus secrets). Bounded so a logging storm cannot bloat the table.
LOG_MAX_CONTEXT_CHARS = 4096


def log_persistence_enabled(settings_db: Any | None = None) -> bool:
    """Return whether the persistent log sink is active.

    The operator ask is "errors and warnings from the WHOLE app, in the DB",
    so the sink ships ON by default (:data:`LOG_PERSISTENCE_DEFAULT_ENABLED`).
    The settings key ``database.log_persistence.enabled`` is the explicit
    operator switch: a stored ``False`` turns it off, a stored ``True`` (or no
    row at all) leaves it on. Settings-access failures fail CLOSED to the
    default-on state only when the row is absent/unreadable — never to
    "silent off", which is the failure mode that made the sink look dead.
    """
    try:
        if settings_db is None:
            from nexus_scalp.settings.service import SettingsDatabase

            settings_db = SettingsDatabase()
        row = settings_db.get(LOG_PERSISTENCE_ENABLED_SETTING_KEY)
        if row is None:
            return LOG_PERSISTENCE_DEFAULT_ENABLED
        value = getattr(row, "value", row)
        if value is None or str(value).strip() == "":
            return LOG_PERSISTENCE_DEFAULT_ENABLED
        return bool(value)
    except Exception:
        return LOG_PERSISTENCE_DEFAULT_ENABLED


_counter_lock = Lock()
_counters: dict[str, Any] = {"persisted_total": 0, "dropped_total": 0, "last_persist_error": None}


def _note_persisted() -> None:
    with _counter_lock:
        _counters["persisted_total"] += 1


def _note_dropped(error: BaseException | None = None) -> None:
    with _counter_lock:
        _counters["dropped_total"] += 1
        if error is not None:
            _counters["last_persist_error"] = f"{type(error).__name__}: {error}"


def log_persistence_snapshot() -> dict[str, Any]:
    with _counter_lock:
        return dict(_counters)


def reset_log_persistence_counters() -> None:
    with _counter_lock:
        _counters.update(persisted_total=0, dropped_total=0, last_persist_error=None)


@dataclass
class DatabaseLogEntry:
    """Structured record of a database warning, error, or critical event."""

    level: str  # WARNING, ERROR, CRITICAL
    provider: str
    domain: str
    operation: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    repository: str = ""
    query_name: str = ""
    duration_ms: float = 0.0
    rows: int = 0
    error_code: str = ""
    error_message: str = ""
    correlation_id: str = ""
    masked_sql: str = ""
    # App-wide log sink (PG-LOG-SINK): the fields below carry the rest of the
    # application's WARNING/ERROR/CRITICAL records. They default to empty so
    # every existing caller of ``DatabaseLogEntry`` (query_logging's DB-layer
    # records) keeps constructing it unchanged.
    event_name: str = ""
    logger_name: str = ""
    full_trace: str = ""
    context_json: str = "{}"
    fingerprint: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""
    repeat_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DatabaseLogStore:
    """Manages persistent logging of database warnings and errors."""

    def __init__(
        self,
        config: DatabaseConfig | None = None,
        retention_days: int = 30,
    ) -> None:
        self.cfg = config or load_database_config("audit")
        self.retention_days = retention_days

    def ensure_table(self, driver: Any | None = None) -> None:
        """Create the db_operation_logs table if not present."""
        close_needed = False
        if driver is None:
            driver = get_driver(self.cfg)
            close_needed = True

        try:
            if self.cfg.is_postgresql:
                stmts = [
                    (
                        f"CREATE TABLE IF NOT EXISTS {LOG_TABLE} ("
                        "  id BIGSERIAL PRIMARY KEY,"
                        "  timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                        "  level VARCHAR(16) NOT NULL,"
                        "  provider VARCHAR(32) NOT NULL,"
                        "  domain VARCHAR(64) NOT NULL,"
                        "  operation VARCHAR(128) NOT NULL,"
                        "  repository VARCHAR(128) NOT NULL DEFAULT '',"
                        "  query_name VARCHAR(128) NOT NULL DEFAULT '',"
                        "  duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,"
                        "  rows BIGINT NOT NULL DEFAULT 0,"
                        "  error_code VARCHAR(64) NOT NULL DEFAULT '',"
                        "  error_message TEXT NOT NULL DEFAULT '',"
                        "  correlation_id VARCHAR(64) NOT NULL DEFAULT '',"
                        "  masked_sql TEXT NOT NULL DEFAULT '',"
                        "  event_name VARCHAR(255) NOT NULL DEFAULT '',"
                        "  logger_name VARCHAR(255) NOT NULL DEFAULT '',"
                        "  full_trace TEXT NOT NULL DEFAULT '',"
                        "  context_json JSONB NOT NULL DEFAULT '{}'::jsonb,"
                        "  fingerprint VARCHAR(64) NOT NULL DEFAULT '',"
                        "  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                        "  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                        "  repeat_count BIGINT NOT NULL DEFAULT 1"
                        ")"
                    ),
                    f"CREATE INDEX IF NOT EXISTS idx_{LOG_TABLE}_ts ON {LOG_TABLE} (timestamp)",
                    f"CREATE INDEX IF NOT EXISTS idx_{LOG_TABLE}_level ON {LOG_TABLE} (level)",
                    f"CREATE INDEX IF NOT EXISTS idx_{LOG_TABLE}_fp ON {LOG_TABLE} (fingerprint, last_seen_at)",
                ]
            else:
                stmts = [
                    (
                        f"CREATE TABLE IF NOT EXISTS {LOG_TABLE} ("
                        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        "  timestamp TEXT NOT NULL,"
                        "  level TEXT NOT NULL,"
                        "  provider TEXT NOT NULL,"
                        "  domain TEXT NOT NULL,"
                        "  operation TEXT NOT NULL,"
                        "  repository TEXT NOT NULL DEFAULT '',"
                        "  query_name TEXT NOT NULL DEFAULT '',"
                        "  duration_ms REAL NOT NULL DEFAULT 0.0,"
                        "  rows INTEGER NOT NULL DEFAULT 0,"
                        "  error_code TEXT NOT NULL DEFAULT '',"
                        "  error_message TEXT NOT NULL DEFAULT '',"
                        "  correlation_id TEXT NOT NULL DEFAULT '',"
                        "  masked_sql TEXT NOT NULL DEFAULT '',"
                        "  event_name TEXT NOT NULL DEFAULT '',"
                        "  logger_name TEXT NOT NULL DEFAULT '',"
                        "  full_trace TEXT NOT NULL DEFAULT '',"
                        "  context_json TEXT NOT NULL DEFAULT '{}',"
                        "  fingerprint TEXT NOT NULL DEFAULT '',"
                        "  first_seen_at TEXT NOT NULL,"
                        "  last_seen_at TEXT NOT NULL,"
                        "  repeat_count INTEGER NOT NULL DEFAULT 1"
                        ")"
                    ),
                    f"CREATE INDEX IF NOT EXISTS idx_{LOG_TABLE}_ts ON {LOG_TABLE} (timestamp)",
                    f"CREATE INDEX IF NOT EXISTS idx_{LOG_TABLE}_level ON {LOG_TABLE} (level)",
                    f"CREATE INDEX IF NOT EXISTS idx_{LOG_TABLE}_fp ON {LOG_TABLE} (fingerprint, last_seen_at)",
                ]
            for stmt in stmts:
                driver.execute(stmt)
        finally:
            if close_needed:
                driver.close()

    def record(self, entry: DatabaseLogEntry, driver: Any | None = None) -> bool:
        """Persist a warning, error, or critical event (deduplicated).

        Two records with the same :attr:`DatabaseLogEntry.fingerprint` within
        :data:`LOG_DEDUP_WINDOW_SEC` are ONE row: the first inserts, repeats
        bump ``repeat_count``/``last_seen_at`` on that row. Records with no
        fingerprint (the pre-existing DB-layer call sites construct entries
        without one) always insert — their de-duplication is the event
        aggregator's job upstream, and changing that would double-log.
        """
        # Only retain WARNING, ERROR, CRITICAL
        if entry.level.upper() not in {"WARNING", "ERROR", "CRITICAL"}:
            _note_dropped()
            return False
        entry.level = entry.level.upper()
        entry.masked_sql = mask_query_text(entry.masked_sql)
        entry.error_message = mask_query_text(entry.error_message)[:2000]
        entry.provider = mask_query_text(entry.provider)
        entry.domain = mask_query_text(entry.domain)
        entry.operation = mask_query_text(entry.operation)
        entry.repository = mask_query_text(entry.repository)
        entry.query_name = mask_query_text(entry.query_name)
        entry.error_code = mask_query_text(entry.error_code)
        entry.correlation_id = mask_query_text(entry.correlation_id)
        entry.event_name = mask_query_text(entry.event_name)[:255]
        entry.logger_name = mask_query_text(entry.logger_name)[:255]
        entry.full_trace = mask_query_text(entry.full_trace)[:LOG_MAX_TRACE_CHARS]
        entry.context_json = mask_query_text(entry.context_json)[:LOG_MAX_CONTEXT_CHARS]
        if not entry.first_seen_at:
            entry.first_seen_at = entry.timestamp or datetime.now(UTC).isoformat()
        if not entry.last_seen_at:
            entry.last_seen_at = entry.timestamp or datetime.now(UTC).isoformat()

        close_needed = False
        if driver is None:
            driver = get_driver(self.cfg)
            close_needed = True
        try:
            self.ensure_table(driver)
            if entry.fingerprint:
                updated = self._bump_repeat(driver, entry)
                if updated:
                    _note_persisted()
                    return True
            safe_sql = mask_query_text(entry.masked_sql)
            params = (
                entry.timestamp or datetime.now(UTC).isoformat(),
                entry.level.upper(),
                entry.provider,
                entry.domain,
                entry.operation,
                entry.repository,
                entry.query_name,
                entry.duration_ms,
                entry.rows,
                entry.error_code,
                entry.error_message[:2000],
                entry.correlation_id,
                safe_sql,
                entry.event_name,
                entry.logger_name,
                entry.full_trace,
                entry.context_json,
                entry.fingerprint,
                entry.first_seen_at,
                entry.last_seen_at,
                entry.repeat_count,
            )

            cols = (
                "timestamp, level, provider, domain, operation, repository, "
                "query_name, duration_ms, rows, error_code, error_message, "
                "correlation_id, masked_sql, event_name, logger_name, "
                "full_trace, context_json, fingerprint, first_seen_at, "
                "last_seen_at, repeat_count"
            )
            if self.cfg.is_postgresql:
                placeholders = ", ".join("%s" for _ in range(21))
            else:
                placeholders = ", ".join("?" for _ in range(21))

            insert_sql = f"INSERT INTO {LOG_TABLE} ({cols}) VALUES ({placeholders})"
            driver.execute(insert_sql, params)
            _note_persisted()
            return True
        except Exception as exc:
            _note_dropped(exc)
            logger.debug("Failed to persist database log entry: %s", exc)
            return False
        finally:
            if close_needed:
                driver.close()

    def _bump_repeat(self, driver: Any, entry: DatabaseLogEntry) -> bool:
        """Fold a repeat into its existing row; False means insert a new one.

        Matches the most recent row with this fingerprint whose
        ``last_seen_at`` is inside the dedup window. Bounded by construction:
        the (fingerprint, last_seen_at) index exists for exactly this lookup,
        and the window keeps the candidate set small.
        """
        try:
            if self.cfg.is_postgresql:
                select_sql = (
                    f"SELECT id, repeat_count FROM {LOG_TABLE} "
                    f"WHERE fingerprint = %s AND last_seen_at >= %s "
                    f"ORDER BY id DESC LIMIT 1"
                )
            else:
                select_sql = (
                    f"SELECT id, repeat_count FROM {LOG_TABLE} "
                    f"WHERE fingerprint = ? AND last_seen_at >= ? "
                    f"ORDER BY id DESC LIMIT 1"
                )
            cutoff = (datetime.now(UTC) - timedelta(seconds=LOG_DEDUP_WINDOW_SEC)).isoformat()
            row = driver.query_one(select_sql, (entry.fingerprint, cutoff))
            if row is None:
                return False
            row_id = row.get("id")
            repeats = row.get("repeat_count") or 1
            if self.cfg.is_postgresql:
                update_sql = (
                    f"UPDATE {LOG_TABLE} SET repeat_count = %s, last_seen_at = %s WHERE id = %s"
                )
            else:
                update_sql = (
                    f"UPDATE {LOG_TABLE} SET repeat_count = ?, last_seen_at = ? WHERE id = ?"
                )
            driver.execute(update_sql, (int(repeats) + 1, entry.last_seen_at, row_id))
            return True
        except Exception as exc:
            # Dedup is an optimization, never a requirement: on any failure we
            # fall back to a plain INSERT (the caller's record() path).
            logger.debug("Log dedup update failed (falling back to insert): %s", exc)
            return False

    log = record

    def query_recent(
        self,
        limit: int = 50,
        level: str | None = None,
        driver: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Query recent persisted log events."""
        close_needed = False
        if driver is None:
            driver = get_driver(self.cfg)
            close_needed = True
        try:
            self.ensure_table(driver)
            where = ""
            args: tuple[Any, ...] = ()
            if level:
                where = "WHERE level = %s" if self.cfg.is_postgresql else "WHERE level = ?"
                args = (level.upper(),)

            sql = f"SELECT * FROM {LOG_TABLE} {where} ORDER BY id DESC LIMIT {int(limit)}"
            return driver.query(sql, args)
        except Exception as exc:
            logger.debug("Failed to query recent database log entries: %s", exc)
            return []
        finally:
            if close_needed:
                driver.close()

    get_recent_logs = query_recent

    def purge_expired(self) -> int:
        """Purge log records exceeding the retention period."""
        driver = get_driver(self.cfg)
        try:
            self.ensure_table(driver)
            cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
            where = "timestamp < %s" if self.cfg.is_postgresql else "timestamp < ?"
            sql = f"DELETE FROM {LOG_TABLE} WHERE {where}"
            result = driver.execute(sql, (cutoff,))
            # PostgreSQL/SQLite drivers may return a cursor rather than the
            # affected-row count. Use rowcount when available and preserve
            # the integer contract of this method.
            rowcount = getattr(result, "rowcount", result)
            return int(rowcount or 0)
        except Exception as exc:
            logger.error("Failed to prune old database operation logs: %s", exc)
            return 0
        finally:
            driver.close()


DbLogEntry = DatabaseLogEntry
DbLogStore = DatabaseLogStore


# ---------------------------------------------------------------------------
# App-wide log sink (PG-LOG-SINK)
# ---------------------------------------------------------------------------
#
# The operator ask: "add a postgres log system to the whole app so errors and
# warnings are not duplicated, with full trace". The records already flow
# through one structlog pipeline (observability/logging.py); the sink below is
# a stdlib logging.Handler installed by ``configure_logging`` on the ROOT
# logger, so it sees EVERY WARNING/ERROR/CRITICAL the app emits — engine,
# web layer, drivers, fabric, workers — with zero call-site churn and no
# second logging API to keep in sync.
#
# Non-duplication is enforced at three places:
#   1. the handler is installed ONCE on the root logger and marked as its own
#      (the re-configure path replaces exactly its own handlers);
#   2. record-level dedup by provenance fingerprint inside the dedup window;
#   3. the DB layer's own records (query_logging) reach the same table with
#      no fingerprint, so they are never folded — a driver failure is a
#      distinct row from the same exception logged at the app layer.


def _fingerprint(
    level: str,
    logger_name: str,
    event: str,
    error_code: str,
    component: str,
) -> str:
    """Stable provenance key for record-level de-duplication.

    Two records share a fingerprint when they come from the same logger, carry
    the same event name / message text and the same severity and error code —
    i.e. they are the SAME condition firing again. The message is part of the
    key (not just the logger) so two genuinely different defects from one
    component stay distinct rows. sha1 of a stable concatenation; it is a
    grouping key, not a security primitive.
    """
    import hashlib

    parts = (level, logger_name or "", (event or "")[:512], (error_code or ""), (component or ""))
    return hashlib.sha1("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()


def _resolve_exc_info(record: logging.LogRecord) -> Any:
    """Recover the exception triple for a record (stdlib OR structlog).

    Two shapes arrive at a root handler:

    * **stdlib** — ``logger.error("...", exc_info=True)`` sets
      ``record.exc_info`` (resolved to the live ``(type, value, tb)`` triple
      by ``Logger.log``) and the formatter renders it.
    * **structlog through ProcessorFormatter** — ``record.exc_info`` stays
      ``None``: structlog keeps the caller's ``exc_info`` kwarg INSIDE the
      event dict (``record.msg["exc_info"]``), and its own
      ``ExceptionRenderer`` renders the traceback during formatting.

    A DB sink that only reads ``record.exc_info`` would therefore persist the
    FULL TRACE for every plain-stdlib error and NO trace at all for every
    structlog error — which is nearly the whole app. Normalise both shapes
    here: read the kwarg when the attribute is empty, and resolve a ``True``
    against ``sys.exc_info()`` exactly the way ``Logger.log`` does (the
    record was emitted inside an ``except`` block, so the live triple IS the
    record's exception).
    """
    exc_info: Any = getattr(record, "exc_info", None)
    if exc_info:
        return exc_info
    raw = getattr(record, "msg", None)
    if isinstance(raw, dict):
        kw = raw.get("exc_info")
        if kw:
            if kw is True:
                live = sys.exc_info()
                # ``exc_info=True`` outside any ``except`` block has no
                # exception to render (stdlib behaves identically). Comparing
                # against the sentinel tuple distinguishes "no active
                # exception" from a real one so we store an honest empty
                # trace instead of the misleading "NoneType: None".
                if live is not None and live[0] is not None:
                    return live
                return None
            if isinstance(kw, BaseException):
                return (type(kw), kw, kw.__traceback__)
            if isinstance(kw, tuple) and len(kw) == 3:
                return kw
    return None


def _render_trace(record: logging.LogRecord) -> str:
    """Full traceback for the record, rendered once at persist time.

    Handles both the stdlib shape (``record.exc_info`` triple) and the
    structlog-via-``ProcessorFormatter`` shape (``exc_info`` carried inside
    the event dict — see :func:`_resolve_exc_info``). Empty for records
    without an exception — a WARNING that is not an exception has no trace
    to store, and storing an empty string is honest, not a loss.
    """
    exc_info = _resolve_exc_info(record)
    if not exc_info:
        return ""
    try:
        return "".join(traceback.format_exception(*exc_info))
    except Exception:
        # A malformed exc_info must never block the record; fall back to the
        # repr of the value, which is what the message already carries.
        try:
            return repr(exc_info[1])
        except Exception:
            return ""


#: Record keys that are already rendered into the message or into dedicated
#: columns, so they are not duplicated inside ``context_json``.
_DEDUP_CONTEXT_KEYS = frozenset(
    {
        "event",
        "timestamp",
        "level",
        "logger",
        "logger_name",
        "exc_type",
        "exc_value",
        "exc_info",
        "traceback",
    }
)


def _build_context(record: logging.LogRecord) -> str:
    """Render the record's bound context as JSON (secrets already redacted).

    structlog records carry the bound key/value pairs on ``record.msg`` when
    they arrive via ``ProcessorFormatter``; the stdlib attributes hold the
    logger name, line and process/thread ids. Everything that survives the
    pipeline's own redaction pass is safe to persist — the sink never sees an
    unredacted credential because the pipeline's ``_redact_sensitive_fields``
    processor already ran (it is in ``shared_processors``).
    """
    import json

    context: dict[str, Any] = {}
    msg = getattr(record, "msg", None)
    if isinstance(msg, dict):
        for key, value in msg.items():
            if key not in _DEDUP_CONTEXT_KEYS:
                context[str(key)] = value
    # Positional args (a plain ``logger.error("x: %s", v)`` call) are message
    # interpolation inputs; the rendered message already carries them.
    context.setdefault("process", getattr(record, "process", None))
    context.setdefault("thread", getattr(record, "thread", None))
    context.setdefault("line", getattr(record, "lineno", None))
    context.setdefault("file", getattr(record, "pathname", None))
    try:
        return json.dumps(context, default=str, sort_keys=True)
    except Exception:
        try:
            return json.dumps({"_unserializable": True}, default=str)
        except Exception:
            return "{}"


class DatabaseLogHandler(logging.Handler):
    """Root-logger handler persisting WARNING+ records to the log table.

    Failure-isolated by construction (BUG-311 discipline): every statement
    that can raise — the settings read, the store construction, the driver
    write — sits inside a try/except, and a sink fault degrades to a dropped
    record and a debug line, never to an exception reaching the caller. The
    trading hot path is unaffected: INFO/DEBUG records are filtered at the
    handler level (``setLevel(WARNING)``) before any work happens, and the
    write is one bounded INSERT.

    The handler is RECURSION-SAFE: the store's own failure log line goes
    through this handler too, so a persistent DB fault would echo forever.
    A thread-local re-entrance guard breaks that loop on the first recursion.
    """

    _REC_KEY = "_nse_db_log_sink_active"

    def __init__(
        self,
        config: DatabaseConfig | None = None,
        *,
        min_level: int = logging.WARNING,
        enabled: bool | None = None,
    ) -> None:
        super().__init__(level=min_level)
        self._cfg = config
        self._store: DatabaseLogStore | None = None
        self._provider_name = ""
        self._domain = ""
        # Resolved once, lazily, on the first record: constructing a driver
        # before logging is configured can recurse into logging itself.
        self._resolved = False
        self._enabled = enabled

    # -- activation ------------------------------------------------------

    def _resolve(self) -> None:
        """Bind the store + provider label; never raises."""
        if self._resolved:
            return
        self._resolved = True
        try:
            cfg = self._cfg or load_database_config("audit")
            self._cfg = cfg
            self._store = DatabaseLogStore(config=cfg)
            provider = getattr(cfg, "provider", None)
            self._provider_name = str(getattr(provider, "value", provider) or "unknown")
            self._domain = str(getattr(cfg, "domain", "") or "audit")
        except Exception:
            # A misconfigured DB must not disable the console log: the store
            # is dropped and records fall to the file/console handlers only.
            self._store = None

    def _is_enabled(self) -> bool:
        """Operator switch + explicit override; never raises."""
        if self._enabled is not None:
            return bool(self._enabled)
        try:
            return log_persistence_enabled()
        except Exception:
            return LOG_PERSISTENCE_DEFAULT_ENABLED

    # -- emit ------------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not self._is_enabled():
                return
            self._resolve()
            store = self._store
            if store is None:
                return
            # Recursion guard: the store's own error log line re-enters here.
            already = getattr(_thread_local, self._REC_KEY, False)
            if already:
                return
            setattr(_thread_local, self._REC_KEY, True)
            try:
                self._persist(store, record)
            finally:
                setattr(_thread_local, self._REC_KEY, False)
        except Exception:
            # BUG-122 discipline: a logging failure never reaches the caller.
            self.handleError(record)

    def _persist(self, store: DatabaseLogStore, record: logging.LogRecord) -> None:
        level = record.levelname or logging.getLevelName(record.levelno)
        if level not in ("WARNING", "ERROR", "CRITICAL"):
            return
        logger_name = record.name or ""
        # structlog's ``event`` is the message; the rendered text is what a
        # plain stdlib caller passed. Prefer the bound event name.
        raw = getattr(record, "msg", None)
        event = ""
        if isinstance(raw, dict):
            event = str(raw.get("event") or "")
        if not event:
            try:
                event = record.getMessage()
            except Exception:
                event = ""
        component = ""
        if isinstance(raw, dict):
            component = str(raw.get("component") or raw.get("module") or "")
        if not component:
            component = logger_name
        error_code = ""
        if isinstance(raw, dict):
            error_code = str(raw.get("error_code") or raw.get("code") or "")
        correlation_id = ""
        if isinstance(raw, dict):
            correlation_id = str(raw.get("correlation_id") or raw.get("request_id") or "")
        full_trace = _render_trace(record)
        # When a traceback is present, the exception's type+message belongs in
        # error_message so the row is self-describing without joining the trace.
        error_message = ""
        exc_info = _resolve_exc_info(record)
        if exc_info:
            try:
                error_message = f"{exc_info[0].__name__}: {exc_info[1]}"
            except Exception:
                error_message = ""
        entry = DatabaseLogEntry(
            level=level,
            provider=self._provider_name,
            domain=self._domain,
            operation=event or logger_name,
            repository="",
            query_name="",
            duration_ms=0.0,
            rows=0,
            error_code=error_code,
            error_message=error_message,
            correlation_id=correlation_id,
            masked_sql="",
            event_name=event,
            logger_name=logger_name,
            full_trace=full_trace,
            context_json=_build_context(record),
            fingerprint=_fingerprint(level, logger_name, event, error_code, component),
        )
        store.record(entry)


_thread_local = threading.local()


def install_database_log_handler(
    config: DatabaseConfig | None = None,
    *,
    enabled: bool | None = None,
) -> DatabaseLogHandler | None:
    """Install the app-wide DB sink on the root logger; idempotent.

    Called by ``observability.logging.configure_logging`` (both boot paths:
    the double-click launcher and ``nexus start``). Returns the handler so a
    test can assert on it, or ``None`` when the sink could not be built (the
    console/file handlers stay authoritative in that case).

    Idempotent: a prior sink from this module is removed first, so
    re-configuring logging (a boot path that runs twice, a test that
    re-enters ``configure_logging``) never accumulates TWO DB sinks — which
    would double-persist every record and is precisely the duplication the
    sink exists to remove. Mirrors ``configure_logging``'s own
    replace-your-own-handlers contract (BUG-307C).
    """
    try:
        uninstall_database_log_handler()
        handler = DatabaseLogHandler(config=config, enabled=enabled)
        _mark_sink_handler(handler)
        root = logging.getLogger()
        root.addHandler(handler)
        return handler
    except Exception:
        return None


def uninstall_database_log_handler() -> None:
    """Remove every handler installed by :func:`install_database_log_handler`."""
    try:
        root = logging.getLogger()
        for h in list(root.handlers):
            if _is_sink_handler(h):
                root.removeHandler(h)
                with contextlib.suppress(Exception):
                    h.close()
    except Exception:
        pass


def _is_sink_handler(handler: logging.Handler) -> bool:
    return getattr(handler, "_nse_db_log_sink", None) is _SINK_MARKER


def _mark_sink_handler(handler: logging.Handler) -> logging.Handler:
    handler._nse_db_log_sink = _SINK_MARKER  # type: ignore[attr-defined]
    return handler


_SINK_MARKER: Any = object()
