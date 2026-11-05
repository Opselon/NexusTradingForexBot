"""HTTP client for the trusted platform (NSE-Platform device-connect API).

Zero-dependency (stdlib urllib), strict timeouts, HTTPS-pinned by config.
Error taxonomy is explicit so the service layer can never confuse an
infrastructure failure with a missing star.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from nexus_scalp.platform_link.config import PlatformLinkConfig


class PlatformClientError(Exception):
    """Base class: the platform interaction failed. Never a star verdict."""

    code = "PLATFORM_ERROR"


class PlatformUnavailableError(PlatformClientError):
    """Network failure, timeout, 5xx — state unknown, fail closed."""

    code = "VERIFICATION_UNAVAILABLE"


class PlatformRateLimitedError(PlatformClientError):
    """The platform asked us to slow down. Retry later."""

    code = "VERIFICATION_RATE_LIMITED"


class PlatformTokenRejectedError(PlatformClientError):
    """401 from a client-token endpoint: token invalid, expired or revoked."""

    code = "VERIFICATION_EXPIRED"


class PlatformInvalidResponseError(PlatformClientError):
    """The platform answered with an unexpected payload."""

    code = "PLATFORM_INVALID_RESPONSE"


class PlatformClient:
    """Small, strict HTTP client for /api/connect/* and /api/client/*."""

    def __init__(self, config: PlatformLinkConfig | None = None) -> None:
        self.config = config or PlatformLinkConfig.from_env()

    # ------------------------------------------------------------------ verbs
    def _request(self, path: str, payload: dict[str, Any] | None = None, token: str | None = None) -> dict[str, Any]:
        url = f"{self.config.base_url}{path}"
        data = None
        headers = {"Accept": "application/json", "User-Agent": "NexusTradingForexBot"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(
                req, timeout=self.config.connect_timeout + self.config.read_timeout
            ) as resp:
                body = resp.read(1 << 20)
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body = exc.read(1 << 20)
            except OSError:  # pragma: no cover - defensive
                body = b""
            if status in (401, 403):
                raise PlatformTokenRejectedError(f"platform rejected credentials (HTTP {status})") from exc
            if status == 429:
                raise PlatformRateLimitedError("platform rate limited this client") from exc
            if status >= 500:
                raise PlatformUnavailableError(f"platform error (HTTP {status})") from exc
            raise PlatformInvalidResponseError(f"unexpected platform status {status}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PlatformUnavailableError(f"platform unreachable: {exc}") from exc

        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise PlatformInvalidResponseError("platform returned non-JSON body") from exc
        if not isinstance(parsed, dict):
            raise PlatformInvalidResponseError("platform returned non-object JSON")
        if status >= 400:
            # 4xx with JSON body (bad request etc.) — surface as invalid.
            raise PlatformInvalidResponseError(str(parsed.get("error") or f"HTTP {status}"))
        return parsed

    # ---------------------------------------------------------------- endpoints
    def start_desktop_session(self, installation_id: str) -> dict[str, Any]:
        """Create the one-click auth session. Returns the OFFICIAL GitHub
        authorize URL; the session secret is opaque and never user-visible."""
        out = self._request(
            "/api/desktop/session/start", {"installation_id": installation_id}
        )
        for field in ("session_id", "session_secret", "authorize_url"):
            if not isinstance(out.get(field), str) or not out[field]:
                raise PlatformInvalidResponseError(f"desktop/session/start missing {field}")
        if not str(out["authorize_url"]).startswith("https://github.com/login"):
            # The ONLY acceptable redirect target is GitHub's own authorize
            # page (or the platform's login entry that immediately 302s there
            # in dev/test). Never open anything else.
            url = str(out["authorize_url"])
            if not url.endswith("/api/auth/github/login?desktop=" + str(out["session_id"])):
                raise PlatformInvalidResponseError("authorize_url is not the official login entry")
        return out

    def poll_desktop_session(self, session_id: str, session_secret: str) -> dict[str, Any]:
        out = self._request(
            "/api/desktop/session/poll",
            {"session_id": session_id, "session_secret": session_secret},
        )
        if not isinstance(out.get("status"), str):
            raise PlatformInvalidResponseError("desktop/session/poll missing status")
        return out

    def client_me(self, token: str) -> dict[str, Any]:
        return self._request("/api/client/me", token=token)
