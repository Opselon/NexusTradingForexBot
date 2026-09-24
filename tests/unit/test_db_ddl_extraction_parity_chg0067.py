"""CHG-0067 — DDL extraction parity: PG boot must get the complete schema.

WHAT THIS PINS
==============
``migration.sqlite_ddl_statements()`` is the single list PostgreSQL boot
provisioning applies (``fabric.provision_domain`` -> ``migrate_domain`` ->
``apply_schema``). The pre-CHG-0067 implementation scanned AuditRepository
source for triple-quoted ``conn.execute`` CREATE literals only, so 35 tables +
2 indexes were provisioned where the logical schema holds 52 + 49: 47 indexes,
the 9 registry migration tables, the engine's ``schema_meta`` /
``schema_migrations``, and every runtime ``ALTER TABLE ADD COLUMN`` column were
invisible to the provisioner (two ON CONFLICT targets could not even resolve).

REFERENCE SCHEMA (deterministic, no external database files in CI)
------------------------------------------------------------------
* when the checkout carries the canonical ``artifacts/audit.db``, it is probed
  READ-ONLY (``file:...?mode=ro``; a write attempt is rejected by SQLite) and
  its ``sqlite_master`` is the source of truth;
* otherwise (a clean checkout — the CI case) the reference is rebuilt in a
  disposable temp directory by the REAL application bootstrap
  (``AuditRepository(db_url=...)``) plus the real migration engine
  (``DatabaseMigrationEngine.migrate()``, which creates the meta tables and
  applies the registry chain) — an independent code path from the extractor,
  which replays the bootstrap against an in-memory connection instead.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from nexus_scalp.database.migration import sqlite_ddl_statements, verify_domain_schema
from nexus_scalp.database.migration.pg_schema import translate_ddl

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_AUDIT_DB = REPO_ROOT / "artifacts" / "audit.db"

_CREATE_TABLE = re.compile(r"(?is)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)")
_CREATE_INDEX = re.compile(
    r"(?is)^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)"
)
_ADD_COLUMN = re.compile(
    r"(?is)^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)"
)

#: The defect classes CHG-0067 named: registry migration tables (AUDIT-0005..9)
#: and the engine meta tables the version chain cannot converge without.
REGISTRY_TABLES = frozenset(
    {
        "model_promotion_audit",
        "model_rollback_audit",
        "incidents",
        "incident_events",
        "incident_value_traces",
        "incident_quarantine",
        "release_metadata",
        "research_events_archive",
        "research_evidence_archive",
    }
)
ENGINE_META_TABLES = frozenset({"schema_meta", "schema_migrations"})

#: Both ON CONFLICT targets that could not resolve on the old boot schema.
ON_CONFLICT_TARGETS = (
    ("audit_signals", ("signal_dedup_key",), "idx_audit_signals_dedup"),
    ("audit_executions", ("order_id", "status"), "idx_executions_order_status"),
)


@dataclass(frozen=True)
class Reference:
    """The canonical audit schema: object names plus how they were read."""

    tables: frozenset[str]
    indexes: frozenset[str]
    from_live_probe: bool


def _sqlite_master_names(
    db_path: Path, exclude_internal: bool = False
) -> dict[str, frozenset[str]]:
    """Table/index names from ``sqlite_master``, opened strictly read-only.

    ``exclude_internal`` drops the objects SQLite itself generates from a
    constraint — ``sqlite_sequence`` and every ``sqlite_autoindex_*`` — those
    exist because a UNIQUE/PK was declared, not because any DDL named them, and
    the provider creates them the same way.
    """
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("CREATE TABLE _write_probe (x)")  # must raise: mode=ro
    except sqlite3.Error:
        pass
    else:  # pragma: no cover - a writable "read-only" probe is a test bug
        raise AssertionError(f"probe of {db_path} was not read-only")
    try:
        out: dict[str, frozenset[str]] = {}
        for item_kind in ("table", "index"):
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
                (item_kind,),
            ).fetchall()
            out[item_kind] = frozenset(str(row[0]) for row in rows)
        if exclude_internal:
            out["table"] = out["table"] - {"sqlite_sequence"}
            out["index"] = frozenset(
                n for n in out["index"] if not n.startswith("sqlite_autoindex_")
            )
        return out
    finally:
        conn.close()


def _replay_reference(db_path: Path) -> Reference:
    """Rebuild the canonical schema through the real bootstrap + engine."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.database.engine import DatabaseMigrationEngine

    repo = AuditRepository(db_url="sqlite:///" + db_path.resolve().as_posix())
    try:
        result = DatabaseMigrationEngine(db_path, "audit").migrate()
        assert result.get("integrity") == "ok", result
        assert "FAILED" not in str(result.get("state")), result
    finally:
        repo.close()
    names = _sqlite_master_names(db_path, exclude_internal=True)
    return Reference(tables=names["table"], indexes=names["index"], from_live_probe=False)


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> Reference:
    """The canonical audit schema: from the live DB when the checkout carries
    one, rebuilt through the real bootstrap + migration engine otherwise.

    Internal SQLite objects (``sqlite_sequence``, ``sqlite_autoindex_*``) are
    excluded: they are generated from UNIQUE/PK constraints, not declared by
    any DDL, and the provider creates them implicitly the same way."""
    if CANONICAL_AUDIT_DB.exists():
        names = _sqlite_master_names(CANONICAL_AUDIT_DB, exclude_internal=True)
        return Reference(tables=names["table"], indexes=names["index"], from_live_probe=True)
    tmp = tmp_path_factory.mktemp("chg0067_reference")
    return _replay_reference(tmp / "audit.db")


@pytest.fixture(scope="module")
def statements() -> list[str]:
    return sqlite_ddl_statements()


@pytest.fixture(scope="module")
def extracted(statements: list[str]) -> tuple[frozenset[str], frozenset[str]]:
    """(table names, index names) the extraction can actually provision."""
    tables = frozenset(m.group(1) for m in map(_CREATE_TABLE.match, statements) if m)
    indexes = frozenset(m.group(1) for s in statements if (m := _CREATE_INDEX.match(s)))
    return tables, indexes


@pytest.fixture(scope="module")
def replayed(statements: list[str]) -> Iterator[sqlite3.Connection]:
    """The extracted list applied, in order, to a fresh in-memory database."""
    conn = sqlite3.connect(":memory:")
    for raw in statements:
        conn.execute(raw)
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------


def test_every_statement_is_schema_ddl(statements: list[str]) -> None:
    """Nothing but CREATE/ALTER may reach the provisioner (seeds, PRAGMA...)."""
    assert statements, "extraction returned an empty schema"
    for raw in statements:
        assert re.match(r"(?is)^\s*(CREATE|ALTER)\b", raw), raw[:120]


def test_extraction_covers_every_reference_table(
    reference: Reference, extracted: tuple[frozenset[str], frozenset[str]]
) -> None:
    tables, _ = extracted
    missing = sorted(reference.tables - tables)
    assert not missing, f"tables in the canonical schema the extraction cannot provision: {missing}"


def test_extraction_covers_every_reference_index(
    reference: Reference, extracted: tuple[frozenset[str], frozenset[str]]
) -> None:
    _, indexes = extracted
    missing = sorted(reference.indexes - indexes)
    assert not missing, (
        f"indexes in the canonical schema the extraction cannot provision: {missing}"
    )


def test_replay_reference_and_extraction_are_identical(
    reference: Reference, extracted: tuple[frozenset[str], frozenset[str]]
) -> None:
    """Both directions, but only against the same-code replay reference: a live
    probe may legitimately hold objects from older releases, so it is checked
    subset-wise above rather than assumed identical."""
    if reference.from_live_probe:
        pytest.skip("live artifacts/audit.db present — subset coverage asserted instead")
    tables, indexes = extracted
    assert sorted(reference.tables) == sorted(tables)
    assert sorted(reference.indexes) == sorted(indexes)


def test_chg0067_defect_classes_are_present(
    statements: list[str], extracted: tuple[frozenset[str], frozenset[str]]
) -> None:
    """The four classes the old source scan dropped, pinned as floors.

    Floors (not exact counts) so a legitimate new table/index cannot fail the
    suite while a regression back to the 35-table/2-index boot schema does.
    """
    tables, indexes = extracted
    alters = [s for s in statements if re.match(r"(?is)^\s*ALTER\b", s)]
    unique_indexes = [s for s in statements if re.match(r"(?is)^\s*CREATE\s+UNIQUE\s+INDEX", s)]

    assert len(tables) >= 50, f"extraction provisioned only {len(tables)} tables"
    assert len(indexes) >= 45, f"extraction provisioned only {len(indexes)} indexes"
    assert ENGINE_META_TABLES <= tables, (
        "schema_meta/schema_migrations missing (schema_version cannot converge)"
    )
    assert REGISTRY_TABLES <= tables, sorted(REGISTRY_TABLES - tables)
    assert len(alters) >= 25, f"only {len(alters)} runtime ALTER columns extracted (expected >= 25)"
    for table, cols, index_name in ON_CONFLICT_TARGETS:
        matches = [s for s in unique_indexes if index_name in s]
        assert matches, f"unique index {index_name} on {table}{cols} missing from extraction"
        declared = _CREATE_INDEX.match(matches[0])
        assert declared and declared.group(1) == index_name


def test_every_statement_translates_without_error(statements: list[str]) -> None:
    """Zero translation errors across the full set (the pre-fix scan saw 35)."""
    errors: list[str] = []
    translated: list[str] = []
    for raw in statements:
        try:
            translated.append(translate_ddl(raw))
        except Exception as exc:
            errors.append(f"{' '.join(raw.split())[:80]} -> {type(exc).__name__}: {exc}")
    assert not errors, errors
    assert len(translated) == len(statements)

    # Dialect rewrites the newly-covered statements need to execute on PG.
    for out in translated:
        assert not re.search(r"(?i)\bdatetime\s*\(", out), out[:120]
    for out in translated:
        if re.match(r"(?is)^\s*ALTER\b", out):
            assert re.search(r"(?i)ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS", out), out[:120]
    assert not any(re.match(r"(?is)^\s*CREATE\s+VIRTUAL\s+TABLE", s) for s in statements)


def test_extracted_statements_replay_in_order(
    statements: list[str],
    extracted: tuple[frozenset[str], frozenset[str]],
    replayed: sqlite3.Connection,
) -> None:
    """The list is executable as-is: ordering, idempotency and objects agree.

    A statement referencing a table/index created later (or dropped by the
    validation pass) would surface here as an sqlite3.Error.
    """
    tables, indexes = extracted
    built_tables = frozenset(
        str(row[0])
        for row in replayed.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    built_indexes = frozenset(
        str(row[0])
        for row in replayed.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    )
    assert built_tables == tables
    assert built_indexes == indexes

    # Idempotency: every CREATE carries IF NOT EXISTS (so re-provisioning an
    # existing schema is a no-op) and re-executes cleanly. SQLite itself has no
    # ADD COLUMN IF NOT EXISTS — the translator adds that guard for PG, and the
    # translation test asserts it.
    for raw in statements:
        if not re.match(r"(?is)^\s*CREATE\b", raw):
            continue
        assert re.search(r"(?i)IF\s+NOT\s+EXISTS", raw), raw[:120]
        replayed.execute(raw)


def test_on_conflict_targets_resolve(replayed: sqlite3.Connection, statements: list[str]) -> None:
    """Both producers' ON CONFLICT targets resolve on the provisioned schema."""
    from nexus_scalp.adapters.database.audit_repository import _unique_target_resolves

    translated = [translate_ddl(s) for s in statements]
    for table, cols, index_name in ON_CONFLICT_TARGETS:
        assert _unique_target_resolves(replayed, table, cols), f"{table}{cols} unresolved"
        assert any(
            re.match(rf"(?is)^\s*CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+{index_name}\b", t)
            for t in translated
        ), f"{index_name} does not survive translation"


def test_verify_domain_schema_expects_the_full_schema(
    extracted: tuple[frozenset[str], frozenset[str]],
) -> None:
    """The read-only reconciliation reports the same set provisioning applies."""
    tables, _ = extracted
    report = verify_domain_schema("audit", lambda: [])
    assert set(report["expected_tables"]) == set(tables)
    assert report["expected_count"] == len(tables)
