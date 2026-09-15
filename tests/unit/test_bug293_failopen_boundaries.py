"""BUG-293 — input-boundary fail-open fixes (lane-11 L11-4 + L11-6).

Two boundaries that applied or dispatched corrupted input instead of
rejecting it:

L11-4 ``PaperMT5Adapter._load_state`` (persisted paper_state.json reload):
    the legacy field-by-field apply inside one try left EARLIER fields
    mutated when a LATER line raised — VERIFIED probe on the base commit
    applied ``balance=-99999.0`` and ``equity=NaN`` while returning False,
    and ``connect()`` kept trading on that half-state. Invalid position
    rows were silently dropped (another partial-apply: the book shrinks).
    Fix: parse + validate the whole payload into a candidate; swap engine
    fields only on success; on rejection keep current state and emit ONE
    loud structured warning naming the corruption class.

L11-6 Gateway server order payloads (SEND_ORDER / EXECUTE_MARKET_ORDER /
    PLACE_PENDING_ORDER): silently defaulted ``volume or 0.01`` and
    ``price or 0.0`` and forwarded non-finite/negative values straight to
    the broker (the market path had NO structural validation at all —
    RED-BEFORE probe VERIFIED: a payload without volume dispatched with
    0.01, and volume=inf/price=NaN dispatched untouched).
    Fix: ``gateway/order_validation.validated_order_request`` rejects with
    the same typed ``(False, reasons)`` error shape the audited
    ``_validate_pending_request`` uses, BEFORE any adapter dispatch
    (HTTP 200 + ``status=FAILED`` + ``reasons`` — the wire shape the
    client maps to its REJECTED tri-state; HMAC auth untouched).

Logging discipline: module logger monkeypatched with a probe, never caplog
(BUG-112/118 convention — see test_bug259 / test_bug268 headers).
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("NEXUS_PAPER_STRESS_SEED", "42")

import nexus_scalp.adapters.paper.paper_adapter as paper_module
from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
from nexus_scalp.gateway.order_validation import validated_order_request
from nexus_scalp.gateway.server import app, reset_adapter_for_tests

# AUDIT-B2: gateway tests run in DEMO mode with the explicit defaults opt-in.
os.environ.setdefault("NSE_GATEWAY_ALLOW_DEFAULTS", "1")
DEFAULT_KEY = "default_local_key"
DEFAULT_SECRET = "default_local_secret"

SYMBOL = "XAUUSD"


class _LogProbe:
    """Stands in for the module structlog logger; records warning calls."""

    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warnings.append((args, kwargs))

    def debug(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def info(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def error(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass


@pytest.fixture()
def paper_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NEXUS_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("NEXUS_PAPER_PERSIST", raising=False)
    return tmp_path


def _state_path(root: Path) -> Path:
    return root / "paper_state.json"


def _write_state(root: Path, payload: Any) -> None:
    _state_path(root).write_text(json.dumps(payload), encoding="utf-8")


# ===========================================================================
# L11-4 — atomic paper-state load
# ===========================================================================

_CORRUPT_VARIANTS: list[tuple[str, dict[str, Any], str]] = [
    (
        "lane-11 VERIFIED probe payload",
        {
            "balance": -99999.0,
            "equity": float("nan"),
            "_ticket_counter": "notanint",
            "_positions": [],
            "_last_tick_iso": None,
            "closed_tickets": [],
            "symbol": SYMBOL,
        },
        "balance_negative",
    ),
    (
        "NaN equity",
        {
            "balance": 9000.0,
            "equity": float("nan"),
            "_ticket_counter": 5,
            "_positions": [],
            "closed_tickets": [],
            "symbol": SYMBOL,
        },
        "equity_not_finite",
    ),
    (
        "inf balance",
        {
            "balance": float("inf"),
            "equity": 9000.0,
            "_ticket_counter": 5,
            "_positions": [],
            "closed_tickets": [],
            "symbol": SYMBOL,
        },
        "balance_not_finite",
    ),
    (
        "wrong type volume",
        {
            "balance": {"a": 1},
            "equity": 9000.0,
            "_ticket_counter": 5,
            "_positions": [],
            "closed_tickets": [],
            "symbol": SYMBOL,
        },
        "balance_not_a_number",
    ),
    (
        "negative ticket counter",
        {
            "balance": 9000.0,
            "equity": 9000.0,
            "_ticket_counter": -7,
            "_positions": [],
            "closed_tickets": [],
            "symbol": SYMBOL,
        },
        "ticket_counter_negative",
    ),
    (
        "closed tickets not a list",
        {
            "balance": 9000.0,
            "equity": 9000.0,
            "_ticket_counter": 5,
            "_positions": [],
            "closed_tickets": "5,7",
            "symbol": SYMBOL,
        },
        "closed_tickets_not_list",
    ),
    (
        "bad position row poisons whole file",
        {
            "balance": 9000.0,
            "equity": 9000.0,
            "_ticket_counter": 5,
            "_positions": [{"ticket": 1, "symbol": SYMBOL}],
            "closed_tickets": [],
            "symbol": SYMBOL,
        },
        "position_row_invalid[0]",
    ),
]


@pytest.mark.parametrize("name,payload,corruption_class", _CORRUPT_VARIANTS)
def test_corrupt_state_never_partially_applies(
    paper_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    payload: dict[str, Any],
    corruption_class: str,
) -> None:
    """Any corruption → engine fields byte-identical to pre-load + ONE warning.

    RED-BEFORE (base commit, VERIFIED): the lane-11 probe row left
    balance=-99999.0 and equity=NaN APPLIED despite return False.
    """
    probe = _LogProbe()
    monkeypatch.setattr(paper_module, "logger", probe)
    _write_state(paper_root, payload)
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    before = {
        "balance": a.balance,
        "equity": a.equity,
        "ticket": a._ticket_counter,
        "positions": copy.deepcopy(a._positions),
        "closed": copy.deepcopy(a._closed_tickets),
    }
    ok = a._load_state()
    assert ok is False, f"{name}: corrupt load must report failure"
    assert a.balance == before["balance"], f"{name}: balance partially applied"
    assert a.equity == before["equity"], f"{name}: equity partially applied"
    assert a._ticket_counter == before["ticket"], f"{name}: ticket counter applied"
    assert a._positions == before["positions"], f"{name}: positions dropped/applied"
    assert a._closed_tickets == before["closed"], f"{name}: closed set applied"
    # Exactly ONE loud structured warning naming the corruption class.
    assert len(probe.warnings) == 1, f"{name}: expected one warning, got {probe.warnings}"
    _, kwargs = probe.warnings[0]
    assert kwargs.get("corruption_class") == corruption_class, (
        f"{name}: warning must name the corruption class, got {kwargs}"
    )


def test_truncated_json_rejected_and_keeps_state(
    paper_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = _LogProbe()
    monkeypatch.setattr(paper_module, "logger", probe)
    _state_path(paper_root).write_text('{"balance": 500.0, "eq', encoding="utf-8")
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    assert a._load_state() is False
    assert a.balance == 10_000.0
    assert (
        probe.warnings and probe.warnings[0][1]["corruption_class"] == "unreadable_or_invalid_json"
    )


def test_json_array_payload_rejected(paper_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _LogProbe()
    monkeypatch.setattr(paper_module, "logger", probe)
    _state_path(paper_root).write_text("[1, 2, 3]", encoding="utf-8")
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    assert a._load_state() is False
    assert a.balance == 10_000.0
    assert probe.warnings[0][1]["corruption_class"] == "payload_not_object"


def test_connect_never_booted_on_half_state(
    paper_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """connect() swallows _load_state's verdict — the load itself must be atomic.

    RED-BEFORE: connect() traded on balance=-99999/equity=NaN because
    _load_state applied fields before failing; its ignored False never
    mattered. The fix makes the verdict moot: rejection changes nothing.
    """
    monkeypatch.setattr(paper_module, "logger", _LogProbe())
    _write_state(
        paper_root,
        {
            "balance": -99999.0,
            "equity": float("nan"),
            "_ticket_counter": "notanint",
            "_positions": [],
            "closed_tickets": [],
            "symbol": SYMBOL,
        },
    )
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    assert a.connect() is True  # never crash-boots (PAPER tier may degrade)
    assert a.balance == 10_000.0
    assert a.equity == 10_000.0


def test_healthy_file_round_trips_identically(paper_root: Path) -> None:
    """Backward compat: a healthy book persists, then reloads field-for-field.

    Round-trip style pinned by test_paper_persistence (restart restores old
    balance, positions, ticket counter); corrupt variants above must never
    regress this.
    """
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    a.connect()
    a.get_last_tick(SYMBOL)
    from nexus_scalp.domain.enums import OrderType

    ticket = a.execute_market_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY,
        volume=0.10,
        price=4400.00,
        stop_loss=4380.00,
        take_profit=4450.00,
    )
    assert ticket > 0
    persisted = json.loads(_state_path(paper_root).read_text(encoding="utf-8"))
    b = PaperMT5Adapter(initial_balance=99_999.99, symbol=SYMBOL)
    assert b._load_state() is True
    assert b.balance == persisted["balance"]
    assert [p.ticket for p in b._positions] == [ticket]
    assert b._ticket_counter == persisted["_ticket_counter"]
    assert b._closed_tickets == set(persisted["closed_tickets"])


def test_negative_balance_session_persist_then_reload_is_rejected(paper_root: Path) -> None:
    """Self-inflicted blow-up past zero: the next boot refuses to resurrect it.

    The paper close path can drive balance negative (no stop-out floor);
    rejecting that on RELOAD is deliberately conservative — the engine keeps
    the boot default rather than restoring a book indistinguishable from
    corruption, and says so loudly (no silent reset either).
    """
    probe = _LogProbe()
    a = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
    a.connect()
    a.balance = -50.0  # simulate the post-stop-out persisted reality
    a._persist_state()
    with patch.object(paper_module, "logger", probe):
        b = PaperMT5Adapter(initial_balance=10_000.0, symbol=SYMBOL)
        assert b._load_state() is False
    assert b.balance == 10_000.0
    assert probe.warnings[0][1]["corruption_class"] == "balance_negative"


# ===========================================================================
# L11-6 — gateway order-payload fail-closed contract
# ===========================================================================

_MALFORMED_MARKET_PAYLOADS: list[tuple[str, dict[str, Any], str]] = [
    ("missing volume", {"symbol": SYMBOL, "order_type": "BUY", "price": 0.0}, "missing_volume"),
    (
        "null volume",
        {"symbol": SYMBOL, "order_type": "BUY", "volume": None, "price": 0.0},
        "missing_volume",
    ),
    (
        "zero volume",
        {"symbol": SYMBOL, "order_type": "BUY", "volume": 0.0, "price": 0.0},
        "volume_not_positive",
    ),
    (
        "negative volume",
        {"symbol": SYMBOL, "order_type": "BUY", "volume": -0.1, "price": 0.0},
        "volume_negative",
    ),
    (
        "infinite volume",
        {"symbol": SYMBOL, "order_type": "BUY", "volume": float("inf"), "price": 0.0},
        "volume_not_finite",
    ),
    (
        "NaN price",
        {"symbol": SYMBOL, "order_type": "BUY", "volume": 0.1, "price": float("nan")},
        "price_not_finite",
    ),
    (
        "negative price",
        {"symbol": SYMBOL, "order_type": "BUY", "volume": 0.1, "price": -5.0},
        "price_negative",
    ),
    (
        "empty symbol",
        {"symbol": "", "order_type": "BUY", "volume": 0.1, "price": 0.0},
        "empty_symbol",
    ),
    ("missing symbol", {"order_type": "BUY", "volume": 0.1, "price": 0.0}, "missing_symbol"),
    (
        "string volume",
        {"symbol": SYMBOL, "order_type": "BUY", "volume": "lots", "price": 0.0},
        "volume_not_numeric",
    ),
    (
        "boolean volume",
        {"symbol": SYMBOL, "order_type": "BUY", "volume": True, "price": 0.0},
        "volume_not_numeric",
    ),
    ("missing order_type", {"symbol": SYMBOL, "volume": 0.1, "price": 0.0}, "missing_order_type"),
]


def test_001_default_is_now_unreachable_unit() -> None:
    """RED-BEFORE pin (VERIFIED probe): missing volume dispatched volume=0.01.

    The pure validator can now never INVENT a volume — a missing field
    returns reasons, never a defaulted request dict.
    """
    for action_payload, legacy in (
        ({"symbol": SYMBOL, "order_type": "BUY", "price": 0.0}, False),
        ({"symbol": SYMBOL, "order_type": "BUY"}, True),
    ):
        req, reasons = validated_order_request(action_payload, legacy_trade_order=legacy)
        assert req is None
        assert "missing_volume" in reasons
    # and price is never coerced to 0.0 when absent on market path either
    req, reasons = validated_order_request({"symbol": SYMBOL, "order_type": "BUY", "volume": 0.1})
    assert req is None and "missing_price" in reasons


def test_market_and_pending_path_never_dispatch_malformed() -> None:
    """RED-BEFORE (VERIFIED): every row below returned SUCCESS with defaults."""
    fake = MagicMock()
    fake.execute_market_order.side_effect = AssertionError("MUST NOT DISPATCH")
    fake.place_pending_order.side_effect = AssertionError("MUST NOT DISPATCH")
    for name, pl, expected_reason in _MALFORMED_MARKET_PAYLOADS:
        req, reasons = validated_order_request(pl)
        assert req is None, f"{name}: validator accepted malformed payload"
        assert expected_reason in reasons, f"{name}: reasons={reasons}"


@pytest.mark.parametrize("action", ["EXECUTE_MARKET_ORDER", "PLACE_PENDING_ORDER"])
def test_http_shape_typed_failed_with_reasons_before_dispatch(
    paper_root: Path, action: str
) -> None:  # paper_root fixture keeps NEXUS_DATA_ROOT isolated (unused here)
    """Through the FULL HTTP surface: 200 + FAILED + reasons, zero adapter calls."""
    reset_adapter_for_tests()
    fake = MagicMock()
    body = json.dumps(
        {"action": action, "payload": {"symbol": SYMBOL, "order_type": "BUY", "price": 0.0}}
    ).encode()
    ts = str(int(time.time()))
    sig = hmac.new(
        DEFAULT_SECRET.encode(), msg=f"{ts}.".encode() + body, digestmod=hashlib.sha256
    ).hexdigest()
    with patch("nexus_scalp.gateway.server.sys") as mock_sys:
        mock_sys.platform = "win32"
        with patch("nexus_scalp.gateway.server._get_adapter", return_value=fake):
            client = TestClient(app)
            r = client.post(
                "/api/v1/execute",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-NSE-API-KEY": DEFAULT_KEY,
                    "X-NSE-TIMESTAMP": ts,
                    "X-NSE-SIGNATURE": sig,
                },
            )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "FAILED"
    assert data["message"] == "invalid order request"
    assert "missing_volume" in data["reasons"]
    fake.execute_market_order.assert_not_called()
    fake.place_pending_order.assert_not_called()


def test_valid_market_payload_still_accepted_and_forwarded_verbatim() -> None:
    reset_adapter_for_tests()
    fake = MagicMock()
    fake.execute_market_order.return_value = 777001
    payload = {
        "symbol": SYMBOL,
        "order_type": "BUY",
        "volume": 0.05,
        "price": 0.0,  # execute-at-market sentinel must stay legal
        "stop_loss": 0.0,
        "take_profit": 0.0,
    }
    body = json.dumps({"action": "EXECUTE_MARKET_ORDER", "payload": payload}).encode()
    ts = str(int(time.time()))
    sig = hmac.new(
        DEFAULT_SECRET.encode(), msg=f"{ts}.".encode() + body, digestmod=hashlib.sha256
    ).hexdigest()
    with patch("nexus_scalp.gateway.server.sys") as mock_sys:
        mock_sys.platform = "win32"
        with patch("nexus_scalp.gateway.server._get_adapter", return_value=fake):
            client = TestClient(app)
            r = client.post(
                "/api/v1/execute",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-NSE-API-KEY": DEFAULT_KEY,
                    "X-NSE-TIMESTAMP": ts,
                    "X-NSE-SIGNATURE": sig,
                },
            )
    assert r.status_code == 200
    assert r.json()["status"] == "SUCCESS"
    kwargs = fake.execute_market_order.call_args.kwargs
    assert kwargs["volume"] == 0.05
    assert kwargs["price"] == 0.0
    assert kwargs["symbol"] == SYMBOL


def test_legacy_send_order_requires_positive_prices() -> None:
    """TradeOrder domain model pins price/sl/tp gt=0 — legacy path keeps that.

    RED-BEFORE: missing price/sl/tp were coerced to 1.0 placeholders so the
    model would construct; now they fail closed with reasons.
    """
    req, reasons = validated_order_request(
        {"symbol": SYMBOL, "order_type": "BUY", "volume": 0.1, "price": 1.0},
        legacy_trade_order=True,
    )
    assert req is None
    assert "missing_price" not in reasons  # price present...
    assert "stop_loss_not_positive" not in reasons  # ...absent sl/tp: required on legacy
    assert "missing_stop_loss" in reasons and "missing_take_profit" in reasons
    # 0.0 placeholders are NOT legal on the legacy path (domain gt=0)
    req2, reasons2 = validated_order_request(
        {
            "symbol": SYMBOL,
            "order_type": "BUY",
            "volume": 0.1,
            "price": 1.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
        },
        legacy_trade_order=True,
    )
    assert req2 is None
    assert "stop_loss_not_positive" in reasons2 and "take_profit_not_positive" in reasons2
