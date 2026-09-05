# Local Pre-Push Quality Gate (`scripts/ci/check_local.py`)

ONE canonical offline command that catches cheap, deterministic failures
(Ruff lint / Ruff format / import order / critical-suite manifest / fast
tests) BEFORE they consume a GitHub Actions run. CI remains authoritative.

## Recommended workflow

    edit -> python scripts/ci/check_local.py
         -> python scripts/ci/check_local.py --fix   (safe mechanical fixes)
         -> re-run check_local.py
         -> commit -> push -> CI (authoritative)

## Commands

    python scripts/ci/check_local.py            # changed files (default scope)
    python scripts/ci/check_local.py --all      # whole tree (src/ tests/ scripts/)
    python scripts/ci/check_local.py --staged   # staged + new files only
    python scripts/ci/check_local.py --fix      # ruff check --fix + ruff format, then re-check
    python scripts/ci/check_local.py --fast     # skip mypy (syntactic stages only)
    python scripts/ci/check_local.py --json     # pure JSON on stdout (diagnostics -> stderr)

Flags are combinable, e.g. `--fix --fast --json`. Exit codes: 0 = passed,
1 = any stage failed, 2 = usage/configuration error.

## Stages

| Stage                    | Local? | Notes |
|--------------------------|--------|-------|
| ruff lint                | yes    | same pyproject [tool.ruff] config as CI |
| ruff format --check      | yes    | same formatter config |
| mypy src                 | yes    | skipped by --fast |
| critical-suite manifest  | yes    | every path in tests/critical_suite.txt must exist (CONFIGURATION ERROR otherwise) |
| fast targeted tests      | yes    | a few seconds; never xdist/coverage/network |

Full pytest/coverage stays CI-authoritative (see `beforePush` for the heavier
local pre-push gate).

## Scope rules (multi-agent safe)

* Default scope = your changed files: working tree + staged + untracked
  (`git ls-files --others`) + additions vs the push base (origin/main).
* Deleted `.py` files are excluded from lint/format (they don't exist) and
  reported in the JSON envelope as `scope.deleted_py_files`.
* `--fix` touches ONLY files in the enumerated scope. It never stages,
  commits, stashes, resets, or touches foreign/scratch files.

## Auto-fix policy

`--fix` applies ONLY mechanically safe, tool-owned transformations:
ruff lint --fix (import sorting, unused imports, etc.) and ruff format.
It NEVER rewrites business logic, tests, assertions, thresholds, or adds
`# noqa`. Non-mechanical failures (e.g. F821 undefined name) remain hard
failures after --fix.

## JSON contract

`--json` prints a single JSON object:

    {"gate":"check_local","overall":"passed|failed",
     "scope":{"mode":"changed|all|staged","files":[...],"deleted_py_files":[...]},
     "results":[{"name","command","exit_code","status","duration_sec",
                 "detail","fix_attempted","fix_applied","error_classification"}]}

Statuses: passed | failed | errored | skipped | configuration_error
(error_classification: LINT_OR_FORMAT / TYPE_CHECK / CONFIGURATION_ERROR /
TEST_FAILURE / TOOL_OR_CONFIG_MISSING). stdout is pure JSON when --json is
used; tool output tails go to stderr.

## Offline / safety

The gate never downloads anything, never touches MT5, providers, or the
trading engine. A missing tool reports `configuration_error` — it does not
pip-install.

## CI parity — forensic deploy gate is local-only

> Forensic deploy gate (`nexus forensic --deploy-gate --json`, BUG-162) is
> local-only via `beforePush` (`beforePush.sh:107-127` / `beforePush.ps1:671-723`)
> — not enforced in CI; CI health via pytest/coverage + probe, not barrier.
> Health engine: `src/nexus_scalp/release/health.py` (canonical).

CI (`.github/workflows/ci.yml`) runs the same tools against the same
pyproject config plus the full pytest+coverage matrix. The local gate is an
early detector with the same configuration source of truth; it can be wired
into a pre-push hook by running `python scripts/ci/check_local.py --fast`
(no hook is installed by default in this multi-agent repo — each agent opts
in explicitly).
