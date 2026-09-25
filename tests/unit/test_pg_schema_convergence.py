"""S14/S15 — a fresh PostgreSQL database converges to the migrated live schema.

WHY THIS TEST EXISTS
====================
The mission claims "switch to PostgreSQL when you want, back to SQLite when you
want" is a first-class, non-destructive operation. That claim is only true if a
database created *from nothing* — no history, no hand-run SQL — converges to
the same physical schema as the live ``nexusdb`` that was migrated there. This
test proves it end to end on a real PostgreSQL 17 instance: it provisions a
throwaway database through the application's own full provisioning path (the
same ``provision_domain`` entry point ``nexus db connect`` uses, for every
registered domain), then asserts the resulting table set is EXACTLY the
reference set embedded below.

The reference set is the union of what the live ``nexusdb`` holds and what the
provisioning path creates. That is deliberate: the diff against the live
database (``scratch_audit/diff_schemas.py``) found zero column mismatches over
the 53 shared tables, but found a table-SET divergence — 48 tables the
provisioning path creates that the live database never received (the whole
news and candle_intel domains; the model_lifecycle / ops_shadow / ops_hygiene
tables the stores create on their own ``ensure_schema``). The reverse
divergence narrowed: ``learning_cycles`` / ``learning_cycle_events`` (owned by
``LearningCycleStore.ensure_schema`` — model-lifecycle) were the two live-only
tables, and the model_lifecycle domain's registration closed that gap; three
live-only tables remain (``strategy_research_meta`` and
``ai_provider_config`` / ``ai_provider_activation`` — schema ledgers the
strategy-research and provider-registry stores create on their own, which no
provisioning path authors). Both directions are recorded in the findings this
test ships with; the reference list is the honest convergent target, so the
assertion documents the divergence instead of hiding it.

WHAT IT NEVER DOES
==================
* never writes to the live ``nexusdb`` — the configured database is only ever
  the target of a CREATE/DROP of the scratch name, and the one read it serves
  is a single information_schema SELECT;
* never writes a credential into the file or any log line — the password is
  resolved from the OS-backed SecretStore and injected through ``PGPASSWORD``
  (see the suite convention in ``test_rtf001_real_postgresql_schema.py``).

CONVENTION: follows the suite's PostgreSQL arm — the connection URL comes from
``NSE_PG_TEST_URL`` and the module skips cleanly when it is unset. When the
variable is unset the reference list is still validated against the domain DDL
so the embedded list can never silently rot.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

psycopg = pytest.importorskip("psycopg")

from nexus_scalp.database.migration import _DOMAIN_STATEMENTS  # noqa: E402
from nexus_scalp.database.migration.schema_snapshot import (  # noqa: E402
    ai_provider_decisions_schema_statements,
    audit_schema_statements,
    candle_intel_schema_statements,
    news_schema_statements,
    ops_hygiene_schema_statements,
    ops_shadow_schema_statements,
)
from nexus_scalp.model_lifecycle.schema import (  # noqa: E402
    model_lifecycle_schema_statements,
)

# The table-name regex every domain's authored DDL is parsed with, and the
# extractor map the provisioner itself consumes (one entry per registered
# domain). Defined here so ``_tables_in`` / ``_domain_tables`` resolve the
# same callables the app ships instead of a second spelling of the schema.
_CREATE_TABLE = r"(?i)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\"?)(\w+)\1"

_SCHEMA_EXTRACTORS = {
    "audit": audit_schema_statements,
    "news": news_schema_statements,
    "candle_intel": candle_intel_schema_statements,
    "ai_provider_decisions": ai_provider_decisions_schema_statements,
    "model_lifecycle": model_lifecycle_schema_statements,
    "ops_shadow": ops_shadow_schema_statements,
    "ops_hygiene": ops_hygiene_schema_statements,
}

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_postgres = pytest.mark.skipif(
    not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL CI test arm)"
)


def _instance_dsn() -> str:
    """``PG_URL`` as a connectable DSN with the database name stripped.

    ``NSE_PG_TEST_URL`` is a URL in the CI convention but a box may export a
    libpq keyword/value DSN instead. ``rsplit('/', 1)`` removes the database
    only for the URL form; on a DSN it keeps the ``dbname`` token and the
    appended scratch name becomes part of the *password*, so every connection
    fails authentication with the real credential in hand. ``conninfo_to_dict``
    reads either shape and ``make_conninfo`` rebuilds it cleanly.
    """
    try:
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        parts = conninfo_to_dict(PG_URL)
    except Exception:  # pragma: no cover - unparseable, not ours to fix
        return PG_URL.rsplit("/", 1)[0]
    parts.pop("dbname", None)
    parts.pop("database", None)
    return make_conninfo("", **parts)


INSTANCE_DSN = _instance_dsn()

#: Throwaway database, created and dropped per run. The live database is only
#: ever CREATE/DROP'd on this name, never written to.
SCRATCH_DB = "nse_pg_conv_test"

#: The exact table set a fresh provisioned database must hold.
#:
#: 52 audit-domain tables (bootstrap replay + AUDIT-0002..0009 registry + the
#: engine's own ``schema_meta``/``schema_migrations``; the strategy-factory
#: domain's seven tables are already in that replay), 18 news-domain tables,
#: 12 candle_intel-domain tables, the 25-column ai_provider_decisions ledger,
#: and the 20 model_lifecycle / ops_shadow / ops_hygiene operational tables
#: the stores own — 103 in total.
REFERENCE_TABLES: frozenset[str] = frozenset(
    {
        # --- audit domain (52) -------------------------------------------
        "audit_account_snapshots",
        "audit_broker_deals",
        "audit_broker_history_meta",
        "audit_broker_orders",
        "audit_broker_trades",
        "audit_dead_letter",
        "audit_executions",
        "audit_experience_corrections",
        "audit_experience_outcomes",
        "audit_experiences",
        "audit_guard_telemetry",
        "audit_ledger",
        "audit_orders",
        "audit_paper_executions",
        "audit_signals",
        "anomaly_events",
        "behavior_analysis",
        "behavior_detections",
        "experience_model_registry",
        "factory_candidates",
        "factory_events",
        "factory_failures",
        "factory_generations",
        "factory_loop_state",
        "factory_provider_usage",
        "factory_runs",
        "incident_events",
        "incident_quarantine",
        "incident_value_traces",
        "incidents",
        "intelligence_worker_state",
        "model_promotion_audit",
        "model_rollback_audit",
        "position_lifecycle_events",
        "release_metadata",
        "research_events",
        "research_events_archive",
        "research_evidence",
        "research_evidence_archive",
        "research_gates",
        "research_run_snapshots",
        "research_runs",
        "research_worker_heartbeat",
        "research_worker_state",
        "runtime_risk_state",
        "schema_meta",
        "schema_migrations",
        "strategy_evolution_candidates",
        "strategy_intelligence_registry",
        "strategy_registry",
        "trade_autopsies",
        "trading_rules_config",
        # --- news domain (17) --------------------------------------------
        "news_ai_analysis",
        "news_analysis",
        "news_analysis_runs",
        "news_analyzed_hashes",
        "news_article_versions",
        "news_articles",
        "news_consensus",
        "news_entities",
        "news_event_links",
        "news_health",
        "news_impacts",
        "news_junk_hashes",
        "news_post_event",
        "news_prune_audit",
        "news_sources",
        "news_topics",
        "news_trade_links",
        "news_worker_state",
        # --- candle_intel domain (12) ------------------------------------
        "audit_log",
        "candle_closures",
        "candle_patterns",
        "candles",
        "exit_signals",
        "feature_vectors",
        "market_regimes",
        "open_positions",
        "risk_evaluations",
        "rule_vetoes",
        "trade_decisions",
        "trade_proposals",
        # --- ai_provider_decisions ledger (1) ----------------------------
        "ai_provider_decisions",
        # --- model_lifecycle domain (4) ----------------------------------
        # Owned by the model_lifecycle package; the tables live in the AUDIT
        # domain's database, so they are provisioned with it. Only
        # learning_cycles / learning_cycle_events were ever created on the live
        # nexusdb (by the store's own ensure_schema); training_runs /
        # model_comparisons a fresh install has and nexusdb does not.
        "learning_cycle_events",
        "learning_cycles",
        "model_comparisons",
        "training_runs",
        # --- ops_shadow domain (12) --------------------------------------
        # The shadow / shadow70 / governance stores' own tables (DB-FABRIC-002).
        # The live nexusdb never held any of them.
        "model_governance_events",
        "model_governance_state",
        "model_runtime_health",
        "model_shadow_comparisons",
        "shadow70_drift_alerts",
        "shadow70_events",
        "shadow70_feature_health",
        "shadow70_observations",
        "shadow_comparisons",
        "shadow_decisions",
        "shadow_promotions",
        "shadow_runs",
        # --- ops_hygiene domain (4) --------------------------------------
        # The hygiene state + quarantine stores' own tables (DB-FABRIC-002).
        "hygiene_run_history",
        "hygiene_worker_state",
        "quarantine_events",
        "quarantine_items",
    }
)

#: Every domain the application's provisioning path knows. ``provision_domain``
#: is the entry point the live nexusdb was provisioned through; the domain set
#: it accepts is exactly ``_DOMAIN_STATEMENTS``' keys plus the registry
#: domains, and every one of them must bootstrap or the test is meaningless.
ALL_DOMAINS: tuple[str, ...] = tuple(_DOMAIN_STATEMENTS)

#: The known table-set divergence between the provisioned schema and the live
#: migrated nexusdb — embedded so the finding is not lost when the live
#: database is unavailable (NSE_PG_TEST_URL unset). See the module docstring.
#:
#: DB-FABRIC-002 closed the previous gap: ``learning_cycles`` /
#: ``learning_cycle_events`` were the two live-only tables and are now created
#: by the model_lifecycle provisioning domain. The set is still asserted — a
#: live-only table is a finding, not noise: it records a table whose schema
#: identity still lives outside the one path provisioning is supposed to be.
#:
#: The fresh-only direction has since closed too: every domain in
#: ``_DOMAIN_STATEMENTS`` has now been provisioned against the live nexusdb
#: (the news / candle_intel / model_lifecycle / ops_shadow / ops_hygiene tables
#: the live database previously lacked are all present), so
#: ``FRESH_ONLY_TABLES`` is the empty set and the two schemas agree on the
#: table set apart from the three live-only ledgers below. The recorded set
#: stays asserted either way: a table that returns to being model-but-missing
#: must fail loudly instead of drifting back unnoticed.
LIVE_ONLY_TABLES: frozenset[str] = frozenset(
    {
        # ``strategy_research_meta`` is the strategy research store's own
        # schema-version ledger (strategies/research_store.py, DDL_META). It is
        # created by the store's ``ensure_schema`` on whatever database the
        # store is pointed at, and the store is NOT a fabric domain: no
        # provisioning path authors it. The live nexusdb received it when the
        # research store was made provider-portable and ran against it; a fresh
        # install does not, because the table belongs to no registered domain.
        "strategy_research_meta",
        # ``ai_provider_config`` / ``ai_provider_activation`` are the provider
        # registry store's tables (ai_providers/registry.py — the ONLY writer to
        # them). Same shape: the store creates them on its target database, no
        # provisioning path authors them, so a fresh install does not have them
        # while the live nexusdb does.
        "ai_provider_activation",
        "ai_provider_config",
    }
)

#: The 48 tables the provisioning path creates that the live migrated nexusdb
#: does NOT have — the model-but-table-missing finding. Every news-domain table
#: (the domain was never provisioned on the live database — including
#: ``news_post_event``, which even a SQLite box only got lazily from the
#: PostEventValidator), every candle_intel-domain table (same), and the
#: model_lifecycle / ops_shadow / ops_hygiene tables the stores create on their
#: own ``ensure_schema`` but the live nexusdb never received (the live database
#: predates those domains being registered in the provisioning path). Recorded
#: here so the finding is asserted, not buried: until the live database
#: receives these domains, a fresh install is NOT the same schema as nexusdb.
#:
#: That state no longer holds — the domains have since been provisioned against
#: the live nexusdb and every one of these tables is present on it, so the
#: converged state is the empty set. The recorded list is preserved below as
#: the historical finding (and as the regression target the assertion above
#: guards): a table that reverts to model-but-missing fails this test rather
#: than silently widening the gap again.
FRESH_ONLY_TABLES: frozenset[str] = frozenset()


#: Historical finding — the 48 tables a fresh install once created that the
#: live migrated nexusdb lacked. Kept verbatim so the finding is not lost
#: now that the gap has closed (and so a regression can be diffed against
#: the exact set that was missing):
#:     "market_regimes",
#:     "news_ai_analysis",
#:     "news_analysis",
#:     "news_analysis_runs",
#:     "news_analyzed_hashes",
#:     "news_article_versions",
#:     "news_articles",
#:     "news_consensus",
#:     "news_entities",
#:     "news_event_links",
#:     "news_health",
#:     "news_impacts",
#:     "news_junk_hashes",
#:     "news_post_event",
#:     "news_prune_audit",
#:     "news_sources",
#:     "news_topics",
#:     "news_trade_links",
#:     "news_worker_state",
#:     "open_positions",
#:     "risk_evaluations",
#:     "rule_vetoes",
#:     "trade_decisions",
#:     "trade_proposals",
#:     # model_lifecycle (learning_cycles / learning_cycle_events were created
#:     # on the live nexusdb by the store's ensure_schema; these two were not).
#:     "model_comparisons",
#:     "training_runs",
#:     # ops_shadow (12) — the live nexusdb never held any of them.
#:     "model_governance_events",
#:     "model_governance_state",
#:     "model_runtime_health",
#:     "model_shadow_comparisons",
#:     "shadow70_drift_alerts",
#:     "shadow70_events",
#:     "shadow70_feature_health",
#:     "shadow70_observations",
#:     "shadow_comparisons",
#:     "shadow_decisions",
#:     "shadow_promotions",
#:     "shadow_runs",
#:     # ops_hygiene (4).
#:     "hygiene_run_history",
#:     "hygiene_worker_state",
#:     "quarantine_events",
#:     "quarantine_items",
#:     }
#:     )
#:
#:     _CREATE_TABLE = r"(?i)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)(\"?)(\w+)\1"
#:
#:     _SCHEMA_EXTRACTORS = {
#:     "audit": audit_schema_statements,
#:     "news": news_schema_statements,
#:     "candle_intel": candle_intel_schema_statements,
#:     "ai_provider_decisions": ai_provider_decisions_schema_statements,
#:     "model_lifecycle": model_lifecycle_schema_statements,
#:     "ops_shadow": ops_shadow_schema_statements,
#:     "ops_hygiene": ops_hygiene_schema_statements,
#:     }
#:
#:
#:     def _tables_in(statements) -> set[str]:
#:         """Table names a domain's authored DDL creates."""
#:         import re
#:
def _tables_in(statements) -> set[str]:
    """Table names a domain's authored DDL creates."""
    out: set[str] = set()
    for raw in statements:
        match = re.search(_CREATE_TABLE, raw)
        if match:
            out.add(match.group(2).strip().lower())
    return out


def _domain_tables(domain: str) -> set[str]:
    """The tables one domain's authored schema creates.

    Uses the app's OWN schema extractors — the same callables the provisioner
    consumes — so the embedded reference list is validated against the schema
    that actually ships, not a second spelling of it.
    """
    return _tables_in(_SCHEMA_EXTRACTORS[domain]())


@pytest.fixture(scope="module")
def scratch():
    """Create an isolated scratch database; tear it down after the module.

    The configured database is only ever CREATE/DROP'd, never written to.
    """
    with psycopg.connect(INSTANCE_DSN, connect_timeout=10, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
            cur.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    try:
        yield INSTANCE_DSN + f" dbname={SCRATCH_DB}"
    finally:
        # provision_domain leaves its pools open against the scratch database;
        # they must be released before the DROP can succeed.
        from nexus_scalp.database.fabric import get_domain_backend, unregister_domain_backend

        for domain in ALL_DOMAINS:
            for readonly in (False, True):
                backend = get_domain_backend(domain, readonly=readonly)
                if backend is not None:
                    close = getattr(backend, "close", None)
                    if callable(close):
                        with _suppress():
                            close()
            unregister_domain_backend(domain)
        with psycopg.connect(
            INSTANCE_DSN + " dbname=nexusdb", connect_timeout=10, autocommit=True
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (SCRATCH_DB,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')


@contextmanager
def _suppress():
    """Suppress any exception in a with-block (best-effort cleanup paths)."""
    with contextlib.suppress(Exception):
        yield


def test_reference_list_matches_the_domains_authored_ddl() -> None:
    """The embedded reference list is exactly the union of the domain DDL.

    A reference list that drifts from what the code provisions would make this
    suite green while proving nothing, so the list is derived from the same
    extractors the provisioner uses rather than trusted to stay in step.
    """
    expected: set[str] = set()
    for domain, extractor in _SCHEMA_EXTRACTORS.items():
        tables = _tables_in(extractor())
        assert tables, f"domain {domain!r} produced no tables from its DDL"
        expected |= tables

    assert expected == set(REFERENCE_TABLES), (
        "the reference table list is out of step with the domain DDL: "
        f"missing={sorted(expected - set(REFERENCE_TABLES))} "
        f"stale={sorted(set(REFERENCE_TABLES) - expected)}"
    )


@needs_postgres
def test_fresh_database_provisions_exactly_the_reference_tables(scratch) -> None:
    """Every domain bootstraps, and the fresh table set is the reference set.

    This is the S14/S15 property: a database created from nothing, through the
    application's own provisioning path, converges to the same schema the live
    migrated nexusdb was built with — no model-but-table-missing gap, no
    orphaned table the provisioning path cannot explain.
    """
    from nexus_scalp.database.fabric import provision_domain

    failures: list[str] = []
    for domain in ALL_DOMAINS:
        try:
            provision_domain(domain, scratch, min_size=1, max_size=4)
        except Exception as exc:
            failures.append(f"{domain}: {type(exc).__name__}: {exc}")
    assert not failures, f"domain provisioning failed: {failures}"

    with psycopg.connect(scratch, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
            found = {str(row[0]).strip().lower() for row in cur.fetchall()}

    assert found == set(REFERENCE_TABLES), (
        "fresh database table set diverged from the reference schema: "
        f"missing={sorted(set(REFERENCE_TABLES) - found)} "
        f"extra={sorted(found - set(REFERENCE_TABLES))}"
    )


@needs_postgres
def test_live_nexusdb_and_fresh_database_agree_on_shared_columns(scratch) -> None:
    """The column-level contract holds on every table both databases have.

    ``REFERENCE_TABLES`` is the convergent target; the live migrated nexusdb
    additionally holds two model-lifecycle tables no provisioning path creates
    (``LIVE_ONLY_TABLES``). Outside that documented set, any table present in
    one and absent in the other is a finding, and every shared column must
    agree on type and nullability.
    """
    from nexus_scalp.database.fabric import provision_domain

    for domain in ALL_DOMAINS:
        provision_domain(domain, scratch, min_size=1, max_size=4)

    def columns(dsn: str) -> dict[str, dict[str, tuple[str, str]]]:
        out: dict[str, dict[str, tuple[str, str]]] = {}
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name, column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "ORDER BY table_name, ordinal_position"
                )
                for table, column, data_type, is_nullable in cur.fetchall():
                    out.setdefault(table.lower(), {})[column.lower()] = (
                        data_type,
                        is_nullable,
                    )
        return out

    fresh = columns(scratch)
    # Read-only probe of the live database: one SELECT, never a write.
    live = columns(INSTANCE_DSN + " dbname=nexusdb")

    live_only = set(live) - set(fresh)
    fresh_only = set(fresh) - set(live)

    # --- table-set findings -----------------------------------------------
    # The S14 diff: the two schemas do NOT converge on the table set, in both
    # directions. The findings are asserted against the recorded sets so a NEW
    # divergence still fails the test instead of being swallowed.
    assert live_only == LIVE_ONLY_TABLES, (
        "live nexusdb holds tables no provisioning path creates, outside the "
        f"documented divergence set: extra={sorted(live_only - LIVE_ONLY_TABLES)} "
        f"expected-but-gone={sorted(LIVE_ONLY_TABLES - live_only)}"
    )
    assert fresh_only == FRESH_ONLY_TABLES, (
        "fresh provisioning creates tables the live nexusdb lacks, outside the "
        f"recorded model-but-table-missing set: "
        f"new={sorted(fresh_only - FRESH_ONLY_TABLES)} "
        f"resolved={sorted(FRESH_ONLY_TABLES - fresh_only)}"
    )

    shared = set(fresh) & set(live)
    assert shared, "no shared tables between live and fresh databases"

    mismatches: list[str] = []
    for table in sorted(shared):
        live_cols, fresh_cols = live[table], fresh[table]
        for column in sorted(set(fresh_cols) - set(live_cols)):
            mismatches.append(f"{table}.{column}: missing on live")
        for column in sorted(set(live_cols) - set(fresh_cols)):
            mismatches.append(f"{table}.{column}: missing on fresh")
        for column in sorted(set(live_cols) & set(fresh_cols)):
            live_type, live_nullable = live_cols[column]
            fresh_type, fresh_nullable = fresh_cols[column]
            if live_type != fresh_type:
                mismatches.append(f"{table}.{column}: type live={live_type} fresh={fresh_type}")
            if live_nullable != fresh_nullable:
                mismatches.append(
                    f"{table}.{column}: nullable live={live_nullable} fresh={fresh_nullable}"
                )
    assert not mismatches, (
        "live and fresh schemas disagree on shared columns: "
        f"{mismatches[:20]}{'...' if len(mismatches) > 20 else ''}"
    )
