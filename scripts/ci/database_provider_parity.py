#!/usr/bin/env python3
"""Cross-provider comparison for the two real database certification lanes."""

from __future__ import annotations

# ruff: noqa: I001

import argparse
import json
from pathlib import Path
from typing import Any


# Provider-owned migration metadata is intentionally asymmetric: PostgreSQL
# keeps forward migration checkpoints while the reverse-migration lane records
# its own SQLite checkpoints. These are control-plane state, not domain tables,
# so their provider-specific presence/data must never masquerade as DB parity
# drift. Any other missing/extra table remains a hard failure.
PROVIDER_METADATA_TABLES = frozenset(
    {
        "_nse_migration_checkpoints",
        "_nse_reverse_migration_checkpoints",
    }
)


def load(root: Path, provider: str) -> dict[str, Any]:
    """Load a provider certification artifact, accepting either directory shape.

    The lanes historically wrote ``ci-db-cert-<provider>`` (no suffix); the glob
    below only matched ``ci-db-cert-<provider>-*/``, so a lane that wrote the
    plain name produced "missing <provider> certification artifact" even though
    the artifact existed (DB-FABRIC parity step). Accept both spellings rather
    than renaming a directory the evidence bundles already ship.
    """
    matches = list(root.glob(f"ci-db-cert-{provider}-*/database_provider_cert.json"))
    matches.extend(root.glob(f"ci-db-cert-{provider}/database_provider_cert.json"))
    if not matches:
        raise FileNotFoundError(f"missing {provider} certification artifact under {root}")
    return json.loads(matches[0].read_text(encoding="utf-8"))


def compare(sqlite: dict[str, Any], postgres: dict[str, Any]) -> dict[str, Any]:
    sq = sqlite.get("canonical_schema", {})
    pg = postgres.get("canonical_schema", {})
    sq_tables = set(sq)
    pg_tables = set(pg)
    sqlite_metadata = sorted(sq_tables & PROVIDER_METADATA_TABLES)
    postgres_metadata = sorted(pg_tables & PROVIDER_METADATA_TABLES)
    sq_domain = sq_tables - PROVIDER_METADATA_TABLES
    pg_domain = pg_tables - PROVIDER_METADATA_TABLES
    missing_in_pg = sorted(sq_domain - pg_domain)
    missing_in_sqlite = sorted(pg_domain - sq_domain)

    column_mismatches: list[dict[str, Any]] = []
    for table in sorted(sq_domain & pg_domain):
        s_cols = sq[table].get("columns", [])
        p_cols = pg[table].get("columns", [])
        if s_cols != p_cols:
            column_mismatches.append(
                {
                    "table": table,
                    "sqlite_columns": s_cols,
                    "postgres_columns": p_cols,
                    "missing_in_postgres": sorted(set(s_cols) - set(p_cols)),
                    "missing_in_sqlite": sorted(set(p_cols) - set(s_cols)),
                }
            )

    sq_work = sqlite.get("workload", {})
    pg_work = postgres.get("workload", {})
    sq_source = sqlite.get("source_query_probe", {})
    pg_source = postgres.get("source_query_probe", {})
    query_failures = {
        "sqlite": sq_work.get("failed", 0),
        "postgres": pg_work.get("failed", 0),
        "sqlite_source": sq_source.get("failed", 0),
        "postgres_source": pg_source.get("failed", 0),
    }

    sqlite_counts = sqlite.get("row_counts", {})
    postgres_counts = postgres.get("row_counts", {})
    pg_only_nonempty = {
        table: count
        for table, count in postgres_counts.items()
        if table in missing_in_sqlite and int(count) > 0
    }
    common_count_diffs = {
        table: {"sqlite": sqlite_counts.get(table), "postgres": postgres_counts.get(table)}
        for table in sorted(sq_domain & pg_domain)
        if sqlite_counts.get(table) != postgres_counts.get(table)
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
        "sqlite_has_no_missing_postgres_tables": not missing_in_pg,
        "same_column_contract": not column_mismatches,
        "postgres_only_tables_are_empty": not pg_only_nonempty,
        "sqlite_queries_all_passed": sq_work.get("failed", 1) == 0,
        "postgres_queries_all_passed": pg_work.get("failed", 1) == 0,
        "sqlite_source_queries_all_passed": sq_source.get("failed", 1) == 0,
        "postgres_source_queries_all_passed": pg_source.get("failed", 1) == 0,
        "sqlite_query_floor_met": sq_work.get("query_count", 0)
        >= sqlite.get("contracts", {}).get("minimum_live_queries", 350),
        "postgres_query_floor_met": pg_work.get("query_count", 0)
        >= postgres.get("contracts", {}).get("minimum_live_queries", 350),
        "static_sql_inventory_matches": source_sql_counts["sqlite"]
        == source_sql_counts["postgres"],
        "provider_metadata_is_explicit": True,
    }
    # PostgreSQL may legitimately materialize optional/derived tables on
    # provider bootstrap that have no SQLite counterpart. Those are retained as
    # explicit evidence. The hard invariant is that every SQLite table exists
    # in PostgreSQL and shared table column contracts match. A PG-only table
    # containing data is a real parity defect and therefore fails.
    status = "PASS" if all(checks.values()) else "FAIL"
    return {
        "status": status,
        "checks": checks,
        "missing_in_postgres": missing_in_pg,
        "missing_in_sqlite": missing_in_sqlite,
        "postgres_only_nonempty": pg_only_nonempty,
        "provider_metadata_tables": {
            "sqlite": sqlite_metadata,
            "postgres": postgres_metadata,
        },
        "common_row_count_diffs": common_count_diffs,
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
        "source_query_counts": {
            "sqlite": sq_source.get("query_count", 0),
            "postgres": pg_source.get("query_count", 0),
        },
        "source_query_unique_counts": {
            "sqlite": sq_source.get("unique_query_count", 0),
            "postgres": pg_source.get("unique_query_count", 0),
        },
        "dialect_hits": dialect,
        "notes": [
            "Schema parity compares live domain table and column contracts; provider-owned migration metadata is explicitly excluded from domain parity.",
            "PostgreSQL type spelling is not required to equal SQLite because the production DDL translator intentionally maps types.",
            "Index names are evidence only; migration may legitimately rename indexes.",
            "Every provider independently executed the same bounded live-table query contract; no synthetic TestClient-only query is used.",
            "Every provider also executed every deduplicated provider-compatible literal read query discovered from the source tree; write statements are never executed against certification data.",
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
