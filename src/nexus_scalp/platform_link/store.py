"""Persistence for the platform link.

Secret layout (OS-backed encryption via SecureSecretStore / DPAPI):
- ``platform_client_token``   the scoped bearer token issued by the platform
- ``platform_session_secret`` in-flight auth session secret (deleted on completion)
- ``platform_session_id``     in-flight auth session id

Non-secret layout (settings DB, auditable):
- ``platform.installation_id``  stable local installation identity
- ``platform.link_status``      last attested status snapshot (JSON)
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.settings.secret_store import SecureSecretStore
from nexus_scalp.settings.service import SettingsService

SECRET_CLIENT_TOKEN = "platform_client_token"
SECRET_TOKEN_EXPIRES_AT = "platform_token_expires_at"
SECRET_SESSION_SECRET = "platform_session_secret"
SECRET_SESSION_ID = "platform_session_id"
SECRET_SESSION_POLL_INTERVAL = "platform_session_poll_interval"
SETTING_INSTALLATION_ID = "platform.installation_id"
SETTING_LINK_STATUS = "platform.link_status"
SETTING_MODEL_INFO = "platform.model_info"
SETTING_LAST_HANDSHAKE = "platform.last_handshake_at"
SETTING_HANDSHAKE = "platform.startup_handshake"


logger = get_logger("nexus_scalp.platform_link.store")


class PlatformLinkStore:
    """Thin, testable persistence seam over settings + secret store."""

    def __init__(
        self,
        settings: SettingsService | None = None,
        secrets: SecureSecretStore | None = None,
    ) -> None:
        self.settings = settings or SettingsService()
        self.secrets = secrets or SecureSecretStore()

    # ---------------------------------------------------------- installation
    def installation_id(self) -> str:
        existing = self.settings.get(SETTING_INSTALLATION_ID)
        if existing is not None and str(existing.value):
            return str(existing.value)
        install_id = str(uuid.uuid4())
        self.settings.set(SETTING_INSTALLATION_ID, install_id, value_type="string")
        return install_id

    # ---------------------------------------------------------------- tokens
    def client_token(self) -> str | None:
        value = self.secrets.get_secret(SECRET_CLIENT_TOKEN)
        return value or None

    def save_client_token(self, token: str) -> None:
        self.secrets.set_secret(SECRET_CLIENT_TOKEN, token)

    def clear_client_token(self) -> None:
        if self.secrets.has_secret(SECRET_CLIENT_TOKEN):
            self.secrets.delete_secret(SECRET_CLIENT_TOKEN)
        if self.secrets.has_secret(SECRET_TOKEN_EXPIRES_AT):
            self.secrets.delete_secret(SECRET_TOKEN_EXPIRES_AT)

    def save_token_meta(self, expires_at_iso: str) -> None:
        self.secrets.set_secret(SECRET_TOKEN_EXPIRES_AT, expires_at_iso)

    def load_token_meta(self) -> str | None:
        value = self.secrets.get_secret(SECRET_TOKEN_EXPIRES_AT)
        return value or None

    def has_client_token(self) -> bool:
        return self.secrets.has_secret(SECRET_CLIENT_TOKEN)

    # --------------------------------------------------- desktop auth session
    def save_desktop_session(
        self, session_id: str, session_secret: str, poll_interval: float | None = None
    ) -> None:
        self.secrets.set_secret(SECRET_SESSION_ID, session_id)
        self.secrets.set_secret(SECRET_SESSION_SECRET, session_secret)
        # Server-honored cadence (spec §3.1 poll_interval, default 3s) — the
        # client polls at the platform's pace instead of a local guess.
        if poll_interval and poll_interval > 0:
            self.secrets.set_secret(SECRET_SESSION_POLL_INTERVAL, str(poll_interval))

    def load_desktop_session(self) -> tuple[str, str] | None:
        session_id = self.secrets.get_secret(SECRET_SESSION_ID)
        session_secret = self.secrets.get_secret(SECRET_SESSION_SECRET)
        if not session_id or not session_secret:
            return None
        return session_id, session_secret

    def load_session_poll_interval(self) -> float | None:
        raw = self.secrets.get_secret(SECRET_SESSION_POLL_INTERVAL)
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError as exc:  # anti-crash: allow (garbage in the secret slot is non-fatal)
            logger.debug("[PLATFORM-LINK] unparsable session poll interval", error=str(exc))
            return None
        return value if 0.5 <= value <= 60.0 else None

    def clear_desktop_session(self) -> None:
        for name in (SECRET_SESSION_ID, SECRET_SESSION_SECRET, SECRET_SESSION_POLL_INTERVAL):
            if self.secrets.has_secret(name):
                self.secrets.delete_secret(name)

    # ------------------------------------------------------- handshake cadence
    def save_last_handshake(self, epoch: float) -> None:
        self.settings.set(SETTING_LAST_HANDSHAKE, repr(float(epoch)), value_type="string")

    def load_last_handshake(self) -> float | None:
        raw = self.settings.get(SETTING_LAST_HANDSHAKE)
        if raw is None or not str(raw.value):
            return None
        try:
            value = float(str(raw.value))
        except ValueError as exc:  # anti-crash: allow (non-fatal UX state)
            logger.debug("[PLATFORM-LINK] unparsable handshake timestamp", error=str(exc))
            return None
        return value if value > 0 else None

    # ------------------------------------------------------------- model info
    def save_model_info(self, payload: dict[str, Any]) -> None:
        """Cache the /api/client/me model entry (version + benchmark stats).

        Additive + tolerate-unknown policy: only known-safe scalar fields are
        kept, and stale info never grants anything — it is display-only."""
        known = {
            key: payload[key]
            for key in ("version", "id", "dimension", "published_at")
            if isinstance(payload.get(key), (str, int, float))
        }
        benchmark = payload.get("benchmark")
        if isinstance(benchmark, dict):
            for key in ("win_rate", "max_drawdown"):
                if isinstance(benchmark.get(key), (int, float)):
                    known[key] = benchmark[key]
        self.settings.set(
            SETTING_MODEL_INFO, json.dumps(known, sort_keys=True), value_type="string"
        )

    def load_model_info(self) -> dict[str, Any] | None:
        raw = self.settings.get(SETTING_MODEL_INFO)
        if raw is None or not str(raw.value):
            return None
        try:
            data = json.loads(str(raw.value))
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------ status cache
    def save_status(self, payload: dict[str, Any]) -> None:
        self.settings.set(
            SETTING_LINK_STATUS,
            json.dumps(payload, sort_keys=True),
            value_type="string",
        )

    def load_status(self) -> dict[str, Any] | None:
        raw = self.settings.get(SETTING_LINK_STATUS)
        if raw is None or not str(raw.value):
            return None
        try:
            data = json.loads(str(raw.value))
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def clear_status(self) -> None:
        self.settings.set(SETTING_LINK_STATUS, "", value_type="string")

    # -------------------------------------------------- startup handshake cache
    def save_handshake(self, payload: dict[str, Any]) -> None:
        self.settings.set(
            SETTING_HANDSHAKE,
            json.dumps(payload, sort_keys=True),
            value_type="string",
        )

    def load_handshake(self) -> dict[str, Any] | None:
        raw = self.settings.get(SETTING_HANDSHAKE)
        if raw is None or not str(raw.value):
            return None
        try:
            data = json.loads(str(raw.value))
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None
