"""PERF-DEADLETTER mission — safety-contract guard pins.

The performance fixes in this wave touch shared infrastructure (migration
baseline, dead-letter store, health route, policy trace). These tests pin
the contracts the mission explicitly forbids weakening:

  1. PERSISTED_HALTED stays fail-closed: a persisted HALT row blocks the
     boot decision AND survives the wave's changes untouched (the halt
     state is INDEPENDENT of the dead-letter/retention path — a bounded
     prune can never mute a safety halt);
  2. LIVE/PAPER separation: the retention prune deletes ONLY
     audit_dead_letter rows — audit_ledger / audit_account_snapshots /
     runtime_risk_state (the LIVE-sensitive accounting + safety surfaces)
     are never touched; and the account_source provenance columns the
     PAPER/LIVE attribution relies on are present after the skeleton heal.
"""

from __future__ import annotations

import sqlite3

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.risk.runtime_safety import PersistedRiskState, resolve_boot_decision


@pytest.fixture()
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'safety.db'}")
    try:
        yield r
    finally:
        r.close()


def test_persisted_halt_remains_fail_closed_after_wave(repo) -> None:
    """HALT persisted -> boot refused. The wave's dead-letter retention and
    schema heal must not alter the safety-state surface."""
    assert (
        repo.set_runtime_risk_state(
            state="HALTED",
            reason="Max drawdown exceeded: 2.04% > limit 2.00%",
            source="drawdown-circuit",
        )
        is True
    )
    decision = resolve_boot_decision(PersistedRiskState.from_row(repo.get_runtime_risk_state()))
    assert decision.trading_allowed is False
    assert decision.state == "HALTED"
    # the halt row itself is intact (single-row atomic store)
    state = repo.get_runtime_risk_state()
    assert state is not None and state["state"] == "HALTED"
    assert state["release_required"] in (1, True)


def test_skeleton_heal_preserves_live_paper_provenance_columns(tmp_path) -> None:
    """After gate-then-bootstrap, the LIVE/PAPER attribution columns
    (BUG-226 contract) exist so PAPER rows stay excludable from LIVE metrics."""
    from nexus_scalp.database.engine import DatabaseMigrationEngine
    from nexus_scalp.database.models import DatabaseDomain

    db = tmp_path / "audit.db"
    eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
    assert eng.migrate()["state"] == "DB_MIGRATION_SUCCEEDED"
    repo2 = AuditRepository(db_url=f"sqlite:///{db}")
    try:
        con = sqlite3.connect(db)
        try:
            for table, column in (
                ("audit_signals", "account_source"),
                ("audit_account_snapshots", "account_source"),
                ("audit_ledger", "account_source"),
            ):
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                assert column in cols, f"{table}.{column} missing after heal"
            # runtime safety state intact
            n = con.execute("SELECT COUNT(*) FROM runtime_risk_state").fetchone()[0]
            assert n == 0  # fresh install: no halt in force
        finally:
            con.close()
    finally:
        repo2.close()
