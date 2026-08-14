"""
Cross-Platform Remote Gateway Port Contract
===========================================
Abstract Port defining RPC interface for remote MT5 communication across containers/hosts.
"""

from abc import ABC, abstractmethod
from typing import Any


class IGatewayPort(ABC):
    """
    Abstract contract for low-latency remote MT5 gateway clients.
    """

    @abstractmethod
    async def ping(self) -> float:
        """Measures RTT latency to remote gateway in milliseconds."""
        pass

    @abstractmethod
    async def execute_remote_command(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatches HMAC-authenticated payload command to remote gateway host."""
        pass
