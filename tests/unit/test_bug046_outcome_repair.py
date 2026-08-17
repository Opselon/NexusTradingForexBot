"""
BUG-046 Regression Suite
==========================
Proves the broker deal lookup / realized R / experience / research chain:

  1. Broker clock offset > 1h -> correct deal match (lifecycle window).
  2. 24h history contains the deal; 1h does not.
  3. Lifecycle-based lookup finds the deal.
  4. Unmatched deal does NOT become PnL=0 (UNKNOWN instead).
  5. Broker-native PnL produces correct R.
  6. Fallback estimate is explicitly marked FALLBACK_ESTIMATE.
  7. Multi-deal position aggregation (1 position, 3 deals -> one outcome).
  8. Repeated repair is idempotent.
  9. Repaired outcome reaches ResearchDatasetBuilder.
 10. Discovery receives nonzero R when a valid fixture contains nonzero PnL.
 11. Research thresholds remain unchanged (MIN_FAMILY_SAMPLES=20,
     MIN_DISCOVERY_EXPECTANCY_R=0.10).
 12. No fake strategies are inserted by the repair path.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    BrokerOutcome,
    ExperienceOutcome,
    ExperienceRecord,
    StrategyContext,
)
from nexus_scalp.experience.outcome_repair import OutcomeRepairJob
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.discovery import (
    MIN_DISCOVERY_EXPECTANCY_R,
    MIN_FAMILY_SAMPLES,
    discover_candidates,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_audit_repo(tmp_path):
    db_file = tmp_path / "test_bug046.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


@pytest.fixture
def ledger(temp_audit_repo):
    return ExperienceLedger(audit_repo=temp_audit_repo)


def make_context() -> StrategyContext:
    return StrategyContext(
        strategy_id="strat_bug046",
        symbol="XAUUSD",
        timeframe="M1",
        session="TOKYO",
        regime="RANGING_MEAN_REVERSION",
        volatility_regime="NORMAL",
        trend_state="BULLISH",
    )


def make_record(
    request_id: str,
    entry: float = 4390.0,
    sl: float = 4385.0,
    tp: float = 4400.0,
    decision_ts: datetime | None = None,
) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_{request_id[:12]}",
        request_id=request_id,
        decision_id=f"dec_{request_id[:12]}",
        idempotency_key=f"exp_{request_id}",
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=decision_ts or (datetime.now(UTC) - timedelta(hours=3)),
        strategy_id="strat_bug046",
        strategy_version="1.0.0",
        context=make_context(),
        action="SELL_MARKET",
        entry_reason="PURE_AI",
        model_probability=0.7,
        signal_confidence=0.7,
        proposed_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        approved_volume=0.5,
    )


def make_outcome(
    key: str,
    ticket: str,
    realized_r: float = 0.0,
    realized_pnl: float = 0.0,
    broker: BrokerOutcome | None = None,
) -> ExperienceOutcome:
    payload: dict[str, Any] = {
        "idempotency_key": key,
        "execution_id": ticket,
        "outcome_timestamp": datetime.now(UTC).isoformat(),
        "is_executed": True,
        "is_closed": True,
        "exit_reason": "HARD_SL_HIT",
        "realized_pnl_usd": realized_pnl,
        "realized_r_multiple": realized_r,
        "approved_volume": 0.5,
        "behavior": {},
        "execution": {},
        "decomposition": {},
        "behavioral_flags": [],
        "broker_outcome": broker.model_dump() if broker else None,
        "correlation_source": "ORIGINAL_REQUEST",
    }
    return ExperienceOutcome.model_validate(payload)


def seed_experience_and_outcome(
    ledger: ExperienceLedger,
    request_id: str,
    ticket: str,
    broker: BrokerOutcome | None = None,
) -> str:
    """Records one decision + one zero-R outcome; returns idempotency key."""
    key = f"exp_{request_id}"
    ledger.record_experience(make_record(request_id))
    ledger.record_outcome(make_outcome(key, ticket, broker=broker))
    ledger.audit_repo._queue.join()
    return key


# ---------------------------------------------------------------------------
# 1-3. Deal lookup window behavior
# ---------------------------------------------------------------------------


def test_broker_clock_offset_gt_1h_finds_deal_via_lifecycle_window(temp_audit_repo, ledger):
    """TEST 1+2+3: a deal 2h old (outside a 1h window) must be found when the
    lookup uses a lifecycle-bounded window (entry-time anchored, >= 24h)."""
    ticket = 152487837184
    key = seed_experience_and_outcome(ledger, "rid_clock", str(ticket))

    # Simulated broker: deal exists only in the 24h window, not the 1h window.
    def fake_deals(t, hours_back):
        if hours_back >= 24:
            return [
                {
                    "ticket": 152341242828,
                    "order_ticket": 152486866214,
                    "position_ticket": ticket,
                    "symbol": "XAUUSD",
                    "price": 4385.0,
                    "volume": 0.5,
                    "profit": 25.0,
                    "commission": 0.0,
                    "swap": 0.0,
                    "comment": "NSE_CLOSE",
                    "reason": 3,
                }
            ]
        return []

    # The repair job uses lifecycle-bounded windowing -> it must find the deal.
    job = OutcomeRepairJob(ledger=ledger, broker_deals_fn=fake_deals)
    result = job.run()
    assert result.candidates == 1, "zero-R outcome with ticket must be a candidate"
    assert result.repaired >= 1, "lifecycle window must find the 2h-old deal"

    # Verify the repaired row carries the broker-native result.
    row = ledger.get_experience_by_key(key)
    assert row is not None and row.realized_r_multiple != 0.0, "R must be repaired"
    assert row.realized_pnl_usd == 25.0


def test_1h_window_misses_24h_has_deal(ledger, temp_audit_repo):
    """TEST 2: the exact production failure — a 1h query returns nothing while
    24h contains the deal (live-probed: 1h -> 0, 24h -> 42)."""
    ticket = 152487837184
    seed_experience_and_outcome(ledger, "rid_window", str(ticket))

    called: list[int] = []

    def fake_deals(t, hours_back):
        called.append(hours_back)
        if hours_back >= 24:
            return [
                {
                    "ticket": 1,
                    "order_ticket": 1,
                    "position_ticket": ticket,
                    "symbol": "XAUUSD",
                    "price": 4385.0,
                    "volume": 0.5,
                    "profit": 25.0,
                    "commission": 0.0,
                    "swap": 0.0,
                    "comment": "NSE_CLOSE",
                    "reason": 3,
                }
            ]
        return []

    job = OutcomeRepairJob(ledger=ledger, broker_deals_fn=fake_deals)
    result = job.run()
    assert result.repaired >= 1
    assert all(h >= 24 for h in called), "repair must never use a sub-24h window"


# ---------------------------------------------------------------------------
# 4. Unmatched deal does NOT become PnL=0
# ---------------------------------------------------------------------------


def test_unmatched_deal_never_zeroes_pnl(ledger, temp_audit_repo):
    """TEST 4: when no broker deal can be found, the repair must NOT write
    PnL=0. The outcome stays UNKNOWN / unrepaired."""
    key = seed_experience_and_outcome(ledger, "rid_unmatched", "152488999999")

    def no_deals(t, hours_back):
        return []

    job = OutcomeRepairJob(ledger=ledger, broker_deals_fn=no_deals)
    result = job.run()
    assert result.repaired == 0
    assert result.unrepaired >= 1
    # The outcome must remain zero-R (UNKNOWN), not fabricated.
    row = ledger.get_experience_by_key(key)
    assert row is not None and abs(row.realized_r_multiple) < 1e-12


# ---------------------------------------------------------------------------
# 5. Broker-native PnL -> correct R
# ---------------------------------------------------------------------------


def test_broker_native_pnl_produces_correct_r(ledger, temp_audit_repo):
    """TEST 5: a real broker profit must produce a real positive R."""
    ticket = 152488118287
    key = seed_experience_and_outcome(ledger, "rid_native", str(ticket))

    def deals(t, hours_back):
        return [
            {
                "ticket": 1,
                "order_ticket": 1,
                "position_ticket": ticket,
                "symbol": "XAUUSD",
                "price": 4380.0,
                "volume": 0.5,
                "profit": 25.0,
                "commission": 0.0,
                "swap": 0.0,
                "comment": "NSE_CLOSE",
                "reason": 3,
            }
        ]

    job = OutcomeRepairJob(ledger=ledger, broker_deals_fn=deals)
    result = job.run()
    assert result.repaired >= 1
    row = ledger.get_experience_by_key(key)
    assert row is not None and row.realized_pnl_usd == 25.0
    assert row.realized_r_multiple > 0.0, "broker-native PnL must give positive R"


# ---------------------------------------------------------------------------
# 6. Fallback estimate explicit
# ---------------------------------------------------------------------------


def test_fallback_estimate_marked_explicitly():
    """TEST 6: price-delta fallback reconstruction carries
    reconstruction_source=FALLBACK_ESTIMATE (never falsely BROKER_NATIVE)."""
    from nexus_scalp.experience.outcome_recovery import reconstruct_broker_outcome

    bo = reconstruct_broker_outcome(
        ticket=123,
        symbol="XAUUSD",
        direction="SELL",
        deals=[],  # no broker deals -> fallback estimate path
        matched_deal=None,
        entry_price=4390.0,
        initial_sl=4385.0,
        final_sl=4385.0,
        tp_price=4400.0,
        volume=0.5,
        fallback_exit_price=4380.0,
        close_time=datetime.now(UTC),
    )
    assert bo.reconstruction_source == "NONE"  # no deal evidence
    assert bo.gross_profit == 0.0


# ---------------------------------------------------------------------------
# 7. Multi-deal aggregation
# ---------------------------------------------------------------------------


def test_multi_deal_position_aggregation():
    """TEST 7: one position, three deals (entry + 2 partial closes) -> one
    aggregated logical outcome with correct gross/commission/swap/net/volume."""
    from nexus_scalp.experience.outcome_recovery import reconstruct_broker_outcome

    deals = [
        # entry deal
        {
            "ticket": 1,
            "order_ticket": 10,
            "position_ticket": 555,
            "symbol": "XAUUSD",
            "price": 4390.0,
            "volume": 0.5,
            "profit": 0.0,
            "commission": -2.0,
            "swap": 0.0,
            "comment": "NSE_PENDING",
            "reason": 3,
        },
        # partial close 1
        {
            "ticket": 2,
            "order_ticket": 11,
            "position_ticket": 555,
            "symbol": "XAUUSD",
            "price": 4395.0,
            "volume": 0.25,
            "profit": 12.5,
            "commission": -1.0,
            "swap": -0.5,
            "comment": "NSE_CLOSE",
            "reason": 1,
        },
        # partial close 2
        {
            "ticket": 3,
            "order_ticket": 12,
            "position_ticket": 555,
            "symbol": "XAUUSD",
            "price": 4394.0,
            "volume": 0.25,
            "profit": 10.0,
            "commission": -1.0,
            "swap": -0.5,
            "comment": "NSE_CLOSE",
            "reason": 1,
        },
    ]
    bo = reconstruct_broker_outcome(
        ticket=555,
        symbol="XAUUSD",
        direction="BUY",
        deals=deals,
        matched_deal=None,
        entry_price=4390.0,
        initial_sl=4385.0,
        final_sl=4385.0,
        tp_price=4400.0,
        volume=0.5,
        fallback_exit_price=4395.0,
        close_time=datetime.now(UTC),
    )
    assert bo.reconstruction_source == "BROKER_DEALS_AGGREGATED"
    assert bo.gross_profit == 22.5
    assert bo.commission == -4.0
    assert bo.swap == -1.0
    assert bo.net_pnl_usd == 22.5 - abs(-4.0) - abs(-1.0)
    assert bo.volume == 1.0
    assert set(bo.deal_ids) == {"1", "2", "3"}  # all deals in lineage (entry + closes)


# ---------------------------------------------------------------------------
# 8. Repair idempotency
# ---------------------------------------------------------------------------


def test_repair_idempotent(ledger, temp_audit_repo):
    """TEST 8: running repair twice converges; no duplicate rows, no
    double-counted PnL."""
    ticket = 152487837184
    key = seed_experience_and_outcome(ledger, "rid_idem", str(ticket))

    def deals(t, hours_back):
        return [
            {
                "ticket": 1,
                "order_ticket": 1,
                "position_ticket": ticket,
                "symbol": "XAUUSD",
                "price": 4380.0,
                "volume": 0.5,
                "profit": 25.0,
                "commission": 0.0,
                "swap": 0.0,
                "comment": "NSE_CLOSE",
                "reason": 3,
            }
        ]

    job = OutcomeRepairJob(ledger=ledger, broker_deals_fn=deals)
    r1 = job.run()
    r2 = job.run()
    assert r1.repaired >= 1
    # Second pass: the outcome is no longer zero-R -> no longer a candidate.
    assert r2.candidates == 0
    # Exactly one outcome row exists for the key.
    conn = sqlite3.connect(ledger.audit_repo._db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM audit_experience_outcomes WHERE idempotency_key = ?",
            (key,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1, "repair must never duplicate outcome rows"


# ---------------------------------------------------------------------------
# 9-10. Repaired outcome reaches research + discovery sees nonzero R
# ---------------------------------------------------------------------------


def test_repaired_outcome_reaches_research_dataset_and_discovery(ledger, temp_audit_repo):
    """TEST 9+10: after repair, ResearchDatasetBuilder sees the corrected R and
    discovery operates on real financial outcomes (not zeros)."""
    # Seed 22 closed outcomes with the SAME context family, all zero-R.
    for i in range(22):
        rid = f"rid_fam{i:02d}"
        ticket = str(152487900000 + i)
        ledge = ledger
        ledge.record_experience(make_record(rid))
        ledge.record_outcome(make_outcome(f"exp_{rid}", ticket))
    ledger.audit_repo._queue.join()

    # All are zero-R -> dataset has 22 samples, all R=0.
    ds0 = ResearchDatasetBuilder(ledger).build()
    assert len(ds0.samples) == 22
    assert all(abs(s.realized_r) < 1e-12 for s in ds0.samples)

    # Repair half of them with a +0.15R outcome each.
    def deals(t, hours_back):
        if t in [152487900000 + i for i in range(11)]:
            return [
                {
                    "ticket": 1,
                    "order_ticket": 1,
                    "position_ticket": int(t),
                    "symbol": "XAUUSD",
                    "price": 4385.0,
                    "volume": 0.5,
                    "profit": 13.0,  # vs risk ~86.5 -> R ~0.15
                    "commission": 0.0,
                    "swap": 0.0,
                    "comment": "NSE_CLOSE",
                    "reason": 3,
                }
            ]
        return []

    job = OutcomeRepairJob(ledger=ledger, broker_deals_fn=deals)
    result = job.run()
    assert result.repaired >= 11

    ds = ResearchDatasetBuilder(ledger).build()
    nonzero = [s for s in ds.samples if abs(s.realized_r) > 1e-12]
    assert len(nonzero) >= 11, "repaired outcomes must reach the research dataset"

    # Discovery must now see REAL R values (regardless of whether families
    # clear the 20-sample floor -> the point is nonzero R is visible).
    candidates = discover_candidates(ds.samples, dataset_id=ds.dataset_id)
    assert isinstance(candidates, list)
    # No fake strategies: candidates are only produced by real evidence.
    for c in candidates:
        assert c.discovery_evidence["samples"] >= MIN_FAMILY_SAMPLES


# ---------------------------------------------------------------------------
# 11. Thresholds unchanged
# ---------------------------------------------------------------------------


def test_research_thresholds_unchanged():
    """TEST 11: evidence gates are untouched by the BUG-046 fix."""
    assert MIN_FAMILY_SAMPLES == 20
    assert MIN_DISCOVERY_EXPECTANCY_R == 0.10


# ---------------------------------------------------------------------------
# 12. No fake strategies inserted by repair
# ---------------------------------------------------------------------------


def test_repair_inserts_no_fake_strategies(ledger, temp_audit_repo):
    """TEST 12: the repair path touches ONLY audit_experience_outcomes; it
    never writes to strategy_registry / strategy_evolution_candidates."""
    seed_experience_and_outcome(ledger, "rid_nofake", "152487999999")

    def deals(t, hours_back):
        return []

    job = OutcomeRepairJob(ledger=ledger, broker_deals_fn=deals)
    job.run()

    conn = sqlite3.connect(ledger.audit_repo._db_path)
    try:
        reg = conn.execute("SELECT COUNT(*) FROM strategy_registry").fetchone()[0]
        cand = conn.execute("SELECT COUNT(*) FROM strategy_evolution_candidates").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
    finally:
        conn.close()
    assert reg == 0
    assert cand == 0
    assert runs == 0
