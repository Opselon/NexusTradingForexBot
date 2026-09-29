"""DEEP-OPT L1 follow-up: the retired JSON mirror, reclaimed.

Context: PR #568 (DEEP-OPT L1) retired ``shadow_decisions.payload`` at the
producer — a shadow decision's whole record was being serialized into a JSON
column that duplicated values already stored in real columns. The producer
stopped writing it, the read path learned to fall back mirror -> column, and a
backfill was written to migrate the remaining mirror content into the last few
dedicated columns.

The backfill never ran on PostgreSQL. It used SQLite's ``PRAGMA table_info``
and a Python row loop, so ``ensure_schema``'s PostgreSQL branch assumed the
domain schema would install the columns instead — but an already-provisioned
domain is never re-translated, so the eight DEEP-OPT columns (and the six
mirror-only provenance columns added here) never reached an existing
PostgreSQL database. The live ledger kept 108.3 MB of duplicate JSON across
55,342 rows after the producer had already stopped writing it.

These tests pin the fix: the additive heal and the backfill are provider
portable, the migration is byte-identical, and the reclaim is idempotent,
bounded and resumable.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from nexus_scalp.shadow.store import ShadowStore


def _mirror(
    *,
    champion_probs: list[float] | None = None,
    challenger_probs: list[float] | None = None,
    champion_strategy: str = "",
    challenger_strategy: str = "",
    risk_pct: float = 0.0,
    volume: float = 0.0,
    entry: float = 0.0,
    exit_: float = 0.0,
    created_at: str = "2026-09-01T00:00:00",
    champion_version: str = "v1.2.3",
    challenger_version: str = "v1.2.2",
    champion_hash: str = "sha256:champ",
    challenger_hash: str = "sha256:chal",
    config_version: str = "cfg-9",
) -> str:
    """A retired-mirror row in the producer's own (compact) serialization."""
    return json.dumps(
        {
            "champion_probabilities": champion_probs or [],
            "challenger_probabilities": challenger_probs or [],
            "champion_strategy_id": champion_strategy,
            "challenger_strategy_id": challenger_strategy,
            "created_at": created_at,
            "champion": {
                "strategy_id": champion_strategy,
                "model_version": champion_version,
                "artifact_hash": champion_hash,
            },
            "challenger": {
                "strategy_id": challenger_strategy,
                "model_version": challenger_version,
                "artifact_hash": challenger_hash,
            },
            "hypothetical": {"risk_pct": risk_pct, "volume": volume, "entry": entry, "exit": exit_},
            "shared_input": {"configuration_version": config_version},
        },
        separators=(",", ":"),
    )


def _make_sqlite_db(path) -> sqlite3.Connection:
    """A shadow_decisions table shaped like a PRE-DEEP-OPT database.

    Deliberately missing every DEEP-OPT L1 column: this is the shape an
    already-provisioned database has, which is exactly the case the heal must
    repair without a dump/restore.
    """
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE shadow_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_decision_id TEXT UNIQUE NOT NULL,
            run_id TEXT NOT NULL,
            decision_id TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT DEFAULT '',
            champion_model_id TEXT NOT NULL,
            champion_version TEXT NOT NULL,
            challenger_model_id TEXT NOT NULL,
            challenger_version TEXT NOT NULL,
            champion_action TEXT DEFAULT '',
            champion_confidence REAL DEFAULT 0.0,
            challenger_action TEXT DEFAULT '',
            challenger_confidence REAL DEFAULT 0.0,
            payload TEXT DEFAULT ''
        )
        """
    )
    con.execute(
        """
        CREATE TABLE shadow_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE NOT NULL,
            champion_model_id TEXT NOT NULL,
            champion_version TEXT NOT NULL,
            challenger_model_id TEXT NOT NULL,
            challenger_version TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT DEFAULT '',
            decision_count INTEGER DEFAULT 0,
            error TEXT DEFAULT ''
        )
        """
    )
    return con


def _insert_legacy(con: sqlite3.Connection, sid: str, payload: str) -> int:
    cur = con.execute(
        "INSERT INTO shadow_decisions (shadow_decision_id, run_id, timestamp, symbol, "
        "champion_model_id, champion_version, challenger_model_id, challenger_version, payload) "
        "VALUES (?, 'run-1', '2026-09-01', 'EURUSD', 'champ', 'v1', 'chal', 'v1', ?)",
        (sid, payload),
    )
    return int(cur.lastrowid)


class _FakeRepo:
    """A minimal SQLite-backed AuditRepository stand-in for the SQLite path."""

    def __init__(self, path: str) -> None:
        self._db_path = path
        self._is_sqlite = True
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row

    def _connect_sqlite(self, timeout: float = 5.0) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path, timeout=timeout)
        con.row_factory = sqlite3.Row
        return con

    def close(self) -> None:
        self.con.close()


# ---------------------------------------------------------------------------
# The additive heal is provider portable
# ---------------------------------------------------------------------------


def test_add_missing_columns_is_provider_agnostic(tmp_path):
    """The column-introspection helper reads either engine's catalog.

    This is the exact root cause: the original used ``PRAGMA table_info``
    unconditionally, which raises on PostgreSQL and left the whole heal as a
    no-op there.
    """
    con = _make_sqlite_db(str(tmp_path / "news.db"))
    try:
        ShadowStore._add_missing_columns(
            con, "shadow_decisions", [("champion_probabilities", "TEXT DEFAULT '[]'")]
        )
        cols = {r[1] for r in con.execute("PRAGMA table_info(shadow_decisions);")}
        assert "champion_probabilities" in cols
    finally:
        con.close()


def test_add_missing_columns_reads_a_postgres_shaped_catalog(tmp_path):
    """The heal must introspect an information_schema catalog, not PRAGMA.

    This is the regression that let 108 MB of retired mirror JSON sit on the
    live PostgreSQL ledger: ``PRAGMA table_info`` is SQLite-only, so on
    PostgreSQL the additive heal was unreachable. A stub connection exposing
    the catalog the way psycopg's cursor does must be handled the same way a
    SQLite one is.
    """

    class _PgCursor:
        def __init__(self, rows: list) -> None:
            self._rows = rows

        def execute(self, sql: str, args: tuple = ()):
            return self

        def fetchall(self):
            return self._rows

        def __iter__(self):
            return iter(self._rows)

        catalog = ("id", "payload", "champion_probabilities")

    class _PgConn:
        """The shape psycopg3 exposes: no PRAGMA, %s placeholders."""

        def __init__(self) -> None:
            self.altered: list[str] = []

        def execute(self, sql: str, args: tuple = ()):
            cur = _PgCursor([])
            # The helper only introspects and ALTERs; emulate both.
            if "information_schema.columns" in sql:
                cur = _PgCursor([(c,) for c in _PgCursor.catalog])
            elif sql.startswith("ALTER TABLE"):
                self.altered.append(sql)
                cur = _PgCursor([])
            return cur

    con = _PgConn()
    ShadowStore._add_missing_columns(
        con,
        "shadow_decisions",
        [
            ("champion_probabilities", "TEXT DEFAULT '[]'"),
            # A column the PostgreSQL table does NOT have yet.
            ("mirror_created_at", "TEXT DEFAULT ''"),
        ],
    )
    # The existing column is not re-added (idempotent)...
    assert not any("champion_probabilities" in s for s in con.altered)
    # ...but the missing one is, on a connection that has no PRAGMA at all.
    assert any("mirror_created_at" in s for s in con.altered)


def test_heal_adds_every_missing_column_on_sqlite(tmp_path):
    """A pre-DEEP-OPT SQLite database gains all 20 columns in one heal."""
    repo = _FakeRepo(str(tmp_path / "shadow.db"))
    _make_sqlite_db(str(tmp_path / "shadow.db"))
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = repo  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()

        cols = {r[1] for r in repo.con.execute("PRAGMA table_info(shadow_decisions);")}
        for name in (
            "champion_probabilities",
            "challenger_probabilities",
            "champion_strategy_id",
            "challenger_strategy_id",
            "hypothetical_risk_pct",
            "hypothetical_volume",
            "hypothetical_entry",
            "hypothetical_exit",
            "mirror_created_at",
            "champion_model_version",
            "challenger_model_version",
            "champion_artifact_hash",
            "challenger_artifact_hash",
            "shared_configuration_version",
        ):
            assert name in cols, f"heal did not add {name}"
    finally:
        repo.close()


def test_heal_is_idempotent(tmp_path):
    """Running the heal twice adds no columns twice and migrates no rows twice."""
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    _insert_legacy(repo.con, "sd-1", _mirror(champion_probs=[0.1, 0.9]))
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = repo  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()
        store._additive_ensured = False
        store.ensure_schema()

        row = repo.con.execute(
            "SELECT champion_probabilities FROM shadow_decisions WHERE shadow_decision_id = 'sd-1'"
        ).fetchone()
        # A second pass must not re-migrate (the guard is "no probabilities yet").
        assert row[0] == "[0.1,0.9]"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# The backfill is byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "probs",
    [
        [0.1, 0.2, 0.7],
        [0.5],
        [],
        [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0],
    ],
)
def test_backfill_is_byte_identical(tmp_path, probs):
    """The migrated column equals the mirror's own bytes, not a re-serialization.

    ``read_decision_row`` prefers the column over the mirror, so a column that
    differs from the mirror even in whitespace would silently change every
    consumer's reading after migration. The producer writes compact JSON, so
    the backfill must emit compact JSON too.
    """
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    raw = _mirror(champion_probs=probs)
    _insert_legacy(repo.con, "sd-byte", raw)
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = repo  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()

        row = repo.con.execute(
            "SELECT payload, champion_probabilities FROM shadow_decisions "
            "WHERE shadow_decision_id = 'sd-byte'"
        ).fetchone()
        expected = json.dumps(probs, separators=(",", ":"))
        assert row[1] == expected, "column is not byte-identical to the mirror"
        # The mirror itself is untouched: reclaim is a separate step.
        assert row[0] == raw
    finally:
        repo.close()


def test_backfill_migrates_every_mirror_only_field(tmp_path):
    """Every field with no column is carried out of the mirror."""
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    _insert_legacy(
        repo.con,
        "sd-fields",
        _mirror(
            champion_probs=[0.25, 0.75],
            challenger_probs=[0.4, 0.6],
            champion_strategy="scalp_v1",
            challenger_strategy="meanrev_v2",
            risk_pct=1.5,
            volume=0.8,
            entry=1.0825,
            exit_=1.0840,
            created_at="2026-08-15T12:34:56",
            champion_version="v9.9.9",
            challenger_version="v8.8.8",
            champion_hash="sha256:abc",
            challenger_hash="sha256:def",
            config_version="cfg-42",
        ),
    )
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = repo  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()

        row = dict(
            repo.con.execute(
                "SELECT * FROM shadow_decisions WHERE shadow_decision_id = 'sd-fields'"
            ).fetchone()
        )
        assert row["champion_probabilities"] == "[0.25,0.75]"
        assert row["challenger_probabilities"] == "[0.4,0.6]"
        assert row["champion_strategy_id"] == "scalp_v1"
        assert row["challenger_strategy_id"] == "meanrev_v2"
        assert row["hypothetical_risk_pct"] == pytest.approx(1.5)
        assert row["hypothetical_volume"] == pytest.approx(0.8)
        assert row["hypothetical_entry"] == pytest.approx(1.0825)
        assert row["hypothetical_exit"] == pytest.approx(1.0840)
        assert row["mirror_created_at"] == "2026-08-15T12:34:56"
        assert row["champion_model_version"] == "v9.9.9"
        assert row["challenger_model_version"] == "v8.8.8"
        assert row["champion_artifact_hash"] == "sha256:abc"
        assert row["challenger_artifact_hash"] == "sha256:def"
        assert row["shared_configuration_version"] == "cfg-42"
    finally:
        repo.close()


def test_backfill_skips_invalid_json_without_error(tmp_path):
    """A corrupt mirror must not abort the whole migration."""
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    good_id = _insert_legacy(repo.con, "sd-good", _mirror(champion_probs=[0.5, 0.5]))
    _insert_legacy(repo.con, "sd-bad", "{not valid json")
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = repo  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()

        good = repo.con.execute(
            "SELECT champion_probabilities FROM shadow_decisions WHERE id = ?", (good_id,)
        ).fetchone()
        assert good[0] == "[0.5,0.5]"
    finally:
        repo.close()


def test_backfill_respects_flat_and_nested_shapes(tmp_path):
    """The mirror stored strategy ids both flat and nested; both are read."""
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    flat = json.dumps(
        {
            "champion_probabilities": [0.3, 0.7],
            "champion_strategy_id": "flat-strat",
            "challenger_strategy_id": "flat-chal",
        },
        separators=(",", ":"),
    )
    nested = json.dumps(
        {
            "champion_probabilities": [0.2, 0.8],
            "champion": {"strategy_id": "nested-strat", "model_version": "vn"},
            "challenger": {"strategy_id": "nested-chal", "model_version": "vc"},
        },
        separators=(",", ":"),
    )
    _insert_legacy(repo.con, "sd-flat", flat)
    _insert_legacy(repo.con, "sd-nested", nested)
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = repo  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()

        f = dict(
            repo.con.execute(
                "SELECT * FROM shadow_decisions WHERE shadow_decision_id = 'sd-flat'"
            ).fetchone()
        )
        n = dict(
            repo.con.execute(
                "SELECT * FROM shadow_decisions WHERE shadow_decision_id = 'sd-nested'"
            ).fetchone()
        )
        assert f["champion_strategy_id"] == "flat-strat"
        assert n["champion_strategy_id"] == "nested-strat"
        assert n["champion_model_version"] == "vn"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# The read contract survives the migration
# ---------------------------------------------------------------------------


def test_read_decision_row_prefers_column_over_mirror(tmp_path):
    """The column wins, so a migrated row reads the same before and after."""
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    raw = _mirror(champion_probs=[0.1, 0.9], champion_strategy="strat-a")
    _insert_legacy(repo.con, "sd-read", raw)
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = repo  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()

        row = dict(
            repo.con.execute(
                "SELECT * FROM shadow_decisions WHERE shadow_decision_id = 'sd-read'"
            ).fetchone()
        )
        out = ShadowStore.read_decision_row(row)
        assert out["mirror_present"] is True
        assert out["champion_probabilities"] == pytest.approx([0.1, 0.9])
    finally:
        repo.close()


def test_read_decision_row_works_with_no_mirror(tmp_path):
    """Post-reclaim shape: empty mirror, columns populated."""
    row = {
        "id": 1,
        "champion_probabilities": "[0.2,0.8]",
        "challenger_probabilities": "[0.5,0.5]",
        "payload": "",
    }
    out = ShadowStore.read_decision_row(row)
    assert out["mirror_present"] is False
    assert out["champion_probabilities"] == pytest.approx([0.2, 0.8])
    assert out["challenger_probabilities"] == pytest.approx([0.5, 0.5])


# ---------------------------------------------------------------------------
# pending_mirror_bytes is provider portable
# ---------------------------------------------------------------------------


def test_pending_mirror_bytes_reports_unreclaimed_storage(tmp_path):
    """The reclaimable-byte counter works on a database that still carries it."""
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    raw = _mirror(champion_probs=[0.1, 0.9])
    _insert_legacy(repo.con, "sd-bytes", raw)
    repo.con.commit()
    try:
        assert ShadowStore.pending_mirror_bytes(repo.con) == len(raw.encode())
    finally:
        repo.close()


def test_pending_mirror_bytes_is_zero_after_reclaim(tmp_path):
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    _insert_legacy(repo.con, "sd-zero", _mirror(champion_probs=[0.5]))
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = repo  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()
        assert ShadowStore.pending_mirror_bytes(repo.con) > 0
        repo.con.execute("UPDATE shadow_decisions SET payload = ''")
        repo.con.commit()
        assert ShadowStore.pending_mirror_bytes(repo.con) == 0
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# The reclaim path is bounded, idempotent and resumable
# ---------------------------------------------------------------------------


def _reclaim_repo(path: str) -> Any:
    """A repo exposing the seam reclaim_mirror_payloads uses."""

    class _R:
        def __init__(self) -> None:
            self._is_sqlite = True
            self._db_path = path

        def _connect_sqlite(self, timeout: float = 5.0) -> sqlite3.Connection:
            con = sqlite3.connect(path, timeout=timeout)
            con.row_factory = sqlite3.Row
            return con

    return _R()


def test_reclaim_mirror_payloads_requires_migrated_rows(tmp_path):
    """Reclaiming a row that was never backfilled would destroy its only copy.

    The eligibility guard is "mirror present AND probabilities populated", so
    an unmigrated row is left alone.
    """
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    _insert_legacy(repo.con, "sd-unmigrated", _mirror(champion_probs=[0.5, 0.5]))
    # The columns exist (as they do on any provisioned database), but the row
    # has NOT been backfilled, so its mirror is still the only copy.
    repo.con.execute(
        "ALTER TABLE shadow_decisions ADD COLUMN champion_probabilities TEXT DEFAULT '[]'"
    )
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = _reclaim_repo(path)  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = True  # type: ignore[attr-defined]
        counts = store.reclaim_mirror_payloads(batch_size=10)
        assert counts["eligible"] == 0
        row = repo.con.execute("SELECT payload FROM shadow_decisions WHERE id = 1").fetchone()
        assert row[0] != ""
    finally:
        repo.close()


def test_reclaim_mirror_payloads_is_bounded(tmp_path):
    """``max_rows`` caps the call; the remainder stays for a later round."""
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    for i in range(7):
        _insert_legacy(repo.con, f"sd-{i}", _mirror(champion_probs=[float(i) / 10, 0.5]))
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = _reclaim_repo(path)  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()

        counts = store.reclaim_mirror_payloads(batch_size=2, max_rows=3)
        assert counts["eligible"] == 3
        assert counts["reclaimed"] == 3
        remaining = repo.con.execute(
            "SELECT count(*) FROM shadow_decisions WHERE payload <> ''"
        ).fetchone()[0]
        assert remaining == 4
    finally:
        repo.close()


def test_reclaim_mirror_payloads_is_idempotent(tmp_path):
    """A second call reclaims nothing."""
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    _insert_legacy(repo.con, "sd-once", _mirror(champion_probs=[0.5, 0.5]))
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = _reclaim_repo(path)  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()

        first = store.reclaim_mirror_payloads()
        second = store.reclaim_mirror_payloads()
        assert first["reclaimed"] == 1
        assert second["reclaimed"] == 0
        assert second["eligible"] == 0
    finally:
        repo.close()


def test_reclaim_dry_run_changes_nothing(tmp_path):
    """Shadow mode reports the eligible payload without clearing it."""
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    raw = _mirror(champion_probs=[0.5, 0.5])
    _insert_legacy(repo.con, "sd-dry", raw)
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = _reclaim_repo(path)  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()

        counts = store.reclaim_mirror_payloads(dry_run=True)
        assert counts["eligible"] == 1
        assert counts["reclaimed"] == 0
        assert counts["bytes_before"] == len(raw.encode())
        row = repo.con.execute("SELECT payload FROM shadow_decisions WHERE id = 1").fetchone()
        assert row[0] == raw
    finally:
        repo.close()


def test_reclaim_resumes_from_a_partial_run(tmp_path):
    """A keyset cursor, not OFFSET: a stopped run continues exactly where it left off."""
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    for i in range(5):
        _insert_legacy(repo.con, f"sd-r{i}", _mirror(champion_probs=[0.1 * i, 0.5]))
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = _reclaim_repo(path)  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()

        first = store.reclaim_mirror_payloads(batch_size=2, max_rows=2)
        assert first["reclaimed"] == 2
        rest = store.reclaim_mirror_payloads(batch_size=2)
        assert rest["reclaimed"] == 3
        remaining = repo.con.execute(
            "SELECT count(*) FROM shadow_decisions WHERE payload <> ''"
        ).fetchone()[0]
        assert remaining == 0
    finally:
        repo.close()


def test_reclaim_preserves_the_read_contract(tmp_path):
    """After reclaim, read_decision_row still returns the probabilities.

    This is the safety property the whole design rests on: the mirror is only
    disposable because the columns + the fallback reader together reconstruct
    everything the mirror held.
    """
    path = str(tmp_path / "shadow.db")
    _make_sqlite_db(path)
    repo = _FakeRepo(path)
    _insert_legacy(repo.con, "sd-final", _mirror(champion_probs=[0.3, 0.7]))
    repo.con.commit()
    try:
        store = ShadowStore.__new__(ShadowStore)
        store.audit_repo = _reclaim_repo(path)  # type: ignore[attr-defined]
        store._schema_ensured = True  # type: ignore[attr-defined]
        store._additive_ensured = False  # type: ignore[attr-defined]
        store.ensure_schema()
        store.reclaim_mirror_payloads()

        row = dict(
            repo.con.execute(
                "SELECT * FROM shadow_decisions WHERE shadow_decision_id = 'sd-final'"
            ).fetchone()
        )
        assert row["payload"] == ""
        out = ShadowStore.read_decision_row(row)
        assert out["mirror_present"] is False
        assert out["champion_probabilities"] == pytest.approx([0.3, 0.7])
    finally:
        repo.close()
