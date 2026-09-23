"""DB provider options + connection-string REST routes (DATABASE TAB, 2026-09-23).

Self-contained ``APIRouter`` mounted by ``register_diagnostics_state_routes``.
Owns the two NEW endpoints of the db-provider-pro wave; every existing route
in ``diagnostics_state_routes.py`` stays untouched.

CONSUMES: nexus_scalp.database.connection_url (pure parse/build/redact),
  nexus_scalp.settings.provider_options (additive knob persistence),
  nexus_scalp.web.errors (code envelopes — no exception text ever echoed).

PROVIDES: POST /api/db/manage/parse-url, GET|POST /api/db/manage/options.

INVARIANTS (contract §3.2 / §3.3):
  * parse-url NEVER persists the discrete fields and NEVER returns the
    password: when the URL carried one it is routed to the OS SecretStore
    under PG_PASSWORD_SECRET_KEY and `fields` carries the five non-secret
    fields only;
  * every failure returns the existing code envelope (``DB_URL_PARSE_FAILED``
    / ``DB_OPTIONS_INVALID``) with a human sentence + request_id — never a
    traceback and never exception text (which may embed credentials);
  * options writes are additive-only on the existing PG_CONFIG row.

EXTEND: a new endpoint on this router is registered here and nowhere else.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nexus_scalp.database.connection_url import (
    ParsedPgConfig,
    ParseFailure,
    parse_pg_url,
)
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.settings.provider_options import (
    OPTION_KEYS,
    read_options,
    validate_options,
    write_options,
)
from nexus_scalp.web.errors import (
    log_web_error,
    new_request_id,
    request_id_from_request,
)

logger = get_logger("nexus_scalp.web.db_provider_routes")

router = APIRouter(prefix="/api/db/manage")


def _err(code: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    """Code envelope: the client gets the code + a sentence, never internals."""
    rid = request_id or new_request_id()
    return {
        "available": False,
        "success": False,
        "error": {"code": code, "message": message, "request_id": rid},
    }


def _route_secret(password: str) -> None:
    """Route a URL-carried password into the OS SecretStore (never echoed)."""
    if not password:
        return
    from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY
    from nexus_scalp.settings.secret_store import SecureSecretStore

    SecureSecretStore().set_secret(PG_PASSWORD_SECRET_KEY, password)


def _extract_password(raw: str) -> str:
    """Pull the userinfo password out of a URL without echoing it.

    Returns "" when the URL has no password.  Uses urlparse (never logged).
    """
    if "://" not in raw:
        return ""
    from urllib.parse import unquote, urlparse

    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    return unquote(parsed.password) if parsed.password else ""


@router.post("/parse-url")
def parse_url(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Parse a PostgreSQL connection URL into the discrete config fields.

    The discrete form and the URL field stay in sync; a malformed URL is
    refused with its parse reason and never reaches persistence.  When the
    URL carries a password, that password is routed to the OS SecretStore
    under the key the existing endpoints use and is NEVER returned: `fields`
    carries host/port/database/username/ssl_mode ONLY.
    """
    request_id = request_id_from_request(request)
    try:
        raw = str((payload or {}).get("url") or "")
        if not raw.strip():
            return _err(
                "DB_URL_PARSE_FAILED",
                "A connection URL is required.",
                request_id,
            )
        parsed = parse_pg_url(raw)
        if "reason" in parsed:
            reason: str = parsed["reason"]
            return _err("DB_URL_PARSE_FAILED", reason, request_id)

        # A password in the URL is a store write, not a response field.
        password = _extract_password(raw)
        if password:
            _route_secret(password)

        fields: ParsedPgConfig = parsed
        return {"success": True, "fields": fields}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/parse-url", request_id, exc)
        return _err(
            "DB_URL_PARSE_FAILED",
            "The connection URL could not be parsed.",
            request_id,
        )


@router.get("/options")
def get_options(request: Request) -> dict[str, Any]:
    """Read the advanced DatabaseConfig knobs + the domain's database name."""
    request_id = request_id_from_request(request)
    try:
        return {"success": True, "options": read_options("audit")}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/options", request_id, exc)
        return _err(
            "DB_OPTIONS_READ_FAILED",
            "The database options could not be read.",
            request_id,
        )


@router.post("/options")
def post_options(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Persist the advanced knobs ADDITIVELY (existing keys are preserved).

    Rules: ``connect_timeout_sec`` in 1..120, ``command_timeout_sec`` in
    0..600, unknown keys are rejected with ``DB_OPTIONS_INVALID``.
    """
    request_id = request_id_from_request(request)
    try:
        known, reason = validate_options(payload or {})
        if reason:
            return _err("DB_OPTIONS_INVALID", reason, request_id)
        written = write_options({key: (payload or {})[key] for key in known})
        if written.get("error"):
            return _err("DB_OPTIONS_INVALID", str(written["error"]), request_id)
        return {"success": True, "persisted": written.get("persisted") or []}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/options", request_id, exc)
        return _err(
            "DB_OPTIONS_WRITE_FAILED",
            "The database options could not be saved.",
            request_id,
        )


def register_db_provider_routes(app: Any) -> None:
    """Mount this router (called from register_diagnostics_state_routes)."""
    app.include_router(router)


__all__ = [
    "OPTION_KEYS",
    "ParseFailure",
    "ParsedPgConfig",
    "parse_url",
    "register_db_provider_routes",
    "router",
]
