"""LD-1: CHECK-ACC-02 false-CRITICAL on empty execution_id paper outcomes.

Reproduces the field defect against the real artifacts/audit.db shape:
1,687 outcome rows of which 1,465 are pre-execution decision samples with
execution_id='' (never traded, no broker ticket). Those empty-identity rows
collapsed into one fake '' duplicate bucket and made the check return
CRITICAL on every sweep.

Fix contract verified here:
  (a) empty/absent execution identity rows are EXCLUDED from the duplicate
      scan (not whitelisted) and their count is still reported as evidence;
  (b) a NEW duplicate on a real non-empty execution_id is still CRITICAL;
  (c) the two known historical pre-guard incidents are WARNING, not CRITICAL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.forensics import HealthStatus
from nexus_scalp.forensics import checks as C
from nexus_scalp.forensics import checks_accounting as CA

_OUTCOME_COLS = [
    ("id", "INTEGER"),
    ("idempotency_key", "TEXT"),
    ("execution_id", "TEXT"),
    ("realized_pnl_usd", "REAL"),
]


def _mkdb(path: Path, tables: dict[str, list[tuple[str, ...]]]) -> sqlite3.Connection:
    """Creates a hermetic sqlite DB with the given tables (name -> column tuples)."""
    conn = sqlite3.connect(path)
    for name, cols in tables.items():
        col_sql = ", ".join(f"{c} {t}" for c, t in cols)
        conn.execute(f"CREATE TABLE {name} ({col_sql})")
    conn.commit()
    return conn


def _outcomes_db(db: Path, rows: list[tuple]) -> None:
    """Builds an audit_experience_outcomes table and inserts the given rows."""
    conn = _mkdb(db, {"audit_experience_outcomes": _OUTCOME_COLS})
    conn.executemany("INSERT INTO audit_experience_outcomes VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


@pytest.fixture
def temp_audit_db(tmp_path: Path) -> Path:
    """Isolated temp CWD whose artifacts/audit.db is the synthetic audit DB.

    checks_accounting resolves the DB via _audit_path() imported into its own
    namespace, so both bindings are redirected at the temp file.
    """
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    db = tmp_path / "artifacts" / "audit.db"
    return db


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_only_empty_execution_id_rows_are_not_critical(
    tmp_path: Path, monkeypatch, empty: str | None
) -> None:
    """(a) paper outcomes with no broker ticket never trip CRITICAL."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "artifacts" / "audit.db"
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    _outcomes_db(
        db,
        [
            (1, "exp_aaaa", empty, 0.0),
            (2, "exp_bbbb", empty, 0.0),
            (3, "exp_cccc", empty, 0.0),
        ],
    )
    r = C.check_duplicate_economic_outcome()
    assert r.status is not HealthStatus.CRITICAL, r.evidence
    assert r.status is HealthStatus.PASS
    # the empty rows are still reported as evidence — never silently dropped
    assert r.observed["non_executed_rows_without_execution_id"] == 3
    assert r.observed["scanned_rows"] == 0


def test_empty_execution_id_rows_do_not_mask_a_real_duplicate(tmp_path: Path, monkeypatch) -> None:
    """(b) a NEW duplicate on a non-empty id stays CRITICAL alongside paper rows."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "artifacts" / "audit.db"
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    _outcomes_db(
        db,
        [
            (1, "exp_aaaa", "", 0.0),
            (2, "exp_bbbb", "", 0.0),
            (3, "exp_cccc", "770099112233", -18.27),
            (4, "exp_dddd", "770099112233", -31.50),
        ],
    )
    r = C.check_duplicate_economic_outcome()
    assert r.status is HealthStatus.CRITICAL
    assert r.detail == "DUPLICATE_ECONOMIC_OUTCOME"
    assert "770099112233" in r.evidence
    # paper rows still surfaced as evidence alongside the real duplicate
    assert r.observed["non_executed_rows_without_execution_id"] == 2
    assert "770099112233" in r.observed["duplicates"]


@pytest.mark.parametrize("historical", ["152494870397", "152660983978"])
def test_known_historical_duplicates_are_warning(
    tmp_path: Path, monkeypatch, historical: str
) -> None:
    """(c) both immutable pre-guard incidents classify WARNING, never CRITICAL."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "artifacts" / "audit.db"
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    _outcomes_db(
        db,
        [
            (1, "exp_aaaa", historical, -18.27),
            (2, "exp_bbbb", historical, -31.50),
        ],
    )
    r = C.check_duplicate_economic_outcome()
    assert r.status is HealthStatus.WARNING
    assert "HISTORICAL" in r.detail


def test_historical_plus_paper_rows_stay_warning(tmp_path: Path, monkeypatch) -> None:
    """Historical dupe + many empty paper rows resolves to WARNING, not CRITICAL."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "artifacts" / "audit.db"
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    rows = [(i, f"exp_{i:04d}", "", 0.0) for i in range(1, 11)]
    rows += [(11, "exp_hist1", "152494870397", -18.27), (12, "exp_hist2", "152494870397", -31.50)]
    _outcomes_db(db, rows)
    r = C.check_duplicate_economic_outcome()
    assert r.status is HealthStatus.WARNING
    assert "HISTORICAL" in r.detail
    assert r.observed["non_executed_rows_without_execution_id"] == 10


def test_real_db_shape_is_not_critical(tmp_path: Path, monkeypatch) -> None:
    """Live-shape regression: 1,465 empty rows + 2 historical dupes -> WARNING."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "artifacts" / "audit.db"
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    rows = [(i, f"exp_paper_{i:06d}", "", 0.0) for i in range(1, 1466)]
    rows += [
        (1466, "exp_87f47ca2", "152494870397", -18.27),
        (1467, "exp_d9952f5a", "152494870397", -31.50),
        (1468, "exp_377cd4d4", "152660983978", -2.88),
        (1469, "exp_857b3485", "152660983978", -2.88),
    ]
    _outcomes_db(db, rows)
    r = C.check_duplicate_economic_outcome()
    assert r.status is not HealthStatus.CRITICAL, r.evidence
    assert r.status is HealthStatus.WARNING
    assert r.observed["non_executed_rows_without_execution_id"] == 1465
    assert r.observed["scanned_rows"] == 2


def test_empty_string_is_not_whitelisted_in_fresh_duplicates(tmp_path: Path, monkeypatch) -> None:
    """LD-1 guard: '' is excluded by identity, never added to known_historical.

    A duplicate on a real non-empty id coexisting with empty rows must still
    be CRITICAL — proving the empty bucket is skipped, not whitelisted.
    """
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "artifacts" / "audit.db"
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    _outcomes_db(
        db,
        [
            (1, "exp_aaaa", "", 0.0),
            (2, "exp_bbbb", "", 0.0),
            (3, "exp_cccc", "152494870397", -18.27),
            (4, "exp_dddd", "152494870397", -31.50),
            (5, "exp_eeee", "999888777666", 1.0),
            (6, "exp_ffff", "999888777666", 2.0),
        ],
    )
    r = C.check_duplicate_economic_outcome()
    assert r.status is HealthStatus.CRITICAL
    assert "999888777666" in r.evidence
