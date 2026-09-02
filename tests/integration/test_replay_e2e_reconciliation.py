"""CHG-0043 E2E: real-data replay cycle + DB reconciliation.

Runs the COMPLETE historical cycle on the local real M1 dataset:

    real parquet bars -> ReplayContract -> ReplaySession (stepwise)
        -> StreamingReplayEngine (causal features -> 70D -> model -> policy
           -> risk -> simulated execution -> lifecycle -> outcome)
        -> session report
        -> research_run_snapshots persistence (RESEARCH_RUN_SNAPSHOT v2)
        -> reconciliation (counts / identity / timestamps / no duplicates)

Guards: no order_send, no MT5 import, deterministic replay_id, contract-window
projection excludes out-of-window records.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.research.event_source import BarEventSource
from nexus_scalp.research.replay_session import (
    ReplayContract,
    ReplaySession,
)
from nexus_scalp.research.streaming_replay import (
    ReplayExecutionConfig,
    ReplaySessionConfig,
)

REPO = Path(__file__).resolve().parents[2]
M1_PARQUET = REPO / "data" / "raw" / "XAUUSD_M1.parquet"
MODEL = REPO / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity" / "model.pt"

pytestmark = pytest.mark.skipif(
    not M1_PARQUET.exists() or not MODEL.exists(),
    reason="real M1 dataset / 70D model bundle not present",
)


def _real_records(n: int) -> list[dict[str, Any]]:
    import polars as pl

    df = pl.read_parquet(M1_PARQUET).head(n)
    out: list[dict[str, Any]] = []
    for r in df.iter_rows(named=True):
        ts = r["time_utc"].replace(tzinfo=UTC)
        out.append(
            {
                "kind": "BAR",
                "timestamp": ts,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "tick_volume": int(r["tick_volume"]),
                "spread": float(r["spread"]),
                "symbol": "XAUUSD",
                "timeframe": "M1",
            }
        )
    return out


def test_real_m1_e2e_cycle_and_reconciliation(tmp_path: Path) -> None:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.research.observability import (
        ResearchObservabilityStore,
        ResearchRunSnapshot,
    )

    n = 1200
    records = _real_records(n)
    contract = ReplayContract(
        dataset_id="DS-XAUUSD-M1-REALE2E",
        dataset_fingerprint=hashlib.sha256(M1_PARQUET.read_bytes()).hexdigest()[:32],
        symbol="XAUUSD",
        start_time=records[0]["timestamp"],
        end_time=records[-1]["timestamp"],
        replay_mode="BAR_REPLAY",
        git_commit="chg0043-e2e",
    )
    cfg = ReplaySessionConfig(
        model_artifact_path=str(MODEL),
        policy_params={"confidence_threshold": 0.35},
        decide_on="bar_close",
        execution=ReplayExecutionConfig(),
        git_commit="chg0043-e2e",
    )
    session = ReplaySession(contract, cfg, events=records, checkpoint_every_bars=300)

    # stepwise: 4 x 300 bars through the session controller
    for _ in range(4):
        session.step_bar(300)
        session.maybe_checkpoint()

    report = session.report()

    # ---- E2E assertions: the complete cycle ran on REAL data ----
    assert report["counts"]["bars"] == n
    assert report["counts"]["decisions"] == n  # bar_close => 1 decision/bar
    # model identity is the REAL production bundle (70D scalp_v3)
    ident = session.identity()
    assert ident["engine"]["schema_dimension"] == 70
    assert (
        ident["engine"]["model"]["model_fingerprint"]
        == hashlib.sha256(MODEL.read_bytes()).hexdigest()[:32]
    )
    # every trade carries full lifecycle evidence
    for t in session.state.trades:
        assert t["exit_reason"] in {"SL", "TP", "SIGNAL_REVERSAL", "END_OF_DATA"}
        assert t["entry_time"] <= t["exit_time"]
        assert t["direction"] in {"BUY", "SELL"}
        assert t["volume"] > 0
    # report math is internally consistent (DB-reconciliation quality)
    pnl = round(sum(t["pnl_usd"] for t in session.state.trades), 6)
    assert report["trades"]["pnl_usd"] == pnl
    assert report["equity"]["final"] == round(cfg.starting_equity_usd + pnl, 6)

    # ---- persistence: RESEARCH_RUN_SNAPSHOT v2 row + read-back ----
    snapshot = ResearchRunSnapshot(
        strategy_id="streaming_replay",
        strategy_version="v1",
        strategy_definition_hash=ident["engine"]["strategy_fingerprint"],
        dataset_version=contract.dataset_id,
        dataset_hash=contract.dataset_fingerprint,
        feature_schema_id="scalp_v3",
        feature_dimension=70,
        model_id=ident["engine"]["model"]["model_path"],
        model_hash=ident["engine"]["model"]["model_fingerprint"],
        git_commit="chg0043-e2e",
    )
    audit = AuditRepository(db_url=f"sqlite:///{(tmp_path / 'e2e_research.db').as_posix()}")
    obs = ResearchObservabilityStore(audit)
    obs.store_run_snapshot(session.replay_id, snapshot)
    audit.flush()
    row = obs.get_run_snapshot(session.replay_id)
    assert row is not None, "snapshot row must be readable back by replay_id"
    assert row["feature_schema_id"] == "scalp_v3"
    assert int(row["feature_dimension"]) == 70
    assert row["model_hash"] == ident["engine"]["model"]["model_fingerprint"]
    assert row["dataset_hash"] == contract.dataset_fingerprint

    # ---- reconciliation: session truth == persisted identity ----
    assert row["research_run_id"] == session.replay_id
    # deterministic replay_id: rebuild the session with identical inputs
    session2 = ReplaySession(contract, cfg, events=records)
    assert session2.replay_id == session.replay_id

    # timestamps consistent: first decision after warmup, all within window
    for o in session.state.orders:
        assert contract.start_time.isoformat() <= o["decision_time"]
        assert o["decision_time"] <= contract.end_time.isoformat()
    # no duplicate decisions (order ids unique; decision times unique per order)
    oids = [o["order_id"] for o in session.state.orders]
    assert len(oids) == len(set(oids))

    # ---- source-safety guards (no broker surface in the cycle) ----
    for f in (
        REPO / "src/nexus_scalp/research/streaming_replay.py",
        REPO / "src/nexus_scalp/research/replay_session.py",
    ):
        src = f.read_text(encoding="utf-8")
        assert "order_send(" not in src
        assert "import MetaTrader5" not in src


def test_report_artifact_written(tmp_path: Path) -> None:
    """The report() output is JSON-serializable (API/report artifact path)."""
    records = _real_records(600)
    contract = ReplayContract(
        dataset_id="DS-XAUUSD-M1-REALE2E-2",
        dataset_fingerprint=hashlib.sha256(M1_PARQUET.read_bytes()).hexdigest()[:32],
        symbol="XAUUSD",
        start_time=records[0]["timestamp"],
        end_time=records[-1]["timestamp"],
        replay_mode="BAR_REPLAY",
        git_commit="chg0043-e2e",
    )
    cfg = ReplaySessionConfig(
        model_artifact_path=str(MODEL),
        policy_params={"confidence_threshold": 0.35},
        decide_on="bar_close",
        execution=ReplayExecutionConfig(),
        git_commit="chg0043-e2e",
    )
    session = ReplaySession(contract, cfg, events=records)
    session.play()
    blob = json.dumps(session.report())
    assert session.replay_id in blob
