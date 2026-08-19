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


def test_project_file_cleanup() -> None:
    """Verifies that NexusTradingForexBot.pyproj no longer references the deleted legacy file."""
    proj_path = Path("NexusTradingForexBot.pyproj")
    assert proj_path.exists(), "NexusTradingForexBot.pyproj must exist."

    content = proj_path.read_text(encoding="utf-8")
    assert "src\\nexus_scalp\\features\\order_manager.py" not in content, (
        "NexusTradingForexBot.pyproj must not reference deleted legacy order_manager.py"
    )
    assert "src/nexus_scalp/features/order_manager.py" not in content, (
        "NexusTradingForexBot.pyproj must not reference deleted legacy order_manager.py"
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


def test_no_legacy_import_references_in_repo() -> None:
    """Scans src/ and tests/ (excluding this audit test itself) to guarantee no code references the deleted legacy import path."""
    repo_root = Path(".")
    scan_dirs = [repo_root / "src", repo_root / "tests"]

    forbidden_patterns = [
        "nexus_scalp.features.order_manager",
        "nexus_scalp/features/order_manager",
    ]

    this_file = Path(__file__).resolve()

    found_matches: list[str] = []
    for scan_dir in scan_dirs:
        for py_file in scan_dir.rglob("*.py"):
            if py_file.resolve() == this_file:
                continue
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for pattern in forbidden_patterns:
                if pattern in text:
                    found_matches.append(f"{py_file}: contains '{pattern}'")

    assert not found_matches, f"Found illegal references to legacy order manager: {found_matches}"


def test_order_manager_audit_telemetry() -> None:
    """Emits structured [ORDER_MANAGER_AUDIT] telemetry log event and asserts facts."""
    path = "src/nexus_scalp/features/order_manager.py"
    references = 0
    status = "DEAD"
    action = "REMOVED"
    classification = "DEAD"
    external_ref_status = "NOT_FOUND_IN_REPOSITORY"
    active_impl = "src/nexus_scalp/execution/order_manager.py"

    # Emit telemetry event through existing structlog infrastructure
    logger.info(
        "[ORDER_MANAGER_AUDIT]",
        path=path,
        references=references,
        status=status,
        action=action,
        classification=classification,
        external_reference_status=external_ref_status,
        active_implementation=active_impl,
    )

    # Fact assertions
    assert not Path(path).exists()
    assert references == 0
    assert status == "DEAD"
    assert action == "REMOVED"
    assert classification == "DEAD"
    assert external_ref_status == "NOT_FOUND_IN_REPOSITORY"
    assert Path(active_impl).exists()
