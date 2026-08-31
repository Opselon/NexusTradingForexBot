"""Database provider registry — the single authority on which relational
database the application is running against.

Portability contract (DATABASE PORTABILITY mission, 2026-08-20):

  * Domain/business logic MUST NOT branch on provider. Selection happens at
    the infrastructure/configuration boundary (app bootstrap, CLI, UI).
  * Every persistence consumer (AuditRepository, NewsDatabase,
    CandleIntelligenceStore, DatabaseMigrationEngine, health service)
    resolves the active provider through this registry and constructs the
    matching driver from :mod:`nexus_scalp.database.drivers`.
  * ``SQLite`` is the default and must keep working with zero configuration
    (local/small-dataset mode). ``PostgreSQL`` is the scalable/large-dataset
    /production mode.

The active provider is persisted in the SettingsDatabase under
``database.provider`` (see ``nexus_scalp.settings.service``).  The registry
never reads configuration directly — callers pass the resolved provider, so
tests and CLI tools can pin the provider explicitly.
"""

from __future__ import annotations

from enum import StrEnum


class DatabaseProvider(StrEnum):
    """Relational database providers supported by the persistence layer."""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"

    @property
    def is_sqlite(self) -> bool:
        return self is DatabaseProvider.SQLITE

    @property
    def is_postgresql(self) -> bool:
        return self is DatabaseProvider.POSTGRESQL

    @classmethod
    def parse(cls, raw: str | None) -> DatabaseProvider:
        """Parse a user/config value into a provider.

        Accepts the canonical names ``sqlite`` / ``postgresql`` plus the
        common spellings used in URLs and config files
        (``postgres``, ``pgsql``, ``sqlite3``, ``SQLite``, ...).  Unknown or
        empty values resolve to SQLite (the safe default) — never raise at
        the configuration boundary.
        """
        if not raw:
            return cls.SQLITE
        norm = raw.strip().lower().replace("-", "").replace("_", "")
        if norm in {"postgresql", "postgres", "pgsql"}:
            return cls.POSTGRESQL
        if norm in {"sqlite", "sqlite3"}:
            return cls.SQLITE
        return cls.SQLITE

    @classmethod
    def from_url(cls, url: str) -> DatabaseProvider:
        """Detect the provider from a SQLAlchemy-style URL prefix."""
        if not url:
            return cls.SQLITE
        prefix = url.split("://", 1)[0].lower()
        return cls.parse(prefix)


#: Default database file names per persistence domain (artifacts/*.db).
DEFAULT_DB_FILES: dict[str, str] = {
    "audit": "audit.db",
    "news": "news.db",
    "candle_intel": "candle_intel.db",
    # Isolated strategy research DB (2026-08-20): generated-strategy memory
    # lives in its own file, never in the audit DB.
    "strategies": "strategies.db",
}


def default_sqlite_path(domain: str, workspace: str | None = None) -> str:
    """Canonical SQLite file path for a persistence domain.

    BUG-149: when no explicit workspace is given the path is anchored to the
    canonical artifacts directory (release.paths.get_artifacts_dir), which is
    the exe bundle for frozen runs and the repo root for source runs — never
    the raw process CWD (a packaged EXE launched from an arbitrary directory
    previously created a SECOND artifacts tree in that directory).
    """
    import os

    if workspace is not None:
        ws = workspace
    else:
        from nexus_scalp.release.paths import get_artifacts_dir

        ws = str(get_artifacts_dir().parent)
    return os.path.join(ws, "artifacts", DEFAULT_DB_FILES.get(domain, f"{domain}.db"))


def url_for_provider(provider: DatabaseProvider, domain: str, workspace: str | None = None) -> str:
    """Default connection URL for a provider + domain.

    SQLite:      ``sqlite:///<workspace>/artifacts/<domain>.db``
    PostgreSQL:  ``postgresql://<user>:<secret-ref>@<host>:<port>/<db>`` —
                 the placeholder form resolved by
                 :func:`nexus_scalp.database.config.build_postgres_url`.
    """
    if provider.is_sqlite:
        return f"sqlite:///{default_sqlite_path(domain, workspace)}"
    # Placeholder shape; the real URL is assembled by DatabaseConfig using
    # the secret store for the password.
    return "postgresql://nse_user:__NSE_PG_SECRET__@localhost:5432/nse_audit"
