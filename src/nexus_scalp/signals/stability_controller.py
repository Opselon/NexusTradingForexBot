"""Decision Stability Controller (TASK-TEMPORAL-01) — causal, O(1), stateful.

RESEARCH CANDIDATE ONLY — never ACTIVE/CHAMPION without governance.

WHY THIS EXISTS
---------------
STEP-01/02 forensics proved the raw 70D model argmax flips on nearly every
M1 bar (median flip interval 60 s, 597 flips / 4000 events, max decision
margin 0.27). The controller adds CONFIRMATION + HYSTERESIS downstream of
the RAW model decision. It NEVER alters the raw model output — it only
decides WHICH raw direction (if any) becomes the STABLE direction.

DESIGN (brief 26-32)
--------------------
State machine:
    NONE -> BUY_CANDIDATE -> BUY_CONFIRMED   (mirror for SELL)

Transitions:
  - to CANDIDATE(d): raw direction d AND margin >= ENTRY_MIN_MARGIN.
  - to CONFIRMED(d): candidate held for CONFIRM_BARS consecutive decisions
    of the same raw direction, OR strong structural confirmation
    (liquidity sweep/level evidence) in the same direction, OR
    margin >= HARD_REVERSAL_MARGIN (hard reversal).
  - WEAK OPPOSITE: an opposite raw direction with margin < ENTRY_MIN_MARGIN
    does NOT flip a confirmed direction; it only starts a candidate when
    the current direction is NONE.
  - STRONG OPPOSITE: margin >= HARD_REVERSAL_MARGIN + structural
    confirmation -> immediate HARD_REVERSAL (confirms the opposite).
  - max_candidate_age: a candidate that neither confirms nor is replaced
    after MAX_CANDIDATE_AGE decisions resets to NONE (brief 31).

Entry vs exit stability (brief 29): ENTRY_CONFIRM_BARS >= EXIT_CONFIRM_BARS
(the controller exposes both; exit paths use the faster confirmation).

Reset (brief 32): reset() clears all state — called on symbol change, model
change, schema change, runtime restart, timeframe change.

TELEMETRY (brief 36): every CONFIRMED change emits a SIGNAL_STABILITY event
(captured in a bounded deque); micro-flips inside a candidate never emit.

PROPERTIES: causal (uses only past raw decisions), O(1) (constant memory),
stateful, deterministic (same raw sequence -> same stable sequence),
bounded (bounded event deque, max age cap).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class StableDirection(StrEnum):
    NONE = "NONE"
    BUY = "BUY"
    SELL = "SELL"


class StabilityState(StrEnum):
    STABLE = "STABLE"
    NOISY = "NOISY"
    CHANGING = "CHANGING"
    REVERSING = "REVERSING"
    CONFIRMED = "CONFIRMED"


@dataclass(frozen=True)
class StabilityEvent:
    """One [SIGNAL_STABILITY] event (only on CONFIRMED changes)."""

    timestamp: str
    previous: str
    new_direction: str
    pbuy: float
    psell: float
    margin: float
    candidate_age: int
    confirmation_reason: str


@dataclass
class StabilityDecision:
    """Output of one decision step."""

    raw_direction: str
    stable_direction: str
    state: StabilityState
    pbuy: float
    psell: float
    margin: float
    candidate_direction: str
    candidate_age: int
    confirmation_progress: int
    required_confirmation: int
    last_confirmed_direction: str
    event: StabilityEvent | None = None


class DecisionStabilityController:
    """Causal decision confirmation/hysteresis controller (research)."""

    def __init__(
        self,
        entry_min_margin: float = 0.05,
        hard_reversal_margin: float = 0.20,
        entry_confirm_bars: int = 2,
        exit_confirm_bars: int = 1,
        max_candidate_age: int = 12,
        max_events: int = 200,
    ) -> None:
        self.entry_min_margin = entry_min_margin
        self.hard_reversal_margin = hard_reversal_margin
        self.entry_confirm_bars = max(1, entry_confirm_bars)
        self.exit_confirm_bars = max(1, exit_confirm_bars)
        self.max_candidate_age = max(1, max_candidate_age)
        self._events: deque[StabilityEvent] = deque(maxlen=max_events)

        self._stable: StableDirection = StableDirection.NONE
        self._candidate: StableDirection = StableDirection.NONE
        self._candidate_age: int = 0
        self._confirm_streak: int = 0
        self._last_confirmed: StableDirection = StableDirection.NONE

    # ------------------------------------------------------------------
    # core step
    # ------------------------------------------------------------------
    def decide(
        self,
        probabilities: list[float] | tuple[float, ...],
        *,
        pbuy: float | None = None,
        psell: float | None = None,
        timestamp: str | None = None,
        structural_buy: bool = False,
        structural_sell: bool = False,
        position_open: bool = False,
    ) -> StabilityDecision:
        """One decision step from raw model probabilities.

        Args:
            probabilities: raw 3/4-class probs (index 1 = BUY, 2 = SELL).
            pbuy/psell: optional explicit buy/sell probs (fall back to
                probabilities[1]/[2]).
            structural_buy/sell: canonical structural confirmation (e.g.
                liquidity sweep / level break) from the existing signal
                path — never an invented signal.
            position_open: True when a position is open — the controller
                then uses the FASTER exit confirmation (brief 29:
                entry and exit have different costs of delay).
        """
        p_buy = float(pbuy) if pbuy is not None else float(probabilities[1])
        p_sell = float(psell) if psell is not None else float(probabilities[2])
        margin = abs(p_buy - p_sell)
        raw_dir = (
            StableDirection.BUY
            if p_buy > p_sell
            else (StableDirection.SELL if p_sell > p_buy else StableDirection.NONE)
        )
        ts = timestamp or datetime.now(UTC).isoformat()

        event: StabilityEvent | None = None
        old_stable = self._stable

        # ---- candidate progression ------------------------------------
        if raw_dir == StableDirection.NONE:
            self._confirm_streak = 0
        elif self._candidate == StableDirection.NONE:
            # no candidate: a strong-enough raw direction starts one
            if margin >= self.entry_min_margin or structural_buy or structural_sell:
                self._candidate = raw_dir
                self._candidate_age = 0
                self._confirm_streak = 1
        elif raw_dir == self._candidate:
            self._confirm_streak += 1
        # opposite raw direction while a candidate is held
        elif margin >= self.hard_reversal_margin:
            # HARD_REVERSAL: strong margin flips the candidate
            self._candidate = raw_dir
            self._candidate_age = 0
            self._confirm_streak = 1
        else:
            # weak opposite: does not flip; counts against the candidate
            self._confirm_streak = 0
            self._candidate_age += 1

        # ---- candidate -> confirmed ------------------------------------
        confirm_bars = self.entry_confirm_bars
        if position_open:
            # an OPEN position is governed by the (faster) exit confirmation
            confirm_bars = self.exit_confirm_bars

        if self._candidate != StableDirection.NONE:
            self._candidate_age += 1
            struct_ok = (structural_buy and self._candidate == StableDirection.BUY) or (
                structural_sell and self._candidate == StableDirection.SELL
            )
            if (
                self._confirm_streak >= confirm_bars
                or struct_ok
                or margin >= self.hard_reversal_margin
            ):
                if self._stable != self._candidate:
                    self._stable = self._candidate
                    self._last_confirmed = self._candidate
                    event = StabilityEvent(
                        timestamp=ts,
                        previous=old_stable.value,
                        new_direction=self._candidate.value,
                        pbuy=round(p_buy, 6),
                        psell=round(p_sell, 6),
                        margin=round(margin, 6),
                        candidate_age=self._candidate_age,
                        confirmation_reason=(
                            "STRUCTURAL"
                            if struct_ok
                            else "HARD_REVERSAL"
                            if margin >= self.hard_reversal_margin
                            else "CONSECUTIVE_CONFIRMATION"
                        ),
                    )
                    self._events.append(event)
                self._candidate = StableDirection.NONE
                self._candidate_age = 0
                self._confirm_streak = 0

        # ---- candidate age expiry (brief 31) ---------------------------
        if self._candidate != StableDirection.NONE and self._candidate_age > self.max_candidate_age:
            self._candidate = StableDirection.NONE
            self._candidate_age = 0
            self._confirm_streak = 0

        # ---- stability state (brief 33) --------------------------------
        if self._stable == StableDirection.NONE:
            state = (
                StabilityState.NOISY
                if self._candidate != StableDirection.NONE
                else StabilityState.STABLE
            )
        elif raw_dir != self._stable.value and margin >= self.hard_reversal_margin:
            state = StabilityState.REVERSING
        elif raw_dir != self._stable.value:
            state = StabilityState.CHANGING
        else:
            state = StabilityState.CONFIRMED

        progress = min(self._confirm_streak, confirm_bars)
        return StabilityDecision(
            raw_direction=raw_dir.value,
            stable_direction=self._stable.value,
            state=state,
            pbuy=round(p_buy, 6),
            psell=round(p_sell, 6),
            margin=round(margin, 6),
            candidate_direction=self._candidate.value,
            candidate_age=self._candidate_age,
            confirmation_progress=progress,
            required_confirmation=confirm_bars,
            last_confirmed_direction=self._last_confirmed.value,
            event=event,
        )

    # ------------------------------------------------------------------
    # state management
    # ------------------------------------------------------------------
    @property
    def events(self) -> list[StabilityEvent]:
        return list(self._events)

    def last_event(self) -> StabilityEvent | None:
        return self._events[-1] if self._events else None

    def reset(self) -> None:
        """Full state reset (symbol/model/schema/timeframe/restart)."""
        self._stable = StableDirection.NONE
        self._candidate = StableDirection.NONE
        self._candidate_age = 0
        self._confirm_streak = 0
        self._last_confirmed = StableDirection.NONE
        self._events.clear()
