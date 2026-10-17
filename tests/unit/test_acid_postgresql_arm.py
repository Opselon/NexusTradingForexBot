"""ACID gates against a REAL PostgreSQL server (the contract's PG arm).

The mission contract requires the ACID guarantees to be demonstrated on real
SQLite AND real PostgreSQL separately, not on a mock. The SQLite arm is
`test_acid_transaction_integrity.py` / `test_acid_write_plane_durability.py`;
the SQLite-side rollback guards are `test_acid_pg_write_rollback.py` (which
pins the code property against a stub pool, since what it tests is the plane's
own rollback call, not server behaviour).

This file is the PG arm proper: it runs only when ``NSE_PG_TEST_URL`` is set
and it connects to a live server, because the guarantees below are server
behaviours that no stub can reproduce:

  * a transaction that fails mid-batch leaves no partial rows on the server;
  * the connection is reusable afterwards (it is not left INERROR);
  * a committed batch survives a connection drop — durability, not buffering;
  * two concurrent writers cannot interleave a partial batch.

When the URL is unset the module skips cleanly, matching the existing PG arm
contract in ``test_database_portability.py``.
"""

from __future__ import annotations

import os
import threading

import pytest

pg_url = os.environ.get("NSE_PG_TEST_URL", "")
needs_pg = pytest.mark.skipif(not pg_url, reason="NSE_PG_TEST_URL not set (PostgreSQL CI test arm)")

pytestmark = needs_pg

try:  # pragma: no cover — exercised only on the PG arm
    import psycopg

    _PSYCOPG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSYCOPG_AVAILABLE = False

needs_psycopg = pytest.mark.skipif(not _PSYCOPG_AVAILABLE, reason="psycopg not installed locally")


def _dsn() -> str:
    """psycopg.connect takes a keyword DSN, not a URL. Convert either form."""
    url = pg_url
    for scheme in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(scheme):
            url = url[len(scheme) :]
            break
    # user:password@host:port/dbname -> keyword=value DSN
    creds, _, rest = url.partition("@")
    dbname = ""
    if "/" in rest:
        rest, _, dbname = rest.partition("/")
    host, _, port = rest.partition(":")
    user, _, password = creds.partition(":")
    parts = [f"host={host}", f"user={user}", f"dbname={dbname or 'postgres'}"]
    if port:
        parts.append(f"port={port}")
    if password:
        parts.append(f"password={password}")
    return " ".join(parts)


@pytest.fixture
def pg_table():
    """A throwaway table on the live server, dropped after the test.

    The primary key is DEFERRABLE INITIALLY DEFERRED on purpose: the tests here
    exercise a batch that fails at COMMIT time (not at the statement), which is
    the shape the write plane's rollback guard is about. With an immediate
    constraint the duplicate would surface at execute(), which is a different
    and already-handled path.
    """
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS acid_probe")
            cur.execute(
                "CREATE TABLE acid_probe ("
                "id INTEGER, v INTEGER NOT NULL, "
                "CONSTRAINT acid_probe_pkey PRIMARY KEY (id) "
                "DEFERRABLE INITIALLY DEFERRED)"
            )
        conn.commit()
    yield "acid_probe"
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS acid_probe")
        conn.commit()


@needs_psycopg
def test_failed_batch_leaves_no_partial_rows_on_the_server(pg_table):
    """A batch that fails at commit time rolls back completely."""
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acid_probe (id, v) VALUES (1, 10)")
            cur.execute("INSERT INTO acid_probe (id, v) VALUES (1, 20)")  # dup PK
            # The deferred constraint fires HERE, not at the statement above.
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.commit()
            conn.rollback()

            # No partial commit survived: the first row did not land either.
            cur.execute("SELECT count(*) FROM acid_probe")
            assert cur.fetchone()[0] == 0


@needs_psycopg
def test_connection_is_reusable_after_a_failed_batch(pg_table):
    """The connection is not left INERROR: the next write still works."""
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acid_probe (id, v) VALUES (1, 10)")
            cur.execute("INSERT INTO acid_probe (id, v) VALUES (1, 20)")
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.commit()
            conn.rollback()

            # The guard the write plane relies on: after rollback the handle is
            # usable again, so a pool re-using this connection does not fail
            # with "current transaction is aborted".
            cur.execute("INSERT INTO acid_probe (id, v) VALUES (2, 20)")
        conn.commit()

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM acid_probe ORDER BY id")
            assert [r[0] for r in cur.fetchall()] == [2]


@needs_psycopg
def test_committed_batch_survives_a_connection_drop(pg_table):
    """Durability on the real server: a commit is fsync'd, not buffered."""
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acid_probe (id, v) VALUES (1, 11)")
            cur.execute("INSERT INTO acid_probe (id, v) VALUES (2, 22)")
        conn.commit()
        # The connection object is discarded WITHOUT a clean close: a commit
        # that was merely buffered would not be visible to a fresh handle.
        conn.close()

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, v FROM acid_probe ORDER BY id")
            rows = [(r[0], r[1]) for r in cur.fetchall()]
    assert rows == [(1, 11), (2, 22)]


@needs_psycopg
def test_concurrent_writers_cannot_interleave_a_partial_batch(pg_table):
    """Two concurrent writers each commit a full batch with no cross-talk.

    The write plane is a single-writer pipeline, so the concurrency contract on
    this arm is not "two writers race on one row" (that is a lost-update
    scenario which READ COMMITTED allows by design — a documented isolation
    difference between the two engines, and the reason the plane serialises
    writes through one worker). It is: two writers running at the same time
    each land every row they committed, and neither sees the other's partial
    state. A partial commit or a cross-writer leak would show up as a missing
    or extra row here.
    """
    rows_per_writer = 25

    def writer(start: int) -> None:
        with psycopg.connect(_dsn()) as conn:
            for i in range(start, start + rows_per_writer):
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO acid_probe (id, v) VALUES (%s, %s)", (i, i))
            conn.commit()

    threads = [
        threading.Thread(target=writer, args=(1,), name="pg-writer-a"),
        threading.Thread(target=writer, args=(101,), name="pg-writer-b"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20.0)
    assert not any(t.is_alive() for t in threads), "a writer hung"

    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM acid_probe")
            total = cur.fetchone()[0]
            cur.execute("SELECT count(DISTINCT id) FROM acid_probe")
            distinct = cur.fetchone()[0]

    # Every row both writers committed is present, exactly once: no partial
    # batch, no duplicate, no row bleeding across the two writers.
    assert total == 2 * rows_per_writer, f"expected {2 * rows_per_writer} rows, got {total}"
    assert distinct == total, f"duplicate ids among committed rows: {total} vs {distinct}"
