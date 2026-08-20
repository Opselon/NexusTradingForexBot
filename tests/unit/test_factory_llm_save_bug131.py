"""Regression tests: factory LLM config save semantics (BUG-131 save failure).

Root cause: the web UI always sends api_key="" (the key is never echoed back
to the DOM), and set_factory_llm_config interpreted ANY empty string as
"delete the stored secret" -> clicking Save in the UI wiped the user's real
API key -> provider hot-rebuild failed -> "LLM config save failed:
INTERNAL_ERROR / UNKNOWN".

Fixed semantics (production-safe):
1. api_key=""  or api_key=None  -> KEEP the existing stored key (no-op).
2. api_key="sk-..."             -> overwrite the stored key.
3. api_key=""  AND clear_api_key=True -> EXPLICITLY delete the stored key.
4. Saving base_url/model/temperature/timeout/requests NEVER touches the key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus_scalp.settings import SettingsDatabase, SettingsService
from nexus_scalp.settings.secret_store import SecureSecretStore


@pytest.fixture()
def service(tmp_path: Path) -> SettingsService:
    return SettingsService(db=SettingsDatabase(tmp_path / "settings.db"))


class _FakeSecrets:
    """In-memory stand-in for SecureSecretStore (avoids DPAPI in tests)."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set_secret(self, key: str, value: str) -> None:
        self._store[key] = value

    def get_secret(self, key: str) -> str | None:
        return self._store.get(key)

    def delete_secret(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    def has_secret(self, key: str) -> bool:
        return key in self._store


@pytest.fixture()
def svc_with_fake_secrets(tmp_path: Path) -> SettingsService:
    svc = SettingsService(db=SettingsDatabase(tmp_path / "settings_fake.db"))
    svc.secrets = _FakeSecrets()  # type: ignore[assignment]
    return svc


class TestApiKeySemantics:
    def test_empty_key_keeps_existing_secret(self, svc_with_fake_secrets: SettingsService) -> None:
        svc = svc_with_fake_secrets
        svc.set_factory_llm_config(api_key="sk-real-key-123", actor="test")
        assert svc.secrets.get_secret("factory.llm_api_key") == "sk-real-key-123"  # type: ignore[attr-defined]

        # The UI save with EMPTY key field must NOT delete the stored key
        svc.set_factory_llm_config(
            api_key="",
            base_url="http://x/v1",
            model="m",
            temperature=0.5,
            request_timeout_sec=300,
            max_requests_per_generation=60,
            actor="web",
        )
        assert svc.secrets.get_secret("factory.llm_api_key") == "sk-real-key-123"  # type: ignore[attr-defined]

    def test_none_key_keeps_existing_secret(self, svc_with_fake_secrets: SettingsService) -> None:
        svc = svc_with_fake_secrets
        svc.set_factory_llm_config(api_key="sk-real-key-456", actor="test")
        svc.set_factory_llm_config(api_key=None, actor="web")
        assert svc.secrets.get_secret("factory.llm_api_key") == "sk-real-key-456"  # type: ignore[attr-defined]

    def test_new_key_overwrites(self, svc_with_fake_secrets: SettingsService) -> None:
        svc = svc_with_fake_secrets
        svc.set_factory_llm_config(api_key="sk-old", actor="test")
        svc.set_factory_llm_config(api_key="sk-new", actor="web")
        assert svc.secrets.get_secret("factory.llm_api_key") == "sk-new"  # type: ignore[attr-defined]

    def test_explicit_clear_deletes_key(self, svc_with_fake_secrets: SettingsService) -> None:
        svc = svc_with_fake_secrets
        svc.set_factory_llm_config(api_key="sk-temp", actor="test")
        svc.set_factory_llm_config(api_key="", clear_api_key=True, actor="web")
        assert svc.secrets.get_secret("factory.llm_api_key") is None  # type: ignore[attr-defined]

    def test_meta_only_save_keeps_key_and_updates_fields(
        self, svc_with_fake_secrets: SettingsService
    ) -> None:
        svc = svc_with_fake_secrets
        svc.set_factory_llm_config(
            api_key="sk-keep",
            base_url="http://old/v1",
            model="old-model",
            actor="test",
        )
        # User only changes temperature — key field left empty
        svc.set_factory_llm_config(temperature=0.9, actor="web")
        assert svc.secrets.get_secret("factory.llm_api_key") == "sk-keep"  # type: ignore[attr-defined]
        cfg = svc.get_factory_llm_config()
        assert cfg["temperature"] == 0.9
        assert cfg["model"] == "old-model"

    def test_status_still_configured_after_meta_save(
        self, svc_with_fake_secrets: SettingsService
    ) -> None:
        svc = svc_with_fake_secrets
        svc.set_factory_llm_config(
            api_key="sk-keep",
            base_url="http://x/v1",
            model="m",
            actor="test",
        )
        svc.set_factory_llm_config(temperature=0.6, actor="web")
        st = svc.factory_llm_config_status()
        assert st["configured"] is True
        assert svc.secrets.get_secret("factory.llm_api_key") == "sk-keep"  # type: ignore[attr-defined]

    def test_timeout_and_maxreq_persisted(self, svc_with_fake_secrets: SettingsService) -> None:
        svc = svc_with_fake_secrets
        svc.set_factory_llm_config(
            request_timeout_sec=420,
            max_requests_per_generation=25,
            actor="web",
        )
        cfg = svc.get_factory_llm_config()
        assert cfg["request_timeout_sec"] == 420.0
        assert cfg["max_requests_per_generation"] == 25
