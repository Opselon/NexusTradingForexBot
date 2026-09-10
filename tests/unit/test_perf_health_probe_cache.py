"""PERF-HEALTH (2026-09-10) — /health must not run a full DB integrity sweep
per probe.

Measured on the production container (1,006 MB audit.db): PRAGMA
integrity_check costs ~600ms per call, the full HealthEngine sweep ~0.7-2.4s,
and the Docker healthcheck polls /health every 15s — thousands of full DB
scans per day on the request path.

Pinned contract:
  1. /health caches its HealthEngine verdict block on app.state for
     _HEALTH_TTL_SEC: repeated probes inside the TTL do NOT re-run the
     engine (probe-count pin on HealthEngine.overall);
  2. the cached verdict is the RESPONSE verdict (verdict semantics and the
     503 NOT-READY contract are unchanged — never weakened);
  3. the TTL is >= the healthcheck interval documented in docker-compose
     (15s), so every container probe after the first is cache-served;
  4. the deeper integrity authority is untouched: `nexus doctor` /
     `nexus health` / /api/v1/system/health still construct their own
     HealthEngine (out of this route's cache) — integrity_check remains a
     real diagnostic, just not a per-15s one.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from nexus_scalp.web.diagnostics_state_routes import _HEALTH_TTL_SEC


def test_health_ttl_is_at_least_the_docker_interval() -> None:
    # docker-compose.yml core healthcheck interval is 15s; the cache must
    # cover it or the fix is meaningless.
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "interval: 15s" in compose
    assert _HEALTH_TTL_SEC >= 15.0, "/health cache TTL must cover the 15s probe"


def test_health_probe_reuses_cached_verdict(monkeypatch) -> None:
    """Second probe inside the TTL must NOT recompute HealthEngine.overall."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import nexus_scalp.web.diagnostics_state_routes as routes

    app = FastAPI()
    routes.register_diagnostics_state_routes(
        app,
        _err=None,
        _log_err=lambda *a, **k: None,
        serialize_enums=lambda x: x,
        get_system_state=lambda: {},
    )
    client = TestClient(app)

    calls = {"n": 0}

    class _FakeEntry:
        category = "DATABASE"
        verdict = "PASS"
        state = "HEALTHY"
        optional = False

        def to_dict(self):
            return {
                "category": self.category,
                "verdict": self.verdict,
                "reason": "fake",
                "suggestion": "",
                "state": self.state,
                "optional": self.optional,
            }

    def _fake_overall(self, entries=None):
        calls["n"] += 1
        return "READY", [_FakeEntry()]

    monkeypatch.setattr("nexus_scalp.release.health.HealthEngine.overall", _fake_overall)

    r1 = client.get("/health")
    assert r1.status_code == 200
    r2 = client.get("/health")
    assert r2.status_code == 200
    assert calls["n"] == 1, (
        f"the HealthEngine sweep must run ONCE per TTL window (ran {calls['n']}x)"
    )
    assert r1.json()["verdict"] == r2.json()["verdict"] == "READY"


def test_health_probe_cache_expires(monkeypatch) -> None:
    """After the TTL the engine runs again (stale verdicts never stick)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import nexus_scalp.web.diagnostics_state_routes as routes

    app = FastAPI()
    routes.register_diagnostics_state_routes(
        app,
        _err=None,
        _log_err=lambda *a, **k: None,
        serialize_enums=lambda x: x,
        get_system_state=lambda: {},
    )
    client = TestClient(app)

    calls = {"n": 0}

    class _FakeEntry:
        category = "DATABASE"
        verdict = "PASS"
        state = "HEALTHY"
        optional = False

        def to_dict(self):
            return {
                "category": self.category,
                "verdict": self.verdict,
                "reason": "fake",
                "suggestion": "",
                "state": self.state,
                "optional": self.optional,
            }

    def _fake_overall(self, entries=None):
        calls["n"] += 1
        return "READY", [_FakeEntry()]

    monkeypatch.setattr("nexus_scalp.release.health.HealthEngine.overall", _fake_overall)

    assert client.get("/health").status_code == 200
    # age the cache past the TTL
    ts, verdict, checks, critical = app.state.health_probe_cache
    app.state.health_probe_cache = (ts - (_HEALTH_TTL_SEC + 1.0), verdict, checks, critical)
    assert client.get("/health").status_code == 200
    assert calls["n"] == 2, "an expired cache must be recomputed"


def test_health_not_ready_contract_unchanged(monkeypatch) -> None:
    """503 + verdict NOT READY survives the cache (the docker contract)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import nexus_scalp.web.diagnostics_state_routes as routes

    app = FastAPI()
    routes.register_diagnostics_state_routes(
        app,
        _err=None,
        _log_err=lambda *a, **k: None,
        serialize_enums=lambda x: x,
        get_system_state=lambda: {},
    )
    client = TestClient(app)

    class _FakeFailEntry:
        category = "MODEL"
        verdict = "FAIL"
        state = "ERROR"
        optional = False

        def to_dict(self):
            return {
                "category": self.category,
                "verdict": self.verdict,
                "reason": "fake fail",
                "suggestion": "",
                "state": self.state,
                "optional": self.optional,
            }

    def _fake_overall(self, entries=None):
        return "NOT READY", [_FakeFailEntry()]

    monkeypatch.setattr("nexus_scalp.release.health.HealthEngine.overall", _fake_overall)

    r = client.get("/health")
    assert r.status_code == 503
    body = json.loads(r.content.decode("utf-8"))
    # the 503 body carries the verdict (healthcheck.sh parses it from here)
    detail = body.get("detail") or body
    assert detail.get("verdict") == "NOT READY"


def test_integrity_check_still_available_as_explicit_diagnostic() -> None:
    """The deeper diagnostic authority must still exist (not weakened):
    `nexus doctor`/`nexus health` build their own uncached HealthEngine and
    the hygiene verifier runs PRAGMA integrity_check post-cleanup."""
    health_src = Path("src/nexus_scalp/release/health.py").read_text(encoding="utf-8")
    assert "PRAGMA integrity_check" in health_src, (
        "HealthEngine DATABASE check must keep its integrity probe"
    )
    hygiene_src = Path("src/nexus_scalp/hygiene/worker.py").read_text(encoding="utf-8")
    assert "PRAGMA integrity_check" in hygiene_src
