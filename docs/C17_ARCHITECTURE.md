# C17 Engine Architecture (test planning)

Owner: area 1 (C17 engine). The workflow/CI-surface view lives in
`docs/CI_ARCHITECTURE.md` (area 4); this file is the engine's own
dependency model, selection rules, and evidence.

## 1. Who plans, who executes

```
git diff base...head          (C: change_detector, read-only popen)
        |
        v
scripts/ci/ast_extract.py    (Python, stdlib ast ONLY: imports, classes,
        |                      functions, fixtures, test args, dynamic-import
        |                      targets, pytest_plugins)  -> ast-metadata.json
        v
nse-ci-engine plan --metadata ast-metadata.json [--pyproject-scope SCOPE]
        |                      (C17: the SOLE planner — dependency
        |                       resolution, graph construction, impact
        |                       analysis, final selection)
        v
plan.json (SHA-256 digest-checked)  ->  pytest runs exactly those files
```

Rules:

- **C plans, Python executes.** Python never selects tests. There is no
  Python planner and no parity twin.
- **The metadata path is production.** `run_test_plan.py` extracts metadata
  on every plan and passes `--metadata`. A failed extraction, or malformed
  metadata, fails the lane closed (exit 90 / engine rc 3). The engine
  NEVER silently falls back to the line scanner in production; the
  line scanner (`graph_scan_repo`) exists only for the C selftest and for
  callers that pass no `--metadata`.
- **The plan is evidence.** `plan.json` records `metadata_used`,
  `pyproject_scope`, `fallback_reason`, `missing_mappings`, and `notes`.
  The job log prints `C17 METADATA: N files`, `C17 METADATA USED:`,
  `C17 PLAN: selected=N of M`, and `C17 FALLBACK:` on every run.

## 2. The dependency graph (metadata path)

One node per file, keyed by repo-relative path. Edges (importer -> target):

| edge | source |
|---|---|
| static import | `from X import sym`: `X` **and** `X.sym` resolved as modules. `X` alone may not exist (namespace package, no `__init__.py`); the submodule is the real dependency. Every resolved module also edges to its ancestor package nodes (parent chain), so changing a package `__init__.py` selects submodule importers. |
| relative import | resolved against the file's own package; `__init__.py` IS its package (a `from .x import …` inside `release/__init__.py` resolves to `nexus_scalp.release.x`, not `nexus_scalp.x`). An unresolvable relative import flips `relative_unresolved` -> conservative expansion. |
| dynamic import | literal `importlib.import_module("nexus_scalp.…")` / `__import__` targets get real edges. A non-literal target (`<dynamic>`) is unknowable: counted in the plan notes (`N file(s) use non-literal dynamic imports`), NOT expanded — 11 files use one, and expanding every plan for them would make the metadata path useless. Residual risk, see §5. |
| fixture dependency | test arg -> fixture definer. **Scope-aware** (pytest semantics): a fixture defined in a `conftest.py` is visible to tests under that conftest's directory; a fixture defined in a `pytest_plugins` module is visible under the registering conftest's directory (`tests/conftest.py` registers `tests.helpers.shadow70_fixtures`, so `contract` / `tmp_artifacts` are effectively global); a fixture defined anywhere else (test module, helper) is visible only in its own file, where plain import edges already cover the dependency. A test file defining the same fixture name shadows the conftest/plugin one. The old global name match linked every user of `temp_audit_repo` (defined separately in 13 test files) to every definer — 13×13 spurious edges. |
| stale absolute import | target module deleted but still referenced behind `try/except` (e.g. `nexus_scalp.database.audit_repository`): a synthetic node keeps the edge, sets no uncertainty flag — the target has no file, so it can never be a changed file. Mirrors the line scanner. |

Traversal is reverse reachability from the changed modules, and it
**crosses test nodes** (they are not leaves): a test that imports the
changed module may be imported by other tests (shared helpers) or be a
conftest whose fixture users depend on it. The fixture edges only work
because the traversal crosses the conftest node.

## 3. Hub bounding — the rule, the proof, and the residual

A **hub** is any node with more than 25 importers
(`HUB_IMPORTER_THRESHOLD`), test nodes included: an autouse conftest
has hundreds of importers and is a hub like any other composition root.
The traversal never expands through a hub. When it reaches one, it
includes the hub's **direct test importers, but only when there are at
most 25 of them** (`HUB_ONE_HOP_MAX_TESTS`, the bounded one-hop
expansion). A hub that is itself changed is traversed fully.

### Why the cap exists (measured, this repo)

An **uncapped** one-hop re-introduces the explosion hub-bounding exists
to prevent. One-line change in `src/nexus_scalp/research/worker.py`:

| rule | selected |
|---|---|
| no one-hop (old) | 7 (line-scan) |
| uncapped one-hop | **219** — 94 via the `research/__init__` facade, 73 via `live_engine`, 70 via `web/server` (with overlaps) |
| capped one-hop (≤25 direct tests) | 7 (line-scan) / **9 (metadata)** |

The 9 (vs 7) are two real false negatives the metadata path fixes:
`test_debug_features_70d_truth.py` and `test_health_governance_truth.py`
import `debug_research_routes` via `from nexus_scalp.web import …` —
the line scanner only sees the package-level import, the metadata path
resolves the submodule.

### What the rule guarantees

- **The audit's scenario is covered.** A changed module with one
  directly-importing test plus a high-fanout intermediate module whose
  direct test fan-out fits the bound (≤25) selects all of those
  integration tests. Proven by `test_hub_one_hop_expansion` (C selftest)
  and `test_hub_one_hop_capped` (`tests/ci/test_run_test_plan.py`):
  a 27-importer hub contributes its 3 direct tests; a 26-direct-test
  hub stays a hard boundary; a test reaching the hub only via a non-test
  intermediate stays unselected.
- **The bound is per hub and visible.** Worst case per reached hub is
  +25 tests. Every plan whose traversal hit a hub records it in
  `notes` (`hub-bounded reachability: …`), so the truncation is never
  silent. The scheduled full suite remains the backstop.

### Residual (explicit, not silent)

1. A test that reaches a changed module only via a hub **and** a
   non-test intermediate (distance ≥2 past the hub) is not selected.
2. A hub with more than 25 direct test importers (a composition root
   such as `live_engine`, ~73 direct tests) stays a hard boundary: its
   direct tests are not selected for a change below it.
3. A test whose only dependency on a changed module is a **non-literal**
   dynamic import is not selected (counted in the plan notes).

Why this is the accepted trade-off: import reachability is a static
over-approximation, and full transitive closure through composition
roots is unaffordable (779/860 tests for one research leaf before hub
bounding). The choice is between selecting ~200 tests for every leaf
change (which defeats the 3-minute build) and bounding the traversal at
hubs while recording the truncation. Symbol-level reachability (tracking
*which names* a test imports from a package, so `from research import
BacktestEngine` does not depend on `research.worker`) would shrink the
hub fan-outs and is the principled next step; it is not implemented.

## 4. Global invalidation — one canonical policy

The verdict lives in **exactly one place**: `is_global_invalidation()`
in `scripts/ci/native/src/test_selector.c`. `run_test_plan.py` holds no
invalidation list (removed 2026-11-09); `classify_path()` in
`change_detector.c` only routes *lanes* (a `pyproject.toml` change is
still `deps`-lane at file level) and does not decide invalidation.

Unconditional global invalidation: `requirements.lock`,
`requirements.txt`, `uv.lock`, `tests/conftest.py`, `pytest.ini`,
`setup.cfg`, `tox.ini`, `configs/` prefix.

`pyproject.toml` is classified **semantically**. Python diffs the parsed
base/head toml (`classify_pyproject_diff` in `run_test_plan.py`, stdlib
`tomllib`) and passes a scope string (`--pyproject-scope`); the engine
owns the final verdict:

| scope | meaning | verdict |
|---|---|---|
| `pytest` | `[tool.pytest.ini_options]` | **global** |
| `deps` | `[project]` dependencies / `requires-python` / optional-dependencies | **global** |
| `build` | `[build-system]` / `[tool.setuptools.*]` | **global** |
| `unknown` | unrecognised section, or the diff could not be classified | **global** (fail closed) |
| (absent) | old caller, no scope passed | **global** (fail closed) |
| `mypy` | `[tool.mypy.*]` only | NOT global — selects `tests/ci` integrity suite |
| `lint` | `[tool.ruff.*]` only | NOT global — selects `tests/ci` integrity suite |
| `meta` | `[project]` name/version/description/scripts only | NOT global — selects `tests/ci` integrity suite |
| (empty) | no semantic difference (comment/whitespace-only) | NOT global — selects `tests/ci` integrity suite |

A non-global `pyproject.toml` change is CI-tooling scope (same handling
as a workflow change): the `tests/ci` suite runs as a smoke check, the
full critical suite does not. The plan notes record why
(`pyproject.toml change limited to mypy — CI-tooling scope, full suite
not required`), and the fallback reason names the scope when global
(`global invalidation: pyproject.toml (pytest)`).

## 5. Selection-count measurements (2026-11-09, this tree)

Engine `0.2.0`, one changed file, `pull_request` event, targeted mode:

| change | line-scan | metadata |
|---|---|---|
| `research/worker.py` (1 line) | 7 | **9** |
| `research/metrics.py` | 45 | 50 |
| `settings/service.py` | 242 | 265 |
| `settings/secret_store.py` | 244 | 284 |
| `application/live_engine.py` (hub, changed) | 111 | 138 |
| `shadow/shadow70/runtime.py` | — | 20 (plugin-fixture users included) |
| `shadow/shadow70/models.py` | — | 31 |
| `pyproject.toml` (pytest scope) | expanded, 324 | expanded, 324 |
| `pyproject.toml` (mypy scope) | targeted, 12 (`tests/ci`) | targeted, 12 |
| `pyproject.toml` + `tests/conftest.py` | expanded, 324 | expanded, 324 |

Costs: AST extraction takes ~15 s wall on the full repo (1679 files,
3.8 MB JSON) on this VM; the engine plan itself takes <1 s. The
extraction cost is the price of the metadata path and is reported in
the job log (`C17 METADATA: 1679 files`).

## 6. Digest contract

The plan digest is SHA-256 over the canonical JSON **without**
`plan_digest`, with keys sorted, compact separators — byte-identical to
Python's `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.
The C writer escapes non-ASCII as `\uXXXX` (surrogate pairs above the
BMP), exactly like Python's `ensure_ascii=True`. Raw UTF-8 used to make
the C digest differ from Python's re-dump, so any plan with a non-ASCII
string (an em-dash in a fallback reason or note) failed validation with
"plan digest mismatch" — fixed 2026-11-09, covered by a writer selftest.

## 7. Open questions / risks (for the coordinator)

- **Engine version is 0.2.0.** The `ci.yml` `0.1.0` literals in the
  native-binary restore step and cache key were bumped to `0.2.0` by the
  coordinator fixup (431d91e6).
- **Symbol-level reachability** (see §3 residual) is the principled fix
  for hub fan-outs; it is a larger design change, not attempted here.
- **Non-literal dynamic imports** (11 files) and **helper-defined
  fixtures re-exported without an import edge** remain unselected when
  they are the only dependency path. Both are counted/noted where
  possible; the scheduled full suite is the backstop.
- **CI is the oracle for app tests.** Locally the full app suite cannot
  run (no torch in the venv); the metadata-path numbers above are
  planner measurements, and the Quality/OS lanes must confirm them.
