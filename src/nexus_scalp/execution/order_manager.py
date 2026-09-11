"""
Institutional Order Lifecycle & Dynamic Position Management Engine (v6.8 Enterprise Master - 853 Lines Complete Edition)
===========================================================================================================================
Monitors active open positions and pending orders with Wall Street grade execution controls,
Local State Features (LSF) desync detection, Almgren-Chriss Market Impact modeling, and a
60-Scenario Deterministic Decision Router with Absolute Profit-Shield Guards & Advanced Telemetry.

Enterprise Upgrades & Math Foundations Incorporated:
  - Profit-Shield Guard Gate (FORBIDS closing winning positions on micro-giveback or stagnation).
  - High Hold-Score Protection for Winners (Guarantees score >= 85 for trades in profit).
  - 60-Scenario Deterministic Position Management Router (Priority-ordered execution scenarios).
  - Fast Impact-Adjusted Trailing Stops & Break-Even (Triggers safely at optimal ATR thresholds).
  - Local State Features (LSF) Engine (O(1) desync detection, jump shock, & tick starvation tracking).
  - Smart Missed-Position Rescue Architecture (Auto-bootstraps & recalculates untracked positions).
  - 57 Derived Position Metrics Engine (Comprehensive PA, ICT, MFE/MAE, & Microstructure Analytics).
  - Almgren-Chriss Temporary Market Impact Model (Calculates real-time O(1) liquidity depletion).
  - Multi-Stage Partial Take-Profit Scaling (20%, 30%, 40%, 50%, 60% scale-outs at milestone targets).
  - Dynamic Pending Order Tracking & Expiration Guard (Cancels stale LIMIT orders if market moves away).
  - Advanced Telemetry: Second-level Time-in-Profit (TIP), Time-in-Drawdown (TID), Peak Excursions & Efficiency Index.
  - Memory-Leak Free Execution (Garbage collection for positions closed via TP/SL/Manual).

Invariants:
    - Zero Latency Penalty: Position management executes on every live tick (50ms hot path).
    - Full Traceability: Every modification, partial close, or cancellation is audited.
"""

import contextlib
import dataclasses
import math
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.configuration.config import AlgoConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData, TradeOrder
from nexus_scalp.execution.execution_plan import ExecutionPlan
from nexus_scalp.execution.hold_score_ledger import HoldScoreLedger
from nexus_scalp.execution.lifecycle import (
    PendingOrderLifecycle,
    TicketState,
    TicketStateStore,
)
from nexus_scalp.execution.lifecycle.dispatch import DispatchEngine
from nexus_scalp.execution.lifecycle.protection import ProtectionEngine
from nexus_scalp.execution.lifecycle.reconciliation import ReconciliationEngine
from nexus_scalp.execution.lifecycle.scoring import PositionScoringEngine
from nexus_scalp.execution.position_intelligence import (
    SmartMetricsInputs,
    _estimate_liquidation_impact,
    calculate_smart_metrics,
)
from nexus_scalp.execution.position_state_machine import PositionStateMachine
from nexus_scalp.execution.position_states import PositionState
from nexus_scalp.execution.position_tracker import PositionTrackingLedger
from nexus_scalp.execution.protection_ledger import (
    PositionProtectionLedger,
    PositionProtectionState,
)
from nexus_scalp.execution.recovery_budget import RecoveryBudgetLedger
from nexus_scalp.execution.telemetry_throttle import TelemetryThrottle
from nexus_scalp.execution.tickets_cache import TicketsCache
from nexus_scalp.experience.lifecycle import DecisionLifecycle
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.observability.telegram_notifier import TelegramNotifier
from nexus_scalp.ports.mt5_port import IMT5Port
from nexus_scalp.signals.rule_matrix import RuleMatrixEngine

logger = get_logger("nexus_scalp.execution.order_manager")


# =============================================================================
# EXECUTION-WIDE HARD INVARIANTS (Module B)
# =============================================================================

#: Absolute ceiling on lot size for any single dispatch, independent of sizing math.
HARD_MAX_LOTS: float = 10.0

#: Maximum simultaneous exposure: 1 active position OR 1 pending order, engine-wide.
MAX_TOTAL_EXPOSURE: int = 1

#: Re-export: the churn-lock constant is owned by PendingOrderLifecycle (S7)
#: and imported at the top of this module (facade compatibility).
#: Reason marker emitted by SignalPolicy to request an AI position reversal.
AI_REVERSAL_REASON: str = "AI_REVERSAL_SIGNAL"


# =============================================================================
# PROFIT-GIVEBACK / BREAKEVEN PROTECTION INVARIANTS (Ticket 152465527595 fix)
# =============================================================================
# These constants are the single source of truth for the deterministic profit
# protection layer. They are deliberately named (never inlined as magic numbers)
# so the risk desk can audit and tune them in one place.

#: Absolute USD floating profit at which a breakeven stop MUST be attempted.
BREAKEVEN_PROFIT_USD: float = 15.00

#: AGENT4-SPRINT (2026-09-01): R-anchored BE trigger floor. The flat $15
#: trigger fires at ~0.09R on the live ledger (median planned risk ~$168),
#: which locks an entry-level stop far too early and scratches every winner
#: that pulls back before its move develops (55/67 BE-scratches had MFE
#: >= $20). The BE trigger now requires the LARGER of the flat USD floor and
#: this fraction of the position's planned risk. Replay on the audited
#: ledger: 0.15R rescued 10 round-trips (vs 20 under-rescued / over-eager
#: today) while keeping p75 MFE capture at 60% (vs 40% baseline).
BREAKEVEN_TRIGGER_R: float = 0.15

#: ATR multiple that forms the alternative (volatility-scaled) breakeven trigger.
#: The multiple is converted to USD PnL via the symbol contract size before use;
#: raw ATR price units are NEVER compared against USD PnL.
BREAKEVEN_ATR_MULTIPLIER: float = 1.5

#: Locked profit offset for the breakeven stop, expressed in PIPS (not price units).
#: Converted through the canonical pip size resolver (`_resolve_pip_size`).
#: AGENT4-SPRINT (2026-09-01): 0.20 pips locked ~zero profit — a post-lock
#: pullback to entry still rounds the trade to a full scratch (spread+fees
#: unrecovered). 0.60 pips covers the round-trip cost on 2-digit gold so a
#: BE hit is a small positive scratch, not a silent loser.
BREAKEVEN_LOCK_PIPS: float = 0.60

#: Canonical gold pip representation used across the project (see rule_matrix.py).
DEFAULT_PIP_SIZE: float = 0.10

#: Minimum wall-clock gap between breakeven SL modify attempts per ticket. Prevents
#: a broker-rejected breakeven (or a market-pullback deferral) from becoming a retry
#: storm on the tick path (live evidence: 6,674 BREAKEVEN_FAILED audit rows).
BREAKEVEN_ATTEMPT_COOLDOWN_SEC: float = 5.0

#: Peak floating profit (USD) above which profit-erosion protection arms itself.
PROFIT_GIVEBACK_PEAK_USD: float = 20.00

#: Minimum fraction of peak profit that must be retained. Below this the engine
#: treats the trade as a failed winner and cuts it. This is the FLOOR used when
#: the trade has NOT yet crossed the first tier (see TIERED retention below).
PROFIT_GIVEBACK_MIN_RETENTION: float = 0.30

#: TIERED retention floor. The absolute-dollar arming threshold ($20) is too
#: coarse for small scalps: on 0.5-0.7 lots of XAUUSD, $20 peak is only ~3 pips,
#: and normal bid/ask noise trips a flat 30% retention floor, killing runners at
#: break-even. The floor is therefore derived from the PEAK's R multiple:
#:   peak < 0.5R          -> protection stays DISARMED (micro-profit noise zone)
#:   0.5R <= peak < 1.0R  -> allow up to 40% giveback (retain >= 0.60)  [AGENT4-SPRINT]
#:   1.0R <= peak < 1.5R  -> require >= 0.70 retention                  [AGENT4-SPRINT]
#:   peak >= 1.5R         -> lock in >= 0.80 of the move                [AGENT4-SPRINT]
TIERED_GIVEBACK_RETENTION_FLOOR: tuple[tuple[float, float], ...] = (
    # AGENT4-SPRINT (2026-09-01): floors tightened from 0.40/0.50/0.70.
    # Evidence: 68 round-trips returned $4,265 of peak profit to the market;
    #: the old floors retained $1,305 of it, the new ones $1,676 (+28%).
    # Arm threshold (0.5R) and micro-profit disarm are UNCHANGED.
    (0.50, 0.60),  # peak R >= 0.5  -> retain >= 60%
    (1.00, 0.70),  # peak R >= 1.0  -> retain >= 70%
    (1.50, 0.80),  # peak R >= 1.5  -> retain >= 80%
)
#: Below this peak R the giveback protection is DISARMED entirely so micro-profit
#: noise (a 2-pip pullback on a 3-pip scalp) can never close a winner at flat.
TIERED_GIVEBACK_ARM_R: float = 0.50

#: Hold-score penalty applied when profit retention breaches the floor.
PROFIT_GIVEBACK_HOLD_SCORE_PENALTY: int = 50

#: Hard hold-score ceiling for a position that gave back a meaningful profit and
#: is now negative. Normal scoring can never lift the score above this.
NEGATIVE_AFTER_PROFIT_HOLD_SCORE: int = 10

#: ATR multiple used by the protective trailing stop. Mirrors the historical
#: NORMAL_TRAIL distance so trailing behaviour is unchanged for healthy winners.
ATR_TRAILING_MULTIPLIER: float = 1.15

#: Console/stdout telemetry cadence, per ticket. SQLite/audit writes are NEVER
#: throttled by this value.
TELEMETRY_CONSOLE_INTERVAL_SEC: float = 10.0

#: TASK-EXIT-SEPARATION (c): minimum spacing between "AI flip exit suppressed"
#: WARNINGs for the SAME ticket (log-spam guard only; never gates any action).
_AI_FLIP_SUPPRESS_WARN_INTERVAL_SEC: float = 10.0


# -----------------------------------------------------------------------------
# P0 seam S5: dict-shaped views over TicketStateStore.
#
# Every former per-ticket dict on the manager (_entry_prices, _last_modify_sl,
# _sl_modified_flags, ...) is now a property returning a live view backed by
# the canonical TicketState record. Reads fall back to the field default when
# no record exists yet (mirrors dict.get(ticket, default) semantics); writes
# route into the record, creating it lazily. This removes the drift class:
# related per-ticket fields now advance together on ONE object.
# -----------------------------------------------------------------------------


class _TicketStateDictView:
    """Mutable dict-shaped view over one TicketState field, keyed by ticket.

    Implements the dict surface the manager and its tests use:
    __getitem__, __setitem__, get(), __contains__, pop(), setdefault(),
    keys()/values()/items(), __iter__/__len__/__bool__.

    PRESENCE SEMANTICS (critical): a field is considered ABSENT when it still
    holds its dataclass default (0.0 / "" / None / False / {}). This mirrors
    the original fragmented dicts, where a key existed only after an explicit
    write — so ``.get(ticket, caller_default)`` returns the caller default for
    never-written fields (e.g. ``_last_modify_sl.get(t, initial_sl)`` must
    yield the entry SL when no modification was confirmed, per BUG-085),
    never the field's zero default. Writes create the record lazily;
    pop() resets the field to its default (the record itself is owned by the
    store and dropped atomically in the cleanup bundle).
    """

    __slots__ = ("_default", "_field", "_store")

    def __init__(self, store: TicketStateStore, field_name: str) -> None:
        self._store = store
        self._field = field_name
        # Presence semantics come from the dataclass field default itself
        # (supports default_factory for dict-valued fields).
        f = TicketState.__dataclass_fields__[field_name]
        if f.default_factory is not dataclasses.MISSING:
            self._default = f.default_factory()
        else:
            self._default = f.default

    # --- presence helper ---------------------------------------------------
    def _present(self, st: Any) -> bool:
        try:
            return bool(getattr(st, self._field) != self._default)
        except Exception:
            # Explicit fallback: a record that cannot be probed counts as
            # ABSENT (mirrors the original dict-miss semantics). Returning
            # the caller default is the documented read contract above.
            return False

    # --- read surface ------------------------------------------------------
    def get(self, ticket: int, default: Any = None) -> Any:
        st = self._store.peek(ticket)
        if st is None or not self._present(st):
            return self._default if default is None else default
        return getattr(st, self._field)

    def __getitem__(self, ticket: int) -> Any:
        st = self._store.peek(ticket)
        if st is None or not self._present(st):
            raise KeyError(ticket)
        return getattr(st, self._field)

    def __contains__(self, ticket: object) -> bool:
        st = self._store.peek(ticket)  # type: ignore[arg-type]
        return st is not None and self._present(st)

    def keys(self) -> list[int]:
        """Tracked-ticket universe: records whose field was explicitly set."""
        out: list[int] = []
        for t in self._store.keys():
            st = self._store.peek(t)
            if st is not None and self._present(st):
                out.append(t)
        return out

    def values(self) -> list[Any]:
        return [self[t] for t in self.keys()]

    def items(self) -> list[tuple[int, Any]]:
        return [(t, self[t]) for t in self.keys()]

    def __iter__(self) -> Iterator[int]:
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self.keys())

    def __bool__(self) -> bool:
        return len(self) > 0

    # --- write surface -----------------------------------------------------
    def __setitem__(self, ticket: int, value: Any) -> None:
        setattr(self._store.get(ticket), self._field, value)

    def setdefault(self, ticket: int, value: Any) -> Any:
        st = self._store.peek(ticket)
        if st is not None and self._present(st):
            return getattr(st, self._field)
        self[ticket] = value
        return value

    # --- removal -----------------------------------------------------------
    def pop(self, ticket: int, default: Any = None) -> Any:
        st = self._store.peek(ticket)
        if st is None or not self._present(st):
            return default
        cur = getattr(st, self._field)
        setattr(st, self._field, self._default)
        return cur

    def __delitem__(self, ticket: int) -> None:
        self.pop(ticket, None)


class ExitMechanism:
    """Canonical exit-mechanism taxonomy recorded in the audit ledger."""

    TAKE_PROFIT_HIT = "TAKE_PROFIT_HIT"
    HARD_SL_HIT = "HARD_SL_HIT"
    RISK_FREE_SL_HIT = "RISK_FREE_SL_HIT"
    AI_REVERSAL_EXIT = "AI_REVERSAL_EXIT"
    HOLD_SCORE_DECAY = "HOLD_SCORE_DECAY"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    #: Winner that eroded below the retention floor (or went negative) after
    #: having banked >= PROFIT_GIVEBACK_PEAK_USD of unrealized profit.
    PROFIT_GIVEBACK_PROTECTION = "PROFIT_GIVEBACK_PROTECTION"


# PositionState moved to execution/position_states.py (P0 seam S2);
# imported below — facade name preserved for compatibility.


@dataclass
class PositionEvaluationStep:
    """A single observation slice inside the rolling trajectory deque."""

    timestamp: datetime
    pnl: float
    price: float
    hold_score: int
    drawdown: float
    retention: float
    atr: float
    volatility: float


# =============================================================================
# DATA STRUCTURES & VALUE OBJECTS
# =============================================================================


@dataclass
class LSFTicketState:
    """Local State Features (LSF) tracking metrics for an active ticket."""

    seen_ticks: float = 0.0
    desync_score: float = 0.0
    desync_shocks: float = 0.0
    last_price: float = 0.0
    last_profit_delta: float = 0.0
    last_net_delta: float = 0.0
    last_sl: float = 0.0
    last_tp: float = 0.0
    last_modify_intent: float = 0.0
    be_applied: float = 0.0
    trail_applied: float = 0.0


# PositionProtectionState moved to execution/protection_ledger.py
# (P0 seam S1); imported below. Facade name preserved for compatibility.


@dataclass
class SmartPositionMetrics:
    """Dataclass encapsulating 57 derived position metrics for execution routing."""

    spread_to_atr_ratio: float = 0.0
    impact_to_atr_ratio: float = 0.0
    net_to_atr_ratio: float = 0.0
    gross_to_atr_ratio: float = 0.0
    mae_to_atr_ratio: float = 0.0
    mfe_to_atr_ratio: float = 0.0
    mfe_mae_efficiency: float = 0.0
    mfe_giveback_ratio: float = 0.0
    adverse_tick_pressure: float = 0.0
    favorable_tick_pressure: float = 0.0
    stagnation_pressure: float = 0.0
    time_decay_ratio: float = 0.0
    position_age_bucket: float = 0.0
    position_size_pressure: float = 0.0
    volume_step_pressure: float = 0.0
    contract_pressure: float = 0.0
    liquidity_depletion_score: float = 0.0
    impact_to_net_profit_ratio: float = 0.0
    impact_to_gross_ratio: float = 0.0
    spread_impact_combo: float = 0.0
    breakeven_quality: float = 0.0
    trailing_quality: float = 0.0
    risk_reward_decay: float = 0.0
    unrealized_recovery_ratio: float = 0.0
    adverse_excursion_velocity: float = 0.0
    favorable_excursion_velocity: float = 0.0
    stagnation_ticks: float = 0.0
    adverse_ticks: float = 0.0
    favorable_ticks: float = 0.0
    missed_position_rescue_mode: bool = False
    no_sl_risk: bool = False
    sl_distance_to_atr: float = 0.0
    tp_distance_to_atr: float = 0.0
    sl_tp_asymmetry: float = 0.0
    stop_gap_pressure: float = 0.0
    wick_tolerance_pressure: float = 0.0
    volatility_regime_score: float = 0.0
    trend_conflict_score: float = 0.0
    kumo_conflict_score: float = 0.0
    choch_conflict_score: float = 0.0
    liquidity_sweep_conflict_score: float = 0.0
    tenkan_kijun_conflict_score: float = 0.0
    extreme_entry_conflict_score: float = 0.0
    rapid_reversal_conflict_score: float = 0.0
    directional_conflict_score: float = 0.0
    desync_score: float = 0.0
    desync_risk_flag: bool = False
    danger_tier: str = "NORMAL"
    kill_switch_required: bool = False
    time_decay_exit_required: bool = False
    soft_rescue_exit_required: bool = False
    defer_stop_management: bool = False
    defer_scale_out: bool = False
    smart_be_trigger: float = 0.0
    smart_trailing_distance: float = 0.0
    tp1_atr_multiplier: float = 0.0
    rescue_quality_score: float = 0.0


@dataclass(frozen=True)
class _FastReversalDecision:
    """Minimal decision payload for the fast-reversal follow-up (BUG-258).

    The fast reversal (close + stop-order flip inside the protection chain)
    previously submitted to the adapter DIRECTLY, bypassing the dispatch
    gate stack. It now routes through ``dispatch_order``, which needs the
    standard decision surface (action/symbol/geometry/timestamp). The
    request_id is derived from the source ticket so a repeated flip on the
    same ticket within one lifecycle cannot double-fire: the duplicate
    dispatch guard treats a sent request_id as terminal.
    """

    symbol: str
    action: Any
    proposed_entry: float
    stop_loss: float
    take_profit: float
    source_ticket: int
    generated_at: Any = None

    @property
    def request_id(self) -> str:
        return f"fast_reversal_{self.source_ticket}_{self.action.value}"


# =============================================================================
# MASTER ORDER LIFECYCLE MANAGER
# =============================================================================


class OrderLifecycleManager:
    """
    Master Institutional Order Lifecycle Manager orchestrating real-time position management,
    LSF desync detection, Almgren-Chriss slippage mitigation, telemetric tracking, and smart rescue execution.
    """

    def __init__(
        self,
        adapter: IMT5Port,
        audit_repo: AuditRepository | None = None,
        notifier: TelegramNotifier | None = None,  # [EXPANDED] Telegram Integration
        be_trigger_usd: float = 1.00,  # Dynamic base trigger ($1.00 movement before BE lock)
        be_lock_usd: float = 0.25,  # Locks +$0.25 to cover commissions and spread
        trailing_distance_usd: float = 1.50,  # ATR-scaled dynamic trailing distance for Gold noise
        min_modify_step_usd: float = 0.20,  # Minimum price change required before sending order modify IPC
        enable_partial_tp: bool = True,  # Enables partial profit scale-out at TP1
        partial_tp_ratio: float = 0.50,  # Closes 50% volume on TP1 milestone
        max_holding_seconds: float = 1800.0,  # 30 minutes time-decay threshold for stagnant trades
        eta_coefficient: float = 2500.0,  # Base Temporary Impact scale for XAUUSD (Almgren-Chriss)
        rule_matrix: RuleMatrixEngine | None = None,
        algo_config: AlgoConfig | None = None,
        risk_engine: Any = None,
        experience_engine: Any = None,
        lifecycle_tracker: Any = None,
        safety_state_provider: Any = None,
    ) -> None:
        #: TASK-3: optional immutable position-timeline tracker. When present,
        #: the close path finalizes the position timeline (POSITION_EXITED
        #: event) with the canonical realized PnL / R / exit mechanism so the
        #: lifecycle chain is complete (BUG-086). Never blocks on failure.
        self.lifecycle_tracker = lifecycle_tracker
        # PERSISTED-SAFETY-STATE WIRING (Agent-15 capital-protection fix,
        # BUG-256): DispatchEngine dispatch_order / execute_order consult
        # ``om._trading_blocked_by_safety_state`` as the fail-closed defense
        # against a persisted HALTED/KILL_SWITCH row. That method lives on
        # LiveEngine, not on this manager (the real composition root for the
        # dispatch layer), so without a provider the hasattr check was always
        # False and the persisted-halt half of the dispatch gate was INERT
        # (only the RiskEngine kill-switch flag remained). The composition
        # root registers the authority here; when absent the gate resolves
        # conservatively by refusing to claim "not blocked" is fine ONLY
        # because the RiskEngine flag + evaluate_proposal + loop-stop remain
        # — see the regression test for the full chain contract.
        self._safety_state_provider = safety_state_provider
        self.adapter = adapter
        self.mt5_adapter = adapter
        self.audit = audit_repo or AuditRepository()
        # BUG-226: provenance of the account feeding this audit stream. The
        # engine sets this from the effective boot mode; ledger writes read it
        # at write time so a hot-swap is reflected per-trade.
        self.current_account_source = "LIVE"
        self.notifier = notifier
        self.rule_matrix = rule_matrix
        self.algo_config = algo_config or AlgoConfig()
        # Optional RiskEngine used to clamp every dispatch to HARD_MAX_LOTS and
        # perform free-margin pre-checks. When absent, a local clamp still applies.
        self.risk_engine = risk_engine
        self.experience_engine = experience_engine
        self._processed_orders: dict[str, bool] = {}
        # TASK-EXIT-SEPARATION (c): per-ticket last "AI flip suppressed"
        # WARNING stamp (monotonic clock; log-spam guard only).
        self._ai_flip_warn_times: dict[int, float] = {}

        import threading

        self._live_tickets_lock = threading.Lock()
        # S6 Phase-2: cache storage lives in TicketsCache (_tickets_cache);
        # the @property _live_tickets_cache exposes its live dict. Do NOT
        # assign that name — an instance attribute would shadow the property.

        self.be_trigger = be_trigger_usd
        self.be_lock = be_lock_usd
        self.trailing_distance = trailing_distance_usd
        self.min_step = min_modify_step_usd

        # Institutional Execution Features
        self.enable_partial_tp = enable_partial_tp
        self.partial_tp_ratio = partial_tp_ratio
        self.max_holding_seconds = max_holding_seconds
        self.eta_coefficient = eta_coefficient

        # Safety State Machine
        self.global_state = "NORMAL"
        self._consecutive_failures = 0

        # State Tracking for Metrics (Ticket -> Primitive)

        # [EXPANDED] Maps position ticket -> Telegram message_id for Thread Replying
        self._order_message_ids: dict[int, int] = {}
        # [EXPANDED] Maps order_id (from proposal/TradeOrder) -> Telegram message_id
        self._order_id_to_message_id: dict[str, int] = {}

        # [EXPANDED] State tracking for extended notifications

        #: PHASE 08: seconds from open to each observed excursion extreme.
        #: PHASE 08: execution-quality evidence captured at fill time.

        # Advanced Telemetry Trackers

        # Local State Features (LSF) Engine & Desync State Trackers

        # S6-followup: explicit per-ticket tracking-state owner (dicts moved
        # to position_tracker.PositionTrackingLedger; compat properties below).
        self._tracking = PositionTrackingLedger()

        # S6 STEP-A: telemetry throttle owner (BUG-129 shared gate).
        self._telemetry = TelemetryThrottle()
        # S6 Phase-2: live-tickets cache owner (positions+pending view).
        self._tickets_cache = TicketsCache()

        # S6-escalation: hold-score state owner (dicts moved to
        # hold_score_ledger.HoldScoreLedger; compat properties below).
        self._hold_scores = HoldScoreLedger()

        # Throttling & spread tracking for dynamic hold score
        self._rolling_spreads: list[float] = []

        # Part 4 (P0 seam S7): pending lifecycle state is OWNED by
        # PendingOrderLifecycle (execution/lifecycle/pending_orders.py);
        # the manager delegates (see _pending_lifecycle property).

        # P0 seam S5: canonical per-ticket position state store (dict views
        # over TicketState records, defined as properties after __init__).
        self._states = TicketStateStore()

        # P0 seam S2: position lifecycle state lives in PositionStateMachine
        # (execution/position_state_machine.py). Compatibility properties below
        # expose the live dicts under the historical names.
        self._state_machine = PositionStateMachine(
            lambda: (
                getattr(self.algo_config, "min_confirmation_duration", 2.5),
                getattr(self.algo_config, "min_observation_count", 10),
            )
        )

        # Recovery tracking dictionaries
        # P0 seam S3: recovery-budget state lives in RecoveryBudgetLedger
        # (execution/recovery_budget.py). Compatibility properties below expose
        # the live dicts under the historical names (tests read/write them).
        self._recovery_ledger = RecoveryBudgetLedger()

        # =====================================================================
        # MODULE A/B STATE: LEDGER AUTOPSY CONTEXT & REVERSAL BOOKKEEPING
        # =====================================================================
        # Entry context captured at open so the closing autopsy row is complete.
        # SETUP SNAPSHOT (2026-08-18): ticket -> full chart-state fingerprint at
        # dispatch (HTF/SMC/ICT structure, displacement, sessions, guardian).
        # Carried to the closed-trade autopsy for setup/strategy attribution.
        self._entry_setup_snapshots: dict[int, dict[str, Any]] = {}
        #: Ticket -> True once trailing/breakeven actually moved the broker-side SL.
        #: TASK-3: ticket -> bounded list of reversal/regime/liquidity observations
        #: captured WHILE the position was open (MODEL_REVERSAL, REGIME_REVERSAL,
        #: LIQUIDITY_REVERSAL, CONFIDENCE_COLLAPSE). Persisted on the closing
        #: autopsy row so outcome/behavior/reporting can prove WHAT changed while
        #: the position was held — never recomputed from price geometry alone.
        #: Ticket -> net realized PnL / exit mechanism captured during the
        #: closing sweep, used by the lifecycle finalize hook (BUG-086).
        #: Ticket -> model probabilities snapshotted at entry (immutable baseline).
        #: Ticket -> regime at entry (immutable baseline).
        #: Ticket -> deterministic profit-protection state machine (monotonic peak
        #: profit, breakeven lock confirmation, giveback arming, close idempotency,
        #: console-telemetry clock). Keyed strictly by MT5 ticket.
        # P0 seam S1: per-ticket protection state now lives in the ledger
        # (execution/protection_ledger.py). Access stays via
        # self.get_protection_state() — signature and semantics unchanged.
        self._protection_ledger = PositionProtectionLedger()
        #: Ticket -> exit mechanism forced by the engine (AI reversal, hold decay, ...)
        #: which overrides the broker-history heuristic during the autopsy write.
        #: Ticket -> most recent TickData observed for that ticket (used by the
        #: breakeven-aware VOLATILITY_EXPANSION exit logic to decide whether price has
        #: actually breached the locked protective stop before a market close is allowed).
        #: Latest account snapshot, stamped onto each autopsy row.
        self._last_account_balance: float = 0.0
        self._last_account_equity: float = 0.0
        self._peak_equity: float = 0.0
        #: Entry context staged by the policy/engine before the ticket exists
        #: (BUG-081). Bounded registry keyed by the originating order/request id
        #: Phase 14: tickets already reconciled from broker history (dedup guard
        #: for the reconciliation close-loop across repeated passes/restarts).
        self._reconcile_seen: dict[int, bool] = {}
        #: TASK-7: tickets the engine has positively closed or that the broker no
        #: longer reports. Once closed, NO protective modification may be issued for
        #: the ticket (invariant: a CLOSED position cannot receive further protective
        #: modifications).
        #: TASK-7: last arbitrated exit decision per ticket (action + scenario +
        #: timestamp). Set at arbitration time, cleared at autopsy, used for exit
        #: traceability when the position closes before the next management pass.
        #: TASK-7: monotonic gate for the reconciliation close-loop broker fetch.
        #: Prevents a per-tick history_deals_get (BUG-090).
        self._last_reconcile_attempt: float = 0.0

    # -------------------------------------------------------------------------
    # P0 seam S5: canonical per-ticket state. TicketStateStore (one record per
    # ticket) now owns the former fragmented ticket-scoped dicts; the same-named
    # properties below expose live dict-shaped views so every read/write site
    # (and direct test access) keeps working unchanged. Field defaults match the
    # originals; _MISSING sentinel preserves .get(ticket, default) semantics.
    # -------------------------------------------------------------------------

    @property
    def _ticket_state_store(self) -> TicketStateStore:
        """Composition seam for tests and extracted lifecycle modules."""
        return self._states

    # -----------------------------------------------------------------
    # P0 seam S7: pending-order lifecycle owner (composition root).
    # State (churn lock, placement/age, cancel reasons, entry-context
    # lineage registry) lives in PendingOrderLifecycle; the manager only
    # composes and delegates.
    # -----------------------------------------------------------------

    @property
    def _pending_lifecycle(self) -> PendingOrderLifecycle:
        """Lazily composed pending-order lifecycle owner (S7)."""
        pl: PendingOrderLifecycle | None = getattr(self, "_pending_lifecycle_instance", None)
        if pl is None:
            pl = PendingOrderLifecycle(
                adapter=self.adapter,
                tickets_view=lambda: self._live_tickets_cache,
                refresh_cache=self.refresh_live_tickets_cache,
                experience_engine=self.experience_engine,
                audit=self.audit,
            )
            pl.set_entry_order_ids_probe(
                lambda ticket: str(self._entry_order_ids.get(ticket, "") or "")
            )
            self._pending_lifecycle_instance = pl
        return pl

    @property
    def _pending_context_registry(self) -> dict[str, dict[str, Any]]:
        """Live lineage-registry view (owned by PendingOrderLifecycle, S7)."""
        return self._pending_lifecycle._pending_context_registry

    @property
    def _context_bound_tickets(self) -> dict[str, set[int]]:
        """Live bound-families view (owned by PendingOrderLifecycle, S7)."""
        return self._pending_lifecycle._context_bound_tickets

    @property
    def _unbound_ticket_contexts(self) -> dict[int, str]:
        """Live provenance-gap view (owned by PendingOrderLifecycle, S7)."""
        return self._pending_lifecycle._unbound_ticket_contexts

    @property
    def _pending_orders_setup_time(self) -> dict[int, datetime]:
        """Live placement-time dict view (owned by PendingOrderLifecycle, S7).

        Kept as a live-identity property so tests/sweeps that seed or read
        placement ages directly keep working without owning the state.
        """
        return self._pending_lifecycle._pending_orders_setup_time

    def register_entry_context(
        self,
        order_id: str = "",
        entry_reason: str = "",
        ai_confidence: float = 0.0,
        market_regime: str = "",
        expected_entry: float = 0.0,
        dispatch_monotonic: float = 0.0,
        setup_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Delegate: stages dispatch lineage (owned by PendingOrderLifecycle)."""
        self._pending_lifecycle.register_entry_context(
            order_id=order_id,
            entry_reason=entry_reason,
            ai_confidence=ai_confidence,
            market_regime=market_regime,
            expected_entry=expected_entry,
            dispatch_monotonic=dispatch_monotonic,
            setup_snapshot=setup_snapshot,
        )

    @staticmethod
    def _pending_field(pending: Any, *names: str, default: Any = None) -> Any:
        """Delegate: dict/object pending field probe (PendingOrderLifecycle)."""
        return PendingOrderLifecycle.pending_field(pending, *names, default=default)

    def _bind_pending_entry_context(self, ticket: int, decision_order_id: str = "") -> None:
        """Delegate: BUG-081 lineage binding (owned by PendingOrderLifecycle)."""
        self._pending_lifecycle.bind_pending_entry_context(
            ticket,
            decision_order_id,
            entry_reasons=self._entry_reasons,
            entry_confidences=self._entry_confidences,
            entry_regimes=self._entry_regimes,
            entry_order_ids=self._entry_order_ids,
            entry_expected_price=self._entry_expected_price,
            entry_fill_latency_ms=self._entry_fill_latency_ms,
            entry_setup_snapshots=self._entry_setup_snapshots,
        )

    def _prune_bound_context(self, order_id: str) -> None:
        """Delegate: lineage registry pruning (owned by PendingOrderLifecycle)."""
        self._pending_lifecycle.prune_bound_context(order_id)

    def _emit_terminal_for_pending(self, ticket: int, state: Any, detail: str = "") -> bool:
        """Delegate: terminal pending outcome (owned by PendingOrderLifecycle)."""
        return self._pending_lifecycle.emit_terminal_for_pending(ticket, state, detail)

    def _pending_broker_state(self, ticket: int, symbol: str | None = None) -> str:
        """Delegate: broker truth probe (owned by PendingOrderLifecycle)."""
        return self._pending_lifecycle.broker_state(ticket, symbol)

    def cancel_pending_order_verified(self, ticket: int, symbol: str | None = None) -> bool:
        """Delegate: broker-verified cancellation (owned by PendingOrderLifecycle)."""
        return self._pending_lifecycle.cancel_pending_order_verified(ticket, symbol)

    def cancel_pending_order_with_retry(
        self, ticket: int, symbol: str | None = None, max_attempts: int = 3
    ) -> int:
        """Delegate: bounded cancel retry (owned by PendingOrderLifecycle)."""
        return self._pending_lifecycle.cancel_pending_order_with_retry(ticket, symbol, max_attempts)

    def reconcile_pending_state(
        self, symbol: str | None = None, current_tick: TickData | None = None
    ) -> dict[str, Any]:
        """Delegate: pending reconciliation report (owned by PendingOrderLifecycle)."""
        return self._pending_lifecycle.reconcile_pending_state(
            symbol, current_tick, live_tickets=self._live_tickets_cache
        )

    def manage_pending_orders(
        self,
        symbol: str,
        current_tick: TickData,
        symbol_info: SymbolInfo | None = None,
        atr: float = 1.50,
        max_pending_dist_atr_mult: float = 2.50,
    ) -> None:
        """Delegate: pending lifecycle guard (owned by PendingOrderLifecycle)."""
        self._pending_lifecycle.manage_pending_orders(
            symbol,
            current_tick,
            symbol_info,
            atr,
            max_pending_dist_atr_mult,
            audit=self.audit,
        )

    # --- P0 seam S5: dict views over TicketStateStore (generated) ---
    @property
    def _entry_prices(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_price (S5)."""
        return _TicketStateDictView(self._states, "entry_price")

    @property
    def _entry_sls(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_sl (S5)."""
        return _TicketStateDictView(self._states, "entry_sl")

    @property
    def _entry_tps(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_tp (S5)."""
        return _TicketStateDictView(self._states, "entry_tp")

    @property
    def _last_known_volume(self) -> _TicketStateDictView:
        """Live dict view over TicketState.last_known_volume (S5)."""
        return _TicketStateDictView(self._states, "last_known_volume")

    @property
    def _initial_risks(self) -> _TicketStateDictView:
        """Live dict view over TicketState.initial_risk (S5)."""
        return _TicketStateDictView(self._states, "initial_risk")

    @property
    def _entry_expected_price(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_expected_price (S5)."""
        return _TicketStateDictView(self._states, "entry_expected_price")

    @property
    def _entry_atr(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_atr (S5)."""
        return _TicketStateDictView(self._states, "entry_atr")

    @property
    def _entry_spread(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_spread (S5)."""
        return _TicketStateDictView(self._states, "entry_spread")

    @property
    def _entry_fill_latency_ms(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_fill_latency_ms (S5)."""
        return _TicketStateDictView(self._states, "entry_fill_latency_ms")

    @property
    def _last_modify_sl(self) -> _TicketStateDictView:
        """Live dict view over TicketState.last_modify_sl (S5)."""
        return _TicketStateDictView(self._states, "last_modify_sl")

    @property
    def _entry_reasons(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_reason (S5)."""
        return _TicketStateDictView(self._states, "entry_reason")

    @property
    def _entry_confidences(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_confidence (S5)."""
        return _TicketStateDictView(self._states, "entry_confidence")

    @property
    def _entry_regimes(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_regime (S5)."""
        return _TicketStateDictView(self._states, "entry_regime")

    @property
    def _entry_directions(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_direction (S5)."""
        return _TicketStateDictView(self._states, "entry_direction")

    @property
    def _entry_order_ids(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_order_id (S5)."""
        return _TicketStateDictView(self._states, "entry_order_id")

    @property
    def _sl_modified_flags(self) -> _TicketStateDictView:
        """Live dict view over TicketState.sl_modified (S5)."""
        return _TicketStateDictView(self._states, "sl_modified")

    @property
    def _partial_closed_tickets(self) -> _TicketStateDictView:
        """Live dict view over TicketState.partial_closed (S5)."""
        return _TicketStateDictView(self._states, "partial_closed")

    @property
    def _rescue_registered_tickets(self) -> _TicketStateDictView:
        """Live dict view over TicketState.rescue_registered (S5)."""
        return _TicketStateDictView(self._states, "rescue_registered")

    @property
    def _closed_tickets(self) -> _TicketStateDictView:
        """Live dict view over TicketState.is_closed (S5)."""
        return _TicketStateDictView(self._states, "is_closed")

    @property
    def _entry_timestamps(self) -> _TicketStateDictView:
        """Live dict view over TicketState.entry_timestamp (S5)."""
        return _TicketStateDictView(self._states, "entry_timestamp")

    @property
    def _forced_exit_mechanisms(self) -> _TicketStateDictView:
        """Live dict view over TicketState.forced_exit_mechanism (S5)."""
        return _TicketStateDictView(self._states, "forced_exit_mechanism")

    @property
    def _net_pnl_by_ticket(self) -> _TicketStateDictView:
        """Live dict view over TicketState.net_pnl (S5)."""
        return _TicketStateDictView(self._states, "net_pnl")

    @property
    def _exit_mechanism_by_ticket(self) -> _TicketStateDictView:
        """Live dict view over TicketState.exit_mechanism (S5)."""
        return _TicketStateDictView(self._states, "exit_mechanism")

    @property
    def _exit_pending_final_reason(self) -> _TicketStateDictView:
        """Live dict view over TicketState.exit_pending_final (S5)."""
        return _TicketStateDictView(self._states, "exit_pending_final")

    # =========================================================================
    # MODULE A: LEDGER AUTOPSY CONTEXT INGESTION
    # =========================================================================

    def update_account_snapshot(self, account: Any, peak_equity: float | None = None) -> None:
        """
        Records the latest account balance/equity so closed-trade autopsy rows can carry
        an accurate post-trade account snapshot and drawdown percentage.
        """
        try:
            self._last_account_balance = float(getattr(account, "balance", 0.0) or 0.0)
            self._last_account_equity = float(getattr(account, "equity", 0.0) or 0.0)
        except (TypeError, ValueError):
            return

        if peak_equity is not None:
            try:
                self._peak_equity = max(self._peak_equity, float(peak_equity))
            except (TypeError, ValueError):
                pass
        self._peak_equity = max(self._peak_equity, self._last_account_equity)

    def _current_drawdown_percent(self) -> float:
        """Computes drawdown from peak equity as a percentage (0.0 when no peak known)."""
        if self._peak_equity <= 0.0:
            return 0.0
        return max(
            0.0, ((self._peak_equity - self._last_account_equity) / self._peak_equity) * 100.0
        )

    def _price_delta_to_usd(
        self,
        price_delta: float,
        volume: float,
        symbol_info: SymbolInfo | None,
    ) -> float:
        """Converts a price excursion into account currency using the contract size."""
        contract_size = 100.0
        if symbol_info and symbol_info.trade_contract_size > 0:
            contract_size = symbol_info.trade_contract_size
        return float(price_delta) * float(volume) * contract_size

    def register_telegram_message(self, ticket: int, message_id: int | None) -> None:
        """Associates a broker position ticket with its primary Telegram message_id."""
        if message_id is not None:
            self._order_message_ids[ticket] = message_id

    def register_order_message(self, order_id: str, message_id: int) -> None:
        """Temporarily registers message_id for a submitted order_id."""
        self._order_id_to_message_id[order_id] = message_id

    def should_modify_pending_order(
        self,
        ticket: int,
        price: float,
        atr: float,
        now: datetime,
    ) -> bool:
        """Delegate: 30s churn lock (owned by PendingOrderLifecycle)."""
        return self._pending_lifecycle.should_modify_pending_order(ticket, price, atr, now)

    def get_active_live_tickets(self) -> list[dict[str, Any]]:
        """Returns a list of currently live active positions and pending orders matching symbol and magic number."""
        with self._live_tickets_lock:
            return list(self._live_tickets_cache.values())

    # -----------------------------------------------------------------
    # P0 seam S10: broker dispatch composition. execute_order (hedge
    # path), dispatch_order (primary entry path), the dispatch volume
    # clamp and the entry-reason resolver are owned by DispatchEngine
    # (execution/lifecycle/dispatch.py). The engine reads/writes the
    # manager's canonical state surface through `om`; the
    # `_processed_orders` duplicate-dispatch guard stays manager-owned
    # (tests and the debug snapshot read it directly). The manager keeps
    # only these delegation shims under the historical names.
    # -----------------------------------------------------------------

    @property
    def _dispatch(self) -> DispatchEngine:
        """Lazily composed dispatch engine (S10)."""
        eng: DispatchEngine | None = getattr(self, "_dispatch_instance", None)
        if eng is None:
            eng = DispatchEngine(self)
            self._dispatch_instance = eng
        return eng

    def execute_order(self, order: TradeOrder) -> bool:
        """Delegate: hedge-path broker submission (owned by DispatchEngine, S10)."""
        return self._dispatch.execute_order(order)

    def _clamp_dispatch_volume(self, volume: float, symbol: str | None = None) -> float:
        """Delegate: risk-engine + HARD_MAX_LOTS clamp (owned by DispatchEngine, S10)."""
        return self._dispatch._clamp_dispatch_volume(volume, symbol=symbol)

    def dispatch_order(
        self, decision: Any, volume: float, setup_snapshot: dict[str, Any] | None = None
    ) -> bool:
        """Delegate: unified entry dispatch router (owned by DispatchEngine, S10)."""
        return self._dispatch.dispatch_order(decision, volume, setup_snapshot)

    def _resolve_entry_reason(self, decision: Any) -> str:
        """Delegate: canonical ledger entry reason (owned by DispatchEngine, S10)."""
        return self._dispatch._resolve_entry_reason(decision)

    def count_total_exposure(self, symbol: str | None = None) -> tuple[int, int]:
        """
        Counts engine-owned exposure from the live tickets cache.

        Returns:
            (active_positions, active_pending_orders)
        """
        positions = 0
        pendings = 0
        with self._live_tickets_lock:
            for info in self._live_tickets_cache.values():
                if symbol and info.get("symbol") not in (None, symbol):
                    continue
                if info.get("type") == "PENDING":
                    pendings += 1
                else:
                    positions += 1
        return positions, pendings

    def _is_exposure_available(self, symbol: str | None = None) -> bool:
        """
        Enforces MAX_TOTAL_EXPOSURE: at most one active position OR one pending order
        across the entire engine. This is the last line of defence before an order is
        sent to the broker, independent of the policy-level gate.

        BUG-240: the gate is ENGINE-WIDE by contract (this docstring and the
        signals/policy.py gate both say engine-wide), but the implementation
        counted only `symbol`-scoped tickets, so a position held on any other
        instrument left the gate open. The count is now unscoped; the per-symbol
        breakdown stays available for block-reason logging.
        """
        _sym_positions, _sym_pendings = self.count_total_exposure(symbol=symbol)
        positions, pendings = self.count_total_exposure(symbol=None)
        return (positions + pendings) < MAX_TOTAL_EXPOSURE

    # =========================================================================
    # P0-A (BUG-140): TERMINAL PENDING-ORDER EXPERIENCE OUTCOMES
    # -------------------------------------------------------------------------
    # A decision that never becomes a trade MUST still terminate in the
    # experience ledger with an explicit lifecycle state, otherwise the
    # research dataset permanently reports MISSING_OUTCOME for it.
    # =========================================================================

    # =========================================================================
    # MODULE B: AI POSITION REVERSAL PROTOCOL
    # =========================================================================

    def execute_ai_reversal(
        self,
        decision: Any,
        volume: float,
        current_tick: TickData | None = None,
        symbol_info: SymbolInfo | None = None,
    ) -> bool:
        """
        Executes the AI Position Reversal Protocol.

        Intercepts a CLOSE_POSITION decision carrying reason AI_REVERSAL_SIGNAL:
          1. Closes every conflicting active ticket on MT5 immediately.
          2. Records exit_mechanism=AI_REVERSAL_EXIT for those tickets so the ledger
             autopsy attributes the exit correctly (never a generic MANUAL_CLOSE).
          3. Only after the close is confirmed, dispatches the new directional order.

        Opposing orders are NEVER stacked: if the close fails, no new order is sent.
        """
        # TASK-EXIT-SEPARATION (c): the AI direction-flip exit is SUSPENDED
        # unless the operator enables `algo.ai_flip_exit_enabled` (default
        # False, fail-safe). The flag is read LIVE on every call so an
        # operator re-enables without a code change; each suppressed flip
        # logs ONE structured WARNING per ticket, rate-limited.
        if not bool(getattr(getattr(self, "algo_config", None), "ai_flip_exit_enabled", False)):
            self._warn_ai_flip_suspended(
                getattr(decision, "ticket", 0) or 0,
                getattr(
                    getattr(decision, "action", None), "value", str(getattr(decision, "action", ""))
                ),
            )
            return False

        symbol = getattr(decision, "symbol", "") or ""
        new_action = getattr(decision, "reversal_action", None) or getattr(decision, "action", None)

        try:
            positions = self.adapter.get_positions(symbol=symbol) or []
        except Exception as err:
            logger.error("AI REVERSAL: failed to query positions", error=str(err))
            return False

        target_ticket = getattr(decision, "ticket", 0) or 0
        targets = [p for p in positions if (target_ticket in (0, p.ticket))]

        if not targets:
            logger.warning(
                "AI REVERSAL: no active position found to reverse",
                symbol=symbol,
                ticket=target_ticket,
            )
            return False

        all_closed = True
        closed_volume = 0.0
        for pos in targets:
            logger.info(
                ">>> AI REVERSAL PROTOCOL: closing conflicting position before flipping direction <<<",
                ticket=pos.ticket,
                held=pos.type.value,
                new_action=getattr(new_action, "value", str(new_action)),
            )
            # Mark the intended exit mechanism BEFORE the close so the autopsy writer
            # (which runs on the next management pass) attributes it correctly.
            self._forced_exit_mechanisms[pos.ticket] = ExitMechanism.AI_REVERSAL_EXIT

            _flip_close_started = time.monotonic()
            if self.adapter.close_position(ticket=pos.ticket):
                self.audit.log_order(
                    ticket=pos.ticket,
                    order_id=f"ai_reversal_close_{pos.ticket}",
                    symbol=pos.symbol,
                    action="Executed order",
                    price=pos.price_open,
                    stop_loss=pos.sl,
                    take_profit=pos.tp,
                    volume=pos.volume,
                    reason=AI_REVERSAL_REASON,
                    latency=max(0.0, time.monotonic() - _flip_close_started),
                    execution_mode="AI_REVERSAL",
                )
                if self.notifier:
                    with contextlib.suppress(Exception):
                        self.notifier.notify_canonical_close(
                            ticket=pos.ticket,
                            symbol=pos.symbol,
                            entry=pos.price_open,
                            exit_price=(
                                current_tick.bid
                                if (current_tick and pos.type == OrderType.BUY)
                                else (current_tick.ask if current_tick else pos.price_open)
                            ),
                            profit_usd=pos.profit,
                            duration_sec=0.0,
                            exit_reason=ExitMechanism.AI_REVERSAL_EXIT,
                            evidence=f"AI_REVERSAL -> {getattr(new_action, 'value', new_action)}",
                            reply_to_message_id=self._order_message_ids.get(pos.ticket),
                        )
                # Drop the ticket from the cache immediately so the exposure gate frees up
                # in the same tick and the reversal order is not blocked by its own predecessor.
                with self._live_tickets_lock:
                    self._live_tickets_cache.pop(pos.ticket, None)
                closed_volume += float(pos.volume)
            else:
                all_closed = False
                self._forced_exit_mechanisms.pop(pos.ticket, None)
                logger.error(
                    "AI REVERSAL ABORTED: broker refused to close position", ticket=pos.ticket
                )

        if not all_closed:
            # Refuse to stack an opposing order on top of a position we could not close.
            return False

        if new_action is None or new_action == ActionType.CLOSE_POSITION:
            # Pure exit request with no directional follow-up.
            return True

        reversal_decision = decision
        if getattr(decision, "action", None) == ActionType.CLOSE_POSITION:
            try:
                reversal_decision = decision.model_copy(update={"action": new_action})
            except Exception:
                logger.error("AI REVERSAL: unable to derive reversal decision payload")
                return True

        # Mirror the closed exposure when the caller did not size the flip explicitly
        # (e.g. the risk engine returned 0 because no symbol_info was available).
        if volume is None or float(volume) <= 0.0:
            # BUG-258 (Agent-15 capital-protection wave 3): the flip order must
            # carry a risk-approved volume. The caller (DecisionExecutor) sizes
            # the flip through RiskEngine.evaluate_proposal BEFORE invoking
            # this protocol; a zero/non-positive volume here means the risk
            # engine REJECTED or could not evaluate the flip (rejected
            # geometry, no symbol info, breaker/RR/spread/exposure/margin/
            # impact refusal). The old mirrored-volume fallback (sizing from
            # the closed exposure) bypassed canonical risk approval and is
            # REMOVED: the protective close above still happened, but no new
            # order is dispatched without risk-approved sizing.
            logger.warning(
                "[AI_REVERSAL] flip dispatch refused: volume not risk-approved (close-only)",
                ticket=getattr(decision, "ticket", 0) or 0,
                volume=volume,
            )
            return True

        return self.dispatch_order(reversal_decision, volume)

    def execute_lifecycle_action(self, decision: Any) -> bool:
        """
        Unified dispatch router for position lifecycle actions (CLOSE_POSITION, PARTIAL_CLOSE, MODIFY_SL_TP, CANCEL_ORDER).

        A CLOSE_POSITION carrying reason_code AI_REVERSAL_SIGNAL is intercepted and
        routed through the AI Reversal Protocol so the exit is attributed as
        AI_REVERSAL_EXIT in the ledger instead of a generic manual close.
        """
        action = decision.action
        ticket = getattr(decision, "ticket", 0) or 0
        volume = getattr(decision, "volume", None) or 0.0

        # --- AI REVERSAL INTERCEPT ---
        if action == ActionType.CLOSE_POSITION and AI_REVERSAL_REASON in str(
            getattr(decision, "reason_code", "") or ""
        ):
            # TASK-EXIT-SEPARATION (c): while the flip is suspended, the
            # autopsy tag must NOT be set (a later organic exit must not be
            # mislabelled as AI_REVERSAL_EXIT).
            if not bool(getattr(getattr(self, "algo_config", None), "ai_flip_exit_enabled", False)):
                self._warn_ai_flip_suspended(
                    ticket,
                    getattr(getattr(decision, "reversal_action", None), "value", "CLOSE_POSITION"),
                )
                return False
            self._forced_exit_mechanisms[ticket] = ExitMechanism.AI_REVERSAL_EXIT
            logger.info("Intercepted CLOSE_POSITION as AI_REVERSAL_SIGNAL", ticket=ticket)
            return self.execute_ai_reversal(decision=decision, volume=volume)

        logger.info(
            "execute_lifecycle_action mapping action to MT5 command", action=action, ticket=ticket
        )

        if action == ActionType.CLOSE_POSITION:
            _action_started = time.monotonic()
            success = self.mt5_adapter.close_position(ticket=ticket)
            # OBS-TRACE (2026-09-09): banner moved behind verified success -
            # a failed close previously logged *** REAL ORDER/EXECUTION
            # EXECUTED *** as if it had happened (log-as-authority defect).
            if success:
                logger.info(
                    f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: 0.0"
                )
                self.audit.log_order(
                    ticket=ticket,
                    order_id=f"close_{ticket}",
                    symbol="",
                    action="Executed order",
                    price=0.0,
                    stop_loss=0.0,
                    take_profit=0.0,
                    volume=volume,
                    reason="close_position",
                    latency=max(0.0, time.monotonic() - _action_started),
                    execution_mode="STANDARD",
                )
            return success

        elif action == ActionType.PARTIAL_CLOSE:
            _action_started = time.monotonic()
            success = self.mt5_adapter.close_position(ticket=ticket, volume=volume)
            lots = volume if volume is not None else 0.0
            if success:
                logger.info(
                    f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: {lots}"
                )
                self.audit.log_order(
                    ticket=ticket,
                    order_id=f"partial_{ticket}",
                    symbol="",
                    action="Executed order",
                    price=0.0,
                    stop_loss=0.0,
                    take_profit=0.0,
                    volume=lots,
                    reason="partial_close",
                    latency=max(0.0, time.monotonic() - _action_started),
                    execution_mode="STANDARD",
                )
            return success

        elif action == ActionType.MODIFY_SL_TP:
            _action_started = time.monotonic()
            success = self.mt5_adapter.modify_order(
                ticket=ticket, stop_loss=decision.stop_loss, take_profit=decision.take_profit
            )
            if success:
                logger.info(
                    f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: 0.0"
                )
                self.audit.log_order(
                    ticket=ticket,
                    order_id=f"modify_{ticket}",
                    symbol="",
                    action="Modified order",
                    price=0.0,
                    stop_loss=decision.stop_loss,
                    take_profit=decision.take_profit,
                    volume=0.0,
                    reason="modify_order SL/TP",
                    latency=max(0.0, time.monotonic() - _action_started),
                    execution_mode="STANDARD",
                )
            return success

        elif action == ActionType.CANCEL_ORDER:
            # BUG-072/073: broker-verified cancellation — never release the
            # exposure slot on a send-result alone.
            success = self.cancel_pending_order_verified(ticket=ticket)
            if success:
                logger.info(
                    f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: 0.0"
                )
                self.audit.log_order(
                    ticket=ticket,
                    order_id=f"cancel_{ticket}",
                    symbol="",
                    action="Cancelled order",
                    price=0.0,
                    stop_loss=0.0,
                    take_profit=0.0,
                    volume=0.0,
                    reason="Manual cancel_pending_order",
                    latency=0.0,
                    execution_mode="STANDARD",
                )
            return success

        return False

    # =========================================================================
    # MANUAL POSITION ACTIONS (BUG-242, INV-004 single execution authority)
    # -------------------------------------------------------------------------
    # Operator-initiated close/modify requests (web UI) previously called the
    # broker adapter DIRECTLY, bypassing the OrderLifecycleManager: no audit
    # order row, no forced exit mechanism (autopsy mis-attribution), no cache
    # release, no SAFE_MODE/SHADOW boundary. These wrappers keep the operator
    # surface but route it through the manager so every manual mutation
    # carries the same evidence trail as an engine-initiated one.
    # =========================================================================
    def close_position_manual(self, ticket: int) -> bool:
        """Operator manual close routed through the manager (INV-004)."""
        # Evidence state BEFORE the broker call so a successful close is
        # attributed as an operator action, not reconstructed UNKNOWN.
        self._forced_exit_mechanisms[ticket] = ExitMechanism.MANUAL_CLOSE
        success = bool(self.mt5_adapter.close_position(ticket=ticket))
        if success:
            self.audit.log_order(
                ticket=ticket,
                order_id=f"manual_close_{ticket}",
                symbol="",
                action="Executed order",
                price=0.0,
                stop_loss=0.0,
                take_profit=0.0,
                volume=0.0,
                reason="MANUAL_CLOSE via operator surface",
                latency=0.0,
                execution_mode="MANUAL",
            )
            with self._live_tickets_lock:
                self._tickets_cache.pop_ticket(ticket)
        else:
            self._forced_exit_mechanisms.pop(ticket, None)
        return success

    def modify_position_manual(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        """Operator manual SL/TP modify routed through the manager (INV-004)."""
        success = bool(
            self.mt5_adapter.modify_position(
                ticket=ticket, stop_loss=stop_loss, take_profit=take_profit
            )
        )
        if success:
            self.audit.log_order(
                ticket=ticket,
                order_id=f"manual_modify_{ticket}",
                symbol="",
                action="Modified order",
                price=0.0,
                stop_loss=stop_loss,
                take_profit=take_profit,
                volume=0.0,
                reason="MANUAL_MODIFY via operator surface",
                latency=0.0,
                execution_mode="MANUAL",
            )
        return success

    # =========================================================================
    # BROKER-VERIFIED PENDING CANCELLATION (BUG-072/073)
    # -------------------------------------------------------------------------
    # A pending order is considered CANCELED only when broker state confirms
    # it. `cancel_pending_order()` returning False (e.g. retcode 0 = request
    # never reached the server) must NEVER release the exposure slot. The
    # helper below sends the cancel, then verifies with orders_get() and
    # history_orders_get() before declaring success.
    # =========================================================================

    def refresh_live_tickets_cache(
        self, symbol: str | None = None, current_tick: TickData | None = None
    ) -> None:
        """Rebuilds the internal live-tickets cache from the BROKER view.

        Broker truth wins: pendings present on the broker are added, pendings
        absent are dropped. Used after every cancellation and by the periodic
        reconciliation loop so a stale internal pending can never hold the
        exposure slot after the broker already removed the order.
        """
        positions: list[Position] = []
        try:
            positions = self.adapter.get_positions(symbol=symbol) or []
        except Exception as pos_err:
            logger.error("[RECONCILE] positions query failed (isolated)", error=str(pos_err))
        with self._live_tickets_lock:
            new_cache: dict[int, dict[str, Any]] = {}
            for pos in positions:
                new_cache[pos.ticket] = {
                    "ticket": pos.ticket,
                    "symbol": pos.symbol,
                    "price": pos.price_open,
                    "magic": getattr(pos, "magic", 888101),
                    "type": "POSITION",
                    "direction": pos.type.value,
                    "volume": pos.volume,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "profit": pos.profit,
                }
            try:
                get_pending_fn = getattr(self.adapter, "get_pending_orders", None)
                if get_pending_fn:
                    pendings = get_pending_fn(symbol=symbol)
                    if pendings:
                        for pending in pendings:
                            ticket = self._pending_field(pending, "ticket", "order_id")
                            if not ticket:
                                continue
                            pending_type = self._pending_field(pending, "type", "order_type")
                            pending_dir = (
                                "BUY"
                                if "BUY"
                                in str(getattr(pending_type, "value", pending_type)).upper()
                                else "SELL"
                            )
                            new_cache[int(ticket)] = {
                                "ticket": int(ticket),
                                "symbol": self._pending_field(
                                    pending, "symbol", default=symbol or ""
                                ),
                                "price": self._pending_field(
                                    pending, "price_open", "price", default=0.0
                                ),
                                "magic": self._pending_field(
                                    pending, "magic", "magic_number", default=888101
                                ),
                                "type": "PENDING",
                                "direction": pending_dir,
                                "volume": self._pending_field(pending, "volume", default=0.0),
                            }
            except Exception as pending_err:
                logger.error(
                    "[RECONCILE] pending query failed (isolated)",
                    error=str(pending_err),
                )
            # P0-A (BUG-140): pendings that were tracked internally but are now
            # GONE from the broker view AND were not removed by our own verified
            # cancel took a broker-side terminal path (EXPIRED at TTL, REJECTED
            # by the broker, or CANCELED through an external/manual action).
            # Emit the terminal outcome so the decision cannot hang forever.
            # Idempotent at the ledger; the fill path (POSITION bind) removes
            # the ticket from _pending_cancel_reasons before this sweep could
            # ever misfire for a filled order (fills appear as POSITIONs here,
            # not PENDINGs, and bind their own lifecycle).
            try:
                previous_pendings = {
                    int(t)
                    for t, info in self._live_tickets_cache.items()
                    if info.get("type") == "PENDING"
                }
                current_pendings = set(new_cache)
                vanished = previous_pendings - current_pendings
                for gone_ticket in sorted(vanished):
                    reason = self._pending_lifecycle.cancel_reason(gone_ticket)
                    if "AGE" in reason:
                        gone_state = DecisionLifecycle.EXPIRED_UNFILLED
                    elif reason:
                        # We cancelled it ourselves (verified path already
                        # emitted; the ledger dedup guard makes this a no-op).
                        gone_state = DecisionLifecycle.CANCELED_UNFILLED
                    else:
                        gone_state = DecisionLifecycle.EXPIRED_UNFILLED
                    self._emit_terminal_for_pending(
                        ticket=gone_ticket,
                        state=gone_state,
                        detail=f"reconcile sweep: pending vanished from broker view (last_reason={reason or 'none'})",
                    )
            except Exception as sweep_err:
                logger.error(
                    "[RECONCILE] terminal pending sweep failed (isolated)", error=str(sweep_err)
                )
            # S6 Phase-2: publish through the cache owner (never assign the
            # property name — it would shadow the @property).
            self._tickets_cache.swap(new_cache)

    def _is_closed_ticket(self, ticket: int) -> bool:
        """
        TASK-7 invariant guard: True when the ticket is positively closed or a close
        was already accepted, so no protective modification is ever issued for a
        position the broker no longer holds.
        """
        return bool(self._closed_tickets.get(ticket, False)) or bool(
            self.get_protection_state(ticket).close_requested
        )

    def _trading_blocked_by_safety_state(self) -> bool:
        """True when the persisted safety state refuses new trading (BUG-256).

        Delegates to the composition root's authority (LiveEngine.
        _trading_blocked_by_safety_state, registered as
        ``safety_state_provider``). DispatchEngine dispatch_order /
        execute_order consult THIS method, so a persisted HALTED /
        KILL_SWITCH row now actually blocks the primary and hedge dispatch
        paths on the real engine wiring (previously the hasattr probe on
        this manager was always False and the gate was inert).

        Fail-closed: a provider that raises, or a malformed verdict, is
        treated as BLOCKED — capital protection must never depend on the
        provider being healthy.
        """
        provider = getattr(self, "_safety_state_provider", None)
        if provider is None:
            return False
        try:
            return bool(provider())
        except Exception:
            logger.warning(
                "[SAFETY_STATE] trading_blocked provider raised — treating as BLOCKED",
                exc_info=True,
            )
            return True

    def _broker_close_verified(self, ticket: int) -> bool:
        """
        TASK-7 broker-verification bridge (BUG-087).

        DirectMT5Adapter already re-checks positions_get on ambiguous retcodes inside
        close_position. Remote/paper adapters return RPC/simulation status only; for
        them this helper re-queries the live position set to confirm the ticket is
        gone before the engine frees exposure or dispatches a follow-up. Falls back
        to the adapter's own truthfulness when get_positions is unavailable.
        Returns True only when the ticket is confirmed absent.
        """
        try:
            live = self.adapter.get_positions(symbol=None) or []
        except Exception:
            # Verification unavailable: trust the adapter's close result (the
            # Direct adapter is already self-verifying; paper simulation is
            # synchronous). Never treat an exception as proof the position is open.
            return True
        return not any(int(getattr(p, "ticket", 0) or 0) == int(ticket) for p in live)

    def _should_modify_sl(self, ticket: int, new_sl: float) -> bool:
        """Delegate: SL step gate (owned by ProtectionEngine, S10)."""
        return self._protection._should_modify_sl(ticket, new_sl)

    # -----------------------------------------------------------------
    # P0 seam S10: deterministic position protection composition. The
    # breakeven lock, ATR trailing stop, profit-giveback evaluation and
    # enforcement, and their pip/digits/contract resolvers are owned by
    # ProtectionEngine (execution/lifecycle/protection.py). Per-ticket
    # protection state stays in the manager's protection ledger; the
    # engine reads/writes it through `om`. The manager keeps only these
    # delegation shims under the historical names.
    # -----------------------------------------------------------------

    @property
    def _protection(self) -> ProtectionEngine:
        """Lazily composed protection engine (S10)."""
        eng: ProtectionEngine | None = getattr(self, "_protection_instance", None)
        if eng is None:
            eng = ProtectionEngine(self)
            self._protection_instance = eng
        return eng

    def _resolve_pip_size(self, symbol_info: SymbolInfo | None) -> float:
        """Delegate: canonical pip size (owned by ProtectionEngine, S10)."""
        return self._protection._resolve_pip_size(symbol_info)

    def _resolve_price_digits(self, symbol_info: SymbolInfo | None) -> int:
        """Delegate: broker price precision (owned by ProtectionEngine, S10)."""
        return self._protection._resolve_price_digits(symbol_info)

    def _atr_profit_threshold_usd(
        self,
        volume: float,
        symbol_info: SymbolInfo | None,
        atr: float,
    ) -> float:
        """Delegate: ATR trigger in USD PnL (owned by ProtectionEngine, S10)."""
        return self._protection._atr_profit_threshold_usd(volume, symbol_info, atr)

    def calculate_breakeven_sl(
        self,
        pos: Position,
        symbol_info: SymbolInfo | None = None,
    ) -> float:
        """Delegate: breakeven stop price (owned by ProtectionEngine, S10)."""
        return self._protection.calculate_breakeven_sl(pos, symbol_info)

    @staticmethod
    def _is_sl_at_or_beyond(pos: Position, sl_value: float, reference_sl: float) -> bool:
        """Delegate: protective-direction check (owned by ProtectionEngine, S10)."""
        return ProtectionEngine._is_sl_at_or_beyond(pos, sl_value, reference_sl)

    def refresh_protection_state(
        self,
        pos: Position,
        symbol_info: SymbolInfo | None = None,
    ) -> PositionProtectionState:
        """Delegate: broker-state protection reconciliation (owned by ProtectionEngine, S10)."""
        return self._protection.refresh_protection_state(pos, symbol_info)

    def _protective_sl_floor(self, ticket: int) -> float:
        """Delegate: confirmed breakeven lock floor (owned by ProtectionEngine, S10)."""
        return self._protection._protective_sl_floor(ticket)

    def is_sl_improvement(self, pos: Position, new_sl: float) -> bool:
        """Delegate: SL-tightening guard (owned by ProtectionEngine, S10)."""
        return self._protection.is_sl_improvement(pos, new_sl)

    def _log_protection_audit(
        self,
        pos: Position,
        action: str,
        reason: str,
        stop_loss: float = 0.0,
    ) -> None:
        """Delegate: protection audit row (owned by ProtectionEngine, S10)."""
        self._protection._log_protection_audit(pos, action, reason, stop_loss)

    def apply_breakeven_lock(
        self,
        pos: Position,
        symbol_info: SymbolInfo | None = None,
        atr: float = 0.0,
        min_stop_gap: float = 0.0,
        current_tick: TickData | None = None,
    ) -> bool:
        """Delegate: priority-4 breakeven lock (owned by ProtectionEngine, S10)."""
        return self._protection.apply_breakeven_lock(
            pos, symbol_info, atr, min_stop_gap, current_tick
        )

    def _maybe_tighten_protective_sl(
        self,
        pos: Position,
        state: "PositionProtectionState",
        symbol_info: SymbolInfo | None = None,
    ) -> bool:
        """Delegate: dynamic protective-SL tighten (owned by ProtectionEngine, S10)."""
        return self._protection._maybe_tighten_protective_sl(pos, state, symbol_info)

    def _resolve_contract_size(self, symbol_info: SymbolInfo | None) -> float:
        """Delegate: contract size resolver (owned by ProtectionEngine, S10)."""
        return self._protection._resolve_contract_size(symbol_info)

    def _log_throttled_be_failure(
        self,
        state: PositionProtectionState,
        pos: Position,
        message: str,
        breakeven_sl: float,
    ) -> None:
        """Delegate: throttled breakeven failure log (owned by ProtectionEngine, S10)."""
        self._protection._log_throttled_be_failure(state, pos, message, breakeven_sl)

    def _tiered_giveback_floor(self, ticket: int, peak: float) -> tuple[float, bool]:
        """Delegate: tiered retention floor (owned by ProtectionEngine, S10)."""
        return self._protection._tiered_giveback_floor(ticket, peak)

    def _warn_ai_flip_suspended(self, ticket: int, action: str) -> None:
        """TASK-EXIT-SEPARATION (c): ONE structured WARNING per ticket per
        interval when an AI direction-flip exit is suppressed. Rate-limited
        on the monotonic clock (no per-tick I/O, INV-001 respected); the
        per-ticket stamp lives in a plain dict, cleaned up with the ticket."""
        now_mono = time.monotonic()
        last = self._ai_flip_warn_times.get(ticket, 0.0)
        if (now_mono - last) >= _AI_FLIP_SUPPRESS_WARN_INTERVAL_SEC:
            self._ai_flip_warn_times[ticket] = now_mono
            logger.warning(
                "AI FLIP EXIT SUPPRESSED: ai_flip_exit_enabled=false (default); "
                "deterministic protection chain remains the exit authority",
                ticket=ticket,
                suppressed_action=action,
                flag="algo.ai_flip_exit_enabled",
            )

    def _exit_policy_config_value(self, field_name: str, module_constant: float) -> float:
        """TASK-EXIT-SEPARATION (A8): resolve one exit-policy scalar.

        Live AlgoConfig override -> module-constant fallback. Invalid
        (non-finite / non-positive) overrides never disable or corrupt
        protection; the constant always wins when the override is broken.
        """
        raw = getattr(self.algo_config, field_name, None)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return module_constant
        if not math.isfinite(value) or value <= 0.0:
            return module_constant
        return value

    def evaluate_profit_giveback(
        self,
        ticket: int,
        current_pnl_usd: float,
        base_hold_score: int,
    ) -> tuple[int, bool, str]:
        """Delegate: giveback evaluation + score override (owned by ProtectionEngine, S10)."""
        return self._protection.evaluate_profit_giveback(ticket, current_pnl_usd, base_hold_score)

    def enforce_profit_giveback_protection(
        self,
        pos: Position,
        hold_score: int,
        symbol_info: SymbolInfo | None = None,
        regime: str | None = None,
    ) -> tuple[int, bool]:
        """Delegate: giveback protection enforcement (owned by ProtectionEngine, S10)."""
        return self._protection.enforce_profit_giveback_protection(
            pos, hold_score, symbol_info, regime
        )

    def apply_atr_trailing_stop(
        self,
        pos: Position,
        price_current: float,
        atr: float,
        symbol_info: SymbolInfo | None = None,
        min_stop_gap: float = 0.0,
        current_tick: TickData | None = None,
    ) -> bool:
        """Delegate: priority-5 ATR trailing stop (owned by ProtectionEngine, S10)."""
        return self._protection.apply_atr_trailing_stop(
            pos, price_current, atr, symbol_info, min_stop_gap, current_tick
        )

    # =========================================================================
    # DETERMINISTIC POSITION PROTECTION LAYER
    # -------------------------------------------------------------------------
    # Root-cause fix for the profit-giveback incident on ticket #152465527595:
    # a scalp reached +$30.74 unrealized, was never protected, gave the profit
    # back and closed at roughly -$96.86 while hold_score stayed at 90-100.
    #
    # The layer below is deterministic, stateful (per MT5 ticket), idempotent and
    # restart-safe: the in-memory flag is never the sole source of truth, the
    # broker-reported SL is re-inspected on every refresh.
    # =========================================================================

    def get_protection_state(self, ticket: int) -> PositionProtectionState:
        """
        Returns (creating on first use) the protection state bound to this MT5 ticket.

        Delegates to the protection ledger (P0 seam S1); signature and
        lazy-creation semantics unchanged.
        """
        return self._protection_ledger.get(ticket)

    def should_emit_console_telemetry(self, ticket: int, now: float | None = None) -> bool:
        """
        Console/stdout telemetry gate: at most one emission every
        `TELEMETRY_CONSOLE_INTERVAL_SEC` seconds PER TICKET (first event always passes).

        This throttle governs ONLY human-facing console output. SQLite audit records,
        trade-state persistence, risk events, SL modifications, close requests, errors
        and protection events are written through separate, unthrottled paths.
        """
        state = self.get_protection_state(ticket)
        current = time.monotonic() if now is None else float(now)

        if state.last_telemetry_log_time <= 0.0:
            state.last_telemetry_log_time = current
            return True

        if (current - state.last_telemetry_log_time) >= TELEMETRY_CONSOLE_INTERVAL_SEC:
            state.last_telemetry_log_time = current
            return True

        return False

    def _safe_feature_float(
        self, features: FeatureVector | None, attr_name: str, default: float
    ) -> float:
        """Safely extracts a floating point attribute from FeatureVector with fallback."""
        if features is None:
            return default
        val = getattr(features, attr_name, default)
        try:
            fval = float(val)
            if math.isnan(fval) or math.isinf(fval):
                return default
            return fval
        except (TypeError, ValueError):
            return default

    def _current_regime_str(self, regime_state: Any | None, ticket: int) -> str:
        """
        Resolves the CURRENT market-regime label for a ticket.

        Prefers the live `regime_state` threaded from the engine (Phase 15 exit
        audit); falls back to the entry snapshot when the live state is absent
        (e.g. unit tests, warmup-gated ticks), so regime-aware exit logic never
        crashes on a missing input.
        """
        if regime_state is not None:
            with contextlib.suppress(Exception):
                regime = getattr(regime_state, "regime_type", None)
                if regime is not None:
                    return str(getattr(regime, "value", regime))
                return str(regime_state)
        fallback = self._entry_regimes.get(ticket, "")
        return str(fallback)

    def _estimate_liquidation_impact(
        self,
        volume: float,
        symbol_info: SymbolInfo | None,
        atr: float,
    ) -> tuple[float, float]:
        """
        Almgren-Chriss Temporary Market Impact Model (Strict O(1) Math).
        Calculates the expected slippage incurred if the position were to be liquidated via Market Order.
        Returns: (Total Impact in USD, Impact Price Delta per Oz)
        """
        # P0 seam S4: Almgren-Chriss math lives in position_intelligence.
        return _estimate_liquidation_impact(volume, symbol_info, atr, self.eta_coefficient)

    @property
    def _last_tick_for_ticket(self) -> dict[int, Any]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._last_tick_for_ticket

    @property
    def _last_tick_timestamps(self) -> dict[int, datetime]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._last_tick_timestamps

    @property
    def _time_in_profit_sec(self) -> dict[int, float]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._time_in_profit_sec

    @property
    def _time_in_drawdown_sec(self) -> dict[int, float]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._time_in_drawdown_sec

    @property
    def _peak_profit_usd(self) -> dict[int, float]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._peak_profit_usd

    @property
    def _peak_drawdown_usd(self) -> dict[int, float]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._peak_drawdown_usd

    @property
    def _lsf_state(self) -> dict[int, dict[str, float]]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._lsf_state

    @property
    def _last_seen_ts(self) -> dict[int, datetime]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._last_seen_ts

    @property
    def _stagnation_ticks(self) -> dict[int, int]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._stagnation_ticks

    @property
    def _adverse_ticks(self) -> dict[int, int]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._adverse_ticks

    @property
    def _favorable_ticks(self) -> dict[int, int]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._favorable_ticks

    @property
    def _last_price_tracker(self) -> dict[int, float]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._last_price_tracker

    @property
    def _mfe_tracker(self) -> dict[int, float]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._mfe_tracker

    @property
    def _mae_tracker(self) -> dict[int, float]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._mae_tracker

    @property
    def _time_to_mfe_sec(self) -> dict[int, float]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._time_to_mfe_sec

    @property
    def _time_to_mae_sec(self) -> dict[int, float]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._time_to_mae_sec

    @property
    def _reversal_events(self) -> dict[int, list[dict[str, Any]]]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._reversal_events

    @property
    def _entry_probs(self) -> dict[int, dict[str, float]]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._entry_probs

    @property
    def _entry_regime_state(self) -> dict[int, str]:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._entry_regime_state

    @property
    def _hold_score_tracker(self) -> dict[int, int]:
        """Compatibility accessor — live hold-score dict owned by the ledger."""
        return self._hold_scores._hold_score_tracker

    @property
    def _base_hold_score_tracker(self) -> dict[int, int]:
        """Compatibility accessor — live hold-score dict owned by the ledger."""
        return self._hold_scores._base_hold_score_tracker

    @property
    def _last_reasons_tracker(self) -> dict[int, list[str]]:
        """Compatibility accessor — live hold-score dict owned by the ledger."""
        return self._hold_scores._last_reasons_tracker

    @property
    def _last_hold_eval_time(self) -> dict[int, float]:
        """Compatibility accessor — live hold-score dict owned by the ledger."""
        return self._hold_scores._last_hold_eval_time

    @property
    def _last_telemetry_time(self) -> dict[int, float]:
        """Compatibility accessor — live throttle dict owned by TelemetryThrottle."""
        return self._telemetry._last_telemetry_time

    @property
    def _live_tickets_cache(self) -> dict[int, dict[str, Any]]:
        """Compatibility accessor — live cache dict owned by TicketsCache.
        Writers must use _tickets_cache.swap()/pop_ticket() under
        _live_tickets_lock; readers get the live dict (web/debug parity)."""
        return self._tickets_cache.cache

    def _ensure_ticket_bootstrap(
        self,
        ticket: int,
        now: datetime,
        price_current: float,
        profit_price_delta: float,
        net_price_delta: float,
    ) -> None:
        """Delegate — state owned by PositionTrackingLedger (S6-followup)."""
        self._tracking.ensure_bootstrap(
            ticket, now, price_current, profit_price_delta, net_price_delta
        )

    def _update_lsf_desync_metrics(
        self,
        ticket: int,
        now: datetime,
        price_current: float,
        profit_price_delta: float,
        net_price_delta: float,
        atr: float,
    ) -> None:
        """Delegate — state owned by PositionTrackingLedger (S6-followup)."""
        self._tracking.update_lsf_desync_metrics(
            ticket, now, price_current, profit_price_delta, net_price_delta, atr
        )

    def _lsf_get(self, ticket: int, key: str, default: float = 0.0) -> float:
        st = self._lsf_state.get(ticket)
        if not st:
            return default
        try:
            return float(st.get(key, default))
        except Exception:
            return default

    def _lsf_set(self, ticket: int, key: str, value: float) -> None:
        st = self._lsf_state.get(ticket)
        if not st:
            self._lsf_state[ticket] = {}
            st = self._lsf_state[ticket]
        st[key] = float(value)

    def _update_tick_state(
        self,
        ticket: int,
        pos: Position,
        price_current: float,
        profit_price_delta: float,
    ) -> None:
        """Delegate — state owned by PositionTrackingLedger (S6-followup)."""
        self._tracking.update_tick_state(ticket, pos, price_current, profit_price_delta)

    def _calculate_smart_position_metrics(
        self,
        pos: Position,
        price_current: float,
        mid_price: float,
        spread: float,
        atr: float,
        net_price_delta: float,
        gross_price_delta: float,
        impact_price_delta: float,
        total_impact_usd: float,
        holding_duration: float,
        features: FeatureVector | None,
        symbol_info: SymbolInfo | None,
    ) -> dict[str, Any]:
        """Calculates 57 derived O(1) position metrics."""
        ticket = pos.ticket
        # P0 seam S4: the 57-metric kernel lives in
        # execution/position_intelligence.py (pure, verbatim formulas).
        return calculate_smart_metrics(
            SmartMetricsInputs(
                pos=pos,
                price_current=price_current,
                mid_price=mid_price,
                spread=spread,
                atr=atr,
                net_price_delta=net_price_delta,
                gross_price_delta=gross_price_delta,
                impact_price_delta=impact_price_delta,
                total_impact_usd=total_impact_usd,
                holding_duration=holding_duration,
                features=features,
                symbol_info=symbol_info,
                be_trigger=self.be_trigger,
                trailing_distance=self.trailing_distance,
                max_holding_seconds=self.max_holding_seconds,
                atr_sl_buffer_multiplier=getattr(self.algo_config, "atr_sl_buffer_multiplier", 1.5),
                rescue_registered=ticket in self._rescue_registered_tickets,
                lsf_desync_score=self._lsf_get(ticket, "desync_score", 0.0),
                mfe=self._mfe_tracker.get(ticket, gross_price_delta),
                mae=self._mae_tracker.get(ticket, gross_price_delta),
                adverse_ticks=self._adverse_ticks.get(ticket, 0),
                favorable_ticks=self._favorable_ticks.get(ticket, 0),
                stagnation_ticks=self._stagnation_ticks.get(ticket, 0),
            )
        )

    def _recalculate_hold_score_with_position_state(
        self,
        ticket: int,
        base_score: int,
        metrics: dict[str, Any],
        reasons: list[str],
    ) -> int:
        score = base_score
        desync_score = float(metrics.get("desync_score", 0.0))
        if desync_score > 25.0:
            score -= 20
            reasons.append(f"CRITICAL_LSF_DESYNC_PENALTY (Score: {desync_score:.1f})")

        if metrics.get("no_sl_risk", False):
            score -= 15
            reasons.append("UNPROTECTED_NO_STOP_LOSS_RISK")

        return max(0, min(100, score))

    # =========================================================================
    # 60-SCENARIO DETERMINISTIC POSITION MANAGEMENT ROUTER
    # =========================================================================

    def _resolve_position_management_scenario(
        self,
        pos: Position,
        hold_score: int,
        metrics: dict[str, Any],
        net_delta: float,
        gross_delta: float,
        atr: float,
        spread: float,
        holding_duration: float,
        min_stop_gap: float,
    ) -> tuple[str, str]:
        """
        60-Scenario Router with strict Profit Shield (Never closes winning trades prematurely).
        """
        atr_n = max(atr, 0.50)
        spread_ratio = spread / atr_n
        net_atr = net_delta / atr_n

        ticket = pos.ticket
        mae = self._mae_tracker.get(ticket, 0.0)
        mae_atr = mae / atr_n

        desync = float(metrics.get("desync_score", 0.0) or 0.0)
        toxicity_score = float(metrics.get("impact_to_net_profit_ratio", 0.0) or 0.0)
        danger_tier = str(metrics.get("danger_tier", "NORMAL") or "NORMAL")

        kill_switch = bool(metrics.get("kill_switch_required", False))
        timeout_exit = bool(metrics.get("time_decay_exit_required", False))
        defer_stops = bool(metrics.get("defer_stop_management", False))
        defer_scale = bool(metrics.get("defer_scale_out", False))
        missed_rescue = bool(metrics.get("missed_position_rescue_mode", False))
        spread_spike = bool(spread_ratio > 0.25)

        is_winning_trade = bool(net_delta > 0.0 or gross_delta > 0.0)

        # PROFIT-SHIELD GUARD: Never close winning trades in emergency bailout scenarios
        if kill_switch and danger_tier == "CRITICAL_KILL" and not is_winning_trade:
            return "CLOSE", "S01_CRITICAL_COMPOUND_KILL_SWITCH"
        elif kill_switch and toxicity_score >= 4.5 and not is_winning_trade:
            return "CLOSE", "S02_TOXIC_FLOW_KILL_SWITCH"
        elif hold_score < 30 and not is_winning_trade:
            return "CLOSE", "S09_CRITICAL_HOLD_SCORE_BREACH_BAILOUT"
        elif mae_atr >= 1.20 and net_atr <= -0.40 and not is_winning_trade:
            return "CLOSE", "S08_EXCESSIVE_MAE_DRAWDOWN_CUT"
        elif hold_score <= 20 and net_atr <= -0.35 and not is_winning_trade:
            return "CLOSE", "S04_STRUCTURE_FAILURE_WITH_ACTIVE_LOSS"
        elif toxicity_score >= 4.8 and net_atr < -0.20 and not is_winning_trade:
            return "CLOSE", "S05_EXTREME_TOXICITY_NEGATIVE_POSITION"
        elif desync >= 30.0 and net_atr <= -0.50 and not is_winning_trade:
            return "CLOSE", "S06_SEVERE_DESYNC_WITH_UNCONTROLLED_LOSS"
        elif spread_ratio >= 0.40 and net_atr <= -0.50 and not is_winning_trade:
            return "CLOSE", "S07_CATASTROPHIC_SPREAD_EXPANSION"
        elif hold_score <= 10 and net_atr <= -0.25 and not is_winning_trade:
            return "CLOSE", "S10_TERMINAL_HOLD_SCORE_FAILURE"

        elif hold_score < 25 and net_atr <= -0.50 and not is_winning_trade:
            return "CLOSE", "S11_DEEP_LOW_SCORE_BAILOUT"
        elif hold_score < 35 and net_atr <= -0.65 and not is_winning_trade:
            return "CLOSE", "S12_CONFIRMED_LOW_SCORE_BAILOUT"
        elif hold_score < 50 and net_delta < 0.0 and net_atr <= -0.40 and not is_winning_trade:
            return "CLOSE", "S13_STANDARD_EARLY_EMERGENCY_BAILOUT"

        elif timeout_exit and net_delta < 0.10 and not is_winning_trade:
            return "CLOSE", "S21_HARD_STAGNATION_TIMEOUT"
        elif (
            holding_duration > self.max_holding_seconds * 1.50
            and net_atr < 0.0
            and not is_winning_trade
        ):
            return "CLOSE", "S22_EXTENDED_CAPITAL_LOCK_TIMEOUT"

        # Scale out & trailing for winning/healthy trades
        elif net_atr >= 1.50 and not defer_scale:
            return "PARTIAL_CLOSE", "S32_HIGH_PROFIT_SCALE_OUT"
        elif net_atr >= 0.90 and hold_score >= 65:
            return "NORMAL_TRAIL", "S44_HEALTHY_WINNER_NORMAL_TRAIL"
        elif net_delta >= (self.be_trigger * 0.4) and hold_score >= 40:
            return "BREAK_EVEN", "S47_STANDARD_BREAK_EVEN_LOCK"
        elif net_atr >= 0.45:
            return "BREAK_EVEN", "S48_LOW_IMPACT_FAST_BREAK_EVEN"

        elif defer_stops and spread_spike:
            return "DEFER_STOPS", "S52_SPREAD_SPIKE_STOP_DEFER"
        elif missed_rescue and net_atr <= 0.0:
            return "MONITOR", "S56_MISSED_POSITION_STATE_RECONSTRUCTION"
        else:
            return "HOLD", "S60_DEFAULT_CONTROLLED_HOLD"

    # =========================================================================
    # ACTIVE POSITION MONITORING & LIFECYCLE EXECUTION LOOP
    # =========================================================================

    def _ledger_account_source(self) -> str:
        """BUG-226: execution provenance of the currently bound adapter.

        'PAPER' when the simulation adapter is bound, otherwise 'LIVE'.
        Read at write time (never cached) so a set_execution_mode hot-swap is
        reflected on every subsequent ledger row. Never raises: provenance is
        observability and must not block the trade path on a probe failure.
        """
        try:
            from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

            if isinstance(self.adapter, PaperMT5Adapter):
                return "PAPER"
        except Exception:
            pass
        return "LIVE"

    # ------------------------------------------------------------------
    # P0 seam S3 compatibility surface: the historical attribute names
    # now resolve to the ledger's LIVE dicts (same objects — tests and
    # internals that read/write them keep working with no duplication).
    # ------------------------------------------------------------------
    @property
    def _recovery_budget_initial(self) -> dict[int, float]:
        return self._recovery_ledger.recovery_budget_initial

    @property
    def _recovery_budget_remaining(self) -> dict[int, float]:
        return self._recovery_ledger.recovery_budget_remaining

    @property
    def _recovery_budget_consumed(self) -> dict[int, float]:
        return self._recovery_ledger.recovery_budget_consumed

    @property
    def _recovery_initial_loss(self) -> dict[int, float]:
        return self._recovery_ledger.recovery_initial_loss

    @property
    def _recovery_entry_times(self) -> dict[int, datetime]:
        return self._recovery_ledger.recovery_entry_times

    @property
    def _recovery_horizons(self) -> dict[int, float]:
        return self._recovery_ledger.recovery_horizons

    def _initialize_recovery_mode(
        self,
        ticket: int,
        current_pnl_usd: float,
        confidence_factor: float,
        atr: float,
        trend_strength: float,
        now: datetime,
    ) -> None:
        """
        Initializes an immutable USD recovery budget and dynamic time horizon
        when the position first enters negative PnL.
        """
        # P0 seam S3: allocation rules live in the ledger (verbatim).
        # Faithful to the original guard: an already-allocated ticket returns
        # BEFORE the [RECOVERY ENVELOPE LOCKED] log (no duplicate log emission).
        if self._recovery_ledger.is_allocated(ticket):
            return
        initial_risk_usd = self._initial_risks.get(ticket, 0.0)
        budget = self._recovery_ledger.allocate(
            ticket,
            initial_risk_usd=self._initial_risks.get(ticket, 0.0),
            current_pnl_usd=current_pnl_usd,
            confidence_factor=confidence_factor,
            atr=atr,
            trend_strength=trend_strength,
            now=now,
            algo_config=self.algo_config,
        )
        horizon = self._recovery_ledger.recovery_horizons[ticket]
        logger.info(
            "[RECOVERY ENVELOPE LOCKED]",
            ticket=ticket,
            initial_risk=f"${initial_risk_usd:.2f}",
            locked_budget=f"${budget:.2f}",
            horizon_sec=round(horizon, 1),
            initial_loss=f"${current_pnl_usd:.2f}",
        )

    def _evaluate_recovery_budget_and_horizon(
        self,
        ticket: int,
        current_pnl_usd: float,
        now: datetime,
    ) -> tuple[bool, str]:
        """
        Evaluates the immutable recovery budget and dynamic time horizon.
        Returns (is_exhausted, reason).
        """
        return self._recovery_ledger.evaluate_exhaustion(ticket, current_pnl_usd, now)

    # ------------------------------------------------------------------
    # P0 seam S2 compatibility surface: the historical attribute names
    # resolve to the state machine's LIVE dicts (same objects — external
    # readers and tests keep working with no duplication).
    # ------------------------------------------------------------------
    @property
    def _position_states(self) -> dict[int, PositionState]:
        return self._state_machine._states

    @property
    def _state_transition_candidates(
        self,
    ) -> dict[int, tuple[PositionState, datetime, int]]:
        return self._state_machine._candidates

    def transition_state_with_hysteresis(
        self,
        ticket: int,
        target_state: PositionState,
        now: datetime,
    ) -> PositionState:
        """
        Manages state transitions with count-based and time-based hysteresis debouncing.
        Emergency/safety/catastrophic giveback states bypass debouncing with zero latency.
        """
        # P0 seam S2: transition rules live in the state machine (verbatim).
        return self._state_machine.transition_with_hysteresis(ticket, target_state, now)

    def _evaluate_candidate_state(
        self,
        ticket: int,
        pos: Position,
        evidence: dict[str, float],
        pnl_features: dict[str, float],
    ) -> PositionState:
        """
        Maps continuous evidence scores and trajectory features into one of the 11 explicit PositionStates.
        """
        pnl = pos.profit
        is_profitable = pnl >= 0.0

        # Check for catastrophic profit giveback FIRST (before positive/negative split)
        state_p = self.get_protection_state(ticket)
        retention = state_p.retention_ratio(pnl)
        if state_p.peak_win_usd >= PROFIT_GIVEBACK_PEAK_USD:
            retention_floor, armed = self._tiered_giveback_floor(ticket, state_p.peak_win_usd)
            if pnl < 0.0 or (armed and retention <= retention_floor):
                return PositionState.PROFIT_GIVEBACK_CRITICAL
            elif armed and retention < 0.70:
                return PositionState.PROFIT_GIVEBACK_WARNING

        if is_profitable:
            # PROFIT STATES
            if self._sl_modified_flags.get(ticket, False):
                # If trailing is already active
                return PositionState.PROFIT_TRAILING
            elif pnl >= BREAKEVEN_PROFIT_USD:
                return PositionState.PROFIT_PROTECTED
            else:
                return PositionState.PROFIT_UNPROTECTED

        else:
            # LOSS/RECOVERY STATES
            recovery_score = evidence.get("recovery_score", 0.50)
            adverse_score = evidence.get("adverse_score", 0.50)

            # Recovery attempts are budget-capped per ticket: once the budget is
            # spent (or adverse excursion blows past 0.80) stop managing the loss
            # and hard-exit instead of giving the recovery path more rope.
            budget_remaining = self._recovery_ledger.remaining(ticket, 1.0)

            if budget_remaining <= 0.0 or adverse_score > 0.80:
                return PositionState.LOSS_HARD_EXIT

            if recovery_score >= 0.70 and adverse_score < 0.20:
                return PositionState.LOSS_RECOVERY_CONFIRMED
            elif recovery_score >= 0.45:
                return PositionState.LOSS_RECOVERY_CANDIDATE
            elif recovery_score < 0.30:
                return PositionState.LOSS_EXIT_PRESSURE
            else:
                return PositionState.LOSS_RECOVERY_FAILING

    def _arbitrate_decision(
        self,
        ticket: int,
        pos: Position,
        legacy_action: str,
        legacy_scenario: str,
        adaptive_state: PositionState,
        current_pnl_usd: float,
        evidence: dict[str, float],
        now: datetime | None = None,
    ) -> tuple[str, str]:
        """
        Arbitrates the final execution action across the hierarchy levels:
        1. Emergency safety cuts / Broker stops rules (VETO power)
        2. Deterministic protection (BE / Giveback)
        3. Adaptive position exit pressure
        4. Strategy / Router suggestions
        5. Default HOLD

        Ensures that HOLD can never override a protective EXIT/CLOSE action.
        """
        # Calculate time in trade for Spread Overcome Grace Period
        # NOTE: `now` is the CURRENT TICK timestamp threaded from the management
        # loop. Never derive age from the host wall clock: the broker/server clock
        # can be hours ahead of the host, which produced negative ages
        # (e.g. "Age: -10781.6s") and suppressed every time-based exit.
        entry_time = self._entry_timestamps.get(ticket)
        if entry_time:
            if now is not None:
                duration_sec = (now - entry_time).total_seconds()
            else:
                # G3 (audit rev2): no tick timestamp threaded in — deriving the
                # age from the host wall clock is what produced corrupted
                # hold-time penalties on DST/NTP jumps (e.g. "Age: -10781.6s").
                # Rate-limited loud WARNING + conservative 0.0 (inside the
                # 60s grace window) instead of a wall-clock-derived age.
                now_mono = time.monotonic()
                if (now_mono - getattr(self, "_hold_age_fallback_warned_at", 0.0)) >= 300.0:
                    self._hold_age_fallback_warned_at = now_mono
                    logger.warning(
                        "[POSITION] event=HOLD_AGE_FALLBACK "
                        "mode=no_tick_timestamp_conservative_zero "
                        "ticket=%s entry_time_present=%s "
                        "(tick timestamp missing in management loop; "
                        "wall-clock age suppressed — G3 audit rev2)",
                        ticket,
                        True,
                    )
                duration_sec = 0.0
        else:
            duration_sec = 0.0

        # 60-Second Minimum Survival Grace Period
        # Prevents ANY algorithm from closing a trade instantly upon entry before it has a chance to breathe through the spread
        # Determine if this is a hard legacy/rule-matrix cut that must be honored.
        # The S-code list covers the legacy router's emergency scenarios; rule-matrix
        # CLOSE verdicts (RULE_* reasons, e.g. RULE_TIME_DECAY_CHOP_EXIT) are
        # deterministic rule outcomes and must be honored the same way. Without
        # this, a rule-matrix CLOSE fell through to the default HOLD (Phase 15
        # exit audit: "exit generated but swallowed" defect class).
        is_legacy_emergency_cut = legacy_action == "CLOSE" and any(
            code in legacy_scenario
            for code in (
                "S01",
                "S02",
                "S04",
                "S05",
                "S06",
                "S07",
                "S08",
                "S09",
                "S10",
                "S11",
                "S12",
                "S13",
                "S21",
                "S22",
            )
        )
        if legacy_action == "CLOSE" and legacy_scenario.startswith("RULE_"):
            is_legacy_emergency_cut = True
        if duration_sec < 60.0:
            if adaptive_state in (PositionState.LOSS_HARD_EXIT, PositionState.LOSS_EXIT_PRESSURE):
                # Suppress instant exits so the trade can breathe through initial entry spread
                adaptive_state = PositionState.LOSS_RECOVERY_CANDIDATE
            if (
                is_legacy_emergency_cut
                and "S01_CRITICAL_COMPOUND_KILL_SWITCH" not in legacy_scenario
            ):
                # Force HOLD for legacy cuts during the grace period unless it's a global kill switch
                logger.debug(
                    f"Grace Period Override: Suppressed early legacy cut '{legacy_scenario}' for ticket {ticket}. Age: {duration_sec:.1f}s"
                )
                is_legacy_emergency_cut = False
                legacy_action = "HOLD"

        # Level 1: Hard Emergency/Safety Cuts from Legacy Router
        if is_legacy_emergency_cut:
            logger.info(
                f"[EXIT TRACE] Legacy Emergency Cut triggered: {legacy_scenario} for ticket {ticket}. Age: {duration_sec:.1f}s"
            )
            return "CLOSE", legacy_scenario

        # Level 2: Adaptive/Deterministic safety constraints (Recovery budget or Horizon exhausted)
        if adaptive_state == PositionState.LOSS_HARD_EXIT:
            logger.info(
                f"[EXIT TRACE] LOSS_HARD_EXIT triggered for ticket {ticket}. Age: {duration_sec:.1f}s"
            )
            return "CLOSE", "LOSS_HARD_EXIT: recovery budget exhausted or adverse pressure too high"

        # Minimum loss optimization check (Requirement 13)
        initial_risk = self._initial_risks.get(ticket, 0.0)
        if current_pnl_usd < 0.0 and initial_risk > 0.0:
            should_exit, opt_reason = self._evaluate_minimum_loss_optimization(
                ticket, current_pnl_usd, initial_risk, evidence, now=now
            )
            if should_exit:
                # Logging is already handled inside the sub-function for EV traces
                return "CLOSE", opt_reason

        if adaptive_state == PositionState.PROFIT_GIVEBACK_CRITICAL:
            logger.info(
                f"[EXIT TRACE] PROFIT_GIVEBACK_CRITICAL triggered for ticket {ticket}. Age: {duration_sec:.1f}s"
            )
            return "CLOSE", "PROFIT_GIVEBACK_CRITICAL: profit eroded below floor retention"

        # Level 3: Adaptive Exit Pressure
        if adaptive_state == PositionState.LOSS_EXIT_PRESSURE:
            # Low recovery probability -> Exit rather than hoping
            logger.info(
                f"[EXIT TRACE] LOSS_EXIT_PRESSURE triggered for ticket {ticket}. RecProb: {evidence.get('recovery_score', 0.0):.2%}, Age: {duration_sec:.1f}s"
            )
            return (
                "CLOSE",
                f"LOSS_EXIT_PRESSURE: low recovery score ({evidence.get('recovery_score', 0.0):.2%})",
            )

        # Level 4: Trailing Stop / Breakeven Actions
        # If legacy wants BREAK_EVEN or NORMAL_TRAIL, and we are in a protected state:
        if legacy_action in ("BREAK_EVEN", "NORMAL_TRAIL", "PARTIAL_CLOSE", "MODIFY_SL"):
            return legacy_action, legacy_scenario

        # If adaptive state suggests giveback warning, tighten stop
        if adaptive_state == PositionState.PROFIT_GIVEBACK_WARNING:
            return "MODIFY_SL", "PROFIT_GIVEBACK_WARNING: tightening profit protection"

        # Otherwise, default to HOLD
        return "HOLD", "S60_DEFAULT_CONTROLLED_HOLD"

    def evaluate_falling_knife_protection(
        self,
        symbol: str,
        current_tick: TickData,
        positions: list[Position],
        atr: float,
    ) -> None:
        """
        Part 5: Falling Knife Protection.
        If a position has strong unrealized profit, expanding momentum, and price acceleration,
        cancel opposite limit orders to prevent catching the falling knife.
        """
        try:
            get_pending_fn = getattr(self.adapter, "get_pending_orders", None)
            cancel_fn = getattr(self.adapter, "cancel_pending_order", None)
            if not get_pending_fn or not cancel_fn or not positions:
                return

            pending_orders = get_pending_fn(symbol=symbol)
            if not pending_orders:
                return

            for pos in positions:
                # Strong unrealized profit threshold
                if pos.profit > (atr * 20.0):  # Profitable trend detected
                    is_sell_trend = pos.type == OrderType.SELL
                    is_buy_trend = pos.type == OrderType.BUY

                    # Trigger Falling Knife protection
                    for pending in pending_orders:
                        pending_ticket = self._pending_field(pending, "ticket", "order_id")
                        pending_type = self._pending_field(pending, "type", "order_type")
                        pending_type_str = str(
                            getattr(pending_type, "value", pending_type) or ""
                        ).upper()

                        # If we have a profitable SELL trend, cancel opposite BUY_LIMITS
                        # If we have a profitable BUY trend, cancel opposite SELL_LIMITS
                        should_cancel = False
                        if is_sell_trend and "BUY_LIMIT" in pending_type_str:
                            should_cancel = True
                        elif is_buy_trend and "SELL_LIMIT" in pending_type_str:
                            should_cancel = True

                        if should_cancel and pending_ticket:
                            # BUG-140/BUG-164: remember WHY so the verified-cancel
                            # terminal outcome classifies CANCELED_UNFILLED (not
                            # the reconcile-sweep default EXPIRED_UNFILLED).
                            self._pending_lifecycle.note_cancel_reason(
                                pending_ticket, "FALLING_KNIFE_PROTECTION"
                            )
                            # BUG-072/073: broker-verified cancellation.
                            if self.cancel_pending_order_verified(
                                ticket=pending_ticket, symbol=symbol
                            ):
                                self._pending_lifecycle.drop_ticket(pending_ticket)
                                logger.info(
                                    f"FALLING_KNIFE_PROTECTION: Cancelled counter pending order {pending_ticket} due to strong opposite momentum."
                                )
                                self.audit.log_order(
                                    ticket=pending_ticket,
                                    order_id=f"cancel_fk_{pending_ticket}",
                                    symbol=symbol,
                                    action="Cancelled order",
                                    price=float(
                                        self._pending_field(
                                            pending, "price_open", "price", default=0.0
                                        )
                                        or 0.0
                                    ),
                                    stop_loss=float(
                                        self._pending_field(pending, "sl", "stop_loss", default=0.0)
                                        or 0.0
                                    ),
                                    take_profit=float(
                                        self._pending_field(
                                            pending, "tp", "take_profit", default=0.0
                                        )
                                        or 0.0
                                    ),
                                    volume=float(
                                        self._pending_field(pending, "volume", default=0.01) or 0.01
                                    ),
                                    reason="FALLING_KNIFE_PROTECTION",
                                    latency=0.0,
                                    execution_mode="STANDARD",
                                )
        except Exception as err:
            logger.error("Failed to run Falling Knife Protection", error=str(err))

    def manage_active_positions(
        self,
        symbol: str,
        current_tick: TickData,
        feature_vector: FeatureVector | None = None,
        symbol_info: SymbolInfo | None = None,
        probs: Any | None = None,
        account: Any = None,
        regime_state: Any | None = None,
    ) -> list[Position]:
        """
        Main in-trade lifecycle pass: pending-order guard, falling-knife protection,
        MAE/MFE excursion tracking, hold-score routing, and one ledger autopsy row for
        every ticket that has disappeared from the broker's open-positions list.

        `probs` and `regime_state` are the CURRENT tick's model probabilities and
        market-regime state (Phase 15 exit audit): the AI direction-flip exit and
        the adaptive evidence scores must observe the live model/regime, not the
        static entry snapshot.
        """
        atr = max(self._safe_feature_float(feature_vector, "atr_m1", 0.80), 0.50)

        # Refresh the account snapshot so autopsy rows carry accurate post-trade balance,
        # equity and drawdown values.
        if account is not None:
            self.update_account_snapshot(account)

        self.manage_pending_orders(
            symbol=symbol, current_tick=current_tick, symbol_info=symbol_info, atr=atr
        )

        positions = self.adapter.get_positions(symbol=symbol)

        # Apply Falling Knife Protection
        if positions:
            self.evaluate_falling_knife_protection(
                symbol=symbol, current_tick=current_tick, positions=positions, atr=atr
            )

        # Re-build live tickets cache thread-safely
        with self._live_tickets_lock:
            # S6 Phase-2: cache rebuild owned by TicketsCache (verbatim
            # algorithm; swap still happens under _live_tickets_lock here).
            new_cache = self._tickets_cache.rebuild(
                positions=positions,
                pending_lookup=(
                    (lambda: self.adapter.get_pending_orders_snapshot(symbol=symbol))
                    if getattr(self.adapter, "get_pending_orders_snapshot", None)
                    else None
                ),
                pending_field=self._pending_field,
                symbol=symbol,
            )
            self._tickets_cache.swap(new_cache)

        # BUG-072/073: periodic broker-truth reconciliation of the internal
        # pending/position view. Broker wins; mismatch is repaired. Bounded
        # and isolated - never disturbs the tick path on failure.
        try:
            rep = self.reconcile_pending_state(symbol=symbol, current_tick=current_tick)
            if rep["mismatch"]:
                logger.warning(
                    "[EXECUTION_RECONCILIATION] event=MISMATCH "
                    "pending_internal=%s pending_broker=%s repaired=%s",
                    rep["pending_internal"],
                    rep["pending_broker"],
                    rep["repaired"],
                )
        except Exception as reconcile_err2:
            logger.error(
                "[EXECUTION_RECONCILIATION] event=FAILED (isolated)",
                error=str(reconcile_err2),
            )

        now = current_tick.timestamp

        # Phase 14: reconciliation close-loop (BUG-045). Runs BEFORE the
        # dead-ticket sweep: tracked tickets are skipped by the
        # _entry_timestamps guard, while broker-closed tickets that internal
        # state never tracked (restart gap) are discovered here and routed
        # through the same autopsy + experience outcome path. Best-effort and
        # never raising. Runs even when no positions are currently open.
        try:
            self.reconcile_missed_closes(
                symbol=symbol,
                current_tick=current_tick,
                symbol_info=symbol_info,
            )
        except Exception as reconcile_err:
            logger.error(
                "[RECONCILIATION] close-loop failed (isolated)",
                error=str(reconcile_err),
            )

        # S6 seam: vanished-ticket autopsy sweep extracted verbatim (same
        # object, same call position — after reconcile_missed_closes, before
        # the no-positions return).
        self._sweep_dead_tickets(
            symbol=symbol,
            positions=positions,
            current_tick=current_tick,
            now=now,
            symbol_info=symbol_info,
            atr=atr,
        )
        if not positions:
            return []

        min_stop_gap = (
            (symbol_info.stops_level * symbol_info.point)
            if symbol_info and symbol_info.stops_level > 0
            else 0.25
        )
        spread = max(current_tick.ask - current_tick.bid, 0.0)
        mid_price = (current_tick.ask + current_tick.bid) * 0.5

        # Append to rolling average spreads
        self._rolling_spreads.append(spread)
        if len(self._rolling_spreads) > 50:
            self._rolling_spreads.pop(0)

        for pos in positions:
            ticket = pos.ticket

            if ticket not in self._entry_timestamps:
                pos_time = getattr(pos, "time_setup", None) or getattr(pos, "time", None) or now
                self._entry_timestamps[ticket] = pos_time
                self._last_tick_timestamps[ticket] = now
                self._time_in_profit_sec[ticket] = 0.0
                self._time_in_drawdown_sec[ticket] = 0.0
                self._peak_profit_usd[ticket] = 0.0
                self._peak_drawdown_usd[ticket] = 0.0

                # Track entry details
                self._entry_prices[ticket] = pos.price_open
                self._entry_sls[ticket] = pos.sl
                self._entry_tps[ticket] = pos.tp
                self._last_known_volume[ticket] = pos.volume
                self._entry_directions[ticket] = pos.type.value

                risk_price = abs(pos.price_open - pos.sl) if pos.sl > 0 else (atr * 1.5)
                contract_size = (
                    symbol_info.trade_contract_size
                    if symbol_info and symbol_info.trade_contract_size > 0
                    else 100.0
                )
                self._initial_risks[ticket] = pos.volume * contract_size * risk_price

                # Bind the entry context staged at dispatch time to this new ticket so the
                # eventual autopsy row carries entry_reason / confidence / regime.
                self._bind_pending_entry_context(ticket)
                self._sl_modified_flags[ticket] = False
                # PHASE 08: freeze the market conditions observed at the fill so
                # execution quality and stop-placement quality are measurable.
                self._entry_atr[ticket] = float(atr)
                self._entry_spread[ticket] = max(0.0, current_tick.ask - current_tick.bid)

                self.audit.current_account_source = self._ledger_account_source()
                # Robust Financial Ledger opened record
                self.audit.log_ledger_opened(
                    ticket=ticket,
                    symbol=pos.symbol,
                    direction=pos.type.value,
                    volume=pos.volume,
                    entry_price=pos.price_open,
                    timestamp_str=pos_time.isoformat()
                    if hasattr(pos_time, "isoformat")
                    else str(pos_time),
                    order_id=self._entry_order_ids.get(ticket, ""),
                    entry_reason=self._entry_reasons.get(ticket, ""),
                    ai_confidence_at_open=self._entry_confidences.get(ticket, 0.0),
                    market_regime_at_open=self._entry_regimes.get(ticket, ""),
                    initial_sl_price=pos.sl,
                    # BUG-226: execution provenance of the account this trade
                    # ran on, read live from the bound adapter so an engine
                    # hot-swap (set_execution_mode) is reflected per-trade.
                    account_source=self._ledger_account_source(),
                )

                # [EXPANDED] Try to associate message ID with this ticket!
                if self._order_id_to_message_id:
                    last_order_id = list(self._order_id_to_message_id.keys())[-1]
                    msg_id = self._order_id_to_message_id.pop(last_order_id)
                    self._order_message_ids[ticket] = msg_id
                    logger.info(
                        "Associated new position ticket with Telegram message",
                        ticket=ticket,
                        message_id=msg_id,
                    )

            # =================================================================
            # PROTECTION STATE REFRESH (must run before any decision logic)
            # -----------------------------------------------------------------
            # Advances the monotonic peak_win_usd, recomputes the breakeven level
            # and reconciles against the broker-reported SL so a restart cannot
            # duplicate an already-applied breakeven modification.
            # =================================================================
            protection = self.refresh_protection_state(pos, symbol_info)
            # S6-followup: tick-cache + duration telemetry + peak mirror moved to
            # the tracking ledger (verbatim block; call at the identical position).
            self._tracking.record_tick_durations(
                ticket,
                now,
                current_tick,
                pos.profit,
                peak_win_usd=protection.peak_win_usd,
            )

            price_current = current_tick.bid if pos.type == OrderType.BUY else current_tick.ask
            profit_price_delta = (
                (price_current - pos.price_open)
                if pos.type == OrderType.BUY
                else (pos.price_open - price_current)
            )

            total_impact_usd, impact_price_delta = self._estimate_liquidation_impact(
                pos.volume, symbol_info, atr
            )
            net_price_delta = profit_price_delta - impact_price_delta

            self._ensure_ticket_bootstrap(
                ticket, now, price_current, profit_price_delta, net_price_delta
            )
            self._update_lsf_desync_metrics(
                ticket, now, price_current, profit_price_delta, net_price_delta, atr
            )

            self._update_mfe_mae(ticket, profit_price_delta, now=now)
            self._update_tick_state(ticket, pos, price_current, profit_price_delta)

            # TASK-3: model/regime/liquidity reversal observations while OPEN
            # (bounded per-ticket events; never writes to the hot path).
            self._capture_reversal_state(ticket, pos, probs, regime_state, now)

            # [EXPANDED] Real-time order/position modification & partial close checks
            # S6: external-modification sync stage (verbatim block moved to
            # _sync_external_modifications).
            self._sync_external_modifications(pos, ticket, price_current, symbol_info)

            entry_time = self._entry_timestamps[ticket]
            holding_duration = (
                (now - entry_time).total_seconds() if isinstance(entry_time, datetime) else 0.0
            )

            smart_metrics = self._calculate_smart_position_metrics(
                pos=pos,
                price_current=price_current,
                mid_price=mid_price,
                spread=spread,
                atr=atr,
                net_price_delta=net_price_delta,
                gross_price_delta=profit_price_delta,
                impact_price_delta=impact_price_delta,
                total_impact_usd=total_impact_usd,
                holding_duration=holding_duration,
                features=feature_vector,
                symbol_info=symbol_info,
            )

            # Evaluate with a slight throttle (e.g., once every 500ms per open ticket) to prevent CPU thrashing
            current_time = time.time()
            # S6-escalation HOLD-SCORE EVALUATION stage (verbatim block moved to
            # _evaluate_hold_score; throttled base eval + giveback override).
            hold_score, invalidate_reasons, base_hold_score = self._evaluate_hold_score(
                pos=pos,
                ticket=ticket,
                current_time=current_time,
                price_current=price_current,
                feature_vector=feature_vector,
                impact_price_delta=impact_price_delta,
                atr=atr,
                smart_metrics=smart_metrics,
            )

            # --- Trajectory, Evidence, and State machine Processing (Requirements 13-16, 20) ---
            # S6-escalation stage: verbatim block moved to _update_trajectory_and_state.
            (
                pnl_features,
                evidence,
                confidence_factor,
                debounced_state,
                _budget_exhausted,  # consumed inside the stage; kept for return-shape clarity
            ) = self._update_trajectory_and_state(
                pos=pos,
                ticket=ticket,
                now=now,
                protection=protection,
                price_current=price_current,
                hold_score=hold_score,
                atr=atr,
                spread=spread,
                probs=probs,
                feature_vector=feature_vector,
            )
            # Continuous dynamic protection score for telemetry
            prot_score = self._calculate_protection_score(
                ticket, pos, base_hold_score, pnl_features, evidence, confidence_factor, atr
            )

            # S6 STEP-C: protection/AI-flip chain stage (verbatim block; the 4
            # original `continue` sites return skip=True and the caller
            # continues the loop — identical control flow).
            if self._run_protection_chain(
                pos=pos,
                ticket=ticket,
                now=now,
                current_time=current_time,
                protection=protection,
                price_current=price_current,
                hold_score=hold_score,
                base_hold_score=base_hold_score,
                invalidate_reasons=invalidate_reasons,
                atr=atr,
                spread=spread,
                min_stop_gap=min_stop_gap,
                symbol_info=symbol_info,
                smart_metrics=smart_metrics,
                evidence=evidence,
                pnl_features=pnl_features,
                probs=probs,
                feature_vector=feature_vector,
                regime_state=regime_state,
                current_tick=current_tick,
                holding_duration=holding_duration,
                debounced_state=debounced_state,
                prot_score=prot_score,
            ):
                continue
            # S6-escalation DECISION STAGE: rule-matrix -> scenario fallback ->
            # arbitration -> exit-pending -> throttled exit log -> mechanism map ->
            # giveback MFE-SL target. Verbatim block moved to _decide_position_action;
            # broker dispatch below stays in the manager.
            action, scenario, rule_target_sl = self._decide_position_action(
                pos=pos,
                ticket=ticket,
                now=now,
                current_time=current_time,
                atr=atr,
                spread=spread,
                holding_duration=holding_duration,
                price_current=price_current,
                net_price_delta=net_price_delta,
                profit_price_delta=profit_price_delta,
                min_stop_gap=min_stop_gap,
                symbol_info=symbol_info,
                hold_score=hold_score,
                smart_metrics=smart_metrics,
                evidence=evidence,
                debounced_state=debounced_state,
                invalidate_reasons=invalidate_reasons,
                regime_state=regime_state,
            )
            # S6-dispatch: approved-plan broker execution. The plan snapshot is
            # built from the decision stage; the dispatcher runs the verbatim
            # branches (identical broker calls, ordering, and state mutations).
            plan = ExecutionPlan(
                action=action,
                scenario=scenario,
                ticket=ticket,
                symbol=pos.symbol,
                rule_target_sl=rule_target_sl,
                mechanism=self._forced_exit_mechanisms.get(ticket),
            )
            self._execute_position_action(
                plan=plan,
                pos=pos,
                ticket=ticket,
                now=now,
                atr=atr,
                spread=spread,
                min_stop_gap=min_stop_gap,
                price_current=price_current,
                rule_target_sl=rule_target_sl,
                hold_score=hold_score,
                protection=protection,
                symbol_info=symbol_info,
                current_tick=current_tick,
                scenario=scenario,
                action=action,
            )
        return positions

    def _sweep_dead_tickets(
        self,
        symbol: str,
        positions: list[Position],
        current_tick: TickData,
        now: datetime,
        symbol_info: SymbolInfo | None,
        atr: float,
    ) -> None:
        """
        Vanished-ticket autopsy sweep (S6 seam): for every tracked ticket that
        disappeared from the broker's open-positions list, resolve the closing
        deal (history + durable fallback), write the single data-rich autopsy
        row, record the experience outcome, emit telemetry, and release the
        per-ticket state. Extracted VERBATIM from manage_active_positions
        (S6; behavior-preserving method extraction on the same object).
        """
        active_tickets = {pos.ticket for pos in positions} if positions else set()
        tracked_tickets = set(self._entry_timestamps.keys())
        dead_tickets = tracked_tickets - active_tickets

        if dead_tickets:
            # ------------------------------------------------------------------
            # BUG-046 FIX: lifecycle-based deal lookup (never host-1h-only).
            # The MT5 broker/server clock can be hours ahead of the host clock,
            # so a `now - 1h` window misses closes that happened minutes ago in
            # wall time but are older in broker/server time. Anchor the query to
            # the OLDEST tracked entry time and bound it to a sensible minimum so
            # the window ALWAYS covers the complete position lifecycle.
            # ------------------------------------------------------------------
            try:
                oldest_entry = min(
                    (
                        self._entry_timestamps[t]
                        for t in dead_tickets
                        if self._entry_timestamps.get(t)
                    ),
                    default=None,
                )
                hours_back = 24
                if oldest_entry is not None:
                    age_hours = (now - oldest_entry).total_seconds() / 3600.0
                    hours_back = max(24, int(age_hours) + 2)
                # Bounded: never scan more than 7 days per sweep (positions are
                # scalps; anything older is outside the legit lifecycle).
                hours_back = min(hours_back, 24 * 7)
                history_deals = self.adapter.get_closed_deals_history(
                    symbol=symbol, hours_back=hours_back
                )
                logger.debug(
                    "[BROKER_OUTCOME] event=LOOKUP_START",
                    tickets=len(dead_tickets),
                    oldest_entry=oldest_entry.isoformat() if oldest_entry else None,
                    hours_back=hours_back,
                    now=now.isoformat(),
                )
            except Exception as e:
                logger.error("Failed to retrieve closed deals history for ledger", error=e)
                history_deals = []

            for dead_ticket in dead_tickets:
                # S6: per-ticket autopsy pipeline (verbatim body moved to
                # _autopsy_vanished_ticket; iteration-independent).
                self._autopsy_vanished_ticket(
                    dead_ticket,
                    history_deals,
                    symbol,
                    now,
                    current_tick,
                    symbol_info,
                    atr,
                    hours_back,
                )
        for dead_ticket in dead_tickets:
            oid = self._entry_order_ids.get(dead_ticket, "")
            if self.lifecycle_tracker is not None:
                try:
                    net_realized = self._net_pnl_by_ticket.get(dead_ticket, 0.0)
                    risk_dist = self._initial_risks.get(dead_ticket, 0.0)
                    realized_r = net_realized / max(risk_dist, 1e-9) if risk_dist > 0.0 else 0.0
                    final_mechanism = self._forced_exit_mechanisms.get(
                        dead_ticket
                    ) or self._exit_mechanism_by_ticket.get(dead_ticket, "")
                    self.lifecycle_tracker.finalize_exit(
                        ticket=dead_ticket,
                        realized_pnl_usd=net_realized,
                        realized_r=realized_r,
                        exit_mechanism=final_mechanism,
                        at=now,
                    )
                except Exception as finalize_err:
                    logger.error(
                        "[POSITION_TRACK] finalize failed (isolated)",
                        ticket=dead_ticket,
                        error=str(finalize_err),
                    )
            # TASK-7: a broker-gone ticket is positively closed; the exit-pending
            # reason is cleared once the autopsy row carries the decision evidence.
            self._closed_tickets[dead_ticket] = True
            self._exit_pending_final_reason.pop(dead_ticket, None)
            self._cleanup_ticket_state(dead_ticket)
            # BUG-081: prune the fill-family context once the final sibling
            # has closed (bounded registry lifecycle).
            if oid:
                self._prune_bound_context(oid)

    def _decide_position_action(
        self,
        pos: Position,
        ticket: int,
        now: datetime,
        current_time: float,
        atr: float,
        spread: float,
        holding_duration: float,
        price_current: float,
        net_price_delta: float,
        profit_price_delta: float,
        min_stop_gap: float,
        symbol_info: SymbolInfo | None,
        hold_score: int,
        smart_metrics: dict[str, Any],
        evidence: dict[str, Any],
        debounced_state: "PositionState",
        invalidate_reasons: list[str],
        regime_state: Any | None = None,
    ) -> tuple[str, str, float]:
        """DECISION STAGE (S6-escalation): rule-matrix evaluation, scenario
        fallback, multi-stage arbitration, exit-pending record, throttled
        exit-evaluation log, exit-mechanism mapping, and giveback MFE-SL
        targeting. Moved VERBATIM from manage_active_positions' per-position
        loop; broker dispatch remains with the manager. Returns
        (action, scenario, rule_target_sl)."""
        # --- RULE MATRIX IN-TRADE EXIT EVALUATION ---
        rule_exit = None
        rule_target_sl = 0.0
        if self.rule_matrix:
            self.rule_matrix.refresh_cache()
            rule_exit = self.rule_matrix.evaluate_in_trade_exits(
                pos=pos,
                holding_duration_sec=holding_duration,
                price_current=price_current,
                atr=atr,
                mfe_profit=self._mfe_tracker.get(ticket, 0.0),
            )

        if rule_exit:
            if rule_exit["action"] == "CLOSE":
                legacy_action = "CLOSE"
                legacy_scenario = rule_exit["reason"]
            elif rule_exit["action"] == "MODIFY_SL":
                legacy_action = "MODIFY_SL"
                legacy_scenario = rule_exit["reason"]
                rule_target_sl = rule_exit["stop_loss"]
            else:
                legacy_action, legacy_scenario = self._resolve_position_management_scenario(
                    pos=pos,
                    hold_score=hold_score,
                    metrics=smart_metrics,
                    net_delta=net_price_delta,
                    gross_delta=profit_price_delta,
                    atr=atr,
                    spread=spread,
                    holding_duration=holding_duration,
                    min_stop_gap=min_stop_gap,
                )
        else:
            legacy_action, legacy_scenario = self._resolve_position_management_scenario(
                pos=pos,
                hold_score=hold_score,
                metrics=smart_metrics,
                net_delta=net_price_delta,
                gross_delta=profit_price_delta,
                atr=atr,
                spread=spread,
                holding_duration=holding_duration,
                min_stop_gap=min_stop_gap,
            )

        # --- Multi-Stage Decision Arbitration ---
        action, scenario = self._arbitrate_decision(
            ticket=ticket,
            pos=pos,
            legacy_action=legacy_action,
            legacy_scenario=legacy_scenario,
            adaptive_state=debounced_state,
            current_pnl_usd=pos.profit,
            evidence=evidence,
            now=now,
        )

        # TASK-7 exit-decision traceability: persist the arbitrated verdict so a
        # position that closes (or disappears) before the next pass still carries
        # the decision that governed it. Cleared at autopsy.
        with contextlib.suppress(Exception):
            self._exit_pending_final_reason[ticket] = {
                "action": action,
                "reason": scenario,
                "state": debounced_state.value,
                "at": now.isoformat() if hasattr(now, "isoformat") else str(now),
            }

        # -----------------------------------------------------------------
        # Phase 15: structured exit-evaluation log (state-change driven).
        # Emitted at most once per 3s per ticket (BUG-129): a repeating
        # HOLD verdict must never flood the log. Shares the SAME throttle
        # as the INSTITUTIONAL TELEMETRY block above so they stay aligned.
        # -----------------------------------------------------------------
        if (current_time - self._last_telemetry_time.get(ticket, 0.0)) >= 3.0:
            self._last_telemetry_time[ticket] = current_time
            try:
                mae_p = smart_metrics.get("mae_to_atr_ratio", 0.0)
                mfe_p = smart_metrics.get("mfe_to_atr_ratio", 0.0)
                logger.info(
                    "[POSITION_EXIT_EVAL]",
                    ticket=ticket,
                    pnl=round(float(pos.profit), 2),
                    hold_score=int(hold_score),
                    reversal_prob=round(float(evidence.get("adverse_score", 0.0)), 3),
                    continuation_prob=round(float(evidence.get("continuation_score", 0.0)), 3),
                    recovery_prob=round(float(evidence.get("recovery_score", 0.0)), 3),
                    regime=self._current_regime_str(regime_state, ticket) or "UNKNOWN",
                    entry_regime=self._entry_regimes.get(ticket, ""),
                    elapsed_sec=round(holding_duration, 1),
                    mae_atr=round(float(mae_p), 3),
                    mfe_atr=round(float(mfe_p), 3),
                    state=debounced_state.value,
                    decision=action,
                    reason=scenario,
                )
            except Exception as log_err:
                logger.debug(
                    "[POSITION_EXIT_EVAL] log skipped (isolated)",
                    ticket=ticket,
                    error=str(log_err),
                )

        # If the arbitrated decision is a CLOSE initiated by the Adaptive Protection Engine,
        # save the exit mechanism to be written to the financial ledger autopsy
        if action == "CLOSE":
            if "RECOVERY" in scenario or "LOSS" in scenario:
                self._forced_exit_mechanisms[ticket] = ExitMechanism.HOLD_SCORE_DECAY
            elif "GIVEBACK" in scenario:
                self._forced_exit_mechanisms[ticket] = ExitMechanism.PROFIT_GIVEBACK_PROTECTION

        # If arbitrated decision is a custom MODIFY_SL, set the target stop loss
        if action == "MODIFY_SL" and "GIVEBACK" in scenario:
            # Lock 70% of peak profit
            peak_win = self._peak_profit_usd.get(ticket, 0.0)
            contract_sz = (
                symbol_info.trade_contract_size
                if symbol_info and symbol_info.trade_contract_size > 0
                else 100.0
            )
            target_mfe_sl = (
                pos.price_open + (peak_win * 0.70) / max(pos.volume * contract_sz, 1.0)
                if pos.type == OrderType.BUY
                else pos.price_open - (peak_win * 0.70) / max(pos.volume * contract_sz, 1.0)
            )
            rule_target_sl = round(target_mfe_sl, self._resolve_price_digits(symbol_info))

        return action, scenario, rule_target_sl

    def _update_trajectory_and_state(
        self,
        pos: Position,
        ticket: int,
        now: datetime,
        protection: PositionProtectionState,
        price_current: float,
        hold_score: int,
        atr: float,
        spread: float,
        probs: Any | None,
        feature_vector: FeatureVector | None,
    ) -> tuple[dict[str, Any], dict[str, Any], float, "PositionState", bool]:
        """TRACKING/EVIDENCE/STATE STAGE (S6-escalation): trajectory step,
        pnl-features, adaptive evidence scores, recovery-budget evaluation on
        drawdown, candidate-state derivation, and hysteresis debounce. Moved
        VERBATIM from manage_active_positions' per-position loop. Returns
        (pnl_features, evidence, confidence_factor, debounced_state,
        budget_exhausted)."""
        drawdown = abs(min(0.0, pos.profit))
        retention = protection.retention_ratio(pos.profit)
        self._add_trajectory_step(
            ticket=ticket,
            timestamp=now,
            pnl=pos.profit,
            price=price_current,
            hold_score=hold_score,
            drawdown=drawdown,
            retention=retention,
            atr=atr,
            volatility=spread,
        )

        pnl_features = self._calculate_trajectory_features(ticket)
        confidence_factor = self._entry_confidences.get(ticket, 0.0)
        evidence = self._calculate_adaptive_evidence_scores(ticket, pos, probs, feature_vector)

        if pos.profit < 0.0:
            h4_trend = self._safe_feature_float(feature_vector, "htf_h4_trend", 0.0)
            self._initialize_recovery_mode(
                ticket, pos.profit, confidence_factor, atr, h4_trend, now
            )
            budget_exhausted, _budget_reason = self._evaluate_recovery_budget_and_horizon(
                ticket, pos.profit, now
            )
        else:
            budget_exhausted = False

        cand_state = self._evaluate_candidate_state(ticket, pos, evidence, pnl_features)
        if budget_exhausted:
            cand_state = PositionState.LOSS_HARD_EXIT
        debounced_state = self.transition_state_with_hysteresis(ticket, cand_state, now)

        return pnl_features, evidence, confidence_factor, debounced_state, budget_exhausted

    def _evaluate_hold_score(
        self,
        pos: Position,
        ticket: int,
        current_time: float,
        price_current: float,
        feature_vector: FeatureVector | None,
        impact_price_delta: float,
        atr: float,
        smart_metrics: dict[str, Any],
    ) -> tuple[int, list[str], int]:
        """HOLD-SCORE EVALUATION STAGE (S6-escalation): throttled base-score
        evaluation + position-state recalculation + giveback override +
        tracker store. Moved VERBATIM from manage_active_positions'
        per-position loop. Returns (hold_score, invalidate_reasons,
        base_hold_score)."""
        last_eval = self._last_hold_eval_time.get(ticket, 0.0)
        if (current_time - last_eval) >= 0.50:
            base_hold_score, invalidate_reasons = self._calculate_hold_value_score(
                pos, price_current, feature_vector, impact_price_delta, atr, smart_metrics
            )
            base_hold_score = self._recalculate_hold_score_with_position_state(
                ticket, base_hold_score, smart_metrics, invalidate_reasons
            )
            self._base_hold_score_tracker[ticket] = base_hold_score
            self._last_reasons_tracker[ticket] = invalidate_reasons
            self._last_hold_eval_time[ticket] = current_time
        else:
            base_hold_score = self._base_hold_score_tracker.get(ticket, 100)
            invalidate_reasons = self._last_reasons_tracker.get(ticket, ["HEALTHY"])

        # SAFETY OVERRIDE: applied on EVERY pass (never throttled) after the base
        # score is computed but before the score is used for any execution
        # decision, so the base scoring logic can never lift the score back up
        # over a profit-giveback verdict.
        hold_score, _giveback_required, _giveback_reason = self.evaluate_profit_giveback(
            ticket=ticket,
            current_pnl_usd=pos.profit,
            base_hold_score=base_hold_score,
        )
        self._hold_score_tracker[ticket] = hold_score

        return hold_score, invalidate_reasons, base_hold_score

    def _execute_position_action(
        self,
        plan: "ExecutionPlan",
        pos: Position,
        ticket: int,
        now: datetime,
        atr: float,
        spread: float,
        min_stop_gap: float,
        price_current: float,
        rule_target_sl: float,
        hold_score: int,
        protection: PositionProtectionState,
        symbol_info: SymbolInfo | None,
        current_tick: TickData,
        scenario: str,
        action: str,
    ) -> None:
        """BROKER DISPATCH STAGE (S6-dispatch): executes an approved
        ExecutionPlan against the broker adapter — CLOSE / MODIFY_SL /
        PARTIAL_CLOSE / BREAK_EVEN / NORMAL_TRAIL branches, verbatim from
        manage_active_positions' per-position loop (identical call ordering,
        identical arguments, identical state mutations). The plan is intent;
        this stage executes it; the manager remains the orchestrator."""
        action = plan.action
        scenario = plan.scenario
        rule_target_sl = plan.rule_target_sl
        if action == "CLOSE":
            msg_id = self._order_message_ids.get(ticket)
            # Attribute engine-initiated exits to hold-score decay unless a more
            # specific mechanism (e.g. AI reversal) was already tagged.
            self._forced_exit_mechanisms.setdefault(ticket, ExitMechanism.HOLD_SCORE_DECAY)

            logger.info(
                f"[EXIT TRACE] EXECUTING BROKER CLOSE for ticket {ticket} | Mechanism: {self._forced_exit_mechanisms.get(ticket)} | Scenario: {scenario}"
            )

            if self.adapter.close_position(ticket=ticket):
                # TASK-7 (BUG-087): broker-verified close ordering. The exposure
                # slot is freed only after the position is confirmed gone from the
                # broker's live set; the per-ticket trackers survive so the next
                # management pass writes the single data-rich autopsy row.
                self._closed_tickets[ticket] = True
                self._broker_close_verified(ticket)
                if self.notifier:
                    self.notifier.notify_early_emergency_cut(
                        ticket=ticket,
                        score=hold_score,
                        reasons=scenario,
                        saved_usd=pos.profit,
                        reply_to_message_id=msg_id,
                    )
                with self._live_tickets_lock:
                    self._tickets_cache.pop_ticket(ticket)

                # SPLIT-ORDER DESYNC GUARD: a position split across multiple MT5
                # tickets from the SAME dispatch (same order_id/request) must never
                # desync into one ticket closed while its sibling keeps trading.
                # When an emergency/hard exit fires for one leg, propagate the close
                # to every live sibling leg of the same order.
                self._close_sibling_legs(ticket, scenario, now)
            else:
                self._forced_exit_mechanisms.pop(ticket, None)
            # loop-body section, so this continue was a no-op in the original code)

        elif action == "MODIFY_SL":
            # Monotonic safety floor (invariant): a rule-driven SL target may
            # never loosen the broker SL or regress behind the confirmed
            # breakeven lock, even when the rule matrix proposes a wider stop.
            if rule_target_sl > 0.0 and not self.is_sl_improvement(pos, rule_target_sl):
                rule_target_sl = 0.0
            if rule_target_sl > 0.0 and self._should_modify_sl(ticket, rule_target_sl):
                if self.adapter.modify_position(
                    ticket=ticket, stop_loss=rule_target_sl, take_profit=pos.tp
                ):
                    self._last_modify_sl[ticket] = rule_target_sl
                    self._sl_modified_flags[ticket] = True
                    if self.notifier:
                        msg_id = self._order_message_ids.get(ticket)
                        self.notifier.notify_order_modification(
                            ticket=ticket,
                            symbol=pos.symbol,
                            field_modified=f"Stop Loss ({scenario})",
                            old_value=pos.sl,
                            new_value=rule_target_sl,
                            reply_to_message_id=msg_id,
                        )
            # loop-body section, so this continue was a no-op in the original code)

        elif action == "PARTIAL_CLOSE":
            if self.enable_partial_tp and not self._partial_closed_tickets.get(ticket, False):
                vol_step = (
                    symbol_info.volume_step if symbol_info and symbol_info.volume_step > 0 else 0.01
                )
                partial_volume = round(
                    round((pos.volume * self.partial_tp_ratio) / vol_step) * vol_step, 2
                )
                if partial_volume < pos.volume:
                    if self.adapter.close_position(ticket=ticket, volume=partial_volume):
                        self._partial_closed_tickets[ticket] = True

        elif action == "BREAK_EVEN":
            target_sl = (
                pos.price_open + max(self.be_lock, spread)
                if pos.type == OrderType.BUY
                else pos.price_open - max(self.be_lock, spread)
            )
            target_sl = round(target_sl, 2)
            valid_stop = False
            if pos.type == OrderType.BUY:
                if target_sl > pos.sl and (current_tick.bid - target_sl) >= min_stop_gap:
                    valid_stop = True
            elif (pos.sl == 0.0 or target_sl < pos.sl) and (
                target_sl - current_tick.ask
            ) >= min_stop_gap:
                valid_stop = True

            # BUG-086: never re-issue a BREAK_EVEN modify once the protection
            # state machine already confirmed the lock (prevents duplicate
            # broker modifications + duplicate notifications).
            if self.get_protection_state(ticket).was_sl_modified:
                valid_stop = False
            if valid_stop and self._should_modify_sl(ticket, target_sl):
                success = self.adapter.modify_position(
                    ticket=ticket, stop_loss=target_sl, take_profit=pos.tp
                )
                if success:
                    # Only a CONFIRMED modification advances the tracked final SL
                    # (BUG-085).
                    self._last_modify_sl[ticket] = target_sl
                    self._sl_modified_flags[ticket] = True
                    self._log_protection_audit(
                        pos,
                        action="BREAKEVEN_LOCK",
                        reason=f"BREAKEVEN_LOCK_ACTIVATED (router dispatch) target_sl={target_sl}",
                        stop_loss=target_sl,
                    )
                else:
                    self._log_protection_audit(
                        pos,
                        action="BREAKEVEN_FAILED",
                        reason=f"BREAKEVEN LOCK FAILED (router dispatch) target_sl={target_sl}",
                        stop_loss=target_sl,
                    )
                if success and self.notifier:
                    msg_id = self._order_message_ids.get(ticket)
                    orig_risk = self._initial_risks.get(ticket, 0.0)
                    contract_size = (
                        symbol_info.trade_contract_size
                        if symbol_info and symbol_info.trade_contract_size > 0
                        else 100.0
                    )
                    protected_amt = abs(target_sl - pos.price_open) * pos.volume * contract_size
                    self.notifier.notify_break_even_applied_extended(
                        ticket=ticket,
                        new_sl=target_sl,
                        original_risk_usd=orig_risk,
                        protected_amount_usd=protected_amt,
                        reply_to_message_id=msg_id,
                    )

        elif action == "NORMAL_TRAIL":
            trail_distance = max(min_stop_gap, round(atr * 1.15, 2))
            target_sl = (
                price_current - trail_distance
                if pos.type == OrderType.BUY
                else price_current + trail_distance
            )
            target_sl = round(target_sl, 2)
            valid_stop = False
            if pos.type == OrderType.BUY:
                if target_sl > pos.sl and (current_tick.bid - target_sl) >= min_stop_gap:
                    valid_stop = True
            elif (pos.sl == 0.0 or target_sl < pos.sl) and (
                target_sl - current_tick.ask
            ) >= min_stop_gap:
                valid_stop = True

            # Monotonic safety floor (BUG-085): never loosen protection even on
            # a rule-driven NORMAL_TRAIL verdict.
            valid_stop = valid_stop and self.is_sl_improvement(pos, target_sl)
            if valid_stop and self._should_modify_sl(ticket, target_sl):
                old_sl_val = pos.sl
                success = self.adapter.modify_position(
                    ticket=ticket, stop_loss=target_sl, take_profit=pos.tp
                )
                if success:
                    # Only a CONFIRMED modification advances the tracked final SL
                    # (BUG-085).
                    self._last_modify_sl[ticket] = target_sl
                    self._sl_modified_flags[ticket] = True
                if success and self.notifier:
                    msg_id = self._order_message_ids.get(ticket)
                    self.notifier.notify_trailing_stop_advanced_extended(
                        ticket=ticket,
                        old_sl=old_sl_val,
                        new_sl=target_sl,
                        current_price=price_current,
                        reply_to_message_id=msg_id,
                    )

    def _run_protection_chain(
        self,
        pos: Position,
        ticket: int,
        now: datetime,
        current_time: float,
        protection: PositionProtectionState,
        price_current: float,
        hold_score: int,
        base_hold_score: int,
        invalidate_reasons: list[str],
        atr: float,
        spread: float,
        min_stop_gap: float,
        symbol_info: SymbolInfo | None,
        smart_metrics: dict[str, Any],
        evidence: dict[str, Any],
        pnl_features: dict[str, Any],
        probs: Any | None,
        feature_vector: FeatureVector | None,
        regime_state: Any | None,
        current_tick: TickData,
        holding_duration: float,
        debounced_state: "PositionState",
        prot_score: float,
    ) -> bool:
        """PROTECTION/AI-FLIP CHAIN STAGE (S6 STEP-C): AI direction flip +
        fast reversal protection, deterministic protection priority chain
        (giveback -> breakeven -> MFE trailing), and throttled institutional
        telemetry emission. Moved VERBATIM from manage_active_positions'
        per-position loop.

        Returns True when the chain handled this pass (a `continue` site
        fired in the original code) and the caller must skip the remaining
        loop body for this position; returns False to proceed to the
        decision stage."""
        # --- 0. AI DIRECTION FLIP & FAST REVERSAL PROTECTION ---
        ai_flip_detected = False
        ai_flip_action = None
        if probs is not None:
            try:
                probs_list = probs.squeeze().tolist()
                if not isinstance(probs_list, list):
                    probs_list = [probs_list]
                prob_buy = probs_list[1] if len(probs_list) > 1 else 0.0
                prob_sell = probs_list[2] if len(probs_list) > 2 else 0.0

                total_active_prob = prob_buy + prob_sell + 1e-8
                rel_buy_bias = prob_buy / total_active_prob
                rel_sell_bias = prob_sell / total_active_prob

                # Read thresholds from AlgoConfig with whipsaw protection
                rel_threshold = getattr(self.algo_config, "ai_flip_relative_bias_threshold", 0.60)
                min_delta = getattr(self.algo_config, "ai_flip_min_delta", 0.10)

                # Whipsaw guard: require min 15s position duration OR strong relative bias >= (threshold + 0.05)
                whipsaw_guard_passed = holding_duration >= 15.0 or max(
                    rel_buy_bias, rel_sell_bias
                ) >= (rel_threshold + 0.05)

                if whipsaw_guard_passed:
                    if pos.type == OrderType.BUY and (
                        rel_sell_bias >= rel_threshold or prob_sell > prob_buy + min_delta
                    ):
                        ai_flip_detected = True
                        ai_flip_action = ActionType.SELL_STOP
                    elif pos.type == OrderType.SELL and (
                        rel_buy_bias >= rel_threshold or prob_buy > prob_sell + min_delta
                    ):
                        ai_flip_detected = True
                        ai_flip_action = ActionType.BUY_STOP
            except Exception:
                pass

        if ai_flip_detected and ai_flip_action is not None:
            # TASK-EXIT-SEPARATION (c): the AI direction-flip EXIT (close +
            # fast reversal) is SUSPENDED unless the operator enables
            # `algo.ai_flip_exit_enabled` (default False, fail-safe). The
            # flag is read LIVE every pass; ONE rate-limited structured
            # WARNING per ticket is logged on each suppression. The flip
            # code below is intentionally left intact (suspension, not
            # deletion); the position falls through to the deterministic
            # giveback -> breakeven -> trailing protection chain.
            if not bool(getattr(getattr(self, "algo_config", None), "ai_flip_exit_enabled", False)):
                self._warn_ai_flip_suspended(ticket, ai_flip_action.value)
                ai_flip_detected = False
                ai_flip_action = None
            else:
                msg_id = self._order_message_ids.get(ticket)
                logger.info(
                    f">>> AI DIRECTION SHIFT DETECTED: Closing position #{ticket} and executing fast reversal {ai_flip_action.value} <<<"
                )

                # Tag the exit BEFORE closing so the ledger autopsy attributes it to the
                # reversal protocol rather than a generic manual close.
                self._forced_exit_mechanisms[ticket] = ExitMechanism.AI_REVERSAL_EXIT

                if self.adapter.close_position(ticket=ticket):
                    if self.notifier:
                        self.notifier.notify_canonical_close(
                            ticket=ticket,
                            symbol=pos.symbol,
                            entry=pos.price_open,
                            exit_price=price_current,
                            profit_usd=pos.profit,
                            duration_sec=holding_duration,
                            exit_reason=ExitMechanism.AI_REVERSAL_EXIT,
                            evidence=f"AI_REVERSAL ({ai_flip_action.value})",
                            reply_to_message_id=msg_id,
                        )

                    # Free the exposure slot immediately (the broker position is gone) but
                    # deliberately KEEP the per-ticket trackers alive: the next management
                    # pass detects the dead ticket and writes the single autopsy row.
                    with self._live_tickets_lock:
                        self._tickets_cache.pop_ticket(ticket)

                    # Dispatch immediate reversal stop order (clamped to HARD_MAX_LOTS).
                    rev_volume = self._clamp_dispatch_volume(pos.volume, symbol=pos.symbol)
                    if rev_volume <= 0.0:
                        logger.warning(
                            "AI REVERSAL: reversal order skipped, clamped volume is zero",
                            ticket=ticket,
                        )
                        # (continue -> skip-rest signal, S6 STEP-C extraction)
                        return True

                    rev_entry = (
                        current_tick.ask
                        if ai_flip_action == ActionType.BUY_STOP
                        else current_tick.bid
                    )
                    rev_sl = (
                        round(rev_entry - (atr * 1.5), 2)
                        if ai_flip_action == ActionType.BUY_STOP
                        else round(rev_entry + (atr * 1.5), 2)
                    )
                    rev_tp = (
                        round(rev_entry + (atr * 3.0), 2)
                        if ai_flip_action == ActionType.BUY_STOP
                        else round(rev_entry - (atr * 3.0), 2)
                    )
                    # BUG-258 (Agent-15 capital-protection wave 3): the
                    # fast-reversal follow-up was dispatched DIRECTLY through
                    # adapter.place_pending_order — an architectural bypass of
                    # the dispatch gate stack (kill switch, persisted halt,
                    # SAFE_MODE, maintenance window, duplicate request_id,
                    # engine-wide MAX_TOTAL_EXPOSURE) and of the canonical
                    # risk-approval chain. It now routes through
                    # dispatch_order with a full decision payload, so every
                    # dispatch-layer gate applies exactly like a primary
                    # entry. Geometry and clamp semantics are unchanged.
                    fast_reversal_decision = _FastReversalDecision(
                        symbol=pos.symbol,
                        action=ai_flip_action,
                        proposed_entry=rev_entry,
                        stop_loss=rev_sl,
                        take_profit=rev_tp,
                        source_ticket=ticket,
                        generated_at=getattr(current_tick, "timestamp", None),
                    )
                    if not self.dispatch_order(fast_reversal_decision, rev_volume):
                        logger.warning(
                            "[AI_REVERSAL] fast-reversal follow-up refused by the "
                            "dispatch gate stack (close-only)",
                            ticket=ticket,
                            suppressed_action=ai_flip_action.value,
                        )
                    # (continue -> skip-rest signal, S6 STEP-C extraction)
                    return True

                # Close failed: clear the tag so a later organic exit is not mislabelled.
                self._forced_exit_mechanisms.pop(ticket, None)

        # =================================================================
        # DETERMINISTIC PROTECTION PRIORITY CHAIN
        # -----------------------------------------------------------------
        #   1. Emergency / existing hard-risk protection (AI reversal above,
        #      falling-knife guard, kill-switch scenarios in the router)
        #   2. Profit Giveback Protection            <-- here
        #   3. Negative-PnL-after-meaningful-profit   <-- here (same call)
        #   4. Breakeven protection                   <-- here
        #   5. ATR trailing protection                <-- here
        #   6. Normal hold-score decision logic       <-- router below
        #
        # A lower-priority mechanism can never override a higher-priority
        # decision: when giveback protection fires we `continue`, so neither
        # trailing nor the router touches this ticket on this pass.
        # =================================================================
        if protection.close_requested or self._closed_tickets.get(ticket, False):
            # Close already accepted / broker-gone for this ticket. Do not
            # re-submit, and do not let any lower-priority mechanism act on a
            # dying position (TASK-7 closed-state invariant).
            # (continue -> skip-rest signal, S6 STEP-C extraction)
            return True

        hold_score, giveback_active = self.enforce_profit_giveback_protection(
            pos=pos,
            hold_score=hold_score,
            symbol_info=symbol_info,
            # Phase 15: use the CURRENT regime (not the entry snapshot) so the
            # VOLATILITY_EXPANSION giveback-suppression guard reacts to the
            # regime the position is in NOW.
            regime=self._current_regime_str(regime_state, pos.ticket),
        )
        if giveback_active:
            # (continue -> skip-rest signal, S6 STEP-C extraction)
            return True

        # --- Priority 4: BREAKEVEN LOCK ($15.00 or 1.5 ATR in USD) ---
        self.apply_breakeven_lock(
            pos=pos,
            symbol_info=symbol_info,
            atr=atr,
            min_stop_gap=min_stop_gap,
            current_tick=current_tick,
        )

        # --- MFE GIVEBACK TRAILING LOCK ---
        peak_win = self._peak_profit_usd.get(ticket, 0.0)
        contract_sz = (
            symbol_info.trade_contract_size
            if symbol_info and symbol_info.trade_contract_size > 0
            else 100.0
        )
        if peak_win >= 150.0 and pos.profit < (peak_win * 0.70):
            target_mfe_sl = (
                pos.price_open + (peak_win * 0.70) / max(pos.volume * contract_sz, 1.0)
                if pos.type == OrderType.BUY
                else pos.price_open - (peak_win * 0.70) / max(pos.volume * contract_sz, 1.0)
            )
            target_mfe_sl = round(target_mfe_sl, 2)

            valid_stop = False
            if pos.type == OrderType.BUY:
                if target_mfe_sl > pos.sl and (current_tick.bid - target_mfe_sl) >= min_stop_gap:
                    valid_stop = True
            elif (pos.sl == 0.0 or target_mfe_sl < pos.sl) and (
                target_mfe_sl - current_tick.ask
            ) >= min_stop_gap:
                valid_stop = True

            # Never loosen an existing protective stop or regress behind the
            # confirmed breakeven lock.
            valid_stop = valid_stop and self.is_sl_improvement(pos, target_mfe_sl)

            if valid_stop and self._should_modify_sl(ticket, target_mfe_sl):
                success = self.adapter.modify_position(
                    ticket=ticket, stop_loss=target_mfe_sl, take_profit=pos.tp
                )
                if success:
                    # Only a CONFIRMED modification advances the tracked final SL
                    # (BUG-085).
                    self._last_modify_sl[ticket] = target_mfe_sl
                    self._sl_modified_flags[ticket] = True
                    logger.info(
                        ">>> MFE GIVEBACK PROTECTOR: Advanced SL to lock 70% peak profit <<<",
                        ticket=ticket,
                        peak_win=peak_win,
                        locked_sl=target_mfe_sl,
                    )

        total_sec = max(holding_duration, 1.0)
        pct_win = (self._time_in_profit_sec[ticket] / total_sec) * 100
        pct_loss = (self._time_in_drawdown_sec[ticket] / total_sec) * 100

        # Throttled Detailed Telemetry logging (max once every 3.0s per ticket)
        current_time = time.time()
        # S6 STEP-A: throttle owned by TelemetryThrottle (the legacy lazy
        # init never fired post-__init__ construction; guard removed).
        last_telemetry = self._telemetry.last_emit(ticket)
        if (current_time - last_telemetry) >= 3.0:
            logger.info(
                "[INSTITUTIONAL TELEMETRY v6.8]",
                ticket=ticket,
                type=pos.type.value,
                state=debounced_state.value,
                pnl=f"${pos.profit:+.2f}",
                peak_win=f"${self._peak_profit_usd[ticket]:+.2f}",
                peak_loss=f"${self._peak_drawdown_usd[ticket]:+.2f}",
                time_win=f"{pct_win:.0f}%",
                time_loss=f"{pct_loss:.0f}%",
                age_sec=f"{holding_duration:.1f}s",
                atr=f"${atr:.2f}",
                ai_rec_prob=f"{evidence.get('recovery_score', 0.0) * 100:.1f}%",
                ai_adv_prob=f"{evidence.get('adverse_score', 0.0) * 100:.1f}%",
                ai_cont_prob=f"{evidence.get('continuation_score', 0.0) * 100:.1f}%",
                hold_score=f"{hold_score}/100",
                score_reasons=invalidate_reasons if invalidate_reasons else ["HEALTHY"],
                prot_score=f"{prot_score:.1f}/100",
            )
            self._last_telemetry_time[ticket] = current_time

        return False

    # -----------------------------------------------------------------
    # P0 seam S8: reconciliation + autopsy + experience attribution
    # delegate to ReconciliationEngine (execution/lifecycle/reconciliation.py).
    # The engine reads/writes the manager's canonical state surface; the
    # manager keeps only these delegation shims for its call sites.
    # -----------------------------------------------------------------

    @property
    def _reconciliation(self) -> ReconciliationEngine:
        """Lazily composed reconciliation engine (S8)."""
        eng: ReconciliationEngine | None = getattr(self, "_reconciliation_instance", None)
        if eng is None:
            eng = ReconciliationEngine(self)
            self._reconciliation_instance = eng
        return eng

    def reconcile_missed_closes(
        self,
        symbol: str,
        current_tick: TickData,
        symbol_info: SymbolInfo | None = None,
        hours_back: int = 24,
    ) -> int:
        """Delegate: restart close-loop (owned by ReconciliationEngine, S8)."""
        return self._reconciliation.reconcile_missed_closes(
            symbol, current_tick, symbol_info, hours_back
        )

    def _record_experience_outcome(
        self,
        dead_ticket: int,
        now: datetime,
        entry: float,
        exit_price: float,
        initial_sl_val: float,
        vol: float,
        atr: float,
        symbol_info: SymbolInfo | None,
        profit_usd: float,
        comm_usd: float,
        swap_usd: float,
        mae_val: float,
        mfe_val: float,
        mae_usd: float,
        mfe_usd: float,
        duration_sec: float,
        exit_mechanism: str,
        was_sl_modified: bool,
        request_id: str = "",
        broker_outcome: Any = None,
    ) -> None:
        """Delegate: learning hand-off (owned by ReconciliationEngine, S8)."""
        self._reconciliation._record_experience_outcome(
            dead_ticket=dead_ticket,
            now=now,
            entry=entry,
            exit_price=exit_price,
            initial_sl_val=initial_sl_val,
            vol=vol,
            atr=atr,
            symbol_info=symbol_info,
            profit_usd=profit_usd,
            comm_usd=comm_usd,
            swap_usd=swap_usd,
            mae_val=mae_val,
            mfe_val=mfe_val,
            mae_usd=mae_usd,
            mfe_usd=mfe_usd,
            duration_sec=duration_sec,
            exit_mechanism=exit_mechanism,
            was_sl_modified=was_sl_modified,
            request_id=request_id,
            broker_outcome=broker_outcome,
        )

    def _autopsy_vanished_ticket(
        self,
        dead_ticket: int,
        history_deals: list[dict[str, Any]],
        symbol: str,
        now: datetime,
        current_tick: TickData,
        symbol_info: SymbolInfo | None,
        atr: float,
        hours_back: int,
    ) -> None:
        """Delegate: vanished-ticket autopsy (owned by ReconciliationEngine, S8)."""
        self._reconciliation._autopsy_vanished_ticket(
            dead_ticket,
            history_deals,
            symbol,
            now,
            current_tick,
            symbol_info,
            atr,
            hours_back,
        )

    # -----------------------------------------------------------------
    # P0 seam S9: scoring engine composition (hold-value / protection /
    # trajectory scoring + trajectory history ownership moved OUT).
    # -----------------------------------------------------------------

    @property
    def _scoring(self) -> PositionScoringEngine:
        """Lazily composed scoring engine (S9)."""
        eng: PositionScoringEngine | None = getattr(self, "_scoring_instance", None)
        if eng is None:
            eng = PositionScoringEngine(self)
            self._scoring_instance = eng
        return eng

    def _calculate_protection_score(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate: protection scoring (owned by PositionScoringEngine, S9)."""
        return self._scoring._calculate_protection_score(*args, **kwargs)

    def _calculate_continuous_giveback_severity(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate: giveback severity (owned by PositionScoringEngine, S9)."""
        return self._scoring._calculate_continuous_giveback_severity(*args, **kwargs)

    def _calculate_adaptive_evidence_scores(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate: adaptive evidence (owned by PositionScoringEngine, S9)."""
        return self._scoring._calculate_adaptive_evidence_scores(*args, **kwargs)

    def _calculate_hold_value_score(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate: hold-value score (owned by PositionScoringEngine, S9)."""
        return self._scoring._calculate_hold_value_score(*args, **kwargs)

    def _evaluate_minimum_loss_optimization(self, *args: Any, **kwargs: Any) -> Any:
        """Delegate: minimum-loss optimization (owned by PositionScoringEngine, S9)."""
        return self._scoring._evaluate_minimum_loss_optimization(*args, **kwargs)

    def _add_trajectory_step(self, *args: Any, **kwargs: Any) -> None:
        """Delegate: trajectory writer (owned by PositionScoringEngine, S9)."""
        self._scoring._add_trajectory_step(*args, **kwargs)

    def _calculate_trajectory_features(self, *args: Any, **kwargs: Any) -> dict[str, float]:
        """Delegate: trajectory features (owned by PositionScoringEngine, S9)."""
        return self._scoring._calculate_trajectory_features(*args, **kwargs)

    def _sync_external_modifications(
        self,
        pos: Position,
        ticket: int,
        price_current: float,
        symbol_info: SymbolInfo | None,
    ) -> None:
        """ENTRY-SYNC STAGE (S6): detect broker-side SL/TP/volume
        modifications, notify, and advance the broker-side trackers. Moved
        VERBATIM from manage_active_positions' per-position loop."""
        if ticket in self._entry_prices:
            old_sl = self._entry_sls.get(ticket, 0.0)
            old_tp = self._entry_tps.get(ticket, 0.0)
            old_vol = self._last_known_volume.get(ticket, pos.volume)

            if pos.sl != old_sl:
                if self.notifier:
                    self.notifier.notify_order_modification(
                        ticket=ticket,
                        symbol=pos.symbol,
                        field_modified="Stop Loss",
                        old_value=old_sl,
                        new_value=pos.sl,
                        reply_to_message_id=self._order_message_ids.get(ticket),
                    )
                # Phase 14 (BUG-045): the CURRENT broker-side SL is tracked in
                # _last_modify_sl (for the autopsy's final_sl), while
                # _entry_sls remains the SL AT ENTRY. Previously this line
                # overwrote the entry SL, so initial_sl_price == final_sl_price
                # on every autopsy row and the SL modification timeline was
                # lost. _entry_sls is now frozen at open; only the broker-side
                # tracker advances.
                self._last_modify_sl[ticket] = pos.sl
                self._sl_modified_flags[ticket] = True
                self._entry_sls[ticket] = self._entry_sls.get(ticket, pos.sl) or pos.sl

            if pos.tp != old_tp:
                if self.notifier:
                    self.notifier.notify_order_modification(
                        ticket=ticket,
                        symbol=pos.symbol,
                        field_modified="Take Profit",
                        old_value=old_tp,
                        new_value=pos.tp,
                        reply_to_message_id=self._order_message_ids.get(ticket),
                    )
                self._entry_tps[ticket] = pos.tp

            if pos.volume != old_vol:
                if pos.volume < old_vol:
                    closed_lots = round(old_vol - pos.volume, 2)
                    price_delta = (
                        (price_current - pos.price_open)
                        if pos.type == OrderType.BUY
                        else (pos.price_open - price_current)
                    )
                    contract_size = (
                        symbol_info.trade_contract_size
                        if symbol_info and symbol_info.trade_contract_size > 0
                        else 100.0
                    )
                    realized_pnl = closed_lots * contract_size * price_delta
                    if self.notifier:
                        self.notifier.notify_partial_close(
                            ticket=ticket,
                            symbol=pos.symbol,
                            closed_lots=closed_lots,
                            remaining_lots=pos.volume,
                            realized_profit_usd=realized_pnl,
                            reply_to_message_id=self._order_message_ids.get(ticket),
                        )
                elif self.notifier:
                    self.notifier.notify_order_modification(
                        ticket=ticket,
                        symbol=pos.symbol,
                        field_modified="Volume",
                        old_value=old_vol,
                        new_value=pos.volume,
                        reply_to_message_id=self._order_message_ids.get(ticket),
                    )
                self._last_known_volume[ticket] = pos.volume

    def _update_mfe_mae(
        self,
        ticket: int,
        profit_price_delta: float,
        now: datetime | None = None,
    ) -> None:
        """Delegate — state owned by PositionTrackingLedger (S6-followup).
        entry_time anchor comes from the manager-owned _entry_timestamps."""
        self._tracking.update_mfe_mae(
            ticket,
            profit_price_delta,
            entry_time=self._entry_timestamps.get(ticket),
            now=now or datetime.now(UTC),
        )

    def _capture_reversal_state(
        self,
        ticket: int,
        pos: Any,
        probs: Any | None,
        regime_state: Any | None,
        now: datetime,
    ) -> None:
        """Delegate — state owned by PositionTrackingLedger (S6-followup)."""
        self._tracking.capture_reversal_state(ticket, pos, probs, regime_state, now)

    def _close_sibling_legs(self, ticket: int, scenario: str, now: datetime) -> None:
        """
        Closes sibling tickets that belong to the SAME dispatch as `ticket`.

        A split order (multi-lot) can surface as several broker tickets sharing the
        originating order_id/request (`_entry_order_ids`). If one leg is being
        emergency-closed (LOSS_HARD_EXIT / PROFIT_GIVEBACK_CRITICAL / hold-score
        bailout), every live sibling leg must close too so the position is not left
        half-open and desynchronized. Never raises; a sibling failure is isolated.
        """
        try:
            order_id = self._entry_order_ids.get(ticket, "")
            if not order_id:
                return
            sibling_tickets = [
                t for t, oid in self._entry_order_ids.items() if oid == order_id and t != ticket
            ]
            if not sibling_tickets:
                return
            logger.warning(
                "[POSITION] SPLIT_DESYNC_SYNC_CLOSE",
                origin_ticket=ticket,
                siblings=sibling_tickets,
                scenario=scenario,
            )
            for sibling in sibling_tickets:
                try:
                    if self.adapter.close_position(ticket=sibling):
                        self._forced_exit_mechanisms.setdefault(
                            sibling, ExitMechanism.HOLD_SCORE_DECAY
                        )
                        with self._live_tickets_lock:
                            self._live_tickets_cache.pop(sibling, None)
                        logger.warning(
                            "[POSITION] SPLIT_SIBLING_CLOSED",
                            sibling=sibling,
                            origin_ticket=ticket,
                        )
                except Exception as leg_err:
                    logger.error(
                        "[POSITION] SPLIT_SIBLING_CLOSE_FAILED (isolated)",
                        sibling=sibling,
                        error=str(leg_err),
                    )
        except Exception as err:
            logger.error("[POSITION] SPLIT_SYNC close failed (isolated)", error=str(err))

    def _cleanup_ticket_state(self, ticket: int) -> None:
        """Releases all per-ticket state after the closing autopsy row has been written."""
        if hasattr(self, "_last_telemetry_time"):
            self._last_telemetry_time.pop(ticket, None)
        for tracker in (
            self._partial_closed_tickets,
            self._mfe_tracker,
            self._mae_tracker,
            # PHASE 08 excursion timing & execution-quality evidence
            self._time_to_mfe_sec,
            self._time_to_mae_sec,
            self._entry_expected_price,
            self._entry_atr,
            self._entry_spread,
            self._entry_fill_latency_ms,
            self._entry_timestamps,
            self._last_tick_timestamps,
            self._time_in_profit_sec,
            self._time_in_drawdown_sec,
            self._peak_profit_usd,
            self._peak_drawdown_usd,
            self._lsf_state,
            self._last_seen_ts,
            self._stagnation_ticks,
            self._adverse_ticks,
            self._favorable_ticks,
            self._hold_score_tracker,
            self._base_hold_score_tracker,
            self._last_reasons_tracker,
            self._rescue_registered_tickets,
            self._last_modify_sl,
            self._last_price_tracker,
            self._entry_prices,
            self._entry_sls,
            self._entry_tps,
            self._last_known_volume,
            self._initial_risks,
            self._entry_directions,
            # Ledger autopsy context
            self._entry_reasons,
            self._entry_confidences,
            self._entry_regimes,
            self._entry_order_ids,
            self._sl_modified_flags,
            self._forced_exit_mechanisms,
            self._reversal_events,
            self._entry_probs,
            self._entry_regime_state,
            self._net_pnl_by_ticket,
            self._exit_mechanism_by_ticket,
            # New state structures (position state owned by the machine; dropped below)
            # Recovery structures (owned by the ledger; dropped below)
            self._closed_tickets,
            self._exit_pending_final_reason,
        ):
            tracker.pop(ticket, None)
        # P0 seam S5: one atomic teardown for the whole canonical record —
        # replaces the per-field pops above and eliminates partial-cleanup drift.
        self._states.remove(ticket)
        self._pending_lifecycle.drop_ticket(ticket)
        self._scoring._trajectory_history.pop(ticket, None)
        self._recovery_ledger.drop_ticket(ticket)
        self._state_machine.drop_ticket(ticket)
        with self._live_tickets_lock:
            self._tickets_cache.pop_ticket(ticket)
