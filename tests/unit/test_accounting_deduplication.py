"""TDD Step 1 — Deduplication & idempotency accounting tests.

One broker deal ticket MUST count exactly once regardless of repeated polling,
reconnects, rolling-window re-fetches, or terminal restarts. The
broker-history layer enforces this with UNIQUE(ticket) insert-or-ignore
and position_id reconstruction; these tests lock the regression.
"""

from __future__ import annotations

import gc
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_scalp.accounting import AccountingCore, PeriodKind
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.broker_history import deal_identity

_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "mt5"
    / "accounting"
    / "2026-08-21_closed_deals.json"
)


def _fixture_objects() -> list[dict]:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    skip = frozenset(
        {"count", "index", "n_fields", "n_sequence_fields", "n_unnamed_fields", "_none"}
    )
    return [
        {k: (v["value"] if isinstance(v, dict) else v) for k, v in obj.items() if k not in skip}
        for obj in payload.get("objects", [])
    ]


@pytest.fixture()
def audit(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'dedup.db'}", flush_interval_sec=0.05)
    yield repo
    repo.close()
    gc.collect()


@pytest.fixture()
def core(audit) -> AccountingCore:
    return AccountingCore(audit_repo=audit, adapter=None)


def _seed_broker(audit: AuditRepository, deals: list[dict]):
    entries: list[dict] = []
    for d in deals:
        entries.append(
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
                "commission": 0.0,
                "swap": 0.0,
                "fee": 0.0,
                "reason": 0,
                "comment": "ENTRY",
                "external_id": "",
            }
        )
    audit.sync_broker_history(
        orders=[],
        deals=entries + deals,
        symbol="XAUUSD",
        sync_from=datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
        sync_to=datetime(2026, 8, 22, 0, 0, tzinfo=UTC),
    )


class TestDeduplication:
    def test_repeated_sync_is_idempotent(self, audit, core):
        deals = _fixture_objects()
        _seed_broker(audit, deals)
        n1 = len(core.load_trades())
        _seed_broker(audit, deals)  # second identical ingestion
        n2 = len(core.load_trades())
        assert n1 == n2 == 5

    def test_twenty_polls_still_five_trades(self, audit, core):
        deals = _fixture_objects()
        for _ in range(20):
            _seed_broker(audit, deals)
        assert len(core.load_trades()) == 5

    def test_deal_ticket_is_stable_dedup_key(self):
        deals = _fixture_objects()
        assert len({deal_identity(d) for d in deals}) == 5

    def test_overlapping_window_re_ingests_only_duplicates(self, audit, core):
        deals = _fixture_objects()
        _seed_broker(audit, deals)
        # Simulate a rolling 1-day overlap window that re-contains one deal
        subset = deals[:1]
        _seed_broker(audit, subset)
        assert len(core.load_trades()) == 5

    def test_reconnect_empty_window_does_not_clear_state(self, audit, core):
        deals = _fixture_objects()
        _seed_broker(audit, deals)
        before = {t.ticket for t in core.load_trades()}
        # Empty fetch (e.g. MT5 transiently returns no deals) must not wipe state
        audit.sync_broker_history(
            orders=[],
            deals=[],
            symbol="XAUUSD",
            sync_from=datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
            sync_to=datetime(2026, 8, 22, 0, 0, tzinfo=UTC),
        )
        after = {t.ticket for t in core.load_trades()}
        assert after == before

    def test_period_report_idempotent_across_repeated_queries(self, audit, core):
        deals = _fixture_objects()
        _seed_broker(audit, deals)
        at = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
        r1 = core.period_report(PeriodKind.DAY, at=at, use_cache=False)
        r2 = core.period_report(PeriodKind.DAY, at=at, use_cache=False)
        assert r1.total_trades == r2.total_trades == 5
        assert round(r1.net_pnl, 2) == round(r2.net_pnl, 2)
