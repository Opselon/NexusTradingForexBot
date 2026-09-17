"""BUG-305 regression (audit G1 remainder): the runtime broker-offset override
must be honoured by EVERY consumer, not just the epoch-conversion helper.

Audit G1 (docs/agent_handoffs/2026-09-07_NSE_audit_findings_and_status.txt:281)
flagged `BROKER_SERVER_UTC_OFFSET_MINUTES = 180` (adapters/mt5/providers.py:46)
as a hardcode that shifts all data + the accounting day boundary for half the
year (EET GMT+2 winter / EEST GMT+3 summer). A runtime override accessor was
added later (`get_broker_server_utc_offset_minutes`, providers.py:57) and wired
into `broker_epoch_to_utc` / `broker_history._epoch_utc` /
`incidents.timebase.timebase_event_chain`. But the accessor was only PARTIALLY
adopted: the native MT5 history/tick request windows (mt5_adapter.py) and the
LIVE entry maintenance-window guard (execution/lifecycle/dispatch.py) still
read the raw module constant, so an operator who sets
`NSE_BROKER_SERVER_UTC_OFFSET_MINUTES=120` got:
  * epoch OUTPUT conversion at 120min, but INPUT request windows built at 180min
    -> a net 60-minute window skew on reconcile-baseline history reads; and
  * a maintenance-window entry guard evaluated at 180min while the rest of the
    timebase moved to 120min -> entries gated on the WRONG clock (money path).
This is the BUG-303 class: a configurable seam with a bypass consumer.

The fix routes all four remaining reads through the accessor; with the env var
unset the behaviour is byte-identical to the pre-fix constant read (pinned).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from nexus_scalp.adapters.mt5 import mt5_adapter as adapter_mod
from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
from nexus_scalp.adapters.mt5.providers import (
    BROKER_SERVER_UTC_OFFSET_MINUTES,
    get_broker_server_utc_offset_minutes,
)

ENV_VAR = "NSE_BROKER_SERVER_UTC_OFFSET_MINUTES"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADAPTER_SRC = _REPO_ROOT / "src" / "nexus_scalp" / "adapters" / "mt5" / "mt5_adapter.py"
_DISPATCH_SRC = _REPO_ROOT / "src" / "nexus_scalp" / "execution" / "lifecycle" / "dispatch.py"


# ---------------------------------------------------------------------------
# Adapter harness: mocked module-level mt5 surface (no terminal required)
# ---------------------------------------------------------------------------


class _RawRow(dict):
    """Mimics the numpy structured scalar the MT5 package returns (mapping)."""


class _Harness:
    def __init__(self) -> None:
        self.captured: dict[str, Any] = {}
        self.adapter = DirectMT5Adapter(timeout=1000, retries=1)
        self.adapter._connected = True
        self._patch_mt5()

    def _patch_mt5(self) -> None:
        captured = self.captured

        class _FakeMT5:
            COPY_TICKS_ALL = 0x4000

            @staticmethod
            def copy_ticks_range(symbol, from_dt, to_dt, flags):
                captured["call"] = {"from_dt": from_dt, "to_dt": to_dt}
                return []

            @staticmethod
            def history_deals_get(*args, **kwargs):
                captured["deals"] = {"args": args, "kwargs": kwargs}
                return []

            @staticmethod
            def history_orders_get(*args, **kwargs):
                captured["orders"] = {"args": args, "kwargs": kwargs}
                return []

        self._patcher = patch.object(adapter_mod, "mt5", _FakeMT5)
        self._patcher.start()
        self._native_patcher = patch.object(adapter_mod, "HAS_NATIVE_MT5", True)
        self._native_patcher.start()

    def stop(self) -> None:
        self._patcher.stop()
        self._native_patcher.stop()


@pytest.fixture()
def harness() -> Any:
    h = _Harness()
    yield h
    h.stop()


# ---------------------------------------------------------------------------
# 1. Behaviour: the env override reaches the adapter request windows
# ---------------------------------------------------------------------------


class TestAdapterWindowsFollowEnv:
    def test_tick_input_window_shift_follows_env(
        self, harness: _Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_VAR, "120")
        start = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
        end = start + timedelta(minutes=5)
        harness.adapter.get_tick_history("XAUUSD", count=100, from_utc=start, to_utc=end)
        call = harness.captured["call"]
        assert call["from_dt"] == start + timedelta(minutes=120)
        assert call["to_dt"] == end + timedelta(minutes=120)

    def test_history_deals_default_window_follows_env(
        self, harness: _Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_VAR, "60")
        before = datetime.now(UTC)
        harness.adapter.get_history_deals(symbol="XAUUSD")
        after = datetime.now(UTC)
        start, end = harness.captured["deals"]["args"]
        # default window is [now + offset - 1d, now + offset]; the UPPER bound
        # must land on the CONFIGURED offset (60 min), not the 180 module
        # default — this is the split-brain the fix removes.
        assert before + timedelta(minutes=60) <= end <= after + timedelta(minutes=60)
        assert abs((end - start) - timedelta(days=1)) < timedelta(seconds=2)

    def test_history_orders_default_window_follows_env(
        self, harness: _Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_VAR, "60")
        before = datetime.now(UTC)
        harness.adapter.get_history_orders(symbol="XAUUSD")
        after = datetime.now(UTC)
        start, end = harness.captured["orders"]["args"]
        assert before + timedelta(minutes=60) <= end <= after + timedelta(minutes=60)
        assert abs((end - start) - timedelta(days=1)) < timedelta(seconds=2)

    def test_default_env_unset_is_byte_identical(
        self, harness: _Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No env -> the offset is the module default (pre-fix behaviour)."""
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert get_broker_server_utc_offset_minutes() == BROKER_SERVER_UTC_OFFSET_MINUTES
        start = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)
        end = start + timedelta(minutes=5)
        harness.adapter.get_tick_history("XAUUSD", count=100, from_utc=start, to_utc=end)
        call = harness.captured["call"]
        assert call["from_dt"] == start + timedelta(minutes=BROKER_SERVER_UTC_OFFSET_MINUTES)
        assert call["to_dt"] == end + timedelta(minutes=BROKER_SERVER_UTC_OFFSET_MINUTES)


# ---------------------------------------------------------------------------
# 2. Source contract: no executable bare-constant read may return
# ---------------------------------------------------------------------------


def _executable_constant_reads(path: Path) -> list[int]:
    """Line numbers where the raw BROKER_SERVER_UTC_OFFSET_MINUTES constant is
    actually LOADED by executable code (AST Name loads).

    Comment/docstring mentions are documentation, not behaviour, so a textual
    scan would false-positive on the explanatory prose inside docstrings; the
    AST sees only real references.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "BROKER_SERVER_UTC_OFFSET_MINUTES":
            hits.append(node.lineno)
    return sorted(set(hits))


@pytest.mark.parametrize("path", [_ADAPTER_SRC, _DISPATCH_SRC], ids=["mt5_adapter", "dispatch"])
def test_no_executable_bare_constant_read(path: Path) -> None:
    offenders = _executable_constant_reads(path)
    assert offenders == [], (
        f"{path.name} reads the raw BROKER_SERVER_UTC_OFFSET_MINUTES constant at "
        f"line(s) {offenders}; route it through get_broker_server_utc_offset_minutes() "
        f"so the runtime override actually applies (BUG-305)."
    )


def test_dispatch_entry_guard_uses_accessor() -> None:
    source = _DISPATCH_SRC.read_text(encoding="utf-8")
    assert "get_broker_server_utc_offset_minutes" in source, (
        "the live entry maintenance-window guard must use the runtime override"
    )
