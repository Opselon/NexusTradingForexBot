# CI Validation Summary

- **Run:** 653 (id 33587762695)
- **Commit:** `a97ccce98111ff9ac47a25f1d8351747f312e847`
- **Branch:** `refs/heads/main`
- **Workflow:** CI — **Overall: FAILED**
- **Timestamp (UTC):** 2026-09-02T03:39:23Z

## Results

| Check | Status | Details |
|---|---|---|
| Ruff Lint | PASSED | violations found (see ruff/) |
| Ruff Format | PASSED | files would be reformatted (see format/) |
| Mypy | PASSED | type errors found (see mypy/) |
| Pytest | FAILED | see pytest/junit.xml + pytest.txt |
| Coverage | PASSED | 20.0% line coverage |

## Test Statistics

- Tests: 1418
- Passed: 1384
- Failed: 1
- Skipped: 33
- Coverage: 19.9%

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
