# NSE Native CI Engine (`scripts/ci/native/`)

Portable **C17** optimization engine for the CI test-planning pipeline.
This engine is the **sole test planner**: there is no Python planner and
no parity twin. `scripts/ci/run_test_plan.py` only builds/verifies this
binary, runs its plan (SHA-256 digest-checked), and executes the selected
tests with pytest. Verification of the engine itself lives in
`src/selftest.c` (`selftest` + `selftest-fuzz`) and `engine-tests`.

## Components
| File | Responsibility |
|---|---|
| `src/change_detector.c` | lane classification (mirrors `classify_changes.py`) + bounded `git diff` acquisition |
| `src/dependency_graph.c` | module table, import scanning (mirrors the Python AST semantics), reverse edges |
| `src/test_selector.c` | global invalidation, mandatory safety expansion, reverse reachability, canonical plan JSON + digest |
| `src/fingerprint.c` | content-addressed test fingerprints (Algorithm 5 basis) |
| `src/resource_detector.c` (in `fingerprint.c`) | cgroup v2/v1 CPU-quota + memory-limit detection |
| `src/cache_metadata.c` | shared-cache provenance validation (6 compat dimensions) |
| `src/json_output.c` | canonical writer + strict depth-bounded JSON parser (fuzzed) |
| `src/util.c` | arena allocator, StrBuf, SHA-256 |

## Build
```bash
scripts/ci/native/build.sh              # release + selftest
scripts/ci/native/build.sh --sanitize --fuzz   # ASan/UBSan + 20k-iteration fuzz
```

## CLI
```
nse-ci-engine plan --repo-root . --files "a.py b.py" [--commit-sha SHA] [--event NAME] [--full-suite] [--out plan.json]
nse-ci-engine classify --base SHA --head SHA
nse-ci-engine fingerprint --repo-root . --test tests/unit/test_x.py
nse-ci-engine validate-plan plan.json
nse-ci-engine resource-info
nse-ci-engine selftest | selftest-fuzz --iterations N | version
```

## Safety & trust rules
- There is NO Python fallback: if the native binary is missing,
  incompatible, or exits non-zero, the lane fails closed. Python only
  executes the tests this engine selects — it never selects tests.
- Binaries are never restored from untrusted PR caches; deployment requires
  an exact `version` + `os/arch/python` match (`cache_metadata` record).
- Every exit != 0 is treated as failure by callers.

## Measured benchmark (2026-11-07, tier-1 Docker runner container, 400 modules / 300 tests / 58 changed files)
| engine | wall time |
|---|---|
| native (C17, -O2) | **13 ms** |

The Python reference planner was removed 2026-11-09 (the C engine is the
sole planner), so there is no current A/B number — never quote one without
re-measuring.

## Roadmap (honest status)
- Algorithm 2 (pytest-testmon dynamic coverage) — evaluated; NOT wired.
- Algorithm 3 (predictive ML) — shadow-mode only, not enabled.
- Algorithm 4 weighted set-cover — conservative union shipped; weighting pending.
- Algorithm 5 result-reuse — fingerprint format shipped; reuse store pending.
