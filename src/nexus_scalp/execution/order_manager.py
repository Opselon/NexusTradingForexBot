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

import json
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.configuration.config import AlgoConfig
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import Position, SymbolInfo, TickData, TradeOrder
from nexus_scalp.execution.execution_plan import ExecutionPlan
from nexus_scalp.execution.hold_score_ledger import HoldScoreLedger
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
from nexus_scalp.execution.terminal_outcome import emit_terminal_pending_outcome
from nexus_scalp.execution.tickets_cache import TicketsCache
from nexus_scalp.experience.lifecycle import DecisionLifecycle
from nexus_scalp.experience.outcome_recovery import (
    classify_exit_with_evidence,
    reconstruct_broker_outcome,
)
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

#: Pending limit orders are locked (immune to cancel/recreate churn) for this long.
PENDING_ORDER_LOCK_SECONDS: float = 30.0

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
    ) -> None:
        self._last_mod_price: dict[int, float] = {}
        #: TASK-3: optional immutable position-timeline tracker. When present,
        #: the close path finalizes the position timeline (POSITION_EXITED
        #: event) with the canonical realized PnL / R / exit mechanism so the
        #: lifecycle chain is complete (BUG-086). Never blocks on failure.
        self.lifecycle_tracker = lifecycle_tracker
        self._last_mod_time: dict[int, datetime] = {}
        self.adapter = adapter
        self.mt5_adapter = adapter
        self.audit = audit_repo or AuditRepository()
        self.notifier = notifier
        self.rule_matrix = rule_matrix
        self.algo_config = algo_config or AlgoConfig()
        # Optional RiskEngine used to clamp every dispatch to HARD_MAX_LOTS and
        # perform free-margin pre-checks. When absent, a local clamp still applies.
        self.risk_engine = risk_engine
        self.experience_engine = experience_engine
        self._processed_orders: dict[str, bool] = {}

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
        self._partial_closed_tickets: dict[int, bool] = {}

        # [EXPANDED] Maps position ticket -> Telegram message_id for Thread Replying
        self._order_message_ids: dict[int, int] = {}
        # [EXPANDED] Maps order_id (from proposal/TradeOrder) -> Telegram message_id
        self._order_id_to_message_id: dict[str, int] = {}

        # [EXPANDED] State tracking for extended notifications
        self._entry_prices: dict[int, float] = {}
        self._entry_sls: dict[int, float] = {}
        self._entry_tps: dict[int, float] = {}
        self._last_known_volume: dict[int, float] = {}
        self._initial_risks: dict[int, float] = {}

        #: PHASE 08: seconds from open to each observed excursion extreme.
        #: PHASE 08: execution-quality evidence captured at fill time.
        self._entry_expected_price: dict[int, float] = {}
        self._entry_atr: dict[int, float] = {}
        self._entry_spread: dict[int, float] = {}
        self._entry_fill_latency_ms: dict[int, float] = {}
        self._entry_timestamps: dict[int, datetime] = {}

        # Advanced Telemetry Trackers

        # Local State Features (LSF) Engine & Desync State Trackers
        self._rescue_registered_tickets: dict[int, bool] = {}
        self._last_modify_sl: dict[int, float] = {}
        self._entry_directions: dict[int, str] = {}

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

        # Part 4: Pending Order Lifecycle Management tracking
        self._pending_orders_setup_time: dict[int, datetime] = {}

        # Bounded trajectory history (ticket -> deque[PositionEvaluationStep])
        self._trajectory_history: dict[int, deque[PositionEvaluationStep]] = {}
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
        self._entry_reasons: dict[int, str] = {}
        self._entry_confidences: dict[int, float] = {}
        self._entry_regimes: dict[int, str] = {}
        self._entry_order_ids: dict[int, str] = {}
        # SETUP SNAPSHOT (2026-08-18): ticket -> full chart-state fingerprint at
        # dispatch (HTF/SMC/ICT structure, displacement, sessions, guardian).
        # Carried to the closed-trade autopsy for setup/strategy attribution.
        self._entry_setup_snapshots: dict[int, dict[str, Any]] = {}
        #: Ticket -> True once trailing/breakeven actually moved the broker-side SL.
        self._sl_modified_flags: dict[int, bool] = {}
        #: TASK-3: ticket -> bounded list of reversal/regime/liquidity observations
        #: captured WHILE the position was open (MODEL_REVERSAL, REGIME_REVERSAL,
        #: LIQUIDITY_REVERSAL, CONFIDENCE_COLLAPSE). Persisted on the closing
        #: autopsy row so outcome/behavior/reporting can prove WHAT changed while
        #: the position was held — never recomputed from price geometry alone.
        #: Ticket -> net realized PnL / exit mechanism captured during the
        #: closing sweep, used by the lifecycle finalize hook (BUG-086).
        self._net_pnl_by_ticket: dict[int, float] = {}
        self._exit_mechanism_by_ticket: dict[int, str] = {}
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
        self._forced_exit_mechanisms: dict[int, str] = {}
        #: Ticket -> most recent TickData observed for that ticket (used by the
        #: breakeven-aware VOLATILITY_EXPANSION exit logic to decide whether price has
        #: actually breached the locked protective stop before a market close is allowed).
        #: Latest account snapshot, stamped onto each autopsy row.
        self._last_account_balance: float = 0.0
        self._last_account_equity: float = 0.0
        self._peak_equity: float = 0.0
        #: Entry context staged by the policy/engine before the ticket exists
        #: (BUG-081). Bounded registry keyed by the originating order/request id
        #: so EVERY sibling ticket of a broker split-fill resolves the SAME
        #: immutable entry context (order_id, reason, confidence, regime,
        #: expected entry, dispatch clock, setup snapshot). Entries are removed
        #: once the fill family has been bound (idempotent) or after a stale TTL.
        self._pending_context_registry: dict[str, dict[str, Any]] = {}
        #: monotonic dispatch clock per order_id -> fractional-hours age used by
        #: the stale-entry sweep (bounded memory).
        self._pending_context_ts: dict[str, float] = {}
        #: order_id -> set of tickets already bound (idempotent family tracking).
        self._context_bound_tickets: dict[str, set[int]] = {}
        #: tickets with NO staging context ever registered (provenance gap,
        #: BUG-081 error-path observability; entries are distinct from legit 0.0).
        self._unbound_ticket_contexts: dict[int, str] = {}
        self._PENDING_CONTEXT_TTL_SEC: float = 3600.0
        self._PENDING_CONTEXT_MAX_ENTRIES: int = 64
        #: Phase 14: tickets already reconciled from broker history (dedup guard
        #: for the reconciliation close-loop across repeated passes/restarts).
        self._reconcile_seen: dict[int, bool] = {}
        #: P0-A (BUG-140): the most recent cancel reason per pending ticket so
        #: the terminal outcome can distinguish CANCELED_UNFILLED from
        #: EXPIRED_UNFILLED (AGE_EXPIRATION path).
        self._pending_cancel_reasons: dict[int, str] = {}
        #: TASK-7: tickets the engine has positively closed or that the broker no
        #: longer reports. Once closed, NO protective modification may be issued for
        #: the ticket (invariant: a CLOSED position cannot receive further protective
        #: modifications).
        self._closed_tickets: dict[int, bool] = {}
        #: TASK-7: last arbitrated exit decision per ticket (action + scenario +
        #: timestamp). Set at arbitration time, cleared at autopsy, used for exit
        #: traceability when the position closes before the next management pass.
        self._exit_pending_final_reason: dict[int, dict[str, Any]] = {}
        #: TASK-7: monotonic gate for the reconciliation close-loop broker fetch.
        #: Prevents a per-tick history_deals_get (BUG-090).
        self._last_reconcile_attempt: float = 0.0

    # =========================================================================
    # MODULE A: LEDGER AUTOPSY CONTEXT INGESTION
    # =========================================================================

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
        """
        Stages the entry context of the order that is about to be dispatched.

        (BUG-081) The context is held in a BOUNDED registry keyed by the
        originating order/request id so that EVERY sibling ticket of a broker
        split-fill resolves the SAME immutable context -- not just the first
        ticket. A later `register_entry_context` for a NEW order id naturally
        replaces the previous entry (one dispatch at a time), but a multi-ticket
        fill family keeps its context until the family has been fully bound or
        the stale TTL expires.

        `expected_entry` and `dispatch_monotonic` are Phase 08 execution-quality
        evidence: they let the closing autopsy compute real slippage and fill
        latency instead of guessing.
        """
        ctx = {
            "order_id": order_id,
            "entry_reason": entry_reason,
            "ai_confidence": float(ai_confidence or 0.0),
            "market_regime": market_regime,
            "expected_entry": float(expected_entry or 0.0),
            "dispatch_monotonic": float(dispatch_monotonic or 0.0),
            "setup_snapshot": dict(setup_snapshot or {}),
        }
        key = order_id or ""
        self._pending_context_registry[key] = ctx
        self._pending_context_ts[key] = time.monotonic()
        self._sweep_stale_pending_contexts()

    def _sweep_stale_pending_contexts(self) -> None:
        """Evicts stale / over-capacity pending-context registry entries.

        Bounded memory guard: entries older than `_PENDING_CONTEXT_TTL_SEC`
        or beyond `_PENDING_CONTEXT_MAX_ENTRIES` (oldest first) are dropped.
        A dropped context is an explicit provenance gap for tickets that
        arrive after the TTL -- handled by the caller's error path, never
        silently as legitimate zero confidence.
        """
        now = time.monotonic()
        stale = [
            k
            for k, ts in self._pending_context_ts.items()
            if now - ts > self._PENDING_CONTEXT_TTL_SEC
        ]
        for k in stale:
            self._pending_context_registry.pop(k, None)
            self._pending_context_ts.pop(k, None)
            self._context_bound_tickets.pop(k, None)
        if len(self._pending_context_registry) > self._PENDING_CONTEXT_MAX_ENTRIES:
            oldest = sorted(self._pending_context_ts.items(), key=lambda kv: kv[1])[
                : len(self._pending_context_registry) - self._PENDING_CONTEXT_MAX_ENTRIES
            ]
            for k, _ in oldest:
                self._pending_context_registry.pop(k, None)
                self._pending_context_ts.pop(k, None)
                self._context_bound_tickets.pop(k, None)

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
        """
        Gates modification of a live pending order.

        A re-quote is permitted only when BOTH conditions hold:
          - time_since_placement > PENDING_ORDER_LOCK_SECONDS (30s), AND
          - price drift >= 1.0 x ATR.

        This is the 30-second pending lock that prevents cancel/recreate churn.
        """
        last_price = self._last_mod_price.get(ticket)
        last_time = self._last_mod_time.get(ticket)

        if last_price is not None and last_time is not None:
            price_drift = abs(price - last_price)
            time_delta = (now - last_time).total_seconds()

            if time_delta <= PENDING_ORDER_LOCK_SECONDS:
                logger.debug(
                    "PENDING_ORDER_LOCKED: modification suppressed inside 30s lock",
                    ticket=ticket,
                    age_sec=round(time_delta, 1),
                )
                return False

            if price_drift < (1.0 * atr):
                logger.debug(
                    "PENDING_ORDER_HELD: drift below 1.0x ATR",
                    ticket=ticket,
                    drift=round(price_drift, 2),
                    required=round(atr, 2),
                )
                return False

        self._last_mod_price[ticket] = price
        self._last_mod_time[ticket] = now
        return True

    def get_active_live_tickets(self) -> list[dict[str, Any]]:
        """Returns a list of currently live active positions and pending orders matching symbol and magic number."""
        with self._live_tickets_lock:
            return list(self._live_tickets_cache.values())

    def execute_order(self, order: TradeOrder) -> bool:
        """Submits trade deal to broker adapter with duplicate submission prevention."""
        if self.global_state == "SAFE_MODE":
            logger.warning("Order blocked: Safety State is SAFE_MODE.")
            return False

        if order.order_id in self._processed_orders:
            logger.warning(
                "Duplicate order submission blocked by idempotency check", order_id=order.order_id
            )
            return False

        logger.info(
            "Dispatching trade order to broker adapter",
            order_id=order.order_id,
            symbol=order.symbol,
            volume=order.volume,
        )

        success = self.adapter.send_order(order)

        if not success:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self.global_state = "SAFE_MODE"
                logger.critical("TRANSITIONED TO SAFE_MODE: 3 consecutive rejections detected!")
        else:
            self._consecutive_failures = 0

        status_str = "FILLED" if success else "REJECTED"

        self._processed_orders[order.order_id] = success
        self.audit.log_execution(order, status_str)

        self.audit.log_order(
            ticket=0,
            order_id=order.order_id,
            symbol=order.symbol,
            action="Executed order",
            price=order.price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            volume=order.volume,
            reason="execute_order executed",
            latency=0.015,
            execution_mode="STANDARD",
        )

        return success

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
        """
        positions, pendings = self.count_total_exposure(symbol=symbol)
        return (positions + pendings) < MAX_TOTAL_EXPOSURE

    def _clamp_dispatch_volume(self, volume: float, symbol: str | None = None) -> float:
        """
        Routes every dispatch volume through the risk engine clamp when available and
        applies the absolute HARD_MAX_LOTS ceiling unconditionally.
        """
        try:
            vol = float(volume)
        except (TypeError, ValueError):
            return 0.0

        if vol <= 0.0:
            return 0.0

        if self.risk_engine is not None and hasattr(self.risk_engine, "get_clamped_position_size"):
            account = None
            symbol_info = None
            try:
                account = self.adapter.get_account_info()
            except Exception:
                account = None
            try:
                if symbol:
                    symbol_info = self.adapter.get_symbol_info(symbol)
            except Exception:
                symbol_info = None

            try:
                vol = float(
                    self.risk_engine.get_clamped_position_size(
                        volume=vol,
                        account=account,
                        symbol_info=symbol_info,
                    )
                )
            except Exception as clamp_err:
                logger.error(
                    "Risk engine clamp failed; falling back to hard cap", error=str(clamp_err)
                )

        clamped = min(vol, HARD_MAX_LOTS)
        if clamped < vol:
            logger.warning(
                "LOT SIZE CLAMPED to HARD_MAX_LOTS",
                requested=round(vol, 2),
                clamped=round(clamped, 2),
                hard_max=HARD_MAX_LOTS,
            )
        return round(clamped, 2)

    def dispatch_order(
        self, decision: Any, volume: float, setup_snapshot: dict[str, Any] | None = None
    ) -> bool:
        """
        Unified dispatch router for new entry signals (BUY, SELL, BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP).

        Enforces, in order: MAX_TOTAL_EXPOSURE, the HARD_MAX_LOTS clamp via the risk
        engine, and entry-context capture for the ledger autopsy.

        `setup_snapshot` (2026-08-18): the full chart-state fingerprint (HTF/SMC/ICT
        structure, displacement, sessions, guardian) captured at dispatch by the
        caller, attached to the entry context and persisted in the closed-trade
        autopsy row for post-hoc strategy/setup attribution.
        """
        action = decision.action
        symbol = decision.symbol
        price = decision.proposed_entry
        sl = decision.stop_loss
        tp = decision.take_profit

        # --- MAX EXPOSURE ENFORCEMENT (1 position OR 1 pending, engine-wide) ---
        if not self._is_exposure_available(symbol=symbol):
            positions, pendings = self.count_total_exposure(symbol=symbol)
            # BUG-072/073: the internal view is broker-reconciled every tick
            # (manage_active_positions + reconcile_pending_state) and after
            # every verified cancel, so this block reflects real broker state.
            logger.warning(
                "[ENTRY_BLOCKED] layer=EXPOSURE reason=MAX_EXPOSURE_REACHED "
                "open_positions=%s pending_internal=%s max_total_exposure=%s stale_state=false",
                positions,
                pendings,
                MAX_TOTAL_EXPOSURE,
                action=getattr(action, "value", str(action)),
            )
            # P0-A (BUG-140): the decision is terminal — it will never become a
            # trade. Record NOT_DISPATCHED so the experience ledger cannot hang.
            emit_terminal_pending_outcome(
                experience_engine=self.experience_engine,
                request_id=str(getattr(decision, "request_id", "") or ""),
                state=DecisionLifecycle.NOT_DISPATCHED,
                detail="MAX_EXPOSURE_REACHED at dispatch",
            )
            return False

        # --- STRICT LOT SIZING CLAMP (HARD_MAX_LOTS + free margin pre-check) ---
        volume = self._clamp_dispatch_volume(volume, symbol=symbol)
        if volume <= 0.0:
            logger.warning(
                "LOT_SIZE_REJECTED: clamped volume is zero (insufficient free margin or invalid size)",
                action=getattr(action, "value", str(action)),
                symbol=symbol,
            )
            # P0-A (BUG-140): terminal NOT_DISPATCHED (never sent to broker).
            emit_terminal_pending_outcome(
                experience_engine=self.experience_engine,
                request_id=str(getattr(decision, "request_id", "") or ""),
                state=DecisionLifecycle.NOT_DISPATCHED,
                detail="LOT_SIZE_REJECTED (zero volume after clamp)",
            )
            return False

        # Stage the entry context so the ledger autopsy row carries WHY we entered,
        # plus the Phase 08 execution-quality baseline (expected fill + dispatch clock).
        self.register_entry_context(
            order_id=getattr(decision, "request_id", "") or "",
            entry_reason=self._resolve_entry_reason(decision),
            ai_confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
            market_regime=str(getattr(decision, "regime", "") or ""),
            expected_entry=float(getattr(decision, "proposed_entry", 0.0) or 0.0),
            dispatch_monotonic=time.monotonic(),
            setup_snapshot=setup_snapshot,
        )

        logger.info(
            "dispatch_order mapping action to MT5 command",
            action=action,
            symbol=symbol,
            volume=volume,
            execution_id=getattr(decision, "execution_id", None),
        )

        if action in (
            ActionType.BUY,
            ActionType.BUY_MARKET,
            ActionType.SELL,
            ActionType.SELL_MARKET,
        ):
            order_type = OrderType.BUY if "BUY" in action.value else OrderType.SELL
            ticket = self.mt5_adapter.execute_market_order(
                symbol=symbol,
                order_type=order_type,
                volume=volume,
                price=price,
                stop_loss=sl,
                take_profit=tp,
            )
            # Log broker confirmation exactly as required
            logger.info(
                f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: {volume}"
            )
            if ticket > 0:
                self.audit.log_order(
                    ticket=ticket,
                    order_id=decision.request_id,
                    symbol=symbol,
                    action="Executed order",
                    price=price,
                    stop_loss=sl,
                    take_profit=tp,
                    volume=volume,
                    reason=f"dispatch_order {action.value} | exec={getattr(decision, 'execution_id', '') or ''}",
                    latency=0.012,
                    execution_mode=getattr(decision, "execution_mode", "STANDARD") or "STANDARD",
                    execution_id=getattr(decision, "execution_id", None),
                )
            else:
                # P0-A (BUG-140): market dispatch refused (retcode/ticket=0) —
                # the decision can never fill; record the terminal state.
                emit_terminal_pending_outcome(
                    experience_engine=self.experience_engine,
                    request_id=str(getattr(decision, "request_id", "") or ""),
                    state=DecisionLifecycle.REJECTED_UNFILLED,
                    detail="broker refused market order at dispatch (ticket=0)",
                )
            return ticket > 0

        elif action in (
            ActionType.BUY_LIMIT,
            ActionType.SELL_LIMIT,
            ActionType.BUY_STOP,
            ActionType.SELL_STOP,
        ):
            if action == ActionType.BUY_LIMIT:
                order_type = OrderType.BUY_LIMIT
            elif action == ActionType.SELL_LIMIT:
                order_type = OrderType.SELL_LIMIT
            elif action == ActionType.BUY_STOP:
                order_type = OrderType.BUY_STOP
            else:
                order_type = OrderType.SELL_STOP

            ticket = self.mt5_adapter.place_pending_order(
                symbol=symbol,
                order_type=order_type,
                volume=volume,
                price=price,
                stop_loss=sl,
                take_profit=tp,
            )
            if ticket > 0:
                logger.info(
                    f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: {volume}"
                )
                self.audit.log_order(
                    ticket=ticket,
                    order_id=decision.request_id,
                    symbol=symbol,
                    action="Generated candidate",
                    price=price,
                    stop_loss=sl,
                    take_profit=tp,
                    volume=volume,
                    reason=f"dispatch_order pending {action.value} | exec={getattr(decision, 'execution_id', '') or ''}",
                    latency=0.011,
                    execution_mode=getattr(decision, "execution_mode", "STANDARD") or "STANDARD",
                    execution_id=getattr(decision, "execution_id", None),
                )
            else:
                logger.error(
                    f"Pending order dispatch rejected by broker server | Action: {action.value} | Lots: {volume}"
                )
                # P0-A (BUG-140): broker refused the pending order at dispatch —
                # the decision can never fill; record the terminal state.
                emit_terminal_pending_outcome(
                    experience_engine=self.experience_engine,
                    request_id=str(getattr(decision, "request_id", "") or ""),
                    state=DecisionLifecycle.REJECTED_UNFILLED,
                    detail="broker rejected pending order at dispatch (ticket=0)",
                )
            return ticket > 0

        return False

    def _resolve_entry_reason(self, decision: Any) -> str:
        """
        Normalizes the policy decision into one of the canonical ledger entry reasons:
        SMC_GOD_MODE, FAST_LIQUIDITY_SWEEP, or PURE_AI.
        """
        execution_mode = str(getattr(decision, "execution_mode", "") or "")
        reason_code = str(getattr(decision, "reason_code", "") or "")

        if "SMC_GOD_MODE" in execution_mode or "SMC_GOD_MODE" in reason_code:
            return "SMC_GOD_MODE"
        if "SWEEP" in reason_code.upper() or "TICK_SWEEP" in execution_mode:
            return "FAST_LIQUIDITY_SWEEP"
        return "PURE_AI"

    def _bind_pending_entry_context(self, ticket: int, decision_order_id: str = "") -> None:
        """Binds the staged entry context to a freshly observed ticket.

        (BUG-081) Resolves the context from the bounded registry keyed by the
        originating order/request id. Every ticket of a broker split-fill
        resolves the SAME immutable context (order_id, reason, confidence,
        regime, expected entry, dispatch clock, setup snapshot). The registry
        entry is removed only when the WHOLE fill family has been bound
        (idempotent family tracking via `_context_bound_tickets`), so a
        delayed sibling ticket never loses its provenance.

        When NO context was ever staged for the order, the ticket is marked in
        `_unbound_ticket_contexts` (distinct from a legitimate 0.0 confidence)
        with the reason -- never silently treated as a zero-confidence entry.
        """
        bound = False
        reason_gap = ""
        # Resolve the staging context, in order:
        #   1. explicit decision_order_id (caller-provided parent link)
        #   2. the "" legacy slot (order without an explicit id)
        #   3. the SINGLE most recent not-fully-bound dispatch family (the
        #      current in-flight order; broker tickets arrive without a parent
        #      id at bind time, BUG-081). This is the split-fill fix: every
        #      sibling of the same fill still resolves the same context.
        ctx = None
        if decision_order_id:
            ctx = self._pending_context_registry.get(decision_order_id)
        if ctx is None:
            ctx = self._pending_context_registry.get("")
        if ctx is None:
            for oid in sorted(
                self._pending_context_ts, key=self._pending_context_ts.get, reverse=True
            ):
                family = self._context_bound_tickets.get(oid, set())
                # A family still open (tickets live) is the current dispatch.
                if any(t in self._live_tickets_cache for t in family):
                    ctx = self._pending_context_registry.get(oid)
                    if ctx is not None:
                        break
            # Fallback: the newest registered context (front-of-line dispatch).
            if ctx is None and self._pending_context_ts:
                newest = max(self._pending_context_ts, key=self._pending_context_ts.get)
                ctx = self._pending_context_registry.get(newest)
        if ctx is None:
            reason_gap = "NO_STAGED_CONTEXT"
        else:
            self._entry_reasons[ticket] = ctx.get("entry_reason", "PURE_AI") or "PURE_AI"
            self._entry_confidences[ticket] = float(ctx.get("ai_confidence", 0.0) or 0.0)
            self._entry_regimes[ticket] = str(ctx.get("market_regime", "") or "")
            self._entry_order_ids[ticket] = str(ctx.get("order_id", "") or decision_order_id)
            # PHASE 08 execution-quality evidence.
            self._entry_expected_price[ticket] = float(ctx.get("expected_entry", 0.0) or 0.0)
            # SETUP SNAPSHOT (2026-08-18): full chart-state fingerprint captured at
            # dispatch, carried to the closed-trade autopsy for setup attribution.
            self._entry_setup_snapshots[ticket] = dict(ctx.get("setup_snapshot", {}) or {})
            dispatch_mono = float(ctx.get("dispatch_monotonic", 0.0) or 0.0)
            if dispatch_mono > 0.0:
                self._entry_fill_latency_ms[ticket] = max(
                    0.0, (time.monotonic() - dispatch_mono) * 1000.0
                )
            bound = True
            # Idempotent family tracking: keep the context until EVERY ticket of
            # the fill family has been bound. The family is defined by the set of
            # tickets that ever resolved this order id; when this ticket is the
            # first of the family it stays registered so delayed siblings bind.
            oid = self._entry_order_ids.get(ticket) or decision_order_id or ""
            family = self._context_bound_tickets.setdefault(oid, set())
            family.add(ticket)
            logger.info(
                "[TRADE_LINEAGE] context_bound=true",
                parent_execution_id=oid,
                child_ticket=ticket,
                family_size=len(family),
            )
        if not bound:
            # Provenance gap: never silence missing context as legitimate 0.0.
            self._unbound_ticket_contexts[ticket] = reason_gap
            self._entry_reasons.setdefault(ticket, "PURE_AI")
            self._entry_order_ids.setdefault(ticket, decision_order_id)
            logger.warning(
                "[TRADE_LINEAGE] context_bound=false",
                child_ticket=ticket,
                reason=reason_gap,
                decision_order_id=decision_order_id,
            )

    def _prune_bound_context(self, order_id: str) -> None:
        """Removes a fully-bound context family from the registry.

        Called from the close path after the FINAL sibling of the fill family
        has closed, so the registry cannot grow without bound. Idempotent.
        """
        if not order_id:
            return
        family = self._context_bound_tickets.get(order_id, set())
        if not family:
            return
        # Only prune when every bound ticket has been cleaned up (closed).
        if any(t in self._live_tickets_cache for t in family):
            return
        self._pending_context_registry.pop(order_id, None)
        self._pending_context_ts.pop(order_id, None)
        self._context_bound_tickets.pop(order_id, None)
        logger.info(
            "[TRADE_LINEAGE] context_pruned",
            parent_execution_id=order_id,
            family_size=len(family),
        )

    # =========================================================================
    # P0-A (BUG-140): TERMINAL PENDING-ORDER EXPERIENCE OUTCOMES
    # -------------------------------------------------------------------------
    # A decision that never becomes a trade MUST still terminate in the
    # experience ledger with an explicit lifecycle state, otherwise the
    # research dataset permanently reports MISSING_OUTCOME for it.
    # =========================================================================

    def _emit_terminal_for_pending(self, ticket: int, state: Any, detail: str = "") -> bool:
        """Emits the terminal outcome for the decision that placed `ticket`.

        The request_id is resolved from the staged entry context registry
        (`_entry_order_ids[ticket]` is bound to the originating
        decision.request_id at context-bind time). Idempotent: the ledger
        refuses a second outcome for the same key, so repeated sweeps,
        retries or restart replays cannot duplicate the row.
        """
        request_id = str(self._entry_order_ids.get(ticket, "") or "")
        if not request_id:
            # Nothing to attribute: the order was never bound to a tracked
            # decision (e.g. manual order) — nothing to record, no fabrication.
            return False
        written = emit_terminal_pending_outcome(
            experience_engine=self.experience_engine,
            request_id=request_id,
            state=state,
            detail=detail or f"broker ticket {ticket} terminal",
            broker_order_id=str(ticket),
        )
        if written:
            # The lifecycle is closed: drop the ephemeral cancel-reason note.
            self._pending_cancel_reasons.pop(ticket, None)
        return written

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
                    latency=0.009,
                    execution_mode="AI_REVERSAL",
                )
                if self.notifier:
                    try:
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
                    except Exception:
                        pass
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
            volume = closed_volume
            logger.info(
                "AI REVERSAL: sizing flip from closed exposure",
                mirrored_volume=round(closed_volume, 2),
            )

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
            self._forced_exit_mechanisms[ticket] = ExitMechanism.AI_REVERSAL_EXIT
            logger.info("Intercepted CLOSE_POSITION as AI_REVERSAL_SIGNAL", ticket=ticket)
            return self.execute_ai_reversal(decision=decision, volume=volume)

        logger.info(
            "execute_lifecycle_action mapping action to MT5 command", action=action, ticket=ticket
        )

        if action == ActionType.CLOSE_POSITION:
            success = self.mt5_adapter.close_position(ticket=ticket)
            logger.info(
                f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: 0.0"
            )
            if success:
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
                    latency=0.009,
                    execution_mode="STANDARD",
                )
            return success

        elif action == ActionType.PARTIAL_CLOSE:
            success = self.mt5_adapter.close_position(ticket=ticket, volume=volume)
            lots = volume if volume is not None else 0.0
            logger.info(
                f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: {lots}"
            )
            if success:
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
                    latency=0.010,
                    execution_mode="STANDARD",
                )
            return success

        elif action == ActionType.MODIFY_SL_TP:
            success = self.mt5_adapter.modify_order(
                ticket=ticket, stop_loss=decision.stop_loss, take_profit=decision.take_profit
            )
            logger.info(
                f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: 0.0"
            )
            if success:
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
                    latency=0.011,
                    execution_mode="STANDARD",
                )
            return success

        elif action == ActionType.CANCEL_ORDER:
            # BUG-072/073: broker-verified cancellation — never release the
            # exposure slot on a send-result alone.
            success = self.cancel_pending_order_verified(ticket=ticket)
            logger.info(
                f"*** REAL ORDER/EXECUTION EXECUTED ON BROKER SERVER *** Ticket: {ticket} | Action: {action.value} | Lots: 0.0"
            )
            if success:
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
                    latency=0.008,
                    execution_mode="STANDARD",
                )
            return success

        return False

    # =========================================================================
    # BROKER-VERIFIED PENDING CANCELLATION (BUG-072/073)
    # -------------------------------------------------------------------------
    # A pending order is considered CANCELED only when broker state confirms
    # it. `cancel_pending_order()` returning False (e.g. retcode 0 = request
    # never reached the server) must NEVER release the exposure slot. The
    # helper below sends the cancel, then verifies with orders_get() and
    # history_orders_get() before declaring success.
    # =========================================================================
    def _pending_broker_state(self, ticket: int, symbol: str | None = None) -> str:
        """Returns broker truth for a pending ticket.

        ACTIVE  - the ticket is still listed as an active pending order.
        GONE    - the ticket is provably gone: absent from the active list AND
                  the send already succeeded, OR history shows a terminal state.
        UNKNOWN - neither can be positively established (query error, failed
                  send with empty/ambiguous active list, no history record).
        """
        query_error = False
        active_result: str | None = None  # None = query unavailable
        try:
            get_pending_fn = getattr(self.adapter, "get_pending_orders", None)
            if get_pending_fn:
                pendings = get_pending_fn(symbol=symbol)
                if pendings is None:
                    query_error = True
                else:
                    active_result = "ACTIVE"
                    for p in pendings:
                        if int(self._pending_field(p, "ticket", "order_id") or 0) == int(ticket):
                            return "ACTIVE"
                    active_result = "GONE"
        except Exception as verify_err:
            query_error = True
            logger.warning(
                "[PENDING_ORDER] event=CANCEL_VERIFY error=orders_get_failed context=fallback_to_history",
                ticket=ticket,
                error=str(verify_err),
            )
        # Active-order query unavailable/errored: check history_orders_get for a
        # terminal state (CANCELED=2, PARTIAL=3, FILLED=4, REJECTED=5, EXPIRED=6)
        # which positively proves the order is done.
        hist_terminal = None  # None = no history evidence, True/False = terminal/active
        try:
            hist_fn = getattr(self.adapter, "get_history_orders", None)
            if hist_fn:
                from datetime import UTC as _UTC
                from datetime import datetime as _dt
                from datetime import timedelta as _td

                now = _dt.now(_UTC)
                hist = hist_fn(now - _td(hours=1), now, symbol=symbol)
                for h in hist or []:
                    if int(getattr(h, "ticket", 0) or 0) == int(ticket):
                        st = int(getattr(h, "state", 0) or 0)
                        if st in (0, 1, 7, 8, 9):  # STARTED/PLACED/REQUEST_*
                            hist_terminal = False
                        else:
                            hist_terminal = True  # canceled/filled/rejected/expired
                        break
        except Exception as hist_err:
            query_error = True
            logger.warning(
                "[PENDING_ORDER] event=CANCEL_VERIFY error=history_query_failed",
                ticket=ticket,
                error=str(hist_err),
            )
        if active_result == "ACTIVE" or hist_terminal is False:
            return "ACTIVE"
        if active_result == "GONE" or hist_terminal is True:
            return "GONE"
        if query_error:
            return "UNKNOWN"
        return "UNKNOWN"

    def cancel_pending_order_verified(self, ticket: int, symbol: str | None = None) -> bool:
        """Sends the cancel request, THEN verifies broker state.

        Returns True ONLY when broker truth confirms the order is no longer
        active (ACTIVE->GONE, or a DONE send followed by an absent active
        listing). Returns False while the order is still active OR the state
        is UNKNOWN — the exposure slot stays occupied. On confirmation the
        internal live-tickets cache is refreshed from the broker view so a
        stale internal pending can never hold the slot.
        """
        cancel_fn = getattr(self.adapter, "cancel_pending_order", None)
        if cancel_fn is None:
            logger.warning(
                "[PENDING_ORDER] event=CANCEL_REQUEST error=no_cancel_api ticket=%s",
                ticket,
            )
            return False
        logger.info("[PENDING_ORDER] event=CANCEL_REQUEST ticket=%s", ticket)
        try:
            sent = bool(cancel_fn(ticket=ticket))
        except Exception as cancel_err:
            logger.error(
                "[PENDING_ORDER] event=CANCEL_REQUEST error=cancel_raised ticket=%s",
                ticket,
                error=str(cancel_err),
            )
            sent = False

        # Broker truth decides, not the send result.
        state = self._pending_broker_state(ticket=ticket, symbol=symbol)
        if state == "ACTIVE":
            logger.warning(
                "[PENDING_ORDER] event=CANCEL_FAILED ticket=%s broker_state=STILL_ACTIVE send_result=%s",
                ticket,
                sent,
            )
            return False
        if state == "GONE":
            logger.info(
                "[PENDING_ORDER] event=CANCEL_CONFIRMED ticket=%s send_result=%s",
                ticket,
                sent,
            )
            # P0-A (BUG-140): the pending order is terminal at the broker. Emit
            # the terminal experience outcome so the originating decision can
            # never hang without classification (CANCELED vs EXPIRED by reason).
            state_lifecycle = (
                DecisionLifecycle.EXPIRED_UNFILLED
                if "AGE" in self._pending_cancel_reasons.get(ticket, "")
                else DecisionLifecycle.CANCELED_UNFILLED
            )
            self._emit_terminal_for_pending(ticket=ticket, state=state_lifecycle)
            self._pending_orders_setup_time.pop(ticket, None)
            try:
                self.refresh_live_tickets_cache(symbol=symbol)
            except Exception as refresh_err:
                logger.error(
                    "[PENDING_ORDER] event=CANCEL_CONFIRMED error=cache_refresh_failed",
                    ticket=ticket,
                    error=str(refresh_err),
                )
            return True
        # UNKNOWN: a DONE send with a (possibly stale) empty active list is
        # still broker-positive enough to confirm; anything else keeps the lock.
        if sent and state == "UNKNOWN":
            logger.info(
                "[PENDING_ORDER] event=CANCEL_CONFIRMED ticket=%s state=UNKNOWN_but_done_send",
                ticket,
            )
            self._pending_orders_setup_time.pop(ticket, None)
            try:
                self.refresh_live_tickets_cache(symbol=symbol)
            except Exception as refresh_err:
                logger.error(
                    "[PENDING_ORDER] event=CANCEL_CONFIRMED error=cache_refresh_failed",
                    ticket=ticket,
                    error=str(refresh_err),
                )
            return True
        logger.warning(
            "[PENDING_ORDER] event=CANCEL_UNRESOLVED ticket=%s state=%s send_result=%s "
            "-> exposure slot remains occupied",
            ticket,
            state,
            sent,
        )
        return False

    def cancel_pending_order_with_retry(
        self, ticket: int, symbol: str | None = None, max_attempts: int = 3
    ) -> int:
        """Bounded, idempotent cancellation retry.

        Returns the number of cancel attempts used (0 <= n <= max_attempts).
        Each attempt sends the cancel request and verifies broker state;
        stops as soon as the broker confirms the order is gone. Never creates
        a cancellation storm and never releases the exposure slot early.
        """
        attempts = 0
        for _ in range(max(1, int(max_attempts))):
            attempts += 1
            if self.cancel_pending_order_verified(ticket=ticket, symbol=symbol):
                break
            time.sleep(0.05)  # tiny backoff between bounded retries
        return attempts

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
                    reason = self._pending_cancel_reasons.get(gone_ticket, "")
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

    def reconcile_pending_state(
        self, symbol: str | None = None, current_tick: TickData | None = None
    ) -> dict[str, Any]:
        """Compares internal vs broker pending state and repairs the internal
        view so it reflects broker truth (broker wins).

        Returns a structured report:
          {"pending_internal": n, "pending_broker": m, "mismatch": bool,
           "repaired": bool, "broker_error": bool}
        """
        internal_pendings = 0
        with self._live_tickets_lock:
            for info in self._live_tickets_cache.values():
                if info.get("type") == "PENDING":
                    internal_pendings += 1
        broker_pendings = 0
        broker_error = False
        try:
            get_pending_fn = getattr(self.adapter, "get_pending_orders", None)
            if get_pending_fn:
                pendings = get_pending_fn(symbol=symbol)
                if pendings is None:
                    broker_error = True
                else:
                    broker_pendings = len(pendings)
        except Exception as rec_err:
            broker_error = True
            logger.error(
                "[EXECUTION_RECONCILIATION] event=MISMATCH error=broker_query_failed",
                error=str(rec_err),
            )
        mismatch = not broker_error and internal_pendings != broker_pendings
        repaired = False
        if mismatch:
            logger.warning(
                "[EXECUTION_RECONCILIATION] event=MISMATCH "
                "pending_internal=%s pending_broker=%s -> repairing internal view",
                internal_pendings,
                broker_pendings,
            )
            self.refresh_live_tickets_cache(symbol=symbol, current_tick=current_tick)
            repaired = True
        return {
            "pending_internal": internal_pendings,
            "pending_broker": broker_pendings,
            "mismatch": bool(mismatch),
            "repaired": repaired,
            "broker_error": broker_error,
        }

    def _is_closed_ticket(self, ticket: int) -> bool:
        """
        TASK-7 invariant guard: True when the ticket is positively closed or a close
        was already accepted, so no protective modification is ever issued for a
        position the broker no longer holds.
        """
        return bool(self._closed_tickets.get(ticket, False)) or bool(
            self.get_protection_state(ticket).close_requested
        )

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
        """Determines if the proposed new stop loss step is significantly different from last sent modification."""
        last_sl = self._last_modify_sl.get(ticket, 0.0)
        if abs(new_sl - last_sl) >= self.min_step:
            return True
        return False

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

    def _resolve_pip_size(self, symbol_info: SymbolInfo | None) -> float:
        """
        Canonical pip size resolver.

        A pip is 10 broker points, derived from `SymbolInfo.point` whenever the broker
        specification is available. Falls back to the project-wide gold pip constant
        (`DEFAULT_PIP_SIZE`, also used by `rule_matrix.py`) when it is not, so no
        XAUUSD point conversion is hard-coded at the call sites.
        """
        if symbol_info is not None:
            try:
                point = float(symbol_info.point)
                if point > 0.0 and not math.isnan(point) and not math.isinf(point):
                    return point * 10.0
            except (TypeError, ValueError):
                pass
        return DEFAULT_PIP_SIZE

    def _resolve_price_digits(self, symbol_info: SymbolInfo | None) -> int:
        """Broker price precision, defaulting to 2 decimals (XAUUSD convention)."""
        if symbol_info is not None:
            try:
                digits = int(symbol_info.digits)
                if 0 <= digits <= 10:
                    return digits
            except (TypeError, ValueError):
                pass
        return 2

    def _atr_profit_threshold_usd(
        self,
        volume: float,
        symbol_info: SymbolInfo | None,
        atr: float,
    ) -> float:
        """
        Converts `BREAKEVEN_ATR_MULTIPLIER` x ATR (price units) into this position's
        USD PnL using the same contract-size arithmetic the risk engine uses.

        Raw ATR price units are never compared against USD PnL directly.
        """
        try:
            atr_price_delta = max(float(atr), 0.0) * BREAKEVEN_ATR_MULTIPLIER
        except (TypeError, ValueError):
            return math.inf
        usd = self._price_delta_to_usd(atr_price_delta, volume, symbol_info)
        if usd <= 0.0 or math.isnan(usd) or math.isinf(usd):
            # A non-positive/invalid conversion must never create a free trigger.
            return math.inf
        return usd

    def calculate_breakeven_sl(
        self,
        pos: Position,
        symbol_info: SymbolInfo | None = None,
    ) -> float:
        """
        Breakeven stop price locking `BREAKEVEN_LOCK_PIPS` of profit beyond entry.

        BUY : entry + 0.20 pips
        SELL: entry - 0.20 pips
        """
        pip = self._resolve_pip_size(symbol_info)
        offset = BREAKEVEN_LOCK_PIPS * pip
        raw = pos.price_open + offset if pos.type == OrderType.BUY else pos.price_open - offset
        return round(raw, self._resolve_price_digits(symbol_info))

    @staticmethod
    def _is_sl_at_or_beyond(pos: Position, sl_value: float, reference_sl: float) -> bool:
        """
        True when `sl_value` is at or beyond `reference_sl` in the position's favourable
        direction. Used both for the restart-safe breakeven check and to guarantee an
        existing protective stop is never moved backwards.
        """
        if sl_value <= 0.0:
            return False
        if pos.type == OrderType.BUY:
            return sl_value >= (reference_sl - 1e-9)
        return sl_value <= (reference_sl + 1e-9)

    def refresh_protection_state(
        self,
        pos: Position,
        symbol_info: SymbolInfo | None = None,
    ) -> PositionProtectionState:
        """
        Reconciles per-ticket protection state with the position as the broker reports it.

        Performed on EVERY refresh so that:
          - `peak_win_usd` advances monotonically with floating PnL,
          - the breakeven level is always current, and
          - a position whose real SL already sits at/beyond breakeven is treated as
            protected even if this process just restarted and has no memory of it
            (prevents duplicate SL modifications after state reconstruction).
        """
        state = self.get_protection_state(pos.ticket)
        state.update_peak(pos.profit)

        breakeven_sl = self.calculate_breakeven_sl(pos, symbol_info)
        state.breakeven_sl_price = breakeven_sl

        # Real MT5 state wins over the in-memory flag: the flag is never the only
        # source of truth. Note this can only ever mark the position as MORE
        # protected, never less.
        if self._is_sl_at_or_beyond(pos, pos.sl, breakeven_sl):
            if not state.was_sl_modified:
                logger.debug(
                    "BREAKEVEN ALREADY PRESENT ON BROKER: reconstructing protected state",
                    ticket=pos.ticket,
                    actual_sl=pos.sl,
                    breakeven_sl=breakeven_sl,
                )
            state.was_sl_modified = True
            self._sl_modified_flags[pos.ticket] = True

        return state

    def _protective_sl_floor(self, ticket: int) -> float:
        """
        Lowest (BUY) / highest (SELL) stop price any later mechanism is allowed to set,
        i.e. the confirmed breakeven lock. Returns 0.0 when no lock is active.
        """
        state = self._protection_ledger.get(ticket)
        if state is None or not state.was_sl_modified:
            return 0.0
        return state.breakeven_sl_price

    def is_sl_improvement(self, pos: Position, new_sl: float) -> bool:
        """
        Guard shared by breakeven, ATR trailing and rule-driven SL moves.

        Returns True only when `new_sl` tightens protection: it must advance past the
        current broker SL in the profitable direction AND must never regress behind an
        already-confirmed breakeven lock.
        """
        if new_sl <= 0.0:
            return False

        is_buy = pos.type == OrderType.BUY

        # 1. Never loosen the stop the broker already holds.
        if pos.sl > 0.0:
            if is_buy and new_sl <= pos.sl:
                return False
            if not is_buy and new_sl >= pos.sl:
                return False

        # 2. Never move behind a confirmed breakeven lock.
        floor_sl = self._protective_sl_floor(pos.ticket)
        if floor_sl > 0.0:
            if is_buy and new_sl < (floor_sl - 1e-9):
                return False
            if not is_buy and new_sl > (floor_sl + 1e-9):
                return False

        return True

    def _log_protection_audit(
        self,
        pos: Position,
        action: str,
        reason: str,
        stop_loss: float = 0.0,
    ) -> None:
        """
        Writes a protection event to the SQLite audit ledger.

        Deliberately isolated and fully exception-guarded: an audit/telemetry failure
        must never prevent (or disable) a breakeven or close action.
        """
        try:
            self.audit.log_order(
                ticket=pos.ticket,
                order_id=f"protect_{pos.ticket}_{action.lower()}",
                symbol=pos.symbol,
                action=action,
                price=pos.price_open,
                stop_loss=stop_loss,
                take_profit=pos.tp,
                volume=pos.volume,
                reason=reason,
                latency=0.0,
                execution_mode="PROTECTION",
            )
        except Exception as err:
            logger.error(
                "Protection audit write failed (protection continues)",
                ticket=pos.ticket,
                error=str(err),
            )

    def apply_breakeven_lock(
        self,
        pos: Position,
        symbol_info: SymbolInfo | None = None,
        atr: float = 0.0,
        min_stop_gap: float = 0.0,
        current_tick: TickData | None = None,
    ) -> bool:
        """
        Priority-4 protection: locks a breakeven(+0.20 pip) stop once the position has
        earned meaningful profit.

        Activation (either trigger is sufficient):
            current_pnl_usd >= BREAKEVEN_PROFIT_USD            ($15.00)
            current_pnl_usd >= 1.5 ATR expressed in USD PnL

        Guarded by `was_sl_modified` so the modification is issued at most once per
        ticket, and by the broker-state reconciliation in `refresh_protection_state`
        so a restart cannot duplicate it.

        Returns True only when the adapter CONFIRMED the modification.
        """
        state = self.get_protection_state(pos.ticket)

        if state.was_sl_modified or state.close_requested:
            return False

        # Retry cooldown (BUG-085/086): a broker-rejected or deferred breakeven
        # modification must not be re-attempted every management tick. The failure
        # storm on the live path produced 6,674 BREAKEVEN_FAILED audit rows from a
        # handful of tickets; the cooldown bounds retries to one per
        # BREAKEVEN_ATTEMPT_COOLDOWN_SEC while keeping the retry possible.
        now_mono = time.monotonic()
        if (now_mono - state.last_be_attempt_time) < BREAKEVEN_ATTEMPT_COOLDOWN_SEC:
            return False
        state.last_be_attempt_time = now_mono

        current_pnl_usd = float(pos.profit)
        atr_threshold_usd = self._atr_profit_threshold_usd(pos.volume, symbol_info, atr)
        # AGENT4-SPRINT: R-anchored trigger floor — the flat $15 threshold alone
        # fires at ~0.09R and locks an entry-level stop before the move develops.
        initial_risk_usd = self._initial_risks.get(pos.ticket, 0.0)
        r_trigger_usd = BREAKEVEN_TRIGGER_R * initial_risk_usd if initial_risk_usd > 0.0 else 0.0
        be_trigger_usd = max(BREAKEVEN_PROFIT_USD, r_trigger_usd)
        if current_pnl_usd < be_trigger_usd and current_pnl_usd < atr_threshold_usd:
            return False

        breakeven_sl = state.breakeven_sl_price or self.calculate_breakeven_sl(pos, symbol_info)
        state.breakeven_sl_price = breakeven_sl

        # Already at/beyond breakeven on the broker side: nothing to send.
        if self._is_sl_at_or_beyond(pos, pos.sl, breakeven_sl):
            state.was_sl_modified = True
            self._sl_modified_flags[pos.ticket] = True
            return False

        # Respect the broker's minimum stop distance PLUS the live spread so a
        # breakeven modification can never cross into the opposing book. The broker
        # STOP_LEVEL alone is insufficient: on a 2-digit XAUUSD symbol the stops
        # level can be ~0.10-0.35, smaller than the 0.20-0.25 live spread, so a
        # breakeven SL placed exactly at STOP_LEVEL distance would still be rejected
        # (or worse, crossed by the fill). Retry on a later pass instead of burning a
        # guaranteed-reject modification request.
        live_spread = (
            float(current_tick.ask - current_tick.bid) if current_tick is not None else 0.0
        )
        effective_freeze_gap = max(min_stop_gap, 0.35) + max(live_spread, 0.0)
        if current_tick is not None:
            is_buy = pos.type == OrderType.BUY
            current_market_price = current_tick.bid if is_buy else current_tick.ask

            # Verify SL sits on valid side of current market price to prevent MT5 10016 Retcode
            if is_buy and breakeven_sl >= (current_market_price - effective_freeze_gap):
                # Market pulled back before modification dispatched; defer or cap SL safely below market bid
                breakeven_sl = round(
                    current_market_price - effective_freeze_gap,
                    self._resolve_price_digits(symbol_info),
                )
                if breakeven_sl <= pos.price_open:
                    self._log_throttled_be_failure(
                        state,
                        pos,
                        f"BREAKEVEN DEFERRED: market pulled back (Bid: ${current_market_price:.2f}), SL would cross market price",
                        breakeven_sl,
                    )
                    return False

            elif not is_buy and breakeven_sl <= (current_market_price + effective_freeze_gap):
                # Market pulled back before modification dispatched; defer or cap SL safely above market ask
                breakeven_sl = round(
                    current_market_price + effective_freeze_gap,
                    self._resolve_price_digits(symbol_info),
                )
                if breakeven_sl >= pos.price_open:
                    self._log_throttled_be_failure(
                        state,
                        pos,
                        f"BREAKEVEN DEFERRED: market pulled back (Ask: ${current_market_price:.2f}), SL would cross market price",
                        breakeven_sl,
                    )
                    return False

        take_profit = pos.tp  # Existing take-profit is preserved verbatim.

        try:
            success = bool(
                self.mt5_adapter.modify_position(
                    ticket=pos.ticket,
                    stop_loss=breakeven_sl,
                    take_profit=take_profit,
                )
            )
        except Exception as err:
            success = False
            logger.error(
                "BREAKEVEN LOCK ERROR: modify_position raised",
                ticket=pos.ticket,
                error=str(err),
            )

        if not success:
            # Explicitly do NOT set was_sl_modified: the retry stays possible on the
            # next tracking cycle. Failure logging is throttled, the retry is not.
            self._log_throttled_be_failure(
                state,
                pos,
                "BREAKEVEN LOCK FAILED: broker rejected modification, retry pending",
                breakeven_sl,
            )
            return False

        # Only a CONFIRMED modification advances the tracked final SL. A failed
        # attempt must never pollute `_last_modify_sl` (BUG-085): doing so made the
        # autopsy record final_sl != initial_sl with was_sl_modified=False and could
        # suppress the retry via `_should_modify_sl` step comparison.
        self._last_modify_sl[pos.ticket] = breakeven_sl
        state.was_sl_modified = True
        self._sl_modified_flags[pos.ticket] = True

        logger.info(
            "BREAKEVEN LOCK ACTIVATED",
            ticket=f"#{pos.ticket}",
            pnl=f"${current_pnl_usd:.2f}",
            peak=f"${state.peak_win_usd:.2f}",
            entry=pos.price_open,
            sl=breakeven_sl,
        )
        self._log_protection_audit(
            pos,
            action="BREAKEVEN_LOCK",
            reason=f"BREAKEVEN_LOCK_ACTIVATED pnl=${current_pnl_usd:.2f} peak=${state.peak_win_usd:.2f}",
            stop_loss=breakeven_sl,
        )

        if self.notifier:
            try:
                contract_size = self._resolve_contract_size(symbol_info)
                self.notifier.notify_break_even_applied_extended(
                    ticket=pos.ticket,
                    new_sl=breakeven_sl,
                    original_risk_usd=self._initial_risks.get(pos.ticket, 0.0),
                    protected_amount_usd=abs(breakeven_sl - pos.price_open)
                    * pos.volume
                    * contract_size,
                    reply_to_message_id=self._order_message_ids.get(pos.ticket),
                )
            except Exception as err:
                logger.error("Breakeven notification failed", ticket=pos.ticket, error=str(err))

        return True

    def _maybe_tighten_protective_sl(
        self,
        pos: Position,
        state: "PositionProtectionState",
        symbol_info: SymbolInfo | None = None,
    ) -> bool:
        """
        TASK 3 helper: dynamically tightens an already-locked protective stop towards the
        current profit floor (never loosening it). Used in VOLATILITY_EXPANSION when a
        market close is suppressed so the position is still actively defended without
        crossing the spread. Returns True if a modification was issued and confirmed.
        """
        if not state.was_sl_modified:
            return False
        peak = state.peak_win_usd
        # Only meaningful once a meaningful peak profit exists.
        if peak <= 0.0 or pos.profit <= 0.0:
            return False

        contract_sz = self._resolve_contract_size(symbol_info)
        # Target = lock in a portion of current profit, but never below the breakeven level.
        target_profit_lock = pos.profit * 0.85
        if pos.type == OrderType.BUY:
            candidate_sl = pos.price_open + (
                target_profit_lock / max(pos.volume * contract_sz, 1.0)
            )
        else:
            candidate_sl = pos.price_open - (
                target_profit_lock / max(pos.volume * contract_sz, 1.0)
            )
        candidate_sl = round(candidate_sl, self._resolve_price_digits(symbol_info))

        if not self.is_sl_improvement(pos, candidate_sl):
            return False
        if not self._should_modify_sl(pos.ticket, candidate_sl):
            return False

        try:
            success = bool(
                self.mt5_adapter.modify_position(
                    ticket=pos.ticket,
                    stop_loss=candidate_sl,
                    take_profit=pos.tp,
                )
            )
        except Exception:
            return False
        if success:
            self._sl_modified_flags[pos.ticket] = True
            self._last_modify_sl[pos.ticket] = candidate_sl
            logger.info(
                "PROFIT GIVEBACK: dynamic SL tighten in VOLATILITY_EXPANSION",
                ticket=f"#{pos.ticket}",
                new_sl=candidate_sl,
                old_sl=pos.sl,
            )
        return success

    def _resolve_contract_size(self, symbol_info: SymbolInfo | None) -> float:
        """Contract size with the project-wide 100.0 (gold) fallback."""
        if symbol_info is not None and symbol_info.trade_contract_size > 0:
            return float(symbol_info.trade_contract_size)
        return 100.0

    def _log_throttled_be_failure(
        self,
        state: PositionProtectionState,
        pos: Position,
        message: str,
        breakeven_sl: float,
    ) -> None:
        """
        Emits a breakeven-failure warning at most once every
        `TELEMETRY_CONSOLE_INTERVAL_SEC` per ticket so a persistent broker rejection
        cannot flood the console. The audit record is written every time.
        """
        now = time.monotonic()
        if (now - state.last_be_failure_log_time) >= TELEMETRY_CONSOLE_INTERVAL_SEC:
            logger.warning(
                message,
                ticket=pos.ticket,
                breakeven_sl=breakeven_sl,
                actual_sl=pos.sl,
                pnl=f"${pos.profit:+.2f}",
            )
            state.last_be_failure_log_time = now

        self._log_protection_audit(
            pos,
            action="BREAKEVEN_FAILED",
            reason=message,
            stop_loss=breakeven_sl,
        )

    def _tiered_giveback_floor(self, ticket: int, peak: float) -> tuple[float, bool]:
        """
        Returns (retention_floor, armed) for a peak profit.

        The floor is derived from the PEAK expressed in R (peak USD / initial risk
        USD). Tiers let small scalps tolerate normal noise while locking in a
        meaningful share of larger runners. `armed=False` means the giveback
        protection stays DISARMED (micro-profit noise zone).
        """
        risk_usd = self._initial_risks.get(ticket, 0.0)
        if risk_usd <= 0.0 or peak <= 0.0:
            # Without a known planned risk we fall back to the absolute floor so
            # protection is never silently disabled.
            return PROFIT_GIVEBACK_MIN_RETENTION, True
        peak_r = peak / risk_usd
        if peak_r < TIERED_GIVEBACK_ARM_R:
            return PROFIT_GIVEBACK_MIN_RETENTION, False
        floor = PROFIT_GIVEBACK_MIN_RETENTION
        for tier_r, tier_floor in TIERED_GIVEBACK_RETENTION_FLOOR:
            if peak_r >= tier_r:
                floor = tier_floor
            else:
                break
        return floor, True

    def evaluate_profit_giveback(
        self,
        ticket: int,
        current_pnl_usd: float,
        base_hold_score: int,
    ) -> tuple[int, bool, str]:
        """
        Deterministic profit-erosion evaluation and hold-score safety override.

        Runs AFTER the base score has been computed but BEFORE the score is used for
        any execution decision, so normal scoring can never overwrite a safety verdict.

        Returns (final_hold_score, protection_required, reason).
        """
        state = self.get_protection_state(ticket)
        score = int(base_hold_score)
        peak = state.peak_win_usd

        if peak < PROFIT_GIVEBACK_PEAK_USD:
            return max(0, min(100, score)), False, ""

        retention_floor, armed = self._tiered_giveback_floor(ticket, peak)
        if not armed:
            return max(0, min(100, score)), False, ""

        retention = state.retention_ratio(current_pnl_usd)

        # --- Priority 3: negative PnL after a meaningful profit -----------------
        # Evaluated before anything can raise the score again: a trade that banked
        # >= $20 and is now red must never look attractive to hold.
        if current_pnl_usd < 0.0:
            return (
                NEGATIVE_AFTER_PROFIT_HOLD_SCORE,
                True,
                f"NEGATIVE_PNL_AFTER_PEAK peak=${peak:.2f} current=${current_pnl_usd:.2f}",
            )

        # --- Priority 2: tiered profit retention floor breached -----------------
        if retention < retention_floor:
            score -= PROFIT_GIVEBACK_HOLD_SCORE_PENALTY
            score = max(0, min(100, score))
            return (
                score,
                True,
                f"PROFIT_RETENTION_BREACH peak=${peak:.2f} current=${current_pnl_usd:.2f} "
                f"retention={retention:.2%} floor={retention_floor:.2%}",
            )

        return max(0, min(100, score)), False, ""

    def enforce_profit_giveback_protection(
        self,
        pos: Position,
        hold_score: int,
        symbol_info: SymbolInfo | None = None,
        regime: str | None = None,
    ) -> tuple[int, bool]:
        """
        Priority-2/3 protection: arms PROFIT_GIVEBACK_PROTECTION and submits exactly one
        market close for a winner that has eroded past the retention floor or turned
        negative after banking >= PROFIT_GIVEBACK_PEAK_USD.

        Returns (effective_hold_score, protection_active). When protection_active is
        True the caller MUST NOT let any lower-priority mechanism act on the ticket.

        TASK 3 HARDENING: when the close is being triggered inside a high-spread
        VOLATILITY_EXPANSION regime AND a breakeven (or better) stop is ALREADY locked
        on the broker terminal, we must NOT fire a live market close that crosses the
        spread (which would destroy the protected profit). Instead we trust the locked
        SL to do the job and, if possible, tighten it dynamically via modify_position.
        A market close is only permitted if price has crossed below the breakeven SL or
        the SL modification itself fails.
        """
        state = self.get_protection_state(pos.ticket)
        current_pnl_usd = float(pos.profit)

        final_score, protection_required, reason = self.evaluate_profit_giveback(
            ticket=pos.ticket,
            current_pnl_usd=current_pnl_usd,
            base_hold_score=hold_score,
        )

        if not protection_required:
            return final_score, False

        retention = state.retention_ratio(current_pnl_usd)
        state.profit_giveback_triggered = True

        # --- TASK 3: Breakeven-aware exit suppression during VOLATILITY_EXPANSION ---
        is_vol_expansion = regime == "VOLATILITY_EXPANSION"
        breakeven_locked = state.was_sl_modified and self._is_sl_at_or_beyond(
            pos, pos.sl, state.breakeven_sl_price
        )
        if is_vol_expansion and breakeven_locked and not state.close_requested:
            # Reference for the "price crossed below breakeven" check.
            ref = getattr(self, "_last_tick_for_ticket", {}).get(pos.ticket)
            # Determine whether price has already breached the locked protective stop.
            price_below_be = False
            if pos.type == OrderType.BUY:
                price_below_be = ref is not None and getattr(ref, "bid", 1e18) <= pos.sl
            else:
                price_below_be = ref is not None and getattr(ref, "ask", 0.0) >= pos.sl

            if not price_below_be:
                # Do NOT cross the spread with a market close. Keep the locked SL and
                # attempt a dynamic tighten (trailing) via native MT5 modification.
                logger.info(
                    "PROFIT GIVEBACK: breakeven SL already locked in VOLATILITY_EXPANSION; "
                    "suppressing market close, relying on protective SL",
                    ticket=f"#{pos.ticket}",
                    peak=f"${state.peak_win_usd:.2f}",
                    current=f"${current_pnl_usd:.2f}",
                    retention=f"{retention:.2%}",
                    locked_sl=pos.sl,
                )
                logger.info(
                    "[POSITION_EXIT_BLOCKED]",
                    ticket=pos.ticket,
                    intended_action="CLOSE",
                    blocker="VOLATILITY_EXPANSION_BREAKEVEN_SUPPRESSION",
                    reason=(
                        "breakeven SL locked on broker; market close would cross the "
                        "spread and destroy protected profit"
                    ),
                    pnl=round(float(current_pnl_usd), 2),
                    locked_sl=pos.sl,
                )
                # Try to tighten the SL to the current retention floor (still >= breakeven).
                tightened = self._maybe_tighten_protective_sl(pos, state, symbol_info)
                if not tightened:
                    logger.debug(
                        "PROFIT GIVEBACK: SL tighten skipped (already optimal or broker rejected)",
                        ticket=pos.ticket,
                    )
                # Protection is considered active (lower-priority mechanisms must not act),
                # but no market close is dispatched.
                return max(0, min(100, final_score)), True
        # when the previous request was reported as failed (close_requested stays
        # False in that case).
        if state.close_requested:
            logger.debug(
                "PROFIT GIVEBACK PROTECTION: close already requested, suppressing duplicate",
                ticket=pos.ticket,
            )
            return final_score, True

        logger.warning(
            "[EXIT TRACE] PROFIT GIVEBACK PROTECTION TRIGGERED",
            ticket=f"#{pos.ticket}",
            peak=f"${state.peak_win_usd:.2f}",
            current=f"${current_pnl_usd:.2f}",
            retention=f"{retention:.2%}",
            hold_score=final_score,
            reason=reason,
            exit_mechanism=ExitMechanism.PROFIT_GIVEBACK_PROTECTION,
        )
        self._log_protection_audit(
            pos,
            action="PROFIT_GIVEBACK_PROTECTION",
            reason=f"{reason} hold_score={final_score} exit_mechanism={ExitMechanism.PROFIT_GIVEBACK_PROTECTION}",
            stop_loss=pos.sl,
        )

        # Propagate the exit metadata through the EXISTING forced-exit mechanism so the
        # ledger autopsy attributes the close correctly. No parallel interface is added.
        self._forced_exit_mechanisms[pos.ticket] = ExitMechanism.PROFIT_GIVEBACK_PROTECTION

        try:
            closed = bool(self.adapter.close_position(ticket=pos.ticket))
        except Exception as err:
            closed = False
            logger.error(
                "PROFIT GIVEBACK PROTECTION: close_position raised",
                ticket=pos.ticket,
                error=str(err),
            )

        if closed:
            state.close_requested = True
            self._hold_score_tracker[pos.ticket] = final_score
            with self._live_tickets_lock:
                self._live_tickets_cache.pop(pos.ticket, None)
            if self.notifier:
                try:
                    self.notifier.notify_early_emergency_cut(
                        ticket=pos.ticket,
                        score=final_score,
                        reasons=f"{ExitMechanism.PROFIT_GIVEBACK_PROTECTION}: {reason}",
                        saved_usd=current_pnl_usd,
                        reply_to_message_id=self._order_message_ids.get(pos.ticket),
                    )
                except Exception as err:
                    logger.error(
                        "Profit giveback notification failed", ticket=pos.ticket, error=str(err)
                    )
        else:
            # Close failed: clear the forced tag (so an organic exit is not mislabelled)
            # and leave close_requested False so the next cycle retries.
            self._forced_exit_mechanisms.pop(pos.ticket, None)
            self._log_protection_audit(
                pos,
                action="PROFIT_GIVEBACK_CLOSE_FAILED",
                reason=f"{reason} close_position returned falsy, retry pending",
                stop_loss=pos.sl,
            )

        return final_score, True

    def apply_atr_trailing_stop(
        self,
        pos: Position,
        price_current: float,
        atr: float,
        symbol_info: SymbolInfo | None = None,
        min_stop_gap: float = 0.0,
        current_tick: TickData | None = None,
    ) -> bool:
        """
        Priority-5 protection: ATR trailing stop built on the ATR already produced by
        the feature pipeline (`FeatureVector.atr_m1`); no second ATR implementation is
        introduced.

        BUY : trailing_sl = price - ATR * ATR_TRAILING_MULTIPLIER
        SELL: trailing_sl = price + ATR * ATR_TRAILING_MULTIPLIER

        The stop is only ever tightened: `is_sl_improvement` rejects any candidate that
        would loosen the broker SL or regress behind a confirmed breakeven lock.
        """
        state = self.get_protection_state(pos.ticket)
        if state.close_requested or state.profit_giveback_triggered:
            # A higher-priority protection decision is in force; trailing must not
            # replace or cancel it.
            return False

        try:
            distance = max(float(min_stop_gap), round(float(atr) * ATR_TRAILING_MULTIPLIER, 2))
        except (TypeError, ValueError):
            logger.error("ATR trailing skipped: invalid ATR input", ticket=pos.ticket, atr=atr)
            return False

        if distance <= 0.0:
            return False

        target_sl = (
            price_current - distance if pos.type == OrderType.BUY else price_current + distance
        )
        target_sl = round(target_sl, self._resolve_price_digits(symbol_info))

        if not self.is_sl_improvement(pos, target_sl):
            return False

        if current_tick is not None and min_stop_gap > 0.0:
            reference = current_tick.bid if pos.type == OrderType.BUY else current_tick.ask
            gap = (reference - target_sl) if pos.type == OrderType.BUY else (target_sl - reference)
            if gap < min_stop_gap:
                return False

        if not self._should_modify_sl(pos.ticket, target_sl):
            return False

        old_sl = pos.sl
        try:
            success = bool(
                self.adapter.modify_position(
                    ticket=pos.ticket, stop_loss=target_sl, take_profit=pos.tp
                )
            )
        except Exception as err:
            success = False
            logger.error("ATR TRAILING: modify_position raised", ticket=pos.ticket, error=str(err))

        if not success:
            return False

        # Only a CONFIRMED modification advances the tracked final SL (BUG-085).
        self._last_modify_sl[pos.ticket] = target_sl
        self._sl_modified_flags[pos.ticket] = True
        self._log_protection_audit(
            pos,
            action="ATR_TRAILING_STOP",
            reason=f"ATR_TRAILING atr={atr:.5f} multiplier={ATR_TRAILING_MULTIPLIER}",
            stop_loss=target_sl,
        )

        if self.notifier:
            try:
                self.notifier.notify_trailing_stop_advanced_extended(
                    ticket=pos.ticket,
                    old_sl=old_sl,
                    new_sl=target_sl,
                    current_price=price_current,
                    reply_to_message_id=self._order_message_ids.get(pos.ticket),
                )
            except Exception as err:
                logger.error("Trailing notification failed", ticket=pos.ticket, error=str(err))

        return True

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
            try:
                regime = getattr(regime_state, "regime_type", None)
                if regime is not None:
                    return str(getattr(regime, "value", regime))
                return str(regime_state)
            except Exception:
                pass
        return self._entry_regimes.get(ticket, "")

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
    def _last_tick_for_ticket(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._last_tick_for_ticket

    @property
    def _last_tick_timestamps(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._last_tick_timestamps

    @property
    def _time_in_profit_sec(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._time_in_profit_sec

    @property
    def _time_in_drawdown_sec(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._time_in_drawdown_sec

    @property
    def _peak_profit_usd(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._peak_profit_usd

    @property
    def _peak_drawdown_usd(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._peak_drawdown_usd

    @property
    def _lsf_state(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._lsf_state

    @property
    def _last_seen_ts(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._last_seen_ts

    @property
    def _stagnation_ticks(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._stagnation_ticks

    @property
    def _adverse_ticks(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._adverse_ticks

    @property
    def _favorable_ticks(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._favorable_ticks

    @property
    def _last_price_tracker(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._last_price_tracker

    @property
    def _mfe_tracker(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._mfe_tracker

    @property
    def _mae_tracker(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._mae_tracker

    @property
    def _time_to_mfe_sec(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._time_to_mfe_sec

    @property
    def _time_to_mae_sec(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._time_to_mae_sec

    @property
    def _reversal_events(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._reversal_events

    @property
    def _entry_probs(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._entry_probs

    @property
    def _entry_regime_state(self) -> dict:
        """Compatibility accessor — live tracking dict owned by the ledger."""
        return self._tracking._entry_regime_state

    @property
    def _hold_score_tracker(self) -> dict:
        """Compatibility accessor — live hold-score dict owned by the ledger."""
        return self._hold_scores._hold_score_tracker

    @property
    def _base_hold_score_tracker(self) -> dict:
        """Compatibility accessor — live hold-score dict owned by the ledger."""
        return self._hold_scores._base_hold_score_tracker

    @property
    def _last_reasons_tracker(self) -> dict:
        """Compatibility accessor — live hold-score dict owned by the ledger."""
        return self._hold_scores._last_reasons_tracker

    @property
    def _last_hold_eval_time(self) -> dict:
        """Compatibility accessor — live hold-score dict owned by the ledger."""
        return self._hold_scores._last_hold_eval_time

    @property
    def _last_telemetry_time(self) -> dict:
        """Compatibility accessor — live throttle dict owned by TelemetryThrottle."""
        return self._telemetry._last_telemetry_time

    @property
    def _live_tickets_cache(self) -> dict:
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

    def _calculate_hold_value_score(
        self,
        pos: Position,
        price_current: float,
        features: FeatureVector | None,
        impact_price_delta: float,
        atr: float,
        smart_metrics: dict[str, Any] | None = None,
    ) -> tuple[int, list[str]]:
        """
        Calculates position Hold Value Score (0 to 100) dynamically based on real-time metrics:
        Base Score = 100
        Penalty 1 (Drawdown vs Initial Risk/ATR): Subtract up to 40 points as current loss approaches SL.
        Penalty 2 (Time-in-Loss Decay): Subtract up to 30 points if time_loss > 70% of holding duration.
        Penalty 3 (Real-time Spread Expansion): Subtract points if current broker spread exceeds 1.5x rolling average spread.
        Bonus: Add up to 10 points if the PyTorch AI probability still strongly favors the position direction.
        """
        score = 100
        reasons: list[str] = []
        ticket = pos.ticket

        # --- Penalty 1: Drawdown vs Initial Risk/ATR (convex, up to -80) ---
        # Non-linear: as drawdown deepens relative to the planned risk, the penalty
        # accelerates so the engine de-risks gracefully LONG before the emergency
        # horizon. A linear ratio*40 leaves a 50%-of-risk drawdown at score ~80,
        # which keeps the position in the "hold" band until a hard bailout.
        initial_sl = self._entry_sls.get(ticket, pos.sl)
        is_buy = pos.type == OrderType.BUY
        current_loss = 0.0
        initial_risk = 0.0

        if is_buy:
            current_loss = max(0.0, pos.price_open - price_current)
            initial_risk = pos.price_open - initial_sl if initial_sl > 0.0 else (atr * 1.5)
        else:
            current_loss = max(0.0, price_current - pos.price_open)
            initial_risk = initial_sl - pos.price_open if initial_sl > 0.0 else (atr * 1.5)

        if current_loss > 0.0:
            ratio = current_loss / max(0.01, initial_risk)
            # Convex curve: ratio=0.2 -> ~9, ratio=0.5 -> ~36, ratio=0.8 -> ~72,
            # ratio=1.0 -> 80. Score drops decisively below the <50 de-risk band
            # around 40-50% of planned risk, well before a hard stop is needed.
            penalty1 = int(min(80.0, 80.0 * (min(ratio, 1.0) ** 1.5)))
            if penalty1 > 0:
                score -= penalty1
                reasons.append(f"DRAWDOWN_PENALTY (-{penalty1}, ratio={ratio:.2f})")

        # --- Penalty 2: Time-in-Loss Decay (up to -30) ---
        entry_time = self._entry_timestamps.get(ticket)
        if entry_time:
            if entry_time.tzinfo is None:
                holding_duration = (datetime.now() - entry_time).total_seconds()
            else:
                holding_duration = (datetime.now(UTC) - entry_time).total_seconds()
        else:
            holding_duration = 1.0
        time_loss = self._time_in_drawdown_sec.get(ticket, 0.0)

        if holding_duration > 0.0 and (time_loss / holding_duration) > 0.70:
            score -= 30
            reasons.append("TIME_IN_LOSS_DECAY_PENALTY (-30)")

        # --- Penalty 3: Real-time Spread Expansion (up to -20) ---
        if self._rolling_spreads:
            current_spread = self._rolling_spreads[-1]
            avg_spread = sum(self._rolling_spreads) / len(self._rolling_spreads)
            if avg_spread > 0.0 and current_spread > 1.5 * avg_spread:
                score -= 20
                reasons.append("SPREAD_EXPANSION_PENALTY (-20)")

        # --- Bonus: AI/Trend alignment (+10), suppressed while meaningfully underwater ---
        # A positive trend signal must never mask a deep drawdown: bonuses are only
        # worth considering when the position is not materially adverse.
        drawdown_ratio = current_loss / max(0.01, initial_risk) if current_loss > 0.0 else 0.0
        underwater = drawdown_ratio >= 0.30
        if features is not None:
            aligned = False
            if is_buy and features.is_above_kumo:
                aligned = True
            elif not is_buy and features.is_below_kumo:
                aligned = True

            if aligned and not underwater:
                score += 10
                reasons.append("TREND_ALIGNMENT_BONUS (+10)")
            elif aligned and underwater:
                reasons.append("TREND_BONUS_SUPPRESSED_UNDERWATER")

        # PROFIT SHIELD GUARD: Winning trades get guaranteed high floor score of 85.
        # The guard is based on ACTUAL floating PnL, not price-vs-open (which can be
        # fooled by spread/whipsaw), and it is disabled once the position is under
        # water by more than 30% of planned risk so a real loss can never be masked.
        is_in_profit = pos.profit >= 0.0
        if is_in_profit and not underwater:
            score = max(85, score)
            reasons.append("PROFIT_SHIELD_SCORE_FLOOR_ACTIVE")

        return max(0, min(100, score)), reasons

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

    @staticmethod
    def _pending_field(pending: Any, *names: str, default: Any = None) -> Any:
        """
        Reads a field from a pending order that may be either a dict (as returned by the
        live MT5 adapter via `orders_get`) or an object with attributes (as used by
        simulated/paper adapters).

        Without this, dict-shaped pending orders silently resolve every field to the
        default, which previously made the pending-order guard a no-op in production.
        """
        for name in names:
            if isinstance(pending, dict):
                if name in pending and pending[name] is not None:
                    return pending[name]
            else:
                value = getattr(pending, name, None)
                if value is not None:
                    return value
        return default

    def _add_trajectory_step(
        self,
        ticket: int,
        timestamp: datetime,
        pnl: float,
        price: float,
        hold_score: int,
        drawdown: float,
        retention: float,
        atr: float,
        volatility: float,
    ) -> None:
        """Appends a new observation step to the ticket's bounded trajectory history."""
        if ticket not in self._trajectory_history:
            self._trajectory_history[ticket] = deque(maxlen=100)

        step = PositionEvaluationStep(
            timestamp=timestamp,
            pnl=float(pnl),
            price=float(price),
            hold_score=int(hold_score),
            drawdown=float(drawdown),
            retention=float(retention),
            atr=float(atr),
            volatility=float(volatility),
        )
        self._trajectory_history[ticket].append(step)

    def _calculate_continuous_giveback_severity(self, ticket: int, current_pnl_usd: float) -> float:
        """
        Calculates a continuous giveback severity metric (0.0 to 1.0).
        0.0 means no giveback (at peak).
        1.0 means catastrophic giveback (at or below the minimum retention floor).
        """
        state = self.get_protection_state(ticket)
        peak = state.peak_win_usd
        if peak < PROFIT_GIVEBACK_PEAK_USD:
            return 0.0

        catastrophic_floor = peak * PROFIT_GIVEBACK_MIN_RETENTION
        giveback_range = peak - catastrophic_floor
        if giveback_range <= 0.0:
            return 0.0

        severity = (peak - current_pnl_usd) / giveback_range
        return max(0.0, min(1.0, severity))

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

    def _evaluate_minimum_loss_optimization(
        self,
        ticket: int,
        current_pnl_usd: float,
        initial_risk_usd: float,
        evidence: dict[str, float],
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """
        Continuously calculates the expected value of holding vs exiting.
        Returns (should_exit, reason) for exiting at the smallest statistically justified loss.
        """
        if current_pnl_usd >= 0.0:
            return False, ""

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
                now_ref = datetime.now(UTC) if entry_time.tzinfo else datetime.now()
                duration_sec = (now_ref - entry_time).total_seconds()
        else:
            duration_sec = 0.0

        # 60-Second Spread Overcome Grace Period (Prevent instant exit due to spread costs at open)
        if duration_sec < 60.0:
            return False, ""

        recovery_score = evidence.get("recovery_score", 0.50)
        adverse_score = evidence.get("adverse_score", 0.50)

        # Expected Outcomes (payoff magnitudes)
        # Phase 15 audit finding #4 fix (BUG-056): the recovery value MUST be
        # anchored to the PLANNED reward objective (initial risk x minimum
        # risk-reward ratio), never to the CURRENT loss magnitude. The old
        #   expected_recovery_value = max(15.0, abs(current_pnl_usd) * 2.0)
        # grew with the loss while expected_additional_loss = max(1.0, risk -
        # |pnl|) shrank, so EV became MORE positive the deeper the drawdown
        # (verified: EV +55.86 vs threshold -29.53 at pnl -171.12, risk 196.88,
        # rec 0.204, adv 0.542) — the minimum-loss exit could never fire.
        # The payoff is fixed at entry time (reward = initial_risk * RRR) and
        # the additional loss is the REAL remaining distance to the hard SL
        # (initial_risk - |pnl|), which is the honest downside still at stake.
        # A deep-drawdown guard below (drawdown consumed > 60% of risk with
        # weak recovery) catches the case where EV alone stays positive because
        # the remaining SL distance is small — the statistics say exit.
        planned_rr = float(getattr(self.algo_config, "min_risk_reward_ratio", 1.8) or 1.8)
        expected_recovery_value = max(15.0, initial_risk_usd * planned_rr)
        expected_additional_loss = max(1.0, initial_risk_usd - abs(current_pnl_usd))

        # Expected Value (EV) calculation
        ev_hold = (
            recovery_score * expected_recovery_value - adverse_score * expected_additional_loss
        )

        # Minimum-loss exit condition: if the EV of holding is severely negative, or if recovery evidence is weak
        if ev_hold < -0.15 * initial_risk_usd:
            logger.info(
                f"[EXIT TRACE] MIN_LOSS_OPTIMIZATION_EV_BREACH triggered. Ticket: {ticket}, EV: ${ev_hold:.2f}, RecProb: {recovery_score:.2%}, AdvProb: {adverse_score:.2%}, Duration: {duration_sec:.1f}s"
            )
            return (
                True,
                f"MIN_LOSS_OPTIMIZATION_EV_BREACH (EV=${ev_hold:.2f}, rec_prob={recovery_score:.2%}, adv_prob={adverse_score:.2%})",
            )

        # Deep-drawdown guard (BUG-056): when the position has consumed most of
        # its planned risk (>60%) and the model sees weak recovery (<30%), the
        # remaining SL distance is small so EV alone can look positive; the
        # statistics say exit before the hard stop is fully consumed.
        drawdown_fraction = abs(current_pnl_usd) / max(initial_risk_usd, 1.0)
        if drawdown_fraction > 0.60 and recovery_score < 0.30:
            logger.info(
                f"[EXIT TRACE] MIN_LOSS_OPTIMIZATION_DEEP_DRAWDOWN triggered. Ticket: {ticket}, "
                f"RecProb: {recovery_score:.2%}, Drawdown: {drawdown_fraction:.1%} of risk, Duration: {duration_sec:.1f}s"
            )
            return (
                True,
                f"MIN_LOSS_OPTIMIZATION_DEEP_DRAWDOWN (rec_prob={recovery_score:.2%}, drawdown={drawdown_fraction:.1%})",
            )

        if recovery_score < 0.25 and adverse_score > 0.60:
            logger.info(
                f"[EXIT TRACE] MIN_LOSS_OPTIMIZATION_WEAK_RECOVERY triggered. Ticket: {ticket}, RecProb: {recovery_score:.2%}, AdvProb: {adverse_score:.2%}, Duration: {duration_sec:.1f}s"
            )
            return (
                True,
                f"MIN_LOSS_OPTIMIZATION_WEAK_RECOVERY (rec_prob={recovery_score:.2%}, adv_prob={adverse_score:.2%})",
            )

        return False, ""

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
                now_ref = datetime.now(UTC) if entry_time.tzinfo else datetime.now()
                duration_sec = (now_ref - entry_time).total_seconds()
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

    def _calculate_protection_score(
        self,
        ticket: int,
        pos: Position,
        base_hold_score: int,
        pnl_features: dict[str, float],
        evidence: dict[str, float],
        confidence_factor: float,
        atr: float,
    ) -> float:
        """
        Calculates a continuous protection score (0.0 to 100.0) combining baseline state weights
        with continuous risk severity, including protection escalation as risk deteriorates.
        """
        # Retrieve centralized weights from AlgoConfig
        w_prof = getattr(self.algo_config, "w_profit_retention", 0.30)
        w_pnl = getattr(self.algo_config, "w_pnl_trajectory", 0.15)
        w_dd_vel = getattr(self.algo_config, "w_drawdown_velocity", 0.15)
        w_rev = getattr(self.algo_config, "w_market_reversal", 0.20)
        w_rec = getattr(self.algo_config, "w_recovery_probability", 0.10)
        w_hscore = getattr(self.algo_config, "w_hold_score", 0.10)

        # Scale weights continuously based on position state (context-dependent weights)
        is_profitable = pos.profit >= 0.0
        if is_profitable:
            # Shift weight toward profit retention and continuation
            w_prof *= 1.5
            w_rec *= 0.2
        else:
            # Shift weight toward drawdown velocity, recovery, and market reversal
            w_dd_vel *= 1.5
            w_rev *= 1.3
            w_rec *= 1.2
            w_prof *= 0.1

        # Normalize weights
        total_w = w_prof + w_pnl + w_dd_vel + w_rev + w_rec + w_hscore
        if total_w > 0.0:
            w_prof /= total_w
            w_pnl /= total_w
            w_dd_vel /= total_w
            w_rev /= total_w
            w_rec /= total_w
            w_hscore /= total_w

        # Scaled continuous input variables [0.0, 1.0]
        profit_giveback_severity = self._calculate_continuous_giveback_severity(ticket, pos.profit)

        # PnL deterioration: 1.0 when PnL slope is highly negative
        pnl_slope = pnl_features.get("pnl_slope", 0.0)
        pnl_deterioration = max(0.0, min(1.0, -pnl_slope * 2.0))

        # Drawdown velocity (scaled)
        dd_vel = pnl_features.get("drawdown_velocity", 0.0)
        drawdown_velocity = max(0.0, min(1.0, dd_vel * 3.0))

        # Reversal probability (scaled by AI confidence factor continuously)
        effective_ai_weight = confidence_factor
        adverse_prob = evidence.get("adverse_score", 0.0)
        reversal_probability = adverse_prob * effective_ai_weight

        # Recovery probability
        rec_prob = evidence.get("recovery_score", 0.0)
        # We weigh (1 - recovery_probability) as protection pressure
        recovery_probability_pressure = (1.0 - rec_prob) * effective_ai_weight

        # Hold score deterioration
        hold_score_deterioration = max(0.0, min(1.0, (100.0 - base_hold_score) / 100.0))

        # Time risk: increases as time underwater grows
        time_below_be = pnl_features.get("time_below_breakeven", 0.0)
        time_risk = max(0.0, min(1.0, time_below_be / self.max_holding_seconds))

        # Combine variables
        protection_score = (
            w_prof * profit_giveback_severity
            + w_pnl * pnl_deterioration
            + w_dd_vel * drawdown_velocity
            + w_rev * reversal_probability
            + w_rec * recovery_probability_pressure
            + w_hscore * hold_score_deterioration
        )

        # Apply escalation multiplier as risk deteriorates (near hard SL or high time risk)
        escalation_factor = 1.0
        if not is_profitable:
            # Escalation based on time underwater and negative trend slope
            escalation_factor += 0.5 * time_risk
            if pnl_slope < 0.0:
                escalation_factor += 0.3 * min(1.0, abs(pnl_slope))

        protection_score *= escalation_factor
        return max(0.0, min(100.0, protection_score * 100.0))

    def _calculate_adaptive_evidence_scores(
        self,
        ticket: int,
        pos: Position,
        probs: Any | None,
        features: FeatureVector | None,
    ) -> dict[str, float]:
        """
        Computes normalized evidence scores (recovery_score, adverse_score, continuation_score)
        derived from either live neural network predictions (probs) or a bounded evidence/score model fallback.
        """
        is_buy = pos.type == OrderType.BUY
        pnl_features = self._calculate_trajectory_features(ticket)
        pnl_slope = pnl_features.get("pnl_slope", 0.0)

        # 1. Base model predictions if available
        if probs is not None:
            try:
                probs_list = probs.squeeze().tolist()
                if not isinstance(probs_list, list):
                    probs_list = [probs_list]

                # Model predicts: 0=NO_TRADE, 1=BUY, 2=SELL
                p_no_trade = float(probs_list[0]) if len(probs_list) > 0 else 0.4
                p_buy = float(probs_list[1]) if len(probs_list) > 1 else 0.3
                p_sell = float(probs_list[2]) if len(probs_list) > 2 else 0.3

                # Ensure internally consistent normalization
                total_prob = p_no_trade + p_buy + p_sell + 1e-9
                p_no_trade /= total_prob
                p_buy /= total_prob
                p_sell /= total_prob

                if is_buy:
                    continuation_score = p_buy
                    adverse_score = p_sell
                else:
                    continuation_score = p_sell
                    adverse_score = p_buy

            except Exception as err:
                logger.error(
                    "Error parsing neural network probabilities; falling back to heuristic",
                    error=str(err),
                )
                probs = None

        if probs is None:
            # Bounded evidence fallback model (Requirement 1 & 2)
            # Baseline is 0.40
            continuation_score = 0.40
            adverse_score = 0.40

            # Dynamic indicators from feature vector
            if features is not None:
                # Ichimoku trend alignment
                if is_buy and features.is_above_kumo:
                    continuation_score += 0.15
                elif is_buy and features.is_below_kumo:
                    adverse_score += 0.15
                elif not is_buy and features.is_below_kumo:
                    continuation_score += 0.15
                elif not is_buy and features.is_above_kumo:
                    adverse_score += 0.15

                # Choch alignment
                choch_bull = getattr(features, "choch_bullish", False)
                choch_bear = getattr(features, "choch_bearish", False)
                if is_buy and choch_bull:
                    continuation_score += 0.10
                elif is_buy and choch_bear:
                    adverse_score += 0.10
                elif not is_buy and choch_bear:
                    continuation_score += 0.10
                elif not is_buy and choch_bull:
                    adverse_score += 0.10

            # Slope adjustments
            if pnl_slope > 0.0:
                continuation_score += 0.10
                adverse_score -= 0.05
            elif pnl_slope < 0.0:
                adverse_score += 0.10
                continuation_score -= 0.05

            # Strictly normalize
            total = continuation_score + adverse_score + 0.20  # 0.20 represents 'no_trade'
            continuation_score /= total
            adverse_score /= total

        # Compute recovery score
        # Mixture of continuation score and actual recovery trajectory velocity
        rec_vel = pnl_features.get("recovery_velocity", 0.0)
        # Scaled recovery velocity (USD/sec)
        rec_vel_scaled = min(1.0, max(0.0, rec_vel * 5.0))
        recovery_score = 0.70 * continuation_score + 0.30 * rec_vel_scaled

        return {
            "continuation_score": max(0.0, min(1.0, continuation_score)),
            "adverse_score": max(0.0, min(1.0, adverse_score)),
            "recovery_score": max(0.0, min(1.0, recovery_score)),
        }

    def _calculate_trajectory_features(self, ticket: int) -> dict[str, float]:
        """
        Calculates time-aware trajectory features (slopes, velocities, acceleration)
        using an efficient window of the last 10 steps to avoid expensive linear regression.
        """
        history = self._trajectory_history.get(ticket)
        if not history or len(history) < 2:
            return {
                "pnl_slope": 0.0,
                "price_slope": 0.0,
                "drawdown_velocity": 0.0,
                "drawdown_acceleration": 0.0,
                "recovery_velocity": 0.0,
                "time_since_peak": 0.0,
                "time_below_entry": 0.0,
                "time_below_breakeven": 0.0,
                "distance_to_be_velocity": 0.0,
            }

        window = list(history)[-10:]
        first = window[0]
        last = window[-1]
        dt = (last.timestamp - first.timestamp).total_seconds()
        if dt <= 0.0:
            dt = 0.1

        pnl_slope = (last.pnl - first.pnl) / dt
        price_slope = (last.price - first.price) / dt

        prev = window[-2]
        dt_last = (last.timestamp - prev.timestamp).total_seconds()
        if dt_last <= 0.0:
            dt_last = 0.1

        last_dd_vel = (last.drawdown - prev.drawdown) / dt_last

        if len(window) >= 3:
            prev_prev = window[-3]
            dt_prev = (prev.timestamp - prev_prev.timestamp).total_seconds()
            if dt_prev <= 0.0:
                dt_prev = 0.1
            prev_dd_vel = (prev.drawdown - prev_prev.drawdown) / dt_prev
            drawdown_acceleration = (last_dd_vel - prev_dd_vel) / dt_last
        else:
            drawdown_acceleration = 0.0

        drawdown_velocity = last_dd_vel
        recovery_velocity = pnl_slope if pnl_slope > 0.0 else 0.0

        peak_step = max(history, key=lambda s: s.pnl)
        time_since_peak = (last.timestamp - peak_step.timestamp).total_seconds()

        time_below_entry = 0.0
        time_below_breakeven = 0.0
        for i in range(1, len(history)):
            s_prev = history[i - 1]
            s_curr = history[i]
            s_dt = (s_curr.timestamp - s_prev.timestamp).total_seconds()
            if s_curr.pnl < 0.0:
                time_below_entry += s_dt
            if s_curr.pnl < 0.20:
                time_below_breakeven += s_dt

        if last.pnl < 0.0 and prev.pnl < 0.0:
            distance_to_be_velocity = (abs(last.pnl) - abs(prev.pnl)) / dt_last
        else:
            distance_to_be_velocity = 0.0

        return {
            "pnl_slope": pnl_slope,
            "price_slope": price_slope,
            "drawdown_velocity": drawdown_velocity,
            "drawdown_acceleration": drawdown_acceleration,
            "recovery_velocity": recovery_velocity,
            "time_since_peak": max(0.0, time_since_peak),
            "time_below_entry": max(0.0, time_below_entry),
            "time_below_breakeven": max(0.0, time_below_breakeven),
            "distance_to_be_velocity": distance_to_be_velocity,
        }

    def manage_pending_orders(
        self,
        symbol: str,
        current_tick: TickData,
        symbol_info: SymbolInfo | None = None,
        atr: float = 1.50,
        max_pending_dist_atr_mult: float = 2.50,  # Increased from 1.20 to give limit orders breathing room
    ) -> None:
        """
        Pending order lifecycle guard with a hard 30-second churn lock.

        A pending limit order is NEVER cancelled/recreated unless BOTH hold:
          - time_since_placement > PENDING_ORDER_LOCK_SECONDS (30s), AND
          - price drift >= 1.0 x ATR.

        Stale-age expiry (>120s) still applies after the lock window, so an order that
        the market has walked away from is not left hanging forever.
        """
        try:
            get_pending_fn = getattr(self.adapter, "get_pending_orders", None)
            if not get_pending_fn:
                return

            pending_orders = get_pending_fn(symbol=symbol)
            if not pending_orders:
                return

            now = current_tick.timestamp
            max_allowed_dist = round(atr * max_pending_dist_atr_mult, 2)
            #: Minimum price drift (in price units) required to justify a re-quote.
            required_drift = round(atr * 1.0, 2)

            for pending in pending_orders:
                order_type = self._pending_field(pending, "type", "order_type")
                price_open = float(
                    self._pending_field(pending, "price_open", "price", default=0.0) or 0.0
                )
                ticket = self._pending_field(pending, "ticket", "order_id")

                if not ticket or price_open <= 0.0:
                    continue

                if ticket not in self._pending_orders_setup_time:
                    self._pending_orders_setup_time[ticket] = now

                type_str = str(getattr(order_type, "value", order_type) or "").upper()
                is_buy_side = "BUY" in type_str
                dist = (
                    abs(current_tick.ask - price_open)
                    if is_buy_side
                    else abs(current_tick.bid - price_open)
                )
                age = (now - self._pending_orders_setup_time[ticket]).total_seconds()

                # ---------------------------------------------------------------
                # 30-SECOND PENDING LOCK (anti-churn)
                # ---------------------------------------------------------------
                # Inside the lock window the order is untouchable, full stop. This is
                # what stops the high-frequency cancel/recreate loop that previously
                # burned broker request quota and produced order-churn rejections.
                if age <= PENDING_ORDER_LOCK_SECONDS:
                    logger.debug(
                        "PENDING_ORDER_LOCKED: within 30s placement lock, no modification allowed",
                        ticket=ticket,
                        age_sec=round(age, 1),
                        lock_sec=PENDING_ORDER_LOCK_SECONDS,
                    )
                    continue

                # Past the lock window, a re-quote additionally requires real drift.
                if dist < required_drift:
                    logger.debug(
                        "PENDING_ORDER_HELD: price drift below 1.0x ATR threshold",
                        ticket=ticket,
                        drift=round(dist, 2),
                        required_drift=required_drift,
                    )
                    continue

                # Statistically weak criteria for cancellation (evaluated only after the
                # 30s lock has expired AND drift >= 1.0 x ATR):
                # 1. Dist exceeds max allowed dist
                # 2. Stale limit (age > 120s)
                # 3. Market momentum expanding opposite (handled by Falling Knife Protection)
                should_cancel = False
                cancel_reason = ""

                if dist > max_allowed_dist:
                    should_cancel = True
                    cancel_reason = f"DISTANCE_BREACH (${dist:.2f} > ${max_allowed_dist:.2f})"
                elif age > 120.0:
                    should_cancel = True
                    cancel_reason = f"AGE_EXPIRATION ({age:.1f}s > 120.0s)"

                if should_cancel:
                    # BUG-072/073: broker-verified cancellation — the slot is
                    # released only after broker state confirms the removal.
                    # P0-A (BUG-140): remember WHY so the terminal outcome can
                    # distinguish CANCELED_UNFILLED from EXPIRED_UNFILLED.
                    self._pending_cancel_reasons[ticket] = cancel_reason
                    cancelled_ok = self.cancel_pending_order_verified(ticket=ticket, symbol=symbol)
                    if cancelled_ok:
                        logger.info(
                            f"[CANCEL TRACE] PENDING ORDER CANCELLED: Ticket {ticket}. Reason: {cancel_reason}. Max Allowed Dist: ${max_allowed_dist:.2f}"
                        )
                        # Audit cancellation
                        self.audit.log_order(
                            ticket=ticket,
                            order_id=f"cancel_{ticket}",
                            symbol=symbol,
                            action="Expired pending order"
                            if "AGE" in cancel_reason
                            else "Cancelled order",
                            price=price_open,
                            stop_loss=float(
                                self._pending_field(pending, "sl", "stop_loss", default=0.0) or 0.0
                            ),
                            take_profit=float(
                                self._pending_field(pending, "tp", "take_profit", default=0.0)
                                or 0.0
                            ),
                            volume=float(
                                self._pending_field(pending, "volume", default=0.01) or 0.01
                            ),
                            reason=cancel_reason,
                            latency=0.01,
                            execution_mode="PREDICTIVE_LIMIT",
                        )
        except Exception as err:
            logger.error("Failed to manage dynamic pending orders", error=str(err))

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
                            self._pending_cancel_reasons[pending_ticket] = (
                                "FALLING_KNIFE_PROTECTION"
                            )
                            # BUG-072/073: broker-verified cancellation.
                            if self.cancel_pending_order_verified(
                                ticket=pending_ticket, symbol=symbol
                            ):
                                self._pending_orders_setup_time.pop(pending_ticket, None)
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
                                    latency=0.01,
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
        smart_metrics: dict,
        evidence: dict,
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
        try:
            self._exit_pending_final_reason[ticket] = {
                "action": action,
                "reason": scenario,
                "state": debounced_state.value,
                "at": now.isoformat() if hasattr(now, "isoformat") else str(now),
            }
        except Exception:
            pass

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
    ) -> tuple[dict, dict, float, "PositionState", bool]:
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
        smart_metrics: dict,
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
        smart_metrics: dict,
        evidence: dict,
        pnl_features: dict,
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
                    current_tick.ask if ai_flip_action == ActionType.BUY_STOP else current_tick.bid
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
                self.adapter.place_pending_order(
                    symbol=pos.symbol,
                    order_type=OrderType.BUY_STOP
                    if ai_flip_action == ActionType.BUY_STOP
                    else OrderType.SELL_STOP,
                    volume=rev_volume,
                    price=rev_entry,
                    stop_loss=rev_sl,
                    take_profit=rev_tp,
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

    def _autopsy_vanished_ticket(
        self,
        dead_ticket: int,
        history_deals: list,
        symbol: str,
        now: datetime,
        current_tick: TickData,
        symbol_info: SymbolInfo | None,
        atr: float,
        hours_back: int,
    ) -> None:
        """Per-ticket vanished-position autopsy (S6): resolve the closing
        deal (live window + BUG-088/089 durable fallback), write the single
        data-rich autopsy row, record the experience outcome, emit telemetry,
        and release per-ticket state. Moved VERBATIM from
        _sweep_dead_tickets' per-ticket loop (no accumulators, no skips)."""
        entry = self._entry_prices.get(dead_ticket, 0.0)
        tp_price = self._entry_tps.get(dead_ticket, 0.0)
        sl_price = self._entry_sls.get(dead_ticket, 0.0)
        entry_time = self._entry_timestamps.get(dead_ticket)
        duration_sec = (now - entry_time).total_seconds() if entry_time else 0.0
        vol = self._last_known_volume.get(dead_ticket, 0.0)
        direction = self._entry_directions.get(dead_ticket, "BUY")

        matched_deal = next(
            (d for d in history_deals if d.get("position_ticket") == dead_ticket), None
        )
        if matched_deal is None:
            # BUG-088/089 (TASK-7): the live 24h deal window can miss a
            # close (restart gap, window expiry). Fall back to the DURABLE
            # broker-deal capture (audit_broker_deals, position_id join)
            # before conceding FALLBACK_ESTIMATE/UNKNOWN.
            try:
                durable = self.audit.get_broker_deals_for_position(dead_ticket)
            except Exception:
                durable = []
            if durable:
                matched_deal = durable[0]
                history_deals = list(history_deals) + durable
                logger.debug(
                    "[BROKER_OUTCOME] event=DURABLE_DEAL_FALLBACK",
                    ticket=dead_ticket,
                    deals=len(durable),
                )

        # ------------------------------------------------------------------
        # BUG-046 FIX: never default missing broker truth to zero.
        # When no deal matched, realized PnL is UNKNOWN, not $0. The old
        # code wrote profit_usd=0.0 which corrupted every closed outcome
        # (R=0) and starved the research engine. A deterministic
        # price-delta FALLBACK_ESTIMATE is used ONLY when entry/exit
        # prices + volume + contract size are all authoritative; otherwise
        # the value is left None and the outcome layer records UNKNOWN.
        # ------------------------------------------------------------------
        profit_usd = None
        swap_usd = None
        comm_usd = None
        exit_price = entry
        status_str = "CLOSED"

        if matched_deal:
            profit_usd = matched_deal.get("profit", 0.0)
            swap_usd = matched_deal.get("swap", 0.0)
            comm_usd = matched_deal.get("commission", 0.0)
            exit_price = matched_deal.get("price", 0.0)
            deal_reason_code = matched_deal.get("reason", 0)
            comment = matched_deal.get("comment", "")
            logger.debug(
                "[BROKER_OUTCOME] event=MATCHED",
                ticket=dead_ticket,
                source="BROKER_HISTORY",
                position_ticket=matched_deal.get("position_ticket"),
                profit=profit_usd,
            )

            if "NSE_CLOSE" in comment or "emergency" in comment.lower() or "cut" in comment.lower():
                status_str = "MANUALLY_CLOSED"
            elif (
                deal_reason_code == 5  # DEAL_REASON_TP (BUG-083)
                or "tp" in comment.lower()
                or (profit_usd > 0 and abs(exit_price - tp_price) < 0.10)
            ):
                status_str = "CLOSED_TP"
            elif (
                deal_reason_code in (4, 6)  # DEAL_REASON_SL / SO (BUG-083)
                or "sl" in comment.lower()
                or (profit_usd < 0 and abs(exit_price - sl_price) < 0.10)
            ):
                status_str = "CLOSED_SL"
            else:
                status_str = "MANUALLY_CLOSED" if deal_reason_code in (1, 2) else "CLOSED"
        else:
            # No broker deal matched. Fall back to a deterministic price
            # estimate ONLY when authoritative prices are available, and
            # flag it explicitly (FALLBACK_ESTIMATE) so consumers know it
            # is not broker truth.
            exit_price = current_tick.bid if direction == "BUY" else current_tick.ask
            if entry > 0.0 and exit_price > 0.0 and vol > 0.0:
                contract_sz = self._resolve_contract_size(symbol_info)
                price_delta = (
                    (exit_price - entry)
                    if "BUY" in str(direction).upper()
                    else (entry - exit_price)
                )
                profit_usd = float(price_delta) * float(vol) * contract_sz
                swap_usd = 0.0
                comm_usd = 0.0
                logger.debug(
                    "[BROKER_OUTCOME] event=RECONSTRUCTION_FALLBACK",
                    ticket=dead_ticket,
                    source="FALLBACK_ESTIMATE",
                    entry=entry,
                    exit=exit_price,
                    volume=vol,
                    estimated_profit=profit_usd,
                )
            else:
                # Not enough evidence: explicit UNKNOWN (never zero).
                logger.warning(
                    "[BROKER_OUTCOME] event=MATCH_FAILED",
                    ticket=dead_ticket,
                    reason="NO_BROKER_DEAL_AND_NO_PRICE_EVIDENCE",
                    searched_hours_back=hours_back,
                    deals_found=len(history_deals),
                )

        # =============================================================
        # MODULE A: SINGLE DATA-RICH AUTOPSY ROW PER CLOSED TRADE
        # =============================================================
        mae_val = float(self._mae_tracker.get(dead_ticket, 0.0))
        mfe_val = float(self._mfe_tracker.get(dead_ticket, 0.0))
        initial_sl_val = float(sl_price)
        final_sl_val = float(self._last_modify_sl.get(dead_ticket, initial_sl_val))

        # was_sl_modified: True only when trailing/breakeven actually shifted the SL.
        # Phase 14: also honour the explicit modification flag - a
        # breakeven/trailing lock that was applied in-process but later
        # reconciled must survive the autopsy (BUG-045 anomaly E).
        was_sl_modified = bool(
            self._sl_modified_flags.get(dead_ticket, False)
            or abs(final_sl_val - initial_sl_val) > 1e-9
        )

        # is_risk_free_hit: closed on a stop that had already been moved into profit.
        is_risk_free_hit = 0
        if direction == "BUY":
            if final_sl_val >= entry and abs(exit_price - final_sl_val) < 0.15:
                is_risk_free_hit = 1
        elif final_sl_val <= entry and final_sl_val > 0.0 and abs(exit_price - final_sl_val) < 0.15:
            is_risk_free_hit = 1

        # ---- Exit mechanism resolution (engine intent overrides broker heuristic) ----
        forced_mechanism = self._forced_exit_mechanisms.pop(dead_ticket, None)
        # Phase 14: map to the canonical taxonomy via broker evidence
        # (DEAL_REASON + SL/TP geometry + protective context). A stop-out
        # is NEVER labelled MANUAL_CLOSE merely because the internal
        # state machine performed protection logic first (BUG-045).
        (
            exit_mechanism,
            exit_reason_source,
            exit_evidence,
            exit_reason_confidence,
        ) = classify_exit_with_evidence(
            deal_reason_code=matched_deal.get("reason", 0) if matched_deal else 0,
            comment=matched_deal.get("comment", "") if matched_deal else "",
            profit_usd=profit_usd,
            exit_price=exit_price,
            tp_price=tp_price,
            sl_price=sl_price,
            final_sl=final_sl_val,
            entry_price=entry,
            was_sl_modified=bool(was_sl_modified),
            direction=direction,
            forced_mechanism=forced_mechanism,
        )

        # Phase 14: authoritative broker closure reconstruction. When the
        # broker deal evidence is available (multi-deal aggregation
        # included), the realized result comes from the DEAL path - never
        # from a stale floating-PnL default of zero (BUG-045).
        broker_outcome = reconstruct_broker_outcome(
            ticket=dead_ticket,
            symbol=symbol,
            direction=direction,
            deals=history_deals,
            matched_deal=matched_deal,
            entry_price=entry,
            initial_sl=initial_sl_val,
            final_sl=final_sl_val,
            tp_price=tp_price,
            volume=vol,
            fallback_exit_price=exit_price,
            close_time=now,
            entry_time=entry_time,
        )
        if broker_outcome.reconstruction_source != "NONE":
            profit_usd = broker_outcome.gross_profit
            comm_usd = broker_outcome.commission
            swap_usd = broker_outcome.swap
            exit_price = broker_outcome.exit_price
            # BUG-088 (TASK-7): when the broker reconstruction aggregated
            # multiple OUT deals, reclassify on the AGGREGATE PnL + the
            # aggregated comment/reason so a partial-fill family is never
            # classified by a single deal's sign.
            if len(history_deals) > 1:
                try:
                    deal_gross = sum(
                        float(d.get("profit", 0.0) or 0.0)
                        for d in history_deals
                        if d.get("position_ticket") == dead_ticket
                    )
                    profit_usd = deal_gross
                except Exception:
                    pass

        # ---- Quant risk excursions converted to account currency ----
        mae_usd = self._price_delta_to_usd(min(mae_val, 0.0), vol, symbol_info)
        mfe_usd = self._price_delta_to_usd(max(mfe_val, 0.0), vol, symbol_info)
        # Prefer directly observed USD peaks when the tick loop tracked them.
        peak_dd_usd = float(self._peak_drawdown_usd.get(dead_ticket, 0.0))
        peak_win_usd = float(self._peak_profit_usd.get(dead_ticket, 0.0))
        if peak_dd_usd < 0.0:
            mae_usd = peak_dd_usd
        if peak_win_usd > 0.0:
            mfe_usd = peak_win_usd

        open_time_str = (
            entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time or "")
        )
        close_time_str = now.isoformat() if hasattr(now, "isoformat") else str(now)

        self.audit.log_ledger_closed(
            ticket=dead_ticket,
            symbol=symbol,
            direction=direction,
            volume=vol,
            entry_price=entry,
            exit_price=exit_price,
            status=status_str,
            pnl=profit_usd,
            commission=comm_usd,
            swap=swap_usd,
            duration_sec=duration_sec,
            timestamp_str=close_time_str,
            mae=mae_val,
            mfe=mfe_val,
            initial_sl_price=initial_sl_val,
            final_sl_price=final_sl_val,
            is_risk_free_hit=is_risk_free_hit,
            exit_mechanism=exit_mechanism,
            # --- Institutional autopsy fields ---
            order_id=self._entry_order_ids.get(dead_ticket, ""),
            open_time=open_time_str,
            close_time=close_time_str,
            entry_reason=self._entry_reasons.get(dead_ticket, ""),
            ai_confidence_at_open=self._entry_confidences.get(dead_ticket, 0.0),
            market_regime_at_open=self._entry_regimes.get(dead_ticket, ""),
            was_sl_modified=int(was_sl_modified),
            mae_usd=mae_usd,
            mfe_usd=mfe_usd,
            account_balance_after=self._last_account_balance,
            account_equity_after=self._last_account_equity,
            drawdown_percent_after=self._current_drawdown_percent(),
            # DEBUG-AUDIT (2026-08-18): full chart-state fingerprint at
            # dispatch persisted to the ledger for post-hoc setup/strategy
            # attribution of every closed trade.
            entry_setup_snapshot=json.dumps(self._entry_setup_snapshots.get(dead_ticket, {})),
            exit_reason_source=exit_reason_source,
            exit_evidence=exit_evidence,
            exit_reason_confidence=exit_reason_confidence,
            reversal_events_json=json.dumps(self._reversal_events.get(dead_ticket, [])),
        )

        # =============================================================
        # PHASE 08: EXPERIENCE OUTCOME ATTRIBUTION
        # -------------------------------------------------------------
        # Records the append-only outcome for the decision that produced
        # this ticket, including full execution/behaviour evidence so the
        # experience layer can attribute the result across strategy,
        # entry, management, exit and execution quality.
        #
        # Fully isolated: any failure here is logged and ignored. The
        # autopsy row above is already persisted, and no execution
        # decision depends on this call.
        # =============================================================
        if self.experience_engine is not None:
            self._record_experience_outcome(
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
                was_sl_modified=bool(was_sl_modified),
                broker_outcome=broker_outcome,
            )

        net_pnl_log = "UNKNOWN"
        if profit_usd is not None:
            net_pnl_log = f"${(profit_usd - (comm_usd or 0.0) - (swap_usd or 0.0)):+.2f}"
            self._net_pnl_by_ticket[dead_ticket] = (
                float(profit_usd) - float(comm_usd or 0.0) - float(swap_usd or 0.0)
            )
        self._exit_mechanism_by_ticket[dead_ticket] = exit_mechanism

        logger.info(
            "[LEDGER AUTOPSY] Closed trade recorded",
            ticket=dead_ticket,
            direction=direction,
            exit_mechanism=exit_mechanism,
            entry_reason=self._entry_reasons.get(dead_ticket, ""),
            net_pnl=net_pnl_log,
            mae_usd=f"${mae_usd:+.2f}",
            mfe_usd=f"${mfe_usd:+.2f}",
            was_sl_modified=was_sl_modified,
        )

        if self.notifier:
            try:
                msg_id = self._order_message_ids.get(dead_ticket)
                orig_risk = self._initial_risks.get(dead_ticket, 0.0)
                profit_pct = 0.0
                if profit_usd is not None and entry > 0.0:
                    profit_pct = abs(exit_price - entry) / entry * 100.0
                    if (profit_usd + (swap_usd or 0.0) + (comm_usd or 0.0)) < 0:
                        profit_pct = -profit_pct

                total_net_profit = (
                    profit_usd + (swap_usd or 0.0) + (comm_usd or 0.0)
                    if profit_usd is not None
                    else 0.0
                )

                if (
                    status_str == "MANUALLY_CLOSED"
                    and matched_deal
                    and (
                        "NSE_CLOSE" in matched_deal.get("comment", "")
                        or "emergency" in matched_deal.get("comment", "").lower()
                        or "cut" in matched_deal.get("comment", "").lower()
                    )
                ):
                    mae_val = self._mae_tracker.get(dead_ticket, 0.0)
                    dd_pct = (abs(mae_val) / max(atr, 0.50)) * 100.0
                    self.notifier.notify_emergency_cut(
                        ticket=dead_ticket,
                        score=self._hold_score_tracker.get(dead_ticket, 100),
                        reasons=matched_deal.get("comment", "")
                        if matched_deal
                        else "NSE Emergency Cut",
                        saved_usd=abs(total_net_profit)
                        if total_net_profit < 0
                        else total_net_profit,
                        trigger_source="Algorithm Position Router",
                        drawdown_pct=dd_pct,
                        reply_to_message_id=msg_id,
                    )
                elif status_str == "CLOSED_TP":
                    self.notifier.notify_tp_touched(
                        ticket=dead_ticket,
                        symbol=symbol,
                        entry=entry,
                        tp_price=tp_price,
                        exit_price=exit_price,
                        profit_usd=total_net_profit,
                        profit_pct=profit_pct,
                        duration_sec=duration_sec,
                        reply_to_message_id=msg_id,
                    )
                elif status_str == "CLOSED_SL":
                    self.notifier.notify_sl_touched(
                        ticket=dead_ticket,
                        symbol=symbol,
                        entry=entry,
                        sl_price=sl_price,
                        exit_price=exit_price,
                        loss_usd=total_net_profit,
                        loss_pct=profit_pct,
                        duration_sec=duration_sec,
                        risk_usd=orig_risk,
                        reply_to_message_id=msg_id,
                    )
                else:
                    # BUG-081: Telegram consumes the CANONICAL outcome.
                    # The exit label/evidence come from the same classifier
                    # result written to the ledger (AccountingCore /
                    # ExperienceLedger) — never re-inferred from the broker
                    # reason code, and never defaulted to MANUAL.
                    self.notifier.notify_canonical_close(
                        ticket=dead_ticket,
                        symbol=symbol,
                        entry=entry,
                        exit_price=exit_price,
                        profit_usd=total_net_profit,
                        duration_sec=duration_sec,
                        exit_reason=exit_mechanism,
                        evidence=f"{exit_reason_source} | {exit_evidence}",
                        initial_sl=initial_sl_val,
                        final_sl=final_sl_val,
                        strategy=self._entry_reasons.get(dead_ticket, ""),
                        regime=self._entry_regimes.get(dead_ticket, ""),
                        confidence=self._entry_confidences.get(dead_ticket, 0.0),
                        realized_r=total_net_profit / max(orig_risk, 1e-9)
                        if orig_risk > 0.0
                        else 0.0,
                        mfe_usd=mfe_usd,
                        mae_usd=mae_usd,
                        reply_to_message_id=msg_id,
                    )
            except Exception as e:
                logger.error("Failed to notify closed trade", error=e)

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

    def reconcile_missed_closes(
        self,
        symbol: str,
        current_tick: TickData,
        symbol_info: SymbolInfo | None = None,
        hours_back: int = 24,
    ) -> int:
        """
        Phase 14 reconciliation close-loop (BUG-045 spec 23).

        After a restart (or a missed broker close event) the internal ticket
        trackers may be empty while the broker history shows a position that
        was already closed. This method queries the authoritative broker deal
        history and, for every closed position ticket that has a ledger OPENED
        row but no CLOSED row AND no internal tracking, emits the same
        autopsy + experience outcome path as a live close.

        Never raises: reconciliation is a best-effort background concern.
        Learning never blocks protective execution (the close already
        happened at the broker - this only records it).

        Returns the number of missed closes reconciled.
        """
        try:
            # BUG-090 (TASK-7 perf): the reconciliation close-loop must never fetch
            # broker history on every tick. Gate the fetch to once per 60s and skip
            # it entirely when no OPENED-without-CLOSED ledger row exists (the only
            # condition that can produce a reconcile).
            now_mono = time.monotonic()
            if (now_mono - self._last_reconcile_attempt) < 60.0:
                return 0
            self._last_reconcile_attempt = now_mono
            try:
                pending = self.audit.count_ledger_opened_unclosed()
            except Exception:
                pending = -1  # pre-check unavailable: fall through to the fetch
            if pending == 0:
                return 0
            history_deals = self.adapter.get_closed_deals_history(
                symbol=symbol, hours_back=hours_back
            )
            if not history_deals:
                return 0

            # Ticket -> aggregated deal evidence (partial closes merge into one).
            ticket_deals: dict[int, list[dict]] = {}
            for d in history_deals:
                pt = d.get("position_ticket")
                if pt is not None:
                    ticket_deals.setdefault(int(pt), []).append(d)

            reconciled = 0
            now = current_tick.timestamp
            for ticket, deals in ticket_deals.items():
                if ticket in self._entry_timestamps:
                    continue  # already tracked/closed through the live path
                if self._reconcile_seen.get(ticket, False):
                    continue
                # Only reconcile positions we can attribute to this engine
                # (a ledger OPENED placeholder exists for the ticket).
                if not self.audit.has_ledger_opened(ticket):
                    continue

                matched = deals[0]
                entry = float(matched.get("entry_price", 0.0) or 0.0)
                exit_price = float(matched.get("price", 0.0) or 0.0)
                direction = str(matched.get("direction", "BUY") or "BUY")
                vol = float(matched.get("volume", 0.0) or 0.0)
                profit_usd = float(matched.get("profit", 0.0) or 0.0)

                # Entry context recovered from the ledger OPENED row.
                opened = self.audit.get_ledger_opened(ticket)
                if opened:
                    entry = float(opened.get("entry_price", entry) or entry)
                    direction = str(opened.get("direction", direction) or direction)
                    vol = float(opened.get("volume", vol) or vol)
                    # Phase 14: restore the originating request_id so the
                    # experience outcome is attributed to the ORIGINAL decision
                    # (ORIGINAL_REQUEST provenance), not a fallback.
                    opened_order_id = str(opened.get("order_id", "") or "")
                    if opened_order_id:
                        self._entry_order_ids[ticket] = opened_order_id
                        self._entry_reasons[ticket] = (
                            str(opened.get("entry_reason", "") or "") or "PURE_AI"
                        )
                        self._entry_confidences[ticket] = float(
                            opened.get("ai_confidence_at_open", 0.0) or 0.0
                        )
                        self._entry_regimes[ticket] = str(
                            opened.get("market_regime_at_open", "") or ""
                        )

                atr = max(self._safe_feature_float(None, "atr_m1", 0.80), 0.50)
                initial_sl = float(opened.get("initial_sl_price", 0.0) or 0.0)
                final_sl = float(matched.get("sl", initial_sl) or initial_sl)
                broker_outcome = reconstruct_broker_outcome(
                    ticket=ticket,
                    symbol=symbol,
                    direction=direction,
                    deals=deals,
                    matched_deal=None,
                    entry_price=entry,
                    initial_sl=initial_sl,
                    final_sl=final_sl,
                    tp_price=float(matched.get("tp", 0.0) or 0.0),
                    volume=vol,
                    fallback_exit_price=exit_price,
                    close_time=now,
                    entry_time=None,
                )
                (
                    exit_mechanism,
                    exit_reason_source,
                    exit_evidence,
                    exit_reason_confidence,
                ) = classify_exit_with_evidence(
                    deal_reason_code=int(matched.get("reason", 0) or 0),
                    comment=matched.get("comment", ""),
                    profit_usd=profit_usd,
                    exit_price=exit_price,
                    tp_price=float(matched.get("tp", 0.0) or 0.0),
                    sl_price=float(matched.get("sl", 0.0) or 0.0),
                    final_sl=final_sl,
                    entry_price=entry,
                    was_sl_modified=bool(initial_sl and abs(final_sl - initial_sl) > 1e-9),
                    direction=direction,
                )

                # Persist the same single autopsy row the live path writes.
                self.audit.log_ledger_closed(
                    ticket=ticket,
                    symbol=symbol,
                    direction=direction,
                    volume=vol,
                    entry_price=entry,
                    exit_price=broker_outcome.exit_price,
                    status="RECONCILED",
                    pnl=broker_outcome.gross_profit,
                    commission=broker_outcome.commission,
                    swap=broker_outcome.swap,
                    duration_sec=0.0,
                    timestamp_str=now.isoformat() if hasattr(now, "isoformat") else str(now),
                    mae=0.0,
                    mfe=0.0,
                    initial_sl_price=initial_sl,
                    final_sl_price=final_sl,
                    is_risk_free_hit=1 if "BREAK_EVEN" in exit_mechanism else 0,
                    exit_mechanism=exit_mechanism,
                    order_id=opened.get("order_id", "") if opened else "",
                    open_time=opened.get("open_time", "") if opened else "",
                    close_time=now.isoformat() if hasattr(now, "isoformat") else str(now),
                    entry_reason=opened.get("entry_reason", "") if opened else "",
                    ai_confidence_at_open=float(opened.get("ai_confidence_at_open", 0.0) or 0.0),
                    market_regime_at_open=opened.get("market_regime_at_open", "") if opened else "",
                    was_sl_modified=1 if (initial_sl and abs(final_sl - initial_sl) > 1e-9) else 0,
                    mae_usd=0.0,
                    mfe_usd=0.0,
                    account_balance_after=self._last_account_balance,
                    account_equity_after=self._last_account_equity,
                    drawdown_percent_after=self._current_drawdown_percent(),
                    exit_reason_source=exit_reason_source,
                    exit_evidence=exit_evidence,
                    exit_reason_confidence=exit_reason_confidence,
                    reversal_events_json=json.dumps(self._reversal_events.get(ticket, [])),
                )

                if self.experience_engine is not None:
                    self._record_experience_outcome(
                        dead_ticket=ticket,
                        now=now,
                        entry=entry,
                        exit_price=broker_outcome.exit_price,
                        initial_sl_val=initial_sl,
                        vol=vol,
                        atr=atr,
                        symbol_info=symbol_info,
                        profit_usd=broker_outcome.gross_profit,
                        comm_usd=broker_outcome.commission,
                        swap_usd=broker_outcome.swap,
                        mae_val=0.0,
                        mfe_val=0.0,
                        mae_usd=0.0,
                        mfe_usd=0.0,
                        duration_sec=0.0,
                        exit_mechanism=exit_mechanism,
                        was_sl_modified=bool(initial_sl and abs(final_sl - initial_sl) > 1e-9),
                        broker_outcome=broker_outcome,
                    )

                self._reconcile_seen[ticket] = True
                self._closed_tickets[ticket] = True
                self._exit_pending_final_reason.pop(ticket, None)
                reconciled += 1
                logger.info(
                    "[RECONCILIATION] missed close recorded",
                    ticket=ticket,
                    exit_mechanism=exit_mechanism,
                    pnl=broker_outcome.gross_profit,
                )
            return reconciled
        except Exception as err:
            logger.error("[RECONCILIATION] pass failed (isolated)", error=str(err))
            return 0

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
        """
        Forwards a closed position to the Phase 08 experience layer.

        Responsibilities kept strictly here (never inside the experience layer):
          * resolve the originating proposal `request_id` for attribution
          * convert USD PnL into a risk-normalised R multiple
          * hand over the observed execution/behaviour evidence

        Phase 14 (BUG-045): when the in-memory request_id map is empty (lost
        across restart / reconciliation), the broker ticket is forwarded so the
        experience layer can attempt deterministic correlation recovery. The
        outcome is NEVER silently discarded while a correlatable decision may
        exist. The reconstructed broker outcome is passed through so the
        authoritative deal result survives into the experience record.

        This method NEVER raises: the learning layer is non-critical and the
        financial autopsy row has already been persisted by the caller.
        """
        try:
            req_id = self._entry_order_ids.get(dead_ticket, "") or request_id
            if not req_id:
                # No originating request id in memory: forward the broker ticket
                # as the correlation key; the experience layer attempts
                # deterministic recovery (ORIGINAL_REQUEST / POSITION_STATE /
                # BROKER_TICKET_FALLBACK) and only then may reject with full
                # diagnostics. It never silently discards (BUG-045).
                req_id = ""
                correlation_ticket = str(dead_ticket)
            else:
                correlation_ticket = req_id

            sl_distance = abs(entry - initial_sl_val) if initial_sl_val > 0.0 else (atr * 1.5)
            contract_sz = self._resolve_contract_size(symbol_info)
            risk_usd = max(1.0, sl_distance * max(vol, 0.0) * contract_sz)
            # BUG-046: never silently treat missing broker truth as zero PnL.
            # When profit_usd is unknown (no broker deal AND no price evidence),
            # record the outcome as UNKNOWN so research never sees a fake R=0.
            if profit_usd is None:
                net_pnl_usd = 0.0
                r_multiple = 0.0
                logger.warning(
                    "[BROKER_OUTCOME] event=RECONSTRUCTION_UNKNOWN",
                    ticket=dead_ticket,
                    reason="NO_BROKER_DEAL_AND_NO_PRICE_EVIDENCE",
                    realized_r="UNKNOWN",
                )
            else:
                net_pnl_usd = profit_usd - (comm_usd or 0.0) - (swap_usd or 0.0)
                r_multiple = net_pnl_usd / risk_usd

            expected_entry = self._entry_expected_price.get(dead_ticket, 0.0)
            direction = self._entry_directions.get(dead_ticket, "BUY")
            slippage_points = 0.0
            if expected_entry > 0.0 and entry > 0.0:
                raw = entry - expected_entry
                slippage_points = raw if "BUY" in str(direction).upper() else -raw

            broker_payload = None
            if broker_outcome is not None:
                try:
                    broker_payload = broker_outcome.model_dump()
                except Exception:
                    broker_payload = None

            self.experience_engine.record_trade_outcome(
                request_id=req_id,
                execution_id=correlation_ticket if not req_id else str(dead_ticket),
                outcome_timestamp=now,
                is_executed=True,
                is_closed=True,
                exit_reason=exit_mechanism,
                realized_pnl_usd=net_pnl_usd,
                realized_r_multiple=r_multiple,
                mae_points=mae_val,
                mfe_points=mfe_val,
                mae_usd=mae_usd,
                mfe_usd=mfe_usd,
                holding_duration_seconds=duration_sec,
                approved_volume=vol,
                actual_entry=entry,
                slippage_points=slippage_points,
                execution_latency_ms=self._entry_fill_latency_ms.get(dead_ticket, 0.0),
                spread_at_execution=self._entry_spread.get(dead_ticket, 0.0),
                initial_sl_distance=sl_distance,
                sl_moved=was_sl_modified,
                partial_closed=bool(self._partial_closed_tickets.get(dead_ticket, False)),
                atr_at_entry=self._entry_atr.get(dead_ticket, atr),
                time_to_mae_sec=self._time_to_mae_sec.get(dead_ticket, 0.0),
                time_to_mfe_sec=self._time_to_mfe_sec.get(dead_ticket, 0.0),
                broker_outcome=broker_payload,
            )
        except Exception as exp_err:
            logger.error(
                "[EXPERIENCE] outcome forwarding failed (isolated)",
                ticket=dead_ticket,
                error=str(exp_err),
            )

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
            self._pending_orders_setup_time,
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
            self._trajectory_history,
            # Recovery structures (owned by the ledger; dropped below)
            self._closed_tickets,
            self._exit_pending_final_reason,
        ):
            tracker.pop(ticket, None)
        self._recovery_ledger.drop_ticket(ticket)
        self._state_machine.drop_ticket(ticket)
        with self._live_tickets_lock:
            self._tickets_cache.pop_ticket(ticket)
