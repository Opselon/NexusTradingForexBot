#!/usr/bin/env python3
"""Duplicate-canonical-row detector for shared SSOT metadata tables.

BUG-309 / ML-QA-006 — companion to check_merge_marker_residue.py.

WHY THIS EXISTS
---------------
PR #347 (merge 159cd3b3) leaked diff3 middle-arm residue into the two SSOT
metadata files (`agents/taskboard.md`, `docs/ml-system/06_TASK_LEDGER.md`)
and bracketed THREE contradictory rows for the same task id (`ML-CI-002`):
one `DONE`, one `**BLOCKED**`, one `DONE` with the wrong owner. The residue
marker itself was fixed by the merge-marker gate (ML-QA-005), but the
*duplicate-row class* it exposed is independent of markers: every parallel
agent PR rewrites these shared tables from its own snapshot, and a bad
rebase/merge can silently commit two (or more) canonical rows for the same
task id. No existing gate looked at table structure, so the ledger asserted
three different states for one task for weeks.

WHAT COUNTS AS A DEFECT
-----------------------
For each declared table (path + optional section header regex), the scanner
parses the markdown TABLE rows only (lines starting with `|`) and:

1. counts how many rows carry the same canonical task id;
2. flags ids appearing on >1 row inside one table UNLESS every duplicate
   row set is permitted by an explicit allowlist (e.g. the Historical
   Migration Map legitimately names each new id once per legacy row... and
   is therefore scoped OUT by its own section, see DEFAULT_TABLES).

Backticked and plain ids are the same identity (`ML-X-1` == `` `ML-X-1` ``).

Modes:
  exit 0 — clean (every table has at most one canonical row per id)
  exit 1 — defect found (duplicate canonical rows)
  exit 2 — usage/configuration error

`--json` prints a single machine-readable payload via typer.echo (BUG-300:
never console.print — Rich wraps long lines at 80 cols and corrupts JSON).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Each entry: (path, section_regex_or_None, allowlisted_ids).
# section_regex scopes the scan to ONE table section of a big file; when
# None, every table row in the file is scanned as one logical table.
DEFAULT_TABLES: tuple[dict[str, Any], ...] = (
    {
        "path": "docs/ml-system/06_TASK_LEDGER.md",
        "section": r"^## 2\. Master Backlog Table",
        "allow": set(),
    },
    {
        "path": "docs/ml-system/TASK_BOARD.md",
        "section": None,
        "allow": set(),
    },
)

_TASK_ID_RE = re.compile(r"ML-[A-Z]+-\d+")
_ROW_SPLIT_RE = re.compile(r"\|")


def _iter_section_lines(text: str, section_re: re.Pattern[str] | None) -> list[tuple[int, str]]:
    raw_lines = text.splitlines()
    indexed = list(enumerate(raw_lines, 1))
    if section_re is None:
        return indexed
    start: int | None = None
    for i, ln in indexed:
        if start is None and section_re.search(ln):
            start = i
            continue
        if start is not None and ln.startswith("## "):
            return indexed[start - 1 : i - 1]
    if start is None:
        return []
    return indexed[start - 1 :]


def scan_file(
    root: Path,
    rel_path: str,
    section_re: re.Pattern[str] | None,
    allow: set[str],
) -> dict[str, Any]:
    """Return a scan result dict for one (path, section) table."""
    result: dict[str, Any] = {
        "path": rel_path,
        "exists": True,
        "rows_scanned": 0,
        "unique_ids": 0,
        "duplicates": [],
        "section_matched": section_re is not None,
    }
    path = root / rel_path
    if not path.exists():
        result["exists"] = False
        result["error"] = "file not found"
        return result
    text = path.read_text(encoding="utf-8", errors="replace")
    if text is None:
        result["error"] = "empty file"
        return result
    lines = _iter_section_lines(text, section_re)

    seen: dict[str, list[int]] = {}
    header_rows = 0
    separator_rows = 0
    for lineno, line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in _ROW_SPLIT_RE.split(stripped)]
        if all(set(c) <= set("-: ") and c for c in cells if c):
            separator_rows += 1
            continue
        # header detection: any cell equal to 'Task ID' / 'TASK-SWARM-GOV' etc.
        if any(c.lower() in {"task id", "task-id", "id"} for c in cells):
            header_rows += 1
            continue
        ids = set(_TASK_ID_RE.findall(stripped))
        if not ids:
            continue
        # A canonical row is identified by its LEADING task id cell —
        # dependency/prose mentions of other ids inside the row body do not
        # make those ids canonical here.
        first_cell = cells[1] if len(cells) > 1 else ""
        lead = _TASK_ID_RE.search(first_cell.replace("`", ""))
        if not lead:
            continue
        tid = lead.group(0)
        if tid in allow:
            continue
        result["rows_scanned"] += 1
        seen.setdefault(tid, []).append(lineno)

    result["unique_ids"] = len(seen)
    for tid, lns in sorted(seen.items()):
        if len(lns) > 1:
            result["duplicates"].append({"task_id": tid, "lines": lns})
    return result


def run_scan(root: Path) -> tuple[int, list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    rc = 0
    for table in DEFAULT_TABLES:
        section_raw = table["section"]
        section_re = re.compile(section_raw, re.M) if section_raw else None
        res = scan_file(root, table["path"], section_re, table["allow"])
        if not res["exists"]:
            # a missing SSOT file is a config-level defect, not a duplicate
            findings.append(res)
            rc = 1
            continue
        if res["duplicates"]:
            findings.append(res)
            rc = 1
    return rc, findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a single machine-readable JSON payload on stdout",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="repo root (default: cwd)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        if args.json:
            # typer.echo-equivalent: plain sys.stdout.write, no Rich wrapping
            sys.stdout.write(
                json.dumps(
                    {
                        "gate": "duplicate_task_rows",
                        "status": "CONFIG_ERROR",
                        "error": f"root not a directory: {root}",
                    }
                )
                + "\n"
            )
        else:
            print(f"duplicate_task_rows: CONFIG_ERROR root not a directory: {root}")
        return 2

    rc, findings = run_scan(root)
    scanned = [f for f in findings if f.get("exists")]
    total_dupes = sum(len(f["duplicates"]) for f in scanned)

    if args.json:
        payload = {
            "gate": "duplicate_task_rows",
            "status": "DEFECT" if rc == 1 else ("CONFIG_ERROR" if rc == 2 else "CLEAN"),
            "tables_scanned": len(DEFAULT_TABLES),
            "duplicates_found": total_dupes,
            "findings": findings,
        }
        sys.stdout.write(json.dumps(payload, indent=None) + "\n")
    elif rc == 0:
        print(
            f"DUPLICATE_TASK_ROWS_OK: {len(DEFAULT_TABLES)} tables scanned, "
            "0 duplicate canonical rows"
        )
    else:
        print(f"DUPLICATE_TASK_ROWS_DEFECT: {total_dupes} duplicate id(s)")
        for f in scanned:
            for d in f["duplicates"]:
                print(f"  {f['path']}: {d['task_id']} rows at lines {d['lines']}")
        for f in findings:
            if not f.get("exists"):
                print(f"  {f['path']}: MISSING ({f.get('error')})")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
