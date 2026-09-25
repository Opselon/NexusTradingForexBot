---
title: Quality & Testing
description: How quality is enforced — the beforePush gate, test architecture, golden tests, and the no-fake-green rule.
lang: en
---

# Quality & Testing

## The quality gate

Every push passes `beforePush.sh` / `beforePush.ps1` (5 stages):

1. `ruff check` (lint)
2. `ruff format` check
3. `mypy src`
4. **CRITICAL suite** pytest run (`tests/critical_suite.txt`, ~779 tests,
   RAM-aware xdist workers, coverage)
5. Forensic **deploy gate** writing `artifacts/forensics/deploy_gate_result.json`

`-FullSuite` runs the whole ~1931-test unit corpus. CI mirrors the gate
(`.github/workflows/ci.yml`) and adds CodeQL, Trivy, OSV, lockfile-diff and JS
tests on separate workflows.

## Test architecture

| Suite | Location | Character |
| :--- | :--- | :--- |
| Unit | `tests/unit/` | ~1900 tests; critical subset in `critical_suite.txt` |
| Integration | `tests/integration/` | API + engine-level, run standalone/sequential in CI |
| Golden | `tests/golden/` + `test_*_golden.py` | byte/behavior-identical extraction gates (decomposition safety) |
| CLI E2E | `test_cli_end_to_end.py` | 66 end-to-end CLI contracts incl. exit codes |
| Installer | `tests/installer/` | stage protocol tests |
| JS | `tests/js/` | Control Center syntax/behavior gates |

## Golden tests (decomposition safety)

Large hot-path modules are decomposed only behind golden tests written
**before** extraction: the extracted seam must be behaviorally identical
(e.g. OrderManager's state machine, protection ledger, recovery budget;
reporting read-adapter golden JSON).

## No fake green

- A test that can't fail is deleted, not ignored silently.
- Pre-existing failures are classified and owned — never absorbed into
  "passing" runs.
- Exit codes are contracts (`CLI_EXIT_CODES v1`): `0` success · `1` runtime ·
  `2` usage · `3` environment blocked · `4` release verification · `5` update.

## Static anti-crash checks

`scripts/ci/anti_crash_static.py` scans for silent exception handlers
(`SILENT_HANDLER` sites are ledgered as P1 findings, with an explicit inline
allow marker + allowlist for legitimate migrations).
