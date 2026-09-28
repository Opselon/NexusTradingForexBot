"""HEALTH-DBREASON: a failed DB connection must report the driver's OWN error.

The defect: ``DatabaseHealthService.check_domain`` and the drivers' boolean
``ping()`` collapsed every distinct PostgreSQL failure into the fixed string
``"connection failed"``. On a live box whose credential had gone stale the
operator saw:

    DATABASE FAIL  postgresql://localhost:5432/nexusdb: DISCONNECTED (connection failed)
    suggestion: Start the PostgreSQL service / verify credentials

The real exception was ``FATAL: password authentication failed for user
"postgres"`` — a credential rotation, not a dead server. The suggestion
("start the service") was actively wrong, and the actionable fact was in a
``try/except: return False`` three frames down. This test pins:

1. a driver whose ``ping()`` raises reports that exception in ``error``;
2. a driver whose ``ping()`` returns False after recording the failure on the
   shared ``last_failure`` attribute reports THAT failure;
3. the never-empty contract still holds (a False ping with no recorded reason
   keeps the old fallback text so the field is never blank);
4. a connection that succeeds never carries an error string.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver
from nexus_scalp.database.health import DatabaseHealthService


class _AuthFailsDriver(SQLiteDriver):
    """A driver whose connection ALWAYS fails, mimicking a rejected credential.

    ``ping()`` raises (the psycopg v3 shape under a bad password is an
    ``OperationalError``); ``scalar`` raises too, so every read path fails the
    same way a real dead-auth PG server fails.
    """

    _BOOM = RuntimeError('FATAL: password authentication failed for user "postgres"')

    def connect(self, *args: Any, **kwargs: Any) -> Any:
        raise self._BOOM

    def scalar(self, *args: Any, **kwargs: Any) -> Any:
        raise self._BOOM


class _SilentFailDriver(_AuthFailsDriver):
    """``ping()`` returns False after recording the failure on the base class.

    This inherits the REAL ``SQLiteDriver.ping`` (the base implementation the
    HEALTH-DBREASON fix lives in) — it must not override it, or the test would
    measure its own stub instead of the production code path.
    """


class _OpaqueFailDriver(_AuthFailsDriver):
    """The legacy driver shape: ``ping`` swallows the cause and records nothing."""

    def ping(self, conn: Any = None) -> bool:
        return False


def _service_with_driver(monkeypatch: Any, driver: SQLiteDriver) -> DatabaseHealthService:
    """A health service whose audit domain resolves to ``driver``.

    One service per driver: the monkeypatch binds ``get_driver`` to a closure
    over a single instance, so a driver must never be shared between two
    services (the second probe would read the first driver's recorded failure
    and the two services would stop being distinguishable).
    """
    svc = DatabaseHealthService()

    def _fake_get_driver(cfg: DatabaseConfig) -> SQLiteDriver:
        return driver

    monkeypatch.setattr("nexus_scalp.database.health.get_driver", _fake_get_driver)
    return svc


def test_ping_failure_reports_the_driver_exception(monkeypatch: Any) -> None:
    """A raising ping surfaces its exception text, not a fixed string."""
    driver = _AuthFailsDriver(DatabaseConfig.for_sqlite("audit"))
    svc = _service_with_driver(monkeypatch, driver)

    snap = svc.check_domain("audit")

    assert snap["status"] == "DISCONNECTED"
    assert snap["connected"] is False
    assert snap["health"] == "Error"
    # The operator-actionable fact: an AUTH failure, not a bare "connection failed".
    assert "password authentication failed" in snap["error"]
    assert "RuntimeError" in snap["error"]
    # And never the opaque legacy text.
    assert snap["error"] != "connection failed"


def test_ping_false_reports_the_recorded_failure(monkeypatch: Any) -> None:
    """ping()->False records its exception; the probe reads THAT reason.

    This is the PostgreSQL production shape: the boolean contract is preserved
    for every other caller while the health layer additionally gets the cause.
    """
    driver = _SilentFailDriver(DatabaseConfig.for_sqlite("audit"))
    svc = _service_with_driver(monkeypatch, driver)

    # ping must stay boolean (no raise) for ordinary callers.
    assert driver.ping() is False
    assert isinstance(driver.last_failure, RuntimeError)
    assert "password authentication failed" in str(driver.last_failure)

    snap = svc.check_domain("audit")

    assert snap["status"] == "DISCONNECTED"
    assert snap["connected"] is False
    assert "password authentication failed" in snap["error"]
    assert snap["error"] != "connection failed"


def test_ping_false_without_any_recorded_reason_keeps_the_fallback(monkeypatch: Any) -> None:
    """The error field is never blank, even for a legacy driver.

    A driver that swallows its failure and records nothing must still yield a
    non-empty reason — a health surface that shows an empty error reads as
    "unknown" rather than "unhealthy", which is worse than the old text.
    """

    class _OpaqueDriver(_OpaqueFailDriver):
        pass

    svc = _service_with_driver(monkeypatch, _OpaqueDriver(DatabaseConfig.for_sqlite("audit")))

    snap = svc.check_domain("audit")

    assert snap["status"] == "DISCONNECTED"
    assert snap["error"] == "connection failed"


def test_a_healthy_connection_carries_no_error(monkeypatch: Any, tmp_path: Any) -> None:
    """The happy path must not inherit a stale reason from an earlier failure."""
    driver = SQLiteDriver(DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "ok.db")))
    driver.execute("CREATE TABLE audit_ledger (id INTEGER PRIMARY KEY, ticket INTEGER)")
    svc = _service_with_driver(monkeypatch, driver)

    snap = svc.check_domain("audit")

    assert snap["status"] == "CONNECTED"
    assert snap["connected"] is True
    # The successful probe drops the error key (the contract at health.py ~129).
    assert "error" not in snap
    assert snap["latency_ms"] is not None


def test_auth_and_refused_connections_are_distinguishable(monkeypatch: Any) -> None:
    """Two different causes must not collapse to the same string.

    This is the regression's actual cost: an operator with a bad password and
    an operator with a stopped server previously saw byte-identical health
    rows and were told to "start the service" in both cases. Two DISTINCT
    driver instances are used: ``get_driver`` is monkeypatched per service, so
    a shared driver would leak one failure reason into both probes.
    """

    class _AuthDriver(_AuthFailsDriver):
        pass

    class _RefusedDriver(_AuthFailsDriver):
        _BOOM = ConnectionRefusedError("Connection refused: localhost:5432")

    auth = _service_with_driver(monkeypatch, _AuthDriver(DatabaseConfig.for_sqlite("audit")))
    auth_snap = auth.check_domain("audit")

    refused = _service_with_driver(monkeypatch, _RefusedDriver(DatabaseConfig.for_sqlite("audit")))
    refused_snap = refused.check_domain("audit")

    assert "password authentication failed" in auth_snap["error"]
    assert "Connection refused" in refused_snap["error"]
    assert auth_snap["error"] != refused_snap["error"]
    # Both still report the same top-level state — only the REASON differs.
    assert auth_snap["status"] == refused_snap["status"] == "DISCONNECTED"
