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
| `src/dependency_graph.c` | module table, import scanning (mirrors the Python AST semantics), reverse edges, hub marking |
| `src/ast_metadata.c` | AST metadata ingestion: builds the graph from `ast-metadata.json` (the production path) |
| `src/test_selector.c` | global invalidation (the ONE canonical policy), mandatory safety expansion, reverse reachability with bounded hub one-hop, canonical plan JSON + digest |
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
nse-ci-engine plan --repo-root . --files "a.py b.py" [--commit-sha SHA] [--event NAME] [--full-suite] [--out plan.json] [--metadata ast-metadata.json] [--pyproject-scope SCOPE]
nse-ci-engine classify --base SHA --head SHA
nse-ci-engine fingerprint --repo-root . --test tests/unit/test_x.py
nse-ci-engine validate-plan plan.json
nse-ci-engine resource-info
nse-ci-engine selftest | selftest-fuzz --iterations N | version
```

## Metadata path (production, engine 0.2.0)

`run_test_plan.py` runs `scripts/ci/ast_extract.py` on every plan and
passes `--metadata ast-metadata.json`; the engine then builds the graph
from that metadata (`graph_build_from_metadata`, `ast_metadata.c`) and
**never** falls back to the line scanner — malformed metadata fails the
lane closed (rc 3). The line scanner (`graph_scan_repo`) remains only
for the C selftest and for callers that pass no `--metadata`.
`--pyproject-scope` carries the semantic `pyproject.toml` scope from
Python (`pytest|deps|build|mypy|lint|meta|unknown`, empty = no semantic
diff); absent scope fails closed to global invalidation.

### Hub rule (bounded one-hop)

A hub is a node with more than 25 importers (`HUB_IMPORTER_THRESHOLD`).
Traversal never expands through a hub; when reached, the hub's direct
test importers are included **only if there are at most 25 of them**
(`HUB_ONE_HOP_MAX_TESTS`). A changed hub itself is traversed fully.
Truncation is recorded in the plan `notes` (`hub-bounded
reachability: …`). One-line change in `research/worker.py`: 7 files
(line-scan) / 9 (metadata) — uncapped one-hop measured 219.
Full model, proof, and residuals: `docs/C17_ARCHITECTURE.md`.

### Global invalidation (one canonical policy)

`is_global_invalidation()` in `src/test_selector.c` is the ONLY place
that decides it. `pyproject.toml` is classified by semantic section
(`docs/C17_ARCHITECTURE.md` §4): `pytest`/`deps`/`build`/`unknown` and
an absent scope are global; `mypy`/`lint`/`meta`-only and empty scopes
select the `tests/ci` integrity suite instead of the full critical
suite. `tests/conftest.py` stays unconditionally global.
`classify_path()` in `change_detector.c` only routes *lanes* and does
not decide invalidation.

## Digest contract

The plan digest is SHA-256 over the canonical JSON without
`plan_digest`, keys sorted, compact separators — byte-identical to
Python's `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.
The C writer escapes non-ASCII as `\uXXXX` (surrogate pairs above the
BMP), exactly like Python's `ensure_ascii=True`; raw UTF-8 used to make
the C digest differ from Python's re-dump, so any plan with a non-ASCII
string failed validation with "plan digest mismatch" (fixed 2026-11-09,
writer selftest covers the em-dash case).

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
