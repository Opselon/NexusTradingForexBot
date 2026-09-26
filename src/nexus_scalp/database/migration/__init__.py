"""Provider-agnostic schema migration for NSE databases.

Mission: "switch to PostgreSQL when you want, back to SQLite when you want"
has to be a first-class, non-destructive operation. The schema is authored
once in the SQLite dialect (the domain's source of truth) and provisioned on
whatever provider a domain is bound to, idempotently.

Two entry points:
  * :func:`migrate_domain` — the whole flow for a domain: read its DDL,
    translate, apply, verify. Called at boot and by the CLI.
  * :func:`verify_domain_schema` — read-only reconciliation: which tables
    exist on the target vs which the domain expects. Never writes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from nexus_scalp.database.migration.pg_schema import apply_schema
from nexus_scalp.database.models import DatabaseDomain

logger = logging.getLogger(__name__)

#: The replay-based statement extractor for each provisioned domain. A domain
#: absent from this map has no authored DDL, and ``migrate_domain`` refuses to
#: provision it rather than silently reporting success with zero schema.
_DOMAIN_STATEMENTS: dict[str, str] = {
    DatabaseDomain.AUDIT.value: "audit_schema_statements",
    DatabaseDomain.NEWS.value: "news_schema_statements",
    DatabaseDomain.CANDLE_INTEL.value: "candle_intel_schema_statements",
    # The AI-provider decision ledger (ECOSYSTEM-001). Not a DatabaseDomain
    # enum member — it is owned by the ai_providers package, and its DDL is
    # authored there rather than replayed from a migration registry.
    "ai_provider_decisions": "ai_provider_decisions_schema_statements",
    # The operational tables model_lifecycle owns (learning_cycles /
    # learning_cycle_events / training_runs / model_comparisons). They live
    # in the AUDIT domain's database, so they are provisioned WITH it: a
    # fresh PostgreSQL install otherwise had no learning_cycles while the
    # migrated nexusdb did (the table-set divergence recorded in
    # tests/unit/test_pg_schema_convergence.py). Kept in its own module for
    # the same ownership reason as the decision ledger above.
    "model_lifecycle": "model_lifecycle_schema_statements",
    # The strategy factory's seven operational tables (generations, candidates,
    # failures, events, runs, provider_usage, loop_state). Owned by the
    # strategies package; its DDL is authored there (see the extractor below).
    "strategy_factory": "strategy_factory_schema_statements",
    # The operational tables the shadow / shadow70 / governance stores own
    # (DB-FABRIC-002). Not DatabaseDomain members: the DDL is authored in the
    # owning package (shadow.schema) because those tables are the stores'
    # own ensure_schema output, not an audit-domain replay. They live in the
    # ops_shadow domain's database and the live nexusdb audit schema
    # genuinely does NOT hold them, so a new domain registers for them.
    "ops_shadow": "ops_shadow_schema_statements",
    # The operational tables the hygiene state + quarantine stores own
    # (DB-FABRIC-002), same ownership contract as ops_shadow.
    "ops_hygiene": "ops_hygiene_schema_statements",
    # Lane D (2026-09-26): the four isolated-store domains. Same ownership
    # contract as the entries above — the DDL is authored in the owning
    # package (each store's declared schema constants), so the statements are
    # read from there verbatim and the store is deliberately NOT constructed
    # (its constructor opens a real database file / starts a worker thread).
    DatabaseDomain.MARKETPLACE.value: "marketplace_schema_statements",
    DatabaseDomain.MODELS.value: "models_schema_statements",
    DatabaseDomain.STRATEGIES.value: "strategies_schema_statements",
    DatabaseDomain.EXPERIMENTS.value: "experiments_schema_statements",
}


def sqlite_ddl_statements() -> list[str]:
    """The canonical audit-domain logical schema, in the SQLite dialect.

    This is deliberately the ONLY place the schema is authored for provider
    provisioning. It stays in the SQLite dialect because that is what the
    domain's own code emits and what the SQLite provider consumes directly;
    PostgreSQL gets a translated copy (see pg_schema.translate_ddl).

    The list is produced by replaying the whole schema chain into a disposable
    in-memory SQLite database (CHG-0067): the application bootstrap — every
    ``_create_*`` family plus the sibling bootstrap modules it delegates to —
    then the ordered migration registry, then the engine's ``schema_meta`` /
    ``schema_migrations`` tables. Tables, indexes (partial / DESC / unique), the
    runtime ``ALTER TABLE ADD COLUMN`` history and the meta tables all reach the
    provisioner that way; a source scan of triple-quoted CREATE literals can
    only ever see the first of those four (see migration.schema_snapshot).
    """
    from nexus_scalp.database.migration.schema_snapshot import audit_schema_statements

    return list(audit_schema_statements())


def _domain_statements(domain: str) -> list[str]:
    """The domain's complete logical schema, in the SQLite dialect.

    Raises :class:`NotImplementedError` for a domain with no authored DDL so the
    caller can distinguish "not provisioned yet" from a genuine failure.
    """
    from nexus_scalp.database.migration import schema_snapshot

    extractor_name = _DOMAIN_STATEMENTS.get(domain)
    if extractor_name is None:
        raise NotImplementedError(
            f"migration is not authored for domain {domain!r} "
            f"(known: {', '.join(sorted(_DOMAIN_STATEMENTS))})"
        )
    return list(getattr(schema_snapshot, extractor_name)())


def migrate_domain(
    domain: str,
    execute: Callable[[str], Any],
    *,
    stop_on_error: bool = False,
) -> dict[str, Any]:
    """Provision/upgrade a domain's schema on the bound provider.

    ``execute`` runs one translated SQL string. The result record is the
    migration's audit trail — applied count, per-statement errors — so a
    failed migration is visible rather than silently leaving a half-built
    schema behind.

    Covers every domain with authored DDL (audit, news, candle_intel — the
    replay in :mod:`migration.schema_snapshot` produces each list). A domain
    with no authored DDL raises :class:`NotImplementedError` instead of
    no-oping, so a caller can never mistake "not provisioned" for success.
    """
    statements = _domain_statements(domain)
    logger.info("[DB-MIGRATE] domain=%s statements=%d", domain, len(statements))
    return apply_schema(statements, execute, stop_on_error=stop_on_error)


def verify_domain_schema(domain: str, list_tables: Callable[[], list[str]]) -> dict[str, Any]:
    """Read-only reconciliation of a domain's expected vs physical schema.

    ``list_tables`` returns the table names present on the target. Returns the
    set the domain expects, the set found, and the difference — never writes.
    """
    import re

    expected: set[str] = set()
    for raw in _domain_statements(domain):
        m = re.search(r"(?i)CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", raw)
        if m:
            expected.add(m.group(1).strip())
    found = {t.strip() for t in (list_tables() or [])}
    return {
        "domain": domain,
        "expected_tables": sorted(expected),
        "expected_count": len(expected),
        "found_tables": sorted(found),
        "found_count": len(found),
        "missing": sorted(expected - found),
        "extra": sorted(found - expected),
    }
