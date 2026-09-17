# NSE Swarm — 2026-09-17 run7 (QA/Tests)

## AUDIT-A6-WRAPPER — FIXED-VERIFIED locally; remote gates pending

Baseline: origin/main 8416aa1c. Shared checkout remains clean on foreign
release-9.0.14 at ca45db8c; its deleted upstream prevents normal pull. No shared
checkout/reset/stash performed. Work isolated in /tmp/nse-swarm-r7 on
swarm/r7-wrapper. No existing PR owned this wrapper (open PRs #247/#248).

### Pre-change audit and scope
The OPEN A6 item in docs/agent_handoffs/2026-09-07_NSE_audit_findings_and_status.txt:60
is stale in its root cause, but the gate remains unusable. Windows-only resolution
was already replaced in commit 97cde71d; beforePush.sh:48-58 now chooses an override,
POSIX venv, Windows venv or PATH python3. The canonical runner already exists at
scripts/ci/check_local.py:1-27 and supports --help without heavy dependencies.
The actual current defect: beforePush.sh:1-18 is a Python shebang/docstring on a
Bash body. Direct execution reaches Python and dies at `set -euo pipefail`.
A Bash syntax check alone PASSES this bad file, so static validation misses it.

Prioritized this concrete QA gate blocker over P3 mirror-test cleanup; existing
nightly workflow-only items remain out of scope. Runtime, frozen models, MT5
adapter/client, workflows, secrets, databases and trading semantics untouched.

### Reproduction and fix
Before: `./beforePush.sh --help` rc1, SyntaxError at line 20.
Regression battery before fix: 1 passed, 5 failed. The passing case was bash -n.
After: Bash shebang plus shell-comment documentation at beforePush.sh:1-17.
Interpreter selection, canonical gate delegation and exit semantics unchanged
(body now starts at line 19; exec at line 67).

Tests: tests/ci/test_beforepush_wrapper.py:22-71 executes the actual executable
from a foreign working directory, exercises real canonical --help, and spies the
delegation boundary in a space-containing sandbox. Checks --prepush insertion,
argument boundaries and rc 0/1/2/7 propagation; no mirrored gate implementation.
POSIX tests explicitly skip on Windows (PowerShell wrapper unchanged).
Critical-suite registration: tests/critical_suite.txt:362.

### Executed verification
- New regression: 6 passed after fix; baseline 5 failures/1 pass.
- New regression plus classification neighbors: 19 passed.
- All tests/ci: 51 passed, rc0.
- Repo-wide ruff check: All checks passed; format: 2086 files already formatted.
- Targeted mypy on new test: Success, no issues in 1 source file.
- verify_critical_suite_manifest.py: 191 paths all exist.
- git diff --check: rc0.
- Actual PYTHON_BIN=<slim venv> ./beforePush.sh --help: rc0, canonical argparse
  usage includes --prepush and --staged.
- Actual full local wrapper gate executed with PYTHONPATH=src:. and PYTHON_BIN
  set to the slim venv: rc0, overall passed. All seven stages passed (ruff lint,
  format, mypy src, manifest existence, decision IDs, manifest drift, fast_tests).
  This runner selects a fast subset, NOT all critical tests; runtime champion
  canary explicitly skipped because the artifact is absent. Full log preserved
  under artifacts/swarm_agent/reports/2026-09-17_run7_gate.log.
- Initial full-gate attempt without PYTHONPATH failed importing nexus_scalp in
  tests.helpers.shadow70_fixtures. No missing plugin was diagnosed; correcting
  the worktree import environment fixed the run. Direct test_risk_engine: 11
  passed with the corrected environment.
- Full runtime/critical suite NOT executed locally.
  No live MT5, Docker or installed-Windows validation claimed.

### Continuity / failures
PR #245 post-merge full SHA 0eb3352febdeddcd1bf3d772705ee8fa83e2b6a3: gh run list
confirms CI, Tests OS Matrix, OSV, JS, Dependency Lock, Docs completed success;
separate security.yml query also completed success. R4 as a whole remains PARTIAL.
Initial check-runs response was paginated and mostly advisory summaries; it was
NOT used to infer required-gate green. Workflow-scoped queries provided evidence.

Run friction: deleted release upstream blocked pull (isolated worktree used);
installed gh rejected unsupported JSON flags (corrected); initial regression file
needed Ruff formatting (fixed, checks re-run). No unresolved local test failure.
An unnecessary clean clone /tmp/NexusTradingForexBotFresh was created during
triage and left untouched; it is NOT the work artifact or authoritative state.

### Remote verification and dependency unblock
PR #249 initial code commit ada06ec2: 14 successful checks, 4 skipped, one
failure (Dependency drift); Code Quality & Tests, Windows, macOS and security
passed. Main baseline run 35149043181 has the identical stale-lock error as
PR run 35169149893. Local drift check reproduced it; no dependency files had
been changed by this wrapper fix.
Existing PR #248 owned the fix. Independently verified its exact diff (only
nvjitlink 13.4.52 -> 13.4.92 plus four hashes and handoff), ran the real resolver
check (OK, 98 pins), and its three regression tests (passed). All PR checks
were green. Squash merged #248 as 3b82a58c; merged corrected origin/main into
this isolated branch using a normal merge, preserving history and shared WIP.
No downgrade, pyproject edit, force push or workflow edit. PR #249 refreshed
at 992c721c; final gates pending at this report checkpoint.

### Next
Verify PR #249 remote gates before claiming landed. Durable state and mirror retain PR/worktree information so a
restart resumes adjudication instead of duplicating this fix. Role 8 follows
once this item is closed; no workflow or secret edits by this job.
