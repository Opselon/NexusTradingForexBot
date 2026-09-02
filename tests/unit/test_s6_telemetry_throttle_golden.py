"""Agent-5 S6 STEP-A golden: TelemetryThrottle parity.

BUG-129 semantics: first emission allowed; immediate duplicate throttled;
elapsed interval (>= 3.0s) allowed; shared gate across telemetry + exit-log;
survives repeated calls; cleanup releases the ticket; compat property returns
the LIVE dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus_scalp.execution.telemetry_throttle import TelemetryThrottle

T = 201


class TestTelemetryThrottle:
    def test_first_emission_allowed(self):
        th = TelemetryThrottle()
        assert th.may_emit(T, 1000.0) is True

    def test_immediate_duplicate_throttled(self):
        th = TelemetryThrottle()
        th.record(T, 1000.0)
        assert th.may_emit(T, 1000.5) is False
        assert th.may_emit(T, 1002.9) is False

    def test_elapsed_interval_allowed(self):
        th = TelemetryThrottle()
        th.record(T, 1000.0)
        assert th.may_emit(T, 1003.0) is True
        assert th.may_emit(T, 1003.1) is True

    def test_state_survives_repeated_calls(self):
        th = TelemetryThrottle()
        th.record(T, 10.0)
        th.record(T, 20.0)
        th.record(T, 30.0)
        assert th.last_emit(T) == 30.0

    def test_ticket_isolation(self):
        th = TelemetryThrottle()
        th.record(201, 100.0)
        assert th.may_emit(202, 100.0) is True
        assert th.last_emit(202) == 0.0

    def test_drop_ticket(self):
        th = TelemetryThrottle()
        th.record(T, 100.0)
        th.drop_ticket(T)
        assert th.may_emit(T, 100.0) is True
        assert T not in th._last_telemetry_time

    def test_no_authority(self):
        root = Path(__import__("nexus_scalp.execution.telemetry_throttle", fromlist=["x"]).__file__)
        text = root.read_text(encoding="utf-8")
        for banned in (
            "adapter",
            "order_send",
            "close_position",
            "IMT5Port",
            "AuditRepository",
            "TelegramNotifier",
            "notifier",
        ):
            assert banned not in text

    def test_manager_property_live_dict(self):
        src = Path(
            __import__("nexus_scalp.execution.order_manager", fromlist=["x"]).__file__
        ).read_text(encoding="utf-8")
        assert "return self._telemetry._last_telemetry_time" in src
        # cleanup bundle still releases the entry via the live dict
        bundle = src.split("def _cleanup_ticket_state")[1].split("def ")[0]
        assert (
            "_last_telemetry_time.pop(ticket, None)" in bundle
            or "self._last_telemetry_time," in bundle
        )

    def test_manager_throttle_wiring(self):
        """The institutional telemetry block uses the throttle owner."""
        src = Path(
            __import__("nexus_scalp.execution.order_manager", fromlist=["x"]).__file__
        ).read_text(encoding="utf-8")
        assert "self._telemetry.last_emit(ticket)" in src
