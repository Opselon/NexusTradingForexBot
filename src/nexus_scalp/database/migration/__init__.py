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


def additive_columns_statements() -> list[str]:
    """The additive column-heal contract, as provider-agnostic SQL.

    ``AuditRepository._create_sqlite_tables`` heals its own additive columns
    through ``_add_column_if_missing`` (PRAGMA-gated ``ALTER TABLE ... ADD
    COLUMN``). Those calls are FUNCTION CALLS on the connection, not
    triple-quoted conn.execute CREATE literals — so the DDL
    scan in :func:`sqlite_ddl_statements` (the only source the
    provider-agnostic migration reads) structurally cannot see them. A
    PostgreSQL domain was therefore provisioned with the baseline skeleton
    only: the runtime INSERT failed on every ``signal_dedup_key`` /
    ``account_source`` / raw-probability write and the audit worker
    dead-lettered every row (RTF-001).

    This closes the loop with the SAME declarative contract the SQLite side
    heals from — ``APP_REQUIRED_COLUMNS`` — so both providers converge on one
    schema and there is no second column list to drift.

    Only the columns the baseline DDL does NOT already declare are emitted:
    re-adding a present column is a hard error on SQLite (``duplicate column
    name``) and would make this function depend on call order rather than on
    the schema itself. The baseline set is derived from the same scanned
    CREATEs the migration applies, so the two halves can never disagree about
    which columns still need healing.
    """
    from nexus_scalp.database.app_columns import APP_REQUIRED_COLUMNS

    declared = _baseline_declared_columns()
    statements: list[str] = []
    for table, columns in APP_REQUIRED_COLUMNS.items():
        present = declared.get(table, set())
        for col_name, col_ddl in columns:
            if col_name in present:
                continue  # the baseline CREATE already owns it
            # Authored in the SQLite dialect (the domain's source of truth).
            # SQLite has no ``ADD COLUMN IF NOT EXISTS``; the PG translator
            # inserts it so the provider-agnostic migration stays idempotent
            # on PostgreSQL (see translate_ddl).
            statements.append(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl};")
    return statements


def _baseline_declared_columns() -> dict[str, set[str]]:
    """Column names each baseline CREATE TABLE declares (table -> columns).

    Derived from the same DDL :func:`migrate_domain` applies, so the additive
    heals are relative to the schema that actually got created — not to a
    hand-kept list that can drift from it.
    """
    import re

    declared: dict[str, set[str]] = {}
    for raw in sqlite_ddl_statements():
        m = re.match(r"(?i)CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\(", raw)
        if not m:
            continue
        table = m.group(1)
        body = raw[raw.find("(") + 1 :]
        cols: set[str] = set()
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith(
                ("PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT", "--")
            ):
                continue
            cm = re.match(r'^["`]?(\w+)["`]?\s+\w', line)
            if cm:
                cols.add(cm.group(1))
        declared[table] = cols
    return declared


def unique_index_statements() -> list[str]:
    """The UNIQUE-index contract backing every ``ON CONFLICT(col, ...)``.

    Mirrors ``AuditRepository._ensure_unique_constraint_heal`` (BUG-276) for
    providers that do not run the SQLite bootstrap: the application INSERTs
    declare ``ON CONFLICT(<target>)`` clauses, and PostgreSQL refuses them
    unless a UNIQUE constraint exists on exactly those columns. Every entry
    is idempotent (``IF NOT EXISTS``).
    """
    from nexus_scalp.database.app_columns import APP_UNIQUE_TARGETS

    statements: list[str] = []
    for table, targets in APP_UNIQUE_TARGETS.items():
        for cols in targets:
            joined = ", ".join(cols)
            idx_name = f"idx_{table}_app_" + "_".join(cols)
            statements.append(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {idx_name} ON {table}({joined});"
            )
    return statements


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

    import re

    # RTF-001: the additive column heals and the UNIQUE-index targets are
    # function calls in AuditRepository, invisible to the DDL scan. The
    # provider-agnostic migration must apply the SAME declarative contract
    # the SQLite path heals from, or a non-SQLite domain only ever gets the
    # baseline skeleton and every producer INSERT fails.
    #
    # Ordering is a hard dependency chain, not a convenience: a table must
    # exist before ALTERing it, a column must exist before an index can
    # reference it. The scan returns source order, where the baseline index
    # definitions can precede the additive column their predicate needs, so
    # the phases are regrouped explicitly:
    #
    #     baseline CREATE TABLE
    #         -> additive ADD COLUMN (references tables)
    #             -> every CREATE INDEX (references columns)
    baseline = sqlite_ddl_statements()
    base_tables = [s for s in baseline if re.match(r"(?i)^\s*CREATE\s+TABLE", s)]
    base_other = [s for s in baseline if not re.match(r"(?i)^\s*CREATE\s+TABLE", s)]
    statements = (
        base_tables
        + additive_columns_statements()
        + base_other
        + unique_index_statements()
    )
    logger.info("[DB-MIGRATE] domain=%s statements=%d", domain, len(statements))
    return apply_schema(statements, execute, stop_on_error=stop_on_error)


def verify_domain_schema(
    domain: str,
    list_tables: Callable[[], list[str]],
    *,
    list_columns: Callable[[str], list[str]] | None = None,
) -> dict[str, Any]:
    """Read-only reconciliation of a domain's expected vs physical schema.

    ``list_tables`` returns the table names present on the target and
    ``list_columns(table)`` the column names of one table (both providers
    implement these against their own catalogs). Never writes.

    Beyond tables, this verifies the additive column contract
    (``APP_REQUIRED_COLUMNS``): a table that exists without a required column
    is a drift the runtime will hit on its next INSERT, and reporting READY
    over it is the false-ready state RTF-001 shipped with. Column data is
    reported separately from the table diff so a caller can fail loudly on
    either without losing the other.
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

    missing_columns: list[dict[str, str]] = []
    if list_columns is not None:
        try:
            from nexus_scalp.database.app_columns import APP_REQUIRED_COLUMNS

            for table, columns in APP_REQUIRED_COLUMNS.items():
                if table not in found:
                    continue  # already reported by the table diff
                present = {c.strip() for c in (list_columns(table) or [])}
                for col_name, _ddl in columns:
                    if col_name not in present:
                        missing_columns.append({"table": table, "column": col_name})
        except ImportError:  # pragma: no cover - app_columns is stdlib-only
            pass

    return {
        "domain": domain,
        "expected_tables": sorted(expected),
        "expected_count": len(expected),
        "found_tables": sorted(found),
        "found_count": len(found),
        "missing": sorted(expected - found),
        "extra": sorted(found - expected),
        "missing_columns": missing_columns,
        "missing_columns_count": len(missing_columns),
    }
