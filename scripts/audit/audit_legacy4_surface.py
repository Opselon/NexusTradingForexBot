#!/usr/bin/env python3
"""ML-ARCH-001 audit probe: ScalpNet 4-logit WAIT legacy surface (torch-free).

Read-only static audit that answers the ML-ARCH-001 UNKNOWN:

    "Whether any production client still depends on 4-wide checkpoint loading."

The probe is deliberately torch-free (stdlib + ``pathlib`` only) so it can run in
the static CI lane and on a slim verification venv without pulling the
torch/polars chain. It reports:

* every ``num_classes = <N>`` literal in ``src/`` with its enclosing function, and
  classifies the site as PRODUCTION / TEST-TOOLING / SHADOW / SMOKE;
* the 4-wide blast radius in ``tests/`` (Option A cost);
* whether any 4-wide ``.pt`` / ``model.meta.json`` artifact is committed to git
  (the Option A risk surface);
* a derived verdict: is the legacy-4 branch load-bearing or dead weight?

Direction of truth is the source tree at HEAD. Nothing is imported from
``nexus_scalp``; the probe parses text, so it cannot mask a drift by inheriting a
stale constant.

Exit codes: 0 = audit produced a report; 1 = usage / io error. A large legacy
surface is NOT an error here -- this is an audit, not a gate; ML-CI-002 owns the
gate class for contract drift.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Match `num_classes = 4` / `num_classes=4` but not the prose in comments that
# merely mentions the value. Anchored on the assignment shape only.
_NUM_CLASSES = re.compile(r"\bnum_classes\s*=\s*(\d+)")

# A line is a comment-only line (leading whitespace then #) -- those mentions are
# historical prose, not live literals. Inline trailing comments are kept: the code
# before them is live.
_COMMENT_LINE = re.compile(r"^\s*#")

#: Classify a source path by the subsystem it belongs to. The serving path is the
#: only class whose 4-wide literals are money-relevant.
_PATH_CLASS = [
    ("PRODUCTION-SERVING", ("application/live_engine.py", "application/live/inference.py")),
    ("SHADOW", ("shadow/",)),
    ("SMOKE", ("smoke/runner.py",)),
    ("WEB", ("web/model_governance_routes.py",)),
]


def _rel_posix(path: Path, root: Path) -> str:
    """Repo-relative path with ``/`` separators on every OS.

    ``Path.relative_to`` joins with ``os.sep`` (``\\`` on Windows). This probe's
    classification table and its tests compare forward-slash substrings, so on
    the OS Matrix the Windows leg read every site as OTHER and misclassified the
    smoke/shadow legacy-4 surface (BUG-307D). Normalise once, at the boundary.
    """
    return path.relative_to(root).as_posix()


def _classify(rel: str) -> str:
    # ``rel`` is forward-slash normalised by _rel_posix; the table is POSIX.
    for label, prefixes in _PATH_CLASS:
        if any(p in rel for p in prefixes):
            return label
    return "OTHER"


def _enclosing_function(lines: list[str], idx: int) -> str:
    """Nearest preceding `def` at column <= the match (cheap heuristic)."""
    for j in range(idx, -1, -1):
        line = lines[j]
        if re.match(r"^(?:\s*)(?:async\s+)?def\s+([\w-]+)", line):
            return re.match(r"^(?:\s*)(?:async\s+)?def\s+([\w-]+)", line).group(1)  # type: ignore[union-attr]
    return "<module>"


def scan_literals(root: Path, sub: str) -> list[dict[str, object]]:
    """All num_classes literals under `root/sub`, with function context."""
    out: list[dict[str, object]] = []
    base = root / sub
    if not base.exists():
        return out
    for path in sorted(base.rglob("*.py")):
        rel = _rel_posix(path, root)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        for m in _NUM_CLASSES.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            if _COMMENT_LINE.match(lines[lineno - 1]):
                continue
            out.append(
                {
                    "path": rel,
                    "line": lineno,
                    "value": int(m.group(1)),
                    "function": _enclosing_function(lines, lineno - 1),
                    "subsystem": _classify(rel),
                    "code": lines[lineno - 1].strip(),
                }
            )
    return out


def committed_artifacts(root: Path) -> dict[str, Any]:
    """Any 4-wide model artifact committed to git -- the Option A risk surface.

    Uses `git ls-tree` so untracked local files (slim venv, scratch) never inflate
    the surface. Returns counts only, never artifact bytes.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "ls-tree", "-r", "HEAD", "--name-only"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"error": "git ls-tree unavailable"}

    paths = [p for p in proc.stdout.splitlines() if p.strip()]
    pt_paths = [p for p in paths if p.endswith(".pt")]
    meta_paths = [p for p in paths if p.endswith((".meta.json", "manifest.json"))]
    return {
        "committed_pt_count": len(pt_paths),
        "committed_pt_paths": pt_paths[:10],
        "committed_meta_count": len(meta_paths),
        "committed_meta_paths": meta_paths[:10],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".", help="repo root (default CWD)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)

    root = Path(args.repo).resolve()
    if not (root / "src").exists():
        print(f"error: no src/ under {root}", file=sys.stderr)
        return 1

    src_hits = scan_literals(root, "src")
    test_hits = scan_literals(root, "tests")

    legacy_src = [h for h in src_hits if h["value"] == 4]
    legacy_tests = [h for h in test_hits if h["value"] == 4]

    # The money question: does a 4-wide literal sit on the production serving path?
    serving_legacy = [h for h in legacy_src if h["subsystem"] == "PRODUCTION-SERVING"]

    artifacts = committed_artifacts(root)
    raw_pt = artifacts.get("committed_pt_count", 0)
    committed_pt: int = int(raw_pt) if isinstance(raw_pt, (int, str)) else 0

    # Verdict logic: the legacy branch is DEAD WEIGHT when (a) no 4-wide literal
    # is on the serving path AND (b) no 4-wide artifact is committed to git.
    if not serving_legacy and committed_pt == 0:
        verdict = "LEGACY-4-DEAD-WEIGHT"
        reason = (
            "no num_classes=4 literal on the production serving path and zero "
            "committed .pt artifacts -- the 4-wide branch is compatibility-only"
        )
    elif serving_legacy:
        verdict = "LEGACY-4-LOAD-BEARING"
        reason = "a production serving path still constructs a 4-wide head"
    else:
        verdict = "LEGACY-4-COMMITTED-ARTIFACT"
        reason = "a 4-wide checkpoint is committed to git"

    report: dict[str, Any] = {
        "probe": "ml-arch-001-legacy4-surface",
        "repo_root": str(root),
        "trained_class_count_src": 3,
        "legacy_head_classes_src": 4,
        "src_num_classes_sites": len(src_hits),
        "src_legacy4_sites": len(legacy_src),
        "src_legacy4_by_subsystem": {
            label: sum(1 for h in legacy_src if h["subsystem"] == label)
            for label in ["PRODUCTION-SERVING", "SHADOW", "SMOKE", "WEB", "OTHER"]
        },
        "src_legacy4_detail": legacy_src,
        "tests_legacy4_occurrences": len(legacy_tests),
        "tests_legacy4_files": sorted({h["path"] for h in legacy_tests}),
        "committed_artifacts": artifacts,
        "verdict": verdict,
        "verdict_reason": reason,
    }

    if args.json:
        # Unwrapped stdout -- never console.print, which line-wraps and breaks
        # json.loads for machine consumers (BUG-300 class).
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        print(f"ML-ARCH-001 legacy-4 surface audit  @  {root}")
        print("-" * 68)
        print(f"src/ num_classes literals ......... {report['src_num_classes_sites']}")
        print(f"src/ legacy-4 literals ............ {len(legacy_src)}")
        for label in ["PRODUCTION-SERVING", "SHADOW", "SMOKE", "WEB", "OTHER"]:
            n = report["src_legacy4_by_subsystem"][label]
            if n:
                print(f"    {label:<20} {n}")
        for h in legacy_src:
            print(f"    {h['path']}:{h['line']}  in {h['function']}()  [{h['subsystem']}]")
        print(
            f"tests/ legacy-4 occurrences ....... {len(legacy_tests)} "
            f"across {len(report['tests_legacy4_files'])} files"
        )
        print(f"committed .pt artifacts ........... {committed_pt}")
        print("-" * 68)
        print(f"VERDICT: {verdict}")
        print(f"         {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
