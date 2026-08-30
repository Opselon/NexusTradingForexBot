"""BUG-140 P0 regression suite: decision lifecycle, terminal pending-order
outcomes, lifecycle-aware dataset eligibility, context-contract regime split.

Covers the production changes committed as 7d2cf4a (canonical
DecisionLifecycle + idempotent terminal outcome writer), 7e94868
(OrderManager terminal pending-order emission) and 9331df7 (lifecycle-aware
dataset classification + P0-E eligibility census + P2 context-contract
regime/trend separation).

Invariants under test:
  1. Terminal non-trade outcomes carry R=0 / PnL=0 / is_executed=False /
     is_closed=True and an explicit lifecycle marker (never fabricated R).
  2. Terminal outcome persistence is idempotent and causality-checked.
  3. A terminal non-trade outcome never overwrites an authoritative trade.
  4. Dataset eligibility classifies terminal non-trades by exact lifecycle
     instead of a blanket MISSING_OUTCOME, and counts them as census
     evidence, not per-row recoverable findings.
  5. A discovery `regime` constraint matches the sample `regime` dimension,
     never `trend_state` (P2: CONTEXT_CONTRACT_EMPTY_POPULATION regression).
  6. OrderManager terminal paths (exposure block, bound pending cancel)
     actually write the terminal outcome through the production idempotency
     key exp_<request_id>.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.execution.terminal_outcome import (
    emit_terminal_pending_outcome,
    lifecycle_for_dispatch_failure,
)
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.lifecycle import (
    DEGRADED_TERMINAL_STATES,
    LIFECYCLE_PAYLOAD_KEY,
    NON_TRADE_TERMINAL_STATES,
    RECOVERY_SOURCE_BROKER_HISTORY,
    TERMINAL_NON_TRADE_EXIT_REASONS,
    TERMINAL_STATES,
    TRADE_TERMINAL_STATES,
    DecisionLifecycle,
    build_terminal_non_trade_outcome,
    lifecycle_from_outcome,
)
from nexus_scalp.experience.models import (
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
)
from nexus_scalp.research.context_contract import (
    extract_context_contract,
    filter_samples_by_contract,
)
from nexus_scalp.research.dataset import (
    REASON_CANCELED_UNFILLED,
    REASON_FILLED_OUTCOME_MISSING,
    REASON_MISSING_OUTCOME,
    REASON_NOT_DISPATCHED,
    REASON_REJECTED_UNFILLED,
    ResearchDatasetBuilder,
)

try:
    from tests.unit.task4_research_helpers import (
        make_outcome,
        make_record,
        seed_experiences,
    )
except ImportError:  # pragma: no cover - fallback for direct pytest cwd
    from task4_research_helpers import make_outcome, make_record, seed_experiences


# ---------------------------------------------------------------------------
# Fixtures + builders
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'bug140.db'}")
    yield r
    r.close()


@pytest.fixture
def ledger(repo):
    return ExperienceLedger(repo)


def make_tracked_record(
    request_id: str, ts: datetime | None = None, strategy_id: str = "strat_fam"
) -> ExperienceRecord:
    """Production-shaped decision row.

    The pre-trade gate keys experiences by ``exp_<request_id>``
    (ExperienceIntelligenceEngine.build_idempotency_key), so every decision
    written through this helper resolves the SAME key the OrderManager
    terminal bridge resolves at emit time.
    """
    return ExperienceRecord(
        experience_id=f"exp_row_{request_id}",
        request_id=request_id,
        idempotency_key=f"exp_{request_id}",
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=ts or datetime(2024, 1, 1, tzinfo=UTC),
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id=strategy_id,
            symbol="XAUUSD",
            session="LONDON",
            regime="TRENDING",
            volatility_regime="NORMAL",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id="scalp_v1", feature_dimension=50, values=[0.0] * 50
        ),
        action="BUY_MARKET",
        entry_reason="SMC",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
    )


def make_terminal_outcome(record: ExperienceRecord, state: DecisionLifecycle) -> ExperienceOutcome:
    """Terminal non-trade outcome keyed to an existing decision row."""
    return build_terminal_non_trade_outcome(
        idempotency_key=record.idempotency_key,
        state=state,
        detail="regression fixture",
    )


def merged(ledger: ExperienceLedger, key: str) -> ExperienceRecord | None:
    ledger.audit_repo._queue.join()
    return ledger.get_experience_by_key(key)


# ---------------------------------------------------------------------------
# 1. Lifecycle taxonomy
# ---------------------------------------------------------------------------


class TestLifecycleTaxonomy:
    def test_terminal_partitions_are_disjoint_and_complete(self):
        assert not (TRADE_TERMINAL_STATES & NON_TRADE_TERMINAL_STATES)
        assert not (TRADE_TERMINAL_STATES & DEGRADED_TERMINAL_STATES)
        assert not (NON_TRADE_TERMINAL_STATES & DEGRADED_TERMINAL_STATES)
        assert TERMINAL_STATES == (
            TRADE_TERMINAL_STATES | NON_TRADE_TERMINAL_STATES | DEGRADED_TERMINAL_STATES
        )
        # Every non-trade / degraded terminal state has an exit_reason marker.
        for state in NON_TRADE_TERMINAL_STATES | DEGRADED_TERMINAL_STATES:
            assert state in TERMINAL_NON_TRADE_EXIT_REASONS

    @pytest.mark.parametrize(
        "state",
        sorted(NON_TRADE_TERMINAL_STATES | DEGRADED_TERMINAL_STATES, key=lambda s: s.value),
    )
    def test_terminal_non_trade_outcome_fields(self, state: DecisionLifecycle):
        out = build_terminal_non_trade_outcome(idempotency_key="exp_k1", state=state, detail="d")
        assert out.idempotency_key == "exp_k1"
        assert out.is_executed is False
        assert out.is_closed is True
        # No fabricated economics: a non-trade carries zero R and zero PnL.
        assert out.realized_r_multiple == 0.0
        assert out.realized_pnl_usd == 0.0
        assert out.exit_reason == TERMINAL_NON_TRADE_EXIT_REASONS[state]
        assert out.decision_lifecycle == state.value
        assert out.lifecycle_detail == "d"

    def test_payload_marker_key_is_stable(self):
        assert LIFECYCLE_PAYLOAD_KEY == "decision_lifecycle"

    def test_build_rejects_trade_terminal_states(self):
        for state in TRADE_TERMINAL_STATES:
            with pytest.raises(ValueError):
                build_terminal_non_trade_outcome(idempotency_key="exp_k2", state=state)

    def test_recovery_source_marker_exists(self):
        assert RECOVERY_SOURCE_BROKER_HISTORY == "broker_history_recovery"


class TestLifecycleFromOutcome:
    def test_explicit_marker_wins(self):
        assert (
            lifecycle_from_outcome(
                is_executed=False,
                is_closed=True,
                exit_reason="TP",
                decision_lifecycle=DecisionLifecycle.CANCELED_UNFILLED.value,
            )
            is DecisionLifecycle.CANCELED_UNFILLED
        )

    def test_invalid_marker_falls_through_to_exit_reason(self):
        assert (
            lifecycle_from_outcome(
                is_executed=False,
                is_closed=True,
                exit_reason="EXPIRED_UNFILLED",
                decision_lifecycle="NOT_A_STATE",
            )
            is DecisionLifecycle.EXPIRED_UNFILLED
        )

    def test_exit_reason_marker_map(self):
        for state, reason in TERMINAL_NON_TRADE_EXIT_REASONS.items():
            assert (
                lifecycle_from_outcome(
                    is_executed=False,
                    is_closed=True,
                    exit_reason=reason,
                )
                is state
            )

    def test_executed_and_closed_is_filled_closed(self):
        assert (
            lifecycle_from_outcome(is_executed=True, is_closed=True, exit_reason="TP")
            is DecisionLifecycle.FILLED_CLOSED
        )

    def test_legacy_closed_without_execution_is_not_dispatched(self):
        assert (
            lifecycle_from_outcome(is_executed=False, is_closed=True, exit_reason="")
            is DecisionLifecycle.NOT_DISPATCHED
        )

    def test_open_decision_is_not_terminal(self):
        assert (
            lifecycle_from_outcome(is_executed=False, is_closed=False, exit_reason="")
            is DecisionLifecycle.DECISION_CREATED
        )


# ---------------------------------------------------------------------------
# 2. Terminal outcome persistence (ledger, real sqlite)
# ---------------------------------------------------------------------------


class TestTerminalOutcomeLedgerIdempotency:
    def test_roundtrip_and_idempotent_second_write(self, repo, ledger):
        rec = make_tracked_record("req_rt1")
        assert ledger.record_experience(rec) is True
        repo._queue.join()

        first = ledger.record_terminal_outcome(
            make_terminal_outcome(rec, DecisionLifecycle.CANCELED_UNFILLED)
        )
        assert first is True

        merged_rec = merged(ledger, rec.idempotency_key)
        assert merged_rec is not None
        assert merged_rec.is_executed is False
        assert merged_rec.is_closed is True
        assert merged_rec.exit_reason == "CANCELED_UNFILLED"

        # Restart replays / duplicate cancel callbacks must never duplicate.
        second = ledger.record_terminal_outcome(
            make_terminal_outcome(rec, DecisionLifecycle.CANCELED_UNFILLED)
        )
        assert second is False
        assert ledger.has_outcome(rec.idempotency_key) is True

    def test_terminal_never_overwrites_authoritative_trade(self, repo, ledger):
        rec = make_tracked_record("req_trade1")
        ledger.record_experience(rec)
        repo._queue.join()
        trade = make_outcome(rec, 1.5)  # authoritative broker reconstruction
        assert ledger.record_outcome(trade) is True
        repo._queue.join()

        refused = ledger.record_terminal_outcome(
            make_terminal_outcome(rec, DecisionLifecycle.NOT_DISPATCHED)
        )
        assert refused is False
        merged_rec = merged(ledger, rec.idempotency_key)
        assert merged_rec is not None
        assert merged_rec.is_executed is True
        assert merged_rec.realized_r_multiple == 1.5

    def test_causality_and_orphan_refusals(self, repo, ledger):
        rec = make_tracked_record("req_caus")
        ledger.record_experience(rec)
        repo._queue.join()

        early = make_terminal_outcome(rec, DecisionLifecycle.CANCELED_UNFILLED)
        early = early.model_copy(update={"outcome_timestamp": datetime(2023, 1, 1, tzinfo=UTC)})
        assert ledger.record_terminal_outcome(early) is False

        orphan = build_terminal_non_trade_outcome(
            idempotency_key="exp_no_such_decision",
            state=DecisionLifecycle.EXPIRED_UNFILLED,
        )
        assert ledger.record_terminal_outcome(orphan) is False


# ---------------------------------------------------------------------------
# 3. OrderManager emission bridge
# ---------------------------------------------------------------------------


class TestEmitTerminalPendingOutcome:
    def test_emit_writes_through_production_key(self, repo, ledger):
        rec = make_tracked_record("req_w1")
        ledger.record_experience(rec)
        repo._queue.join()
        engine = SimpleNamespace(ledger=ledger)

        assert (
            emit_terminal_pending_outcome(
                experience_engine=engine,
                request_id="req_w1",
                state=DecisionLifecycle.NOT_DISPATCHED,
                detail="gate blocked",
            )
            is True
        )
        row = merged(ledger, "exp_req_w1")
        assert row is not None
        assert row.exit_reason == "NOT_DISPATCHED"

        # Idempotent at the emission layer too.
        assert (
            emit_terminal_pending_outcome(
                experience_engine=engine,
                request_id="req_w1",
                state=DecisionLifecycle.NOT_DISPATCHED,
            )
            is False
        )

    def test_emit_refuses_trade_states_and_missing_inputs(self, repo, ledger):
        engine = SimpleNamespace(ledger=ledger)
        assert (
            emit_terminal_pending_outcome(
                experience_engine=engine,
                request_id="req_x",
                state=DecisionLifecycle.FILLED_CLOSED,
            )
            is False
        )
        assert (
            emit_terminal_pending_outcome(
                experience_engine=None, request_id="req_x", state=DecisionLifecycle.NOT_DISPATCHED
            )
            is False
        )
        assert (
            emit_terminal_pending_outcome(
                experience_engine=engine, request_id="", state=DecisionLifecycle.NOT_DISPATCHED
            )
            is False
        )

    def test_emit_failure_isolated(self, repo):
        class Boom:
            class ledger:  # noqa: N801 - namespace stub
                @staticmethod
                def record_terminal_outcome(outcome):
                    raise RuntimeError("db exploded")

        assert (
            emit_terminal_pending_outcome(
                experience_engine=Boom(),
                request_id="req_boom",
                state=DecisionLifecycle.EXPIRED_UNFILLED,
            )
            is False
        )

    def test_dispatch_failure_mapping(self):
        assert (
            lifecycle_for_dispatch_failure(dispatched=False, broker_rejected=True)
            is DecisionLifecycle.REJECTED_UNFILLED
        )
        assert (
            lifecycle_for_dispatch_failure(dispatched=True, broker_rejected=False)
            is DecisionLifecycle.EXECUTION_FAILED
        )
        assert (
            lifecycle_for_dispatch_failure(dispatched=False, broker_rejected=False)
            is DecisionLifecycle.NOT_DISPATCHED
        )


class TestOrderManagerTerminalWiring:
    def _manager(self, repo, ledger) -> OrderLifecycleManager:
        return OrderLifecycleManager(
            adapter=SimpleNamespace(
                execute_market_order=lambda **kw: 0,
                place_pending_order=lambda **kw: 0,
            ),
            audit_repo=repo,
            experience_engine=SimpleNamespace(ledger=ledger),
        )

    def _decision(self, request_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            action=ActionType.BUY_MARKET,
            symbol="XAUUSD",
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            request_id=request_id,
            confidence=0.7,
            execution_mode="STANDARD",
        )

    def test_exposure_block_emits_not_dispatched(self, repo, ledger):
        rec = make_tracked_record("req_blk")
        ledger.record_experience(rec)
        repo._queue.join()
        om = self._manager(repo, ledger)
        # Engine-wide exposure slot occupied -> dispatch must be refused.
        om._live_tickets_cache[111] = {"symbol": "XAUUSD", "type": "POSITION"}

        assert om.dispatch_order(self._decision("req_blk"), 0.1) is False
        row = merged(ledger, "exp_req_blk")
        assert row is not None
        assert row.exit_reason == REASON_NOT_DISPATCHED
        assert row.is_executed is False

    def test_bound_pending_cancel_emits_canceled_unfilled(self, repo, ledger):
        rec = make_tracked_record("req_pend")
        ledger.record_experience(rec)
        repo._queue.join()
        om = self._manager(repo, ledger)
        om._entry_order_ids[777] = "req_pend"

        assert (
            om._emit_terminal_for_pending(
                777, DecisionLifecycle.CANCELED_UNFILLED, "verified cancel"
            )
            is True
        )
        row = merged(ledger, "exp_req_pend")
        assert row is not None
        assert row.exit_reason == REASON_CANCELED_UNFILLED

        # Idempotent: a second sweep for the same ticket must not duplicate.
        assert (
            om._emit_terminal_for_pending(
                777, DecisionLifecycle.CANCELED_UNFILLED, "verified cancel"
            )
            is False
        )

    def test_unbound_ticket_emits_nothing(self, repo, ledger):
        om = self._manager(repo, ledger)
        assert om._emit_terminal_for_pending(999, DecisionLifecycle.CANCELED_UNFILLED) is False


# ---------------------------------------------------------------------------
# 4. Dataset lifecycle eligibility (P0-C / P0-E)
# ---------------------------------------------------------------------------


def seed_with_terminal(
    ledger: ExperienceLedger,
    repo,
    request_id: str,
    state: DecisionLifecycle | None,
    *,
    strategy_id: str = "strat_fam",
) -> None:
    rec = make_tracked_record(request_id, strategy_id=strategy_id)
    ledger.record_experience(rec)
    if state is not None:
        ledger.record_outcome(make_terminal_outcome(rec, state))
    repo._queue.join()


class TestDatasetLifecycleEligibility:
    def test_terminal_non_trade_is_ineligible_without_fabricated_r(self, repo, ledger):
        seed_with_terminal(ledger, repo, "c_140a", DecisionLifecycle.CANCELED_UNFILLED)
        rec = ledger.get_experience_by_key("exp_c_140a")
        assert rec is not None
        builder = ResearchDatasetBuilder(ledger)
        ok, reason, _detail = builder.evaluate_sample(rec)
        assert ok is False
        assert reason == REASON_CANCELED_UNFILLED
        # Zero stays zero: no fabricated economics entered the record.
        assert rec.realized_r_multiple == 0.0
        assert rec.realized_pnl_usd == 0.0

    @pytest.mark.parametrize(
        ("state", "expected_reason"),
        [
            (DecisionLifecycle.CANCELED_UNFILLED, REASON_CANCELED_UNFILLED),
            (DecisionLifecycle.EXPIRED_UNFILLED, "EXPIRED_UNFILLED"),
            (DecisionLifecycle.REJECTED_UNFILLED, REASON_REJECTED_UNFILLED),
            (DecisionLifecycle.REPLACED_UNFILLED, "REPLACED_UNFILLED"),
            (DecisionLifecycle.EXECUTION_FAILED, "EXECUTION_FAILED"),
            (DecisionLifecycle.NOT_DISPATCHED, REASON_NOT_DISPATCHED),
            (DecisionLifecycle.FILLED_OUTCOME_MISSING, REASON_FILLED_OUTCOME_MISSING),
        ],
    )
    def test_every_terminal_state_has_a_distinct_dataset_reason(
        self, repo, ledger, state, expected_reason
    ):
        seed_with_terminal(ledger, repo, f"c_{state.value.lower()}", state)
        rec = ledger.get_experience_by_key(f"exp_c_{state.value.lower()}")
        builder = ResearchDatasetBuilder(ledger)
        ok, reason, _detail = builder.evaluate_sample(rec)
        assert ok is False
        assert reason == expected_reason

    def test_filled_outcome_missing_is_recoverable_finding(self, repo, ledger):
        seed_with_terminal(ledger, repo, "c_fom", DecisionLifecycle.FILLED_OUTCOME_MISSING)
        builder = ResearchDatasetBuilder(ledger)
        report = builder.audit()
        assert report["total_records"] == 1
        assert report["eligible"] == 0
        assert report["rejection_reasons"].get(REASON_FILLED_OUTCOME_MISSING) == 1
        entry = report["rejections"][0]
        assert entry["recoverable"] is True

    def test_unknown_hang_stays_missing_outcome_recoverable(self, repo, ledger):
        seed_with_terminal(ledger, repo, "c_hang", None)
        builder = ResearchDatasetBuilder(ledger)
        report = builder.audit()
        assert report["rejection_reasons"].get(REASON_MISSING_OUTCOME) == 1
        assert report["rejections"][0]["recoverable"] is True

    def test_audit_counts_terminal_non_trades_quietly(self, repo, ledger):
        # 2 valid trades + 1 canceled + 1 expired + 1 unresolved hang.
        seed_experiences(ledger, repo, 2, prefix="ok140", r_values=[0.4, -0.2])
        seed_with_terminal(ledger, repo, "c_cx", DecisionLifecycle.CANCELED_UNFILLED)
        seed_with_terminal(ledger, repo, "c_ex", DecisionLifecycle.EXPIRED_UNFILLED)
        seed_with_terminal(ledger, repo, "c_hg", None)
        builder = ResearchDatasetBuilder(ledger)
        report = builder.audit()
        assert report["total_records"] == 5
        assert report["eligible"] == 2
        assert report["terminal_non_trades"] == 2
        # Only the genuinely unresolved record is a per-row recoverable finding.
        assert report["rejected"] == 1
        assert report["rejection_reasons"] == {REASON_MISSING_OUTCOME: 1}

    def test_build_census_travels_with_dataset(self, repo, ledger):
        seed_experiences(ledger, repo, 2, prefix="ok141", r_values=[0.4, -0.2])
        seed_with_terminal(ledger, repo, "c_cy", DecisionLifecycle.CANCELED_UNFILLED)
        seed_with_terminal(ledger, repo, "c_fy", DecisionLifecycle.FILLED_OUTCOME_MISSING)
        seed_with_terminal(ledger, repo, "c_hy", None)
        ds = ResearchDatasetBuilder(ledger).build()
        assert len(ds.samples) == 2  # only valid trades enter research
        census = ds.provenance_extra
        assert census["total_decisions"] == 5
        assert census["valid_research_samples"] == 2
        assert census["terminal_non_trades"] == 1
        assert census["filled_outcome_missing"] == 1
        assert census["unresolved_missing_outcome"] == 1
        rules = census["eligibility_rules"]
        assert rules["contract_version"] == "p0e-bug140-1"
        assert rules["EXECUTED_CLOSED"].startswith("research eligible")
        assert "forbidden" in rules["fabricated_r"]

    def test_duplicate_keys_collapse_in_dataset(self, repo, ledger):
        seed_experiences(ledger, repo, 2, prefix="dup140")
        # Re-record the same decisions under a second strategy listing path:
        # the builder must collapse by idempotency key, never double count.
        seed_experiences(ledger, repo, 2, prefix="dup140")
        report = ResearchDatasetBuilder(ledger).audit()
        assert report["total_records"] == 2


# ---------------------------------------------------------------------------
# 5. Context contract regime/trend separation (P2)
# ---------------------------------------------------------------------------


class TestContextContractRegimeSplit:
    def test_regime_token_lands_in_regime_states_not_trend(self):
        contract = extract_context_contract({"regime": {"require": "RANGING_MEAN_REVERSION"}}, None)
        assert contract["regime_states"] == ["RANGING_MEAN_REVERSION"]
        assert contract["trend_states"] == []

    def test_trend_token_stays_in_trend_states(self):
        contract = extract_context_contract({"regime": {"require": "BULLISH"}}, None)
        assert contract["trend_states"] == ["BULLISH"]
        assert contract["regime_states"] == []

    def test_discovery_regime_contract_matches_samples_by_regime(self, repo, ledger):
        # Discovery-family samples carry the FULL regime taxonomy on
        # sample.regime while sample.trend_state stays BULLISH/BEARISH/NEUTRAL.
        seed_experiences(ledger, repo, 2, prefix="reg_ok", r_values=[0.5, -0.3])
        for _rec in ledger.get_experiences_for_strategy("strat_fam", limit=10):
            pass  # default helper regime is RANGING_MEAN_REVERSION via make_record
        builder = ResearchDatasetBuilder(ledger)
        ds = builder.build()
        samples = ds.samples
        assert len(samples) == 2
        assert all(s.regime == "RANGING_MEAN_REVERSION" for s in samples)
        assert all(s.trend_state == "BULLISH" for s in samples)

        contract = extract_context_contract({"regime": {"require": "RANGING_MEAN_REVERSION"}}, None)
        matches, _diag = filter_samples_by_contract(samples, contract)
        assert len(matches) == 2  # pre-fix this was 0 (CONTEXT_CONTRACT_EMPTY_POPULATION)

        other = extract_context_contract({"regime": {"require": "TRENDING_MOMENTUM"}}, None)
        matches_other, _diag2 = filter_samples_by_contract(samples, other)
        assert len(matches_other) == 0  # the dimension actually filters

    def test_hypothesis_token_fill_respects_split(self):
        contract = extract_context_contract(
            None, {"market_condition": "LONDON | TRENDING | VOLATILITY_EXPANSION"}
        )
        assert contract["sessions"] == ["LONDON"]
        # TRENDING is a regime-family token: it must not leak into trend_states.
        assert "TRENDING" in contract["regime_states"]
        assert "TRENDING" not in contract["trend_states"]
        assert contract["volatility_regimes"] == ["EXPANSION"]
