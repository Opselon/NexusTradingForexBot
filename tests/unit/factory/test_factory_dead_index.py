"""idx_factory_cand_hash: a hash index whose key is never a lookup key, removed.

Continuation of the dead-index sweep. ``idx_factory_cand_hash``
(``factory_candidates(definition_hash)``) had **0 scans** on the live
PostgreSQL ledger against 4,108 rows.

Unlike the governance index (removed in #578, where the *query* existed but no
caller used it), this one is dead at a deeper level: ``definition_hash`` is
never a lookup key anywhere in the codebase. It is written (marketplace packs,
evidence snapshots) and read back as a plain column value, but no query ever
does ``WHERE definition_hash = ?`` — a grep for that pattern across the whole
tree returns zero matches.

An index on a column that is never filtered can never be used by the planner,
so its entire cost is write amplification on every ``factory_candidates``
insert.
"""

from __future__ import annotations

import inspect

import pytest

from nexus_scalp.strategies.factory import store as factory_store

# ---------------------------------------------------------------------------
# The index is gone from both schema sources
# ---------------------------------------------------------------------------


def test_audit_repository_no_longer_creates_the_hash_index():
    """The research-tables bootstrap path drops it."""
    from nexus_scalp.adapters.database import audit_repository as repo_mod

    src = inspect.getsource(repo_mod)
    occurrences = [ln.strip() for ln in src.splitlines() if "idx_factory_cand_hash" in ln]
    # The only acceptable mention is the removal note explaining WHY.
    assert occurrences, "the removal rationale comment should be present"
    assert all("REMOVED" in ln for ln in occurrences), (
        "idx_factory_cand_hash must only appear as a documented removal, never as a CREATE INDEX"
    )


def test_factory_store_schema_no_longer_declares_the_hash_index():
    """The factory store's own schema string drops it too.

    The index was declared in TWO places — the audit repository's research
    tables and the factory store's schema string. Removing only one would
    leave the other re-creating it, so both must be checked.
    """
    src = inspect.getsource(factory_store)
    occurrences = [ln.strip() for ln in src.splitlines() if "idx_factory_cand_hash" in ln]
    assert occurrences, "the removal rationale comment should be present"
    assert all("REMOVED" in ln for ln in occurrences), (
        "idx_factory_cand_hash must only appear as a documented removal"
    )


# ---------------------------------------------------------------------------
# The index the factory hot path actually needs is retained
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "index_name",
    [
        "idx_factory_cand_gen",
        "idx_factory_fail_gen",
        "idx_factory_events_gen",
    ],
)
def test_every_retained_factory_index_survives(index_name):
    """The removal touched exactly one index and no others."""
    from nexus_scalp.adapters.database import audit_repository as repo_mod

    src = inspect.getsource(repo_mod)
    assert index_name in src, f"{index_name} disappeared — the removal was too broad"


def test_the_generation_index_still_covers_the_hot_read():
    """The real factory read is generation-scoped; its index must survive.

    ``factory_store`` reads candidates by ``generation_id`` (with
    ``population_index`` ordering), which ``idx_factory_cand_gen`` serves.
    Removing the hash index must not touch that.
    """
    src = inspect.getsource(factory_store)
    assert "idx_factory_cand_gen ON factory_candidates(generation_id, population_index)" in src, (
        "the generation index the hot read uses must be retained"
    )


# ---------------------------------------------------------------------------
# The decision is evidence-based, not incidental
# ---------------------------------------------------------------------------


def test_definition_hash_is_not_a_lookup_key_anywhere():
    """The column the index covered is never used in a WHERE clause.

    This is the property that makes removal safe rather than merely
    convenient: if a hash-lookup query ever appears, the index becomes
    load-bearing again and should be re-added. Pinning it here means a future
    PR that adds ``WHERE definition_hash = ?`` fails this test and is forced
    to restore the index instead of silently regressing the read.
    """
    import pathlib

    src_root = pathlib.Path(factory_store.__file__).parent.parent
    hits = []
    for path in src_root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for ln in text.splitlines():
            stripped = ln.strip()
            if stripped.startswith(("#", "--", '"""', "'")):
                continue
            if "definition_hash" in ln and ("WHERE" in ln.upper() or "= ?" in ln):
                # Skip the evidence-schema field assignment, not a filter.
                if "strategy_definition_hash" in ln and "WHERE" not in ln.upper():
                    continue
                hits.append(f"{path.name}: {stripped}")
    # The evidence schema stores strategy_definition_hash as a VALUE
    # (``strategy_definition_hash=...``), which is not a lookup. Those are the
    # only acceptable matches.
    real = [h for h in hits if "WHERE" in h.upper()]
    assert not real, (
        "definition_hash is now used in a WHERE clause — the removed index is "
        f"load-bearing again, re-add idx_factory_cand_hash. Found: {real[:3]}"
    )


def test_definition_hash_is_still_written():
    """Removing the index does not remove the column or its writers.

    The value is still recorded (marketplace packs, evidence snapshots) — only
    the index on it is gone.
    """
    src = inspect.getsource(factory_store)
    assert "definition_hash" in src, "the column itself must be untouched"
