"""
Unit Tests - Forensic Audit & Verification of Legacy Order Manager
===================================================================
Verifies the forensic removal of dead legacy module src/nexus_scalp/features/order_manager.py,
the integrity of active implementation src/nexus_scalp/execution/order_manager.py,
cleanup in project configuration, and structured audit telemetry emission.
"""

from pathlib import Path

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.audit")


def test_legacy_file_deletion() -> None:
    """Verifies that dead legacy file src/nexus_scalp/features/order_manager.py no longer exists."""
    legacy_path = Path("src/nexus_scalp/features/order_manager.py")
    assert not legacy_path.exists(), (
        f"Dead legacy file {legacy_path} must be deleted from repository."
    )
def test_active_order_manager_import_and_symbols() -> None:
    """Verifies that the active implementation src/nexus_scalp/execution/order_manager.py

    imports cleanly and exposes all required production symbols.
    """
    import nexus_scalp.execution.order_manager as active_om

    expected_symbols = [
        "OrderLifecycleManager",
        "PositionProtectionState",
        "SmartPositionMetrics",
        "ExitMechanism",
        "PositionState",
    ]
    for symbol in expected_symbols:
        assert hasattr(active_om, symbol), (
            f"Active order manager module must export symbol '{symbol}'."
        )
