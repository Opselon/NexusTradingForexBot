"""Isolated user/installation settings subsystem (BUG-072).

Public surface:
    SettingsService        — canonical settings provider (DB + secure secrets)
    SettingsDatabase       — isolated app_settings.db persistence
    SecureSecretStore      — OS-protected secret storage (Windows DPAPI)
    load_settings_service  — factory
"""

from __future__ import annotations

from nexus_scalp.settings.paths import (
    SETTINGS_DB_FILENAME,
    settings_db_path,
    settings_db_url,
)
from nexus_scalp.settings.secret_store import (
    SCHEME_ACL,
    SCHEME_DPAPI,
    SecretStoreError,
    SecureSecretStore,
)
from nexus_scalp.settings.service import (
    HOT_RESTRICTED,
    HOT_SAFE,
    INSTALLATION_ONLY,
    MIGRATION_FLAG_KEY,
    RESTART_REQUIRED,
    SECRET,
    STATE_CONFIG_INVALID,
    STATE_MIGRATION_REQUIRED,
    STATE_OK,
    STATE_SECRET_UNAVAILABLE,
    STATE_SETTINGS_DB_CORRUPT,
    STATE_SETTINGS_DB_UNAVAILABLE,
    TELEGRAM_ADMIN_KEY,
    TELEGRAM_ENABLED_KEY,
    TELEGRAM_TOKEN_KEY,
    SettingsDatabase,
    SettingsService,
    SettingsState,
    SettingValue,
    load_settings_service,
    new_correlation_id,
)

__all__ = [
    "HOT_RESTRICTED",
    "HOT_SAFE",
    "INSTALLATION_ONLY",
    "MIGRATION_FLAG_KEY",
    "RESTART_REQUIRED",
    "SCHEME_ACL",
    "SCHEME_DPAPI",
    "SECRET",
    "SETTINGS_DB_FILENAME",
    "STATE_CONFIG_INVALID",
    "STATE_MIGRATION_REQUIRED",
    "STATE_OK",
    "STATE_SECRET_UNAVAILABLE",
    "STATE_SETTINGS_DB_CORRUPT",
    "STATE_SETTINGS_DB_UNAVAILABLE",
    "TELEGRAM_ADMIN_KEY",
    "TELEGRAM_ENABLED_KEY",
    "TELEGRAM_TOKEN_KEY",
    "SecretStoreError",
    "SecureSecretStore",
    "SettingValue",
    "SettingsDatabase",
    "SettingsService",
    "SettingsState",
    "load_settings_service",
    "new_correlation_id",
    "settings_db_path",
    "settings_db_url",
]
