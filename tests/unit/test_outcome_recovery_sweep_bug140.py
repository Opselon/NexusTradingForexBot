"""BUG-140 Phase 2 regression suite: historical missing-outcome recovery sweep.

Covers :class:`HistoricalOutcomeRecoverySweep` in
``src/nexus_scalp/experience/outcome_recovery_sweep.py`` and its integration
with :class:`ResearchDatasetBuilder`.

Invariants under test:
  1. FILLED-and-closed decision: full reconstruction from broker deal rows
     (net PnL, realized R, duration, exit reason, slip). R/PnL come ONLY
     from broker truth; never fabricated or zero-substituted.
  2. Provenance is stamped explicitly: correlation_detail carries the
     RECOVERY_SOURCE_BROKER_HISTORY marker; broker_outcome.reconstruction_source
     is BROKER_DEALS or BROKER_DEALS_AGGREGATED.
  3. CANCELED broker orders with no fills recover as CANCELED_UNFILLED (no
     fake trade row; R=0/PnL=0/is_executed=False).
  4. EXPIRED / REJECTED broker orders recover to their exact terminal states.
  5. FILLED order with no close deals is SKIPPED (open position / incomplete
     history), never terminated or zero-substituted.
  6. Causality refusal: deals whose close time precedes the decision are
     rejected, never clamped.
  7. Decisions with no dispatch evidence are SKIPPED (not guessed).
  8. Idempotent: running the sweep twice yields zero new writes; no row or
     PnL is ever duplicated.
  9. Dataset census integration: ds.provenance_extra["recovered_outcomes"]
     counts real sweep provenance markers, and recovered trades enter
     valid_research_samples with valid R.
  10. Dry-run mode classifies without writing to the ledger.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.broker_history import create_history_tables
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.lifecycle import (
    RECOVERY_SOURCE_BROKER_HISTORY,
    DecisionLifecycle,
)
from nexus_scalp.experience.models import (
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
)
from nexus_scalp.experience.outcome_recovery_sweep import (
    HistoricalOutcomeRecoverySweep,
    _broker_epoch_to_utc,
)
from nexus_scalp.research.dataset import ResearchDatasetBuilder

# ---------------------------------------------------------------------------
# Fixtures + seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'sweep.db'}")
    # Ensure broker-history tables exist in the test DB.
    conn = sqlite3.connect(r._db_path)
    create_history_tables(conn)
    conn.close()
    yield r
    r.close()


@pytest.fixture
def ledger(repo):
    return ExperienceLedger(repo)


def seed_decision(
    ledger: ExperienceLedger,
    request_id: str,
    ts: datetime,
    entry: float = 2000.0,
    sl: float = 1990.0,
    tp: float = 2020.0,
    action: str = "BUY_MARKET",
) -> ExperienceRecord:
    rec = ExperienceRecord(
        experience_id=f"exp_{request_id}",
        request_id=request_id,
        idempotency_key=f"exp_{request_id}",
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=ts,
        strategy_id="strat_fam",
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id="strat_fam",
            symbol="XAUUSD",
            session="LONDON",
            regime="TRENDING",
            volatility_regime="NORMAL",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id="scalp_v1", feature_dimension=50, values=[0.0] * 50
        ),
        action=action,
        entry_reason="SMC",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
    )
    ledger.record_experience(rec)
    ledger.audit_repo._queue.join()
    return rec


def seed_dispatch(
    repo: AuditRepository,
    request_id: str,
    ticket: int,
    ts: datetime,
    symbol: str = "XAUUSD",
    price: float = 2000.0,
) -> None:
    conn = sqlite3.connect(repo._db_path)
    conn.execute(
        """INSERT INTO audit_orders
           (ticket, order_id, symbol, action, price, stop_loss, take_profit,
            volume, reason, latency, execution_mode, execution_id, timestamp)
           VALUES (?, ?, ?, 'BUY', ?, 1990.0, 2020.0, 0.1, 'dispatch', 0.01,
                   'STANDARD', 'EXEC-1', ?)""",
        (ticket, request_id, symbol, price, ts.isoformat()),
    )
    conn.commit()
    conn.close()


def seed_broker_order(
    repo: AuditRepository,
    ticket: int,
    state: int,
    position_id: int = 0,
    time_setup: int = 1784141119,
    price: float = 2000.0,
) -> None:
    conn = sqlite3.connect(repo._db_path)
    conn.execute(
        """INSERT INTO audit_broker_orders
           (ticket, position_id, symbol, type, magic, state, volume_initial,
            volume_current, price_open, time_setup, time_done)
           VALUES (?, ?, 'XAUUSD', 0, 100, ?, 0.1, 0.1, ?, ?, ?)""",
        (ticket, position_id, state, price, time_setup, time_setup + 300),
    )
    conn.commit()
    conn.close()


def get_outcome_payload(repo: AuditRepository, idempotency_key: str) -> dict:
    conn = sqlite3.connect(repo._db_path)
    row = conn.execute(
        "SELECT payload FROM audit_experience_outcomes WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    conn.close()
    return json.loads(row[0]) if row and row[0] else {}


def seed_deal(
    repo: AuditRepository,
    deal_ticket: int,
    order_ticket: int,
    position_id: int,
    entry: int,
    volume: float,
    price: float,
    profit: float,
    epoch_sec: int,
    reason: int = 5,  # TP (MT5 DEAL_REASON_TP = 5)
    commission: float = 0.0,
    swap: float = 0.0,
) -> None:
    conn = sqlite3.connect(repo._db_path)
    conn.execute(
        """INSERT INTO audit_broker_deals
           (ticket, "order", position_id, symbol, type, entry, magic, time,
            reason, volume, price, profit, commission, swap, net_result)
           VALUES (?, ?, ?, 'XAUUSD', 0, ?, 100, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            deal_ticket,
            order_ticket,
            position_id,
            entry,
            epoch_sec,
            reason,
            volume,
            price,
            profit,
            commission,
            swap,
            profit + commission + swap,
        ),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHistoricalOutcomeRecoverySweep:
    def test_broker_epoch_helper(self):
        # GMT+3 server time: 1784141119 -> UTC is 3 hours earlier
        dt = _broker_epoch_to_utc(1784141119)
        assert dt is not None
        assert dt.tzinfo == UTC
        assert _broker_epoch_to_utc(0) is None

    def test_filled_and_closed_trade_full_recovery(self, repo, ledger):
        """Invariant 1 & 2: reconstructed trade outcome from real deal evidence."""
        dec_ts = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        rec = seed_decision(ledger, "req_fill_1", dec_ts, entry=2000.0, sl=1990.0, tp=2020.0)
        seed_dispatch(repo, "req_fill_1", ticket=1001, ts=dec_ts)
        # Server epoch for GMT+3 (decision_ts + 3h = 13:00 server)
        server_epoch = int(dec_ts.timestamp()) + 180 * 60
        seed_broker_order(repo, ticket=1001, state=4, position_id=5001, time_setup=server_epoch)
        # Fill deal (entry=0) at +1min, Close deal (entry=1, profit=100.0) at +5min
        seed_deal(
            repo,
            9001,
            1001,
            5001,
            entry=0,
            volume=0.1,
            price=2000.0,
            profit=0.0,
            epoch_sec=server_epoch + 60,
        )
        seed_deal(
            repo,
            9002,
            1001,
            5001,
            entry=1,
            volume=0.1,
            price=2010.0,
            profit=100.0,
            epoch_sec=server_epoch + 300,
            commission=-1.0,
        )

        sweep = HistoricalOutcomeRecoverySweep(ledger=ledger)
        res = sweep.run()

        assert res.scanned == 1
        assert res.recovered == 1
        assert res.filled_recovered == 1
        assert res.canceled_recovered == 0

        # Verify outcome in ledger
        merged_rec = ledger.get_experience_by_key(rec.idempotency_key)
        assert merged_rec is not None
        assert merged_rec.is_executed is True
        assert merged_rec.is_closed is True
        # net = 100 - 1.0 (commission on the close deal); risk = 10 * 0.1 * 100
        # = 100 USD; R = 99.0/100 = 0.99.
        assert merged_rec.realized_pnl_usd == pytest.approx(99.0, rel=1e-6)
        assert merged_rec.realized_r_multiple == pytest.approx(0.99, rel=1e-3)
        assert merged_rec.exit_reason in ("TAKE_PROFIT_HIT", "BROKER_CLOSE", "TP", "SYSTEM_CLOSE")

        # Provenance stamped
        payload = get_outcome_payload(repo, rec.idempotency_key)
        assert payload
        assert RECOVERY_SOURCE_BROKER_HISTORY in payload.get("correlation_detail", "")
        bo = payload.get("broker_outcome") or {}
        assert bo.get("reconstruction_source") in (
            "BROKER_DEALS",
            "BROKER_DEALS_AGGREGATED",
        )

    def test_deals_order_fallback_join(self, repo, ledger):
        """Production pattern: broker order has position_id=0; deal knows position."""
        dec_ts = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        rec = seed_decision(ledger, "req_fallback", dec_ts)
        seed_dispatch(repo, "req_fallback", ticket=1002, ts=dec_ts)
        server_epoch = int(dec_ts.timestamp()) + 180 * 60
        # position_id=0 on broker order (legacy sync state)
        seed_broker_order(repo, ticket=1002, state=4, position_id=0, time_setup=server_epoch)
        # deals carry position_id=7001 and order=1002
        seed_deal(
            repo,
            9011,
            1002,
            7001,
            entry=0,
            volume=0.1,
            price=2000.0,
            profit=0.0,
            epoch_sec=server_epoch + 60,
        )
        seed_deal(
            repo,
            9012,
            1002,
            7001,
            entry=1,
            volume=0.1,
            price=1995.0,
            profit=-50.0,
            epoch_sec=server_epoch + 120,
        )

        sweep = HistoricalOutcomeRecoverySweep(ledger=ledger)
        res = sweep.run()
        assert res.filled_recovered == 1
        merged_rec = ledger.get_experience_by_key(rec.idempotency_key)
        assert merged_rec is not None
        assert merged_rec.realized_pnl_usd < 0.0

    def test_close_deal_different_order_ticket_recovered(self, repo, ledger):
        """QA forensic (BUG-140 Phase 2): broker order position_id=0 AND the close
        deal carries a DIFFERENT "order" ticket than the entry deal.

        Before the fix the ticket-based fallback matched only the entry deal, so
        ``closes`` stayed empty and the sweep reported SKIP_NO_CLOSE_DEALS for a
        genuinely filled-and-closed trade (production: filled_recovered=0,
        skipped_no_close_deals=7). After the fix the entry deal's position_id
        drives a re-query that surfaces the close deal and full reconstruction.
        """
        dec_ts = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        rec = seed_decision(ledger, "req_split_order", dec_ts, entry=2000.0, sl=1990.0, tp=2020.0)
        seed_dispatch(repo, "req_split_order", ticket=1010, ts=dec_ts)
        server_epoch = int(dec_ts.timestamp()) + 180 * 60
        # Legacy sync: broker order row has position_id=0.
        seed_broker_order(repo, ticket=1010, state=4, position_id=0, time_setup=server_epoch)
        # Entry deal: order=1010 (the dispatched ticket), position=7101, entry commission=-0.5.
        seed_deal(
            repo,
            9101,
            1010,
            7101,
            entry=0,
            volume=0.1,
            price=2000.0,
            profit=0.0,
            epoch_sec=server_epoch + 60,
            commission=-0.5,
        )
        # Close deal: DIFFERENT order ticket 1011, same position 7101, real loss.
        seed_deal(
            repo,
            9102,
            1011,
            7101,
            entry=1,
            volume=0.1,
            price=1984.0,
            profit=-165.69,
            epoch_sec=server_epoch + 600,
            commission=-1.0,
        )

        sweep = HistoricalOutcomeRecoverySweep(ledger=ledger)
        res = sweep.run()

        assert res.filled_recovered == 1
        assert res.skipped_no_close_deals == 0
        assert res.skipped_invalid == 0

        merged_rec = ledger.get_experience_by_key(rec.idempotency_key)
        assert merged_rec is not None
        assert merged_rec.is_executed is True
        assert merged_rec.is_closed is True
        # net = -165.69 - 0.5 (entry commission) - 1.0 (close commission)
        assert merged_rec.realized_pnl_usd == pytest.approx(-167.19, rel=1e-6)
        # risk = |2000 - 1990| * 0.1 * 100 = 100 USD
        assert merged_rec.realized_r_multiple == pytest.approx(-1.6719, rel=1e-3)

        payload = get_outcome_payload(repo, rec.idempotency_key)
        assert payload
        assert RECOVERY_SOURCE_BROKER_HISTORY in payload.get("correlation_detail", "")
        bo = payload.get("broker_outcome") or {}
        assert bo.get("reconstruction_source") in (
            "BROKER_DEALS",
            "BROKER_DEALS_AGGREGATED",
        )

    def test_canceled_order_recovers_as_canceled_unfilled(self, repo, ledger):
        """Invariant 3: canceled pending -> CANCELED_UNFILLED (no fake trade)."""
        dec_ts = datetime(2026, 8, 1, 11, 0, tzinfo=UTC)
        rec = seed_decision(ledger, "req_cx_1", dec_ts)
        seed_dispatch(repo, "req_cx_1", ticket=2001, ts=dec_ts)
        seed_broker_order(repo, ticket=2001, state=2, position_id=0)  # state=2 CANCELED

        sweep = HistoricalOutcomeRecoverySweep(ledger=ledger)
        res = sweep.run()
        assert res.recovered == 1
        assert res.canceled_recovered == 1
        assert res.filled_recovered == 0

        merged_rec = ledger.get_experience_by_key(rec.idempotency_key)
        assert merged_rec is not None
        assert merged_rec.is_executed is False
        assert merged_rec.is_closed is True
        assert merged_rec.realized_r_multiple == 0.0
        assert merged_rec.realized_pnl_usd == 0.0
        assert merged_rec.exit_reason == "CANCELED_UNFILLED"

    def test_expired_and_rejected_states(self, repo, ledger):
        """Invariant 4: EXPIRED (state=6) and REJECTED (state=5)."""
        dec_ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        seed_decision(ledger, "req_exp", dec_ts)
        seed_dispatch(repo, "req_exp", ticket=3001, ts=dec_ts)
        seed_broker_order(repo, ticket=3001, state=6)

        seed_decision(ledger, "req_rej", dec_ts + timedelta(minutes=1))
        seed_dispatch(repo, "req_rej", ticket=3002, ts=dec_ts)
        seed_broker_order(repo, ticket=3002, state=5)

        res = HistoricalOutcomeRecoverySweep(ledger=ledger).run()
        assert res.expired_recovered == 1
        assert res.rejected_recovered == 1
        assert ledger.get_experience_by_key("exp_req_exp").exit_reason == "EXPIRED_UNFILLED"
        assert ledger.get_experience_by_key("exp_req_rej").exit_reason == "REJECTED_UNFILLED"

    def test_fill_without_close_deals_is_skipped(self, repo, ledger):
        """Invariant 5: fill with NO close deal is an open position -> skipped."""
        dec_ts = datetime(2026, 8, 1, 13, 0, tzinfo=UTC)
        seed_decision(ledger, "req_open", dec_ts)
        seed_dispatch(repo, "req_open", ticket=4001, ts=dec_ts)
        server_epoch = int(dec_ts.timestamp()) + 180 * 60
        seed_broker_order(repo, ticket=4001, state=4, position_id=6001, time_setup=server_epoch)
        # Entry deal ONLY (no exit deal)
        seed_deal(
            repo,
            9021,
            4001,
            6001,
            entry=0,
            volume=0.1,
            price=2000.0,
            profit=0.0,
            epoch_sec=server_epoch,
        )

        res = HistoricalOutcomeRecoverySweep(ledger=ledger).run()
        assert res.recovered == 0
        assert res.skipped_no_close_deals == 1
        # Outcome remains unpersisted (honest: still open / incomplete)
        assert ledger.has_outcome("exp_req_open") is False

    def test_causality_refusal(self, repo, ledger):
        """Invariant 6: close time before decision time is refused."""
        dec_ts = datetime(2026, 8, 1, 14, 0, tzinfo=UTC)
        seed_decision(ledger, "req_bad_caus", dec_ts)
        seed_dispatch(repo, "req_bad_caus", ticket=5001, ts=dec_ts)
        # Deal epoch set to 1 hour BEFORE decision
        bad_epoch = int((dec_ts - timedelta(hours=1)).timestamp()) + 180 * 60
        seed_broker_order(repo, ticket=5001, state=4, position_id=7001, time_setup=bad_epoch)
        seed_deal(
            repo,
            9031,
            5001,
            7001,
            entry=0,
            volume=0.1,
            price=2000.0,
            profit=0.0,
            epoch_sec=bad_epoch,
        )
        seed_deal(
            repo,
            9032,
            5001,
            7001,
            entry=1,
            volume=0.1,
            price=2005.0,
            profit=50.0,
            epoch_sec=bad_epoch + 10,
        )

        res = HistoricalOutcomeRecoverySweep(ledger=ledger).run()
        assert res.recovered == 0
        assert res.skipped_causality == 1
        assert ledger.has_outcome("exp_req_bad_caus") is False

    def test_no_dispatch_evidence_skipped(self, repo, ledger):
        """Invariant 7: decision without audit_orders row is skipped (not guessed)."""
        dec_ts = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)
        seed_decision(ledger, "req_no_disp", dec_ts)
        res = HistoricalOutcomeRecoverySweep(ledger=ledger).run()
        assert res.recovered == 0
        assert res.skipped_no_dispatch == 1

    def test_sweep_is_idempotent(self, repo, ledger):
        """Invariant 8: running twice yields zero new writes; no row duplicated."""
        dec_ts = datetime(2026, 8, 1, 16, 0, tzinfo=UTC)
        seed_decision(ledger, "req_idem", dec_ts)
        seed_dispatch(repo, "req_idem", ticket=6001, ts=dec_ts)
        server_epoch = int(dec_ts.timestamp()) + 180 * 60
        seed_broker_order(repo, ticket=6001, state=4, position_id=8001, time_setup=server_epoch)
        seed_deal(
            repo,
            9041,
            6001,
            8001,
            entry=0,
            volume=0.1,
            price=2000.0,
            profit=0.0,
            epoch_sec=server_epoch,
        )
        seed_deal(
            repo,
            9042,
            6001,
            8001,
            entry=1,
            volume=0.1,
            price=2010.0,
            profit=100.0,
            epoch_sec=server_epoch + 60,
        )

        first = HistoricalOutcomeRecoverySweep(ledger=ledger).run()
        assert first.recovered == 1

        second = HistoricalOutcomeRecoverySweep(ledger=ledger).run()
        # All decisions now have outcomes -> scanned=0, recovered=0
        assert second.scanned == 0
        assert second.recovered == 0

    def test_dry_run_does_not_persist(self, repo, ledger):
        """Invariant 10: dry_run classifies without writing."""
        dec_ts = datetime(2026, 8, 1, 17, 0, tzinfo=UTC)
        seed_decision(ledger, "req_dry", dec_ts)
        seed_dispatch(repo, "req_dry", ticket=7001, ts=dec_ts)
        seed_broker_order(repo, ticket=7001, state=2)

        res = HistoricalOutcomeRecoverySweep(ledger=ledger).run(dry_run=True)
        assert res.recovered == 1
        assert res.canceled_recovered == 1
        # Ledger stays empty
        assert ledger.has_outcome("exp_req_dry") is False

    def test_dataset_census_counts_recovered_outcomes(self, repo, ledger):
        """Invariant 9: dataset census counts recovered outcomes explicitly."""
        dec_ts = datetime(2026, 8, 1, 18, 0, tzinfo=UTC)
        seed_decision(ledger, "req_ds_fill", dec_ts)
        seed_dispatch(repo, "req_ds_fill", ticket=8001, ts=dec_ts)
        server_epoch = int(dec_ts.timestamp()) + 180 * 60
        seed_broker_order(repo, ticket=8001, state=4, position_id=9001, time_setup=server_epoch)
        seed_deal(
            repo,
            9051,
            8001,
            9001,
            entry=0,
            volume=0.1,
            price=2000.0,
            profit=0.0,
            epoch_sec=server_epoch,
        )
        seed_deal(
            repo,
            9052,
            8001,
            9001,
            entry=1,
            volume=0.1,
            price=2015.0,
            profit=150.0,
            epoch_sec=server_epoch + 120,
        )

        # Recover the trade
        HistoricalOutcomeRecoverySweep(ledger=ledger).run()

        # Build research dataset
        builder = ResearchDatasetBuilder(ledger)
        ds = builder.build()
        assert len(ds.samples) == 1
        assert ds.samples[0].realized_pnl_usd == pytest.approx(150.0, rel=1e-6)
        census = ds.provenance_extra
        assert census["total_decisions"] == 1
        assert census["valid_research_samples"] == 1
        assert census["recovered_outcomes"] == 1
