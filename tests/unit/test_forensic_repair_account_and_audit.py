"""Forensic repair regression tests (2026-08-30 forensic repair pass).

Covers two P1 live-safety repairs:

1. MT5 account identity verification (P1, live safety):
   DirectMT5Adapter.connect() must fail-safe (AUTHENTICATION_ERROR, connect
   returns False) when the terminal's actual logged-in account differs from
   the configured expected account. A wrong account must never be able to
   place orders through this adapter.

2. AuditRepository silent read-failure observability (P1, persistence):
   Integrity-relevant read helpers used by the reconciliation close-loop
   (has_ledger_opened / count_ledger_opened_unclosed / get_ledger_opened /
   get_broker_deals_for_position) must LOG the exception before returning
   their degraded sentinel value.
"""

from __future__ import annotations

import sqlite3
import sys
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.mt5 import mt5_adapter as mt5_adapter_module
from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

# ---------------------------------------------------------------------------
# 1. MT5 account identity safety
# ---------------------------------------------------------------------------


class _FakeTerminalInfo:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.name = "Fake Terminal"


class _FakeAccountInfo:
    def __init__(self, login: int, server: str = "FakeServer") -> None:
        self.login = login
        self.server = server
        self.company = "FakeBroker"


class _FakeMT5WrongAccount:
    last_error_value = (0, "ok")

    @staticmethod
    def initialize(**_kwargs: Any) -> bool:
        return True

    @staticmethod
    def terminal_info() -> _FakeTerminalInfo:
        return _FakeTerminalInfo()

    @staticmethod
    def version() -> tuple[int, int, int]:
        return (5, 0, 0)

    @staticmethod
    def login(login: int, password: str, server: str) -> bool:
        return True

    @staticmethod
    def account_info() -> _FakeAccountInfo:
        return _FakeAccountInfo(login=9999999)

    @staticmethod
    def shutdown() -> None:
        return None


class _FakeMT5MatchingAccount:
    last_error_value = (0, "ok")

    @staticmethod
    def initialize(**_kwargs: Any) -> bool:
        return True

    @staticmethod
    def terminal_info() -> _FakeTerminalInfo:
        return _FakeTerminalInfo()

    @staticmethod
    def version() -> tuple[int, int, int]:
        return (5, 0, 0)

    @staticmethod
    def login(login: int, password: str, server: str) -> bool:
        return True

    @staticmethod
    def account_info() -> _FakeAccountInfo:
        return _FakeAccountInfo(login=111222)

    @staticmethod
    def shutdown() -> None:
        return None


@pytest.fixture
def fake_mt5_module():
    original_module = sys.modules.get("MetaTrader5")
    original_has = mt5_adapter_module.HAS_NATIVE_MT5
    original_mt5 = mt5_adapter_module.mt5

    def _install(mod: Any) -> None:
        sys.modules["MetaTrader5"] = mod
        mt5_adapter_module.HAS_NATIVE_MT5 = True
        mt5_adapter_module.mt5 = mod

    yield _install

    if original_module is not None:
        sys.modules["MetaTrader5"] = original_module
    else:
        sys.modules.pop("MetaTrader5", None)
    mt5_adapter_module.HAS_NATIVE_MT5 = original_has
    mt5_adapter_module.mt5 = original_mt5


def _make_adapter(expected_account: int | None) -> DirectMT5Adapter:
    return DirectMT5Adapter(
        account=expected_account,
        password="secret",
        server="FakeServer",
        timeout=1000,
        retries=1,
    )


def test_connect_fails_safe_when_logged_in_account_differs_from_expected(
    fake_mt5_module, monkeypatch
) -> None:
    fake_mt5_module(_FakeMT5WrongAccount)
    adapter = _make_adapter(expected_account=111222)

    logged = []
    monkeypatch.setattr(
        mt5_adapter_module.logger, "critical", lambda msg, *a, **k: logged.append(msg)
    )

    result = adapter.connect()

    assert result is False
    assert adapter._connected is False
    assert (
        adapter.connection_state().state
        == mt5_adapter_module.MT5ConnectionState.AUTHENTICATION_ERROR
    )
    assert any("ACCOUNT MISMATCH" in m for m in logged)


def test_connect_succeeds_when_logged_in_account_matches_expected(fake_mt5_module) -> None:
    fake_mt5_module(_FakeMT5MatchingAccount)
    adapter = _make_adapter(expected_account=111222)
    assert adapter.connect() is True


# ---------------------------------------------------------------------------
# 2. AuditRepository silent read-failure observability
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_repo(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'audit_test.db'}")
    repo._start_background_worker()
    yield repo
    repo._running = False
    try:
        repo._worker_thread.join(timeout=2.0)
    except Exception:
        pass


def test_audit_read_helpers_log_on_error(audit_repo, monkeypatch):
    """P1: database read failures in reconciliation helpers must be logged."""
    logged_errors = []
    import nexus_scalp.adapters.database.audit_repository as ar_mod

    monkeypatch.setattr(ar_mod.logger, "error", lambda msg, *a, **k: logged_errors.append(msg))

    def _failing_connect(*_a: Any, **_k: Any):
        raise sqlite3.OperationalError("simulated locked database")

    monkeypatch.setattr(ar_mod.sqlite3, "connect", _failing_connect)

    assert audit_repo.has_ledger_opened(ticket=42) is False
    assert audit_repo.count_ledger_opened_unclosed() == -1
    assert audit_repo.get_ledger_opened(ticket=7) is None
    assert audit_repo.get_broker_deals_for_position(position_id=1) == []

    assert len(logged_errors) >= 4
    assert any("has_ledger_opened failed" in m for m in logged_errors)
    assert any("count_ledger_opened_unclosed failed" in m for m in logged_errors)
    assert any("get_ledger_opened failed" in m for m in logged_errors)
    assert any("get_broker_deals_for_position failed" in m for m in logged_errors)


def test_read_helpers_still_work_on_healthy_db(audit_repo):
    audit_repo.log_ledger_opened(
        ticket=555,
        symbol="XAUUSD",
        direction="BUY",
        volume=0.01,
        entry_price=2000.0,
        timestamp_str="2026-08-30T00:00:00+00:00",
    )
    audit_repo.flush(timeout_sec=5.0)

    assert audit_repo.has_ledger_opened(ticket=555) is True
    assert audit_repo.has_ledger_opened(ticket=999) is False
    assert audit_repo.get_ledger_opened(ticket=555) is not None
    assert audit_repo.count_ledger_opened_unclosed() >= 1
