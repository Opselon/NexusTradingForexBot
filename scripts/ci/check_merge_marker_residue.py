#!/usr/bin/env python3
"""Merge-marker residue guard: failed conflict resolution must not reach git.

Root cause (ML-QA-005): PR #347 (merge 159cd3b3) shipped the *middle* arm of a
diff3 conflict into the working tree as literal content::

    ||||||| eb73440a
    | `ML-CI-002` | ... | **BLOCKED** | ... |

with the DONE side following as the "accepted" resolution. Only the leading
``<<<<<<<`` line was stripped, so neither ``git grep -n '<<<<<<<'`` nor a
human skimming the table caught it. The residue sat on ``main`` for weeks in
two SSOT metadata files (``agents/taskboard.md`` and
``docs/ml-system/06_TASK_LEDGER.md``), leaving contradictory duplicate rows
(one saying BLOCKED, one DONE) that every task-selection cycle then had to
re-litigate by hand.

Marker families detected — deliberately restricted to the three UNAMBIGUOUS
diff3/merge2 line markers:

* ``<<<<<<<`` conflict open (merge2/diff3)
* ``|||||||`` diff3 "original" separator — the arm PR #347 leaked
* ``>>>>>>>`` conflict close

``=======`` is deliberately NOT matched. It is inherently ambiguous: a lone
``=======`` is a legitimate RST underline or setext heading, and both real
RST section underlines in this repo ship exactly that shape
(``model_lifecycle/champion_sentinel.py:5`` and
``research/trading_metrics.py:6``). A bare-separator rule therefore cannot be
made precise without markdown/RST parsing, and it buys nothing: any conflict
block that still contains ``=======`` also still contains ``<<<<<<<`` and
``>>>>>>>``, which this gate already flags. The partial-marker leak that
actually reached main was the ``|||||||`` arm, which is unambiguous and is
caught.

Because partial-marker shapes (this incident's exact form) can only be
recognised per-file, the scanner reads every tracked text file from
``git ls-files`` — not just a fixed directory — so a future leak into src/,
frontend/ or a lockfile is caught too.

Exit codes: 0 = clean, 1 = residue found, 2 = tooling/error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_MAX_BYTES = 8_000_000  # skip anything absurd; tracked binaries are rare

MARKER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("conflict-open", "<<<<<<<"),
    ("diff3-original", "|||||||"),
    ("conflict-close", ">>>>>>>"),
)


@dataclass
class Finding:
    kind: str
    path: str
    line_no: int
    detail: str


@dataclass
class Report:
    files_scanned: int = 0
    text_scanned: int = 0
    skipped_binary: int = 0
    skipped_oversize: int = 0
    findings: list[Finding] = field(default_factory=list)
    tooling_errors: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.findings and not self.tooling_errors

    def to_json(self) -> str:
        payload = {
            "gate": "merge_marker_residue",
            "ok": self.ok,
            "files_scanned": self.files_scanned,
            "text_scanned": self.text_scanned,
            "skipped_binary": self.skipped_binary,
            "skipped_oversize": self.skipped_oversize,
            "findings": [
                {
                    "kind": f.kind,
                    "path": f.path,
                    "line_no": f.line_no,
                    "detail": f.detail,
                }
                for f in self.findings
            ],
            "tooling_errors": list(self.tooling_errors),
            "elapsed_ms": round(self.elapsed_ms, 1),
        }
        return json.dumps(payload, indent=2)


def _tracked_files(repo: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout
    files: list[Path] = []
    for chunk in out.split("\0"):
        if chunk:
            files.append(repo / chunk)
    return files


def _looks_textual(raw: bytes) -> bool:
    if b"\x00" in raw:
        return False
    # Cheap heuristic: high proportion of non-ASCII control bytes -> binary.
    sample = raw[:8192]
    if not sample:
        return True
    ctrl = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return (ctrl / len(sample)) < 0.30


def _scan_lines(path: Path, rel: str, lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for i, line in enumerate(lines, start=1):
        for kind, needle in MARKER_PATTERNS:
            if line.startswith(needle):
                findings.append(
                    Finding(
                        kind=kind,
                        path=rel,
                        line_no=i,
                        detail=line.strip()[:160],
                    )
                )
                break  # one finding per line is enough
    return findings


def check_residue(repo: Path | None = None) -> Report:
    """Runs the full residue scan. Deterministic, offline, dependency-free."""
    import time

    t0 = time.perf_counter()
    repo = Path(repo) if repo is not None else REPO
    rep = Report()
    try:
        files = _tracked_files(repo)
    except FileNotFoundError as exc:
        rep.tooling_errors.append(f"git ls-files failed: {exc}")
        return rep
    except subprocess.CalledProcessError as exc:
        rep.tooling_errors.append(
            f"git ls-files rc={exc.returncode}: {(exc.stderr or '').strip()[:200]}"
        )
        return rep

    rep.files_scanned = len(files)
    for path in files:
        try:
            st = path.stat()
        except OSError:
            continue  # deleted-but-tracked; not our concern
        if not st.st_size:
            continue
        if st.st_size > _MAX_BYTES:
            rep.skipped_oversize += 1
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if not _looks_textual(raw):
            rep.skipped_binary += 1
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        rep.text_scanned += 1
        lines = text.splitlines()
        rep.findings.extend(_scan_lines(path, str(path.relative_to(repo)), lines))
    rep.elapsed_ms = (time.perf_counter() - t0) * 1000
    return rep


def _format_text(rep: Report) -> str:
    lines: list[str] = []
    if rep.ok:
        lines.append(
            f"Merge-marker residue clean: {rep.text_scanned}/{rep.files_scanned} "
            f"tracked files scanned ({rep.elapsed_ms:.1f} ms), "
            f"{rep.skipped_binary} binary + {rep.skipped_oversize} oversize skipped."
        )
        return "\n".join(lines) + "\n"
    for err in rep.tooling_errors:
        lines.append(f"::error::Merge-marker residue tooling error: {err}")
    for f in rep.findings:
        lines.append(f"::error::Merge-marker residue [{f.kind}] {f.path}:{f.line_no}: {f.detail}")
    lines.append(
        f"\n{len(rep.findings)} finding(s) across {rep.text_scanned} text files "
        f"({rep.elapsed_ms:.1f} ms). Resolve the conflict properly (pick ONE side), "
        "never leave the diff3 '|||||||' arm in the tree."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        default=str(REPO),
        help="repository root (default: inferred from script location)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable report instead of text",
    )
    args = ap.parse_args(argv)

    repo = Path(args.root).resolve()
    if not repo.is_dir():
        print(f"::error::repository root not found: {repo}", file=sys.stderr)
        return 2
    rep = check_residue(repo)
    if args.json:
        # Unwrapped stdout (a rich console.print would wrap at 80 columns and
        # corrupt the payload with literal newlines — the BUG-300 CLI lesson).
        sys.stdout.write(rep.to_json() + "\n")
    else:
        sys.stdout.write(_format_text(rep))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
