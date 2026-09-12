"""Order-write uncertainty semantics (mission package P0): UNKNOWN != FAILED.

Contract pins:
  1. WriteResult typed outcome: SUCCESS / REJECTED / UNKNOWN — a write can
     never be collapsed into a bool.
  2. A communication failure (exception from the RPC) during a broker write
     resolves to UNKNOWN, never FAILED: the broker may have executed the
     order and lost the response.
  3. UNKNOWN from execute_market_order / place_pending_order is surfaced as
     ticket=0 plus is_unknown=True evidence (never silent success).
  4. The idempotency fingerprint (magic+symbol+type+volume+price) makes a
     post-UNKNOWN reconciliation deterministic and re-send-safe.
  5. The OrderIntentStore persists intents (intent_id -> payload) so a
     process restart cannot orphan an in-flight write; idempotency-key
     derivation is pure and stable across restarts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.execution.order_write")


class WriteOutcome(Enum):
    """Definitive tri-state for a broker write attempt."""

    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class WriteResult:
    """Typed result of one broker write attempt.

    ticket == 0 with outcome != SUCCESS is the canonical 'no ticket'
    representation; `unknown` is the explicit ambiguity flag so callers
    (and audit rows) never confuse REJECTED with UNKNOWN.
    """

    outcome: WriteOutcome
    ticket: int = 0
    detail: str = ""
    retcode: int | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is WriteOutcome.SUCCESS

    @property
    def unknown(self) -> bool:
        return self.outcome is WriteOutcome.UNKNOWN


def success(ticket: int, detail: str = "") -> WriteResult:
    return WriteResult(WriteOutcome.SUCCESS, ticket=int(ticket), detail=detail)


def rejected(retcode: int | None = None, detail: str = "") -> WriteResult:
    return WriteResult(WriteOutcome.REJECTED, ticket=0, retcode=retcode, detail=detail)


def unknown(detail: str = "", retcode: int | None = None) -> WriteResult:
    return WriteResult(WriteOutcome.UNKNOWN, ticket=0, retcode=retcode, detail=detail)


def idempotency_fingerprint(
    *,
    symbol: str,
    order_type: str,
    volume: float,
    price: float,
    magic: int = 888101,
) -> str:
    """Deterministic fingerprint of a write request (restart-stable).

    Matches the broker-side equivalence rule used for pending-order
    reuse (symbol + type + volume + price under the bot magic), so a
    post-UNKNOWN reconciliation checks exactly what a retry would have
    created.
    """
    return f"{int(magic)}|{symbol}|{order_type}|{round(float(volume), 9)}|{round(float(price), 9)}"


@dataclass
class OrderIntent:
    """Persistent identity of one order write attempt.

    Survives process restart via OrderIntentStore (JSON lines, append-only).
    status: PENDING (write dispatched, response unknown/absent) ->
            RESOLVED_SUCCESS / RESOLVED_REJECTED / RECONCILED_SUCCESS /
            RECONCILED_REJECTED / FAILED_LOCAL (never sent).
    """

    intent_id: str
    request_id: str
    created_at_utc: str
    symbol: str
    side: str
    order_kind: str  # MARKET | PENDING | CLOSE | MODIFY
    requested_volume: float
    price: float
    fingerprint: str
    status: str = "PENDING"
    ticket: int | None = None
    resolved_at_utc: str | None = None
    resolution_detail: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def from_json(raw: str) -> OrderIntent:
        return OrderIntent(**json.loads(raw))


class OrderIntentStore:
    """Append-only JSONL persistence for order intents (restart-safe).

    One line per intent state transition (append-only audit semantics);
    the live view resolves to the LAST record per intent_id. A crash
    between the broker send and the response therefore leaves a PENDING
    record on disk that the next startup can reconcile against broker
    truth instead of guessing.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, intent: OrderIntent) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(intent.to_json() + "\n")

    def load_pending(self) -> dict[str, OrderIntent]:
        """Latest record per intent_id that is still PENDING."""
        if not self._path.exists():
            return {}
        latest: dict[str, OrderIntent] = {}
        with self._path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    intent = OrderIntent.from_json(line)
                except (ValueError, TypeError, KeyError):
                    continue  # corrupt line never breaks recovery
                latest[intent.intent_id] = intent
        return {iid: intent for iid, intent in latest.items() if intent.status == "PENDING"}

    def resolve(
        self,
        intent_id: str,
        *,
        status: str,
        ticket: int | None = None,
        detail: str = "",
        resolved_at: datetime | None = None,
    ) -> None:
        pending = self.load_pending()
        intent = pending.get(intent_id)
        if intent is None:
            return
        self.record(
            OrderIntent(
                intent_id=intent.intent_id,
                request_id=intent.request_id,
                created_at_utc=intent.created_at_utc,
                symbol=intent.symbol,
                side=intent.side,
                order_kind=intent.order_kind,
                requested_volume=intent.requested_volume,
                price=intent.price,
                fingerprint=intent.fingerprint,
                status=status,
                ticket=ticket,
                resolved_at_utc=resolved_at.isoformat()
                if resolved_at
                else datetime.now(UTC).isoformat(),
                resolution_detail=detail,
            )
        )


__all__ = [
    "OrderIntent",
    "OrderIntentStore",
    "WriteOutcome",
    "WriteResult",
    "idempotency_fingerprint",
    "rejected",
    "success",
    "unknown",
]
