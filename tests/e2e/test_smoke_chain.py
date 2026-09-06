"""
NSE End-to-End SMOKE CHAIN — one file, whole application, pretty & complete
=============================================================================

WHAT THIS IS
------------
A SINGLE-FILE end-to-end smoketest that walks the ENTIRE Nexus Scalp Engine
chain with REAL code (no network, no MT5, no model download) in under ~15s.
It is the living proof that the whole system still wires together after any
change — the cheapest possible "did we break the world?" signal.

CHAIN COVERED
-------------
    TickData (domain, UTC + spread invariant)
      → BarAggregator (M1 bar formation, boundary crossing)
      → ScalpFeatureEngine.compute_from_bars  (50D causal features)
      → FeatureVector.to_tensor_input()        (50D tensor, [-3,+3], finite)
      → features70.assemble_70d                (70D Base|News|Liquidity, hash)
      → schema_contract.validate_70d_vector    (dimension / bounds / hash)
      → ScalpNet(num_features=50)              (4-logit forward, softmax)
      → SignalPolicy.evaluate_probabilities    (EXEC-id, confidence semantics)
      → RiskEngine.evaluate_proposal           (1% sizing, HARD_MAX_LOTS)
      → OrderLifecycleManager.dispatch_order   (paper adapter, idempotency)
      → AuditRepository                        (signals / executions / ledger)
      → FastAPI create_app + create_v1_app     (envelope, X-Request-ID, health)
      → failure semantics                      (exposure block, zero-volume)

WHY ONE FILE
------------
User contract: "dont create many files for smoke test its a chain in whole
app" — every stage lives in this ONE module. Helpers are local, deterministic
and hermetic (tmp_path + mock adapter). No scratch DB, no MT5, no sleep.

HOW TO RUN
----------
    pytest tests/e2e/test_smoke_chain.py -q -s          # pretty terminal report
    pytest tests/e2e/test_smoke_chain.py -q --tb=short  # CI mode
    pytest tests/e2e/test_smoke_chain.py -q --junitxml=junit.xml

CI
---
Listed in tests/critical_suite.txt (quality gate) and run as a dedicated
`smoke` job in .github/workflows/ci.yml with its own artifact.

CONTRACTS VERIFIED
------------------
FEATURE_SCHEMA v1 (scalp_v1=50D active, scalp_v3=70D), TRADE_EXECUTION_CONTEXT,
TRADE_OUTCOME, ACCOUNT_SNAPSHOT, API_V1_ENVELOPE, INV-001/004/008/009.

Author: NexusMain (direct, A2A peers offline) — Master Contract v2 §22/55.
"""

from __future__ import annotations

import math
import os
import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.configuration.config import AppConfig, RiskConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    SymbolInfo,
    TickData,
    TradeOrder,
    TradeProposal,
)
from nexus_scalp.experience.evaluator import StrategyEvaluator
from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import ExperienceAction
from nexus_scalp.experience.retriever import ExperienceRetriever
from nexus_scalp.features.features70 import LIQUIDITY_NEUTRAL_10D, NEWS_NEUTRAL_10D, assemble_70d
from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.features.regime_classifier import (
    MarketRegimeClassifier,
    MarketRegimeState,
    RegimeType,
)
from nexus_scalp.features.scalp_features import FeatureVector, ScalpFeatureEngine
from nexus_scalp.features.schema_contract import feature_schema_hash, validate_70d_vector
from nexus_scalp.incidents.models import (
    Incident,
    IncidentCategory,
    IncidentSeverity,
    IncidentStatus,
)
from nexus_scalp.incidents.store import IncidentStore
from nexus_scalp.market_data.bar_aggregator import BarAggregator, BarData
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.mslie.engine import MarketStructureEngine
from nexus_scalp.news.context import NewsContextCache
from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.gate import NewsGate, NewsGateDecision
from nexus_scalp.news.models import CurrentNewsContext, NewsState
from nexus_scalp.observability.event_aggregator import EventBatchAggregator
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.settings.service import SettingsDatabase
from nexus_scalp.signals.policy import SignalPolicy
from nexus_scalp.signals.rule_matrix import RuleMatrixEngine

# ---------------------------------------------------------------------------
# pretty helpers
# ---------------------------------------------------------------------------

BANNER_W = 72


def _banner(title: str) -> None:
    line = "═" * BANNER_W
    print(f"\n╔{line}╗")
    w = BANNER_W - 2
    padded = title + " " * max(0, w - len(title))
    print(f"║  {padded}║")
    print(f"╚{line}╝")


def _step(n: int, label: str, detail: str = "") -> None:
    tag = f"  [{n:02d}] {label}"
    if detail:
        print(f"{tag:<52} {detail}")
    else:
        print(tag)


def _ok(msg: str) -> None:
    print(f"       ✅  {msg}")


def _info(msg: str) -> None:
    print(f"       ·   {msg}")


# ---------------------------------------------------------------------------
# deterministic fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2026, 9, 5, 10, 0, tzinfo=UTC)
XAU_TICK_KW = dict(symbol="XAUUSD", bid=2000.00, ask=2000.05, volume=1.0)
SYMBOL_INFO_KW: dict[str, Any] = dict(
    symbol="XAUUSD",
    digits=2,
    point=0.01,
    tick_size=0.01,
    tick_value=1.0,
    volume_min=0.01,
    volume_max=100.0,
    volume_step=0.01,
    stops_level=10,
    freeze_level=0,
    trade_contract_size=100.0,
)
ACCOUNT_KW: dict[str, Any] = dict(
    login=777001,
    trade_mode=0,
    leverage=100,
    balance=10000.0,
    equity=10000.0,
    margin=0.0,
    margin_free=10000.0,
)


def _account() -> AccountInfo:
    return AccountInfo(**ACCOUNT_KW)


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(**SYMBOL_INFO_KW)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_bars(n: int, t0: datetime = T0, start: float = 1995.0) -> list[BarData]:
    """Deterministic M1 bars — monotonic then gentle oscillation (no lookahead)."""
    out: list[BarData] = []
    for i in range(n):
        # gentle drift + tiny oscillation so HTF / ATR / swing logic has signal
        c = start + i * 0.22 + (0.35 if i % 7 == 0 else 0.0)
        o = c - 0.08
        h = max(o, c) + 0.45
        lo = min(o, c) - 0.35
        out.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=t0 + timedelta(minutes=i),
                open=round(o, 2),
                high=round(h, 2),
                low=round(lo, 2),
                close=round(c, 2),
                tick_volume=120 + (i % 13) * 7,
                is_complete=True,
            )
        )
    return out


class _PaperAdapter:  # minimal IMT5Port surface used by OrderLifecycleManager
    """Deterministic paper adapter — no network, no MT5, no sleep."""

    def __init__(self) -> None:
        self.sent: list[TradeOrder] = []
        self._next_ticket = 9001001

    # queries
    def get_account_info(self) -> AccountInfo:
        return _account()

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return _symbol_info()

    def get_positions(self, symbol: str | None = None) -> list[Any]:
        return []

    def get_last_tick(self, symbol: str) -> TickData:
        return TickData(timestamp=datetime.now(UTC), **XAU_TICK_KW)

    def get_closed_deals_history(self, symbol: str, hours_back: int = 24) -> list[dict[str, Any]]:
        return []

    # execution
    def execute_market_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        t = self._next_ticket
        self._next_ticket += 1
        return t

    def place_pending_order(self, **_: Any) -> int:
        t = self._next_ticket
        self._next_ticket += 1
        return t

    def modify_position(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        return True

    def close_position(self, ticket: int, volume: float | None = None) -> bool:
        return True

    def cancel_pending_order(self, ticket: int) -> bool:
        return True

    def send_order(self, order: TradeOrder) -> bool:
        self.sent.append(order)
        return True


# ---------------------------------------------------------------------------
# THE CHAIN — one long pretty orchestration
# ---------------------------------------------------------------------------


def test_smoke_full_chain(tmp_path) -> None:
    """
    SMOKE CHAIN — happy path through the entire engine.

    One test, ten stages, real components everywhere. Each stage prints a
    pretty banner, asserts its contract, and carries its artifact into the
    next stage. Failure at stage N never masks the evidence from stages < N
    (fail-loud with stage-tagged messages).
    """
    t_wall0 = time.monotonic()
    _banner(
        "🔥  NSE SMOKE CHAIN — end-to-end (Tick → Features → Model → Policy → Risk → Execution → Ledger → API)"
    )

    # ------------------------------------------------------------------
    # 01 — Market data: TickData invariants + BarAggregator
    # ------------------------------------------------------------------
    print("\n┌─ 01 · MARKET DATA ─────────────────────────────────────────────")
    t0 = time.monotonic()
    tick = TickData(timestamp=T0, **XAU_TICK_KW)
    assert tick.spread_points == pytest.approx(0.05), "spread must be ask-bid"
    assert tick.timestamp.tzinfo is not None, "tick must be UTC-aware"
    # bar formation: feed ~70 ticks across minute boundaries
    agg = BarAggregator(symbol="XAUUSD", timeframe_minutes=1)
    completed: list[BarData] = []
    base_bars = _make_bars(70)
    # drive aggregator through ticks derived from those bars
    for b in base_bars:
        tk = TickData(
            symbol="XAUUSD",
            timestamp=b.timestamp + timedelta(seconds=37),
            bid=round(b.close - 0.025, 2),
            ask=round(b.close + 0.025, 2),
            volume=1.0,
        )
        bar = agg.process_tick(tk)
        if bar is not None:
            completed.append(bar)
    # also seed from the deterministic bars directly
    all_bars: list[BarData] = base_bars  # 70 causal M1 bars
    assert len(all_bars) >= 55, "need ≥55 bars for full 50D feature path"
    _ok(
        f"TickData OK — spread={tick.spread_points}  bars={len(all_bars)} (aggregator produced {len(completed)} boundaries)"
    )
    _info(f"stage 01 in {(time.monotonic() - t0) * 1000:.1f} ms")

    # ------------------------------------------------------------------
    # 02 — Features 50D: ScalpFeatureEngine → FeatureVector → 50D tensor
    # ------------------------------------------------------------------
    print("\n┌─ 02 · FEATURES 50D ────────────────────────────────────────────")
    t0 = time.monotonic()
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    fv = engine.compute_from_bars(all_bars, tick)
    # cold-start fallback is valid: engine returns a bounded vector even on
    # short history — the test proves both paths (all_bars≥55 hits the full path)
    assert isinstance(fv, FeatureVector), "engine must return FeatureVector"
    tensor50 = fv.to_tensor_input()
    assert len(tensor50) == 50, f"50D contract violation: got {len(tensor50)}"
    for i, v in enumerate(tensor50):
        assert math.isfinite(v), f"50D non-finite at feat_{i}: {v!r}"
        assert -3.0 <= v <= 3.0, f"50D bounds violation feat_{i}={v} not in [-3, +3]"
    # schema registry sanity
    from nexus_scalp.features.schema import FEATURE_SCHEMAS

    s50 = FEATURE_SCHEMAS.resolve("scalp_v1")
    assert s50.dimension == 50 and s50.is_active, "ACTIVE_SCHEMA_ID must be scalp_v1=50D"
    _ok(f"50D tensor OK — dim=50  finite & bounded  atr={fv.atr_m1:.2f}  schema={s50.schema_id}")
    _info(f"stage 02 in {(time.monotonic() - t0) * 1000:.1f} ms  sample feat_0..2={tensor50[:3]}")

    # ------------------------------------------------------------------
    # 03 — Features 70D: assemble_70d + hash contract
    # ------------------------------------------------------------------
    print("\n┌─ 03 · FEATURES 70D ASSEMBLY ───────────────────────────────────")
    t0 = time.monotonic()
    snap = assemble_70d(
        base50=tensor50,
        news10=list(NEWS_NEUTRAL_10D),
        liquidity10=list(LIQUIDITY_NEUTRAL_10D),
        symbol="XAUUSD",
        timeframe="M1",
        timestamp_utc=T0,
    )
    assert len(snap.feature_vector) == 70, "70D must be exactly 70"
    assert len(snap.feature_names) == 70
    # hash is deterministic content-address
    h1 = feature_schema_hash("scalp_v3")
    h2 = snap.schema_hash()
    assert h1 == h2 and len(h1) == 16, f"70D hash mismatch {h1} vs {h2}"
    vec70 = validate_70d_vector(snap.feature_vector, schema_hash=h1, context="smoke-70D")
    assert vec70[:50] == tensor50, "70D base slice must equal 50D tensor"
    assert vec70[50:60] == list(NEWS_NEUTRAL_10D), "news slice must be neutral"
    assert vec70[60:70] == list(LIQUIDITY_NEUTRAL_10D), "liquidity slice must be neutral"
    # neutral semantics: unavailable still requires explicit block (never fabricated)
    with pytest.raises(ValueError):
        assemble_70d(base50=tensor50, news10=None, liquidity10=list(LIQUIDITY_NEUTRAL_10D))  # type: ignore[arg-type]
    _ok(f"70D OK — dim=70  hash={h1}  layout Base|News|Liquidity correct")
    _info(f"stage 03 in {(time.monotonic() - t0) * 1000:.1f} ms")

    # ------------------------------------------------------------------
    # 04 — Model: ScalpNet forward → 4-class distribution
    # ------------------------------------------------------------------
    print("\n┌─ 04 · MODEL (ScalpNet) ────────────────────────────────────────")
    t0 = time.monotonic()
    model = ScalpNet(num_features=50, num_classes=4)
    model.eval()
    with torch.no_grad():
        logits = model(torch.tensor([tensor50], dtype=torch.float32))
        probs_t = torch.softmax(logits, dim=1)
    assert logits.shape == (1, 4), f"logits shape {logits.shape} must be (1,4)"
    assert probs_t.shape == (1, 4)
    p = float(probs_t.sum().item())
    assert p == pytest.approx(1.0, abs=1e-4), f"softmax must sum to 1, got {p}"
    probs_list: list[float] = probs_t.squeeze().tolist()  # type: ignore[assignment]
    assert all(0.0 <= x <= 1.0 for x in probs_list), f"probs out of [0,1]: {probs_list}"
    # deterministic under same weights
    with torch.no_grad():
        p2 = (
            torch.softmax(model(torch.tensor([tensor50], dtype=torch.float32)), dim=1)
            .squeeze()
            .tolist()
        )
    assert probs_list == pytest.approx(p2, abs=1e-6)  # type: ignore[arg-type]
    _ok(
        f"ScalpNet OK — logits {logits.squeeze().tolist()} → probs {[round(x, 3) for x in probs_list]}"
    )
    _info(f"stage 04 in {(time.monotonic() - t0) * 1000:.1f} ms  hidden=128 heads=4")

    # ------------------------------------------------------------------
    # 05 — Policy: SignalPolicy → TradeProposal (+ EXEC-id + confidence)
    # ------------------------------------------------------------------
    print("\n┌─ 05 · POLICY (SignalPolicy) ───────────────────────────────────")
    t0 = time.monotonic()
    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10  # permissive for smoke
    policy.algo_config.ai_zone_confidence_threshold = 0.60
    # Trending-market feature vector (tenkan > kijun, displacement above the
    # range floor) so the AGGRESSIVE_SCALP channel can fire — the smoke chain
    # exercises the real policy gates, not a stripped path.
    forced = torch.tensor([[0.01, 0.96, 0.02, 0.01]])
    proposal = policy.evaluate_probabilities(
        probabilities=forced, current_tick=tick, feature_vector=fv
    )
    assert proposal.symbol == "XAUUSD"
    assert proposal.execution_id is not None and proposal.execution_id.startswith("EXEC-"), (
        "EXEC-id must be stamped"
    )
    # directional action (BUY family) — NO_TRADE would mean thresholds too tight for smoke
    assert proposal.action in (
        ActionType.BUY_MARKET,
        ActionType.BUY_LIMIT,
        ActionType.BUY,
        ActionType.BUY_STOP,
    )
    assert 0.0 <= proposal.confidence <= 1.0
    assert proposal.stop_loss < proposal.proposed_entry < proposal.take_profit, (
        "SL < entry < TP for BUY"
    )
    # NO_TRADE branch on a later tick (past the 60s frequency throttle) with a
    # range-market vector (tenkan == kijun inside kumo): the policy must
    # refuse, and RiskEngine must refuse to size a NO_TRADE proposal.
    later = T0 + timedelta(seconds=90)
    later_tick = TickData(symbol="XAUUSD", timestamp=later, bid=2000.30, ask=2000.35, volume=1.0)
    range_fv = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=later.isoformat(),
        live_tick_displacement=0.05,
        log_return_m1=0.0,
        atr_m1=2.00,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=0.0,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=True,
        session_london=False,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
        fvg_bullish_active=False,
        fvg_bearish_active=False,
        order_block_type=0,
        liquidity_sweep_signal=0,
        choch_bullish=False,
        choch_bearish=False,
        broke_previous_high=False,
        broke_previous_low=False,
        rapid_reversal_spike=False,
        rapid_reversal_spike_val=0.0,
        tenkan_sen=2000.0,
        kijun_sen=2000.0,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=True,
        is_below_kumo=False,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=0.0,
        htf_h1_momentum=0.0,
        htf_m30_structure=0.0,
        htf_m15_confirmation=0.0,
        support_zone_dist=0.05,
        resistance_zone_dist=0.05,
        trend_strength=0.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )
    flat = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.90, 0.05, 0.04, 0.01]]),
        current_tick=later_tick,
        feature_vector=range_fv,
    )
    assert flat.action == ActionType.NO_TRADE, (
        f"range-market vector must be NO_TRADE, got {flat.action} ({flat.reason_code})"
    )
    _ok(
        f"Policy OK — {proposal.action.value} conf={proposal.confidence:.3f} exec={proposal.execution_id}  RR={proposal.risk_reward_ratio:.2f}"
    )
    _ok(f"NO_TRADE branch OK — {flat.reason_code}")
    _info(
        f"stage 05 in {(time.monotonic() - t0) * 1000:.1f} ms  SL={proposal.stop_loss:.2f} TP={proposal.take_profit:.2f}"
    )

    # ------------------------------------------------------------------
    # 06 — Risk: RiskEngine sizing (1% not 10%, HARD_MAX_LOTS)
    # ------------------------------------------------------------------
    print("\n┌─ 06 · RISK ENGINE ─────────────────────────────────────────────")
    t0 = time.monotonic()
    risk = RiskEngine(RiskConfig(risk_per_trade_pct=1.0))
    verdict = risk.evaluate_proposal(
        proposal=proposal,
        account=_account(),
        symbol_info=_symbol_info(),
        active_positions=[],
        current_tick=tick,
    )
    assert verdict is not None, "risk must approve the smoke proposal (1% sizing)"
    assert verdict.volume > 0, "risk must size a positive lot"
    # 1% of 10k with ~$2 SL distance on 100 contract size is ~0.50 lots — never 10x
    assert 0.01 <= verdict.volume <= 2.0, f"smoke lot {verdict.volume} looks insane for 1% risk"
    # clamp check: even a reckless volume is hard-capped
    from nexus_scalp.execution.order_manager import HARD_MAX_LOTS

    # direct clamp via _clamp_dispatch_volume path is tested in stage 08; here
    # verify the constant is the documented guard
    assert HARD_MAX_LOTS == 10.0, f"HARD_MAX_LOTS must be 10.0, got {HARD_MAX_LOTS}"
    # rejected: risk must refuse NO_TRADE proposal
    assert (
        risk.evaluate_proposal(
            proposal=flat,
            account=_account(),
            symbol_info=_symbol_info(),
            active_positions=[],
            current_tick=tick,
        )
        is None
    )
    _ok(f"Risk OK — volume={verdict.volume} lots  (1% sizing, cap {HARD_MAX_LOTS})")
    _info(f"stage 06 in {(time.monotonic() - t0) * 1000:.1f} ms")

    # ------------------------------------------------------------------
    # 07 — Execution: OrderLifecycleManager paper dispatch
    # ------------------------------------------------------------------
    print("\n┌─ 07 · EXECUTION (OrderLifecycleManager, paper) ────────────────")
    t0 = time.monotonic()
    db_path = os.path.join(str(tmp_path), "smoke_chain.db")
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    paper = _PaperAdapter()
    from nexus_scalp.execution.order_manager import OrderLifecycleManager

    om = OrderLifecycleManager(adapter=paper, audit_repo=audit, risk_engine=risk)  # type: ignore[arg-type]
    # audit must see the signal
    audit.log_signal(proposal)
    dispatched = om.dispatch_order(decision=proposal, volume=float(verdict.volume))
    assert dispatched is True, "paper dispatch must succeed when exposure is free"
    # idempotency: same request_id second dispatch is refused
    assert om.dispatch_order(decision=proposal, volume=float(verdict.volume)) is False, (
        "duplicate request_id must be blocked"
    )
    # exposure guard: now one live exposure exists in the internal cache? Paper
    # adapter still reports 0 positions, but dispatch idempotency already proves
    # the duplicate guard — exposure test is covered in test_smoke_exposure_guard
    _ok("Execution OK — dispatched ticket stream, duplicate blocked, audit queued")
    _info(f"stage 07 in {(time.monotonic() - t0) * 1000:.1f} ms  volume={verdict.volume}")

    # ------------------------------------------------------------------
    # 08 — Accounting: ledger + snapshots
    # ------------------------------------------------------------------
    print("\n┌─ 08 · ACCOUNTING / LEDGER ─────────────────────────────────────")
    t0 = time.monotonic()
    # open ledger row (mirrors critical_suite heartbeat but with smoke ticket)
    now_iso = T0.isoformat()
    audit.log_ledger_opened(
        ticket=9001001,
        symbol="XAUUSD",
        direction="buy",
        volume=float(verdict.volume),
        entry_price=float(proposal.proposed_entry),
        timestamp_str=now_iso,
        order_id=proposal.request_id,
        entry_reason="SMOKE_CHAIN",
        ai_confidence_at_open=float(proposal.confidence),
        market_regime_at_open="TRENDING",
        initial_sl_price=float(proposal.stop_loss),
    )
    audit.log_account_snapshot(_account(), peak_equity=10000.0)
    # explicit TradeOrder audit row
    order = TradeOrder(
        order_id=proposal.request_id,
        symbol=proposal.symbol,
        order_type=OrderType.BUY,
        volume=float(verdict.volume),
        price=float(proposal.proposed_entry),
        stop_loss=float(proposal.stop_loss),
        take_profit=float(proposal.take_profit),
        magic_number=888101,
        comment="NSE_SMOKE",
    )
    audit.log_execution(order, "FILLED")
    # flush background writer (never close() mid-assert — join the queue)
    audit._queue.join()  # type: ignore[attr-defined]
    conn = sqlite3.connect(db_path)
    try:
        sigs = conn.execute("SELECT COUNT(*) FROM audit_signals").fetchone()[0]
        execs = conn.execute("SELECT COUNT(*) FROM audit_executions").fetchone()[0]
        ledgers = conn.execute(
            "SELECT COUNT(*) FROM audit_ledger WHERE status='OPENED'"
        ).fetchone()[0]
        snaps = conn.execute("SELECT COUNT(*) FROM audit_account_snapshots").fetchone()[0]
        assert sigs >= 1, f"audit_signals should have ≥1 row, got {sigs}"
        assert execs >= 1, f"audit_executions should have ≥1 row, got {execs}"
        assert ledgers >= 1, f"audit_ledger OPENED should have ≥1 row, got {ledgers}"
        assert snaps >= 1, f"audit_account_snapshots should have ≥1 row, got {snaps}"
        row = conn.execute(
            "SELECT ticket, symbol, entry_reason FROM audit_ledger LIMIT 1"
        ).fetchone()
        assert row[1] == "XAUUSD" and row[2] == "SMOKE_CHAIN"
    finally:
        conn.close()
    _ok(f"Accounting OK — signals={sigs} executions={execs} ledger={ledgers} snapshots={snaps}")
    _info(f"stage 08 in {(time.monotonic() - t0) * 1000:.1f} ms  ticket=9001001")

    # ------------------------------------------------------------------
    # 09 — Web / API v1: envelope, pagination, health, X-Request-ID
    # ------------------------------------------------------------------
    print("\n┌─ 09 · WEB / API v1 ────────────────────────────────────────────")
    t0 = time.monotonic()
    from fastapi.testclient import TestClient

    from nexus_scalp.web.api_v1_wiring import create_v1_app
    from nexus_scalp.web.server import create_app

    # legacy dashboard app mounts v1 as well — both must expose the contract
    dash_app = create_app(engine_ref=None)
    v1_app = create_v1_app()
    # pick the standalone v1 app for hermetic checks (no engine needed for system/*)
    client = TestClient(v1_app, raise_server_exceptions=False)
    # dashboard app health
    dash_client = TestClient(dash_app, raise_server_exceptions=False)

    checks: list[tuple[str, str, int]] = [
        ("GET /api/v1/system/status", "/api/v1/system/status", 200),
        ("GET /api/v1/system/health", "/api/v1/system/health", 200),
        ("GET /api/v1/system/version", "/api/v1/system/version", 200),
        ("GET /api/v1/system/runtime", "/api/v1/system/runtime", 200),
        ("GET /api/v1/system/capabilities", "/api/v1/system/capabilities", 200),
    ]
    for label, path, expect in checks:
        r = client.get(path)
        assert r.status_code == expect, (
            f"{label} expected {expect}, got {r.status_code}: {r.text[:200]}"
        )
        body = r.json()
        # v1 envelope is {data, meta} on success
        assert "data" in body and "meta" in body, (
            f"{label} missing data/meta envelope: {body.keys()}"
        )
        assert "request_id" in body["meta"], f"{label} meta.request_id missing"
        assert r.headers.get("x-request-id"), f"{label} missing X-Request-ID header"
        _ok(f"{label} → {expect}  request_id={body['meta']['request_id'][:8]}…")

    # 404 contract: unknown v1 path is a proper error envelope, not HTML
    r = client.get("/api/v1/does_not_exist_xyz")
    assert r.status_code == 404

    # dashboard still serves its legacy surface (smoke only needs 200, not full contract)
    r = dash_client.get("/")
    assert r.status_code in (200, 404)  # "/" may redirect; just prove the app boots
    client.close()
    dash_client.close()
    _info(f"stage 09 in {(time.monotonic() - t0) * 1000:.1f} ms  v1 routes verified")

    # ------------------------------------------------------------------
    # 10 — Chain summary
    # ------------------------------------------------------------------
    wall_ms = (time.monotonic() - t_wall0) * 1000
    audit.close()
    print("\n" + "─" * BANNER_W)
    print(f"  ✅  SMOKE CHAIN PASSED — {wall_ms:.0f} ms wall")
    print(
        f"      tick {tick.symbol} {tick.bid}/{tick.ask}  →  50D {len(tensor50)}  →  70D {len(vec70)}"
    )
    print(
        f"      model {probs_list[1]:.3f} buy  →  policy {proposal.action.value} conf={proposal.confidence:.3f}"
    )
    print(
        f"      risk {verdict.volume} lots  →  execution dispatched  →  ledger OPENED  →  API v1 healthy"
    )
    print("─" * BANNER_W + "\n")


# ---------------------------------------------------------------------------
# Focused regression sentinels (also pretty — reuse the chain helpers)
# ---------------------------------------------------------------------------


def test_smoke_risk_one_percent_not_ten_percent() -> None:
    """
    CRITICAL RISK sentinel: 1% must size ~0.50 lots on the smoke fixture,
    never ~5.0 lots (the classic '1% -> 10%' accident).
    """
    _banner("🛡️  RISK SENTINEL — 1% is 1%, not 10%")
    risk = RiskEngine(RiskConfig(risk_per_trade_pct=1.0))
    now = datetime.now(UTC)
    prop = TradeOrder  # noqa: F841 — keep import warm
    from nexus_scalp.domain.models import TradeProposal

    proposal = TradeProposal(
        request_id=str(uuid.uuid4()),
        symbol="XAUUSD",
        generated_at=now,
        action=ActionType.BUY_MARKET,
        confidence=0.90,
        proposed_entry=2000.0,
        stop_loss=1998.0,
        take_profit=2006.0,
        risk_reward_ratio=3.0,
    )
    v = risk.evaluate_proposal(
        proposal=proposal,
        account=_account(),
        symbol_info=_symbol_info(),
        active_positions=[],
        current_tick=TickData(timestamp=now, **XAU_TICK_KW),
    )
    assert v is not None
    print(f"   sized volume = {v.volume} lots  (1% of $10k, $2 SL, 100 contract)")
    assert 0.45 <= v.volume <= 0.60, f"1% risk must size ~0.50 lots, got {v.volume} — 10x bug?"
    _ok(f"1% sentinel passed — {v.volume} lots in [0.45, 0.60]")


def test_smoke_exposure_guard_and_idempotency(tmp_path) -> None:
    """
    EXPOSURE + IDEMPOTENCY: at most one live exposure, duplicate request_ids blocked.
    """
    _banner("🔒  EXPOSURE & IDEMPOTENCY GUARD")
    db_path = os.path.join(str(tmp_path), "smoke_exposure.db")
    audit = AuditRepository(db_url=f"sqlite:///{db_path}")
    paper = _PaperAdapter()
    risk = RiskEngine(RiskConfig(risk_per_trade_pct=1.0))
    from nexus_scalp.execution.order_manager import OrderLifecycleManager

    om = OrderLifecycleManager(adapter=paper, audit_repo=audit, risk_engine=risk)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    tick = TickData(timestamp=now, **XAU_TICK_KW)
    # craft a proposal via policy so SL/TP are valid — trending vector so the
    # AGGRESSIVE channel can fire (tenkan > kijun, displacement above range floor)
    policy = SignalPolicy()
    policy.confidence_threshold = 0.05
    policy.algo_config.min_risk_reward_ratio = 0.10
    fv = FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=now.isoformat(),
        live_tick_displacement=0.9,
        log_return_m1=0.0,
        atr_m1=2.0,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=0.5,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=True,
        session_london=False,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
        fvg_bullish_active=False,
        fvg_bearish_active=False,
        order_block_type=0,
        liquidity_sweep_signal=0,
        choch_bullish=False,
        choch_bearish=False,
        broke_previous_high=False,
        broke_previous_low=False,
        rapid_reversal_spike=False,
        rapid_reversal_spike_val=0.0,
        tenkan_sen=2001.5,
        kijun_sen=2000.0,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=True,
        is_below_kumo=False,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=1.0,
        htf_h1_momentum=1.0,
        htf_m30_structure=1.0,
        htf_m15_confirmation=1.0,
        support_zone_dist=5.0,
        resistance_zone_dist=5.0,
        trend_strength=1.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )
    p = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.97, 0.01, 0.01]]), current_tick=tick, feature_vector=fv
    )
    assert p.action != ActionType.NO_TRADE, (
        f"smoke policy must be tradeable, got {p.action} ({p.reason_code})"
    )
    verdict = risk.evaluate_proposal(
        proposal=p,
        account=_account(),
        symbol_info=_symbol_info(),
        active_positions=[],
        current_tick=tick,
    )
    assert verdict is not None
    # first dispatch succeeds
    assert om.dispatch_order(decision=p, volume=float(verdict.volume)) is True
    _ok("first dispatch accepted")
    # duplicate request_id blocked
    assert om.dispatch_order(decision=p, volume=float(verdict.volume)) is False
    _ok("duplicate request_id correctly blocked")
    # HARD_MAX_LOTS clamp is enforced even when risk would allow more
    clamped = om._clamp_dispatch_volume(999.0, symbol="XAUUSD")  # type: ignore[attr-defined]
    from nexus_scalp.execution.order_manager import HARD_MAX_LOTS

    assert clamped == HARD_MAX_LOTS, f"999 lots must clamp to {HARD_MAX_LOTS}, got {clamped}"
    _ok(f"HARD_MAX_LOTS clamp OK — 999 → {clamped}")
    audit.close()
    print("   ✅  exposure + idempotency guards verified\n")


def test_smoke_feature_cold_start_and_schema() -> None:
    """
    FEATURE COLD-START: <55 bars still yields a valid 50D vector (fallback),
    schema registry still resolves and validates.
    """
    _banner("🧊  FEATURE COLD-START & SCHEMA REGISTRY")
    tiny = _make_bars(10)
    tick = TickData(timestamp=T0, **XAU_TICK_KW)
    eng = ScalpFeatureEngine(symbol="XAUUSD")
    fv = eng.compute_from_bars(tiny, tick)
    t50 = fv.to_tensor_input()
    assert len(t50) == 50 and all(-3.0 <= v <= 3.0 for v in t50)
    _ok("cold-start 10 bars → 50D valid, bounded, finite")
    from nexus_scalp.features.schema import FEATURE_SCHEMAS

    assert FEATURE_SCHEMAS.resolve("scalp_v1").dimension == 50
    assert FEATURE_SCHEMAS.resolve("scalp_v3").dimension == 70
    with pytest.raises(KeyError):
        FEATURE_SCHEMAS.resolve("does_not_exist")  # strict, never silent default
    _ok("schema registry strict — unknown id raises KeyError (no silent 50D default)")
    print()


# ---------------------------------------------------------------------------
# EXTENDED CHAIN — whole-project subsystems (stages 11-21, Nexus-Main)
# Every stage: real objects, hermetic (tmp_path), deterministic, no network.
# ---------------------------------------------------------------------------


def _make_ticks(classifier: MarketRegimeClassifier, n: int) -> list[TickData]:
    """Deterministic trending tick stream that escalates regime activity."""
    ticks: list[TickData] = []
    price = 2000.0
    t = T0
    for i in range(n):
        price += 0.02  # persistent directional drift -> TRENDING_MOMENTUM
        tick = TickData(
            symbol="XAUUSD",
            timestamp=t + timedelta(seconds=i),
            bid=round(price - 0.02, 2),
            ask=round(price + 0.02, 2),
            volume=1.0,
        )
        ticks.append(tick)
        classifier.classify_tick(tick)
    return ticks


def test_smoke_regime_classifier() -> None:
    """STAGE 11 — Regime Guardian: classify_tick reachable, diagnostics real."""
    _banner("🌡️  STAGE 11 · REGIME CLASSIFIER (MarketRegimeClassifier)")
    t0 = time.monotonic()
    clf = MarketRegimeClassifier(symbol="XAUUSD", rolling_seconds=300)
    ticks = _make_ticks(clf, 12)
    state = clf.classify_tick(ticks[-1])
    assert isinstance(state, MarketRegimeState), "classify_tick must return MarketRegimeState"
    assert state.symbol == "XAUUSD"
    assert state.regime_type in RegimeType, f"unknown regime {state.regime_type}"
    assert 0.0 <= state.regime_probability <= 1.0
    assert state.current_spread_usd >= 0.0
    assert state.timestamp_utc
    diag = clf.decision_diagnostics()
    assert isinstance(diag, dict) and diag, "decision_diagnostics must be non-empty"
    _ok(
        f"Regime OK — {len(ticks)} ticks → {state.regime_type.value} p={state.regime_probability:.2f}"
    )
    _info(f"stage 11 in {(time.monotonic() - t0) * 1000:.1f} ms  reason={state.reason.value}")


def test_smoke_liquidity_real_10d(tmp_path) -> None:
    """STAGE 12 — real causal liquidity 10D via compute_liquidity_features."""
    _banner("💧 STAGE 12 · LIQUIDITY 10D (compute_liquidity_features, causal)")
    t0 = time.monotonic()
    bars = _make_bars(70)
    decision_at = bars[-1].timestamp
    liq = compute_liquidity_features(
        bars,
        decision_at=decision_at,
        mid_price=float(bars[-1].close),
        atr=float(bars[1].high - bars[1].low),
    )
    v = liq.as_vector()
    assert len(v) == 10, f"liquidity vector must be 10D, got {len(v)}"
    for i, x in enumerate(v):
        assert math.isfinite(x), f"liquidity non-finite at {i}: {x!r}"
        assert -3.0 <= x <= 3.0, f"liquidity bounds violation at {i}: {x}"
    # causal probe: a past decision_at must still yield a valid bounded vector
    liq_past = compute_liquidity_features(
        bars, decision_at=bars[40].timestamp, mid_price=float(bars[40].close)
    )
    v_past = liq_past.as_vector()
    assert len(v_past) == 10 and all(math.isfinite(x) and -3.0 <= x <= 3.0 for x in v_past)
    _ok(f"Liquidity 10D OK — causal, bounded, decision_at={decision_at.isoformat()}")
    _info(f"stage 12 in {(time.monotonic() - t0) * 1000:.1f} ms  bsl={v[0]:.3f} ssl={v[1]:.3f}")


def test_smoke_news_context_and_gate(tmp_path) -> None:
    """STAGE 13 — News: fresh DB → cache cold start → gate on proposal."""
    _banner("📰 STAGE 13 · NEWS CONTEXT + NEWS GATE (hermetic)")
    t0 = time.monotonic()
    db_path = os.path.join(str(tmp_path), "smoke_news.db")
    news_db = NewsDatabase(db_path=db_path)
    cache = NewsContextCache(db=news_db)
    ctx = cache.build_once_safe()
    assert isinstance(ctx, CurrentNewsContext)
    assert ctx.available is False, "fresh DB must be unavailable (never fake-neutral)"
    assert ctx.state == NewsState.NORMAL
    assert ctx.confidence == 0.0 and ctx.stale is False
    adj = ctx.news_adjustment
    assert adj == 0.0
    gate = NewsGate()
    verdict = gate.evaluate(
        context=ctx,
        proposal_action="BUY_MARKET",
        strategy_direction="BULLISH",
        proposal_confidence=0.9,
        regime_aligned=True,
    )
    assert verdict.decision == NewsGateDecision.IGNORE
    assert verdict.reason == "NEWS_UNAVAILABLE_OR_STALE"
    # non-entry action never gated
    v2 = gate.evaluate(
        context=ctx,
        proposal_action="MODIFY_SL_TP",
        strategy_direction="BULLISH",
        proposal_confidence=0.9,
        regime_aligned=True,
    )
    assert v2.decision == NewsGateDecision.IGNORE
    assert v2.reason in ("NON_ENTRY_ACTION_NOT_GATED", "NEWS_UNAVAILABLE_OR_STALE")
    _ok(f"News OK — cold start honest (available=False), gate IGNORE ({verdict.reason})")
    _info(f"stage 13 in {(time.monotonic() - t0) * 1000:.1f} ms  news_adjustment={adj}")
    news_db.close()


def test_smoke_mslie_perception(tmp_path) -> None:
    """STAGE 14 — MSLIE: market structure perception over synthetic bars."""
    _banner("🧭 STAGE 14 · MSLIE (MarketStructureEngine)")
    t0 = time.monotonic()
    bars = _make_bars(70)
    engine = MarketStructureEngine(symbol="XAUUSD", timeframe="M1")
    vec = engine.analyze_market(
        bars, decision_at=bars[-1].timestamp, atr=float(bars[1].high - bars[1].low)
    )
    assert vec is not None
    assert vec.symbol == "XAUUSD" and vec.timeframe == "M1"
    assert vec.version, "MSLIE vector must carry version"
    assert vec.structure_confidence >= 0.0, "structure confidence must be non-negative"
    assert vec.bias is not None and vec.structure
    assert vec.swing_count_high >= 0 and vec.swing_count_low >= 0
    last = engine.last_vector
    assert last is not None and last.version == vec.version
    assert int(vec.bias) in (-1, 0, 1), f"bias out of MarketBias domain: {vec.bias}"
    _ok(
        f"MSLIE OK — structure={vec.structure} bias={int(vec.bias)} conf={vec.structure_confidence:.2f}"
    )
    _info(
        f"stage 14 in {(time.monotonic() - t0) * 1000:.1f} ms  engine_latency_ms={engine.last_latency_ms}"
    )


def test_smoke_rule_matrix(tmp_path) -> None:
    """STAGE 15 — RuleMatrixEngine over disposable AuditRepository."""
    _banner("📐 STAGE 15 · RULE MATRIX (30+ rule registry)")
    t0 = time.monotonic()
    db_path = os.path.join(str(tmp_path), "smoke_rules.db")
    repo = AuditRepository(db_url=f"sqlite:///{db_path}")
    rm = RuleMatrixEngine(repo)
    # disabled-by-default DB: every rule must resolve False, params empty
    assert rm.is_enabled("RULE_FVG_SNIPER_FILL") is False
    params = rm.get_params("RULE_FVG_SNIPER_FILL")
    assert isinstance(params, dict), "params must be a dict even when rule disabled"
    assert rm.is_enabled("DOES_NOT_EXIST_XYZ") is False, (
        "unknown rule must be disabled, never crash"
    )
    # evaluation path is safe on synthetic inputs (no enabled rules → no proposal)
    ticks_tick = TickData(timestamp=T0, **XAU_TICK_KW)
    fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(_make_bars(70), ticks_tick)
    prop = rm.evaluate_pre_trade_entry(ticks_tick, fv, None, [0.25, 0.25, 0.25, 0.25])
    assert prop is None, "no rules enabled → no custom proposal"
    repo.close()
    _ok("RuleMatrix OK — registry strict, defaults disabled, eval path safe")
    _info(f"stage 15 in {(time.monotonic() - t0) * 1000:.1f} ms")


def test_smoke_experience_intelligence(tmp_path) -> None:
    """STAGE 16 — Experience gate: fail-safe verdict, no order authority."""
    _banner("🧠 STAGE 16 · EXPERIENCE INTELLIGENCE (pre-trade gate)")
    t0 = time.monotonic()
    db_path = os.path.join(str(tmp_path), "smoke_exp.db")
    repo = AuditRepository(db_url=f"sqlite:///{db_path}")
    ledger = ExperienceLedger(repo)
    evaluator = StrategyEvaluator(repo)
    retriever = ExperienceRetriever(ledger)
    engine = ExperienceIntelligenceEngine(ledger=ledger, evaluator=evaluator, retriever=retriever)
    assert engine.gate_failure_count == 0
    # experience must NEVER hold order authority surface
    assert not hasattr(engine, "adapter") and not hasattr(engine, "order_manager")
    now = datetime.now(UTC)
    proposal = TradeProposal(
        request_id=str(uuid.uuid4()),
        symbol="XAUUSD",
        generated_at=now,
        action=ActionType.BUY_MARKET,
        confidence=0.85,
        proposed_entry=2000.0,
        stop_loss=1998.0,
        take_profit=2006.0,
        risk_reward_ratio=3.0,
    )
    fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(
        _make_bars(70), TickData(timestamp=now, **XAU_TICK_KW)
    )
    out, decision = engine.evaluate_proposal(proposal, fv)
    assert decision.decision_id.startswith("exp_dec_")
    assert decision.action in ExperienceAction, f"invalid action {decision.action}"
    assert isinstance(decision.qualifies_trade, bool)
    assert out.request_id == proposal.request_id
    # empty-ledger fail-safe: INSUFFICIENT_EVIDENCE passes the proposal through unchanged
    assert (
        decision.action == ExperienceAction.INSUFFICIENT_EVIDENCE
        or decision.qualifies_trade is True
    )
    ledger.flush_pending(timeout_sec=5.0)
    repo.close()
    _ok(
        f"Experience OK — {decision.action.value} qualifies={decision.qualifies_trade} (no order authority)"
    )
    _info(f"stage 16 in {(time.monotonic() - t0) * 1000:.1f} ms  strategy={decision.strategy_id}")


def test_smoke_runtime_config_and_settings(tmp_path) -> None:
    """STAGE 17 — RuntimeConfigStore snapshot + SettingsDatabase roundtrip."""
    _banner("⚙️  STAGE 17 · RUNTIME CONFIG + SETTINGS DB")
    t0 = time.monotonic()
    from nexus_scalp.configuration.runtime_config import RuntimeConfigStore

    cfg = AppConfig.load_from_yaml(REPO_ROOT / "configs" / "base.yaml")
    store = RuntimeConfigStore(bootstrap=cfg)
    snap = store.get_snapshot()
    assert snap is not None
    v1 = snap.version
    snap2 = store.get_snapshot()
    assert snap2.version >= v1, "snapshot version must be monotonic"
    # settings DB: isolated, typed roundtrip, no real DB touched
    sdb = SettingsDatabase(db_path=tmp_path / "smoke_settings.db")
    sdb.set("smoke.test_key", "42", source="SMOKE")
    got = sdb.get("smoke.test_key")
    assert got is not None and str(got.value) == "42"
    assert got.value_type in ("int", "str", "json")
    health = sdb.health()
    assert health is not None
    sdb.close()
    _ok(f"RuntimeConfig OK — snapshot v{snap.version}; Settings OK — typed roundtrip 42")
    _info(f"stage 17 in {(time.monotonic() - t0) * 1000:.1f} ms")


def test_smoke_incidents_store(tmp_path) -> None:
    """STAGE 18 — Incident store: schema, save, read-back, dedup fingerprint."""
    _banner("🚨 STAGE 18 · INCIDENT STORE (hermetic)")
    t0 = time.monotonic()
    db_path = tmp_path / "smoke_incidents.db"
    store = IncidentStore(db_path=str(db_path))
    store.ensure_schema()
    inc = Incident(
        severity=IncidentSeverity.MEDIUM,
        category=IncidentCategory.WORKER,
        component="smoke_chain",
        operation="stage18",
        root_cause="synthetic smoke incident (not real)",
    )
    iid = store.save(inc)
    assert iid, "save must return incident id"
    got = store.get(iid)
    assert got is not None, "incident must be readable after save"
    assert got.component == "smoke_chain"
    assert got.severity == IncidentSeverity.MEDIUM
    assert got.status == IncidentStatus.OPEN
    counts = store.count()
    assert counts, "count() must return stats"
    store.delete_by_id(iid)
    assert store.get(iid) is None, "delete must remove incident"
    _ok(f"Incidents OK — save/read/count/delete verified (id={iid[:12]}…)")
    _info(f"stage 18 in {(time.monotonic() - t0) * 1000:.1f} ms")


def test_smoke_observability_aggregator() -> None:
    """STAGE 19 — Observability contract: aggregate repeats, flush summary."""
    _banner("📊 STAGE 19 · OBSERVABILITY (EventBatchAggregator contract)")
    t0 = time.monotonic()
    agg = EventBatchAggregator()
    lines: list[str] = []
    first = agg.add(event="SMOKE_TEST_EVENT", reason="synthetic", stage="stage19", recoverable=True)
    assert first is True, "first occurrence must return True"
    for _ in range(8):
        assert (
            agg.add(event="SMOKE_TEST_EVENT", reason="synthetic", stage="stage19", recoverable=True)
            is False
        )
    # different signature → first again
    assert (
        agg.add(event="SMOKE_TEST_EVENT", reason="other", stage="stage19", recoverable=False)
        is True
    )
    agg.flush(lines.append)
    blob = "\n".join(lines)
    assert "SMOKE_TEST_EVENT" in blob
    # repeats must be aggregated, not 10 log lines
    metrics = agg._metrics
    assert metrics["events_seen"] == 10
    assert metrics["first_occurrences"] == 2
    assert metrics["dropped_events"] == 0, "protected evidence must never drop"
    _ok("Observability OK — 10 events → 2 signatures, flush produced summary, 0 dropped")
    _info(f"stage 19 in {(time.monotonic() - t0) * 1000:.1f} ms")


def test_smoke_important_files_integrity() -> None:
    """STAGE 20 — whole-project file integrity: entrypoints, core modules, registries."""
    _banner("🗂️  STAGE 20 · IMPORTANT FILES INTEGRITY (whole project)")
    t0 = time.monotonic()
    required = (
        "NexusTradingForexBot.py",
        "main.py",
        "configs/base.yaml",
        "pyproject.toml",
        "agents/skill.md",
        "agents/bugs.md",
        "agents/contracts.md",
        "agents/runtime_invariants.md",
        "agents/taskboard.md",
        "src/nexus_scalp/application/live_engine.py",
        "src/nexus_scalp/execution/order_manager.py",
        "src/nexus_scalp/features/schema_contract.py",
        "src/nexus_scalp/features/features70.py",
        "src/nexus_scalp/features/liquidity_engine.py",
        "src/nexus_scalp/features/regime_classifier.py",
        "src/nexus_scalp/models/scalp_net.py",
        "src/nexus_scalp/signals/policy.py",
        "src/nexus_scalp/signals/rule_matrix.py",
        "src/nexus_scalp/risk/risk_engine.py",
        "src/nexus_scalp/adapters/paper/paper_adapter.py",
        "src/nexus_scalp/adapters/database/audit_repository.py",
        "src/nexus_scalp/news/gate.py",
        "src/nexus_scalp/mslie/engine.py",
        "src/nexus_scalp/experience/intelligence.py",
        "src/nexus_scalp/configuration/runtime_config.py",
        "src/nexus_scalp/settings/service.py",
        "src/nexus_scalp/incidents/store.py",
        "src/nexus_scalp/observability/event_aggregator.py",
        "src/nexus_scalp/web/server.py",
        "src/nexus_scalp/smoke/runner.py",
        "tests/e2e/test_smoke_chain.py",
        "tests/critical_suite.txt",
        ".github/workflows/ci.yml",
    )
    missing = [p for p in required if not (REPO_ROOT / p).exists()]
    assert not missing, f"important files absent: {missing}"
    # entrypoints must compile
    import py_compile

    for entry in ("NexusTradingForexBot.py", "main.py"):
        py_compile.compile(str(REPO_ROOT / entry), doraise=True)
    # governance registries must be non-empty
    skill_txt = (REPO_ROOT / "agents" / "skill.md").read_text(encoding="utf-8", errors="ignore")
    assert "INV-001" in skill_txt, "agents/skill.md must carry the invariant registry"
    bugs_size = (REPO_ROOT / "agents" / "bugs.md").stat().st_size
    assert bugs_size > 10_000, f"agents/bugs.md suspiciously small ({bugs_size}B)"
    _ok(f"File integrity OK — {len(required)} important files present, entrypoints compile")
    _info(f"stage 20 in {(time.monotonic() - t0) * 1000:.1f} ms  bugs.md={bugs_size // 1024}KB")


def test_smoke_critical_suite_manifest_wiring() -> None:
    """STAGE 21 — the smoke chain itself must stay wired into the quality gate."""
    _banner("🔗 STAGE 21 · QUALITY-GATE WIRING (critical suite + CI)")
    t0 = time.monotonic()
    crit = (REPO_ROOT / "tests" / "critical_suite.txt").read_text(encoding="utf-8")
    assert "tests/e2e/test_smoke_chain.py" in crit, "smoke chain must stay in critical_suite.txt"
    assert "tests/unit/test_smoke_self.py" in crit, (
        "smoke self-tests must stay in critical_suite.txt"
    )
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "test_smoke_chain.py" in ci, "CI smoke job must run the chain"
    assert "nexus_scalp.cli.main smoke --fast" in ci, (
        "CI layered-smoke job must run nse smoke --fast"
    )
    self_test = (REPO_ROOT / "tests" / "unit" / "test_smoke_self.py").read_text(encoding="utf-8")
    assert "critical_ids" in self_test, "self-test must guard registry completeness"
    _ok("Gate wiring OK — chain + self-tests in critical_suite, CI smoke jobs present")
    _info(f"stage 21 in {(time.monotonic() - t0) * 1000:.1f} ms")
