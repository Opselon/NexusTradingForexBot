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

logger = logging.getLogger(__name__)


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
    """
    if domain != "audit":
        # Only the audit domain has authored DDL today. Refusing (rather than
        # no-oping) keeps "migrate" honest as other domains come online.
        raise NotImplementedError(f"migration is authored for the audit domain, not {domain!r}")

    statements = sqlite_ddl_statements()
    logger.info("[DB-MIGRATE] domain=%s statements=%d", domain, len(statements))
    return apply_schema(statements, execute, stop_on_error=stop_on_error)


def verify_domain_schema(domain: str, list_tables: Callable[[], list[str]]) -> dict[str, Any]:
    """Read-only reconciliation of a domain's expected vs physical schema.

    ``list_tables`` returns the table names present on the target. Returns the
    set the domain expects, the set found, and the difference — never writes.
    """
    if domain != "audit":
        raise NotImplementedError(
            f"schema verification is authored for the audit domain, not {domain!r}"
        )

    import re

    expected: set[str] = set()
    for raw in sqlite_ddl_statements():
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
