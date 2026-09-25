"""Query-failure observability for the PostgreSQL layer (DB-FABRIC-OBS).

The operator's ask: "add FULL log ERROR OR WARNING for Queries on Postgres so
we can trace problems". The live engine log (2026-09-25T23:44-23:47) showed
four shapes that made PG query problems untraceable:

  1. ``[DB-FABRIC] operational write failed domain=ops_shadow
     op=governance.record_event error=syntax error at or near "OR"`` — the
     failing SQL itself was never logged, nor the placeholder/arity mismatch
     that caused it;
  2. ``[DB-FABRIC] audit provider read degraded ... no read plane registered
     for domain 'audit'`` — repeated 368 times in three minutes: a warning so
     noisy that operators stop reading the log;
  3. ``Audit batch insert failed ... error=the query has 0 placeholders but
     32 parameters were passed`` — the message was truncated to 500 chars and
     the failing QUERY was absent entirely;
  4. a dead pooled connection or a pool checkout timeout surfaced only as a
     generic exception with no pool identity and no pool stats.

Everything here is a DB-layer helper: the engine tick path imports nothing new
and gains no logging call (INV-001 stays intact). Call sites are the pool, the
planes, and the store adapters that already own the failure.

Redaction contract
------------------
A failure is only traceable when the SQL text is in the log, but the SQL text
must never carry a credential or a bound value. :func:`mask_query_text`
applies the same discipline as :func:`nexus_scalp.database.config.mask_url_password`
:
  * parameters are counted, never rendered — the arity mismatch in (3) is
    proven by ``placeholder_count``/``arg_count`` alone;
  * DSN-shaped substrings inside the statement are masked in place (a
    ``password=...`` literal landing in a query is exactly how (1) escaped
    tracing while leaking a secret);
  * any residual high-entropy token (a key, a bearer token, an MT5 password)
    is collapsed to ``***`` so the statement shape is preserved and the
    secret is not.

:func:`mask_url_password` is reused verbatim for DSN masking so there is one
masking discipline in the codebase, not two.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nexus_scalp.database.config import mask_url_password
from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:
    from types import TracebackType

logger = get_logger("nexus_scalp.database.query_logging")

#: Slow-query WARNING threshold in milliseconds. A query that SUCCEEDS but
#: exceeds this logs once with the statement, the measured duration and the
#: row count so a performance regression is traceable to a query, not a
#: "something is slow". Module constant (not buried in a config object) so it
#: is tunable from one place and monkeypatchable from tests.
PG_SLOW_QUERY_THRESHOLD_MS: float = 200.0

#: Degraded-read escalation: the same (domain, reason) pair degrading for
#: this many CONSECUTIVE occurrences stops being a WARNING and becomes an
#: ERROR — a condition that persists past this point is an incident, and 368
#: identical warnings is how operators stop reading the log.
PG_DEGRADED_ESCALATION_AFTER: int = 10

#: A repeated degraded-read WARNING is emitted every Nth occurrence instead of
#: every occurrence (the first one is always emitted, and the escalation
#: above fires independently). Keeps the log readable while the cumulative
#: counter still records every degradation.
PG_DEGRADED_LOG_EVERY: int = 20

#: Cap on the SQL text rendered into a log line. Failures are diagnosed from
#: the statement shape + the arity mismatch; a pathological multi-MB query
#: must not be able to flood the error log.
_MAX_SQL_LOG_CHARS: int = 4000

# -- redaction ------------------------------------------------------------

#: Key=value secret shapes that survive :func:`mask_query_text`. Matches both
#: a libpq ``password=x`` pair and a URL userinfo block; the value is masked
#: in place so the key name survives for tracing. A QUOTED value is matched
#: too (``password='...'``) — a credential that landed in a statement is
#: exactly the leak this masker exists to stop, and the masking is lossy by
#: design: only the statement SHAPE is needed to trace a failure, and
#: placeholder counting runs on the ORIGINAL statement, not the masked one.
_SECRET_KV = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|auth)\s*([:=])\s*(?:'([^']*)'|\"([^\"]*)\"|[^\s;)]*)"
)
#: URL userinfo (``scheme://user:pw@host``) — the value is masked in place so
#: the host survives for tracing.
_USERINFO = re.compile(r"(://[^:/@\s]+:)([^@\s]*)(@)")
#: A bare high-entropy token (a key with no key name). The value is masked
#: ONLY when it matches this whole-token validator: mixed case + digits +
#: length is the same discipline the observability layer's own redactor uses
#: (BUG-121), so an ordinary identifier or a lowercase UUID is never
#: collapsed while a real credential is.
_TOKEN = re.compile(r"[A-Za-z0-9_\-]+")
_TOKEN_IS_SECRET = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z0-9_\-]{16,}$")
#: PostgreSQL ``%s``/``$1`` placeholders — counted, and their text is kept
#: verbatim (they carry no data).
_PG_PLACEHOLDER = re.compile(r"%(?:s|\(\d+\)s)|\$\d+")
#: SQLite qmark placeholder — kept for statements logged before translation.
_QMARK_PLACEHOLDER = re.compile(r"\?")


def mask_query_text(sql: Any) -> str:
    """Render a SQL statement for logging: shape preserved, secrets out.

    Never raises: a logging-path failure must never become the failure. A
    non-string statement degrades to a short type hint rather than ``repr``,
    which would surface bound values.

    * DSN-shaped substrings are masked with the DSN masker (same helper the
      whole codebase uses for connection strings);
    * inline ``password=...`` / ``token=...`` pairs are collapsed;
    * a bare high-entropy token (a key with no key name) becomes ``***``;
    * the text is bounded so a pathological statement cannot flood the log.
    """
    if not isinstance(sql, str):
        return f"<non-string statement:{type(sql).__name__}>"
    try:
        masked = sql
        if "://" in masked:
            masked = mask_url_password(masked)
        masked = _SECRET_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}***", masked)
        masked = _USERINFO.sub(r"\1***\3", masked)
        # Bare high-entropy tokens: validate the WHOLE token before masking so
        # an ordinary long identifier or a lowercase UUID is never collapsed
        # (over-redaction is its own traceability bug), and everything around
        # a masked token stays verbatim so the statement shape is preserved.
        masked = _TOKEN.sub(
            lambda m: "***" if _TOKEN_IS_SECRET.match(m.group(0)) else m.group(0), masked
        )
        if len(masked) > _MAX_SQL_LOG_CHARS:
            head = masked[: _MAX_SQL_LOG_CHARS // 2]
            tail = masked[-(_MAX_SQL_LOG_CHARS // 3) :]
            masked = f"{head}…<truncated {len(sql) - len(head) - len(tail)} chars>…{tail}"
        return masked
    except Exception:
        return "<statement masking failed>"


def placeholder_count(sql: Any) -> int:
    """Number of bind placeholders in a statement (0 for a non-string).

    Counts occurrences, not unique shapes: ``( %s, %s, %s )`` has THREE
    placeholders, and that count is what proves an arity mismatch against
    ``arg_count``. Mixed placeholder styles are never a real statement, so
    the larger of the two counts wins rather than summing.
    """
    if not isinstance(sql, str):
        return 0
    try:
        pg = len(_PG_PLACEHOLDER.findall(sql))
        if pg:
            return pg
        return len(_QMARK_PLACEHOLDER.findall(sql))
    except Exception:
        return 0


def arg_count(args: Any) -> int:
    """Number of bound values passed alongside a statement (never raises).

    Accepts a sequence of values or a sequence of parameter rows
    (``executemany`` shape); the arity is what proves the mismatch in the
    live failure, and a value must never be rendered to compute it.
    """
    try:
        if args is None:
            return 0
        if isinstance(args, (str, bytes, bytearray, dict)):
            return 1
        n = len(args)
        if n == 0:
            return 0
        first = args[0]
        if isinstance(first, (tuple, list)):
            # executemany: count the first parameter row.
            return len(first)
        return n
    except Exception:
        return 0


def _full_error(exc: BaseException) -> str:
    """The full error message (not truncated), with the type for triage."""
    try:
        name = type(exc).__name__
        msg = str(exc)
        if not msg:
            return name
        return f"{name}: {msg}"
    except Exception:
        return "<error rendering failed>"


def _log(fn: Callable[..., None], msg: str, *args: Any, **kwargs: Any) -> None:
    """Emit a log record; a logging-path failure never propagates."""
    try:
        fn(msg, *args, **kwargs)
    except Exception:
        try:
            logger.debug("query logging path failed; record dropped")
        except Exception:
            pass


def log_query_failure(
    *,
    operation: str,
    exc: BaseException,
    sql: Any = "",
    args: Any = None,
    domain: str = "",
    kind: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """ERROR for one failed PG query: full context, no bound values.

    Fields a trace needs: domain, operation, error_type, the FULL error
    message (no 500-char truncation), the masked SQL statement, and
    placeholder_count vs arg_count — the pair that proves the live
    ``syntax error at or near "OR"`` / ``0 placeholders but 32 parameters``
    class of failure.
    """
    context: dict[str, Any] = {
        "domain": domain or "",
        "operation": operation or "",
        "error_type": type(exc).__name__,
        "error_message": _full_error(exc),
        "sql": mask_query_text(sql),
        "placeholder_count": placeholder_count(sql),
        "arg_count": arg_count(args),
    }
    if kind:
        context["kind"] = kind
    if extra:
        for k, v in extra.items():
            if k not in context:
                context[k] = v
    # The message carries the headline fields for a human scanning the severity
    # log; the kwargs carry the SAME fields machine-readably (structlog binds
    # them onto the record). One record, one structured payload.
    _log(
        logger.error,
        "[PG-QUERY] query failed domain=%s op=%s error_type=%s placeholders=%d args=%d",
        context["domain"],
        context["operation"],
        context["error_type"],
        context["placeholder_count"],
        context["arg_count"],
        **context,
    )


def log_slow_query(
    *,
    operation: str,
    duration_ms: float,
    sql: Any = "",
    rows: int | None = None,
    domain: str = "",
) -> None:
    """WARNING for a query that SUCCEEDED but exceeded the slow threshold.

    Cheap on the fast path by contract: the caller records only
    ``time.monotonic()`` and calls this only when the threshold is crossed —
    this function does the formatting. ``rows`` is the fetched row count when
    the caller already has it (never a second fetch).
    """
    if duration_ms < PG_SLOW_QUERY_THRESHOLD_MS:
        return
    context: dict[str, Any] = {
        "domain": domain or "",
        "operation": operation or "",
        "duration_ms": round(float(duration_ms), 2),
        "threshold_ms": PG_SLOW_QUERY_THRESHOLD_MS,
        "sql": mask_query_text(sql),
    }
    if rows is not None:
        context["rows"] = int(rows)
    _log(
        logger.warning,
        "[PG-QUERY] slow query domain=%s op=%s duration_ms=%.2f threshold_ms=%.2f",
        context["domain"],
        context["operation"],
        context["duration_ms"],
        context["threshold_ms"],
        **context,
    )


# -- slow-query timing (monotonic, never datetime) -------------------------


class QueryTimer:
    """Monotonic timer for the slow-query WARNING.

    ``time.monotonic()`` only (never ``datetime`` — wall clock skew would make
    a duration lie). The start is recorded on ``__enter__``; the WARNING is
    emitted on ``__exit__`` ONLY when the measured duration crossed the
    threshold, so the fast path costs one subtraction and one comparison and
    never formats a log line.

    Never raises from a timing or logging failure: ``exc_type`` is returned
    untouched so the caller's exception propagates unchanged. Aliased as
    ``query_timer`` for call sites that read it as a context-manager helper.
    """

    __slots__ = ("_domain", "_op", "_sql", "duration_ms", "rows", "start")

    def __init__(
        self,
        operation: str,
        sql: Any = "",
        *,
        domain: str = "",
        rows: int | None = None,
    ) -> None:
        self._op = operation
        self._sql = sql
        self._domain = domain
        self.rows = rows
        self.start: float | None = None
        self.duration_ms: float = 0.0

    def __enter__(self) -> QueryTimer:
        try:
            self.start = time.monotonic()
        except Exception:
            self.start = None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.start is None:
            return None
        try:
            elapsed = (time.monotonic() - self.start) * 1000.0
        except Exception:
            return None
        self.duration_ms = elapsed
        if exc_type is None:
            log_slow_query(
                operation=self._op,
                duration_ms=elapsed,
                sql=self._sql,
                rows=self.rows,
                domain=self._domain,
            )
        return None


#: Call-site name: reads as the context-manager helper it is.
query_timer = QueryTimer


# -- degraded-read deduplication + escalation ------------------------------


class _DegradedState:
    """Per-(domain, reason) degraded-read bookkeeping (process-local)."""

    __slots__ = ("consecutive", "emitted", "first_at", "total")

    def __init__(self) -> None:
        self.total = 0
        self.consecutive = 0
        self.emitted = 0
        self.first_at = time.monotonic()


class DegradedReadTracker:
    """Deduplicate repeated degraded-read warnings; escalate persistence.

    Replaces the 368-identical-warning pattern: the FIRST occurrence for a
    ``(domain, reason)`` pair logs at WARNING with full context, repeats drop
    to DEBUG with a cumulative counter, and a pair that degrades for more than
    :data:`PG_DEGRADED_ESCALATION_AFTER` consecutive occurrences escalates to
    ERROR — that is the difference between "a provider is not provisioned"
    and "reads have been silently wrong for a minute".

    Thread-safe through a single lock; the hot read path takes it only when a
    degradation actually happens (the healthy path never calls in).
    """

    __slots__ = ("_lock", "_states")

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._states: dict[tuple[str, str], _DegradedState] = {}

    def note(
        self,
        *,
        domain: str,
        reason: str,
        operation: str = "",
        detail: str = "",
    ) -> None:
        """Record one degraded read and emit the right single log record.

        Never raises: this is a logging path on a read that already failed to
        route, so a logging fault here would turn a degraded read into a
        crash.
        """
        try:
            key = (domain or "", reason or "")
            with self._lock:
                state = self._states.get(key)
                if state is None:
                    state = _DegradedState()
                    self._states[key] = state
                state.total += 1
                state.consecutive += 1
                escalated = state.consecutive > PG_DEGRADED_ESCALATION_AFTER
                should_warn = (
                    state.emitted == 0 or state.total % PG_DEGRADED_LOG_EVERY == 0 or escalated
                )
                state.emitted += 1 if should_warn else 0
                total = state.total
                consecutive = state.consecutive
                emitted = state.emitted
        except Exception:
            return

        context = {
            "domain": key[0],
            "reason": key[1],
            "operation": operation or "",
            "occurrences": total,
            "consecutive": consecutive,
            "emissions": emitted,
        }
        if detail:
            context["detail"] = detail
        if escalated:
            _log(
                logger.error,
                "[PG-QUERY] read degraded ESCALATED domain=%s reason=%s "
                "consecutive=%d occurrences=%d — degrading beyond "
                "PG_DEGRADED_ESCALATION_AFTER=%d; reads are returning "
                "documented defaults, data is not being served from this "
                "domain",
                key[0],
                key[1],
                consecutive,
                total,
                PG_DEGRADED_ESCALATION_AFTER,
                **context,
            )
            return
        if should_warn:
            _log(
                logger.warning,
                "[PG-QUERY] read degraded domain=%s reason=%s occurrences=%d "
                "consecutive=%d -> documented default (no read plane for "
                "this domain; repeats deduplicated at DEBUG)",
                key[0],
                key[1],
                total,
                consecutive,
                **context,
            )
            return
        _log(
            logger.debug,
            "[PG-QUERY] read degraded (deduplicated) domain=%s reason=%s "
            "occurrences=%d consecutive=%d",
            key[0],
            key[1],
            total,
            consecutive,
            **context,
        )

    def reset(self, domain: str = "", reason: str = "") -> None:
        """Clear bookkeeping (a recovered domain starts counting fresh)."""
        try:
            with self._lock:
                if not domain and not reason:
                    self._states.clear()
                    return
                for key in [
                    k
                    for k in self._states
                    if (not domain or k[0] == domain) and (not reason or k[1] == reason)
                ]:
                    self._states.pop(key, None)
        except Exception:
            return

    def snapshot(self) -> dict[str, Any]:
        """Read-only view of degradation state for a health surface."""
        try:
            with self._lock:
                return {
                    f"{d}|{r}": {
                        "occurrences": s.total,
                        "consecutive": s.consecutive,
                        "emissions": s.emitted,
                    }
                    for (d, r), s in self._states.items()
                }
        except Exception:
            return {}


#: Process-global tracker for degraded reads. Reused by every store adapter
#: so the deduplication is per (domain, reason), not per caller — three
#: stores hitting the same unprovisioned domain produce one warning, not
#: three floods.
degraded_reads = DegradedReadTracker()


def note_degraded_read(
    *,
    domain: str,
    reason: str,
    operation: str = "",
    detail: str = "",
) -> None:
    """Record a degraded read on the process-global tracker (never raises)."""
    try:
        degraded_reads.note(domain=domain, reason=reason, operation=operation, detail=detail)
    except Exception:
        return


# -- connection / pool failure classification ------------------------------


def classify_pool_error(exc: BaseException) -> dict[str, Any]:
    """Classify a pool/connection failure: checkout timeout vs server-side.

    Inspects the exception TYPE (not its message, which is locale-dependent):
    ``psycopg_pool.PoolTimeout`` is a client-side exhaustion, psycopg's
    ``OperationalError`` is a server/network condition, ``InterfaceError`` is
    a dead handle the pool already knows about. Falls back to the base type
    name for anything else so the record is never empty.
    """
    name = type(exc).__name__
    qualname = ""
    try:
        qualname = type(exc).__module__ + "." + name
    except Exception:
        qualname = name
    if "PoolTimeout" in qualname or name == "PoolTimeout":
        return {
            "failure_class": "checkout_timeout",
            "client_side": True,
            "pool_error_type": name,
        }
    if name in {"PoolClosed", "TooManyRequests"} or "PoolClosed" in qualname:
        return {
            "failure_class": "pool_closed",
            "client_side": True,
            "pool_error_type": name,
        }
    if name in {"InterfaceError", "PoolError"}:
        return {
            "failure_class": "connection_dead",
            "client_side": True,
            "pool_error_type": name,
        }
    if name in {"OperationalError", "CannotConnectNowError"} or "OperationalError" in qualname:
        return {
            "failure_class": "server_error",
            "client_side": False,
            "pool_error_type": name,
        }
    if "ProgrammingError" in qualname:
        return {
            "failure_class": "statement_error",
            "client_side": True,
            "pool_error_type": name,
        }
    return {
        "failure_class": "other",
        "client_side": None,
        "pool_error_type": name,
    }


def log_pool_failure(
    *,
    pool_name: str,
    operation: str,
    exc: BaseException,
    domain: str = "",
    stats: dict[str, Any] | None = None,
) -> None:
    """ERROR for a pool/connection failure with pool identity and stats.

    ``stats`` comes from the pool's own ``stats()`` surface
    (``PgPool.stats()``); a checkout timeout with 8/8 connections in use is a
    capacity problem, the same timeout with 0 in use is a dead server — the
    record has to carry which one it is.
    """
    classification = classify_pool_error(exc)
    context: dict[str, Any] = {
        "domain": domain or "",
        "pool": pool_name or "",
        "operation": operation or "",
        "error_type": type(exc).__name__,
        "error_message": _full_error(exc),
        "failure_class": classification["failure_class"],
        "client_side": classification["client_side"],
        "pool_error_type": classification["pool_error_type"],
        "pool_stats": stats if isinstance(stats, dict) else {},
    }
    _log(
        logger.error,
        "[PG-POOL] connection failure pool=%s op=%s class=%s client_side=%s",
        context["pool"],
        context["operation"],
        context["failure_class"],
        context["client_side"],
        **context,
    )


def pool_failure_guard(
    fn: Callable[..., Any],
    *,
    pool_name: str,
    operation: str,
    domain: str = "",
    stats: Callable[[], dict[str, Any]] | dict[str, Any] | None = None,
    sql: Any = "",
    args: Any = None,
) -> Any:
    """Run ``fn``, classifying and logging any pool/query failure.

    The one seam call sites use so a checkout timeout, a dead connection or a
    query error all surface as a structured ERROR carrying the pool name, the
    failure class, the pool stats and the masked statement. Re-raises the
    original exception: observability never changes the failure contract.
    """
    try:
        return fn()
    except BaseException as exc:
        stats_val: dict[str, Any] | None = None
        if callable(stats):
            with _SuppressCtx():
                stats_val = stats()
        elif isinstance(stats, dict):
            stats_val = stats
        cls = classify_pool_error(exc)
        if cls["failure_class"] in {"checkout_timeout", "pool_closed", "connection_dead"}:
            log_pool_failure(
                pool_name=pool_name,
                operation=operation,
                exc=exc,
                domain=domain,
                stats=stats_val,
            )
        else:
            log_query_failure(
                operation=operation,
                exc=exc,
                sql=sql,
                args=args,
                domain=domain,
                extra={"pool": pool_name, "pool_stats": stats_val or {}},
            )
        raise


class _SuppressCtx:
    """``contextlib.suppress(Exception)`` inlined: this module is a leaf."""

    def __enter__(self) -> _SuppressCtx:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return True


__all__ = [
    "PG_DEGRADED_ESCALATION_AFTER",
    "PG_DEGRADED_LOG_EVERY",
    "PG_SLOW_QUERY_THRESHOLD_MS",
    "DegradedReadTracker",
    "QueryTimer",
    "arg_count",
    "classify_pool_error",
    "degraded_reads",
    "log_pool_failure",
    "log_query_failure",
    "log_slow_query",
    "mask_query_text",
    "note_degraded_read",
    "placeholder_count",
    "pool_failure_guard",
    "query_timer",
]
