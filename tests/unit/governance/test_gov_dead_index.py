"""idx_gov_events_model: a write-cost index with no read benefit, removed.

Rescan finding after the shadow/news payload reclaims: on the live PostgreSQL
ledger ``idx_gov_events_model`` (5.5 MB) had **0 scans** against a
``model_governance_events`` table of 59k+ rows and growing — the
highest-write-frequency table in the database, written from 11 hot-path
``record_event()`` call sites (champion sync, the governance engine, the
shadow runtime, transactions, verification).

Every event write paid to maintain an index that nothing ever read. The only
production read of the table is ``operational_digest``'s ``list_events(limit=
50)`` with no ``model_id`` filter, which ``idx_gov_events_ts`` serves.

These tests pin the decision so the index does not silently come back, and pin
the rule for the rest of the DB: an index with no consumer is a write cost and
must be justified by a real query.
"""

from __future__ import annotations

import pytest

from nexus_scalp.governance.store import GovernanceStore
from nexus_scalp.shadow.schema import ops_shadow_schema_statements


@pytest.fixture(scope="module")
def domain_statements() -> tuple[str, ...]:
    return ops_shadow_schema_statements()


# ---------------------------------------------------------------------------
# The index is gone from both schema sources
# ---------------------------------------------------------------------------


def test_domain_schema_no_longer_declares_the_dead_index(domain_statements):
    """The ops_shadow domain (the PostgreSQL provisioning path) drops it."""
    assert not any("idx_gov_events_model" in stmt for stmt in domain_statements), (
        "idx_gov_events_model must not be provisioned: 0 scans on the live ledger"
    )


def test_governance_store_schema_no_longer_declares_the_dead_index():
    """The SQLite bootstrap path drops it too, so both providers converge."""
    src = GovernanceStore.ensure_schema.__doc__ or ""
    # The index name must not appear anywhere in the module's index list.
    import inspect

    from nexus_scalp import governance as gov_module

    src_text = inspect.getsource(gov_module.store)
    occurrences = [line for line in src_text.splitlines() if "idx_gov_events_model" in line]
    # The only acceptable mention is the removal comment explaining WHY.
    assert all("REMOVED" in line for line in occurrences), (
        "idx_gov_events_model must only appear as a documented removal, never as a CREATE INDEX"
    )
    assert src is not None


# ---------------------------------------------------------------------------
# The index the production read actually needs is still there
# ---------------------------------------------------------------------------


def test_the_serving_index_is_retained(domain_statements):
    """idx_gov_events_ts serves the only production read; it must survive."""
    assert any("idx_gov_events_ts" in stmt for stmt in domain_statements), (
        "removing the dead index must not remove the one the digest query uses"
    )


def test_model_governance_state_index_is_retained(domain_statements):
    """The state lookup index is a different index and must be untouched."""
    assert any("idx_gov_state_model" in stmt for stmt in domain_statements)


# ---------------------------------------------------------------------------
# The read contract is unchanged
# ---------------------------------------------------------------------------


def test_list_events_still_supports_a_model_id_filter():
    """The query path is retained: a future caller can still filter by model_id.

    Removing the index does not remove the capability — it removes the
    *eager* cost of maintaining it. The clause is still built when a caller
    passes model_id, so a real consumer can re-add the index and get the
    same plan back.
    """
    import inspect

    from nexus_scalp.governance import store as gov_store

    src = inspect.getsource(gov_store)
    # The parameter and the WHERE clause builder both survive.
    assert "model_id" in inspect.signature(gov_store.GovernanceStore.list_events).parameters
    assert 'clauses.append("model_id = ?")' in src


# ---------------------------------------------------------------------------
# The general rule: an index in the domain schema must have a query
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "index_name",
    [
        "idx_gov_events_ts",
        "idx_gov_state_model",
        "idx_gov_comp_ts",
        "idx_shadow_runs_status",
    ],
)
def test_every_retained_index_is_still_declared(domain_statements, index_name):
    """Sanity: the removal touched exactly one index and no others."""
    assert any(index_name in stmt for stmt in domain_statements), (
        f"{index_name} disappeared — the removal was too broad"
    )


def test_exactly_one_index_was_removed(domain_statements):
    """The diff removes idx_gov_events_model and nothing else.

    A broader removal would be a different (riskier) change; this keeps the
    blast radius auditable.
    """
    import inspect

    from nexus_scalp.shadow import schema as schema_module

    src = inspect.getsource(schema_module)
    removed = [line.strip() for line in src.splitlines() if "idx_gov_events_model" in line]
    assert len(removed) == 1, f"expected exactly one removal note, got {removed}"
    assert "REMOVED" in removed[0]
