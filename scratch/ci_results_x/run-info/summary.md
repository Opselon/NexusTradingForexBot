# CI Validation Summary

- **Run:** 622 (id 33581709459)
- **Commit:** `6b893f045f3ef94e33c3988d3da76aef99252e3b`
- **Branch:** `refs/heads/main`
- **Workflow:** CI — **Overall: FAILED**
- **Timestamp (UTC):** 2026-09-02T02:04:14Z

## Results

| Check | Status | Details |
|---|---|---|
| Ruff Lint | FAILED | violations found (see ruff/) |
| Ruff Format | FAILED | files would be reformatted (see format/) |
| Mypy | FAILED | type errors found (see mypy/) |
| Pytest | FAILED | see pytest/junit.xml + pytest.txt |
| Coverage | PASSED | 49.0% line coverage |

## Test Statistics

- Tests: 1418
- Passed: 1383
- Failed: 2
- Skipped: 33
- Coverage: 49.2%

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
