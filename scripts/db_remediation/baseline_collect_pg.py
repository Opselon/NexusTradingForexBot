"""Phase 1 zero-write forensic baseline collector (PostgreSQL).

Part of the 19-agent database remediation program, Phase 1 (read-only forensic
baseline). This script is deliberately strict:

* It opens the target database with ``default_transaction_read_only = on`` so
  that no statement in this file can mutate the server, even by accident.
* Every probe runs inside its own try/except; a probe that fails (missing
  extension, insufficient privilege, version-dependent column) is recorded as
  NOT MEASURABLE with its error text rather than aborting the run. Part 4 sec.6
  forbids fabricating a metric that the instrumentation cannot produce.
* Nothing is dropped, vacuumed, analyzed, reindexed, or reconfigured.

Outputs (both under --outdir):
  baseline_pg_<stamp>.json   machine-readable, full fidelity
  baseline_pg_<stamp>.md     human-readable evidence record

Usage:
  .venv/Scripts/python.exe scripts/db_remediation/baseline_collect_pg.py \
      --outdir docs/forensic-docs/remediation/phase1
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

# Credentials are supplied at runtime; no secret is embedded in this file.
DEFAULT_DSN = "host=127.0.0.1 port=5432 dbname=nexusdb user=postgres"

# --- Probe definitions -------------------------------------------------------
# (name, sql) pairs. Read-only by construction: SELECT / SHOW-equivalent only.

IDENTITY_SQL = """
SELECT
    version()                                   AS server_version,
    current_database()                          AS database,
    current_user                                AS current_user,
    current_schema()                            AS current_schema,
    inet_server_addr()::text                    AS server_addr,
    inet_server_port()                          AS server_port,
    now()                                       AS collected_at,
    pg_postmaster_start_time()                  AS postmaster_start,
    pg_size_pretty(pg_database_size(current_database())) AS database_size_pretty,
    pg_database_size(current_database())        AS database_size_bytes,
    current_setting('server_encoding')          AS encoding,
    current_setting('TimeZone')                 AS timezone,
    current_setting('server_version_num')       AS version_num
"""

ROLE_SQL = """
SELECT rolname, rolsuper, rolinherit, rolcanlogin, rolbypassrls
FROM pg_roles WHERE rolname = current_user
"""

STATS_RESET_SQL = """
SELECT datname, stats_reset, xact_commit, xact_rollback, blks_read, blks_hit,
       tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted,
       conflicts, temp_files, temp_bytes, deadlocks, checksum_failures,
       blk_read_time, blk_write_time, session_time, active_time,
       idle_in_transaction_time, sessions, sessions_abandoned,
       sessions_fatal, sessions_killed
FROM pg_stat_database
WHERE datname = current_database()
"""

EXTENSIONS_SQL = """
SELECT extname, extversion, extnamespace::regnamespace::text AS schema
FROM pg_extension ORDER BY extname
"""

SETTINGS_SQL = """
SELECT name, setting, unit, source, pending_restart
FROM pg_settings
WHERE name IN (
    'shared_buffers', 'effective_cache_size', 'work_mem', 'maintenance_work_mem',
    'random_page_cost', 'seq_page_cost', 'autovacuum', 'track_counts',
    'track_activities', 'track_io_timing', 'track_wal_io_timing',
    'shared_preload_libraries', 'compute_query_id', 'max_connections',
    'wal_level', 'archive_mode', 'archive_command', 'checkpoint_timeout',
    'max_wal_size', 'min_wal_size', 'autovacuum_max_workers',
    'autovacuum_naptime', 'autovacuum_vacuum_scale_factor',
    'autovacuum_analyze_scale_factor', 'autovacuum_vacuum_cost_delay',
    'autovacuum_vacuum_cost_limit', 'log_min_duration_statement',
    'log_autovacuum_min_duration', 'idle_in_transaction_session_timeout',
    'statement_timeout', 'default_statistics_target', 'huge_pages',
    'max_worker_processes', 'max_parallel_workers', 'jit',
    'synchronous_commit', 'full_page_writes', 'wal_compression'
)
ORDER BY name
"""

PG_STAT_STATEMENTS_SQL = """
SELECT extname, extversion FROM pg_extension WHERE extname = 'pg_stat_statements'
"""

PG_STAT_STATEMENTS_PRELOAD_SQL = """
SELECT name, setting, source FROM pg_settings
WHERE name IN ('shared_preload_libraries', 'compute_query_id')
"""

TABLE_STATS_SQL = """
SELECT
    s.schemaname,
    s.relname,
    s.n_live_tup,
    s.n_dead_tup,
    s.n_mod_since_analyze,
    s.n_ins_since_vacuum,
    s.seq_scan,
    s.seq_tup_read,
    s.idx_scan,
    s.idx_tup_fetch,
    s.n_tup_ins,
    s.n_tup_upd,
    s.n_tup_del,
    s.n_tup_hot_upd,
    s.n_tup_newpage_upd,
    s.last_vacuum,
    s.last_autovacuum,
    s.last_analyze,
    s.last_autoanalyze,
    s.vacuum_count,
    s.autovacuum_count,
    s.analyze_count,
    s.autoanalyze_count,
    pg_total_relation_size(s.relid)  AS total_bytes,
    pg_table_size(s.relid)           AS heap_incl_toast_bytes,
    pg_relation_size(s.relid)        AS heap_bytes,
    pg_indexes_size(s.relid)         AS index_bytes,
    pg_size_pretty(pg_total_relation_size(s.relid)) AS total_pretty
FROM pg_stat_user_tables s
ORDER BY pg_total_relation_size(s.relid) DESC
"""

INDEX_STATS_SQL = """
SELECT
    s.schemaname,
    s.relname        AS table_name,
    s.indexrelname   AS index_name,
    s.idx_scan,
    s.idx_tup_read,
    s.idx_tup_fetch,
    pg_relation_size(s.indexrelid) AS index_bytes,
    pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_pretty,
    i.indisunique,
    i.indisprimary,
    i.indisvalid,
    i.indisready,
    i.indislive,
    pg_get_indexdef(s.indexrelid) AS index_def
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
ORDER BY pg_relation_size(s.indexrelid) DESC
"""

FOOTPRINT_SQL = """
SELECT
    n.nspname                       AS schema_name,
    c.relname                       AS table_name,
    c.relkind::text                 AS relkind,
    pg_size_pretty(pg_table_size(c.oid))   AS table_size,
    pg_size_pretty(pg_indexes_size(c.oid)) AS index_size,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
    pg_table_size(c.oid)            AS table_bytes,
    pg_indexes_size(c.oid)          AS index_total_bytes,
    pg_total_relation_size(c.oid)   AS total_bytes,
    pg_relation_size(c.oid)         AS heap_bytes,
    pg_table_size(c.oid) - pg_relation_size(c.oid) AS toast_bytes,
    c.reltoastrelid::regclass::text AS toast_relation,
    c.reltuples::bigint             AS planner_row_estimate,
    c.relpages                      AS pages,
    c.relhasindex,
    c.relpersistence::text          AS persistence
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'm', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_total_relation_size(c.oid) DESC
"""

COLUMNS_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
       c.udt_name, c.is_nullable, c.character_maximum_length,
       c.ordinal_position
FROM information_schema.columns c
WHERE c.table_schema = 'public'
ORDER BY c.table_name, c.ordinal_position
"""

FROZEN_XID_SQL = """
SELECT c.oid::regclass::text AS relation, age(c.relfrozenxid) AS xid_age,
       mxid_age(c.relminmxid) AS mxid_age
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'm', 't')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY age(c.relfrozenxid) DESC
LIMIT 30
"""

ACTIVITY_SQL = """
SELECT pid, usename, application_name, client_addr::text, state,
       backend_start, xact_start, query_start, state_change,
       now() - xact_start   AS xact_age,
       now() - query_start  AS query_age,
       wait_event_type, wait_event, backend_xid, backend_xmin,
       left(query, 400) AS query
FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid()
ORDER BY xact_start NULLS LAST, query_start NULLS LAST
"""

LOCKS_SQL = """
SELECT l.locktype, l.mode, l.granted, l.pid,
       d.datname, n.nspname, c.relname,
       pg_blocking_pids(l.pid) AS blocking_pids,
       left(a.query, 200) AS query
FROM pg_locks l
LEFT JOIN pg_database d ON d.oid = l.database
LEFT JOIN pg_class c ON c.oid = l.relation
LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_activity a ON a.pid = l.pid
WHERE NOT l.granted OR l.pid <> pg_backend_pid()
ORDER BY l.granted, l.pid
"""

WAL_SQL = """
SELECT now() AS sample_at, stats_reset, wal_records, wal_fpi, wal_bytes,
       wal_buffers_full, wal_write, wal_sync, wal_write_time, wal_sync_time
FROM pg_stat_wal
"""

# PG17 moved columns between pg_stat_bgwriter / pg_stat_checkpointer (e.g.
# num_done, buffers_backend) and renamed max_dead_tuples ->
# max_dead_tuple_bytes in pg_stat_progress_vacuum. Selecting the whole row is
# justified here and ONLY here: these are single-row (or few-row) system
# statistics views whose complete shape is exactly what we must record, and a
# hardcoded column list silently loses a column on the next minor upgrade.
CHECKPOINTER_SQL = """
SELECT * FROM pg_stat_checkpointer
"""

BGWRITER_SQL = """
SELECT * FROM pg_stat_bgwriter
"""

AUTOVACUUM_PROGRESS_SQL = """
SELECT p.*, a.query_start
FROM pg_stat_progress_vacuum p
LEFT JOIN pg_stat_activity a ON a.pid = p.pid
"""

STATIO_SQL = """
SELECT schemaname, relname, heap_blks_read, heap_blks_hit,
       idx_blks_read, idx_blks_hit, toast_blks_read, toast_blks_hit,
       tidx_blks_read, tidx_blks_hit
FROM pg_statio_user_tables
ORDER BY heap_blks_read + idx_blks_read DESC
LIMIT 40
"""

TABLE_SIZES_SQL = """
SELECT schemaname, relname,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       pg_total_relation_size(relid) AS total_bytes
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
"""

IO_SQL = """
SELECT backend_type, object, context, sum(reads) AS reads, sum(writes) AS writes,
       sum(writebacks) AS writebacks, sum(extends) AS extends,
       sum(hits) AS hits, sum(evictions) AS evictions,
       sum(fsyncs) AS fsyncs
FROM pg_stat_io
GROUP BY backend_type, object, context
ORDER BY sum(reads) + sum(writes) DESC
LIMIT 40
"""

PROBES: list[tuple[str, str, bool]] = [
    # (section, sql, fetchall?)  -- False means fetchone
    ("identity", IDENTITY_SQL, False),
    ("current_role", ROLE_SQL, False),
    ("database_stats", STATS_RESET_SQL, False),
    ("extensions", EXTENSIONS_SQL, True),
    ("settings", SETTINGS_SQL, True),
    ("pg_stat_statements_present", PG_STAT_STATEMENTS_SQL, True),
    ("query_id_settings", PG_STAT_STATEMENTS_PRELOAD_SQL, True),
    ("table_stats", TABLE_STATS_SQL, True),
    ("index_stats", INDEX_STATS_SQL, True),
    ("footprint", FOOTPRINT_SQL, True),
    ("frozen_xid", FROZEN_XID_SQL, True),
    ("activity", ACTIVITY_SQL, True),
    ("locks", LOCKS_SQL, True),
    ("wal", WAL_SQL, False),
    ("checkpointer", CHECKPOINTER_SQL, False),
    ("bgwriter", BGWRITER_SQL, False),
    ("autovacuum_progress", AUTOVACUUM_PROGRESS_SQL, True),
    ("io", IO_SQL, True),
    ("statio", STATIO_SQL, True),
    ("columns", COLUMNS_SQL, True),
]


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def collect(dsn: str) -> tuple[dict[str, Any], dict[str, str]]:
    result: dict[str, Any] = {}
    errors: dict[str, str] = {}

    with psycopg.connect(dsn, connect_timeout=10, autocommit=True) as conn:
        # Enforce sec.4 of the execution protocol: read-only at the server side.
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
            cur.execute("SET statement_timeout = '60s'")
            cur.execute("SET idle_in_transaction_session_timeout = '30s'")

        for name, sql, many in PROBES:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    if many:
                        cols = [d.name for d in (cur.description or [])]
                        rows = cur.fetchall()
                        result[name] = {
                            "columns": cols,
                            "row_count": len(rows),
                            "rows": [
                                {c: _jsonable(v) for c, v in zip(cols, r, strict=True)}
                                for r in rows
                            ],
                        }
                    else:
                        row = cur.fetchone()
                        cols = [d.name for d in (cur.description or [])]
                        result[name] = (
                            {c: _jsonable(v) for c, v in zip(cols, row, strict=True)}
                            if row
                            else None
                        )
            except Exception as exc:  # noqa: BLE001 - a failed probe is data
                errors[name] = f"{type(exc).__name__}: {exc}".splitlines()[0]
                result[name] = {"error": errors[name]}

    return result, errors


def summarise(data: dict[str, Any], errors: dict[str, str]) -> str:
    lines: list[str] = []
    ident = data.get("identity") or {}
    lines.append("## Server identity")
    for key in (
        "server_version",
        "database",
        "current_user",
        "server_addr",
        "server_port",
        "collected_at",
        "postmaster_start",
        "database_size_pretty",
        "database_size_bytes",
        "encoding",
        "timezone",
    ):
        lines.append(f"- **{key}**: {ident.get(key)}")

    lines.append("")
    lines.append("## Query observability gate")
    pss = data.get("pg_stat_statements_present") or {}
    if pss.get("row_count"):
        lines.append(f"- pg_stat_statements: PRESENT {pss['rows']}")
    else:
        lines.append(
            "- pg_stat_statements: **ABSENT** -> per sec.6 of the execution "
            "protocol, query-level rankings by total/mean time, calls, rows, "
            "shared blocks or temp blocks are "
            "NOT MEASURABLE WITH CURRENT INSTRUMENTATION."
        )
    qid = data.get("query_id_settings") or {}
    for row in qid.get("rows", []):
        lines.append(f"- {row['name']} = {row['setting']} (source={row['source']})")

    lines.append("")
    lines.append("## Largest relations")
    fp = data.get("footprint") or {}
    lines.append("| # | schema | table | kind | heap | toast | indexes | total | planner rows |")
    lines.append("|---|--------|-------|------|------|-------|---------|-------|--------------|")
    for i, row in enumerate((fp.get("rows") or [])[:25], 1):
        lines.append(
            f"| {i} | {row['schema_name']} | {row['table_name']} | {row['relkind']} | "
            f"{row['table_size']} | {row['toast_bytes']}B | {row['index_size']} | "
            f"{row['total_size']} | {row['planner_row_estimate']} |"
        )

    lines.append("")
    lines.append("## Scan / write activity (hot-path evidence)")
    ts = data.get("table_stats") or {}
    lines.append(
        "| table | live | dead | seq_scan | seq_tup_read | idx_scan | ins | upd | del | total |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for row in (ts.get("rows") or [])[:25]:
        lines.append(
            f"| {row['relname']} | {row['n_live_tup']} | {row['n_dead_tup']} | "
            f"{row['seq_scan']} | {row['seq_tup_read']} | {row['idx_scan']} | "
            f"{row['n_tup_ins']} | {row['n_tup_upd']} | {row['n_tup_del']} | {row['total_pretty']} |"
        )

    lines.append("")
    lines.append("## Probe errors (NOT MEASURABLE)")
    if errors:
        for name, err in errors.items():
            lines.append(f"- **{name}**: {err}")
    else:
        lines.append("- none")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--label", default="phase1")
    args = ap.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        data, errors = collect(args.dsn)
    except Exception:
        print("CONNECT FAILED")
        traceback.print_exc()
        return 2

    payload = {
        "collected_at_utc": stamp,
        "label": args.label,
        "probe_errors": errors,
        "data": data,
    }
    json_path = outdir / f"baseline_pg_{args.label}_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    md_path = outdir / f"baseline_pg_{args.label}_{stamp}.md"
    md_path.write_text(
        f"# PostgreSQL zero-write baseline ({args.label})\n\n"
        f"Collected (UTC): {stamp}\n"
        f"Session forced `default_transaction_read_only = on`; no mutation performed.\n\n"
        + summarise(data, errors)
        + "\n",
        encoding="utf-8",
    )

    print(f"JSON: {json_path}")
    print(f"MD  : {md_path}")
    print(f"probe_errors={len(errors)} {list(errors)}")
    ident = data.get("identity") or {}
    print(f"db={ident.get('database')} size={ident.get('database_size_pretty')}")
    for section in ("footprint", "table_stats", "index_stats"):
        block = data.get(section) or {}
        print(f"{section}: {block.get('row_count', 'ERR')} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
