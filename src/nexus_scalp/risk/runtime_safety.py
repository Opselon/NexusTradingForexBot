"""Runtime safety-state contracts (pure policy — no I/O, no side effects).

Mission: RUNTIME-STATE + FINANCIAL-DATA-INTEGRITY (P0 safety-state durability).

Central invariant: a safety state triggered by a real trading event MUST
survive process restart until explicitly released. This module owns the PURE
half of that contract (vocabulary, persisted-row codec, boot decision,
hot-path error circuit, account freshness classification, consecutive-loss
evaluation). All persistence lives in ``AuditRepository.runtime_risk_state``
(one canonical single-row store, atomic SQLite upsert); all engine wiring
lives in ``LiveEngine``. No module here opens a file, socket, or DB.

State vocabulary (canonical, one store — never parallel booleans):

- RUNNING      persisted; trading permitted.
- HALTED       persisted; drawdown/risk halt. Restart does NOT clear it.
- KILL_SWITCH  persisted; operator kill. Restart does NOT clear it.
- DEGRADED     SESSION-LOCAL only (hot-path circuit / stale account).
                Never persisted: it is derived, recomputed each tick.
- SHADOW       execution MODE (existing concept, domain.enums), not a row in
                this store — preserved as a semantic distinction, not duplicated.

Release contract: HALTED/KILL_SWITCH rows carry ``release_required=True``.
Only ``AuditRepository.release_runtime_risk_state`` (explicit, audited,
actor-stamped) or the ``nexus risk release`` CLI can clear them. A restart,
reconnect, reload or broker reconnect can never clear them (resolve_boot_decision
fails closed on anything that is not RUNNING).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Iterable

__all__ = [
    "RUNTIME_RISK_STATE_VERSION",
    "RuntimeRiskState",
    "PersistedRiskState",
    "BootDecision",
    "resolve_boot_decision",
    "HotPathErrorCircuit",
    "AccountFreshness",
    "classify_account_freshness",
    "evaluate_consecutive_losses",
    "evaluate_consecutive_loss_freeze",
]

#: Row-schema version for future migrations. Unknown higher versions are
#: read best-effort (columns are additive); unknown lower versions are
#: treated as current (no destructive migration has ever shipped).
RUNTIME_RISK_STATE_VERSION = 1

#: States that are DURABLE (persisted in the runtime_risk_state row).
PERSISTED_STATES = frozenset({"RUNNING", "HALTED", "KILL_SWITCH"})


class RuntimeRiskState(StrEnum):
    """Canonical runtime risk states (persisted subset + DEGRADED)."""

    RUNNING = "RUNNING"
    HALTED = "HALTED"
    KILL_SWITCH = "KILL_SWITCH"
    DEGRADED = "DEGRADED"

    @classmethod
    def persisted_values(cls) -> frozenset[str]:
        return frozenset(s.value for s in cls if s.value in PERSISTED_STATES)


@dataclass(frozen=True)
class PersistedRiskState:
    """One canonical persisted safety decision (single-row store payload).

    Constructed on every halt/kill trigger and read at every boot. All
    timestamps are UTC ISO-8601 strings; money fields are account-currency
    floats captured at trigger time for forensic context.
    """

    state: str
    reason: str = ""
    source: str = ""
    triggered_at: str = ""
    balance: float = 0.0
    equity: float = 0.0
    peak_equity: float = 0.0
    release_required: bool = True
    released_at: str | None = None
    release_actor: str | None = None
    consecutive_losses: int = 0
    version: int = RUNTIME_RISK_STATE_VERSION

    def __post_init__(self) -> None:
        if self.state not in PERSISTED_STATES:
            raise ValueError(f"state must be one of {sorted(PERSISTED_STATES)}, got {self.state!r}")

    @property
    def is_blocking(self) -> bool:
        """True when this state must refuse trading (fail closed)."""
        return self.state in ("HALTED", "KILL_SWITCH")

    def to_row(self) -> dict[str, Any]:
        """Flat dict matching the runtime_risk_state column layout."""
        return {
            "version": int(self.version),
            "state": str(self.state),
            "reason": str(self.reason or ""),
            "source": str(self.source or ""),
            "triggered_at": str(self.triggered_at or ""),
            "balance": float(self.balance),
            "equity": float(self.equity),
            "peak_equity": float(self.peak_equity),
            "release_required": 1 if self.release_required else 0,
            "released_at": self.released_at,
            "release_actor": self.release_actor,
            "consecutive_losses": int(self.consecutive_losses),
        }

    @classmethod
    def from_row(cls, row: Any) -> PersistedRiskState | None:
        """Best-effort decode of a runtime_risk_state row (or dict).

        Returns None when the row is unusable (missing state). Unknown
        states are PRESERVED verbatim — the boot decision fails closed
        on them instead of silently normalizing to RUNNING.
        """
        if row is None:
            return None
        get = row.get if isinstance(row, dict) else lambda k, d=None: (
            row[k] if k in row.keys() else d
        )
        state = str(get("state", "") or "")
        if not state:
            return None

        def _f(key: str) -> float:
            try:
                return float(get(key, 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        try:
            version = int(get("version", RUNTIME_RISK_STATE_VERSION) or RUNTIME_RISK_STATE_VERSION)
        except (TypeError, ValueError):
            version = RUNTIME_RISK_STATE_VERSION
        try:
            losses = int(get("consecutive_losses", 0) or 0)
        except (TypeError, ValueError):
            losses = 0
        return cls(
            state=state,
            reason=str(get("reason", "") or ""),
            source=str(get("source", "") or ""),
            triggered_at=str(get("triggered_at", "") or ""),
            balance=_f("balance"),
            equity=_f("equity"),
            peak_equity=_f("peak_equity"),
            release_required=bool(get("release_required", 1)),
            released_at=get("released_at"),
            release_actor=get("release_actor"),
            consecutive_losses=max(0, losses),
            version=version,
        )


@dataclass(frozen=True)
class BootDecision:
    """What startup must do with the persisted safety state."""

    trading_allowed: bool
    state: str
    detail: str


def resolve_boot_decision(row: PersistedRiskState | None) -> BootDecision:
    """Startup safety resolution — restored BEFORE any trading is enabled.

    Contract (HALT INVARIANT):
    - no persisted row (fresh install)  -> RUNNING (trading allowed);
    - RUNNING                           -> trading allowed;
    - HALTED / KILL_SWITCH              -> trading REFUSED (never auto-clear);
    - anything unknown                  -> trading REFUSED (fail closed).

    Recalculating drawdown or account equity MUST NOT influence this
    decision: only an explicit release clears a persisted halt.
    """
    if row is None:
        return BootDecision(trading_allowed=True, state="RUNNING", detail="NO_PERSISTED_STATE")
    if row.state == "RUNNING":
        return BootDecision(trading_allowed=True, state="RUNNING", detail="")
    if row.state in ("HALTED", "KILL_SWITCH"):
        return BootDecision(
            trading_allowed=False,
            state=row.state,
            detail=f"PERSISTED_{row.state}: {row.reason or 'reason not recorded'}"
            f" (triggered_at={row.triggered_at or 'NOT_RECORDED'})",
        )
    return BootDecision(
        trading_allowed=False,
        state=row.state,
        detail=f"UNKNOWN_PERSISTED_STATE={row.state!r} — fail closed",
    )


class HotPathErrorCircuit:
    """Consecutive-error circuit breaker for the tick hot path.

    Policy (configurable; 10 errors / 10 minutes is the repo default):
    N consecutive hot-path exceptions inside the window trip the circuit ->
    the engine degrades to DEGRADED (no NEW entries; position management and
    protective exits continue untouched).

    Reset rule — meaningful recovery only: a single success does NOT zero the
    counter (error/success/error/success flapping must not hide a systemic
    fault). The counter resets only after a full ``error_window_sec`` has
    elapsed since the LAST error.
    """

    def __init__(
        self,
        *,
        max_consecutive_errors: int = 10,
        error_window_sec: float = 600.0,
    ) -> None:
        if max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be >= 1")
        if error_window_sec <= 0.0:
            raise ValueError("error_window_sec must be > 0")
        self.max_consecutive_errors = int(max_consecutive_errors)
        self.error_window_sec = float(error_window_sec)
        self.consecutive_error_count: int = 0
        self.error_window_start: float = 0.0
        self.last_error_at: float = 0.0
        self.last_error_type: str = ""
        self.total_errors: int = 0

    def record_error(self, now: float, exc: BaseException | None = None) -> bool:
        """Records one hot-path failure. Returns True when now tripped."""
        if self.consecutive_error_count <= 0 or (now - self.last_error_at) > self.error_window_sec:
            # First error of a new window (or the old window expired).
            self.error_window_start = now
            self.consecutive_error_count = 0
        self.consecutive_error_count += 1
        self.last_error_at = now
        self.last_error_type = type(exc).__name__ if exc is not None else "UnknownError"
        self.total_errors += 1
        return self.is_tripped(now)

    def record_success(self, now: float) -> None:
        """Records one clean pipeline pass.

        Resets the consecutive count ONLY when the whole error window has
        passed cleanly since the last error (no naive zeroing).
        """
        if self.consecutive_error_count > 0 and (now - self.last_error_at) >= self.error_window_sec:
            self.consecutive_error_count = 0

    def is_tripped(self, now: float) -> bool:
        """True while the breaker demands controlled degradation."""
        if self.consecutive_error_count < self.max_consecutive_errors:
            return False
        return (now - self.error_window_start) <= self.error_window_sec

    def window_remaining_sec(self, now: float) -> float:
        """Seconds until the current window expires (0 when none open)."""
        remaining = self.error_window_sec - (now - self.error_window_start)
        return max(0.0, remaining) if self.consecutive_error_count > 0 else 0.0

    def reset(self) -> None:
        """Explicit recovery (operator release / successful re-arm)."""
        self.consecutive_error_count = 0
        self.error_window_start = 0.0
        self.last_error_at = 0.0
        self.last_error_type = ""


class AccountFreshness(StrEnum):
    """Classification of the account snapshot used for sizing decisions."""

    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"


def classify_account_freshness(
    *,
    snapshot: Any,
    last_success_refresh: float,
    now: float,
    max_age_sec: float,
) -> AccountFreshness:
    """Pure account-freshness classification.

    - MISSING: no snapshot has ever been captured.
    - STALE:   the last SUCCESSFUL refresh is older than ``max_age_sec``
              (reuses the existing live-freshness policy threshold).
    - FRESH:   otherwise.
    """
    if snapshot is None or last_success_refresh <= 0.0:
        return AccountFreshness.MISSING
    if (now - last_success_refresh) > max_age_sec:
        return AccountFreshness.STALE
    return AccountFreshness.FRESH


def evaluate_consecutive_losses(rows: Iterable[tuple[str, float]]) -> tuple[int, str]:
    """Derives the consecutive-loss chain from CANONICAL realized outcomes.

    ``rows``: newest-first ``(status, net_pnl_usd)`` finalized ledger rows
    (OPENED placeholders and non-finalized outcomes must be excluded by the
    caller). Rejected orders / failed telemetry / duplicates never appear
    here, so they can never count as losses.

    Semantics (explicit):
    - pnl <  0 -> counts toward the chain;
    - pnl >= 0 (win or breakeven) -> interrupts the chain (explicit reset);
    - the chain stops at the first non-loss outcome.

    Returns ``(count, last_loss_close_time_iso)`` — the timestamp of the
    NEWEST loss in the chain ('' when count == 0), used by the freeze window.
    """
    count = 0
    last_loss_ts = ""
    for _status, pnl in rows:
        try:
            value = float(pnl)
        except (TypeError, ValueError):
            break  # unparseable outcome: never fabricate a count past it
        if value < 0.0:
            count += 1
            if count == 1:
                # caller passes (status, pnl); timestamp is threaded via a
                # parallel tuple when available (see repo.get_consecutive_losses)
                last_loss_ts = ""
        else:
            break
    return count, last_loss_ts


def evaluate_consecutive_losses_with_time(
    rows: Iterable[tuple[str, float, str]],
) -> tuple[int, str]:
    """Like evaluate_consecutive_losses but rows carry (status, pnl, close_time).

    Returns (consecutive_losses, newest_loss_close_time_iso).
    """
    count = 0
    last_loss_ts = ""
    for _status, pnl, close_time in rows:
        try:
            value = float(pnl)
        except (TypeError, ValueError):
            break
        if value < 0.0:
            count += 1
            if not last_loss_ts:
                last_loss_ts = str(close_time or "")
        else:
            break
    return count, last_loss_ts


def evaluate_consecutive_loss_freeze(
    *,
    consecutive_losses: int,
    last_loss_close_time: str,
    now: datetime,
    threshold: int,
    freeze_hours: float,
) -> bool:
    """Pure RULE_CONSECUTIVE_LOSS_FREEZE evaluation (operator opt-in rule).

    True when the canonical consecutive-loss chain reached ``threshold`` AND
    the freeze window (``freeze_hours`` since the newest loss) is still open.
    Threshold / freeze_hours come from the rule's own persisted parameters —
    never hard-coded here.
    """
    if consecutive_losses < threshold:
        return False
    if freeze_hours <= 0.0:
        return True
    if not last_loss_close_time:
        # Chain exceeded the threshold but the newest loss time is unknown:
        # freeze (fail closed) — the operator can release via rule toggle.
        return True
    try:
        ts = last_loss_close_time.strip()
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        last = datetime.fromisoformat(ts)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last).total_seconds() < freeze_hours * 3600.0
