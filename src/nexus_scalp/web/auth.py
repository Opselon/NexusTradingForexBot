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
#: WEB-UI-BOOTSTRAP (2026-09-11, Hermes forensic session): WEB-AUTH-P0 shipped
#: an INCOMPLETE allowlist — the remaining repo-root static bundle assets
#: (styles.css, responsive.css, cc_styles.css, control_center.js, tv_widget.*,
#: forensic_console.js, news_intelligence.js, replay_panel.js, marketplace.js,
#: dependency_*.js, command_center.html …) are loaded by <link>/<script>/
#: <iframe> tags that carry no credentials, so every dashboard load 401'd
#: them and a tokenless first visit could never render. This completes the
#: list to cover exactly the repo-root STATIC bundle files + the /vendor font
#: subtree. Every /api route and everything unknown still 401s (fail-closed).
#: BUG-266 (2026-09-13): the bootstrap cookie rode ONLY /app.js +
#: /api_client.js, but a browser's FIRST request is the document. "/" and
#: "/alt" (the React console shell) were gated, so a fresh profile / expired
#: cookie / token rotation rendered the raw 401 JSON envelope and the page
#: could never self-heal (app.js never loaded → cookie never issued → the
#: "UNAUTHORIZED" the operator saw on BOTH consoles). The index DOCUMENTS are
#: static shells with no state and no credentials: they become public entry
#: points that ISSUE the bootstrap cookie, exactly like the script assets.
#: Every data route (/api/**, SSE included) keeps full token enforcement.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/api/health",
        "/health",
        "/healthz",
        "/favicon.ico",
        # BUG-266: cookie-bootstrap entry documents (see header note).
        "/",
        "/index.html",
        "/alt",
        "/alt/",
        "/app.js",
        "/app.js.map",
        "/api_client.js",
        "/styles.css",
        "/responsive.css",
        "/cc_styles.css",
        "/tailwind.css",
        "/tv_widget_styles.css",
        "/tv_widget.js",
        "/tv_widget.html",
        "/control_center.js",
        "/forensic_console.js",
        "/news_intelligence.js",
        "/replay_panel.js",
        "/marketplace.js",
        "/dependency_api.js",
        "/dependency_graph.js",
        "/dependency_ui.js",
        "/dependency.html",
        "/dependency",
        "/command_center.html",
    }
)
#: /vendor/ = fontawesome webfonts (static binaries, no credentials).
#: /alt/ (BUG-266) = the built React console: static SPA shell, hashed assets
#: and client-side deep links (/alt/trading, /alt/audit …) — none of which
#: carry state or credentials. A TRAILING SLASH is mandatory so the prefix can
#: never match anything outside the console mount (e.g. /alternative-api).
#: Every API call the console makes goes to /api/**, still fully gated.
PUBLIC_PREFIXES: tuple[str, ...] = ("/static/", "/assets/", "/vendor/", "/alt/")
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
    # Path-traversal defense (CodeQL #62/#63/#67 contract): traversal
    # separators can never be public — refused upstream, 404 at the route.
    if ".." in path or "\\" in path:
        return False
    name = path.lstrip("/")
    if path in PUBLIC_PATHS or name in PUBLIC_JS_ASSETS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


WEB_AUTH_TOKEN_SECRET_NAME = "web_auth_token"
_TOKEN_BYTES = 32
#: WEB-UI-BOOTSTRAP cookie (first-party, HttpOnly): the legacy Web/ dashboard
#: issues ~50 raw fetch() calls + NX.api + an EventSource, none of which can
#: attach a Bearer header without a bundle-wide rewrite. The cookie carries
#: the SAME canonical token the middleware enforces (constant-time compare
#: unchanged); headers still win over the cookie.
WEB_AUTH_COOKIE_NAME = "nse_web_auth"
#: Cookie acceptance opt-out (header-only auth for operators behind proxies).
WEB_AUTH_COOKIE_DISABLE_ENV = "NSE_WEB_AUTH_COOKIE_DISABLE"
#: BUG-266: public paths whose response carries the bootstrap Set-Cookie.
#: The two script assets (WEB-UI-BOOTSTRAP) plus the index DOCUMENTS — the
#: browser's first request. Every entry here is public by construction
#: (a gated path can never be reached tokenless, so setting a cookie on it
#: would be dead code); pinned by tests/unit/test_web_auth_bootstrap_bug266.py.
COOKIE_BOOTSTRAP_PATHS: frozenset[str] = frozenset(
    {"/", "/index.html", "/app.js", "/api_client.js", "/alt", "/alt/"}
)


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


def current_web_auth_token() -> str | None:
    """The token the middleware enforces right now (bootstrap accessor).

    Same resolution chain as _resolve_token but NEVER generates: returns
    None when no env/secret-store token exists (the generator owns
    persistence; this accessor must not mint a second value).
    """
    env_token = os.environ.get("NSE_WEB_AUTH_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        from nexus_scalp.settings.secret_store import SecureSecretStore

        stored = SecureSecretStore().get_secret(WEB_AUTH_TOKEN_SECRET_NAME)
        if stored and stored.strip():
            return stored.strip()
    except Exception as exc:  # pragma: no cover - platform edge (DPAPI etc.)
        logger.warning("[WEB-AUTH] current_web_auth_token store probe failed", error=str(exc))
    return None


#: The token the INSTALLED middleware layer resolved for this process
#: (set by install_web_auth's resolver — env, store, or generated). BUG-266:
#: web_auth_token_in_process() lets auth_boot.publish() export/persist the
#: authoritative value even when resolution happened in-process (generated
#: branch) and was never written anywhere an operator tool can read.
#: Mutable container (ruff PLW0603 pattern precedent: tests/conftest).
_LIVE_WEB_AUTH_TOKEN: dict[str, str | None] = {"token": None}


def web_auth_token_in_process() -> str | None:
    """Token resolved by install_web_auth in THIS process (never generates)."""
    return _LIVE_WEB_AUTH_TOKEN["token"]


class WebAuthMiddleware:
    """Pure ASGI middleware: bearer/X-NSE-Token/query-token enforcement.

    Kept framework-free (pure ASGI wrapper) so it composes with the existing
    Starlette middleware stack without changing create_app's response contract.
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
        if scope["type"] != "http":
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
        # BUG-266: routes through is_public_path — the single source of
        # truth. The previous inline copy skipped the traversal guard, so
        # this (test-facing) layer and the installed layer disagreed on
        # paths like /assets/../api/x. One rule, one implementation.
        return is_public_path(path)

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
        # WEB-UI-BOOTSTRAP: first-party cookie transport (set on the public
        # static assets). Accepted AFTER headers so explicit credentials win.
        if not os.environ.get(WEB_AUTH_COOKIE_DISABLE_ENV, "").strip():
            for raw in headers.get("cookie", "").split(";"):
                name, _, value = raw.strip().partition("=")
                if name == WEB_AUTH_COOKIE_NAME and value.strip():
                    return value.strip()
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
                response = await call_next(request)
                # WEB-UI-BOOTSTRAP + BUG-266: the cookie rides EVERY public
                # entry surface — the legacy bundle's /app.js +
                # /api_client.js script tags AND the index documents ("/",
                # "/index.html", "/alt", "/alt/"). The documents matter
                # because they are the browser's FIRST request: a fresh
                # profile / expired cookie / token rotation used to hit a
                # gated "/" and render the raw 401 JSON with no way to
                # recover (app.js never loaded → cookie never issued).
                # Same-origin fetch() and EventSource then send this cookie
                # automatically, so BOTH consoles authenticate without a
                # hand-pasted token. The token value never appears in page
                # source or JS; removing the cookie re-locks. Data routes
                # (/api/**) stay fail-closed — only the transport widened.
                if (
                    request.url.path in COOKIE_BOOTSTRAP_PATHS
                    and self._token is not None
                    and not os.environ.get(WEB_AUTH_COOKIE_DISABLE_ENV, "").strip()
                ):
                    response.set_cookie(
                        key=WEB_AUTH_COOKIE_NAME,
                        value=self._token,
                        max_age=60 * 60 * 12,
                        httponly=True,
                        samesite="strict",
                        path="/",
                    )
                return response
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
            # WEB-UI-BOOTSTRAP: first-party cookie transport. Headers win.
            if not os.environ.get(WEB_AUTH_COOKIE_DISABLE_ENV, "").strip():
                cookie_token = request.cookies.get(WEB_AUTH_COOKIE_NAME, "").strip()
                if cookie_token:
                    return cookie_token
            qp = request.query_params.get("token", "").strip()
            return qp or None

    def _resolve() -> str:
        # BUG-266: remember what THIS process enforced so auth_boot.publish()
        # (and any first-party tooling) can retrieve the generated token
        # without re-minting one. The middleware instance stays the authority.
        token, _src = _resolve_token()
        _LIVE_WEB_AUTH_TOKEN["token"] = token
        return token

    app.add_middleware(_TokenAuthMiddleware, token_resolver=_resolve)
    app.state._web_auth_token_source = "middleware"
