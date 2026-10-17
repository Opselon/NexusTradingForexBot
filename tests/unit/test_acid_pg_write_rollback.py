"""ACID gates for the pooled PostgreSQL write path — rollback discipline.

Scope: the two write methods that used to commit without an explicit rollback
on failure (`_apply_many`, `_execute_translated`). The batched path
(`_apply_batch`) always had the guard; the pool does roll back on `putconn`, so
this was never a live data-leak — but leaving the connection INERROR until
return makes any same-handle re-use fail with `current transaction is aborted`,
and a financial write path should not depend on pool internals for its atomicity.

The real-PostgreSQL arm of the ACID contract lives in the `NSE_PG_TEST_URL`
suite (`test_database_portability.py`, `test_pg_dba_migrations.py`); those
require a live server and skip cleanly when it is unset. These tests pin the
guard itself against a stub pool so they run on every platform: what is under
test is that the plane calls rollback before the connection goes back, which is
the code property the fix adds and no server is needed to observe.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pytest

from nexus_scalp.database.fabric.pg_planes import PgWritePlane, PoolLimits


class _StubCursor:
    def __init__(self, events: list[str], *, fail: str | None) -> None:
        self._events = events
        self._fail = fail
        self.rowcount = -1

    def __enter__(self) -> _StubCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def execute(self, sql: str, args: tuple = ()) -> None:
        self._events.append(f"execute:{sql.split(maxsplit=1)[0]}")
        if self._fail == "execute":
            raise RuntimeError("simulated: the statement failed")

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> None:
        self._events.append(f"executemany:{sql.split(maxsplit=1)[0]}")
        if self._fail == "executemany":
            raise RuntimeError("simulated: the batch statement failed")


class _StubConnection:
    def __init__(self, events: list[str], *, fail: str | None) -> None:
        self._events = events
        self._fail = fail

    def cursor(self) -> _StubCursor:
        return _StubCursor(self._events, fail=self._fail)

    def commit(self) -> None:
        self._events.append("commit")

    def rollback(self) -> None:
        self._events.append("rollback")


class _StubPool:
    """Stands in for PgPool: lends one connection, records every call."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.events: list[str] = []
        self.conn = _StubConnection(self.events, fail=fail)

    @property
    def name(self) -> str:
        return "pg-write-stub"

    @contextmanager
    def connection(self) -> Iterator[_StubConnection]:
        self.events.append("checkout")
        try:
            yield self.conn
        finally:
            self.events.append("putconn")


def _plane(*, fail: str | None = None) -> tuple[PgWritePlane, list[str]]:
    plane = PgWritePlane.__new__(PgWritePlane)
    plane._limits = PoolLimits()  # type: ignore[attr-defined]
    plane._pool = _StubPool(fail=fail)  # type: ignore[assignment]
    return plane, plane._pool.events  # type: ignore[attr-defined]


def test_execute_translated_rolls_back_on_failure() -> None:
    """A failed single write rolls back before the connection is returned."""
    plane, events = _plane(fail="execute")

    with pytest.raises(RuntimeError):
        plane._execute_translated("INSERT INTO t (a) VALUES (%s)", (1,))

    assert events == ["checkout", "execute:INSERT", "rollback", "putconn"], (
        f"missing rollback: {events}"
    )
    # And nothing was committed — the write did not partially land.
    assert "commit" not in events


def test_apply_many_rolls_back_on_failure() -> None:
    """A failed executemany rolls back before the connection is returned."""
    plane, events = _plane(fail="executemany")

    with pytest.raises(RuntimeError):
        plane._apply_many("INSERT INTO t (a) VALUES (%s)", [(1,), (2,)])

    assert events == ["checkout", "executemany:INSERT", "rollback", "putconn"], (
        f"missing rollback: {events}"
    )
    assert "commit" not in events


def test_execute_translated_commits_on_success() -> None:
    """The success path is unchanged: commit, then return the connection."""
    plane, events = _plane()

    plane._execute_translated("INSERT INTO t (a) VALUES (%s)", (1,))

    assert events == ["checkout", "execute:INSERT", "commit", "putconn"], events
    assert "rollback" not in events


def test_apply_many_commits_on_success() -> None:
    plane, events = _plane()

    plane._apply_many("INSERT INTO t (a) VALUES (%s)", [(1,), (2,)])

    assert events == ["checkout", "executemany:INSERT", "commit", "putconn"], events
    assert "rollback" not in events


def test_apply_batch_still_rolls_back_on_failure() -> None:
    """The pre-existing guard on the batched path is not regressed by the fix."""
    plane, events = _plane(fail="execute")

    statements: list[tuple[str, Sequence[Sequence[Any]], str]] = [
        ("INSERT INTO t (a) VALUES (?)", [(1,)], "INSERT INTO t (a) VALUES (%s)")
    ]

    with pytest.raises(RuntimeError):
        plane._apply_batch(statements)

    assert events == ["checkout", "execute:INSERT", "rollback", "putconn"], events
    assert "commit" not in events
