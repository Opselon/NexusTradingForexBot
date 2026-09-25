"""Dedicated News database (PHASE 12) — facade over cohesive mixins.

A SEPARATE SQLite database (``artifacts/news.db``) so news rows, article
bodies, analysis payloads and AI traces NEVER mix with the core trading
ledger tables. Trading/accounting data survives complete News subsystem
deletion.

Modularization (Agent-5, CHG-0032-A1 program): the public identity of the
news store is unchanged — ``NewsDatabase`` with the same methods, SQL and
constructor surface — while method clusters live in cohesive,
verbatim-extracted siblings:

    db_schema.py    DDL + idempotent schema init (SchemaMixin)
    db_articles.py  sources/articles/versions/dedup hashes (ArticlesMixin)
    db_analysis.py  analysis/impacts/consensus/AI/trade-links (AnalysisMixin)
    db_queries.py   read/ops surface + connection close (QueriesMixin)

Connection ownership stays single-source: ``_connect``/``_now``/``__init__``
live HERE; the mixins are plain stateless method carriers (no __init__, no
extra state). Existing imports keep working unchanged:

    from nexus_scalp.news.database import NewsDatabase

Schema design principles (unchanged):
    * normalised but practical (13 tables, no blind table creation),
    * deterministic article identity (article_hash UNIQUE) for dedup,
    * append-only versioning (news_article_versions),
    * analysis/impact/consensus history (news_analysis, news_impacts,
      news_consensus, news_analysis_runs),
    * trade linkage (news_trade_links),
    * worker state + health (news_worker_state, news_health),
    * rebuildability: derived state is recomputable from raw articles.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.database.config import DatabaseConfig, load_database_config
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.drivers.proxy import PortableConnection
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.database")


# --- schema DDL single source lives in db_schema.py; re-exported here as a
# documented compatibility surface (name-stable: _SCHEMA_SQL/_INDEX_SQL) ---
# --- extracted cluster mixins: verbatim method carriers (single source) ---
from nexus_scalp.news.db_analysis import AnalysisMixin  # noqa: E402
from nexus_scalp.news.db_articles import ArticlesMixin  # noqa: E402
from nexus_scalp.news.db_queries import QueriesMixin  # noqa: E402
from nexus_scalp.news.db_schema import (  # noqa: E402
    _INDEX_SQL,  # noqa: F401 — documented compatibility re-export
    _SCHEMA_SQL,  # noqa: F401 — documented compatibility re-export
    SchemaMixin,
)


class _NewsDatabaseCore:
    """Connection + identity core: __init__, _connect, _now (verbatim)."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        config: DatabaseConfig | None = None,
    ) -> None:
        """Provider-aware news store (DATABASE PORTABILITY).

        `db_path` remains for backward compatibility (SQLite).  `config`
        selects the provider explicitly (PostgreSQL support).  The store talks
        to the database through the portable connection proxy so all existing
        SQL call sites run unchanged on both providers.
        """
        self.db_path = Path(db_path) if db_path else None
        if config is not None:
            self._config = config
        elif self.db_path is not None:
            self._config = DatabaseConfig.for_sqlite("news", path=str(self.db_path))
        else:
            self._config = load_database_config("news")
            self.db_path = (
                Path(self._config.sqlite_connect_path) if self._config.is_sqlite else None
            )
        self._driver = get_driver(self._config)
        if self._config.is_sqlite and self.db_path is not None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # SchemaMixin (extracted cluster) provides initialize_schema at runtime.
        self.initialize_schema()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _connect(self, timeout: float = 5.0) -> Any:
        """Portable connection (SQLite native; PostgreSQL proxied)."""
        if self._config.is_sqlite:
            conn = self._driver.connect(timeout=timeout)
            conn.row_factory = sqlite3.Row
            return conn
        return PortableConnection(self._driver, timeout=timeout)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------


class NewsDatabase(
    _NewsDatabaseCore,
    SchemaMixin,
    ArticlesMixin,
    AnalysisMixin,
    QueriesMixin,
):
    """Dedicated SQLite persistence for the News Intelligence subsystem.

    The trading ``AuditRepository`` is intentionally NOT used: news must be
    independently initialised, backed up, migrated, queried and deleted
    without touching trading history.
    """


__all__ = ["NewsDatabase"]
