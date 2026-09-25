"""BUG-276 regression battery — baseline-skeleton UNIQUE shadow (role 7).

The PERF-DEADLETTER wave (2026-09-10) healed the migration gate's id-only
baseline skeletons to the application's COLUMN contract
(database/app_columns.APP_REQUIRED_COLUMNS), but left the CONSTRAINT half of
the shadow intact: the skeleton replaces the app DDL's UNIQUE constraints
with a plain ``id INTEGER PRIMARY KEY``, and neither CREATE TABLE IF NOT
EXISTS (no-op on the skeleton) nor ADD COLUMN can restore a missing unique
index. Result: every producer INSERT whose ``ON CONFLICT(<cols>)`` target
has no matching UNIQUE/PK index fails outright —

    "ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint"

— and the audit worker dead-letters that table's rows FOREVER. Proven live
in the 2026-09-14 nightly Client E2E container (run 34820773492):
audit_guard_telemetry dead-lettering ~19 rows/second from the first tick;
locally reproduced at HEAD: 15 of the ON CONFLICT targets in src/ are dead
on a fresh gate-first database (audit_signals / audit_orders /
audit_executions already self-heal inside the app bootstrap).

Fix under test:
  * engine._create_baseline_tables builds the unique indexes at baseline
    (tables are empty in the same transaction — fail-loud);
  * AuditRepository._ensure_unique_constraint_heal repairs EXISTING
    skeleton-shadowed databases at every boot (the field shape: DB poisoned
    by the pre-fix engine) — idempotent, no-op on healthy databases,
    loud-error + skip when existing rows genuinely violate the constraint.

Every test in this file is a behavior pin executed against real SQLite.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.adapters.database.audit_repository import (  # noqa: E402
    AuditRepository,
    _unique_target_resolves,
)
from nexus_scalp.database.app_columns import APP_UNIQUE_TARGETS  # noqa: E402
from nexus_scalp.database.engine import DatabaseMigrationEngine  # noqa: E402
from nexus_scalp.database.models import DatabaseDomain  # noqa: E402


def _gate_first_db(tmp_path: Path) -> Path:
    """Runs the migration gate on a FRESH database (docker entrypoint order)."""
    db = tmp_path / "audit.db"
    res = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT).migrate()
    assert res["state"] == "DB_MIGRATION_SUCCEEDED", res
    return db


def _columns(db: Path, table: str) -> set[str]:
    con = sqlite3.connect(db)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


# (table, ON CONFLICT cols) pairs straight from the producers' SQL.
CONTRACT_TARGETS = sorted(
    (table, cols) for table, targets in APP_UNIQUE_TARGETS.items() for cols in targets
)


# ---------------------------------------------------------------------------
# 1) Fresh gate-first install: every ON CONFLICT target must RESOLVE after
#    the gate alone (the engine baseline heal), before any app boot.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table,cols", CONTRACT_TARGETS)
def test_gate_first_unique_target_resolves(tmp_path: Path, table: str, cols: tuple) -> None:
    db = _gate_first_db(tmp_path)
    con = sqlite3.connect(db)
    try:
        assert table in {
            r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }, f"{table} missing on a fresh gate-first DB"
        assert _unique_target_resolves(con, table, cols), (
            f"BUG-276: ON CONFLICT({', '.join(cols)}) on {table} has no UNIQUE/PK "
            "target after the migration gate — every producer upsert for this "
            "table dead-letters forever"
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 2) End-to-end producer shapes on a gate-first DB + app bootstrap: the
#    exact INSERTs from the failing code must work (this is what the E2E
#    container proved dead: guard telemetry upsert, experience ledger,
#    research worker state, strategy registry).
# ---------------------------------------------------------------------------

PRODUCER_SQL: dict[str, str] = {
    # audit_repository._log_guard_telemetry (the 19 rows/s E2E failure)
    "audit_guard_telemetry": (
        "INSERT INTO audit_guard_telemetry (window_start, symbol, reason_code, count) "
        "VALUES (?, ?, ?, 1) ON CONFLICT(window_start, symbol, reason_code) "
        "DO UPDATE SET count = count + 1"
    ),
    # experience/ledger._INSERT_EXPERIENCE_SQL
    "audit_experiences": (
        "INSERT INTO audit_experiences (idempotency_key, payload) VALUES (?, ?) "
        "ON CONFLICT(idempotency_key) DO NOTHING"
    ),
    # experience/ledger._INSERT_OUTCOME_SQL
    "audit_experience_outcomes": (
        "INSERT INTO audit_experience_outcomes (idempotency_key, payload) VALUES (?, ?) "
        "ON CONFLICT(idempotency_key) DO NOTHING"
    ),
    # research/worker.py checkpoint upsert
    "research_worker_state": (
        "INSERT INTO research_worker_state (scope, cycle_count) VALUES (?, ?) "
        "ON CONFLICT(scope) DO UPDATE SET cycle_count = cycle_count + 1"
    ),
    # research/registry.py strategy upsert
    "strategy_registry": (
        "INSERT INTO strategy_registry (strategy_id, strategy_version, lifecycle, "
        "created_at, updated_at) VALUES (?, ?, 'ACTIVE', ?, ?) "
        "ON CONFLICT(strategy_id, strategy_version) DO UPDATE SET updated_at = excluded.updated_at"
    ),
    # experience/provenance.py model registry upsert
    "experience_model_registry": (
        "INSERT INTO experience_model_registry (model_id, model_version, "
        "artifact_fingerprint, registered_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(model_id, model_version, artifact_fingerprint) "
        "DO UPDATE SET registered_at = excluded.registered_at"
    ),
}


@pytest.mark.parametrize("table,sql", sorted(PRODUCER_SQL.items()))
def test_producer_upserts_survive_gate_then_bootstrap(tmp_path: Path, table: str, sql: str) -> None:
    db = _gate_first_db(tmp_path)
    repo = AuditRepository(db_url=f"sqlite:///{db}")  # app bootstrap (no-op CREATEs)
    try:
        con = sqlite3.connect(db)
        try:
            # Run the producer statement TWICE: ON CONFLICT must absorb the
            # replay (idempotency is the whole point of these INSERTs).
            if table == "audit_guard_telemetry":
                args = ("2026-09-14T10:00:00", "XAUUSD", "TICK_DUPLICATE_SUPPRESSED")
            elif table in ("audit_experiences", "audit_experience_outcomes"):
                args = ("idem-key-1", "{}")
            elif table == "research_worker_state":
                args = ("research", 1)
            elif table == "strategy_registry":
                args = ("s-1", "1.0.0", "t0", "t1")
            else:  # experience_model_registry
                args = ("m-1", "v-1", "f-1", "t0")
            con.execute(sql, args)
            con.execute(sql, args)  # replay — must NOT raise
            con.commit()
            if table == "audit_guard_telemetry":
                n = con.execute(
                    "SELECT count FROM audit_guard_telemetry "
                    "WHERE window_start=? AND symbol=? AND reason_code=?",
                    args,
                ).fetchone()[0]
                assert n == 2, f"telemetry upsert did not increment (count={n})"
            else:
                rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                assert rows == 1, f"{table}: replay created a duplicate row (rows={rows})"
            # The worker must not have dead-lettered anything.
            dl = con.execute("SELECT COUNT(*) FROM audit_dead_letter").fetchone()[0]
            assert dl == 0, f"{dl} rows dead-lettered on a healthy schema"
        finally:
            con.close()
    finally:
        repo.close()


def test_guard_telemetry_through_the_real_worker(tmp_path: Path) -> None:
    """The EXACT dead-lettered shape from the E2E container: log_signal routes
    TICK_DUPLICATE_SUPPRESSED to _log_guard_telemetry -> async queue -> worker.
    Post-fix the row must land in audit_guard_telemetry, zero dead letters."""
    from datetime import UTC, datetime

    from nexus_scalp.domain.models import ActionType, TradeProposal

    db = _gate_first_db(tmp_path)
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    try:
        proposal = TradeProposal(
            request_id="req-b276-0001",
            execution_id="EXEC-20260914-000000-b276",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=ActionType.NO_TRADE,
            confidence=0.1,
            proposed_entry=1900.0,
            stop_loss=1899.0,
            take_profit=1902.0,
            risk_reward_ratio=2.0,
            reason_code="TICK_DUPLICATE_SUPPRESSED",
            model_action="NO_TRADE",
            buy_probability=0.1,
            sell_probability=0.1,
            no_trade_probability=0.8,
            regime="UNKNOWN",
            regime_confidence=0.0,
            risk_allowed=False,
            guardian_status="IDLE",
            rejection_reason=None,
            final_action="NO_TRADE",
            risk_checks={},
            execution_mode="STANDARD",
            override_reason=None,
            decision_stage="STANDARD_EVAL",
            blocked_by=None,
            htf_score=0.0,
            smc_score=0.0,
            confidence_before_filters=0.0,
            confidence_after_filters=0.0,
        )
        repo.log_signal(proposal)
        repo.log_signal(proposal)
        assert repo.flush(timeout_sec=10), "audit queue did not drain"
        con = sqlite3.connect(db)
        try:
            row = con.execute(
                "SELECT count FROM audit_guard_telemetry WHERE reason_code=?",
                ("TICK_DUPLICATE_SUPPRESSED",),
            ).fetchone()
            dl = con.execute("SELECT COUNT(*) FROM audit_dead_letter").fetchone()[0]
        finally:
            con.close()
        assert row is not None, "guard telemetry row lost (dead-letter class BUG-276)"
        assert row[0] == 2, f"telemetry replay not aggregated into count (got {row[0]})"
        assert dl == 0, f"{dl} rows dead-lettered on a healthy schema"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# 3) Field repair: a database already poisoned by the PRE-FIX engine (skeleton
#    shape, no unique indexes) must be healed by the app bootstrap on boot.
# ---------------------------------------------------------------------------


def test_existing_poisoned_db_is_healed_at_boot(tmp_path: Path) -> None:
    db = _gate_first_db(tmp_path)
    # Strip the baseline heal -> exact pre-fix skeleton shape.
    con = sqlite3.connect(db)
    for (name,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%_uq_%'"
    ).fetchall():
        con.execute(f"DROP INDEX {name}")
    con.commit()
    # A pre-fix producer attempt fails exactly like the E2E container:
    with pytest.raises(sqlite3.OperationalError, match="ON CONFLICT clause does not match"):
        con.execute(
            "INSERT INTO research_worker_state (scope, cycle_count) VALUES ('research', 1) "
            "ON CONFLICT(scope) DO UPDATE SET cycle_count = cycle_count + 1"
        )
    con.close()
    # Boot the app: the bootstrap heal must restore the targets.
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    try:
        con = sqlite3.connect(db)
        try:
            con.execute(
                "INSERT INTO research_worker_state (scope, cycle_count) VALUES ('research', 1) "
                "ON CONFLICT(scope) DO UPDATE SET cycle_count = cycle_count + 1"
            )
            con.execute(
                "INSERT INTO research_worker_state (scope, cycle_count) VALUES ('research', 1) "
                "ON CONFLICT(scope) DO UPDATE SET cycle_count = cycle_count + 1"
            )
            con.commit()
            n = con.execute(
                "SELECT cycle_count FROM research_worker_state WHERE scope='research'"
            ).fetchone()[0]
            assert n == 2, f"post-heal upsert did not increment (cycle_count={n})"
        finally:
            con.close()
    finally:
        repo.close()


def test_boot_heal_survives_genuine_duplicates(tmp_path: Path) -> None:
    """A poisoned DB that already holds duplicate key rows (created by the
    pre-fix plain-INSERT fallback paths) must NOT crash the boot: the heal
    logs loudly, skips that one index, and still heals every other target."""
    db = _gate_first_db(tmp_path)
    con = sqlite3.connect(db)
    for (name,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%_uq_%'"
    ).fetchall():
        con.execute(f"DROP INDEX {name}")
    con.execute("INSERT INTO research_worker_state (scope, cycle_count) VALUES ('research', 1)")
    con.execute("INSERT INTO research_worker_state (scope, cycle_count) VALUES ('research', 2)")
    con.commit()
    con.close()
    repo = AuditRepository(db_url=f"sqlite:///{db}")  # must not raise
    try:
        con = sqlite3.connect(db)
        try:
            # The conflicted table stays un-indexed (loud error path)...
            assert not _unique_target_resolves(con, "research_worker_state", ("scope",))
            # ...while every other target WAS healed.
            assert _unique_target_resolves(
                con, "strategy_registry", ("strategy_id", "strategy_version")
            )
            assert _unique_target_resolves(con, "audit_experiences", ("idempotency_key",))
        finally:
            con.close()
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# 4) Healthy-database zero-churn contract: on an app-first database (real
#    app DDL owns the constraints) the heal adds NOTHING and repeated boots
#    are byte-stable.
# ---------------------------------------------------------------------------


def _uq_index_names(db: Path) -> set[str]:
    con = sqlite3.connect(db)
    try:
        return {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%_uq_%'"
            )
        }
    finally:
        con.close()


def test_app_first_bootstrap_adds_no_heal_indexes(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    repo.close()
    after_first = _uq_index_names(db)
    assert after_first == set(), "heal built redundant indexes on a healthy app-first DB"
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    repo.close()
    assert _uq_index_names(db) == after_first


def test_gate_first_heal_is_stable_across_boots(tmp_path: Path) -> None:
    db = _gate_first_db(tmp_path)
    after_gate = _uq_index_names(db)
    assert len(after_gate) == len(
        [1 for _, targets in APP_UNIQUE_TARGETS.items() for _ in targets]
    ), "baseline heal did not build exactly the contract targets"
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    repo.close()
    assert _uq_index_names(db) == after_gate, "boot heal churned a baseline-healed DB"


# ---------------------------------------------------------------------------
# 5) CLASS guard: every ON CONFLICT target in src/ that addresses a table
#    living in the audit DB must resolve on a fresh gate-first database.
#    Catches future producers that reintroduce the dead-target shape.
# ---------------------------------------------------------------------------


def _scan_src_on_conflict_targets() -> set[tuple[str, tuple[str, ...]]]:
    import re

    pattern = re.compile(r"INSERT\s+INTO\s+(\w+)[^;]*?ON\s+CONFLICT\s*\(([^)]*)\)", re.S | re.I)
    found: set[tuple[str, tuple[str, ...]]] = set()
    src = REPO_ROOT / "src"
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in pattern.finditer(text):
            cols = tuple(c.strip().strip('"').lower() for c in m.group(2).split(","))
            found.add((m.group(1).lower(), cols))
    return found


def test_every_src_on_conflict_target_in_audit_db_resolves(tmp_path: Path) -> None:
    """After gate + app bootstrap (the production sequence), any audit-DB
    table named by an src/ ON CONFLICT INSERT must have a live target."""
    db = _gate_first_db(tmp_path)
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    try:
        con = sqlite3.connect(db)
        try:
            tables = {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            dead: list[str] = []
            scanned = 0
            for table, cols in _scan_src_on_conflict_targets():
                if table not in tables:
                    continue  # lives in another domain DB (news/settings/...)
                scanned += 1
                if not _unique_target_resolves(con, table, cols):
                    dead.append(f"{table}({', '.join(cols)})")
            assert scanned >= 18, (
                f"scanner only matched {scanned} audit-DB ON CONFLICT targets — "
                "regex drift, this guard would silently pass"
            )
            assert not dead, "BUG-276 shape reappeared for: " + "; ".join(sorted(dead))
        finally:
            con.close()
    finally:
        repo.close()
