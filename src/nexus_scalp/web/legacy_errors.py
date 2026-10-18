"""Legacy web API error boundary: structured degradations instead of opaque 500s.

WHERE/WHY: ``web/api_v1/errors.py`` registers exception handlers that are
deliberately SCOPED TO ``/api/v1`` — on any other path the handlers either
re-raise (the generic ``Exception`` branch) or re-issue the legacy response
shape. So the 257-route legacy dashboard surface (``/api/...``, e.g.
``/api/ai-providers``) has NO application-level boundary at all: a driver-level
infrastructure failure (``psycopg.OperationalError`` from FATAL password
authentication, a refused socket, a missing database) propagates out of the
router, past Starlette's exception middleware, and reaches the ASGI server as
an opaque HTTP 500 with no body the frontend can branch on (an ExceptionGroup
under a real server, ``TestClient`` propagating the exception under tests).

This module is the deliberate inverse policy for the legacy path:

* ``/api/v1`` — v1 handlers own the surface, this one stays out of the way;
* known DB infrastructure failures  -> HTTP 503 with a machine-readable
  DEGRADED envelope (the frontend can distinguish "dead database" from
  "engine is not attached" from "genuinely crashed" and degrade honestly);
* everything else is re-raised UNCHANGED — an unknown defect must never be
  converted into a success-shaped response (the boundary is strict; an
  unclassified exception keeps today's opaque 500 so the operator knows it is
  a bug, not a maintenance window).

Contract (the frontend branches on these stable fields):
``HTTP 503`` + ``{status: "DEGRADED", database_state: ..., provider_state:
"unavailable", detail: <safe driver reason>, safe_diagnostics: {provider,
host, port, database, username, password_configured}}``. The legacy envelope
shape (``available`` / ``success`` / ``error.{code,message,request_id}``) is
preserved so ``Web/api_client.js`` keeps working unchanged.

REDUCTION: the surfaced ``detail`` is the driver's own connection reason,
masked through :func:`nexus_scalp.database.config.mask_url_password` (same
discipline as ``nexus_scalp.web.api_v1.incidents`` and ``release/health.py``'s
``_connection_reason``). The driver message contains no secret material — it
names host/port/user and the REJECTED state, never the credential. Passwords
are NEVER echoed; ``password_configured`` is a boolean only.

USED BY: ``web/api_v1_wiring.py`` (``register_web_error_boundary`` is installed
next to ``register_v1_exception_handlers``) — one composition point, one
boundary, no route-level try/except sprinkles.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

#: Boundary scope. The v1 tree owns its own error contract (api_v1/errors.py),
#: which is path-guarded and re-raises on any non-v1 path; this boundary is the
#: complement for the REST of the control surface. The platform tree
#: (``/api/v1``) is explicitly excluded so the two contracts never overlap.
_API_V1_PREFIX = "/api/v1"

#: HTTP status for a DEGRADED response. 503 (Service Unavailable) rather than
#: 500: the service is up, the datastore is not — a retryable, operator-visible
#: condition, not a server defect.
DEGRADED_HTTP_STATUS = 503


class DatabaseState(StrEnum):
    """Stable vocabulary for WHAT went wrong at the datastore (contract field)."""

    #: Credentials rejected by the provider (FATAL password authentication,
    #: missing password, peer/trust auth refused).
    AUTH_FAILED = "AUTH_FAILED"
    #: The server could not be reached at all (socket refused, DNS failure,
    #: connect timeout, pool exhausted / pool closed).
    UNREACHABLE = "UNREACHABLE"
    #: The provider answered but the database itself does not exist.
    DATABASE_NOT_FOUND = "DATABASE_NOT_FOUND"
    #: The server was reached and authenticated but the role has no access to
    #: the requested object (permission denied).
    PERMISSION_DENIED = "PERMISSION_DENIED"

    #: Catch-all for a classified-but-unmapped condition. Kept distinct from the
    #: re-raise path: a state we KNOW is infrastructure but cannot name yet is
    #: still a DEGRADED response, just with the least-specific state.
    UNKNOWN = "UNKNOWN"


#: Stable application error code surfaced in the legacy ``error.code`` field
#: (``Web/api_client.js`` reads it to pick the operator-facing banner).
DB_INFRASTRUCTURE_ERROR_CODE = "DB_INFRASTRUCTURE"

#: Human-facing message. Names the CONDITION and the RESOURCE, never the cause
#: internals: the real traceback goes to the log only (same discipline as
#: ``nexus_scalp.web.errors.log_web_error``).
_MESSAGES: dict[DatabaseState, str] = {
    DatabaseState.AUTH_FAILED: "The database rejected the configured credentials.",
    DatabaseState.UNREACHABLE: "The database server could not be reached.",
    DatabaseState.DATABASE_NOT_FOUND: "The configured database does not exist.",
    DatabaseState.PERMISSION_DENIED: "The database account lacks permission for this data.",
    DatabaseState.UNKNOWN: "The database is unavailable.",
}


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

#: Case-insensitive substring probes against the driver's own connection reason
#: (psycopg3 does NOT populate ``exc.diag.sqlstate`` on connection failures —
#: verified against psycopg 3.2.13/3.3.6: only the *server-answered* subclasses
#: like ``CannotConnectNow`` carry 57P03. A failed handshake has no SQLSTATE to
#: read, so the message text is the only reliable signal, exactly like
#: ``release/health.py._connection_reason`` does today).
_AUTH_REFUSAL_MARKERS: tuple[str, ...] = (
    "password authentication failed",
    "no password supplied",
    "peer authentication failed",
    "authentication failed",
    "invalid password",
)

_UNREACHABLE_MARKERS: tuple[str, ...] = (
    "connection refused",
    "could not connect to server",
    "connection timed out",
    "timeout expired",
    "getaddrinfo",
    "name or service not known",
    "network is unreachable",
    "server closed the connection unexpectedly",
    "too many clients already",
    "remaining connection slots are reserved",
)

#: ``DATABASE_NOT_FOUND`` / ``PERMISSION_DENIED``: reached-and-answered
#: conditions. Matched by the provider's literal error shape (``database
#: "nexusdb" does not exist``), so the marker is the whole phrase —
#: ``'database "'`` alone would fire on every driver message that mentions a
#: database by name. Both are tested AFTER the auth/unreachable probes.
_SPECIFIC_ANSWERED_MARKERS: dict[DatabaseState, tuple[str, ...]] = {
    DatabaseState.DATABASE_NOT_FOUND: ('database "nexusdb" does not exist', "does not exist"),
    DatabaseState.PERMISSION_DENIED: ("permission denied", "must have privilege"),
}

#: Server-answered SQLSTATEs from a provider that was REACHED but refused the
#: operation. psycopg3 only sets ``sqlstate`` when the server actually spoke.
_SERVER_SQLSTATES: dict[str, DatabaseState] = {
    "28000": DatabaseState.AUTH_FAILED,  # invalid_authorization_specification
    "28P01": DatabaseState.AUTH_FAILED,  # invalid_password
    "3D000": DatabaseState.DATABASE_NOT_FOUND,  # invalid_catalog_name
    "42501": DatabaseState.PERMISSION_DENIED,  # insufficient_privilege
    "57P03": DatabaseState.UNREACHABLE,  # cannot_connect_now
    "53300": DatabaseState.UNREACHABLE,  # too_many_connections
    "57P01": DatabaseState.UNREACHABLE,  # admin_shutdown
    "57P02": DatabaseState.UNREACHABLE,  # crash_shutdown
}

#: Exact type-name matches for the classes psycopg3 raises when the failure
#: happens BEFORE the server could answer (no SQLSTATE available). Matched by
#: name (not isinstance) because psycopg is an OPTIONAL dependency — the module
#: must classify without importing it, and a missing psycopg means the driver
#: surfaces a plain ``RuntimeError``/``ImportError`` anyway.
#:
#: NOTE: ``OperationalError`` is deliberately NOT an AUTH marker here. It is the
#: base class of the UNREACHABLE subclasses below (CannotConnectNow,
#: ConnectionFailure, ...) and carries no refusal signal of its own — listing
#: it would classify every refused socket as AUTH_FAILED. A bare
#: ``OperationalError`` is decided by its MESSAGE (``_classify_by_message``),
#: and the only ``OperationalError`` subclass psycopg raises specifically for a
#: rejected credential is ``InvalidPassword``.
_TYPE_NAMES_AUTH = {"InvalidPassword"}
_TYPE_NAMES_UNREACHABLE = {
    "CannotConnectNow",
    "ConnectionFailure",
    "ConnectionRefused",
    "ConnectionDone",
    "ConnectionDoesNotExist",
    "TooManyConnections",
    "TooManyRequests",
    "PoolTimeout",
    "PoolClosed",
}


def _message(exc: BaseException) -> str:
    """Bounded driver reason (never empty, never raising)."""
    try:
        text = str(exc)
    except Exception:
        return ""
    return text[:2000] if text else ""


def _classify_by_message(text: str) -> DatabaseState | None:
    """Message-text probes, ordered MOST SPECIFIC first.

    psycopg's connection-failure message names the failing resource, e.g.
    ``connection to server at "127.0.0.1", port 5432 failed: FATAL: password
    authentication failed for user "postgres"``.

    Precedence is the whole point of this function: the AUTH refusal markers
    are tested BEFORE the UNREACHABLE markers because psycopg wraps BOTH in a
    message containing ``could not connect to server`` (it is the only signal
    a bare ``OperationalError`` for a refused socket carries), and a socket
    that never answered is NOT an auth failure. A wrong credential, by
    contrast, is ALWAYS phrased as a refusal the server reports. So:
    refusal verb present -> AUTH_FAILED; else the connect marker -> UNREACHABLE.
    """
    lowered = text.lower()
    if any(marker in lowered for marker in _AUTH_REFUSAL_MARKERS):
        return DatabaseState.AUTH_FAILED
    if any(marker in lowered for marker in _UNREACHABLE_MARKERS):
        return DatabaseState.UNREACHABLE
    for state, markers in _SPECIFIC_ANSWERED_MARKERS.items():
        for marker in markers:
            if marker in lowered:
                return state
    return None


def _classify_by_type(exc: BaseException) -> DatabaseState | None:
    """Type-name classification for connection classes without a SQLSTATE.

    Reached LAST (after the message probes): psycopg's base ``OperationalError``
    is the PARENT of the UNREACHABLE subclasses, so a bare one has no signal of
    its own and is only ever seen here when the message had nothing to say —
    in which case a connection failure is the honest, least-specific answer.
    """
    name = type(exc).__name__
    if name in _TYPE_NAMES_UNREACHABLE:
        return DatabaseState.UNREACHABLE
    if name in _TYPE_NAMES_AUTH:
        return DatabaseState.AUTH_FAILED
    # ``InterfaceError`` (dead handle), pool and timeout wrappers: unreachable.
    if name in {"InterfaceError", "PoolError", "ConnectionTimeoutError", "TimeoutError"}:
        return DatabaseState.UNREACHABLE
    # psycopg's base connection-failure class: still infrastructure. Without
    # this, a bare ``OperationalError`` with no message markers would fall
    # through to None and keep raising the opaque 500 the boundary exists to
    # replace. Routed to UNREACHABLE (least-specific connection failure), not
    # AUTH_FAILED — a socket that never answered is not a credential refusal.
    if name == "OperationalError":
        return DatabaseState.UNREACHABLE
    return None


def classify_db_infrastructure_failure(exc: BaseException) -> DatabaseState | None:
    """Classify a KNOWN infrastructure failure, or return None for anything else.

    psycopg3 populates ``sqlstate`` ONLY when the server answered with a
    SQLSTATE (verified: connection failures leave it ``None``), so the server-
    answered states are read first and the message/type signals second.

    Precedence (most specific first):

    1. ``sqlstate`` — the provider answered; unambiguous.
    2. MESSAGE markers — a refusal verb (``password authentication failed``)
       distinguishes AUTH_FAILED from UNREACHABLE inside psycopg's shared
       ``could not connect to server`` wrapper, and the literal
       ``database "x" does not exist`` / ``permission denied`` shapes name
       the reached-and-answered conditions. Probed BEFORE the type name
       because psycopg's base ``OperationalError`` is the parent of the
       UNREACHABLE subclasses too — the message is what separates them.
    3. TYPE NAME — last resort. A bare ``OperationalError`` (psycopg's base
       connection-failure class) carries no message markers of its own, so
       without this fallback it would fall through to None and a real
       infrastructure failure would keep raising the opaque 500 this boundary
       exists to replace. The message path still wins when it has an opinion.

    Returning ``None`` is the whole contract: it means "not a known
    infrastructure failure" and the caller must re-raise so the defect stays a
    real 500 instead of a success-shaped degradation.
    """
    # 1. Server-answered SQLSTATE: most reliable signal (the provider was reached).
    try:
        sqlstate = getattr(exc, "sqlstate", None) or getattr(
            getattr(exc, "diag", None), "sqlstate", None
        )
    except Exception:
        sqlstate = None
    if isinstance(sqlstate, str) and sqlstate.upper() in _SERVER_SQLSTATES:
        return _SERVER_SQLSTATES[sqlstate.upper()]

    # 2. MESSAGE markers: separate AUTH_FAILED from UNREACHABLE, and name the
    #    reached-and-answered conditions (DATABASE_NOT_FOUND / PERMISSION_DENIED).
    text = _message(exc)
    by_message = _classify_by_message(text)
    if by_message is not None:
        return by_message

    # 3. TYPE NAME: the base ``OperationalError`` carries no message of its own
    #    but is still unambiguously infrastructure (a connection failure).
    return _classify_by_type(exc)


def is_db_infrastructure_failure(exc: BaseException) -> bool:
    """``True`` only for a classified (known) infrastructure failure."""
    return classify_db_infrastructure_failure(exc) is not None


# ---------------------------------------------------------------------------
# Safe diagnostics — no secret ever leaves this process
# ---------------------------------------------------------------------------


def _mask_reason(text: str) -> str:
    """Reuses the codebase's ONE masking discipline on the driver reason.

    A DSN-shaped substring (``postgresql://user:pass@host``) embedded in a
    driver message is masked in place; the connection reason itself never
    carries the credential, but the guard is structural, not assumed.
    """
    try:
        from nexus_scalp.database.config import mask_url_password
    except ImportError:  # pragma: no cover - config always importable in-tree
        return text
    try:
        return mask_url_password(text)
    except Exception:
        return text


def _bool_or_none(value: Any) -> bool | None:
    """Boolean-or-None: never a truthy-but-not-bool value on the contract."""
    if isinstance(value, bool):
        return value
    return None


def safe_diagnostics(exc: BaseException) -> dict[str, Any]:
    """Per-field diagnostics for the frontend, with no credential material.

    Source: the driver's connection reason (server/host/port/database/user are
    the fields the operator ALREADY configured and already sees in the control
    center's DB panel — surfacing them is what makes the 503 actionable). The
    password is represented ONLY as ``password_configured: bool``.
    """
    reason = _mask_reason(_message(exc))
    out: dict[str, Any] = {
        "provider": None,
        "host": None,
        "port": None,
        "database": None,
        "username": None,
        "password_configured": None,
    }

    # psycopg3's connection-failure message names the endpoint, e.g.
    # ``connection to server at "127.0.0.1", port 5432 failed: ...``.
    lower = reason.lower()
    host_start = lower.find('at "')
    if host_start != -1:
        host_start += 4
        host_end = reason.find('"', host_start)
        if host_end != -1:
            out["host"] = reason[host_start:host_end] or None
            port_idx = lower.find("port ", host_end)
            if port_idx != -1:
                port_end = port_idx + 5
                digits = ""
                while port_end < len(reason) and reason[port_end].isdigit():
                    digits += reason[port_end]
                    port_end += 1
                out["port"] = int(digits) if digits else None
    if out["host"] is None:
        for marker in ("host=", "server at "):
            idx = lower.find(marker)
            if idx != -1:
                rest = reason[idx + len(marker) :].strip().strip('"')
                out["host"] = rest.split(":")[0].split(" ")[0] or None
                if out["host"]:
                    break

    for user_marker in ('for user "', "user="):
        idx = lower.find(user_marker)
        if idx != -1:
            start = idx + len(user_marker)
            end = reason.find('"', start) if user_marker.startswith("for") else len(reason)
            candidate = reason[start:end].strip() if end != -1 else reason[start:].strip()
            candidate = candidate.split(" ")[0] or None
            if candidate:
                out["username"] = candidate
                break

    for db_marker in ('database "', "database="):
        idx = lower.find(db_marker)
        if idx != -1:
            start = idx + len(db_marker)
            end = reason.find('"', start) if db_marker.startswith("database") else len(reason)
            candidate = reason[start:end].strip() if end != -1 else reason[start:].strip()
            candidate = candidate.split(" ")[0] or None
            if candidate:
                out["database"] = candidate
                break

    return out


def enrich_diagnostics(
    diagnostics: dict[str, Any], *, config: Any = None, password_configured: Any = None
) -> dict[str, Any]:
    """Fills the provider/password_configured fields from the live config.

    Import-safe: ``load_database_config`` is the authoritative resolver already
    used by ``web/server._default_audit_config`` (DATABASE PORTABILITY), and
    ``resolve_password`` is the SecretStore seam the live engine uses at
    connect time — reading it here only answers "is a credential stored", never
    the credential itself. Every failure path yields ``None`` rather than
    raising: a diagnostics helper must never be the thing that breaks the error
    boundary itself.
    """
    out = dict(diagnostics)

    cfg = config
    if cfg is None:
        try:
            from nexus_scalp.database.config import load_database_config

            cfg = load_database_config("audit")
        except Exception:
            cfg = None

    if cfg is not None:
        try:
            out["provider"] = str(getattr(cfg, "provider", "") or "postgresql") or None
        except Exception:
            out["provider"] = out.get("provider")
        if not out.get("host"):
            out["host"] = getattr(cfg, "host", None) or None
        if not out.get("port"):
            port = getattr(cfg, "port", None)
            out["port"] = int(port) if port else None
        if not out.get("database"):
            out["database"] = getattr(cfg, "database", None) or None
        if not out.get("username"):
            pg = getattr(cfg, "postgresql_config", None)
            out["username"] = getattr(pg, "user", None) or None

    if isinstance(password_configured, bool):
        out["password_configured"] = password_configured
    elif out.get("password_configured") is None:
        try:
            from nexus_scalp.database.config import resolve_password

            out["password_configured"] = bool(cfg is not None and resolve_password(cfg))
        except Exception:
            # No credential stored (resolve_password raises) or the store is
            # unavailable: report the honest "not configured", never the value.
            out["password_configured"] = bool(
                cfg is not None and getattr(cfg, "password_secret", "")
            )
    return out


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def degraded_payload(
    request: Request | None,
    state: DatabaseState,
    *,
    reason: str = "",
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds the DEGRADED contract body (legacy envelope + structured fields).

    The legacy ``available`` / ``success`` / ``error`` fields are preserved so
    existing clients keep their behavior; the frontend additionally branches on
    ``status`` / ``database_state`` to render a database-specific banner.
    """
    from nexus_scalp.web.api_v1.common import _request_id

    rid = _request_id(request)
    safe_reason = _mask_reason(reason) if reason else ""
    body: dict[str, Any] = {
        # -- legacy contract fields (Web/api_client.js) --
        "available": False,
        "success": False,
        "error": {
            "code": DB_INFRASTRUCTURE_ERROR_CODE,
            "message": _MESSAGES.get(state, _MESSAGES[DatabaseState.UNKNOWN]),
            "request_id": rid,
        },
        # -- structured degradation contract --
        "status": "DEGRADED",
        "database_state": state.value,
        "provider_state": "unavailable",
        "detail": safe_reason,
        "safe_diagnostics": diagnostics or {},
    }
    return body


def degraded_response(
    request: Request | None,
    exc: BaseException,
    state: DatabaseState | None = None,
) -> JSONResponse:
    """503 JSONResponse for a classified infrastructure failure.

    Logs the FULL exception (traceback, exception type) through the hardened
    web-error logger first — the operator keeps the real cause in the log, the
    browser only gets the safe contract.
    """
    resolved = (
        state
        if state is not None
        else (classify_db_infrastructure_failure(exc) or DatabaseState.UNKNOWN)
    )
    reason = _message(exc)
    diagnostics = enrich_diagnostics(safe_diagnostics(exc))

    from nexus_scalp.observability.logging import get_logger
    from nexus_scalp.web.errors import log_web_error

    # ONE request_id for the whole response. ``_request_id`` falls back to
    # ``new_request_id()`` (random) when no correlation middleware set one, so
    # ``degraded_payload`` must NOT be called twice — the header would carry a
    # different id than the body and the log, breaking frontend correlation.
    body = degraded_payload(request, resolved, reason=reason, diagnostics=diagnostics)
    rid = body["error"]["request_id"]
    endpoint = request.url.path if request is not None else "/api"
    log_web_error(
        get_logger("nexus_scalp.web.legacy_errors"),
        endpoint,
        rid,
        exc,
        resource=f"database:{resolved.value}",
        context={"database_state": resolved.value, "safe_diagnostics": diagnostics},
    )

    return JSONResponse(
        status_code=DEGRADED_HTTP_STATUS,
        content=body,
        headers={"Retry-After": "10", "X-Request-ID": rid},
    )


# ---------------------------------------------------------------------------
# Handler registration (the inverse policy of the v1 handlers)
# ---------------------------------------------------------------------------


def _is_boundary_path(path: str) -> bool:
    """``True`` for legacy routes the boundary owns (everything but /api/v1)."""
    return not path.startswith(_API_V1_PREFIX)


def register_web_error_boundary(app: FastAPI) -> None:
    """Install the legacy-route DB error boundary on ``app``.

    Registered alongside ``register_v1_exception_handlers`` (see
    ``api_v1_wiring.register_api_v1``) so BOTH contracts are installed at the
    single composition point and neither is forgotten:

    * ``/api/v1`` -> the v1 handlers own the response (this boundary re-raises
      so the v1 contract stays byte-exact);
    * ``/api/legacy-route...`` -> a KNOWN DB infrastructure failure becomes a
      structured 503 DEGRADED response;
    * anything else -> re-raised UNCHANGED (opaque 500, exactly today's
      behavior — unknown defects never become success-shaped responses).

    Ordering note: FastAPI routes exception handlers by the exception's MRO
    (``starlette._exception_handler._lookup_exception_handler``), and psycopg's
    connection failures all derive from ``psycopg.Error`` -> ``Exception``. The
    generic ``Exception`` handler registered here therefore only fires when NO
    more specific handler (v1's, or FastAPI's own) matched — a base-class
    handler cannot shadow a subclass one. The ``Exception`` handler is used
    (rather than a psycopg-specific base) because psycopg is an OPTIONAL
    dependency and the boundary must not import it.
    """
    if getattr(app.state, "legacy_db_error_boundary", False):
        return
    app.state.legacy_db_error_boundary = True

    # FastAPI/Starlette keys ``app.exception_handlers`` by exception CLASS
    # (``Starlette.add_exception_handler`` is a plain dict assignment), so a
    # second ``@app.exception_handler(Exception)`` would OVERWRITE the v1
    # handler. Composition order would otherwise be load-bearing and silent.
    # Chaining on top of the existing entry keeps the v1 contract byte-exact
    # and gives this boundary a single, well-defined place to sit.
    previous = app.exception_handlers.get(Exception)

    # NOTE: async, to match the v1 handlers this chains onto
    # (``api_v1/errors.py`` registers async handlers) — ``previous`` may be a
    # coroutine function and must be awaited on the pass-through path.

    @app.exception_handler(Exception)
    async def _legacy_db_infrastructure_handler(request: Request, exc: Exception) -> JSONResponse:
        # The v1 tree owns its own contract — never intercept it.
        if not _is_boundary_path(request.url.path):
            if previous is not None:
                return await previous(request, exc)  # type: ignore[misc, arg-type]
            raise exc

        state = classify_db_infrastructure_failure(exc)
        if state is None:
            # Unknown defect: preserve today's opaque failure. A maintenance
            # window must never be indistinguishable from a bug. NOTE: the v1
            # handler is deliberately NOT consulted on a legacy path — its
            # contract is scoped to /api/v1 and its envelope would be a lie
            # here (see web/api_v1/errors.py::_is_v1_path).
            raise exc
        return degraded_response(request, exc, state)
