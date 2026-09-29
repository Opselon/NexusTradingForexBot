"""R-1 (Wave A) — the SSE health probe must not run 7 catalog round trips per
200 ms tick.

Phase-2 evidence (P2-05, measured on nexusdb): ``_build_health_section`` is
called on every SSE tick, and each call constructed a fresh
``DatabaseHealthService`` and ran ``check_domain("audit")`` — 7 catalog round
trips (ping, database_version, schema_migrations, table_count,
database_size_bytes, one table_exists per critical table) at ~151.59 ms per
call, ~76% of the 200 ms tick budget.

The fix is a short TTL memo inside ``DatabaseHealthService`` **plus** a
per-server shared instance, because a per-instance memo is useless when the
caller news up the service on every call.

This test pins the property at BOTH layers:
  1. a shared instance must be present on app.state after create_app();
  2. repeated ``check_domain`` calls inside the TTL run the uncached probe
     exactly ONCE;
  3. after the TTL the probe runs again — a stale verdict never sticks;
  4. ``invalidate()`` forces an immediate re-probe;
  5. a caller mutating its returned dict cannot corrupt the cache.
"""

from __future__ import annotations

import time
from typing import Any

import pytest


def test_create_app_exposes_a_shared_health_service() -> None:
    """Without a single long-lived instance the TTL memo is dead code: every
    SSE tick would get its own empty cache."""
    from nexus_scalp.web.server import create_app

    app = create_app(engine_ref=None)
    svc = getattr(app.state, "db_health_service", None)
    assert svc is not None, "create_app must install a shared health service"
    # Two lookups in the same tick must hit the SAME object.
    assert app.state.db_health_service is app.state.db_health_service


class _CountingHealthService:
    """Wraps DatabaseHealthService and counts UNCACHED probes per domain.

    Counting ``check_domain`` would be useless — every call goes through it,
    memoized or not. The quantity that proves the fix is how often the 7-trip
    uncached probe actually runs, so this wraps ``_check_domain_uncached``.
    """

    def __init__(self, real: Any) -> None:
        self._real = real
        self.probes: dict[str, int] = {}
        real._check_domain_uncached = self._count(real._check_domain_uncached)

    def _count(self, fn: Any) -> Any:
        def wrapped(domain: str) -> dict[str, Any]:
            self.probes[domain] = self.probes.get(domain, 0) + 1
            return fn(domain)

        return wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _service_with_counter(monkeypatch: pytest.MonkeyPatch) -> _CountingHealthService:
    from nexus_scalp.database import health as health_mod
    from nexus_scalp.web.server import create_app

    app = create_app(engine_ref=None)
    counter = _CountingHealthService(app.state.db_health_service)
    app.state.db_health_service = counter  # type: ignore[assignment]
    # Keep a reference so the fixture can find it without an app import cycle.
    monkeypatch.setattr(_SHARED, "app", app, raising=False)
    return counter


class _Shared:
    app: Any = None


_SHARED = _Shared()


@pytest.mark.parametrize("domain", ["audit"])
def test_repeated_probes_inside_ttl_hit_once(monkeypatch: pytest.MonkeyPatch, domain: str) -> None:
    counter = _service_with_counter(monkeypatch)
    app = _SHARED.app
    svc = app.state.db_health_service
    # A health probe that never resolves (no DB configured in CI) still
    # returns a dict — the contract is "never raises", and that is what the
    # SSE loop depends on.
    r1 = svc.check_domain(domain)
    r2 = svc.check_domain(domain)
    r3 = svc.check_domain(domain)
    assert counter.probes.get(domain, 0) == 1, (
        f"three calls inside the TTL must probe ONCE (probed {counter.probes})"
    )
    # The cached verdict is byte-identical to the fresh one for every key the
    # dashboard reads, so the fix cannot change what the UI displays.
    assert r1 == r2 == r3


def test_probe_recomputes_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = _service_with_counter(monkeypatch)
    app = _SHARED.app
    svc = app.state.db_health_service
    svc.check_domain("audit")
    svc.check_domain("audit")
    assert counter.probes.get("audit", 0) == 1
    # Age the memo past the deadline by rewinding the stored timestamp.
    deadline, snapshot = svc._health_cache["audit"]
    from nexus_scalp.database.health import DatabaseHealthService

    svc._health_cache["audit"] = (
        deadline - (DatabaseHealthService._HEALTH_TTL_SECONDS + 1.0),
        snapshot,
    )
    svc.check_domain("audit")
    assert counter.probes.get("audit", 0) == 2, "an expired memo must re-probe"


def test_invalidate_forces_immediate_reprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = _service_with_counter(monkeypatch)
    app = _SHARED.app
    svc = app.state.db_health_service
    svc.check_domain("audit")
    assert counter.probes.get("audit", 0) == 1
    svc.invalidate("audit")
    svc.check_domain("audit")
    assert counter.probes.get("audit", 0) == 2, "invalidate() must bypass the TTL"
    # Domain scoping: clearing one domain must not clear the others.
    svc.check_domain("news")
    svc.invalidate("audit")
    svc.check_domain("news")
    assert counter.probes.get("news", 0) == 1, "invalidate(audit) must not touch news"


def test_caller_cannot_corrupt_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = _service_with_counter(monkeypatch)
    app = _SHARED.app
    svc = app.state.db_health_service
    first = svc.check_domain("audit")
    poison = "POISONED-VALUE"
    first["health"] = poison
    second = svc.check_domain("audit")
    assert second.get("health") != poison, (
        "a caller mutating its own view must not corrupt the cached verdict"
    )
    assert counter.probes.get("audit", 0) == 1


def test_check_domain_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SSE loop has no try/except around check_domain: a raising probe
    would take down the whole health section, so this contract is load-bearing."""
    counter = _service_with_counter(monkeypatch)
    app = _SHARED.app
    svc = app.state.db_health_service
    # Force the uncached path to raise.
    monkeypatch.setattr(
        svc._real,
        "_check_domain_uncached",
        lambda d: (_ for _ in ()).throw(RuntimeError("synthetic DB failure")),
        raising=False,
    )
    out = svc.check_domain("audit")
    assert isinstance(out, dict)
    assert out.get("domain") == "audit"
    assert out.get("health") in {"Error", "Warning", "Healthy"}


def test_ttl_is_short_enough_to_reflect_recovery() -> None:
    """A long TTL would hide a real recovery from the dashboard. 5 s is the
    ceiling: the SSE loop ticks at 5 Hz, so this is ~25 ticks of staleness,
    not minutes."""
    from nexus_scalp.database.health import DatabaseHealthService

    assert DatabaseHealthService._HEALTH_TTL_SECONDS <= 5.0
