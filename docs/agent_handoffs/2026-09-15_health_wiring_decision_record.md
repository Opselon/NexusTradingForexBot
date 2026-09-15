# HEALTH-WIRING DECISION RECORD (role 12 Reviewer/Gatekeeper, 2026-09-15)

Item: `check_worker_liveness` + `check_a2a_gateway` are defined in HealthEngine
(`src/nexus_scalp/release/health.py:518` / `:573`) but ABSENT from the `run_all()`
checks list (`health.py:1158-1181`). Heartbeat-stale FAIL and gateway-unreachable
WARNING are therefore invisible to `/health` + `doctor`. Ledger: HEALTH-WIRING-UNDECIDED (P2).
This record settles the framing so a future wave lands it deliberately, not blind.

## Decision

1. **A2A_GATEWAY: NEVER wire into `run_all()` as consumed by the web `/health` route.**
   `check_a2a_gateway` does a synchronous `urllib.request.urlopen(timeout=3)` GET to
   `http://{host}:{port}/health` (`health.py:606-610`). The web `/health` handler builds
   its verdict via `HealthEngine().overall()` (`web/diagnostics_state_routes.py:136`),
   which calls `run_all()`. Wiring A2A into that list makes the health endpoint make a
   blocking HTTP request TO ITSELF on every uncached computation: a sync socket on the
   event loop inside the request it is serving (3s stall minimum, 503-vs-503 deadlock
   shape once DEGRADED also 503s — the 60s verdict cache only hides it between refreshes).
   Correct placement: the standalone CLI surfaces ONLY (`nexus doctor` runs as its own
   process — external probe, no recursion; it already handles engine-not-running as WARNING).
   That means a `run_all(...)` that the web route calls must exclude it; doctor's sweep
   includes it. Today doctor and /health share `run_all()` — the wiring therefore needs an
   `include_external_probes: bool` parameter (or a separate doctor list), not a one-line append.

2. **WORKER_LIVENESS: wire, behind the same list split, and keep it non-critical.**
   Cost is one `SELECT last_cycle_at ... LIMIT 1` per table over a mode=ro connection
   (`health.py:532-544`) — cheap, no PRAGMA integrity sweep, safe inside the 60s cache.
   It reports a genuinely silent failure class (a crashed intelligence/research worker is
   invisible everywhere else — the BUG-254/276 lineage showed checkpoint tables are dead
   weight until someone reads them). Category is NOT in `CRITICAL_CATEGORIES`
   (`health.py:70-78`), so a stale-heartbeat FAIL moves READY -> DEGRADED, never NOT READY:
   verdict-surface change is real (docker healthcheck accepts DEGRADED, `docker/healthcheck.sh`
   parses the 503 body) but restart-neutral. Acceptable.
   Caveat the landing PR must handle: a freshly-installed engine that has never run a worker
   cycle gets "checkpoint tables empty" WARNING (`health.py:545-550`) — mark it
   `optional=True, state=NOT_INITIALIZED` like the other lazy subsystems (`health.py:955-962`)
   so `nexus verify`/smoke exit-code matrices don't newly red on fresh installs.

3. **Test-surface consequence (why "blind" wiring was refused):** pinning the exact
   `run_all()` list/count is a known test shape in this repo (doctor "USER ACTION" +
   len(entries) SSOT from BUG-277; the 24-check doctor banner). Any wiring PR must re-pin
   those pins in the SAME commit and must re-run the docker fresh-volume boot gate, since
   DEGRADED flips the /health body content the healthcheck parses.

## Action for a future wave
Implement as: `run_all(include_external_probes: bool = False)`; doctor passes True; the web
route keeps False (A2A never self-probes). Append `("WORKER_LIVENESS", ...)` always-on with
the empty-tables path upgraded to optional/NOT_INITIALIZED. Update doctor counts + any
run_all-length pins; targeted lanes: `tests/unit/test_bug277*`, cli-e2e doctor|health,
diagnostics routes. Est. half-day. Until then BOTH checks stay intentionally unwired —
this is now a DECIDED design position, not an oversight.

Related live items unchanged: NIGHTLY-E2E-CURLF and NIGHTLY-CHAOS-CIRESULTSDIR are
operator workflow-file edits (out of this job's scope); BUG-276 live acceptance still
pending (no scheduled Nightly Client E2E run at/after 1b66e014 as of 2026-09-15 06:00Z —
latest is 09-14 08:03Z at 79401911).
