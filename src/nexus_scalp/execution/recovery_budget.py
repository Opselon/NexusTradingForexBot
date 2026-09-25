"""Recovery-budget ledger (Agent-5 P0 seam S3).

Extracted VERBATIM from execution/order_manager.py (behavior-preserving
decomposition). Owns the six per-ticket recovery-budget dicts and the two
pure mutations (allocation + evaluation). NO broker I/O, NO order placement,
NO risk decisions — state/control infrastructure only.

Allocation rule (immutable per ticket): budget = min(initial_risk *
recovery_budget_pct_of_r, remaining risk to entry); evaluated lazily on the
first negative-PnL management pass and never re-allocated (I1).

Evaluation rule: consumed = max(0, |current_loss| - initial_loss);
remaining = max(0, initial - consumed)  => remaining can never go negative
(I2); exhaustion is a PURE recompute from immutable initial values — the
enforcement (immediate close) lives in the caller (I4: failed recovery never
silently becomes success because the caller closes on the exhausted verdict).

Lifecycle: entries are removed by the manager's per-ticket cleanup bundle
(_cleanup_ticket_state) via drop_ticket().

USED BY: execution/order_manager.py (facade methods delegate; compatibility
properties expose the live dicts under the historical attribute names).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class RecoveryBudgetLedger:
    """Owns the six per-ticket recovery-budget dicts (single source of truth)."""

    def __init__(self) -> None:
        self.recovery_budget_initial: dict[int, float] = {}
        self.recovery_budget_remaining: dict[int, float] = {}
        self.recovery_budget_consumed: dict[int, float] = {}
        self.recovery_initial_loss: dict[int, float] = {}
        self.recovery_entry_times: dict[int, datetime] = {}
        self.recovery_horizons: dict[int, float] = {}

    def is_allocated(self, ticket: int) -> bool:
        return ticket in self.recovery_budget_initial

    def allocate(
        self,
        ticket: int,
        *,
        initial_risk_usd: float,
        current_pnl_usd: float,
        confidence_factor: float,
        atr: float,
        trend_strength: float,
        now: datetime,
        algo_config: Any,
    ) -> float:
        """Initializes the immutable budget + dynamic horizon (verbatim rules).

        Returns the allocated budget. No-op (returns existing budget) when the
        ticket is already allocated (I1).
        """
        if ticket in self.recovery_budget_initial:
            return self.recovery_budget_initial[ticket]

        if initial_risk_usd <= 0.0:
            initial_risk_usd = atr * 1.50 * 100.0 * 0.10  # fallback rough estimate

        # Recovery Budget = % of Initial Risk, clamped by actual remaining risk to entry
        pct_r = getattr(algo_config, "recovery_budget_pct_of_r", 0.50)
        budget = initial_risk_usd * pct_r
        current_loss_abs = abs(current_pnl_usd)
        remaining_risk = max(0.0, initial_risk_usd - current_loss_abs)
        budget = min(budget, remaining_risk)

        self.recovery_budget_initial[ticket] = budget
        self.recovery_budget_remaining[ticket] = budget
        self.recovery_budget_consumed[ticket] = 0.0
        self.recovery_initial_loss[ticket] = current_loss_abs
        self.recovery_entry_times[ticket] = now

        # Dynamic, bounded recovery horizon (Requirement 15)
        default_horizon = getattr(algo_config, "default_recovery_horizon_sec", 180.0)
        min_horizon = getattr(algo_config, "min_recovery_horizon_sec", 30.0)
        max_horizon = getattr(algo_config, "max_recovery_horizon_sec", 600.0)

        # Scale based on ATR, confidence, and trend strength
        base_hor = default_horizon
        atr_n = max(atr, 0.50)
        base_hor /= atr_n / 1.50  # high volatility -> shorter horizon
        base_hor *= confidence_factor + 0.5  # high confidence -> longer horizon
        if trend_strength < -0.20:
            base_hor *= 0.70  # strong adverse trend -> shorter horizon

        horizon = max(min_horizon, min(max_horizon, base_hor))
        self.recovery_horizons[ticket] = horizon
        return budget

    def evaluate_exhaustion(
        self, ticket: int, current_pnl_usd: float, now: datetime
    ) -> tuple[bool, str]:
        """Evaluates the immutable recovery budget and dynamic time horizon.

        Returns (is_exhausted, reason) — verbatim semantics.
        """
        if ticket not in self.recovery_budget_initial:
            return False, ""

        # 1. Budget check
        initial_loss = self.recovery_initial_loss.get(ticket, 0.0)
        current_loss = abs(current_pnl_usd) if current_pnl_usd < 0.0 else 0.0

        # Budget is consumed if drawdown widens from recovery entry point
        consumed = max(0.0, current_loss - initial_loss)
        self.recovery_budget_consumed[ticket] = consumed

        initial_budget = self.recovery_budget_initial.get(ticket, 0.0)
        remaining = max(0.0, initial_budget - consumed)
        self.recovery_budget_remaining[ticket] = remaining

        if remaining <= 0.0:
            return (
                True,
                f"RECOVERY_BUDGET_EXHAUSTED (initial=${initial_budget:.2f}, consumed=${consumed:.2f})",
            )

        # 2. Time Horizon check (dynamic but strictly bounded, no moving goalposts)
        entry_time = self.recovery_entry_times.get(ticket)
        horizon = self.recovery_horizons.get(ticket, 180.0)
        if entry_time is not None:
            elapsed = (now - entry_time).total_seconds()
            if elapsed > horizon:
                return True, f"RECOVERY_TIME_EXHAUSTED ({elapsed:.1f}s > {horizon:.1f}s)"

        return False, ""

    def remaining(self, ticket: int, default: float = 1.0) -> float:
        """Read-only view used by the position state machine."""
        return self.recovery_budget_remaining.get(ticket, default)

    def drop_ticket(self, ticket: int) -> None:
        """Releases all six per-ticket entries (cleanup-bundle participation)."""
        self.recovery_budget_initial.pop(ticket, None)
        self.recovery_budget_remaining.pop(ticket, None)
        self.recovery_budget_consumed.pop(ticket, None)
        self.recovery_initial_loss.pop(ticket, None)
        self.recovery_entry_times.pop(ticket, None)
        self.recovery_horizons.pop(ticket, None)
