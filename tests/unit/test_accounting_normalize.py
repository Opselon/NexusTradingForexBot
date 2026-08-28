import pytest
from nexus_scalp.accounting.normalize import _is_long

def test_is_long():
    """Test that _is_long correctly identifies long direction strings."""
    # True cases
    assert _is_long("BUY") is True
    assert _is_long("buy") is True
    assert _is_long("Buy") is True
    assert _is_long("BUY_LIMIT") is True
    assert _is_long("buy_stop") is True
    assert _is_long("LONG") is True
    assert _is_long("long") is True
    assert _is_long("0") is True

    # False cases
    assert _is_long("SELL") is False
    assert _is_long("sell") is False
    assert _is_long("Sell") is False
    assert _is_long("SELL_LIMIT") is False
    assert _is_long("sell_stop") is False
    assert _is_long("SHORT") is False
    assert _is_long("short") is False
    assert _is_long("1") is False
    assert _is_long("UNKNOWN") is False
    assert _is_long("") is False
