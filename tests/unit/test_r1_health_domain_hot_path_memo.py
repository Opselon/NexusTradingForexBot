"""R-1 (Phase-2 remediation) — ``check_domain`` must not probe at SSE cadence.

Measured defect (Phase-1 baseline F-002 / Phase-2 matrix R-1):
``check_domain("audit")`` costs up to ~152 ms per call on PostgreSQL (7 catalog
round trips). ``get_system_state()`` runs on EVERY SSE tick (200 ms per client)
and on every dashboard poll, and its health section calls
``DatabaseHealthService().check_domain("audit")`` — a FRESH service per call.
At 5 Hz per client that is ~35 round trips/s per client and it is 76% of every
200 ms tick.

The memo replays a healthy CONNECTED snapshot within a short TTL. Pinned here:

  1. a healthy snapshot is replayed within the TTL and discloses the hit
     (``snap["memo"] is True``), with the original ``latency_ms`` preserved;
  2. the probe is NOT re-run while the memo is live (a counting driver proves
     the second call did no work);
  3. a FAILURE is never memoized — a disconnected/Error result is recomputed
     on every call, so an operator sees recovery the instant it happens. This
     is the same rule the PERF-DB-STATUS cache pins: errors stay retryable;
  4. the memo is bypassed whenever ``get_driver`` has been replaced on the
     health module (tests and diagnostics inject their own driver), so an
     injected failure can never read a cached healthy snapshot for the same
     config identity;
  5. a config change (provider switch / different target) gets a fresh entry;
  6. ``invalidate_domain_cache()`` drops the memoized state.

Why a class-level memo: both production call sites
(``web/server.py:_build_health_section`` and ``release/health.py``) construct a
new ``DatabaseHealthService`` per call, so an instance cache would never hit.
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.database import health as health_mod
from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver
from nexus_scalp.database.health import DatabaseHealthService


class _CountingSQLiteDriver(SQLiteDriver):
    """A real SQLite driver that counts the probe round trips it serves."""

    def __init__(self, cfg: DatabaseConfig) -> None:
        super().__init__(cfg)
        self.ping_calls = 0
        self.table_count_calls = 0

    def ping(self, conn: Any = None) -> bool:
        self.ping_calls += 1
        return super().ping(conn)

    def table_count(self, conn: Any = None) -> int:
        self.table_count_calls += 1
        return super().table_count(conn)


@pytest.fixture(autouse=True)
def _clean_domain_cache(monkeypatch: Any) -> Any:
    """Every test starts with an empty memo (the cache is class-level).

    The memo's driver-injection bypass disables it whenever ``get_driver`` has
    been replaced — which is how tests install their driver — so each memo
    test also sets the documented test hook to exercise the replay path.
    """
    monkeypatch.setattr(DatabaseHealthService, "_memo_allow_injected_drivers", True)
    DatabaseHealthService.invalidate_domain_cache()
    yield
    DatabaseHealthService.invalidate_domain_cache()


def _install_driver(monkeypatch: Any, driver: SQLiteDriver) -> DatabaseHealthService:
    """Bind ``get_driver`` on the health module to ``driver`` (per-test)."""
    monkeypatch.setattr(health_mod, "get_driver", lambda cfg: driver)
    return DatabaseHealthService()


def _service_with_config(monkeypatch: Any, cfg: DatabaseConfig) -> DatabaseHealthService:
    """A service whose ``resolve_config`` always returns ``cfg``.

    ``resolve_config`` is the resolution surface the R-1 memo keys on: in
    production it reads the settings DB, which is exactly why the memo must
    key on the RESOLVED CONFIG rather than anything the caller hands it.
    """
    svc = DatabaseHealthService()
    monkeypatch.setattr(svc, "resolve_config", lambda domain: cfg)
    return svc


def _healthy_service(
    tmp_path: Any, monkeypatch: Any
) -> tuple[DatabaseHealthService, _CountingSQLiteDriver]:
    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "probe.db"))
    driver = _CountingSQLiteDriver(cfg)
    driver.execute("CREATE TABLE audit_ledger (id INTEGER PRIMARY KEY, ticket INTEGER)")
    driver.execute("CREATE TABLE audit_orders (id INTEGER PRIMARY KEY)")
    driver.execute("CREATE TABLE audit_signals (id INTEGER PRIMARY KEY)")
    driver.execute("CREATE TABLE audit_account_snapshots (id INTEGER PRIMARY KEY)")
    monkeypatch.setattr(health_mod, "get_driver", lambda c: driver)
    return _service_with_config(monkeypatch, cfg), driver


def test_healthy_snapshot_is_replayed_within_ttl(tmp_path: Any, monkeypatch: Any) -> None:
    """Two calls within the TTL return the same probe, and it is disclosed."""
    svc, driver = _healthy_service(tmp_path, monkeypatch)

    first = svc.check_domain("audit")
    assert first["status"] == "CONNECTED"
    assert first["connected"] is True
    assert first.get("memo") is not True  # a real probe, not a replay

    second = svc.check_domain("audit")
    assert second["status"] == "CONNECTED"
    assert second.get("memo") is True  # the hit is disclosed
    # The replayed payload is the same probe result, latency included.
    assert second["latency_ms"] == first["latency_ms"]


def test_the_memo_does_no_database_work_on_replay(tmp_path: Any, monkeypatch: Any) -> None:
    """The whole point: a replayed snapshot costs zero driver round trips."""
    svc, driver = _healthy_service(tmp_path, monkeypatch)

    svc.check_domain("audit")
    after_first = driver.ping_calls
    assert after_first >= 1

    svc.check_domain("audit")  # served from the memo
    svc.check_domain("audit")  # served from the memo

    assert driver.ping_calls == after_first, "a replayed snapshot must not ping"
    assert driver.table_count_calls == 1, "table_count must run exactly once"


def test_failures_are_never_memoized(tmp_path: Any, monkeypatch: Any) -> None:
    """A disconnected result is recomputed every call until it recovers.

    Caching an error would freeze the operator's view at the moment of
    failure and hide the recovery — the exact anti-pattern the
    PERF-DB-STATUS cache forbids.
    """
    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "gone.db"))

    class _FailingDriver(_CountingSQLiteDriver):
        def ping(self, conn: Any = None) -> bool:
            self.ping_calls += 1
            return False  # never connects

    failing = _FailingDriver(cfg)
    monkeypatch.setattr(health_mod, "get_driver", lambda c: failing)
    svc = _service_with_config(monkeypatch, cfg)

    one = svc.check_domain("audit")
    assert one["connected"] is False
    assert one["health"] == "Error"
    assert one.get("memo") is not True

    two = svc.check_domain("audit")
    assert two.get("memo") is not True, "a failure must never be replayed"
    assert failing.ping_calls == 2, "a failure must re-probe every call"


def test_driver_injection_bypasses_the_memo(tmp_path: Any, monkeypatch: Any) -> None:
    """A replaced ``get_driver`` disables the memo in production.

    Tests and diagnostics inject drivers to observe specific failures. If the
    memo served a cached healthy snapshot for the same config identity, an
    injected failing driver would report HEALTHY — the worst possible lie.
    """
    # Prime the memo with the hook on, then turn it back off (production state).
    healthy_svc, _ = _healthy_service(tmp_path, monkeypatch)
    healthy_svc.check_domain("audit")
    assert DatabaseHealthService._domain_health_cache, "memo should be primed"
    monkeypatch.setattr(DatabaseHealthService, "_memo_allow_injected_drivers", False)

    # Inject a failing driver for the SAME config identity.
    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "probe.db"))

    class _Down(SQLiteDriver):
        def ping(self, conn: Any = None) -> bool:
            return False

    _down = _Down(cfg)
    monkeypatch.setattr(health_mod, "get_driver", lambda c: _down)

    snap = _service_with_config(monkeypatch, cfg).check_domain("audit")
    assert snap["connected"] is False, "an injected failure must not read the memo"
    assert snap.get("memo") is not True


def test_a_different_target_gets_its_own_entry(tmp_path: Any, monkeypatch: Any) -> None:
    """Config identity, not domain name alone, keys the memo."""
    svc_a, driver_a = _healthy_service(tmp_path, monkeypatch)
    svc_a.check_domain("audit")
    assert len(DatabaseHealthService._domain_health_cache) == 1

    # A different SQLite target is a different database; it must probe.
    other_path = tmp_path / "other.db"
    cfg_b = DatabaseConfig.for_sqlite("audit", path=str(other_path))
    driver_b = _CountingSQLiteDriver(cfg_b)
    driver_b.execute("CREATE TABLE audit_ledger (id INTEGER PRIMARY KEY, ticket INTEGER)")
    driver_b.execute("CREATE TABLE audit_orders (id INTEGER PRIMARY KEY)")
    driver_b.execute("CREATE TABLE audit_signals (id INTEGER PRIMARY KEY)")
    driver_b.execute("CREATE TABLE audit_account_snapshots (id INTEGER PRIMARY KEY)")
    monkeypatch.setattr(health_mod, "get_driver", lambda c: driver_b)

    snap = _service_with_config(monkeypatch, cfg_b).check_domain("audit")
    assert snap.get("memo") is not True, "a new target must probe, not replay"
    assert driver_b.ping_calls >= 1
    assert len(DatabaseHealthService._domain_health_cache) == 2


def test_invalidate_drops_the_memo(tmp_path: Any, monkeypatch: Any) -> None:
    """A provider switch / migration must be observable immediately."""
    svc, driver = _healthy_service(tmp_path, monkeypatch)
    svc.check_domain("audit")
    assert DatabaseHealthService._domain_health_cache

    DatabaseHealthService.invalidate_domain_cache()
    assert not DatabaseHealthService._domain_health_cache

    snap = svc.check_domain("audit")
    assert snap.get("memo") is not True, "invalidation forces a fresh probe"


def test_ttl_is_short_enough_to_track_a_real_change(tmp_path: Any, monkeypatch: Any) -> None:
    """The TTL cannot be so long that a real outage hides behind it."""
    ttl = DatabaseHealthService.DOMAIN_HEALTH_TTL_SECONDS
    assert 0.5 <= ttl <= 30.0, "must stay in the same order as the DB-status cache"


def test_existing_failure_reason_tests_still_pass(tmp_path: Any, monkeypatch: Any) -> None:
    """The memo preserves the HEALTH-DBREASON contract verbatim.

    ``test_health_db_reason`` monkeypatches ``get_driver`` to inject specific
    failures; the driver-injection bypass keeps those tests meaningful (a
    cached healthy snapshot would have masked every one of them).
    """
    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "auth.db"))

    class _AuthFails(SQLiteDriver):
        _BOOM = RuntimeError("password authentication failed for user postgres")

        def ping(self, conn: Any = None) -> bool:
            raise self._BOOM

    monkeypatch.setattr(health_mod, "get_driver", lambda c: _AuthFails(cfg))

    snap = _service_with_config(monkeypatch, cfg).check_domain("audit")
    assert snap["status"] == "DISCONNECTED"
    assert "password authentication failed" in snap["error"]
    assert snap.get("memo") is not True
