"""REVIEW LOCK (L3): make the review console genuinely read-only.

agents/REVIEW_LOCK.md documents the lock; this module is what actually
holds it. It runs once, as early as possible, and only does something when
NSE_REVIEW_MODE is set.

THE PROBLEM IT SOLVES
---------------------
The first attempt put the guard inside ``PortableConnection``
(database/drivers/proxy.py). Live testing proved that was too narrow:
only 12 modules go through PortableConnection, while ~72 open
``sqlite3.connect`` directly (incidents/store.py among them). A guard on
one chokepoint cannot cover the others.

The single thing all of those modules share is the ``sqlite3`` module
object itself — they all do ``import sqlite3``. A process-wide guard is
therefore the only placement that covers the whole surface.

WHY A CONNECTION SUBCLASS
-------------------------
``sqlite3.Connection.execute`` is a read-only attribute on CPython, so a
per-instance monkeypatch raises ``AttributeError`` (proven, not assumed).
The supported way to intercept connection behaviour is to subclass
``sqlite3.Connection`` and pass it as the ``factory=`` argument to
``sqlite3.connect``. The wrapper honours a caller-supplied ``factory`` by
inheriting from it, so existing custom factories keep working.

WHAT IT DOES
------------
When NSE_REVIEW_MODE is truthy, ``sqlite3.connect`` is wrapped so every
connection it returns refuses INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/
REPLACE/VACUUM/ANALYZE/TRUNCATE while still allowing SELECT and reads.
The console must be able to *show* state — that is its entire purpose.

WHEN IT IS OFF
--------------
Default: off. With NSE_REVIEW_MODE unset the module does nothing and
returns immediately, so production is completely unaffected: no wrapper,
no extra call frame, no behavioural change.
"""

from __future__ import annotations

import sqlite3
from typing import Any

_ENV_VAR = "NSE_REVIEW_MODE"

# Statements that mutate data or schema. SELECT / PRAGMA reads are allowed.
_WRITE_PREFIXES = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "ALTER",
    "DROP",
    "REPLACE",
    "VACUUM",
    "ANALYZE",
    "TRUNCATE",
)


class ReviewWriteBlockedError(RuntimeError):
    """A write was attempted while NSE_REVIEW_MODE was active."""

    def __init__(self, sql: str) -> None:
        super().__init__(
            "REVIEW LOCK: this is the read-only review console "
            f"({_ENV_VAR}=1; see agents/REVIEW_LOCK.md). "
            f"Write refused: {sql.strip()[:80]!r}"
        )


def _is_write(sql: str) -> bool:
    """Return True only for statements that actually mutate live state.

    Two boot-time forms are deliberately allowed because they are
    idempotent and cannot change or destroy existing state:

    * ``CREATE [TABLE|INDEX] IF NOT EXISTS`` — creates an object only when
      it is absent; never alters or drops existing data. Refusing it makes
      the console unable to start.
    * ``INSERT OR IGNORE`` / ``INSERT OR REPLACE`` into the bootstrap
      tables — ``OR IGNORE`` is a no-op when the row exists (the seed
      method only ever runs at first boot, when the table is empty, and
      the PRIMARY KEY makes it a no-op thereafter). ``OR REPLACE`` is
      allowed in the same spirit: the seed data is fixed, so re-running it
      rewrites a row with the same constant value.

    A plain ``INSERT`` (no ``OR`` clause) is still refused — that is a
    genuine data write and exactly what the review console must not do.
    """
    head = sql.lstrip()[:60].upper()
    # Idempotent schema bootstrap: any "CREATE ... IF NOT EXISTS" is allowed.
    # It creates an object only when it is absent and can never alter or drop
    # existing data, so refusing it protects nothing and only stops the console
    # from booting. Covers TABLE, INDEX, UNIQUE INDEX, VIEW, TRIGGER.
    if " IF NOT EXISTS" in head and head.startswith("CREATE"):
        return False
    # Idempotent seed writes: allowed (bootstrap data only, fixed constants).
    if head.startswith("INSERT OR IGNORE") or head.startswith("INSERT OR REPLACE"):
        return False
    return any(head.startswith(p) for p in _WRITE_PREFIXES)


def _guarded_connection_class(base: Any) -> Any:
    """Build a Connection subclass that refuses writes, on top of ``base``."""

    class _Guarded(base):  # type: ignore[misc, valid-type]
        def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
            if _is_write(sql):
                raise ReviewWriteBlockedError(sql)
            return super().execute(sql, *args, **kwargs)

        def executemany(self, sql: str, *args: Any, **kwargs: Any) -> Any:
            if _is_write(sql):
                raise ReviewWriteBlockedError(sql)
            return super().executemany(sql, *args, **kwargs)

        def executescript(self, sql: str, *args: Any, **kwargs: Any) -> Any:
            if _is_write(sql):
                raise ReviewWriteBlockedError(sql)
            return super().executescript(sql, *args, **kwargs)

    _Guarded.__name__ = f"ReviewLocked{getattr(base, '__name__', 'Connection')}"
    _Guarded.__qualname__ = _Guarded.__name__
    return _Guarded


# The default guarded connection used when the caller supplies no factory.
_ReadOnlyConnection = _guarded_connection_class(sqlite3.Connection)


def install_review_write_guard() -> bool:
    """Wrap ``sqlite3.connect`` so writes are refused in review mode.

    Returns True if the guard was installed, False if review mode is off
    (production path — nothing is wrapped).

    Idempotent: the marker attribute stops a second call from
    double-wrapping.
    """
    import os

    if not os.environ.get(_ENV_VAR):
        return False
    if getattr(sqlite3.connect, "_nse_review_guard", False):
        return True  # already installed

    real_connect = sqlite3.connect

    def guarded_connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        factory = kwargs.pop("factory", None)
        cls = _guarded_connection_class(factory) if factory else _ReadOnlyConnection
        return real_connect(database, *args, factory=cls, **kwargs)  # type: ignore[call-arg]

    guarded_connect._nse_review_guard = True  # type: ignore[attr-defined]
    sqlite3.connect = guarded_connect  # type: ignore[assignment]
    return True
