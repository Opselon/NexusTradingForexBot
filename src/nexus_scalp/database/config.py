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


class DatabaseConfigError(RuntimeError):
    """Configuration contract violation — FAIL LOUDLY, never silently degrade.

    A PostgreSQL configuration that is missing required fields or holds
    out-of-range values must NOT silently fall back to SQLite (the observed
    defect: a malformed ``database.postgresql_config`` row reverted the whole
    stack to SQLite with no signal). This error is raised at validation
    boundaries so the operator sees exactly what is wrong.
    """


#: SettingsDatabase key where the active provider is persisted.
PROVIDER_SETTING_KEY = "database.provider"

#: SQL-standard SSL mode values accepted by both the driver and the UI.
#: Matches nexus_scalp.database.connection_url._SSL_MODES (canonical set).
_VALID_SSL_MODES = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})

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

    def validate(self) -> None:
        """Validate this configuration against the persistence contract.

        Raises :class:`DatabaseConfigError` with a single actionable sentence
        naming the offending field. A PostgreSQL configuration that cannot be
        used MUST raise here rather than degrade to SQLite.
        """
        if self.is_sqlite:
            return
        if self.is_postgresql:
            if not self.host:
                raise DatabaseConfigError("PostgreSQL config is missing its host.")
            if not self.database:
                raise DatabaseConfigError("PostgreSQL config is missing its database name.")
            if not self.username:
                raise DatabaseConfigError("PostgreSQL config is missing its username.")
            if not (1 <= self.port <= 65535):
                raise DatabaseConfigError(f"PostgreSQL port {self.port} is out of range (1-65535).")
            if self.connect_timeout_sec is not None and self.connect_timeout_sec <= 0:
                raise DatabaseConfigError(
                    f"connect_timeout_sec must be positive, got {self.connect_timeout_sec}."
                )
            if self.ssl_mode and self.ssl_mode not in _VALID_SSL_MODES:
                raise DatabaseConfigError(
                    f"ssl_mode '{self.ssl_mode}' is not a recognized SSL mode "
                    f"(expected one of: {', '.join(sorted(_VALID_SSL_MODES))})."
                )

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
        # A credential is operator-chosen and may carry characters that are
        # illegal in a URL (spaces, '@', '/', ':', '%'). Emitting them raw
        # makes psycopg reject the whole string with "unexpected spaces", so
        # every userinfo component is percent-encoded before assembly.
        from urllib.parse import quote

        host_part = self.host
        if ":" in host_part and not host_part.startswith("["):
            host_part = f"[{host_part}]"
        return (
            f"postgresql://{quote(self.username or '', safe='')}"
            f":{quote(pw, safe='')}"
            f"@{host_part}:{self.port or DEFAULT_PG_PORT}"
            f"/{quote(self.database or '', safe='')}"
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
        """Rebuild a config from persisted settings (see :meth:`to_dict`).

        Fails loudly on a malformed PostgreSQL configuration: a partial or
        corrupt row raises :class:`DatabaseConfigError` rather than silently
        reverting to SQLite. ``raw`` being ``None``/empty still means "no
        persisted configuration" and returns the SQLite default — that is the
        legitimate unset case, not a fallback.
        """
        if not raw:
            return cls.for_sqlite(domain)
        try:
            cfg = cls(
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
        except (TypeError, ValueError) as exc:
            raise DatabaseConfigError(
                f"PostgreSQL configuration row is malformed: {exc}. "
                "Fix the stored database.postgresql_config or switch the "
                "provider explicitly with `nexus db-portability switch sqlite`."
            ) from exc
        # An unknown provider string is a config error, not a SQLite default.
        cfg.validate()
        return cfg


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


def _pg_dsn_kwargs(url: str) -> dict[str, Any]:
    """Parse a ``postgresql://[user[:pw]@]host[:port]/database`` DSN.

    The minimal shape :meth:`DatabaseConfig.for_postgres` needs to point at the
    cluster the seam names.  A password in the URL is left in the URL — the
    config never stores a plaintext password, it stores a SECRET KEY
    (``PG_PASSWORD_SECRET_KEY``), and the live credential is resolved at
    connect time from the secret store.  Components that carry their own
    password (an isolated CI cluster) read the DSN directly.
    """
    rest = url.split("://", 1)[1]
    authority, _, path = rest.partition("/")
    user = ""
    if "@" in authority:
        user, authority = authority.rsplit("@", 1)
    # Strip the password: never copied into the config object.
    if ":" in user:
        user = user.split(":", 1)[0]
    host, _, port = authority.partition(":")
    kwargs: dict[str, Any] = {"host": host or "localhost"}
    if port.strip().isdigit():
        kwargs["port"] = int(port)
    if path.strip():
        kwargs["database"] = path.strip()
    if user.strip():
        kwargs["username"] = user.strip()
    return kwargs


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

    Exception — the audit test-isolation seam (``NEXUS_AUDIT_DB``, BUG-223 /
    BUG-278 / CHG-0067) sits ABOVE the persisted settings: for domain
    ``"audit"`` an explicit ``NSE_DATABASE__PROVIDER`` still beats everything,
    an explicit ``NSE_DATABASE__SQLITE_PATH`` beats the seam, and the seam
    itself beats the persisted provider and persisted PostgreSQL connection
    settings. Without the seam, persisted settings keep their full authority.
    Non-audit domains ignore the seam entirely.
    """
    from nexus_scalp.settings.service import SettingsDatabase

    cfg = DatabaseConfig.for_sqlite(domain)

    envd = env if env is not None else os.environ
    provider_env = envd.get("NSE_DATABASE__PROVIDER", "").strip()

    # BUG-278 / CHG-0067: the BUG-223 test-isolation seam (NEXUS_AUDIT_DB) must
    # reach THIS resolver too — LiveEngine's implicit audit construction goes
    # through load_database_config("audit"), which previously anchored to the
    # PRODUCTION artifacts/audit.db regardless of the env (the seam was only
    # honored inside AuditRepository's legacy implicit default).
    #
    # Precedence ladder for domain == "audit" (CHG-0067 standing user
    # directive — supersedes the persisted-wins wording of the original
    # BUG-278 note):
    #   1. an explicit NSE_DATABASE__PROVIDER always wins (containers / CI);
    #   2. an explicit NSE_DATABASE__SQLITE_PATH wins over the seam;
    #   3. NEXUS_AUDIT_DB wins over the PERSISTED provider AND the persisted
    #      PostgreSQL connection settings — a machine that chose PostgreSQL
    #      must not lose its test-isolation just because the settings DB says
    #      so (this is the fix; previously the persisted block below overrode
    #      the seam);
    #   4. without the seam, persisted settings behave exactly as before — a
    #      production install that chose PostgreSQL keeps PostgreSQL;
    #   5. non-audit domains ignore the seam entirely.
    audit_seam_applies = False
    if domain == "audit" and not provider_env:
        env_audit_db = envd.get("NEXUS_AUDIT_DB", "").strip()
        if env_audit_db and not envd.get("NSE_DATABASE__SQLITE_PATH", "").strip():
            audit_seam_applies = True
            # The seam is a DATABASE URL, not a filesystem path: a postgresql://
            # URL was being stamped into sqlite_path and every downstream reader
            # (the web DB console, the driver factory) then tried to open it as
            # a SQLite file and died with "unable to open database file" — the
            # audit READ surface went blind on a PostgreSQL cluster even though
            # the writes had landed. Detect the scheme and build the right
            # provider config: postgresql:// -> for_postgres with the DSN's
            # host/port/database/user, everything else stays a SQLite path.
            if env_audit_db.lower().startswith("postgresql://"):
                cfg = DatabaseConfig.for_postgres(
                    domain=domain,
                    **_pg_dsn_kwargs(env_audit_db),
                )
            else:
                cfg = DatabaseConfig.for_sqlite(domain, path=env_audit_db)

    # --- persisted settings (authoritative for interactive installs) -------
    # When the audit test-isolation seam applies (CHG-0067 contract 3), the
    # persisted provider and persisted PostgreSQL connection settings must NOT
    # be allowed to flip the resolution off the seam — so the whole persisted
    # override is skipped for that path. Every other run (seam absent, another
    # domain, or an explicit env provider) keeps today's behavior.
    db = None
    if (settings_db_path is not None or not provider_env) and not audit_seam_applies:
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

                # SettingsDatabase.get() already decodes value_type=json
                # rows to a dict (service.py persists this key as json).
                # Accept either shape: raw string (older rows) or dict.
                # FAIL LOUDLY (Phase 5/6 contract): a malformed PG config
                # row raises DatabaseConfigError — it must never silently
                # fall through and leave the SQLite default in place.
                try:
                    parsed = (
                        json.loads(pg_raw.value) if isinstance(pg_raw.value, str) else pg_raw.value
                    )
                except (TypeError, ValueError) as exc:
                    raise DatabaseConfigError(
                        "database.postgresql_config is not valid JSON: "
                        f"{exc}. Fix or remove the stored row, or switch the "
                        "provider explicitly with `nexus db-portability switch sqlite`."
                    ) from exc
                if not isinstance(parsed, dict):
                    raise DatabaseConfigError(
                        "database.postgresql_config must be a JSON object, got "
                        f"{type(parsed).__name__}."
                    )
                pg = DatabaseConfig.from_dict(parsed, domain)
                if pg.is_postgresql:
                    pg.domain = domain
                    if not pg.sqlite_path:
                        pg.sqlite_path = cfg.sqlite_path
                    cfg = pg
        except DatabaseConfigError:
            raise
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
