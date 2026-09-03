"""
BUG-226 regression tests: PAPER provenance isolation.
=====================================================
The paper simulator (PaperMT5Adapter) allocates tickets from 100001 and seeds
its account at balance==equity==margin_free==10000.0. When a PAPER session
shares the canonical artifacts/audit.db (paper boot or hot-swap), those rows
contaminated every dashboard metric: a synthetic -75,341.78 trade (ticket
100002, entry at the 2000.08 seed price, exit at the live 4430.46) and a
647-row 10000.0 equity plateau produced net -$82k and a fake -74.7% drawdown.

Contract: PAPER rows are TAGGED at write time and EXCLUDED at read time;
raw evidence is never rewritten (master contract s47).
"""

from __future__ import annotations

import pytest

from nexus_scalp.accounting import AccountingCore
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.models import AccountInfo


@pytest.fixture()
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'audit.db'}")
    r._start_background_worker()
    yield r
    r.close()


def _paper_open_close(repo: AuditRepository) -> None:
    repo.log_ledger_opened(
        ticket=100002,
        symbol="XAUUSD",
        direction="SELL_LIMIT",
        volume=0.31,
        entry_price=2000.08,
        timestamp_str="2026-09-03T07:40:04+00:00",
        entry_reason="PURE_AI",
        ai_confidence_at_open=0.4655,
        market_regime_at_open="RANGING_MEAN_REVERSION",
        initial_sl_price=2001.68,
        account_source="PAPER",
    )
    repo.log_ledger_closed(
        ticket=100002,
        symbol="XAUUSD",
        direction="SELL_LIMIT",
        volume=0.31,
        entry_price=2000.08,
        exit_price=4430.46,
        status="CLOSED",
        pnl=-75341.78,
        commission=0.0,
        swap=0.0,
        duration_sec=84.8,
        timestamp_str="2026-09-03T07:41:29+00:00",
        exit_mechanism="MANUAL_CLOSE",
        order_id="4e5b5a05-5936-4a4e-8d0a-57659b27f271",
        open_time="2026-09-03T07:40:04+00:00",
        close_time="2026-09-03T07:41:29+00:00",
        entry_reason="PURE_AI",
        ai_confidence_at_open=0.4655,
        market_regime_at_open="RANGING_MEAN_REVERSION",
        account_source="PAPER",
    )


def _live_open_close(
    repo: AuditRepository, ticket: int, pnl: float, entry: float, exit_: float
) -> None:
    repo.log_ledger_opened(
        ticket=ticket,
        symbol="XAUUSD",
        direction="BUY",
        volume=0.10,
        entry_price=entry,
        timestamp_str="2026-09-03T10:00:00+00:00",
        entry_reason="PURE_AI",
        initial_sl_price=entry - 5.0,
        account_source="LIVE",
    )
    repo.log_ledger_closed(
        ticket=ticket,
        symbol="XAUUSD",
        direction="BUY",
        volume=0.10,
        entry_price=entry,
        exit_price=exit_,
        status="CLOSED",
        pnl=pnl,
        commission=0.0,
        swap=0.0,
        duration_sec=60.0,
        timestamp_str="2026-09-03T10:01:00+00:00",
        exit_mechanism="SYSTEM_CLOSE",
        open_time="2026-09-03T10:00:00+00:00",
        close_time="2026-09-03T10:01:00+00:00",
        entry_reason="PURE_AI",
        account_source="LIVE",
    )


def _flush(repo: AuditRepository) -> None:
    import time

    repo._queue.join()
    time.sleep(0.2)


def test_paper_tagged_rows_never_reach_metrics(repo):
    """A PAPER-tagged -75k trade must not move net PnL or the trade count."""
    _paper_open_close(repo)
    _live_open_close(repo, 152569686362, 7.0, 4327.65, 4327.90)
    _flush(repo)

    core = AccountingCore(audit_repo=repo, adapter=None)
    trades = core.load_trades()

    tickets = [t.ticket for t in trades]
    assert 100002 not in tickets, "PAPER ticket leaked into canonical metrics"
    assert 152569686362 in tickets, "REAL trade was filtered out"
    assert all(t.net_pnl > -1000 for t in trades), "synthetic whale still present"


def test_legacy_untagged_paper_ticket_excluded(repo):
    """Untagged low-ticket rows (paper-era legacy) are excluded by ticket space."""
    # simulate legacy row: no account_source tag at all
    repo.log_ledger_opened(
        ticket=100015,
        symbol="XAUUSD",
        direction="SELL_LIMIT",
        volume=0.23,
        entry_price=1997.77,
        timestamp_str="2026-09-03T02:59:00+00:00",
        initial_sl_price=1999.91,
    )
    repo.log_ledger_closed(
        ticket=100015,
        symbol="XAUUSD",
        direction="SELL_LIMIT",
        volume=0.23,
        entry_price=1997.77,
        exit_price=1999.36,
        status="CLOSED",
        pnl=-36.57,
        commission=0.0,
        swap=0.0,
        duration_sec=158.8,
        timestamp_str="2026-09-03T03:01:39+00:00",
        exit_mechanism="HOLD_SCORE_DECAY",
        open_time="2026-09-03T02:59:00+00:00",
        close_time="2026-09-03T03:01:39+00:00",
    )
    _live_open_close(repo, 152569680109, 8.82, 4327.68, 4327.26)
    _flush(repo)

    core = AccountingCore(audit_repo=repo, adapter=None)
    tickets = [t.ticket for t in core.load_trades()]
    assert 100015 not in tickets
    assert 152569680109 in tickets


def test_hybrid_seed_entry_live_exit_row_excluded(repo):
    """Defensive net: entry at the paper seed price, exit at live price."""
    repo.log_ledger_closed(
        ticket=100002,
        symbol="XAUUSD",
        direction="SELL_LIMIT",
        volume=0.31,
        entry_price=2000.08,
        exit_price=4430.46,
        status="CLOSED",
        pnl=-75341.78,
        commission=0.0,
        swap=0.0,
        duration_sec=84.8,
        timestamp_str="2026-09-03T07:41:29+00:00",
        exit_mechanism="MANUAL_CLOSE",
        open_time="2026-09-03T07:40:04+00:00",
        close_time="2026-09-03T07:41:29+00:00",
    )
    _flush(repo)

    core = AccountingCore(audit_repo=repo, adapter=None)
    assert core.load_trades() == []


def test_paper_seed_plateau_snapshots_excluded(repo, tmp_path):
    """balance==equity==margin_free==10000.0 rows must not create fake drawdown."""
    import sqlite3

    from nexus_scalp.accounting.periods import utc_now

    now = utc_now()
    # paper seed plateau + real live snapshot pair
    for balance, equity, margin_free in [
        (10000.0, 10000.0, 10000.0),  # paper seed
        (31000.0, 31100.0, 30000.0),  # real
        (10000.0, 10000.0, 10000.0),  # paper seed again
        (31100.0, 31050.0, 30000.0),  # real
    ]:
        con = sqlite3.connect(str(tmp_path / "audit.db"))
        con.execute(
            "INSERT INTO audit_account_snapshots (timestamp, balance, equity, margin_free, peak_equity)"
            " VALUES (?,?,?,?,?)",
            (now.strftime("%Y-%m-%d %H:%M:%S"), balance, equity, margin_free, 39601.37),
        )
        con.commit()
        con.close()

    core = AccountingCore(audit_repo=repo, adapter=None)
    snaps = core.load_snapshots()
    assert len(snaps) == 2, "paper seed plateau rows leaked into the equity series"
    assert all(s.equity > 20000 for s in snaps)

    dd = core.drawdown_report()
    assert dd.max_drawdown_pct < 1.0, f"fake plateau drawdown leaked: {dd.max_drawdown_pct}%"


def test_broker_trade_fallback_still_works(repo):
    """With zero engine-autopsy rows the broker-history path stays intact."""
    core = AccountingCore(audit_repo=repo, adapter=None)
    # audit_broker_trades empty -> empty list (no fabrication)
    assert core.load_trades() == []
