"""Centralized database configuration model + loader.

DATABASE PORTABILITY mission — single authoritative configuration for both
providers.  The model can represent: provider, connection string, database
name, host, port, username, password / secret reference, SSL mode, command
timeout, migration behavior, automatic migration policy and pooling
configuration.

Security contract:
  * passwords are NEVER hard-coded in source, configs or repo files;
  * the PostgreSQL password is stored in the OS-backed SecretStore (DPAPI on
    Windows, ACL-protected file elsewhere — see
    :mod:`nexus_scalp.settings.secret_store`);
  * environment overrides use the project's ``NSE_`` prefix convention.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus_scalp.database.provider import DatabaseProvider
from nexus_scalp.settings.secret_store import SecureSecretStore

#: SettingsDatabase key where the active provider is persisted.
PROVIDER_SETTING_KEY = "database.provider"

#: SettingsDatabase key where the PostgreSQL connection config is persisted
#: (JSON).  The password is stored separately in the SecretStore under
#: PG_PASSWORD_SECRET_KEY.
PG_CONFIG_SETTING_KEY = "database.postgresql_config"

#: SecretStore key for the PostgreSQL password.
PG_PASSWORD_SECRET_KEY = "db.postgresql.password"

#: Default PostgreSQL port (standard).
DEFAULT_PG_PORT = 5432

#: Placeholder used inside the (non-secret) connection URL when the password
#: must be injected at connect time from the secret store.
PG_SECRET_PLACEHOLDER = "__NSE_PG_SECRET__"


@dataclass
class DatabaseConfig:
    """Full connection + behavior configuration for a persistence domain.

    Attributes:
        provider: active relational provider.
        domain: persistence domain name (audit | news | candle_intel | ...).
        host: PostgreSQL host; empty for SQLite.
        port: PostgreSQL port; 0 for SQLite.
        database: PostgreSQL database name, or the SQLite file path
            (when ``sqlite_path`` is empty).
        username: PostgreSQL role; empty for SQLite.
        password_secret: SecretStore key holding the PostgreSQL password;
            empty for SQLite.  Never store the plaintext password here.
        ssl_mode: PostgreSQL SSL mode (disable | allow | prefer | require |
            verify-ca | verify-full).  Empty for SQLite.
        command_timeout_sec: per-statement timeout; 0 = provider default.
        migrate_on_startup: apply schema migrations at startup (automatic
            migration policy).
        pooling_enabled: use connection pooling when the provider supports it
            (PostgreSQL pgbouncer-compatible URL when disabled).
        connect_timeout_sec: connection establishment timeout.
    """

    provider: DatabaseProvider = DatabaseProvider.SQLITE
    domain: str = "audit"
    host: str = ""
    port: int = 0
    database: str = ""
    username: str = ""
    password_secret: str = ""
    ssl_mode: str = ""
    command_timeout_sec: int = 0
    migrate_on_startup: bool = True
    pooling_enabled: bool = True
    connect_timeout_sec: int = 10
    #: Optional explicit SQLite file path (overrides `database`).
    sqlite_path: str = ""
    #: Optional explicit file:// URI for SQLite (e.g. file::memory:?cache=shared).
    sqlite_uri: str = ""

    # -- constructors -----------------------------------------------------

    @classmethod
    def for_sqlite(cls, domain: str, path: str = "", uri: str = "") -> DatabaseConfig:
        from nexus_scalp.database.provider import default_sqlite_path

        return cls(
            provider=DatabaseProvider.SQLITE,
            domain=domain,
            sqlite_path=path or default_sqlite_path(domain),
            sqlite_uri=uri,
        )

    @classmethod
    def for_postgres(
        cls,
        domain: str = "audit",
        host: str = "localhost",
        port: int = DEFAULT_PG_PORT,
        database: str = "nse_audit",
        username: str = "nse_user",
        password_secret: str = PG_PASSWORD_SECRET_KEY,
        ssl_mode: str = "",
        command_timeout_sec: int = 0,
        migrate_on_startup: bool = True,
        pooling_enabled: bool = True,
        connect_timeout_sec: int = 10,
    ) -> DatabaseConfig:
        return cls(
            provider=DatabaseProvider.POSTGRESQL,
            domain=domain,
            host=host,
            port=port,
            database=database,
            username=username,
            password_secret=password_secret,
            ssl_mode=ssl_mode,
            command_timeout_sec=command_timeout_sec,
            migrate_on_startup=migrate_on_startup,
            pooling_enabled=pooling_enabled,
            connect_timeout_sec=connect_timeout_sec,
        )

    # -- accessors --------------------------------------------------------

    @property
    def is_sqlite(self) -> bool:
        return self.provider.is_sqlite

    @property
    def is_postgresql(self) -> bool:
        return self.provider.is_postgresql

    @property
    def sqlite_connect_path(self) -> str:
        """Path/URI passed to the sqlite3 driver."""
        return self.sqlite_uri or self.sqlite_path or self.database or ":memory:"

    def build_url(self, password: str = PG_SECRET_PLACEHOLDER) -> str:
        """SQLAlchemy-style connection URL for diagnostics/logging.

        The password is NEVER logged: when called without an explicit
        password the URL carries the placeholder, and
        :func:`mask_url_password` strips even that for log emission.
        """
        if self.is_sqlite:
            return f"sqlite:///{self.sqlite_connect_path}"
        pw = password if password else self.password_secret
        host_part = self.host
        if ":" in host_part and not host_part.startswith("["):
            host_part = f"[{host_part}]"
        return (
            f"postgresql://{self.username}:{pw}@{host_part}:{self.port or DEFAULT_PG_PORT}"
            f"/{self.database}"
        )

    # -- persistence ------------------------------------------------------

    def to_dict(self, include_secret_ref: bool = True) -> dict[str, Any]:
        """Plain dict for settings persistence.  Only the secret KEY is
        stored, never the password value."""
        out: dict[str, Any] = {
            "provider": self.provider.value,
            "domain": self.domain,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "ssl_mode": self.ssl_mode,
            "command_timeout_sec": self.command_timeout_sec,
            "migrate_on_startup": self.migrate_on_startup,
            "pooling_enabled": self.pooling_enabled,
            "connect_timeout_sec": self.connect_timeout_sec,
            "sqlite_path": self.sqlite_path,
            "sqlite_uri": self.sqlite_uri,
        }
        if include_secret_ref:
            out["password_secret"] = self.password_secret
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None, domain: str = "audit") -> DatabaseConfig:
        """Rebuild a config from persisted settings (see :meth:`to_dict`)."""
        if not raw:
            return cls.for_sqlite(domain)
        try:
            return cls(
                provider=DatabaseProvider.parse(raw.get("provider")),
                domain=raw.get("domain") or domain,
                host=str(raw.get("host") or ""),
                port=int(raw.get("port") or 0),
                database=str(raw.get("database") or ""),
                username=str(raw.get("username") or ""),
                password_secret=str(raw.get("password_secret") or ""),
                ssl_mode=str(raw.get("ssl_mode") or ""),
                command_timeout_sec=int(raw.get("command_timeout_sec") or 0),
                migrate_on_startup=bool(raw.get("migrate_on_startup", True)),
                pooling_enabled=bool(raw.get("pooling_enabled", True)),
                connect_timeout_sec=int(raw.get("connect_timeout_sec") or 10),
                sqlite_path=str(raw.get("sqlite_path") or ""),
                sqlite_uri=str(raw.get("sqlite_uri") or ""),
            )
        except (TypeError, ValueError):
            return cls.for_sqlite(domain)


def mask_url_password(url: str) -> str:
    """Sanitize a connection URL for logs: never emit a real password."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        creds, tail = rest.rsplit("@", 1)
        if ":" in creds:
            user, _pw = creds.split(":", 1)
            creds = f"{user}:***"
        rest = f"{creds}@{tail}"
    return f"{scheme}://{rest}"


def load_database_config(
    domain: str = "audit",
    *,
    settings_db_path: str | None = None,
    env: dict[str, str] | None = None,
) -> DatabaseConfig:
    """Load the authoritative DatabaseConfig for a domain.

    Resolution order (last wins):
      1. defaults (SQLite, canonical artifacts path);
      2. persisted settings database (``database.provider`` +
         ``database.postgresql_config``);
      3. environment overrides (``NSE_DATABASE__PROVIDER``,
         ``NSE_DATABASE__PG_HOST`` etc. — the double-underscore convention
         used throughout the configuration system; env wins for containers).
    """
    from nexus_scalp.settings.service import SettingsDatabase

    cfg = DatabaseConfig.for_sqlite(domain)

    envd = env if env is not None else os.environ
    provider_env = envd.get("NSE_DATABASE__PROVIDER", "").strip()

    # --- persisted settings (authoritative for interactive installs) -------
    db = None
    if settings_db_path is not None or not provider_env:
        # Opening the settings DB is best-effort: a fresh environment (no
        # app_settings.db yet) must fall back to SQLite defaults silently.
        try:
            db = SettingsDatabase(db_path=Path(settings_db_path) if settings_db_path else None)
            prov = db.get(PROVIDER_SETTING_KEY)
            if prov and prov.value:
                selected = DatabaseProvider.parse(prov.value)
                if selected.is_postgresql:
                    pg_cfg = DatabaseConfig.for_postgres(domain=domain)
                    pg_cfg.sqlite_path = cfg.sqlite_path
                    cfg = pg_cfg
            pg_raw = db.get(PG_CONFIG_SETTING_KEY)
            if pg_raw and pg_raw.value:
                import json

                try:
                    # SettingsDatabase.get() already decodes value_type=json
                    # rows to a dict (service.py persists this key as json).
                    # Accept either shape: raw string (older rows) or dict.
                    parsed = (
                        json.loads(pg_raw.value)
                        if isinstance(pg_raw.value, str)
                        else pg_raw.value
                    )
                    if isinstance(parsed, dict):
                        pg = DatabaseConfig.from_dict(parsed, domain)
                        if pg.is_postgresql:
                            pg.domain = domain
                            if not pg.sqlite_path:
                                pg.sqlite_path = cfg.sqlite_path
                            cfg = pg
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    # --- environment overrides (containers / CI / one-off runs) ------------
    if provider_env:
        env_provider = DatabaseProvider.parse(provider_env)
        if env_provider.is_postgresql:
            cfg = DatabaseConfig.for_postgres(
                domain=domain,
                host=envd.get("NSE_DATABASE__PG_HOST", cfg.host or "localhost"),
                port=int(envd.get("NSE_DATABASE__PG_PORT", str(cfg.port or DEFAULT_PG_PORT))),
                database=envd.get("NSE_DATABASE__PG_DATABASE", cfg.database or f"nse_{domain}"),
                username=envd.get("NSE_DATABASE__PG_USER", cfg.username or "nse_user"),
                password_secret=PG_PASSWORD_SECRET_KEY,
                ssl_mode=envd.get("NSE_DATABASE__PG_SSLMODE", cfg.ssl_mode or ""),
                migrate_on_startup=cfg.migrate_on_startup,
                pooling_enabled=cfg.pooling_enabled,
            )
        else:
            path = envd.get("NSE_DATABASE__SQLITE_PATH", "")
            cfg = DatabaseConfig.for_sqlite(domain, path=path)
    return cfg


def resolve_password(cfg: DatabaseConfig, secret_store: SecureSecretStore | None = None) -> str:
    """Resolve the PostgreSQL password from the secret store.

    Raises RuntimeError when PostgreSQL is configured but the password is
    missing (secure-configuration contract: no silent empty credentials).
    """
    if not cfg.is_postgresql:
        return ""
    store = secret_store or SecureSecretStore()
    key = cfg.password_secret or PG_PASSWORD_SECRET_KEY
    pw = store.get_secret(key)
    if not pw:
        raise RuntimeError(
            f"PostgreSQL password not found in secret store under '{key}'. "
            "Set it via `nexus db postgres set-password` or the DATABASE "
            "MANAGEMENT UI before connecting."
        )
    return pw


def build_postgres_url(cfg: DatabaseConfig, secret_store: SecureSecretStore | None = None) -> str:
    """Assemble the real PostgreSQL URL, injecting the secret password."""
    pw = resolve_password(cfg, secret_store)
    return cfg.build_url(password=pw)
