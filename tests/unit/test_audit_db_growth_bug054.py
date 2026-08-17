"""BUG-054 regression suite: persistent signal dedup, guard telemetry,
lean payloads, and bounded retention purge.

Covers spec §23:
- identical decision twice -> exactly one row (across request_ids)
- different decision -> rows preserved
- TICK_DUPLICATE_SUPPRESSED / ORDER_FREQUENCY_THROTTLED -> telemetry only
- MAX_EXPOSURE_REACHED -> real audit row preserved
- payload contains only approved fields, no duplicate probabilities
- retention purges old data, keeps fresh, never touches ledger
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal


def make_proposal(**over: object) -> TradeProposal:
    base: dict[str, object] = dict(
        request_id="req-1",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.NO_TRADE,
        confidence=0.63,
        proposed_entry=4413.2,
        stop_loss=4374.58,
        take_profit=4455.68,
        risk_reward_ratio=1.1,
        reason_code="REGIME_RANGING_MEAN_REVERSION",
        model_action="BUY_LIMIT",
        buy_probability=0.239,
        sell_probability=0.1,
        no_trade_probability=0.66,
        regime="RANGING_MEAN_REVERSION",
        regime_confidence=0.8,
        risk_allowed=True,
        guardian_status="ACTIVE",
        execution_mode="STANDARD",
        decision_stage="STANDARD_EVAL",
        blocked_by=None,
        htf_score=0.5,
        smc_score=0.7,
        confidence_before_filters=0.7,
        confidence_after_filters=0.63,
        rejection_reason="",
    )
    base.update(over)
    return TradeProposal(**base)  # type: ignore[arg-type]


@pytest.fixture()
def repo(tmp_path) -> AuditRepository:
    r = AuditRepository(
        db_url=f"sqlite:///{tmp_path / 'test.db'}",
        flush_interval_sec=0.05,
    )
    yield r
    r.close()


def _flush(repo: AuditRepository, seconds: float = 1.0) -> None:
    """Let the background worker drain the queue."""
    repo._queue.join()
    time.sleep(seconds)


def _conn(repo: AuditRepository) -> sqlite3.Connection:
    return sqlite3.connect(repo._db_path, timeout=5.0)


def test_identical_decision_collapses_to_one_row(repo: AuditRepository) -> None:
    p = make_proposal()
    repo.log_signal(p)
    # Same decision evaluated again: new UUID, same candle/action/stage/mode/reason.
    repo.log_signal(make_proposal(request_id="req-2"))
    _flush(repo)
    with _conn(repo) as c:
        n = c.execute("SELECT COUNT(*) FROM audit_signals WHERE symbol='XAUUSD'").fetchone()[0]
    assert n == 1


def test_different_candle_is_new_decision(repo: AuditRepository) -> None:
    repo.log_signal(make_proposal())
    repo.log_signal(make_proposal(generated_at=datetime.now(UTC) + timedelta(minutes=1)))
    _flush(repo)
    with _conn(repo) as c:
        n = c.execute("SELECT COUNT(*) FROM audit_signals WHERE symbol='XAUUSD'").fetchone()[0]
    assert n == 2


def test_guard_codes_go_to_telemetry_not_signals(repo: AuditRepository) -> None:
    for i in range(5):
        repo.log_signal(
            make_proposal(request_id=f"dup-{i}", reason_code="TICK_DUPLICATE_SUPPRESSED")
        )
    for i in range(3):
        repo.log_signal(
            make_proposal(request_id=f"thr-{i}", reason_code="ORDER_FREQUENCY_THROTTLED")
        )
    _flush(repo)
    with _conn(repo) as c:
        n_sig = c.execute("SELECT COUNT(*) FROM audit_signals").fetchone()[0]
        rows = c.execute(
            "SELECT reason_code, SUM(count) FROM audit_guard_telemetry GROUP BY reason_code"
        ).fetchall()
    assert n_sig == 0
    assert dict(rows) == {
        "TICK_DUPLICATE_SUPPRESSED": 5,
        "ORDER_FREQUENCY_THROTTLED": 3,
    }


def test_max_exposure_reached_remains_auditable(repo: AuditRepository) -> None:
    repo.log_signal(make_proposal(request_id="max-1", reason_code="MAX_EXPOSURE_REACHED"))
    _flush(repo)
    with _conn(repo) as c:
        n = c.execute(
            "SELECT COUNT(*) FROM audit_signals WHERE reason_code='MAX_EXPOSURE_REACHED'"
        ).fetchone()[0]
    assert n == 1


def test_payload_is_minimal_and_approved_only(repo: AuditRepository) -> None:
    repo.log_signal(make_proposal(request_id="pay-1", reason_code="MAX_EXPOSURE_REACHED"))
    _flush(repo)
    with _conn(repo) as c:
        row = c.execute(
            "SELECT payload FROM audit_signals WHERE reason_code='MAX_EXPOSURE_REACHED'"
        ).fetchone()
    payload = json.loads(row[0])
    assert set(payload.keys()) == {
        "model_action",
        "ai_buy_probability",
        "ai_sell_probability",
        "ai_no_trade_probability",
        "regime_confidence",
        "risk_allowed",
        "guardian_status",
        "rejection_reason",
    }
    assert "risk_checks" not in payload
    assert "buy_probability" not in payload
    assert len(row[0]) < 400  # was ~1.2KB


def test_purge_removes_old_keeps_fresh_and_never_touches_ledger(
    repo: AuditRepository, tmp_path
) -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    repo.log_signal(
        make_proposal(request_id="old-1", generated_at=old, reason_code="REGIME_TRENDING_MOMENTUM")
    )
    repo.log_signal(make_proposal(request_id="fresh-1", reason_code="REGIME_TRENDING_MOMENTUM"))
    _flush(repo)
    # Seed a ledger row that MUST survive.
    with _conn(repo) as c:
        c.execute(
            "INSERT INTO audit_ledger (ticket, symbol, direction, volume, entry_price, status, timestamp) "
            "VALUES (9999, 'XAUUSD', 'BUY', 0.1, 4400.0, 'CLOSED', ?)",
            (datetime.now(UTC).isoformat(),),
        )
        c.execute(
            "INSERT INTO position_lifecycle_events "
            "(event_key, ticket, trade_id, experience_id, symbol, timeframe, event_type, sequence, event_timestamp, market_context, position_snapshot, payload) "
            "VALUES ('lev_old1', '999', '', '', 'XAUUSD', 'M1', 'POSITION_MOVING', 1, ?, '{}', '{}', '{}')",
            (old.isoformat(),),
        )
        c.execute(
            "INSERT INTO position_lifecycle_events "
            "(event_key, ticket, trade_id, experience_id, symbol, timeframe, event_type, sequence, event_timestamp, market_context, position_snapshot, payload) "
            "VALUES ('lev_fresh', '998', '', '', 'XAUUSD', 'M1', 'POSITION_MOVING', 1, ?, '{}', '{}', '{}')",
            (datetime.now(UTC).isoformat(),),
        )
        c.commit()

    res = repo.purge_old_audit_data(signal_retention_days=7, moving_retention_days=3)
    assert res["deleted"]["audit_signals"] == 1

    with _conn(repo) as c:
        n_old = c.execute("SELECT COUNT(*) FROM audit_signals WHERE request_id='old-1'").fetchone()[
            0
        ]
        n_fresh = c.execute(
            "SELECT COUNT(*) FROM audit_signals WHERE request_id='fresh-1'"
        ).fetchone()[0]
        n_lev_old = c.execute(
            "SELECT COUNT(*) FROM position_lifecycle_events WHERE event_key='lev_old1'"
        ).fetchone()[0]
        n_lev_fresh = c.execute(
            "SELECT COUNT(*) FROM position_lifecycle_events WHERE event_key='lev_fresh'"
        ).fetchone()[0]
        n_ledger = c.execute("SELECT COUNT(*) FROM audit_ledger WHERE ticket=9999").fetchone()[0]
    assert n_old == 0
    assert n_fresh == 1
    assert n_lev_old == 0
    assert n_lev_fresh == 1
    assert n_ledger == 1  # accounting truth untouched
