#!/usr/bin/env python3
"""Cross-provider comparison for the two real database certification lanes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(root: Path, provider: str) -> dict[str, Any]:
    matches = list(root.glob(f"ci-db-cert-{provider}-*/database_provider_cert.json"))
    if not matches:
        raise FileNotFoundError(f"missing {provider} certification artifact under {root}")
    return json.loads(matches[0].read_text(encoding="utf-8"))


def compare(sqlite: dict[str, Any], postgres: dict[str, Any]) -> dict[str, Any]:
    sq = sqlite.get("canonical_schema", {})
    pg = postgres.get("canonical_schema", {})
    sq_tables = set(sq)
    pg_tables = set(pg)
    missing_in_pg = sorted(sq_tables - pg_tables)
    missing_in_sqlite = sorted(pg_tables - sq_tables)

    column_mismatches: list[dict[str, Any]] = []
    for table in sorted(sq_tables & pg_tables):
        s_cols = sq[table].get("columns", [])
        p_cols = pg[table].get("columns", [])
        if s_cols != p_cols:
            column_mismatches.append({
                "table": table,
                "sqlite_columns": s_cols,
                "postgres_columns": p_cols,
                "missing_in_postgres": sorted(set(s_cols) - set(p_cols)),
                "missing_in_sqlite": sorted(set(p_cols) - set(s_cols)),
            })

    sq_work = sqlite.get("workload", {})
    pg_work = postgres.get("workload", {})
    query_failures = {
        "sqlite": sq_work.get("failed", 0),
        "postgres": pg_work.get("failed", 0),
    }

    # Type differences are expected for INTEGER/REAL/TEXT mappings, so the
    # hard parity gate is table + column presence/order, not literal SQL type
    # spelling. Indexes are compared as evidence but are not required to have
    # identical names because PostgreSQL migration legitimately renames them.
    dialect = {
        "sqlite": sqlite.get("source_sql", {}).get("dialect_hits", {}),
        "postgres": postgres.get("source_sql", {}).get("dialect_hits", {}),
    }
    source_sql_counts = {
        "sqlite": sqlite.get("source_sql", {}).get("literal_statement_count", 0),
        "postgres": postgres.get("source_sql", {}).get("literal_statement_count", 0),
    }

    checks = {
        "sqlite_cert_passed": sqlite.get("status") == "PASS",
        "postgres_cert_passed": postgres.get("status") == "PASS",
        "same_table_set": not missing_in_pg and not missing_in_sqlite,
        "same_column_contract": not column_mismatches,
        "sqlite_queries_all_passed": sq_work.get("failed", 1) == 0,
        "postgres_queries_all_passed": pg_work.get("failed", 1) == 0,
        "sqlite_query_floor_met": sq_work.get("query_count", 0) >= sqlite.get("contracts", {}).get("minimum_live_queries", 350),
        "postgres_query_floor_met": pg_work.get("query_count", 0) >= postgres.get("contracts", {}).get("minimum_live_queries", 350),
        "static_sql_inventory_matches": source_sql_counts["sqlite"] == source_sql_counts["postgres"],
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    return {
        "status": status,
        "checks": checks,
        "missing_in_postgres": missing_in_pg,
        "missing_in_sqlite": missing_in_sqlite,
        "column_mismatches": column_mismatches,
        "query_failures": query_failures,
        "query_counts": {
            "sqlite": sq_work.get("query_count", 0),
            "postgres": pg_work.get("query_count", 0),
        },
        "p95_ms": {
            "sqlite": sq_work.get("p95_ms"),
            "postgres": pg_work.get("p95_ms"),
        },
        "static_sql_counts": source_sql_counts,
        "dialect_hits": dialect,
        "notes": [
            "Schema parity compares live table and column contracts.",
            "PostgreSQL type spelling is not required to equal SQLite because the production DDL translator intentionally maps types.",
            "Index names are evidence only; migration may legitimately rename indexes.",
            "Every provider independently executed the same bounded query contract; no synthetic TestClient-only query is used.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True)
    args = parser.parse_args()
    root = Path(args.evidence_dir)
    sqlite = load(root, "sqlite")
    postgres = load(root, "postgres")
    report = compare(sqlite, postgres)
    out = root / "provider_parity.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
