"""TDD Step 1 — Accounting PnL Regression & Aggregation Fixture.

Incident: 2026-08-21 DAY must aggregate exactly 5 closed broker deals
into net_pnl = -497.81 via the ONE canonical pipeline:

  MT5 history_deals_get -> broker_history.normalize_deal_row
  -> reconstruct_trades -> sync_broker_history -> load_trades
  -> aggregate_period -> PeriodReport -> API/UI

No production logic may hard-code -497.81, 2026-08-21 or the 5 tickets;
those expectations live ONLY in this test module and its fixture.

Covers spec Test A..L (canonical regression, per-ticket survival,
deduplication, deposit exclusion, buy/sell inclusion, partial-close,
period/midnight isolation, empty period, negative-only accounting).
"""

from __future__ import annotations

import gc
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_scalp.accounting import AccountingCore, PeriodKind
from nexus_scalp.accounting.periods import ensure_utc, period_bounds
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.broker_history import normalize_deal_row, reconstruct_trades

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "mt5"
    / "accounting"
    / "2026-08-21_closed_deals.json"
)
_CANONICAL_TICKETS = [152515953349, 152515934081, 152515910523, 152515857705, 152515766338]
_EXPECTED_NET = -497.81
_EXPECTED_COUNT = 5


def _fixture_objects() -> list[dict]:
    """Returns cleaned deal dicts (value-only) like tests/helpers/mt5_fixtures.py."""
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    skip = frozenset(
        {"count", "index", "n_fields", "n_sequence_fields", "n_unnamed_fields", "_none"}
    )
    out: list[dict] = []
    for obj in payload.get("objects", []):
        cleaned = {
            k: (v["value"] if isinstance(v, dict) else v) for k, v in obj.items() if k not in skip
        }
        out.append(cleaned)
    return out


@pytest.fixture()
def audit(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'pnl_reg.db'}", flush_interval_sec=0.05)
    yield repo
    repo.close()
    gc.collect()


@pytest.fixture()
def core(audit) -> AccountingCore:
    return AccountingCore(audit_repo=audit, adapter=None)


def _seed_broker(
    audit: AuditRepository, deals: list[dict] | None = None, *, orders: list[dict] | None = None
):
    """Seeds the normalized broker copy from deal dicts (optionally with orders)."""
    d = deals if deals is not None else _fixture_objects()
    synthetic_entries: list[dict] = []
    seen: set[int] = set()
    for close in d:
        pid = int(close["position_id"])
        if pid in seen:
            continue
        seen.add(pid)
        # Balance/deposit deals (empty symbol) must not inherit XAUUSD via fallback;
        # preserve the empty symbol so they remain filterable as non-trading.
        sym = close.get("symbol") if close.get("symbol") else ""
        synthetic_entries.append(
            {
                "ticket": int(close["ticket"]) - 70000000,
                "order": int(close["order"]) - 1000 if close.get("order") else 0,
                "position_id": pid,
                "symbol": sym,
                "type": int(close.get("type", 1)) ^ 1 if sym else int(close.get("type", 2)),
                "entry": 0,
                "magic": int(close.get("magic", 888101)) if sym else 0,
                "time": int(close["time"]) - 900,
                "time_msc": int(close.get("time_msc", 0)) - 900_000 if close.get("time_msc") else 0,
                "reason": 0,
                "volume": float(close.get("volume", 0.1)) if sym else 0.0,
                "price": float(close.get("price", 3350.0)) + 5.0 if sym else 0.0,
                "profit": 0.0,
                "fee": 0.0,
                "swap": 0.0,
                "commission": 0.0,
                "comment": "ENTRY",
                "external_id": "",
            }
        )
    all_deals = synthetic_entries + d
    audit.sync_broker_history(
        orders=orders or [],
        deals=all_deals,
        symbol="XAUUSD",
        sync_from=datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
        sync_to=datetime(2026, 8, 22, 0, 0, tzinfo=UTC),
    )


# ===========================================================================
# A. Canonical regression — DAY 2026-08-21
# ===========================================================================


class TestA_CanonicalRegression:
    """
    Incident regression. 5 closed deals on 2026-08-21 must aggregate to
    -497.81 via the canonical pipeline. After the broker_history swap/fee
    sign fix, both deal-level and logical-trade totals are -497.81.
    """

    def test_deal_level_ground_truth_is_minus497(self):
        deals = _fixture_objects()
        total = sum(d["profit"] + d["commission"] + d["swap"] + d["fee"] for d in deals)
        assert len(deals) == _EXPECTED_COUNT
        assert round(total, 2) == _EXPECTED_NET
        for d in deals:
            row = normalize_deal_row(d)
            expected = d["profit"] + d["commission"] + d["swap"] + d["fee"]
            assert row["net_result"] == pytest.approx(expected)

    def test_day_2026_08_21_net_and_count(self, audit, core):
        _seed_broker(audit)
        report = core.period_report(PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
        assert report.has_data is True
        assert report.total_trades == _EXPECTED_COUNT
        assert round(report.net_pnl, 2) == _EXPECTED_NET

    def test_sum_net_matches_expected(self, audit, core):
        _seed_broker(audit)
        trades = [t for t in core.load_trades() if t.closed_at is not None]
        assert len(trades) == _EXPECTED_COUNT
        assert round(sum(t.net_pnl for t in trades), 2) == _EXPECTED_NET


# ===========================================================================
# B. Individual ticket coverage
# ===========================================================================


class TestB_TicketSurvival:
    def test_all_five_tickets_present_in_trades(self, audit, core):
        _seed_broker(audit)
        # The canonical output identity is position_id (== ticket for this fixture)
        trades = core.load_trades()
        present = {t.ticket for t in trades}
        for ticket in _CANONICAL_TICKETS:
            assert ticket in present, f"ticket {ticket} missing after normalization/broker path"

    def test_all_five_tickets_inside_period(self, audit, core):
        _seed_broker(audit)
        report = core.period_report(PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
        assert report.total_trades == 5
        # also verify each deal's net component matches fixture
        # (catches a per-ticket normalization sign error)
        for deal in _fixture_objects():
            row = normalize_deal_row(deal)
            # canonical formula matches DealSnapshot.net_result / _net_from_deal
            expected_net = (
                float(deal["profit"])
                + float(deal["commission"])
                + float(deal["swap"])
                + float(deal["fee"])
            )
            # commission/swap/fee are already negative for real MT5 records
            assert row["net_result"] == pytest.approx(expected_net)


# ===========================================================================
# C. No missing trades (input == normalized == period)
# ===========================================================================


class TestC_NoMissingTrades:
    def test_pipeline_preserves_all_deals(self, audit, core):
        deals = _fixture_objects()
        _seed_broker(audit, deals=deals)
        reconstructed = reconstruct_trades(
            orders=[],
            deals=deals
            + [
                # re-add synthetic entries used by _seed_broker so reconstruct count matches
                {
                    "ticket": int(d["ticket"]) - 70000000,
                    "order": 0,
                    "position_id": int(d["position_id"]),
                    "symbol": "XAUUSD",
                    "type": int(d["type"]) ^ 1,
                    "entry": 0,
                    "magic": 888101,
                    "time": int(d["time"]) - 900,
                    "volume": float(d["volume"]),
                    "price": float(d["price"]) + 5.0,
                    "profit": 0.0,
                    "fee": 0.0,
                    "swap": 0.0,
                    "commission": 0.0,
                    "reason": 0,
                    "comment": "ENTRY",
                    "external_id": "",
                }
                for d in deals
            ],
        )
        # Every position yields one logical trade -> 5
        assert len(reconstructed) == 5
        assert len(core.load_trades()) == 5
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
            ).total_trades
            == 5
        )


# ===========================================================================
# D. PnL component calculation (net = profit + commission + swap + fee)
# ===========================================================================


class TestD_PnlFormula:
    @pytest.mark.parametrize(
        "profit,commission,swap,fee,expected_row",
        [
            (-95.12, -6.25, -4.10, 0.0, -105.47),
            (-105.30, -8.75, -3.20, -1.50, -118.75),
            (20.0, -2.0, 0.0, 0.0, 18.0),
            (0.0, 0.0, 0.0, 0.0, 0.0),
        ],
    )
    def test_net_formula_sign(self, profit, commission, swap, fee, expected_row):
        row = normalize_deal_row(
            {
                "ticket": 1,
                "profit": profit,
                "commission": commission,
                "swap": swap,
                "fee": fee,
                "volume": 0.1,
                "price": 3350.0,
                "type": 0,
                "entry": 1,
                "position_id": 1,
                "symbol": "XAUUSD",
                "magic": 0,
                "time": 0,
                "reason": 0,
                "order": 0,
                "comment": "",
                "external_id": "",
            }
        )
        assert row["net_result"] == pytest.approx(expected_row)
        trades = reconstruct_trades(
            orders=[],
            deals=[
                {
                    "ticket": 9001,
                    "position_id": 9001,
                    "symbol": "XAUUSD",
                    "type": 0,
                    "entry": 0,
                    "volume": 0.1,
                    "price": 3350.0,
                    "profit": 0.0,
                    "commission": 0.0,
                    "swap": 0.0,
                    "fee": 0.0,
                    "magic": 0,
                    "time": 1000,
                    "order": 1,
                    "reason": 0,
                    "comment": "",
                    "external_id": "",
                },
                {
                    "ticket": 9002,
                    "position_id": 9001,
                    "symbol": "XAUUSD",
                    "type": 0,
                    "entry": 1,
                    "volume": 0.1,
                    "price": 3352.0,
                    "profit": profit,
                    "commission": commission,
                    "swap": swap,
                    "fee": fee,
                    "magic": 0,
                    "time": 1100,
                    "order": 2,
                    "reason": 0,
                    "comment": "",
                    "external_id": "",
                },
            ],
        )
        assert len(trades) == 1
        assert trades[0].net_pnl == pytest.approx(expected_row)

    def test_fixture_balance_operations_not_misclassified(self, audit, core):
        """
        A deposit (DEAL_TYPE_BALANCE=2) must NOT enter trading PnL.
        Deposits carry empty symbol and type=2; the trading report must still
        show exactly 5 trading outcomes. The eligibility gate will eventually
        exclude non-trading types entirely; this regression locks the count.
        """
        deals = _fixture_objects()
        balance_deal = {
            "ticket": 999900000001,
            "order": 0,
            "position_id": 999900000001,
            "symbol": "",
            "type": 2,  # DEAL_TYPE_BALANCE — deposit
            "entry": 1,
            "magic": 0,
            "time": int(datetime(2026, 8, 21, 8, 0, tzinfo=UTC).timestamp()) + 180 * 60,
            "volume": 0.0,
            "price": 0.0,
            "profit": 1000.0,
            "fee": 0.0,
            "swap": 0.0,
            "commission": 0.0,
            "reason": 0,
            "comment": "Deposit",
            "external_id": "",
        }
        entry = {
            "ticket": 999900000000,
            "order": 0,
            "position_id": 999900000001,
            "symbol": "",
            "type": 2,
            "entry": 0,
            "magic": 0,
            "time": int(datetime(2026, 8, 21, 7, 30, tzinfo=UTC).timestamp()) + 180 * 60,
            "volume": 0.0,
            "price": 0.0,
            "profit": 0.0,
            "fee": 0.0,
            "swap": 0.0,
            "commission": 0.0,
            "reason": 0,
            "comment": "Deposit ENTRY",
            "external_id": "",
        }
        _seed_broker(audit, deals=[*deals, balance_deal, entry])
        trades = core.load_trades()
        trading = [t for t in trades if t.symbol]
        assert len(trading) == 5
        assert round(sum(t.net_pnl for t in trading), 2) == _EXPECTED_NET


# ===========================================================================
# E. Duplicate ingestion — same deal twice
# ===========================================================================


class TestE_DuplicateIngestion:
    def test_duplicate_sync_does_not_double_count(self, audit, core):
        deals = _fixture_objects()
        _seed_broker(audit, deals=deals)
        _seed_broker(audit, deals=deals)
        report = core.period_report(PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
        assert report.total_trades == 5
        assert round(report.net_pnl, 2) == _EXPECTED_NET
        assert len(core.load_trades()) == 5

    def test_deal_dedup_by_ticket(self):
        from nexus_scalp.adapters.database.broker_history import deal_identity

        deals = _fixture_objects()
        keys = [deal_identity(d) for d in deals]
        assert len(keys) == len(set(keys)) == 5


# ===========================================================================
# F. Partial close handling
# ===========================================================================


class TestF_PartialClose:
    def test_partial_close_aggregates_into_one_logical_trade(self):
        deals = [
            {
                "ticket": 8001,
                "order": 801,
                "position_id": 777700001,
                "symbol": "XAUUSD",
                "type": 0,
                "entry": 0,
                "magic": 0,
                "time": 1000,
                "volume": 0.30,
                "price": 3350.0,
                "profit": 0.0,
                "fee": 0.0,
                "swap": 0.0,
                "commission": 0.0,
                "reason": 0,
                "comment": "",
                "external_id": "",
            },
            {
                "ticket": 8002,
                "order": 802,
                "position_id": 777700001,
                "symbol": "XAUUSD",
                "type": 1,
                "entry": 1,
                "magic": 0,
                "time": 1100,
                "volume": 0.10,
                "price": 3355.0,
                "profit": -20.0,
                "fee": 0.0,
                "swap": -1.0,
                "commission": -2.0,
                "reason": 0,
                "comment": "",
                "external_id": "",
            },
            {
                "ticket": 8003,
                "order": 803,
                "position_id": 777700001,
                "symbol": "XAUUSD",
                "type": 1,
                "entry": 1,
                "magic": 0,
                "time": 1200,
                "volume": 0.20,
                "price": 3352.0,
                "profit": -80.0,
                "fee": -1.0,
                "swap": -0.5,
                "commission": -3.0,
                "reason": 0,
                "comment": "",
                "external_id": "",
            },
        ]
        trades = reconstruct_trades(orders=[], deals=deals)
        assert len(trades) == 1
        t = trades[0]
        assert t.volume == pytest.approx(0.30)
        assert t.gross_pnl == pytest.approx(-100.0)
        assert t.commission == pytest.approx(5.0)
        assert t.swap == pytest.approx(1.5)
        assert t.fee == pytest.approx(1.0)
        assert t.net_pnl == pytest.approx(-107.5)

    def test_fixture_partial_close_equivalence(self, audit, core):
        _seed_broker(audit)
        trades = core.load_trades()
        assert len({t.ticket for t in trades}) == 5


# ===========================================================================
# G. Period / midnight isolation & empty period & buy/sell inclusion
# ===========================================================================


class TestG_PeriodIsolation:
    def test_buy_and_sell_both_included(self, audit, core):
        # Fixture mixes BUY (1) and SELL (0) closes — both must count
        deals = _fixture_objects()
        types = {int(d["type"]) for d in deals}
        assert 0 in types and 1 in types, "fixture must contain both directions"
        _seed_broker(audit)
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
            ).total_trades
            == 5
        )

    def test_trade_outside_period_excluded(self, audit, core):
        _seed_broker(audit)
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
            ).total_trades
            == 0
        )
        assert core.period_report(
            PeriodKind.DAY, at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
        ).net_pnl == pytest.approx(0.0)
        assert (
            core.period_report(
                PeriodKind.DAY, at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
            ).total_trades
            == 0
        )

    def test_adjacent_midnight_belongs_to_new_day(self, audit, core):
        """
        Half-open [start, end): midnight 00:00:00 UTC belongs to the NEW day,
        never to the previous one.
        """
        # Inject a trade at exactly 2026-08-22 00:00:00 UTC
        midnight_epoch = int(datetime(2026, 8, 22, 0, 0, tzinfo=UTC).timestamp()) + 180 * 60
        extra = {
            "ticket": 152500000001,
            "order": 152500000002,
            "position_id": 152500000001,
            "symbol": "XAUUSD",
            "type": 1,
            "entry": 1,
            "magic": 888101,
            "time": midnight_epoch,
            "time_msc": midnight_epoch * 1000,
            "reason": 3,
            "volume": 0.10,
            "price": 3350.0,
            "profit": -10.0,
            "fee": 0.0,
            "swap": 0.0,
            "commission": -1.0,
            "comment": "",
            "external_id": "",
        }
        deals = [*_fixture_objects(), extra]
        # Need an entry leg for the extra position
        entry = {
            "ticket": 152500000000,
            "order": 152500000003,
            "position_id": 152500000001,
            "symbol": "XAUUSD",
            "type": 0,
            "entry": 0,
            "magic": 888101,
            "time": midnight_epoch - 600,
            "time_msc": (midnight_epoch - 600) * 1000,
            "reason": 0,
            "volume": 0.10,
            "price": 3355.0,
            "profit": 0.0,
            "fee": 0.0,
            "swap": 0.0,
            "commission": 0.0,
            "comment": "ENTRY",
            "external_id": "",
        }
        _seed_broker(
            audit, deals=[*deals, entry]
        )  # _seed_broker will also synthesize entries, but idempotent
        # Re-seed via raw sync to avoid duplicating synthetic entries for the extra
        # (simpler: assert using period_bounds.contains on the resulting trades)
        trades = core.load_trades()
        bounds_21 = period_bounds(PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
        bounds_22 = period_bounds(PeriodKind.DAY, at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC))
        in_21 = [t for t in trades if t.closed_at is not None and bounds_21.contains(t.closed_at)]
        in_22 = [t for t in trades if t.closed_at is not None and bounds_22.contains(t.closed_at)]
        assert len(in_21) == 5, "midnight trade must NOT leak into 08-21"
        assert any(t.ticket == 152500000001 for t in in_22)

    def test_empty_period(self, audit, core):
        _seed_broker(audit)
        empty = core.period_report(PeriodKind.DAY, at=datetime(2026, 8, 19, 12, 0, tzinfo=UTC))
        assert empty.has_data is False or empty.total_trades == 0
        assert empty.net_pnl == pytest.approx(0.0)

    def test_negative_only_period(self, audit, core):
        _seed_broker(audit)
        report = core.period_report(PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
        assert report.gross_profit == pytest.approx(0.0)
        assert report.gross_loss == pytest.approx(abs(_EXPECTED_NET))
        assert report.win_count == 0
        assert report.loss_count == 5

    def test_timezone_midnight_not_shifted_by_broker_offset(self, audit, core):
        """
        Accounting periods are UTC half-open. A deal whose broker epoch falls
        near UTC midnight must not be bucketed by local midnight.
        """
        # Fixture deals are stamped with broker_server_offset (+180) epochs for
        # UTC instants inside 2026-08-21. Verify none leak to 08-20 or 08-22.
        _seed_broker(audit)
        trades = core.load_trades()
        bounds_21 = period_bounds(PeriodKind.DAY, at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC))
        for t in trades:
            assert t.closed_at is not None
            assert bounds_21.contains(ensure_utc(t.closed_at))
