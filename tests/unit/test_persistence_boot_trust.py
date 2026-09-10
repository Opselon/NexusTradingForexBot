"""Agent-17 P0 regression battery 1: boot trust (fail-closed under DB uncertainty).

PINS the runtime_risk_state trust boundary:
  T1  healthy HALT row -> boot REFUSED (regression guard: pre-existing contract).
  T2  read failure (corrupt image / unavailable DB) -> boot REFUSED with
      state=DB_READ_UNCERTAIN (was: trading_allowed=True via NO_PERSISTED_STATE).
  T3  unset row (fresh install, healthy read returning None) -> boot allowed.
  T4  release contract: None row still refuses release (no resurrection).

Durable contracts only — no timing, no I/O mocking beyond the read path.
"""

from __future__ import annotations

import sqlite3

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.risk.runtime_safety import PersistedRiskState, resolve_boot_decision


@pytest.fixture()
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'trust.db'}")
    try:
        yield r
    finally:
        r.close()


def _boot(repo: AuditRepository):
    return resolve_boot_decision(PersistedRiskState.from_row(repo.get_runtime_risk_state()))


def test_healthy_halt_row_blocks_boot(repo) -> None:
    assert repo.set_runtime_risk_state(state="HALTED", reason="drawdown", source="t1") is True
    decision = _boot(repo)
    assert decision.trading_allowed is False
    assert decision.state == "HALTED"


def test_read_failure_fails_closed_not_open(repo, monkeypatch) -> None:
    """THE P0 PIN: DB uncertainty at boot must never read as 'safe to trade'."""
    assert repo.set_runtime_risk_state(state="KILL_SWITCH", reason="operator", source="t2") is True

    def broken_read(timeout):
        raise sqlite3.OperationalError("database disk image is malformed")

    monkeypatch.setattr(repo, "_connect_sqlite", broken_read)
    from nexus_scalp.adapters.database.audit_repository import RuntimeRiskStateReadError

    with pytest.raises(RuntimeRiskStateReadError):
        repo.get_runtime_risk_state()

    class _FakeOM:
        """Duck-typed engine surface consumed by _restore_runtime_risk_state."""

        def __init__(self, audit):
            self.audit = audit
            self._runtime_risk_state = "RUNNING"
            self._runtime_risk_detail = ""
            self._halt_reason = ""
            self._halt_triggered_at = ""
            self._running = True

        def _apply_persisted_halt(self, decision):
            self._runtime_risk_state = decision.state
            self._runtime_risk_detail = decision.detail
            if decision.state in ("HALTED", "KILL_SWITCH", "DB_READ_UNCERTAIN"):
                self._running = False
                self._halt_reason = decision.detail

    from nexus_scalp.application.live_engine import LiveEngine

    om = _FakeOM(repo)
    decision = LiveEngine._restore_runtime_risk_state(om)
    assert decision.trading_allowed is False
    assert decision.state == "DB_READ_UNCERTAIN"
    assert om._running is False


def test_fresh_install_with_healthy_read_allows_boot(repo) -> None:
    assert repo.get_runtime_risk_state() is None
    decision = resolve_boot_decision(PersistedRiskState.from_row(repo.get_runtime_risk_state()))
    assert decision.trading_allowed is True
    assert decision.state == "RUNNING"


def test_release_of_unreadable_state_is_refused(repo, monkeypatch) -> None:
    """A failed read must not enable release bookkeeping against unknown state."""
    assert repo.set_runtime_risk_state(state="HALTED", reason="x", source="t4") is True

    def broken_read(timeout):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(repo, "_connect_sqlite", broken_read)
    from nexus_scalp.adapters.database.audit_repository import RuntimeRiskStateReadError

    with pytest.raises(RuntimeRiskStateReadError):
        repo.release_runtime_risk_state(actor="op", note="try")
