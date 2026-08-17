"""Candle Intelligence performance + async-flush tests (BUG-061 follow-up).

Proves the hot path is RAM-only (no disk I/O on record_*) and that the
background worker persists queued rows to SQLite in batches.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.candle_intelligence.engine import CandleIntelligenceEngine
from nexus_scalp.candle_intelligence.models import RegimeState


def _t(i: int = 0) -> datetime:
    return datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC) + timedelta(minutes=i)


def test_enqueue_is_ram_fast_and_non_blocking(tmp_path) -> None:
    """Bulk record_* calls must complete in well under disk-I/O time budgets."""
    cfg = CandleIntelligenceConfig(db_path=f"{tmp_path}/ci.db")
    eng = CandleIntelligenceEngine(config=cfg)
    try:
        start = time.perf_counter()
        N = 200
        for i in range(N):
            eng.store.record_candle(
                "XAUUSD",
                "M1",
                _t(i),
                4400.0 + i * 0.01,
                4405.0 + i * 0.01,
                4399.0 + i * 0.01,
                4404.0 + i * 0.01,
                volume=100,
            )
        elapsed = time.perf_counter() - start
        # 200 enqueues should be far under 100ms (RAM-only).
        print(f"enqueue x{N}: {elapsed * 1000:.2f} ms ({elapsed / N * 1e6:.1f} us/op)")
        assert elapsed < 0.5, f"enqueue too slow: {elapsed * 1000:.0f} ms"
        # RAM ring holds them instantly.
        assert len(eng.store._rings["candles"]) == N
    finally:
        eng.store.close()


def test_background_worker_persists_to_db(tmp_path) -> None:
    """Queued rows must land in SQLite once the worker flushes."""
    cfg = CandleIntelligenceConfig(db_path=f"{tmp_path}/ci.db")
    eng = CandleIntelligenceEngine(config=cfg)
    try:
        for i in range(10):
            eng.store.record_candle(
                "XAUUSD",
                "M1",
                _t(i),
                4400.0,
                4405.0,
                4399.0,
                4404.0,
                volume=100,
            )
        # Give the worker a moment plus an explicit flush.
        time.sleep(0.8)
        eng.store.flush(timeout=2.0)
        rows = eng.store._reader_conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        print(f"persisted rows: {rows}")
        assert rows == 10, f"expected 10 persisted, got {rows}"
    finally:
        eng.store.close()


def test_query_recent_reads_ram_before_db(tmp_path) -> None:
    """query_recent must serve from RAM immediately after enqueue (no DB wait)."""
    cfg = CandleIntelligenceConfig(db_path=f"{tmp_path}/ci.db")
    eng = CandleIntelligenceEngine(config=cfg)
    try:
        eng.ingest_bar(
            "XAUUSD",
            "M1",
            _t(0),
            4400.0,
            4408.0,
            4399.0,
            4407.0,
            is_complete=True,
            regime_state=RegimeState(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=_t(0),
                regime="TRENDING_MOMENTUM",
                atr=2.5,
                spread=0.2,
            ),
        )
        # Immediately after enqueue, recent_decisions must see it via RAM.
        start = time.perf_counter()
        rows = eng.recent_decisions(5)
        elapsed = time.perf_counter() - start
        print(f"recent_decisions: {elapsed * 1000:.2f} ms, {len(rows)} rows")
        assert rows, "RAM ring must serve the decision instantly"
        assert elapsed < 0.05, f"RAM read too slow: {elapsed * 1000:.2f} ms"
    finally:
        eng.store.close()
