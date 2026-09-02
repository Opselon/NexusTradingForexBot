"""Telegram transport plumbing: DNS-fallback HTTPS, sync send, response parsing.

Extracted VERBATIM from telegram_notifier.py (Agent-5 modularization,
CHG-0032-A1 program). ``TransportMixin`` is a stateless method carrier over
``TelegramNotifier``'s state; preserves BUG-131/TELEGRAM_DNS_BLOCKED
direct-IP fallback semantics exactly (SNI preserved, blackhole ranges,
fallback IP order).

Single-source ownership of transport constants lives HERE:
    _DNS_POISON_BLACKHOLE_RANGES, _TELEGRAM_FALLBACK_IPS, _TIMEOUT_ERRORS
(the facade re-exports them for backward-compatible imports — the DNS
regression tests read ``telegram_notifier._TELEGRAM_FALLBACK_IPS``).

USED BY: observability/telegram_notifier.py (facade).
DO-NOT-PUT-HERE: message formatting (tg_notifications), worker/queue (core).
"""

from __future__ import annotations

import http.client
import json
import logging
import queue
import re
import socket
import ssl
import sys as _sys

# record_placeholder is defined at the facade's TAIL (after the mixin wiring),
# so it cannot be imported at module level; resolve at call time instead.
import sys as _sys_mod
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, cast

from nexus_scalp.observability._tg_core_protocol import _TelegramCoreProto
from nexus_scalp.observability.telegram_notifier import (
    TELEGRAM_CONFIG_ERROR,
    TELEGRAM_DNS_BLOCKED,
    TELEGRAM_NETWORK_ERROR,
    TELEGRAM_QUEUE_ERROR,
    TELEGRAM_RATE_LIMIT,
    TELEGRAM_SERIALIZATION_ERROR,
    TELEGRAM_TIMEOUT,
    TELEGRAM_UNKNOWN_ERROR,
    NotificationRecord,
    classify_http_response,
    new_correlation_id,
)


def _record_placeholder_late() -> str:
    _facade = _sys_mod.modules.get("nexus_scalp.observability.telegram_notifier")
    fn = getattr(_facade, "record_placeholder", None)
    return fn() if fn else "(queued)"


# NOTE on imports: tg_transport is imported by the facade AFTER NotificationRecord/
# classify_http_response/_category_for_code are defined there (bottom wiring), so
# this import never sees a partially-initialized module. The category constants
# and logger are imported below from the facade to stay single-source.

logger = logging.getLogger(__name__)

_TIMEOUT_ERRORS = (TimeoutError, urllib.error.URLError)

_DNS_POISON_BLACKHOLE_RANGES: tuple[tuple[int, int], ...] = (
    (0xC0000000, 0xC000FFFF),  # 192.0.0.0/24  (RFC 6890 TEST-NET-1)
    (0xC6120000, 0xC613FFFF),  # 198.18.0.0/15 (RFC 2544 benchmarking)
    (0xC6336400, 0xC63364FF),  # 198.51.100.0/24 (TEST-NET-2)
    (0xCB007100, 0xCB0071FF),  # 203.0.113.0/24 (TEST-NET-3)
    (0x7F000000, 0x7F0000FF),  # 127.0.0.0/8 localhost block
)

_TELEGRAM_FALLBACK_IPS: tuple[str, ...] = (
    "149.154.167.220",
    "149.154.167.222",
    "149.154.175.50",
    "91.108.56.130",
)


class TransportMixin(_TelegramCoreProto):
    """Stateless carrier for transport-level methods (verbatim)."""

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
        # Late binding: the facade class is not yet bound at mixin-import
        # time; resolve at call time (this path only runs on DNS lookups).
        _facade = _sys.modules.get("nexus_scalp.observability.telegram_notifier")
        _notifier_cls = getattr(_facade, "TelegramNotifier", None)
        if _notifier_cls is None:
            return True  # fail safe: treat unresolved as poisoned -> bypass
        poisoned = any(_notifier_cls._is_blackhole_ip(str(info[4][0])) for info in infos)
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
        # Explicitly construct a client context so legacy TLS versions are
        # never implicitly enabled by a platform/OpenSSL default.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.MAXIMUM_SUPPORTED
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
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
            # Typed local via cast: the facade's __init__ owns this attribute
            # (circular import prevents mypy cross-module resolution here).
            old_ts = cast("list[float]", getattr(self, "_sent_timestamps"))  # noqa: B009
            recent: list[float] = [t for t in old_ts if now - t < 60.0]
            setattr(self, "_sent_timestamps", recent)  # noqa: B010
            if len(recent) >= self.rate_limit:
                logger.warning("Telegram rate-limit exceeded (%d msgs/min).", self.rate_limit)
                return {
                    "ok": False,
                    "retryable": True,
                    "category": TELEGRAM_RATE_LIMIT,
                    "safe_message": "local rate limit exceeded",
                    "http_status": 429,
                    "telegram_error_code": None,
                }
            getattr(self, "_sent_timestamps").append(now)  # noqa: B009

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
                f"notification_id={_record_placeholder_late()}  (filled by worker)\n"
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
