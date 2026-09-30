#!/usr/bin/env python3
"""Real SQLite <-> PostgreSQL migration certification.

Uses the production migrator classes, not a fake copy routine.  The source
database contains representative operational/financial rows, constraints and
indexes.  The test proves:

SQLite -> PostgreSQL -> SQLite
    schema creation, row counts, values, identity continuity, financial sums,
    deterministic digests, reverse-copy, and migration idempotency.

All objects are uniquely named ci_cert_* so the disposable CI PostgreSQL
database is never confused with application tables.  No production database
is touched.
"""

# fmt: off

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def digest_sqlite(path: Path, tables: list[str]) -> dict[str, Any]:
    conn = sqlite3.connect(path)
    try:
        result: dict[str, Any] = {}
        for table in tables:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
            rows = conn.execute(
                f'SELECT {", ".join(chr(34)+c.replace(chr(34), chr(34)*2)+chr(34) for c in cols)} '
                f'FROM "{table}" ORDER BY 1'
            ).fetchall()
            h = hashlib.sha256()
            for row in rows:
                h.update(json.dumps(list(row), default=str, separators=(",", ":")).encode())
                h.update(b"\n")
            result[table] = {
                "columns": cols,
                "rows": len(rows),
                "digest": h.hexdigest(),
                "pnl": (
                    float(conn.execute(f'SELECT COALESCE(SUM("pnl"),0) FROM "{table}"').fetchone()[0])
                    if "pnl" in cols else None
                ),
            }
        return result
    finally:
        conn.close()


def build_source(path: Path) -> list[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    tables = [
        "ci_cert_orders",
        "ci_cert_ledger",
        "ci_cert_snapshots",
        "ci_cert_events",
    ]
    try:
        conn.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE ci_cert_orders (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                volume REAL NOT NULL,
                price REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_ci_cert_orders_symbol ON ci_cert_orders(symbol);

            CREATE TABLE ci_cert_ledger (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                pnl REAL NOT NULL,
                commission REAL NOT NULL,
                swap REAL NOT NULL,
                net_pnl REAL NOT NULL,
                note TEXT,
                FOREIGN KEY(order_id) REFERENCES ci_cert_orders(id)
            );
            CREATE INDEX idx_ci_cert_ledger_order ON ci_cert_ledger(order_id);

            CREATE TABLE ci_cert_snapshots (
                id INTEGER PRIMARY KEY,
                balance REAL NOT NULL,
                equity REAL NOT NULL,
                peak_equity REAL NOT NULL,
                captured_at TEXT NOT NULL
            );

            CREATE TABLE ci_cert_events (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        for i in range(1, 81):
            conn.execute(
                "INSERT INTO ci_cert_orders VALUES (?,?,?,?,?,?)",
                (i, "XAUUSD" if i % 2 else "EURUSD", "CLOSED" if i % 3 else "OPEN",
                 0.01 + (i % 7) * 0.01, 2000.0 + i * 0.13, f"2026-09-30T00:{i%60:02d}:00Z"),
            )
            pnl = ((i % 11) - 5) * 1.25
            conn.execute(
                "INSERT INTO ci_cert_ledger VALUES (?,?,?,?,?,?,?)",
                (i, i, pnl, 0.11 + i * 0.001, -0.02, pnl - 0.11 - i * 0.001 - 0.02, f"row-{i}"),
            )
            conn.execute(
                "INSERT INTO ci_cert_snapshots VALUES (?,?,?,?,?)",
                (i, 30000 + i * 3.5, 29950 + i * 3.7, 30100 + i * 3.8,
                 f"2026-09-30T00:{i%60:02d}:30Z"),
            )
            conn.execute(
                "INSERT INTO ci_cert_events VALUES (?,?,?,?)",
                (i, "SIGNAL" if i % 2 else "FILL", json.dumps({"i": i, "ok": i % 3 != 0}),
                 f"2026-09-30T00:{i%60:02d}:45Z"),
            )
        conn.commit()
        return tables
    finally:
        conn.close()


def postgres_config() -> Any:
    from nexus_scalp.database.config import DatabaseConfig, PG_PASSWORD_SECRET_KEY
    from nexus_scalp.settings.secret_store import SecureSecretStore

    password = os.environ.get("NSE_PG_TEST_PASSWORD", "nse_password_dev")
    SecureSecretStore().set_secret(PG_PASSWORD_SECRET_KEY, password)
    return DatabaseConfig.for_postgres(
        domain="audit",
        host=os.environ.get("NSE_DATABASE__PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("NSE_DATABASE__PG_PORT", "5432")),
        database=os.environ.get("NSE_DATABASE__PG_DATABASE", "nse_audit"),
        username=os.environ.get("NSE_DATABASE__PG_USER", "nse_user"),
        password_secret=PG_PASSWORD_SECRET_KEY,
        ssl_mode="disable",
    )


def run() -> dict[str, Any]:
    from nexus_scalp.database.config import DatabaseConfig
    from nexus_scalp.database.migrate_engine import MigrationOptions, SqliteToPostgresMigrator
    from nexus_scalp.database.migrate_reverse import PostgresToSqliteMigrator

    root = Path(os.environ.get("RUNNER_TEMP", str(ROOT / ".ci-runtime"))) / "migration-cert"
    root.mkdir(parents=True, exist_ok=True)
    source_path = root / "source.sqlite"
    roundtrip_path = root / "roundtrip.sqlite"
    tables = build_source(source_path)
    source_digest = digest_sqlite(source_path, tables)

    # The reverse migrator requires destination tables to exist. Copy the
    # schema from the original SQLite source before reverse validation.
    src_conn = sqlite3.connect(source_path)
    dst_conn = sqlite3.connect(roundtrip_path)
    try:
        for table in tables:
            ddl = src_conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            dst_conn.execute(ddl)
            idx_rows = src_conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                "AND sql IS NOT NULL", (table,)
            ).fetchall()
            for (idx_sql,) in idx_rows:
                dst_conn.execute(idx_sql)
        dst_conn.commit()
    finally:
        src_conn.close()
        dst_conn.close()

    sqlite_cfg = DatabaseConfig.for_sqlite("audit", path=str(source_path))
    roundtrip_cfg = DatabaseConfig.for_sqlite("audit", path=str(roundtrip_path))
    pg_cfg = postgres_config()
    opts = MigrationOptions(
        batch_size=64,
        resume=True,
        force_restart=False,
        tables=tables,
        validate_checksums=True,
    )

    started = time.perf_counter()
    forward = SqliteToPostgresMigrator(sqlite_cfg, pg_cfg, opts)
    preview = forward.preview()
    forward_report = forward.run()
    if forward_report.status != "COMPLETE" or forward_report.validation != "PASSED":
        return {
            "status": "FAIL",
            "stage": "sqlite_to_postgres",
            "preview": preview,
            "forward": forward_report.to_dict(),
            "source_digest": source_digest,
        }

    reverse = PostgresToSqliteMigrator(pg_cfg, roundtrip_cfg, opts)
    reverse_report = reverse.run()
    if reverse_report.status != "SUCCESS" or reverse_report.validation != "PASS":
        return {
            "status": "FAIL",
            "stage": "postgres_to_sqlite",
            "forward": forward_report.to_dict(),
            "reverse": reverse_report.to_dict(),
            "source_digest": source_digest,
        }

    roundtrip_digest = digest_sqlite(roundtrip_path, tables)
    digest_match = all(
        source_digest[t]["rows"] == roundtrip_digest[t]["rows"]
        and source_digest[t]["digest"] == roundtrip_digest[t]["digest"]
        and source_digest[t]["pnl"] == roundtrip_digest[t]["pnl"]
        for t in tables
    )

    # Idempotency: rerun the forward migration against the already populated
    # destination. The production migrator must not duplicate or corrupt rows.
    second = SqliteToPostgresMigrator(sqlite_cfg, pg_cfg, opts)
    second_report = second.run()
    idempotent = second_report.status == "COMPLETE" and second_report.validation == "PASSED"

    elapsed = round((time.perf_counter() - started) * 1000, 2)
    return {
        "status": "PASS" if digest_match and idempotent else "FAIL",
        "stage": "complete",
        "elapsed_ms": elapsed,
        "source_tables": tables,
        "source_digest": source_digest,
        "roundtrip_digest": roundtrip_digest,
        "digest_match": digest_match,
        "idempotent_forward_pass": idempotent,
        "preview": preview,
        "forward": forward_report.to_dict(),
        "reverse": reverse_report.to_dict(),
        "second_forward": second_report.to_dict(),
        "contracts": {
            "forward_migration_passed": forward_report.status == "COMPLETE"
            and forward_report.validation == "PASSED",
            "reverse_migration_passed": reverse_report.status == "SUCCESS"
            and reverse_report.validation == "PASS",
            "financial_values_preserved": digest_match,
            "idempotent_rerun_passed": idempotent,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args()
    report = run()
    out = Path(args.evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "database_migration_cert.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report.get("status"),
        "stage": report.get("stage"),
        "elapsed_ms": report.get("elapsed_ms"),
        "digest_match": report.get("digest_match"),
        "idempotent_forward_pass": report.get("idempotent_forward_pass"),
    }, indent=2))
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
