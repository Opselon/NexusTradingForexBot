"""Read / write the advanced DatabaseConfig knobs (DATABASE TAB, 2026-09-23).

The config model already carries ``command_timeout_sec``, ``connect_timeout_sec``,
``migrate_on_startup`` and ``pooling_enabled``, but they had no API surface —
this module is that surface, persisted ADDITIVELY on the SAME settings row the
discrete form uses (``PG_CONFIG_SETTING_KEY``).

INVARIANTS (contract §3.3):
  * the write is ADDITIVE: existing keys on the PG_CONFIG row
    (provider/domain/host/port/database/username/ssl_mode/password_secret)
    are preserved — never replaced, never deleted;
  * the ``password_secret`` REFERENCE is preserved and is never overwritten
    with a plaintext password (the password itself never enters this module);
  * unknown keys are rejected by the caller (fail-loud), never silently kept;
  * persistence goes through ``SettingsDatabase`` with ``value_type="json"``,
    ``source="USER_SETTINGS"``, ``actor="web"``.

EXTEND: add a knob by entering it in ``OPTION_KEYS`` + ``DEFAULT_OPTIONS`` and
teaching ``validate_options`` its bounds.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("nexus_scalp.settings.provider_options")

#: The advanced knobs this module owns (contract §3.3).  These are the ONLY
#: keys a POST /api/db/manage/options body may carry.
#:
#: Pool sizing (PG-POOL-CONFIG-001): the four keys below were the missing
#: half of "the operator changes the pool size in the UI and nothing happens"
#: — the fabric built PoolLimits from call-site literals only.  They ride the
#: SAME additive row and the SAME bounds discipline as the others; a row that
#: predates them reads them as absent, and the fabric then keeps its own
#: defaults (never zero-filled).
OPTION_KEYS: frozenset[str] = frozenset(
    {
        "command_timeout_sec",
        "connect_timeout_sec",
        "migrate_on_startup",
        "pooling_enabled",
        "pool_min_size",
        "pool_max_size",
        "pool_idle_timeout_sec",
        "pool_max_lifetime_sec",
    }
)

_POOL_KEYS: tuple[str, ...] = (
    "pool_min_size",
    "pool_max_size",
    "pool_idle_timeout_sec",
    "pool_max_lifetime_sec",
)

#: Keys that must survive an additive write (they belong to the discrete form).
#: ``password_secret`` is a REFERENCE to the OS SecretStore — never a value.
_PROTECTED_KEYS: frozenset[str] = frozenset(
    {
        "provider",
        "domain",
        "host",
        "port",
        "database",
        "username",
        "ssl_mode",
        "password_secret",
    }
)

#: Backend defaults — mirror ``DatabaseConfig.for_postgres`` exactly so the UI
#: never renders an invented number for an unset knob (contract: a missing
#: value renders as UNAVAILABLE, but once the surface exists a row exists).
#:
#: The pool knobs deliberately have NO default here (PG-POOL-CONFIG-001): a
#: missing key means "the fabric's own defaults apply", and reporting a
#: fabricated number would both misrepresent the running pool and make a
#: fresh row indistinguishable from a deliberate 0.  ``read_options`` keeps
#: them absent and the UI renders UNAVAILABLE.
DEFAULT_OPTIONS: dict[str, Any] = {
    "command_timeout_sec": 0,
    "connect_timeout_sec": 10,
    "migrate_on_startup": True,
    "pooling_enabled": True,
}

#: Bounds (inclusive).  Documented to the operator through the error envelope.
#: Pool bounds mirror psycopg_pool's own contract: min_size may be 0 (open
#: lazily) but never above max_size; idle/lifetime are seconds, 0 = never reap.
_BOUNDS: dict[str, tuple[int, int]] = {
    "connect_timeout_sec": (1, 120),
    "command_timeout_sec": (0, 600),
    "pool_min_size": (0, 64),
    "pool_max_size": (1, 128),
    "pool_idle_timeout_sec": (0, 86400),
    "pool_max_lifetime_sec": (0, 604800),
}


def _settings_database() -> Any:
    """Open the settings store (late import — keeps this module import-clean)."""
    from nexus_scalp.settings.service import SettingsDatabase

    return SettingsDatabase()


def _config_key() -> str:
    from nexus_scalp.database.config import PG_CONFIG_SETTING_KEY

    return PG_CONFIG_SETTING_KEY


def _read_row() -> dict[str, Any]:
    """Return the persisted PG config row as a dict ({} when absent/unreadable)."""
    from nexus_scalp.database.config import PG_CONFIG_SETTING_KEY

    db = None
    try:
        db = _settings_database()
        row = db.get(PG_CONFIG_SETTING_KEY)
        value = row.value if row is not None else None
        if value is None:
            return {}
        if isinstance(value, str):
            import json

            value = json.loads(value)
        return dict(value) if isinstance(value, dict) else {}
    except Exception as exc:  # best-effort read: a fresh env has no row yet
        logger.debug("[PROVIDER_OPTIONS] config row unreadable: %s", exc)
        return {}
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _known_domain_db_names() -> list[str]:
    """Per-domain database names from the domain registry (DEFAULT_DB_FILES)."""
    from nexus_scalp.database.provider import DEFAULT_DB_FILES

    return sorted(DEFAULT_DB_FILES)


def _default_database_for(domain: str) -> str:
    """Canonical PostgreSQL database name for a domain (constructor-sourced)."""
    from nexus_scalp.database.config import DatabaseConfig

    return str(DatabaseConfig.for_postgres(domain=domain or "audit").database)


def validate_options(payload: dict[str, Any]) -> tuple[set[str], str]:
    """Check an incoming options payload.

    Returns ``(known, reason)``: ``known`` is the set of recognized keys,
    ``reason`` is the first refusal sentence or ``""`` when the payload is
    valid.  Never raises — the route turns the sentence into a code envelope.
    """
    if not isinstance(payload, dict):
        return set(), "Options payload must be an object."
    known = set(payload) & OPTION_KEYS
    unknown = set(payload) - OPTION_KEYS
    if unknown:
        return known, (
            f"Unknown option key(s): {', '.join(sorted(unknown))}. "
            "Allowed keys are: "
            f"{', '.join(sorted(OPTION_KEYS))}."
        )
    for key in sorted(known):
        value = payload[key]
        if key in _BOUNDS:
            if not isinstance(value, int) or isinstance(value, bool):
                return known, f"Option '{key}' must be a whole number."
            low, high = _BOUNDS[key]
            if not low <= value <= high:
                return known, f"Option '{key}' must be between {low} and {high}."
        elif not isinstance(value, bool):
            return known, f"Option '{key}' must be true or false."
    # Pool sizing is a PAIR: a min above the max would make psycopg_pool raise
    # at open time on the next provision (the fabric clamps it, but the row
    # would then silently mean something other than what it says).
    if {"pool_min_size", "pool_max_size"} <= known:
        if payload["pool_min_size"] > payload["pool_max_size"]:
            return known, (
                f"Option 'pool_min_size' ({payload['pool_min_size']}) must not "
                f"exceed 'pool_max_size' ({payload['pool_max_size']})."
            )
    return known, ""


def read_options(domain: str = "audit") -> dict[str, Any]:
    """Read the advanced knobs + the domain's configured database name.

    A knob absent from the persisted row falls back to the backend default
    (contract: never zero-fill, never invent).  The pool-sizing knobs have NO
    default: absent means "the fabric's own defaults apply" and the key is
    omitted entirely so the UI renders UNAVAILABLE rather than a number the
    running pool was never actually given.
    """
    row = _read_row()
    options = dict(DEFAULT_OPTIONS)
    for key in OPTION_KEYS:
        if key in row and row[key] is not None:
            options[key] = row[key]
    # Pool knobs are opt-in: drop the ones the row never carried so the UI
    # shows UNAVAILABLE instead of a fabricated default.
    for key in _POOL_KEYS:
        if key not in row or row[key] is None:
            options.pop(key, None)
    database = ""
    if domain and str(row.get("domain") or "audit") == domain:
        database = str(row.get("database") or "")
    if not database:
        # The config is domain-scoped; the row's own domain wins, else the
        # canonical default name for the requested domain is reported as such
        # (sourced from the real constructor, never a literal).
        database = _default_database_for(domain)
    return {
        **options,
        "domain": domain or "audit",
        "database": database,
    }


def write_options(payload: dict[str, Any], domain: str = "audit") -> dict[str, Any]:
    """Persist ``payload``'s recognized keys ADDITIVELY onto the PG config row.

    Returns ``{"options": {...}, "persisted": [...keys]}``.  The caller MUST
    have validated the payload via :func:`validate_options` first (unknown
    keys are dropped here rather than persisted, but the route rejects them
    before this point).
    """
    _, reason = validate_options(payload)
    if reason:
        return {"options": None, "persisted": [], "error": reason}

    row = _read_row()
    merged: dict[str, Any] = dict(row)
    for key in OPTION_KEYS:
        if key in payload and payload[key] is not None:
            merged[key] = payload[key]

    # Guard rail: an options write may only ADD its own keys.  Whatever the
    # discrete form owns (host/port/database/username/ssl_mode/provider/
    # domain + the secret REFERENCE) is restored from the original row, so a
    # future knob can never silently clobber connection truth.
    for key in _PROTECTED_KEYS:
        if key in row:
            merged[key] = row[key]

    # Security-critical guard: the row must keep the SecretStore REFERENCE and
    # must never carry a plaintext password.  Additive-only, no deletions.
    merged.pop("password", None)
    if not merged.get("password_secret"):
        from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY

        merged["password_secret"] = PG_PASSWORD_SECRET_KEY
    merged.setdefault("provider", "postgresql")
    merged.setdefault("domain", domain or "audit")

    db = None
    try:
        db = _settings_database()
        db.set(
            _config_key(),
            merged,
            value_type="json",
            source="USER_SETTINGS",
            actor="web",
        )
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    persisted = sorted(key for key in OPTION_KEYS if key in payload)
    logger.info("[PROVIDER_OPTIONS] persisted additive keys=%s", persisted)
    return {"options": read_options(domain), "persisted": persisted}


def options_status_snapshot(domain: str = "audit") -> dict[str, Any]:
    """The ``options`` + ``domains`` blocks for /api/db/manage/status."""
    return {
        "options": {key: read_options(domain)[key] for key in sorted(OPTION_KEYS)},
        "domains": _known_domain_db_names(),
    }
