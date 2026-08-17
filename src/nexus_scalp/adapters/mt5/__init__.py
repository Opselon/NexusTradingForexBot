"""
MT5 Adapter Package
====================

Direct MetaTrader 5 Win32 adapter + broker-aware provider snapshots.

IMPORTANT (circular-import contract):
    - `nexus_scalp.ports.mt5_port` imports provider snapshot types from
      `nexus_scalp.adapters.mt5.providers` and the connection state from
      `nexus_scalp.adapters.mt5.diagnostics`.
    - `nexus_scalp.adapters.mt5.mt5_adapter` imports `IMT5Port` from the port.
    This package __init__ therefore performs NO eager re-export of the port
    symbols - importing `nexus_scalp.adapters.mt5.providers` must NOT trigger
    `ports.mt5_port` while it is still initializing (circular-import guard).

Use the explicit submodule paths instead:
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
    from nexus_scalp.adapters.mt5.providers import AccountSnapshot
    from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState
"""
