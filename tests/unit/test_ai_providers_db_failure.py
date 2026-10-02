"""AI-providers routes under DB failure (TASK-API-DB-ERROR-BOUNDARY, integration).

Proves the boundary closes the reported incident: the legacy
``/api/ai-providers`` family used to re-raise a driver-level infrastructure
failure out of the router, reaching the ASGI server as an opaque HTTP 500
with no body the frontend can branch on (``web/api_v1/errors.py`` handlers are
path-guarded to ``/api/v1``).

Strategy: build a REAL app (``create_app``) with the routes mounted exactly as
the server does, then patch the registry driver's ``query`` to raise the
infrastructure classes psycopg raises. The exception now propagates through
the same ASGI layers a live request does and must be caught by the boundary.

``ProviderRegistryStore`` resolves to SQLite under these tests (the settings
target), which is the honest environment for a unit suite that must never
touch the user's real PostgreSQL. The ``OperationalError`` class is psycopg's
on both providers, so the classification is identical — this is the incident's
AUTH_FAILED shape, not a driver-specific one.

The target MUST be a ``Path``, not a ``str``: ``ProviderRegistryStore``
discriminates a SQLite path from a PostgreSQL DSN *by type*
(``isinstance(db_path, str)`` means DSN), mirroring
``resolve_registry_target``'s documented ``Path | str`` contract. A string
path here would be read as a DSN, build a live PostgreSQL driver, and the
driver-class patch below would never land — the suite would silently hit the
user's real database instead of the temp file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.web.ai_providers_routes import router as ai_providers_router
from nexus_scalp.web.legacy_errors import (
    DEGRADED_HTTP_STATUS,
    DatabaseState,
    register_web_error_boundary,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


pytestmark = pytest.mark.usefixtures("_isolated_ai_providers")


_AUTH_FAILURE = (
    'connection to server at "127.0.0.1", port 5432 failed: FATAL: password '
    'authentication failed for user "postgres"'
)


def _build_app() -> FastAPI:
    """App mirroring the server composition: routes, then the boundary."""
    app = FastAPI(title="Nexus Scalp Engine Control Center")
    app.include_router(ai_providers_router)
    register_web_error_boundary(app)
    return app


@pytest.fixture
def _isolated_ai_providers(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the registry at a temp target + reset the route singleton.

    ``route_index`` calls ``get_ai_provider_orchestrator()`` which caches a
    module-level singleton; without the reset a prior test's instance (and its
    driver) survives into the next test and the patch never lands.

    The target is resolved INSIDE ``ProviderRegistryStore.__init__`` via
    ``from ... import resolve_registry_target`` (a from-import binds the NAME
    at call time), so patching the module attribute is what reaches it.
    """
    from nexus_scalp.settings import paths

    monkeypatch.setattr(paths, "resolve_registry_target", lambda: tmp_path / "settings.db")

    from nexus_scalp.web import ai_providers_routes

    ai_providers_routes._reset_orchestrator()
    yield
    ai_providers_routes._reset_orchestrator()


@pytest.fixture
def app() -> FastAPI:
    return _build_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """``raise_server_exceptions=False``: a real ASGI server never re-raises.

    Starlette's ``ServerErrorMiddleware`` sends the Exception handler's
    response AND unconditionally re-raises afterwards (by design, so test
    clients can opt in). Observing the 503 the browser would actually get
    requires opting out.
    """
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _patch_registry_query(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    """Make every registry read raise the infrastructure failure.

    ``ProviderRegistryStore`` has no ``query`` of its own — it delegates to
    ``self._driver.query`` (the SQLite/PostgreSQL driver), so the patch target
    is the DRIVER class. The store is constructed INSIDE ``route_index`` when
    the orchestrator is first built, so the driver instance does not exist at
    import time; patching the class reaches it whenever it is built.
    """
    from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver

    def _boom(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise exc

    monkeypatch.setattr(SQLiteDriver, "query", _boom, raising=False)


# ---------------------------------------------------------------------------
# The incident: an infrastructure failure becomes a structured degradation.
# ---------------------------------------------------------------------------


def test_route_index_returns_structured_degradation_on_auth_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_registry_query(monkeypatch, psycopg.OperationalError(_AUTH_FAILURE))

    resp = client.get("/api/ai-providers/")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    body = resp.json()
    assert body["status"] == "DEGRADED"
    assert body["database_state"] == DatabaseState.AUTH_FAILED.value
    assert body["provider_state"] == "unavailable"
    assert body["error"]["code"] == "DB_INFRASTRUCTURE"
    assert body["safe_diagnostics"]["host"] == "127.0.0.1"
    assert body["safe_diagnostics"]["port"] == 5432
    assert body["safe_diagnostics"]["username"] == "postgres"
    # Contract law 1: never {} and never 200-OK on a DB failure.
    assert body
    assert resp.status_code != 200


def test_route_index_returns_503_when_server_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_registry_query(monkeypatch, psycopg.OperationalError("connection refused"))

    resp = client.get("/api/ai-providers/")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.json()["database_state"] == DatabaseState.UNREACHABLE.value


def test_route_index_returns_503_when_database_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_registry_query(
        monkeypatch, psycopg.OperationalError('database "nexusdb" does not exist')
    )

    resp = client.get("/api/ai-providers/")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.json()["database_state"] == DatabaseState.DATABASE_NOT_FOUND.value


def test_route_index_returns_503_on_permission_denied(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_registry_query(
        monkeypatch, psycopg.OperationalError("permission denied for table ai_provider_config")
    )

    resp = client.get("/api/ai-providers/")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.json()["database_state"] == DatabaseState.PERMISSION_DENIED.value


def test_route_providers_list_degrades_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider TABLE route (Section 19) hits the same datastore."""
    _patch_registry_query(monkeypatch, psycopg.OperationalError(_AUTH_FAILURE))

    resp = client.get("/api/ai-providers/providers")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.json()["status"] == "DEGRADED"


def test_route_activation_degrades_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_registry_query(monkeypatch, psycopg.OperationalError(_AUTH_FAILURE))

    resp = client.get("/api/ai-providers/activation")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.json()["status"] == "DEGRADED"


def test_export_degrades_not_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """``route_export`` reads the registry directly through ``list_configs``."""
    from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver

    def _boom(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise psycopg.OperationalError(_AUTH_FAILURE)

    monkeypatch.setattr(SQLiteDriver, "query", _boom, raising=False)

    resp = client.post("/api/ai-providers/export")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.json()["status"] == "DEGRADED"


# ---------------------------------------------------------------------------
# Contract laws 1, 3 and 8 — the boundary's hard edges.
# ---------------------------------------------------------------------------


def test_no_secret_leaks_into_the_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Law 3: a DSN-shaped substring in the driver message is masked, never raw."""
    _patch_registry_query(
        monkeypatch,
        psycopg.OperationalError(
            "connection failed: postgresql://postgres:hunter2@127.0.0.1:5432/nexusdb"
        ),
    )

    resp = client.get("/api/ai-providers/")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert "hunter2" not in resp.text
    assert "postgresql://postgres:hunter2" not in resp.text


def test_unknown_defect_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Law 8: a genuine bug must NOT become a success-shaped degradation.

    Uses ``raise_server_exceptions=True`` deliberately — that is the client
    shape under which a defect the boundary re-raised is OBSERVABLE as a
    raised exception rather than swallowed into a body.
    """
    _patch_registry_query(monkeypatch, RuntimeError("genuinely a bug: NoneType has no attribute"))

    app = _build_app()
    with TestClient(app, raise_server_exceptions=True) as client, pytest.raises(RuntimeError):
        client.get("/api/ai-providers/")


def test_routes_still_work_when_the_db_is_healthy(client: TestClient) -> None:
    """Regression guard: the boundary never intercepts the happy path.

    The registry resolves to a temp SQLite file; an empty box is a valid,
    HEALTHY state (no providers configured), so this asserts the boundary is
    NOT swallowing normal operation — a boundary that returns 503 for every
    request would pass every failure test above.
    """
    resp = client.get("/api/ai-providers/")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "OK"
    assert "active_provider" in body  # a real payload, not a degradation


def test_degraded_payload_carries_request_id_and_retry_after(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_registry_query(monkeypatch, psycopg.OperationalError("connection refused"))

    resp = client.get("/api/ai-providers/")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.headers.get("Retry-After") == "10"
    rid = resp.headers.get("X-Request-ID")
    assert rid
    # The request_id travels in BOTH places and they must agree (the frontend
    # echoes it back for correlation), but a fresh request generates a fresh
    # id — so read both off THIS response rather than assuming a fixed value.
    assert resp.json()["error"]["request_id"] == rid
