"""Agent-5 P2-A GOLDEN TEST (brief #8): snapshot the four DB-reading report
stages (model/execution/behavioral/anomaly_state) BEFORE the queries.py
read-adapter extraction, then assert identical semantics AFTER.

Fixture mirrors test_performance_report_intelligence.py construction
(AuditRepository tmp sqlite -> AccountingCore) so the golden runs over the
real AuditRepository -> AccountingCore -> PerformanceReportEngine chain.
"""
from __future__ import annotations

import gc
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.accounting import AccountingCore, PeriodKind
from nexus_scalp.accounting.periods import period_bounds
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience import ExperienceLedger
from nexus_scalp.reporting import PerformanceReportEngine

GOLDEN_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Temp", "agent5-inv", "p2a_golden.json"
)


class _FakeAdapter:
    def get_account_info(self):  # pragma: no cover - not exercised here
        return None


@pytest.fixture()
def audit(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'test.db'}", flush_interval_sec=0.05)
    yield repo
    repo.close()
    gc.collect()


@pytest.fixture()
def core(audit):
    ledger = ExperienceLedger(audit_repo=audit)
    return AccountingCore(audit_repo=audit, adapter=_FakeAdapter(), experience_ledger=ledger)


def _flush(audit: AuditRepository, seconds: float = 0.4) -> None:
    audit.flush(timeout_sec=5.0)


def _seed_signals(audit, now: datetime, n: int = 6) -> None:
    for i in range(n):
        ts = now - timedelta(minutes=i)
        audit._queue.put_nowait(
            (
                "INSERT INTO audit_signals "
                "(request_id, symbol, action, confidence, proposed_entry, stop_loss, "
                "take_profit, regime, generated_at, payload, execution_mode, "
                "reason_code, decision_stage, blocked_by) "
                "VALUES (?, 'XAUUSD', ?, ?, 0.0, 0.0, 0.0, 'TRENDING', ?, ?, "
                "'STANDARD', '', 'FINAL_DECISION', ?)",
                (
                    f"req_{i}_{ts.timestamp()}",
                    "SELL" if i % 2 else "BUY",
                    0.7,
                    ts.isoformat(),
                    json.dumps({"blocked_by": ["GATE_A"] if i % 3 == 0 else []}),
                    "GATE_A" if i % 3 == 0 else "",
                ),
            )
        )
    _flush(audit)


def _seed_orders(audit, now: datetime, n: int = 4) -> None:
    for i in range(n):
        ts = now - timedelta(minutes=i)
        audit._queue.put_nowait(
            (
                "INSERT INTO audit_orders "
                "(ticket, order_id, symbol, action, price, stop_loss, take_profit, "
                "volume, reason, latency, execution_mode, timestamp) "
                "VALUES (?, ?, 'XAUUSD', 'BUY_MARKET', 2000.0, 1990.0, 2020.0, 1.0, "
                "?, ?, 'STANDARD', ?)",
                (
                    100 + i,
                    f"ord_{i}_{ts.timestamp()}",
                    "execute_order executed",
                    12.5 + i,
                    ts.isoformat(),
                ),
            )
        )
    _flush(audit)


def _capture(engine: PerformanceReportEngine, now: datetime) -> dict:
    bounds = period_bounds(PeriodKind.DAY, now)
    m = engine._stage_model(bounds)
    e = engine._stage_execution(bounds)
    b = engine._stage_behavioral([])
    a = engine._stage_anomaly_state([])

    def _d(x):
        return x.to_dict() if hasattr(x, "to_dict") else x.model_dump()

    return {
        "model": _d(m),
        "execution": _d(e),
        "behavioral_empty": _d(b),
        "anomaly_empty": _d(a),
    }


class TestP2AGoldenDBStages:
    def test_db_stage_sections_stable(self, core, audit) -> None:
        """Sections keep identical record counts/fields across the extraction."""
        now = datetime.now(UTC).replace(microsecond=0)
        _seed_signals(audit, now)
        _seed_orders(audit, now)
        engine = PerformanceReportEngine(core=core, kind=PeriodKind.DAY)
        captured = _capture(engine, now)

        # behavioral/anomaly with empty trade list must be truthful NO_DATA
        assert captured["behavioral_empty"]["state"] == "NO_DATA"
        assert captured["behavioral_empty"]["has_data"] is False
        assert captured["anomaly_empty"]["has_data"] is False

        # model stage: 6 signals in-period, funnel fields present
        assert captured["model"]["has_data"] is True
        assert captured["model"]["prediction_count"] == 6

        # execution stage: 4 orders, latency stats present
        assert captured["execution"]["has_data"] is True
        assert captured["execution"]["sample_count"] == 4

        if os.environ.get("P2A_WRITE_GOLDEN"):
            with open(GOLDEN_PATH, "w", encoding="utf-8") as fh:
                json.dump(captured, fh, indent=1, sort_keys=True, default=str)
            with open(GOLDEN_PATH, encoding="utf-8") as fh:
                golden = json.load(fh)
        else:
            pytest.skip("golden capture mode not requested")

        assert captured == golden

    def test_stage_semantics_preserved(self, core, audit) -> None:
        """Field-level equality between pre/post extraction (runs post-extract)."""
        now = datetime.now(UTC).replace(microsecond=0)
        _seed_signals(audit, now)
        _seed_orders(audit, now)
        engine = PerformanceReportEngine(core=core, kind=PeriodKind.DAY)
        captured = _capture(engine, now)
        if not os.path.exists(GOLDEN_PATH):
            pytest.skip("no golden snapshot yet (pre-extraction run)")
        with open(GOLDEN_PATH, encoding="utf-8") as fh:
            golden = json.load(fh)
        assert captured == golden
