"""Duplicate ADR/DEC identifier guard (P3) — one ID, one canonical record.

P3 finding (verified): ``agents/decisions/`` carried TWO files claiming the
DEC-0002 identity with DIFFERENT subjects:

    DEC-0002-hermes-kanban-swarm-integration.md   (re-introduced by an
        installer commit on 2026-09-02, c13bcce3)
    DEC-0002-nodejs-runtime-role.md               (the original, on main)

The kanban-swarm decision already had a canonical renumbered home as
DEC-0003 (with an explicit renumbering note). The duplicate DEC-0002 file
was later re-added, re-creating the collision this repo's ledger rules
forbid (see bugs.md ledger-collision rule).

Resolution (this commit):
* the duplicate DEC-0002-hermes-kanban-swarm-integration.md file is
  RETIRED (kept as an empty tombstone pointing at DEC-0003 so inbound
  links never 404 silently); DEC-0003 remains the canonical record;
* ``scripts/ci/check_decision_ids.py`` statically enforces 1:1
  DEC-XXXX -> file uniqueness across agents/decisions/ (and fails on
  tombstones that still carry full decision content);
* wired into scripts/ci/scan_secrets.py-adjacent local checks and the
  CI static lane so the regression cannot return unnoticed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

__all__ = ["find_duplicate_decision_ids", "main"]

#: A decision file named DEC-XXXX-<slug>.md
_DEC_FILE = re.compile(r"^DEC-(\d{3,4})-([a-z0-9-]+)\.md$", re.IGNORECASE)
#: The H1 title inside a record.
_H1 = re.compile(r"^#\s+(DEC-\d{3,4})\b", re.IGNORECASE | re.MULTILINE)


def find_duplicate_decision_ids(decisions_dir: Path | str) -> list[str]:
    """Returns a list of human-readable duplicate-ID findings (empty = OK).

    A retired-duplicate tombstone (a file whose body no longer carries a
    `# DEC-XXXX` H1 title, i.e. full decision content was removed and
    replaced with a redirect note) does NOT count as a duplicate — the
    tombstone exists only so inbound links resolve to the resolution note.
    """
    root = Path(decisions_dir)
    if not root.is_dir():
        return [f"decisions directory not found: {root}"]
    owners: dict[str, list[str]] = {}
    has_content: dict[str, list[str]] = {}
    for f in sorted(root.glob("DEC-*.md")):
        m = _DEC_FILE.match(f.name)
        dec_id = m.group(1).upper() if m else f.stem.split("-")[1].upper()
        owners.setdefault(dec_id, []).append(f.name)
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - unreadable file
            has_content.setdefault(dec_id, []).append(f.name)
            continue
        # Full decision content = an H1 title line declaring the DEC id.
        if _H1.search(text):
            has_content.setdefault(dec_id, []).append(f.name)
    problems: list[str] = []
    for dec_id, content_files in sorted(has_content.items()):
        if len(content_files) > 1:
            all_files = owners.get(dec_id, [])
            extra = [f for f in all_files if f not in content_files]
            problems.append(
                f"DEC-{dec_id} has {len(content_files)} content-bearing files "
                f"({', '.join(content_files)})"
                + (f"; tombstones: {', '.join(extra)}" if extra else "")
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    decisions = repo / "agents" / "decisions"
    problems = find_duplicate_decision_ids(decisions)
    if problems:
        print("::error::duplicate decision IDs detected")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Decision-ID uniqueness check clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
