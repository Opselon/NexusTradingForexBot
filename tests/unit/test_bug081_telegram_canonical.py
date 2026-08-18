"""
BUG-081 Telegram canonical-close regression guards (TEST 11).

Proves the POSITION CLOSED notification consumes the SAME canonical exit
reason the ledger/experience use — never a hardcoded "MANUAL POSITION CLOSE
DETECTED" fallback (the production incident on ticket 152500222827).
"""

from __future__ import annotations

from nexus_scalp.observability.telegram_notifier import TelegramNotifier


class _CapturingNotifier(TelegramNotifier):
    """Records the last message instead of sending it."""

    def __init__(self) -> None:
        # Dummy credentials: never used because send() is overridden.
        super().__init__(bot_token="TEST_TOKEN_NOT_REAL", admin_id=0)
        self.last_message: str | None = None
        self.send_called = 0

    def send(self, message: str, **kwargs: object) -> int | None:
        self.last_message = message
        self.send_called += 1
        return 1


def _make_notifier() -> _CapturingNotifier:
    n = _CapturingNotifier()
    # Override the internal send pipeline entirely; the real one needs config.
    return n


def test_canonical_close_uses_exit_reason_not_manual():
    """A break-even protective close must NOT say MANUAL."""
    n = _make_notifier()
    n.notify_canonical_close(
        ticket=152500222827,
        symbol="XAUUSD",
        entry=4358.48,
        exit_price=4358.17,
        profit_usd=5.27,
        duration_sec=44.0,
        exit_reason="BREAK_EVEN_SL_HIT",
        evidence="ENGINE_SL_MODIFICATION",
        initial_sl=4368.11,
        final_sl=4358.15,
        strategy="PURE_AI",
        regime="TRENDING_MOMENTUM",
        confidence=0.251,
        realized_r=0.03,
        mfe_usd=27.54,
        mae_usd=-26.01,
    )
    assert n.send_called == 1
    assert n.last_message is not None
    # The incident message said MANUAL — the canonical one must not.
    assert "MANUAL POSITION CLOSE DETECTED" not in n.last_message
    assert "POSITION CLOSED" in n.last_message
    assert "BREAK-EVEN STOP" in n.last_message
    assert "ENGINE_SL_MODIFICATION" in n.last_message
    assert "4368.11 → 4358.15" in n.last_message
    assert "152500222827" in n.last_message
    assert "+$5.27" in n.last_message


def test_canonical_close_unknown_stays_unknown():
    """Insufficient evidence stays UNKNOWN — never MANUAL."""
    n = _make_notifier()
    n.notify_canonical_close(
        ticket=1,
        symbol="XAUUSD",
        entry=2000.0,
        exit_price=1999.0,
        profit_usd=-10.0,
        duration_sec=10.0,
        exit_reason="UNKNOWN",
        evidence="",
    )
    assert n.send_called == 1
    assert n.last_message is not None
    assert "MANUAL" not in n.last_message
    assert "UNKNOWN" in n.last_message


def test_exit_label_mapping_never_manual_for_protective():
    """Protective exit labels map to their true class, never MANUAL."""
    n = _make_notifier()
    assert n._exit_label("BREAK_EVEN_SL_HIT") == "BREAK-EVEN STOP"
    assert n._exit_label("TRAILING_STOP_HIT") == "TRAILING STOP"
    assert n._exit_label("HARD_SL_HIT") == "ORIGINAL STOP LOSS"
    assert n._exit_label("TAKE_PROFIT_HIT") == "TAKE PROFIT"
    assert n._exit_label("AI_REVERSAL_EXIT") == "STRATEGY EXIT (AI REVERSAL)"
    assert n._exit_label("PROFIT_GIVEBACK_PROTECTION") == "EMERGENCY EXIT (GIVEBACK)"
    assert n._exit_label("MANUAL_CLOSE") == "MANUAL CLOSE"
    assert n._exit_label("UNKNOWN") == "UNKNOWN"
    assert n._exit_label("") == "UNKNOWN"
