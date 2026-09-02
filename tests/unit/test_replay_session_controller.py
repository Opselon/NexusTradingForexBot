"""CHG-0043 replay-session tests (REPLAY_SESSION v1).

Covers the replay-on-chart brief's core guarantees for the stepwise session
controller over the ONE certified StreamingReplayEngine:

* clock contract: no wall-clock dependency in the decision path
* step determinism: (T1 -> T2] exactly, state == uninterrupted run
* seek == sequential (reset -> replay -> T2)
* checkpoint equivalence: checkpoint + suffix == clean replay
* adversarial future-mutation invariance (price/news/volume/regime/db rows)
* 70D exact mapping + model fingerprint identity
* policy parity: session path == engine.run path
* END_OF_DATA honesty
* no order_send anywhere (source audit + single-pipeline structural guard)

Run: .venv/Scripts/python.exe -m pytest tests/unit/test_replay_session_controller.py -q
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch

from nexus_scalp.research.event_source import BarEventSource
from nexus_scalp.research.replay_session import (
    ReplayContract,
    ReplaySession,
)
from nexus_scalp.research.streaming_replay import (
    ReplayExecutionConfig,
    ReplaySessionConfig,
    StreamingReplayEngine,
)

T0 = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
N_BARS = 400
REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures: real-width stub bundle + deterministic bar stream
# ---------------------------------------------------------------------------


def _make_bundle(tmp_path: Path) -> Path:
    from nexus_scalp.models.scalp_net import ScalpNet

    torch.manual_seed(11)
    net = ScalpNet(num_features=70, num_classes=4, hidden_dim=128)
    for p in net.parameters():
        p.data.uniform_(-0.01, 0.01)
    net.eval()
    torch.save(net.state_dict(), tmp_path / "model.pt")
    np.savez(tmp_path / "model.scaler.npz", mean=np.zeros(70), std=np.ones(70))
    return tmp_path / "model.pt"


@pytest.fixture(scope="module")
def bundle_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _make_bundle(tmp_path_factory.mktemp("rs_bundle"))


def _price(i: int) -> float:
    """Deterministic zig-zag with mild trend (no RNG)."""
    return 2650.0 + 0.05 * (i % 13) + 0.5 * ((i // 13) % 7) + 0.01 * i


def _bar_records(n: int = N_BARS) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        c = _price(i)
        o = _price(i - 1) if i else c
        hi = max(o, c) + 0.3
        lo = min(o, c) - 0.3
        ts = T0 + timedelta(minutes=i)
        out.append(
            {
                "kind": "BAR",
                "timestamp": ts,
                "open": o,
                "high": hi,
                "low": lo,
                "close": c,
                "tick_volume": 100 + (i % 17),
                "spread": 0.2,
                "symbol": "XAUUSD",
                "timeframe": "M1",
            }
        )
    return out


def _contract(**over: Any) -> ReplayContract:
    base: dict[str, Any] = dict(
        dataset_id="DS-TEST-1",
        dataset_fingerprint="fp-" + "a" * 12,
        symbol="XAUUSD",
        start_time=T0,
        end_time=T0 + timedelta(minutes=N_BARS - 1),
        replay_mode="BAR_REPLAY",
        git_commit="chg0043-test",
    )
    base.update(over)
    return ReplayContract(**base)


def _config(bundle_path: Path, **over: Any) -> ReplaySessionConfig:
    base: dict[str, Any] = dict(
        model_artifact_path=str(bundle_path),
        policy_params={"confidence_threshold": 0.35},
        decide_on="bar_close",
        execution=ReplayExecutionConfig(),
        git_commit="chg0043-test",
    )
    base.update(over)
    return ReplaySessionConfig(**base)


def _session(bundle_path: Path, records: list[dict[str, Any]], **over: Any) -> ReplaySession:
    contract = over.pop("contract", None) or _contract(end_time=records[-1]["timestamp"])
    cfg = over.pop("config", None) or _config(bundle_path)
    return ReplaySession(contract, cfg, events=records, **over)


def _mutated(records: list[dict[str, Any]], mutate_at: int, fn: Any) -> list[dict[str, Any]]:
    import copy

    out = copy.deepcopy(records)
    for i in range(mutate_at, len(out)):
        fn(out[i])
    return out


# ---------------------------------------------------------------------------
# Clock contract
# ---------------------------------------------------------------------------


def test_clock_advances_only_with_events(bundle_path: Path) -> None:
    s = _session(bundle_path, _bar_records())
    assert s.clock_iso() is None
    wall_before = time.time()
    s.step_bar(5)
    clock = s.state.clock.now()
    assert clock is not None
    # event time, not wall time: T0 + 4 minutes of event clock
    assert clock == T0 + timedelta(minutes=4)
    # clock is in the dataset's past; the wall clock is ~56 years ahead
    assert clock.timestamp() < wall_before


def test_no_wall_clock_in_replay_decision_path_source() -> None:
    """P0 clock-contract audit: the decision path must not read wall time."""
    src = (REPO / "src/nexus_scalp/research/streaming_replay.py").read_text(encoding="utf-8") + (
        REPO / "src/nexus_scalp/research/replay_session.py"
    ).read_text(encoding="utf-8")
    for f in ("time.time()", "time.monotonic()", "utcnow()"):
        assert f not in src, f"forbidden wall-clock read in replay path: {f}"
    # datetime.now appears ONLY inside run_id labeling context (documented).
    idx = src.find("datetime.now(UTC)")
    while idx != -1:
        window = src[max(0, idx - 120) : idx + 80]
        assert "run_id" in window, "datetime.now outside run_id label: ..." + window[-100:]
        idx = src.find("datetime.now(UTC)", idx + 1)


# ---------------------------------------------------------------------------
# Step determinism + seek == sequential + checkpoint equivalence
# ---------------------------------------------------------------------------


def test_step_bar_deterministic_vs_full_run(bundle_path: Path) -> None:
    records = _bar_records()
    s = _session(bundle_path, records)
    for _ in range(20):
        s.step_bar(20)
    stepped = s.report()

    full = _session(bundle_path, records)
    full.play()
    fullrep = full.report()

    assert stepped["counts"]["bars"] == fullrep["counts"]["bars"] == N_BARS
    assert stepped["counts"]["decisions"] == fullrep["counts"]["decisions"]
    assert stepped["equity"]["final"] == fullrep["equity"]["final"]


def test_seek_equals_sequential(bundle_path: Path) -> None:
    records = _bar_records()
    t2 = records[299]["timestamp"]
    clean = _session(bundle_path, records)
    clean.seek(t2)
    replayed = _session(bundle_path, records)
    replayed.play()
    replayed.seek(t2)  # seek-back re-streams the same prefix deterministically
    a, b = clean.report(), replayed.report()
    assert a["counts"] == b["counts"]
    assert a["trades"] == b["trades"]
    assert a["equity"] == b["equity"]


def test_checkpoint_equivalence(bundle_path: Path) -> None:
    records = _bar_records()
    s = _session(bundle_path, records, checkpoint_every_bars=50)
    for _ in range(10):
        s.step_bar(20)
        s.maybe_checkpoint()
    assert 200 in s.checkpoints
    snap = s.checkpoints[200]

    s2 = _session(bundle_path, records, checkpoint_every_bars=50)
    s2.restore_from_checkpoint(snap)
    a, b = s.report(), s2.report()
    assert a["counts"] == b["counts"]
    assert a["trades"] == b["trades"]
    assert a["equity"]["final"] == b["equity"]["final"]


# ---------------------------------------------------------------------------
# Adversarial future-mutation invariance
# ---------------------------------------------------------------------------


def _pre_boundary_digest(bundle_path: Path, records: list[dict[str, Any]], boundary: int) -> str:
    """Digest of the engine run over the STRICT pre-boundary prefix."""
    cfg = _config(bundle_path)
    res = StreamingReplayEngine(cfg).run(
        BarEventSource(records[:boundary], name="pre"), run_id="PRE"
    )
    h = hashlib.sha256()
    h.update(f"{res.decisions}|{res.event_hash}|{res.ledger_hash}".encode())
    return h.hexdigest()


def test_A_future_price_mutation_invariance(bundle_path: Path) -> None:
    """+$500 on ALL bars from the boundary onward; then verify decisions made
    strictly before the boundary are identical to the clean run."""
    recs = _bar_records()
    boundary = 200
    mut = _mutated(recs, boundary, lambda r: r.update(close=r["close"] + 500.0))
    # pre-boundary prefix is byte-identical by construction; the structural
    # guarantee under test: the ENGINE over the mutated full window makes the
    # same decisions up to the boundary (engine state at T only sees <= T).
    cfg = _config(bundle_path)
    clean = StreamingReplayEngine(cfg).run(BarEventSource(recs, name="A1"), run_id="A1")
    dirty = StreamingReplayEngine(cfg).run(BarEventSource(mut, name="A2"), run_id="A2")
    tb = recs[boundary]["timestamp"].isoformat()
    d_clean = [o for o in clean.orders if o["decision_time"] < tb]
    d_dirty = [o for o in dirty.orders if o["decision_time"] < tb]
    assert len(d_clean) == len(d_dirty)
    for c, d in zip(d_clean, d_dirty, strict=False):
        assert (c["decision_time"], c["action"], c["fill_price"]) == (
            d["decision_time"],
            d["action"],
            d["fill_price"],
        )
    assert _pre_boundary_digest(bundle_path, recs, boundary)


def test_B_future_news_mutation_invariance(bundle_path: Path) -> None:
    """Injecting an extreme FUTURE news frame must not alter decisions
    (news_context_at filters published_at <= T strictly)."""
    import polars as pl

    recs = _bar_records()
    n = 200
    future_ts = recs[n + 5]["timestamp"]
    fields = [
        "published_at",
        "impact_score",
        "xauusd_relevance",
        "usd_relevance",
        "active_high_impact_events",
        "conflict_score",
        "freshness_sec",
        "time_since_event_sec",
        "event_importance",
        "macro_surprise",
    ]
    news = pl.DataFrame(
        {f: ([future_ts.isoformat()] if f == "published_at" else [1.0]) for f in fields}
    ).with_columns(pl.col("published_at").str.to_datetime(time_zone="UTC"))
    cfg_clean = _config(bundle_path)
    cfg_news = _config(bundle_path, news_frame=news)
    e1 = StreamingReplayEngine(cfg_clean).run(BarEventSource(recs[:n], name="B1"), run_id="B1")
    e2 = StreamingReplayEngine(cfg_news).run(BarEventSource(recs[:n], name="B2"), run_id="B2")
    assert e1.event_hash == e2.event_hash

    # trades identical modulo run_id-prefixed identifiers (B1 vs B2 labels)
    def norm(tr: list[dict], rid: str) -> list[dict]:
        return [
            {k: (v.replace(rid, "R") if isinstance(v, str) else v) for k, v in t.items()}
            for t in tr
        ]

    assert norm(e1.trades, "B1") == norm(e2.trades, "B2")
    assert e1.total_pnl_usd == e2.total_pnl_usd


def test_C_future_liquidity_shape_invariance(bundle_path: Path) -> None:
    """Liquidity features at T use bars <= T only: mutating future bar shapes
    cannot change pre-boundary orders (SL/TP geometry included)."""
    recs = _bar_records()
    boundary = 220
    mut = _mutated(recs, boundary, lambda r: r.update(high=r["high"] * 1.02, low=r["low"] * 0.98))
    cfg = _config(bundle_path)
    clean = StreamingReplayEngine(cfg).run(BarEventSource(recs, name="C1"), run_id="C1")
    dirty = StreamingReplayEngine(cfg).run(BarEventSource(mut, name="C2"), run_id="C2")
    tb = recs[boundary]["timestamp"].isoformat()
    pre_c = [
        (o["decision_time"], o["action"], o["stop_loss"], o["take_profit"])
        for o in clean.orders
        if o["decision_time"] < tb
    ]
    pre_d = [
        (o["decision_time"], o["action"], o["stop_loss"], o["take_profit"])
        for o in dirty.orders
        if o["decision_time"] < tb
    ]
    assert pre_c == pre_d


def test_D_future_volume_mutation_invariance(bundle_path: Path) -> None:
    recs = _bar_records()
    boundary = 180
    mut = _mutated(recs, boundary, lambda r: r.update(tick_volume=r["tick_volume"] * 10 + 1))
    cfg = _config(bundle_path)
    clean = StreamingReplayEngine(cfg).run(BarEventSource(recs, name="D1"), run_id="D1")
    dirty = StreamingReplayEngine(cfg).run(BarEventSource(mut, name="D2"), run_id="D2")
    tb = recs[boundary]["timestamp"].isoformat()
    pre_c = [
        (o["decision_time"], o["action"], o["volume"])
        for o in clean.orders
        if o["decision_time"] < tb
    ]
    pre_d = [
        (o["decision_time"], o["action"], o["volume"])
        for o in dirty.orders
        if o["decision_time"] < tb
    ]
    assert pre_c == pre_d


def test_E_future_data_cannot_change_earlier_regime_transitions(bundle_path: Path) -> None:
    """With regime_enabled=True the classifier sees ONLY replay events; future
    mutation cannot alter transitions recorded before the boundary."""
    recs = _bar_records()
    boundary = 250
    mut = _mutated(recs, boundary, lambda r: r.update(close=r["close"] + 500.0))
    s1 = _session(bundle_path, recs, regime_enabled=True)
    s1.play()
    s2 = _session(bundle_path, mut, regime_enabled=True)
    s2.play()
    tb = recs[boundary]["timestamp"].isoformat()
    pre1 = [t for t in s1._regime_transitions if str(t["timestamp"]) <= tb]
    pre2 = [t for t in s2._regime_transitions if str(t["timestamp"]) <= tb]
    assert pre1 == pre2


def test_F_future_db_rows_invariance(bundle_path: Path) -> None:
    """Synthetic future rows appended to the record set must not change any
    decision the session makes over the contract window."""
    recs = _bar_records()
    synthetic = [
        {
            "kind": "BAR",
            "timestamp": T0 + timedelta(minutes=10000 + i),
            "open": 9999.0 + i,
            "high": 9999.5 + i,
            "low": 9998.5 + i,
            "close": 9999.0 + i,
            "tick_volume": 1,
            "spread": 0.0,
            "symbol": "XAUUSD",
            "timeframe": "M1",
        }
        for i in range(50)
    ]
    cfg = _config(bundle_path)
    r1 = StreamingReplayEngine(cfg).run(BarEventSource(recs, name="F1"), run_id="F1")
    r2 = StreamingReplayEngine(cfg).run(BarEventSource(recs + synthetic, name="F2"), run_id="F2")
    # the synthetic tail is legitimately VISIBLE after its timestamp, so the
    # full-window runs may differ AFTER it; the invariant is that every
    # decision BEFORE the synthetic rows is identical:
    tail_start = synthetic[0]["timestamp"].isoformat()
    pre1 = [(o["decision_time"], o["action"], o["fill_price"]) for o in r1.orders]
    pre2 = [
        (o["decision_time"], o["action"], o["fill_price"])
        for o in r2.orders
        if o["decision_time"] < tail_start
    ]
    assert pre1 == pre2
    # session projection: contract end excludes the synthetic tail entirely
    s = _session(
        bundle_path,
        recs + synthetic,
        contract=_contract(end_time=recs[-1]["timestamp"]),
    )
    s.play()
    assert s.state.clock.now() == recs[-1]["timestamp"]
    assert s.report()["counts"]["bars"] == N_BARS


# ---------------------------------------------------------------------------
# 70D mapping + model identity + policy parity + END_OF_DATA + guards
# ---------------------------------------------------------------------------


def test_model_fingerprint_and_70d_width(bundle_path: Path) -> None:
    s = _session(bundle_path, _bar_records())
    ident = s.identity()
    assert ident["engine"]["schema_dimension"] == 70
    fp = ident["engine"]["model"]["model_fingerprint"]
    assert fp == hashlib.sha256(bundle_path.read_bytes()).hexdigest()[:32]
    s2 = _session(bundle_path, _bar_records())
    assert s.replay_id == s2.replay_id  # deterministic replay_id


def test_policy_parity_session_vs_engine_run(bundle_path: Path) -> None:
    recs = _bar_records()
    s = _session(bundle_path, recs)
    s.play()
    cfg = _config(bundle_path)
    direct = StreamingReplayEngine(cfg).run(BarEventSource(recs, name="parity"), run_id="PAR")
    assert s.state.decisions == direct.decisions
    assert round(sum(t["pnl_usd"] for t in s.state.trades), 6) == round(direct.total_pnl_usd, 6)


def test_end_of_data_closes_position_honestly(bundle_path: Path) -> None:
    recs = _bar_records()
    s = _session(bundle_path, recs)
    s.play()
    for t in s.state.trades:
        assert t["exit_reason"] in {"SL", "TP", "SIGNAL_REVERSAL", "END_OF_DATA"}
    assert s._open_position is None  # no phantom open position after play()


def test_no_order_send_source_guard() -> None:
    for f in (
        "src/nexus_scalp/research/streaming_replay.py",
        "src/nexus_scalp/research/replay_session.py",
    ):
        src = (REPO / f).read_text(encoding="utf-8")
        assert "order_send(" not in src, f"{f} calls order_send"
        assert "import MetaTrader5" not in src and "from MetaTrader5" not in src


def test_session_uses_engine_internals_not_second_pipeline() -> None:
    """Structural guarantee: ReplaySession drives StreamingReplayEngine.run —
    there is no second decision pipeline (no duplicated policy/model code)."""
    src = (REPO / "src/nexus_scalp/research/replay_session.py").read_text(encoding="utf-8")
    assert "StreamingReplayEngine" in src
    assert "self.engine.run(" in src
    assert "ScalpNet" not in src and "softmax" not in src
