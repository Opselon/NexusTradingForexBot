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

from nexus_scalp.settings.secret_store import SecureSecretStore
from nexus_scalp.settings.service import SettingsService

SECRET_CLIENT_TOKEN = "platform_client_token"
SECRET_SESSION_SECRET = "platform_session_secret"
SECRET_SESSION_ID = "platform_session_id"
SETTING_INSTALLATION_ID = "platform.installation_id"
SETTING_LINK_STATUS = "platform.link_status"


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

    def has_client_token(self) -> bool:
        return self.secrets.has_secret(SECRET_CLIENT_TOKEN)

    # --------------------------------------------------- desktop auth session
    def save_desktop_session(self, session_id: str, session_secret: str) -> None:
        self.secrets.set_secret(SECRET_SESSION_ID, session_id)
        self.secrets.set_secret(SECRET_SESSION_SECRET, session_secret)

    def load_desktop_session(self) -> tuple[str, str] | None:
        session_id = self.secrets.get_secret(SECRET_SESSION_ID)
        session_secret = self.secrets.get_secret(SECRET_SESSION_SECRET)
        if not session_id or not session_secret:
            return None
        return session_id, session_secret

    def clear_desktop_session(self) -> None:
        for name in (SECRET_SESSION_ID, SECRET_SESSION_SECRET):
            if self.secrets.has_secret(name):
                self.secrets.delete_secret(name)

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
