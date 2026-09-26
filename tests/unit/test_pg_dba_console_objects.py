"""Lane H (DBA) — the diagnostics UI reports the DBA layer's objects.

The cluster had ZERO functions/triggers and 102/116 never-analyzed tables.
The console could only count TABLES, so a cluster with the full server-side
query layer looked identical to one with none of it. This pins the surface
that proves the layer landed — from the LIVE cluster when PostgreSQL is
configured, and from a stub driver that cannot touch a real database.

The endpoint is read-only, takes no user-controlled SQL text, and reports the
DBA layer's own objects by name (not every object in the database).
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.migration import indexes as indexes_mod
from nexus_scalp.database.migration import triggers as triggers_mod
from nexus_scalp.web import db_console


class _StubPgDriver:
    """Driver double answering a fixed object inventory (never a real DB).

    The console's driver contract is ``query_readonly(sql, args)`` returning
    dicts; the endpoint's probes use ``?`` placeholders the driver translates.
    The stub dispatches on the probe's SQL text so each probe sees the rows
    shaped for it (the inventory, the named-object counts and the bloat list).
    """

    def __init__(self, inventory: list[dict[str, Any]] | None = None) -> None:
        self._inventory = inventory if inventory is not None else []
        self._bloat: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def with_bloat(self, rows: list[dict[str, Any]]) -> _StubPgDriver:
        self._bloat = rows
        return self

    def ping(self) -> bool:
        return True

    def query_readonly(self, sql: str, args: Any = ()) -> list[dict[str, Any]]:
        self.calls.append(sql)
        s = sql.strip().lower()
        # The inventory probe is the outer SELECT with the AS aliases; the
        # named-object probes are scalar ``count(*)`` lookups and the bloat
        # probe reads pg_stat_user_tables.
        if "never_analyzed" in s:
            return list(self._inventory)
        if "pg_stat_user_tables" in s:
            return list(self._bloat)
        if "count(*)" in s:
            # A named-object probe: answer 1 per expected name so the endpoint
            # sums this layer's own objects correctly.
            return [{"n": len(args) if args else 0}] if args else [{"n": 1}]
        return list(self._inventory)

    def close(self) -> None:
        return None


class _FailingDriver(_StubPgDriver):
    """A role that cannot read the catalog: every probe degrades, none raise."""

    def query_readonly(self, sql: str, args: Any = ()) -> list[dict[str, Any]]:
        self.calls.append(sql)
        raise RuntimeError("permission denied for relation pg_proc")


def _inventory_rows(**over: Any) -> list[dict[str, Any]]:
    base: dict[str, Any] = {
        "tables": 116,
        "views": 0,
        "functions": 1,
        "triggers": 14,
        "indexes": 270,
        "never_analyzed": 0,
    }
    base.update(over)
    return [base]


class TestObjectInventory:
    def test_reports_counts_and_dba_layer_objects(self) -> None:
        driver = _StubPgDriver(_inventory_rows())
        inv = db_console.pg_object_inventory(driver)
        assert inv["objects"] == {
            "tables": 116,
            "views": 0,
            "functions": 1,
            "triggers": 14,
            "indexes": 270,
            "never_analyzed": 0,
        }
        # The DBA layer's own objects, named: 1 function, one trigger per wired
        # table, one index per seq-scan-pressure table.
        assert inv["dba_layer"]["trigger_function"] == 1
        assert inv["dba_layer"]["updated_at_triggers"] == len(triggers_mod.TRIGGER_TARGETS)
        assert inv["dba_layer"]["missing_indexes"] == len(indexes_mod.MISSING_INDEXES)

    def test_never_analyzed_count_is_the_signal_the_layer_clears(self) -> None:
        driver = _StubPgDriver(_inventory_rows(never_analyzed=102, functions=0, triggers=0))
        inv = db_console.pg_object_inventory(driver)
        # This is the exact pre-Lane-H shape: 102 unanalyzed tables and no
        # server-side objects at all.
        assert inv["objects"]["never_analyzed"] == 102
        assert inv["objects"]["functions"] == 0
        assert inv["objects"]["triggers"] == 0

    def test_a_catalog_the_role_cannot_read_degrades_not_dies(self) -> None:
        driver = _FailingDriver()
        inv = db_console.pg_object_inventory(driver)
        # Never raises: counts degrade to null and the report is still shaped
        # for the frontend.
        assert inv["objects"]["functions"] is None
        assert inv["dba_layer"]["trigger_function"] is None
        assert inv["bloat"] == []

    def test_bloat_reports_only_tables_with_dead_tuples(self) -> None:
        # The probe is ``ORDER BY n_dead_tup DESC``, so the stub answers in
        # that order and the endpoint preserves it.
        driver = _StubPgDriver(_inventory_rows()).with_bloat(
            [
                {"relname": "audit_ledger", "n_live_tup": 44, "n_dead_tup": 49},
                {"relname": "news_articles", "n_live_tup": 295, "n_dead_tup": 28},
            ]
        )
        inv = db_console.pg_object_inventory(driver)
        assert [(b["table"], b["dead_tuples"]) for b in inv["bloat"]] == [
            ("audit_ledger", 49),
            ("news_articles", 28),
        ]

    def test_the_sql_carries_no_user_controlled_text(self) -> None:
        driver = _StubPgDriver(_inventory_rows())
        db_console.pg_object_inventory(driver)
        for sql in driver.calls:
            # The probes are fixed literals; only the parameter VALUES are
            # supplied separately (the driver translates the placeholders).
            assert ";" not in sql.strip().rstrip(";"), "interior semicolon in a probe"
            assert "/*" not in sql and "*/" not in sql


class TestObjectsEndpoint:
    def test_sqlite_answers_available_false_not_a_fake_inventory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = DatabaseConfig.for_sqlite("audit")
        monkeypatch.setattr(
            db_console, "_driver_for", lambda name, cors=True: (_StubPgDriver(), cfg)
        )
        out = db_console.console_objects("audit")
        assert out["success"] is True
        assert out["available"] is False
        # The DBA layer is a PostgreSQL-only concern: SQLite has no
        # server-side objects, and the endpoint says so rather than reporting
        # zeros that would look like a failed migration.
        assert "reason" in out

    def test_postgres_reports_live_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = DatabaseConfig.for_postgres(
            domain="audit", host="localhost", port=5432, database="nexusdb", username="postgres"
        )
        monkeypatch.setattr(
            db_console,
            "_driver_for",
            lambda name, cors=True: (_StubPgDriver(_inventory_rows()), cfg),
        )
        out = db_console.console_objects("audit")
        assert out["success"] is True
        assert out["available"] is True
        assert out["objects"]["functions"] == 1
        assert out["objects"]["triggers"] == 14
        assert out["objects"]["never_analyzed"] == 0
        assert out["provider"] == "postgresql"

    def test_an_unreachable_postgres_reports_the_target_and_a_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = DatabaseConfig.for_postgres(
            domain="audit", host="localhost", port=5432, database="nexusdb", username="postgres"
        )

        class _Dead(_StubPgDriver):
            def ping(self) -> bool:
                return False

        monkeypatch.setattr(db_console, "_driver_for", lambda name, cors=True: (_Dead(), cfg))
        out = db_console.console_objects("audit")
        assert out["success"] is False
        assert out["code"] == "DB_UNREACHABLE"
        assert "localhost:5432/nexusdb" in out["error"]

    def test_an_unknown_database_is_rejected(self) -> None:
        out = db_console.console_objects("no-such-domain")
        assert out["success"] is False
        assert "unknown database" in out["error"]
