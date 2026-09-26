"""Reconstruct the complete audit-domain logical schema by replay (CHG-0067).

WHY A REPLAY, AND NOT A SOURCE SCAN
===================================
The pre-CHG-0067 extractor read ``AuditRepository``'s source and kept only the
triple-quoted ``conn.execute`` CREATE literals. Anything authored anywhere else
was silently dropped, so PostgreSQL boot provisioned 35 tables + 2 indexes out
of the real 52 + 49:

* 47 of the 49 indexes — those issued through helpers (``_ensure_index``),
  through ``for ... in (...)`` tuples, through sibling bootstrap modules, or
  through the migration registry, never reach the provisioner;
* the 9 tables the audit migration registry creates (AUDIT-0005..0009);
* the engine's own ``schema_meta`` / ``schema_migrations`` tables — without
  which ``schema_version`` can never converge on the provider;
* 27 columns added at runtime by ``ALTER TABLE ADD COLUMN`` (present in every
  live schema, absent from every ``CREATE TABLE`` literal);
* the bootstrap tables owned by sibling modules (``audit_dead_letter``, the
  ``audit_broker_*`` family, ``audit_paper_executions``).

THE REPLAY
==========
A disposable in-memory SQLite connection records every DDL statement the real
bootstrap executes (a trace callback), then the ordered migration registry and
the engine meta tables are applied on top. What comes out is the schema a fresh
SQLite database would hold — tables, indexes (partial, DESC, unique), the
runtime ``ALTER`` history and the meta tables — in execution order and in the
original ``IF NOT EXISTS`` spelling, so the list stays idempotent when
PostgreSQL re-applies it.

Every captured statement is validated against the replayed schema before it is
returned (the application guards some DDL with ``contextlib.suppress``, so a
statement can be executed without landing), and the whole thing runs in
memory: no canonical database file is ever opened, let alone written.

WAVE 3 generalizes the replay past audit: news and candle_intel had no authored
provisioning path at all, so ``provision_domain`` swallowed the
``NotImplementedError`` from ``migrate_domain`` and reported success while the
domain had zero tables on PostgreSQL. Each domain now gets the same treatment
(see ``replay_schema``), replaying its own declared bootstrap DDL — read from
the schema modules that own it, never by constructing the store, which touches
the filesystem and starts workers — plus its ordered migration registry and the
engine meta tables.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from nexus_scalp.database.models import DatabaseDomain
from nexus_scalp.database.registry import migrations_for

logger = logging.getLogger(__name__)

#: Statements that define a logical schema object. Everything else the replay
#: executes (PRAGMA, seed INSERTs, ANALYZE, SELECT guards) is not schema.
_DDL_STATEMENT = re.compile(r"(?is)^\s*(?:CREATE\s+(?:TABLE|UNIQUE\s+INDEX|INDEX)|ALTER\s+TABLE)\b")
_CREATE_TABLE = re.compile(r'(?is)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)("?)(\w+)\1')
_CREATE_INDEX = re.compile(
    r'(?is)^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)("?)(\w+)\1'
)
_ADD_COLUMN = re.compile(r'(?is)^\s*ALTER\s+TABLE\s+("?)(\w+)\1\s+ADD\s+COLUMN\s+("?)(\w+)\3')


def _open_replay() -> tuple[sqlite3.Connection, list[str]]:
    """A disposable in-memory connection plus the DDL statements it executes."""
    captured: list[str] = []
    conn = sqlite3.connect(":memory:")

    def _record(statement: str) -> None:
        sql = statement.strip()
        if _DDL_STATEMENT.match(sql):
            captured.append(sql)

    conn.set_trace_callback(_record)
    return conn, captured


_IF_NOT_EXISTS_GUARD = re.compile(
    r'(?is)^(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX))\s+((?!IF\s+NOT\s+EXISTS)("?[\w]+"?))'
)


def _restore_if_not_exists(statement: str) -> str:
    """Undo SQLite's catalog normalization of ``CREATE ... IF NOT EXISTS``.

    ``sqlite_master.sql`` drops the ``IF NOT EXISTS`` clause from a statement
    it stores (the table exists by construction, so the clause is moot to
    SQLite). The replay hands the provisioner the *original* spelling because
    that is the idempotent form every other captured statement uses; without
    the guard, a PostgreSQL re-provisioning run would fail on the tables the
    previous run already created.
    """
    return _IF_NOT_EXISTS_GUARD.sub(r"\1 IF NOT EXISTS \2", statement)


def _apply_audit_bootstrap(conn: sqlite3.Connection) -> None:
    """Run the DDL path an ``AuditRepository`` construction executes.

    ``object.__new__`` deliberately skips ``__init__``: construction starts a
    background write worker and touches the filesystem, neither of which a
    schema snapshot needs. The ``_create_*`` family only reads class attributes
    and the dead-letter store, so the half-built instance runs it unchanged —
    including the ``_add_column_if_missing`` history and the sibling bootstrap
    modules (broker history, paper executions, research/factory context heals,
    unique-constraint heal) it delegates to.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.adapters.database.dead_letter_store import DeadLetterStore

    host = object.__new__(AuditRepository)
    host.dead_letter_store = DeadLetterStore(
        conn_factory=sqlite3.connect,
        is_sqlite=True,
        db_path="",
        write_sink=None,
    )
    AuditRepository._create_sqlite_tables(host, conn)
    _apply_learning_cycle_tables(conn)


def _apply_news_bootstrap(conn: sqlite3.Connection) -> None:
    """Run the DDL path a ``NewsDatabase`` construction executes.

    The store is deliberately NOT constructed: ``NewsDatabase.__init__`` builds
    a driver against a real path and creates the parent directory. Its schema
    identity is the declared DDL in ``news.db_schema`` plus the guarded column
    heals ``SchemaMixin.initialize_schema`` performs between the tables and the
    indexes — replayed here in the same order by calling ``SchemaMixin``'s own
    column guard, so the runtime ``ALTER TABLE ADD COLUMN`` history reaches the
    provisioner exactly as the audit domain's does.
    """
    from nexus_scalp.news.db_schema import (
        _INDEX_SQL,
        _SCHEMA_SQL,
        SchemaMixin,
    )

    for ddl in _SCHEMA_SQL:
        conn.execute(ddl)
    # The runtime column heals the schema init performs between the tables and
    # the indexes (guarded, so only the ones that have not landed execute).
    # The mixin is a stateless method carrier (its Protocol declares the
    # connection core, ``_ensure_article_status_column`` needs none of it), so
    # the unbound function is called directly rather than through an instance.
    SchemaMixin._ensure_article_status_column(  # type: ignore[abstract]
        SchemaMixin(),  # type: ignore[abstract]
        conn,
    )
    for idx in _INDEX_SQL:
        conn.execute(idx)
    # The post-event memory table is created lazily by PostEventValidator
    # rather than by the store's schema init, so its DDL never reached the
    # provisioner — a PostgreSQL box had every news table except this one and
    # the validator's writes failed on it. Its DDL lives with the validator;
    # the replay applies it here so provisioning stays the one path a domain's
    # schema reaches PostgreSQL through.
    from nexus_scalp.news.memory.post_event import _POST_EVENT_DDL

    conn.execute(_POST_EVENT_DDL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_post_event_article ON news_post_event(article_id);"
    )


def _apply_candle_intel_bootstrap(conn: sqlite3.Connection) -> None:
    """Run the DDL path a ``CandleIntelStore`` construction executes.

    The store is deliberately NOT constructed: its constructor starts a
    background write worker thread and resolves a workspace path. Its schema
    identity is the declared DDL in ``store._SCHEMAS`` plus the per-table
    ``idx_<table>_ts`` indexes ``_init_schema`` builds over ``TABLES``.
    """
    from nexus_scalp.candle_intelligence.store import _SCHEMAS, TABLES

    for ddl in _SCHEMAS.values():
        conn.execute(ddl)
    for table in TABLES:
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_ts ON {table}(ts);")


def _apply_learning_cycle_tables(conn: sqlite3.Connection) -> None:
    """Create ``learning_cycles`` / ``learning_cycle_events`` — the learning loop's tables.

    ``LearningCycleStore`` owns their DDL (it is the only module that reads and
    writes them), so the statements are imported rather than copied: two
    spellings of the cycle schema would be a drift waiting to happen. The
    store's ``ensure_schema`` opens its *own* SQLite connection, so the owner's
    DDL is harvested from the catalog of the database it just created instead
    of the replay connection — the catalog holds the exact text the owner
    issued, in the original ``IF NOT EXISTS`` spelling, and the harvest is
    therefore idempotent and never invents a third spelling.
    """
    import os
    import tempfile

    from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleStore

    # The suffix is the trap's exemption marker: the harvest needs a REAL file
    # (the store builds its schema on its own connection, so a ``:memory:``
    # database the harvest connects to separately holds zero tables), and the
    # file is created and deleted within this function. The SQLite runtime trap
    # exempts ``_schema_harvest.db`` so this transient scratch file is not
    # mistaken for operational data on a PostgreSQL box.
    fd, path = tempfile.mkstemp(suffix="_schema_harvest.db")
    os.close(fd)
    try:
        LearningCycleStore(path)
        harvest_conn = sqlite3.connect(path)
        try:
            # The objects are matched on the DDL TEXT, not the object name:
            # the indexes are named ``idx_learning_*`` (they sort *before*
            # ``learning_*`` alphabetically) while the tables are
            # ``learning_*``, so a name filter would pick up one and drop the
            # other. ``sql <> ''`` matters too — SQLite stores the catalog text
            # of an object it cannot re-create (an implicit index) as an EMPTY
            # string, not NULL, so ``IS NOT NULL`` would let it through.
            rows = harvest_conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type IN ('table', 'index') AND sql <> '' "
                "AND sql LIKE '%learning%' "
                "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name"
            ).fetchall()
        finally:
            harvest_conn.close()
    finally:
        Path(path).unlink(missing_ok=True)

    for row in rows:
        statement = _restore_if_not_exists(str(row[0]).strip())
        conn.execute(statement)


#: The bootstrap DDL path for each domain — the statements a fresh database
#: receives from the domain's own store construction, replayed verbatim.
_DOMAIN_BOOTSTRAP: dict[DatabaseDomain, Callable[[sqlite3.Connection], None]] = {
    DatabaseDomain.AUDIT: _apply_audit_bootstrap,
    DatabaseDomain.NEWS: _apply_news_bootstrap,
    DatabaseDomain.CANDLE_INTEL: _apply_candle_intel_bootstrap,
}


def _apply_domain_bootstrap(conn: sqlite3.Connection, domain: DatabaseDomain) -> None:
    """Apply one domain's bootstrap DDL path to the replay connection."""
    bootstrap = _DOMAIN_BOOTSTRAP.get(domain)
    if bootstrap is None:
        # Unreachable for registry domains (migrations_for raises first);
        # kept as a defensive anchor so a new domain cannot silently replay
        # another's schema.
        raise NotImplementedError(f"no bootstrap replay authored for domain {domain!r}")
    bootstrap(conn)


def _apply_registry(conn: sqlite3.Connection, domain: DatabaseDomain) -> None:
    """Apply a domain's ordered migration registry on top of its bootstrap.

    This is the half of the schema that lives in code the bootstrap never
    calls. No registry helper reads ``db_path`` (it is only the engine's
    reporting handle), so an empty path is honest here.
    """
    for migration in migrations_for(domain):
        migration.apply(conn, Path())


def _apply_engine_meta_tables(conn: sqlite3.Connection) -> None:
    """Create ``schema_meta`` / ``schema_migrations`` — the engine's own tables.

    The engine owns their DDL (it is what reads and writes them), so the
    statements are imported rather than copied: two spellings of the version
    chain would be a drift waiting to happen.
    """
    from nexus_scalp.database.engine import _HISTORY_TABLE_DDL, _META_TABLE_DDL

    conn.execute(_META_TABLE_DDL)
    conn.execute(_HISTORY_TABLE_DDL)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(row[1]).lower() for row in rows}


def _validated_statements(captured: list[str], conn: sqlite3.Connection) -> list[str]:
    """Keep the captured DDL that really exists in the replayed schema.

    A statement can be executed without landing — the application wraps some
    DDL in ``contextlib.suppress`` as expected control flow — and handing that
    to the provisioner would reproduce the very defect this module fixes.
    De-duplicated, execution order preserved (tables before their indexes,
    columns before the indexes over them), original ``IF NOT EXISTS`` spelling
    preserved so re-provisioning stays idempotent.
    """
    tables = {
        str(row[0]).lower()
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    indexes = {
        str(row[0]).lower()
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    columns: dict[str, set[str]] = {}
    statements: list[str] = []
    seen: set[str] = set()
    for sql in captured:
        key = " ".join(sql.lower().split())
        if key in seen:
            continue
        seen.add(key)
        present = False
        if (match := _CREATE_TABLE.match(sql)) is not None:
            present = match.group(2).lower() in tables
        elif (match := _CREATE_INDEX.match(sql)) is not None:
            present = match.group(2).lower() in indexes
        elif (match := _ADD_COLUMN.match(sql)) is not None:
            table = match.group(2).lower()
            if table in tables:
                cached = columns.get(table)
                if cached is None:
                    cached = columns.setdefault(table, _table_columns(conn, match.group(2)))
                present = match.group(4).lower() in cached
        if present:
            statements.append(sql)
        else:
            logger.warning(
                "[DB-SCHEMA] DDL did not land in the replayed schema, dropped: %s",
                " ".join(sql.split())[:120],
            )
    return statements


def replay_schema(*, domain: DatabaseDomain) -> tuple[str, ...]:
    """Replay one domain's real bootstrap + ordered migration chain.

    The generalized form of the audit replay (CHG-0067 wave 3): the domain's
    declared bootstrap DDL, its ordered migration registry, then the engine
    meta tables are applied to a disposable in-memory connection while a trace
    callback records every statement; the ones that actually landed become the
    provisioner's schema list. Deterministic, no database file involved.
    """
    conn, captured = _open_replay()
    try:
        _apply_domain_bootstrap(conn, domain)
        _apply_registry(conn, domain)
        _apply_engine_meta_tables(conn)
        statements = _validated_statements(captured, conn)
    finally:
        conn.close()
    logger.info(
        "[DB-SCHEMA] domain=%s logical schema replayed: %d statements",
        domain.value,
        len(statements),
    )
    return tuple(statements)


@lru_cache(maxsize=1)
def audit_schema_statements() -> tuple[str, ...]:
    """The complete audit-domain logical schema as SQLite DDL statements.

    Deterministic and free of any dependency on a database file: the replay is
    in-memory and discarded. Exceptions propagate on purpose — a provisioner
    that silently receives a partial schema is the defect this replaced.
    """
    return replay_schema(domain=DatabaseDomain.AUDIT)


@lru_cache(maxsize=1)
def news_schema_statements() -> tuple[str, ...]:
    """The complete news-domain logical schema as SQLite DDL statements.

    Same replay contract as the audit domain: the schema modules that own the
    DDL (``news.db_schema``) are applied to a disposable in-memory connection
    — the store object itself is deliberately NOT constructed (its constructor
    touches the filesystem and resolves configuration) — then the ordered
    ``NEWS`` registry and the engine meta tables on top.
    """
    return replay_schema(domain=DatabaseDomain.NEWS)


@lru_cache(maxsize=1)
def candle_intel_schema_statements() -> tuple[str, ...]:
    """The complete candle_intel-domain logical schema as SQLite DDL statements.

    Same replay contract: ``CandleIntelStore``'s declared schema (``_SCHEMAS``
    + the per-table ``idx_*_ts`` indexes its ``_init_schema`` builds) is applied
    to a disposable in-memory connection — the store is deliberately NOT
    constructed (its constructor starts a background write worker and touches
    the filesystem) — then the ordered ``CANDLE_INTEL`` registry and the engine
    meta tables on top.
    """
    return replay_schema(domain=DatabaseDomain.CANDLE_INTEL)


def ai_provider_decisions_schema_statements() -> tuple[str, ...]:
    """The complete AI-provider-decision domain schema as SQLite DDL statements.

    Unlike the replay domains this one has no ordered migration registry: the
    schema is authored directly in ``ai_providers.store._SCHEMA`` (SQLite
    dialect), so the statements are read from it verbatim and the store is
    deliberately NOT constructed (its constructor opens a real database file).
    """
    from nexus_scalp.ai_providers.store import _SCHEMA

    return tuple(s.strip() for s in _SCHEMA.split(";") if s.strip())


def model_lifecycle_schema_statements() -> tuple[str, ...]:
    """The model_lifecycle-owned operational tables as SQLite DDL statements.

    Those tables (``learning_cycles`` / ``learning_cycle_events`` /
    ``training_runs`` / ``model_comparisons``) live in the AUDIT domain's
    database but were not part of its authored DDL, so a fresh PostgreSQL
    install had no ``learning_cycles`` while the migrated nexusdb did (the
    table-set divergence recorded in tests/unit/test_pg_schema_convergence.py).
    The DDL is authored in the owning package (``model_lifecycle.schema``)
    because those tables are the package's contract, exactly as
    ``ai_providers.store._SCHEMA`` owns the decision ledger's DDL above.
    """
    from nexus_scalp.model_lifecycle.schema import model_lifecycle_schema_statements

    return model_lifecycle_schema_statements()


def strategy_factory_schema_statements() -> tuple[str, ...]:
    """The complete strategy-factory domain schema as SQLite DDL statements.

    Same contract as ``ai_provider_decisions_schema_statements``: the schema is
    authored directly in ``strategies.factory.store._SCHEMA`` (SQLite dialect,
    mirrored by the audit bootstrap for PostgreSQL), so the statements are read
    from it verbatim and the store is deliberately NOT constructed (its
    constructor opens a real database file).
    """
    from nexus_scalp.strategies.factory.store import _SCHEMA

    return tuple(s.strip() for s in _SCHEMA.split(";") if s.strip())


def ops_shadow_schema_statements() -> tuple[str, ...]:
    """The shadow / shadow70 / governance tables as SQLite DDL statements.

    Same ownership contract as ``model_lifecycle_schema_statements``: the DDL is
    authored in the owning package (``nexus_scalp.shadow.schema``) because those
    tables are the stores' own ``ensure_schema`` output, not an audit-domain
    replay. The registry resolves the extractor by name against this module, so
    the package-owned function is re-exported here.
    """
    from nexus_scalp.shadow.schema import ops_shadow_schema_statements

    return ops_shadow_schema_statements()


def ops_hygiene_schema_statements() -> tuple[str, ...]:
    """The hygiene-state / quarantine tables as SQLite DDL statements.

    Same contract as ``ops_shadow_schema_statements``: the DDL is authored in
    ``nexus_scalp.hygiene.schema`` and re-exported so the registry's name-based
    resolution finds it.
    """
    from nexus_scalp.hygiene.schema import ops_hygiene_schema_statements

    return ops_hygiene_schema_statements()


# ---------------------------------------------------------------------------
# Lane D (2026-09-26): the four isolated-store domains.
#
# Each extractor reads the DDL the owning store declares, splits it into
# statements, and appends the store's own index statements — the exact DDL a
# fresh SQLite database would receive from the store's ``ensure_schema``. The
# store is deliberately NOT constructed: its constructor opens a real database
# file (and, for candle_intel, starts a background worker).
# ---------------------------------------------------------------------------


def marketplace_schema_statements() -> tuple[str, ...]:
    """The marketplace domain's tables + indexes (SQLite dialect).

    ``MarketplaceStore.ensure_schema`` applies ``ALL_DDL`` then the ``INDEXES``
    tuples; both are read from the store module verbatim so a PostgreSQL
    provision converges on the store's own physical schema.
    """
    from nexus_scalp.marketplace.store import ALL_DDL, INDEXES

    out: list[str] = []
    for _table, ddl in ALL_DDL:
        out.extend(s.strip() for s in ddl.split(";") if s.strip())
    for idx_name, table, cols in INDEXES:
        out.append(f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON {table} ({cols})')
    return tuple(out)


def models_schema_statements() -> tuple[str, ...]:
    """The model registry's tables + indexes (SQLite dialect).

    ``ModelRegistry._ensure_schema`` creates ``model_checkpoints`` /
    ``model_load_history`` and three indexes; the registry authors that DDL
    inline (no module-level schema constant exists), so the statements are
    HARVESTED from the catalog of a disposable database the registry itself
    builds on its own connection — the same harvest contract
    ``_apply_learning_cycle_tables`` uses (the store must own the connection;
    a ``:memory:`` database a second connection opens holds zero tables).
    ``AUTOINCREMENT`` and the SQLite ``INTEGER PRIMARY KEY`` identity are
    translated by ``pg_schema.translate_ddl`` on the provider.
    """
    import os
    import sqlite3
    import tempfile

    from nexus_scalp.model_generation.model_registry import ModelRegistry

    # The SQLite runtime trap exempts ``_schema_harvest.db`` so this transient
    # scratch file is not mistaken for operational data on a PostgreSQL box.
    fd, path = tempfile.mkstemp(suffix="_schema_harvest.db")
    os.close(fd)
    try:
        ModelRegistry(path)
        harvest_conn = sqlite3.connect(path)
        try:
            rows = harvest_conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type IN ('table', 'index') AND sql <> '' "
                "AND name LIKE 'model_%' "
                "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name"
            ).fetchall()
        finally:
            harvest_conn.close()
    finally:
        Path(path).unlink(missing_ok=True)

    return tuple(_restore_if_not_exists(str(r[0]).strip()) for r in rows)


def strategies_schema_statements() -> tuple[str, ...]:
    """The strategies domain's factory tables + indexes (SQLite dialect).

    ``StrategyResearchStore.ensure_schema`` applies ``ALL_DDL`` then the
    ``INDEXES`` tuples; both are read from the store module verbatim.
    """
    from nexus_scalp.strategies.research_store import ALL_DDL, INDEXES

    out: list[str] = []
    for _table, ddl in ALL_DDL:
        out.extend(s.strip() for s in ddl.split(";") if s.strip())
    for idx_name, table, cols in INDEXES:
        out.append(f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON {table} ({cols})')
    return tuple(out)


def experiments_schema_statements() -> tuple[str, ...]:
    """The experiments domain's table + indexes (SQLite dialect).

    ``ExperimentRegistry.__init__`` applies ``_SCHEMA`` (two ``CREATE INDEX``
    statements follow the table in the same string); read verbatim.
    """
    from nexus_scalp.model_lab.experiment_registry import _SCHEMA

    return tuple(s.strip() for s in _SCHEMA.split(";") if s.strip())
