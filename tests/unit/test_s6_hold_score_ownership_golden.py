"""Agent-5 S6-escalation COMMIT 1 golden: HoldScoreLedger ownership.

State-parity tests for the hold-score boundary: the manager's historical
attribute names must return the LEDGER's live dicts (single source of truth),
drops are ticket-scoped, and the ledger stores state only (no policy/eval/
broker/audit authority).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nexus_scalp.execution.hold_score_ledger import HoldScoreLedger

A, B = 201, 202


class TestHoldScoreOwnership:
    def test_manager_properties_return_live_ledger_dicts(self):
        """Compat properties expose the ledger's dicts (not copies)."""
        import nexus_scalp.execution.order_manager as om_mod

        src = Path(om_mod.__file__).read_text(encoding="utf-8")
        for f in (
            "_hold_score_tracker",
            "_base_hold_score_tracker",
            "_last_reasons_tracker",
            "_last_hold_eval_time",
        ):
            assert f"self._hold_scores.{f}" in src
            # property accessor exists
            assert f"def {f}(self) -> dict:" in src

    def test_ledger_state_roundtrip_and_drop(self):
        led = HoldScoreLedger()
        led._hold_score_tracker[A] = 42
        led._base_hold_score_tracker[A] = 55
        led._last_reasons_tracker[A] = ["GIVEBACK", "STALE"]
        led._last_hold_eval_time[A] = 1234.5

        led.drop_ticket(A)
        assert A not in led._hold_score_tracker
        assert A not in led._base_hold_score_tracker
        assert A not in led._last_reasons_tracker
        assert A not in led._last_hold_eval_time

    def test_ticket_isolation(self):
        led = HoldScoreLedger()
        led._hold_score_tracker[A] = 10
        led._hold_score_tracker[B] = 90
        led.drop_ticket(A)
        assert led._hold_score_tracker[B] == 90

    def test_no_authority(self):
        src = Path(HoldScoreLedger.__module__.replace(".", "/") + ".py")
        root = Path(__import__("nexus_scalp.execution.hold_score_ledger", fromlist=["x"]).__file__)
        text = root.read_text(encoding="utf-8")
        for banned in ("adapter", "order_send", "close_position", "IMT5Port",
                       "AuditRepository", "TelegramNotifier", "evaluate_profit_giveback"):
            assert banned not in text, f"ledger must not gain {banned} authority"

    def test_cleanup_bundle_still_releases_hold_state(self):
        """The manager's atomic cleanup bundle pops via the live-dict property."""
        import nexus_scalp.execution.order_manager as om_mod

        src = Path(om_mod.__file__).read_text(encoding="utf-8")
        bundle = src.split("def _cleanup_ticket_state")[1].split("def ")[0]
        for f in ("_hold_score_tracker", "_base_hold_score_tracker",
                  "_last_reasons_tracker"):
            assert f"self.{f}," in bundle, f"cleanup must keep {f} in the atomic bundle"
