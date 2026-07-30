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

    with pytest.raises(RuntimeError, match="MT5 Adapter is not connected"):
        adapter.get_account_info()
