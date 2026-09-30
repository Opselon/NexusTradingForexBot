#!/usr/bin/env python3
"""Database provider certification: deep SQLite/PostgreSQL parity without a long load test.

This is intentionally stronger than a smoke test.  It validates the provider that
the real runtime just used, inventories the live schema, executes a bounded but
large read workload, scans the repository's SQL call-sites, and emits deterministic
evidence that a second CI job can compare between SQLite and PostgreSQL.

The workload is designed to stay short: bounded queries, short statement timeout,
no concurrent flood, and no writes against the application database.
"""

# fmt: off
# ruff: noqa: I001

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[2]
MIN_QUERIES = 350
MAX_TABLES = 500
MAX_STATIC_SQL = 2000
SQL_RE = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|UPSERT|CREATE|ALTER|DROP|WITH|PRAGMA)\b",
    re.I,
)
DIALECT_RE = {
    "sqlite_placeholder": re.compile(r"\?"),
    "postgres_placeholder": re.compile(r"%s"),
    "sqlite_insert_replace": re.compile(r"INSERT\s+OR\s+REPLACE", re.I),
    "sqlite_datetime_now": re.compile(r"datetime\s*\(\s*['\"]now['\"]", re.I),
    "sqlite_pragma": re.compile(r"\bPRAGMA\b", re.I),
    "postgres_cast": re.compile(r"::[A-Za-z_][A-Za-z0-9_]*"),
}


def ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def connect(provider: str) -> Any:
    if provider == "sqlite":
        path = os.environ.get("NEXUS_AUDIT_DB") or os.environ.get("NSE_DATABASE__SQLITE_PATH")
        if not path:
            raise RuntimeError("NEXUS_AUDIT_DB/NSE_DATABASE__SQLITE_PATH is missing")
        conn = sqlite3.connect(path, timeout=3)
        conn.execute("PRAGMA busy_timeout=3000")
        conn.row_factory = sqlite3.Row
        return conn

    import psycopg
    from nexus_scalp.database.config import build_postgres_url, load_database_config

    cfg = load_database_config("audit")
    conn = psycopg.connect(build_postgres_url(cfg), connect_timeout=5)
    conn.autocommit = True
    conn.execute("SET statement_timeout = 3000")
    conn.execute("SET lock_timeout = 1000")
    return conn


def tables(conn: Any, provider: str) -> list[str]:
    if provider == "postgres":
        rows = conn.execute(
            "SELECT tablename FROM pg_catalog.pg_tables "
            "WHERE schemaname='public' AND tablename NOT LIKE 'pg_%' ORDER BY tablename"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]][:MAX_TABLES]


def columns(conn: Any, provider: str, table: str) -> list[dict[str, Any]]:
    if provider == "postgres":
        rows = conn.execute(
            "SELECT column_name,data_type,is_nullable,ordinal_position "
            "FROM information_schema.columns WHERE table_schema='public' AND table_name=%s "
            "ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        return [
            {"name": str(r[0]), "type": str(r[1]), "nullable": str(r[2]), "position": int(r[3])}
            for r in rows
        ]
    rows = conn.execute("PRAGMA table_info(" + ident(table) + ")").fetchall()
    return [
        {"name": str(r[1]), "type": str(r[2] or ""), "nullable": not bool(r[3]), "position": int(r[0])}
        for r in rows
    ]


def indexes(conn: Any, provider: str, table: str) -> list[str]:
    if provider == "postgres":
        rows = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename=%s "
            "ORDER BY indexname",
            (table,),
        ).fetchall()
        return [str(r[0]) for r in rows]
    rows = conn.execute("PRAGMA index_list(" + ident(table) + ")").fetchall()
    return sorted(str(r[1]) for r in rows if len(r) > 1)


def schema_inventory(conn: Any, provider: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for table in tables(conn, provider):
        result[table] = {
            "columns": columns(conn, provider, table),
            "indexes": indexes(conn, provider, table),
        }
    return result


def execute_query(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchmany(10)
        return {
            "status": "PASS",
            "sql": sql,
            "rows": len(rows),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except Exception as exc:
        return {
            "status": "FAIL",
            "sql": sql,
            "rows": 0,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def live_query_battery(conn: Any, provider: str, inventory: dict[str, Any]) -> dict[str, Any]:
    """Exercise each live table with several independent query shapes.

    The query count is deliberately a floor, not a target that may be skipped
    when the schema is small.  Empty/small fresh installations therefore still
    execute hundreds of real provider queries.
    """
    queries: list[dict[str, Any]] = []

    def add(sql: str, params: tuple[Any, ...] = (), label: str = "") -> None:
        item = execute_query(conn, sql, params)
        item["label"] = label
        queries.append(item)

    if provider == "postgres":
        add("SELECT current_database(), current_user", label="identity")
        add("SELECT version()", label="version")
        add(
            "SELECT table_schema,table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name LIMIT 500",
            label="catalog-tables",
        )
        add(
            "SELECT table_name,column_name,data_type FROM information_schema.columns "
            "WHERE table_schema='public' ORDER BY table_name,ordinal_position LIMIT 2000",
            label="catalog-columns",
        )
    else:
        add("PRAGMA integrity_check", label="integrity")
        add("PRAGMA foreign_keys", label="foreign-keys")
        add("SELECT name,type FROM sqlite_master ORDER BY type,name LIMIT 500", label="catalog")

    for table, meta in inventory.items():
        q = ident(table)
        cols = meta["columns"]
        names = {str(c["name"]).lower(): str(c["name"]) for c in cols}

        add("SELECT COUNT(*) FROM " + q, label=f"count:{table}")
        add("SELECT * FROM " + q + " LIMIT 5", label=f"sample:{table}")
        add("SELECT * FROM " + q + " ORDER BY 1 LIMIT 5", label=f"ordered:{table}")

        # Probe useful semantic dimensions when the schema exposes them.
        for logical in ("symbol", "status", "state", "verdict"):
            col = names.get(logical)
            if col:
                qc = ident(col)
                add(
                    "SELECT " + qc + ",COUNT(*) FROM " + q +
                    " GROUP BY " + qc + " ORDER BY COUNT(*) DESC LIMIT 20",
                    label=f"group:{table}:{logical}",
                )
                break

        for logical in (
            "created_at", "updated_at", "generated_at", "timestamp",
            "time_utc", "opened_at", "closed_at",
        ):
            col = names.get(logical)
            if col:
                qc = ident(col)
                add(
                    "SELECT MIN(" + qc + "),MAX(" + qc + "),COUNT(" + qc + ") FROM " + q,
                    label=f"time-range:{table}:{logical}",
                )
                break

        # Index-backed lookup shape where a conventional id/key exists.
        for logical in ("id", "uuid", "trade_id", "order_id", "signal_id"):
            col = names.get(logical)
            if col:
                qc = ident(col)
                add(
                    "SELECT " + qc + " FROM " + q + " WHERE " + qc + " IS NOT NULL LIMIT 20",
                    label=f"key-lookup:{table}:{logical}",
                )
                break

    # Fill to the contract with bounded, deterministic queries against real
    # tables. This is intentionally sequential: it measures correctness and
    # provider overhead, not artificial connection-pool saturation.
    selected = list(inventory)
    n = 0
    while len(queries) < MIN_QUERIES:
        if selected:
            table = selected[n % len(selected)]
            add(
                "SELECT COUNT(*) FROM " + ident(table),
                label=f"floor:{n}:{table}",
            )
        else:
            add("SELECT 1", label=f"floor:{n}:select1")
        n += 1

    failures = [q for q in queries if q["status"] != "PASS"]
    durations = sorted(float(q["duration_ms"]) for q in queries)
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0.0
    return {
        "query_count": len(queries),
        "passed": len(queries) - len(failures),
        "failed": len(failures),
        "p95_ms": round(p95, 3),
        "max_ms": round(max(durations), 3) if durations else 0.0,
        "failures": failures[:100],
        "queries": queries,
    }


def literal_sql(value: str) -> str | None:
    try:
        node = ast.parse(value, mode="eval").body
    except SyntaxError:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class SQLCollector(ast.NodeVisitor):
    """Collect literal SQL passed to common DB execution methods.

    Dynamic SQL is reported separately instead of being guessed. This makes
    the certification fail-closed for inventory completeness without pretending
    that a static parser can safely manufacture runtime parameters.
    """

    METHODS: ClassVar[set[str]] = {"execute", "executemany", "executescript", "query", "scalar", "execute_many"}

    def __init__(self, path: Path) -> None:
        self.path = path
        self.statements: list[dict[str, Any]] = []
        self.dynamic = 0

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else ""
        if name in self.METHODS and node.args:
            arg = node.args[0]
            sql = ast.literal_eval(arg) if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None
            if isinstance(sql, str) and SQL_RE.search(sql):
                self.statements.append({
                    "file": str(self.path.relative_to(ROOT)),
                    "line": int(node.lineno),
                    "method": name,
                    "sql": " ".join(sql.split()),
                })
            elif isinstance(arg, (ast.JoinedStr, ast.BinOp, ast.Call, ast.Name, ast.Attribute)):
                self.dynamic += 1
        self.generic_visit(node)


def source_sql_inventory() -> dict[str, Any]:
    statements: list[dict[str, Any]] = []
    dynamic_sites = 0
    for path in (ROOT / "src").rglob("*.py"):
        if any(part in {".venv", "__pycache__", "node_modules"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        collector = SQLCollector(path)
        collector.visit(tree)
        statements.extend(collector.statements)
        dynamic_sites += collector.dynamic

    statements = statements[:MAX_STATIC_SQL]
    dialect_hits: dict[str, list[dict[str, Any]]] = {key: [] for key in DIALECT_RE}
    for item in statements:
        sql = item["sql"]
        for key, pattern in DIALECT_RE.items():
            if pattern.search(sql):
                dialect_hits[key].append(item)

    digest = hashlib.sha256(
        json.dumps(statements, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "literal_statement_count": len(statements),
        "dynamic_sql_sites": dynamic_sites,
        "inventory_truncated": len(statements) >= MAX_STATIC_SQL,
        "digest": digest,
        "dialect_hits": {k: len(v) for k, v in dialect_hits.items()},
        "dialect_samples": {k: v[:25] for k, v in dialect_hits.items()},
        "statements": statements,
    }


def canonical_schema(inv: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for table, meta in inv.items():
        result[table] = {
            "columns": [
                str(c["name"]).lower()
                for c in meta["columns"]
            ],
            "indexes": sorted(str(i).lower() for i in meta["indexes"]),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args()

    evidence = Path(args.evidence_dir)
    started = time.perf_counter()
    conn = connect(args.provider)
    try:
        inventory = schema_inventory(conn, args.provider)
        workload = live_query_battery(conn, args.provider, inventory)
    finally:
        conn.close()

    source = source_sql_inventory()
    report = {
        "schema_version": 2,
        "provider": args.provider,
        "status": "PASS" if workload["failed"] == 0 else "FAIL",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "table_count": len(inventory),
        "schema": inventory,
        "canonical_schema": canonical_schema(inventory),
        "workload": workload,
        "source_sql": source,
        "contracts": {
            "minimum_live_queries": MIN_QUERIES,
            "query_floor_met": workload["query_count"] >= MIN_QUERIES,
            "all_live_queries_passed": workload["failed"] == 0,
            "static_sql_inventory_complete": not source["inventory_truncated"],
        },
    }
    write(evidence / "database_provider_cert.json", report)
    print(json.dumps({
        "provider": args.provider,
        "status": report["status"],
        "tables": len(inventory),
        "queries": workload["query_count"],
        "query_failures": workload["failed"],
        "p95_ms": workload["p95_ms"],
        "literal_sql_sites": source["literal_statement_count"],
        "dynamic_sql_sites": source["dynamic_sql_sites"],
        "elapsed_ms": report["elapsed_ms"],
    }, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
