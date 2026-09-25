"""Operational-data provider resolution for the DB fabric (DB-FABRIC-002).

The audit domain's operational stores — shadow, shadow70, governance,
incidents, hygiene state + quarantine, and the forensics read probes — were
each written against ``sqlite3.connect(audit_repo._db_path)``.  On a box the
operator switched to PostgreSQL (``nexus db-portability switch`` — the
persisted ``database.provider`` + ``database.postgresql_config`` settings)
every one of them kept opening the SQLite file: the audit store queued writes
into a repository whose ``_is_sqlite`` guard then refused them, and the
read probes opened a file that either did not exist or held stale data.

The fix mirrors the proven audit-domain pattern
(:func:`adapters.database.audit_repository.resolve_audit_db_url` /
:meth:`AuditRepository._build_pooled_write_backend`, and the AI-provider
decision ledger of PR #454):

  * resolve the ACTIVE provider through ``load_database_config('audit')`` and
    route through the fabric's pooled backends
    (``get_domain_backend`` / ``provision_domain``) when it is PostgreSQL;
  * SQLite stays the default and byte-identical in behaviour;
  * the test-isolation seams (``NEXUS_AUDIT_DB`` / ``NEXUS_*_DB``) keep
    winning over the persisted provider so a test never opens a live pool.

DOMAIN ROUTING
==============
Three domains carry the operational tables:

  * ``audit`` — the tables the live nexusdb schema ALREADY holds, created by
    the governed AUDIT-0005/AUDIT-0006 migration chain (``incidents``,
    ``incident_events``, ``incident_value_traces``, ``incident_quarantine``,
    ``model_promotion_audit``, ``model_rollback_audit``).  These route through
    the EXISTING ``audit`` domain pooled backend and NEVER need a new domain.
  * ``ops_shadow`` — the shadow / shadow70 / governance stores' tables
    (``shadow_runs``, ``shadow_decisions``, ``shadow_comparisons``,
    ``shadow_promotions``, ``shadow70_observations``, ``shadow70_events``,
    ``shadow70_feature_health``, ``shadow70_drift_alerts``,
    ``model_governance_events``, ``model_governance_state``,
    ``model_shadow_comparisons``, ``model_runtime_health``), which the live
    nexusdb schema genuinely does NOT hold.
  * ``ops_hygiene`` — the hygiene state + quarantine stores' tables
    (``hygiene_worker_state``, ``hygiene_run_history``, ``quarantine_items``,
    ``quarantine_events``), also absent from the live nexusdb schema.

The two new domains are deliberately separate: they are the ONLY tables
``provision_domain`` has to create, so an operator who already provisioned
``audit`` on PostgreSQL is never asked to re-bootstrap tables they already
have (and the shadow stores' lazy DDL never runs against a provider whose
schema the governed migration chain owns).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from nexus_scalp.database.config import (
    DatabaseConfigError,
    build_postgres_url,
    load_database_config,
)
from nexus_scalp.database.provider import default_sqlite_path
from nexus_scalp.settings.secret_store import SecureSecretStore

logger = __import__("nexus_scalp.observability.logging", fromlist=["get_logger"]).get_logger(
    "nexus_scalp.database.ops_provider"
)

#: The domain the operational stores share with the canonical audit tables.
#: Tables that already live in the nexusdb audit schema route through the
#: EXISTING ``audit`` pooled backend; no new domain is registered for them.
AUDIT_DOMAIN = "audit"

#: Domain for the shadow / shadow70 / governance operational tables, which the
#: live nexusdb audit schema does NOT hold. Genuinely needs a domain of its
#: own so ``provision_domain`` can bootstrap its tables.
OPS_SHADOW_DOMAIN = "ops_shadow"

#: Domain for the hygiene state + quarantine tables, also absent from the live
#: nexusdb audit schema.
OPS_HYGIENE_DOMAIN = "ops_hygiene"

#: Domains this module may provision. Every operational store resolves to one
#: of these (or to SQLite, which needs no domain at all).
OPS_DOMAINS: frozenset[str] = frozenset({AUDIT_DOMAIN, OPS_SHADOW_DOMAIN, OPS_HYGIENE_DOMAIN})

#: Tables the live nexusdb audit schema already owns. Their stores route
#: through the EXISTING ``audit`` domain pooled backend — registering a new
#: domain for them would either duplicate the schema or re-run DDL the
#: governed migration chain is authoritative for.
AUDIT_OWNED_TABLES: frozenset[str] = frozenset(
    {
        "incidents",
        "incident_events",
        "incident_value_traces",
        "incident_quarantine",
        "model_promotion_audit",
        "model_rollback_audit",
        # read-path tables the forensics/incident probes SELECT from
        "audit_ledger",
        "audit_orders",
        "audit_experiences",
        "audit_experience_outcomes",
        "audit_experience_corrections",
        "audit_broker_trades",
        "audit_broker_history_meta",
        "audit_signals",
        "audit_guard_telemetry",
        "experience_model_registry",
        "position_lifecycle_events",
        "research_runs",
        "strategy_registry",
        "strategy_intelligence_registry",
        "trade_autopsies",
        "schema_meta",
    }
)

#: The shadow / shadow70 / governance stores' tables — absent from the live
#: nexusdb audit schema, so they need their own provisioned domain.
SHADOW_TABLES: frozenset[str] = frozenset(
    {
        "shadow_runs",
        "shadow_decisions",
        "shadow_comparisons",
        "shadow_promotions",
        "shadow70_observations",
        "shadow70_events",
        "shadow70_feature_health",
        "shadow70_drift_alerts",
        "model_governance_events",
        "model_governance_state",
        "model_shadow_comparisons",
        "model_runtime_health",
    }
)

#: The hygiene state + quarantine stores' tables — also absent from the live
#: nexusdb audit schema.
HYGIENE_TABLES: frozenset[str] = frozenset(
    {
        "hygiene_worker_state",
        "hygiene_run_history",
        "quarantine_items",
        "quarantine_events",
    }
)

#: The operational tables the fabric must be able to provision, grouped by the
#: domain that owns them. Tables that already live in the audit schema are
#: deliberately NOT here: ``provision_domain('audit')`` creates them through
#: the governed migration chain, and re-creating them under a second domain
#: would leave two copies of the operator's incident evidence.
OPS_SCHEMA: dict[str, frozenset[str]] = {
    OPS_SHADOW_DOMAIN: SHADOW_TABLES,
    OPS_HYGIENE_DOMAIN: HYGIENE_TABLES,
}

#: Test-isolation seams: when any of these is set, the operational stores stay
#: pinned to SQLite even on a PostgreSQL-configured box (same contract as
#: ``NEXUS_AUDIT_DB`` pinning the audit domain, BUG-223). A test that points
#: one at a temp file never wants a live PostgreSQL pool.
_ISOLATION_SEAMS: tuple[str, ...] = (
    "NEXUS_AUDIT_DB",
    "NEXUS_OPS_SHADOW_DB",
    "NEXUS_OPS_HYGIENE_DB",
    "NEXUS_HYGIENE_STATE_DB",
    "NEXUS_QUARANTINE_DB",
)


def _isolation_seam_active(env: dict[str, str] | None = None) -> bool:
    """True when a test-isolation seam pins the operational stores to SQLite."""
    envd = env if env is not None else os.environ
    return any(envd.get(k, "").strip() for k in _ISOLATION_SEAMS)


def resolve_audit_db_url(db_url: str = "", config: Any = None) -> str:
    """Resolve the audit database URL for an operational store.

    Mirrors :func:`adapters.database.audit_repository.resolve_audit_db_url`
    exactly so the operational stores and ``AuditRepository`` land on the same
    database. Precedence:

      1. an explicit ``db_url`` / ``config`` (caller keeps full authority);
      2. the ``NEXUS_AUDIT_DB`` test-isolation seam (BUG-223) — SQLite, even
         on a PostgreSQL-configured box;
      3. the ACTIVE persisted provider (``load_database_config('audit')``);
      4. the SQLite default.
    """
    if config is not None:
        provider = getattr(getattr(config, "provider", None), "value", "sqlite")
        if str(provider).lower() in ("postgresql", "postgres", "pg"):
            return build_postgres_url(config, SecureSecretStore())
        return f"sqlite:///{getattr(config, 'sqlite_connect_path', '') or 'artifacts/audit.db'}"
    if not db_url:
        env_db = os.environ.get("NEXUS_AUDIT_DB", "").strip()
        if env_db:
            return f"sqlite:///{Path(env_db).as_posix()}"
        try:
            persisted = load_database_config("audit")
            if getattr(persisted, "is_postgresql", False):
                return build_postgres_url(persisted, SecureSecretStore())
        except Exception:  # pragma: no cover - settings DB unavailable
            pass
        return f"sqlite:///{Path(default_sqlite_path('audit')).as_posix()}"
    return db_url


def active_provider_is_postgresql(domain: str = "audit") -> bool:
    """Is the ACTIVE persisted provider PostgreSQL for the operational stores?

    The decision is made once, at the store's construction, and always through
    the persisted settings (the app-level provider switch).  The
    test-isolation seams win over the persisted provider, exactly as they do
    for the audit domain, so a test never opens a live PostgreSQL pool.
    """
    if _isolation_seam_active():
        return False
    try:
        return bool(load_database_config(domain).is_postgresql)
    except Exception:  # pragma: no cover - settings DB unavailable
        return False


def domain_for_table(table: str) -> str:
    """The fabric domain a table's store routes through.

    Tables the live nexusdb audit schema already owns (``incidents``,
    ``model_promotion_audit``, the ``audit_*`` / research / strategy probes)
    route through the EXISTING ``audit`` domain backend. Tables the audit
    schema genuinely does NOT hold (shadow, shadow70, governance events,
    hygiene state, quarantine) route through the domain that provisions them.
    """
    if table in AUDIT_OWNED_TABLES:
        return AUDIT_DOMAIN
    if table in SHADOW_TABLES:
        return OPS_SHADOW_DOMAIN
    if table in HYGIENE_TABLES:
        return OPS_HYGIENE_DOMAIN
    # An unknown operational table defaults to the audit domain: the read
    # probes that hit unlisted tables (audit_*/news_* cross-checks) are
    # reading audit-domain data, and the audit backend is already the
    # authoritative pool for it.
    return AUDIT_DOMAIN


def domain_for_tables(tables: frozenset[str]) -> str:
    """The single domain a store owning ``tables`` should route through.

    A store whose tables straddle the audit-owned and shadow-owned sets
    (``GovernanceStore`` writes ``model_promotion_audit`` alongside
    ``model_governance_events``) routes through the domain that owns the
    MAJORITY of its tables, with the audit domain winning a tie: the audit
    schema is the one already provisioned on the operator's box, so routing a
    straddling store there never asks for a fresh bootstrap when the
    operator's data already exists.
    """
    if not tables:
        return AUDIT_DOMAIN
    counts: dict[str, int] = {}
    for t in tables:
        counts[domain_for_table(t)] = counts.get(domain_for_table(t), 0) + 1
    audit_owned = counts.get(AUDIT_DOMAIN, 0)
    other = sum(v for k, v in counts.items() if k != AUDIT_DOMAIN)
    if other > audit_owned:
        # The store owns more non-audit tables: it needs a domain that
        # provisions them.
        for d in (OPS_SHADOW_DOMAIN, OPS_HYGIENE_DOMAIN):
            if counts.get(d, 0):
                return d
    return AUDIT_DOMAIN


def domain_dsn(domain: str) -> str:
    """The resolved PostgreSQL DSN for an operational domain.

    The DSN carries the injected secret (never logged; use
    :func:`mask_url_password` for any display surface).
    """
    cfg = load_database_config(domain)
    if not cfg.is_postgresql:
        raise DatabaseConfigError(
            f"domain {domain!r} is not configured for PostgreSQL (active provider is SQLite)"
        )
    return build_postgres_url(cfg, SecureSecretStore())


def resolve_pooled_backend(domain: str, dsn: str | None = None) -> Any:
    """Resolve (or bootstrap) the fabric's pooled WRITE backend for a domain.

    Auto-provisions the domain the first time a process touches a pooled
    provider — without this, the first store on a PostgreSQL box would raise
    ``no pooled write backend`` on its very first boot, since nothing else
    had registered the domain yet (the audit domain's own pattern, RTF-002).

    Reads must go through :func:`resolve_read_backend`: the pooled READ pool
    is a separate, read-only-by-construction pool, and a write-shaped backend
    is refused by the read guard outright.
    """
    from nexus_scalp.database.fabric import get_domain_backend, provision_domain

    backend = get_domain_backend(domain, readonly=False)
    if backend is not None:
        return backend
    if not dsn:
        dsn = domain_dsn(domain)
    return provision_domain(domain, dsn, min_size=1, max_size=4)


def resolve_read_backend(domain: str) -> Any:
    """The fabric's pooled READ backend for a domain, or ``None``.

    ``None`` is the observable-degradation signal the audit read guard
    documents: the caller returns its declared default rather than inventing
    an ad-hoc connection path.
    """
    from nexus_scalp.database.fabric import get_domain_backend

    return get_domain_backend(domain, readonly=True)


__all__ = [
    "AUDIT_DOMAIN",
    "AUDIT_OWNED_TABLES",
    "HYGIENE_TABLES",
    "OPS_DOMAINS",
    "OPS_HYGIENE_DOMAIN",
    "OPS_SCHEMA",
    "OPS_SHADOW_DOMAIN",
    "SHADOW_TABLES",
    "active_provider_is_postgresql",
    "domain_dsn",
    "domain_for_table",
    "domain_for_tables",
    "resolve_audit_db_url",
    "resolve_pooled_backend",
    "resolve_read_backend",
]
