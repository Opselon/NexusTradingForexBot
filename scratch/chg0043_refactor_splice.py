"""CHG-0043 surgical refactor splice for research/streaming_replay.py.

Behavior-preserving extraction: run() loop body -> _process_event/_finalize_run
on a shared _RunState so the stepwise controller (replay_session.py) drives the
IDENTICAL path. Additive: causal regime wiring (default OFF) + decision trace.
Byte-safe: reads/writes with newline='' preserved; new blocks adopt the file EOL.
"""
from __future__ import annotations

from pathlib import Path

P = Path("src/nexus_scalp/research/streaming_replay.py")
with open(P, "r", encoding="utf-8", newline="") as fh:
    s = fh.read()
EOL = "\r\n" if "\r\n" in s else "\n"


def N(block: str) -> str:
    """Convert a LF-authored block to the file's EOL."""
    return block.replace("\n", EOL) if EOL != "\n" else block


def splice(old: str, new: str, *, count: int = 1) -> None:
    global s
    old_f = N(old)
    n = s.count(old_f)
    assert n == count, f"anchor count {n} != {count}: {old[:80]!r}"
    s = s.replace(old_f, N(new), count)


# --- A1: module constant ------------------------------------------------------
splice(
    'BAR_MODE_SYNTHETIC_SPREAD_USD: float = 0.20\n',
    'BAR_MODE_SYNTHETIC_SPREAD_USD: float = 0.20\n'
    '\n'
    '#: CHG-0043: bounded per-decision evidence trace size (observability only;\n'
    '#: never feeds back into the decision path — INV-018).\n'
    'DECISION_TRACE_LIMIT: int = 5000\n',
)

# --- A2: ReplaySessionConfig field -------------------------------------------
splice(
    '    git_commit: str = ""\n    starting_equity_usd: float = 10_000.0\n',
    '    git_commit: str = ""\n    starting_equity_usd: float = 10_000.0\n'
    '    #: CHG-0043 (additive): causal regime wiring. False (default) keeps the\n'
    '    #: exact pre-refactor behavior (regime_state=None everywhere); True runs\n'
    '    #: the production MarketRegimeClassifier causally on replay ticks.\n'
    '    regime_enabled: bool = False\n',
)

# --- A3: identity() includes the new flag -------------------------------------
splice(
    '            "decide_on": self.decide_on,\n'
    '            "git_commit": self.git_commit,\n'
    '            "starting_equity_usd": self.starting_equity_usd,\n'
    '        }\n',
    '            "decide_on": self.decide_on,\n'
    '            "git_commit": self.git_commit,\n'
    '            "starting_equity_usd": self.starting_equity_usd,\n'
    '            "regime_enabled": self.regime_enabled,\n'
    '        }\n',
)

# --- A4: ReplayRunResult additive fields --------------------------------------
splice(
    '    warmup_skipped_decisions: int\n'
    '    decision_mode: str\n'
    '\n'
    '    def to_dict(self) -> dict[str, Any]:\n',
    '    warmup_skipped_decisions: int\n'
    '    decision_mode: str\n'
    '    #: CHG-0043 (additive): bounded per-decision evidence trace + regime\n'
    '    #: transition records. Empty unless the caller enables tracing/regime.\n'
    '    decision_trace: list[dict[str, Any]] = field(default_factory=list)\n'
    '    regime_transitions: list[dict[str, Any]] = field(default_factory=list)\n'
    '\n'
    '    def to_dict(self) -> dict[str, Any]:\n',
)

# --- A5: to_dict carries the new fields ---------------------------------------
splice(
    '            "warmup_skipped_decisions": self.warmup_skipped_decisions,\n'
    '            "decision_mode": self.decision_mode,\n',
    '            "warmup_skipped_decisions": self.warmup_skipped_decisions,\n'
    '            "decision_mode": self.decision_mode,\n'
    '            "decision_trace": self.decision_trace,\n'
    '            "regime_transitions": self.regime_transitions,\n'
)

# --- A6: FrozenPolicyRunner.evaluate regime pass-through ----------------------
splice(
    '    def evaluate(self, probs_tensor: Any, tick: Any, fv: Any) -> Any:\n'
    '        return self.policy.evaluate_probabilities(\n'
    '            probs_tensor,\n'
    '            current_tick=tick,\n'
    '            feature_vector=fv,\n'
    '            regime_state=None,\n'
    '        )\n',
    '    def evaluate(\n'
    '        self, probs_tensor: Any, tick: Any, fv: Any, regime_state: Any = None\n'
    '    ) -> Any:\n'
    '        """Same production policy semantics; regime_state is the CAUSAL\n'
    '        classifier output at T (None keeps the historical behavior)."""\n'
    '        return self.policy.evaluate_probabilities(\n'
    '            probs_tensor,\n'
    '            current_tick=tick,\n'
    '            feature_vector=fv,\n'
    '            regime_state=regime_state,\n'
    '        )\n',
)

# --- A7: _RunState dataclass before the engine class --------------------------
RUN_STATE = '''
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
    feature_engine: "_FeatureEngineHolder"
    #: CHG-0043 additive: causal regime classifier (None = disabled; the
    #: default keeps byte-identical behavior with pre-refactor runs).
    regime_classifier: Any | None = None
    regime_state: Any | None = None
    prev_regime_type: str = ""
    decision_trace: list[dict[str, Any]] = field(default_factory=list)
    regime_transitions: list[dict[str, Any]] = field(default_factory=list)


'''
idx = s.index("class StreamingReplayEngine:")
s = s[:idx] + N(RUN_STATE) + s[idx:]

# --- A8: replace run() with run + step-capable internals ----------------------
NEW_RUN = '''    def run(
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
        state.event_hasher.update(
            f"{ev.kind.value}|{getattr(ev, 'timestamp', None)}|".encode()
        )
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
            if (
                state.open_pos is not None
                and self.config.execution.evaluate_sl_tp_every_event
            ):
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
                    state.open_pos.stop_loss
                    if exit_reason == "SL"
                    else state.open_pos.take_profit
                )
                self._close_position(
                    state, state.open_pos, exit_reason, exit_price, ev.timestamp
                )
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
            orders=[o.__dict__ | {"run_id": state.rid} for o in state.orders],
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

'''

start = s.index("    def run(")
idx_dp = s.index("# Decision path")
sep_start = s.rfind("    # ---", 0, idx_dp)
assert 0 < start < sep_start, (start, sep_start)
s = s[:start] + N(NEW_RUN) + s[sep_start:]

# --- A9: replace _decide + _risk_gate -----------------------------------------
NEW_DECIDE = '''    def _decide(self, state: _RunState, tick: TickEvent) -> _OpenPosition | None:
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
        proposal = self.policy.evaluate(
            probs_t, live_tick, fv, regime_state=state.regime_state
        )

        action = str(getattr(proposal, "action", ""))
        # CHG-0043 additive: bounded per-decision evidence trace.
        if len(state.decision_trace) < DECISION_TRACE_LIMIT:
            state.decision_trace.append(
                self._trace_record(state, tick, fv, probs, proposal, action)
            )

        if "BUY" not in action and "SELL" not in action:
            return state.open_pos

        # --- Risk gate (production RiskEngine semantics; replay state only) ---
        order = self._risk_gate(
            proposal, tick, state.equity, state.risk_engine, state.regime_state
        )
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
            self._close_position(
                state, state.open_pos, "SIGNAL_REVERSAL", exit_px, tick.timestamp
            )
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

'''

idx_dec = s.index("    def _decide(")
idx_int = s.index("    # Internals")
sep_start2 = s.rfind("    # ---", 0, idx_int)
assert 0 < idx_dec < sep_start2, (idx_dec, sep_start2)
s = s[:idx_dec] + N(NEW_DECIDE) + s[sep_start2:]

P.write_bytes(s.encode("utf-8"))
print("SPLICED ok, EOL=", repr(EOL), "len=", len(s))
