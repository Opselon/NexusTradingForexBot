# LOG-DEFECTS-WAVE — Live Log Defect Remediation Contract (2026-09-23)

Base: `6a8aa2cb` = `origin/main` tip (chart wave-3, PR #403). Verified.
Integration branch: `agent/hermes/log-defects-wave` (worktree `C:/Users/Capsizer/source/repos/nse-logdefects`).
No SEC-WAVE lock entries exist at `origin/main:agents/locks.yaml` — the local
working-tree `agents/locks.yaml` diff belongs to the *other* session's
`agent/hermes/sec-remediation-wave` lane (base `b3038b99`, worktree
`nse-sec-remediation`). DO NOT touch it, stage it, or carry it into our PR.

## Shared rules for all lanes

1. Work ONLY in the `nse-logdefects` worktree. Never touch the shared
   `NexusTradingForexBot` checkout (another session's WIP + the live engine's
   untracked `configs/`, `.env`, `artifacts/`).
2. One branch, one PR. Lanes are file-disjoint; the integrator merges.
3. Pytest from the worktree: `PYTHONPATH=src` is mandatory — the shared
   `.venv` editable `.pth` pins `nexus_scalp` to the MAIN checkout's `src/`.
   `-p no:cacheprovider`, redirect output to a file, read the file.
4. Lint per change: `ruff format --check .` and `ruff check .` must be clean
   (CI gates ruff-format before mypy/pytest; a red ruff blocks everything).
5. Do NOT weaken any assertion, golden, or CI check to get green. A check that
   must pass-by-construction is a contract violation, not a fix.
6. Commit immediately after each verified patch. `git branch --show-current`
   before every `git add`+`git commit` pair (parallel sessions can flip the
   shared checkout; a foreign branch absorb is the known race here).
7. Stage only your own files: `git diff --cached --name-only` must list exactly
   your scope, immediately before commit. `git restore --staged <path>` any
   foreign path.
8. Every lane appends ONE row to `agents/taskboard.md` (9-column table:
   TASK-ID | Owner | Priority | Title | Deps | Files | Contracts | Blocker |
   Status) under its own `## <TAG> — title (date)` section. Append-only union;
   never renumber or reformat another row. This file conflicts on every merge —
   keep all rows, chronological order, remote-first then local.
9. Tests for each lane must be NEW tests in `tests/unit/` asserting the NEW
   behaviour. Never delete an existing assertion. Prefer invariant/contract
   tests over mock volume.

## The five defects (evidence-verified, all reproduce at 6a8aa2cb)

### LD-1 — CHECK-ACC-02 FALSE CRITICAL (money-integrity false alarm) — P0
`src/nexus_scalp/forensics/checks_accounting.py::check_duplicate_economic_outcome`

The log line `[FORENSIC_ALERT] check=CHECK-ACC-02 status=CRITICAL` fires every
~10s. It is a FALSE POSITIVE. Live `artifacts/audit.db` (read-only probe):

```
total rows:           1687
empty execution_id:   1465   (ALL is_executed=0)
non-empty dupes:      2  -> 152494870397 (x2), 152660983978 (x2)
is_executed=1 empty:  0
```

1,465 of 1,687 rows are non-executed (paper/decision-sample) outcomes whose
`execution_id` is the empty string `''`. The check groups by `execution_id`
with no null/empty guard, so all 1,465 collapse into ONE bucket of size 1465
and are reported as a single "execution identity with >1 outcome". The
`known_historical = {"152494870397"}` allowlist only masks one of the two real
dupes; `''` is not in it, so `fresh` is non-empty → CRITICAL forever.

The invariant's own docstring is explicit: "an execution identity (broker
ticket) must appear at most once". An empty string is not a broker ticket.
`audit_experience_outcomes` carries `is_executed` for exactly this
distinction (see `check_experience_outcome_gap`'s own §21 note: pre-execution
decision samples "never trade and legitimately have no outcome").

FIX (fail-closed, no data touched — audit trail is immutable):
- Exclude rows whose effective execution identity is empty/None from the
  duplicate scan (they have no broker ticket to own an outcome).
- Report them as a benign, bounded count in `observed` so the operator still
  sees them (never silently dropped evidence).
- Keep both historical dupes classified correctly: `152494870397` is in the
  allowlist; add `152660983978` to the known-historical set (both are
  immutable pre-guard rows — the check already documents this class as
  WARNING, and CRITICAL is reserved for NEW duplicates by its own contract).
- A NEW duplicate on a real, non-empty, non-historical execution_id stays
  CRITICAL. That is the invariant the check exists to protect.
- DO NOT mutate the DB, and do not add a whitelist entry for `''` — that would
  mask a genuine future duplicate written with an empty id.

### LD-2 — FastAPI Duplicate Operation IDs (module-router accumulation) — P0
`src/nexus_scalp/web/debug_research_routes.py` lines ~1254-1270 and ~2183-2196

CORRECTED BY LANE B (the original contract read below was WRONG — kept for
provenance): `register_*_routes(app)` does NOT mount anything on `app`. It
only decorates the MODULE-level `APIRouter()` with `@router.get/post`, and the
following `app.include_router(<that router>)` is the SOLE mount. The warnings
appear because FastAPI's `include_router` is not idempotent and the router is
process-global STATE: on the 2nd+ `create_app()` in one process (which the
forensic check performed every sweep — see LD-3) each `include_router` appends
a fresh wrapper for the already-accumulated router, so 10 + 45 = 55 duplicate
operation IDs per extra build and an inflated OpenAPI surface.

Original (incorrect) contract read: "register_*_routes(app) already attaches
every route via the module-level router; the following include_router re-adds
them all → delete the include_router calls." Deleting them would have
UNREGISTERED every /api/intelligence/* and /api/models/* route. Lane B read the
producer and rejected that instruction — the right fix is below.

LANE B FIX (applied, verified): keep `include_router` as the sole mount and
CLEAR the module router immediately before re-registering
(`intelligence_router.routes.clear()` / `_mgr.router.routes.clear()` before
each `register_*_routes(app)`), making the mount exactly-once per app while
preserving original route ORDER. The `from ... import ... as _mgr` import was
hoisted above the register call (no behaviour change).
Verified by `tests/unit/test_ld2_route_registration.py`: across 3 successive
`create_app()` builds, ZERO "Duplicate Operation ID" warnings, and the
(method, path) operation set is IDENTICAL across builds.

### LD-3 — app rebuilt per forensic sweep (create_app on a loop) — P0
`src/nexus_scalp/forensics/checks_observability.py::check_api_200_but_wrong`

The log repeats the FULL `[ALT-UI] serving alternative React console from
...` line plus the entire 58-warning FastAPI dump every ~7-20 seconds, in lock
step with the forensic engine's cadence. `[ALT-UI]` logs exactly once per
`create_app()` call, and `create_app()` runs once per boot per
`engine_boot.py:802`. The recurring burst therefore comes from the one caller
that rebuilds the app repeatedly: `check_api_200_but_wrong` calls
`create_app()` + `app.openapi()` on EVERY forensic sweep (forensics/engine.py
"API" group, `run_checks` per cycle).

This is the root cause of the log storm: each sweep constructs the whole
FastAPI app (all routes, all static mounts, the ALT-UI resolution + log line)
just to test set membership of 5 endpoint paths. It also makes the LD-2
warnings appear continuously instead of once at boot.

FIX: memoize the path surface per process (build the app ONCE, cache the
resolved path set; the route surface is static after boot). Keep the check's
contract identical — same result for the same registered routes, still
fail-closed to UNKNOWN if the app cannot be built. A per-process module-level
cache keyed by nothing (route surface does not change post-boot) is correct;
add a test that a route added to a FRESH app is still seen (cache must not
outlive the app object it describes — cache the pair, not a global set, or the
test will catch it).
Do NOT lower the sweep cadence or remove the check — the check is right, only
its cost is wrong.

### LD-4 — RESEARCH worker kick timeout noise (45s vs 120-150s cycles) — P1
`src/nexus_scalp/application/live_engine.py` + `live_workers.py`

`[WORKER_KICK] event=TIMEOUT worker=RESEARCH timeout_sec=45.0 — detaching
hung call` fires every ~60-90s. The research worker's own UPDATE lines show
`duration_ms=124953 / 132346 / 149451` (125-150s) for a healthy cycle that
sets `work_done=False` — i.e. the worker is NOT hung, it is doing a long
dataset pass that legitimately exceeds the 45s kick timeout. The kick
abandons a live, healthy call every time, then re-runs it (work is repeated,
not lost — `work_done=False` proves nothing was committed).

FIX (operator-visible, no hot-path change): the timeout is already env-
overridable (`NSE_WORKER_KICK_TIMEOUT`, default 45). Raise the DEFAULT to a
value above the observed healthy-cycle ceiling (150s → 180s) and document why
in the constant's comment. Do NOT touch the tick hot path, and do not change
the kick/backpressure mechanism — only the default budget. The mechanism is
correct (a truly hung call must still be detached); 45s was simply sized
against the wrong cycle length.
Optional P2: log the worker's last-known cycle duration in the TIMEOUT line so
an operator can tell "slow cycle" from "hung call" at a glance.

### LD-5 — NEWS fetch connection-refused noise — P2 (config/local, not code)
`[NEWS_FETCH] source=forexlive status=FAILURE error=fetch error: [WinError
10061] No connection could be made because the target machine actively
refused it failures=2 backoff_sec=60`

Local egress to forexlive is blocked/refused (proxy or firewall on this box),
and the source already backs off correctly (60s). This is environment, not a
code defect — NO code change. Verify the backoff is honoured and the failures
counter is bounded; if the source already fails closed and backs off, the
correct action is to note it and leave it. Do NOT disable the source or
silence the warning for everyone.

## Lane table (file-disjoint ownership)

| Lane | Defects | Owns (files, exclusive) | Tests to add |
|---|---|---|---|
| LD-LANE-A | LD-1 | `src/nexus_scalp/forensics/checks_accounting.py` | `tests/unit/test_ld1_acc02_empty_exec.py` |
| LD-LANE-B | LD-2 | `src/nexus_scalp/web/debug_research_routes.py` | `tests/unit/test_ld2_route_registration.py` |
| LD-LANE-C | LD-3 | `src/nexus_scalp/forensics/checks_observability.py` | `tests/unit/test_ld3_app_build_caching.py` |
| LD-LANE-D | LD-4 | `src/nexus_scalp/application/live_engine.py`, `src/nexus_scalp/application/live_workers.py` | `tests/unit/test_ld4_worker_kick_timeout.py` |
| LD-LEAD | LD-5, taskboard, PR, CI loop | `agents/taskboard.md`, `agents/decisions/DEC-LD.md`, this contract | none (verification only) |

Cross-lane dependency: LD-2 and LD-3 both touch the app surface. LD-3's test
asserts "create_app built once" and LD-2's asserts "zero duplicate op IDs".
Run both suites after integrating both — order does not matter (disjoint
files), but the assertions interact at the app level.

## Verification gates (integrator runs, all must pass)

1. `ruff format --check .` and `ruff check .` clean at the branch tip.
2. `mypy src` no NEW errors vs base.
3. Per-lane targeted pytest: `PYTHONPATH=src ./.venv/Scripts/python.exe -m
   pytest tests/unit/test_ld*.py -p no:cacheprovider > pytest_out.txt 2>&1`
   (run from the worktree; `.venv` is the MAIN repo's shared venv).
4. LD-2 functional probe: build the app in-process and assert the number of
   Duplicate-Operation-ID warnings is 0 (filter warnings for
   "Duplicate Operation ID").
5. LD-1 functional probe: run `check_duplicate_economic_outcome()` against the
   live `artifacts/audit.db` (read-only URI) and assert it is NOT CRITICAL
   and that the two real historical dupes still surface as WARNING.
6. LD-3 probe: assert `check_api_200_but_wrong()` still returns the correct
   verdict and that `create_app` is invoked at most once across N sweeps.
7. `tests/unit/test_check_local_gate.py` and
   `tests/unit/test_gate_bypass_detection.py` are EXCLUDED from full-suite
   runs (they spawn real subprocesses and hang/flake under load). Run them
   standalone if touched, never inside a batch.
8. Commit hygiene: every commit's `git diff --cached --name-only` lists only
   that lane's files. Final PR must not contain `agents/locks.yaml`,
   `configs/`, `.env`, `artifacts/`, or any foreign WIP.

## Merge plan

1. Integrator (LD-LEAD) fast-forwards the lane with all four lane commits.
2. `git ls-remote origin agent/hermes/log-defects-wave` before any push; if
   the ref moved, fetch and re-verify before pushing (parallel agents
   recreate/force-push refs — never force-push, use `--force-with-lease` only
   on refs you own, and remote ref deletion is a STOP-condition here).
3. Push the branch, open PR via `gh pr create` (base `main`).
4. Required contexts: green CI (ruff, mypy, pytest) + 'Validate documentation'
   (docs.yml). For a code-only PR, dispatch docs.yml via the API
   (`POST /repos/Opselon/NexusTradingForexBot/actions/workflows/docs.yml/
   dispatches` with `{"ref": "<branch>"}`) so the context attaches.
5. Merge with `--squash` ONLY (merge commits are 405 on this repo; the
   parallel coordinator's pattern). Record the lane tip SHA in the commit
   body and verify tree parity (`git rev-parse <merge>^{tree}` ==
   `<lane-tip>^{tree}`) before resyncing local main.
6. After merge: resync `main` to `origin/main`
   (`git branch -f main origin/main` — but NOTE `main` is checked out in the
   `nse-cc-lane` worktree, so update it THERE or via `git -C`), then remove
   the worktree and lane branch. Do not touch the shared checkout's branch.
