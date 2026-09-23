"""LD-3: check_api_200_but_wrong() must not rebuild the FastAPI app per sweep.

Root cause: the check called create_app() + app.openapi() on EVERY forensic
sweep (~7-20s), rebuilding the whole app — routes, static mounts, the ALT-UI
directory resolution that logs the repeating ``[ALT-UI] serving ...`` line —
just for a set-membership test.

This file pins three invariants of the fix:
  (a) the check's verdict contract is unchanged for a given route set;
  (b) create_app() is invoked AT MOST ONCE across multiple check invocations;
  (c) a FRESH app factory / fresh process state is not poisoned by a stale
      global cache (the trap a bare module-level set would fall into).
"""

from __future__ import annotations

import contextlib
import logging

import pytest

from nexus_scalp.forensics import checks_observability as obs
from nexus_scalp.forensics.checks_observability import (
    _resolved_api_paths,
    check_api_200_but_wrong,
)
from nexus_scalp.forensics.models import CheckResult, HealthStatus

CHECK_ID = "CHECK-API-01"
EXPECTED_ENDPOINTS = (
    "/api/status",
    "/api/chart/history",
    "/api/news/sources",
    "/api/research/health",
    "/api/mt5/status",
)


@contextlib.contextmanager
def _counting_create_app(monkeypatch):
    """Patch nexus_scalp.web.server.create_app with a call-counting wrapper.

    Counts via the module's OWN attribute, so the wrapper is picked up by the
    check's local ``from nexus_scalp.web.server import create_app`` import and
    a fresh wrapper instance is a distinct cache key (fresh-app isolation, (c)).
    """
    from nexus_scalp.web import server

    original = server.create_app
    calls = {"n": 0}

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(server, "create_app", wrapper)
    # The real app is genuinely built by create_app(); keep the prior cache from
    # satisfying the check so the counter observes the actual first build.
    monkeypatch.setitem(obs.__dict__, "_API_SURFACE_CACHE", {})
    try:
        yield calls
    finally:
        with contextlib.suppress(Exception):
            monkeypatch.delattr(server, "create_app")


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    """Every test starts with an empty per-process cache (test isolation)."""
    monkeypatch.setitem(obs.__dict__, "_API_SURFACE_CACHE", {})
    yield


# ---------------------------------------------------------------------------
# (a) verdict contract unchanged
# ---------------------------------------------------------------------------


class TestVerdictContract:
    def test_real_app_surface_passes(self):
        r = check_api_200_but_wrong()
        assert isinstance(r, CheckResult)
        assert r.check_id == CHECK_ID
        assert r.status is HealthStatus.PASS
        assert r.detail in (None, "")
        for ep in EXPECTED_ENDPOINTS:
            assert r.observed[ep] is True

    def test_missing_endpoint_is_degraded_not_unknown(self, monkeypatch):
        """A built app lacking a semantic-health endpoint degrades, not UNKNOWN."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/api/status")
        def status():  # pragma: no cover - registration only
            return {}

        # Only /api/status registered -> 4 of 5 missing.
        monkeypatch.setattr(
            "nexus_scalp.web.server.create_app",
            lambda: app,
            raising=False,
        )
        r = check_api_200_but_wrong()
        assert r.status is HealthStatus.DEGRADED
        assert r.detail == "API_SURFACE_MISSING"
        assert r.observed["/api/status"] is True
        assert r.observed["/api/mt5/status"] is False
        assert "/api/mt5/status" in r.evidence

    def test_unbuildable_app_fails_closed_to_unknown(self, monkeypatch):
        """App cannot be built -> UNKNOWN, never a fabricated PASS (§37)."""

        def boom():
            raise RuntimeError("config missing")

        monkeypatch.setattr("nexus_scalp.web.server.create_app", boom, raising=False)
        r = check_api_200_but_wrong()
        assert r.status is HealthStatus.UNKNOWN
        assert "RuntimeError" in r.evidence
        assert "config missing" in r.evidence
        assert all(v is False for v in r.observed.values())

    def test_build_failure_is_not_cached(self, monkeypatch):
        """A failed build must remain retriable: next sweep gets another shot."""
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            from fastapi import FastAPI

            app = FastAPI()
            for ep in EXPECTED_ENDPOINTS:

                @app.get(ep)
                def _route():  # pragma: no cover - registration only
                    return {}

            return app

        monkeypatch.setattr("nexus_scalp.web.server.create_app", flaky, raising=False)
        first = check_api_200_but_wrong()
        assert first.status is HealthStatus.UNKNOWN
        second = check_api_200_but_wrong()
        assert second.status is HealthStatus.PASS
        assert calls["n"] == 2  # the failure was not cached as "forever broken"


# ---------------------------------------------------------------------------
# (b) create_app() invoked at most once across multiple invocations
# ---------------------------------------------------------------------------


class TestBuildOnce:
    def test_create_app_called_once_across_sweeps(self, monkeypatch):
        with _counting_create_app(monkeypatch) as calls:
            for _ in range(5):
                r = check_api_200_but_wrong()
                assert r.status is HealthStatus.PASS
        assert calls["n"] == 1, f"create_app() called {calls['n']}x — must build once"

    def test_create_app_called_once_across_concurrent_sweeps(self, monkeypatch):
        import threading

        with _counting_create_app(monkeypatch) as calls:
            barrier = threading.Barrier(8)
            results: list[CheckResult] = []
            lock = threading.Lock()

            def sweep():
                barrier.wait()
                r = check_api_200_but_wrong()
                with lock:
                    results.append(r)

            threads = [threading.Thread(target=sweep) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        assert len(results) == 8
        assert all(r.status is HealthStatus.PASS for r in results)
        assert calls["n"] == 1, f"create_app() called {calls['n']}x under contention"

    def test_direct_helper_builds_once(self, monkeypatch):
        with _counting_create_app(monkeypatch) as calls:
            for _ in range(3):
                paths, err = _resolved_api_paths()
                assert err is None
                assert "/api/status" in paths
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# (c) fresh app state is NOT poisoned by a stale global cache
# ---------------------------------------------------------------------------


class TestFreshAppNotPoisoned:
    def test_fresh_app_factory_gets_own_cache_entry(self, monkeypatch):
        """A distinct create_app identity must not see another factory's surface."""
        from fastapi import FastAPI

        # First factory: the real app — all 5 endpoints present, cached now.
        r1 = check_api_200_but_wrong()
        assert r1.status is HealthStatus.PASS

        # Second factory: a FRESH minimal app with only ONE of the endpoints.
        fresh = FastAPI()

        @fresh.get("/api/status")
        def _only():  # pragma: no cover - registration only
            return {}

        monkeypatch.setattr("nexus_scalp.web.server.create_app", lambda: fresh, raising=False)
        r2 = check_api_200_but_wrong()
        # The stale full-surface cache must NOT satisfy this check: the fresh
        # app really is missing 4 endpoints, so the verdict must degrade.
        assert r2.status is HealthStatus.DEGRADED
        assert r2.observed["/api/status"] is True
        assert r2.observed["/api/mt5/status"] is False
        assert len(obs._API_SURFACE_CACHE) == 2  # both factories cached, keyed apart

    def test_route_added_to_cached_app_is_still_seen(self, monkeypatch):
        """A route added to the SAME cached app must not be masked by the cache."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/api/status")
        def _status():  # pragma: no cover - registration only
            return {}

        monkeypatch.setattr("nexus_scalp.web.server.create_app", lambda: app, raising=False)
        r1 = check_api_200_but_wrong()
        assert r1.status is HealthStatus.DEGRADED  # 4 of 5 missing

        @app.get("/api/mt5/status")
        def _mt5():  # pragma: no cover - registration only
            return {}

        r2 = check_api_200_but_wrong()
        assert r2.observed["/api/mt5/status"] is True  # new route detected, no rebuild

    def test_cache_does_not_leak_between_isolated_checks(self, monkeypatch):
        """A real build followed by a fresh-app failure must not read the old PASS."""
        r = check_api_200_but_wrong()
        assert r.status is HealthStatus.PASS

        monkeypatch.setattr(
            "nexus_scalp.web.server.create_app",
            lambda: (_ for _ in ()).throw(RuntimeError("fresh process, no config")),
            raising=False,
        )
        r2 = check_api_200_but_wrong()
        assert r2.status is HealthStatus.UNKNOWN
        assert "fresh process, no config" in r2.evidence

    def test_no_alt_ui_log_repeat_across_sweeps(self, monkeypatch, caplog):
        """LD-3's observable symptom: the [ALT-UI] line fires ONCE, not per sweep."""
        server = pytest.importorskip("nexus_scalp.web.server")
        with _counting_create_app(monkeypatch) as calls:
            with caplog.at_level(logging.INFO, logger="nexus_scalp"):
                for _ in range(4):
                    assert check_api_200_but_wrong().status is HealthStatus.PASS
        alt_lines = [
            rec
            for rec in caplog.records
            if getattr(rec, "name", "") == getattr(server, "__name__", "")
            and "[ALT-UI]" in rec.getMessage()
        ]
        assert not alt_lines, "create_app() must not be re-invoked per sweep"
        assert calls["n"] == 1
