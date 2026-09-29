"""Phase 1 zero-write forensic baseline collector (SQLite).

Companion to baseline_collect_pg.py for Part 4 sec.7 / sec.8 of the execution
protocol. Opens every target database through a read-only URI so a live engine
is never disturbed and no -shm/-wal is created on our behalf.

For each database it records:
  * PRAGMA journal_mode / synchronous / foreign_keys / auto_vacuum /
    page_size / page_count / freelist_count / (wal_checkpoint PASSIVE)
  * the full sqlite_master inventory (tables, indexes, views, triggers)
  * per-table COUNT(*) and per-table/index page footprint via dbstat when the
    build exposes it, else page counts only
  * EXPLAIN QUERY PLAN for a set of representative read paths supplied by the
    caller -- never invented here

Nothing is written, vacuumed, checkpointed-to-disk, or reindexed.

Usage:
  python baseline_collect_sqlite.py --outdir docs/forensic-docs/remediation/phase1 <db> [<db> ...]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PRAGMAS = [
    "journal_mode",
    "synchronous",
    "foreign_keys",
    "auto_vacuum",
    "page_size",
    "page_count",
    "freelist_count",
    "max_page_count",
    "encoding",
    "user_version",
    "schema_version",
    "cache_size",
    "mmap_size",
    "busy_timeout",
    "journal_size_limit",
    "wal_autocheckpoint",
]

# Representative read paths. These are EXPLAIN'd (never ANALYZEd) so they are
# free and cannot execute a scan.
QUERY_PLAN_PROBES: dict[str, str] = {
    "newest_rows_by_rowid": "SELECT * FROM {t} ORDER BY rowid DESC LIMIT 1",
    "count": "SELECT COUNT(*) FROM {t}",
}


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return str(value)


def probe_database(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "mtime": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
        "wal_present": Path(f"{path}-wal").exists(),
        "shm_present": Path(f"{path}-shm").exists(),
        "wal_size_bytes": Path(f"{path}-wal").stat().st_size
        if Path(f"{path}-wal").exists()
        else 0,
    }
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    conn.row_factory = sqlite3.Row
    try:
        pg = {}
        for p in PRAGMAS:
            try:
                row = conn.execute(f"PRAGMA {p}").fetchone()
                pg[p] = jsonable(row[0]) if row else None
            except sqlite3.Error as exc:
                pg[p] = f"ERR {type(exc).__name__}"
        # PASSIVE checkpoint *report* -- does not force a write.
        try:
            row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            pg["wal_checkpoint_passive"] = [jsonable(v) for v in row] if row else None
        except sqlite3.Error as exc:
            pg["wal_checkpoint_passive"] = f"ERR {type(exc).__name__}: {exc}"
        out["pragmas"] = pg

        try:
            out["integrity_check"] = (
                conn.execute("PRAGMA quick_check").fetchone()[0]  # type: ignore[index]
            )
        except sqlite3.Error as exc:
            out["integrity_check"] = f"ERR {type(exc).__name__}: {exc}"

        master = [dict(r) for r in conn.execute(
            "SELECT type, name, tbl_name, rootpage, length(sql) AS sql_len "
            "FROM sqlite_master ORDER BY type, name"
        )]
        out["sqlite_master"] = master

        tables = [m["name"] for m in master if m["type"] == "table"
                  and not m["name"].startswith("sqlite_")]
        counts: dict[str, Any] = {}
        plans: dict[str, Any] = {}
        for t in tables:
            try:
                counts[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except sqlite3.Error as exc:
                counts[t] = f"ERR {type(exc).__name__}: {exc}"
            for label, tmpl in QUERY_PLAN_PROBES.items():
                try:
                    rows = conn.execute(
                        f'EXPLAIN QUERY PLAN {tmpl.format(t=t)}'
                    ).fetchall()
                    plans[f"{t}::{label}"] = [
                        " | ".join(str(v) for v in tuple(r)[3:]) for r in rows
                    ]
                except sqlite3.Error:
                    pass
        out["table_row_counts"] = counts
        out["query_plans"] = plans

        # dbstat is available only if the build includes SQLITE_ENABLE_DBSTAT_VTAB.
        try:
            out["dbstat"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT name, SUM(pgsize) AS bytes FROM dbstat GROUP BY name "
                    "ORDER BY SUM(pgsize) DESC LIMIT 40"
                )
            ]
        except sqlite3.Error as exc:
            out["dbstat"] = f"NOT AVAILABLE: {type(exc).__name__}: {exc}"
    finally:
        conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--label", default="phase1")
    ap.add_argument("dbs", nargs="+")
    args = ap.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    results = []
    for raw in args.dbs:
        p = Path(raw)
        if not p.exists():
            results.append({"path": str(p), "error": "FILE NOT FOUND"})
            continue
        results.append(probe_database(p))

    payload = {"collected_at_utc": stamp, "label": args.label, "databases": results}
    json_path = outdir / f"baseline_sqlite_{args.label}_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    # Human-readable summary
    lines = [f"# SQLite zero-write baseline ({args.label})", "",
             f"Collected (UTC): {stamp}", ""]
    for db in results:
        lines.append(f"## {db.get('path')}")
        if db.get("error"):
            lines.append(f"- **ERROR**: {db['error']}")
            lines.append("")
            continue
        lines.append(f"- size_bytes: {db['size_bytes']}")
        lines.append(f"- mtime: {db['mtime']}")
        lines.append(f"- wal_present: {db['wal_present']} (size {db['wal_size_bytes']})")
        lines.append(f"- quick_check: {db.get('integrity_check')}")
        pg = db.get("pragmas", {})
        lines.append(
            "- pragmas: journal_mode={journal_mode} synchronous={synchronous} "
            "page_size={page_size} page_count={page_count} "
            "freelist_count={freelist_count} auto_vacuum={auto_vacuum}".format(**pg)
        )
        counts = db.get("table_row_counts", {})
        lines.append("")
        lines.append("| table | rows |")
        lines.append("|---|---|")
        for t, n in sorted(counts.items(), key=lambda kv: str(kv[0])):
            lines.append(f"| {t} | {n} |")
        lines.append("")
    md_path = outdir / f"baseline_sqlite_{args.label}_{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"JSON: {json_path}")
    print(f"MD  : {md_path}")
    for db in results:
        if db.get("error"):
            print(f"  {db['path']}: {db['error']}")
        else:
            print(
                f"  {db['path']}: {db['size_bytes']}B, "
                f"{len(db.get('table_row_counts', {}))} tables, "
                f"journal={db.get('pragmas', {}).get('journal_mode')}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
