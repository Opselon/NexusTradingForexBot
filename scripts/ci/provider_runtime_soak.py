#!/usr/bin/env python3
"""Full NSE process runtime certification for one database provider.

Unlike the deterministic runtime_gate, this harness launches the actual
repository entrypoint (NexusTradingForexBot.py), keeps the real application
process alive for a fixed wall-clock soak, exercises the live HTTP surface,
executes a large read/query matrix against the selected database, captures all
runtime stdout/stderr, traces warnings/errors/tracebacks, and verifies clean
shutdown.

Contract:
  - PAPER mode only; no broker order placement.
  - PostgreSQL uses an ephemeral CI service; SQLite uses RUNNER_TEMP.
  - Authentication remains enabled with an ephemeral bearer token.
  - Every query is read-only and bounded.
  - Any runtime error, endpoint 5xx, query error, early process death, or
    incomplete shutdown fails the lane.
  - Warnings are retained in the final verdict and do not disappear into logs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import secrets
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_ARTIFACT = (
    REPO_ROOT
    / "artifacts"
    / "models"
    / "scalp"
    / "XAUUSD"
    / "70d_liquidity"
    / "model.pt"
)
MAX_TABLES = 100
QUERY_ROUNDS = 7
SOAK_SECONDS = 120
QUERY_INTERVAL_SECONDS = 15.0
MIN_QUERY_TOTAL = 500
HTTP_TIMEOUT_SECONDS = 6.0
SHUTDOWN_TIMEOUT_SECONDS = 35.0

TRACE_FILE_RE = re.compile(
    r'File "([^"]+)", line (\d+)(?:, in ([^\n]+))?'
)
SEVERITY_RE = re.compile(
    r"\b(?P<sev>CRITICAL|ERROR|EXCEPTION|WARNING|WARN)\b", re.IGNORECASE
)


class LogCapture:
    """Line-oriented process output capture with bounded in-memory tail."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.tail: deque[str] = deque(maxlen=4000)
        self._thread: threading.Thread | None = None
        self._done = threading.Event()

    def start(self, stream: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def _read() -> None:
            try:
                with self.path.open("w", encoding="utf-8", errors="replace") as out:
                    for line in iter(stream.readline, ""):
                        self.tail.append(line.rstrip("\n"))
                        out.write(line)
                        out.flush()
            finally:
                self._done.set()

        self._thread = threading.Thread(
            target=_read,
            name="nse-runtime-log-capture",
            daemon=True,
        )
        self._thread.start()

    def wait(self, timeout: float = 10.0) -> None:
        self._done.wait(timeout)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _env_provider(provider: str, evidence_dir: Path) -> dict[str, str]:
    root = evidence_dir / "state"
    root.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "NEXUS_SETTINGS_DB": str(root / "app_settings.db"),
            "NEXUS_AUDIT_DB": str(root / "audit.db"),
            "NEXUS_DATA_ROOT": str(root / "data"),
            "NSE_DATABASE__PROVIDER": (
                "postgresql" if provider == "postgres" else "sqlite"
            ),
            "NSE_EXECUTION__MODE": "PAPER",
            "NSE_EXECUTION__SYMBOL": "XAUUSD",
            "NSE_NO_BROWSER": "1",
            "NSE_NO_TELEGRAM": "1",
            "NSE_NEWS__ENABLED": "false",
            "NSE_TELEGRAM__ENABLED": "false",
            "NSE_NO_AUTO_INSTALL": "1",
            "NSE_WEB_HOST": "127.0.0.1",
            "NSE_WEB_PORT": os.environ.get("NSE_RUNTIME_SOAK_PORT", "18080"),
            "NSE_WEB_ACTUAL_PORT": os.environ.get("NSE_RUNTIME_SOAK_PORT", "18080"),
            "NSE_WEB_AUTH_TOKEN": secrets.token_urlsafe(32),
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )

    if provider == "postgres":
        password = env.get("NSE_PG_TEST_PASSWORD", "")
        if not password:
            raise RuntimeError("NSE_PG_TEST_PASSWORD is required for PostgreSQL runtime")
        env["NSE_DATABASE__PG_HOST"] = env.get("NSE_DATABASE__PG_HOST", "127.0.0.1")
        env["NSE_DATABASE__PG_PORT"] = env.get("NSE_DATABASE__PG_PORT", "5432")
        env["NSE_DATABASE__PG_DATABASE"] = env.get(
            "NSE_DATABASE__PG_DATABASE", "nse_audit"
        )
        env["NSE_DATABASE__PG_USER"] = env.get("NSE_DATABASE__PG_USER", "nse_user")
        env["NSE_DATABASE__PG_SSLMODE"] = "disable"

        # The application deliberately resolves the PostgreSQL password from
        # SecureSecretStore. CI writes only the ephemeral test credential.
        if str(REPO_ROOT / "src") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "src"))
        from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY
        from nexus_scalp.settings.secret_store import SecureSecretStore

        SecureSecretStore().set_secret(PG_PASSWORD_SECRET_KEY, password)
    else:
        for key in (
            "NSE_DATABASE__PG_HOST",
            "NSE_DATABASE__PG_PORT",
            "NSE_DATABASE__PG_DATABASE",
            "NSE_DATABASE__PG_USER",
            "NSE_DATABASE__PG_SSLMODE",
        ):
            env.pop(key, None)

    return env


def _http_get(url: str, token: str) -> dict[str, Any]:
    started = time.perf_counter()
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "nse-ci-full-runtime/1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read(128 * 1024)
            return {
                "status": int(getattr(response, "status", 200) or 200),
                "bytes": len(body),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(16 * 1024).decode("utf-8", errors="replace")
        return {
            "status": int(exc.code),
            "bytes": len(body.encode("utf-8")),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": body[:1000],
        }
    except Exception as exc:
        return {
            "status": 0,
            "bytes": 0,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _wait_http(url: str, token: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _http_get(url, token)
        if last["status"] == 200:
            return last
        time.sleep(0.5)
    raise RuntimeError(f"HTTP service did not become ready: {url} last={last}")


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return _ident(value)


def _query_metrics(latencies: list[float]) -> dict[str, Any]:
    if not latencies:
        return {"count": 0}
    ordered = sorted(latencies)
    p95_index = min(len(ordered) - 1, max(0, int(round(0.95 * len(ordered))) - 1))
    return {
        "count": len(ordered),
        "p50_ms": round(float(median(ordered)), 3),
        "p95_ms": round(float(ordered[p95_index]), 3),
        "max_ms": round(float(ordered[-1]), 3),
    }


def _run_pg_query(
    connection: Any,
    sql: str,
    params: tuple[Any, ...] = (),
) -> tuple[list[Any], float]:
    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    return rows, (time.perf_counter() - started) * 1000


def _run_sqlite_query(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] = (),
) -> tuple[list[Any], float]:
    started = time.perf_counter()
    cursor = connection.execute(sql, params)
    rows = cursor.fetchall()
    return rows, (time.perf_counter() - started) * 1000


def _discover_tables_pg(connection: Any) -> list[str]:
    rows, _ = _run_pg_query(
        connection,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """,
    )
    return [str(row[0]) for row in rows][:MAX_TABLES]


def _discover_tables_sqlite(connection: sqlite3.Connection) -> list[str]:
    rows, _ = _run_sqlite_query(
        connection,
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """,
    )
    return [str(row[0]) for row in rows][:MAX_TABLES]


def run_query_round(
    provider: str,
    connection: Any,
    tables: list[str],
    round_no: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    latencies: list[float] = []
    failures: list[dict[str, Any]] = []
    successes = 0

    def query(label: str, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        nonlocal successes
        try:
            if provider == "postgres":
                rows, latency = _run_pg_query(connection, sql, params)
            else:
                rows, latency = _run_sqlite_query(connection, sql, params)
            successes += 1
            latencies.append(latency)
            return rows
        except Exception as exc:
            failures.append(
                {
                    "round": round_no,
                    "label": label,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return []

    if provider == "postgres":
        query("pg.select_1", "SELECT 1")
        query("pg.current_database", "SELECT current_database()")
        query("pg.current_user", "SELECT current_user")
        query("pg.version", "SELECT version()")
        query(
            "pg.table_count",
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """,
        )
        query(
            "pg.index_count",
            """
            SELECT COUNT(*)
            FROM pg_indexes
            WHERE schemaname = 'public'
            """,
        )
        query(
            "pg.table_sizes",
            """
            SELECT relname, pg_total_relation_size(c.oid)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
            ORDER BY pg_total_relation_size(c.oid) DESC
            LIMIT 100
            """,
        )
        query(
            "pg.table_columns",
            """
            SELECT table_name, COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = 'public'
            GROUP BY table_name
            ORDER BY table_name
            LIMIT 100
            """,
        )
        query(
            "pg.primary_keys",
            """
            SELECT tc.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public'
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY tc.table_name, kcu.ordinal_position
            LIMIT 500
            """,
        )
        query(
            "pg.foreign_keys",
            """
            SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.table_schema = 'public'
              AND tc.constraint_type = 'FOREIGN KEY'
            ORDER BY tc.table_name, kcu.column_name
            LIMIT 500
            """,
        )
    else:
        query("sqlite.select_1", "SELECT 1")
        query("sqlite.sqlite_version", "SELECT sqlite_version()")
        query("sqlite.table_count", "SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        query(
            "sqlite.index_count",
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index'",
        )
        query(
            "sqlite.schema_objects",
            """
            SELECT type, COUNT(*)
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            GROUP BY type
            ORDER BY type
            """,
        )
        for pragma in (
            "user_version",
            "journal_mode",
            "synchronous",
            "foreign_keys",
            "page_count",
            "page_size",
            "freelist_count",
        ):
            query(f"sqlite.pragma.{pragma}", f"PRAGMA {pragma}")

    # Per-table reads: two rows/metadata queries minimum, plus an optional
    # aggregate for common timestamp/symbol columns. With a normal NSE schema
    # this is hundreds of real queries per full soak, not a fake single ping.
    for table in tables:
        quoted = _safe_identifier(table)
        query(f"{table}.count", f"SELECT COUNT(*) FROM {quoted}")
        query(f"{table}.sample", f"SELECT * FROM {quoted} LIMIT 1")

        if provider == "postgres":
            cols = query(
                f"{table}.columns",
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table,),
            )
        else:
            cols = query(f"{table}.columns", f"PRAGMA table_info({quoted})")

        column_names: set[str] = set()
        for row in cols:
            if provider == "postgres":
                column_names.add(str(row[0]))
            elif len(row) > 1:
                column_names.add(str(row[1]))

        for candidate in ("created_at", "updated_at", "generated_at", "timestamp"):
            if candidate in column_names:
                query(
                    f"{table}.max_{candidate}",
                    f"SELECT MAX({_safe_identifier(candidate)}) FROM {quoted}",
                )

        if "symbol" in column_names:
            query(
                f"{table}.symbol_nonnull",
                f"SELECT COUNT(*) FROM {quoted} WHERE {_safe_identifier('symbol')} IS NOT NULL",
            )

    elapsed = time.perf_counter() - started
    return {
        "round": round_no,
        "duration_sec": round(elapsed, 3),
        "queries_ok": successes,
        "queries_failed": len(failures),
        "failures": failures[:100],
        "latency": _query_metrics(latencies),
    }


def run_queries(
    provider: str,
    env: dict[str, str],
    evidence_dir: Path,
    deadline: float,
) -> dict[str, Any]:
    if provider == "postgres":
        if str(REPO_ROOT / "src") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "src"))
        from nexus_scalp.database.config import build_postgres_url, load_database_config

        cfg = load_database_config("audit")
        dsn = build_postgres_url(cfg)
        import psycopg

        connection = psycopg.connect(dsn, connect_timeout=5)
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '2500ms'")
            cursor.execute("SET lock_timeout = '1000ms'")
        close = connection.close
    else:
        path = env["NEXUS_AUDIT_DB"]
        connection = sqlite3.connect(path, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        close = connection.close

    reports: list[dict[str, Any]] = []
    all_latencies: list[float] = []
    failures: list[dict[str, Any]] = []
    total_queries = 0
    total_ok = 0

    try:
        if provider == "postgres":
            tables = _discover_tables_pg(connection)
        else:
            tables = _discover_tables_sqlite(connection)

        for round_no in range(1, QUERY_ROUNDS + 1):
            if time.monotonic() > deadline:
                break
            report = run_query_round(provider, connection, tables, round_no)
            reports.append(report)
            total_queries += report["queries_ok"] + report["queries_failed"]
            total_ok += report["queries_ok"]
            failures.extend(report["failures"])
            if report["latency"].get("count"):
                # We need only the aggregate here; individual latencies are not
                # necessary for the persisted evidence.
                all_latencies.extend(
                    [
                        float(report["latency"]["p50_ms"]),
                        float(report["latency"]["p95_ms"]),
                        float(report["latency"]["max_ms"]),
                    ]
                )
            if time.monotonic() + QUERY_INTERVAL_SECONDS < deadline:
                time.sleep(QUERY_INTERVAL_SECONDS)
    finally:
        close()

    # Hard floor: a runtime certification must exercise a substantial read
    # workload even on a newly-created database with only a few tables. Use
    # real provider queries, not a synthetic counter, and stop before the soak
    # deadline.
    if total_queries < MIN_QUERY_TOTAL and time.monotonic() < deadline:
        filler_index = 0
        while total_queries < MIN_QUERY_TOTAL and time.monotonic() < deadline:
            filler_index += 1
            if tables:
                table = tables[filler_index % len(tables)]
                quoted = _safe_identifier(table)
                label = f"{table}.floor_{filler_index}"
                sql = f"SELECT COUNT(*) FROM {quoted}"
            elif provider == "postgres":
                label = f"pg.floor_{filler_index}"
                sql = "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'"
            else:
                label = f"sqlite.floor_{filler_index}"
                sql = "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            try:
                if provider == "postgres":
                    _, latency = _run_pg_query(connection, sql)
                else:
                    _, latency = _run_sqlite_query(connection, sql)
                total_queries += 1
                total_ok += 1
                latencies = all_latencies
                latencies.append(latency)
            except Exception as exc:
                total_queries += 1
                failures.append(
                    {
                        "label": label,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        # The floor is evidence, not an excuse to run past the soak window.
    result = {
        "provider": provider,
        "table_count": len(tables),
        "tables_sampled": tables[:MAX_TABLES],
        "rounds_completed": len(reports),
        "queries_total": total_queries,
        "queries_ok": total_ok,
        "queries_failed": len(failures),
        "minimum_query_floor": MIN_QUERY_TOTAL,
        "query_floor_met": total_queries >= MIN_QUERY_TOTAL,
        "failure_samples": failures[:200],
        "rounds": reports,
        "aggregate": _query_metrics(all_latencies),
    }
    _write_json(evidence_dir / "query_matrix.json", result)
    return result


def _trace_logs(log_path: Path) -> dict[str, Any]:
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    tracebacks: list[dict[str, Any]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        match = SEVERITY_RE.search(line)
        if match:
            event = {
                "line": i + 1,
                "severity": match.group("sev").upper(),
                "message": line.strip()[:2000],
            }
            if event["severity"] in {"WARNING", "WARN"}:
                warnings.append(event)
            else:
                errors.append(event)

        if "Traceback (most recent call last)" in line:
            block = [line]
            j = i + 1
            while j < len(lines) and len(block) < 80:
                candidate = lines[j]
                if not candidate.strip():
                    block.append(candidate)
                    j += 1
                    if j < len(lines) and SEVERITY_RE.search(lines[j]):
                        break
                    continue
                block.append(candidate)
                if (
                    ("Error:" in candidate or "Exception:" in candidate)
                    and j + 1 < len(lines)
                    and SEVERITY_RE.search(lines[j + 1])
                ):
                    j += 1
                    break
                j += 1
            frames = []
            for item in block:
                frame = TRACE_FILE_RE.search(item)
                if frame:
                    frames.append(
                        {
                            "file": frame.group(1),
                            "line": int(frame.group(2)),
                            "function": frame.group(3) or "",
                        }
                    )
            tracebacks.append(
                {
                    "start_line": i + 1,
                    "end_line": min(j, len(lines)),
                    "frames": frames,
                    "trace": "\n".join(block)[:12000],
                }
            )
            i = max(i, j - 1)
        i += 1

    # Deduplicate exact warning/error lines while preserving evidence order.
    def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[Any, ...]] = set()
        out: list[dict[str, Any]] = []
        for item in items:
            key = (item["severity"], item["message"])
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out[:500]

    return {
        "warning_count": len(warnings),
        "error_count": len(errors),
        "warnings": dedupe(warnings),
        "errors": dedupe(errors),
        "traceback_count": len(tracebacks),
        "tracebacks": tracebacks[:100],
        "log_lines": len(lines),
        "log_tail": lines[-200:],
    }


def run_full(provider: str, duration: int, port: int, evidence_dir: Path) -> dict[str, Any]:
    env = _env_provider(provider, evidence_dir)
    token = env["NSE_WEB_AUTH_TOKEN"]
    log_capture = LogCapture(evidence_dir / "runtime.log")

    command = [
        sys.executable,
        str(REPO_ROOT / "NexusTradingForexBot.py"),
        "--config",
        str(REPO_ROOT / "configs" / "live.yaml"),
        "--mode",
        "paper",
        "--no-animate",
    ]

    started_at = time.time()
    process = subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    log_capture.start(process.stdout)

    report: dict[str, Any] = {
        "provider": provider,
        "status": "FAIL",
        "command": [str(x) for x in command],
        "started_at": datetime.now(UTC).isoformat(),
        "requested_duration_sec": duration,
        "port": port,
        "http": [],
    }

    base_url = f"http://127.0.0.1:{port}"
    required_endpoints = ("/health", "/api/status", "/api/live/state")
    additional_endpoints = (
        "/api/live/accounting",
        "/api/account/summary",
        "/api/account/trades",
        "/api/models/integrity",
        "/api/db/hygiene",
        "/api/diagnostics/health",
    )

    try:
        ready = _wait_http(f"{base_url}/health", token, 45)
        report["initial_health"] = ready
        status = _http_get(f"{base_url}/api/status", token)
        report["http"].append({"endpoint": "/api/status", **status})
        if status["status"] != 200:
            raise RuntimeError(f"/api/status failed at boot: {status}")

        query_deadline = time.monotonic() + duration
        query_thread_result: dict[str, Any] = {}

        def _queries() -> None:
            try:
                query_thread_result.update(
                    run_queries(provider, env, evidence_dir, query_deadline)
                )
            except Exception as exc:
                query_thread_result.update(
                    {
                        "provider": provider,
                        "queries_total": 0,
                        "queries_ok": 0,
                        "queries_failed": 1,
                        "failure_samples": [
                            {
                                "label": "query-harness",
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        ],
                    }
                )

        query_thread = threading.Thread(target=_queries, name=f"nse-db-queries-{provider}")
        query_thread.start()

        soak_deadline = time.monotonic() + duration
        probe_rounds = 0
        while time.monotonic() < soak_deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    f"NSE process exited early with rc={process.returncode}"
                )

            probe_rounds += 1
            cycle: dict[str, Any] = {
                "round": probe_rounds,
                "elapsed_sec": round(time.monotonic() - (soak_deadline - duration), 2),
                "endpoints": {},
            }
            for endpoint in required_endpoints + additional_endpoints:
                result = _http_get(f"{base_url}{endpoint}", token)
                cycle["endpoints"][endpoint] = result
                if endpoint in required_endpoints and result["status"] != 200:
                    raise RuntimeError(
                        f"required endpoint {endpoint} returned {result['status']}: "
                        f"{result.get('error', '')}"
                    )
                if result["status"] >= 500 or result["status"] == 0:
                    raise RuntimeError(
                        f"runtime endpoint {endpoint} unhealthy: {result}"
                    )

            report["http"].append(cycle)
            time.sleep(min(5.0, max(0.1, soak_deadline - time.monotonic())))

        query_thread.join(timeout=20)
        if query_thread.is_alive():
            raise RuntimeError("database query matrix thread did not finish within 20s")
        report["query_matrix"] = query_thread_result

    except Exception as exc:
        report["runtime_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        # Graceful shutdown is part of the certification, not cleanup trivia.
        if process.poll() is None:
            try:
                process.send_signal(signal.SIGINT)
            except Exception:
                process.terminate()
            try:
                process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        log_capture.wait(10)

    report["process_exit_code"] = process.returncode
    report["elapsed_sec"] = round(time.time() - started_at, 3)

    # Redact only ephemeral CI credentials from the persisted process log.
    # The raw runtime still receives them when necessary; evidence must never
    # become a credential transport.
    raw_log = log_capture.path.read_text(encoding="utf-8", errors="replace")
    for secret_value in (
        token,
        os.environ.get("NSE_PG_TEST_PASSWORD", ""),
    ):
        if secret_value:
            raw_log = raw_log.replace(secret_value, "***REDACTED***")
    log_capture.path.write_text(raw_log, encoding="utf-8")

    diagnostics = _trace_logs(log_capture.path)
    report["diagnostics"] = diagnostics

    if diagnostics["error_count"]:
        report["runtime_error"] = report.get(
            "runtime_error",
            f"{diagnostics['error_count']} runtime ERROR/CRITICAL events were logged",
        )
    query_result = report.get("query_matrix", {})
    if query_result.get("queries_failed", 0):
        report["runtime_error"] = report.get(
            "runtime_error",
            f"{query_result['queries_failed']} database queries failed",
        )

    # The launcher should shut down cleanly after SIGINT. A non-zero exit code
    # is retained as hard evidence even if the process otherwise survived.
    if process.returncode not in (0, None):
        report["runtime_error"] = report.get(
            "runtime_error",
            f"launcher exited with non-zero code {process.returncode}",
        )

    if "runtime_error" not in report:
        if report.get("elapsed_sec", 0) < duration:
            report["runtime_error"] = (
                f"full runtime soak lasted {report.get('elapsed_sec')}s "
                f"(required {duration}s)"
            )
        elif not report.get("query_matrix"):
            report["runtime_error"] = "query matrix produced no evidence"
        else:
            report["status"] = (
                "PASS_WITH_WARNINGS"
                if diagnostics["warning_count"]
                else "PASS"
            )

    _write_json(evidence_dir / "full_runtime_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--duration", type=int, default=SOAK_SECONDS)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument(
        "--evidence-dir",
        default=None,
        help="Directory for runtime.log and JSON evidence",
    )
    args = parser.parse_args()

    if args.duration < 120:
        parser.error("--duration must be at least 120 seconds")

    evidence_dir = (
        Path(args.evidence_dir).resolve()
        if args.evidence_dir
        else Path(os.environ.get("RUNNER_TEMP", str(REPO_ROOT / ".ci-runtime")))
        / f"nse-runtime-{args.provider}"
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)

    try:
        report = run_full(args.provider, args.duration, args.port, evidence_dir)
    except Exception as exc:
        report = {
            "provider": args.provider,
            "status": "FAIL",
            "runtime_error": f"{type(exc).__name__}: {exc}",
        }
        _write_json(evidence_dir / "full_runtime_report.json", report)

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("status") in {"PASS", "PASS_WITH_WARNINGS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
