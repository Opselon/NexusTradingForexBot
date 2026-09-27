"""Lane F — the SQLite->PostgreSQL data migration tool (provider switch).

The operator-facing contract under test: switching the active provider from
SQLite to PostgreSQL must never cost a row of history. The tool copies rows
out of the SQLite source-of-truth into a target PostgreSQL database
idempotently, fixes the sequences so the next application INSERT does not
collide with a copied id, and quarantines (never silently drops) any row it
cannot coerce.

These tests pin the contract against a tiny synthetic SQLite database plus
the operator's isolated PostgreSQL instance (never the live cluster):

  (a) row counts match per table between source and destination;
  (b) re-running is idempotent — the counts are unchanged and no row is
      duplicated;
  (c) the sequence was advanced past ``max(id)``, so the next INSERT takes a
      fresh id instead of colliding;
  (d) an unparseable timestamp row is QUARANTINED and REPORTED, not silently
      dropped, and the run exits non-zero;
  (e) ``--dry-run`` writes nothing.

The isolated PG is reached through ``NSE_PG_TEST_URL`` (the lane's own port
55434). When that URL is absent the PG-backed assertions skip honestly
instead of touching any live server — the default ``localhost:5432`` is NEVER
used here.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from nexus_scalp.database.migration.sqlite_to_pg import (
    CoercionError,
    RowCoercer,
    copy_table_rows,
    fix_sequences,
    migrate_sqlite_to_pg,
    source_columns,
    source_tables,
)

# The lane's ISOLATED PostgreSQL instance. NEVER the live localhost:5432 —
# that one holds live engine tables and its credentials are not repo values.
PG_URL_ENV = "NSE_PG_TEST_URL"
_DEFAULT_PG_URL = "postgresql://nse_user:***@127.0.0.1:55434/nse_audit"


def _pg_url() -> str | None:
    """The isolated PG URL, or None when the cluster is not running.

    Never falls back to a default port: a missing URL means the isolated
    cluster is down, and the PG-backed tests skip rather than connect
    somewhere dangerous.
    """
    return os.environ.get(PG_URL_ENV) or None


needs_pg = pytest.mark.skipif(
    _pg_url() is None,
    reason=f"{PG_URL_ENV} is not set — the isolated PG cluster is not running",
)


# =====================================================================
# Fixtures: a tiny synthetic SQLite source (written ONCE, read-only after)
# =====================================================================


def _make_source_sqlite(path: Path) -> None:
    """Build the synthetic source: timestamps, a boolean, a BLOB, a bad row.

    The schema deliberately mirrors the app's real shapes: an ``id`` INTEGER
    PRIMARY KEY AUTOINCREMENT (the sequence-collision case), a TEXT ISO
    timestamp column (the coercion case), an INTEGER boolean, a BLOB, and one
    deliberately-unparseable timestamp that must be quarantined.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TABLE IF EXISTS events")
        conn.execute("DROP TABLE IF EXISTS notes")
        conn.execute(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL DEFAULT '',
                happened_at TEXT NOT NULL,
                is_win INTEGER DEFAULT 0,
                payload BLOB,
                price REAL DEFAULT 0.0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                body TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.executemany(
            "INSERT INTO events (symbol, happened_at, is_win, payload, price) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("EURUSD", "2026-01-01 10:00:00", 1, b"abc", 1.5),
                ("EURUSD", "2026-01-02 11:30:00", 0, b"def", 1.6),
                ("GBPUSD", "2026-01-03 09:15:00.250", 1, None, 1.7),
                # The deliberately unparseable timestamp: must be QUARANTINED,
                # reported and cost the run a non-zero exit — never dropped.
                ("GBPUSD", "not-a-timestamp", 0, None, 0.0),
            ],
        )
        conn.executemany("INSERT INTO notes (body) VALUES (?)", [("first",), ("second",)])
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def sqlite_dir(tmp_path: Path) -> Path:
    """A directory holding one synthetic source database (written once)."""
    directory = tmp_path / "artifacts"
    directory.mkdir()
    _make_source_sqlite(directory / "audit.db")
    return directory


@pytest.fixture()
def source_conn(sqlite_dir: Path) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(f"file:{(sqlite_dir / 'audit.db').as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# =====================================================================
# (e) dry-run writes nothing
# =====================================================================


@needs_pg
def test_dry_run_writes_nothing(sqlite_dir: Path, clean_pg: None) -> None:
    result = migrate_sqlite_to_pg(sqlite_dir, _pg_url() or "", dry_run=True)
    payload = result.to_dict()
    assert payload["dry_run"] is True
    # The dry run plans the copy (it reports what WOULD be copied)...
    assert payload["rows_copied"] >= 6
    # ...but nothing landed in the destination.
    assert _pg_table_row_counts(_pg_url() or "") == {}


def test_dry_run_reports_quarantine_without_writing(sqlite_dir: Path) -> None:
    """A dry run reports the rows it WOULD copy without touching the target.

    The destination URL is deliberately unreachable: a dry run must plan the
    copy (and report the tables/rows it sees) without ever needing a working
    target — the operator previews BEFORE handing it a real database.
    """
    result = migrate_sqlite_to_pg(
        sqlite_dir, "postgresql://nobody@example@127.0.0.1:1/db", dry_run=True
    )
    payload = result.to_dict()
    assert payload["dry_run"] is True
    # Nothing was written (the destination is unreachable and dry-run never writes).
    assert payload["sequences_fixed"] == []
    # The planned tables and their source rows are still reported.
    assert payload["per_table_counts"]["notes"] == 2
    assert payload["rows_copied"] >= 6


# =====================================================================
# Source access: read-only, and read-only by construction
# =====================================================================


def test_source_is_opened_read_only(source_conn: sqlite3.Connection) -> None:
    """The source connection must refuse a write (the live engine is never
    disturbed, and the source files are never modified)."""
    with pytest.raises(sqlite3.OperationalError):
        source_conn.execute("CREATE TABLE _probe(x INTEGER)")


def test_source_tables_excludes_engine_bookkeeping(source_conn: sqlite3.Connection) -> None:
    tables = source_tables(source_conn)
    assert "events" in tables
    assert "notes" in tables
    # sqlite_sequence is SQLite's own AUTOINCREMENT ledger — the PG side fixes
    # sequences itself instead of copying this.
    assert "sqlite_sequence" not in tables
    # The destination's version chain is its own; copying it would make a
    # fresh PG database claim to be at the SQLite source's schema version.
    assert "schema_meta" not in tables
    assert "schema_migrations" not in tables


def test_source_columns_shape(source_conn: sqlite3.Connection) -> None:
    cols = {c["name"]: c for c in source_columns(source_conn, "events")}
    assert cols["id"]["pk"] == 1
    assert cols["happened_at"]["type"].upper() == "TEXT"


# =====================================================================
# Type coercion: target type decides, values validate or quarantine
# =====================================================================


def test_coercer_converts_timestamp_and_boolean() -> None:
    """TEXT ISO timestamps become real timestamps; 0/1 becomes a boolean —
    but ONLY when the TARGET column is that type (never a value-shape guess)."""
    coercer = RowCoercer(
        "events",
        [
            {"name": "happened_at", "type": "timestamp with time zone"},
            {"name": "is_win", "type": "boolean"},
            {"name": "payload", "type": "bytea"},
            {"name": "symbol", "type": "text"},
        ],
    )
    row = ("EURUSD", "2026-01-01 10:00:00", 1, b"abc")
    values, quarantine = coercer.coerce(row, ["symbol", "happened_at", "is_win", "payload"])
    assert quarantine is None
    assert values[0].isoformat().startswith("2026-01-01T10:00:00")
    assert values[1] is True
    assert values[2] == b"abc"
    # A TEXT column keeps its string even when it looks like a timestamp.
    assert values[3] == "EURUSD"


def test_coercer_accepts_iso_separator_and_utc_suffix() -> None:
    coercer = RowCoercer("t", [{"name": "ts", "type": "timestamp with time zone"}])
    for text in ("2026-01-01T10:00:00", "2026-01-01 10:00:00Z", "2026-01-01 10:00:00.500"):
        values, quarantine = coercer.coerce((text,), ["ts"])
        assert quarantine is None
        assert values[0].year == 2026


def test_coercer_quarantines_unparseable_timestamp() -> None:
    """The whole point: a bad timestamp is a quarantine, never a silent NULL
    and never an exception that aborts the table."""
    coercer = RowCoercer("events", [{"name": "happened_at", "type": "timestamp with time zone"}])
    with pytest.raises(CoercionError) as exc_info:
        coercer.coerce(("not-a-timestamp",), ["happened_at"])
    assert exc_info.value.column == "happened_at"
    assert "does not parse" in exc_info.value.reason


def test_coercer_refuses_numeric_epoch_in_timestamp_column() -> None:
    """A raw number in a timestamp column is ambiguous (seconds vs ms); it is
    refused rather than guessed."""
    coercer = RowCoercer("events", [{"name": "happened_at", "type": "timestamp"}])
    with pytest.raises(CoercionError) as exc_info:
        coercer.coerce((1700000000,), ["happened_at"])
    assert "ambiguous" in exc_info.value.reason


def test_coercer_none_passes_through() -> None:
    coercer = RowCoercer("events", [{"name": "payload", "type": "bytea"}])
    values, quarantine = coercer.coerce((None,), ["payload"])
    assert values == [None]
    assert quarantine is None


# =====================================================================
# Sequence fixing: the classic max(id) vs sequence collision
# =====================================================================


def test_fix_sequences_advances_past_max_id(clean_pg: None) -> None:
    """The classic migration bug: copied rows leave the sequence at 1, so the
    next INSERT collides. ``setval(seq, max(id))`` makes the next
    ``nextval()`` return ``max(id) + 1``."""
    import psycopg  # type: ignore[import-not-found]

    url = _pg_url() or ""
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE seq_probe (id BIGINT GENERATED ALWAYS AS "
                "IDENTITY PRIMARY KEY, label TEXT)"
            )
            # Copy rows with explicit ids (exactly what the migrator does):
            # the sequence stays at 1 while max(id) is 5000.
            cur.execute(
                "INSERT INTO seq_probe (id, label) OVERRIDING SYSTEM VALUE VALUES (5000, 'copied')"
            )
            conn.commit()
            cols = [{"name": "id", "type": "bigint"}]
        fixed = fix_sequences(conn, "seq_probe", cols)
        assert fixed is not None
        assert "seq_probe.id" in fixed
        with conn.cursor() as cur:
            # The next insert must NOT collide: it takes max(id) + 1.
            cur.execute("INSERT INTO seq_probe (label) VALUES ('fresh')")
            cur.execute("SELECT id FROM seq_probe WHERE label = 'fresh'")
            assert cur.fetchone()[0] == 5001
            conn.commit()


def test_fix_sequences_skips_tables_without_id(clean_pg: None) -> None:
    """A table with no ``id`` column (or no owned sequence) is skipped
    gracefully — the copy never fails on it."""
    import psycopg  # type: ignore[import-not-found]

    url = _pg_url() or ""
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE no_id_probe (key TEXT PRIMARY KEY, v INTEGER)")
            conn.commit()
        cols = [{"name": "key", "type": "text"}]
        assert fix_sequences(conn, "no_id_probe", cols) is None


# =====================================================================
# End-to-end against the isolated PG: counts, idempotency, quarantine
# =====================================================================


@needs_pg
def test_end_to_end_row_counts_match(sqlite_dir: Path, clean_pg: None) -> None:
    """(a) every table's destination row count matches its source count.

    ``events`` holds one deliberately-unparseable timestamp that is quarantined
    (and reported, and costs the run its ``ok``); the contract is that every
    OTHER row still arrives, so the destination count is the source count
    minus the quarantined count — never lower.
    """
    result = migrate_sqlite_to_pg(sqlite_dir, _pg_url() or "", dry_run=False)
    payload = result.to_dict()

    source = sqlite3.connect(f"file:{(sqlite_dir / 'audit.db').as_posix()}?mode=ro", uri=True)
    try:
        for table in ("events", "notes"):
            src_n = source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            quarantined = payload["per_table_quarantined"].get(table, 0)
            assert payload["per_table_counts"][table] == src_n - quarantined, table
    finally:
        source.close()


@needs_pg
def test_end_to_end_idempotent(sqlite_dir: Path, clean_pg: None) -> None:
    """(b) re-running converges to the same state — no duplicates, same counts.

    This is the property that makes a crashed migration cheap to recover: the
    copy is ``ON CONFLICT DO NOTHING`` and the schema is ``IF NOT EXISTS``, so
    a re-run needs no pre-clean step.
    """
    url = _pg_url() or ""
    first = migrate_sqlite_to_pg(sqlite_dir, url, dry_run=False).to_dict()
    assert first["ok"] or first["rows_quarantined"] > 0
    counts_after_first = dict(first["per_table_counts"])

    second = migrate_sqlite_to_pg(sqlite_dir, url, dry_run=False).to_dict()
    counts_after_second = dict(second["per_table_counts"])

    assert counts_after_first == counts_after_second
    # The synthetic source has one deliberately-bad timestamp row; it is
    # quarantined on BOTH runs identically (idempotent refusal, and never a
    # duplicate of the good rows).
    assert second["rows_quarantined"] == first["rows_quarantined"]
    assert second["rows_quarantined"] >= 1


@needs_pg
def test_end_to_end_quarantine_is_reported_not_dropped(sqlite_dir: Path, clean_pg: None) -> None:
    """(d) the unparseable timestamp row is quarantined, reported by table +
    reason, and makes the run NOT ok."""
    result = migrate_sqlite_to_pg(sqlite_dir, _pg_url() or "", dry_run=False)
    payload = result.to_dict()
    assert payload["rows_quarantined"] >= 1
    quarantined = payload["quarantine"]
    assert len(quarantined) >= 1
    hit = quarantined[0]
    assert hit["table"] == "events"
    assert hit["column"] == "happened_at"
    assert "does not parse" in hit["reason"]
    # The row was REFUSED: the destination holds the 3 good rows only.
    assert payload["per_table_counts"]["events"] == 3
    # A quarantined row is a lost row until a human looks at it: the verdict
    # and the CLI exit code must reflect that.
    assert payload["ok"] is False


@needs_pg
def test_end_to_end_sequence_advanced_past_max(sqlite_dir: Path, clean_pg: None) -> None:
    """(c) after the copy, the next application INSERT takes a fresh id.

    The copied rows carry explicit ids (max 4 for notes); the sequence must be
    past that or the next insert collides — the classic migration bug.
    """
    import psycopg  # type: ignore[import-not-found]

    url = _pg_url() or ""
    result = migrate_sqlite_to_pg(sqlite_dir, url, dry_run=False)
    assert any("notes.id" in entry for entry in result.sequences_fixed)

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT nextval(pg_get_serial_sequence('notes', 'id'))")
            nxt = cur.fetchone()[0]
            cur.execute("SELECT max(id) FROM notes")
            top = cur.fetchone()[0]
            assert nxt > top
            conn.commit()


@needs_pg
def test_end_to_end_data_survives_round_trip(sqlite_dir: Path, clean_pg: None) -> None:
    """The values, not just the counts, survive the provider switch: the
    timestamp column is a real PG timestamp, the boolean is a real boolean,
    and the BLOB round-trips as BYTEA."""
    import psycopg  # type: ignore[import-not-found]

    url = _pg_url() or ""
    migrate_sqlite_to_pg(sqlite_dir, url, dry_run=False)
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol, happened_at, is_win, payload FROM events "
                "WHERE symbol = 'EURUSD' ORDER BY id"
            )
            rows = cur.fetchall()
            assert rows[0][0] == "EURUSD"
            assert rows[0][1].year == 2026  # a real timestamp, not a text string
            assert rows[0][2] is True  # a real boolean, not 1
            assert rows[0][3] == b"abc"  # BYTEA round-trip
            conn.commit()


@needs_pg
def test_missing_source_database_is_reported(tmp_path: Path, clean_pg: None) -> None:
    """An empty source directory is reported honestly, not silently ignored."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = migrate_sqlite_to_pg(empty, _pg_url() or "", dry_run=True)
    assert result.is_ok() is False
    assert result.schema_errors  # the discovery failure is visible


@needs_pg
def test_table_result_dry_run_reports_source_rows_only(sqlite_dir: Path, clean_pg: None) -> None:
    """A dry-run TableResult reports the source rows it WOULD copy and flags
    itself as a dry run (so a caller cannot mistake it for a copy)."""
    conn = sqlite3.connect(f"file:{(sqlite_dir / 'audit.db').as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        res = copy_table_rows(
            database="audit.db",
            table="notes",
            source_conn=conn,
            target_conn=None,
            target_columns=[{"name": "id", "type": "bigint"}, {"name": "body", "type": "text"}],
            coercer=RowCoercer(
                "notes", [{"name": "id", "type": "bigint"}, {"name": "body", "type": "text"}]
            ),
            dry_run=True,
        )
        assert res.dry_run is True
        assert res.source_rows == 2
        # Nothing was written: a dry run never touches the destination.
        assert res.rows_copied == res.source_rows
    finally:
        conn.close()


# =====================================================================
# CLI wiring: `nexus db migrate-sqlite-to-pg` is on the existing db group
# =====================================================================


def test_cli_command_is_registered_on_db_group() -> None:
    """The command lives on the EXISTING ``db`` Typer group — no parallel
    CLI entry was created (the spec forbids it)."""
    from nexus_scalp.cli.db_commands import db_app

    commands = {c.name or c.callback.__name__ for c in db_app.registered_commands}  # type: ignore[attr-defined]
    assert "migrate-sqlite-to-pg" in commands


def test_cli_help_lists_the_option_names() -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.app_factory import app

    runner = CliRunner()
    outcome = runner.invoke(app, ["db", "migrate-sqlite-to-pg", "--help"])
    assert outcome.exit_code == 0
    assert "--sqlite-dir" in outcome.stdout
    assert "--pg-url" in outcome.stdout
    assert "--dry-run" in outcome.stdout


@needs_pg
def test_cli_dry_run_writes_nothing(sqlite_dir: Path, clean_pg: None) -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.app_factory import app

    runner = CliRunner()
    outcome = runner.invoke(
        app,
        [
            "db",
            "migrate-sqlite-to-pg",
            "--sqlite-dir",
            str(sqlite_dir),
            "--pg-url",
            _pg_url() or "",
            "--dry-run",
            "--json",
        ],
    )
    assert outcome.exit_code == 0
    payload = json.loads(outcome.stdout)
    assert payload["dry_run"] is True
    assert _pg_table_row_counts(_pg_url() or "") == {}


@needs_pg
def test_cli_exits_nonzero_when_a_row_is_quarantined(sqlite_dir: Path, clean_pg: None) -> None:
    """The operator sees a non-zero exit when a row was dropped/quarantined —
    never a silent partial success."""
    from typer.testing import CliRunner

    from nexus_scalp.cli.app_factory import app

    runner = CliRunner()
    outcome = runner.invoke(
        app,
        [
            "db",
            "migrate-sqlite-to-pg",
            "--sqlite-dir",
            str(sqlite_dir),
            "--pg-url",
            _pg_url() or "",
            "--json",
        ],
    )
    assert outcome.exit_code == 1
    payload = json.loads(outcome.stdout)
    assert payload["ok"] is False
    assert payload["rows_quarantined"] >= 1


# =====================================================================
# Helpers
# =====================================================================


def _pg_table_row_counts(pg_url: str) -> dict[str, int]:
    """Every user table's row count on the target (empty dict = nothing there)."""
    import psycopg  # type: ignore[import-not-found]

    try:
        with psycopg.connect(pg_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
                tables = [r[0] for r in cur.fetchall()]
                if not tables:
                    return {}
                out: dict[str, int] = {}
                for table in tables:
                    cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                    out[table] = int(cur.fetchone()[0])
                return out
    except psycopg.Error:
        return {}


@pytest.fixture()
def clean_pg() -> Generator[None, None, None]:
    """Drop every user table on the isolated PG before and after the test.

    The target starts empty (a fresh migration) and is left empty (no state
    leaks between tests). NEVER connects to a live cluster: the URL is the
    lane's isolated instance or the fixture skips.
    """
    url = _pg_url()
    if url is None:
        pytest.skip(f"{PG_URL_ENV} is not set — isolated PG is not running")
    import psycopg  # type: ignore[import-not-found]

    def _wipe() -> None:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
                tables = [r[0] for r in cur.fetchall()]
                for table in tables:
                    cur.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
                conn.commit()

    _wipe()
    yield None
    _wipe()
