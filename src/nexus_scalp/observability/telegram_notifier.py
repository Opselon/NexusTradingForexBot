"""Telegram production notification subsystem — forensic lifecycle (BUG-072).

Complete observable lifecycle for every notification:

    ENQUEUED -> SEND_START(attempt) -> SEND_RESULT / SEND_FAILED
        -> DELIVERED | FAILED_FINAL (never silent)

Design:
- Queue + dedicated worker thread; network I/O NEVER on the caller's path
  (trading hot path stays independent of Telegram).
- HTTP response VERIFIED: HTTP 200 + ok=true  -> DELIVERED;
  HTTP 200 + ok=false -> FAILURE; 429 honors Retry-After; 5xx bounded retry.
- Explicit error taxonomy (TELEGRAM_AUTH_ERROR, TARGET, RATE_LIMIT, ...).
- Worker heartbeat + health_state() (READY / DEGRADED / STOPPED).
- Correlation IDs threaded ENQUEUE -> SEND -> RESULT.
- NEVER logs token / Authorization / secret-bearing structures.
"""

from __future__ import annotations

import html
import http.client
import json
import logging
import queue
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, ClassVar

from nexus_scalp.domain.models import AccountInfo, TradeOrder
from nexus_scalp.features.scalp_features import FeatureVector
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


class TelegramNotifier:
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
        self._sent_timestamps: list[float] = []
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
                "[TELEGRAM] event=DEDUP_FINAL notification_id=%s correlation_id=%s "
                "safe_reason=%s",
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

    def _invoke_callback(self, record: NotificationRecord) -> None:
        cb = record.callback
        if cb is None:
            return
        try:
            cb(record.message_id if record.status == "DELIVERED" else None)
        except Exception as cb_err:
            logger.error(
                "[TELEGRAM] callback error notification_id=%s error=%s",
                record.notification_id,
                cb_err,
            )

    @staticmethod
    def _is_blackhole_ip(ip: str) -> bool:
        """True when the IP falls in a reserved benchmarking/blackhole range.

        The classic DNS-poisoning answer for api.telegram.org is 198.18.x.x
        (RFC 2544). 192.0.0.x / 198.51.100.x / 203.0.113.x (RFC 6890) and
        127.0.0.x are equally suspicious for a public API host.
        """
        try:
            packed = socket.inet_aton(ip)
        except (OSError, ValueError):
            return False
        value = int.from_bytes(packed, "big")
        return any(lo <= value <= hi for lo, hi in _DNS_POISON_BLACKHOLE_RANGES)

    @staticmethod
    def _split_api_url(url: str) -> tuple[str, str]:
        """Split https://host/path -> (host, path)."""
        try:
            parsed = urllib.parse.urlsplit(url)
            return parsed.hostname or "", parsed.path or "/"
        except ValueError:
            return "", "/"

    def _should_bypass_dns(self, host: str) -> bool:
        """True when the resolver is poisoned/blackholing the host.

        Resolution failure also counts: connecting to a known-good
        fallback IP beats hanging on a dead resolver.
        """
        try:
            infos = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        except OSError as exc:
            logger.warning("[TELEGRAM_DNS] event=RESOLVE_FAILED host=%s error=%s", host, exc)
            return True
        if not infos:
            return True
        poisoned = any(TelegramNotifier._is_blackhole_ip(str(info[4][0])) for info in infos)
        self._last_dns_poisoned = poisoned
        return poisoned

    def _direct_https_open(
        self,
        ip: str,
        host: str,
        path: str,
        data: bytes | None,
        method: str,
        timeout: float,
    ) -> Any:
        """HTTPS request that connects to `ip` but keeps `host` for SNI +
        Host header (the --resolve equivalent urllib lacks)."""
        context = ssl.create_default_context()
        raw_sock = socket.create_connection((ip, 443), timeout=timeout)
        try:
            tls_sock = context.wrap_socket(raw_sock, server_hostname=host)
        except Exception:
            raw_sock.close()
            raise
        conn = http.client.HTTPSConnection(host, timeout=timeout)
        conn.sock = tls_sock
        headers = {"Content-Type": "application/json"}
        conn.request(method, path, body=data, headers=headers)
        return conn.getresponse()

    def _urlopen_with_dns_fallback(
        self, url: str, data: bytes | None, method: str, timeout: float
    ) -> Any:
        """urlopen that bypasses DNS poisoning for Telegram endpoints.

        When the system resolver returns a blackhole/rubbish answer for the
        API host (or DNS is down entirely), connect to a known-good Telegram
        IP while preserving SNI + Host for correct TLS. When DNS is healthy
        the call is byte-for-byte the previous urllib behavior.
        """
        host, path = self._split_api_url(url)
        if host and self._should_bypass_dns(host):
            last_error: Exception | None = None
            for ip in _TELEGRAM_FALLBACK_IPS:
                try:
                    logger.warning("[TELEGRAM_DNS] event=DIRECT_IP_ATTEMPT host=%s ip=%s", host, ip)
                    return self._direct_https_open(ip, host, path, data, method, timeout)
                except Exception as exc:  # try next fallback IP
                    last_error = exc
                    logger.warning("[TELEGRAM_DNS] event=DIRECT_IP_FAILED ip=%s error=%s", ip, exc)
            if last_error is not None:
                raise last_error
        return urllib.request.urlopen(
            urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method=method,
            ),
            timeout=timeout,
        )

    def _send_msg_sync(self, record: NotificationRecord) -> dict[str, Any]:
        """One HTTP POST. Returns an outcome dict (never raises except network)."""
        header = f"<b>[{record.priority}]</b>"
        if self.environment:
            header += f" <b>({self.environment.upper()})</b>"
        header += "\n"
        full_text = self._truncate_message(header + self._redact_secrets(record.text))

        with self._lock:
            if self._is_duplicate_or_cooling_down(full_text):
                logger.debug("Deduplicated repeated Telegram alert.")
                return {
                    "ok": False,
                    "retryable": False,
                    "category": "DEDUPLICATED",
                    "safe_message": "duplicate suppressed",
                    "http_status": 200,
                    "telegram_error_code": None,
                }
            now = time.time()
            self._sent_timestamps = [t for t in self._sent_timestamps if now - t < 60.0]
            if len(self._sent_timestamps) >= self.rate_limit:
                logger.warning("Telegram rate-limit exceeded (%d msgs/min).", self.rate_limit)
                return {
                    "ok": False,
                    "retryable": True,
                    "category": TELEGRAM_RATE_LIMIT,
                    "safe_message": "local rate limit exceeded",
                    "http_status": 429,
                    "telegram_error_code": None,
                }
            self._sent_timestamps.append(now)

        payload: dict[str, Any] = {
            "chat_id": self.admin_id,
            "text": full_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if record.reply_to_message_id:
            payload["reply_to_message_id"] = record.reply_to_message_id

        data = json.dumps(payload).encode("utf-8")
        with self._urlopen_with_dns_fallback(
            self._send_url, data=data, method="POST", timeout=self.timeout_seconds
        ) as resp:
            http_status = resp.status
            body = resp.read()
        outcome = self._parse_response(http_status, body)
        if outcome.get("ok") is True:
            # Register the dedup signature ONLY on a confirmed delivery:
            # a failed send (timeout/5xx) never registers, so a retry is
            # NOT suppressed as a duplicate (the pre-fix poisoning bug).
            sig = re.sub(r"\s+", "", full_text)[:150]
            with self._lock:
                self._recent_messages[sig] = time.time()
        return outcome

    def _parse_response(self, http_status: int, body: bytes) -> dict[str, Any]:
        """HTTP 200 is NOT success — verify the JSON `ok` field (spec §6)."""
        if http_status == 200:
            try:
                parsed = json.loads(body.decode("utf-8", errors="replace"))
            except (ValueError, UnicodeDecodeError):
                return {
                    "ok": False,
                    "retryable": False,
                    "category": TELEGRAM_SERIALIZATION_ERROR,
                    "safe_message": "invalid JSON in Telegram response",
                    "http_status": 200,
                    "telegram_error_code": None,
                }
            if isinstance(parsed, dict) and parsed.get("ok") is True:
                result = parsed.get("result") or {}
                return {
                    "ok": True,
                    "message_id": result.get("message_id"),
                    "http_status": 200,
                    "telegram_error_code": None,
                    "category": "",
                    "retryable": False,
                    "safe_message": "delivered",
                }
            # HTTP 200 + ok=false -> FAILURE
            classified = classify_http_response(200, body)
            return {
                "ok": False,
                "http_status": 200,
                "telegram_error_code": classified["telegram_error_code"],
                "category": classified["category"],
                "retryable": classified["retryable"],
                "safe_message": classified["safe_message"],
            }
        classified = classify_http_response(http_status, body)
        return {
            "ok": False,
            "http_status": http_status,
            "telegram_error_code": classified["telegram_error_code"],
            "category": classified["category"],
            "retryable": classified["retryable"],
            "safe_message": classified["safe_message"],
        }

    @staticmethod
    def classify_http_error(http_status: int, body: bytes) -> dict[str, Any]:
        """Public classification entry (used by tests + diagnostics)."""
        return classify_http_response(http_status, body)

    def _classify_exception(self, exc: Exception) -> tuple[str, bool]:
        if isinstance(exc, _TIMEOUT_ERRORS):
            if self._last_dns_poisoned:
                self._last_dns_poisoned = False
                return TELEGRAM_DNS_BLOCKED, False
            return TELEGRAM_TIMEOUT, True
        if isinstance(exc, ConnectionError):
            return TELEGRAM_NETWORK_ERROR, True
        if isinstance(exc, urllib.error.HTTPError):
            return classify_http_response(exc.code, exc.read() if hasattr(exc, "read") else None)[
                "category"
            ], classify_http_response(exc.code, None)["retryable"]
        return TELEGRAM_UNKNOWN_ERROR, False

    # =====================================================================
    # Connectivity diagnostics (spec §7)
    # =====================================================================

    def get_me(self) -> dict[str, Any]:
        """Safe live connectivity probe (getMe): DNS/TLS/auth verification."""
        if not self._me_url:
            return {
                "ok": False,
                "category": TELEGRAM_CONFIG_ERROR,
                "safe_message": "bot token missing",
            }
        try:
            with self._urlopen_with_dns_fallback(
                self._me_url, data=None, method="GET", timeout=self.timeout_seconds
            ) as resp:
                http_status = resp.status
                body = resp.read()
            parsed = json.loads(body.decode("utf-8", errors="replace"))
            if http_status == 200 and parsed.get("ok") is True:
                bot = parsed.get("result") or {}
                return {
                    "ok": True,
                    "http_status": 200,
                    "username": bot.get("username"),
                    "bot_name": bot.get("first_name"),
                    "bot_id": bot.get("id"),
                }
            return {
                "ok": False,
                "http_status": http_status,
                "category": classify_http_response(http_status, body)["category"],
                "safe_message": str(parsed.get("description") or "getMe failed")[:160],
            }
        except Exception as exc:
            category, _ = self._classify_exception(exc)
            return {
                "ok": False,
                "category": category,
                "safe_message": str(exc)[:160],
            }

    def send_diagnostic(self, label: str = "NEXUS TELEGRAM DIAGNOSTIC TEST") -> dict[str, Any]:
        """Send ONE clearly-labeled diagnostic message; return the real result."""
        record = NotificationRecord(
            notification_id=new_correlation_id("diag"),
            correlation_id=new_correlation_id("corr"),
            event_type="DIAGNOSTIC",
            priority="INFO",
            target_class="TELEGRAM",
            created_at=time.time(),
            text=(
                f"{label}\n"
                f"notification_id={record_placeholder()}  (filled by worker)\n"
                f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
            ),
        )
        try:
            self._queue.put(record, timeout=1.0)
        except queue.Full:
            return {"ok": False, "category": TELEGRAM_QUEUE_ERROR, "safe_message": "queue full"}
        # Wait for the real result (bounded).
        deadline = time.time() + self.timeout_seconds + 3.0
        while time.time() < deadline and record.status not in (
            "DELIVERED",
            "FAILED_FINAL",
            "SEND_FAILED",
        ):
            time.sleep(0.05)
        if record.status == "DELIVERED":
            return {
                "ok": True,
                "message_id": record.message_id,
                "correlation_id": record.correlation_id,
                "notification_id": record.notification_id,
            }
        return {
            "ok": False,
            "category": record.category or TELEGRAM_UNKNOWN_ERROR,
            "safe_message": record.description or "delivery not confirmed",
            "correlation_id": record.correlation_id,
            "notification_id": record.notification_id,
        }

    # =====================================================================
    # Helpers (unchanged contracts)
    # =====================================================================

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

    def notify_startup(
        self,
        symbol: str,
        mode: str,
        balance: float,
        equity: float,
        callback: Any | None = None,
    ) -> int | None:
        """1. System Launch Banner Alert"""
        msg = (
            f"🚀 <b>NEXUS SCALP ENGINE STARTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"⚙️ <b>Execution Mode:</b> <code>{self._escape(mode)}</code>\n"
            f"💰 <b>Balance:</b> <code>${balance:,.2f}</code>\n"
            f"📈 <b>Equity:</b> <code>${equity:,.2f}</code>\n"
            f"🕒 <b>Status:</b> <i>Active & Operational</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_info(self, title: str, message: str, callback: Any | None = None) -> int | None:
        msg = (
            f"ℹ️ <b>{self._escape(title)}</b>\n"  # noqa: RUF001
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{self._escape(message)}"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def send_message(
        self,
        text: str,
        callback: Any | None = None,
        severity: str = "INFO",
    ) -> int | None:
        return self.send(text, callback=callback, severity=severity)

    def notify_generic_message(
        self,
        title: str,
        message: str,
        severity: str = "INFO",
        callback: Any | None = None,
    ) -> int | None:
        msg = f"🔔 <b>{self._escape(title)}</b>\n━━━━━━━━━━━━━━━━━━━━━\n{self._escape(message)}"
        return self.send(msg, callback=callback, severity=severity)

    def notify_test_message(self, callback: Any | None = None) -> int | None:
        msg = (
            f"✅ <b>TELEGRAM CONNECTION OK</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧪 <b>Test message delivered.</b>\n"
            f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n"
            f"<i>Your bot token and admin chat ID are correctly configured.</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_engine_stopped(
        self, reason: str = "manual", callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🛑 <b>NEXUS SCALP ENGINE STOPPED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>Reason:</b> <code>{self._escape(reason)}</code>\n"
            f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_engine_error(
        self, error: str, context: str = "", callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🚨 <b>ENGINE ERROR</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <b>Context:</b> <code>{self._escape(context or 'engine')}</code>\n"
            f"❌ <b>Error:</b> <code>{self._escape(error)[:1500]}</code>\n"
            f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        )
        return self.send(msg, callback=callback, severity="CRITICAL")

    def notify_audit_purge(
        self, deleted: dict[str, Any], duration_ms: float, callback: Any | None = None
    ) -> int | None:
        parts = " | ".join(f"{k}: {v}" for k, v in (deleted or {}).items()) or "nothing deleted"
        msg = (
            f"🧹 <b>AUDIT RETENTION PURGE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🗑 <b>Deleted:</b> <code>{self._escape(parts)}</code>\n"
            f"⏱ <b>Duration:</b> <code>{duration_ms:.0f} ms</code>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_warmup(
        self, state: str, symbol: str, detail: str = "", callback: Any | None = None
    ) -> int | None:
        emoji = "✅" if state.upper() == "READY" else "⏳"
        msg = (
            f"{emoji} <b>WARMUP STATE: {self._escape(state.upper())}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📋 <b>Detail:</b> {self._escape(detail or '—')}"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_daily_summary(
        self, stats: dict[str, Any], callback: Any | None = None
    ) -> int | None:
        def _fmt(k: str, dflt: Any = "—") -> str:
            v = stats.get(k, dflt)
            return "—" if v is None or v == "" else str(v)

        msg = (
            f"📊 <b>DAILY PERFORMANCE SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 <b>Date:</b> <code>{_fmt('date')}</code>\n"
            f"💼 <b>Trades:</b> <code>{_fmt('trades')}</code>\n"
            f"✅ <b>Wins:</b> <code>{_fmt('wins')}</code> | ❌ <b>Losses:</b> <code>{_fmt('losses')}</code>\n"
            f"🎯 <b>Win Rate:</b> <code>{_fmt('win_rate')}</code>\n"
            f"💰 <b>Net PnL:</b> <code>{_fmt('net_pnl')}</code>\n"
            f"📉 <b>Max Drawdown:</b> <code>{_fmt('max_drawdown')}</code>\n"
            f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_order_opened(
        self, order: TradeOrder, risk_usd: float, callback: Any | None = None
    ) -> int | None:
        emoji = "🟢" if "BUY" in order.order_type.value else "🔴"
        msg = (
            f"{emoji} <b>ORDER DISPATCHED TO BROKER</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(order.symbol)}</code>\n"
            f"📌 <b>Type:</b> <code>{self._escape(order.order_type.value)}</code>\n"
            f"📦 <b>Lots:</b> <code>{order.volume}</code>\n"
            f"💵 <b>Entry Price:</b> <code>{order.price:.2f}</code>\n"
            f"🛑 <b>Stop Loss:</b> <code>{order.stop_loss:.2f}</code>\n"
            f"🎯 <b>Take Profit:</b> <code>{order.take_profit:.2f}</code>\n"
            f"⚠️ <b>Risk Allocated:</b> <code>${risk_usd:.2f}</code>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_order_closed_profit(
        self,
        ticket: int,
        symbol: str,
        lots: float,
        entry: float,
        exit_price: float,
        profit_usd: float,
        profit_pct: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"🎉 <b>PROFITABLE TRADE CLOSED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code> ({lots} lots)\n"
            f"💵 <b>Entry:</b> <code>{entry:.2f}</code> ➔ "
            f"<b>Exit:</b> <code>{exit_price:.2f}</code>\n"
            f"💵 <b>Net Profit:</b> <code>+${profit_usd:,.2f}</code> (+{profit_pct:.2f}%)\n"
            f"✅ <b>Status:</b> <i>Target Achieved / Trailing Closed</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_order_closed_loss(
        self,
        ticket: int,
        symbol: str,
        lots: float,
        entry: float,
        exit_price: float,
        loss_usd: float,
        loss_pct: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"🔻 <b>TRADE CLOSED IN LOSS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code> ({lots} lots)\n"
            f"💵 <b>Entry:</b> <code>{entry:.2f}</code> ➔ "
            f"<b>Exit:</b> <code>{exit_price:.2f}</code>\n"
            f"💸 <b>Loss Amount:</b> <code>-${abs(loss_usd):,.2f}</code> (-{abs(loss_pct):.2f}%)\n"
            f"🛡️ <b>Capital Safeguard:</b> <i>Risk Limited by Stop Loss</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="WARNING"
        )

    def notify_manual_close(
        self,
        ticket: int,
        symbol: str,
        entry: float,
        exit_price: float,
        profit_usd: float,
        duration_sec: float,
        reason: str,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        emoji = "🎉" if profit_usd >= 0 else "🔻"
        msg = (
            f"{emoji} <b>MANUAL POSITION CLOSE DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"💵 <b>Entry Price:</b> <code>{entry:.2f}</code>\n"
            f"💵 <b>Exit Price:</b> <code>{exit_price:.2f}</code>\n"
            f"💰 <b>Net PnL:</b> <code>{'+$' if profit_usd >= 0 else '-$'}{abs(profit_usd):,.2f}</code>\n"
            f"⏱️ <b>Duration:</b> <code>{int(duration_sec)}s</code>\n"
            f"📝 <b>MT5 Closing Reason:</b> <code>{self._escape(reason)}</code>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_canonical_close(
        self,
        ticket: int,
        symbol: str,
        entry: float,
        exit_price: float,
        profit_usd: float,
        duration_sec: float,
        exit_reason: str,
        evidence: str = "",
        initial_sl: float = 0.0,
        final_sl: float = 0.0,
        strategy: str = "",
        regime: str = "",
        confidence: float = 0.0,
        realized_r: float = 0.0,
        mfe_usd: float = 0.0,
        mae_usd: float = 0.0,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        """POSITION CLOSED — consumes the CANONICAL outcome (BUG-081).

        The exit reason and evidence come from the same classifier result that
        feeds AccountingCore / ExperienceLedger. This method NEVER re-infers
        manual / SL / BE / trailing from the broker reason code. The label is
        derived from the canonical taxonomy (ExitReason) and shown with its
        evidence so the message is auditable.
        """
        emoji = "🎉" if profit_usd >= 0 else "🔻"
        label = self._exit_label(exit_reason)
        lines = [
            f"{emoji} <b>POSITION CLOSED</b>\n",
            "━━━━━━━━━━━━━━━━━━━━━\n",
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n",
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n",
            f"💵 <b>Entry:</b> <code>{entry:.2f}</code>  →  <b>Exit:</b> <code>{exit_price:.2f}</code>\n",
            f"💰 <b>Net PnL:</b> <code>{'+$' if profit_usd >= 0 else '-$'}{abs(profit_usd):,.2f}</code>\n",
        ]
        if realized_r:
            lines.append(f"📐 <b>R:</b> <code>{realized_r:+.2f}R</code>\n")
        lines.append(f"🚪 <b>Exit:</b> <code>{label}</code>\n")
        if evidence:
            lines.append(f"🧾 <b>Evidence:</b> <code>{self._escape(evidence)}</code>\n")
        if initial_sl > 0.0 or final_sl > 0.0:
            lines.append(f"🛡️ <b>SL:</b> <code>{initial_sl:.2f} → {final_sl:.2f}</code>\n")
        if duration_sec > 0:
            lines.append(f"⏱️ <b>Duration:</b> <code>{int(duration_sec)}s</code>\n")
        meta = []
        if strategy:
            meta.append(f"Strategy: {self._escape(strategy)}")
        if regime:
            meta.append(f"Regime: {self._escape(regime)}")
        if confidence:
            meta.append(f"Confidence: {confidence:.2f}")
        if mfe_usd or mae_usd:
            meta.append(f"MFE {mfe_usd:+.2f} / MAE {mae_usd:+.2f}")
        if meta:
            lines.append("🧠 " + " | ".join(meta) + "\n")
        return self.send(
            "".join(lines),
            reply_to_message_id=reply_to_message_id,
            callback=callback,
            severity="INFO",
        )

    def _exit_label(self, exit_reason: str) -> str:
        """Human label for the canonical ExitReason taxonomy (never re-classifies)."""
        r = (exit_reason or "").upper()
        mapping = {
            "TAKE_PROFIT_HIT": "TAKE PROFIT",
            "HARD_SL_HIT": "ORIGINAL STOP LOSS",
            "RISK_FREE_SL_HIT": "RISK-FREE STOP",
            "BREAK_EVEN_SL_HIT": "BREAK-EVEN STOP",
            "TRAILING_STOP_HIT": "TRAILING STOP",
            "MANUAL_CLOSE": "MANUAL CLOSE",
            "SYSTEM_CLOSE": "SYSTEM CLOSE",
            "RECONCILIATION_CLOSE": "RECONCILIATION CLOSE",
            "BROKER_CLOSE": "BROKER CLOSE",
            "AI_REVERSAL_EXIT": "STRATEGY EXIT (AI REVERSAL)",
            "HOLD_SCORE_DECAY": "STRATEGY EXIT (HOLD SCORE)",
            "PROFIT_GIVEBACK_PROTECTION": "EMERGENCY EXIT (GIVEBACK)",
            "UNKNOWN": "UNKNOWN",
        }
        return mapping.get(r, self._escape(exit_reason or "UNKNOWN"))

    def notify_early_emergency_cut(
        self,
        ticket: int,
        score: int,
        reasons: str,
        saved_usd: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"⚡ <b>EARLY EMERGENCY CUT (CAPITAL SAVED)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"📉 <b>Hold Score:</b> <code>{score}/100</code>\n"
            f"⚠️ <b>Invalidation Reason:</b> <code>{self._escape(reasons)}</code>\n"
            f"🛡️ <b>Action:</b> <i>Closed Early to Avoid Full SL (Saved ~${saved_usd:.2f})</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="WARNING"
        )

    def notify_break_even_applied(
        self,
        ticket: int,
        new_sl: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"🛡️ <b>BREAK-EVEN APPLIED (RISK-FREE)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"🔒 <b>New Stop Loss:</b> <code>{new_sl:.2f}</code>\n"
            f"✨ <b>Status:</b> <i>Trade is now 100% Risk-Free!</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_trailing_stop_advanced(
        self,
        ticket: int,
        new_sl: float,
        current_price: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"📈 <b>TRAILING STOP ADVANCED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket:</b> <code>#{ticket}</code>\n"
            f"🎯 <b>Current Price:</b> <code>{current_price:.2f}</code>\n"
            f"🔒 <b>Locked Stop Loss:</b> <code>{new_sl:.2f}</code>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_break_even_applied_extended(
        self,
        ticket: int,
        new_sl: float,
        original_risk_usd: float,
        protected_amount_usd: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"🛡️ <b>BREAK-EVEN LOCK ACTIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"🔒 <b>Protected Stop Loss:</b> <code>{new_sl:.2f}</code>\n"
            f"⚠️ <b>Original Risk:</b> <code>${original_risk_usd:.2f}</code>\n"
            f"🔒 <b>Protected PnL:</b> <code>${protected_amount_usd:.2f}</code>\n"
            f"✨ <b>Status:</b> <i>Trade is now 100% Risk-Free!</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_trailing_stop_advanced_extended(
        self,
        ticket: int,
        old_sl: float,
        new_sl: float,
        current_price: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"📈 <b>TRAILING STOP ADVANCED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"🎯 <b>Current Price:</b> <code>{current_price:.2f}</code>\n"
            f"🔒 <b>Stop Loss Step:</b> <code>{old_sl:.2f}</code> ➔ <b><code>{new_sl:.2f}</code></b>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_partial_close(
        self,
        ticket: int,
        symbol: str,
        closed_lots: float,
        remaining_lots: float,
        realized_profit_usd: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        emoji = "🎉" if realized_profit_usd >= 0 else "💸"
        msg = (
            f"🥞 <b>PARTIAL POSITION CLOSE (SCALE-OUT)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📦 <b>Scaled Volume:</b> <code>{closed_lots} lots</code>\n"
            f"Remaining Volume:</b> <code>{remaining_lots} lots</code>\n"
            f"{emoji} <b>Realized PnL:</b> <code>{'+$' if realized_profit_usd >= 0 else '-$'}{abs(realized_profit_usd):,.2f}</code>\n"
            f"🛡️ <b>Action:</b> <i>Scaled out part of the position to secure profits</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_emergency_cut(
        self,
        ticket: int,
        score: int,
        reasons: str,
        saved_usd: float,
        trigger_source: str,
        drawdown_pct: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"🚨 <b>EMERGENCY BAILOUT INITIATED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📉 <b>Hold Score:</b> <code>{score}/100</code>\n"
            f"⚠️ <b>Detailed Reason:</b> <code>{self._escape(reasons)}</code>\n"
            f"🛠️ <b>Trigger Source:</b> <code>{self._escape(trigger_source)}</code>\n"
            f"📉 <b>Pre-close Drawdown:</b> <code>{drawdown_pct:.2f}%</code>\n"
            f"🛡️ <b>Action:</b> <i>Emergency closed! (Saved ~${saved_usd:.2f})</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="WARNING"
        )

    def notify_tp_touched(
        self,
        ticket: int,
        symbol: str,
        entry: float,
        tp_price: float,
        exit_price: float,
        profit_usd: float,
        profit_pct: float,
        duration_sec: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"🎯 <b>TAKE PROFIT TOUCHED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"💵 <b>Entry Price:</b> <code>{entry:.2f}</code>\n"
            f"🎯 <b>TP Target:</b> <code>{tp_price:.2f}</code>\n"
            f"💵 <b>Exit Price:</b> <code>{exit_price:.2f}</code>\n"
            f"💰 <b>Profit/Loss:</b> <code>+${profit_usd:,.2f}</code>\n"
            f"📈 <b>Percentage Result:</b> <code>+{profit_pct:.2f}%</code>\n"
            f"⏱️ <b>Duration:</b> <code>{int(duration_sec)}s</code>\n"
            f"✨ <b>Status:</b> <i>Successfully hit TP target!</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_sl_touched(
        self,
        ticket: int,
        symbol: str,
        entry: float,
        sl_price: float,
        exit_price: float,
        loss_usd: float,
        loss_pct: float,
        duration_sec: float,
        risk_usd: float,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"🛑 <b>STOP LOSS TOUCHED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"💵 <b>Entry Price:</b> <code>{entry:.2f}</code>\n"
            f"🛑 <b>SL Level:</b> <code>{sl_price:.2f}</code>\n"
            f"💵 <b>Exit Price:</b> <code>{exit_price:.2f}</code>\n"
            f"💸 <b>Loss Amount:</b> <code>-${abs(loss_usd):,.2f}</code>\n"
            f"📉 <b>Percentage Result:</b> <code>-{abs(loss_pct):.2f}%</code>\n"
            f"⏱️ <b>Duration:</b> <code>{int(duration_sec)}s</code>\n"
            f"⚠️ <b>Allocated Risk:</b> <code>${risk_usd:.2f}</code>\n"
            f"🛡️ <b>Capital Safeguard:</b> <i>Position closed to protect equity</i>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="WARNING"
        )

    def notify_order_modification(
        self,
        ticket: int,
        symbol: str,
        field_modified: str,
        old_value: Any,
        new_value: Any,
        reply_to_message_id: int | None = None,
        callback: Any | None = None,
    ) -> int | None:
        msg = (
            f"⚙️ <b>POSITION CONTRACT MODIFIED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎫 <b>Ticket ID:</b> <code>#{ticket}</code>\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"⚙️ <b>Field Modified:</b> <code>{self._escape(field_modified)}</code>\n"
            f"📝 <b>Change:</b> <code>{self._escape(old_value)}</code> ➔ <b><code>{self._escape(new_value)}</code></b>"
        )
        return self.send(
            msg, reply_to_message_id=reply_to_message_id, callback=callback, severity="INFO"
        )

    def notify_survival_mode_changed(
        self, active: bool, drawdown_pct: float, callback: Any | None = None
    ) -> int | None:
        status = (
            "🔴 ACTIVATED (HIGH CONVICTION ONLY)" if active else "🟢 DEACTIVATED (NORMAL TRADING)"
        )
        msg = (
            f"🛡️ <b>ACCOUNT SURVIVAL MODE: {status}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📉 <b>Current Peak Drawdown:</b> <code>{drawdown_pct:.2f}%</code>"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_account_health(
        self, account: AccountInfo, drawdown_pct: float, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"📊 <b>ACCOUNT FINANCIAL HEALTH REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>Login:</b> <code>#{account.login}</code>\n"
            f"💰 <b>Balance:</b> <code>${account.balance:,.2f}</code>\n"
            f"📈 <b>Equity:</b> <code>${account.equity:,.2f}</code>\n"
            f"💵 <b>Free Margin:</b> <code>${account.margin_free:,.2f}</code>\n"
            f"📉 <b>Peak Drawdown:</b> <code>{drawdown_pct:.2f}%</code>\n"
            f"⚡ <b>Leverage:</b> <code>1:{account.leverage}</code>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_spread_spike(
        self, symbol: str, current_spread: float, max_allowed: float, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"⚠️ <b>SPREAD SPIKE DETECTED (TRADE BLOCKED)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📊 <b>Current Spread:</b> <code>{current_spread:.1f} pts</code>\n"
            f"🛑 <b>Max Permissible:</b> <code>{max_allowed:.1f} pts</code>"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_volume_anomaly(
        self, symbol: str, volume: float, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🌊 <b>SMART MONEY VOLUME ANOMALY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📊 <b>Tick Volume Spike:</b> <code>{volume} ticks/sec</code>"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_kill_switch_activated(self, reason: str, callback: Any | None = None) -> int | None:
        msg = (
            f"🚨 <b>EMERGENCY KILL SWITCH ACTIVATED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 <b>Action:</b> <i>All new execution halted immediately!</i>\n"
            f"📝 <b>Reason:</b> <code>{self._escape(reason)}</code>"
        )
        return self.send(msg, callback=callback, severity="CRITICAL")

    def notify_error(self, context: str, error_msg: str, callback: Any | None = None) -> int | None:
        msg = (
            f"⚠️ <b>SYSTEM OPERATIONAL ERROR</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 <b>Context:</b> <code>{self._escape(context)}</code>\n"
            f"❌ <b>Error:</b> <code>{self._escape(error_msg)}</code>"
        )
        return self.send(msg, callback=callback, severity="ERROR")

    def notify_market_summary(
        self, symbol: str, features: FeatureVector, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🌐 <b>MARKET TELEMETRY RADAR SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"💵 <b>Displacement:</b> <code>${features.live_tick_displacement:+.2f}</code>\n"
            f"📊 <b>ATR (M1):</b> <code>${features.atr_m1:.2f}</code>\n"
            f"☁️ <b>Ichimoku:</b> <code>TK_Cross:{features.tk_cross_signal}</code>\n"
            f"🧱 <b>ICT State:</b> <code>"
            f"FVG:{features.fvg_bullish_active}|OB:{features.order_block_type}</code>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_shutdown(
        self, reason: str = "User Initiated", callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🛑 <b>NEXUS SCALP ENGINE SHUTTING DOWN</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 <b>Reason:</b> <code>{self._escape(reason)}</code>\n"
            f"🕒 <b>Status:</b> <i>Engine Disconnected Cleanly</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_market_extremes(
        self,
        symbol: str,
        high_50: float,
        low_50: float,
        range_pos_pct: float,
        callback: Any | None = None,
    ) -> int | None:
        pos_type = "🔥 EXTREME HIGH (PEAK)" if range_pos_pct >= 0.90 else "❄️ EXTREME LOW (FLOOR)"
        msg = (
            f"⛰️ <b>MARKET STRUCTURE EXTREME DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"🔺 <b>50-Bar High:</b> <code>{high_50:.2f}</code>\n"
            f"🔻 <b>50-Bar Low:</b> <code>{low_50:.2f}</code>\n"
            f"📍 <b>Range Position:</b> <code>{range_pos_pct * 100:.1f}% ({pos_type})</code>"
        )
        return self.send(msg, callback=callback, severity="WARNING")

    def notify_choch_detected(
        self, symbol: str, direction: str, callback: Any | None = None
    ) -> int | None:
        emoji = "🟢" if direction == "BULLISH" else "🔴"
        msg = (
            f"{emoji} <b>ICT CHANGE OF CHARACTER (ChoCh)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"🔀 <b>Direction Shift:</b> <code>{self._escape(direction)}</code>\n"
            f"💡 <b>Market Structure:</b> <i>Potential Trend Reversal Initiated</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_liquidity_sweep(
        self, symbol: str, sweep_type: str, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🧹 <b>LIQUIDITY SWEEP / STOP HUNT DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"🎯 <b>Type:</b> <code>{self._escape(sweep_type)}</code>\n"
            f"⚡ <b>Action:</b> <i>Smart Money Swept Liquidity Pools</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_fvg_detected(
        self, symbol: str, fvg_type: str, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"📐 <b>ICT FAIR VALUE GAP (FVG) ACTIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"📌 <b>Imbalance Type:</b> <code>{self._escape(fvg_type)}</code>\n"
            f"⌛ <b>Strategy:</b> <i>Waiting for Limit Retest Entry</i>"
        )
        return self.send(msg, callback=callback, severity="INFO")

    def notify_order_block(
        self, symbol: str, ob_type: str, callback: Any | None = None
    ) -> int | None:
        msg = (
            f"🧱 <b>INSTITUTIONAL ORDER BLOCK DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Symbol:</b> <code>{self._escape(symbol)}</code>\n"
            f"🔹 <b>Block Type:</b> <code>{self._escape(ob_type)}</code>"
        )
        return self.send(msg, callback=callback, severity="INFO")


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def record_placeholder() -> str:
    return new_correlation_id("nid")
