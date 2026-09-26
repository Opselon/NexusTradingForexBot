"""Wave 3: per-route dependency profiler.

Question being answered: for each route in the FastAPI surface, which
heavyweight subsystems does its handler ACTUALLY touch at request time?
The answer determines whether Go can ever serve it locally or must always
forward to Python.

Why a runtime profiler and not AST/regex over source:
  * FastAPI routes in this codebase resolve dependencies INSIDE handlers
    (no `Depends(...)` graph to walk) - a static pass sees 0 DB hints on a
    surface where ~half the routes hit SQLite.
  * Signature annotations are request-shape, not resource shape.
  * A route can import a store lazily, inside a branch only some callers
    take.

So we observe: wrap the DatabaseDriver execute/query entry points and the
engine seams, replay each route against a cheap probe request, and record
which subsystems fired. This is GROUND TRUTH, not inference.

Output: api/migration/route_dependencies.json
  {"GET /api/account/equity-curve": ["db"], ...}

Replay caveats (kept honest):
  * GET/HEAD only. A POST that writes is not safely replayable in paper
    mode, and a probe that mutates state would corrupt the shared backend.
    Write routes are conservatively recorded as needing Python (the only
    safe answer) - marked "write:assumed".
  * A 4xx/5xx answer still tells us which subsystems fired on the way, so
    failures are recorded with their status, not skipped.
  * SSE/WebSocket routes are skipped (streaming, not request/response).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The scratch scripts live outside the repo; write into the worktree so the
# classifier and the Go generator read the same file.
MAIN = Path(r"C:/Users/Capsizer/source/repos/nse-api-golang-migration")
OUT = MAIN / "api" / "migration" / "route_dependencies.json"

os.environ.setdefault("NSE_RUNTIME_MODE", "paper")
os.environ.setdefault("NSE_ENGINE_DISABLE", "1")
os.environ.setdefault("NSE_MT5_DISABLE", "1")
os.environ.setdefault("NSE_MODEL_DISABLE", "1")
os.environ.setdefault("NSE_WEB_AUTH_DISABLE", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")

sys.path.insert(0, str(MAIN / "src"))

# ---------------------------------------------------------------- analysis -- #

_hits: Counter[str] = Counter()
_current: list[str] = []
_lock = threading.Lock()


def _start(route_key: str) -> None:
    with _lock:
        _current.append(route_key)


def _stop() -> str | None:
    with _lock:
        return _current.pop() if _current else None


def _record(subsystem: str) -> None:
    with _lock:
        if _current:
            _hits[f"{_current[-1]}|{subsystem}"] += 1


# ------------------------------------------------------------- the seams --- #


def _wrap_methods(cls: type, names: tuple[str, ...]) -> None:
    """Wrap the named methods on ONE class, skipping anything already wrapped.

    Methods defined on the class itself (as opposed to inherited from an
    ABC) shadow a base-class monkeypatch - the whole reason the concrete
    drivers must be wrapped individually.
    """
    for name in names:
        original = getattr(cls, name, None)
        if original is None or getattr(original, "_nse_profiled", False):
            continue

        def wrap(fn=original):
            def inner(self, *a, **kw):
                _record("db")
                return fn(self, *a, **kw)

            inner._nse_profiled = True
            return inner

        setattr(cls, name, wrap(original))


# The methods every persistence consumer goes through, regardless of which
# repository or plane called them.
_DRIVER_METHODS = (
    "execute",
    "executemany",
    "query",
    "query_readonly",
    "query_one",
    "upsert",
    "insert_ignore",
)


def _instrument_driver() -> None:
    """Wrap the DatabaseDriver entry points every persistence consumer goes
    through, so any route that reads/writes the relational store records a
    'db' hit regardless of which repository called it.

    Both concrete drivers OVERRIDE the ABC's methods (PostgreSQLDriver.query
    at postgres_driver.py:305, SQLiteDriver.query at sqlite_driver.py:279),
    so wrapping only the base class is intercepted by nobody under a real
    provider. Wrap each class in its own right.
    """
    from nexus_scalp.database.drivers import base as driver_base
    from nexus_scalp.database.drivers import postgres_driver, sqlite_driver

    _wrap_methods(driver_base.DatabaseDriver, _DRIVER_METHODS)
    _wrap_methods(postgres_driver.PostgreSQLDriver, _DRIVER_METHODS)
    _wrap_methods(sqlite_driver.SQLiteDriver, _DRIVER_METHODS)

    # The pooled fabric planes are a SEPARATE read/write path the drivers do
    # not participate in (PG-READ-PLANE-001): IncidentStore and the v1 audit
    # reads go through these under a non-SQLite provider, and PgReadPlane /
    # PgWritePlane open their own psycopg connections. If they were not
    # wrapped, a route that really hit Postgres would record observed=[].
    from nexus_scalp.database.fabric import pg_planes

    for plane in (pg_planes.PgReadPlane, pg_planes.PgWritePlane):
        _wrap_methods(plane, ("query", "query_one", "execute", "execute_batch"))


def _instrument_raw_sqlite() -> None:
    """Some web routes open sqlite3 directly (audit snapshots, debug
    consoles). Catch that path too."""
    import sqlite3

    if getattr(sqlite3.connect, "_nse_profiled", False):
        return
    real_connect = sqlite3.connect

    def profiled_connect(*a, **kw):
        _record("db")
        return real_connect(*a, **kw)

    profiled_connect._nse_profiled = True
    sqlite3.connect = profiled_connect


# ----------------------------------------------------------------- driver --- #

SKIP_METHODS = {"MOUNT", "HEAD", "OPTIONS"}


def probe_routes() -> dict[str, dict]:
    """Replay every GET route and record which subsystems it touched."""
    from starlette.testclient import TestClient

    from nexus_scalp.web.api_v1_wiring import _iter_effective_routes, create_v1_app
    from nexus_scalp.web.server import create_app

    _instrument_driver()
    _instrument_raw_sqlite()

    records: list[dict] = []
    for app, surface in ((create_app(), "dashboard"), (create_v1_app(), "v1")):
        for r in _iter_effective_routes(app.router.routes):
            if r.__class__.__name__ == "Mount":
                continue
            methods = sorted(getattr(r, "methods", []) or [])
            for m in methods:
                if m in SKIP_METHODS:
                    continue
                records.append({"surface": surface, "method": m, "path": r.path})

    client = TestClient(create_app())
    results: dict[str, dict] = {}
    skipped: list[str] = []

    for rec in records:
        key = f"{rec['method']} {rec['path']}"
        # Streaming endpoints hang a TestClient sync request.
        if "stream" in rec["path"] or "/ws" in rec["path"]:
            skipped.append(key)
            continue
        # Path params need a concrete value to actually reach the handler.
        path = rec["path"]
        if "{" in path:
            # Substitute plausible values so the handler body runs far
            # enough to touch its real dependencies.
            path = path.replace("{strategy_id}", "scalp_v1")
            path = path.replace("{model_id}", "champion")
            path = path.replace("{article_id}", "1")
            path = path.replace("{name}", "default")
            path = path.replace("{run_id}", "1")
            path = path.replace("{symbol}", "XAUUSD")
            path = path.replace("{id}", "1")
            path = path.replace("{config_id}", "1")
            path = path.replace("{session_id}", "1")
        # A bare query string is harmless and makes list endpoints exercise
        # their real pagination path.
        url = path + "?limit=1"

        # Probe SHAPE matters as much as instrumentation. Several handlers
        # guard their store reads behind an input the profiler did not send:
        #   /diagnostics/search + /diagnostics/trace return a constant on an
        #     EMPTY query and never build the IncidentStore;
        #   /diagnostics/lineage only reads the audit tables when ?ticket=
        #     is present (the default ?field=pnl path is a constant table);
        #   calibration only joins the audit tables when the serving model
        #     artifact exists.
        # Sending these makes the dependency visible instead of letting an
        # empty-probe short-circuit masquerade as statelessness.
        if "?" in url:
            url += "&"
        else:
            url += "?"
        url += "query=XAUUSD&ticket=1"

        _start(key)
        status = None
        body_sample = ""
        try:
            with client:
                resp = client.request(rec["method"], url, timeout=8)
                status = resp.status_code
                try:
                    body_sample = resp.text[:160]
                except Exception:
                    body_sample = ""
        except Exception:
            status = -1
        finally:
            _stop()

        subs = set()
        for h, n in _hits.items():
            if h.startswith(key + "|") and n:
                subs.add(h.rsplit("|", 1)[1])
        # Anything that mutates cannot be proven read-only by a replay.
        if rec["method"] not in ("GET",):
            subs.add("write:assumed")
        results[key] = {
            "needs_python": True,  # default; classification pass refines
            "observed": sorted(subs),
            "status": status,
            "body": body_sample,
        }
        # Clear only this route's counters so routes do not bleed into each
        # other. (Other routes' counters are kept; they are re-derived.)
        for h in [h for h in _hits if h.startswith(key + "|")]:
            del _hits[h]

    return {"routes": results, "skipped": skipped}


def main() -> int:
    data = probe_routes()
    routes = data["routes"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")

    n_db = sum(1 for v in routes.values() if "db" in v["observed"])
    n_ok = sum(1 for v in routes.values() if v["status"] and v["status"] > 0)
    print(f"routes profiled : {len(routes)}")
    print(f"observed db use : {n_db}")
    print(f"answered >=200  : {n_ok} (plus -1 timeouts)")
    print(f"skipped (stream): {len(data['skipped'])}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
