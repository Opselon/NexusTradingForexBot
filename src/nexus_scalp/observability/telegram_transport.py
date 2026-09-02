"""Telegram transport layer for the NSE CI/CD observability subsystem.

Extends the existing production TelegramNotifier with what CI/CD reporting
needs on top of enqueue/send:

* document upload (sendDocument) with HTML caption
* pre-send secret redaction of BOTH message text and file contents
* size limits (Telegram file cap ~50 MB; we enforce a safer 20 MB default)
* bounded, observable sendDocument with the same error taxonomy + retry
  semantics as the notifier (429 Retry-After, 5xx bounded retry, timeouts)

Design rule: notification failures are ISOLATED — a Telegram failure never
raises into the caller; every call returns a structured result dict. CI must
never fail because Telegram is degraded.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Telegram Bot API document size ceiling (50 MB). We stay well under it.
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

#: Secret-shaped patterns — masks applied BEFORE anything reaches Telegram.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\d{8,10}:[A-Za-z0-9_-]{25,}"), "[REDACTED_BOT_TOKEN]"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GH_PAT]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED_SK]"),
    (re.compile(r"xoxb-[A-Za-z0-9-]{10,}"), "[REDACTED_SLACK]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS]"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (
        re.compile(r"-----END (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        "[REDACTED_PRIVATE_KEY_END]",
    ),
]

#: key=value / key: value shapes for generic secrets.
_GENERIC_SECRET_RE = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|token|auth|credential)\s*[=:]\s*[^\s,;&'\"]+"
)


def redact_secrets(text: str) -> str:
    """Mask secret-shaped strings in arbitrary text (logs, file contents)."""
    if not text:
        return text
    out = text
    for pattern, mask in _SECRET_PATTERNS:
        out = pattern.sub(mask, out)
    out = _GENERIC_SECRET_RE.sub(r"\1=[REDACTED]", out)
    return out


def _classify_document_response(http_status: int | None, body: bytes | None) -> dict[str, Any]:
    """Reuse the notifier's taxonomy vocabulary (no secrets in the result)."""
    if http_status == 200:
        return {"ok": True, "category": "DELIVERED", "retryable": False}
    if http_status == 429:
        return {"ok": False, "category": "TELEGRAM_RATE_LIMIT", "retryable": True}
    if http_status is not None and 500 <= http_status < 600:
        return {"ok": False, "category": "TELEGRAM_SERVER_ERROR", "retryable": True}
    if http_status is None:
        return {"ok": False, "category": "TELEGRAM_NETWORK_ERROR", "retryable": True}
    if http_status in (401, 403):
        return {"ok": False, "category": "TELEGRAM_AUTH_ERROR", "retryable": False}
    return {
        "ok": False,
        "category": "TELEGRAM_HTTP_ERROR",
        "retryable": http_status in (408, 409, 425, 429),
    }


def _retry_after_from_body(body: bytes | None) -> float | None:
    if not body:
        return None
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
        params = data.get("parameters") or {}
        ra = params.get("retry_after")
        return float(ra) if ra is not None else None
    except Exception:
        return None


class TelegramDocumentTransporter:
    """sendDocument wrapper: multipart upload + caption + redaction.

    Sync, bounded, and isolated. Callers (worker or CLI) invoke upload() and
    inspect the returned dict. No network I/O is ever hidden inside a trading
    hot path.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        api_base: str = "https://api.telegram.org",
        timeout_seconds: float = 30.0,
        maximum_retries: int = 2,
        retry_backoff: float = 2.0,
        max_document_bytes: int = MAX_DOCUMENT_BYTES,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base = api_base
        self.timeout_seconds = timeout_seconds
        self.maximum_retries = maximum_retries
        self.retry_backoff = retry_backoff
        self.max_document_bytes = max_document_bytes
        self._uploaded_count = 0
        self._failed_count = 0

    # ------------------------------------------------------------------

    def upload(
        self, file_path: str | Path, caption: str = "", *, disable_notification: bool = False
    ) -> dict[str, Any]:
        """Upload one file as a Telegram document with an HTML caption.

        Returns {ok, category, retryable, message_id?, safe_message?}.
        Never raises; a failed upload is a structured result.
        """
        path = Path(file_path)
        if not path.exists():
            self._failed_count += 1
            return {
                "ok": False,
                "category": "TELEGRAM_FILE_NOT_FOUND",
                "retryable": False,
                "safe_message": str(path),
            }
        size = path.stat().st_size
        if size > self.max_document_bytes:
            self._failed_count += 1
            return {
                "ok": False,
                "category": "TELEGRAM_FILE_TOO_LARGE",
                "retryable": False,
                "safe_message": f"{path.name} {size} bytes > {self.max_document_bytes}",
            }

        caption_redacted = redact_secrets(caption)
        file_content = path.read_bytes()

        attempt = 0
        last_result: dict[str, Any] = {
            "ok": False,
            "category": "TELEGRAM_UNKNOWN_ERROR",
            "retryable": True,
        }
        while attempt <= self.maximum_retries:
            attempt += 1
            last_result = self._post_document(
                path, file_content, caption_redacted, disable_notification
            )
            if last_result.get("ok"):
                self._uploaded_count += 1
                return last_result
            if not last_result.get("retryable", False):
                break
            retry_after = last_result.get("retry_after")
            delay = (
                retry_after
                if retry_after is not None
                else self.retry_backoff * (2 ** (attempt - 1))
            )
            logger.warning(
                "[TELEGRAM_DOC] event=RETRY attempt=%d category=%s delay=%.1fs",
                attempt,
                last_result.get("category"),
                delay,
            )
            if attempt <= self.maximum_retries:
                time.sleep(min(delay, 10.0))
        self._failed_count += 1
        last_result.setdefault("safe_message", "document upload failed")
        return last_result

    # ------------------------------------------------------------------

    def _post_document(
        self,
        path: Path,
        content: bytes,
        caption: str,
        disable_notification: bool,
    ) -> dict[str, Any]:
        url = f"{self.api_base}/bot{self.bot_token}/sendDocument"
        boundary = f"----nexus-ci-{int(time.time() * 1000)}"
        fields = [
            ("chat_id", self.chat_id),
            ("caption", caption),
            ("parse_mode", "HTML"),
            ("disable_notification", "true" if disable_notification else "false"),
        ]
        body = self._build_multipart(boundary, fields, path.name, content)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read(100_000)
                return self._parse_document_response(resp.status, raw)
        except urllib.error.HTTPError as err:
            raw = err.read(100_000)
            return self._parse_document_response(err.code, raw)
        except (TimeoutError, urllib.error.URLError):
            return {"ok": False, "category": "TELEGRAM_NETWORK_ERROR", "retryable": True}

    def _parse_document_response(self, http_status: int, body: bytes) -> dict[str, Any]:
        result = dict(_classify_document_response(http_status, body))
        if http_status == 429:
            ra = _retry_after_from_body(body)
            if ra is not None:
                result["retry_after"] = ra
        if result.get("ok"):
            try:
                data = json.loads(body.decode("utf-8", errors="replace"))
                msg = data.get("result") or {}
                result["message_id"] = msg.get("message_id")
            except Exception:
                pass
        if not result.get("ok"):
            # Keep any error body OUT of the result (may embed secrets).
            result["safe_message"] = (
                f"HTTP {http_status}" if http_status else result.get("category")
            )
        return result

    @staticmethod
    def _build_multipart(
        boundary: str, fields: list[tuple[str, str]], filename: str, content: bytes
    ) -> bytes:
        parts: list[bytes] = []
        for name, value in fields:
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            parts.append(value.encode("utf-8"))
            parts.append(b"\r\n")
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'.encode()
        )
        parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
        parts.append(content)
        parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return b"".join(parts)

    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        return {"uploaded": self._uploaded_count, "failed": self._failed_count}


__all__ = [
    "MAX_DOCUMENT_BYTES",
    "TelegramDocumentTransporter",
    "redact_secrets",
]
