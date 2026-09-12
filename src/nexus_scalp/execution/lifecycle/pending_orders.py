"""PendingOrderLifecycle — explicit owner of pending-order lifecycle state.

P0 seam S7 (god-file decomposition, extraction 1 of the order-manager wave):
the pending-order churn lock, the bounded entry-context (lineage) registry,
the broker-verified cancellation path, and the pending reconciliation report
move OUT of ``OrderLifecycleManager`` verbatim. The manager keeps thin
delegating methods under the historical names so call sites and tests are
unaffected — but the implementation and the state ownership now live HERE.

State ownership moved out of the manager (previously 8 manager-owned dicts
+ 2 scalars):

    _last_mod_price            : ticket -> last re-quote price (churn lock)
    _last_mod_time             : ticket -> last re-quote time (churn lock)
    _pending_orders_setup_time : ticket -> placement time (age/expiry)
    _pending_cancel_reasons    : ticket -> last cancel reason (BUG-140)
    _pending_context_registry  : order_id -> staged entry context (BUG-081)
    _pending_context_ts        : order_id -> monotonic staging clock
    _context_bound_tickets     : order_id -> set of bound sibling tickets
    _unbound_ticket_contexts   : ticket -> provenance-gap reason
    _PENDING_CONTEXT_TTL_SEC / _PENDING_CONTEXT_MAX_ENTRIES

Collaborators (injected, read-only from this component's side):
    adapter            : IMT5Port — broker queries + cancel + cache refresh
    tickets_view       : callable() -> live tickets dict (for family checks)
    refresh_cache      : callable(symbol, tick) -> None (TicketsCache rebuild)
    experience_engine  : terminal pending-outcome emission
    audit              : cancellation audit rows

Ownership contract:
    READS   : live tickets view (family membership), adapter broker truth
    WRITES  : only its own dicts above
    AUTHORITY: NONE — no position management, no dispatch, no risk math.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.domain.models import TickData
from nexus_scalp.execution.terminal_outcome import emit_terminal_pending_outcome

logger = logging.getLogger(__name__)

#: Pending orders are untouchable for this long after placement (anti-churn).
PENDING_ORDER_LOCK_SECONDS: float = 30.0


class PendingOrderLifecycle:
    """Pending-order lifecycle owner: churn lock, lineage registry, cancel."""

    def __init__(
        self,
        adapter: Any,
        tickets_view: Callable[[], dict[int, dict[str, Any]]],
        refresh_cache: Callable[..., None],
        experience_engine: Any = None,
        audit: Any = None,
    ) -> None:
        self.adapter = adapter
        self._tickets_view = tickets_view
        self._refresh_cache = refresh_cache
        self.experience_engine = experience_engine
        self.audit = audit

        # --- churn lock state (re-quote gating) -----------------------------
        self._last_mod_price: dict[int, float] = {}
        self._last_mod_time: dict[int, datetime] = {}
        # --- placement/age + cancel-reason state ---------------------------
        self._pending_orders_setup_time: dict[int, datetime] = {}
        #: P0-A (BUG-140): the most recent cancel reason per pending ticket so
        #: the terminal outcome can distinguish CANCELED_UNFILLED from
        #: EXPIRED_UNFILLED (AGE_EXPIRATION path).
        self._pending_cancel_reasons: dict[int, str] = {}
        # --- bounded entry-context (lineage) registry (BUG-081) ------------
        #: so EVERY sibling ticket of a broker split-fill resolves the SAME
        #: immutable entry context (order_id, reason, confidence, regime,
        #: expected entry, dispatch clock, setup snapshot). Entries are removed
        #: once the fill family has been bound (idempotent) or after a stale TTL.
        self._pending_context_registry: dict[str, dict[str, Any]] = {}
        #: monotonic staging clock per order_id (bounded-memory sweep).
        self._pending_context_ts: dict[str, float] = {}
        #: order_id -> set of tickets already bound (idempotent family tracking).
        self._context_bound_tickets: dict[str, set[int]] = {}
        #: tickets with NO staging context ever registered (provenance gap,
        #: BUG-081 error-path observability; entries are distinct from legit 0.0).
        self._unbound_ticket_contexts: dict[int, str] = {}
        self._PENDING_CONTEXT_TTL_SEC: float = 3600.0
        self._PENDING_CONTEXT_MAX_ENTRIES: int = 64
        #: canonical entry-order-id probe; defaults to "unattributed" until the
        #: manager registers the TicketStateStore-backed view.
        self._entry_order_ids_probe: Callable[[int], str] = lambda ticket: ""

    # ------------------------------------------------------------------
    # Entry-context staging + lineage binding (BUG-081)
    # ------------------------------------------------------------------

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

    def bind_pending_entry_context(
        self,
        ticket: int,
        decision_order_id: str = "",
        *,
        entry_reasons: Any,
        entry_confidences: Any,
        entry_regimes: Any,
        entry_order_ids: Any,
        entry_expected_price: Any,
        entry_fill_latency_ms: Any,
        entry_setup_snapshots: Any,
    ) -> None:
        """Binds the staged entry context to a freshly observed ticket.

        (BUG-081) Resolves the context from the bounded registry keyed by the
        originating order/request id. Every ticket of a broker split-fill
        resolves the SAME immutable context (order_id, reason, confidence,
        regime, expected entry, dispatch clock, setup snapshot). The registry
        entry is removed only when the WHOLE fill family has been bound
        (idempotent family tracking via `_context_bound_tickets`), so a
        delayed sibling ticket never loses its provenance.

        When NO context was ever staged for the ticket, the ticket is marked in
        `_unbound_ticket_contexts` (distinct from a legitimate 0.0 confidence)
        with the reason -- never silently treated as a zero-confidence entry.

        The canonical per-ticket fields live in the TicketStateStore's views,
        which remain owned by the manager; they are passed explicitly so this
        component writes provenance without owning the ticket record store.
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
                live = self._tickets_view()
                # A family still open (tickets live) is the current dispatch.
                if any(t in live for t in family):
                    ctx = self._pending_context_registry.get(oid)
                    if ctx is not None:
                        break
            if ctx is None and self._pending_context_ts:
                newest = max(self._pending_context_ts, key=self._pending_context_ts.get)
                ctx = self._pending_context_registry.get(newest)
        if ctx is None:
            reason_gap = "NO_STAGED_CONTEXT"
        else:
            entry_reasons[ticket] = ctx.get("entry_reason", "PURE_AI") or "PURE_AI"
            entry_confidences[ticket] = float(ctx.get("ai_confidence", 0.0) or 0.0)
            entry_regimes[ticket] = str(ctx.get("market_regime", "") or "")
            entry_order_ids[ticket] = str(ctx.get("order_id", "") or decision_order_id)
            # PHASE 08 execution-quality evidence.
            entry_expected_price[ticket] = float(ctx.get("expected_entry", 0.0) or 0.0)
            # SETUP SNAPSHOT (2026-08-18): full chart-state fingerprint captured at
            # dispatch, carried to the closed-trade autopsy for setup attribution.
            entry_setup_snapshots[ticket] = dict(ctx.get("setup_snapshot", {}) or {})
            dispatch_mono = float(ctx.get("dispatch_monotonic", 0.0) or 0.0)
            if dispatch_mono > 0.0:
                entry_fill_latency_ms[ticket] = max(
                    0.0, (time.monotonic() - dispatch_mono) * 1000.0
                )
            bound = True
            # Idempotent family tracking: keep the context until EVERY ticket of
            # the fill family has been bound. The family is defined by the set of
            # tickets that ever resolved this order id; when this ticket is the
            # first of the family it stays registered so delayed siblings bind.
            oid = entry_order_ids.get(ticket) or decision_order_id or ""
            family = self._context_bound_tickets.setdefault(oid, set())
            family.add(ticket)
            logger.info(
                "[TRADE_LINEAGE] context_bound=true",
                extra={
                    "parent_execution_id": oid,
                    "child_ticket": ticket,
                    "family_size": len(family),
                },
            )
        if not bound:
            # Provenance gap: never silence missing context as legitimate 0.0.
            self._unbound_ticket_contexts[ticket] = reason_gap
            entry_reasons.setdefault(ticket, "PURE_AI")
            entry_order_ids.setdefault(ticket, decision_order_id)
            logger.warning(
                "[TRADE_LINEAGE] context_bound=false",
                extra={
                    "child_ticket": ticket,
                    "reason": reason_gap,
                    "decision_order_id": decision_order_id,
                },
            )

    def prune_bound_context(self, order_id: str) -> None:
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
        live = self._tickets_view()
        if any(t in live for t in family):
            return
        self._pending_context_registry.pop(order_id, None)
        self._pending_context_ts.pop(order_id, None)
        self._context_bound_tickets.pop(order_id, None)
        logger.info(
            "[TRADE_LINEAGE] context_pruned",
            extra={"parent_execution_id": order_id, "family_size": len(family)},
        )

    # ------------------------------------------------------------------
    # Churn lock (30s pending modification lock)
    # ------------------------------------------------------------------

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
                    "PENDING_ORDER_LOCKED: modification suppressed inside 30s lock "
                    "(ticket=%s age_sec=%.1f)",
                    ticket,
                    round(time_delta, 1),
                )
                return False

            if price_drift < (1.0 * atr):
                logger.debug(
                    "PENDING_ORDER_HELD: drift below 1.0x ATR (ticket=%s drift=%.2f required=%.2f)",
                    ticket,
                    round(price_drift, 2),
                    round(atr, 2),
                )
                return False

        self._last_mod_price[ticket] = price
        self._last_mod_time[ticket] = now
        return True

    # ------------------------------------------------------------------
    # Broker-verified cancellation
    # ------------------------------------------------------------------

    @staticmethod
    def pending_field(pending: Any, *names: str, default: Any = None) -> Any:
        """
        Reads a field from a pending order that may be either a dict (as returned by the
        live MT5 adapter via `orders_get`) or an object with attributes (as used by
        simulated/paper adapters). Returns `default` when no name resolves.
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

    def broker_state(self, ticket: int, symbol: str | None = None) -> str:
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
                        if int(self.pending_field(p, "ticket", "order_id") or 0) == int(ticket):
                            return "ACTIVE"
                    active_result = "GONE"
        except Exception as verify_err:
            query_error = True
            logger.warning(
                "[PENDING_ORDER] event=CANCEL_VERIFY error=orders_get_failed "
                "context=fallback_to_history ticket=%s error=%s",
                ticket,
                verify_err,
            )
        # Active-order query unavailable/errored: check history_orders_get for a
        # terminal state (CANCELED=2, PARTIAL=3, FILLED=4, REJECTED=5, EXPIRED=6)
        # which positively proves the order is done.
        hist_terminal = None  # None = no history evidence, True/False = terminal/active
        try:
            hist_fn = getattr(self.adapter, "get_history_orders", None)
            if hist_fn:
                from datetime import timedelta

                now = datetime.now(UTC)
                hist = hist_fn(now - timedelta(hours=1), now, symbol=symbol)
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
                "[PENDING_ORDER] event=CANCEL_VERIFY error=history_query_failed ticket=%s error=%s",
                ticket,
                hist_err,
            )
        if active_result == "ACTIVE" or hist_terminal is False:
            return "ACTIVE"
        if active_result == "GONE" or hist_terminal is True:
            return "GONE"
        if query_error:
            return "UNKNOWN"
        return "UNKNOWN"

    def emit_terminal_for_pending(
        self, ticket: int, state: Any, detail: str = "", at: datetime | None = None
    ) -> bool:
        """Emits the terminal outcome for the decision that placed `ticket`.

        The request_id is resolved from the staged entry context registry
        (`entry_order_ids[ticket]` is bound to the originating
        decision.request_id at context-bind time). Idempotent: the ledger
        refuses a second outcome for the same key, so repeated sweeps,
        retries or restart replays cannot duplicate the row.

        ``at`` (BUG-261): the tick/broker-domain timestamp for the
        outcome. When supplied it replaces the wall-clock fallback so
        the ledger's causality guard (outcome_timestamp >=
        decision_timestamp) cannot reject the outcome on host-behind
        skew. The caller supplies the current tick timestamp from the
        manage loop.
        """
        request_id = str(self._entry_order_ids_probe(ticket))
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
            outcome_timestamp=at,
        )
        if written:
            # The lifecycle is closed: drop the ephemeral cancel-reason note.
            self._pending_cancel_reasons.pop(ticket, None)
        return written

    def set_entry_order_ids_probe(self, probe: Callable[[int], str]) -> None:
        """Registers the canonical entry-order-id probe (manager's S5 view)."""
        self._entry_order_ids_probe = probe

    def cancel_pending_order_verified(
        self, ticket: int, symbol: str | None = None, at: datetime | None = None
    ) -> bool:
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
                "[PENDING_ORDER] event=CANCEL_REQUEST error=cancel_raised ticket=%s error=%s",
                ticket,
                cancel_err,
            )
            sent = False

        # Broker truth decides, not the send result.
        state = self.broker_state(ticket=ticket, symbol=symbol)
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
            from nexus_scalp.experience.lifecycle import DecisionLifecycle

            state_lifecycle = (
                DecisionLifecycle.EXPIRED_UNFILLED
                if "AGE" in self._pending_cancel_reasons.get(ticket, "")
                else DecisionLifecycle.CANCELED_UNFILLED
            )
            # BUG-261: tick-domain stamp — the caller must supply
            # current_tick.timestamp; None degrades to the ledger
            # clamp fallback (see terminal_outcome.py).
            self.emit_terminal_for_pending(ticket=ticket, state=state_lifecycle, at=at)
            self._pending_orders_setup_time.pop(ticket, None)
            try:
                self._refresh_cache(symbol=symbol)
            except Exception as refresh_err:
                logger.error(
                    "[PENDING_ORDER] event=CANCEL_CONFIRMED error=cache_refresh_failed "
                    "ticket=%s error=%s",
                    ticket,
                    refresh_err,
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
                self._refresh_cache(symbol=symbol)
            except Exception as refresh_err:
                logger.error(
                    "[PENDING_ORDER] event=CANCEL_CONFIRMED error=cache_refresh_failed "
                    "ticket=%s error=%s",
                    ticket,
                    refresh_err,
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
        self, ticket: int, symbol: str | None = None, max_attempts: int = 3, at: Any = None
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
            if self.cancel_pending_order_verified(ticket=ticket, symbol=symbol, at=at):
                break
            time.sleep(0.05)  # tiny backoff between bounded retries
        return attempts

    # ------------------------------------------------------------------
    # Reconciliation (broker wins)
    # ------------------------------------------------------------------

    def reconcile_pending_state(
        self,
        symbol: str | None = None,
        current_tick: TickData | None = None,
        live_tickets: dict[int, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Compares internal vs broker pending state and repairs the internal
        view so it reflects broker truth (broker wins).

        Returns a structured report:
          {"pending_internal": n, "pending_broker": m, "mismatch": bool,
           "repaired": bool, "broker_error": bool}
        """
        if live_tickets is None:
            live_tickets = self._tickets_view()
        internal_pendings = 0
        for info in live_tickets.values():
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
                "[EXECUTION_RECONCILIATION] event=MISMATCH error=broker_query_failed error=%s",
                rec_err,
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
            self._refresh_cache(symbol=symbol, current_tick=current_tick)
            repaired = True
        return {
            "pending_internal": internal_pendings,
            "pending_broker": broker_pendings,
            "mismatch": bool(mismatch),
            "repaired": repaired,
            "broker_error": broker_error,
        }

    # ------------------------------------------------------------------
    # Lifecycle guard (the per-pass manage loop)
    # ------------------------------------------------------------------

    def manage_pending_orders(
        self,
        symbol: str,
        current_tick: TickData,
        symbol_info: Any | None = None,
        atr: float = 1.50,
        max_pending_dist_atr_mult: float = 2.50,  # Increased from 1.20 to give limit orders breathing room
        audit: Any = None,
    ) -> None:
        """
        Pending order lifecycle guard with a hard 30-second churn lock.

        A pending limit order is NEVER cancelled/recreated unless BOTH hold:
          - time_since_placement > PENDING_ORDER_LOCK_SECONDS (30s), AND
          - price drift >= 1.0 x ATR.

        Stale-age expiry (>120s) still applies after the lock window, so an order that
        the market has walked away from is not left hanging forever.
        """
        audit = audit if audit is not None else self.audit
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
                order_type = self.pending_field(pending, "type", "order_type")
                price_open = float(
                    self.pending_field(pending, "price_open", "price", default=0.0) or 0.0
                )
                ticket = self.pending_field(pending, "ticket", "order_id")

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
                        "PENDING_ORDER_LOCKED: within 30s placement lock, no modification "
                        "allowed (ticket=%s age_sec=%.1f lock_sec=%s)",
                        ticket,
                        round(age, 1),
                        PENDING_ORDER_LOCK_SECONDS,
                    )
                    continue

                # Past the lock window, a re-quote additionally requires real drift.
                if dist < required_drift:
                    logger.debug(
                        "PENDING_ORDER_HELD: price drift below 1.0x ATR threshold "
                        "(ticket=%s drift=%.2f required_drift=%.2f)",
                        ticket,
                        round(dist, 2),
                        required_drift,
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
                    # BUG-261: thread the tick-domain timestamp so the
                    # terminal outcome cannot fall back to the host wall
                    # clock (ledger CAUSALITY_REJECTED on host-behind skew).
                    cancelled_ok = self.cancel_pending_order_verified(
                        ticket=ticket, symbol=symbol, at=current_tick.timestamp
                    )
                    if cancelled_ok:
                        logger.info(
                            f"[CANCEL TRACE] PENDING ORDER CANCELLED: Ticket {ticket}. "
                            f"Reason: {cancel_reason}. Max Allowed Dist: ${max_allowed_dist:.2f}"
                        )
                        # Audit cancellation
                        audit.log_order(
                            ticket=ticket,
                            order_id=f"cancel_{ticket}",
                            symbol=symbol,
                            action="Expired pending order"
                            if "AGE" in cancel_reason
                            else "Cancelled order",
                            price=price_open,
                            stop_loss=float(
                                self.pending_field(pending, "sl", "stop_loss", default=0.0) or 0.0
                            ),
                            take_profit=float(
                                self.pending_field(pending, "tp", "take_profit", default=0.0) or 0.0
                            ),
                            volume=float(
                                self.pending_field(pending, "volume", default=0.01) or 0.01
                            ),
                            reason=cancel_reason,
                            latency=0.01,
                            execution_mode="PREDICTIVE_LIMIT",
                        )
        except Exception as err:
            logger.error("Failed to manage dynamic pending orders: %s", err)

    # ------------------------------------------------------------------
    # Teardown hook (called from the manager's cleanup bundle)
    # ------------------------------------------------------------------

    def cancel_reason(self, ticket: int) -> str:
        """Last cancel reason for a pending ticket ("" when none). BUG-140."""
        return self._pending_cancel_reasons.get(ticket, "")

    def note_cancel_reason(self, ticket: int, reason: str) -> None:
        """Records WHY a pending is being cancelled (BUG-140 classification)."""
        self._pending_cancel_reasons[ticket] = reason

    def drop_ticket(self, ticket: int) -> None:
        """Releases per-ticket pending state (part of the cleanup bundle)."""
        self._pending_orders_setup_time.pop(ticket, None)
