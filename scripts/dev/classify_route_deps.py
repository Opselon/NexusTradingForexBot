"""Wave 3: classify every route by the subsystems its handler actually needs.

Input  : api/migration/route_dependencies.json (runtime profiler output)
Output : the same file with `needs_python` + `why` resolved per route, and a
         summary the generator turns into a Go-side routing table.

Classification contract (the part that has to be honest):

  * "db" observed          -> needs Python. The Go plane has no database
                              driver and never will (DATABASE PORTABILITY
                              mission: SQLite *and* PostgreSQL via one
                              DatabaseDriver seam). Go cannot answer these.
  * write:assumed          -> needs Python. Non-GET routes were not safely
                              replayable; the only defensible answer is to
                              forward. Marked assumed, not observed.
  * neither, answered 2xx  -> Go-servable CANDIDATE. It read no relational
                              state, so the response could be served from
                              Go. Still forwarded today (Wave 3 only
                              classifies); a later wave caches/serves.
  * neither, answered 4xx/5xx/-1
                           -> needs Python. The handler could not complete
                              without state this probe environment lacked,
                              which is itself evidence the route needs the
                              Python plane's backing state to answer.

A route is only marked Go-servable when it answered 2xx AND touched none of
the heavyweight subsystems. That is a strict, two-sided test — neither
"looks cheap" nor "answered at all" is sufficient alone.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The scratch scripts live outside the repo; resolve the worktree explicitly.
MAIN = Path(r"C:/Users/Capsizer/source/repos/nse-api-golang-migration")
SRC = MAIN / "api" / "migration" / "route_dependencies.json"

# Subsystems that only the Python plane can satisfy.
PYTHON_ONLY = ("db",)

# Markers a handler emits when it SHORT-CIRCUITED because a backing subsystem
# was absent - the route answered 200 but with no real data. A route that
# returns one of these under a disabled engine is NOT stateless: it simply
# took the early-exit branch instead of touching its real dependency. Serving
# such a body from Go would ship a permanent stub where Python ships live
# state. This was a real classifier false positive (account/performance,
# trades, drawdown all returned 200 ENGINE_UNAVAILABLE in the probe).
STUB_MARKERS = (
    '"reason":"ENGINE_UNAVAILABLE"',
    '"reason": "ENGINE_UNAVAILABLE"',
    '"available":false',
    '"available": false',
    '"MT5_UNAVAILABLE"',
    '"MODEL_UNAVAILABLE"',
    '"UNAVAILABLE"',
)


def _is_stub(body: str) -> bool:
    b = body.replace(" ", "")
    return any(m.replace(" ", "") in b for m in STUB_MARKERS)


# A truly stateless route still returns SUBSTANTIVE content. These shapes are
# the other masked-dependency class: an empty list / empty object / null
# means the handler short-circuited on an absent subsystem (engine not
# attached, store not populated) and returned the "nothing to report" branch
# instead of touching its real dependency. /api/account/trades is the proven
# case: [] with no engine, audit.get_broker_trades() with one. Shipping that
# [] from Go would permanently return an empty trade history.
EMPTY_ANSWERS = ("[]", "{}", "null", '{"data":[]}', '{"data":{}}', "")


# A handler that reports its own failure inside a 200 body (this codebase's
# legacy convention: {"success":false,"error":...} with HTTP 200) did not
# answer the question. The DB console routes do this when a required input
# is absent from the probe - the route is a live query interface, not a
# stateless constant.
FAILED_200_MARKERS = ('"success":false', '"success": false', '"success":"false"')


def _is_failed_200(body: str) -> bool:
    b = body.replace(" ", "")
    return any(m.replace(" ", "") in b for m in FAILED_200_MARKERS)


# The route's own 200 body was a substantive envelope (not a bare []), but
# the ITEMS were empty. This is the most dangerous masked class: the v1 read
# layer funnels through readers guarded by ``if not repo._is_sqlite: return
# []`` (124 such guards across the codebase). On a box whose persisted
# provider is PostgreSQL the guard fires BEFORE the driver seam is touched,
# so the profiler records observed=[] and the route looks perfectly
# stateless - while the route is a live query that returns real rows the
# moment the SQLite path is active. An empty-items envelope is therefore
# treated as UNPROVEN, never as evidence of statelessness.
EMPTY_ITEM_MARKERS = ('"items":[]', '"items": []', '"runs":[]', '"runs": []')


def _is_empty_items(body: str) -> bool:
    b = body.replace(" ", "")
    return any(m.replace(" ", "") in b for m in EMPTY_ITEM_MARKERS)


# CONDITIONALLY-DB ROUTES: handlers that read a store only when a runtime
# precondition the probe cannot synthesise holds (an attached engine, a
# present serving-model artifact). The profiler probes with the engine
# disabled, so it legitimately records observed=[] and the body looks
# substantive - but with an engine attached the same route runs a live
# SELECT. Source is the only honest authority for these, so they are pinned
# here: correctness over a clean-looking table.
CONDITIONALLY_DB = {
    "GET /api/experience/summary": (
        "engine-gated: builds the summary only when app.state.engine has an "
        "experience_engine, then SELECTs strategy_intelligence_registry over "
        "engine.audit._db_path (debug_research_routes.py:861-885)"
    ),
    "GET /api/operator/calibration": (
        "artifact-gated: joins audit_experiences to audit_experience_outcomes "
        "only when the serving model fingerprint exists "
        "(calibration_monitor.py:65-98)"
    ),
}


def classify(
    observed: list[str], status: int, method: str, body: str, key: str = ""
) -> tuple[bool, str]:
    if key in CONDITIONALLY_DB:
        return True, f"conditionally-db:{CONDITIONALLY_DB[key].split(':')[0]}"

    for sub in PYTHON_ONLY:
        if sub in observed:
            return True, f"observed:{sub}"

    if method not in ("GET", "HEAD"):
        return True, "write:assumed"

    if status is None or status < 0:
        return True, "probe-failed"

    if 200 <= status < 400:
        # A 200 that is a synthesized "subsystem absent" stub is a masked
        # dependency, not a stateless answer.
        if _is_stub(body or ""):
            return True, "stub-in-2xx"
        # An empty answer likewise means the handler took its nothing-to-
        # report branch under a missing subsystem.
        if (body or "").strip() in EMPTY_ANSWERS:
            return True, "empty-in-2xx"
        # A self-reported failure inside a 200: the handler did not (could
        # not) answer under the probe, so its dependency is unproven.
        if _is_failed_200(body or ""):
            return True, "failed-in-2xx"
        # A substantive envelope whose items/runs list is empty: the v1
        # read layer short-circuits non-SQLite providers before the driver
        # seam, so observed=[] here is not evidence of statelessness.
        if _is_empty_items(body or ""):
            return True, "empty-items-in-2xx"
        return False, "stateless-2xx"

    return True, f"non-2xx:{status}"


def main() -> int:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    routes = data["routes"]

    summary = Counter()
    for key, rec in routes.items():
        method = key.split(" ", 1)[0]
        needs, why = classify(
            rec.get("observed", []),
            rec.get("status", -1),
            method,
            rec.get("body", ""),
            key,
        )
        rec["needs_python"] = needs
        rec["why"] = why
        summary[why] += 1

    SRC.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")

    go_candidates = [k for k, v in routes.items() if not v["needs_python"]]
    print(f"classified       : {len(routes)} routes")
    print(f"by reason        : {dict(summary)}")
    print(f"needs Python     : {len(routes) - len(go_candidates)}")
    print(f"Go-servable      : {len(go_candidates)}")
    print("  (stateless GET that touched no subsystem and answered 2xx)")
    print(f"skipped (stream) : {len(data.get('skipped', []))}")
    print()
    print("Go-servable sample:")
    for k in sorted(go_candidates)[:12]:
        print(f"  {k}  [{routes[k]['why']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
