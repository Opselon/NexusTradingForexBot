"""
Unit Tests - MT5 Adapter Logic
==============================
Tests Native MT5 adapter error handling and disconnected states.
"""

import pytest

from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter


def test_adapter_unconnected_state_raises_error() -> None:
    """Ensures querying market metrics on an unconnected adapter raises explicit error."""
    adapter = DirectMT5Adapter()
    assert not adapter.is_connected()

    with pytest.raises(RuntimeError, match="Failed to fetch account info from MT5"):
        adapter.get_account_info()
