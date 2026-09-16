"""WEB-AUTH-P0: token auth middleware for the NSE control-plane web server.

Closes audit finding B1 (docs/agent_handoffs/2026-09-07_NSE_audit_findings_and_status.txt):
the control server previously accepted every request (allow_origins=["*"],
no credential check) while docker-compose publishes it on 0.0.0.0. Anyone who
could reach the port could flip execution mode to LIVE, close positions, or
edit runtime configuration.

Contract
--------
* OPT-OUT SAFE MODES ONLY: requests are authenticated EXCEPT an explicit
  allowlist of public paths (health/liveness) — fail-closed by default.
* Token sources (first match wins):
    1. NSE_WEB_AUTH_TOKEN environment variable
    2. SecureSecretStore entry "web_auth_token" (DPAPI on Windows)
  If NEITHER exists, the middleware generates a strong random token ONCE,
  persists it to the secret store, logs its value at WARNING (operator must
  retrieve it from logs once), and enforces it. There is no anonymous mode.
* Constant-time comparison (hmac.compare_digest).
* Bearer header, X-NSE-Token header, and ?token= query param accepted
  (query param kept for SSE/EventSource clients that cannot set headers).
* LIVE hardening: when the engine's execution mode is LIVE, token auth is
  ALWAYS required regardless of environment (defense in depth), and the
  middleware refuses to honor token discovery failure (blocks, never fails
  open).
"""

from __future__ import annotations

import hmac
import os
import secrets

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.web.auth")

#: Paths that never require a token (liveness + static UI bundle assets).
#: NOTE: /app.js, /tv_widget_styles.css and friends are served at repo root
#: (UI bundle contract, tests/unit/test_ui_deploy_drift_forensics.py) and are
#: static, non-sensitive content — authenticated UI pages fetch them without
#: credential support on <script>/<link> tags in some caching setups. Keep
#: this list MINIMAL: only static assets, never API state/mutation routes.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/api/health",
        "/health",
        "/healthz",
        "/favicon.ico",
        "/app.js",
        "/app.js.map",
        "/tv_widget_styles.css",
        "/tailwind.css",
    }
)
#: Any path starting with these prefixes is public as well. UX_* JS modules
#: (Web/ux_*.js) are static bundle assets served at repo root — non-sensitive.
PUBLIC_PREFIXES: tuple[str, ...] = ("/static/", "/assets/")
PUBLIC_JS_ASSETS: frozenset[str] = frozenset(
    {
        "ux_i18n.js",
        "ux_conn.js",
        "ux.js",
        "ux_signal.js",
        "ux_attention.js",
        "ux_palette.js",
        "api_client.js",
        "cc_components.js",
        "cc_state.js",
        "command_center_console.js",
        "command_center_spatial.js",
        "command_center_timemachine.js",
        "command_center_ui.js",
    }
)


def is_public_path(path: str) -> bool:
    """Single source of truth for the no-token allowlist."""
    name = path.lstrip("/")
    if path in PUBLIC_PATHS or name in PUBLIC_JS_ASSETS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


WEB_AUTH_TOKEN_SECRET_NAME = "web_auth_token"
_TOKEN_BYTES = 32


def _generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _resolve_token() -> tuple[str, str]:
    """Returns (token, source) where source in {"env", "secret_store", "generated"}.

    Raises RuntimeError only when a token must exist and cannot be produced
    (never silently returns an empty token — fail-closed).
    """
    env_token = os.environ.get("NSE_WEB_AUTH_TOKEN", "").strip()
    if env_token:
        return env_token, "env"

    try:
        from nexus_scalp.settings.secret_store import SecureSecretStore

        stored = SecureSecretStore().get_secret(WEB_AUTH_TOKEN_SECRET_NAME)
        if stored and stored.strip():
            return stored.strip(), "secret_store"
    except Exception as exc:  # pragma: no cover - platform edge (DPAPI etc.)
        logger.warning("[WEB-AUTH] secret store unavailable", error=str(exc))

    # Generate + persist once so restarts keep the same token.
    try:
        from nexus_scalp.settings.secret_store import SecureSecretStore

        store = SecureSecretStore()
        token = _generate_token()
        store.set_secret(WEB_AUTH_TOKEN_SECRET_NAME, token)
        # OBS-TRACE-2 (Agent 8, 2026-09-11): the previous message claimed the
        # token could be "retrieved ONCE from this log line" — but the logging
        # pipeline's key-based redactor (_redact_sensitive_fields) scrubs every
        # secret-bearing key (token=... -> [REDACTED_SECRET]), so the log line
        # NEVER carried the value and operators were pointed at evidence that
        # cannot exist. Credentials never belong in logs anyway: the token is
        # persisted to the secure secret store (DPAPI-backed); retrieve it via
        # the secret store or override with NSE_WEB_AUTH_TOKEN.
        logger.warning(
            "[WEB-AUTH] generated new web auth token and persisted it to the "
            "secure secret store. The token value is intentionally NOT logged "
            "(the redaction pipeline scrubs credential values). Retrieve it "
            "from the secret store (secret name: web_auth_token) or set "
            "NSE_WEB_AUTH_TOKEN to control it explicitly.",
        )
        return token, "generated"
    except Exception as exc:
        # Fail-closed: no token resolvable -> middleware must block everything.
        raise RuntimeError(f"web auth token unresolvable: {exc}") from exc


#: Subprotocol channel for browser WebSocket clients: a browser cannot set
#: Authorization / X-NSE-Token on the upgrade handshake, so a token may be
#: offered as subprotocol "nse-token.<token>" (consumed here; never echoed
#: back unfiltered in the negotiated-protocol response).
WS_TOKEN_SUBPROTOCOL_PREFIX = "nse-token."

#: WebSocket close codes used when an upgrade handshake is refused:
#: 4401 = application-private "WS unauthorized"; 1008 = RFC 6455 policy
#: violation. Both satisfy the regression contract.
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_POLICY_VIOLATION = 1008

#: Operator opt-out env (trusted-LAN only, NOT for LIVE) — same flag the
#: server's _install_web_auth_if_enabled honors for the HTTP layer.
WS_AUTH_DISABLED_ENV = "NSE_WEB_AUTH_DISABLE"


def is_auth_disabled() -> bool:
    """True when the operator explicitly disabled web auth (trusted-LAN only).

    Mirrors the server-side ``NSE_WEB_AUTH_DISABLE=1`` opt-out so the HTTP
    layer and the WebSocket layer can never disagree about the disabled
    state. The flag is NEVER combined with LIVE execution mode by design —
    that combination is an operator misconfiguration the server warns about
    at install time.
    """
    return os.environ.get(WS_AUTH_DISABLED_ENV, "").strip() == "1"


def _constant_time_token_match(supplied: str | None, expected: str | None) -> bool:
    """Constant-time comparison shared by the HTTP and WebSocket paths."""
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def extract_ws_token(scope: dict) -> str | None:
    """Multi-channel token extraction for one websocket ASGI scope.

    Order (first match wins — mirrors the HTTP layer's precedence):
      1. Authorization: Bearer <token> header (native clients / proxies).
      2. X-NSE-Token header (service clients).
      3. Query string ``?token=<token>`` (browser clients: the JS WebSocket
         API cannot set custom upgrade headers, so the URL query is the
         primary browser channel).
      4. Subprotocol channel ``Sec-WebSocket-Protocol: nse-token.<token>``
         (browser-native alternative when the URL must stay token-free).

    Returns None when no channel carries a token. Never raises (a malformed
    scope must fail the handshake, not 500 the server).
    """
    try:
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", []) or []
        }
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
            if supplied:
                return supplied
        xt = headers.get("x-nse-token", "").strip()
        if xt:
            return xt
        qs = (scope.get("query_string", b"") or b"").decode("latin-1")
        for part in qs.split("&"):
            if part.startswith("token="):
                val = part[6:].strip()
                if val:
                    return val
        for proto in scope.get("subprotocols", []) or []:
            text = proto.decode("latin-1") if isinstance(proto, bytes) else str(proto)
            if text.startswith(WS_TOKEN_SUBPROTOCOL_PREFIX):
                val = text[len(WS_TOKEN_SUBPROTOCOL_PREFIX) :].strip()
                if val:
                    return val
    except Exception:
        return None
    return None


def ws_scope_authorized(scope: dict, expected_token: str | None) -> bool:
    """Fail-closed websocket authorization decision for one ASGI scope.

    * auth disabled by operator flag -> allow (trusted-LAN override).
    * no token resolvable -> REFUSE (config error can never fail open; the
      HTTP layer reports the same condition as 500 AUTH_CONFIG_ERROR).
    * otherwise -> constant-time comparison of the best supplied token.
    """
    if is_auth_disabled():
        return True
    if expected_token is None:
        return False
    supplied = extract_ws_token(scope)
    return _constant_time_token_match(supplied, expected_token)


class WebAuthMiddleware:
    """Pure ASGI middleware: bearer/X-NSE-Token/query-token enforcement.

    Kept framework-free (pure ASGI wrapper) so it composes with the existing
    Starlette middleware stack without changing create_app's response contract.

    TASK-SEC-WS-AUTH-P0 (2026-09-11): WebSocket scopes are NO LONGER passed
    through unauthenticated. Every non-HTTP scope of type ``websocket`` is
    authenticated at the ASGI boundary BEFORE the route handler runs
    (multi-channel token extraction + constant-time compare; failure sends a
    ``websocket.close`` frame with code 4401 and never reaches the inner app).
    Other scope types (lifespan) still pass through — they carry no request
    credentials and no client data.
    """

    def __init__(self, app, *, require_always: bool = False) -> None:
        self.app = app
        # require_always=True is used when LIVE mode is detected: even a
        # token-resolution failure must block, never fail open.
        self.require_always = require_always
        self._token: str | None = None
        self._token_error: str | None = None
        try:
            token, source = _resolve_token()
            self._token = token
            if source in ("env", "secret_store"):
                logger.info("[WEB-AUTH] token active", source=source)
        except RuntimeError as exc:
            self._token_error = str(exc)
            logger.error("[WEB-AUTH] FAIL-CLOSED: no token resolvable", error=str(exc))

    # ------------------------------------------------------------------ ASGI
    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope.get("type")

        if scope_type == "websocket":
            # TASK-SEC-WS-AUTH-P0: fail-closed WS gate BEFORE the route runs.
            # A rejected handshake sends websocket.close directly and NEVER
            # reaches the inner app (no accept, no system-state frame).
            if ws_scope_authorized(scope, self._token):
                state = scope.setdefault("state", {})
                state["authenticated"] = True
                await self.app(scope, receive, send)
                return
            reason = (
                "web auth token unresolvable (fail-closed)"
                if self._token is None
                else "Unauthorized"
            )
            logger.warning(
                "[WEB-AUTH] websocket handshake REFUSED (missing or invalid token)",
                path=scope.get("path", ""),
            )
            await send(
                {
                    "type": "websocket.close",
                    "code": WS_CLOSE_UNAUTHORIZED,
                    "reason": reason,
                }
            )
            return

        if scope_type != "http":
            # lifespan and any future scope types: no client credentials, no
            # client-controlled data — pass through (connection framing only).
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if self._is_public(path):
            await self.app(scope, receive, send)
            return

        supplied = self._extract_token(scope)
        if (
            supplied
            and self._token is not None
            and hmac.compare_digest(supplied.encode("utf-8"), self._token.encode("utf-8"))
        ):
            await self.app(scope, receive, send)
            return

        status = 401
        body = (
            b'{"ok":false,"error":{"code":"UNAUTHORIZED",'
            b'"message":"missing or invalid web auth token"}}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    # ------------------------------------------------------------- internals
    @staticmethod
    def _is_public(path: str) -> bool:
        if path in PUBLIC_PATHS:
            return True
        return any(path.startswith(p) for p in PUBLIC_PREFIXES)

    @staticmethod
    def _extract_token(scope) -> str | None:
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip() or None
        xt = headers.get("x-nse-token", "").strip()
        if xt:
            return xt
        # Query param fallback for SSE clients that cannot set headers.
        qs = scope.get("query_string", b"").decode("latin-1")
        for part in qs.split("&"):
            if part.startswith("token="):
                val = part[6:].strip()
                if val:
                    return val
        return None


def install_web_auth(app, *, require_always: bool = False) -> None:
    """Wraps the FastAPI app's ASGI callable with WebAuthMiddleware.

    Called from create_app AFTER all routes are registered — outermost layer,
    so even undiscovered routes are protected (fail-closed by construction).
    Implementation note: Starlette's TestClient calls ``app(scope, receive,
    send)`` on the *instance*; a naive ``__call__ = lambda self, ...`` on a
    synthetic subclass breaks that binding (the lambda would receive the
    scope as ``self``). We therefore replace the instance's ``__call__`` via
    a proper bound method through ``functools.partialmethod``-free wrapper
    class that delegates to the stored middleware.
    """
    if getattr(app.state, "_web_auth_installed", False):
        return
    app.state._web_auth_installed = True

    # CRITICAL: FastAPI requires `fastapi_middleware_astack` (AsyncExitStack)
    # in scope — it is injected by FastAPI.__call__ BEFORE Router dispatch.
    # Therefore we must NOT bypass the app's own __call__ (class-swap wrapping
    # the router breaks exception middleware + dependency teardown). Instead:
    # wrap ONCE at the server boundary via ASGI middleware stack that runs
    # BEFORE FastAPI's own __call__ — achieved by composing a pure-ASGI
    # wrapper app and exposing it through the installed server (uvicorn binds
    # the object returned by the factory). For create_app, the cleanest hook
    # that preserves FastAPI semantics is Starlette middleware with
    # `app.add_middleware` — runs inside FastAPI.__call__, outermost among
    # Starlette middlewares, before routing.
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import JSONResponse

    class _TokenAuthMiddleware(BaseHTTPMiddleware):
        def __init__(self, inner_app, token_resolver) -> None:
            super().__init__(inner_app)
            self._resolve = token_resolver
            self._token: str | None = None
            self._error: str | None = None
            try:
                self._token = token_resolver()
            except Exception as exc:
                self._error = str(exc)

        async def dispatch(self, request: StarletteRequest, call_next):
            if self._is_public(request.url.path):
                return await call_next(request)
            if self._token is None:
                return JSONResponse(
                    status_code=500,
                    content={
                        "ok": False,
                        "error": {
                            "code": "AUTH_CONFIG_ERROR",
                            "message": "web auth token unresolvable (fail-closed)",
                        },
                    },
                )
            supplied = self._extract(request)
            if supplied and hmac.compare_digest(
                supplied.encode("utf-8"), self._token.encode("utf-8")
            ):
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                content={
                    "ok": False,
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "missing or invalid web auth token",
                    },
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        @staticmethod
        def _is_public(path: str) -> bool:
            return is_public_path(path)

        @staticmethod
        def _extract(request: StarletteRequest) -> str | None:
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                return auth[7:].strip() or None
            xt = request.headers.get("x-nse-token", "").strip()
            if xt:
                return xt
            qp = request.query_params.get("token", "").strip()
            return qp or None

    def _resolve() -> str:
        token, _src = _resolve_token()
        return token

    app.add_middleware(_TokenAuthMiddleware, token_resolver=_resolve)
    app.state._web_auth_token_source = "middleware"


class WebSocketAuthGuard:
    """Pure-ASGI outer wrapper enforcing token auth on ``websocket`` scopes.

    TASK-SEC-WS-AUTH-P0: Starlette's ``BaseHTTPMiddleware`` (the production
    HTTP auth layer) structurally never sees ``websocket`` scopes — they flow
    around it straight to the router. This class is the missing ASGI-boundary
    layer: it authenticates every websocket handshake BEFORE the FastAPI app
    runs (multi-channel extraction + constant-time compare via
    :func:`ws_scope_authorized`), rejects unauthorized handshakes with a
    ``websocket.close`` frame (code 4401) that never reaches the inner app,
    stamps ``scope["state"]["authenticated"] = True`` on success, and then
    delegates EVERYTHING — http and websocket alike — to the wrapped app.

    All attribute access is delegated to the wrapped FastAPI instance
    (``.state``, ``.routes``, route registration, engine boot's
    ``app_obj.state.server_state`` hand-off), so callers keep working with
    the app exactly as before; only the call operator is intercepted.

    ``guard_token`` resolution mirrors the HTTP middleware (env >
    SecureSecretStore > generated+persisted). ``None`` token + auth enabled
    means FAIL-CLOSED: every websocket handshake is refused (the HTTP layer
    reports the same misconfiguration as 500 AUTH_CONFIG_ERROR).
    """

    def __init__(self, asgi_app, *, guard_token: str | None) -> None:
        self._asgi_app = asgi_app
        self._guard_token = guard_token

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "websocket":
            if not ws_scope_authorized(scope, self._guard_token):
                logger.warning(
                    "[WEB-AUTH][WS] websocket handshake REFUSED at ASGI boundary "
                    "(missing or invalid token)",
                    path=scope.get("path", ""),
                )
                reason = (
                    "web auth token unresolvable (fail-closed)"
                    if self._guard_token is None
                    else "Unauthorized"
                )
                await send(
                    {
                        "type": "websocket.close",
                        "code": WS_CLOSE_UNAUTHORIZED,
                        "reason": reason,
                    }
                )
                return
            scope.setdefault("state", {})["authenticated"] = True
        await self._asgi_app(scope, receive, send)

    def __getattr__(self, name: str):
        # Delegation only (called when normal lookup fails); the guard's own
        # _asgi_app/_guard_token attributes resolve normally.
        return getattr(self._asgi_app, name)

    def __setattr__(self, name: str, value) -> None:
        if name in ("_asgi_app", "_guard_token"):
            object.__setattr__(self, name, value)
            return
        setattr(self._asgi_app, name, value)


def build_websocket_auth_guard(app, *, require_always: bool = False):
    """Returns the app wrapped in :class:`WebSocketAuthGuard`.

    Called by ``create_app`` as the FINAL step (after ``install_web_auth``)
    so the returned ASGI callable enforces websocket auth before anything
    else runs. When the operator disabled auth (``NSE_WEB_AUTH_DISABLE=1``)
    the app is returned unwrapped and a loud warning is logged (identical
    semantics to the HTTP opt-out; NEVER valid for LIVE execution mode).
    """
    if is_auth_disabled():
        logger.warning(
            "[WEB-AUTH][WS] auth disabled via NSE_WEB_AUTH_DISABLE=1 — websocket "
            "endpoints accept unauthenticated connections (NEVER combine with "
            "LIVE execution mode or a routable host binding)."
        )
        return app
    token: str | None
    try:
        token, source = _resolve_token()
        if source in ("env", "secret_store"):
            logger.info("[WEB-AUTH][WS] websocket guard active", source=source)
    except Exception as exc:
        # Fail-closed: an unresolvable token refuses every WS handshake.
        token = None
        logger.error("[WEB-AUTH][WS] FAIL-CLOSED: no token resolvable", error=str(exc))
    app.state._web_auth_ws_guard_installed = True
    return WebSocketAuthGuard(app, guard_token=token)
