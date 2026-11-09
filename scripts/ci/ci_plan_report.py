#!/usr/bin/env python3
"""ci_plan_report.py — pretty, colourised CI planner report.

Prints a human-readable table of what the C planner decided (changed files,
graph size, selected tests, reasons, timings) to STDOUT so it appears LIVE in
the GitHub Actions log, and mirrors it into $GITHUB_STEP_SUMMARY.

Reads only the plan/manifest JSON the planner already writes. Never prints
secrets: paths, counts, reasons and timings only.

Usage:
    python scripts/ci/ci_plan_report.py --results ci-results --plan ci-results/test-plan/plan.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# CI runners are UTF-8, but a local Windows console is cp1252 and would
# raise UnicodeEncodeError on the box-drawing characters. Reconfigure
# stdout to UTF-8 with a safe fallback before anything is printed.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
from collections import Counter

# ANSI colours (GitHub Actions renders these in the log).
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_CYAN = "\033[96m"
C_MAGENTA = "\033[95m"
C_BLUE = "\033[94m"


def _c(text: str, colour: str) -> str:
    return f"{colour}{text}{C_RESET}"


def bar(value: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return ""
    filled = round(width * value / total)
    filled = max(0, min(width, filled))
    return "#" * filled + "-" * (width - filled)


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="ci-results")
    ap.add_argument("--plan", default="")
    ap.add_argument("--total-tests", type=int, default=0)
    args = ap.parse_args(argv)

    plan_path = args.plan or os.path.join(args.results, "test-plan", "plan.json")
    manifest_path = os.path.join(args.results, "test-plan", "execution-manifest.json")

    plan = load(plan_path)
    manifest = load(manifest_path)

    lines: list[str] = []

    def emit(s: str = "") -> None:
        lines.append(s)
        print(s, flush=True)

    emit()
    emit(_c("╔" + "═" * 74 + "╗", C_CYAN))
    emit(
        _c("║", C_CYAN)
        + _c("  NSE CI — NATIVE C17 TEST IMPACT REPORT".ljust(74), C_BOLD)
        + _c("║", C_CYAN)
    )
    emit(_c("╚" + "═" * 74 + "╝", C_CYAN))

    if not plan:
        emit(_c(f"  no plan at {plan_path}", C_RED))
        return 0

    mode = plan.get("selection_mode", "?")
    full = plan.get("full_suite_required")
    fallback = plan.get("fallback_reason")
    selected = plan.get("selected_tests", [])
    n_sel = len(selected)

    # ---- engine / graph -------------------------------------------------
    emit()
    emit(_c("  ENGINE", C_BOLD))
    emit(
        f"    planner        : {_c(manifest.get('planner', {}).get('impl', 'nse-native-c17'), C_GREEN)}"
    )
    emit(f"    engine version : {manifest.get('planner', {}).get('engine_version', '0.1.0')}")
    emit(f"    parity         : {_c(str(manifest.get('parity_mode', 'n/a')), C_GREEN)}")
    emit(f"    plan digest    : {_c(plan.get('plan_digest', '')[:16] + '...', C_DIM)}")

    # ---- change set -----------------------------------------------------
    changed = manifest.get("changed_files", [])
    if not changed:
        changed = manifest.get("changed", [])
    emit()
    emit(_c("  CHANGE SET", C_BOLD))
    emit(f"    changed files  : {_c(str(len(changed)), C_YELLOW)}")
    for f in changed[:12]:
        emit(f"      {_c('•', C_DIM)} {f}")
    if len(changed) > 12:
        emit(f"      {_c('…', C_DIM)} and {len(changed) - 12} more")

    # ---- selection ------------------------------------------------------
    total = args.total_tests or manifest.get("total_tests") or 0
    emit()
    emit(_c("  SELECTION", C_BOLD))
    mode_colour = C_GREEN if mode == "targeted" else C_YELLOW
    emit(f"    mode           : {_c(mode, mode_colour)}")
    emit(f"    full suite     : {_c('YES' if full else 'no', C_RED if full else C_GREEN)}")
    if fallback:
        emit(f"    fallback       : {_c(str(fallback), C_YELLOW)}")
    if total:
        pct = 100.0 * n_sel / total
        emit(
            f"    tests selected : {_c(str(n_sel), C_BOLD)} / {total}  "
            f"{_c(f'{pct:.1f}%', C_GREEN if pct < 50 else C_YELLOW)}"
        )
        emit(f"    {_c(bar(n_sel, total), C_GREEN)}")
    else:
        emit(f"    tests selected : {_c(str(n_sel), C_BOLD)}")

    # ---- reasons --------------------------------------------------------
    reasons: Counter[str] = Counter()
    for t in selected:
        for r in t.get("reason", []):
            reasons[r] += 1
    if reasons:
        emit()
        emit(_c("  WHY THESE TESTS", C_BOLD))
        for r, n in reasons.most_common():
            emit(f"    {_c(f'{n:5d}', C_CYAN)}  {r}")

    # ---- critical -------------------------------------------------------
    crit = [t for t in selected if t.get("priority") == "critical"]
    if crit:
        emit()
        emit(_c(f"  CRITICAL / SAFETY ({len(crit)})", C_BOLD))
        for t in crit[:10]:
            emit(f"    {_c('!', C_RED)} {t.get('id')}")
        if len(crit) > 10:
            emit(f"    {_c('…', C_DIM)} and {len(crit) - 10} more")

    # ---- timings --------------------------------------------------------
    timing = manifest.get("timings") or {}
    if timing:
        emit()
        emit(_c("  TIMINGS (seconds)", C_BOLD))
        for k, v in timing.items():
            try:
                emit(f"    {k:<22} {float(v):8.2f}")
            except (TypeError, ValueError):
                emit(f"    {k:<22} {v}")

    emit()
    emit(_c("─" * 76, C_DIM))
    emit()

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write("### NSE CI — native C17 test impact\n\n")
                fh.write("| field | value |\n| --- | --- |\n")
                fh.write(f"| mode | `{mode}` |\n")
                fh.write(f"| full suite | `{bool(full)}` |\n")
                fh.write(f"| changed files | {len(changed)} |\n")
                fh.write(f"| tests selected | **{n_sel}** |\n")
                if total:
                    fh.write(f"| of total | {total} ({100.0 * n_sel / total:.1f}%) |\n")
                fh.write(f"| parity | `{manifest.get('parity_mode', 'n/a')}` |\n")
                if fallback:
                    fh.write(f"| fallback | {fallback} |\n")
                if reasons:
                    fh.write("\n**Selection reasons**\n\n")
                    for r, n in reasons.most_common():
                        fh.write(f"- `{r}`: {n}\n")
                fh.write("\n")
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
