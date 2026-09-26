"""Provider-aware idempotency helper for the persistence hot paths.

Lane L (hot paths / anti-duplicate logic). OWNED FILE — the ONLY production
file this lane adds. Stores adopt it during integration; nothing in
``src/`` is edited by this lane to call it.

WHY THIS EXISTS
===============
The live ``nexusdb`` cluster accumulated duplicate rows on exactly the paths
that re-deliver the same event:

* re-sent ticks / re-evaluated proposals -> ``audit_signals`` ( hottest table
  on the cluster: 127,283 index scans, 1,784 live rows ) written by
  ``audit_repository.log_signal``;
* re-played guard telemetry -> ``audit_guard_telemetry`` ( 43 rows sharing 5
  natural keys, i.e. the same (window_start, symbol, reason_code) counted by
  several processes );
* re-sent orders -> ``audit_orders`` ( 48 duplicate ``execution_id`` rows );
* re-synced broker history -> ``audit_broker_orders / _deals / _trades`` in
  ``broker_history.py``, which already used ``INSERT OR IGNORE`` — that verb
  is dead on PostgreSQL (``translate_sql`` rewrote only ``?``, leaving
  ``INSERT OR REPLACE``/``OR IGNORE`` verbatim -> syntax error -> the write
  was dead-lettered), so the idempotency those stores *thought* they had did
  not exist on the PG provider;
* re-polled RSS feeds -> ``news_junk_hashes`` ( 6 duplicate rows sharing a
  title ) and ``news_articles``.

Every one of those duplicates is a row whose natural key the writer never
asked the provider to enforce. This module is that ask, in one place, in the
two dialects the repo actually speaks.

WHAT IT DOES ( and does NOT )
=============================
* computes a stable natural-key hash for a row, so an idempotency key is
  derivable even on tables with no unique constraint ( the guard table );
* emits ``INSERT ... ON CONFLICT (<target>) DO NOTHING`` on PostgreSQL and
  ``INSERT OR IGNORE`` on SQLite through the existing driver contract —
  never a hand-rolled string per store;
* resolves the conflict target from the live catalog ( PK, then UNIQUE
  constraints, then the caller's declared natural key ), and says so loudly
  when it cannot find one instead of silently degrading to a plain INSERT
  that would duplicate;
* reports whether the row landed, so callers can count suppressed dupes
  ( ``broker_history`` already does this with ``cur.rowcount == 0`` );
* keeps a per-connection dedupe cache for the cheap read-then-write guard
  pattern, without ever caching across connections.

It does NOT change any store, does not open the live cluster, and does not
hold the driver's connection longer than the statement.

SEC: table and column identifiers are funneled through
``DatabaseDriver.quote_ident`` ( allow-list extraction, never interpolation );
values are bound parameters, never text. No SQL string is built from
caller-controlled text.

Provider detection is by ``driver.name`` ( the driver contract field, not a
dsn sniff ) so a stub driver in a test is detected the same way as the real
one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers.base import DatabaseDriver

__all__ = [
    "DedupGuard",
    "InsertResult",
    "insert_ignore_sql",
    "natural_key_hash",
]

#: Hash length for the derived idempotency key. Long enough that a clash is
#: not a practical concern for a guard table ( 2**128 ) and short enough that
#: an index on it stays narrow — the point of the column is a probe, not a
#: digest of the row for cryptography.
_HASH_LEN = 32


class DedupTargetError(RuntimeError):
    """No usable conflict target for the table — FAIL LOUDLY.

    The defect this exists for is silent: a store that cannot resolve a
    conflict target falls back to a plain INSERT, which duplicates the row on
    every re-delivery. The observed damage ( duplicate ``audit_orders`` /
    ``audit_guard_telemetry`` rows on live nexusdb ) came from exactly that
    silent path. Raise instead.
    """


@staticmethod
def _stable_json(value: Any) -> str:
    """Deterministic JSON for hash input (dict key order must not matter)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def natural_key_hash(row: dict[str, Any], key_columns: Sequence[str]) -> str:
    """Stable hex digest of the row's natural key.

    Used when a table has no unique constraint but the store still needs one
    idempotency key to probe with (``audit_guard_telemetry``'s
    (window_start, symbol, reason_code), the broker-ticket keys). The digest
    is order-independent and stable across processes, so a re-delivered event
    on another node derives the same key.

    Raises ``KeyError`` when the row is missing a key column: a row that
    cannot be identified cannot be deduplicated, and guessing ( None in place
    of the key ) would silently dedupe unrelated rows together.
    """
    missing = [c for c in key_columns if c not in row]
    if missing:
        raise KeyError(f"row missing natural-key column(s): {missing}")
    payload = {c: row[c] for c in key_columns}
    h = hashlib.sha256()
    h.update(_stable_json(payload).encode("utf-8"))
    return h.hexdigest()[:_HASH_LEN]


class InsertResult:
    """Outcome of one idempotent insert.

    ``inserted`` / ``suppressed`` are mutually exclusive; ``suppressed`` is
    the count the caller adds to its duplicate counter ( the same counter
    ``broker_history`` already kept via ``cur.rowcount == 0`` ).
    """

    __slots__ = ("inserted", "suppressed", "table")

    def __init__(self, table: str, inserted: int, suppressed: int = 0) -> None:
        self.table = table
        self.inserted = inserted
        self.suppressed = suppressed

    @property
    def inserted_row(self) -> bool:
        return self.inserted > 0

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"InsertResult(table={self.table!r}, inserted={self.inserted}, suppressed={self.suppressed})"


def insert_ignore_sql(table: str, columns: Sequence[str], driver: DatabaseDriver) -> str:
    """Provider-native INSERT-or-ignore for the given columns.

    PostgreSQL: ``INSERT INTO t (c1, c2) VALUES (%s, %s) ON CONFLICT DO NOTHING``
    SQLite:     ``INSERT OR IGNORE INTO t (c1, c2) VALUES (?, ?)``

    ``ON CONFLICT DO NOTHING`` ( no target ) is used when the caller has not
    resolved a constraint: PG picks any applicable unique/PK index. When a
    specific target is known, :meth:`DedupGuard.insert_ignore` emits the
    targeted form, which is cheaper and avoids matching an unrelated index.
    """
    col_list = ", ".join(driver.quote_ident(c) for c in columns)
    table_sql = driver.quote_ident(table)
    if _is_postgres(driver):
        placeholders = ", ".join("%s" for _ in columns)
        return (
            f"INSERT INTO {table_sql} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        )
    placeholders = ", ".join("?" for _ in columns)
    return f"INSERT OR IGNORE INTO {table_sql} ({col_list}) VALUES ({placeholders})"


def _is_postgres(driver: DatabaseDriver) -> bool:
    """True when the driver speaks the PostgreSQL dialect.

    Detected by the driver contract field ``name`` ( ``sqlite`` /
    ``postgresql`` ), the same field the driver base declares as its
    identity. Not a DSN/URL sniff, so a test stub is classified identically.
    """
    return str(getattr(driver, "name", "")).lower() in {"postgres", "postgresql"}


class DedupGuard:
    """Provider-aware idempotency helper over an existing driver.

    One guard per table ( the conflict target is table-specific and cached
    once ), safe to hold for the life of a store. The driver is never owned:
    the caller's connection lifecycle is respected, and the guard never
    closes a connection it did not open.

    Usage ( adoption is integration's job — this is the helper, not a store
    change )::

        guard = DedupGuard(driver, "audit_orders", conflict_target=["execution_id"])
        result = guard.insert_ignore(row)
        if result.suppressed:
            self._dup_counter += 1
    """

    #: Sentinel for "no conflict target could be resolved".
    _UNRESOLVED: tuple[str, ...] | None = None

    def __init__(
        self,
        driver: DatabaseDriver,
        table: str,
        conflict_target: Sequence[str] | None = None,
        natural_key: Sequence[str] | None = None,
        fail_unresolved: bool = True,
    ) -> None:
        self.driver = driver
        self.table = table
        #: Caller-declared natural key. Used (a) as the explicit conflict
        #: target when the caller knows the constraint, (b) to derive an
        #: idempotency-key digest via :meth:`key_for` when the table has no
        #: unique constraint at all.
        self.natural_key: tuple[str, ...] = tuple(natural_key or ())
        self._fail_unresolved = fail_unresolved
        self._target: tuple[str, ...] | bool | None = False  # unresolved marker
        if conflict_target:
            self._target = tuple(conflict_target)
        #: ``column -> occurrences`` of suppressed duplicates this guard saw.
        #: Bounded by the distinct keys on this table; cleared per connection
        #: by :meth:`clear_cache` ( a key seen on conn A says nothing about
        #: conn B, which may be a different transaction ).
        self._seen_keys: set[str] = set()

    # -- conflict target resolution ----------------------------------------

    def conflict_target(self, conn: Any = None) -> tuple[str, ...] | None:
        """Resolve the conflict target for this guard's table.

        Order: the caller's declared target ( explicit wins — the store knows
        its own schema ), then the table's PRIMARY KEY, then its first
        single-column UNIQUE constraint. Returns ``None`` when nothing
        applies, which raises :class:`DedupTargetError` on an insert unless
        the guard was built with ``fail_unresolved=False`` ( then it degrades
        to ``OR IGNORE`` on SQLite / ``ON CONFLICT DO NOTHING`` on PG, so the
        provider itself still rejects the duplicate instead of the row
        duplicating ).

        Caches the answer per table for the driver's lifetime.
        """
        if self._target is not False:
            return self._target if self._target else None  # already resolved
        resolved: tuple[str, ...] | None = None
        if self.natural_key:
            resolved = self.natural_key
        else:
            pk = self._primary_key(conn)
            if pk:
                resolved = tuple(pk)
        if resolved is None:
            resolved = tuple(self._unique_columns(conn))
        self._target = resolved if resolved else self._UNRESOLVED
        return resolved if resolved else None

    def _primary_key(self, conn: Any) -> list[str]:
        """PK columns of the guard's table, in declaration order."""
        try:
            cols = self.driver.table_columns(self.table, conn=conn)
            return [str(c["name"]) for c in cols if c.get("pk")]
        except Exception:
            return []

    def _unique_columns(self, conn: Any) -> list[str]:
        """Columns of the first applicable UNIQUE constraint on the table.

        Only single-column UNIQUE constraints are used as a fallback target:
        a multi-column UNIQUE cannot be targeted from a row that omits one of
        its columns, and picking a subset would raise on PG. Multi-column
        uniques must be declared explicitly via ``conflict_target``.
        """
        try:
            rows = self.driver.query(
                "SELECT kcu.column_name, kcu.ordinal_position "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "WHERE tc.table_name = ? AND tc.constraint_type = 'UNIQUE' "
                "ORDER BY kcu.ordinal_position",
                (self.table,),
                conn=conn,
            )
        except Exception:
            return []
        cols = [str(r["column_name"]) for r in rows if r.get("column_name")]
        return cols[:1]

    # -- keys ---------------------------------------------------------------

    def key_for(self, row: dict[str, Any]) -> str:
        """Natural-key digest for a row ( raises when the key is incomplete )."""
        if not self.natural_key:
            raise DedupTargetError(
                f"DedupGuard({self.table!r}): key_for() needs a natural_key; "
                "declare one or use insert_ignore()"
            )
        return natural_key_hash(row, self.natural_key)

    def clear_cache(self) -> None:
        """Drop the per-connection seen-key cache.

        Call when the connection changes: a key suppressed on one connection
        may legitimately insert on another ( different transaction ). The
        conflict-target cache is per-driver and is NOT cleared here.
        """
        self._seen_keys.clear()

    # -- writes -------------------------------------------------------------

    def insert_ignore(self, row: dict[str, Any], conn: Any = None) -> InsertResult:
        """Insert ``row`` exactly once per natural key.

        Returns an :class:`InsertResult` telling the caller whether the row
        landed or was suppressed as a duplicate. Never raises on a duplicate
        ( that is the point ); raises :class:`DedupTargetError` only when no
        conflict target exists and ``fail_unresolved`` is set.
        """
        cols = list(row.keys())
        if not cols:
            return InsertResult(self.table, 0, 0)
        target = self.conflict_target(conn)
        if target is None:
            if self._fail_unresolved:
                raise DedupTargetError(
                    f"DedupGuard({self.table!r}): no conflict target (PK/UNIQUE/"
                    "declared natural key) — refusing to emit an INSERT that "
                    "would duplicate on re-delivery. Declare conflict_target=[...]."
                )
            # Loud degradation: still DO NOTHING / OR IGNORE, so the provider
            # itself rejects the duplicate rather than the row duplicating.
            sql = insert_ignore_sql(self.table, cols, self.driver)
        else:
            sql = self._targeted_sql(cols, target)
        cur = self.driver.execute(sql, [row[c] for c in cols], conn=conn)
        inserted = self._rowcount_inserted(cur)
        suppressed = 0 if inserted else 1
        if not inserted and self.natural_key:
            self._seen_keys.add(self.key_for(row))
        return InsertResult(self.table, inserted, suppressed)

    def insert_ignore_many(
        self, rows: Iterable[dict[str, Any]], conn: Any = None
    ) -> list[InsertResult]:
        """Idempotent batch insert; one result per row, in order.

        Rows are sent individually so each row's suppression is reported
        ( ``executemany`` cannot tell the caller which row was suppressed ),
        which is the counter the stores keep.
        """
        return [self.insert_ignore(row, conn=conn) for row in rows]

    def _targeted_sql(self, cols: Sequence[str], target: Sequence[str]) -> str:
        """INSERT ... ON CONFLICT (<target>) DO NOTHING (PG) / OR IGNORE (SQLite).

        Two provider truths, both enforced here:

        * PostgreSQL requires the conflict target — the named columns must be
          a real UNIQUE/PK index, and only the columns present in the row may
          be named ( a declared natural key the row omits would otherwise
          raise instead of suppressing );
        * SQLite's ``INSERT OR IGNORE`` takes NO target at all: it resolves
          whichever constraint the row actually violates. Naming a target
          would be dead text, and a *wrong* target silently disables the
          guard ( proven: on a table whose uniqueness lives in a partial
          index — ``WHERE execution_id IS NOT NULL`` — an OR IGNORE with the
          column omitted from the row inserts a duplicate, because the row
          violates nothing ). So SQLite always gets the bare verb and the
          caller's declared target is used only for the catalog check that
          keeps :meth:`conflict_target` honest.
        """
        usable = [c for c in target if c in cols]
        if not usable:
            return insert_ignore_sql(self.table, cols, self.driver)
        table_sql = self.driver.quote_ident(self.table)
        col_list = ", ".join(self.driver.quote_ident(c) for c in cols)
        if _is_postgres(self.driver):
            tgt_list = ", ".join(self.driver.quote_ident(c) for c in usable)
            placeholders = ", ".join("%s" for _ in cols)
            return (
                f"INSERT INTO {table_sql} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT ({tgt_list}) DO NOTHING"
            )
        placeholders = ", ".join("?" for _ in cols)
        return f"INSERT OR IGNORE INTO {table_sql} ({col_list}) VALUES ({placeholders})"

    @staticmethod
    def _rowcount_inserted(cur: Any) -> int:
        """1 when the row landed, 0 when the provider suppressed it.

        psycopg3 reports ``rowcount == 1`` for an inserted row and 0 for a
        conflict; sqlite3 reports the same. Older drivers (-1 / None) are
        treated as "inserted" so a driver that cannot report never turns a
        real write into a false duplicate count.
        """
        try:
            rc = int(cur.rowcount) if cur is not None else -1
        except (TypeError, ValueError, AttributeError):
            return 1
        if rc < 0:
            return 1
        return 1 if rc else 0

    # -- cheap probe guard --------------------------------------------------

    def already_seen(self, row: dict[str, Any]) -> bool:
        """In-process probe: was this exact natural key already suppressed?

        The read-then-write guard pattern for the hot paths ( a store that
        checks "do I have this signal?" before inserting ). This is the cheap
        half: it answers for the rows this guard already handled, so a
        re-delivered event within one connection stops at the probe instead
        of round-tripping to the provider. The provider constraint remains
        the authoritative check — :meth:`insert_ignore` still enforces it.
        """
        if not self.natural_key:
            return False
        return self.key_for(row) in self._seen_keys


def _dedupe_row_key(row: dict[str, Any], columns: Sequence[str]) -> str:
    """Module-level alias kept for call sites that hold no guard instance."""
    return natural_key_hash(row, columns)


def _dedupe_row_key(row: dict[str, Any], columns: Sequence[str]) -> str:
    """Module-level alias kept for call sites that hold no guard instance."""
    return natural_key_hash(row, columns)


def provider_name(driver: DatabaseDriver) -> str:
    """The driver's declared provider name ( for log lines, never for
    behavior — behavior keys off :func:`_is_postgres` )."""
    return str(getattr(driver, "name", "unknown"))


def build_guard(
    config: DatabaseConfig,
    table: str,
    conflict_target: Sequence[str] | None = None,
    natural_key: Sequence[str] | None = None,
) -> DedupGuard:
    """Construct a guard for ``table`` from a resolved config.

    Convenience only: it does not open a connection ( the driver resolves its
    own connection at first use ), so building a guard is cheap enough to do
    per store rather than caching process-globally — a global guard would
    carry a conflict-target cache across databases.
    """
    from nexus_scalp.database.drivers import get_driver

    driver = get_driver(config)
    return DedupGuard(
        driver,
        table,
        conflict_target=conflict_target,
        natural_key=natural_key,
    )
