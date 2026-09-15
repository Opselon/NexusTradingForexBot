"""
BUG-297 — Evaluator registry reads: hot-path connection reuse
==============================================================
Perf-wave R5 follow-through (lane-10 §3.1 item 1): the evaluator twin of the
spread-percentile loop-I/O bug fixed by BUG-292/#213 — and the one wave-perf's
closeout named "the most concrete remaining regression risk".

VERIFIED REACHABILITY MAP (2026-09-15, origin/main fa34fccd):

    evaluator.get_registered_strategy_score  (raw connect, timeout 5.0)
      <- intelligence.ExperienceIntelligenceEngine._get_score TIER 3
         (pre-trade budget-exhausted fallback; tier 1 = TTL cache, tier 2 =
         budgeted inline refresh)
         <- _evaluate_internal <- evaluate_proposal
         <- application/live/tick_pipeline.run_post_policy_stages:71
         <- LiveEngine._process_tick_pipeline
         <- application/live/runtime_loop.py:440 — EVENT-LOOP THREAD. PRE-TRADE.
         <- ALSO accounting/core.py:692 (_attach_strategy_intelligence, warmed
            off-loop by AccountingWorker._refresh_once) and :932 (forensic
            trace build, web/off-loop).
    evaluator.list_registered_scores  (raw connect, timeout 5.0)
      <- zero callers in src/ or tests/ today: public API surface, reachable
         from any thread → routed through the same seam.
    evaluator._clear_registry  (raw connect + DELETE, timeout 10.0)
      <- rebuild_derived_intelligence <- self_heal <- startup
         (asyncio.to_thread) / web debug routes — off-loop-safe, but the
         same per-call churn and the same URI-contract bypass; unified onto
         the seam so the evaluator module keeps exactly ONE connect owner.

Pre-fix, every tier-3 proposal on the tick loop paid a full sqlite3.connect
churn, and each raw connect bypassed AuditRepository's single
``_connect_sqlite`` URI contract (shared in-memory audit DBs silently open a
junk FILE without uri=True — the 2026-09-09 disk-leak class).

Contracts pinned here:
  (a) correctness through the real ledger write path,
  (b) REUSE: N lookups cost 1 connect (RED-before: grew per call),
  (c) degradation parity: registry I/O never raises into the caller; a failed
      cached handle is dropped and the next call reopens fresh; closed repo
      falls back to exact pre-fix one-shot behavior,
  (d) source pins: no raw connect inside the three former sites, one module
      connect site (the stub-compat fallback), no check_same_thread escape,
  (e) production opens go through the repository seam, not the fallback,
  (f) the tier-3 pre-trade shape end-to-end,
  (h/i) teardown determinism and stale-handle immunity across repo swaps.
"""

from __future__ import annotations

import ast
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData, TradeProposal
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    StrategyContext,
)
from nexus_scalp.experience.quality import compute_behavior_metrics
from nexus_scalp.experience.retriever import ExperienceRetriever

# =============================================================================
# FIXTURES (same real-substrate shape as tests/unit/test_experience_intelligence)
# =============================================================================


@pytest.fixture
def temp_audit_repo(tmp_path):
    db_file = tmp_path / "test_bug297_registry.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


@pytest.fixture
def components(temp_audit_repo):
    """Ledger + evaluator wired against a temp database (real write API)."""
    ledger = ExperienceLedger(audit_repo=temp_audit_repo)
    evaluator = StrategyEvaluator(audit_repo=temp_audit_repo)
    return ledger, evaluator


def make_record(key: str, strategy_id: str, ts: datetime) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=f"req_{key}",
        idempotency_key=key,
        symbol="XAUUSD",
        decision_timestamp=ts,
        strategy_id=strategy_id,
        context=StrategyContext(strategy_id=strategy_id),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
            values=[0.1] * CANONICAL_FEATURE_DIMENSION,
            feature_hash=ExperienceLedger.compute_feature_hash(
                [0.1] * CANONICAL_FEATURE_DIMENSION, CANONICAL_FEATURE_SCHEMA_ID
            ),
        ),
        action="BUY_MARKET",
        entry_reason="TEST_SETUP",
        model_probability=0.60,
        signal_confidence=0.60,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )


def seed_closed_trades(
    repo: AuditRepository,
    ledger: ExperienceLedger,
    strategy_id: str,
    r_values: list[float],
    prefix: str = "s",
) -> None:
    """Persists N decision+outcome pairs through the REAL ledger write API."""
    start = datetime.now(UTC) - timedelta(hours=6)
    for i, r in enumerate(r_values):
        key = f"{prefix}_{strategy_id}_{i}"
        ts = start + timedelta(minutes=i)
        ledger.record_experience(make_record(key, strategy_id, ts))
        ledger.record_outcome(
            ExperienceOutcome(
                idempotency_key=key,
                execution_id=f"tk_{key}",
                outcome_timestamp=ts + timedelta(seconds=60),
                is_executed=True,
                is_closed=True,
                exit_reason="TAKE_PROFIT_HIT",
                realized_pnl_usd=r * 100.0,
                realized_r_multiple=r,
                approved_volume=0.10,
                behavior=compute_behavior_metrics(
                    mae_points=-2.0 if r > 0 else -11.0,
                    mfe_points=max(1.0, r * 10.0),
                    mae_usd=(-2.0 if r > 0 else -11.0) * 10.0,
                    mfe_usd=max(1.0, r * 10.0) * 10.0,
                    planned_risk_distance=10.0,
                    duration_sec=300.0,
                    initial_sl_distance=10.0,
                    atr_at_entry=5.0,
                ),
            )
        )
    repo._queue.join()


def persist_score(
    evaluator: StrategyEvaluator,
    ledger: ExperienceLedger,
    repo: AuditRepository,
    strategy_id: str,
    limit: int = 200,
):
    """Evaluate + persist through the async queue; returns the score.

    The evaluator is closed afterwards on purpose: evaluate_strategy's own
    registry priming read must not pre-populate the handle cache that the
    reuse tests below instrument.
    """
    score = evaluator.evaluate_strategy(
        strategy_id, ledger.get_experiences_for_strategy(strategy_id, limit=limit)
    )
    repo._queue.join()
    evaluator.close()
    return score


class ConnectCounter:
    """Monkeypatches the module-global sqlite3.connect and counts calls."""

    def __init__(self) -> None:
        self.count = 0
        self.original = sqlite3.connect

    def __enter__(self) -> ConnectCounter:
        outer = self

        def _counting(*args: Any, **kwargs: Any):
            outer.count += 1
            return outer.original(*args, **kwargs)

        sqlite3.connect = _counting  # type: ignore[assignment]
        return outer

    def __exit__(self, *exc: Any) -> None:
        sqlite3.connect = self.original  # type: ignore[assignment]


# =============================================================================
# (a) CORRECT READS THROUGH THE REAL LEDGER WRITE PATH
# =============================================================================


def test_bug297_a_registry_read_returns_persisted_stats(components, temp_audit_repo):
    """(a) A score seeded through the real ledger + persist path reads back
    with correct stats through the reused-connection seam."""
    ledger, evaluator = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_b297_a", [1.2] * 22, prefix="a")
    written = persist_score(evaluator, ledger, temp_audit_repo, "strat_b297_a")

    # First read opens the handle; the second must REUSE the cached one.
    with ConnectCounter() as cc:
        read1 = evaluator.get_registered_strategy_score("strat_b297_a")
        first = cc.count
        read2 = evaluator.get_registered_strategy_score("strat_b297_a")
        second = cc.count
    assert first == 1, "first registry read on a cold cache must open exactly one handle"
    assert second == 1, "BUG-297: second read must reuse the cached connection (grew per call)"

    assert read1 is not None and read2 is not None
    assert read1.sample_count == 22
    assert read1.expectancy_r == pytest.approx(written.expectancy_r)
    assert read1.lifecycle_state == written.lifecycle_state
    assert read1.confidence_score == written.confidence_score
    assert read2.strategy_id == "strat_b297_a"


def test_bug297_a2_list_registered_scores_bound(components, temp_audit_repo):
    """list_registered_scores (no production callers today; public surface)
    reads newest-first through the same seam and reuses the handle."""
    ledger, evaluator = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_list_x", [1.0] * 21, prefix="lx")
    seed_closed_trades(temp_audit_repo, ledger, "strat_list_y", [-1.0] * 13, prefix="ly")
    persist_score(evaluator, ledger, temp_audit_repo, "strat_list_x")
    persist_score(evaluator, ledger, temp_audit_repo, "strat_list_y")

    with ConnectCounter() as cc:
        rows = evaluator.list_registered_scores(limit=50)
        first = cc.count
        rows2 = evaluator.list_registered_scores(limit=50)
        second = cc.count
    assert first == 1 and second == 1
    assert {r.strategy_id for r in rows} == {"strat_list_x", "strat_list_y"}
    assert len(rows2) == 2

    bounded = evaluator.list_registered_scores(limit=1)
    assert len(bounded) == 1


# =============================================================================
# (b) CONNECTION REUSE ACROSS N SCORE LOOKUPS  (RED-before: grows per call)
# =============================================================================


def test_bug297_b_connect_count_constant_over_N_lookups(components, temp_audit_repo):
    """(b) 10 registry lookups on a running repository cost ONE connect total;
    the 5.0 read tier and the 10.0 write tier keep separate cached handles.

    RED-BEFORE (executed at fa34fccd, pre-fix tree): the identical counter
    grew by one per call — 10 connects for 10 lookups, 11 including the
    clear-tier connect below."""
    ledger, evaluator = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_b297_b", [0.8] * 20, prefix="b")
    persist_score(evaluator, ledger, temp_audit_repo, "strat_b297_b")

    with ConnectCounter() as cc:
        for _ in range(10):
            assert evaluator.get_registered_strategy_score("strat_b297_b") is not None
        assert cc.count == 1, f"expected 1 connect for 10 lookups, saw {cc.count}"
        # Same 5.0 tier -> still one connect for the listing too.
        for _ in range(3):
            assert evaluator.list_registered_scores(limit=10)
        assert cc.count == 1
        # Different busy-timeout tier -> its own handle, then reused.
        evaluator._clear_registry()
        assert cc.count == 2
        evaluator._clear_registry()
        assert cc.count == 2, "write tier must reuse its cached handle too"


def test_bug297_b2_rebuild_clear_handle_is_reused(components, temp_audit_repo, monkeypatch):
    """Self-heal's DELETE goes through the seam: two rebuilds enter the
    repository's ONE connect site exactly once for the 10.0 tier.

    Spy on ``AuditRepository._connect_sqlite`` (not raw sqlite3.connect): the
    ledger keeps its own per-query connects OUT OF SCOPE here — BUG-297's
    contract is the evaluator module."""
    ledger, evaluator = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_heal_b297", [1.1] * 25, prefix="h")

    calls: list[float] = []
    original = AuditRepository._connect_sqlite

    def _spy(self, timeout):
        calls.append(float(timeout))
        return original(self, timeout)

    monkeypatch.setattr(AuditRepository, "_connect_sqlite", _spy)
    first = evaluator.rebuild_derived_intelligence(ledger)
    temp_audit_repo._queue.join()
    assert "strat_heal_b297" in first
    assert calls.count(10.0) == 1, f"first rebuild must enter the seam once: {calls}"

    second = evaluator.rebuild_derived_intelligence(ledger)
    temp_audit_repo._queue.join()
    assert "strat_heal_b297" in second
    assert calls.count(10.0) == 1, f"second rebuild must REUSE the write handle: {calls}"


# =============================================================================
# (c) DEGRADATION PARITY — unreadable/closed DB never raises into the caller
# =============================================================================


def test_bug297_c_unreadable_db_degrades_without_raise(components, temp_audit_repo, tmp_path):
    """(c) With a poisoned path, reads return the same degraded shapes as the
    pre-fix except paths (None / []), the failed handle is dropped, and the
    NEXT call reopens fresh and succeeds again. No raise escapes the seam."""
    ledger, evaluator = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_b297_c", [1.5] * 21, prefix="c")
    persist_score(evaluator, ledger, temp_audit_repo, "strat_b297_c")
    assert evaluator.get_registered_strategy_score("strat_b297_c") is not None

    good_path = temp_audit_repo._db_path
    bogus_dir = tmp_path / "not-a-db"
    bogus_dir.mkdir()

    # sqlite refuses to serve a directory path (connect- or statement-time
    # depending on platform); either way the callers' except paths must absorb
    # it. The path swap changes the seam's cache key, so the poisoned attempt
    # can never poison the good handle either.
    temp_audit_repo._db_path = str(bogus_dir)
    assert evaluator.get_registered_strategy_score("strat_b297_c") is None
    assert evaluator.list_registered_scores() == []
    evaluator._clear_registry()  # must not raise either (logged failure only)
    temp_audit_repo._db_path = good_path

    # Recovery: next call reopens fresh and reads correctly.
    recovered = evaluator.get_registered_strategy_score("strat_b297_c")
    assert recovered is not None
    assert recovered.sample_count == 21


def test_bug297_c2_manually_closed_handle_is_replaced(components, temp_audit_repo):
    """A cached handle closed behind the seam's back (dead-fd class) fails
    once into the degraded shape, is evicted, and the next call transparently
    reopens."""
    ledger, evaluator = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_b297_c2", [0.9] * 20, prefix="c2")
    persist_score(evaluator, ledger, temp_audit_repo, "strat_b297_c2")
    assert evaluator.get_registered_strategy_score("strat_b297_c2") is not None

    cached: sqlite3.Connection = evaluator._thread_state()["conns"][5.0]
    cached.close()  # simulate a dead handle

    assert evaluator.get_registered_strategy_score("strat_b297_c2") is None
    # Evicted: the dead object is not cached any more...
    assert evaluator._thread_state()["conns"].get(5.0) is not cached
    # ...and the next call reopens and reads.
    assert evaluator.get_registered_strategy_score("strat_b297_c2") is not None


def test_bug297_c3_closed_repository_delegates_one_shot(temp_audit_repo, tmp_path):
    """After repo.close() (the shutdown/test-teardown shape) reads still work
    pre-fix style: one-shot connect per call, closed on every exit path — and
    cached handles are released first, so nothing keeps the Windows file
    locked (tmp-DB cleanup contract, same reason _setup_storage closes its
    setup connection explicitly)."""
    db_file = tmp_path / "bug297_delegation.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    ledger = ExperienceLedger(audit_repo=repo)
    evaluator = StrategyEvaluator(audit_repo=repo)
    seed_closed_trades(repo, ledger, "strat_deleg", [1.0] * 20, prefix="d")
    persist_score(evaluator, ledger, repo, "strat_deleg")
    assert evaluator.get_registered_strategy_score("strat_deleg") is not None
    assert 5.0 in evaluator._thread_state()["conns"]

    repo.close()
    with ConnectCounter() as cc:
        assert evaluator.get_registered_strategy_score("strat_deleg") is not None
        assert evaluator.get_registered_strategy_score("strat_deleg") is not None
    assert cc.count == 2, "closed repo = pre-fix parity: one-shot connect per call"
    assert evaluator._thread_state()["conns"] == {}, "delegation must release cached handles"
    db_file.unlink()  # PermissionError on Windows under any leaked handle


# =============================================================================
# (d) SOURCE PINS — raw connects stay out of the registry readers
# =============================================================================


def _function_body(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            seg = ast.get_source_segment(source, node)
            assert seg is not None
            return seg
    raise AssertionError(f"function {name} not found")


@pytest.mark.parametrize(
    "func",
    ["get_registered_strategy_score", "list_registered_scores", "_clear_registry"],
)
def test_bug297_d_no_raw_connect_in_readers(func):
    """The three former raw sites must go through the reuse seam; a re-added
    sqlite3.connect (or a direct _connect_sqlite hop that bypasses caching) is
    a class-guard failure — the BUG-285 'comment says bounded, code says
    per-call' lesson, and the exact shape lane-10 §3.1 flagged."""
    import inspect

    import nexus_scalp.experience.evaluator as ev_mod

    body = _function_body(inspect.getsource(ev_mod), func)
    for forbidden in ("sqlite3.connect", "_connect_sqlite"):
        assert forbidden not in body, f"{func} must not call {forbidden} directly"
    assert "_registry_conn" in body, f"{func} must read/write via the reuse seam"


def test_bug297_d2_single_module_connect_site():
    """The evaluator module keeps exactly ONE sqlite3.connect call site — the
    stub-compat fallback inside _open_conn — and the repository seam is the
    preferred opener there."""
    import inspect

    import nexus_scalp.experience.evaluator as ev_mod

    source = inspect.getsource(ev_mod)
    tree = ast.parse(source)
    connect_lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "sqlite3"
        ):
            connect_lines.append(node.lineno)
    assert len(connect_lines) == 1, f"expected exactly 1 raw connect, found lines {connect_lines}"
    body = _function_body(source, "_open_conn")
    assert "sqlite3.connect" in body, "the single connect must live in _open_conn's fallback"
    assert "_connect_sqlite" in body, "_open_conn must prefer the repository's URI-safe seam"


def test_bug297_d3_no_check_same_thread_escape():
    """The design claim 'per-thread handles, no shared-connection locking' is
    pinned: no connect call in the evaluator may pass check_same_thread (AST
    form, so the pin survives the word appearing in explanatory comments)."""
    import inspect

    import nexus_scalp.experience.evaluator as ev_mod

    tree = ast.parse(inspect.getsource(ev_mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                assert kw.arg != "check_same_thread", f"line {node.lineno} shares a raw handle"
    # And the evaluator owns no threading.Lock at all — nothing to lock.
    source = inspect.getsource(ev_mod)
    assert "threading.Lock" not in source


# =============================================================================
# (e) PRODUCTION OPENS GO THROUGH THE REPOSITORY SEAM (not the fallback)
# =============================================================================


def test_bug297_e_production_path_uses_repository_seam(components, temp_audit_repo, monkeypatch):
    """The seam must open through AuditRepository._connect_sqlite — the URI
    contract owner — per busy tier, and then stop entering it while cached."""
    ledger, evaluator = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_b297_e", [1.3] * 20, prefix="e")
    persist_score(evaluator, ledger, temp_audit_repo, "strat_b297_e")

    calls: list[float] = []
    original = AuditRepository._connect_sqlite

    def _spy(self, timeout):
        calls.append(float(timeout))
        return original(self, timeout)

    monkeypatch.setattr(AuditRepository, "_connect_sqlite", _spy)
    assert evaluator.get_registered_strategy_score("strat_b297_e") is not None
    assert calls == [5.0]
    # Reuse: later reads hit neither the seam nor a raw connect.
    assert evaluator.get_registered_strategy_score("strat_b297_e") is not None
    assert evaluator.list_registered_scores()
    assert calls == [5.0], "reused handles must not re-enter the repository connect site"
    # Write tier (10.0) opens its own handle through the same seam, once.
    evaluator._clear_registry()
    assert calls == [5.0, 10.0]
    evaluator._clear_registry()
    assert calls == [5.0, 10.0]


# =============================================================================
# (f) TIER-3 PRE-TRADE SHAPE — budget-exhausted proposals read the registry
# =============================================================================


def make_proposal(request_id: str = "req_b297") -> TradeProposal:
    return TradeProposal(
        request_id=request_id,
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY_MARKET,
        confidence=0.80,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )


def test_bug297_f_tier3_pre_trade_path_reuses_connection(components, temp_audit_repo):
    """End-to-end shape of the R5 regression risk: entry proposals arriving
    faster than the <=4/s inline-refresh budget fall to tier-3 (registry
    read) ON THE CALLING THREAD. Pre-fix each one paid connect+SELECT; now
    the calling thread pays one connect EVER per timeout tier while the
    repository runs."""
    from nexus_scalp.features.scalp_features import BarData, ScalpFeatureEngine

    ledger, evaluator = components
    engine = ExperienceIntelligenceEngine(
        ledger=ledger,
        evaluator=evaluator,
        retriever=ExperienceRetriever(ledger=ledger),
        enabled=True,
        max_inline_refresh_per_sec=4.0,  # budget ON so tier-3 can starve
        score_cache_ttl_sec=1.0,
    )

    now = datetime.now(UTC)
    bars = [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=now - timedelta(minutes=100 - i),
            open=2000.0,
            high=2005.0,
            low=1995.0,
            close=2002.0,
            tick_volume=100.0,
            is_complete=True,
        )
        for i in range(50)
    ]
    fv = ScalpFeatureEngine().compute_from_bars(
        bars, current_tick=TickData(symbol="XAUUSD", timestamp=now, bid=2000.0, ask=2000.5)
    )
    context = engine.build_proposal_context(
        proposal=make_proposal(), feature_vector=fv, regime_state=None
    )
    # Seed + persist the score under the EXACT family id the gate resolves,
    # then blank any handles that priming opened.
    exps = ledger.get_experiences_for_strategy(context.strategy_id, limit=200)
    assert not exps  # nothing under that family id yet
    seed_closed_trades(temp_audit_repo, ledger, context.strategy_id, [1.2] * 25, prefix="t3")
    persist_score(evaluator, ledger, temp_audit_repo, context.strategy_id)
    evaluator.close()

    with ConnectCounter() as cc:
        for _ in range(6):
            # Force tier-3: budget starved + TTL cache dropped, so _get_score
            # must fall through to get_registered_strategy_score.
            engine._last_inline_refresh = time.monotonic()
            engine._score_cache.clear()
            entry = engine._get_score(context=context, decision_timestamp=now)
            assert entry is not None
            assert entry.sample_count == 25
    assert cc.count == 1, (
        f"BUG-297: six tier-3 pre-trade fallbacks must cost 1 connect total, got {cc.count} "
        "(RED-before grew one per proposal)"
    )


def test_bug297_g_cross_thread_handles_are_independent(components, temp_audit_repo):
    """Per-thread design proof: a second thread gets its own handle (no
    shared-connection races), both read correctly, and the main thread's
    cache is untouched by the worker's lifecycle."""
    ledger, evaluator = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_threads", [1.4] * 20, prefix="th")
    persist_score(evaluator, ledger, temp_audit_repo, "strat_threads")
    assert evaluator.get_registered_strategy_score("strat_threads") is not None
    main_handle = evaluator._thread_state()["conns"][5.0]

    results: dict[str, Any] = {}

    def _worker() -> None:
        results["score"] = evaluator.get_registered_strategy_score("strat_threads")
        results["handles"] = dict(evaluator._thread_state()["conns"])

    t = threading.Thread(target=_worker)
    t.start()
    t.join(timeout=10.0)
    assert not t.is_alive()
    assert results["score"] is not None
    assert results["score"].sample_count == 20
    worker_handle = results["handles"][5.0]
    assert worker_handle is not main_handle, "threads must not share one sqlite handle"
    assert evaluator._thread_state()["conns"][5.0] is main_handle


# =============================================================================
# (h/i) TEARDOWN + STALE-HANDLE IMMUNITY
# =============================================================================


def test_bug297_h_close_releases_cached_handles(components, temp_audit_repo):
    ledger, evaluator = components
    seed_closed_trades(temp_audit_repo, ledger, "strat_close", [1.0] * 20, prefix="cl")
    persist_score(evaluator, ledger, temp_audit_repo, "strat_close")
    assert evaluator.get_registered_strategy_score("strat_close") is not None
    assert 5.0 in evaluator._thread_state()["conns"]

    evaluator.close()
    assert evaluator._thread_state()["conns"] == {}
    # After an explicit close the next read transparently reopens (one connect).
    with ConnectCounter() as cc:
        assert evaluator.get_registered_strategy_score("strat_close") is not None
    assert cc.count == 1


def test_bug297_i_repo_swap_never_serves_stale_handle(tmp_path):
    """The (repo id, db path) cache key: pointing the SAME evaluator at a
    second repository must not read the FIRST database (pre-fix per-call
    connects could not go stale — the reuse design must not introduce that)."""
    db1 = tmp_path / "one.db"
    db2 = tmp_path / "two.db"
    repo1 = AuditRepository(db_url=f"sqlite:///{db1}")
    repo2 = AuditRepository(db_url=f"sqlite:///{db2}")
    evaluator = None
    try:
        ledger1 = ExperienceLedger(audit_repo=repo1)
        ledger2 = ExperienceLedger(audit_repo=repo2)
        evaluator = StrategyEvaluator(audit_repo=repo1)
        seed_closed_trades(repo1, ledger1, "only_on_one", [1.0] * 20, prefix="o1")
        persist_score(evaluator, ledger1, repo1, "only_on_one")
        assert evaluator.get_registered_strategy_score("only_on_one") is not None

        evaluator.audit_repo = repo2
        assert evaluator.get_registered_strategy_score("only_on_one") is None, (
            "a stale cached handle served the OLD database across a repo swap"
        )
        seed_closed_trades(repo2, ledger2, "only_on_two", [2.0] * 20, prefix="o2")
        persist_score(evaluator, ledger2, repo2, "only_on_two")
        assert evaluator.get_registered_strategy_score("only_on_two") is not None
    finally:
        if evaluator is not None:
            evaluator.close()
        repo1.close()
        repo2.close()
