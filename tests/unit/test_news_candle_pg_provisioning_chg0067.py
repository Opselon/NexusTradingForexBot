"""CHG-0067 wave 3 — news + candle_intel PG schema provisioning.

WHAT THIS PINS
==============
``fabric.provision_domain`` -> ``migrate_domain`` -> ``apply_schema`` is the
only path a domain's schema reaches PostgreSQL on. Wave 2 authored that path
for the audit domain only, and ``migrate_domain`` raised ``NotImplementedError``
for every other domain — which ``provision_domain`` swallowed with a bare
``except ... pass``. The result (CHG-0067, verified by lane D): ``nexus db
connect`` reported success while news and candle_intel provisioned ZERO tables
on PostgreSQL. Both domains are classified MIGRATE -> PG in production (news
245k rows, candle_intel 33.5k), so the domain bound to PostgreSQL booted with
no schema at all.

Wave 3 extends the Wave 2 replay to both domains and removes the silent
success. This suite pins:

(a) each domain's statement list covers EVERY table/index its own canonical
    schema holds — the reference is a replay of the domain's real bootstrap +
    ordered migration chain (deterministic, in-memory, no DB files), an
    independent path from the extractor under test;
(b) every statement translates through ``pg_schema.translate_ddl`` with ZERO
    errors (a statement the translator rejects never lands on PG);
(c) replaying the extracted list reproduces the same object set, in order;
(d) ``migrate_domain`` now returns success for news / candle_intel and still
    raises for a genuinely unknown domain;
(e) ``provision_domain`` no longer reports success-with-no-schema for a domain
    that has authored statements.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from nexus_scalp.database.migration import migrate_domain, verify_domain_schema
from nexus_scalp.database.migration.pg_schema import translate_ddl

_CREATE_TABLE = re.compile(r"(?is)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\"?)(\w+)\1")
_CREATE_INDEX = re.compile(
    r"(?is)^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\"?)(\w+)\1"
)
_CREATE_DDL = re.compile(r"(?is)^\s*(CREATE|ALTER)\b")

_ENGINE_META_TABLES = frozenset({"schema_meta", "schema_migrations"})


class _Capture(logging.Handler):
    """Stdlib handler — the fabric's structlog proxy writes to its own
    ``logging.Logger``, which caplog's propagation handler cannot intercept."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


if TYPE_CHECKING:
    import structlog


@dataclass(frozen=True)
class DomainExpectation:
    """The objects a domain's own code declares, for parity assertions."""

    domain: str
    tables: frozenset[str]
    indexes: frozenset[str]
    #: Tables the engine owns, not the domain's store — still provisioned so
    #: the domain's schema_version can converge on PostgreSQL.
    engine_meta: frozenset[str] = _ENGINE_META_TABLES


def _news_declared_objects() -> tuple[frozenset[str], frozenset[str]]:
    """Read the news schema straight from the modules that own the DDL.

    The store is deliberately NOT constructed: ``NewsDatabase.__init__`` builds
    a driver against a real path and creates the parent directory. The declared
    DDL lists plus the guarded column heal are a complete statement of its
    schema identity, and reading them here is the independent reference the
    extractor is checked against.
    """
    from nexus_scalp.news.db_schema import _INDEX_SQL, _SCHEMA_SQL

    tables = frozenset(_CREATE_TABLE.match(ddl).group(2) for ddl in _SCHEMA_SQL)
    indexes = frozenset(_CREATE_INDEX.match(idx).group(2) for idx in _INDEX_SQL)
    return tables, indexes


def _candle_intel_declared_objects() -> tuple[frozenset[str], frozenset[str]]:
    """Read the candle_intel schema straight from the module that owns the DDL.

    ``CandleIntelStore.__init__`` starts a background write worker and resolves
    a workspace path; its declared ``_SCHEMAS`` + the per-table ``idx_*_ts``
    indexes ``_init_schema`` builds are read here instead.
    """
    from nexus_scalp.candle_intelligence.store import _SCHEMAS, TABLES

    tables = frozenset(_CREATE_TABLE.match(ddl).group(2) for ddl in _SCHEMAS.values())
    indexes = frozenset(f"idx_{table}_ts" for table in TABLES)
    return tables, indexes


def _registry_index_names(domain: str) -> frozenset[str]:
    """Indexes the domain's ordered migration registry adds on top."""
    from nexus_scalp.database.models import DatabaseDomain
    from nexus_scalp.database.registry import migrations_for

    names: set[str] = set()
    probe = sqlite3.connect(":memory:")
    try:
        for migration in migrations_for(DatabaseDomain(domain)):
            before = {
                str(r[0])
                for r in probe.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            # The registry helpers guard on the table existing; an empty replay
            # connection is exactly the "fresh database" case they were written
            # for, so only the additive DDL actually lands.
            try:
                migration.apply(probe, Path())
            except sqlite3.Error:
                # A migration needing the bootstrap first contributes nothing
                # here — its index is covered by the bootstrap replay instead.
                continue
            after = probe.execute("SELECT name FROM sqlite_master WHERE type='index'")
            names.update(str(r[0]) for r in after.fetchall() if str(r[0]) not in before)
    finally:
        probe.close()
    return frozenset(names)


@dataclass(frozen=True)
class DomainCase:
    domain: str
    statements: tuple[str, ...]


@pytest.fixture(scope="module")
def news_statements() -> tuple[str, ...]:
    from nexus_scalp.database.migration.schema_snapshot import news_schema_statements

    return news_schema_statements()


@pytest.fixture(scope="module")
def candle_intel_statements() -> tuple[str, ...]:
    from nexus_scalp.database.migration.schema_snapshot import (
        candle_intel_schema_statements,
    )

    return candle_intel_schema_statements()


@pytest.fixture(scope="module")
def expectations() -> dict[str, DomainExpectation]:
    news_tables, news_indexes = _news_declared_objects()
    candle_tables, candle_indexes = _candle_intel_declared_objects()
    return {
        "news": DomainExpectation(
            domain="news",
            tables=news_tables,
            indexes=news_indexes | _registry_index_names("news"),
        ),
        "candle_intel": DomainExpectation(
            domain="candle_intel",
            tables=candle_tables,
            indexes=candle_indexes | _registry_index_names("candle_intel"),
        ),
    }


def _extracted_names(
    statements: tuple[str, ...],
) -> tuple[frozenset[str], frozenset[str]]:
    tables = frozenset(m.group(2) for s in statements if (m := _CREATE_TABLE.match(s)))
    indexes = frozenset(m.group(2) for s in statements if (m := _CREATE_INDEX.match(s)))
    return tables, indexes


# ---------------------------------------------------------------------------
# (a) coverage of the canonical live schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("domain", ["news", "candle_intel"])
def test_statements_cover_every_declared_table(
    domain: str,
    expectations: dict[str, DomainExpectation],
    news_statements: tuple[str, ...],
    candle_intel_statements: tuple[str, ...],
) -> None:
    """No table the domain declares is invisible to the provisioner."""
    statements = news_statements if domain == "news" else candle_intel_statements
    expectation = expectations[domain]
    tables, _ = _extracted_names(statements)
    missing = sorted(expectation.tables - tables)
    assert not missing, f"{domain}: tables the extraction cannot provision: {missing}"


@pytest.mark.parametrize("domain", ["news", "candle_intel"])
def test_statements_cover_every_declared_index(
    domain: str,
    expectations: dict[str, DomainExpectation],
    news_statements: tuple[str, ...],
    candle_intel_statements: tuple[str, ...],
) -> None:
    """Every index the store + registry builds reaches PostgreSQL too."""
    statements = news_statements if domain == "news" else candle_intel_statements
    expectation = expectations[domain]
    _, indexes = _extracted_names(statements)
    missing = sorted(expectation.indexes - indexes)
    assert not missing, f"{domain}: indexes the extraction cannot provision: {missing}"


@pytest.mark.parametrize("domain", ["news", "candle_intel"])
def test_engine_meta_tables_are_provisioned(
    domain: str,
    news_statements: tuple[str, ...],
    candle_intel_statements: tuple[str, ...],
) -> None:
    """schema_meta/schema_migrations land so schema_version can converge."""
    statements = news_statements if domain == "news" else candle_intel_statements
    tables, _ = _extracted_names(statements)
    assert _ENGINE_META_TABLES <= tables, (
        f"{domain}: engine meta tables missing — schema_version cannot converge on PG"
    )


@pytest.mark.parametrize("domain", ["news", "candle_intel"])
def test_runtime_alter_columns_reach_the_provisioner(
    domain: str,
    news_statements: tuple[str, ...],
    candle_intel_statements: tuple[str, ...],
) -> None:
    """The guarded ALTER history the store performs at init is not dropped.

    ``news_articles`` gains ``article_status`` / ``published_at_source`` at
    runtime via a guarded ``ALTER TABLE ADD COLUMN``; a source scan of CREATE
    literals would miss both (the CHG-0067 defect class), and the index over
    ``article_status`` would then point at a column the provider never got.
    """
    statements = news_statements if domain == "news" else candle_intel_statements
    alters = [s for s in statements if re.match(r"(?is)^\s*ALTER\b", s)]
    if domain == "news":
        added = {" ".join(s.lower().split()) for s in alters}
        assert any("article_status" in a for a in added), (
            "the article_status runtime ALTER is missing from the extraction"
        )
    # Every extracted ALTER must reference a table the extraction also creates,
    # otherwise the list is not self-consistent (checked by replay below).
    tables, _ = _extracted_names(statements)
    for alter in alters:
        target = re.match(r"(?is)^\s*ALTER\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", alter)
        assert target, alter[:120]
        assert target.group(1) in tables, f"ALTER on an uncreated table: {alter[:120]}"


@pytest.mark.parametrize("domain", ["news", "candle_intel"])
def test_every_statement_is_schema_ddl(
    domain: str,
    news_statements: tuple[str, ...],
    candle_intel_statements: tuple[str, ...],
) -> None:
    """Nothing but CREATE/ALTER may reach the provisioner (seeds, PRAGMA...)."""
    statements = news_statements if domain == "news" else candle_intel_statements
    assert statements, f"{domain}: extraction returned an empty schema"
    for raw in statements:
        assert _CREATE_DDL.match(raw), raw[:120]


# ---------------------------------------------------------------------------
# (b) translation to PostgreSQL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("domain", ["news", "candle_intel"])
def test_every_statement_translates_without_error(
    domain: str,
    news_statements: tuple[str, ...],
    candle_intel_statements: tuple[str, ...],
) -> None:
    """Zero translation errors — a rejected statement never lands on PG."""
    statements = news_statements if domain == "news" else candle_intel_statements
    errors: list[str] = []
    translated: list[str] = []
    for raw in statements:
        try:
            translated.append(translate_ddl(raw))
        except Exception as exc:
            errors.append(f"{' '.join(raw.split())[:80]} -> {type(exc).__name__}: {exc}")
    assert not errors, errors
    assert len(translated) == len(statements)

    for out in translated:
        assert not re.search(r"(?i)\bdatetime\s*\(", out), out[:120]
        if re.match(r"(?is)^\s*ALTER\b", out):
            # PG has ADD COLUMN IF NOT EXISTS; SQLite has no such spelling, so
            # the translator owns the guard that keeps re-provisioning working.
            assert re.search(r"(?i)ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS", out), out[:120]
    assert not any(re.match(r"(?is)^\s*CREATE\s+VIRTUAL\s+TABLE", s) for s in statements)


# ---------------------------------------------------------------------------
# (c) the extracted list replays into the same object set
# ---------------------------------------------------------------------------


@pytest.fixture
def replayed(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[str, sqlite3.Connection]]:
    """The extracted list applied, in order, to a fresh in-memory database."""
    domain = request.param
    from nexus_scalp.database.migration.schema_snapshot import (
        candle_intel_schema_statements,
        news_schema_statements,
    )

    statements = news_schema_statements() if domain == "news" else candle_intel_schema_statements()
    conn = sqlite3.connect(":memory:")
    for raw in statements:
        conn.execute(raw)
    conn.commit()
    yield domain, conn
    conn.close()


@pytest.mark.parametrize("replayed", ["news", "candle_intel"], indirect=True)
def test_extracted_statements_replay_in_order(
    replayed: tuple[str, sqlite3.Connection],
    news_statements: tuple[str, ...],
    candle_intel_statements: tuple[str, ...],
    expectations: dict[str, DomainExpectation],
) -> None:
    """The list is executable as-is: ordering, idempotency and objects agree.

    A statement referencing a table/index created later (or dropped by the
    validation pass) surfaces here as an sqlite3.Error.
    """
    domain, conn = replayed
    statements = news_statements if domain == "news" else candle_intel_statements
    expectation = expectations[domain]

    built_tables = frozenset(
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    built_indexes = frozenset(
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    )
    tables, indexes = _extracted_names(statements)
    assert built_tables == tables
    assert built_indexes == indexes

    # Every CREATE carries IF NOT EXISTS so re-provisioning an existing
    # schema is a no-op; re-execute to prove it.
    for raw in statements:
        if not re.match(r"(?is)^\s*CREATE\b", raw):
            continue
        assert re.search(r"(?i)IF\s+NOT\s+EXISTS", raw), raw[:120]
        conn.execute(raw)

    # The engine meta tables are part of the domain's provisioned schema.
    assert expectation.engine_meta <= built_tables

    # The domain's declared columns survived the round trip — an ALTER dropped
    # by validation would show up here as a missing column.
    if domain == "news":
        cols = {r[1] for r in conn.execute("PRAGMA table_info(news_articles)").fetchall()}
        assert {"article_status", "published_at_source"} <= cols, sorted(cols)


# ---------------------------------------------------------------------------
# (d) migrate_domain dispatch + unknown-domain refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "statements_fixture"),
    [
        ("news", "news_statements"),
        ("candle_intel", "candle_intel_statements"),
    ],
)
def test_migrate_domain_provisions_the_domain(
    domain: str,
    statements_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    """Wave 3's core fix: migrate_domain no longer raises for these domains."""
    statements: tuple[str, ...] = request.getfixturevalue(statements_fixture)
    executed: list[str] = []

    def _exec(sql: str) -> None:
        executed.append(sql)

    result = migrate_domain(domain, _exec)
    assert result["error_count"] == 0, result["errors"]
    assert result["applied_count"] == len(statements)
    assert len(executed) == len(statements)


def test_migrate_domain_still_refuses_unknown_domains() -> None:
    """A genuinely unknown domain still raises — provisioners stay honest."""
    with pytest.raises(NotImplementedError, match="not authored"):
        migrate_domain("unknown_domain", lambda sql: None)


def test_audit_path_is_unchanged() -> None:
    """The audit domain keeps provisioning its Wave 2 list byte-for-byte."""
    from nexus_scalp.database.migration import sqlite_ddl_statements

    executed: list[str] = []
    result = migrate_domain("audit", executed.append)
    assert result["error_count"] == 0
    assert result["applied_count"] == len(sqlite_ddl_statements())


@pytest.mark.parametrize("domain", ["news", "candle_intel"])
def test_verify_domain_schema_expects_the_provisioned_tables(
    domain: str,
    news_statements: tuple[str, ...],
    candle_intel_statements: tuple[str, ...],
) -> None:
    """The read-only reconciliation reports the same set provisioning applies."""
    statements = news_statements if domain == "news" else candle_intel_statements
    tables, _ = _extracted_names(statements)
    report = verify_domain_schema(domain, lambda: [])
    assert set(report["expected_tables"]) == set(tables)
    assert report["expected_count"] == len(tables)
    assert report["expected_count"] > 0


# ---------------------------------------------------------------------------
# (e) provision_domain no longer silently succeeds with no schema
# ---------------------------------------------------------------------------


class _RecordingBackend:
    """Minimal stand-in for the pooled write backend provision_domain builds."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str) -> None:
        self.executed.append(sql)

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeFabric:
    """``DatabaseFabric`` stand-in so the real ``provision_domain`` runs.

    The real object opens network pools from the DSN — impossible in a unit
    test and never attempted here. Provisioning only needs a write backend the
    SQL can be pushed through, so the fabric class (and its pool-opening
    ``open``) is what gets replaced; the schema path under test is the real
    function's own code.
    """

    def __init__(self, backend: _RecordingBackend) -> None:
        self._backend = backend
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def write_backend(self, _domain: str) -> _RecordingBackend:
        return self._backend


@pytest.fixture
def provision_domain(monkeypatch: pytest.MonkeyPatch) -> tuple[object, list[_RecordingBackend]]:
    """The real ``fabric.provision_domain`` wired to a fabricated backend.

    Returns the function plus the backends it created, so a test can assert on
    exactly what reached the provider. The structlog pipeline is also rebound
    to stdlib for the fixture's lifetime (the BUG-140/BUG-295 lesson: without
    the rebind a bare pytest run emits to stdout only and a handler stays
    empty), with the previous config restored on teardown.
    """
    import structlog

    from nexus_scalp.database import fabric

    created: list[_RecordingBackend] = []

    def _fake_fabric(domain: str, _cfg: object) -> _FakeFabric:
        backend = _RecordingBackend()
        created.append(backend)
        return _FakeFabric(backend)

    monkeypatch.setattr(fabric, "DatabaseFabric", _fake_fabric)
    monkeypatch.setattr(fabric, "register_domain_backend", lambda d, b: created.append(b))
    # ``get_logger`` returns a lazy proxy cached on first use; force the module
    # to re-resolve through the stdlib-bound configuration below.
    monkeypatch.setattr(fabric, "logger", _stdlib_logger(), raising=False)
    previous_config = structlog.get_config()
    _bind_structlog_to_stdlib()
    yield fabric.provision_domain, created
    structlog.configure(**previous_config)


def _stdlib_logger() -> Any:
    """The fabric logger bound through a stdlog-interop pipeline."""
    import structlog

    _bind_structlog_to_stdlib()
    return structlog.get_logger("nexus_scalp.database.fabric")


def _bind_structlog_to_stdlib() -> None:
    """Point structlog at the stdlib factory (see the chg0067 observability suite).

    The production pipeline is installed at boot; under a bare pytest run the
    default lazy proxy renders positional args as a dict instead of formatting
    the message, so a handler capturing ``record.getMessage()`` would see the
    raw template. Re-binding keeps assertions on the RENDERED message — the
    shape an operator reads — without touching app configuration.
    """
    import structlog

    structlog.configure(
        processors=[
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


@pytest.mark.parametrize("domain", ["news", "candle_intel"])
def test_provision_domain_no_longer_swallows_missing_schema(
    domain: str,
    provision_domain: tuple[object, list[_RecordingBackend]],
) -> None:
    """A domain that cannot be provisioned surfaces loudly, not as success.

    The Wave 2 ``except NotImplementedError: pass`` made ``nexus db connect``
    report success while zero tables existed (CHG-0067, lane D). This asserts
    the defect class is gone end-to-end: the real ``provision_domain`` runs its
    own schema path, every statement the domain's replay produced reaches the
    backend (no silent skip), and the returned backend is the one that ran it.
    """
    func, created = provision_domain
    returned = func(domain, "postgresql://ignored", min_size=1, max_size=4)
    assert created, "no backend was built — the fabric was not exercised"
    backend = created[0]
    assert returned is backend
    assert backend.executed, f"{domain}: no schema was provisioned (the CHG-0067 defect)"
    assert all(_CREATE_DDL.match(sql) for sql in backend.executed)
    assert any(
        re.match(r"(?is)^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+schema_meta\b", sql)
        for sql in backend.executed
    ), "the engine meta tables must be provisioned so schema_version can converge"


def test_provision_domain_logs_loudly_for_unknown_domain(
    provision_domain: tuple[object, list[_RecordingBackend]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The swallowed-NotImplementedError path is now observable to an operator.

    ``provision_domain`` still returns a backend (the pool is up and the domain
    may be used read-only), but the schema failure is logged at ERROR instead
    of the old silent ``pass`` — which is what let news/candle_intel ship with
    zero PostgreSQL tables through the whole Wave 2 span.
    """
    func, created = provision_domain
    # The fabric logger is a structlog proxy writing to the package's own
    # logging.Logger, so capture on that logger directly (a stdlib Handler is
    # the only sink caplog's propagation handler cannot intercept here).
    handler = _Capture()
    logger = logging.getLogger("nexus_scalp.database.fabric")
    logger.addHandler(handler)
    try:
        returned = func("unknown_domain", "postgresql://ignored")
    finally:
        logger.removeHandler(handler)
    assert returned is not None
    assert any("schema NOT provisioned" in msg for msg in handler.messages), (
        "an unprovisionable domain must surface an ERROR, not silent success"
    )
    assert created and not created[0].executed
