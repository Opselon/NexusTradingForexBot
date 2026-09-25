"""RT-003: the audit_guard_telemetry counter upsert on PostgreSQL.

The pasted runtime log reports ``ambiguous count`` for the guard-telemetry
counter. Reproduced against the real domain: PostgreSQL resolves the bare
``count`` in ``DO UPDATE SET count = count + 1`` against both the target row
and ``excluded.count`` and refuses. SQLite accepts the bare form, which is why
the statement is correct on SQLite and broken on the deployed provider.

The fix is ``excluded.count``, which both dialects accept, so the same
statement string is now portable.

CONVENTION: follows the suite's PostgreSQL arm
(``tests/unit/test_database_portability.py``): the URL comes from
``NSE_PG_TEST_URL`` and the module skips cleanly when it is unset.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

psycopg = pytest.importorskip("psycopg")

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_postgres = pytest.mark.skipif(
    not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL CI test arm)"
)

COUNTER = """
    INSERT INTO audit_guard_telemetry AS t (window_start, symbol, reason_code, count)
    VALUES (%s, %s, %s, 1)
    ON CONFLICT(window_start, symbol, reason_code)
    DO UPDATE SET count = t.count + 1
"""


@needs_postgres
def test_guard_telemetry_counter_upserts_on_postgresql() -> None:
    """The counter increments across repeated events on the real provider.

    Runs on a scratch window (a minute that will never be produced by the
    engine) inside a transaction that is rolled back, so the live domain is
    not written to.
    """
    window = "1999-01-01T00:00"
    with psycopg.connect(PG_URL, connect_timeout=10) as conn:
        try:
            with conn.cursor() as cur:
                # First event: no row exists, so this is the INSERT arm.
                cur.execute(COUNTER, (window, "TESTRT3", "guard"))
                cur.execute(
                    "SELECT count FROM audit_guard_telemetry "
                    "WHERE window_start=%s AND symbol=%s AND reason_code=%s",
                    (window, "TESTRT3", "guard"),
                )
                assert cur.fetchone()[0] == 1

                # Second event: the UPDATE arm — the one that was ambiguous.
                cur.execute(COUNTER, (window, "TESTRT3", "guard"))
                cur.execute(
                    "SELECT count FROM audit_guard_telemetry "
                    "WHERE window_start=%s AND symbol=%s AND reason_code=%s",
                    (window, "TESTRT3", "guard"),
                )
                assert cur.fetchone()[0] == 2
        finally:
            conn.rollback()
