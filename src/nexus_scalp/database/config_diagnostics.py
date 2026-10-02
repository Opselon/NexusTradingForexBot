"""Effective PostgreSQL config diagnostics — WHAT is set, and WHO set it.

GROUND_TRUTH §C2: :func:`nexus_scalp.database.config.load_database_config`
implements a sound precedence ladder (defaults < persisted settings <
per-key env overlay < audit test-seam), but the ladder is UNVERIFIABLE at
runtime: there is no single place that reports the *effective* PostgreSQL
configuration *and* which layer supplied each field. An operator who sees
``password authentication failed for user "postgres"`` cannot tell whether
the username came from a persisted ``database.postgresql_config`` row or
from an ``NSE_DATABASE__PG_USER`` export in a shell they forgot about — and
that is exactly the distinction the incident turned on.

This module reports, for one resolved ``DatabaseConfig``:

* the non-secret connection fields (provider/host/port/database/username/
  ssl_mode) — never a password;
* the SOURCE of each field — ``default`` / ``persisted_settings`` /
  ``env_overlay`` / ``audit_seam`` — re-derived from the same
  env + persisted-settings evidence the resolver itself reads. The docstring
  of ``load_database_config`` is the authority for the ladder; this
  re-derivation must never re-run connection logic (§F read-plane contract:
  no connection, no driver, no ``SELECT *``, no unbounded ``fetchall``);
* a stable FINGERPRINT of the non-secret identity, for log correlation
  across two shells that claim to be the same deployment;
* the runtime identity: the ``nexus_scalp`` module actually imported + the
  process CWD;
* ``stale_editable_install`` — is the running ``nexus_scalp`` the code this
  process's CWD belongs to? See §B: on this box a bare
  ``import nexus_scalp`` resolves to ``...\\nse-pr597\\src`` (a STALE
  non-git snapshot) while the work happens elsewhere. Two shells in two
  checkouts then run DIFFERENT CODE with no signal. This diagnostic makes
  that silent defect LOUD. It never silently corrects the install — it
  reports the truth and points the operator at the fix.

Security contract (hard): NEVER log or return a password value, NEVER
return a URL carrying one. Credential availability is reported as a BOOLEAN
only, via :meth:`SecureSecretStore.has_secret`. Everything emitted is safe
to paste into an issue.

Failure isolation: every probe is individually guarded. A settings DB that
cannot be opened yields ``field_sources`` whose values are ``"UNKNOWN"`` —
reported as unavailable, never default-filled and never a crash.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus_scalp.database.config import (
    PG_CONFIG_SETTING_KEY,
    PG_PASSWORD_SECRET_KEY,
    PROVIDER_SETTING_KEY,
    DatabaseConfig,
)

#: The layers of the documented precedence ladder. ``UNKNOWN`` marks a source
#: that could not be re-derived (settings DB unreadable) and is NEVER
#: collapsed to ``default``: "we could not check" is not "nothing set it".
SOURCE_DEFAULT = "default"
SOURCE_PERSISTED = "persisted_settings"
SOURCE_ENV = "env_overlay"
SOURCE_SEAM = "audit_seam"
#: Alias kept for readers who name the seam layer by its full word.
SOURCE_AUDIT_SEAM = SOURCE_SEAM
SOURCE_UNKNOWN = "UNKNOWN"

#: Provider key of the per-key overlay (see the ``load_database_config``
#: docstring): it gates the whole env branch and is the one key the audit
#: seam never overrides.
_ENV_PROVIDER_KEY = "NSE_DATABASE__PROVIDER"

_ENV_FIELD_KEYS: dict[str, str] = {
    "host": "NSE_DATABASE__PG_HOST",
    "port": "NSE_DATABASE__PG_PORT",
    "database": "NSE_DATABASE__PG_DATABASE",
    "username": "NSE_DATABASE__PG_USER",
    "ssl_mode": "NSE_DATABASE__PG_SSLMODE",
}

#: The non-secret connection fields, in the (provider, host, port, database,
#: username, ssl_mode) order the fingerprint hashes. ``provider`` is first
#: because a provider change redefines what every other field means.
_CONNECTION_FIELDS: tuple[str, ...] = (
    "provider",
    "host",
    "port",
    "database",
    "username",
    "ssl_mode",
)

_STALE_DETAIL_TEMPLATE = (
    "nexus_scalp resolves to {module_path}, which is NOT the checkout this "
    "process runs from (cwd={cwd}, expected repo root={repo_root}). This is "
    "the stale-editable-install defect (GROUND_TRUTH §B): the venv's "
    "__editable__.nexus_scalp_engine.pth points at another tree — the "
    "diagnostics and the code under test are not the same code. Fix the "
    "install target (`uv pip install -e .` from {repo_root}, or export "
    "PYTHONPATH={src_dir}) and re-run; do not paper over it."
)


@dataclass
class ConfigDiagnosticsResult:
    """Structured diagnostic payload (safe to paste into an issue)."""

    #: The resolved non-secret connection fields.
    effective: dict[str, Any] = field(default_factory=dict)
    #: Per-field provenance (values: the ``SOURCE_*`` constants).
    field_sources: dict[str, str] = field(default_factory=dict)
    #: Stable hex digest of the non-secret connection identity.
    fingerprint: str = ""
    #: Credential availability — boolean only, never the value.
    password_configured: bool | None = None
    #: Secret-store key consulted (diagnostic only; the key is not a secret).
    password_secret_key: str = ""
    #: Runtime identity.
    runtime_identity: dict[str, Any] = field(default_factory=dict)
    #: Stale-editable-install detection.
    stale_editable_install: bool = False
    stale_editable_detail: str = ""
    #: Provenance-derivation problems (each probe is individually guarded;
    #: a populated list means evidence was unavailable, never a crash).
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Flat dict form (the return shape of ``effective_config_diagnostics``)."""
        return {
            "effective": dict(self.effective),
            "field_sources": dict(self.field_sources),
            "config_identity": {
                "fingerprint": self.fingerprint,
                "fields": list(_CONNECTION_FIELDS),
            },
            "password_configured": self.password_configured,
            "password_secret_key": self.password_secret_key,
            "runtime_identity": dict(self.runtime_identity),
            "stale_editable_install": {
                "detected": self.stale_editable_install,
                "detail": self.stale_editable_detail,
            },
            "warnings": list(self.warnings),
        }

    # -- assertions for callers/tests -----------------------------------

    @property
    def config_fingerprint(self) -> str:
        """Alias kept for readers who grep the fingerprint by name."""
        return self.fingerprint


def _provider_of(cfg: DatabaseConfig) -> str:
    """The provider name the way :func:`load_database_config` reports it."""
    if cfg.is_postgresql:
        return "postgresql"
    if cfg.is_sqlite:
        return "sqlite"
    return str(getattr(cfg.provider, "value", cfg.provider) or "unknown")


def _config_fingerprint(cfg: DatabaseConfig) -> str:
    """Stable digest of the non-secret connection identity.

    Deliberately excludes every secret: the fingerprint is meant for log
    correlation and issue pastes, so it must be safe to publish. A stable
    identity is what lets an operator compare two shells and see they are
    NOT talking about the same effective config.
    """
    parts = [_provider_of(cfg)]
    for name in _CONNECTION_FIELDS[1:]:
        parts.append(str(getattr(cfg, name, "") or ""))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"dbcfg-{digest[:16]}"


def _module_identity() -> dict[str, Any]:
    """Which ``nexus_scalp`` is actually imported, plus the process CWD."""
    out: dict[str, Any] = {"cwd": str(Path.cwd())}
    try:
        import nexus_scalp

        resolved = getattr(nexus_scalp, "__file__", None)
        out["nexus_scalp_module"] = str(resolved) if resolved else None
    except Exception as exc:  # pragma: no cover - import-time failure isolation
        out["nexus_scalp_module"] = None
        out["nexus_scalp_import_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _repo_root_of(module_file: str | None, cwd: str) -> Path | None:
    """The checkout a resolved module file or CWD belongs to, or None.

    ``src/nexus_scalp/__init__.py`` -> parents[2]; ``src/nexus_scalp/...``
    -> parents[2] as well (any file *under* the package shares the root).
    Used only for provenance comparison — never for path building.
    """
    try:
        if module_file:
            p = Path(module_file).resolve()
            for parent in p.parents:
                if parent.name == "src":
                    return parent.parent
        if cwd:
            p = Path(cwd).resolve()
            for parent in p.parents:
                if parent.name == "src":
                    return parent.parent
            return p
    except (OSError, ValueError):
        return None
    return None


def _stale_editable_check(module_file: str | None, cwd: str) -> tuple[bool, str]:
    """Detect the resolved module living under a DIFFERENT checkout than CWD.

    The rule (GROUND_TRUTH §B): the resolved ``nexus_scalp.__file__`` must
    live under the repo root of the process CWD — both under the SAME
    checkout. When the editable install pins imports to a foreign tree (a
    stale non-git snapshot like ``nse-pr597``), the process silently runs
    different code than its CWD implies. Never fix it silently — flag it.
    """
    if not module_file:
        return False, ""
    mod_root = _repo_root_of(module_file, cwd)
    cwd_root = _repo_root_of(None, cwd)
    if mod_root is None or cwd_root is None:
        return False, ""
    try:
        same = mod_root.resolve() == cwd_root.resolve()
        if same:
            return False, ""
        src_dir = mod_root / "src"
        return True, _STALE_DETAIL_TEMPLATE.format(
            module_path=module_file,
            cwd=cwd,
            repo_root=str(cwd_root),
            src_dir=str(src_dir),
        )
    except (OSError, ValueError):
        return False, ""


def _read_persisted_settings(
    settings_db_path: str | None,
) -> tuple[dict[str, Any], str | None, bool]:
    """Read the persisted provider + pg_config rows (never a connection).

    Returns ``(pg_config_dict, persisted_provider, openable)``:

    * ``pg_config_dict`` — the persisted ``database.postgresql_config`` row
      (empty when the row is absent, unparsable, or not a JSON object);
    * ``persisted_provider`` — the persisted ``database.provider`` value
      (``None`` when the row is absent);
    * ``openable`` — whether the settings DB could be opened at all. This is
      the only thing separating "fresh install, no rows yet" (the default
      layer legitimately owns the fields) from an unreadable DB (UNKNOWN).

    Any failure to open the DB yields ``({}, None, False)`` — the caller
    marks affected sources UNKNOWN rather than default-filling them.
    """
    try:
        from nexus_scalp.settings.service import SettingsDatabase

        db = SettingsDatabase(db_path=Path(settings_db_path) if settings_db_path else None)
    except Exception:
        return {}, None, False
    pg_config: dict[str, Any] = {}
    provider: str | None = None
    try:
        prov_row = db.get(PROVIDER_SETTING_KEY)
        if prov_row is not None and prov_row.value:
            provider = str(prov_row.value).strip()
        pg_row = db.get(PG_CONFIG_SETTING_KEY)
        if pg_row and pg_row.value:
            raw = pg_row.value
            parsed: Any = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):
                pg_config = parsed
        return pg_config, provider, True
    except Exception:
        return {}, None, False
    finally:
        try:
            db.close()
        except Exception:
            pass


def _derive_field_sources(
    cfg: DatabaseConfig,
    *,
    env: dict[str, str],
    pg_settings: dict[str, Any],
    persisted_provider: str | None,
    settings_db_open: bool,
) -> dict[str, str]:
    """Re-derive the provenance of each connection field.

    Mirrors the documented ladder in ``load_database_config`` exactly:

    1. ``default`` — the field carries the value the resolver's own default
       constructor produces (``for_sqlite`` / ``for_postgres``) and no
       higher layer named it;
    2. ``persisted_settings`` — the value matches the persisted
       ``database.postgresql_config`` row (the persisted row is consulted
       whenever the audit seam does not apply, BUG-PGENV);
    3. ``env_overlay`` — the corresponding ``NSE_DATABASE__*`` key is set in
       the environment, and env wins per key;
    4. ``audit_seam`` — the field came from ``NEXUS_AUDIT_DB`` (audit domain
       only, no explicit env provider, no explicit sqlite path).

    ``UNKNOWN`` marks a field whose provenance could not be established —
    the settings DB could not be opened. It is never downgraded to
    ``default``: the whole point of the diagnostic is that "could not check"
    must not look like "nothing set it".
    """
    provider_env = (env.get(_ENV_PROVIDER_KEY) or "").strip()
    audit_seam_url = (env.get("NEXUS_AUDIT_DB") or "").strip()
    sqlite_path_env = (env.get("NSE_DATABASE__SQLITE_PATH") or "").strip()
    audit_seam_applies = (
        cfg.domain == "audit" and not provider_env and bool(audit_seam_url) and not sqlite_path_env
    )

    sources: dict[str, str] = {}

    # --- provider: the seam overrides the persisted provider; env beats all.
    if audit_seam_applies:
        sources["provider"] = SOURCE_SEAM
    elif provider_env:
        sources["provider"] = SOURCE_ENV
    elif persisted_provider:
        if settings_db_open:
            sources["provider"] = SOURCE_PERSISTED
        else:
            sources["provider"] = SOURCE_UNKNOWN
    else:
        # No provider row anywhere: the resolver's own default built the
        # config (the fresh-install case — an OPENABLE settings DB with no
        # rows). Only an unopenable DB is UNKNOWN.
        sources["provider"] = SOURCE_DEFAULT if settings_db_open else SOURCE_UNKNOWN

    # --- the connection fields the overlay can name.
    for name, env_key in _ENV_FIELD_KEYS.items():
        if env.get(env_key):
            sources[name] = SOURCE_ENV
            continue
        # 2b. the audit seam built the whole config from its own URL/path.
        if audit_seam_applies:
            sources[name] = (
                SOURCE_SEAM
                if audit_seam_url.lower().startswith("postgresql://")
                else SOURCE_DEFAULT
            )
            continue
        if not settings_db_open:
            sources[name] = SOURCE_UNKNOWN
            continue
        persisted_value = pg_settings.get(name)
        default_value = _default_value_for(cfg, name)
        if persisted_value is not None and str(persisted_value) != "":
            # The persisted pg_config row supplied this key (env did not name
            # it — BUG-PGENV: the persisted row fills every key env leaves
            # unset, even when the env branch shaped the config).
            sources[name] = SOURCE_PERSISTED
        elif default_value is not None and _values_equal(default_value, getattr(cfg, name, None)):
            sources[name] = SOURCE_DEFAULT
        else:
            # A value that is neither the default nor the persisted row: it
            # was set by a constructor the ladder does not name. Report it
            # honestly rather than guessing a layer.
            sources[name] = SOURCE_UNKNOWN

    return sources


def _default_value_for(cfg: DatabaseConfig, name: str) -> Any:
    """The value the resolver's default constructors assign to ``name``."""
    if cfg.is_postgresql:
        defaults = DatabaseConfig.for_postgres(domain=cfg.domain)
    else:
        defaults = DatabaseConfig.for_sqlite(cfg.domain)
    return getattr(defaults, name, None)


def _values_equal(a: Any, b: Any) -> bool:
    """Compare a persisted/env value with the resolved field, type-tolerant.

    Persisted JSON stores numbers as ints and everything else as strings;
    the resolved field is typed (e.g. ``port: int``). Compare on the string
    form so ``5432`` and ``"5432"`` are the same connection.
    """
    if a is None or b is None:
        return a is None and b is None
    return str(a).strip() == str(b).strip()


def _check_password_configured(
    cfg: DatabaseConfig, secret_root: Path | None = None
) -> tuple[bool | None, str, list[str]]:
    """Credential AVAILABILITY only — never the value.

    Returns ``(configured_or_None, secret_key, warnings)``. ``None`` means
    the store could not be consulted; the boolean is never a fallback.

    ``secret_root`` overrides the store's directory; when unset, the
    ``NEXUS_SECRETS_FILE`` env var (the test-isolation seam) is honored so a
    probe never has to touch the machine's real store.
    """
    key = (cfg.password_secret or PG_PASSWORD_SECRET_KEY or "").strip()
    if not cfg.is_postgresql:
        # SQLite carries no password: report not-applicable, not "missing".
        return False, key, []
    try:
        from nexus_scalp.settings.secret_store import SecureSecretStore

        root = secret_root
        if root is None:
            env_file = os.environ.get("NEXUS_SECRETS_FILE", "").strip()
            if env_file:
                root = Path(env_file).parent
        store = SecureSecretStore(root=root) if root is not None else SecureSecretStore()
        return bool(store.has_secret(key)), key, []
    except Exception as exc:  # pragma: no cover - store unavailable
        return None, key, [f"secret store unavailable: {type(exc).__name__}: {exc}"]


def _public_safe_url(cfg: DatabaseConfig) -> str:
    """Masked URL for correlation — never carries a real password.

    ``build_url()`` already emits the placeholder credential; the mask is
    applied defensively in case a caller injects a real one.
    """
    from nexus_scalp.database.config import mask_url_password

    return mask_url_password(cfg.build_url())


def effective_config_diagnostics(
    cfg: DatabaseConfig,
    *,
    settings_db_path: str | None = None,
    env: dict[str, str] | None = None,
    runtime_identity: dict[str, Any] | None = None,
    secret_root: Path | None = None,
) -> dict[str, Any]:
    """Report the effective PostgreSQL config + per-field source + identity.

    Parameters mirror the resolver so the diagnostic can be produced for the
    SAME config the caller is about to use (no second resolution, no
    connection, no driver): ``cfg`` is the value
    ``load_database_config(domain, settings_db_path=..., env=...)`` returned.

    Returns a dict with:

    * ``effective`` — the non-secret connection fields (provider, host,
      port, database, username, ssl_mode) plus a masked ``url``. NO
      password, NO secret material, anywhere in the payload.
    * ``field_sources`` — which layer set each field
      (``default`` / ``persisted_settings`` / ``env_overlay`` /
      ``audit_seam`` / ``UNKNOWN``).
    * ``config_identity`` — ``fingerprint`` (stable digest of the
      non-secret identity) + the hashed field list, for log correlation.
    * ``password_configured`` — boolean only (via
      ``SecureSecretStore().has_secret(cfg.password_secret or
      PG_PASSWORD_SECRET_KEY)``); ``None`` when the store is unavailable.
      ``secret_root`` overrides the store directory (else the
      ``NEXUS_SECRETS_FILE`` env var, the test-isolation seam).
    * ``runtime_identity`` — the resolved ``nexus_scalp`` module path + the
      process CWD.
    * ``stale_editable_install`` — ``detected`` + ``detail`` when the
      imported ``nexus_scalp`` is not the checkout the process CWD belongs
      to (the §B silent-provenance defect, made loud).
    * ``warnings`` — provenance problems (empty when every probe succeeded).

    Never raises: a settings DB that cannot be opened, a secret store that
    cannot be consulted, or an unresolvable module path each degrade to an
    explicit ``UNKNOWN`` entry in the payload. Never logs.
    """
    envd = env if env is not None else os.environ
    result = ConfigDiagnosticsResult()

    # -- effective, non-secret fields ------------------------------------
    result.effective = {name: getattr(cfg, name, None) for name in _CONNECTION_FIELDS}
    result.effective["url"] = _public_safe_url(cfg)

    # -- provenance ------------------------------------------------------
    pg_settings, persisted_provider, settings_openable = _read_persisted_settings(settings_db_path)
    result.field_sources = _derive_field_sources(
        cfg,
        env=envd,
        pg_settings=pg_settings,
        persisted_provider=persisted_provider,
        settings_db_open=settings_openable,
    )
    if not settings_openable:
        result.warnings.append(
            "settings DB could not be opened; field_sources for persisted "
            "layers are UNKNOWN (not default-filled)"
        )

    # -- identity --------------------------------------------------------
    result.fingerprint = _config_fingerprint(cfg)
    result.runtime_identity = (
        dict(runtime_identity) if runtime_identity is not None else _module_identity()
    )
    module_file = result.runtime_identity.get("nexus_scalp_module")
    cwd = result.runtime_identity.get("cwd") or ""
    stale, detail = _stale_editable_check(module_file, cwd)
    result.stale_editable_install = stale
    result.stale_editable_detail = detail

    # -- credential availability (boolean only) --------------------------
    configured, key, pw_warnings = _check_password_configured(cfg, secret_root)
    result.password_configured = configured
    result.password_secret_key = key
    result.warnings.extend(pw_warnings)

    return result.to_dict()


__all__ = [
    "SOURCE_AUDIT_SEAM",
    "SOURCE_DEFAULT",
    "SOURCE_ENV",
    "SOURCE_PERSISTED",
    "SOURCE_SEAM",
    "SOURCE_UNKNOWN",
    "ConfigDiagnosticsResult",
    "effective_config_diagnostics",
]
