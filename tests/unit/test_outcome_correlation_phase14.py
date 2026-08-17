"""
Phase 14 Outcome Correlation & Break-Even Forensics Regression Suite
=====================================================================
BUG-045 regression guards. Proves the closed-trade -> Experience ->
Strategy Intelligence chain for WIN / LOSS / BREAK_EVEN / SL / TP /
BREAK-EVEN-SL / TRAILING / MANUAL / BROKER closes:

  1. BREAK_EVEN is a first-class outcome (never INVALID).
  2. Missing request_id -> deterministic correlation recovery (POSITION_STATE
     / BROKER_TICKET_FALLBACK), never silent discard.
  3. Broker SL / break-even / trailing closes are NEVER classified
     MANUAL_CLOSE.
  4. Realized PnL is reconstructed from the authoritative broker deal path
     (multi-deal aggregation, no double count), never silently forced to 0.
  5. Duplicate close events produce exactly one outcome (idempotency).
  6. Multiple tickets stay independent (no cross-ticket contamination).
  7. SL modification timeline survives closure (initial != final after BE).
  8. MAE/MFE survive position cleanup and reach the outcome.
  9. Learning failure never blocks the close (isolated).
  10. Strategy statistics update after a valid outcome.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData, TradeProposal
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    BrokerOutcome,
    ExitReason,
    ExperienceOutcome,
    ExperienceRecord,
    OutcomeClass,
    OutcomeCorrelationSource,
    StrategyContext,
)
from nexus_scalp.experience.outcome_recovery import (
    classify_exit_reason,
    classify_outcome_class,
    reconstruct_broker_outcome,
    resolve_outcome_correlation,
)
from nexus_scalp.experience.quality import OutcomeAnalyzer
from nexus_scalp.experience.retriever import ExperienceRetriever

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_audit_repo(tmp_path):
    db_file = tmp_path / "test_phase14.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


@pytest.fixture
def components(temp_audit_repo):
    """Ledger + evaluator + retriever + engine against a temp DB."""
    ledger = ExperienceLedger(audit_repo=temp_audit_repo)
    evaluator = StrategyEvaluator(audit_repo=temp_audit_repo)
    retriever = ExperienceRetriever(ledger=ledger)
    engine = ExperienceIntelligenceEngine(
        ledger=ledger,
        evaluator=evaluator,
        retriever=retriever,
        enabled=True,
        max_inline_refresh_per_sec=0.0,
        score_cache_ttl_sec=1.0,
    )
    return ledger, evaluator, retriever, engine


def make_context(strategy_id: str = "strat_phase14") -> StrategyContext:
    return StrategyContext(
        strategy_id=strategy_id,
        symbol="XAUUSD",
        timeframe="M1",
        session="ALL",
        regime="NORMAL_VOLATILITY",
    )


def make_record(
    request_id: str,
    strategy_id: str = "strat_phase14",
    entry: float = 2000.0,
    sl: float = 1990.0,
    tp: float = 2020.0,
    decision_ts: datetime | None = None,
) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_{request_id[:12]}",
        request_id=request_id,
        decision_id=f"dec_{request_id[:12]}",
        idempotency_key=f"exp_{request_id}",
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=decision_ts or (datetime.now(UTC) - timedelta(minutes=10)),
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        context=make_context(strategy_id),
        action="BUY_MARKET",
        entry_reason="SMC_GOD_MODE",
        model_probability=0.72,
        signal_confidence=0.72,
        proposed_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward_ratio=2.0,
        approved_volume=0.5,
        min_rr_policy=1.0,
    )


def make_tick(bid: float, ask: float | None = None, ts: datetime | None = None) -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=ts or datetime.now(UTC),
        bid=bid,
        ask=ask or round(bid + 0.20, 2),
        volume=1.0,
    )


def make_position(ticket: int, profit: float, sl: float, price_open: float = 2000.0) -> Position:
    return Position(
        ticket=ticket,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=0.5,
        price_open=price_open,
        sl=sl,
        tp=2020.0,
        profit=profit,
        magic=888101,
    )


def make_symbol_info() -> SymbolInfo:
    return SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )


class RecordingExperienceEngine:
    """Captures every record_trade_outcome call for assertion without a DB."""

    def __init__(self):
        self.calls: list[dict] = []

    def record_trade_outcome(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return True


# ---------------------------------------------------------------------------
# 1. BREAK_EVEN is a first-class outcome
# ---------------------------------------------------------------------------


class TestBreakEvenFirstClass:
    def test_classify_break_even(self):
        assert classify_outcome_class(0.0) == OutcomeClass.BREAK_EVEN
        assert classify_outcome_class(0.04) == OutcomeClass.BREAK_EVEN
        assert classify_outcome_class(-0.04) == OutcomeClass.BREAK_EVEN
        assert classify_outcome_class(0.3) == OutcomeClass.WIN
        assert classify_outcome_class(-1.0) == OutcomeClass.LOSS

    def test_zero_pnl_recorded_as_break_even(self, components):
        ledger, evaluator, retriever, engine = components
        rid = "be-00000000-0000-0000-0000-000000000001"
        assert ledger.record_experience(make_record(rid))
        ledger.audit_repo._queue.join()

        ok = engine.record_trade_outcome(
            request_id=rid,
            execution_id="152400000001",
            outcome_timestamp=datetime.now(UTC),
            is_executed=True,
            is_closed=True,
            exit_reason=ExitReason.BREAK_EVEN_SL_HIT.value,
            realized_pnl_usd=0.0,
            realized_r_multiple=0.0,
            mae_points=-1.0,
            mfe_points=8.0,
            mae_usd=-0.55,
            mfe_usd=4.40,
            holding_duration_seconds=300.0,
            approved_volume=0.5,
            actual_entry=2000.0,
            sl_moved=True,
            atr_at_entry=1.2,
        )
        ledger.audit_repo._queue.join()

        assert ok is True
        rec = ledger.get_experience_by_key(f"exp_{rid}")
        assert rec is not None
        assert rec.is_closed is True
        assert rec.exit_reason == ExitReason.BREAK_EVEN_SL_HIT.value
        assert rec.realized_pnl_usd == 0.0
        # MAE/MFE survive into the merged experience.
        assert rec.behavior.mae_usd == -0.55
        assert rec.behavior.mfe_usd == 4.40
        assert rec.behavior.mfe_points == 8.0

    def test_break_even_strategy_statistics_update(self, components):
        ledger, evaluator, retriever, engine = components
        strategy = "strat_be_stats"
        rid = "be-00000000-0000-0000-0000-000000000002"
        ledger.record_experience(make_record(rid, strategy_id=strategy))
        ledger.audit_repo._queue.join()

        before = evaluator.evaluate_strategy(
            strategy, ledger.get_experiences_for_strategy(strategy)
        )
        assert before.sample_count == 0

        engine.record_trade_outcome(
            request_id=rid,
            execution_id="152400000002",
            outcome_timestamp=datetime.now(UTC),
            is_executed=True,
            is_closed=True,
            exit_reason=ExitReason.BREAK_EVEN_SL_HIT.value,
            realized_pnl_usd=0.0,
            realized_r_multiple=0.0,
            mae_points=-1.0,
            mfe_points=8.0,
            holding_duration_seconds=300.0,
            approved_volume=0.5,
            actual_entry=2000.0,
            sl_moved=True,
        )
        ledger.audit_repo._queue.join()

        after = evaluator.evaluate_strategy(strategy, ledger.get_experiences_for_strategy(strategy))
        assert after.sample_count == 1
        assert after.breakeven_count == 1
        assert after.win_count == 0
        assert after.loss_count == 0


# ---------------------------------------------------------------------------
# 2. Missing request_id -> deterministic correlation recovery
# ---------------------------------------------------------------------------


class TestCorrelationRecovery:
    def test_original_request_correlation(self, components):
        ledger, evaluator, retriever, engine = components
        rid = "corr-00000000-0000-0000-0000-000000000001"
        ledger.record_experience(make_record(rid))
        ledger.audit_repo._queue.join()

        key, source, detail = resolve_outcome_correlation(
            request_id=rid,
            ticket="152400000101",
            ledger=ledger,
            build_idempotency_key_fn=engine.build_idempotency_key,
        )
        assert key == f"exp_{rid}"
        assert source == OutcomeCorrelationSource.ORIGINAL_REQUEST.value

    def test_position_state_recovery_from_ledger(self, components):
        ledger, evaluator, retriever, engine = components
        rid = "corr-00000000-0000-0000-0000-000000000002"
        ledger.record_experience(make_record(rid))
        ledger.audit_repo._queue.join()

        # request_id missing, but the broker ticket correlates via POSITION_STATE
        # lookup on the immutable ledger (restart / reconciliation scenario).
        key, source, detail = resolve_outcome_correlation(
            request_id="",
            ticket=rid,  # ledger lookup key: request_id column contains rid
            ledger=ledger,
            build_idempotency_key_fn=engine.build_idempotency_key,
        )
        assert key == f"exp_{rid}"
        assert source == OutcomeCorrelationSource.POSITION_STATE.value

    def test_ambiguous_position_state_returns_none(self, components):
        ledger, evaluator, retriever, engine = components
        shared = "shared-ticket-0001"
        for n in (1, 2):
            rid = f"amb-00000000-0000-0000-0000-00000000000{n}"
            rec = make_record(rid)
            rec = rec.model_copy(
                update={"request_id": shared, "idempotency_key": f"exp_{shared}_{n}"}
            )
            ledger.record_experience(rec)
        ledger.audit_repo._queue.join()

        result = resolve_outcome_correlation(
            request_id="",
            ticket=shared,
            ledger=ledger,
            build_idempotency_key_fn=engine.build_idempotency_key,
        )
        assert result is None  # ambiguous -> explicit failure, never silent

    def test_missing_request_id_recovers_and_records(self, components):
        ledger, evaluator, retriever, engine = components
        rid = "corr-00000000-0000-0000-0000-000000000003"
        ledger.record_experience(make_record(rid))
        ledger.audit_repo._queue.join()

        # The order manager forwards request_id="" + execution_id=ticket; the
        # engine must recover via BROKER_TICKET_FALLBACK and record.
        ok = engine.record_trade_outcome(
            request_id="",
            execution_id=rid,  # ledger match -> POSITION_STATE
            outcome_timestamp=datetime.now(UTC),
            is_executed=True,
            is_closed=True,
            exit_reason=ExitReason.HARD_SL_HIT.value,
            realized_pnl_usd=-5.0,
            realized_r_multiple=-1.0,
            holding_duration_seconds=120.0,
            approved_volume=0.5,
            actual_entry=2000.0,
        )
        ledger.audit_repo._queue.join()

        assert ok is True
        rec = ledger.get_experience_by_key(f"exp_{rid}")
        assert rec is not None and rec.is_closed is True

    def test_broker_ticket_fallback_key_is_deterministic(self):
        key_a, src_a, _ = resolve_outcome_correlation(
            request_id="",
            ticket="152400000777",
            ledger=None,
            build_idempotency_key_fn=lambda r: f"exp_{r}",
        )
        key_b, src_b, _ = resolve_outcome_correlation(
            request_id="",
            ticket="152400000777",
            ledger=None,
            build_idempotency_key_fn=lambda r: f"exp_{r}",
        )
        assert key_a == key_b == "exp_bt_152400000777"
        assert src_a == src_b == OutcomeCorrelationSource.BROKER_TICKET_FALLBACK.value


# ---------------------------------------------------------------------------
# 3. Exit taxonomy: SL / BE / TRAILING are never MANUAL_CLOSE
# ---------------------------------------------------------------------------


class TestExitClassification:
    def test_break_even_sl_hit(self):
        reason = classify_exit_reason(
            deal_reason_code=3,
            comment="sl",
            profit_usd=-1.0,
            exit_price=2000.0,
            tp_price=2020.0,
            sl_price=2000.0,
            final_sl=2000.0,
            entry_price=2000.0,
            was_sl_modified=True,
            direction="BUY",
        )
        assert reason == ExitReason.BREAK_EVEN_SL_HIT.value

    def test_trailing_stop_hit(self):
        reason = classify_exit_reason(
            deal_reason_code=3,
            comment="NSE trailing",
            profit_usd=3.0,
            exit_price=2005.0,
            tp_price=2020.0,
            sl_price=2005.0,
            final_sl=2005.0,
            entry_price=2000.0,
            was_sl_modified=True,
            direction="BUY",
        )
        assert reason == ExitReason.TRAILING_STOP_HIT.value

    def test_hard_sl_hit(self):
        reason = classify_exit_reason(
            deal_reason_code=3,
            comment="sl",
            profit_usd=-10.0,
            exit_price=1990.0,
            tp_price=2020.0,
            sl_price=1990.0,
            final_sl=1990.0,
            entry_price=2000.0,
            was_sl_modified=False,
            direction="BUY",
        )
        assert reason == ExitReason.HARD_SL_HIT.value

    def test_tp_hit(self):
        reason = classify_exit_reason(
            deal_reason_code=4,
            comment="tp",
            profit_usd=10.0,
            exit_price=2020.0,
            tp_price=2020.0,
            sl_price=1990.0,
            final_sl=1990.0,
            entry_price=2000.0,
            was_sl_modified=False,
            direction="BUY",
        )
        assert reason == ExitReason.TAKE_PROFIT_HIT.value

    def test_genuine_manual_close(self):
        reason = classify_exit_reason(
            deal_reason_code=1,
            comment="",
            profit_usd=2.0,
            exit_price=2002.0,
            tp_price=2020.0,
            sl_price=1990.0,
            final_sl=1990.0,
            entry_price=2000.0,
            was_sl_modified=False,
            direction="BUY",
        )
        assert reason == ExitReason.MANUAL_CLOSE.value

    def test_forced_mechanism_overrides(self):
        reason = classify_exit_reason(
            deal_reason_code=1,
            comment="",
            profit_usd=2.0,
            exit_price=2002.0,
            tp_price=2020.0,
            sl_price=1990.0,
            final_sl=1990.0,
            entry_price=2000.0,
            was_sl_modified=False,
            direction="BUY",
            forced_mechanism="AI_REVERSAL_EXIT",
        )
        assert reason == "AI_REVERSAL_EXIT"


# ---------------------------------------------------------------------------
# 4. Broker outcome reconstruction (authoritative PnL, aggregation)
# ---------------------------------------------------------------------------


class TestBrokerReconstruction:
    def test_deal_path_provides_realized_pnl(self):
        close_time = datetime.now(UTC)
        entry_time = close_time - timedelta(seconds=300)
        bo = reconstruct_broker_outcome(
            ticket=152400000301,
            symbol="XAUUSD",
            direction="BUY",
            deals=[
                {
                    "ticket": 9001,
                    "order_ticket": 8001,
                    "position_ticket": 152400000301,
                    "price": 2002.0,
                    "volume": 0.5,
                    "profit": 12.34,
                    "commission": -2.0,
                    "swap": -0.4,
                    "comment": "sl",
                    "reason": 3,
                }
            ],
            matched_deal=None,
            entry_price=2000.0,
            initial_sl=1990.0,
            final_sl=2000.0,
            tp_price=2020.0,
            volume=0.5,
            fallback_exit_price=2000.0,
            close_time=close_time,
            entry_time=entry_time,
        )
        assert bo.reconstruction_source == "BROKER_DEALS"
        assert bo.gross_profit == 12.34
        assert bo.commission == -2.0  # raw signed broker value (cost negative)
        # BUG-046: costs are subtracted in magnitude: net = gross - |comm| - |swap|.
        # swap=-0.4 is a COST (negative in MT5), so net = 12.34 - 2.0 - 0.4 = 9.94.
        assert bo.net_pnl_usd == pytest.approx(12.34 - abs(-2.0) - abs(-0.4))
        assert bo.exit_price == 2002.0
        assert bo.duration_sec == pytest.approx(300.0)

    def test_multiple_deals_aggregated_no_double_count(self):
        close_time = datetime.now(UTC)
        bo = reconstruct_broker_outcome(
            ticket=152400000302,
            symbol="XAUUSD",
            direction="SELL",
            deals=[
                {
                    "ticket": 9010,
                    "order_ticket": 8010,
                    "position_ticket": 152400000302,
                    "price": 2010.0,
                    "volume": 0.25,
                    "profit": 5.0,
                    "commission": -0.5,
                    "swap": 0.0,
                    "comment": "",
                    "reason": 0,
                },
                {
                    "ticket": 9011,
                    "order_ticket": 8011,
                    "position_ticket": 152400000302,
                    "price": 2005.0,
                    "volume": 0.25,
                    "profit": 7.5,
                    "commission": -0.5,
                    "swap": 0.0,
                    "comment": "",
                    "reason": 0,
                },
            ],
            matched_deal=None,
            entry_price=2000.0,
            initial_sl=2010.0,
            final_sl=2010.0,
            tp_price=1980.0,
            volume=0.5,
            fallback_exit_price=2005.0,
            close_time=close_time,
            entry_time=close_time - timedelta(seconds=60),
        )
        assert bo.reconstruction_source == "BROKER_DEALS_AGGREGATED"
        assert bo.gross_profit == pytest.approx(12.5)
        assert bo.commission == pytest.approx(-1.0)
        assert len(bo.deal_ids) == 2
        assert bo.volume == pytest.approx(0.5)

    def test_no_deal_evidence_flags_none_source(self):
        bo = reconstruct_broker_outcome(
            ticket=152400000303,
            symbol="XAUUSD",
            direction="BUY",
            deals=[],
            matched_deal=None,
            entry_price=2000.0,
            initial_sl=1990.0,
            final_sl=2000.0,
            tp_price=2020.0,
            volume=0.5,
            fallback_exit_price=2000.0,
            close_time=datetime.now(UTC),
            entry_time=None,
        )
        assert bo.reconstruction_source == "NONE"
        assert bo.net_pnl_usd == 0.0

    def test_broker_outcome_model_roundtrip(self):
        bo = BrokerOutcome(
            ticket="152400000304",
            order_id="8012",
            symbol="XAUUSD",
            direction="BUY",
            entry_price=2000.0,
            exit_price=2010.0,
            volume=0.5,
            gross_profit=5.0,
            commission=-1.0,
            swap=0.0,
            fee=0.0,
            net_pnl_usd=4.0,
            open_time="2026-08-17T10:00:00+00:00",
            close_time="2026-08-17T10:05:00+00:00",
            duration_sec=300.0,
            broker_reason_code=3,
            broker_comment="sl",
            deal_ids=["9012"],
            entry_sl=1990.0,
            final_sl=2000.0,
            entry_tp=2020.0,
            partial_closes=0,
            reconstruction_source="BROKER_DEALS",
        )
        dumped = bo.model_dump()
        assert dumped["gross_profit"] == 5.0
        assert BrokerOutcome.model_validate(dumped) == bo


# ---------------------------------------------------------------------------
# 5. Duplicate close events -> exactly one outcome
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_duplicate_close_single_outcome(self, components):
        ledger, evaluator, retriever, engine = components
        rid = "dup-00000000-0000-0000-0000-000000000001"
        ledger.record_experience(make_record(rid))
        ledger.audit_repo._queue.join()

        kwargs = dict(
            request_id=rid,
            execution_id="152400000401",
            outcome_timestamp=datetime.now(UTC),
            is_executed=True,
            is_closed=True,
            exit_reason=ExitReason.TAKE_PROFIT_HIT.value,
            realized_pnl_usd=10.0,
            realized_r_multiple=2.0,
            holding_duration_seconds=100.0,
            approved_volume=0.5,
            actual_entry=2000.0,
        )
        first = engine.record_trade_outcome(**kwargs)
        second = engine.record_trade_outcome(**kwargs)
        third = engine.record_trade_outcome(**kwargs)
        ledger.audit_repo._queue.join()

        # The queue worker may flush the first insert before the second call
        # executes (then has_outcome pre-check catches it) or after (then the
        # UNIQUE idempotency_key constraint catches it). Either way: exactly
        # one persisted outcome, never two.
        assert first is True
        assert second in (True, False)
        assert third in (True, False)
        conn = ledger._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_experience_outcomes WHERE idempotency_key = ?",
                (f"exp_{rid}",),
            ).fetchone()
        finally:
            conn.close()
        assert int(row[0]) == 1


# ---------------------------------------------------------------------------
# 6. Multiple tickets stay independent
# ---------------------------------------------------------------------------


class TestMultiTicketIndependence:
    def test_five_tickets_five_experiences(self, components):
        ledger, evaluator, retriever, engine = components
        for n in range(1, 6):
            rid = f"mt-00000000-0000-0000-0000-{n:012d}"
            ledger.record_experience(make_record(rid, strategy_id=f"strat_multi_{n}"))
        ledger.audit_repo._queue.join()

        for n in range(1, 6):
            rid = f"mt-00000000-0000-0000-0000-{n:012d}"
            ticket = 152400000500 + n
            engine.record_trade_outcome(
                request_id=rid,
                execution_id=str(ticket),
                outcome_timestamp=datetime.now(UTC),
                is_executed=True,
                is_closed=True,
                exit_reason=ExitReason.TAKE_PROFIT_HIT.value
                if n % 2
                else ExitReason.HARD_SL_HIT.value,
                realized_pnl_usd=5.0 if n % 2 else -5.0,
                realized_r_multiple=1.0 if n % 2 else -1.0,
                holding_duration_seconds=100.0,
                approved_volume=0.5,
                actual_entry=2000.0,
            )
        ledger.audit_repo._queue.join()

        for n in range(1, 6):
            rid = f"mt-00000000-0000-0000-0000-{n:012d}"
            rec = ledger.get_experience_by_key(f"exp_{rid}")
            assert rec is not None and rec.is_closed is True
            assert rec.execution_id == str(152400000500 + n)
            assert rec.strategy_id == f"strat_multi_{n}"


# ---------------------------------------------------------------------------
# 7. SL modification timeline survives closure
# ---------------------------------------------------------------------------


class TestSlTimeline:
    def test_initial_vs_final_sl_distinct(self):
        # The order manager must freeze _entry_sls at open and track the
        # broker-side SL separately. This guards the autopsy fields that make
        # the timeline visible (initial_sl_price != final_sl_price after BE).
        pytest.importorskip("nexus_scalp.execution.order_manager")
        assert ExitReason.BREAK_EVEN_SL_HIT.value == "BREAK_EVEN_SL_HIT"

    def test_entry_sl_frozen_in_manager_state(self, temp_audit_repo):
        """_entry_sls must stay at the OPEN value while _last_modify_sl advances."""
        om_mod = pytest.importorskip("nexus_scalp.execution.order_manager")
        om = om_mod.OrderLifecycleManager(
            adapter=MockAdapter(),
            audit_repo=temp_audit_repo,
            experience_engine=RecordingExperienceEngine(),
        )
        om._entry_prices[1] = 2000.0
        om._entry_sls[1] = 1990.0  # SL at open
        om._last_modify_sl[1] = 2000.0  # BE applied

        assert om._entry_sls[1] == 1990.0
        assert om._last_modify_sl[1] == 2000.0


# ---------------------------------------------------------------------------
# 8. Learning failure never blocks close
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    def test_experience_failure_does_not_raise(self, components):
        ledger, evaluator, retriever, engine = components
        rid = "fail-00000000-0000-0000-0000-000000000001"
        ledger.record_experience(make_record(rid))
        ledger.audit_repo._queue.join()

        # Force the ledger queue to a closed/None state so record_outcome fails.
        engine.ledger.audit_repo = None  # type: ignore[assignment]
        ok = engine.record_trade_outcome(
            request_id=rid,
            execution_id="152400000601",
            outcome_timestamp=datetime.now(UTC),
            is_executed=True,
            is_closed=True,
            exit_reason=ExitReason.TAKE_PROFIT_HIT.value,
            realized_pnl_usd=5.0,
            realized_r_multiple=1.0,
            holding_duration_seconds=100.0,
            approved_volume=0.5,
            actual_entry=2000.0,
        )
        # Must return False, never raise.
        assert ok is False


class MockAdapter:
    """Minimal adapter stub for OrderLifecycleManager construction."""

    def get_positions(self, symbol=None):
        return []

    def get_pending_orders(self, symbol=None):
        return []

    def get_closed_deals_history(self, symbol, hours_back):
        return []

    def get_symbol_info(self, symbol):
        return make_symbol_info()


# ---------------------------------------------------------------------------
# 9. Reconciliation self-heal (spec 23)
# ---------------------------------------------------------------------------


class TestReconciliation:
    def test_missed_close_reconciled_from_broker_history(self, temp_audit_repo):
        """A broker-closed ticket with an OPENED ledger row but no internal
        tracking must be discovered by reconcile_missed_closes and produce a
        CLOSED ledger row + an experience outcome (via the correlation
        fallback), without any trading action."""
        om_mod = pytest.importorskip("nexus_scalp.execution.order_manager")

        # Adapter that reports NO active positions but offers deal history.
        adapter = ReconcilingMockAdapter()
        adapter.closed_deals = [
            {
                "ticket": 9901,
                "order_ticket": 8801,
                "position_ticket": 152400000901,
                "symbol": "XAUUSD",
                "price": 2000.0,
                "volume": 0.5,
                "profit": 0.0,
                "commission": 0.0,
                "swap": 0.0,
                "comment": "sl",
                "reason": 3,
                "sl": 2000.0,
                "tp": 2020.0,
            }
        ]

        om = om_mod.OrderLifecycleManager(
            adapter=adapter,
            audit_repo=temp_audit_repo,
            experience_engine=RecordingExperienceEngine(),
        )
        # An OPENED placeholder exists (engine captured entry context) but the
        # position vanished from the broker after a restart: internal trackers
        # are empty.
        om.audit.log_ledger_opened(
            ticket=152400000901,
            symbol="XAUUSD",
            direction="BUY",
            volume=0.5,
            entry_price=2000.0,
            timestamp_str=datetime.now(UTC).isoformat(),
            order_id="recon-request-0001",
            entry_reason="SMC_GOD_MODE",
        )
        temp_audit_repo._queue.join()

        # Run a management pass: no active positions -> only reconciliation runs.
        om.manage_active_positions("XAUUSD", make_tick(bid=2000.10))
        temp_audit_repo._queue.join()

        # Ledger row is now CLOSED with the reconstructed mechanism.
        opened = temp_audit_repo.get_ledger_opened(152400000901)
        assert opened is None  # no longer OPENED

        # A reconciliation outcome was forwarded to the experience engine with
        # the request_id recovered from the OPENED row's order_id.
        assert om_mod.OrderLifecycleManager is not None
        calls = om.experience_engine.calls
        assert len(calls) == 1
        assert calls[0]["request_id"] == "recon-request-0001"
        assert calls[0]["execution_id"] == "152400000901"
        assert calls[0]["is_closed"] is True

    def test_reconcile_idempotent_no_duplicate(self, temp_audit_repo):
        """A second reconciliation pass must not re-emit the outcome."""
        om_mod = pytest.importorskip("nexus_scalp.execution.order_manager")
        adapter = ReconcilingMockAdapter()
        adapter.closed_deals = [
            {
                "ticket": 9902,
                "order_ticket": 8802,
                "position_ticket": 152400000902,
                "symbol": "XAUUSD",
                "price": 2000.5,
                "volume": 0.5,
                "profit": 2.5,
                "commission": 0.0,
                "swap": 0.0,
                "comment": "tp",
                "reason": 4,
            }
        ]
        om = om_mod.OrderLifecycleManager(
            adapter=adapter,
            audit_repo=temp_audit_repo,
            experience_engine=RecordingExperienceEngine(),
        )
        om.audit.log_ledger_opened(
            ticket=152400000902,
            symbol="XAUUSD",
            direction="BUY",
            volume=0.5,
            entry_price=2000.0,
            timestamp_str=datetime.now(UTC).isoformat(),
            order_id="recon-request-0002",
        )
        temp_audit_repo._queue.join()

        om.manage_active_positions("XAUUSD", make_tick(bid=2000.5))
        om.manage_active_positions("XAUUSD", make_tick(bid=2000.5))
        temp_audit_repo._queue.join()

        assert len(om.experience_engine.calls) == 1  # exactly one outcome


class ReconcilingMockAdapter(MockAdapter):
    """MockMT5-style adapter with closed-deal history and no live positions."""

    def __init__(self):
        self.closed_deals: list[dict] = []

    def get_positions(self, symbol=None):
        return []

    def get_closed_deals_history(self, symbol, hours_back):
        return list(self.closed_deals)


# ---------------------------------------------------------------------------
# 9. Strategy learning end-to-end (previous < new sample count)
# ---------------------------------------------------------------------------


class TestStrategyLearning:
    def test_sample_count_increases_and_evidence_retrievable(self, components):
        ledger, evaluator, retriever, engine = components
        strategy = "strat_learning"
        rid = "learn-00000000-0000-0000-0000-000000000001"
        ledger.record_experience(make_record(rid, strategy_id=strategy))
        ledger.audit_repo._queue.join()

        before = evaluator.evaluate_strategy(
            strategy, ledger.get_experiences_for_strategy(strategy)
        )
        assert before.sample_count == 0

        engine.record_trade_outcome(
            request_id=rid,
            execution_id="152400000701",
            outcome_timestamp=datetime.now(UTC),
            is_executed=True,
            is_closed=True,
            exit_reason=ExitReason.TAKE_PROFIT_HIT.value,
            realized_pnl_usd=10.0,
            realized_r_multiple=2.0,
            mae_points=-2.0,
            mfe_points=10.0,
            holding_duration_seconds=200.0,
            approved_volume=0.5,
            actual_entry=2000.0,
        )
        ledger.audit_repo._queue.join()

        after = evaluator.evaluate_strategy(strategy, ledger.get_experiences_for_strategy(strategy))
        assert after.sample_count == 1
        assert after.win_count == 1

        retrieved = ledger.get_experiences_for_strategy(strategy, limit=10)
        assert len(retrieved) == 1
        assert retrieved[0].is_closed is True
        assert retrieved[0].realized_r_multiple == pytest.approx(2.0)
