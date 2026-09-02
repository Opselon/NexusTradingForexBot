# CI Validation Summary

- **Run:** 633 (id 33583881735)
- **Commit:** `86b13d67383cd7a3f5bc19a36a299480b65616df`
- **Branch:** `refs/heads/main`
- **Workflow:** CI — **Overall: FAILED**
- **Timestamp (UTC):** 2026-09-02T02:38:31Z

## Results

| Check | Status | Details |
|---|---|---|
| Ruff Lint | FAILED | violations found (see ruff/) |
| Ruff Format | PASSED | files would be reformatted (see format/) |
| Mypy | PASSED | type errors found (see mypy/) |
| Pytest | FAILED | see pytest/junit.xml + pytest.txt |
| Coverage | PASSED | 49.0% line coverage |

## Test Statistics

- Tests: 1418
- Passed: 1384
- Failed: 1
- Skipped: 33
- Coverage: 48.7%

## Build Environment

- Python: 3.11
- OS: Linux
- Event: push / Actor: Opselon
- Tools: ruff ruff 0.16.5, mypy mypy 2.3.1 (compiled: yes), pytest pytest 9.1.1

## Evidence

Full machine-readable results are in this artifact: `ci-results/`.
- `run-info/summary.md` (this file) · `run-info/manifest.json` (all files + sha256)
- `run-info/SHA256SUMS.txt` (checksums) · `run-info/*.json` (per-check status + exit codes)
- `ruff/lint.json` + `lint.txt` · `format/format.txt` · `mypy/mypy.txt`
- `pytest/junit.xml` + `pytest.txt` + `coverage.xml` (+ `pytest/htmlcov/` when generated)
