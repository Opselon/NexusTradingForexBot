#!/usr/bin/env python3
"""End-to-end DB fabric certification for the real NSE application.

This is deliberately stronger than a provider smoke test:
  * starts the real launcher on SQLite using a disposable persisted DB;
  * runs a bounded provider-neutral query corpus and records every statement;
  * configures PostgreSQL through the SAME HTTP endpoint the UI uses;
  * tests the real connection, previews, then starts the real UI migration;
  * waits for migration validation + automatic provider cutover;
  * restarts the real application on PostgreSQL and re-runs the corpus;
  * performs a bounded PostgreSQL -> SQLite reverse migration;
  * switches the persisted provider back to SQLite and restarts;
  * compares tables, columns and row counts after every transition;
  * emits a compact benchmark and machine-readable evidence.

No LIVE trading, MT5 orders, external news, or network market data are used.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MIN_QUERY_FLOOR = 200
MAX_TABLES = 50
QUERY_TIMEOUT = 8
STARTUP_TIMEOUT = 60

def find_free_port_pair(start_port: int) -> tuple[int, int]:
    """Find two simultaneously-free loopback ports for web + Go sidecar.

    The application boots FastAPI and a Go/API sidecar. Checking only the
    Python web port is insufficient: the derived ``port + 1`` can already be
    occupied by a stale sidecar or another local service. Probe and reserve
    both sockets together so a lifecycle phase never selects a partial pair.
    """
    import socket

    for web_port in range(start_port, start_port + 50):
        for go_port in (web_port + 1, web_port + 2, web_port + 3):
            sockets: list[socket.socket] = []
            try:
                for candidate in (web_port, go_port):
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(("127.0.0.1", candidate))
                    sockets.append(sock)
                return web_port, go_port
            except OSError:
                pass
            finally:
                for sock in sockets:
                    sock.close()
    raise RuntimeError(
        f"no free web/Go port pair in {start_port}-{start_port + 52}"
    )


def wait_port_free(port: int, timeout: float = 20.0) -> None:
    """Require the previous runtime instance to release its Python web port."""
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                pass
        except OSError:
            return
        time.sleep(0.25)
    raise RuntimeError(f"port {port} was not released within {timeout:.1f}s")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def http_json(
    base: str, method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = None
    headers = {
        "Authorization": "Bearer " + os.environ.get("NSE_WEB_AUTH_TOKEN", "ci-runtime-token")
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=QUERY_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace") or "{}")


def wait_ready(
    base: str,
    proc: subprocess.Popen[str] | None = None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Wait for both health surfaces and fail with the real startup evidence."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    last_health: Any = None
    last_status: Any = None
    last_error: str | None = None
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            tail = ""
            if log_path and log_path.exists():
                tail = log_path.read_text(
                    encoding="utf-8", errors="replace"
                )[-12000:]
            raise RuntimeError(
                f"application exited before readiness rc={proc.returncode}; "
                f"log_tail={tail}"
            )
        try:
            last_health = http_json(base, "GET", "/health")
            last_status = http_json(base, "GET", "/api/status")
            return {"health": last_health, "status": last_status}
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(1)
    tail = ""
    if log_path and log_path.exists():
        tail = log_path.read_text(
            encoding="utf-8", errors="replace"
        )[-12000:]
    raise RuntimeError(
        f"application did not become ready at {base}: "
        f"health={last_health!r} status={last_status!r} "
        f"error={last_error!r} log_tail={tail}"
    )


def start_app(
    port: int,
    settings_db: Path,
    audit_db: Path,
    provider: str,
    config_path: Path,
    go_port: int,
) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env.update(
        {
            "CI": "true",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "NEXUS_SETTINGS_DB": str(settings_db),
            "NEXUS_AUDIT_DB": str(audit_db),
            "NSE_WEB_HOST": "127.0.0.1",
            "NSE_WEB_PORT": str(port),
            "NSE_WEB_ACTUAL_PORT": str(port),
            "NSE_WEB_AUTH_DOTENV_DISABLE": "1",
            "NSE_GO_ADDR": f"127.0.0.1:{go_port}",
            "NSE_WEB_AUTH_TOKEN": "ci-runtime-token",
            "NSE_EXECUTION__MODE": "PAPER",
            "NSE_EXECUTION__SYMBOL": "XAUUSD",
            "NSE_PAPER_DATA__MODE": "SYNTHETIC",
            "NSE_NEWS__ENABLED": "false",
            "NSE_NEWS__ANALYSIS__ENABLED": "false",
            "NSE_NO_TELEGRAM": "1",
            "NSE_TELEGRAM__ENABLED": "false",
            "NSE_NO_BROWSER": "1",
            "NSE_NO_AUTO_INSTALL": "1",
            "NSE_DATABASE__PROVIDER": provider,
        }
    )
    command = [
        sys.executable,
        str(REPO_ROOT / "NexusTradingForexBot.py"),
        "--config",
        str(config_path),
        "--mode",
        "paper",
        "--symbol",
        "XAUUSD",
        "--no-animate",
    ]
    log = settings_db.parent / f"db-fabric-{provider}.log"
    handle = log.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    proc._nse_log_handle = handle  # type: ignore[attr-defined]
    return proc


def stop_app(proc: subprocess.Popen[str]) -> dict[str, Any]:
    """Gracefully stop the launcher and all child processes in its session."""
    forced = False
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            forced = True
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                forced = True
    
    # NSE starts the Go/API sidecar as a child process. The old harness only
    # waited for the Python parent, so the sidecar could survive a restart and
    # steal the Go port from the next provider lifecycle phase. Each launcher
    # gets its own session; drain that session after the parent exits.
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        pgid = None
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGTERM if forced else signal.SIGINT)
        except ProcessLookupError:
            pass
        except PermissionError:
            forced = True
    
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                break
            time.sleep(0.2)
        else:
            forced = True
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    
    if proc.poll() is None:
        proc.wait(timeout=10)
    handle = getattr(proc, "_nse_log_handle", None)
    if handle:
        handle.close()
    return {"exit_code": proc.returncode, "forced": forced or proc.returncode not in (0, None)}


def open_db(provider: str, sqlite_path: Path):
    if provider == "sqlite":
        conn = sqlite3.connect(sqlite_path, timeout=QUERY_TIMEOUT)
        conn.execute("PRAGMA busy_timeout=8000")
        return conn
    import psycopg

    from nexus_scalp.database.config import (
        PG_PASSWORD_SECRET_KEY,
        DatabaseConfig,
        build_postgres_url,
    )
    from nexus_scalp.settings.secret_store import SecureSecretStore

    password = os.environ.get("NSE_PG_TEST_PASSWORD", "nse_password_dev")
    SecureSecretStore().set_secret(PG_PASSWORD_SECRET_KEY, password)
    cfg = DatabaseConfig.for_postgres(
        domain="audit",
        host=os.environ.get("NSE_DATABASE__PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("NSE_DATABASE__PG_PORT", "5432")),
        database=os.environ.get("NSE_DATABASE__PG_DATABASE", "nse_audit"),
        username=os.environ.get("NSE_DATABASE__PG_USER", "nse_user"),
        ssl_mode="disable",
    )
    conn = psycopg.connect(build_postgres_url(cfg), connect_timeout=5)
    conn.autocommit = True
    conn.execute("SET statement_timeout = 8000")
    return conn


def tables_and_columns(provider: str, conn: Any) -> dict[str, list[str]]:
    if provider == "sqlite":
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        out: dict[str, list[str]] = {}
        for (name,) in rows:
            cols = conn.execute(
                'PRAGMA table_info("' + str(name).replace('"', '""') + '")'
            ).fetchall()
            out[str(name)] = [str(row[1]) for row in cols if len(row) > 1]
        return out
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
    ).fetchall()
    out = {}
    for (name,) in rows:
        cols = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
            (name,),
        ).fetchall()
        out[str(name)] = [str(row[0]) for row in cols]
    return out


def row_counts(conn: Any, schema: dict[str, list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in list(schema)[:MAX_TABLES]:
        try:
            row = conn.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()
            counts[table] = int(row[0] or 0)
        except Exception:
            counts[table] = -1
    return counts


def query_corpus(
    provider: str, conn: Any, schema: dict[str, list[str]], rounds: int = 2
) -> dict[str, Any]:
    """Run the same generated read-only corpus against either provider."""
    selected = list(schema)[:MAX_TABLES]
    cases: list[tuple[str, str]] = []
    for table in selected:
        qtable = '"' + table.replace('"', '""') + '"'
        cases.extend(
            [
                (f"count:{table}", f"SELECT COUNT(*) FROM {qtable}"),
                (f"sample:{table}", f"SELECT * FROM {qtable} LIMIT 5"),
                (f"order:{table}", f"SELECT * FROM {qtable} LIMIT 20"),
                (f"exists:{table}", f"SELECT EXISTS(SELECT 1 FROM {qtable})"),
            ]
        )
    while len(cases) < MIN_QUERY_FLOOR:
        table = selected[len(cases) % len(selected)] if selected else None
        if table:
            safe = table.replace('"', '""')
            cases.append((f"repeat-count:{len(cases)}:{table}", f'SELECT COUNT(*) FROM "{safe}"'))
        else:
            cases.append((f"select-one:{len(cases)}", "SELECT 1"))
    results: list[dict[str, Any]] = []
    for round_no in range(rounds):
        for label, sql in cases:
            started = time.perf_counter()
            try:
                cur = conn.execute(sql)
                rows = cur.fetchmany(5)
                results.append(
                    {
                        "round": round_no + 1,
                        "label": label,
                        "sql": sql,
                        "status": "PASS",
                        "rows": len(rows),
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "round": round_no + 1,
                        "label": label,
                        "sql": sql,
                        "status": "FAIL",
                        "error": f"{type(exc).__name__}: {exc}",
                        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    }
                )
    durations = sorted(float(x["duration_ms"]) for x in results)
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0.0
    return {
        "provider": provider,
        "query_count": len(results),
        "failed": sum(1 for x in results if x["status"] != "PASS"),
        "p95_ms": round(p95, 3),
        "mean_ms": round(sum(durations) / len(durations), 3) if durations else 0.0,
        "results": results,
    }


# Provider/runtime tables that are deliberately not part of the portable
# domain-data contract. They are inspected and queried, but they are allowed
# to differ in population or exist only on the destination provider.
EPHEMERAL_RUNTIME_TABLES = {
    "research_worker_heartbeat",
}
INTERNAL_MIGRATION_TABLES = {
    "_nse_migration_checkpoints",
}


def _canonical_columns(columns: list[str]) -> list[str]:
    # PostgreSQL folds unquoted identifiers to lowercase while SQLite preserves
    # source spelling. The application refers to both through quoted/translated
    # DDL, so parity here is semantic/case-insensitive, not byte-for-byte case.
    return [column.casefold() for column in columns]


def compare_states(left: dict[str, list[str]], right: dict[str, list[str]]) -> dict[str, Any]:
    left_tables = set(left)
    right_tables = set(right)
    required_tables = left_tables - INTERNAL_MIGRATION_TABLES
    missing_right = sorted(required_tables - right_tables)

    # PostgreSQL owns a private migration checkpoint table created by the
    # production migrator. It is evidence, not an application-domain table.
    extra_right = sorted((right_tables - left_tables) - INTERNAL_MIGRATION_TABLES)

    column_diffs = {
        table: {
            "left": left[table],
            "right": right[table],
            "left_canonical": _canonical_columns(left[table]),
            "right_canonical": _canonical_columns(right[table]),
        }
        for table in sorted(required_tables & right_tables)
        if _canonical_columns(left[table]) != _canonical_columns(right[table])
    }
    return {
        "missing_right": missing_right,
        "extra_right": extra_right,
        "column_diffs": column_diffs,
        "ephemeral_runtime_tables": sorted(EPHEMERAL_RUNTIME_TABLES & left_tables),
        "match": not missing_right and not extra_right and not column_diffs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-db", type=Path, required=True)
    parser.add_argument("--settings-db", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18090)
    args = parser.parse_args()
    args.evidence_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(REPO_ROOT / "src"))
    os.environ["NSE_WEB_AUTH_TOKEN"] = "ci-runtime-token"
    pg_password = os.environ.get("NSE_PG_TEST_PASSWORD", "nse_password_dev")
    pg = {
        "host": os.environ.get("NSE_DATABASE__PG_HOST", "127.0.0.1"),
        "port": int(os.environ.get("NSE_DATABASE__PG_PORT", "5432")),
        "database": os.environ.get("NSE_DATABASE__PG_DATABASE", "nse_audit"),
        "username": os.environ.get("NSE_DATABASE__PG_USER", "nse_user"),
    }

    evidence: dict[str, Any] = {"status": "FAIL", "phases": [], "errors": [], "warnings": []}
    sqlite_work = args.evidence_dir / "certification.sqlite"

    # The real application is launched with an isolated config copy. This
    # preserves the production configuration graph while disabling network-only
    # background sources that should not make DB certification depend on feeds.
    config_path = args.evidence_dir / "runtime.yaml"
    try:
        import yaml

        config_data = yaml.safe_load(
            (REPO_ROOT / "configs" / "base.yaml").read_text(encoding="utf-8")
        ) or {}
        if isinstance(config_data, dict):
            news_cfg = config_data.setdefault("news", {})
            if isinstance(news_cfg, dict):
                news_cfg["enabled"] = False
                analysis_cfg = news_cfg.setdefault("analysis", {})
                if isinstance(analysis_cfg, dict):
                    analysis_cfg["enabled"] = False
            hygiene_cfg = config_data.setdefault("database_hygiene", {})
            if isinstance(hygiene_cfg, dict):
                hygiene_cfg["enabled"] = False
        config_path.write_text(
            yaml.safe_dump(config_data, sort_keys=False),
            encoding="utf-8",
        )
    except Exception as exc:
        raise RuntimeError(f"failed to prepare isolated runtime config: {exc}") from exc

    port, go_port = find_free_port_pair(args.port)
    base = f"http://127.0.0.1:{port}"
    sqlite_work.write_bytes(args.sqlite_db.read_bytes())
    os.environ["NEXUS_AUDIT_DB"] = str(sqlite_work)
    os.environ["NEXUS_SETTINGS_DB"] = str(args.settings_db)
    args.settings_db.parent.mkdir(parents=True, exist_ok=True)

    proc: subprocess.Popen[str] | None = None
    try:
        proc = start_app(port, args.settings_db, sqlite_work, "sqlite", config_path, go_port)
        evidence["startup_sqlite"] = wait_ready(
            base, proc, args.settings_db.parent / "db-fabric-sqlite.log"
        )
        evidence["phases"].append({"phase": "sqlite_boot", "status": "PASS"})

        sqlite_conn = open_db("sqlite", sqlite_work)
        sqlite_schema = tables_and_columns("sqlite", sqlite_conn)
        sqlite_counts = row_counts(sqlite_conn, sqlite_schema)
        sqlite_queries = query_corpus("sqlite", sqlite_conn, sqlite_schema)
        sqlite_conn.close()
        evidence["sqlite_before"] = {
            "schema": sqlite_schema,
            "counts": sqlite_counts,
            "queries": sqlite_queries,
        }
        if sqlite_queries["failed"]:
            raise RuntimeError(f"SQLite query corpus failed: {sqlite_queries['failed']}")

        config_payload = {
            "provider": "postgresql",
            "domain": "audit",
            **pg,
            "ssl_mode": "disable",
            "password": pg_password,
            "confirm_password": pg_password,
        }
        status = http_json(base, "GET", "/api/db/manage/status")
        if status.get("success") is not True:
            raise RuntimeError("database management status failed")
        configured = http_json(base, "POST", "/api/db/manage/config", config_payload)
        if configured.get("success") is not True:
            raise RuntimeError(f"UI config path failed: {configured}")
        tested = http_json(base, "POST", "/api/db/manage/test-connection", config_payload)
        if tested.get("success") is not True:
            raise RuntimeError(f"UI PostgreSQL connection test failed: {tested}")
        preview = http_json(
            base,
            "POST",
            "/api/db/manage/preview",
            {**pg, "sqlite_path": str(sqlite_work)},
        )
        if preview.get("success") is not True:
            raise RuntimeError(f"UI migration preview failed: {preview}")
        evidence["phases"].append({"phase": "ui_config_test_preview", "status": "PASS"})

        started = http_json(
            base,
            "POST",
            "/api/db/manage/migrate",
            {
                **pg,
                "sqlite_path": str(sqlite_work),
                "password": pg_password,
                "confirm": True,
                "resume": True,
                "batch_size": 1000,
                "validate_checksums": True,
            },
        )
        if started.get("success") is not True:
            raise RuntimeError(f"UI migration did not start: {started}")

        deadline = time.monotonic() + 90
        migration_report: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            progress = http_json(base, "GET", "/api/db/manage/progress")
            if progress.get("done"):
                migration_report = progress.get("report") or {}
                break
            time.sleep(1)
        if not migration_report:
            raise RuntimeError("UI migration did not finish within 90s")
        if (
            migration_report.get("status") not in {"COMPLETE", "SUCCESS"}
            or migration_report.get("validation") != "PASSED"
        ):
            raise RuntimeError(f"UI migration validation failed: {migration_report}")
        evidence["migration_report"] = migration_report
        evidence["phases"].append({"phase": "ui_sqlite_to_postgres", "status": "PASS"})

        status_after_migration = http_json(base, "GET", "/api/db/manage/status")
        if status_after_migration.get("provider") != "postgresql":
            raise RuntimeError(
                f"provider was not cut over after validated migration: {status_after_migration}"
            )

        # Freeze all application writers before the direct parity snapshot.
        # The UI migration is intentionally performed while NSE is running, but
        # a live PAPER engine can legitimately add a signal/account snapshot
        # between the end of the copy and the parity read. Comparing an active
        # writer against a moving source would manufacture false corruption.
        stop_result = stop_app(proc)
        wait_port_free(port)
        wait_port_free(go_port)
        evidence["shutdown_sqlite"] = stop_result
        proc = None

        pg_conn = open_db("postgres", sqlite_work)
        pg_schema = tables_and_columns("postgres", pg_conn)
        pg_counts = row_counts(pg_conn, pg_schema)
        schema_compare = compare_states(sqlite_schema, pg_schema)
        count_diffs = {
            table: {"sqlite": sqlite_counts[table], "postgres": pg_counts.get(table)}
            for table in sqlite_counts
            if table not in EPHEMERAL_RUNTIME_TABLES
            and sqlite_counts[table] != pg_counts.get(table)
        }
        ephemeral_count_diffs = {
            table: {"sqlite": sqlite_counts[table], "postgres": pg_counts.get(table)}
            for table in sqlite_counts
            if table in EPHEMERAL_RUNTIME_TABLES and sqlite_counts[table] != pg_counts.get(table)
        }
        pg_queries = query_corpus("postgres", pg_conn, pg_schema)
        pg_conn.close()
        evidence["postgres_after"] = {
            "schema": pg_schema,
            "counts": pg_counts,
            "queries": pg_queries,
        }
        evidence["schema_compare"] = schema_compare
        evidence["count_diffs"] = count_diffs
        evidence["ephemeral_count_diffs"] = ephemeral_count_diffs
        evidence["parity_notes"] = [
            "Column parity is case-insensitive because SQLite preserves casing and PostgreSQL normalizes unquoted identifiers.",
            "research_worker_heartbeat is runtime state and is queried on both providers but excluded from persistent row-count equality.",
            "_nse_migration_checkpoints is migrator-owned metadata and is excluded from application-domain table parity.",
        ]
        if not schema_compare["match"] or count_diffs:
            raise RuntimeError(
                f"SQLite/PostgreSQL schema or count mismatch: {schema_compare}; {count_diffs}"
            )
        if pg_queries["failed"]:
            raise RuntimeError(f"PostgreSQL query corpus failed: {pg_queries['failed']}")
        evidence["phases"].append({"phase": "postgres_frozen_query_parity", "status": "PASS"})

        port, go_port = find_free_port_pair(port + 1)
        base = f"http://127.0.0.1:{port}"
        proc = start_app(
            port, args.settings_db, sqlite_work, "postgresql", config_path, go_port
        )
        evidence["startup_postgres_restart"] = wait_ready(
            base, proc, args.settings_db.parent / "db-fabric-postgresql.log"
        )
        post_restart = http_json(base, "GET", "/api/status")
        if not post_restart.get("success", True) and post_restart.get("status") not in (
            200,
            "ok",
            "OK",
        ):
            raise RuntimeError(f"PostgreSQL restart status failed: {post_restart}")
        evidence["phases"].append({"phase": "postgres_restart", "status": "PASS"})

        reverse = http_json(base, "POST", "/api/db/manage/reverse-migrate", {"batch_size": 1000})
        if reverse.get("success") is not True:
            raise RuntimeError(f"reverse migration failed: {reverse}")
        evidence["reverse_report"] = reverse.get("report")
        evidence["phases"].append({"phase": "postgres_to_sqlite_reverse", "status": "PASS"})

        switched_back = http_json(base, "POST", "/api/db/manage/provider", {"provider": "sqlite"})
        if switched_back.get("success") is not True:
            raise RuntimeError(f"provider switch back to SQLite failed: {switched_back}")
        stop_result = stop_app(proc)
        wait_port_free(port)
        wait_port_free(go_port)
        evidence["shutdown_postgres"] = stop_result
        proc = None
        port, go_port = find_free_port_pair(port + 1)
        base = f"http://127.0.0.1:{port}"
        proc = start_app(
            port, args.settings_db, sqlite_work, "sqlite", config_path, go_port
        )
        evidence["startup_sqlite_final"] = wait_ready(
            base, proc, args.settings_db.parent / "db-fabric-sqlite.log"
        )

        final_conn = open_db("sqlite", sqlite_work)
        final_schema = tables_and_columns("sqlite", final_conn)
        final_counts = row_counts(final_conn, final_schema)
        final_queries = query_corpus("sqlite", final_conn, final_schema)
        final_conn.close()
        final_schema_compare = compare_states(sqlite_schema, final_schema)
        final_count_diffs = {
            table: {"before": sqlite_counts[table], "after": final_counts.get(table)}
            for table in sqlite_counts
            if sqlite_counts[table] != final_counts.get(table)
        }
        evidence["sqlite_final"] = {
            "schema": final_schema,
            "counts": final_counts,
            "queries": final_queries,
        }
        evidence["final_schema_compare"] = final_schema_compare
        evidence["final_count_diffs"] = final_count_diffs
        if not final_schema_compare["match"] or final_count_diffs or final_queries["failed"]:
            raise RuntimeError("reverse-migration SQLite verification failed")

        evidence["benchmark"] = {
            "sqlite_before_p95_ms": sqlite_queries["p95_ms"],
            "postgres_p95_ms": pg_queries["p95_ms"],
            "sqlite_final_p95_ms": final_queries["p95_ms"],
            "query_floor_per_phase": MIN_QUERY_FLOOR * 2,
            "sqlite_query_count": sqlite_queries["query_count"],
            "postgres_query_count": pg_queries["query_count"],
        }
        evidence["status"] = "PASS"
    except Exception as exc:
        evidence["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        if proc is not None:
            evidence["shutdown"] = stop_app(proc)
        write_json(args.evidence_dir / "db_fabric_certification.json", evidence)

    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
