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
    is_parse_failure,
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


def _lifecycle_manager() -> Any:
    from nexus_scalp.database.provider_lifecycle import ProviderLifecycleManager
    return ProviderLifecycleManager()


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
        if is_parse_failure(parsed):
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


@router.get("/dashboard")
def get_dashboard(request: Request) -> dict[str, Any]:
    """Real runtime database dashboard with sizes, pool, and latency."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.health import DatabaseHealthService

        svc = DatabaseHealthService()
        return {"success": True, "dashboard": svc.dashboard_snapshot()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/dashboard", request_id, exc)
        return _err(
            "DB_DASHBOARD_FAILED",
            "Could not load database dashboard.",
            request_id,
        )


@router.get("/views")
def get_views(request: Request) -> dict[str, Any]:
    """Return analytics-view status on the active provider."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.config import load_database_config
        from nexus_scalp.database.drivers import get_driver
        from nexus_scalp.database.views import ensure_analytics_views

        cfg = load_database_config("audit")
        driver = get_driver(cfg)
        try:
            return {"success": True, "views": ensure_analytics_views(driver)}
        finally:
            driver.close()
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/views", request_id, exc)
        return _err("DB_VIEWS_FAILED", "Could not inspect analytics views.", request_id)


@router.post("/views/ensure")
def ensure_views(request: Request) -> dict[str, Any]:
    """Explicitly create analytics views on the active provider."""
    return get_views(request)


@router.get("/provider-state")
def get_provider_state(request: Request) -> dict[str, Any]:
    """Lifecycle transition state for provider switching."""
    request_id = request_id_from_request(request)
    try:
        mgr = _lifecycle_manager()
        return {"success": True, "state": mgr.get_state().to_dict()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/provider-state", request_id, exc)
        return _err(
            "DB_PROVIDER_STATE_FAILED",
            "Could not read provider state.",
            request_id,
        )


@router.post("/transition/start")
def transition_start(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Initiate a multi-step transition toward a target provider."""
    request_id = request_id_from_request(request)
    try:
        target = str((payload or {}).get("target_provider") or "sqlite")
        mgr = _lifecycle_manager()
        st = mgr.start_transition(target)
        return {"success": True, "state": st.to_dict()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/transition/start", request_id, exc)
        return _err(
            "DB_TRANSITION_START_FAILED",
            "Could not initiate provider transition.",
            request_id,
        )


@router.post("/transition/test")
def transition_test(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    request_id = request_id_from_request(request)
    try:
        mgr = _lifecycle_manager()
        ok = mgr.test_target_connection((payload or {}).get("config_overrides"))
        return {"success": ok, "tested": ok, "state": mgr.get_state().to_dict()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/transition/test", request_id, exc)
        return _err("DB_TRANSITION_TEST_FAILED", "Could not test target provider.", request_id)


@router.post("/transition/migrate")
def transition_migrate(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Run an explicit provider migration and keep activation separate."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.config import DatabaseConfig, load_database_config
        from nexus_scalp.database.migrate_engine import MigrationOptions, SqliteToPostgresMigrator

        mgr = _lifecycle_manager()
        mgr.mark_migrating()
        source = load_database_config("audit")
        target = DatabaseConfig.for_postgres("audit")
        if not source.is_sqlite:
            return _err("DB_TRANSITION_MIGRATE_INVALID", "Migration requires SQLite as the source.", request_id)
        report = SqliteToPostgresMigrator(
            source, target, MigrationOptions(batch_size=int((payload or {}).get("batch_size") or 2000))
        ).run()
        passed = report.status == "SUCCESS"
        mgr.mark_migration(passed, "Migration failed" if not passed else "")
        return {"success": passed, "report": report.to_dict(), "state": mgr.get_state().to_dict()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/transition/migrate", request_id, exc)
        return _err("DB_TRANSITION_MIGRATE_FAILED", "Could not migrate to target provider.", request_id)


@router.post("/transition/verify")
def transition_verify(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    request_id = request_id_from_request(request)
    try:
        mgr = _lifecycle_manager()
        passed = bool((payload or {}).get("passed", False))
        mgr.mark_verification(passed, str((payload or {}).get("error") or ""))
        return {"success": passed, "state": mgr.get_state().to_dict()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/transition/verify", request_id, exc)
        return _err("DB_TRANSITION_VERIFY_FAILED", "Could not record provider verification.", request_id)


@router.post("/transition/activate")
def transition_activate(request: Request) -> dict[str, Any]:
    request_id = request_id_from_request(request)
    try:
        mgr = _lifecycle_manager()
        activated = mgr.confirm_activation(force=False)
        return {"success": activated, "activated": activated, "state": mgr.get_state().to_dict()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/transition/activate", request_id, exc)
        return _err("DB_TRANSITION_ACTIVATE_FAILED", "Provider activation was not confirmed.", request_id)


@router.post("/transition/divergence")
def transition_divergence(request: Request) -> dict[str, Any]:
    """Check for unmigrated operational rows before switching providers."""
    request_id = request_id_from_request(request)
    try:
        mgr = _lifecycle_manager()
        res = mgr.check_divergence()
        return {"success": True, "divergence": res.to_dict()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/transition/divergence", request_id, exc)
        return _err(
            "DB_DIVERGENCE_CHECK_FAILED",
            "Could not complete divergence check.",
            request_id,
        )


@router.post("/reverse-migrate")
def reverse_migrate(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Stream operational data from PostgreSQL back to SQLite."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.config import DatabaseConfig, load_database_config
        from nexus_scalp.database.migrate_engine import MigrationOptions
        from nexus_scalp.database.migrate_reverse import PostgresToSqliteMigrator

        src = load_database_config("audit")
        if not src.is_postgresql:
            return _err(
                "DB_REVERSE_MIGRATE_REJECTED",
                "Reverse migration requires PostgreSQL to be the configured active provider.",
                request_id,
            )
        dst = DatabaseConfig.for_sqlite("audit")

        opts = MigrationOptions(
            batch_size=int((payload or {}).get("batch_size") or 2000),
            validate_checksums=True,
        )
        mig = PostgresToSqliteMigrator(src, dst, opts)
        report = mig.run()
        return {"success": report.status == "SUCCESS", "report": report.to_dict()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/reverse-migrate", request_id, exc)
        return _err(
            "DB_REVERSE_MIGRATE_FAILED",
            "Reverse migration from PostgreSQL to SQLite failed.",
            request_id,
        )


@router.get("/purge/policies")
def get_purge_policies(request: Request) -> dict[str, Any]:
    """Get domain-aware data lifecycle policies."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.lifecycle import DatabaseLifecycleManager

        mgr = DatabaseLifecycleManager()
        return {"success": True, "policies": mgr.get_policies()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/purge/policies", request_id, exc)
        return _err(
            "DB_PURGE_POLICIES_FAILED",
            "Could not load purge policies.",
            request_id,
        )


@router.post("/purge/preview")
def preview_purge(request: Request) -> dict[str, Any]:
    """Estimate purgeable rows across policies without deleting."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.lifecycle import DatabaseLifecycleManager

        mgr = DatabaseLifecycleManager()
        items = mgr.preview_purge()
        return {"success": True, "preview": [item.__dict__ for item in items]}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/purge/preview", request_id, exc)
        return _err(
            "DB_PURGE_PREVIEW_FAILED",
            "Could not generate purge preview.",
            request_id,
        )


@router.post("/purge/run")
def run_purge(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Execute domain-aware batched purging of expired rows."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.lifecycle import DatabaseLifecycleManager

        mgr = DatabaseLifecycleManager()
        batch_size = int((payload or {}).get("batch_size") or 2000)
        maintenance = bool((payload or {}).get("run_maintenance", True))
        res = mgr.run_purge(batch_size=batch_size, run_maintenance=maintenance)
        return {"success": not res.errors, "result": res.to_dict()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/purge/run", request_id, exc)
        return _err(
            "DB_PURGE_RUN_FAILED",
            "Data lifecycle purge failed.",
            request_id,
        )


@router.get("/logs")
def get_logs(request: Request) -> dict[str, Any]:
    """Read persisted database warning/error records."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.log_store import DatabaseLogStore

        return {"success": True, "logs": DatabaseLogStore().query_recent(limit=50)}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/logs", request_id, exc)
        return _err("DB_LOGS_FAILED", "Could not load database logs.", request_id)


@router.get("/purge/history")
def get_purge_history(request: Request) -> dict[str, Any]:
    """Read the lifecycle manager's recent purge history."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.lifecycle import DatabaseLifecycleManager

        return {"success": True, "history": DatabaseLifecycleManager().get_history()}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/purge/history", request_id, exc)
        return _err("DB_PURGE_HISTORY_FAILED", "Could not load purge history.", request_id)


@router.post("/purge/policy")
def update_purge_policy(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Set one retention policy through the lifecycle manager."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.lifecycle import DatabaseLifecycleManager

        table = str((payload or {}).get("table") or "")
        days = int((payload or {}).get("retention_days"))
        if not table or days < 0:
            return _err(
                "DB_PURGE_POLICY_INVALID", "A table and non-negative days are required.", request_id
            )
        policy = DatabaseLifecycleManager().update_policy(table, days)
        return {"success": True, "policy": policy}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/purge/policy", request_id, exc)
        return _err("DB_PURGE_POLICY_FAILED", "Could not save purge policy.", request_id)


@router.post("/maintenance")
def run_maintenance(request: Request) -> dict[str, Any]:
    """Run database-specific routine maintenance."""
    request_id = request_id_from_request(request)
    try:
        from nexus_scalp.database.lifecycle import DatabaseLifecycleManager

        mgr = DatabaseLifecycleManager()
        report = mgr.run_maintenance()
        return {"success": True, "maintenance": report}
    except Exception as exc:
        log_web_error(logger, "/api/db/manage/maintenance", request_id, exc)
        return _err(
            "DB_MAINTENANCE_FAILED",
            "Database maintenance routine failed.",
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
