"""Streaming Replay Engine (CHG-0035, STREAMING_REPLAY v1).

ONE causal replay engine shared by BAR replay, TICK replay and the FORWARD
TEST runner (user brief §15, §24, §40, §41 — no duplicate replay engines, no
duplicate strategy logic). The canonical event pipeline per decision:

    TICK/BAR -> MARKET STATE -> CAUSAL FEATURES (50D + news + liquidity @T)
             -> LOCAL scalp_v3 70D MODEL -> FROZEN STRATEGY (SignalPolicy)
             -> RISK (RiskEngine) -> SIMULATED EXECUTION
             -> POSITION STATE -> TRADE LEDGER

HARD INVARIANTS (test-enforced):

* LOGICAL CLOCK ONLY: simulation time is event timestamps. No wall-clock
  sleeps and no datetime.now() in the decision path (brief §17).
* LOCAL MODEL: the 70D bundle (model + scaler from the artifact path) is
  loaded ONCE per session, torch.inference_mode, no external provider (§27,
  §28). Artifacts may be pinned to a frozen directory (forward test).
* NO EXECUTION SIDE EFFECTS: no adapter import, no mt5.order_send anywhere
  in this module. Fills are simulated from HISTORICAL bid/ask with
  direction-aware pricing: BUY entry ASK / SELL entry BID / BUY exit BID /
  SELL exit ASK (§18, §19, §63, §64).
* SL/TP resolution uses tick chronology: the FIRST tick that touches SL or
  TP decides the exit (resolves OHLC ambiguity, §20). Bar replay feeds
  SL/TP via high/low touch without intra-bar chronology claims; the
  SL-first tie-break is conservative and is a documented
  EXPECTED_RESOLUTION_DIFFERENCE vs tick replay (§22, §23).
* DETERMINISM: same source + same frozen config => identical event, order,
  fill, position and ledger sequences (§16). No RNG is used anywhere.
* BOUNDED MEMORY: only the causal feature window is retained; diagnostics
  are summaries and bounded samples, never per-tick tensor dumps (§52, §53).

STRATEGY FREEZE (§82): the engine calls the production
SignalPolicy.evaluate_probabilities — the SAME Strategy Core as live. It
does NOT reimplement strategy rules. SignalPolicy time state advances only
via event timestamps (carried inside TickData); no wall-clock reads.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.event_source import (
    BarEvent,
    DataErrorEvent,
    HistoricalEventSource,
    TickEvent,
)
from nexus_scalp.signals.policy import SignalPolicy

logger = get_logger("nexus_scalp.research.streaming_replay")

#: Completed bars required before the 50D feature engine produces real
#: features (canonical warm-up; below it decisions are skipped as WARMUP).
FEATURE_WARMUP_BARS: int = 55

#: Default artifact path for the production 70D scalp_v3 bundle.
DEFAULT_MODEL_ARTIFACT = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"

#: Default synthetic spread (USD) used ONLY by bar-mode decisions, matching
#: the compute_70d_frame dataset convention (synthetic tick at bar close).
BAR_MODE_SYNTHETIC_SPREAD_USD: float = 0.20

#: CHG-0043: bounded per-decision evidence trace size (observability only;
#: never feeds back into the decision path — INV-018).
DECISION_TRACE_LIMIT: int = 5000


# ---------------------------------------------------------------------------
# Frozen model bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelArtifacts:
    """Local model bundle identity + the loaded objects (loaded ONCE)."""

    model_path: Path
    scaler_path: Path
    model_fingerprint: str
    scaler_fingerprint: str
    num_features: int
    model: Any  # ScalpNet (eval mode)
    scaler_mean: np.ndarray | None
    scaler_std: np.ndarray | None

    def identity(self) -> dict[str, Any]:
        return {
            "model_path": str(self.model_path),
            "scaler_path": str(self.scaler_path),
            "model_fingerprint": self.model_fingerprint,
            "scaler_fingerprint": self.scaler_fingerprint,
            "num_features": self.num_features,
        }


def _sha256_file(path: Path, prefix: int = 32) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:prefix]


def load_model_artifacts(model_path: str | Path) -> ModelArtifacts:
    """Loads model + scaler from disk ONCE. Fails loudly; never fabricates.

    The model input width comes from the checkpoint's own declared tensor
    width (BUG-125 convention) — never from a filename or class default.

    Weights load STRICT (ScalpNet(state_dict) → eval) so a corrupted or
    foreign checkpoint fails the session instead of predicting garbage.
    """
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    p = Path(model_path)
    if not p.exists():
        raise FileNotFoundError(f"model artifact missing: {p}")
    scaler_path = p.with_suffix(".scaler.npz")
    mean = std = None
    if scaler_path.exists():
        data = np.load(scaler_path)
        mean = np.asarray(data["mean"], dtype=np.float64)
        std = np.asarray(data["std"], dtype=np.float64)
    probe = torch.load(p, map_location="cpu")
    w = probe.get("input_projection.weight") if isinstance(probe, dict) else None
    if w is None or not hasattr(w, "shape") or len(w.shape) != 2:
        raise ValueError(f"model artifact has no input_projection.weight: {p}")
    num_features = int(w.shape[1])
    head_dim = int(w.shape[0])
    try:
        model = ScalpNet(num_features=num_features, num_classes=4, hidden_dim=head_dim)
        model.load_state_dict(probe)  # strict: any mismatch = hard failure
    except (RuntimeError, ValueError) as e:
        raise ValueError(
            f"model artifact failed strict ScalpNet load (corrupted/foreign checkpoint): {p}: {e}"
        ) from e
    model.eval()
    return ModelArtifacts(
        model_path=p,
        scaler_path=scaler_path if scaler_path.exists() else p,
        model_fingerprint=_sha256_file(p),
        scaler_fingerprint=_sha256_file(scaler_path) if scaler_path.exists() else "",
        num_features=num_features,
        model=model,
        scaler_mean=mean,
        scaler_std=std,
    )


# ---------------------------------------------------------------------------
# Frozen configuration contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReplayExecutionConfig:
    """Execution simulation contract (§19, §43, §77, §78)."""

    #: Broker symbol economics — callers pass values captured through the
    #: probed symbol_info contract (defaults mirror the probed XAUUSD facts:
    #: trade_contract_size=100.0, point=0.01, tick_value=0.1).
    contract_size: float = 100.0
    tick_size: float = 0.01
    tick_value: float = 0.1
    volume_min: float = 0.01
    volume_step: float = 0.01
    #: Recorded logical latency decoration (ms) — fills are immediate; no
    #: real sleeping ever happens (§21).
    latency_signal_to_fill_ms: float = 0.0
    #: SL/TP evaluated on every event of the replay path (§20).
    evaluate_sl_tp_every_event: bool = True

    def identity(self) -> dict[str, Any]:
        return {
            "contract_size": self.contract_size,
            "tick_size": self.tick_size,
            "tick_value": self.tick_value,
            "volume_min": self.volume_min,
            "volume_step": self.volume_step,
            "latency_signal_to_fill_ms": self.latency_signal_to_fill_ms,
            "evaluate_sl_tp_every_event": self.evaluate_sl_tp_every_event,
        }


@dataclass(frozen=True, slots=True)
class ReplaySessionConfig:
    """Complete frozen session identity (§87-§92)."""

    experiment_type: str = "REPLAY"  # REPLAY | FORWARD_TEST
    symbol: str = "XAUUSD"
    timeframe: str = "M1"
    model_artifact_path: str = DEFAULT_MODEL_ARTIFACT
    #: Frozen SignalPolicy constructor params — captured into the session
    #: fingerprint and never mutated during/after a run (§84).
    policy_params: dict[str, Any] = field(default_factory=dict)
    execution: ReplayExecutionConfig = field(default_factory=ReplayExecutionConfig)
    #: Decision cadence: "bar_close" (first tick of a new minute in tick
    #: mode; after each bar in bar mode) or "every_tick".
    decide_on: str = "bar_close"
    #: Causal news frame (polars) consumed via news_context_at(t).
    news_frame: Any = None
    git_commit: str = ""
    starting_equity_usd: float = 10_000.0
    #: CHG-0043 (additive): causal regime wiring. False (default) keeps the
    #: exact pre-refactor behavior (regime_state=None everywhere); True runs
    #: the production MarketRegimeClassifier causally on replay ticks.
    regime_enabled: bool = False

    def identity(self) -> dict[str, Any]:
        return {
            "experiment_type": self.experiment_type,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "model_artifact_path": self.model_artifact_path,
            "policy_params": dict(sorted(self.policy_params.items())),
            "execution": self.execution.identity(),
            "decide_on": self.decide_on,
            "git_commit": self.git_commit,
            "starting_equity_usd": self.starting_equity_usd,
            "regime_enabled": self.regime_enabled,
        }

    def fingerprint(self, model_fingerprint: str = "", scaler_fingerprint: str = "") -> str:
        payload = self.identity()
        payload["model_fingerprint"] = model_fingerprint
        payload["scaler_fingerprint"] = scaler_fingerprint
        raw = repr(sorted(payload.items())).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Ledger records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    """One simulated order with logical latency timestamps (§21)."""

    order_id: str
    signal_time: datetime
    decision_time: datetime
    order_time: datetime
    fill_time: datetime
    action: str
    order_type: str
    volume: float
    requested_price: float
    fill_price: float
    stop_loss: float
    take_profit: float
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    trade_id: str
    entry_order_id: str
    exit_order_id: str
    direction: str
    volume: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str  # SL | TP | SIGNAL_REVERSAL | END_OF_DATA
    pnl_usd: float
    mae_usd: float
    mfe_usd: float
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "direction": self.direction,
            "volume": self.volume,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price,
            "exit_time": self.exit_time.isoformat(),
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "pnl_usd": round(self.pnl_usd, 6),
            "mae_usd": round(self.mae_usd, 6),
            "mfe_usd": round(self.mfe_usd, 6),
            "entry_order_id": self.entry_order_id,
            "exit_order_id": self.exit_order_id,
            "run_id": self.run_id,
        }


def _order_to_dict(o: SimulatedOrder) -> dict[str, Any]:
    """slots-safe dict projection (BUG-192: dataclass slots have no __dict__)."""
    return {
        "order_id": o.order_id,
        "signal_time": o.signal_time.isoformat(),
        "decision_time": o.decision_time.isoformat(),
        "order_time": o.order_time.isoformat(),
        "fill_time": o.fill_time.isoformat(),
        "action": o.action,
        "order_type": o.order_type,
        "volume": o.volume,
        "requested_price": o.requested_price,
        "fill_price": o.fill_price,
        "stop_loss": o.stop_loss,
        "take_profit": o.take_profit,
        "run_id": o.run_id,
    }


def _ledger_digest(trades: list[SimulatedTrade], events_hash: str) -> str:
    h = hashlib.sha256()
    h.update(events_hash.encode("utf-8"))
    for t in trades:
        h.update(
            "|".join(
                (
                    t.trade_id,
                    t.direction,
                    f"{t.volume:.4f}",
                    t.entry_time.isoformat(),
                    f"{t.entry_price:.5f}",
                    t.exit_time.isoformat(),
                    f"{t.exit_price:.5f}",
                    t.exit_reason,
                    f"{t.pnl_usd:.8f}",
                )
            ).encode("utf-8")
        )
    return h.hexdigest()[:32]


# ---------------------------------------------------------------------------
# Frozen strategy adapter (SAME Strategy Core as live — §82)
# ---------------------------------------------------------------------------


class FrozenPolicyRunner:
    """Constructs SignalPolicy ONCE from frozen params; never mutates them.

    The production policy's persistent state (cooldown / flip memory /
    re-entry locks) evolves ONLY via evaluate_probabilities on replay
    events — identical semantics to live. No parameter is rewritten during
    or after a run (§84).
    """

    def __init__(self, params: dict[str, Any]) -> None:
        self.params: dict[str, Any] = dict(sorted(params.items()))
        self.policy = SignalPolicy(**self.params)
        self._fingerprint = hashlib.sha256(repr(self.params).encode("utf-8")).hexdigest()[:32]

    def fingerprint(self) -> str:
        return self._fingerprint

    def evaluate(self, probs_tensor: Any, tick: Any, fv: Any, regime_state: Any = None) -> Any:
        """Same production policy semantics; regime_state is the CAUSAL
        classifier output at T (None keeps the historical behavior)."""
        return self.policy.evaluate_probabilities(
            probs_tensor,
            current_tick=tick,
            feature_vector=fv,
            regime_state=regime_state,
        )


# ---------------------------------------------------------------------------
# The ONE replay engine
# ---------------------------------------------------------------------------


@dataclass
class _OpenPosition:
    direction: str  # BUY | SELL
    volume: float
    entry_order_id: str
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    mae_usd: float = 0.0
    mfe_usd: float = 0.0


@dataclass
class ReplayRunResult:
    """Structured replay outcome (§53: summaries, not tensor dumps)."""

    run_id: str
    experiment_type: str
    config_fingerprint: str
    model_identity: dict[str, Any]
    strategy_fingerprint: str
    schema_hash: str
    event_hash: str
    ledger_hash: str
    events_seen: int
    decisions: int
    orders: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    data_errors: list[dict[str, Any]]
    first_event: str
    last_event: str
    total_pnl_usd: float
    final_equity_usd: float
    warmup_skipped_decisions: int
    decision_mode: str
    #: CHG-0043 (additive): bounded per-decision evidence trace + regime
    #: transition records. Empty unless the caller enables tracing/regime.
    decision_trace: list[dict[str, Any]] = field(default_factory=list)
    regime_transitions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "experiment_type": self.experiment_type,
            "config_fingerprint": self.config_fingerprint,
            "model_identity": self.model_identity,
            "strategy_fingerprint": self.strategy_fingerprint,
            "schema_hash": self.schema_hash,
            "event_hash": self.event_hash,
            "ledger_hash": self.ledger_hash,
            "events_seen": self.events_seen,
            "decisions": self.decisions,
            "orders": self.orders,
            "order_count": len(self.orders),
            "trade_count": len(self.trades),
            "trades": self.trades,
            "data_errors": self.data_errors,
            "first_event": self.first_event,
            "last_event": self.last_event,
            "total_pnl_usd": round(self.total_pnl_usd, 6),
            "final_equity_usd": round(self.final_equity_usd, 6),
            "warmup_skipped_decisions": self.warmup_skipped_decisions,
            "decision_mode": self.decision_mode,
            "decision_trace": self.decision_trace,
            "regime_transitions": self.regime_transitions,
        }


@dataclass
class _RunState:
    """Mutable per-run replay state (CHG-0043).

    ONE state object shared by StreamingReplayEngine.run() and the stepwise
    ReplaySessionController (research/replay_session.py) so both drive the
    IDENTICAL event-processing path. Field-for-field the same locals run()
    used before the refactor (hash-equivalence probe-verified).
    """

    rid: str
    cfg_fp: str
    identity: dict[str, Any]
    risk_engine: Any
    equity: float
    completed: list[BarData]
    open_pos: _OpenPosition | None
    trades: list[SimulatedTrade]
    orders: list[SimulatedOrder]
    data_errors: list[dict[str, Any]]
    event_hasher: Any
    events_seen: int
    decisions: int
    last_decide_minute: tuple[int, int, int, int, int] | None
    first_ts: str
    last_ts: str
    last_tick: TickEvent | None
    feature_engine: _FeatureEngineHolder
    #: CHG-0043 additive: causal regime classifier (None = disabled; the
    #: default keeps byte-identical behavior with pre-refactor runs).
    regime_classifier: Any | None = None
    regime_state: Any | None = None
    prev_regime_type: str = ""
    decision_trace: list[dict[str, Any]] = field(default_factory=list)
    regime_transitions: list[dict[str, Any]] = field(default_factory=list)


class StreamingReplayEngine:
    """Streams a HistoricalEventSource through the canonical causal chain.

    One instance per replay session. Model/scaler/policy are bound at
    construction and are NEVER replaced mid-run (freeze, §70). Every run
    is independent and deterministic; the same engine may be re-run on any
    re-iterable source with identical results.
    """

    def __init__(
        self,
        config: ReplaySessionConfig,
        *,
        artifacts: ModelArtifacts | None = None,
        policy: FrozenPolicyRunner | None = None,
    ) -> None:
        self.config = config
        self.artifacts = artifacts or load_model_artifacts(config.model_artifact_path)
        self.policy = policy or FrozenPolicyRunner(config.policy_params)

    # ------------------------------------------------------------------
    # Session identity
    # ------------------------------------------------------------------

    def session_identity(self) -> dict[str, Any]:
        return {
            "config": self.config.identity(),
            "model": self.artifacts.identity(),
            "strategy_fingerprint": self.policy.fingerprint(),
            "schema_hash": self._schema_hash(),
            "schema_dimension": int(self.artifacts.num_features),
        }

    def config_fingerprint(self) -> str:
        return self.config.fingerprint(
            self.artifacts.model_fingerprint, self.artifacts.scaler_fingerprint
        )

    def _schema_hash(self) -> str:
        try:
            from nexus_scalp.features.schema_contract import feature_schema_hash

            return feature_schema_hash()
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Local model inference (loaded once, inference_mode, no network)
    # ------------------------------------------------------------------

    def _predict_probs(self, vec70: list[float]) -> list[float]:
        """Runs the LOCAL model. Never trains, never calls a provider."""
        import torch

        if len(vec70) != int(self.artifacts.num_features):
            raise ValueError(
                f"70D contract violation: vector width {len(vec70)} != "
                f"model width {self.artifacts.num_features} (no fallback, INV-009)"
            )
        vec = np.asarray(vec70, dtype=np.float64).reshape(1, -1)
        if self.artifacts.scaler_mean is not None and self.artifacts.scaler_std is not None:
            vec = np.clip(
                (vec - self.artifacts.scaler_mean.reshape(1, -1))
                / (self.artifacts.scaler_std.reshape(1, -1) + 1e-8),
                -5.0,
                5.0,
            )
        x = torch.tensor(vec, dtype=torch.float32)
        x = torch.nan_to_num(x, nan=0.0, posinf=1.0, neginf=-1.0)
        model = self.artifacts.model
        model.eval()
        prior = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            with torch.inference_mode():
                probs = torch.softmax(model(x), dim=-1).cpu().numpy()[0]
        finally:
            torch.set_num_threads(prior)
        return [float(p) for p in probs]

    # ------------------------------------------------------------------
    # Causal feature assembly at decision time T (§29-§32)
    # ------------------------------------------------------------------

    def _assemble_vector(
        self,
        completed_bars: list[BarData],
        tick: TickEvent,
        fv: Any,
    ) -> list[float]:
        """Assembles the canonical scalp_v3 vector from CAUSAL state only.

        Uses exactly the production producers: the 50D engine's FeatureVector
        (already computed from the causal window), news via news_context_at
        on the session frame, liquidity via the canonical
        compute_liquidity_features on closed bars <= T. Raises on any
        contract violation (no silent pad/truncate, INV-009).
        """
        from nexus_scalp.features.features70 import (
            clamp_neutral_family,
            news_10d_from_context,
        )
        from nexus_scalp.features.liquidity_runtime import build_70d_vector
        from nexus_scalp.features.schema_contract import (
            feature_schema_hash,
            validate_70d_vector,
        )
        from nexus_scalp.model_generation.news_bridge import news_context_at
        from nexus_scalp.model_generation.schema_v2 import LIQUIDITY_HISTORY_LIMIT

        x50 = fv.to_tensor_input()

        news_frame = self.config.news_frame
        if news_frame is not None:
            ctx = news_context_at(news_frame, tick.timestamp)
            news10 = clamp_neutral_family(news_10d_from_context(ctx), (0.0,) * 10)
        else:
            news10 = [0.0] * 10

        liquid_bars = completed_bars[-LIQUIDITY_HISTORY_LIMIT:]
        liquid = _liquidity_features(
            liquid_bars,
            decision_at=tick.timestamp,
            mid_price=(tick.bid + tick.ask) / 2.0,
            atr=fv.atr_m1,
        )
        liq10 = list(liquid.as_vector())

        vec70 = build_70d_vector(x50, family_10=news10, liquidity_10=liq10)
        validate_70d_vector(vec70, schema_hash=feature_schema_hash(), context="streaming_replay")
        return vec70

    # ------------------------------------------------------------------
    # Simulated execution (direction-aware, historical bid/ask only)
    # ------------------------------------------------------------------

    def _fill_price(self, action: str, tick: TickEvent) -> float:
        """BUY fills at ASK, SELL fills at BID (brief §19)."""
        return tick.ask if "BUY" in action else tick.bid

    def _exit_price(self, direction: str, tick: TickEvent) -> float:
        """BUY exits at BID, SELL exits at ASK (brief §19)."""
        return tick.bid if direction == "BUY" else tick.ask

    def _pnl_usd(self, direction: str, volume: float, entry: float, exit_: float) -> float:
        """Broker-economic PnL from probed economics (§78).

        XAUUSD: contract_size=100 oz/lot => USD = delta * contract * lots
        (probe-verified: order_calc_profit BUY 0.1 lot +1.0 move = 10.0 USD).
        """
        delta = (exit_ - entry) if direction == "BUY" else (entry - exit_)
        return delta * self.config.execution.contract_size * volume

    def _clamp_volume(self, volume: float) -> float:
        ex = self.config.execution
        steps = max(1, round(volume / ex.volume_step)) if ex.volume_step > 0 else 1
        return max(ex.volume_min, steps * ex.volume_step)

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(
        self,
        source: HistoricalEventSource,
        *,
        run_id: str | None = None,
        max_events: int | None = None,
    ) -> ReplayRunResult:
        """Streams events; returns the deterministic run result.

        CHG-0043: the loop body now lives in _process_event and the result
        assembly in _finalize_run so the stepwise controller
        (research/replay_session.py) drives the IDENTICAL path. Same source +
        same config => identical event/ledger hashes (probe-verified).
        """
        rid = run_id or f"REPLAY-{datetime.now(UTC):%Y%m%d%H%M%S}-{id(self) % 100000:05d}"
        state = self._init_run_state(rid)
        for ev in self._iter_events(source, max_events):
            self._process_event(state, ev)
        result = self._finalize_run(state)
        # CHG-0043: keep the last run's bounded decision trace reachable for
        # the API layer (decision drill-down serves ENGINE truth, never a
        # chart-side recomputation). Observability only (INV-018).
        self._last_decision_trace: list[dict[str, Any]] = list(state.decision_trace)
        logger.info(
            "[STREAMING_REPLAY] event=RUN_COMPLETE",
            run_id=rid,
            experiment_type=self.config.experiment_type,
            events=result.events_seen,
            decisions=result.decisions,
            orders=len(result.orders),
            trades=len(result.trades),
            total_pnl_usd=round(result.total_pnl_usd, 4),
            ledger_hash=result.ledger_hash,
        )
        return result

    # ------------------------------------------------------------------
    # Stepwise-capable run internals (CHG-0043; behavior-preserving)
    # ------------------------------------------------------------------

    def _init_run_state(self, rid: str) -> _RunState:
        """Fresh deterministic run state (one per run / controller session)."""
        from nexus_scalp.configuration.config import RiskConfig
        from nexus_scalp.risk.risk_engine import RiskEngine

        regime_classifier = None
        if getattr(self.config, "regime_enabled", False):
            from nexus_scalp.features.regime_classifier import MarketRegimeClassifier

            regime_classifier = MarketRegimeClassifier(symbol=self.config.symbol)

        return _RunState(
            rid=rid,
            cfg_fp=self.config_fingerprint(),
            identity=self.session_identity(),
            risk_engine=RiskEngine(config=RiskConfig(risk_per_trade_pct=0.5)),
            equity=float(self.config.starting_equity_usd),
            completed=[],
            open_pos=None,
            trades=[],
            orders=[],
            data_errors=[],
            event_hasher=hashlib.sha256(),
            events_seen=0,
            decisions=0,
            last_decide_minute=None,
            first_ts="",
            last_ts="",
            last_tick=None,
            feature_engine=_FeatureEngineHolder(self.config.symbol),
            regime_classifier=regime_classifier,
        )

    def _close_position(
        self,
        state: _RunState,
        pos: _OpenPosition,
        exit_reason: str,
        exit_price: float,
        ts: datetime,
    ) -> None:
        """Single ledger owner: appends the trade + updates equity (the
        pre-refactor close_position closure, method-ized verbatim)."""
        pnl = self._pnl_usd(pos.direction, pos.volume, pos.entry_price, exit_price)
        state.trades.append(
            SimulatedTrade(
                trade_id=f"{state.rid}-T{len(state.trades) + 1:05d}",
                entry_order_id=pos.entry_order_id,
                exit_order_id=f"{state.rid}-XT{len(state.trades) + 1:05d}",
                direction=pos.direction,
                volume=pos.volume,
                entry_time=pos.entry_time,
                entry_price=pos.entry_price,
                exit_time=ts,
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl_usd=pnl,
                mae_usd=pos.mae_usd,
                mfe_usd=pos.mfe_usd,
                run_id=state.rid,
            )
        )
        state.equity += pnl
        state.open_pos = None

    def _maybe_classify_regime(self, state: _RunState, tick: TickEvent) -> None:
        """Causal regime classification at T (CHG-0043, additive).

        Runs the production MarketRegimeClassifier on the replay tick so
        regime(T) depends ONLY on information <= T. Disabled (None) keeps
        the exact pre-refactor behavior. Transition records are appended
        when the classified regime type changes.
        """
        if state.regime_classifier is None:
            return
        from nexus_scalp.domain.models import TickData

        live_tick = TickData(
            symbol=tick.symbol,
            timestamp=tick.timestamp,
            bid=tick.bid,
            ask=tick.ask,
            volume=tick.volume,
        )
        state.regime_state = state.regime_classifier.classify_tick(live_tick)
        rtype = str(
            getattr(state.regime_state.regime_type, "value", state.regime_state.regime_type)
        )
        if state.prev_regime_type and rtype != state.prev_regime_type:
            state.regime_transitions.append(
                {
                    "at": tick.timestamp.isoformat(),
                    "from": state.prev_regime_type,
                    "to": rtype,
                    "probability": float(state.regime_state.regime_probability),
                    "reason": str(
                        getattr(state.regime_state.reason, "value", state.regime_state.reason)
                    ),
                }
            )
        state.prev_regime_type = rtype

    def _trace_record(
        self,
        state: _RunState,
        tick: TickEvent,
        fv: Any,
        probs: list[float],
        proposal: Any,
        action: str,
    ) -> dict[str, Any]:
        """One bounded evidence row per decision (NO_TRADE included).

        Fields are observability-only; nothing here feeds back into the
        decision path (INV-018). Outcome fields are absent BY DESIGN:
        outcome(T) is measured strictly after the decision by the research
        layer (counterfactual engine), never during it.
        """
        is_trade = "BUY" in action or "SELL" in action
        regime_type = (
            getattr(state.regime_state.regime_type, "value", state.regime_state.regime_type)
            if state.regime_state is not None
            else None
        )
        return {
            "ts": tick.timestamp.isoformat(),
            "decision_index": state.decisions,
            "bid": tick.bid,
            "ask": tick.ask,
            "probs": [round(float(p), 6) for p in probs],
            "action": action or "NO_TRADE",
            "is_trade": is_trade,
            "confidence": float(getattr(proposal, "confidence", 0.0) or 0.0),
            "reason_code": str(getattr(proposal, "reason_code", "") or ""),
            "blocked_by": str(getattr(proposal, "blocked_by", "") or "") or None,
            "rejection_reason": str(getattr(proposal, "rejection_reason", "") or "") or None,
            "decision_stage": str(getattr(proposal, "decision_stage", "") or "") or None,
            "guardian_status": str(getattr(proposal, "guardian_status", "") or "") or None,
            "regime": regime_type,
            "regime_confidence": (
                float(state.regime_state.regime_probability)
                if state.regime_state is not None
                else None
            ),
            "atr_m1": float(getattr(fv, "atr_m1", 0.0) or 0.0),
            "spread": round(tick.ask - tick.bid, 6),
            "entry": float(getattr(proposal, "proposed_entry", 0.0) or 0.0) or None,
            "stop_loss": float(getattr(proposal, "stop_loss", 0.0) or 0.0) or None,
            "take_profit": float(getattr(proposal, "take_profit", 0.0) or 0.0) or None,
            "risk_allowed": getattr(proposal, "risk_allowed", None),
            "risk_accepted": None,
        }

    def _process_event(self, state: _RunState, ev: Any) -> None:
        """Processes ONE event through the canonical path (the pre-refactor
        run() loop body, verbatim semantics)."""
        state.events_seen += 1
        state.event_hasher.update(f"{ev.kind.value}|{getattr(ev, 'timestamp', None)}|".encode())
        if isinstance(getattr(ev, "timestamp", None), datetime):
            if state.first_ts == "":
                state.first_ts = ev.timestamp.isoformat()
            state.last_ts = ev.timestamp.isoformat()

        if isinstance(ev, DataErrorEvent):
            if len(state.data_errors) < 200:
                state.data_errors.append(
                    {
                        "reason": ev.reason,
                        "timestamp": ev.timestamp.isoformat() if ev.timestamp else "",
                        "raw_index": ev.raw_index,
                        "source": ev.source_name,
                    }
                )
            return

        if isinstance(ev, BarEvent):
            state.completed.append(
                BarData(
                    symbol=ev.symbol,
                    timeframe=ev.timeframe,
                    timestamp=ev.timestamp,
                    open=ev.open,
                    high=ev.high,
                    low=ev.low,
                    close=ev.close,
                    tick_volume=ev.tick_volume,
                    is_complete=True,
                )
            )
            # Bar-level SL/TP surveillance (conservative SL-first
            # tie-break; documented difference vs tick replay).
            if state.open_pos is not None and self.config.execution.evaluate_sl_tp_every_event:
                hit_sl = (
                    ev.low <= state.open_pos.stop_loss <= ev.high
                    if state.open_pos.direction == "BUY"
                    else ev.high >= state.open_pos.stop_loss >= ev.low
                )
                hit_tp = (
                    ev.high >= state.open_pos.take_profit >= ev.low
                    if state.open_pos.direction == "BUY"
                    else ev.low <= state.open_pos.take_profit >= ev.low
                )
                if hit_sl or hit_tp:
                    exit_reason = "SL" if (hit_sl or not hit_tp) else "TP"
                    exit_price = (
                        state.open_pos.stop_loss
                        if exit_reason == "SL"
                        else state.open_pos.take_profit
                    )
                    self._close_position(
                        state, state.open_pos, exit_reason, exit_price, ev.timestamp
                    )
            # Bar-mode decision on the synthetic close tick (dataset
            # builder convention; keeps bar/tick parity comparable).
            if self.config.decide_on == "bar_close":
                synth = TickEvent(
                    timestamp=ev.timestamp,
                    bid=ev.close,
                    ask=ev.close + BAR_MODE_SYNTHETIC_SPREAD_USD,
                    volume=float(ev.tick_volume),
                    symbol=ev.symbol,
                )
                state.last_tick = synth
                # CHG-0043 P1 FIX: bar-mode decisions are now counted (the
                # pre-refactor counter only incremented in the tick branch,
                # so bar-mode runs reported decisions=0 despite orders).
                state.decisions += 1
                self._maybe_classify_regime(state, synth)
                state.open_pos = self._decide(state, synth)
            return

        # --- TickEvent ---
        state.last_tick = ev
        if state.open_pos is not None:
            # tick-chronological SL/TP (§20) + MFE/MAE path tracking (§80)
            if state.open_pos.direction == "BUY":
                hit_sl = ev.bid <= state.open_pos.stop_loss
                hit_tp = ev.bid >= state.open_pos.take_profit
                cur_price = ev.bid
            else:
                hit_sl = ev.ask >= state.open_pos.stop_loss
                hit_tp = ev.ask <= state.open_pos.take_profit
                cur_price = ev.ask
            cur_pnl = self._pnl_usd(
                state.open_pos.direction,
                state.open_pos.volume,
                state.open_pos.entry_price,
                cur_price,
            )
            state.open_pos.mae_usd = min(state.open_pos.mae_usd, cur_pnl)
            state.open_pos.mfe_usd = max(state.open_pos.mfe_usd, cur_pnl)
            if hit_sl or hit_tp:
                exit_reason = "SL" if hit_sl else "TP"
                exit_price = (
                    state.open_pos.stop_loss if exit_reason == "SL" else state.open_pos.take_profit
                )
                self._close_position(state, state.open_pos, exit_reason, exit_price, ev.timestamp)
                return

        if self.config.decide_on == "every_tick" or self._new_minute(
            ev.timestamp, state.last_decide_minute
        ):
            state.last_decide_minute = self._minute_key(ev.timestamp)
            state.decisions += 1
            self._maybe_classify_regime(state, ev)
            state.open_pos = self._decide(state, ev)

    def _finalize_run(self, state: _RunState) -> ReplayRunResult:
        """END_OF_DATA close + deterministic result assembly (verbatim from
        the pre-refactor run() tail)."""
        # End of data: close an open position honestly at the last price
        # (exit_reason=END_OF_DATA — visible, never hidden, §76/§79).
        if state.open_pos is not None and state.last_tick is not None:
            self._close_position(
                state,
                state.open_pos,
                "END_OF_DATA",
                self._exit_price(state.open_pos.direction, state.last_tick),
                state.last_tick.timestamp,
            )

        event_hash = state.event_hasher.hexdigest()[:32]
        total_pnl = float(sum(t.pnl_usd for t in state.trades))
        return ReplayRunResult(
            run_id=state.rid,
            experiment_type=self.config.experiment_type,
            config_fingerprint=state.cfg_fp,
            model_identity=state.identity["model"],
            strategy_fingerprint=self.policy.fingerprint(),
            schema_hash=state.identity["schema_hash"],
            event_hash=event_hash,
            ledger_hash=_ledger_digest(state.trades, event_hash),
            events_seen=state.events_seen,
            decisions=state.decisions,
            orders=[_order_to_dict(o) | {"run_id": state.rid} for o in state.orders],
            trades=[t.to_dict() for t in state.trades],
            data_errors=state.data_errors,
            first_event=state.first_ts,
            last_event=state.last_ts,
            total_pnl_usd=total_pnl,
            final_equity_usd=state.equity,
            warmup_skipped_decisions=state.feature_engine.warmup_skips,
            decision_mode=self.config.decide_on,
            decision_trace=state.decision_trace,
            regime_transitions=state.regime_transitions,
        )

    # ------------------------------------------------------------------
    # Decision path
    # ------------------------------------------------------------------

    def _decide(self, state: _RunState, tick: TickEvent) -> _OpenPosition | None:
        """One decision: features -> model -> policy -> risk -> simulated fill.

        CHG-0043: state-object signature; regime_state is the CAUSAL
        classifier output at T (None when regime wiring is disabled); every
        decision appends a bounded evidence trace row (NO_TRADE included).
        """
        import torch

        from nexus_scalp.domain.models import TickData

        warmup_bars = len(state.completed)
        if warmup_bars < FEATURE_WARMUP_BARS:
            state.feature_engine.warmup_skips += 1
            return state.open_pos
        state.feature_engine.warmed = True

        live_tick = TickData(
            symbol=tick.symbol,
            timestamp=tick.timestamp,
            bid=tick.bid,
            ask=tick.ask,
            volume=tick.volume,
        )
        fv = state.feature_engine.compute(state.completed, live_tick)
        try:
            vec70 = self._assemble_vector(state.completed, tick, fv)
        except Exception as e:  # contract violation is a hard data problem
            logger.error(
                "[STREAMING_REPLAY] event=FEATURE_ASSEMBLY_FAILED",
                run_id=state.rid,
                ts=tick.timestamp.isoformat(),
                error=str(e),
            )
            raise

        probs = self._predict_probs(vec70)
        probs_t = torch.tensor([probs], dtype=torch.float32)
        proposal = self.policy.evaluate(probs_t, live_tick, fv, regime_state=state.regime_state)

        action = str(getattr(proposal, "action", ""))
        # CHG-0043 additive: bounded per-decision evidence trace.
        if len(state.decision_trace) < DECISION_TRACE_LIMIT:
            state.decision_trace.append(
                self._trace_record(state, tick, fv, probs, proposal, action)
            )

        if "BUY" not in action and "SELL" not in action:
            return state.open_pos

        # --- Risk gate (production RiskEngine semantics; replay state only) ---
        order = self._risk_gate(proposal, tick, state.equity, state.risk_engine, state.regime_state)
        if order is None:
            if state.decision_trace:
                last = state.decision_trace[-1]
                last["risk_accepted"] = False
                last["blocked_by"] = "RISK_ENGINE"
            return state.open_pos
        if state.decision_trace:
            last = state.decision_trace[-1]
            last["risk_accepted"] = True
            last["order_id"] = f"{state.rid}-O{len(state.orders) + 1:05d}"

        fill = self._fill_price(action, tick)
        sim_order = SimulatedOrder(
            order_id=f"{state.rid}-O{len(state.orders) + 1:05d}",
            signal_time=tick.timestamp,
            decision_time=tick.timestamp,
            order_time=tick.timestamp,
            fill_time=tick.timestamp,
            action=action,
            order_type="MARKET",
            volume=float(order.volume),
            requested_price=float(order.price),
            fill_price=fill,
            stop_loss=float(order.stop_loss),
            take_profit=float(order.take_profit),
            run_id=state.rid,
        )
        state.orders.append(sim_order)

        if state.open_pos is not None:
            same_dir = (state.open_pos.direction == "BUY" and "BUY" in action) or (
                state.open_pos.direction == "SELL" and "SELL" in action
            )
            if same_dir:
                return state.open_pos
            # reversal: close at market (SIGNAL_REVERSAL) then open opposite.
            # _close_position appends the ledger trade + updates equity in the
            # run loop (single ledger owner — no duplicate accounting).
            exit_px = self._exit_price(state.open_pos.direction, tick)
            self._close_position(state, state.open_pos, "SIGNAL_REVERSAL", exit_px, tick.timestamp)
            state.open_pos = None

        return _OpenPosition(
            direction="BUY" if "BUY" in action else "SELL",
            volume=self._clamp_volume(float(order.volume)),
            entry_order_id=sim_order.order_id,
            entry_time=tick.timestamp,
            entry_price=fill,
            stop_loss=float(order.stop_loss),
            take_profit=float(order.take_profit),
        )

    def _risk_gate(
        self,
        proposal: Any,
        tick: TickEvent,
        equity: float,
        risk_engine: Any,
        regime_state: Any = None,
    ) -> Any | None:
        """Production RiskEngine.evaluate_proposal with replay state only."""
        from nexus_scalp.domain.models import AccountInfo, SymbolInfo

        account = AccountInfo(
            login=0,
            trade_mode=0,
            leverage=100,
            balance=equity,
            equity=equity,
            margin=0.0,
            margin_free=equity,
        )
        symbol_info = SymbolInfo(
            symbol=tick.symbol,
            digits=2,
            point=self.config.execution.tick_size,
            tick_size=self.config.execution.tick_size,
            tick_value=self.config.execution.tick_value,
            volume_min=self.config.execution.volume_min,
            volume_max=10.0,
            volume_step=self.config.execution.volume_step,
            stops_level=0,
            freeze_level=0,
            trade_contract_size=self.config.execution.contract_size,
        )
        return risk_engine.evaluate_proposal(
            proposal,
            account=account,
            symbol_info=symbol_info,
            active_positions=[],
            current_tick=tick,
            regime_state=regime_state,
            atr=1.5,
            pending_orders=[],
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _iter_events(self, source: HistoricalEventSource, max_events: int | None) -> Any:
        count = 0
        for ev in source.events():
            if max_events is not None and count >= max_events:
                break
            count += 1
            yield ev

    @staticmethod
    def _minute_key(ts: datetime) -> tuple[int, int, int, int, int]:
        return (ts.year, ts.month, ts.day, ts.hour, ts.minute)

    def _new_minute(self, ts: datetime, last: tuple[int, int, int, int, int] | None) -> bool:
        return last is None or self._minute_key(ts) != last


class _FeatureEngineHolder:
    """One ScalpFeatureEngine per run session (features are pure/stateless
    per call; the holder exists to avoid re-construction per decision)."""

    def __init__(self, symbol: str) -> None:
        from nexus_scalp.features.scalp_features import ScalpFeatureEngine

        self._engine = ScalpFeatureEngine(symbol=symbol)
        self.warmup_skips: int = 0
        self.warmed: bool = False

    def compute(self, completed_bars: list[BarData], tick: Any) -> Any:
        return self._engine.compute_from_bars(list(completed_bars), tick)


def _liquidity_features(
    bars: list[BarData], *, decision_at: datetime, mid_price: float, atr: float
) -> Any:
    from nexus_scalp.features.liquidity_engine import compute_liquidity_features

    return compute_liquidity_features(bars, decision_at=decision_at, mid_price=mid_price, atr=atr)
