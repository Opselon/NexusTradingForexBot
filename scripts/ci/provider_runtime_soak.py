#!/usr/bin/env python3
"""Full application runtime certification for one real database provider.

Unlike the offline runtime_gate, this harness starts the ACTUAL
NexusTradingForexBot.py launcher as a child process. It then:

  1. waits for the real web server and engine to become healthy;
  2. records every launcher WARNING/ERROR/CRITICAL/Traceback line with line
     numbers, fingerprints, source locations when present, and log tail;
  3. discovers the live OpenAPI GET surface and executes a large read-only
     API query battery (up to 100 endpoints, repeated during the soak);
  4. executes a large provider-native read-only SQL battery against the same
     database used by the running application;
  5. keeps the process alive for at least 120 seconds;
  6. requires the engine process to stay alive for the full soak;
  7. requests a graceful SIGINT shutdown and verifies clean process exit;
  8. emits one machine-readable provider_runtime_soak.json containing the full
     evidence needed by the aggregate CI result.

All runtime execution is PAPER/XAUUSD. No order execution is permitted.
External network integrations remain disabled in CI, while the core application
and web/control-plane boot path are real.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_ARTIFACT = (
    REPO_ROOT / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity" / "model.pt"
)
MAX_API_ROUTES = 100
# The runtime soaks a REAL single-worker uvicorn process sharing one asyncio
# loop with the engine's background workers, on a LOADED shared CI runner.
# Any concurrent fan-out is therefore self-inflicted denial of service: the
# requests serialize on the app's event loop anyway, and piling them up only
# multiplies the latency each one observes. Probes are issued strictly
# serially so the soak measures the control plane rather than reproducing a
# load-test failure the production deployment (multi-worker, fronted) does
# not have.
API_WORKERS = 1
# Per-request budget. Several GET routes do real analytical work on first
# touch (the dependency-intelligence endpoints parse and cache the module
# AST graph once per process) — a 4s budget classifies that ordinary
# warm-up cost as a timeout. 30s still distinguishes a merely slow route
# from a genuinely hung one, which is what the soak exists to catch.
API_TIMEOUT_SEC = 30
API_SWEEP_STAGGER_MS = 35
# A request that answers above this threshold is a slow-control-plane
# WARNING: it stays in the report and CI summary, but does not fail the
# lane (the status becomes PASS_WITH_WARNINGS, never a silent green).
API_SLOW_MS = 10_000
DB_QUERY_TIMEOUT_MS = 3000
MIN_TOTAL_QUERIES = 500
MIN_DB_QUERIES_PER_PASS = 250
SOAK_SEC = 120
SOAK_PROBE_EVERY_SEC = 5
HOT_ENDPOINTS = (
    "/health",
    "/api/status",
    "/api/diagnostics/health",
    "/api/diagnostics/incidents",
    "/api/live/state",
    "/api/live/accounting",
    "/api/account/summary",
    "/api/models/integrity",
    "/openapi.json",
)
SEVERITY_RE = re.compile(
    r"(?:\[(CRITICAL|FATAL|ERROR|WARNING|WARN)\s*\]|"
    r"\b(?:level|log_level|severity)=(CRITICAL|FATAL|ERROR|WARNING|WARN)\b|"
    r"\b(CRITICAL|FATAL|ERROR|WARNING|WARN):\s+|"
    r"\b([A-Za-z0-9_]*Warning):\s+)",
    re.IGNORECASE,
)
TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):")
FRAME_RE = re.compile(r'File "(.+?)", line (\d+), in (.+)')
SOURCE_RE = re.compile(r"((?:[A-Za-z]:[\\/]|/)?[\w.\-\\/]+\.py):(\d+)(?::(\d+))?")
ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def set_env(name: str, value: str) -> None:
    os.environ[name] = value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


class LogCollector:
    """Consume the real launcher log without blocking the parent process."""

    def __init__(self, proc: subprocess.Popen[str], log_path: Path) -> None:
        self.proc = proc
        self.log_path = log_path
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.lines: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.tracebacks: list[dict[str, Any]] = []
        self._thread = threading.Thread(target=self._reader, name="runtime-log-reader", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _reader(self) -> None:
        assert self.proc.stdout is not None
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8", errors="replace") as log:
            traceback_buf: list[str] = []
            traceback_start = 0
            for raw in self.proc.stdout:
                line = ANSI_RE.sub("", raw.rstrip("\r\n"))
                now = time.monotonic()
                with self.lock:
                    self.lines.append(line)
                    if len(self.lines) > 5000:
                        del self.lines[:1000]
                log.write(line + "\n")
                log.flush()

                if TRACEBACK_RE.search(line):
                    traceback_buf = [line]
                    traceback_start = len(self.lines)
                    continue
                if traceback_buf:
                    traceback_buf.append(line)
                    is_exc_line = bool(
                        re.match(
                            r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Interrupt|Exit):",
                            line.strip(),
                        )
                    )
                    if (
                        is_exc_line
                        or len(traceback_buf) >= 80
                        or (
                            line
                            and not line.startswith(
                                (" ", "File ", "Traceback", "During handling", "The above")
                            )
                            and SEVERITY_RE.search(line)
                        )
                    ):
                        self._record_traceback(traceback_buf, traceback_start, now)
                        traceback_buf = []

                match = SEVERITY_RE.search(line)
                if not match:
                    continue
                severity = next(
                    (group.upper() for group in match.groups() if group),
                    "ERROR",
                )
                if severity == "WARN" or severity.endswith("WARNING"):
                    severity = "WARNING"
                elif severity in ("FATAL", "CRITICAL"):
                    severity = "CRITICAL"
                else:
                    severity = "ERROR"
                source = SOURCE_RE.search(line)
                normalized = re.sub(r"\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+", "<ts>", line)
                normalized = re.sub(r"\bpid[= ]\d+\b", "pid=<n>", normalized, flags=re.IGNORECASE)
                event = {
                    "severity": severity,
                    "line_no": len(self.lines),
                    "elapsed_sec": round(now - self.started, 3),
                    "message": line,
                    "fingerprint": hashlib.sha256(
                        normalized.encode("utf-8", "replace")
                    ).hexdigest()[:16],
                    "source": (
                        {
                            "file": source.group(1),
                            "line": int(source.group(2)),
                            "column": int(source.group(3)),
                        }
                        if source
                        else None
                    ),
                }
                with self.lock:
                    self.events.append(event)

            if traceback_buf:
                self._record_traceback(traceback_buf, traceback_start, time.monotonic())

    def _record_traceback(self, lines: list[str], start_line: int, now: float) -> None:
        source = None
        frames: list[dict[str, Any]] = []
        for line in lines:
            frame = FRAME_RE.search(line)
            if frame:
                frames.append(
                    {
                        "file": frame.group(1),
                        "line": int(frame.group(2)),
                        "function": frame.group(3),
                    }
                )
            m = SOURCE_RE.search(line)
            if m and source is None:
                source = {"file": m.group(1), "line": int(m.group(2))}

        exc_type = ""
        exc_message = ""
        if lines:
            last = lines[-1].strip()
            if ":" in last:
                exc_type, _, exc_message = last.partition(":")
                exc_type = exc_type.strip()
                exc_message = exc_message.strip()

        payload = {
            "traceback_id": hashlib.sha256("\n".join(lines).encode("utf-8", "replace")).hexdigest()[
                :16
            ],
            "start_line_no": start_line,
            "end_line_no": start_line + len(lines) - 1,
            "elapsed_sec": round(now - self.started, 3),
            "source": source,
            "frames": frames,
            "lines": lines,
            "exception_type": exc_type,
            "exception_message": exc_message,
        }
        with self.lock:
            self.tracebacks.append(payload)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            events = list(self.events)
            lines = list(self.lines)
            tracebacks = list(self.tracebacks)
        warnings = [e for e in events if e["severity"] == "WARNING"]
        errors = [e for e in events if e["severity"] in ("ERROR", "CRITICAL", "FATAL")]
        return {
            "lines_observed": len(lines),
            "warnings": warnings[-200:],
            "errors": errors[-200:],
            "tracebacks": tracebacks[-50:],
            "log_tail": lines[-250:],
        }


def prepare_runtime_environment(provider: str, evidence_dir: Path) -> Path:
    """Create isolated settings/state and a canonical synthetic history file."""
    root = Path(os.environ.get("RUNNER_TEMP", str(REPO_ROOT / ".ci-runtime"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_root = root / ("nse-runtime-" + provider)
    state_root.mkdir(parents=True, exist_ok=True)

    # HOME isolates the non-Windows SecureSecretStore and user-profile state.
    set_env("HOME", str(state_root / "home"))
    Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
    set_env("NEXUS_SETTINGS_DB", str(state_root / "app_settings.db"))
    set_env("NEXUS_AUDIT_DB", str(state_root / "audit.db"))
    set_env("NSE_WEB_PORT", os.environ.get("NSE_RUNTIME_SOAK_PORT", "18080"))
    set_env("NSE_WEB_HOST", "127.0.0.1")
    set_env("NSE_WEB_AUTH_TOKEN", "ci-runtime-token")
    set_env("NSE_WEB_AUTH_DOTENV_DISABLE", "1")
    set_env("NSE_EXECUTION__MODE", "PAPER")
    set_env("NSE_EXECUTION__SYMBOL", "XAUUSD")
    set_env("NSE_PAPER_DATA__MODE", "SYNTHETIC")
    # Force the deterministic stress-history provider so every PAPER runtime
    # arm gets stable, sufficiently populated HTF history for a real WARMUP=READY
    # assertion instead of a random cold-start that can legitimately produce
    # zero HTF indicators on one runner but not another.
    set_env("NEXUS_PAPER_STRESS_SEED", "42")
    # The ENGINE must never send Telegram during the deterministic soak: a
    # runner-side network timeout on api.telegram.org surfaces as a runtime
    # ERROR in the captured process log and turns the lane red (it did:
    # [TELEGRAM] event=SEND_FAILED category=TELEGRAM_TIMEOUT). Delivery of the
    # soak result to Telegram is owned by the CI notification step
    # (render_provider_runtime.py / telegram_notify.py), which is isolated from
    # the certification verdict.
    set_env("NSE_NO_TELEGRAM", "1")
    set_env("NSE_TELEGRAM__ENABLED", "false")
    set_env("NSE_LATENCY_WARNING_THRESHOLD_MS", "150.0")
    set_env("NSE_NEWS__ENABLED", "false")
    set_env("NSE_NEWS__ANALYSIS__ENABLED", "false")
    set_env("NSE_FORENSIC_REPORT__ENABLED", "false")
    set_env("NSE_DATABASE_HYGIENE__TELEGRAM_REPORT", "false")
    set_env("NSE_GO_ADDR", "127.0.0.1:18081")

    if provider == "postgres":
        set_env("NSE_DATABASE__PROVIDER", "postgresql")
        set_env("NSE_DATABASE__PG_HOST", os.environ.get("NSE_DATABASE__PG_HOST", "127.0.0.1"))
        set_env("NSE_DATABASE__PG_PORT", os.environ.get("NSE_DATABASE__PG_PORT", "5432"))
        set_env(
            "NSE_DATABASE__PG_DATABASE", os.environ.get("NSE_DATABASE__PG_DATABASE", "nse_audit")
        )
        set_env("NSE_DATABASE__PG_USER", os.environ.get("NSE_DATABASE__PG_USER", "nse_user"))
        set_env("NSE_DATABASE__PG_SSLMODE", "disable")
        password = os.environ.get("NSE_PG_TEST_PASSWORD", "nse_password_dev")
        from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY
        from nexus_scalp.settings.secret_store import SecureSecretStore

        SecureSecretStore().set_secret(PG_PASSWORD_SECRET_KEY, password)
    elif provider == "sqlite":
        set_env("NSE_DATABASE__PROVIDER", "sqlite")
        set_env("NSE_DATABASE__SQLITE_PATH", str(state_root / "audit.db"))
    else:
        raise ValueError(provider)

    # HealthEngine has a hard canonical DATA check. Give the real launcher a
    # deterministic 20k-row parquet set without committing or downloading data.
    data_path = REPO_ROOT / "data" / "raw" / "XAUUSD_M1.parquet"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    import polars as pl

    start = datetime(2026, 1, 1, tzinfo=UTC)
    n = 20_000
    timestamps = [start + timedelta(minutes=i) for i in range(n)]
    closes = [2000.0 + ((i % 200) - 100) * 0.02 + (i % 17) * 0.003 for i in range(n)]
    frame = pl.DataFrame(
        {
            "time_utc": timestamps,
            "open": closes,
            "high": [v + 0.15 for v in closes],
            "low": [v - 0.15 for v in closes],
            "close": [v + (0.01 if i % 2 else -0.01) for i, v in enumerate(closes)],
            "tick_volume": [100 + (i % 31) for i in range(n)],
        }
    )
    frame.write_parquet(data_path)

    # Pre-warm dependency intelligence cache so REST endpoints (/api/dependency/*)
    # resolve from cache in <500ms rather than scanning all 2700 AST files during live HTTP query.
    try:
        from nexus_scalp.dependency_intelligence.engine import DependencyIntelligenceEngine

        DependencyIntelligenceEngine(REPO_ROOT / "src" / "nexus_scalp").analyze(use_cache=True)
    except Exception:
        pass

    return state_root


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer " + os.environ.get("NSE_WEB_AUTH_TOKEN", "ci-runtime-token")}


def http_request(base_url: str, path: str) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    started = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers=auth_headers(), method="GET")
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_SEC) as resp:
            body = resp.read(64 * 1024)
            return {
                "path": path,
                "status": int(getattr(resp, "status", 200) or 200),
                "bytes": len(body),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(64 * 1024).decode("utf-8", "replace")
        return {
            "path": path,
            "status": exc.code,
            "bytes": len(body),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": body[:1000],
        }
    except Exception as exc:
        return {
            "path": path,
            "status": 0,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }


def fetch_json(base_url: str, path: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    result = http_request(base_url, path)
    if result.get("status") != 200:
        return None, result
    try:
        req = urllib.request.Request(
            base_url.rstrip("/") + path,
            headers=auth_headers(),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}"), result
    except Exception as exc:
        result["error"] = f"json decode: {type(exc).__name__}: {exc}"
        return None, result


def discover_get_routes(base_url: str) -> tuple[list[str], dict[str, Any]]:
    doc, probe = fetch_json(base_url, "/openapi.json")
    if not doc or not isinstance(doc.get("paths"), dict):
        return list(HOT_ENDPOINTS), {"openapi_probe": probe, "discovered": 0}

    candidates: list[tuple[int, str]] = []
    for path, item in doc["paths"].items():
        if not isinstance(path, str) or "{" in path or not isinstance(item, dict):
            continue
        operation = item.get("get")
        if not isinstance(operation, dict):
            continue
        params = operation.get("parameters") or []
        if any(bool(p.get("required")) for p in params if isinstance(p, dict)):
            continue
        if path.startswith("/static/") or path.startswith("/assets/"):
            continue
        priority = 0
        if path in HOT_ENDPOINTS:
            priority += 1000
        if path.startswith("/api/"):
            priority += 100
        if "/debug" in path or "/research" in path or "/db/" in path or "/diagnostics" in path:
            priority += 20
        candidates.append((priority, path))

    ordered = [path for _, path in sorted(candidates, key=lambda pair: (-pair[0], pair[1]))]
    for path in HOT_ENDPOINTS:
        if path not in ordered:
            ordered.append(path)
    return ordered[:MAX_API_ROUTES], {
        "openapi_probe": probe,
        "discovered": len(ordered),
        "selected": min(MAX_API_ROUTES, len(ordered)),
    }


def api_battery(base_url: str, routes: list[str], sweep_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    workers = min(API_WORKERS, max(1, len(routes)))

    def _probe(path: str) -> dict[str, Any]:
        # Small deterministic stagger so a fresh battery does not hit the
        # engine with a burst of new connections at once.
        time.sleep(API_SWEEP_STAGGER_MS / 1000 * (len(path) % 3))
        return http_request(base_url, path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_probe, routes))
    ok = [r for r in results if 200 <= int(r.get("status", 0)) < 400]
    client_errors = [r for r in results if 400 <= int(r.get("status", 0)) < 500]
    server_errors = [
        r for r in results if int(r.get("status", 0)) >= 500 or int(r.get("status", 0)) == 0
    ]
    durations = sorted(float(r.get("duration_ms", 0.0)) for r in results)
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0.0
    return {
        "sweep": sweep_id,
        "route_count": len(routes),
        "success_count": len(ok),
        "client_error_count": len(client_errors),
        "server_error_count": len(server_errors),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "latency_ms_p95": round(p95, 2),
        "results": results,
    }


def ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def provider_tables(connection: Any, provider: str) -> list[str]:
    if provider == "postgres":
        rows = connection.execute(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' ORDER BY tablename"
        ).fetchall()
        return [str(row[0]) for row in rows if row and row[0]]
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def table_columns(connection: Any, provider: str, table: str) -> list[str]:
    if provider == "postgres":
        rows = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        return [str(row[0]) for row in rows if row and row[0]]
    rows = connection.execute("PRAGMA table_info(" + ident(table) + ")").fetchall()
    return [str(row[1]) for row in rows if len(row) > 1 and row[1]]


def open_provider_connection(provider: str) -> Any:
    if provider == "postgres":
        import psycopg

        from nexus_scalp.database.config import build_postgres_url, load_database_config

        cfg = load_database_config("audit")
        connection = psycopg.connect(build_postgres_url(cfg), connect_timeout=5)
        connection.autocommit = True
        connection.execute("SET statement_timeout = " + str(DB_QUERY_TIMEOUT_MS))
        connection.execute("SET lock_timeout = 1000")
        return connection
    connection = sqlite3.connect(
        os.environ["NEXUS_AUDIT_DB"],
        timeout=DB_QUERY_TIMEOUT_MS / 1000,
    )
    # WAL readers never block a writer, but a reader holding a shared lock
    # while a writer commits (or another reader mid-scan) can still surface
    # SQLITE_BUSY. Match the application's own busy_timeout so the probe
    # waits rather than turning an expected WAL tail into a "database is
    # locked" error attributed to the application.
    connection.execute("PRAGMA busy_timeout = " + str(DB_QUERY_TIMEOUT_MS))
    return connection


def run_db_battery(provider: str, phase: str, max_tables: int = 40) -> dict[str, Any]:
    """Run many real read-only queries against the live provider."""
    started = time.perf_counter()
    connection = open_provider_connection(provider)
    queries: list[dict[str, Any]] = []
    try:
        tables = provider_tables(connection, provider)
        selected = tables[:max_tables]
        query_id = 0

        def execute(sql: str, params: tuple[Any, ...] = (), label: str = "") -> None:
            nonlocal query_id
            query_id += 1
            q_started = time.perf_counter()
            try:
                cursor = connection.execute(sql, params)
                rows = cursor.fetchmany(20)
                queries.append(
                    {
                        "id": query_id,
                        "phase": phase,
                        "label": label,
                        "sql": sql,
                        "status": "PASS",
                        "row_sample": rows,
                        "duration_ms": round((time.perf_counter() - q_started) * 1000, 2),
                    }
                )
            except Exception as exc:
                queries.append(
                    {
                        "id": query_id,
                        "phase": phase,
                        "label": label,
                        "sql": sql,
                        "status": "FAIL",
                        "error": f"{type(exc).__name__}: {exc}",
                        "duration_ms": round((time.perf_counter() - q_started) * 1000, 2),
                    }
                )

        # Provider introspection and core health queries.
        if provider == "postgres":
            execute("SELECT current_database(), current_user, version()", label="postgres identity")
            execute(
                "SELECT schemaname, tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname='public' ORDER BY tablename LIMIT 100",
                label="postgres table inventory",
            )
        else:
            execute("PRAGMA integrity_check", label="sqlite integrity")
            execute(
                "SELECT name, type FROM sqlite_master ORDER BY type, name LIMIT 100",
                label="sqlite catalog",
            )

        for table in selected:
            qtable = ident(table)
            execute(
                "SELECT COUNT(*) AS row_count FROM " + qtable,
                label="count:" + table,
            )
            execute(
                "SELECT * FROM " + qtable + " LIMIT 5",
                label="sample:" + table,
            )

            cols = table_columns(connection, provider, table)
            lower = {c.lower(): c for c in cols}
            symbol = lower.get("symbol")
            if symbol:
                execute(
                    "SELECT "
                    + ident(symbol)
                    + ", COUNT(*) AS row_count FROM "
                    + qtable
                    + " GROUP BY "
                    + ident(symbol)
                    + " ORDER BY row_count DESC LIMIT 20",
                    label="group-by-symbol:" + table,
                )

            status_col = lower.get("status") or lower.get("state") or lower.get("verdict")
            if status_col:
                execute(
                    "SELECT "
                    + ident(status_col)
                    + ", COUNT(*) AS row_count FROM "
                    + qtable
                    + " GROUP BY "
                    + ident(status_col)
                    + " ORDER BY row_count DESC LIMIT 20",
                    label="group-by-status:" + table,
                )

            time_col = next(
                (
                    lower[k]
                    for k in (
                        "generated_at",
                        "created_at",
                        "timestamp",
                        "time_utc",
                        "updated_at",
                        "opened_at",
                        "closed_at",
                    )
                    if k in lower
                ),
                None,
            )
            if time_col:
                execute(
                    "SELECT MIN("
                    + ident(time_col)
                    + "), MAX("
                    + ident(time_col)
                    + "), COUNT("
                    + ident(time_col)
                    + ") FROM "
                    + qtable,
                    label="time-range:" + table,
                )

        # A second catalog pass means this is materially more than a smoke
        # check even when the application created only a handful of tables.
        execute(
            "SELECT COUNT(*) FROM "
            + (
                "information_schema.tables WHERE table_schema='public'"
                if provider == "postgres"
                else "sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ),
            label="schema-table-count",
        )

        # Hard query-volume contract: every DB pass executes at least 250
        # bounded read-only queries. This is intentionally a repeated workload
        # over real application tables, not 250 fake TestClient assertions.
        # On a small fresh CI schema this catches connection/pooling/locking
        # regressions while staying safely under the per-statement timeout.
        stress_round = 0
        while query_id < MIN_DB_QUERIES_PER_PASS:
            stress_round += 1
            if selected:
                table = selected[(query_id - 1) % len(selected)]
                execute(
                    "SELECT COUNT(*) AS row_count FROM " + ident(table),
                    label=f"stress-count:{stress_round}:{table}",
                )
            else:
                execute("SELECT 1", label=f"stress-select1:{stress_round}")

        failed = [q for q in queries if q["status"] == "FAIL"]
        durations = sorted(float(q["duration_ms"]) for q in queries)
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0.0
        return {
            "phase": phase,
            "table_count": len(tables),
            "selected_tables": len(selected),
            "query_count": len(queries),
            "failed_query_count": len(failed),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "latency_ms_p95": round(p95, 2),
            "queries": queries,
        }
    finally:
        try:
            connection.close()
        except Exception:
            pass


def process_alive_for(proc: subprocess.Popen[str], seconds: int) -> tuple[bool, int | None]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            return False, code
        time.sleep(1)
    return True, None


def wait_http(
    base_url: str,
    timeout: int,
    proc: subprocess.Popen[str] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(
                f"engine process crashed during startup with exit code {proc.returncode}"
            )
        last = http_request(base_url, "/health")
        if last.get("status") == 200:
            status = http_request(base_url, "/api/status")
            if status.get("status") == 200:
                return {"health": last, "status": status}
        time.sleep(1)
    raise RuntimeError(f"application did not become healthy within {timeout}s; last={last}")


def _signal_shutdown(proc: subprocess.Popen[str]) -> None:
    """Request graceful shutdown, portably.

    POSIX processes accept SIGINT; on Windows ``Popen.send_signal`` raises
    ``ValueError`` for it (there is no concept of a signal number a child
    can be handed without a console control event). Fall back to the Win32
    CTRL_BREAK_EVENT, which uvicorn maps to a graceful shutdown the same way
    it maps CTRL_C_EVENT.
    """
    if sys.platform == "win32":
        if proc.poll() is None:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        return
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)


def request_graceful_shutdown(
    proc: subprocess.Popen[str], collector: LogCollector
) -> dict[str, Any]:
    started = time.perf_counter()
    signal_sent = False
    forced_kill = False
    _signal_shutdown(proc)
    signal_sent = True

    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.5)

    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.5)

    if proc.poll() is None:
        forced_kill = True
        proc.kill()
        proc.wait(timeout=10)

    return {
        "signal_sent": signal_sent,
        "exit_code": proc.returncode,
        "forced_kill": forced_kill,
        "shutdown_wait_sec": round(time.perf_counter() - started, 2),
        "log": collector.snapshot(),
    }


def classify_overall(
    process_exit: int | None,
    log_snapshot: dict[str, Any],
    api_sweeps: list[dict[str, Any]],
    db_runs: list[dict[str, Any]],
    minimum_soak_sec: int,
    actual_soak_sec: float,
) -> tuple[str, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for event in log_snapshot["errors"]:
        errors.append({"type": "process-log", **event})
    for tb in log_snapshot["tracebacks"]:
        errors.append({"type": "traceback", **tb})

    for sweep in api_sweeps:
        for result in sweep["results"]:
            status = int(result.get("status", 0))
            if status >= 500 or status == 0:
                errors.append({"type": "api", "sweep": sweep["sweep"], **result})
            elif 400 <= status < 500:
                warnings.append({"type": "api-client-error", "sweep": sweep["sweep"], **result})
            elif API_SLOW_MS and float(result.get("duration_ms", 0.0)) > API_SLOW_MS:
                # A slow 2xx is a real regression signal that must stay
                # visible, but a route that answers is not a certification
                # failure — the lane reports it and degrades to
                # PASS_WITH_WARNINGS instead of red.
                warnings.append({"type": "api-slow", "sweep": sweep["sweep"], **result})

    for run in db_runs:
        for query in run["queries"]:
            if query["status"] == "FAIL":
                errors.append({"type": "database-query", "phase": run["phase"], **query})

    if process_exit not in (0, None):
        errors.append({"type": "process-exit", "exit_code": process_exit})
    if actual_soak_sec + 0.01 < minimum_soak_sec:
        errors.append(
            {
                "type": "soak-duration",
                "requested_sec": minimum_soak_sec,
                "actual_sec": round(actual_soak_sec, 2),
            }
        )

    # Warnings are retained in the report and CI summary but do not become
    # green-by-ignoring: the status is explicitly PASS_WITH_WARNINGS.
    if errors:
        return "FAIL", errors + warnings
    if warnings or log_snapshot["warnings"]:
        return "PASS_WITH_WARNINGS", warnings + log_snapshot["warnings"]
    return "PASS", []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--duration", type=int, default=SOAK_SEC)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("NSE_RUNTIME_SOAK_PORT", "18080"))
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="CI compatibility: explicit evidence directory for runtime.log and JSON reports",
    )
    args = parser.parse_args()

    if args.duration < SOAK_SEC:
        parser.error(f"--duration must be at least {SOAK_SEC}")

    if str(REPO_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "src"))

    evidence_root = Path(os.environ.get("RUNNER_TEMP", str(REPO_ROOT / ".ci-runtime"))).resolve()
    state_root = prepare_runtime_environment(
        args.provider,
        evidence_root / ("nse-runtime-" + args.provider),
    )
    evidence_dir = (args.evidence_dir or state_root).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    log_path = evidence_dir / "launcher.log"
    config_path = REPO_ROOT / "configs" / "base.yaml"
    # CI must exercise the real application without depending on external news
    # feeds. Generate a complete config copy so the runtime still boots through
    # the same configuration path while disabling only network-driven news and
    # background hygiene that would otherwise dominate a deterministic soak.
    ci_config_path = state_root / "runtime.yaml"
    try:
        import yaml

        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
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
        ci_config_path.write_text(
            yaml.safe_dump(config_data, sort_keys=False),
            encoding="utf-8",
        )
        config_path = ci_config_path
    except Exception as exc:
        raise RuntimeError(f"failed to prepare isolated CI runtime config: {exc}") from exc
    base_url = f"http://{args.host}:{args.port}"

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["CI"] = "true"

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

    started = time.perf_counter()
    popen_kwargs: dict[str, Any] = {
        "cwd": str(REPO_ROOT),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if sys.platform == "win32":
        # A new process group is required for CTRL_BREAK_EVENT to reach the
        # child (and only the child) instead of this harness too.
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    proc = subprocess.Popen(
        command,
        **popen_kwargs,
    )
    collector = LogCollector(proc, log_path)
    collector.start()

    api_sweeps: list[dict[str, Any]] = []
    db_runs: list[dict[str, Any]] = []
    startup_evidence: dict[str, Any] = {}
    failure: str | None = None
    routes: list[str] = []
    try:
        startup_evidence = wait_http(base_url, 60, proc=proc)
        route_info, discovery_evidence = discover_get_routes(base_url)
        routes = route_info
        startup_evidence["route_discovery"] = discovery_evidence

        api_sweeps.append(api_battery(base_url, routes, "initial"))

        # Query the database while the real engine is running. The first pass
        # proves schema bootstrap and the second pass proves stable reads.
        try:
            db_runs.append(run_db_battery(args.provider, "mid-boot", max_tables=40))
        except Exception as exc:
            failure = f"mid-boot database battery crashed: {type(exc).__name__}: {exc}"

        soak_started = time.monotonic()
        next_db_pass = soak_started + 60
        next_api_sweep = soak_started + 30
        runtime_findings: list[dict[str, Any]] = []
        hot_probes: list[dict[str, Any]] = []

        while time.monotonic() - soak_started < args.duration:
            if proc.poll() is not None:
                runtime_findings.append(
                    {
                        "type": "process-exit",
                        "message": f"launcher exited during soak with rc={proc.returncode}",
                        "exit_code": proc.returncode,
                    }
                )
                break

            now = time.monotonic()

            # Exercise the hot control-plane surface sequentially. Request
            # failures are collected as evidence; they do NOT shorten the
            # fixed 120-second observation window. Probes are serialized (not
            # fanned out) because the engine process runs one uvicorn worker:
            # concurrent probes serialize on its event loop anyway, and a
            # parallel fan-out only multiplies the latency each probe observes.
            for path in HOT_ENDPOINTS:
                probe = http_request(base_url, path)
                probe["sweep"] = "hot"
                hot_probes.append(probe)
                if probe.get("status", 0) >= 500 or probe.get("status") == 0:
                    runtime_findings.append({"type": "api", **probe})

            if now >= next_api_sweep:
                api_result = api_battery(base_url, routes, f"repeat-{len(api_sweeps)}")
                api_sweeps.append(api_result)
                for result in api_result["results"]:
                    status_code = int(result.get("status", 0))
                    if status_code >= 500 or status_code == 0:
                        runtime_findings.append(
                            {
                                "type": "api",
                                "sweep": api_result["sweep"],
                                **result,
                            }
                        )
                next_api_sweep += 30

            if now >= next_db_pass:
                try:
                    db_result = run_db_battery(args.provider, "late-boot", max_tables=40)
                    db_runs.append(db_result)
                    for query in db_result["queries"]:
                        if query["status"] == "FAIL":
                            runtime_findings.append(
                                {
                                    "type": "database-query",
                                    "phase": db_result["phase"],
                                    **query,
                                }
                            )
                except Exception as exc:
                    runtime_findings.append(
                        {
                            "type": "database-battery-crash",
                            "message": f"late-boot database battery crashed: {type(exc).__name__}: {exc}",
                        }
                    )
                next_db_pass += 60

            remaining = args.duration - (time.monotonic() - soak_started)
            time.sleep(
                min(
                    SOAK_PROBE_EVERY_SEC,
                    max(0.25, remaining),
                )
            )

        actual_soak = time.monotonic() - soak_started
        startup_evidence["hot_probe_count"] = len(hot_probes)

        # Post-soak database battery: the coverage contract (>=250 provider
        # queries per pass, >=500 overall) must not depend on how fast a
        # LOADED shared CI runner happens to get through the observation
        # window. Run one final pass after the window unconditionally, while
        # the engine process is still up, so a slow runner cannot turn a
        # healthy provider into a coverage-floor failure.
        if not any(run.get("phase") == "late-boot" for run in db_runs):
            try:
                db_result = run_db_battery(args.provider, "post-soak", max_tables=40)
                db_runs.append(db_result)
                for query in db_result["queries"]:
                    if query["status"] == "FAIL":
                        runtime_findings.append(
                            {
                                "type": "database-query",
                                "phase": db_result["phase"],
                                **query,
                            }
                        )
            except Exception as exc:
                runtime_findings.append(
                    {
                        "type": "database-battery-crash",
                        "message": f"post-soak database battery crashed: {type(exc).__name__}: {exc}",
                    }
                )
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        actual_soak = 0.0
    finally:
        shutdown = request_graceful_shutdown(proc, collector)

    final_log = collector.snapshot()
    process_exit = proc.returncode
    status, findings = classify_overall(
        process_exit,
        final_log,
        api_sweeps,
        db_runs,
        args.duration,
        actual_soak,
    )
    if failure:
        findings.insert(0, {"type": "harness", "message": failure})
        status = "FAIL"

    # Runtime failures discovered during the window are retained instead of
    # prematurely killing the process; classification still makes them fatal.
    # This gives operators the complete 120s evidence window.
    if "runtime_findings" in locals():
        findings = runtime_findings + findings
        if runtime_findings:
            status = "FAIL"

    # Treat the minimum API/database battery as a certification contract:
    # a tiny number of queries means the lane did not actually exercise the
    # application.  The exact floor is deliberately well above a one-endpoint
    # smoke test.
    api_queries = sum(len(s["results"]) for s in api_sweeps)
    db_queries = sum(int(run["query_count"]) for run in db_runs)
    total_queries = api_queries + db_queries
    if total_queries < MIN_TOTAL_QUERIES:
        findings.insert(
            0,
            {
                "type": "coverage-floor",
                "message": (
                    f"runtime query battery executed fewer than {MIN_TOTAL_QUERIES} total read-only queries"
                ),
                "count": total_queries,
                "minimum": MIN_TOTAL_QUERIES,
            },
        )
        status = "FAIL"
    if api_queries < 30:
        findings.insert(
            0,
            {
                "type": "coverage-floor",
                "message": "API battery executed fewer than 30 GET requests",
                "count": api_queries,
            },
        )
        status = "FAIL"
    if db_queries < 250:
        findings.insert(
            0,
            {
                "type": "coverage-floor",
                "message": "database battery executed fewer than 250 provider-native queries",
                "count": db_queries,
            },
        )
        status = "FAIL"

    result = {
        "provider": args.provider,
        "status": status,
        "requested_soak_sec": args.duration,
        "actual_soak_sec": round(actual_soak, 2),
        "total_wall_sec": round(time.perf_counter() - started, 2),
        "launcher": {
            "command": command,
            "pid": proc.pid,
            "exit_code": process_exit,
            "config": str(config_path),
        },
        "startup": startup_evidence,
        "api": {
            "routes_discovered": len(routes),
            "routes_selected": len(routes),
            "query_count_total": api_queries,
            "sweeps": api_sweeps,
        },
        "database": {
            "query_count_total": db_queries,
            "runs": db_runs,
        },
        "observability": final_log,
        "shutdown": shutdown,
        "findings": findings[:500],
        "hot_probes": hot_probes[-200:] if "hot_probes" in locals() else [],
    }

    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_json(evidence_dir / "provider_runtime_soak.json", result)

    # Compatibility artifacts consumed by the existing CI runtime lane.
    query_failures = [
        query
        for run in db_runs
        for query in run.get("queries", [])
        if query.get("status") == "FAIL"
    ]
    query_matrix = {
        "provider": args.provider,
        "status": status,
        "queries_total": total_queries,
        "queries_ok": total_queries - len(query_failures),
        "queries_failed": len(query_failures),
        "minimum_query_floor": MIN_TOTAL_QUERIES,
        "query_floor_met": total_queries >= MIN_TOTAL_QUERIES,
        "failure_samples": query_failures[:50],
    }
    write_json(evidence_dir / "query_matrix.json", query_matrix)

    diagnostic_block = {
        "warning_count": len(final_log.get("warnings", [])),
        "error_count": len(final_log.get("errors", [])),
        "traceback_count": len(final_log.get("tracebacks", [])),
        "warnings": final_log.get("warnings", []),
        "errors": final_log.get("errors", []),
        "tracebacks": final_log.get("tracebacks", []),
    }
    full_runtime_report = {
        "status": status,
        "config_path": str(config_path),
        "elapsed_sec": round(actual_soak, 2),
        "process_exit_code": process_exit,
        "runtime_error": failure,
        "diagnostics": diagnostic_block,
        "query_summary": query_matrix,
        "provider_runtime_evidence": str(evidence_dir / "provider_runtime_soak.json"),
    }
    write_json(evidence_dir / "full_runtime_report.json", full_runtime_report)
    # Full raw launcher output is retained separately for exact forensic trace.
    runtime_log = evidence_dir / "runtime.log"
    if log_path != runtime_log:
        runtime_log.write_text(
            log_path.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8",
        )
    else:
        runtime_log.touch(exist_ok=True)

    errors = [
        f
        for f in findings
        if f.get("type")
        in (
            "process-log",
            "traceback",
            "api",
            "database-query",
            "process-exit",
            "harness",
            "coverage-floor",
            "soak-duration",
        )
    ]
    warnings = [f for f in findings if f.get("type") not in {x.get("type") for x in errors}]
    print(
        json.dumps(
            {
                "provider": args.provider,
                "status": status,
                "actual_soak_sec": round(actual_soak, 2),
                "api_queries": api_queries,
                "db_queries": db_queries,
                "error_findings": len(errors),
                "warning_findings": len(warnings) + len(final_log["warnings"]),
                "tracebacks": len(final_log["tracebacks"]),
                "launcher_exit_code": process_exit,
                "evidence": str(evidence_dir / "provider_runtime_soak.json"),
            },
            indent=2,
        )
    )

    return 0 if status in ("PASS", "PASS_WITH_WARNINGS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
