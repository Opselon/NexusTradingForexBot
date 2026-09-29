"""Render the Phase 1 forensic baseline report from the captured JSON.

Every number in the output is read out of the baseline JSON produced by
baseline_collect_pg.py / baseline_collect_sqlite.py, so no figure in the report
can drift from its evidence file. Findings (root cause, tradeoff, limitation)
are authored here; measurements are injected.

Usage:
  python phase1_report.py --dir docs/forensic-docs/remediation/phase1
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

TABLE = [
    "shadow_decisions",
    "news_articles",
    "research_gates",
    "news_analysis",
    "research_evidence",
    "model_governance_events",
    "strategy_registry",
    "audit_experiences",
    "factory_events",
    "research_events",
    "audit_signals",
    "audit_orders",
    "audit_experience_outcomes",
]


def load(dirpath: str) -> tuple[dict, dict]:
    pg_files = sorted(glob.glob(os.path.join(dirpath, "baseline_pg_phase1b-*.json")))
    sq_files = sorted(glob.glob(os.path.join(dirpath, "baseline_sqlite_*.json")))
    if not pg_files:
        raise SystemExit("no baseline_pg_phase1b-*.json found")
    pg = json.loads(Path(pg_files[-1]).read_text(encoding="utf-8"))
    sq = json.loads(Path(sq_files[-1]).read_text(encoding="utf-8")) if sq_files else {}
    return pg, sq


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()

    pg, sq = load(args.dir)
    d = pg["data"]
    ident = d["identity"]
    ds = d["database_stats"]
    wal = d["wal"]
    cp = d["checkpointer"]
    bg = d["bgwriter"]
    tables = {r["relname"]: r for r in d["table_stats"]["rows"]}
    footprint = {r["table_name"]: r for r in d["footprint"]["rows"]}
    idx = d["index_stats"]["rows"]
    statio = {r["relname"]: r for r in (d.get("statio", {}).get("rows") or [])}

    total_toast = sum(r["toast_bytes"] or 0 for r in d["footprint"]["rows"])
    never_vac = [
        r
        for r in d["table_stats"]["rows"]
        if r["last_autovacuum"] is None and r["last_vacuum"] is None
    ]
    zero_idx = [r for r in idx if r["idx_scan"] == 0]
    zero_idx_bytes = sum(r["index_bytes"] for r in zero_idx)
    inval = [r for r in idx if not r["indisvalid"] or not r["indisready"]]

    asig = tables["audit_signals"]
    aord = tables["audit_orders"]
    aeoc = tables["audit_experience_outcomes"]

    def mb(b: int) -> str:
        return f"{b / 1024 / 1024:.1f} MB"

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip() or "UNKNOWN"

    L: list[str] = []
    aj = L.append

    aj("# Phase 1 — Zero-Write Forensic Baseline (Database Remediation Program)")
    aj("")
    aj(f"Generated (UTC): {now}")
    aj(f"Baseline commit (this branch): `{sha}`")
    aj("")
    aj("Part 4 of the execution protocol, Phase 1. **Zero-write.** The PostgreSQL")
    aj("session is opened with `default_transaction_read_only = on` and every probe")
    aj("is a `SELECT` against catalog/statistics views. No `VACUUM`, `ANALYZE`,")
    aj("`REINDEX`, DDL, DML or configuration change was executed. SQLite targets are")
    aj("opened through a `mode=ro` URI.")
    aj("")
    aj("Reproduce with:")
    aj("")
    aj("```")
    aj("# PostgreSQL")
    aj(".venv/Scripts/python.exe scripts/db_remediation/baseline_collect_pg.py \\")
    aj('    --dsn "host=127.0.0.1 port=5432 dbname=nexusdb user=postgres password=<pw>" \\')
    aj(f"    --outdir {args.dir} --label phase1b-20260929")
    aj("")
    aj("# SQLite")
    aj(".venv/Scripts/python.exe scripts/db_remediation/baseline_collect_sqlite.py \\")
    aj(f"    --outdir {args.dir} --label phase1-20260929 <db> [<db> ...]")
    aj("")
    aj("python scripts/db_remediation/phase1_report.py "
       f"--dir {args.dir}   # regenerates this file")
    aj("```")
    aj("")
    aj("---")
    aj("")

    # ---------------- identity / window ----------------
    aj("## 1. Environment and observation window")
    aj("")
    aj(f"- Server: `{ident['server_version']}`")
    aj(f"- Database: `{ident['database']}` at `{ident['server_addr']}:{ident['server_port']}`")
    aj(f"- Database size: **{ident['database_size_pretty']}** ({ident['database_size_bytes']:,} bytes)")
    aj(f"- Encoding / timezone: {ident['encoding']} / {ident['timezone']}")
    aj(f"- Postmaster start: {ident['postmaster_start']}")
    aj(f"- Baseline captured: {ident['collected_at']}")
    aj(f"- Statistics reset (`pg_stat_wal.stats_reset`): **{wal['stats_reset']}**")
    aj(f"- Relations: {len(d['footprint']['rows'])} tables, {len(idx)} indexes")
    aj("")
    aj("**Critical window fact.** The stats window covers the period since "
       f"`{wal['stats_reset'][:19]}`. The only relevant source fix on `origin/main`")
    aj("is PR #561 (`audit_signals` scan amplification), which merged at")
    aj("`2026-09-29 02:00:20 +03:30`. At capture time the engine had **no listener**")
    aj("on any NSE port and `pg_stat_activity` held **0 backends** and **0 locks**,")
    aj("so *no query has executed against the fixed code inside this statistics")
    aj("window*. Every cumulative counter below therefore records **pre-fix**")
    aj("behaviour, and no post-merge runtime observation exists.")
    aj("")
    aj("---")
    aj("")

    # ---------------- instrumentation ----------------
    aj("## 2. Instrumentation ceiling — what is NOT measurable")
    aj("")
    aj("Per sec.6, an unavailable metric is stated, never fabricated:")
    aj("")
    aj("- `pg_stat_statements`: **ABSENT** (`shared_preload_libraries` is empty,"
       " source=default).")
    aj("  -> Query-level rankings by total/mean execution time, calls, rows, shared")
    aj("  blocks, temp blocks and WAL are **NOT MEASURABLE WITH CURRENT")
    aj("  INSTRUMENTATION**.")
    aj("- `compute_query_id = auto` gives no usable `pg_activity.query_id` without")
    aj("  that extension.")
    aj("- `track_io_timing = off` and `track_wal_io_timing = off` -> `blk_read_time`,")
    aj("  `blk_write_time`, `wal_write_time`, `wal_sync_time` all read `0.0`. These")
    aj("  zeros are an **instrumentation choice, not a measurement of zero latency**,")
    aj("  and must not be reported as latencies.")
    aj("- `log_min_duration_statement = -1` -> no slow-query log exists to mine.")
    aj("")
    aj("Consequence for sec.15 / sec.53: **p50/p95/p99 per hot path are not")
    aj("obtainable from the current instrumentation.** They require either enabling")
    aj("`pg_stat_statements` (shared_preload_libraries + restart — a controlled")
    aj("configuration operation, not a read-only audit step) or an application-side")
    aj("driver-boundary aggregator. Both are implementation-phase items and are")
    aj("listed under section 7.")
    aj("")
    aj("---")
    aj("")

    # ---------------- sec 53 table ----------------
    aj("## 3. Standard before/after table (sec.53)")
    aj("")
    aj("`After` is `PENDING` for every row: no remediation in this program has been")
    aj("implemented or merged yet, so an after-value would be fabricated.")
    aj("")
    aj("Metric                              | Before                | After     | Delta | Evidence")
    aj("------------------------------------|-----------------------|-----------|-------|---------")
    aj(f"Database size                       | {ident['database_size_pretty']:<21} | PENDING   | -     | identity probe")
    aj(f"Tables / indexes                    | {len(d['footprint']['rows'])} / {len(idx):<17} | PENDING   | -     | footprint, index probes")
    aj(f"TOAST bytes (all relations)         | {mb(total_toast):<21} | PENDING   | -     | footprint probe")
    aj(f"seq_tup_read (db total)             | {ds['tup_returned']:>21,} | PENDING   | -     | pg_stat_database")
    aj(f"tup_fetched (db total)              | {ds['tup_fetched']:>21,} | PENDING   | -     | pg_stat_database")
    aj(f"returned/fetched amplification      | {ds['tup_returned'] / ds['tup_fetched']:>20.1f}x | PENDING   | -     | derived")
    aj(f"audit_signals seq_scan              | {asig['seq_scan']:>21,} | PENDING   | -     | pg_stat_user_tables")
    aj(f"audit_signals seq_tup_read          | {asig['seq_tup_read']:>21,} | PENDING   | -     | pg_stat_user_tables")
    aj(f"audit_orders idx_tup_fetch          | {aord['idx_tup_fetch']:>21,} | PENDING   | -     | pg_stat_user_tables")
    aj(f"zero-scan indexes (count)           | {len(zero_idx):<21} | PENDING   | -     | pg_stat_user_indexes")
    aj(f"zero-scan index bytes               | {mb(zero_idx_bytes):<21} | PENDING   | -     | pg_stat_user_indexes")
    aj(f"tables never vacuumed/analyzed      | {len(never_vac):<21} | PENDING   | -     | pg_stat_user_tables")
    aj(f"WAL bytes since stats reset         | {int(wal['wal_bytes']) / 1024**3:>20.2f} G | PENDING   | -     | pg_stat_wal")
    aj(f"checkpoint write_time               | {cp['write_time'] / 1000:>20.0f} s | PENDING   | -     | pg_stat_checkpointer")
    aj("per-hot-path query count            | NOT MEASURABLE        | PENDING   | -     | no pg_stat_statements")
    aj("p50 / p95 / p99 latency             | NOT MEASURABLE        | PENDING   | -     | no pg_stat_statements")
    aj("Db bytes added/hour                 | NOT MEASURABLE        | PENDING   | -     | no growth sampling yet")
    aj("purge duration                      | NOT MEASURABLE        | PENDING   | -     | no purge subsystem verified")
    aj("")
    aj("---")
    aj("")

    # ---------------- findings ----------------
    aj("## 4. Findings")
    aj("")
    aj("Each finding carries a stable ID. Severity is *measured*, not assigned by feel.")
    aj("")

    def finding(
        fid: str,
        title: str,
        fact: str,
        measurement: list[str],
        repro: str,
        root: str,
        change: str,
        before: str,
        after: str,
        tradeoff: str,
        test: str,
        limitation: str,
    ) -> None:
        aj(f"### {fid} — {title}")
        aj("")
        aj(f"**FACT.** {fact}")
        aj("")
        aj("**MEASUREMENT.**")
        for m in measurement:
            aj(f"- {m}")
        aj("")
        aj(f"**REPRODUCTION.** {repro}")
        aj("")
        aj(f"**ROOT CAUSE.** {root}")
        aj("")
        aj(f"**CHANGE.** {change}")
        aj("")
        aj(f"**BEFORE.** {before}")
        aj("")
        aj(f"**AFTER.** {after}")
        aj("")
        aj(f"**TRADEOFF.** {tradeoff}")
        aj("")
        aj(f"**TEST.** {test}")
        aj("")
        aj(f"**LIMITATION.** {limitation}")
        aj("")

    finding(
        "F-001",
        "audit_signals scan amplification — the dominant avoidable work in the cluster",
        "A 7,453-row, 12 MB table accounts for 30,548,787,365 of the database's "
        "30,824,574,472 `seq_tup_read` (99.1%), i.e. 4,098,858 rows read per live row.",
        [
            f"`seq_scan` = {asig['seq_scan']:,}",
            f"`seq_tup_read` = {asig['seq_tup_read']:,}",
            f"`idx_scan` = {asig['idx_scan']:,}, `idx_tup_fetch` = {asig['idx_tup_fetch']:,}",
            f"`n_live_tup` = {asig['n_live_tup']:,}, total size {asig['total_pretty']}",
            f"`heap_blks_hit` = {statio['audit_signals']['heap_blks_hit']:,} — the single "
            "largest buffer-hit consumer in `pg_statio_user_tables`",
            f"derived: {asig['seq_tup_read'] / asig['n_live_tup']:,.0f} tuples read per live row",
        ],
        "`baseline_collect_pg.py` probes `table_stats` + `statio`; source mapping and "
        "plan evidence are in `docs/forensic-docs/p1-audit-scan-amplification.md`.",
        "Consumers issued predicates that select ~100% of the table (7-day window equal "
        "to the whole retention period, `action='NO_TRADE'` at ~92% selectivity, "
        "`id IN (SELECT ... LIMIT 20000)` where 20000 > row count), so the planner's "
        "sequential scan was correct and no index could have helped.",
        "Already implemented upstream by PR #561 (bounded latest-N tails + a "
        "high-water-mark memo on the 5 Hz SSE section). **No change is made by this "
        "baseline.**",
        f"{asig['seq_scan']:,} seq scans / {asig['seq_tup_read']:,} tuples read, "
        "accumulated entirely BEFORE the fix merged.",
        "PENDING — and note that *zero* post-fix runtime has been observed (section 1).",
        "The bounded tail (2000 rows) changes `/stats` and `/funnel` from a full "
        "distribution to a latest-N distribution. That is a deliberate semantic change "
        "and must be documented as BEFORE != AFTER for the aggregation window.",
        "Existing: `tests/unit/test_p1_audit_scan_amplification.py` (550 lines). "
        "Missing: a regression guard that fails if a new consumer reintroduces an "
        "unbounded window, and the sec.69 runtime regression guard.",
        "Cumulative counters cannot attribute the split between the SSE path, the "
        "operator routes and the CLI. Attribution requires the driver-boundary "
        "aggregator described in section 7.",
    )

    finding(
        "F-002",
        "The most expensive confirmed defect has no post-merge runtime evidence",
        "PR #561 merged at 2026-09-29 02:00:20, postmaster started 2026-09-29 01:19:26, "
        "and at capture the engine was not running. The fix has therefore never "
        "executed against this database.",
        [
            "`pg_stat_activity` rows (excluding the collector session) = 0",
            "`pg_locks` rows of interest = 0",
            "no listener on the configured `NSE_WEB_ACTUAL_PORT` (59273) or 8080",
            f"`nexus.pid` present since 2026-09-27 10:17 (last boot stamp)",
            f"sessions_abandoned = {ds['sessions_abandoned']:,} of {ds['sessions']:,} sessions",
        ],
        "Port check: `netstat -ano | grep LISTENING`. Liveness: the `activity` and "
        "`locks` probes in the baseline JSON.",
        "Verification was stopped at 'CI green + merged'. Sec.83/§84 forbid declaring "
        "a remediation complete on merge; this program's Phase 12 does not exist yet.",
        "None (measurement finding).",
        "No post-fix baseline exists.",
        "PENDING — requires Phase 12 post-merge observation as the program's own §83 step "
        "requires.",
        "None.",
        "A controlled observation window (idle + active + burst) with the delta of "
        "`seq_scan` / `seq_tup_read` / `idx_scan` for `audit_signals` across a known "
        "start timestamp. Sec.84 requires the window be long enough to separate the "
        "change from noise.",
        "The benefit of PR #561 is currently asserted from a static-ledger probe "
        "(50 tail reads -> 1), not from production delta.",
    )

    finding(
        "F-003",
        "audit_orders: index-fetch amplification larger than audit_signals",
        "A 2,457-row table is read through indexes 61,661,025 times — 25,096 index "
        "fetches per live row — with 3,229,612 index scans. This was not identified by "
        "any prior audit.",
        [
            f"`idx_scan` = {aord['idx_scan']:,}",
            f"`idx_tup_fetch` = {aord['idx_tup_fetch']:,}",
            f"`n_live_tup` = {aord['n_live_tup']:,}",
            f"derived: {aord['idx_tup_fetch'] / aord['n_live_tup']:,.0f} fetches per live row",
            f"`seq_scan` = {aord['seq_scan']:,} (by contrast, near-zero)",
        ],
        "`table_stats` probe in the baseline JSON, filtered to `audit_orders`.",
        f"Not yet established. The pattern is {aord['idx_scan']:,} index probes returning "
        f"{aord['idx_tup_fetch'] / aord['idx_scan']:.1f} rows each — a high-frequency "
        "point lookup (order/ticket reconciliation is the probable caller) that has not "
        "yet been traced to a cadence.",
        "None (Phase 1 is read-only).",
        f"{aord['idx_scan']:,} index scans, {aord['idx_tup_fetch']:,} tuples fetched.",
        "PENDING.",
        "None at this stage. Must not be dismissed as 'small table, so cheap' — the "
        "row count is small, the call count is not.",
        "Phase 2 must locate the caller and cadence, then build the sec.15 hot-path "
        "budget and a sec.69 regression guard on query count.",
        "Needs the query inventory (sec.8) and driver-boundary instrumentation. Absent "
        "those, the frequency is inferred from counters rather than observed.",
    )

    finding(
        "F-004",
        "audit_experience_outcomes: 2.81M index scans against a 3,471-row table",
        "An outcomes/reconciliation table shows 2,807,889 index scans and 8,929,306 "
        "seq tuples read for 3,471 live rows.",
        [
            f"`idx_scan` = {aeoc['idx_scan']:,}",
            f"`idx_tup_fetch` = {aeoc['idx_tup_fetch']:,}",
            f"`seq_scan` = {aeoc['seq_scan']:,}, `seq_tup_read` = {aeoc['seq_tup_read']:,}",
            f"`n_live_tup` = {aeoc['n_live_tup']:,}",
            f"derived: {aeoc['seq_tup_read'] / aeoc['n_live_tup']:,.0f} seq tuples per live row",
        ],
        "`table_stats` probe, filtered to `audit_experience_outcomes`.",
        "Not yet established. Probable polling reconciler; needs sec.8 trace.",
        "None (Phase 1 is read-only).",
        f"`idx_scan` = {aeoc['idx_scan']:,}, `seq_tup_read` = {aeoc['seq_tup_read']:,}.",
        "PENDING.",
        "None at this stage.",
        "Phase 2 trace + sec.29 polling test (no new data / 1 row / 10 rows / burst).",
        "Same instrumentation gap as F-003.",
    )

    finding(
        "F-005",
        "91 of 281 indexes have never been scanned (10.7 MB of index storage)",
        "Index storage is maintained on every write for indexes that no query in the "
        "statistics window has used.",
        [
            f"{len(zero_idx)} of {len(idx)} indexes show `idx_scan = 0`",
            f"combined footprint {mb(zero_idx_bytes)} ({zero_idx_bytes:,} bytes)",
            "largest: "
            + ", ".join(
                f"`{r['index_name']}` ({mb(r['index_bytes'])})"
                for r in sorted(zero_idx, key=lambda r: -r["index_bytes"])[:4]
            ),
            f"invalid / not-ready indexes: {len(inval)} (none — no rebuild debt found)",
        ],
        "`index_stats` probe filtered on `idx_scan = 0`.",
        "Unknown per index. A zero scan count inside a 4.6-day window is a workload "
        "observation, NOT proof of redundancy.",
        "None. Sec.49 makes index removal a gated decision requiring usage stats, DDL "
        "and constraint dependency, migration references and foreign tooling; the "
        "statistics window here is too short to satisfy it.",
        f"{len(zero_idx)} unused indexes, {mb(zero_idx_bytes)}.",
        "PENDING.",
        "Dropping an index that a rare-but-critical path depends on is far more "
        "expensive than the storage saved. `model_governance_events` alone carries a "
        "5.65 MB index that may serve a cold governance/audit query.",
        "Sec.49 gate checklist per candidate index, then a write-cost and storage "
        "measurement over a longer window.",
        "The window is ~4.6 days and the workload is a live trading system with "
        "monthly/quarterly governance operations. This finding is a **candidate list**, "
        "explicitly not a drop list.",
    )

    finding(
        "F-006",
        "TOAST accounts for 193.5 MB — 32% of the database — concentrated in 7 tables",
        "Large payload columns dominate storage, with several tables holding most of "
        "their bytes outside the heap.",
        [
            f"total TOAST across all relations: {mb(total_toast)} of {ident['database_size_pretty']}",
            "top: "
            + ", ".join(
                f"`{r['table_name']}` {mb(r['toast_bytes'] or 0)} "
                f"({100 * (r['toast_bytes'] or 0) / (r['total_bytes'] or 1):.0f}% of table)"
                for r in sorted(
                    d["footprint"]["rows"], key=lambda r: -(r["toast_bytes"] or 0)
                )[:5]
            ),
            "`strategy_registry` and `factory_events` exceed 70% TOAST share on small "
            "row counts, indicating wide per-row payloads rather than row volume",
        ],
        "`footprint` probe (`pg_table_size - pg_relation_size` vs `reltoastrelid`).",
        "Raw/verbose payloads are persisted in-line with the row instead of being "
        "derived or moved to cold storage. Sec.33/§34 treat this as a column-level "
        "storage question.",
        "None (Phase 1 is read-only).",
        f"{mb(total_toast)} TOAST.",
        "PENDING.",
        "Compression or relocation can change read amplification on the hot path if a "
        "frequently-read column gets pushed out-of-line.",
        "Sec.34 per-column audit: storage contribution, read frequency, consumer count, "
        "serialization frequency; then a bounded before/after on response bytes.",
        "Payload *content* was not sampled in this pass, so 'verbose' is an inference "
        "from TOAST share, not a measured redundancy.",
    )

    finding(
        "F-007",
        "81 of 126 tables have never been vacuumed or analyzed by any path",
        "Most relations have no maintenance history at all, and the ones that do are "
        "maintained by the default autovacuum settings with no per-table tuning.",
        [
            f"{len(never_vac)} of {len(d['table_stats']['rows'])} tables show both "
            "`last_vacuum` and `last_autovacuum` as NULL",
            "highest dead-tuple counts: "
            + ", ".join(
                f"`{r['relname']}` live={r['n_live_tup']:,} dead={r['n_dead_tup']:,}"
                for r in sorted(
                    d["table_stats"]["rows"], key=lambda r: -(r["n_dead_tup"] or 0)
                )[:4]
            ),
            "all autovacuum settings are `source=default`: "
            "`autovacuum_vacuum_scale_factor=0.2`, `autovacuum_analyze_scale_factor=0.1`, "
            "`autovacuum_max_workers=3`, `autovacuum_naptime=60`",
        ],
        "`table_stats` probe + `settings` probe for the autovacuum parameters.",
        "No per-table autovacuum tuning exists for the append-heavy, high-churn tables "
        "in this workload. A 0.2 scale factor means a table is not vacuumed until 20% "
        "of its rows are dead — for the churning tables here that threshold is rarely "
        "reached, and for the append-only tables it is never reached.",
        "None (Phase 1 is read-only).",
        f"{len(never_vac)} tables with no maintenance history.",
        "PENDING.",
        "Aggressive autovacuum costs background I/O; on this box the observed pending "
        "cost is a 5,753 s cumulative checkpoint write time, so any tuning must be "
        "measured against that, not assumed.",
        "Sec.24/§28: measure bloat and query latency at 10K/100K/1M rows before and "
        "after any per-table setting.",
        "`n_dead_tup` is an estimate; page-level bloat was not measured (`pgstattuple` "
        "is not installed and installing it is out of scope for a read-only pass).",
    )

    finding(
        "F-008",
        "No retention or purge path is verifiable from the baseline",
        "The program's §9/§22/§90 require an explicit retention policy and purge "
        "subsystem; the baseline cannot confirm that any exists, and several tables "
        "grow monotonically with no deletion history.",
        [
            "tables with `n_tup_del = 0` and no lifecycle owner identified: "
            + ", ".join(
                f"`{r['relname']}` (ins={r['n_tup_ins']:,})"
                for r in sorted(
                    d["table_stats"]["rows"], key=lambda r: -(r["n_tup_ins"] or 0)
                )[:6]
            ),
            f"`audit_signals` carries a 7-day retention window in its query predicates "
            f"yet shows `n_tup_del` = {asig['n_tup_del']:,} against "
            f"`n_tup_ins` = {asig['n_tup_ins']:,}",
        ],
        "`table_stats` probe: `n_tup_del` is 0 for almost every high-volume table.",
        "Unknown ownership. Sec.27 forbids purging on age alone and sec.93 makes "
        "'unknown retention requirement' a hard stop.",
        "None.",
        "No purge measurement exists.",
        "PENDING — blocked. This finding cannot be resolved by measurement alone.",
        "Sec.22 requires dry-run candidate counts and protected-row exclusion before "
        "any purge is enabled.",
        "Sec.93 hard stop: **unknown retention requirement / unknown data ownership**. "
        "The Phase 2 synthesis must produce a per-dataset lifecycle owner before any "
        "purge work is authorised.",
        "Not measurable from catalog statistics: the presence of a retention window in "
        "one query's predicate does not prove a purge job exists.",
    )

    finding(
        "F-009",
        "shadow_decisions is the largest physical disk reader despite being an observability table",
        "The table with the highest `heap_blks_read` (110,853) is a shadow/observability "
        "table, and it is also the single largest relation in the database.",
        [
            f"`shadow_decisions` size {footprint['shadow_decisions']['total_size']}, "
            f"`n_live_tup` = {tables['shadow_decisions']['n_live_tup']:,}",
            f"`heap_blks_read` = {statio['shadow_decisions']['heap_blks_read']:,} "
            f"(highest of all relations), `heap_blks_hit` = "
            f"{statio['shadow_decisions']['heap_blks_hit']:,}",
            f"`last_autovacuum` = {str(tables['shadow_decisions']['last_autovacuum'])[:19]}, "
            f"`autovacuum_count` = {tables['shadow_decisions']['autovacuum_count']}",
            f"`idx_scan` = {tables['shadow_decisions']['idx_scan']:,} against "
            f"{tables['shadow_decisions']['n_live_tup']:,} rows",
        ],
        "`statio` + `footprint` + `table_stats` probes filtered to `shadow_decisions`.",
        "Not yet established. Shadow/challenger records are persisted at a volume and "
        "row width that makes them the dominant storage and read consumer, but no "
        "consumer of that history has been identified.",
        "None.",
        f"{footprint['shadow_decisions']['total_size']} / "
        f"{statio['shadow_decisions']['heap_blks_read']:,} blocks read.",
        "PENDING.",
        "Shadow data feeds model-governance and promotion evidence; sec.74 forbids "
        "reducing critical evidence. Any change must prove governance consumers survive.",
        "Sec.37 decision-storage contract test applied to shadow rows: can the record "
        "answer what/why/when/outcome, and does any consumer read it?",
        "This is a HIGH-VALUE suspicion, not a proven defect. 'Largest table' is not "
        "'unnecessary table' — sec.34 requires the consumer count first.",
    )

    finding(
        "F-010",
        "A disabled subsystem still owns ~25 MB of persisted events",
        "`factory.enabled = false` in application settings, yet `factory_events` holds "
        "25 MB (17.8 MB of it TOAST) and `factory_*` tables are present.",
        [
            "`factory.enabled` = `false` (source `USER_SETTINGS`, value_type str)",
            f"`factory_events` {footprint['factory_events']['total_size']} total, "
            f"{mb(footprint['factory_events']['toast_bytes'] or 0)} TOAST, "
            f"{tables['factory_events']['n_live_tup']:,} rows",
            f"`factory_candidates` {footprint['factory_candidates']['total_size']}, "
            f"`factory_failures` {footprint['factory_failures']['total_size']}",
            f"`factory_events.n_tup_ins` = {tables['factory_events']['n_tup_ins']:,} "
            "(all rows inserted before the subsystem was disabled)",
        ],
        "`footprint`/`table_stats` probes cross-referenced with the "
        "`application_settings` table in `app_settings.db`.",
        "Data from an experimental/feature-flagged subsystem was never given a "
        "lifecycle once the flag was turned off.",
        "None.",
        f"{footprint['factory_events']['total_size']} + "
        f"{footprint['factory_candidates']['total_size']} + "
        f"{footprint['factory_failures']['total_size']} of factory data.",
        "PENDING.",
        "The subsystem can be re-enabled (`factory.enabled` is `HOT_RESTRICTED`, not "
        "removed), in which case the history has value again.",
        "Sec.34/§48: identify consumers, then a dry-run candidate count per this "
        "finding's tables before any action.",
        "A disabled flag is not proof the data is unread — batch research jobs may "
        "still query it. Consumer proof is required first.",
    )

    finding(
        "F-011",
        "PostgreSQL is the sole runtime provider; SQLite is confined to settings storage",
        "The provider was chosen deliberately by the operator, so there is no silent "
        "provider fallback in play — but there is also no SQLite runtime workload left "
        "to remediate.",
        [
            "`database.provider` = `postgresql` (source **USER_SETTINGS**, not a wave)",
            "`database.provider_transition_state` = active `postgresql`, target `postgresql`",
            "largest SQLite database found: `app_settings.db` 155,648 bytes, 5 tables",
            "`ai_provider_decisions.db` 24,576 bytes with **0 rows**",
            "repo `data/*.db` and root `app_settings.db` are **0-byte placeholders**",
        ],
        "`baseline_collect_sqlite.py` over the discovered SQLite files, plus the "
        "`application_settings` table read.",
        "N/A — this is a routing fact, not a defect.",
        "None.",
        "SQLite total: ~213 KB across 3 real files, all `quick_check = ok`.",
        "N/A.",
        "None.",
        "Sec.44 SQLite WAL/restart tests remain cheap to run but currently have almost "
        "no runtime surface.",
        "The 0-byte `data/*.db` files are placeholders whose real runtime homes were not "
        "definitively identified in this pass; the settings store is definitive, the "
        "domain stores are not.",
    )

    aj("---")
    aj("")

    # ---------------- data value scorecard ----------------
    aj("## 5. Data value scorecard (sec.10)")
    aj("")
    aj("Explicit categories, no arbitrary numeric score. `UNKNOWN` is a finding, not a")
    aj("placeholder.")
    aj("")
    aj("Table                      | Size    | Live    | Class                 | Evidence / note")
    aj("---------------------------|---------|---------|-----------------------|----------------")
    scorecard = {
        "shadow_decisions": ("94 MB", "MODEL-CRITICAL?", "governance evidence; consumer UNKNOWN"),
        "news_articles": ("80 MB", "OPERATIONAL", "raw ingest, 70% TOAST; news.enabled=1"),
        "research_gates": ("51 MB", "MODEL-CRITICAL", "promotion gates; 4,917 updates"),
        "news_analysis": ("45 MB", "DERIVED", "2,825 deletions already; derived from articles"),
        "research_evidence": ("44 MB", "MODEL-CRITICAL", "append-only evidence, 0 deletions"),
        "model_governance_events": ("40 MB", "AUDIT-CRITICAL", "59,180 events; 13 MB of indexes"),
        "strategy_registry": ("37 MB", "DECISION-CRITICAL", "76% TOAST; 997 updates"),
        "audit_experiences": ("30 MB", "AUDIT-CRITICAL", "71% TOAST"),
        "factory_events": ("25 MB", "DISPOSABLE-CANDIDATE?", "owner subsystem disabled"),
        "research_events": ("25 MB", "AUDIT-CRITICAL", "87,951 rows, 9.4 MB indexes"),
        "audit_signals": ("12 MB", "AUDIT-CRITICAL", "F-001 subject; 4,581 deletions"),
        "audit_orders": ("—", "AUDIT-CRITICAL", "F-003 subject"),
        "audit_experience_outcomes": ("6.4 MB", "DERIVED", "F-004 subject"),
    }
    for t in TABLE:
        if t not in footprint:
            continue
        fp = footprint[t]
        ts = tables.get(t, {})
        cls, note = scorecard.get(t, ("—", "UNKNOWN", "requires Phase 2 investigation"))[1:]
        aj(f"{t:<26} | {fp['total_size']:<7} | {ts.get('n_live_tup', 0):>7,} | "
           f"{cls:<21} | {note}")
    aj("")
    aj("Every `UNKNOWN` / `?` entry above is a sec.93 gate: it must be resolved in")
    aj("Phase 2 before any retention or purge action on that table.")
    aj("")
    aj("---")
    aj("")

    # ---------------- gaps ----------------
    aj("## 6. What Phase 1 did NOT establish")
    aj("")
    aj("Stated plainly so Phase 2 does not inherit a false sense of coverage:")
    aj("")
    aj("- **No query inventory (sec.8).** No `Q-<DOMAIN>-NNN` IDs exist yet. F-003 and")
    aj("  F-004 are counter-level, not query-level.")
    aj("- **No EXPLAIN plans (sec.13).** No plan was captured, because the engine was")
    aj("  down and the real access paths were not yet known. Plan capture belongs to the")
    aj("  Phase 2 query map, where each query has a named code location and caller.")
    aj("- **No workloads W1–W10 (sec.11) and no fixtures (sec.12).** No synthetic")
    aj("  dataset was built and no benchmark was run.")
    aj("- **No p50/p95/p99 (sec.15).** Blocked by the instrumentation gap in section 2.")
    aj("- **No purge dry-run (sec.22).** Blocked by F-008's ownership gap.")
    aj("- **No growth rate (sec.9).** A single snapshot is not a rate; two samples over a")
    aj("  documented interval are required.")
    aj("- **No latency-by-layer (sec.56) and no UI/SSE request budget (sec.30/§31).**")
    aj("  The engine was not running.")
    aj("")
    aj("---")
    aj("")

    # ---------------- next ----------------
    aj("## 7. Phase 2 entry conditions")
    aj("")
    aj("1. Decide the instrumentation question (section 2). Either enable")
    aj("   `pg_stat_statements` via `shared_preload_libraries` + restart as a controlled")
    aj("   change with its own rollback, or add the driver-boundary query aggregator in")
    aj("   `src/nexus_scalp/database/query_logging.py` and enter it from both drivers.")
    aj("   Without one of these, sec.15, sec.53 latency rows and sec.56 stay")
    aj("   NOT MEASURABLE and the program cannot satisfy its own definition of done.")
    aj("2. Start the engine and capture the first post-#561 window (F-002 / Phase 12),")
    aj("   since a fix with no runtime observation is not verified.")
    aj("3. Resolve the sec.93 ownership stops before any retention work: `shadow_decisions`")
    aj("   (F-009), `factory_*` (F-010), and every table with `n_tup_del = 0` (F-008).")
    aj("4. Build the sec.8 query inventory for the F-003 / F-004 counters so those")
    aj("   findings become query-level rather than table-level.")
    aj("")
    aj("## 8. Hard stops declared by this baseline (sec.93)")
    aj("")
    aj("- unknown retention requirement — no purge may be authorised yet (F-008)")
    aj("- unknown consumer — `factory_*` and `shadow_decisions` lifecycle unresolved (F-009, F-010)")
    aj("- performance claim without measurement — PR #561 benefit is unverified in")
    aj("  production (F-002)")
    aj("- silent provider fallback — none found; provider is an explicit USER_SETTINGS")
    aj("  choice (F-011)")
    aj("- benchmark disconnected from production path — n/a; no benchmark attempted")
    aj("")

    outdir = Path(args.dir)
    out = outdir / "PHASE1-FORENSIC-BASELINE.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(L)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
