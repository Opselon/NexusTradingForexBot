"""Evidence-driven profit-protection circuit breakers (mission P0, items 7B/7C/7D).

Scope and authority (INV-003 — RiskEngine stays authoritative):
    This module computes breaker STATE ONLY. It never touches the broker,
    never closes positions, and never switches execution modes. The only
    effect of a tripped breaker is that RiskEngine.evaluate_proposal rejects
    NEW entries while the breaker is active — open-position protective
    management (SL/TP/trailing) is unaffected, by design (halt new entries,
    do not blindly liquidate).

Evidence basis for the shipped defaults (measured 2026-09-07 from the
canonical audit DB, artifacts/audit.db, audit_broker_trades, 3879 closed
XAUUSD trades 2026-07-15..2026-09-04, broker truth, magic 888101 + house):
    - Worst realized day:      -$32,842.89 (2026-07-30)
    - Worst realized week:     -$52,427.72 (2026-W31)
    - Consecutive-loss streaks: max 105; streaks >= 8 losses occurred 21
      times; >= 13 losses occurred 7 times.
    The defaults below are therefore CONFIG-DRIVEN with measured context,
    not universal truths: an operator may and should tune them per account.
    Daily/weekly budgets are expressed as % of the period-start equity so
    they scale with the account.

Loss-budget semantics (7B/7C):
    Daily budget   = (period_start_equity - equity) / period_start_equity
    measured on every evaluate_proposal call (equity-based, includes open
    floating PnL — a conservative, failure-early measure). Periods are UTC
    calendar day and ISO week; a new period re-arms the breaker
    automatically. No trade stream is required for budgets.

Consecutive-loss cooldown (7D):
    Requires an explicit outcome feed: record_trade_result(net_pnl_usd,
    closed_at) — called by the composition root when a trade closes.
    A streak of `consecutive_loss_cooldown` adverse outcomes arms a timed
    cooldown; any non-adverse outcome resets the streak. The feed is
    OPTIONAL until wired: an un-fed breaker reports cooldown as
    INSUFFICIENT_DATA instead of fabricating one.

Purity: no DB, no adapter, no I/O. Deterministic given its inputs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.risk.circuit_breakers")


class CircuitBreakerConfig(BaseModel):
    """Profit-protection budgets (mission 7B/7C/7D). All ranges validated."""

    #: Daily loss budget as % of day-start equity (mission 7B). Default 2.0
    #: mirrors the account-drawdown family already in RiskConfig (2.0%).
    daily_loss_budget_pct: float = Field(default=2.0, gt=0.0, le=100.0)
    #: Weekly loss budget as % of week-start equity (mission 7C).
    weekly_loss_budget_pct: float = Field(default=5.0, gt=0.0, le=100.0)
    #: Consecutive adverse outcomes before a timed cooldown (mission 7D).
    #: Default 8: measured streaks >= 8 occurred 21x in 3879 trades —
    #: frequent enough to matter, rare enough not to fire on noise.
    consecutive_loss_cooldown: int = Field(default=8, ge=2, le=1000)
    #: Cooldown duration once armed (mission 7D: cooldown, not halt).
    cooldown_duration_sec: float = Field(default=4 * 3600.0, gt=0.0, le=7 * 86400.0)
    #: Equity floor below which budgets cannot be evaluated honestly.
    min_equity: float = Field(default=1.0, gt=0.0)


@dataclass
class BreakerSnapshot:
    """Serializable breaker state for digest/telemetry/API consumers."""

    allowed: bool
    level: str  # NORMAL | DAILY_HALT | WEEKLY_HALT | COOLDOWN | INSUFFICIENT_DATA
    reason: str
    until: str | None  # ISO timestamp when the block lifts (None = period end)
    daily_loss_pct: float | None
    weekly_loss_pct: float | None
    consecutive_losses: int
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "level": self.level,
            "reason": self.reason,
            "until": self.until,
            "daily_loss_pct": self.daily_loss_pct,
            "weekly_loss_pct": self.weekly_loss_pct,
            "consecutive_losses": self.consecutive_losses,
            "evidence": self.evidence,
        }


class CircuitBreakerEngine:
    """Daily/weekly loss budgets + consecutive-loss cooldown (state owner).

    Single canonical owner of breaker state. Pure Python state + clock;
    persistence/versioning is the caller's concern (audit trail via the
    engine's existing audit writer, not here).
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self.config = config or CircuitBreakerConfig()
        self._day: date | None = None
        self._week: tuple[int, int] | None = None  # (iso_year, iso_week)
        self._day_start_equity: float | None = None
        self._week_start_equity: float | None = None
        self._consecutive_losses: int = 0
        self._cooldown_until: datetime | None = None
        self._cooldown_armed_at: datetime | None = None
        self._last_trade_closed_at: datetime | None = None

    # ------------------------------------------------------------------
    # PERIOD IDENTITY + ANCHOR PERSISTENCE (BUG-259, Agent-15 wave 3)
    # ------------------------------------------------------------------
    # The day/week anchors are now identity-tagged: an anchor only counts
    # for the period it was taken in. `restore_anchors` re-arms a restarting
    # process with the anchors persisted by the PREVIOUS process, so a loss
    # taken earlier in the same trading day keeps counting against the
    # budget (previously a restart silently re-anchored to current equity).
    # Anything absent/corrupt/ambiguous is refused (fail-closed to the
    # caller) — the restore NEVER fabricates an anchor from current equity.
    # ------------------------------------------------------------------

    def restore_anchors(
        self,
        *,
        day_anchor: float,
        day_utc: str,
        week_anchor: float,
        week_iso: str,
        now: datetime,
    ) -> bool:
        """Adopts persisted anchors for the CURRENT period identities.

        Accepts the restore ONLY when the persisted day identity matches
        today's UTC date and the persisted ISO-week identity matches the
        current ISO week. A stale identity (yesterday's anchor) is rejected:
        the new period re-arms from the next evaluation, exactly like a
        natural rollover. Returns True only when BOTH anchors were adopted.
        """
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        day = now.date()
        week = self._iso_week(day)
        try:
            if date.fromisoformat(str(day_utc)[:10]) != day:
                return False
            persisted_week = str(week_iso)
            hyphen = persisted_week.find("-W")
            if hyphen <= 0:
                return False
            if (int(persisted_week[:hyphen]), int(persisted_week[hyphen + 2 :])) != week:
                return False
            day_anchor = float(day_anchor)
            week_anchor = float(week_anchor)
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(day_anchor) and day_anchor > 0.0):
            return False
        if not (math.isfinite(week_anchor) and week_anchor > 0.0):
            return False
        self._day = day
        self._day_start_equity = day_anchor
        self._week = week
        self._week_start_equity = week_anchor
        return True

    def period_identities(self, now: datetime) -> tuple[str, str]:
        """Canonical identity strings for the current day / ISO week."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        day = now.date()
        iso = self._iso_week(day)
        return day.isoformat(), f"{iso[0]}-W{iso[1]}"

    # ------------------------------------------------------------------
    # Period bookkeeping (equity-based budgets)
    # ------------------------------------------------------------------

    @staticmethod
    def _iso_week(d: date) -> tuple[int, int]:
        iso = d.isocalendar()
        return (iso[0], iso[1])

    def update_equity(self, equity: float, now: datetime) -> None:
        """Rolls day/week period anchors. Call before every budget check.

        Invalid/non-finite equity keeps the previous anchors unchanged
        (fail-inert: a bad quote must never fabricate a new budget baseline).
        """
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if not isinstance(equity, (int, float)) or isinstance(equity, bool):
            return
        equity = float(equity)
        if not math.isfinite(equity) or equity <= 0.0:
            return

        d = now.date()
        week = self._iso_week(d)
        if self._day != d:
            self._day = d
            self._day_start_equity = equity
        if self._week != week:
            self._week = week
            self._week_start_equity = equity

    def _loss_pct(self, start: float | None, equity: float) -> float | None:
        if start is None or start <= 0.0 or not math.isfinite(equity):
            return None
        return max(0.0, ((start - equity) / start) * 100.0)

    # ------------------------------------------------------------------
    # Trade-outcome feed (consecutive-loss cooldown)
    # ------------------------------------------------------------------

    def record_trade_result(self, net_pnl_usd: float, closed_at: datetime) -> None:
        """Feed one closed-trade outcome. Adverse = net_pnl_usd < 0.

        Non-finite PnL is ignored (fail-inert). Zero-PnL counts as
        non-adverse (a scratch is not a stop-out).
        """
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=UTC)
        if not isinstance(net_pnl_usd, (int, float)) or isinstance(net_pnl_usd, bool):
            return
        pnl = float(net_pnl_usd)
        if not math.isfinite(pnl):
            return
        self._last_trade_closed_at = closed_at
        if pnl < 0.0:
            self._consecutive_losses += 1
            in_cooldown = self._cooldown_until is not None and closed_at < self._cooldown_until
            if (
                self._consecutive_losses >= self.config.consecutive_loss_cooldown
                and not in_cooldown
            ):
                self._cooldown_until = closed_at + timedelta(
                    seconds=self.config.cooldown_duration_sec
                )
                self._cooldown_armed_at = closed_at
                logger.critical(
                    "[BREAKER] event=CONSECUTIVE_LOSS_COOLDOWN_ARMED streak=%s until=%s",
                    self._consecutive_losses,
                    self._cooldown_until.isoformat(),
                )
        else:
            self._consecutive_losses = 0

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, equity: float, now: datetime) -> BreakerSnapshot:
        """Current breaker verdict for NEW-ENTRY authority."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        self.update_equity(equity, now)

        evidence: dict[str, Any] = {}
        daily = self._loss_pct(self._day_start_equity, equity)
        weekly = self._loss_pct(self._week_start_equity, equity)
        evidence["day_start_equity"] = self._day_start_equity
        evidence["week_start_equity"] = self._week_start_equity
        evidence["daily_loss_budget_pct"] = self.config.daily_loss_budget_pct
        evidence["weekly_loss_budget_pct"] = self.config.weekly_loss_budget_pct

        if self._cooldown_until is not None:
            if now < self._cooldown_until:
                evidence["cooldown_until"] = self._cooldown_until.isoformat()
                evidence["cooldown_armed_at"] = (
                    self._cooldown_armed_at.isoformat() if self._cooldown_armed_at else None
                )
                return BreakerSnapshot(
                    allowed=False,
                    level="COOLDOWN",
                    reason=(
                        f"CONSECUTIVE_LOSS_COOLDOWN ({self._consecutive_losses} adverse "
                        f"outcomes >= {self.config.consecutive_loss_cooldown})"
                    ),
                    until=self._cooldown_until.isoformat(),
                    daily_loss_pct=daily,
                    weekly_loss_pct=weekly,
                    consecutive_losses=self._consecutive_losses,
                    evidence=evidence,
                )
            evidence["cooldown_expired_at"] = self._cooldown_until.isoformat()
            self._cooldown_until = None  # expired: re-arm needs a NEW streak

        if daily is None or weekly is None or self._day_start_equity is None:
            # No honest equity anchor yet — report truthfully, do not block.
            return BreakerSnapshot(
                allowed=True,
                level="INSUFFICIENT_DATA",
                reason="NO_PERIOD_EQUITY_ANCHOR",
                until=None,
                daily_loss_pct=daily,
                weekly_loss_pct=weekly,
                consecutive_losses=self._consecutive_losses,
                evidence=evidence,
            )

        if daily > self.config.daily_loss_budget_pct:
            logger.critical(
                "[BREAKER] event=DAILY_LOSS_BUDGET_BREACH loss_pct=%.2f budget=%.2f",
                daily,
                self.config.daily_loss_budget_pct,
            )
            return BreakerSnapshot(
                allowed=False,
                level="DAILY_HALT",
                reason=f"DAILY_LOSS_BUDGET_BREACH ({daily:.2f}% > "
                f"{self.config.daily_loss_budget_pct:.2f}%)",
                until=None,  # lifts at UTC midnight (period rollover)
                daily_loss_pct=daily,
                weekly_loss_pct=weekly,
                consecutive_losses=self._consecutive_losses,
                evidence=evidence,
            )

        if weekly > self.config.weekly_loss_budget_pct:
            logger.critical(
                "[BREAKER] event=WEEKLY_LOSS_BUDGET_BREACH loss_pct=%.2f budget=%.2f",
                weekly,
                self.config.weekly_loss_budget_pct,
            )
            return BreakerSnapshot(
                allowed=False,
                level="WEEKLY_HALT",
                reason=f"WEEKLY_LOSS_BUDGET_BREACH ({weekly:.2f}% > "
                f"{self.config.weekly_loss_budget_pct:.2f}%)",
                until=None,  # lifts at ISO-week rollover
                daily_loss_pct=daily,
                weekly_loss_pct=weekly,
                consecutive_losses=self._consecutive_losses,
                evidence=evidence,
            )

        return BreakerSnapshot(
            allowed=True,
            level="NORMAL",
            reason="",
            until=None,
            daily_loss_pct=daily,
            weekly_loss_pct=weekly,
            consecutive_losses=self._consecutive_losses,
            evidence=evidence,
        )
