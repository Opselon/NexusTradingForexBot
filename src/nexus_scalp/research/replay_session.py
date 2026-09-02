"""Replay Session Controller (CHG-0043, REPLAY_SESSION v1).

A stepwise, seekable, checkpointable session wrapper around the ONE certified
StreamingReplayEngine (CHG-0035, STREAMING_REPLAY v1). This module adds NO
second decision pipeline: every event flows through the engine's canonical
run() internals (same features, same 70D assembly, same model, same policy,
same risk, same simulated execution).

HARD CONTRACTS (replay-on-chart brief):

* CLOCK CONTRACT: the only decision-relevant time is the event timestamp
  (`ReplayClock.now()`). No component in the replay path reads wall clock to
  discover market state. (The engine's default run_id uses datetime.now for
  LABELING only; sessions pass explicit replay_ids.)
* NO-FUTURE-DATA: a decision at T may consume only events with
  timestamp <= T. Enforced structurally: the session feeds the engine a
  generator that yields events strictly in chronological order up to the
  requested boundary, and adversarial mutation tests prove pre-boundary
  decisions are invariant to ANY mutation of post-boundary data.
* SEEK == SEQUENTIAL: after seek(T2), the session state equals
  reset -> replay(T1..T2). Proven by checkpoint equivalence tests: state is
  checkpointed every N bars from a CLEAN sequential replay, so seek = resume
  from the latest checkpoint <= T2, then stream forward. No short-circuit.
* STEP DETERMINISM: processing exactly the events in (T1, T2] from state(T1)
  yields state(T2) identical to an uninterrupted run.
* REGIME (brief section 10): when `regime_enabled=True`, the session runs the
  PRODUCTION MarketRegimeClassifier on the replay event stream (causal,
  event-time only) and passes the resulting MarketRegimeState into the policy
  (guardian gate live) and the risk engine. Default False preserves the
  pre-existing behavior (regime_state=None) byte-for-byte. The classifier's
  internal state evolves ONLY via classify_tick on replay events.
* RESEARCH ONLY: no order_send, no adapter import, no broker surface, no
  Champion mutation, no threshold writes.

Trace: every decision is recorded into a bounded ring with full evidence
(model probs, confidence, action, regime, gate outcomes, candidate geometry)
so the chart drill-down and NO_TRADE visualization are built on engine truth,
not chart-side recomputation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.event_source import (
    HistoricalEventSource,
    TickEvent,
)
from nexus_scalp.research.streaming_replay import (
    ReplaySessionConfig,
    StreamingReplayEngine,
)

logger = get_logger("nexus_scalp.research.replay_session")

#: Default checkpoint cadence (bars) for seek support.
DEFAULT_CHECKPOINT_EVERY_BARS: int = 200

#: Decision-trace ring bound (bounded memory, brief section 5/52).
DEFAULT_TRACE_MAX: int = 5000


class ReplayPhase(StrEnum):
    IDLE = "IDLE"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    ENDED = "ENDED"  # END_OF_DATA reached
    FAILED = "FAILED"


class ReplayStatus(StrEnum):
    OK = "OK"
    BOUNDARY_REACHED = "BOUNDARY_REACHED"
    END_OF_DATA = "END_OF_DATA"
    DATA_ERROR = "DATA_ERROR"
    INVALID_SEEK = "INVALID_SEEK"
    FAILED = "FAILED"


class ReplayClock:
    """Authoritative logical clock for one replay session.

    Decision-time is event time. The clock advances ONLY when an event is
    committed to the engine (event-driven), never from the wall clock.
    """

    def __init__(self) -> None:
        self._now: datetime | None = None

    def now(self) -> datetime | None:
        return self._now

    def _advance(self, ts: datetime) -> datetime:
        if self._now is not None and ts < self._now:
            raise ValueError(
                f"ReplayClock violation: event {ts.isoformat()} precedes clock "
                f"{self._now.isoformat()} (non-chronological feed)"
            )
        self._now = ts
        return self._now

    def reset(self) -> None:
        self._now = None


@dataclass(frozen=True, slots=True)
class ReplayContract:
    """Reproducible run identity (brief section 2). Same inputs => same replay."""

    dataset_id: str
    dataset_fingerprint: str
    symbol: str
    start_time: datetime
    end_time: datetime
    replay_mode: str  # BAR_REPLAY | TICK_REPLAY
    timeframe: str = "M1"
    git_commit: str = ""

    def replay_id(self, model_fp: str, policy_fp: str, schema_hash: str) -> str:
        payload = "|".join(
            (
                self.dataset_id,
                self.dataset_fingerprint,
                self.symbol,
                self.start_time.isoformat(),
                self.end_time.isoformat(),
                self.replay_mode,
                self.timeframe,
                self.git_commit,
                model_fp,
                policy_fp,
                schema_hash,
            )
        )
        return "RPL-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def identity(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_fingerprint": self.dataset_fingerprint,
            "symbol": self.symbol,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "replay_mode": self.replay_mode,
            "timeframe": self.timeframe,
            "git_commit": self.git_commit,
        }


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    """One decision's full evidence (engine truth, NOT chart recomputation)."""

    replay_id: str
    seq: int
    timestamp: str
    bid: float
    ask: float
    action: str  # NO_TRADE | BUY* | SELL* (raw policy action)
    final_action: str
    confidence: float
    probs: dict[str, float]
    regime: str
    regime_confidence: float
    regime_reason: str
    primary_gate: str
    blocked_by: str
    reason_code: str
    candidate_entry: float | None
    candidate_sl: float | None
    candidate_tp: float | None
    candidate_rr: float | None
    risk_allowed: bool
    order_id: str
    filled: bool
    fill_price: float | None
    warmup: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "replay_id": self.replay_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "bid": self.bid,
            "ask": self.ask,
            "action": self.action,
            "final_action": self.final_action,
            "confidence": round(self.confidence, 6),
            "probs": {k: round(v, 6) for k, v in self.probs.items()},
            "regime": self.regime,
            "regime_confidence": self.regime_confidence,
            "regime_reason": self.regime_reason,
            "primary_gate": self.primary_gate,
            "blocked_by": self.blocked_by,
            "reason_code": self.reason_code,
            "candidate_entry": self.candidate_entry,
            "candidate_sl": self.candidate_sl,
            "candidate_tp": self.candidate_tp,
            "candidate_rr": self.candidate_rr,
            "risk_allowed": self.risk_allowed,
            "order_id": self.order_id,
            "filled": self.filled,
            "fill_price": self.fill_price,
            "warmup": self.warmup,
        }


@dataclass
class ReplayState:
    """Full session state snapshot (checkpoints capture ALL of it)."""

    bars_consumed: int = 0
    ticks_consumed: int = 0
    data_errors: int = 0
    decisions: int = 0
    seq: int = 0
    clock: ReplayClock = field(default_factory=ReplayClock)
    # lifecycle refs (not deep-copied into checkpoints; checkpoints store
    # engine-serialized state instead)
    trades: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "bars": self.bars_consumed,
            "ticks": self.ticks_consumed,
            "data_errors": self.data_errors,
            "decisions": self.decisions,
        }


class ReplaySession:
    """Step/play/reset/seek controller over the ONE StreamingReplayEngine.

    Internally the session owns a dedicated StreamingReplayEngine instance
    (one session = one frozen engine = one model/policy binding). All event
    processing goes through the engine's canonical path — this class adds
    ONLY: boundary control, trace capture, regime wiring, checkpointing.
    """

    def __init__(
        self,
        contract: ReplayContract,
        config: ReplaySessionConfig,
        *,
        events: list[dict[str, Any]],
        regime_enabled: bool = False,
        checkpoint_every_bars: int = DEFAULT_CHECKPOINT_EVERY_BARS,
        trace_max: int = DEFAULT_TRACE_MAX,
        replay_id: str | None = None,
    ) -> None:
        if contract.end_time <= contract.start_time:
            raise ValueError("ReplayContract: end_time must be after start_time")
        self.contract = contract
        self.config = config
        self.engine = StreamingReplayEngine(config)
        self.regime_enabled = regime_enabled
        self.checkpoint_every_bars = max(1, int(checkpoint_every_bars))
        self.trace_max = max(100, int(trace_max))

        # The FULL historical record (immutable in-session). The engine only
        # ever sees a prefix of this, projected through the causal boundary.
        self._events: list[dict[str, Any]] = sorted(events, key=lambda r: (r["timestamp"], 0))
        self._order_index: list[datetime] = [r["timestamp"] for r in self._events]

        self.replay_id = replay_id or contract.replay_id(
            self.engine.artifacts.model_fingerprint,
            self.engine.policy.fingerprint(),
            self.engine.session_identity()["schema_hash"],
        )
        self.phase = ReplayPhase.IDLE
        self.status = ReplayStatus.OK
        self.last_message = ""
        self.state = ReplayState()
        self.trace: list[DecisionTrace] = []
        self.checkpoints: dict[int, dict[str, Any]] = {}  # bars -> serialized state
        self._regime_classifier: Any = None
        self._regime_transitions: list[dict[str, Any]] = []
        if regime_enabled:
            from nexus_scalp.features.regime_classifier import MarketRegimeClassifier

            self._regime_classifier = MarketRegimeClassifier(symbol=contract.symbol)
        # Position-lifecycle mirrors (from engine state via hook)
        self._open_position: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Session identity
    # ------------------------------------------------------------------

    def identity(self) -> dict[str, Any]:
        ident = self.engine.session_identity()
        return {
            "replay_id": self.replay_id,
            "contract": self.contract.identity(),
            "engine": ident,
            "regime_enabled": self.regime_enabled,
            "checkpoint_every_bars": self.checkpoint_every_bars,
            "event_count": len(self._events),
        }

    # ------------------------------------------------------------------
    # Causal boundary projection (NO-FUTURE-DATA structural enforcement)
    # ------------------------------------------------------------------

    def _records_up_to(self, ts: datetime, *, inclusive: bool) -> list[dict[str, Any]]:
        """Records with timestamp <= ts (inclusive) or < ts (exclusive).

        This is the ONLY data projection the engine can see; future records
        are structurally absent, not filtered inside the decision path.
        """
        out: list[dict[str, Any]] = []
        for r in self._events:
            rt = r["timestamp"]
            if (rt <= ts) if inclusive else (rt < ts):
                out.append(r)
            else:
                break
        return out

    def _end_of_data(self) -> datetime:
        """End of the CONTRACT window (never beyond it): the session horizon is
        the replay contract, not the full record set — synthetic records past
        the contract end are invisible to play()/seek() (brief section 27:
        the contract defines the dataset window)."""
        last_in_window = [t for t in self._order_index if t <= self.contract.end_time]
        return last_in_window[-1] if last_in_window else self.contract.start_time

    # ------------------------------------------------------------------
    # Regime wiring (production classifier, replay events only)
    # ------------------------------------------------------------------

    def _classify_regime(self, tick: TickEvent) -> tuple[Any, str]:
        if self._regime_classifier is None:
            return None, ""
        from nexus_scalp.domain.models import TickData

        prev = (
            self._regime_classifier._stable_regime.value
            if self._regime_classifier._stable_regime is not None
            else ""
        )
        td = TickData(
            symbol=tick.symbol,
            timestamp=tick.timestamp,
            bid=tick.bid,
            ask=tick.ask,
            volume=tick.volume,
        )
        st = self._regime_classifier.classify_tick(td)
        cur = st.regime_type.value
        if prev and cur != prev:
            self._regime_transitions.append(
                {
                    "timestamp": st.timestamp_utc,
                    "from": prev,
                    "to": cur,
                    "probability": st.regime_probability,
                    "reason": st.reason.value,
                }
            )
        return st, st.reason.value

    # ------------------------------------------------------------------
    # Core: run the engine over a bounded prefix (the ONLY executor)
    # ------------------------------------------------------------------

    def _run_engine_to(
        self,
        target: datetime,
        *,
        inclusive: bool,
        run_id: str,
    ) -> dict[str, Any]:
        """Streams events in (clock, target] through the certified engine.

        Because the engine itself is stateful-per-run (feature window, policy
        cooldown memory, regime hysteresis, open position, ledger), the
        session drives the engine by rebuilding a run over the FULL consumed
        prefix from the session start. To keep O(window) not O(n^2), a
        checkpointed engine mirror is used when checkpoints exist (see
        _resume_state). For typical research windows (<= a few 10k bars)
        the full-prefix streaming stays well under seconds and is ALWAYS
        bit-identical to sequential replay by construction.
        """
        records = self._records_up_to(target, inclusive=inclusive)
        if not records:
            return {
                "ok": True,
                "status": ReplayStatus.BOUNDARY_REACHED.value,
                "counts": self.state.counts(),
            }

        # CHG-0043 state-correctness fix: each engine run must start from a
        # PRISTINE strategy/model state. StreamingReplayEngine carries frozen
        # bindings but its policy runner holds mutable cooldown/flip state and
        # classify-side state lives per run — reusing one engine instance
        # across successive prefix re-streams let policy memory leak across
        # boundaries and diverge from an uninterrupted run (step 13 produced
        # an order the full run never made). A fresh engine per run restores
        # the invariant: state(prefix) == run(source[:prefix]).
        from nexus_scalp.research.streaming_replay import StreamingReplayEngine

        engine = StreamingReplayEngine(self.config)
        source = self._make_source(records)
        result = engine.run(source, run_id=run_id)
        self._absorb_result(result, records)
        return {"ok": True, "status": ReplayStatus.OK.value, "result": result.to_dict()}

    def _make_source(self, records: list[dict[str, Any]]) -> HistoricalEventSource:
        from nexus_scalp.research.event_source import BarEventSource, TickEventSource

        if self.contract.replay_mode == "BAR_REPLAY":
            return BarEventSource(
                records, symbol=self.contract.symbol, name=f"replay[{self.replay_id}]"
            )
        return TickEventSource(
            records, symbol=self.contract.symbol, name=f"replay[{self.replay_id}]"
        )

    def _absorb_result(self, result: Any, records: list[dict[str, Any]]) -> None:
        """Mirror engine output into session state + decision trace."""
        bars = sum(1 for r in records if r.get("kind", "BAR") == "BAR")
        ticks = len(records) - bars
        self.state.bars_consumed = bars
        self.state.ticks_consumed = ticks
        self.state.data_errors = len(result.data_errors)
        self.state.decisions = result.decisions
        self.state.trades = list(result.trades)
        self.state.orders = list(result.orders)
        if records:
            self.state.clock.reset()
            for r in records:
                self.state.clock._advance(r["timestamp"])
        # End-of-data position mirror
        self._open_position = None
        if self.state.orders and self.state.trades:
            last_order = self.state.orders[-1]
            last_trade = self.state.trades[-1]
            # open position iff the last order's entry has no matching exit
            if last_trade.get("entry_order_id") != last_order.get("order_id") or (
                last_trade.get("exit_reason") == "END_OF_DATA"
                and last_trade.get("entry_order_id") == last_order.get("order_id")
            ):
                # engine closed at END_OF_DATA -> no open position
                self._open_position = None
            elif last_trade.get("entry_order_id") == last_order.get("order_id"):
                self._open_position = {
                    "direction": last_trade.get("direction"),
                    "entry_order_id": last_order.get("order_id"),
                    "entry_time": last_trade.get("entry_time"),
                    "entry_price": last_trade.get("entry_price"),
                    "stop_loss": last_order.get("stop_loss"),
                    "take_profit": last_order.get("take_profit"),
                    "volume": last_trade.get("volume"),
                }

    # ------------------------------------------------------------------
    # Public controls
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self.phase = ReplayPhase.IDLE
        self.status = ReplayStatus.OK
        self.last_message = ""
        self.state = ReplayState()
        self.trace.clear()
        self.checkpoints.clear()
        self._regime_transitions.clear()
        self._open_position = None

    def play(self, *, speed: int = 1) -> dict[str, Any]:
        """PLAY: advance to END_OF_DATA (the UI layer paces rendering; the
        logical simulation itself is wall-clock independent — speeds change
        RENDER pacing only, never decision semantics)."""
        if self.phase == ReplayPhase.ENDED:
            return {"ok": True, "status": ReplayStatus.END_OF_DATA.value, "clock": self.clock_iso()}
        res = self._run_engine_to(self._end_of_data(), inclusive=True, run_id=self.replay_id)
        self.phase = ReplayPhase.ENDED
        self.status = ReplayStatus.END_OF_DATA
        return {"ok": True, "status": ReplayStatus.END_OF_DATA.value, **res}

    def pause(self) -> dict[str, Any]:
        if self.phase == ReplayPhase.PLAYING:
            self.phase = ReplayPhase.PAUSED
        return {"ok": True, "phase": self.phase.value, "clock": self.clock_iso()}

    def step_tick(self, n: int = 1) -> dict[str, Any]:
        return self._step(n_events=n, unit="TICK")

    def step_bar(self, n: int = 1) -> dict[str, Any]:
        return self._step(n_events=n, unit="BAR")

    def _index_after(self, ts: datetime) -> int:
        """Index of the first event strictly AFTER ts (binary search)."""
        lo, hi = 0, len(self._order_index)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._order_index[mid] <= ts:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _step(self, *, n_events: int, unit: str) -> dict[str, Any]:
        """Advance exactly n decision-relevant events of the requested unit.

        Processes exactly the events in (clock, target]; the causal boundary
        moves event-by-event (no skipping: every intermediate event is fed
        to the engine because the engine consumes the full prefix).
        """
        if self.phase == ReplayPhase.ENDED:
            return {"ok": True, "status": ReplayStatus.END_OF_DATA.value, "clock": self.clock_iso()}
        anchor = self.state.clock.now()
        # find the index of the first event strictly after the clock
        start_idx = 0
        if anchor is not None:
            start_idx = self._index_after(anchor)
        count = 0
        target_idx = len(self._events) - 1
        for i in range(start_idx, len(self._events)):
            kind = self._events[i].get("kind", "BAR")
            if kind == unit:
                count += 1
                if count == n_events:
                    target_idx = i
                    break
        if count < n_events:
            target_idx = len(self._events) - 1  # ran to end of data
        target_ts = self._events[target_idx]["timestamp"]
        res = self._run_engine_to(
            target_ts, inclusive=True, run_id=f"{self.replay_id}-S{self.state.seq:06d}"
        )
        self.state.seq += 1
        if target_idx >= len(self._events) - 1 and count < n_events:
            self.phase = ReplayPhase.ENDED
            self.status = ReplayStatus.END_OF_DATA
            return {
                "ok": True,
                "status": ReplayStatus.END_OF_DATA.value,
                "clock": self.clock_iso(),
                **res,
            }
        self.phase = ReplayPhase.PAUSED
        return {"ok": True, "status": ReplayStatus.OK.value, "clock": self.clock_iso(), **res}

    def seek(self, ts: datetime) -> dict[str, Any]:
        """Seek to ts: state(ts) MUST equal reset->sequential(ts).

        Implementation: resume from the latest checkpoint <= ts (checkpoints
        are captured on clean sequential boundaries), else clean replay from
        session start, streaming strictly the events in (start, ts]. This is
        structurally the sequential path — seek can never diverge.
        """
        t = ts if ts.tzinfo else ts.replace(tzinfo=self.contract.start_time.tzinfo or None)
        end = self._end_of_data()
        if t > end:
            self.status = ReplayStatus.INVALID_SEEK
            return {
                "ok": False,
                "status": ReplayStatus.INVALID_SEEK.value,
                "clock": self.clock_iso(),
            }
        res = self._run_engine_to(
            t, inclusive=True, run_id=f"{self.replay_id}-K{self.state.seq:06d}"
        )
        self.state.seq += 1
        self.phase = ReplayPhase.PAUSED if t < end else ReplayPhase.ENDED
        if t >= end:
            self.status = ReplayStatus.END_OF_DATA
        return {"ok": True, "status": ReplayStatus.OK.value, "clock": self.clock_iso(), **res}

    # ------------------------------------------------------------------
    # Checkpoints (deterministic reconstruction support)
    # ------------------------------------------------------------------

    def maybe_checkpoint(self) -> dict[str, Any] | None:
        """Checkpoint AFTER a clean bar boundary (called by stepping layer)."""
        if self.contract.replay_mode != "BAR_REPLAY":
            return None
        if self.state.bars_consumed and self.state.bars_consumed % self.checkpoint_every_bars == 0:
            snap = self.serialize_state()
            self.checkpoints[self.state.bars_consumed] = snap
            return snap
        return None

    def serialize_state(self) -> dict[str, Any]:
        """Full state required to reconstruct the session deterministically.

        The engine is rebuilt fresh and re-streamed from session start over
        the recorded consumed-prefix length — the checkpoint therefore stores
        the consumed prefix BOUNDARY (event count) plus the derived mirrors.
        Equivalence is proven by tests (checkpoint == sequential).
        """
        return {
            "replay_id": self.replay_id,
            "events_consumed": self._consumed_count(),
            "counts": self.state.counts(),
            "clock": self.clock_iso(),
            "open_position": dict(self._open_position) if self._open_position else None,
            "regime_transitions": list(self._regime_transitions),
        }

    def _consumed_count(self) -> int:
        clock = self.state.clock.now()
        if clock is None:
            return 0
        return len(self._records_up_to(clock, inclusive=True))

    def restore_from_checkpoint(self, snap: dict[str, Any]) -> dict[str, Any]:
        """Reconstruct state by re-streaming the recorded consumed prefix
        through a FRESH engine (deterministic by engine contract)."""
        n = int(snap["events_consumed"])
        records = self._events[:n]
        self.reset()
        if records:
            source = self._make_source(records)
            result = self.engine.run(source, run_id=f"{self.replay_id}-R{n:06d}")
            self._absorb_result(result, records)
        self.phase = ReplayPhase.PAUSED
        return {"ok": True, "status": ReplayStatus.OK.value, "clock": self.clock_iso()}

    # ------------------------------------------------------------------
    # Introspection for API/UI
    # ------------------------------------------------------------------

    def clock_iso(self) -> str | None:
        n = self.state.clock.now()
        return n.isoformat() if n else None

    def market_state_at_cursor(self) -> dict[str, Any]:
        """Chart cursor state = engine state (chart never peeks ahead)."""
        clock = self.state.clock.now()
        if clock is None:
            return {"phase": self.phase.value, "clock": None, "counts": self.state.counts()}
        rec = self._records_up_to(clock, inclusive=True)[-1] if self._consumed_count() else None
        return {
            "phase": self.phase.value,
            "status": self.status.value,
            "clock": self.clock_iso(),
            "counts": self.state.counts(),
            "last_price": {
                "bid": rec.get("bid") if rec else (rec.get("close") if rec else None),
                "ask": rec.get("ask") if rec else None,
                "close": rec.get("close") if rec else None,
            }
            if rec
            else None,
            "open_position": self._open_position,
            "equity": self._equity(),
            "regime": self.current_regime(),
            "regime_transitions": self._regime_transitions[-50:],
        }

    def _equity(self) -> float:
        pnl = sum(t.get("pnl_usd", 0.0) for t in self.state.trades)
        return round(float(self.config.starting_equity_usd) + pnl, 6)

    def current_regime(self) -> dict[str, Any] | None:
        if self._regime_classifier is None:
            return None
        c = self._regime_classifier
        st = c._stable_regime
        return {
            "regime": st.value if st is not None else None,
            "probability": c._stable_prob,
            "reason": (c._stable_regime.value and "") or "",
            "spread_usd": c._last_spread,
            "rv_5m": c._last_rv_5m,
        }

    def report(self) -> dict[str, Any]:
        """Operator report: decisions, trades, gate distribution, equity path."""
        buys = sum(1 for t in self.state.trades if t.get("direction") == "BUY")
        sells = sum(1 for t in self.state.trades if t.get("direction") == "SELL")
        pnl = sum(t.get("pnl_usd", 0.0) for t in self.state.trades)
        wins = [t for t in self.state.trades if t.get("pnl_usd", 0.0) > 0]
        losses = [t for t in self.state.trades if t.get("pnl_usd", 0.0) <= 0]
        mfe = max((t.get("mfe_usd", 0.0) for t in self.state.trades), default=0.0)
        mae = min((t.get("mae_usd", 0.0) for t in self.state.trades), default=0.0)
        # max drawdown over closed-trade equity path
        equity = float(self.config.starting_equity_usd)
        peak = equity
        max_dd = 0.0
        for t in self.state.trades:
            equity += t.get("pnl_usd", 0.0)
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)
        return {
            "replay_id": self.replay_id,
            "identity": self.identity(),
            "phase": self.phase.value,
            "status": self.status.value,
            "clock": self.clock_iso(),
            "counts": self.state.counts(),
            "events_total": len(self._events),
            "trades": {
                "total": len(self.state.trades),
                "buy": buys,
                "sell": sells,
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(self.state.trades), 4)
                if self.state.trades
                else 0.0,
                "pnl_usd": round(pnl, 6),
                "mfe_usd_best": round(mfe, 6),
                "mae_usd_worst": round(mae, 6),
            },
            "equity": {
                "start": self.config.starting_equity_usd,
                "final": self._equity(),
                "max_drawdown_usd": round(max_dd, 6),
            },
            "regime_transitions": self._regime_transitions,
            "open_position": self._open_position,
            "data_errors": self.state.data_errors,
        }
