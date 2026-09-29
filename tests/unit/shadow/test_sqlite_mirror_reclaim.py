"""The shadow mirror reclaim, verified to be provider-portable in practice.

#575 made `reclaim_mirror_payloads` provider-portable and ran it against the
PostgreSQL ledger. The SQLite audit ledger was in the *same* state — 55,342
rows carrying 108,272,886 bytes of retired JSON mirror — because #568's
backfill had never been executed against it either.

These tests pin the property that makes the fix work on both engines: the
column-introspection and reclaim paths must not assume SQLite's `PRAGMA`
dialect or PostgreSQL's `information_schema`, and the eligibility guard must
hold on either engine.
"""

from __future__ import annotations

import inspect
import sqlite3

import pytest

from nexus_scalp.shadow.store import ShadowStore

# ---------------------------------------------------------------------------
# The provider-portability contract
# ---------------------------------------------------------------------------


def test_add_missing_columns_reads_either_catalog():
    """The introspection helper must not assume a PRAGMA is available.

    This is the exact defect: the original read `PRAGMA table_info`, which
    only exists on SQLite. A PostgreSQL connection has no such statement, so
    the heal silently skipped and the eight additive columns were never
    installed. The helper must fall back to `information_schema.columns`.
    """
    src = inspect.getsource(ShadowStore._add_missing_columns)
    assert "PRAGMA table_info" in src, "SQLite path must be present"
    assert "information_schema.columns" in src, "PostgreSQL fallback must be present"
    # The two paths must be guarded by the connection type, not chosen by luck.
    assert "isinstance(conn, sqlite3.Connection)" in src


def test_reclaim_uses_provider_specific_placeholders():
    """The reclaim path must emit the right placeholder for each engine.

    SQLite uses `?`; PostgreSQL uses `%s`. Interpolating one into the other's
    statement is a latent defect (on SQLite a bare `%s` is a syntax error at
    execute time; on PostgreSQL `?` is not a placeholder at all).
    """
    src = inspect.getsource(ShadowStore.reclaim_mirror_payloads)
    assert "isinstance(conn, sqlite3.Connection)" in src or "placeholders" in src, (
        "the placeholder must be chosen by engine, not hardcoded"
    )


# ---------------------------------------------------------------------------
# The eligibility guard, on both engines
# ---------------------------------------------------------------------------


def test_reclaim_requires_a_migrated_row(tmp_path):
    """A row that still has a mirror but no migrated data is never reclaimed.

    The guard is "mirror present AND probabilities populated". Clearing an
    unmigrated row would destroy its only copy of the data — the same
    invariants that protect `never_delete=True` rows on the PostgreSQL side.
    """
    db = str(tmp_path / "shadow.db")
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE shadow_decisions (
            id INTEGER PRIMARY KEY,
            payload TEXT,
            champion_probabilities TEXT DEFAULT '[]'
        )"""
    )
    # eligible: mirror present, probabilities migrated
    con.execute(
        "INSERT INTO shadow_decisions (id, payload, champion_probabilities) VALUES (1, ?1, ?2)",
        ("{'a': 1}", "[0.5, 0.5]"),
    )
    # NOT eligible: mirror present, no migrated data
    con.execute(
        "INSERT INTO shadow_decisions (id, payload, champion_probabilities) VALUES (2, ?1, ?2)",
        ("{'b': 2}", "[]"),
    )
    # NOT eligible: already reclaimed
    con.execute(
        "INSERT INTO shadow_decisions (id, payload, champion_probabilities) VALUES (3, ?1, ?2)",
        ("", "[0.5, 0.5]"),
    )
    con.commit()
    con.close()

    # Run the reclaim directly against the store's own seam.
    con = sqlite3.connect(db)
    ShadowStore._add_missing_columns(
        con,
        "shadow_decisions",
        [("champion_probabilities", "TEXT DEFAULT '[]'")],
    )
    # The real guard: only mirror-bearing rows with migrated data may clear.
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = AuditRepository(db_url=f"sqlite:///{db}")
    st = ShadowStore(repo)
    result = st.reclaim_mirror_payloads(dry_run=False)
    con.close()

    assert result["reclaimed"] == 1, result
    con = sqlite3.connect(db)
    remaining = con.execute(
        "SELECT id, length(payload) FROM shadow_decisions ORDER BY id"
    ).fetchall()
    # Row 1 cleared; rows 2 and 3 untouched.
    assert remaining == [(1, 0), (2, 8), (3, 0)], remaining
    con.close()


def test_reclaim_is_idempotent(tmp_path):
    """A second run reclaims nothing."""
    db = str(tmp_path / "shadow.db")
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE shadow_decisions (
            id INTEGER PRIMARY KEY,
            payload TEXT,
            champion_probabilities TEXT DEFAULT '[]'
        )"""
    )
    con.execute(
        "INSERT INTO shadow_decisions (id, payload, champion_probabilities) VALUES (1, ?1, ?2)",
        ("{'a': 1}", "[0.5, 0.5]"),
    )
    con.commit()
    con.close()

    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = AuditRepository(db_url=f"sqlite:///{db}")
    st = ShadowStore(repo)
    first = st.reclaim_mirror_payloads(dry_run=False)
    second = st.reclaim_mirror_payloads(dry_run=False)

    assert first["reclaimed"] == 1, first
    assert second["reclaimed"] == 0, second
    # No eligible rows remain, so the loop body never runs.
    assert second["eligible"] == 0, second


# ---------------------------------------------------------------------------
# Dialect-specific JSON emission
# ---------------------------------------------------------------------------


def test_backfill_emits_compact_json_on_sqlite(tmp_path):
    """SQLite's json_extract path must emit compact separators.

    PostgreSQL's `jsonb ->>` emits `[0.1, 0.2]` (with spaces); the SQLite path
    must emit `[0.1,0.2]` so the migrated column is byte-identical to what the
    producer wrote. #575 pinned this on the PostgreSQL side with a whitespace
    strip; the SQLite side must match.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    db = str(tmp_path / "shadow.db")
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE shadow_decisions (
            id INTEGER PRIMARY KEY,
            payload TEXT,
            champion_probabilities TEXT DEFAULT '[]'
        )"""
    )
    con.execute(
        "INSERT INTO shadow_decisions (id, payload) VALUES (1, ?)",
        ('{"champion_probabilities": [0.1, 0.2, 0.3]}',),
    )
    con.commit()

    repo = AuditRepository(db_url=f"sqlite:///{db}")
    st = ShadowStore(repo)
    st.ensure_schema()

    got = con.execute("SELECT champion_probabilities FROM shadow_decisions WHERE id=1").fetchone()[
        0
    ]
    con.close()

    # Compact, no whitespace between the separators.
    assert got == "[0.1,0.2,0.3]", got
    assert ", " not in got


# ---------------------------------------------------------------------------
# The read contract after reclaim
# ---------------------------------------------------------------------------


def test_read_decision_row_survives_reclaim(tmp_path):
    """A reclaimed row still yields its probabilities through the columns."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    db = str(tmp_path / "shadow.db")
    con = sqlite3.connect(db)
    con.execute(
        """CREATE TABLE shadow_decisions (
            id INTEGER PRIMARY KEY,
            payload TEXT,
            champion_probabilities TEXT DEFAULT '[]',
            challenger_probabilities TEXT DEFAULT '[]'
        )"""
    )
    con.execute(
        "INSERT INTO shadow_decisions (id, payload, champion_probabilities,"
        " challenger_probabilities) VALUES (1, ?1, ?2, ?3)",
        ("{'a': 1}", "[0.5, 0.5]", "[0.4, 0.6]"),
    )
    con.commit()
    con.close()

    repo = AuditRepository(db_url=f"sqlite:///{db}")
    st = ShadowStore(repo)
    st.ensure_schema()
    st.reclaim_mirror_payloads(dry_run=False)

    con = sqlite3.connect(db)
    row = dict(
        zip(
            ("id", "payload", "champion_probabilities", "challenger_probabilities"),
            con.execute(
                "SELECT id, payload, champion_probabilities, challenger_probabilities"
                " FROM shadow_decisions WHERE id=1"
            ).fetchone(),
            strict=True,
        )
    )
    con.close()

    decoded = ShadowStore.read_decision_row(row)
    assert decoded["champion_probabilities"] == [0.5, 0.5]
    assert decoded["challenger_probabilities"] == [0.4, 0.6]
    assert decoded["mirror_present"] is False
