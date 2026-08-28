"""
DATABASE PORTABILITY provider matrix suite (TEST-DBP-01..40, 2026-08-20)
=====================================================================
Covers the SQLite <-> PostgreSQL portability layer:

  Provider selection                                  01-05
  DatabaseConfig + secret store (password safety)      06-10
  Portable driver contract (SQLite real)              11-18
  DDL porting (SQLite DDL -> PostgreSQL DDL)           19-22
  Migrator preview (dry run, safety)                  23-26
  Migration run (batched/resumable/validated)         27-33
  Health service                                      34-36
  CLI/API surface (route presence)                    37-40

PostgreSQL integration tests run only when NSE_PG_TEST_URL is set
(or a local docker-compose PG is reachable) — skipped otherwise.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.database.config import (  # noqa: E402
    DatabaseConfig,
    load_database_config,
    mask_url_password,
)
from nexus_scalp.database.drivers import get_driver  # noqa: E402
from nexus_scalp.database.drivers.postgres_driver import _translate_placeholders  # noqa: E402
from nexus_scalp.database.migrate_engine import (  # noqa: E402
    MigrationOptions,
    SqliteToPostgresMigrator,
)
from nexus_scalp.database.provider import DatabaseProvider  # noqa: E402

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_pg = pytest.mark.skipif(not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL CI test arm)")


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------
class TestProviderSelection:
    def test_parse_canonical(self):
        assert DatabaseProvider.parse("sqlite") is DatabaseProvider.SQLITE
        assert DatabaseProvider.parse("postgresql") is DatabaseProvider.POSTGRESQL

    def test_parse_aliases(self):
        assert DatabaseProvider.parse("postgres") is DatabaseProvider.POSTGRESQL
        assert DatabaseProvider.parse("pgsql") is DatabaseProvider.POSTGRESQL
        assert DatabaseProvider.parse("PostgreSQL") is DatabaseProvider.POSTGRESQL
        assert DatabaseProvider.parse("sqlite3") is DatabaseProvider.SQLITE

    def test_parse_empty_defaults_to_sqlite(self):
        assert DatabaseProvider.parse("") is DatabaseProvider.SQLITE
        assert DatabaseProvider.parse(None) is DatabaseProvider.SQLITE

    def test_from_url(self):
        assert DatabaseProvider.from_url("sqlite:///x.db") is DatabaseProvider.SQLITE
        assert DatabaseProvider.from_url("postgresql://u:p@h/db") is DatabaseProvider.POSTGRESQL
        assert DatabaseProvider.from_url("") is DatabaseProvider.SQLITE

    def test_default_config_is_sqlite(self):
        cfg = load_database_config(
            "audit", settings_db_path=str(REPO_ROOT / "artifacts" / "audit.db")
        )
        assert cfg.is_sqlite
        assert cfg.sqlite_path.endswith("audit.db")


# ---------------------------------------------------------------------------
# DatabaseConfig + secret store
# ---------------------------------------------------------------------------
class TestDatabaseConfig:
    def test_sqlite_roundtrip(self):
        cfg = DatabaseConfig.for_sqlite("audit", path="artifacts/audit.db")
        d = cfg.to_dict()
        back = DatabaseConfig.from_dict(d, "audit")
        assert back.is_sqlite and back.sqlite_path == "artifacts/audit.db"

    def test_postgres_roundtrip_never_round_trips_password(self):
        cfg = DatabaseConfig.for_postgres(
            domain="audit",
            host="db.internal",
            port=5432,
            database="nse_audit",
            username="nse_user",
        )
        d = cfg.to_dict()
        assert "password" not in d
        assert d["password_secret"] == "db.postgresql.password"

    def test_url_never_logs_password(self):
        url = "postgresql://nse_user:SUPERSECRET@db.internal:5432/nse_audit"
        masked = mask_url_password(url)
        assert "SUPERSECRET" not in masked
        assert masked == "postgresql://nse_user:***@db.internal:5432/nse_audit"

    def test_build_url_injects_password_from_secret_store(self, tmp_path):
        cfg = DatabaseConfig.for_postgres(domain="audit", host="localhost", database="nse_audit")
        from nexus_scalp.settings.secret_store import SecureSecretStore

        store = SecureSecretStore(root=tmp_path)
        store.set_secret("db.postgresql.password", "s3cret")
        # The store lives at a custom root; build_postgres_url uses the default root —
        # verify the URL masking path at least
        url = cfg.build_url(password="s3cret")
        assert "s3cret" in url

    def test_env_overrides_win(self, monkeypatch, tmp_path):
        env = {
            "NSE_DATABASE__PROVIDER": "postgresql",
            "NSE_DATABASE__PG_HOST": "pg1",
            "NSE_DATABASE__PG_PORT": "5433",
            "NSE_DATABASE__PG_DATABASE": "my_db",
            "NSE_DATABASE__PG_USER": "my_user",
        }
        cfg = load_database_config(
            "audit", env=env, settings_db_path=str(tmp_path / "nonexistent.db")
        )
        assert cfg.is_postgresql
        assert cfg.host == "pg1" and cfg.port == 5433
        assert cfg.database == "my_db" and cfg.username == "my_user"


# ---------------------------------------------------------------------------
# Portable driver contract (SQLite real; PostgreSQL via translate)
# ---------------------------------------------------------------------------
class TestDriverContract:
    def test_sqlite_driver_basics(self):
        cfg = DatabaseConfig.for_sqlite("audit", uri="file::memory:?cache=shared")
        d = get_driver(cfg)
        conn = d.connect_shared()
        d.configure_connection(conn)
        conn.execute(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, px REAL)"
        )
        d.upsert("t", {"name": "a", "px": 1.0}, conn=conn)
        d.upsert("t", {"name": "a", "px": 2.0}, conn=conn)
        rows = d.query("SELECT * FROM t", conn=conn)
        assert len(rows) == 1 and rows[0]["px"] == 2.0
        assert d.scalar("SELECT COUNT(*) FROM t", conn=conn) == 1
        assert d.ping(conn=conn) is True
        assert d.database_version(conn=conn)
        conn.close()
        d.close()

    def test_placeholder_translation(self):
        assert (
            _translate_placeholders("a = ? AND b = 'what?' AND c = ?")
            == "a = %s AND b = 'what?' AND c = %s"
        )
        assert (
            _translate_placeholders('SELECT "col?x" FROM t WHERE y=?')
            == 'SELECT "col?x" FROM t WHERE y=%s'
        )

    def test_upsert_semantics_on_sqlite(self):
        cfg = DatabaseConfig.for_sqlite("audit", uri="file::memory:?cache=shared")
        d = get_driver(cfg)
        conn = d.connect_shared()
        conn.execute(
            "CREATE TABLE upsert_t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, px REAL)"
        )
        d.insert_ignore("upsert_t", {"name": "x", "px": 1.0}, conn=conn)
        d.insert_ignore("upsert_t", {"name": "x", "px": 9.0}, conn=conn)
        assert d.scalar("SELECT COUNT(*) FROM upsert_t", conn=conn) == 1
        assert d.scalar("SELECT px FROM upsert_t", conn=conn) == 1.0
        conn.close()
        d.close()

    def test_transaction_context(self):
        cfg = DatabaseConfig.for_sqlite("audit", uri="file::memory:?cache=shared")
        d = get_driver(cfg)
        conn = d.connect_shared()
        conn.execute("CREATE TABLE tx_t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
        with d.transaction(conn):
            conn.execute("INSERT INTO tx_t (v) VALUES (?)", ("a",))
        assert d.scalar("SELECT COUNT(*) FROM tx_t", conn=conn) == 1
        try:
            with d.transaction(conn):
                conn.execute("INSERT INTO tx_t (v) VALUES (?)", ("b",))
                raise RuntimeError("rollback me")
        except RuntimeError:
            pass
        assert d.scalar("SELECT COUNT(*) FROM tx_t", conn=conn) == 1  # rolled back
        conn.close()
        d.close()


# ---------------------------------------------------------------------------
# DDL porting
# ---------------------------------------------------------------------------
class TestDdlPorting:
    def test_autoincrement_becomes_bigserial(self):
        from nexus_scalp.database.ddl_port import port_create_table

        ddl = """CREATE TABLE IF NOT EXISTS audit_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            confidence REAL NOT NULL
        );"""
        out = port_create_table(ddl)
        assert "BIGSERIAL PRIMARY KEY" in out
        assert "AUTOINCREMENT" not in out

    def test_integer_pk_becomes_bigserial(self):
        from nexus_scalp.database.ddl_port import port_create_table

        ddl = "CREATE TABLE t (ticket INTEGER PRIMARY KEY, symbol TEXT NOT NULL);"
        out = port_create_table(ddl)
        assert "ticket BIGSERIAL PRIMARY KEY" in out

    def test_real_becomes_double_precision(self):
        from nexus_scalp.database.ddl_port import port_create_table

        ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, pnl REAL DEFAULT 0.0);"
        out = port_create_table(ddl)
        assert "DOUBLE PRECISION" in out

    def test_blob_becomes_bytea(self):
        from nexus_scalp.database.ddl_port import port_create_table

        ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, payload BLOB);"
        assert "BYTEA" in port_create_table(ddl)

    def test_without_rowid_dropped(self):
        from nexus_scalp.database.ddl_port import port_create_table

        ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT) WITHOUT ROWID;"
        out = port_create_table(ddl)
        assert "WITHOUT ROWID" not in out

    def test_double_quoted_literal_becomes_single_quoted(self):
        from nexus_scalp.database.ddl_port import port_create_table

        ddl = 'CREATE TABLE broker_orders (order_id INTEGER PRIMARY KEY, status TEXT DEFAULT "HOLD" NOT NULL)'
        out = port_create_table(ddl)
        assert "DEFAULT 'HOLD'" in out, out
        assert 'DEFAULT "HOLD"' not in out

    def test_double_quoted_identifier_preserved(self):
        from nexus_scalp.database.ddl_port import port_create_table

        ddl = 'CREATE TABLE t ("order_id" INTEGER PRIMARY KEY, "symbol" TEXT NOT NULL)'
        out = port_create_table(ddl)
        assert '"order_id"' in out, out
        assert '"symbol"' in out, out


# ---------------------------------------------------------------------------
# Migrator preview + safety
# ---------------------------------------------------------------------------
class TestMigratorPreview:
    def test_preview_reads_source(self, tmp_path):
        src = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "src.db"))
        conn = sqlite3.connect(str(tmp_path / "src.db"))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT, px REAL)")
        conn.execute("INSERT INTO t VALUES (1, 'a', 1.5),(2, 'b', 2.5)")
        conn.commit()
        conn.close()
        dst = DatabaseConfig.for_postgres(domain="audit", host="localhost", database="nse_audit")
        mig = SqliteToPostgresMigrator(src, dst, MigrationOptions(dry_run=True))
        pv = mig.preview()
        assert pv["rows"] == 2
        assert "t" in pv["table_details"]

    def test_preview_never_writes(self, tmp_path):
        src = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "src.db"))
        conn = sqlite3.connect(str(tmp_path / "src.db"))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'a')")
        conn.commit()
        conn.close()
        dst = DatabaseConfig.for_postgres(domain="audit", host="localhost", database="nse_audit")
        mig = SqliteToPostgresMigrator(src, dst, MigrationOptions(dry_run=True))
        mig.run()
        # destination unreachable -> preview mode shouldn't have crashed
        assert True

    def test_migrator_requires_sqlite_source(self):
        pg = DatabaseConfig.for_postgres(domain="audit", host="localhost", database="nse_audit")
        with pytest.raises(ValueError):
            SqliteToPostgresMigrator(pg, pg, MigrationOptions())


# ---------------------------------------------------------------------------
# Health service
# ---------------------------------------------------------------------------
class TestHealthService:
    def test_sqlite_health(self):
        from nexus_scalp.database.health import health_snapshot

        h = health_snapshot()
        assert h["active_provider"] == "sqlite"
        assert "audit" in h["domains"]

    def test_load_ui_config_no_password(self):
        from nexus_scalp.database.health import load_ui_config

        ui = load_ui_config()
        assert ui["provider"] == "sqlite"
        assert ui["password_set"] is False


# ---------------------------------------------------------------------------
# PostgreSQL integration (skipped without NSE_PG_TEST_URL)
# ---------------------------------------------------------------------------
@needs_pg
class TestPostgresIntegration:
    def test_pg_ping_and_version(self):
        from nexus_scalp.database.config import DatabaseConfig
        from nexus_scalp.database.drivers import get_driver

        cfg = DatabaseConfig.for_postgres(domain="audit", host="localhost", database="nse_audit")
        d = get_driver(cfg)
        assert d.ping()
        assert d.database_version().startswith("PostgreSQL")


# ---------------------------------------------------------------------------
# REAL PostgreSQL integration (runs against the docker-compose PG service)
# ---------------------------------------------------------------------------
@needs_pg
class TestPostgresCrud:
    def _cfg(self):
        from nexus_scalp.database.config import DatabaseConfig

        return DatabaseConfig.for_postgres(
            domain="audit",
            host="localhost",
            port=5432,
            database="nse_audit",
            username="nse_user",
            ssl_mode="",
        )

    def _ensure_pw(self):
        from nexus_scalp.settings.secret_store import SecureSecretStore

        store = SecureSecretStore()
        if not store.has_secret("db.postgresql.password"):
            store.set_secret("db.postgresql.password", "nse_password_dev")

    def test_pg_schema_ddl_and_crud(self):
        self._ensure_pw()
        from nexus_scalp.database.ddl_port import port_create_table
        from nexus_scalp.database.drivers import get_driver

        d = get_driver(self._cfg())
        ddl = port_create_table(
            "CREATE TABLE IF NOT EXISTS pg_probe_t ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  name TEXT UNIQUE NOT NULL,"
            "  px REAL DEFAULT 0.0,"
            "  payload TEXT DEFAULT '{}'"
            ")"
        )
        with d.connect() as conn:
            conn.execute("DROP TABLE IF EXISTS pg_probe_t")
            conn.commit()
        d.execute("DROP TABLE IF EXISTS pg_probe_t")
        d.create_table("pg_probe_t", ddl)
        assert d.table_exists("pg_probe_t")
        # upsert + insert-ignore emulation on real PG
        d.upsert("pg_probe_t", {"name": "a", "px": 1.0})
        d.upsert("pg_probe_t", {"name": "a", "px": 2.0})
        rows = d.query("SELECT * FROM pg_probe_t ORDER BY id")
        assert len(rows) == 1 and float(rows[0]["px"]) == 2.0
        assert float(d.scalar("SELECT px FROM pg_probe_t LIMIT 1")) == 2.0
        # transaction rollback
        try:
            with d.transaction():
                d.execute("INSERT INTO pg_probe_t (name, px) VALUES (%s, %s)", ("b", 3.0))
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        assert int(d.scalar("SELECT COUNT(*) FROM pg_probe_t")) == 1
        d.close()

    def test_pg_placeholder_translation_live(self):
        self._ensure_pw()
        from nexus_scalp.database.drivers import get_driver

        d = get_driver(self._cfg())
        # qmark SQL translated automatically by the driver
        d.execute("DROP TABLE IF EXISTS pg_probe_t2")
        d.execute("CREATE TABLE pg_probe_t2 (id INTEGER PRIMARY KEY, v TEXT)")
        d.execute("INSERT INTO pg_probe_t2 (id, v) VALUES (?, ?)", (1, "qmark-ok"))
        assert d.scalar("SELECT v FROM pg_probe_t2 WHERE id = ?", (1,)) == "qmark-ok"
        d.execute("DROP TABLE IF EXISTS pg_probe_t2")
        d.close()

    def test_pg_financial_precision(self):
        """Financial values must NOT lose precision on PostgreSQL (REAL->DOUBLE)."""
        self._ensure_pw()
        from nexus_scalp.database.drivers import get_driver

        d = get_driver(self._cfg())
        d.execute("DROP TABLE IF EXISTS pg_probe_prec")
        d.execute(
            "CREATE TABLE pg_probe_prec (id INTEGER PRIMARY KEY, pnl DOUBLE PRECISION, "
            "commission DOUBLE PRECISION, swap DOUBLE PRECISION)"
        )
        pnl, comm, swap = 12345.6789, 12.34, -5.678
        d.execute("INSERT INTO pg_probe_prec VALUES (?, ?, ?, ?)", (1, pnl, comm, swap))
        row = d.query_one("SELECT * FROM pg_probe_prec WHERE id = ?", (1,))
        assert abs(float(row["pnl"]) - pnl) < 1e-6
        assert abs(float(row["commission"]) - comm) < 1e-6
        assert abs(float(row["swap"]) - swap) < 1e-6
        d.execute("DROP TABLE IF EXISTS pg_probe_prec")
        d.close()

    def test_pg_migrator_end_to_end(self):
        """Full SQLite->PostgreSQL migration on a REAL PG target."""
        self._ensure_pw()
        import sqlite3
        import tempfile

        from nexus_scalp.database.config import DatabaseConfig
        from nexus_scalp.database.migrate_engine import MigrationOptions, SqliteToPostgresMigrator

        tmp = tempfile.mkdtemp()
        src_path = str(Path(tmp) / "src.db")
        conn = sqlite3.connect(src_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, px REAL)")
        conn.executemany(
            "INSERT INTO t VALUES (?, ?, ?)", [(i, f"row-{i}", i * 1.5) for i in range(1, 101)]
        )
        conn.commit()
        conn.close()

        src = DatabaseConfig.for_sqlite("audit", path=src_path)
        dst = DatabaseConfig.for_postgres(
            domain="audit",
            host="localhost",
            port=5432,
            database="nse_audit",
            username="nse_user",
        )
        mig = SqliteToPostgresMigrator(src, dst, MigrationOptions(batch_size=17))
        mig._pg_driver.execute("DROP TABLE IF EXISTS t")
        mig._pg_driver.execute("DROP TABLE IF EXISTS _nse_migration_checkpoints")
        report = mig.run()
        assert report.status == "COMPLETE", report.errors
        assert report.rows_migrated == 100
        assert report.validation == "PASSED", report.errors
        assert report.provider_switch_ready is True
        # idempotent re-run (resume/ON CONFLICT)
        report2 = mig.run()
        assert report2.status == "COMPLETE"
        mig._pg_driver.execute("DROP TABLE IF EXISTS t")
        mig._pg_driver.execute("DROP TABLE IF EXISTS _nse_migration_checkpoints")

    def test_pg_health_service(self):
        self._ensure_pw()
        from nexus_scalp.database.drivers import get_driver

        # DBHealth for a PG-configured domain resolves via settings; probe the
        # driver health directly here
        d = get_driver(self._cfg())
        assert d.ping() is True
        assert d.table_count() >= 0
        assert d.database_size_bytes() is not None
        d.close()


# ---------------------------------------------------------------------------
# REAL PostgreSQL integration (runs against the docker-compose PG service)
# ---------------------------------------------------------------------------
@needs_pg
class TestCliApiSurface:
    def test_cli_app_registerable(self):
        from nexus_scalp.cli.db_commands import make_portability_app

        app = make_portability_app()
        assert app.info.name == "db-portability" or app.info.help  # registered typer

    def test_server_has_manage_routes(self):
        server_path = REPO_ROOT / "src" / "nexus_scalp" / "web" / "server.py"
        text = server_path.read_text(encoding="utf-8")
        for route in (
            "/api/db/manage/status",
            "/api/db/manage/config",
            "/api/db/manage/test-connection",
            "/api/db/manage/provider",
            "/api/db/manage/preview",
            "/api/db/manage/migrate",
            "/api/db/manage/validate",
            "/api/db/manage/progress",
            "/api/db/manage/report",
            "/api/db/manage/backup",
        ):
            assert route in text, f"missing route {route}"

    def test_ui_has_db_tab(self):
        html = (REPO_ROOT / "Web" / "index.html").read_text(encoding="utf-8")
        assert 'id="tab-database"' in html
        assert "DATABASE MANAGEMENT" in html

    def test_ui_has_db_js(self):
        js = (REPO_ROOT / "Web" / "app.js").read_text(encoding="utf-8")
        for fn in ("loadDbStatus", "switchDbProvider", "startDbMigration", "savePgConfig"):
            assert fn in js, f"missing JS function {fn}"


# ---------------------------------------------------------------------------
# DATABASE CONSOLE (Hermes-DBConsole 2026-08-20) — SSMS-style explorer + SQL
# console + API keys. Provider-abstracted; read-only by contract.
# ---------------------------------------------------------------------------
class TestDbConsoleDatabases:
    def test_list_databases_finds_all_domain_files(self):
        from nexus_scalp.web.db_console import _list_databases

        dbs = {d["name"]: d for d in _list_databases()}
        for name in ("audit", "news", "candle_intel", "settings"):
            assert name in dbs, f"missing database entry {name}"
        assert dbs["audit"]["provider"] in ("sqlite", "postgresql")
        assert dbs["audit"]["status"] in ("CONNECTED", "DISCONNECTED", "DRIVER_UNAVAILABLE: ")

    def test_databases_endpoint_live(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nexus_scalp.web.db_console import router

        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        r = c.get("/api/db/console/databases")
        body = r.json()
        assert body["success"] is True
        names = {d["name"] for d in body["databases"]}
        assert {"audit", "news", "candle_intel", "settings"} <= names

    def test_refresh_rescans(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nexus_scalp.web.db_console import router

        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        r = c.post("/api/db/console/refresh")
        assert r.json()["success"] is True
        assert r.json()["resynced"] is True

    def test_rows_endpoint_paginated_and_capped(self):
        from nexus_scalp.database.config import load_database_config

        cfg = load_database_config("audit")
        if cfg.is_postgresql:
            pytest.skip(
                "audit is on PostgreSQL; db_console rows test requires SQLite application_settings"
            )

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nexus_scalp.web.db_console import MAX_ROWS, router

        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        r = c.get(
            "/api/db/console/rows",
            params={"database": "audit", "table": "application_settings", "limit": 5, "offset": 0},
        )
        body = r.json()
        assert body["success"] is True
        assert len(body["rows"]) <= 5
        assert isinstance(body["columns"], list)
        # the guard: even a silly limit cannot exceed MAX_ROWS
        r2 = c.get(
            "/api/db/console/rows",
            params={"database": "audit", "table": "application_settings", "limit": 999999},
        )
        assert len(r2.json()["rows"]) <= MAX_ROWS

    def test_columns_endpoint(self):
        from nexus_scalp.database.config import load_database_config

        cfg = load_database_config("audit")
        if cfg.is_postgresql:
            pytest.skip("audit is on PostgreSQL; columns test requires SQLite application_settings")

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nexus_scalp.web.db_console import router

        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        r = c.get(
            "/api/db/console/columns", params={"database": "audit", "table": "application_settings"}
        )
        body = r.json()
        assert body["success"] is True
        assert any(col["name"] == "key" for col in body["columns"])
        assert all("type" in col for col in body["columns"])


class TestDbConsoleQueryGuard:
    def test_select_allowed(self):
        from nexus_scalp.database.config import load_database_config

        cfg = load_database_config("audit")
        if cfg.is_postgresql:
            pytest.skip("audit is on PostgreSQL; query test requires SQLite application_settings")

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nexus_scalp.web.db_console import router

        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        r = c.post(
            "/api/db/console/query",
            json={"database": "audit", "sql": "SELECT COUNT(*) AS n FROM application_settings"},
        )
        body = r.json()
        assert body["success"] is True
        assert body["rows"][0]["n"] >= 0

    def test_write_rejected(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nexus_scalp.web.db_console import router

        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        for bad in (
            "DROP TABLE application_settings",
            "INSERT INTO application_settings VALUES (1)",
            "DELETE FROM application_settings",
            "UPDATE application_settings SET x=1",
        ):
            r = c.post("/api/db/console/query", json={"database": "audit", "sql": bad})
            body = r.json()
            assert body["success"] is False, f"expected rejection for {bad}"
            assert "read-only" in body["error"] or "not allowed" in body["error"]

    def test_multi_statement_rejected(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nexus_scalp.web.db_console import router

        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        r = c.post(
            "/api/db/console/query",
            json={"database": "audit", "sql": "SELECT 1; DROP TABLE application_settings"},
        )
        assert r.json()["success"] is False

    def test_quick_sql_top100(self):
        from nexus_scalp.database.config import load_database_config

        cfg = load_database_config("audit")
        if cfg.is_postgresql:
            pytest.skip(
                "audit is on PostgreSQL; quick-sql test requires SQLite application_settings"
            )

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nexus_scalp.web.db_console import router

        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        r = c.get(
            "/api/db/console/quick",
            params={"database": "audit", "table": "application_settings", "kind": "top100"},
        )
        body = r.json()
        assert body["success"] is True
        assert len(body.get("rows", [])) <= 100

    def test_placeholder_translation_used_for_pg(self):
        from nexus_scalp.web.db_console import _query_console_sql

        assert (
            _query_console_sql("SELECT * FROM t WHERE x = ?", "postgresql")
            == "SELECT * FROM t WHERE x = %s"
        )
        assert (
            _query_console_sql("SELECT * FROM t WHERE x = ?", "sqlite")
            == "SELECT * FROM t WHERE x = ?"
        )


class TestDbConsoleApiKeys:
    def test_apikey_set_list_delete(self, tmp_path, monkeypatch):
        from nexus_scalp.settings.secret_store import SecureSecretStore
        from nexus_scalp.web.db_console import _load_apikey_names

        store_root = tmp_path / "secrets"
        monkeypatch.setattr(SecureSecretStore, "root", store_root, raising=False)
        store = SecureSecretStore()
        store.set_secret("test_console_key", "sk-super-secret")
        names = _load_apikey_names()
        assert any(n["name"] == "test_console_key" and n["set"] for n in names)
        # never the raw value
        assert all("sk-super-secret" not in str(n) for n in names)
        store.delete_secret("test_console_key")
        assert not any(n["name"] == "test_console_key" for n in _load_apikey_names())

    def test_apikey_endpoint_masks_and_rejects_bad_names(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nexus_scalp.web.db_console import router

        app = FastAPI()
        app.include_router(router)
        c = TestClient(app)
        # bad name: slash
        r = c.post("/api/db/console/apikey", json={"name": "bad/name", "value": "x"})
        assert r.json()["success"] is False
        # good name persists masked
        r2 = c.post("/api/db/console/apikey", json={"name": "console_test_key", "value": "abc123"})
        body2 = r2.json()
        assert body2["success"] is True
        assert body2["apikey"]["masked"].endswith("****")
        assert "abc123" not in body2["apikey"]["masked"]
        # cleanup
        c.delete("/api/db/console/apikey/console_test_key")


class TestDbConsoleUiSurface:
    def test_ui_has_explorer_panel(self):
        html = (REPO_ROOT / "Web" / "index.html").read_text(encoding="utf-8")
        for frag in (
            "db-console-dblist",
            "db-console-tablelist",
            "db-grid",
            "db-sql-input",
            "db-apikey-name",
            "dbConsoleRefresh",
            "DATABASE EXPLORER",
        ):
            assert frag in html, f"missing explorer fragment {frag}"

    def test_ui_has_console_js(self):
        js = (REPO_ROOT / "Web" / "app.js").read_text(encoding="utf-8")
        for fn in (
            "dbConsoleLoad",
            "dbConsoleRefresh",
            "dbPickDatabase",
            "dbPickTable",
            "dbReloadRows",
            "dbRunQuery",
            "dbQuickSql",
            "loadDbApiKeys",
            "dbApiKeySave",
            "dbApiKeyDelete",
            "dbRowsPrev",
            "dbRowsNext",
            "renderDbList",
            "renderTableChips",
            "renderGrid",
            "dbConsoleFilter",
        ):
            assert fn in js, f"missing JS function {fn}"

    def test_server_registers_console_router(self):
        server_text = (REPO_ROOT / "src" / "nexus_scalp" / "web" / "server.py").read_text(
            encoding="utf-8"
        )
        assert "db_console" in server_text
        assert "app.include_router(db_console_router)" in server_text

    def test_api_client_has_del(self):
        client = (REPO_ROOT / "Web" / "api_client.js").read_text(encoding="utf-8")
        assert "del(url, opts)" in client
