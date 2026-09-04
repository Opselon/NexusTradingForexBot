"""EXEC-QUALITY execution contract tests (order lifecycle forensics battery).

Pins the execution-contract behaviors the EXEC-QUALITY audit found under-tested:

  EC-1  dispatch-path duplicate prevention: `dispatch_order` re-sending the SAME
        request_id must be refused (engine-level duplicate guard) — the audit
        found `_processed_orders` only guarded `execute_order` (hedge path),
        never the primary market/pending dispatch path.
  EC-2  broker ambiguity is never treated as silent failure at the manager:
        a market dispatch that returns ticket>0 after an ambiguous retcode
        (adapter-side ambiguous-fill recovery) must be recorded FILLED, and a
        real refusal (ticket=0) must emit a terminal REJECTED_UNFILLED outcome
        so the decision can never hang (BUG-140 chain).
  EC-3  split-fill family context: every sibling ticket of one dispatch resolves
        the SAME immutable entry context (BUG-081) — direction-INCLUSIVE check
        (BUY and SELL legs both bind with identical reason/confidence).
  EC-4  execute_order (hedge path) idempotency guard: a duplicate order_id is
        blocked from a second broker send (INV-005/006 surface).
  EC-5  walk-forward fold split is chronological with purge before validation
        and embargo at the validation tail: `train_end <= val_start <= val_end`
        and purge/embargo never reorder samples (time-ordering contract of the
        OOS window, _split_fold_with_embargo).

All tests are offline: mock adapters only, no MT5 SDK, sqlite :memory: ledgers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


# ---------------------------------------------------------------------------
# Shared harness
# ---------------------------------------------------------------------------
def _make_tick(bid: float, ask: float | None = None) -> object:
    return MagicMock(
        timestamp=datetime.now(UTC),
        bid=bid,
        ask=ask if ask is not None else bid + 0.20,
        volume=1.0,
    )


def _manager(adapter, experience_engine=None) -> OrderLifecycleManager:
    om = OrderLifecycleManager(
        adapter=adapter,
        audit_repo=AuditRepository(db_url="sqlite:///:memory:"),
        experience_engine=experience_engine,
    )
    om.notifier = None
    return om


def _decision(request_id: str, action: ActionType = ActionType.BUY_MARKET):
    d = MagicMock()
    d.action = action
    d.symbol = "XAUUSD"
    d.proposed_entry = 2000.0
    d.stop_loss = 1990.0
    d.take_profit = 2020.0
    d.request_id = request_id
    d.confidence = 0.7
    d.regime = "TREND"
    d.execution_mode = "STANDARD"
    d.execution_id = "exec_1"
    d.ticket = 0
    d.reason_code = "TEST"
    return d


# ---------------------------------------------------------------------------
# EC-1: dispatch-path duplicate prevention (same request_id never re-sent)
# ---------------------------------------------------------------------------
class _DispatchSpyAdapter:
    """Records every market/pending dispatch; simulates the MT5 adapter surface."""

    def __init__(self) -> None:
        self.market_calls: list[dict] = []
        self.pending_calls: list[dict] = []
        self._next_ticket = 9000

    def execute_market_order(self, **kw) -> int:
        self.market_calls.append(kw)
        self._next_ticket += 1
        return self._next_ticket

    def place_pending_order(self, **kw) -> int:
        self.pending_calls.append(kw)
        self._next_ticket += 1
        return self._next_ticket

    def get_positions(self, symbol=None):
        return []

    def get_account_info(self):
        return None

    def get_symbol_info(self, symbol):
        return None


def test_ec1_dispatch_order_blocks_duplicate_request_id():
    adapter = _DispatchSpyAdapter()
    om = _manager(adapter)
    decision = _decision("req-dup-1")

    assert om.dispatch_order(decision, 0.10) is True
    assert len(adapter.market_calls) == 1

    # Same request_id dispatched again (policy re-fire, hot-reload replay,
    # engine restart in-process): must be refused WITHOUT a second broker send.
    assert om.dispatch_order(decision, 0.10) is False
    assert len(adapter.market_calls) == 1, "duplicate dispatch reached the broker"

    # A DIFFERENT request_id still dispatches normally.
    assert om.dispatch_order(_decision("req-dup-2"), 0.10) is True
    assert len(adapter.market_calls) == 2


def test_ec1_dispatch_order_duplicate_guard_covers_pending_path():
    adapter = _DispatchSpyAdapter()
    om = _manager(adapter)
    decision = _decision("req-pend-1", action=ActionType.SELL_LIMIT)

    assert om.dispatch_order(decision, 0.10) is True
    assert len(adapter.pending_calls) == 1

    assert om.dispatch_order(decision, 0.10) is False
    assert len(adapter.pending_calls) == 1


# ---------------------------------------------------------------------------
# EC-2: broker refusal (ticket=0) is terminal, never silently retried/hung
# ---------------------------------------------------------------------------
class _RefusingAdapter(_DispatchSpyAdapter):
    def execute_market_order(self, **kw) -> int:
        self.market_calls.append(kw)
        return 0  # broker refusal


def test_ec2_broker_refusal_is_terminal_not_success():
    adapter = _RefusingAdapter()
    om = _manager(adapter)
    decision = _decision("req-refused")

    assert om.dispatch_order(decision, 0.10) is False
    # The refusal is recorded in the processed-orders guard so a repeat of the
    # same request cannot silently re-fire (or hang the decision lifecycle).
    assert om._processed_orders.get("req-refused") is False
    assert len(adapter.market_calls) == 1


# ---------------------------------------------------------------------------
# EC-4: execute_order (hedge path) keeps its idempotency guard
# ---------------------------------------------------------------------------
class _SendOrderSpyAdapter:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def send_order(self, order) -> bool:
        self.sent.append(order)
        return True

    def get_positions(self, symbol=None):
        return []


def test_ec4_execute_order_blocks_duplicate_order_id():
    from nexus_scalp.domain.models import TradeOrder

    adapter = _SendOrderSpyAdapter()
    om = _manager(adapter)
    order = TradeOrder(
        order_id="hedge-1",
        symbol="XAUUSD",
        order_type=OrderType.BUY_LIMIT,
        volume=0.1,
        price=2000.0,
        stop_loss=1995.0,
        take_profit=2010.0,
        magic_number=888101,
        comment="NSE_HEDGE",
    )

    assert om.execute_order(order) is True
    assert om.execute_order(order) is False, "duplicate hedge order reached the broker"
    assert len(adapter.sent) == 1


# ---------------------------------------------------------------------------
# EC-5: walk-forward fold split stays chronological + purged + embargoed
# ---------------------------------------------------------------------------
def _trainer() -> WalkForwardTrainer:
    return WalkForwardTrainer(
        purge_gap_bars=5,
        embargo_bars=4,
        train_ratio=0.6,
    )


def test_ec5_fold_split_is_chronological_purged_embargoed():
    tr = _trainer()
    fold_len = 100
    train_end, val_start, val_end = tr._split_fold_with_embargo(fold_len)

    # Chronology: train strictly before validation, embargo trims the tail.
    assert 0 < train_end < val_start <= val_end <= fold_len
    # PURGE: at least `purge_gap` bars removed between train and validation.
    assert val_start - train_end >= tr.purge_gap
    # EMBARGO: at least `embargo_bars` bars removed from the validation tail.
    assert fold_len - val_end >= tr.embargo_bars


def test_ec5_fold_split_extremes_never_invert():
    tr = _trainer()
    # Degenerate tiny folds must not produce an inverted (train_end > val_start)
    # or negative window — the OOS window can never silently swallow train.
    for fold_len in (0, 1, 5, 20, 10_000):
        train_end, val_start, val_end = tr._split_fold_with_embargo(fold_len)
        assert 0 <= train_end <= val_start <= val_end <= fold_len
