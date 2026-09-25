#!/usr/bin/env python3
"""CI final gate for the ci.yml quality job (CHG-0052 successor, fail-closed).

Replaces the inline final-gate heredoc that previously lived in
.github/workflows/ci.yml. Same status vocabulary and same output format, but
one class is fixed to be FAIL-CLOSED instead of FAIL-OPEN:

  * failed / errored  -> real failure of that check           -> job fails
  * blocked           -> step never ran because an upstream    -> recorded,
                         gate failed (root failure already      run already red
                         fails the run via its own check)
  * MISSING result JSON -> previously treated as "skipped" and the run went
     GREEN. That was a silent-skip hole: any lost GITHUB_ENV write, a skipped
     `if:` misfire, or a partial runner crash made every unexecuted gate
     disappear from the verdict ("ALL CHECKS PASSED" while pytest/mypy never
     ran - reproduced against the real make_ci_results pipeline).
     Now: a gate check with NO result file fails the job. The ONLY legitimate
     "never ran" paths are the ones that write status=blocked records (the
     per-step fallback or make_ci_results.py classify-gate), and those leave
     JSON files behind - so a missing file is always an unclassified skip.

Exit codes: 0 all checks passed/blocked-with-root-failure; 1 gate failure.

Local probe (no CI needed):
    CI_RESULTS_DIR=ci-results python scripts/ci/ci_final_gate.py
"""

from __future__ import annotations

import json
import os
import sys

#: The quality-job gate checks. MUST stay in sync with the steps in ci.yml
#: that write run-info/<name>.json (every one of them is mandatory on the
#: clean-format path).
CHECKS = (
    "ruff_lint",
    "ruff_format",
    "mypy",
    "pytest",
    "smoke",
    "coverage",
    "critical_coverage",
    "runtime_gate",
    "layered_smoke",
)


def main() -> int:
    root = os.environ.get("CI_RESULTS_DIR", "ci-results")
    info_dir = os.path.join(root, "run-info")
    statuses: dict[str, str] = {}
    missing: list[str] = []
    for name in CHECKS:
        path = os.path.join(info_dir, f"{name}.json")
        try:
            with open(path, encoding="utf-8") as fh:
                statuses[name] = json.load(fh).get("status", "skipped")
        except FileNotFoundError:
            # FAIL-CLOSED: no result file = the check never ran AND was never
            # classified as blocked. A green run may not be reported for a
            # gate that has no recorded outcome.
            statuses[name] = "missing"
            missing.append(name)
        except Exception as e:  # unreadable/corrupt JSON is a real failure
            statuses[name] = "errored"
            print(f"UNREADABLE CHECK JSON: {path} -> {e}")

    failed = [k for k, v in statuses.items() if v in ("failed", "errored")]
    blocked = [k for k, v in statuses.items() if v == "blocked"]
    repair = os.environ.get("RUFF_REPAIR_STATUS", "")
    print(" | ".join(f"{k}={v}" for k, v in statuses.items()))
    if repair == "repaired":
        print(
            "AUTO_REPAIRED_BUT_SOURCE_DIRTY: ruff format repaired the CHECKOUT;"
            " the COMMITTED tree remains malformed and ruff_format stays failed."
        )
    if blocked:
        print(f"BLOCKED (never ran - upstream root failure): {', '.join(blocked)}")
    if failed:
        print(f"FAILING CHECKS: {', '.join(failed)}")
    if missing:
        print("MISSING RESULTS (never ran, never classified blocked): " + ", ".join(missing))
    failing = failed + missing
    if failing:
        print(
            f"::error::CI gate failed on: {', '.join(failing)} | "
            + " | ".join(f"{k}={v}" for k, v in statuses.items())
        )
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
