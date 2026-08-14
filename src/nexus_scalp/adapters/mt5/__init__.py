"""
Port Interfaces
===============
Abstract contracts defining boundaries between core application domain and adapters.
"""

from nexus_scalp.ports.gateway_port import IGatewayPort
from nexus_scalp.ports.mt5_port import IMT5Port

__all__ = ["IGatewayPort", "IMT5Port"]
