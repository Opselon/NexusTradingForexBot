"""Tests for the pro_auto console telemetry ring (Agent-5 P2-B).

After the extraction these exercise nexus_scalp.news.pro_auto_console
directly AND via the pro_auto facade re-export (both paths must work —
brief #21/#73 contract-test rule).
"""
from __future__ import annotations

import importlib

import pytest


def _mod():
    return importlib.import_module("nexus_scalp.news.pro_auto_console")


@pytest.fixture()
def console():
    mod = _mod()
    mod.reset_console_for_tests()
    return mod


class TestConsoleRing:
    def test_push_assigns_monotonic_seq_and_ts(self, console):
        console.push_console({"kind": "info", "msg": "a"})
        console.push_console({"kind": "info", "msg": "b"})
        hist = console.get_console_history(limit=10)
        assert [e["seq"] for e in hist] == [1, 2]
        assert all(e.get("ts") for e in hist)

    def test_bounded_ring(self, console):
        for i in range(700):
            console.push_console({"kind": "info", "msg": f"m{i}"})
        assert console.console_status()["size"] <= 500
        # latest seq survives the ring eviction
        assert console.console_status()["latest_seq"] == 700

    def test_since_seq_replay(self, console):
        for i in range(5):
            console.push_console({"kind": "info", "msg": f"m{i}"})
        out = console.get_console_history(limit=200, since_seq=3)
        assert [e["seq"] for e in out] == [4, 5]
        # bounded limit honored
        out2 = console.get_console_history(limit=1, since_seq=0)
        assert len(out2) == 1

    def test_limit_bounds(self, console):
        for i in range(10):
            console.push_console({"kind": "info", "msg": f"m{i}"})
        assert len(console.get_console_history(limit=0)) == 1  # min 1
        assert len(console.get_console_history(limit=10_000)) == 10  # cap 500

    def test_facade_reexport_works(self):
        """pro_auto must keep exposing the console API (compat surface)."""
        pro_auto = importlib.import_module("nexus_scalp.news.pro_auto")
        assert hasattr(pro_auto, "get_console_history")
        assert hasattr(pro_auto, "console_status")
        assert callable(pro_auto._console_push)
