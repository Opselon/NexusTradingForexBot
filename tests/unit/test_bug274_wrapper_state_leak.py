"""BUG-274: P1-seam wrapper-state leak class (NSE-Swarm role 6, 2026-09-14).

The god-file decomposition wave (P1 seams L5-L8, S10) moved method bodies out
of ``LiveEngine`` / ``OrderLifecycleManager`` into BOUND wrapper classes
(``BarHandler(om)``, ``RuntimeLoop(om)``, ``TickPipeline(om)``,
``ProtectionEngine(om)``) where the ONLY state surface is ``self.om``. Every
``getattr(self, "X")`` that was correct pre-extraction (self IS the engine)
silently becomes a DEFAULT-READ after the move: the attribute lives on the
composition root, never on the wrapper.

BUG-169 already proved this class on the duplicate-tick guard (fixed for
``_pipeline_last_*`` only - see test_market_data_integrity_ag13::md7). The
2026-09-14 AST sweep found the 13 remaining sites:

  bar_handler.py:40/140/162/175/210/214/302  candle_intel, news_engine,
      _last_proposal, mslie_engine, _online_train_disabled,
      _online_train_width_warn_at, _online_ft_disabled_log_at
  runtime_loop.py:328/338/375/393            _last_account_refresh,
      _last_account_info x2, _last_snapshot_refresh
  tick_pipeline.py:308                       server_state
  protection.py:721                          _last_tick_for_ticket

Functional impact of the four provable-by-behavior shapes:
  * RuntimeLoop:328/393 - PERF-02's 5s account refresh + snapshot throttles
    never engage (wrapper default 0.0, epoch-clock always stale): the loop
    performs the per-tick gateway RPC the throttle exists to eliminate. The
    companion test (test_perf02_account_refresh_throttle.py) pins a hand-
    copied MIRROR of the block, so shipped code drifted while tests stayed
    green.
  * BarHandler:214/302 - the once-per-hour CRITICAL (width-contract split)
    and once-per-hour INFO (DISABLED_BY_CONFIG) gates short-circuit to True
    on every call: with the retrain counter past the interval (it never
    resets while fine-tune is disabled by default) the log line fires EVERY
    completed bar. The BUG-273-exec-trace class again.
  * BarHandler:40/175 - the candle-intel + MSLIE bar feeds can never run
    (wrapper never has the attribute -> always None) even when the operator
    enables the subsystems.
  * BarHandler:140/162 - radar payloads permanently report news_state=None
    and decision_reason=None.

xdist-safe: behavior tests drive the REAL wrapper methods against a typed
double (mirroring test_agent16_hotpath_perf._HandlerOM) with the module
logger monkeypatched (no caplog - structlog host routing).
"""

from __future__ import annotations

import inspect
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# shared doubles
# ---------------------------------------------------------------------------


class _Trainer:
    def __init__(self, width: int) -> None:
        self.num_features = width


def _tick(price: float = 1.0):
    from nexus_scalp.domain.models import TickData

    ts = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    return TickData(
        symbol="XAUUSD",
        bid=price,
        ask=price + 0.2,
        last=price,
        volume=1.0,
        timestamp=ts,
    )


class _Bar:
    def __init__(self, ts: datetime, close: float = 1.0) -> None:
        self.timestamp = ts
        self.close = close
        self.high = close + 0.5
        self.low = close - 0.5
        self.open = close
        self.tick_volume = 10


def _bar_record(width: int) -> dict:
    rec = {f"feat_{i}": 0.1 for i in range(width)}
    rec.update(close=1.0, high=1.5, low=0.5, open=1.0, spread=0.2, atr_m1=1.5)
    return rec


class _HandlerOM:
    """LiveEngine-shaped double for BarHandler.on_new_bar (bound seam: every
    attribute lives HERE, the handler must reach it through self.om)."""

    def __init__(self, trainer_width: int = 50) -> None:
        self.trainer = _Trainer(trainer_width)
        self._rolling_feature_records: list[dict] = []
        self._bars_since_last_retrain = 0
        self._retrain_interval_bars = 50
        self._retrain_inflight = False
        self._online_finetune_enabled = False
        self._online_train_width_warn_at = 0.0
        self._online_ft_disabled_log_at = 0.0
        self._online_train_disabled = False
        self._retrain_task = None
        self._governance_reference_vector = None
        self._last_candle_decision: object = None
        self._last_market_radar: dict | None = None
        self._last_mslie_vector: object = None
        self._last_regime_state: object = None
        self._last_proposal: object = None
        self.order_manager = SimpleNamespace(_position_states={})
        self.news_engine: object = None
        self.candle_intel: object = None
        self.mslie_engine: object = None
        self.setup_detector: object = None
        self.aggregator: object = None
        self._build_retrain_record: object = None

    def _validate_50d_tensor(self, vec, context: str = ""):
        return list(vec)


def _make_handler(om: _HandlerOM):
    from nexus_scalp.application.live.bar_handler import BarHandler

    return BarHandler(om)


def _fv(width: int):
    return SimpleNamespace(to_tensor_input=lambda: [0.1] * width, atr_m1=1.5)


@pytest.fixture()
def bh_logs(monkeypatch) -> list:
    from nexus_scalp.application.live import bar_handler as bh

    got: list = []

    def _rec(level):
        def _fn(*a, **k):
            got.append((level, a, k))

        return _fn

    double = SimpleNamespace(
        critical=_rec("critical"),
        error=_rec("error"),
        warning=_rec("warn"),
        info=_rec("info"),
        debug=_rec("debug"),
    )
    monkeypatch.setattr(bh, "logger", double)
    return got


# ---------------------------------------------------------------------------
# 1. RuntimeLoop: PERF-02 throttles must read/write the ENGINE stamps
# ---------------------------------------------------------------------------


def test_runtime_loop_account_refresh_reads_engine_state() -> None:
    """Pre-fix: getattr(self, "_last_account_refresh") on the RuntimeLoop
    wrapper is always 0.0 -> the 5s PERF-02 throttle NEVER engages and the
    adapter RPC runs per tick (the exact BUG-169 md7 shape, unfixed sibling).
    The engine writes the stamp (self.om._last_account_refresh = _now), so
    the read must come from the same surface."""
    from nexus_scalp.application.live.runtime_loop import RuntimeLoop

    src = inspect.getsource(RuntimeLoop.run)
    assert 'getattr(self.om, "_last_account_refresh"' in src
    assert 'getattr(self, "_last_account_refresh"' not in src
    assert 'getattr(self.om, "_last_snapshot_refresh"' in src
    assert 'getattr(self, "_last_snapshot_refresh"' not in src
    assert 'getattr(self.om, "_last_account_info"' in src
    assert 'getattr(self, "_last_account_info"' not in src


# ---------------------------------------------------------------------------
# 2. BarHandler: throttle stamps + subsystem gates must read the ENGINE
# ---------------------------------------------------------------------------


def test_bar_handler_reads_target_engine_surface() -> None:
    from nexus_scalp.application.live.bar_handler import BarHandler

    src = inspect.getsource(BarHandler.on_new_bar)
    for name in (
        "candle_intel",
        "news_engine",
        "_last_proposal",
        "mslie_engine",
        "_online_train_disabled",
        "_online_train_width_warn_at",
        "_online_ft_disabled_log_at",
    ):
        assert f'getattr(self, "{name}"' not in src, f"wrapper-local read survived: {name}"
    # _a16_last_trainer_width is INTENTIONALLY wrapper-local (assigned in the
    # same method) - the width-epoch tracker must NOT be pinned to self.om.
    assert "_a16_last_trainer_width" in src


def test_width_split_critical_is_hourly_not_per_bar(bh_logs) -> None:
    """Width-contract split (50D records vs 70D trainer): the BUG-185 CRITICAL
    must fire once per 3600s. Pre-fix, the gate read the wrapper's missing
    stamp (always falsy) so EVERY bar past the retrain interval re-logged."""
    om = _HandlerOM(trainer_width=70)
    om._bars_since_last_retrain = 60
    handler = _make_handler(om)
    tick = _tick()

    def _mismatched_builder(**_kw):
        return _bar_record(50)

    om._build_retrain_record = _mismatched_builder

    for _ in range(3):
        handler.on_new_bar(tick, _fv(50), _Bar(tick.timestamp))

    crits = [l for l in bh_logs if l[0] == "critical"]
    assert len(crits) == 1, (
        f"width-split CRITICAL must be rate-limited to 1/hour, got {len(crits)} "
        "(per-bar spam = the gate reads the wrapper stamp instead of the engine's)"
    )
    # stamp must land on the ENGINE surface the next evaluation reads
    assert om._online_train_width_warn_at > 0.0


def test_ft_disabled_info_is_hourly_not_per_bar(bh_logs) -> None:
    """Matching widths, >=300 records, retrain counter past interval, fine-tune
    DISABLED (the shipped default): the DISABLED_BY_CONFIG INFO is contract
    'one throttled INFO per retrain window', not per bar."""
    om = _HandlerOM(trainer_width=50)
    om._rolling_feature_records = [_bar_record(50) for _ in range(300)]
    om._bars_since_last_retrain = 60
    handler = _make_handler(om)
    tick = _tick()

    def _good_builder(**_kw):
        return _bar_record(50)

    om._build_retrain_record = _good_builder

    for _ in range(3):
        handler.on_new_bar(tick, _fv(50), _Bar(tick.timestamp))

    infos = [l for l in bh_logs if l[0] == "info" and "DISABLED_BY_CONFIG" in str(l[1])]
    assert len(infos) == 1, f"DISABLED_BY_CONFIG must fire once per hour, got {len(infos)}"
    assert om._online_ft_disabled_log_at > 0.0


def test_online_train_disabled_flag_gates_from_engine(bh_logs) -> None:
    """_online_train_disabled=True (unknown-width self-disable at boot) lives
    on the engine. Pre-fix the handler never saw it: a deliberately disabled
    trainer kept flowing into the fine-tune dispatch branch."""
    om = _HandlerOM(trainer_width=50)
    om._online_train_disabled = True
    om._bars_since_last_retrain = 60
    om._rolling_feature_records = [_bar_record(50) for _ in range(300)]
    handler = _make_handler(om)
    tick = _tick()

    def _good_builder(**_kw):
        return _bar_record(50)

    om._build_retrain_record = _good_builder
    handler.on_new_bar(tick, _fv(50), _Bar(tick.timestamp))

    # the disabled trainer must take the width-guard EARLY RETURN (no
    # DISABLED_BY_CONFIG INFO from the fine-tune branch - that branch is
    # unreachable while the trainer is self-disabled)
    infos = [l for l in bh_logs if l[0] == "info" and "DISABLED_BY_CONFIG" in str(l[1])]
    assert not infos, "self-disabled trainer must never reach the fine-tune dispatch"
    crits = [l for l in bh_logs if l[0] == "critical"]
    assert len(crits) == 1, "self-disable must surface via the width-split CRITICAL"


def test_radar_news_state_and_decision_reason_flow_from_engine() -> None:
    """Radar payload fields are read through the wrapper pre-fix -> permanently
    null. Engine-owned news_engine + _last_proposal must reach the payload."""
    om = _HandlerOM(trainer_width=50)
    om._last_proposal = SimpleNamespace(reason_code="TEST_R1")
    om.news_engine = SimpleNamespace(
        current_context=lambda: SimpleNamespace(state=SimpleNamespace(value="HIGH_IMPACT"))
    )
    om.setup_detector = SimpleNamespace(
        min_quality=0.0,
        detect=lambda rec, timestamp: [
            SimpleNamespace(quality=0.9, to_contract=lambda: {"name": "S1"})
        ],
    )
    handler = _make_handler(om)
    tick = _tick()

    def _good_builder(**_kw):
        return _bar_record(50)

    om._build_retrain_record = _good_builder
    handler.on_new_bar(tick, _fv(50), _Bar(tick.timestamp))

    radar = om._last_market_radar
    assert radar is not None, "radar payload missing (double shape drift)"
    assert radar["news_state"] == "HIGH_IMPACT", (
        f"news_state must come from the engine news_engine, got {radar['news_state']!r}"
    )
    assert radar["decision_reason"] == "TEST_R1", (
        f"decision_reason must come from the engine _last_proposal, got {radar['decision_reason']!r}"
    )


def test_mslie_bar_feed_targets_engine_surface() -> None:
    """ms.analyze_market can only run if the handler reads self.om.mslie_engine
    (wrapper read is permanently None -> subsystem dead regardless of config)."""
    om = _HandlerOM(trainer_width=50)
    bar = _Bar(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    om.aggregator = SimpleNamespace(get_completed_bars=lambda: [bar])
    calls: list = []

    def _analyze(bars, *, decision_at, mid_price, atr):
        calls.append(bars)
        return "MSLIE-VECTOR"

    om.mslie_engine = SimpleNamespace(analyze_market=_analyze)
    handler = _make_handler(om)
    tick = _tick()

    def _good_builder(**_kw):
        return _bar_record(50)

    om._build_retrain_record = _good_builder
    handler.on_new_bar(tick, _fv(50), _Bar(tick.timestamp))

    assert calls, "engine-enabled mslie_engine must receive the bar-close feed"
    assert om._last_mslie_vector == "MSLIE-VECTOR"


# ---------------------------------------------------------------------------
# 3. TickPipeline / ProtectionEngine: source pins (md7 precedent)
# ---------------------------------------------------------------------------


def test_tick_pipeline_chart_gate_reads_engine_server_state() -> None:
    """server_state lives on the engine (boot assigns it); the wrapper read is
    permanently None so the live SMC chart-overlay refresh NEVER runs between
    bar closes (UI chart froze to bar cadence + manual sync)."""
    from nexus_scalp.application.live.tick_pipeline import TickPipeline

    src = inspect.getsource(TickPipeline.run_post_policy_stages)
    assert 'getattr(self.om, "server_state", None)' in src
    assert 'getattr(self, "server_state", None)' not in src


def test_protection_engine_last_tick_view_reads_engine() -> None:
    """_last_tick_for_ticket is an OrderManager property; ProtectionEngine is a
    BOUND wrapper, so the wrapper read yields {} -> the VOLATILITY_EXPANSION
    breakeven-breach check can never observe a price and the market-close
    branch is permanently dead."""
    from nexus_scalp.execution.lifecycle.protection import ProtectionEngine

    src = inspect.getsource(ProtectionEngine)
    assert (
        'getattr(self.om, "_last_tick_for_ticket"' in src or "self.om._last_tick_for_ticket" in src
    )
    assert 'getattr(self, "_last_tick_for_ticket"' not in src


# ---------------------------------------------------------------------------
# 4. class-wide guard: no NEW wrapper leak in the bound seams
# ---------------------------------------------------------------------------

_BOUND_SEAMS = [
    "src/nexus_scalp/application/live/bar_handler.py",
    "src/nexus_scalp/application/live/runtime_loop.py",
    "src/nexus_scalp/application/live/tick_pipeline.py",
    "src/nexus_scalp/execution/lifecycle/protection.py",
    "src/nexus_scalp/application/live/maintenance.py",
    "src/nexus_scalp/execution/lifecycle/reconciliation.py",
]


def test_no_new_guarded_wrapper_reads_in_bound_seams() -> None:
    """Every ``getattr(self, "X")`` inside a BOUND seam must name an attribute
    the file itself assigns (legitimate wrapper-local state). A read of
    anything else targets the composition root and defaults silently."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for rel in _BOUND_SEAMS:
        path = root / rel
        tree = ast.parse(path.read_text())
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            written = {
                n2.attr
                for n2 in ast.walk(cls)
                if isinstance(n2, ast.Attribute)
                and isinstance(n2.value, ast.Name)
                and n2.value.id == "self"
                and isinstance(n2.ctx, ast.Store)
            }
            for n2 in ast.walk(cls):
                if (
                    isinstance(n2, ast.Call)
                    and isinstance(n2.func, ast.Name)
                    and n2.func.id == "getattr"
                    and n2.args
                    and isinstance(n2.args[0], ast.Name)
                    and n2.args[0].id == "self"
                    and len(n2.args) > 1
                    and isinstance(n2.args[1], ast.Constant)
                    and isinstance(n2.args[1].value, str)
                    and n2.args[1].value not in written
                ):
                    offenders.append(f"{rel}:{n2.lineno} {n2.args[1].value}")
    assert not offenders, "wrapper-state leak (BUG-274 class): " + "; ".join(offenders)
