"""BUG-261 regression: terminal-outcome clock-domain parity (G3/BUG-259/BUG-260 class).

``emit_terminal_pending_outcome`` stamped ``outcome_timestamp`` with the host
wall clock (``datetime.now(UTC)``). The ledger's causality guard
(``outcome_timestamp >= decision_timestamp``, where decision_timestamp is the
tick/broker-domain ``proposal.generated_at``) REFUSES a wall-clock stamp
whenever the host runs BEHIND the broker — hanging the decision as
MISSING_OUTCOME forever. The observed production skew direction was host-AHEAD
(``Age: -10781.6s`` in hold_duration.py), which passes causality but
future-dates the row; host-behind is the data-loss direction.

Contract under test:
  1. Tick-domain ``outcome_timestamp`` (decision.generated_at / tick.timestamp)
     passes the ledger causality guard verbatim (no clamp needed).
  2. A host-behind wall clock (outcome < decision) is REFUSED by the ledger
     (proves the hazard is real, not theoretical).
  3. The BUG-261 clamp fallback (outcome_timestamp=None): wall clock clamped
     UP to the decision timestamp via ledger lookup -> outcome RECORDED
     (would have been refused pre-fix).
  4. ``OrderIntentStore.resolve(resolved_at=...)`` honors an explicit
     tick-domain stamp; default remains wall clock (zero callers in prod).
  5. ``emit_terminal_for_pending(at=...)`` threads the tick timestamp into
     the emitted outcome (pending manage-loop path).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.broker_history import create_history_tables
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.lifecycle import DecisionLifecycle
from nexus_scalp.experience.models import (
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
)


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'bug261.db'}")
    conn = sqlite3.connect(r._db_path)
    create_history_tables(conn)
    conn.close()
    yield r
    r.close()


def _record(request_id: str, decision_ts: datetime) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_row_{request_id}",
        request_id=request_id,
        idempotency_key=f"exp_{request_id}",
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=decision_ts,
        strategy_id="strat_bug261",
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id="strat_bug261",
            symbol="XAUUSD",
            session="LONDON",
            regime="TRENDING_MOMENTUM",
            volatility_regime="NORMAL",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(values=[0.0] * 50),
        action="BUY_MARKET",
        entry_reason="SMC",
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        approved_volume=0.1,
    )


def _engine(repo) -> ExperienceIntelligenceEngine:
    ledger = ExperienceLedger(repo)
    return ExperienceIntelligenceEngine(ledger=ledger, evaluator=None, retriever=None, enabled=True)


class TestBug261TerminalOutcomeTickDomain:
    def test_tick_domain_stamp_passes_causality_verbatim(self, repo):
        """decision.generated_at (== tick.timestamp) as outcome_timestamp."""
        from nexus_scalp.execution.terminal_outcome import (
            emit_terminal_pending_outcome,
        )

        tick_ts = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
        engine = _engine(repo)
        engine.ledger.record_experience(_record("req_tick_a", tick_ts))
        ok = emit_terminal_pending_outcome(
            experience_engine=engine,
            request_id="req_tick_a",
            state=DecisionLifecycle.NOT_DISPATCHED,
            detail="dispatch gate rejection",
            outcome_timestamp=tick_ts,  # == decision.generated_at
        )
        assert ok is True
        repo.flush()
        merged = engine.ledger.get_experience_by_key("exp_req_tick_a")
        assert merged is not None
        assert merged.exit_reason == "NOT_DISPATCHED"

    def test_host_behind_wall_clock_is_refused_by_ledger(self, repo):
        """Proves the hazard: outcome < decision -> CAUSALITY_REJECTED."""
        from nexus_scalp.experience.models import ExperienceOutcome

        broker_ts = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
        ledger = ExperienceLedger(repo)
        ledger.record_experience(_record("req_skew_b", broker_ts))
        repo.flush()
        # Host 3h behind the broker (the data-loss skew direction).
        host_behind = broker_ts - timedelta(hours=3)
        ok = ledger.record_terminal_outcome(
            ExperienceOutcome(
                idempotency_key="exp_req_skew_b",
                execution_id="",
                outcome_timestamp=host_behind,
                is_executed=False,
                is_closed=True,
                exit_reason="NOT_DISPATCHED",
                realized_pnl_usd=0.0,
                realized_r_multiple=0.0,
            )
        )
        assert ok is False  # ledger refuses: causality guard holds

    def test_none_falls_back_to_ledger_clamp_not_refusal(self, repo):
        """outcome_timestamp=None -> clamp to decision ts -> RECORDED."""
        from nexus_scalp.execution.terminal_outcome import (
            emit_terminal_pending_outcome,
        )

        # Decision 3h in the wall-clock FUTURE: deterministically simulates
        # host-behind-broker skew (broker domain ahead of the host clock)
        # regardless of when the test runs. Pre-fix the raw wall-clock
        # fallback lands BEFORE the decision -> ledger CAUSALITY_REJECTED
        # -> RED (ok is False). Post-fix the clamp lifts the stamp to the
        # decision timestamp -> RECORDED -> GREEN.
        broker_ts = datetime.now(UTC) + timedelta(hours=3)
        engine = _engine(repo)
        engine.ledger.record_experience(_record("req_clamp_c", broker_ts))
        repo.flush()
        ok = emit_terminal_pending_outcome(
            experience_engine=engine,
            request_id="req_clamp_c",
            state=DecisionLifecycle.CANCELED_UNFILLED,
            detail="broker cancel confirmed (tick-less path)",
            outcome_timestamp=None,  # tick-less caller: clamp engages
        )
        assert ok is True
        repo.flush()
        merged = engine.ledger.get_experience_by_key("exp_req_clamp_c")
        assert merged is not None
        assert merged.exit_reason == "CANCELED_UNFILLED"
        assert merged.outcome_timestamp >= broker_ts

    def test_pending_emit_threads_tick_timestamp(self, repo):
        """emit_terminal_for_pending(at=tick_ts) lands on the outcome row."""
        from nexus_scalp.execution.lifecycle.pending_orders import (
            PendingOrderLifecycle,
        )

        tick_ts = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
        engine = _engine(repo)
        engine.ledger.record_experience(_record("req_pend_d", tick_ts))
        repo.flush()
        lifecycle = PendingOrderLifecycle(
            adapter=object(),
            tickets_view=lambda: {},
            refresh_cache=lambda *a, **k: None,
            experience_engine=engine,
        )
        lifecycle.set_entry_order_ids_probe(lambda ticket: "req_pend_d")
        ok = lifecycle.emit_terminal_for_pending(
            ticket=777001,
            state=DecisionLifecycle.CANCELED_UNFILLED,
            at=tick_ts,
        )
        assert ok is True
        repo.flush()
        merged = engine.ledger.get_experience_by_key("exp_req_pend_d")
        assert merged is not None
        assert merged.exit_reason == "CANCELED_UNFILLED"
        assert merged.outcome_timestamp == tick_ts


class TestBug261OrderIntentResolveAt:
    def test_resolve_honors_explicit_tick_stamp(self, tmp_path):
        from nexus_scalp.execution.order_write import (
            OrderIntent,
            OrderIntentStore,
            idempotency_fingerprint,
        )

        path = tmp_path / "intents.jsonl"
        store = OrderIntentStore(path)
        store.record(
            OrderIntent(
                intent_id="int-261",
                request_id="req-261",
                created_at_utc="2026-09-12T10:00:00+00:00",
                symbol="XAUUSD",
                side="BUY",
                order_kind="MARKET",
                requested_volume=0.1,
                price=2000.0,
                fingerprint=idempotency_fingerprint(
                    symbol="XAUUSD", order_type="BUY", volume=0.1, price=2000.0
                ),
            )
        )
        tick_ts = datetime(2026, 9, 12, 10, 0, 5, tzinfo=UTC)
        store.resolve("int-261", status="SUCCESS", ticket=123, resolved_at=tick_ts)
        store2 = OrderIntentStore(path)
        assert store2.load_pending() == {}
        # Resolved row carries the explicit tick-domain stamp.
        last: dict = {}
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    import json as _json

                    last = _json.loads(line)
        assert last["resolved_at_utc"] == tick_ts.isoformat()
        assert last["status"] == "SUCCESS"

    def test_resolve_default_remains_wall_clock(self, tmp_path):
        from nexus_scalp.execution.order_write import (
            OrderIntent,
            OrderIntentStore,
        )

        path = tmp_path / "intents2.jsonl"
        store = OrderIntentStore(path)
        store.record(
            OrderIntent(
                intent_id="int-261b",
                request_id="req-261b",
                created_at_utc="2026-09-12T10:00:00+00:00",
                symbol="XAUUSD",
                side="SELL",
                order_kind="MARKET",
                requested_volume=0.1,
                price=2000.0,
                fingerprint="fp",
            )
        )
        before = datetime.now(UTC)
        store.resolve("int-261b", status="RECONCILED_SUCCESS", ticket=456)
        after = datetime.now(UTC)
        last: dict = {}
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    import json as _json

                    last = _json.loads(line)
        stamped = datetime.fromisoformat(last["resolved_at_utc"])
        assert before <= stamped <= after
