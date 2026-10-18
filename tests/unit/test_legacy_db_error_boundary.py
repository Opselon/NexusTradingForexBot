"""Legacy-route DB error boundary contract tests (TASK-API-DB-ERROR-BOUNDARY).

Exercises ``nexus_scalp.web.legacy_errors`` as an INSTALLED app boundary:
known infrastructure failures on legacy routes -> HTTP 503 DEGRADED with the
structured payload; unknown defects still raise (opaque 500 preserved).

Why TestClient and not plain handler calls: the contract is about what the
ASGI surface returns when the exception propagates OUT of the router
(``starlette._exception_handler.wrap_app_handling_exceptions``), which is the
layer a bare handler call skips entirely. ``raise_server_exceptions=True``
(default) is what makes "still raises" an assertable fact rather than a
generic 500 body.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from nexus_scalp.web.legacy_errors import (
    DEGRADED_HTTP_STATUS,
    DatabaseState,
    classify_db_infrastructure_failure,
    register_web_error_boundary,
)

_AUTH_MESSAGE = (
    'connection to server at "127.0.0.1", port 5432 failed: FATAL: password '
    'authentication failed for user "postgres"'
)


def _app(exc: BaseException | None = None, *, path: str = "/api/ai-providers") -> FastAPI:
    """Minimal app: ONE route that raises ``exc`` (or 200s when None) + boundary.

    The exception is captured in the route closure on purpose: FastAPI binds
    ``route.endpoint`` into ``route.app`` at registration time, so mutating
    ``endpoint`` afterwards would never reach the ASGI layer under test.
    """
    app = FastAPI()

    @app.get(path)
    def legacy_route() -> dict:  # type: ignore[type-arg]
        if exc is not None:
            raise exc
        return {"status": "OK"}

    register_web_error_boundary(app)
    return app


def _client(app: FastAPI) -> TestClient:
    """``raise_server_exceptions=False`` — the response-observing contract.

    Starlette's ``ServerErrorMiddleware`` sends the Exception handler's
    response AND unconditionally re-raises afterwards (by design: "allows
    test clients to optionally raise the error"). A real ASGI server never
    re-raises, so this is the client shape that observes what a browser gets.
    """

    return TestClient(app, raise_server_exceptions=False)


def test_boundary_serves_degraded_503_on_auth_failure() -> None:
    app = _app(psycopg.OperationalError(_AUTH_MESSAGE))

    with _client(app) as client:
        resp = client.get("/api/ai-providers")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    body = resp.json()
    # -- structured degradation contract --
    assert body["status"] == "DEGRADED"
    assert body["database_state"] == DatabaseState.AUTH_FAILED.value
    assert body["provider_state"] == "unavailable"
    # -- legacy envelope preserved (Web/api_client.js branches on these) --
    assert body["available"] is False
    assert body["success"] is False
    assert body["error"]["code"] == "DB_INFRASTRUCTURE"
    assert body["error"]["message"]
    assert body["error"]["request_id"]
    # -- safe diagnostics: the six reachable fields, NO secret --
    diag = body["safe_diagnostics"]
    assert set(diag) == {
        "provider",
        "host",
        "port",
        "database",
        "username",
        "password_configured",
    }
    assert diag["host"] == "127.0.0.1"
    assert diag["port"] == 5432
    assert diag["username"] == "postgres"
    # password_configured is a BOOLEAN only — the credential never leaves (law 3).
    assert isinstance(diag["password_configured"], bool)
    assert "password_configured_value" not in diag
    # The surfaced detail names the CONDITION, never the credential.
    assert "password authentication failed" in body["detail"]
    for secret_like in ("hunter2", "supersecret", "password=hunter2"):
        assert secret_like not in body["detail"]


def test_unreachable_maps_to_503() -> None:
    app = _app(psycopg.OperationalError("connection refused"))

    with _client(app) as client:
        resp = client.get("/api/ai-providers")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.json()["database_state"] == DatabaseState.UNREACHABLE.value


def test_missing_database_maps_to_503() -> None:
    app = _app(psycopg.OperationalError('database "nexusdb" does not exist'))

    with _client(app) as client:
        resp = client.get("/api/ai-providers")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.json()["database_state"] == DatabaseState.DATABASE_NOT_FOUND.value


def test_permission_denied_maps_to_503() -> None:
    app = _app(psycopg.OperationalError("permission denied for table ai_provider_config"))

    with _client(app) as client:
        resp = client.get("/api/ai-providers")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.json()["database_state"] == DatabaseState.PERMISSION_DENIED.value


def test_server_answered_sqlstate_classifies() -> None:
    """``exc.diag.sqlstate`` is authoritative when the server actually answered.

    psycopg3 leaves ``sqlstate`` None on connection failures (verified against
    psycopg 3.2.x/3.3.x) but sets it once the provider spoke — so a wrong
    password the SERVER rejected still classifies as AUTH_FAILED.
    """
    exc = _SqlstateError("28P01", "password authentication failed")
    app = _app(exc)

    with _client(app) as client:
        resp = client.get("/api/ai-providers")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.json()["database_state"] == DatabaseState.AUTH_FAILED.value


def test_unknown_defect_still_raises() -> None:
    """Contract law 8: an unclassified defect is NEVER a success-shaped body.

    ``raise_server_exceptions=True`` (default) propagates the real exception,
    so this is the regression guard for the boundary collapsing into a
    catch-all that turns bugs into 503 maintenance windows.
    """
    app = _app(RuntimeError("genuinely a bug: NoneType has no attribute 'x'"))

    with TestClient(app, raise_server_exceptions=True) as client, pytest.raises(RuntimeError):
        client.get("/api/ai-providers")


def test_http_exception_on_legacy_path_is_not_a_degradation() -> None:
    """A route's deliberate 4xx must stay a 4xx — the boundary only owns infra."""
    app = _app()

    @app.get("/api/ai-providers/missing")
    def not_found_route() -> dict:  # type: ignore[type-arg]
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="provider not configured")

    register_web_error_boundary(app)

    with _client(app) as client:
        resp = client.get("/api/ai-providers/missing")

    assert resp.status_code == 404
    assert resp.json().get("status") != "DEGRADED"


def test_v1_path_defers_to_the_v1_handler() -> None:
    """The v1 tree keeps its OWN contract — the boundary neither owns nor drops it.

    This is the double-registration hazard: ``add_exception_handler`` keys by
    exception class, so installing a second ``Exception`` handler must CHAIN
    onto the v1 one rather than replace it (api_v1/errors.py is registered
    first in api_v1_wiring.register_api_v1).
    """
    app = FastAPI()

    @app.exception_handler(Exception)
    async def v1_handler(request: object, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "Internal server error."},
        )

    @app.get("/api/v1/incidents")
    def v1_route() -> dict:  # type: ignore[type-arg]
        raise RuntimeError("v1 defect")

    register_web_error_boundary(app)

    with _client(app) as client:
        resp = client.get("/api/v1/incidents")

    assert resp.status_code == 500
    assert resp.json()["code"] == "INTERNAL_ERROR"
    assert "DEGRADED" not in resp.text


def test_registration_is_idempotent() -> None:
    """Double registration must not stack or replace handlers.

    NOTE: this app is built by ``_app`` (boundary installed by the helper) so
    the extra ``register_web_error_boundary`` calls below exercise the guard,
    not a handler swap — which is why the response stays 200, not a 503.
    """
    app = _app()
    register_web_error_boundary(app)
    register_web_error_boundary(app)

    assert app.state.legacy_db_error_boundary is True
    with _client(app) as client:
        resp = client.get("/api/ai-providers")
    assert resp.status_code == 200
    assert resp.json()["status"] == "OK"


def test_dsn_password_is_masked_in_detail() -> None:
    """A DSN-shaped substring leaking inside a driver message gets masked."""
    app = _app(
        psycopg.OperationalError(
            "connection failed: postgresql://postgres:hunter2@127.0.0.1:5432/nexusdb"
        )
    )

    with _client(app) as client:
        resp = client.get("/api/ai-providers")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert "hunter2" not in resp.text


def test_retry_after_and_request_id_headers() -> None:
    app = _app(psycopg.OperationalError("connection refused"))

    with _client(app) as client:
        resp = client.get("/api/ai-providers")

    assert resp.status_code == DEGRADED_HTTP_STATUS
    assert resp.headers.get("Retry-After") == "10"
    assert resp.headers.get("X-Request-ID")


def test_never_an_empty_payload_on_db_failure() -> None:
    """Contract law 1: a known DB failure is never ``{}`` and never 200-OK."""
    app = _app(psycopg.OperationalError("connection refused"))

    with _client(app) as client:
        resp = client.get("/api/ai-providers")

    assert resp.status_code != 200
    body = resp.json()
    assert body  # never empty
    assert body["status"] == "DEGRADED"


class _SqlstateError(Exception):
    """Minimal stand-in for a psycopg error the SERVER answered.

    psycopg3 sets ``sqlstate`` only when the provider actually spoke (a failed
    handshake leaves it ``None`` — verified on psycopg 3.2.x/3.3.x), and
    ``Diagnostic.sqlstate`` is a read-only property, so a plain subclass with
    the attribute the classifier reads is the faithful stand-in. It deliberately
    is NOT a psycopg subclass: the classifier reads attributes, not isinstance.
    """

    def __init__(self, sqlstate: str, message: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.diag = type("Diagnostic", (), {"sqlstate": sqlstate})()


# ---------------------------------------------------------------------------
# Classifier unit tests (no ASGI): the classification table itself.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ('password authentication failed for user "postgres"', DatabaseState.AUTH_FAILED),
        ("Peer authentication failed for user postgres", DatabaseState.AUTH_FAILED),
        ("connection refused", DatabaseState.UNREACHABLE),
        ("connection timed out", DatabaseState.UNREACHABLE),
        ("getaddrinfo failed: name or service not known", DatabaseState.UNREACHABLE),
        ('database "nexusdb" does not exist', DatabaseState.DATABASE_NOT_FOUND),
        ("permission denied for table foo", DatabaseState.PERMISSION_DENIED),
        ("must have privilege SELECT on foo", DatabaseState.PERMISSION_DENIED),
        ("totally unrelated message", None),
    ],
)
def test_message_classification(message: str, expected: DatabaseState | None) -> None:
    assert classify_db_infrastructure_failure(RuntimeError(message)) == expected


def test_auth_beats_unreachable_specificity() -> None:
    """``AUTH_FAILED`` is probed before ``UNREACHABLE``: psycopg wraps a refused
    handshake in a message that also contains the generic connect marker."""
    exc = psycopg.OperationalError("connection refused: password authentication failed")
    assert classify_db_infrastructure_failure(exc) == DatabaseState.AUTH_FAILED


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [
        ("28000", DatabaseState.AUTH_FAILED),
        ("28P01", DatabaseState.AUTH_FAILED),
        ("3D000", DatabaseState.DATABASE_NOT_FOUND),
        ("42501", DatabaseState.PERMISSION_DENIED),
        ("57P03", DatabaseState.UNREACHABLE),
        ("53300", DatabaseState.UNREACHABLE),
    ],
)
def test_sqlstate_classification(sqlstate: str, expected: DatabaseState) -> None:
    exc = _SqlstateError(sqlstate, "server-answered failure")
    assert classify_db_infrastructure_failure(exc) == expected


def test_unclassified_exception_returns_none() -> None:
    """``None`` IS the contract — the caller must re-raise (never 200)."""
    assert classify_db_infrastructure_failure(ValueError("nope")) is None
