"""Telegram notifier: bounded queue + worker + DNS-fallback transport +
domain notifications — FACADE over cohesive mixins.

Modularization (Agent-5, CHG-0032-A1 program): public identity unchanged —
``TelegramNotifier``, ``NotificationRecord``, ``classify_http_response``
and every method resolve exactly as before. Method clusters live in
verbatim-extracted siblings:

    tg_transport.py      DNS-fallback HTTPS plumbing + response parsing +
                         transport constants (TransportMixin; BUG-131 semantics)
    tg_notifications.py  the ~40 notify_* operator-facing message builders
                         (NotificationsMixin; exact message text preserved)

The core notifier keeps ALL state (queue, worker thread, session stats,
dedup cache) plus send/_dispatch_record/health/heartbeat/shutdown and the
formatting helpers. Mixins are stateless carriers (no __init__).
Backward-compat re-exports: _TIMEOUT_ERRORS/_DNS_POISON_BLACKHOLE_RANGES/
_TELEGRAM_FALLBACK_IPS (single source: tg_transport.py).
USED BY: live_engine, order_manager, web/server, ci_telegram_reporter, tests.
"""

from __future__ import annotations

import html
import json
import logging
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, ClassVar

from nexus_scalp.settings import new_correlation_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error taxonomy (spec §5)
# ---------------------------------------------------------------------------
TELEGRAM_CONFIG_ERROR = "TELEGRAM_CONFIG_ERROR"
TELEGRAM_AUTH_ERROR = "TELEGRAM_AUTH_ERROR"
TELEGRAM_TARGET_ERROR = "TELEGRAM_TARGET_ERROR"
TELEGRAM_NETWORK_ERROR = "TELEGRAM_NETWORK_ERROR"
TELEGRAM_TIMEOUT = "TELEGRAM_TIMEOUT"
TELEGRAM_RATE_LIMIT = "TELEGRAM_RATE_LIMIT"
TELEGRAM_SERVER_ERROR = "TELEGRAM_SERVER_ERROR"
TELEGRAM_HTTP_ERROR = "TELEGRAM_HTTP_ERROR"
TELEGRAM_API_ERROR = "TELEGRAM_API_ERROR"
TELEGRAM_SERIALIZATION_ERROR = "TELEGRAM_SERIALIZATION_ERROR"
TELEGRAM_QUEUE_ERROR = "TELEGRAM_QUEUE_ERROR"
TELEGRAM_WORKER_ERROR = "TELEGRAM_WORKER_ERROR"
TELEGRAM_UNKNOWN_ERROR = "TELEGRAM_UNKNOWN_ERROR"
TELEGRAM_DNS_BLOCKED = "TELEGRAM_DNS_BLOCKED"

#: Non-retryable target-side rejections (4xx classes that never succeed on retry).
_NON_RETRYABLE_API_CODES = {400, 401, 403, 404, 409, 420}
_RETRYABLE_API_CODES = {429} | set(range(500, 600))
_TIMEOUT_ERRORS = (TimeoutError, urllib.error.URLError)

#: RFC 2544/6890 benchmark block — the classic DNS-poisoning answer.
#: When api.telegram.org resolves here, the upstream resolver (ISP/router/
#: MITM) is returning a blackhole and every Telegram HTTPS call will hang.
_DNS_POISON_BLACKHOLE_RANGES: tuple[tuple[int, int], ...] = (
    (0xC0000000, 0xC000FFFF),  # 192.0.0.0/24  (RFC 6890 TEST-NET-1)
    (0xC6120000, 0xC613FFFF),  # 198.18.0.0/15 (RFC 2544 benchmarking)
    (0xC6336400, 0xC63364FF),  # 198.51.100.0/24 (TEST-NET-2)
    (0xCB007100, 0xCB0071FF),  # 203.0.113.0/24 (TEST-NET-3)
    (0x7F000000, 0x7F0000FF),  # 127.0.0.0/8 localhost block
)

#: Known-good Telegram datacenter IPs (IPv4), used as a DNS-poison bypass.
_TELEGRAM_FALLBACK_IPS: tuple[str, ...] = (
    "149.154.167.220",
    "149.154.167.222",
    "149.154.175.50",
    "91.108.56.130",
)

#: Worker state exposed via health_state() (spec §10).
WORKER_READY = "READY"
WORKER_DEGRADED = "DEGRADED"
WORKER_STOPPED = "STOPPED"
WORKER_STARTING = "STARTING"


@dataclass
class NotificationRecord:
    """One notification's full forensic lifecycle."""

    notification_id: str
    correlation_id: str
    event_type: str
    priority: str
    target_class: str
    created_at: float
    attempts: int = 0
    last_attempt_at: float | None = None
    status: str = "ENQUEUED"  # ENQUEUED|SEND_START|SEND_RESULT|SEND_FAILED|DELIVERED|FAILED_FINAL
    http_status: int | None = None
    telegram_ok: bool | None = None
    telegram_error_code: int | None = None
    description: str = ""
    category: str = ""
    retryable: bool = False
    message_id: int | None = None
    reply_to_message_id: int | None = None
    text: str = ""
    callback: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "correlation_id": self.correlation_id,
            "event_type": self.event_type,
            "priority": self.priority,
            "target_class": self.target_class,
            "created_at": self.created_at,
            "attempts": self.attempts,
            "status": self.status,
            "http_status": self.http_status,
            "telegram_ok": self.telegram_ok,
            "telegram_error_code": self.telegram_error_code,
            "description": self.description[:200],
            "category": self.category,
            "retryable": self.retryable,
            "message_id": self.message_id,
        }


def classify_http_response(
    http_status: int | None,
    body: bytes | None,
) -> dict[str, Any]:
    """Classify a Telegram HTTP response into the error taxonomy.

    Returns {category, retryable, severity, safe_message, telegram_error_code}.
    NEVER includes the raw body when it may carry secrets.
    """
    telegram_code: int | None = None
    description = ""
    if body:
        try:
            parsed = json.loads(body.decode("utf-8", errors="replace"))
            if isinstance(parsed, dict):
                telegram_code = parsed.get("error_code")
                description = str(parsed.get("description") or "")[:160]
        except (ValueError, UnicodeDecodeError):
            pass

    if http_status == 200:
        # 200 + ok=false is still a Telegram-level failure.
        if telegram_code in _NON_RETRYABLE_API_CODES:
            return {
                "category": _category_for_code(telegram_code, description),
                "retryable": False,
                "severity": "ERROR",
                "safe_message": description or "Telegram rejected the message",
                "telegram_error_code": telegram_code,
            }
        return {
            "category": TELEGRAM_API_ERROR,
            "retryable": telegram_code in _RETRYABLE_API_CODES,
            "severity": "ERROR" if telegram_code else "WARNING",
            "safe_message": description or "Telegram API rejected the message",
            "telegram_error_code": telegram_code,
        }
    if http_status == 429:
        return {
            "category": TELEGRAM_RATE_LIMIT,
            "retryable": True,
            "severity": "WARNING",
            "safe_message": "Telegram rate limit exceeded",
            "telegram_error_code": telegram_code,
        }
    if http_status is not None and 500 <= http_status < 600:
        return {
            "category": TELEGRAM_SERVER_ERROR,
            "retryable": True,
            "severity": "ERROR",
            "safe_message": f"Telegram server error (HTTP {http_status})",
            "telegram_error_code": telegram_code,
        }
    if http_status is not None and 400 <= http_status < 500:
        return {
            "category": _category_for_code(http_status, description),
            "retryable": False,
            "severity": "ERROR",
            "safe_message": description or f"Telegram HTTP error {http_status}",
            "telegram_error_code": telegram_code or http_status,
        }
    return {
        "category": TELEGRAM_HTTP_ERROR,
        "retryable": True,
        "severity": "ERROR",
        "safe_message": f"Unexpected HTTP status {http_status}",
        "telegram_error_code": telegram_code,
    }


def _category_for_code(code: int, description: str) -> str:
    if code == 401:
        return TELEGRAM_AUTH_ERROR
    if code in (400, 404):
        return TELEGRAM_TARGET_ERROR
    if code == 403:
        # 403:bot was blocked by the user / can't initiate chat
        return TELEGRAM_TARGET_ERROR
    if code == 429:
        return TELEGRAM_RATE_LIMIT
    return TELEGRAM_API_ERROR


# --- extracted mixins are wired AFTER the names above (NotificationRecord /
# classify_http_response/_category_for_code) because tg_transport imports them
# from here; the transport constants are single-sourced in tg_transport and
# re-exported below for backward compatibility (DNS regression tests).

from nexus_scalp.observability.tg_notifications import NotificationsMixin  # noqa: E402
from nexus_scalp.observability.tg_transport import (  # noqa: E402
    _DNS_POISON_BLACKHOLE_RANGES,  # noqa: F401
    _TELEGRAM_FALLBACK_IPS,  # noqa: F401
    _TIMEOUT_ERRORS,  # noqa: F401
    TransportMixin,
)


# record_placeholder is referenced by tg_transport at runtime; the verbatim
# implementation from the original module tail lives at the module tail below
# (bound before any call). Kept as a single definition — see tail.
def record_placeholder() -> str:
    return new_correlation_id("nid")


class TelegramNotifier(TransportMixin, NotificationsMixin):
    """Queue-backed Telegram alert engine with a fully observable lifecycle."""

    SEVERITY_WEIGHTS: ClassVar[dict[str, int]] = {
        "INFO": 1,
        "WARNING": 2,
        "ERROR": 3,
        "CRITICAL": 4,
    }

    def __init__(
        self,
        bot_token: str,
        admin_id: str,
        enabled: bool = True,
        environment: str = "production",
        minimum_severity: str = "INFO",
        timeout_seconds: float = 4.0,
        maximum_retries: int = 3,
        retry_backoff: float = 2.0,
        queue_capacity: int = 100,
        rate_limit: int = 20,
        deduplication_window: float = 60.0,
        cooldown_seconds: float = 300.0,
        graceful_shutdown_timeout: float = 5.0,
        api_base: str = "https://api.telegram.org",
        worker_interval: float = 0.1,
    ) -> None:
        self.bot_token = bot_token
        self.admin_id = admin_id or ""
        self.enabled = enabled and bool(bot_token) and bool(self.admin_id)
        self.environment = environment
        self.minimum_severity = minimum_severity.upper()
        self.timeout_seconds = timeout_seconds
        self.maximum_retries = maximum_retries
        self.retry_backoff = retry_backoff
        self.queue_capacity = queue_capacity
        self.rate_limit = rate_limit
        self.deduplication_window = deduplication_window
        self.cooldown_seconds = cooldown_seconds
        self.graceful_shutdown_timeout = graceful_shutdown_timeout
        self.api_base = api_base

        self._send_url = f"{api_base}/bot{bot_token}/sendMessage" if bot_token else ""
        self._me_url = f"{api_base}/bot{bot_token}/getMe" if bot_token else ""

        # Queue + worker
        self._queue: queue.Queue[NotificationRecord | None] = queue.Queue(maxsize=queue_capacity)
        self._lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pending_tasks_count = 0
        self._sent_timestamps = []  # type: list[float]
        self._recent_messages: dict[str, float] = {}

        # Worker telemetry (heartbeat)
        self._sent_count = 0
        self._failed_count = 0
        self._retry_count = 0
        self._last_success: float | None = None
        self._last_failure: float | None = None
        self._last_failure_category: str = ""
        self._last_heartbeat: float = 0.0
        self._worker_started_at: float | None = None
        self._worker_crash: str = ""
        self._worker_running = False
        self._last_dns_poisoned = False
        # BUG-129: throttle the BLOCKED_NOT_CONFIGURED log (fires on every
        # send() attempt while Telegram is unconfigured; on a hot path that
        # produced ~13 spam warnings per second).
        self._last_blocked_log_time: float = 0.0
        self._blocked_log_count: int = 0

        self.start_worker()

    # =====================================================================
    # Worker lifecycle (spec §10: never die silently)
    # =====================================================================

    def start_worker(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_main,
            name="telegram_queue_worker",
            daemon=True,
        )
        self._worker_started_at = time.time()
        self._worker_thread.start()
        logger.info(
            "[TELEGRAM_WORKER] event=START notification_channel=%s",
            "enabled" if self.enabled else "disabled",
        )

    def _worker_main(self) -> None:
        self._worker_running = True
        logger.info("[TELEGRAM_WORKER] event=RUNNING")
        while not self._stop_event.is_set():
            try:
                try:
                    record = self._queue.get(timeout=0.2)
                except queue.Empty:
                    self._heartbeat()
                    continue
                if record is None:
                    break
                self._dispatch_record(record)
                self._queue.task_done()
            except Exception as exc:
                self._worker_crash = f"{type(exc).__name__}: {exc}"
                self._failed_count += 1
                self._last_failure = time.time()
                self._last_failure_category = TELEGRAM_WORKER_ERROR
                logger.error(
                    "[TELEGRAM_WORKER] event=CRASH exception_type=%s message=%s",
                    type(exc).__name__,
                    exc,
                )
                time.sleep(0.5)
        self._worker_running = False
        logger.info("[TELEGRAM_WORKER] event=STOP")

    def _heartbeat(self) -> None:
        now = time.time()
        if now - self._last_heartbeat < 5.0:
            return
        self._last_heartbeat = now
        try:
            logger.info(
                "[TELEGRAM_WORKER] event=HEARTBEAT queue_size=%d sent=%d failed=%d "
                "last_success=%s last_failure=%s failure_category=%s",
                self._queue.qsize(),
                self._sent_count,
                self._failed_count,
                _fmt_ts(self._last_success),
                _fmt_ts(self._last_failure),
                self._last_failure_category or "-",
            )
        except Exception:  # logging pipeline closed (test teardown) — not fatal
            pass

    def stop_worker(self, timeout: float | None = None) -> None:
        timeout = timeout or self.graceful_shutdown_timeout
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
        logger.info("[TELEGRAM_WORKER] event=STOP_REQUESTED")

    def health_state(self) -> dict[str, Any]:
        """Truthful live worker + channel diagnostics (spec §10/§35)."""
        if not self.enabled:
            status = WORKER_STOPPED
        elif self._worker_running:
            status = WORKER_DEGRADED if self._failed_count > 0 else WORKER_READY
        else:
            status = WORKER_STOPPED
        return {
            "status": status,
            "enabled": self.enabled,
            "configured": bool(self.bot_token and self.admin_id),
            "queue_size": self._queue.qsize(),
            "sent_count": self._sent_count,
            "failed_count": self._failed_count,
            "retry_count": self._retry_count,
            "last_success": _fmt_ts(self._last_success),
            "last_failure": _fmt_ts(self._last_failure),
            "failure_category": self._last_failure_category,
            "worker_started_at": _fmt_ts(self._worker_started_at),
            "worker_crash": self._worker_crash,
            "dns_poisoned": self._last_dns_poisoned,
        }

    # =====================================================================
    # Public send API (never blocks, never raises into the caller)
    # =====================================================================

    def send(
        self,
        html_text: str,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
        severity: str = "INFO",
        event_type: str = "GENERIC",
        correlation_id: str | None = None,
    ) -> int | None:
        """Enqueue a notification; returns message_id only on sync resolution.

        NEVER performs network I/O on the caller's thread. Failure is always
        observable via logs + health_state() — never a bare silent None.
        """
        if not self.enabled:
            # Rate-limited log: a hot-path caller (e.g. per-tick position
            # eval) must never spam WARNING lines while Telegram is simply
            # not configured. First occurrence logs immediately, then at most
            # once per 60s (BUG-129).
            now_log = time.time()
            self._blocked_log_count += 1
            if (now_log - self._last_blocked_log_time) >= 60.0:
                self._last_blocked_log_time = now_log
                logger.warning(
                    "[TELEGRAM] event=BLOCKED_NOT_CONFIGURED severity=%s reason=BOT_TOKEN_OR_ADMIN_MISSING "
                    "notification_id=%s correlation_id=%s blocked_since_start=%d",
                    severity,
                    new_correlation_id("notif"),
                    correlation_id or "-",
                    self._blocked_log_count,
                )
            self._failed_count += 1
            self._last_failure = time.time()
            self._last_failure_category = TELEGRAM_CONFIG_ERROR
            if callback:
                try:
                    callback(None)
                except Exception:
                    pass
            return None

        msg_weight = self.SEVERITY_WEIGHTS.get(severity.upper(), 1)
        min_weight = self.SEVERITY_WEIGHTS.get(self.minimum_severity.upper(), 1)
        if msg_weight < min_weight:
            return None

        record = NotificationRecord(
            notification_id=new_correlation_id("notif"),
            correlation_id=correlation_id or new_correlation_id("corr"),
            event_type=event_type,
            priority=severity.upper(),
            target_class="TELEGRAM",
            created_at=time.time(),
            reply_to_message_id=reply_to_message_id,
            text=html_text,
            callback=callback,
        )

        # Queue capacity: CRITICAL always enqueues; others log an explicit DROP.
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            if severity.upper() != "CRITICAL":
                logger.error(
                    "[TELEGRAM_QUEUE] event=DROP notification_id=%s reason=QUEUE_FULL "
                    "queue_size=%d severity=%s",
                    record.notification_id,
                    self._queue.qsize(),
                    severity,
                )
                self._failed_count += 1
                self._last_failure = time.time()
                self._last_failure_category = TELEGRAM_QUEUE_ERROR
                return None
            # CRITICAL: block briefly rather than drop.
            try:
                self._queue.put(record, timeout=1.0)
            except queue.Full:
                logger.error(
                    "[TELEGRAM_QUEUE] event=DROP notification_id=%s reason=QUEUE_FULL_CRITICAL",
                    record.notification_id,
                )
                return None

        logger.info(
            "[TELEGRAM] event=ENQUEUED notification_id=%s correlation_id=%s "
            "event_type=%s priority=%s queue_size=%d",
            record.notification_id,
            record.correlation_id,
            event_type,
            severity.upper(),
            self._queue.qsize(),
        )

        if callback is not None:
            return None
        # Synchronous-ish wait for tests/simple scripts (bounded, non-blocking for prod).
        deadline = time.time() + 0.05
        while time.time() < deadline:
            if record.status in ("DELIVERED", "FAILED_FINAL", "SEND_FAILED"):
                break
            time.sleep(0.002)
        return record.message_id if record.status == "DELIVERED" else None

    # =====================================================================
    # Worker dispatch (HTTP + verification + bounded retry)
    # =====================================================================

    def _dispatch_record(self, record: NotificationRecord) -> None:
        record.status = "SEND_START"
        record.attempts += 1
        record.last_attempt_at = time.time()
        logger.info(
            "[TELEGRAM] event=SEND_START notification_id=%s correlation_id=%s attempt=%d",
            record.notification_id,
            record.correlation_id,
            record.attempts,
        )

        retries = 0
        backoff = self.retry_backoff
        while True:
            try:
                outcome = self._send_msg_sync(record)
                if outcome["ok"]:
                    record.status = "DELIVERED"
                    record.message_id = outcome["message_id"]
                    record.http_status = outcome["http_status"]
                    record.telegram_ok = True
                    self._sent_count += 1
                    self._last_success = time.time()
                    logger.info(
                        "[TELEGRAM] event=DELIVERED notification_id=%s correlation_id=%s "
                        "attempt=%d http_status=%s telegram_ok=true message_id=%s",
                        record.notification_id,
                        record.correlation_id,
                        record.attempts,
                        outcome["http_status"],
                        record.message_id,
                    )
                    self._invoke_callback(record)
                    return
                # Failure -> classify + maybe retry
                record.http_status = outcome["http_status"]
                record.telegram_ok = False
                record.telegram_error_code = outcome["telegram_error_code"]
                record.description = outcome["safe_message"]
                record.category = outcome["category"]
                record.retryable = outcome["retryable"]
                # DEDUPLICATED is an expected filter (GENERIC coalescing), not an error.
                # Downgrade from WARNING/ERROR to DEBUG so the console is not spammed
                # (the user's 11:32 log had ~10 FAILED_FINAL in 1s for duplicates).
                if outcome["category"] == "DEDUPLICATED":
                    logger.debug(
                        "[TELEGRAM] event=DEDUP_SUPPRESSED notification_id=%s correlation_id=%s "
                        "http_status=%s safe_reason=%s",
                        record.notification_id,
                        record.correlation_id,
                        outcome["http_status"],
                        outcome["safe_message"][:120],
                    )
                else:
                    logger.warning(
                        "[TELEGRAM] event=SEND_FAILED notification_id=%s correlation_id=%s "
                        "attempt=%d category=%s retryable=%s http_status=%s "
                        "telegram_error_code=%s safe_reason=%s",
                        record.notification_id,
                        record.correlation_id,
                        record.attempts,
                        outcome["category"],
                        outcome["retryable"],
                        outcome["http_status"],
                        outcome["telegram_error_code"],
                        outcome["safe_message"][:120],
                    )
                if not outcome["retryable"] or retries >= self.maximum_retries:
                    break
                retries += 1
                self._retry_count += 1
                record.attempts += 1
                logger.info(
                    "[TELEGRAM] event=RETRYING notification_id=%s attempt=%d backoff=%.1f",
                    record.notification_id,
                    record.attempts,
                    backoff,
                )
                time.sleep(backoff)
                backoff *= 2
            except Exception as exc:
                category, retryable = self._classify_exception(exc)
                record.category = category
                record.retryable = retryable
                record.description = str(exc)[:160]
                logger.error(
                    "[TELEGRAM] event=SEND_FAILED notification_id=%s correlation_id=%s "
                    "attempt=%d category=%s exception_type=%s retryable=%s",
                    record.notification_id,
                    record.correlation_id,
                    record.attempts,
                    category,
                    type(exc).__name__,
                    retryable,
                )
                if not retryable or retries >= self.maximum_retries:
                    break
                retries += 1
                self._retry_count += 1
                record.attempts += 1
                time.sleep(backoff)
                backoff *= 2

        record.status = "FAILED_FINAL"
        self._failed_count += 1
        self._last_failure = time.time()
        self._last_failure_category = record.category
        # DEDUPLICATED is expected coalescing, not a failure — debug only.
        # The previous spam was ~10 FAILED_FINAL/1s at the same timestamp.
        if record.category == "DEDUPLICATED":
            logger.debug(
                "[TELEGRAM] event=DEDUP_FINAL notification_id=%s correlation_id=%s safe_reason=%s",
                record.notification_id,
                record.correlation_id,
                record.description[:160],
            )
        else:
            logger.error(
                "[TELEGRAM] event=FAILED_FINAL notification_id=%s correlation_id=%s "
                "category=%s http_status=%s telegram_error_code=%s retryable=%s "
                "safe_reason=%s",
                record.notification_id,
                record.correlation_id,
                record.category,
                record.http_status,
                record.telegram_error_code,
                record.retryable,
                record.description[:160],
            )
        self._invoke_callback(record)

    def _escape(self, text: Any) -> str:
        return html.escape(str(text))

    def _truncate_message(self, text: str) -> str:
        if len(text) <= 4096:
            return text
        truncated = text[:4000] + "\n... [TRUNCATED] ..."
        return truncated + "</i></b></code>"

    def _redact_secrets(self, text: str) -> str:
        text = re.sub(r"\d{8,10}:[A-Za-z0-9_-]{20,}", "[REDACTED_BOT_TOKEN]", text)
        text = re.sub(
            r"(?i)(password|secret|key|token|auth)\s*[:=]\s*[^\s]+", r"\1=[REDACTED]", text
        )
        return text

    def _is_duplicate_or_cooling_down(self, html_text: str) -> bool:
        now = time.time()
        self._recent_messages = {
            k: t for k, t in self._recent_messages.items() if now - t < self.deduplication_window
        }
        # Signature keeps digits: stripping them made every message with
        # a number share the first-150-chars prefix (report ids, pnl,
        # trade counts), so the 2nd/3rd distinct notification was wrongly
        # suppressed as a duplicate.
        # Dedup compares ONLY against successfully delivered messages:
        # a failed attempt never registers, so retries of a timed-out
        # send are NOT suppressed (the pre-fix poisoning bug).
        sig = re.sub(r"\s+", "", html_text)[:150]
        return sig in self._recent_messages

    def shutdown(self, timeout: float | None = None) -> None:
        self.stop_worker(timeout=timeout)

    # =====================================================================
    # Template notifications (unchanged signatures; now fully observable)
    # =====================================================================


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


__all__ = ["NotificationRecord", "TelegramNotifier", "classify_http_response"]
