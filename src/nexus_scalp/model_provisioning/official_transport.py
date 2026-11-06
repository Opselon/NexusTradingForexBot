"""Bounded HTTPS-only transport for signed official artifacts."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nexus_scalp.model_provisioning.official_contract import https_url, require


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate each hop before urllib makes the redirected request."""

    max_redirections = 5
    max_repeats = 2

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(
    url: str,
    dest: Path,
    *,
    timeout: float,
    max_bytes: int,
    cancel_event: Any = None,
    headers: dict[str, str] | None = None,
) -> None:
    from nexus_scalp.model_provisioning.official import OfficialBundleError

    https_url(url)
    started = time.monotonic()
    request_headers = {"User-Agent": "NexusScalpEngine/official-model", **(headers or {})}
    req = urllib.request.Request(url, headers=request_headers)
    opener = urllib.request.build_opener(HTTPSRedirectHandler())
    try:
        with opener.open(req, timeout=timeout) as resp, dest.open("wb") as out:
            https_url(resp.geturl())
            length = resp.headers.get("Content-Length")
            if length is not None:
                require(
                    length.isdecimal() and int(length) <= max_bytes,
                    "DOWNLOAD_TOO_LARGE",
                    "declared response exceeds bound",
                )
            count = 0
            while True:
                require(
                    cancel_event is None or not cancel_event.is_set(),
                    "CANCELLED",
                    "download cancelled",
                )
                require(
                    time.monotonic() - started < timeout,
                    "DOWNLOAD_TIMEOUT",
                    "download deadline exceeded",
                )
                chunk = resp.read(min(64 * 1024, max_bytes - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                require(count <= max_bytes, "DOWNLOAD_TOO_LARGE", "response exceeds bound")
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        # Platform API spec §5 invariant 4 + §6.3: a delivery denial ALWAYS
        # carries a reason — branch on it instead of a generic failure.
        if exc.code == 401:
            raise OfficialBundleError(
                "AUTHORIZATION_REQUIRED",
                "the platform rejected the download credentials — reconnect GitHub",
            ) from exc
        if exc.code == 403:
            reason = _denial_reason(exc)
            raise OfficialBundleError(
                "LEASE_DENIED" if reason else "DOWNLOAD_FORBIDDEN",
                f"delivery denied: {reason}" if reason else "delivery forbidden",
            ) from exc
        raise OfficialBundleError(
            "HTTP_NOT_FOUND" if exc.code == 404 else "DOWNLOAD_FAILED", f"HTTP status {exc.code}"
        ) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OfficialBundleError("DOWNLOAD_FAILED", str(exc)) from exc
    finally:
        # Failure/cancellation must not leave resumable-looking partial bytes.
        import sys

        if sys.exc_info()[0] is not None:
            dest.unlink(missing_ok=True)


def _denial_reason(exc: urllib.error.HTTPError) -> str:
    """Lease verdict from the universal error envelope (spec §1/§5)."""
    import json

    try:
        parsed = json.loads(exc.read(1 << 16).decode("utf-8"))
    except Exception:
        return ""
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict):
        return ""
    reason = str(error.get("reason") or "")
    if not reason:
        message = str(error.get("message") or "")
        if ": " in message:
            reason = message.split(": ", 1)[1]
    return reason
