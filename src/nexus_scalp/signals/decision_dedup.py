"""Semantic decision identity + state-aware replay dedup (TASK-DEDUP-REPLAY-001).

ROOT CAUSE (forensic 2026-10-01, XAUUSD LIVE): after an MT5 IPC reconnect the
``symbol_info_tick`` poll re-serves the broker's last cached quote. Bid/ask can
drift while the *effective* decision is unchanged, so the engine's 3-field
duplicate guard (ts+bid+ask) does not fire, the full pipeline runs, and
``SignalPolicy._evaluate_duplicate_tick`` re-surfaces ``_last_real_proposal``
with a FRESH ``request_id`` and ``decision_stage="DEDUP_GATE"``. The executor's
DEDUP_GATE boundary then correctly refuses it — 3,476
``ORDER_MUTATION_SUPPRESSED`` lines in one hour. The net was never wrong; the
upstream replay/amplification was.

This module owns the two pieces that stop the amplification at the source:

* :func:`decision_fingerprint` — a canonical ACTIONABLE-INTENT identity derived
  from a proposal. Two proposals are the *same business decision* iff their
  order-mutation semantics agree: symbol, action/side, entry (limit/stop/market)
  price, stop-loss, take-profit, volume, and the strategy/model revision that
  produced them. ``request_id`` / ``execution_id`` / timestamps are deliberately
  EXCLUDED — they differ on every replay while the intent is identical.
* :class:`ReplayDecisionDedup` — an in-memory, per-decision-identity guard with
  explicit state-aware invalidation. A repeated *equivalent* actionable proposal
  is only suppressed while the broker/order state that made it actionable is
  unchanged; a cancel, a fill, a position change, a strategy/model revision
  change, or any change in the order-mutation parameters re-arms it.

CONTRACT — what this is NOT:

* It is NOT a request-id idempotency layer (dispatch already has one,
  ``_processed_orders`` + durable ``audit_executions`` UNIQUE identity).
* It is NOT on the tick hot path as any I/O: everything is in-memory and O(1)
  (one dict lookup + a bounded bookkeeping stamp). INV-001 is preserved.
* It NEVER weakens the executor's DEDUP_GATE net (Parts 1/5). It runs in the
  policy, *upstream* of proposal emission, and only ever downgrades a replayed
  duplicate to NO_TRADE — the same direction the net already forces.
* It NEVER suppresses a genuinely new decision: the fingerprint is a strict
  function of the order-mutation parameters, so any parameter change (entry
  2650.50 -> 2651.00, a new SL/TP, a revised model) is a different identity.

Design notes:

* ``NO_TRADE`` / ``WAIT`` proposals are never fingerprinted or tracked — they
  carry no order mutation, so they cannot duplicate by definition.
* Prices are quantized to the broker's tick size before comparison. Two
  proposals whose entries differ by 1e-12 from float noise are the same intent;
  two differing by one tick are not. Quantization makes the fingerprint robust
  to float noise WITHOUT collapsing real price changes (the tick size is the
  smallest price change the broker can quote).
* Volume ``None`` and ``0.0`` are canonicalized to a single bucket: the proposal
  layer has no volume at all (sizing happens later in RiskEngine), and the
  intent is unaffected by that distinction.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

#: Price quantization resolution when no broker tick size is known (XAUUSD has
#: 3 digits => tick size 0.001; 1e-6 is far below any real tick and only exists
#: to kill float noise). Never collapse real price changes.
_DEFAULT_PRICE_EPSILON: float = 1e-6

#: The fingerprint version. Bumped only if the identity composition changes in
#: an incompatible way (a new field is additive; a removed/renamed one is not).
_FINGERPRINT_VERSION: int = 1

#: Non-actionable actions. These never reach the dedup state machine because
#: they mutate no broker state, so "duplicate" is meaningless for them.
_NON_ACTIONABLE_STAGES: frozenset[str] = frozenset(
    {
        "NO_TRADE",
        "WAIT",
    }
)


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def _is_actionable(proposal: Any) -> bool:
    """True when the proposal describes an actual order mutation.

    ``NO_TRADE``/``WAIT`` abstentions and anything the domain already marked as
    non-executable (DEDUP_GATE replay, SHADOW downgrade, blocked proposals) are
    not actionable: they never reach a broker and so cannot be a duplicate of a
    prior *intent*. The DEDUP_GATE check is what keeps this guard from ever
    fingerprinting the very replay artifact it exists to stop.
    """
    try:
        action = proposal.action
    except AttributeError:
        return False
    action_val = getattr(action, "value", str(action))
    if action_val in _NON_ACTIONABLE_STAGES:
        return False
    # A replayed/already-blocked/shadowed proposal is not a fresh intent.
    stage = str(getattr(proposal, "decision_stage", "") or "")
    if stage in {"DEDUP_GATE", "SHADOW_OBSERVATION_ONLY"} or stage.startswith("BLOCKED"):
        return False
    blocked_by = str(getattr(proposal, "blocked_by", "") or "")
    if blocked_by:
        return False
    return True


def _action_side(action: Any) -> str:
    """Canonical side+order-kind key: BUY_LIMIT / SELL_MARKET / CLOSE_POSITION ..."""
    return str(getattr(action, "value", str(action))).upper()


def _quantize(price: float, tick_size: float | None) -> float:
    """Snap a price onto the broker's tick grid (or to a sub-tick noise floor).

    ``math.floor(p/tick + 0.5) * tick`` is the symmetric round-half-up to tick.
    With a 0.001 XAUUSD tick, 2650.4999999 and 2650.5000001 both snap to
    2650.500 while 2651.000 stays distinct — float noise dies, real levels live.
    """
    if not math.isfinite(price) or price <= 0.0:
        return price
    step = float(tick_size) if (tick_size and float(tick_size) > 0.0) else _DEFAULT_PRICE_EPSILON
    return math.floor(price / step + 0.5) * step


def decision_fingerprint(
    proposal: Any,
    *,
    tick_size: float | None = None,
    strategy_revision: str | None = None,
) -> str | None:
    """Canonical actionable-intent identity for a proposal.

    Returns ``None`` for non-actionable proposals (nothing to dedup).

    Identity composition (all order-mutation semantics, no identifiers):

    * ``symbol``
    * ``action`` (side + order kind: BUY_LIMIT / SELL_MARKET / ...)
    * quantized ``proposed_entry`` (the limit/stop/market level)
    * quantized ``stop_loss`` / ``take_profit``
    * ``ticket`` (the position a lifecycle action acts on — a CLOSE on ticket A
      is NOT the same intent as a CLOSE on ticket B)
    * canonicalized ``volume`` (None -> 0.0: the proposal layer carries no size)
    * ``reversal_action`` when present (a close-then-flip is a different intent
      from the close alone)
    * ``strategy_revision`` — the model/strategy revision that produced the
      decision. A revision change is a genuine decision change: the same entry
      from a different model is a different business decision.

    Deliberately EXCLUDED (they differ per replay while intent is identical):
    ``request_id``, ``execution_id``, ``generated_at``/timestamps,
    ``confidence`` (a probability, not an order parameter), correlation ids.
    """
    if not _is_actionable(proposal):
        return None

    symbol = str(getattr(proposal, "symbol", "") or "").upper()
    side = _action_side(getattr(proposal, "action", None))
    entry = _quantize(float(getattr(proposal, "proposed_entry", 0.0) or 0.0), tick_size)
    sl = _quantize(float(getattr(proposal, "stop_loss", 0.0) or 0.0), tick_size)
    tp = _quantize(float(getattr(proposal, "take_profit", 0.0) or 0.0), tick_size)
    ticket = int(getattr(proposal, "ticket", 0) or 0)
    volume_raw = getattr(proposal, "volume", None)
    volume = 0.0 if volume_raw is None else float(volume_raw or 0.0)
    reversal = str(getattr(getattr(proposal, "reversal_action", None), "value", "") or "").upper()
    revision = str(strategy_revision or _DEFAULT_REVISION)

    parts = [
        f"v{_FINGERPRINT_VERSION}",
        symbol,
        side,
        f"E={entry:.10f}",
        f"S={sl:.10f}",
        f"T={tp:.10f}",
        f"TICKET={ticket}",
        f"V={volume:.8f}",
        f"REV={revision}",
    ]
    if reversal:
        parts.append(f"FLIP={reversal}")
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


#: Revision used when the caller supplies none. Absent a revision, proposals are
#: still distinguished by every other parameter, so dedup stays sound; the
#: revision simply adds model-change as a first-class re-arm trigger.
_DEFAULT_REVISION: str = "UNSPECIFIED"


# ---------------------------------------------------------------------------
# State-aware replay dedup
# ---------------------------------------------------------------------------


@dataclass
class _DedupEntry:
    """Bookkeeping for one currently-suppressed decision identity."""

    fingerprint: str
    first_proposal: Any
    suppressed_count: int = 0
    last_suppressed_at: float = 0.0
    #: The broker/order-state signature captured when the intent was first
    #: accepted. Any later change in this signature invalidates the suppression
    #: (Part 4): a cancel/fill/position change re-arms the decision.
    broker_state_signature: str = ""

    def __post_init__(self) -> None:
        if self.last_suppressed_at == 0.0:
            import time

            self.last_suppressed_at = time.monotonic()


@dataclass
class DedupVerdict:
    """Outcome of a replay-dedup check.

    Attributes:
        duplicate: True when the proposal is a repeated *equivalent* intent that
            must be downgraded to NO_TRADE upstream.
        fingerprint: the decision identity (None only for non-actionable input).
        reason: machine-readable suppression reason (the NSE semantics contract
            is reused: ``DEDUP_REPLAY`` for a replayed duplicate,
            ``DEDUP_REPLAY_STALE`` when the entry aged past its max lifetime).
        suppressed_count: how many times this identity has been suppressed so
            far (for the periodic summary telemetry).
    """

    duplicate: bool
    fingerprint: str | None
    reason: str | None = None
    suppressed_count: int = 0


class ReplayDecisionDedup:
    """In-memory, state-aware dedup of repeated actionable decisions.

    The hot path is ``check(proposal)``: one fingerprint computation + one dict
    lookup. No I/O, no broker query, no DB round-trip (INV-001).

    Suppression is *state-aware* (Part 4): an identity is only suppressed while
    the broker/order state that made it actionable is unchanged. The signature
    is derived from state the policy already receives (open/pending position
    counts + live tickets), so no extra broker RPC is added; the caller supplies
    it from data already on hand. ``order_manager`` is accepted for the live
    path and read through its existing cheap accessors (``get_active_live_tickets``
    returns a copy of the in-memory ticket cache — no broker call).
    """

    #: Suppression entries are re-validated if they have not been touched for
    #: this many seconds. Bounds stale suppression without a timer-driven sweep:
    #: a decision identity that has not repeated in this window is forgotten, so
    #: the same intent naturally re-arms after a quiet period.
    _max_idle_sec: float = 300.0

    def __init__(self, *, max_idle_sec: float | None = None) -> None:
        self._entries: dict[str, _DedupEntry] = {}
        self._lock = threading.RLock()
        if max_idle_sec is not None and float(max_idle_sec) > 0.0:
            self._max_idle_sec = float(max_idle_sec)
        #: Total duplicates suppressed (for periodic summary telemetry).
        self.total_suppressed: int = 0
        #: Per-reason counters (telemetry keeps the classes distinguishable).
        self.reason_counts: dict[str, int] = {}

    # -- public API -------------------------------------------------------

    def check(
        self,
        proposal: Any,
        *,
        tick_size: float | None = None,
        strategy_revision: str | None = None,
        order_manager: Any = None,
    ) -> DedupVerdict:
        """Classify ``proposal``: fresh, or a repeated equivalent intent.

        Non-actionable proposals pass straight through (``duplicate=False``).
        """
        fingerprint = decision_fingerprint(
            proposal,
            tick_size=tick_size,
            strategy_revision=strategy_revision,
        )
        if fingerprint is None:
            return DedupVerdict(duplicate=False, fingerprint=None)

        signature = self._broker_state_signature(order_manager)

        with self._lock:
            entry = self._entries.get(fingerprint)
            if entry is None:
                # Fresh actionable intent: remember it, suppress nothing.
                self._entries[fingerprint] = _DedupEntry(
                    fingerprint=fingerprint,
                    first_proposal=proposal,
                    broker_state_signature=signature,
                )
                return DedupVerdict(duplicate=False, fingerprint=fingerprint)

            # State-aware invalidation (Part 4).
            if entry.broker_state_signature != signature:
                # The broker/order state changed since this intent was accepted:
                # the proposal may legitimately be actionable again. Re-arm.
                entry.broker_state_signature = signature
                entry.first_proposal = proposal
                entry.suppressed_count = 0
                return DedupVerdict(duplicate=False, fingerprint=fingerprint)

            idle = time.monotonic() - entry.last_suppressed_at
            if idle > self._max_idle_sec:
                # Stale suppression: the decision has not repeated in a while.
                # Forget it and let this proposal through as fresh.
                self._entries[fingerprint] = _DedupEntry(
                    fingerprint=fingerprint,
                    first_proposal=proposal,
                    broker_state_signature=signature,
                )
                return DedupVerdict(duplicate=False, fingerprint=fingerprint)

            # Same intent, same broker state, recent: this is the replay.
            entry.suppressed_count += 1
            entry.last_suppressed_at = time.monotonic()
            self.total_suppressed += 1
            reason = "DEDUP_REPLAY"
            self.reason_counts[reason] = self.reason_counts.get(reason, 0) + 1
            return DedupVerdict(
                duplicate=True,
                fingerprint=fingerprint,
                reason=reason,
                suppressed_count=entry.suppressed_count,
            )

    def forget(self, fingerprint: str | None) -> None:
        """Explicitly re-arm one decision identity (e.g. after a cancel)."""
        if not fingerprint:
            return
        with self._lock:
            self._entries.pop(fingerprint, None)

    def reset(self) -> None:
        """Drop all suppression state (feed reconnect, engine restart)."""
        with self._lock:
            self._entries.clear()
            self.total_suppressed = 0
            self.reason_counts.clear()

    def snapshot(self) -> dict[str, Any]:
        """Bounded read of current suppression state (diagnostics/UI only)."""
        with self._lock:
            return {
                "active_identities": len(self._entries),
                "total_suppressed": self.total_suppressed,
                "reason_counts": dict(self.reason_counts),
                "max_idle_sec": self._max_idle_sec,
            }

    # -- internals --------------------------------------------------------

    @staticmethod
    def _broker_state_signature(order_manager: Any) -> str:
        """Cheap broker/order-state signature from state already in memory.

        Purpose: detect that the broker/order world changed so a repeated
        decision can become actionable again (Part 4). Sources (all in-memory
        accessors, no broker RPC):

        * ``order_manager.get_active_live_tickets()`` — the live tickets cache
          (a copy of the in-memory map; a fill/cancel/new-position changes it).

        Missing/absent order manager => ``""``: dedup still works on the
        decision-identity axis alone; only the state-aware re-arm is degraded.
        """
        if order_manager is None:
            return ""
        try:
            tickets = order_manager.get_active_live_tickets()
        except Exception:
            return ""
        parts: list[str] = []
        for t in tickets or []:
            try:
                parts.append(f"{int(t.get('ticket', 0) or 0)}:{t.get('type', '') or ''!s}")
            except Exception:
                continue
        return ",".join(sorted(parts))
