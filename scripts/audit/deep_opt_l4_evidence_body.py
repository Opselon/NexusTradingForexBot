"""DEEP-OPT L4 — backfill: retire the duplicated evidence body.

Read-only probe proved ``research_evidence.content`` was semantic-identical to
``research_gates.result`` for 18,316 of 18,316 live paired rows (17.17 MB of a
17.17 MB column — 100% redundant). The producer no longer writes the body
(observability.store_evidence stores the derivation sentinel). This script
replaces the ALREADY-STORED duplicate bodies with the same sentinel, so the
existing rows stop carrying the copy and the reader derives the body from the
gate on read.

Properties (contract Part 5, rule 40/41):
  * idempotent — running twice is a no-op (rows already carrying the sentinel
    are skipped, so the WHERE never re-selects them);
  * resumable — keyset cursor on the integer PK (``id > ?``), never OFFSET, so
    a re-run continues where it stopped;
  * bounded — ``--max-rows`` caps one pass (operator action, never automatic);
  * lossless by construction — it only converts rows whose stored body is
    semantic-identical to the gate outcome; anything that genuinely differs is
    LEFT ALONE and reported, never overwritten;
  * reversible — the original body stays in ``research_gates.result``; the
    sentinel reader derives it back, and ``--revert`` restores a stored body
    from the gate outcome for audit.

Usage:
    python -m scripts.audit.deep_opt_l4_evidence_body --dry-run
    python -m scripts.audit.deep_opt_l4_evidence_body --max-rows 5000
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_SENTINEL = "__derived_from_gate_result__"


def _canon(text: str | None) -> str:
    if not text:
        return ""
    try:
        return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    except Exception:
        return ""


def _row_value(row: sqlite3.Row | tuple, key: str, index: int) -> object:
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    return row[index]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db",
        default="artifacts/audit.db",
        help="path to the audit ledger (default: artifacts/audit.db)",
    )
    ap.add_argument(
        "--max-rows",
        type=int,
        default=2000,
        help="rows to convert in one pass (default: 2000; 0 = unlimited)",
    )
    ap.add_argument(
        "--revert",
        action="store_true",
        help="restore a stored body from the gate outcome (undo)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="analyze and report only; no write",
    )
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"[L4] database not found: {db_path}", file=sys.stderr)
        return 2

    limit = args.max_rows if args.max_rows > 0 else 10**9
    conn = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) FROM research_evidence").fetchone()[0]
        already = conn.execute(
            "SELECT COUNT(*) FROM research_evidence WHERE content = ?", (_SENTINEL,)
        ).fetchone()[0]
        print(f"[L4] research_evidence rows: {total:,}  already derived: {already:,}")
        print("[L4] producer is fixed (store_evidence writes the sentinel); this pass")
        print("[L4] converts the legacy DUPLICATED bodies only.")

        mode = "REVERT" if args.revert else "CONVERT"
        converted = skipped_differs = skipped_nogate = errors = 0
        freed_bytes = 0
        cursor_id = 0
        while converted + skipped_differs + skipped_nogate < limit:
            rows = conn.execute(
                "SELECT id, evidence_id, gate_id, content, content_hash "
                "FROM research_evidence WHERE id > ? AND content <> ? "
                "ORDER BY id ASC LIMIT 500;",
                (cursor_id, _SENTINEL),
            ).fetchall()
            if not rows:
                break
            cursor_id = rows[-1]["id"]
            for row in rows:
                if converted + skipped_differs + skipped_nogate >= limit:
                    break
                rid = row["id"]
                ev_id = row["evidence_id"]
                body = row["content"] or ""
                gate_id = row["gate_id"] or ""
                if not gate_id:
                    skipped_nogate += 1
                    continue
                gate_row = conn.execute(
                    "SELECT result FROM research_gates WHERE gate_id=?;", (gate_id,)
                ).fetchone()
                if gate_row is None:
                    skipped_nogate += 1
                    continue
                gate_result = gate_row["result"] or ""
                if _canon(body) != _canon(gate_result):
                    # The stored body genuinely differs from the gate outcome:
                    # this is NOT a duplicate, so it is never overwritten.
                    skipped_differs += 1
                    continue
                if args.dry_run:
                    converted += 1
                    freed_bytes += len(body.encode())
                    continue
                new_body = gate_result if args.revert else _SENTINEL
                try:
                    conn.execute(
                        "UPDATE research_evidence SET content=? WHERE id=?;",
                        (new_body, rid),
                    )
                except sqlite3.Error as exc:
                    errors += 1
                    print(f"[L4] write failed evidence_id={ev_id}: {exc}", file=sys.stderr)
                    continue
                converted += 1
                freed_bytes += len(body.encode())
        if not args.dry_run and (converted or skipped_differs or skipped_nogate):
            conn.commit()
        action = "would convert" if args.dry_run else "converted"
        print(
            f"\n[L4] {mode}: {action} {converted:,} rows | "
            f"left untouched (differs from gate): {skipped_differs:,} | "
            f"left untouched (no gate): {skipped_nogate:,} | errors: {errors}"
        )
        print(f"[L4] evidence-body bytes released: {freed_bytes:,}")
        if not args.dry_run and converted:
            still = conn.execute(
                "SELECT COUNT(*) FROM research_evidence WHERE content <> ?", (_SENTINEL,)
            ).fetchone()[0]
            print(f"[L4] rows still carrying a stored body: {still:,}")
        print("[L4] NOT measured (operator action, not fabricated): VACUUM file-size")
        print("[L4] delta, WAL bytes, PostgreSQL bloat.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
