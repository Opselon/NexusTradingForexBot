"""Regression tests: first-run LIVE bars bootstrap (the line-0 DATA gate).

Context (observed on the real v9.0.18 clean-room install): a brand-new user
has no ``data/raw/XAUUSD_M1.parquet`` (the tick lake is gitignored), so
``check_data`` hard-FAILs, the ``/health`` verdict reads NOT READY, and the
Control Center launch gate times out with "SERVER NOT READY ... Control
Center not opened" even though the engine itself booted fine on its live
feed.

PRODUCT CONTRACT (operator direction, 2026-11): the bootstrap must NEVER
write a static/shipped dataset. The only acceptable history is fresh bars
pulled from the operator's own MetaTrader 5 terminal after the console asks
them to open it and log in. These tests pin that contract: no seed fallback
exists, an unavailable terminal leaves the DATA gate honestly FAILED, and
nothing is ever fabricated.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from nexus_scalp.release import data_bootstrap as dbt


def _make_frame(rows: int) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "time": [1777655700 + 60 * i for i in range(rows)],
            "open": [4656.13 + (i % 100) * 0.01 for i in range(rows)],
            "high": [4660.16 + (i % 100) * 0.01 for i in range(rows)],
            "low": [4654.96 + (i % 100) * 0.01 for i in range(rows)],
            "close": [4655.84 + (i % 100) * 0.01 for i in range(rows)],
            "tick_volume": [642 for _ in range(rows)],
            "spread": [4 for _ in range(rows)],
            "real_volume": [0 for _ in range(rows)],
            "time_utc": [
                f"2026-05-01T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}"
                for i in range(rows)
            ],
        }
    )


def _seed_anywhere() -> list[Path]:
    """There must be NO seed lookup left anywhere in the module."""
    import inspect

    source = inspect.getsource(dbt)
    assert "install_seed_bars" not in source, "seed fallback must not exist"
    assert "_seed_candidates" not in source, "seed candidate lookup must not exist"
    return []


class TestNoStaticSeedContract:
    """The product rule: history comes from the operator's broker, or not at all."""

    def test_module_exports_no_seed_function(self):
        """``install_seed_bars`` / seed machinery must be gone from the public API."""
        _seed_anywhere()
        assert not hasattr(dbt, "install_seed_bars")
        assert not hasattr(dbt, "_seed_candidates")
        assert "install_seed_bars" not in dbt.__all__

    def test_no_seed_directory_is_probed(self, tmp_path, monkeypatch):
        """A leftover data_seed dir on disk must never be picked up."""
        (tmp_path / "data_seed").mkdir()
        (tmp_path / "data_seed" / "XAUUSD_M1.csv").write_text("not real data")

        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)
        monkeypatch.setattr(
            dbt, "fetch_live_mt5_bars", lambda *a, **k: {"ok": False, "reason": "no terminal"}
        )

        out = dbt.ensure_canonical_bars(interactive=False)

        assert out["action"] == "none"
        assert not slot.exists()


class TestEnsureCanonicalBars:
    """The boot seam: idempotent, live-first, honest when unprovisionable."""

    def test_existing_slot_is_kept_and_never_rewritten(self, tmp_path, monkeypatch):
        """A machine that already has bars never pays for re-acquisition."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        slot.parent.mkdir(parents=True, exist_ok=True)
        slot.write_bytes(b"existing real bars")
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: True)

        out = dbt.ensure_canonical_bars()

        assert out["action"] == "keep"
        assert slot.read_bytes() == b"existing real bars"

    def test_live_mt5_fetch_fast_path(self, tmp_path, monkeypatch):
        """Terminal already up: bars are fetched immediately, no human prompt."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)

        calls: list[str] = []

        def fake_live(symbol, timeframe, **kwargs):
            calls.append("live")
            return {"ok": True, "source": "mt5-live", "rows": 50000, "path": str(slot)}

        monkeypatch.setattr(dbt, "fetch_live_mt5_bars", fake_live)

        out = dbt.ensure_canonical_bars(interactive=True, console=None)

        assert out["action"] == "fetched-live"
        assert out["rows"] == 50000
        assert calls == ["live"]

    def test_unprovisionable_machine_reports_honestly_and_never_fabricates(
        self, tmp_path, monkeypatch
    ):
        """No terminal -> action=none, NOT a fabricated candles file."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)
        monkeypatch.setattr(
            dbt, "fetch_live_mt5_bars", lambda *a, **k: {"ok": False, "reason": "no terminal"}
        )

        out = dbt.ensure_canonical_bars(interactive=False)

        assert out["action"] == "none"
        assert "reason" in out
        assert not slot.exists()

    def test_non_interactive_never_prompts_or_waits(self, tmp_path, monkeypatch):
        """Service/CI contexts: one attempt, no console prompt, no polling loop."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)

        fetch_calls: list[int] = []
        import time as _time

        start = _time.monotonic()

        def fake_live(symbol, timeframe, **kwargs):
            fetch_calls.append(1)
            return {"ok": False, "reason": "no terminal"}

        monkeypatch.setattr(dbt, "fetch_live_mt5_bars", fake_live)

        out = dbt.ensure_canonical_bars(interactive=False, wait_seconds=300)

        assert out["action"] == "none"
        # Exactly one fetch attempt (no polling loop) and it returned fast.
        assert len(fetch_calls) == 1
        assert _time.monotonic() - start < 5.0

    def test_interactive_polls_until_terminal_available(self, tmp_path, monkeypatch):
        """Operator flow: failures then success — the retry loop acquires live bars."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)

        attempts: list[int] = []

        def fake_live(symbol, timeframe, **kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                return {"ok": False, "reason": "terminal not connected"}
            return {"ok": True, "source": "mt5-live", "rows": 42_000, "path": str(slot)}

        monkeypatch.setattr(dbt, "fetch_live_mt5_bars", fake_live)
        # Never ask a human; keep the polling loop on a short leash.
        monkeypatch.setattr(dbt, "_prompt_retry", lambda console, deadline: True)

        class FakeConsole:
            def print(self, *a, **k):
                pass

        out = dbt.ensure_canonical_bars(
            interactive=True,
            console=FakeConsole(),
            wait_seconds=30.0,
            poll_interval=0.01,
        )

        assert out["action"] == "fetched-live"
        assert out["rows"] == 42_000
        assert len(attempts) == 3

    def test_interactive_stops_when_operator_declines(self, tmp_path, monkeypatch):
        """Operator says "no" -> stop polling, honest action=none, no file."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)
        monkeypatch.setattr(
            dbt, "fetch_live_mt5_bars", lambda *a, **k: {"ok": False, "reason": "no terminal"}
        )
        monkeypatch.setattr(dbt, "_prompt_retry", lambda console, deadline: False)

        class FakeConsole:
            def print(self, *a, **k):
                pass

        out = dbt.ensure_canonical_bars(interactive=True, console=FakeConsole(), wait_seconds=30.0)

        assert out["action"] == "none"
        assert "reason" in out
        assert not slot.exists()

    def test_interactive_times_out_honestly(self, tmp_path, monkeypatch):
        """Timeout elapsed with no terminal -> action=none, DATA gate stays loud."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)
        monkeypatch.setattr(
            dbt, "fetch_live_mt5_bars", lambda *a, **k: {"ok": False, "reason": "no terminal"}
        )
        monkeypatch.setattr(dbt, "_prompt_retry", lambda console, deadline: True)

        class FakeConsole:
            def print(self, *a, **k):
                pass

        out = dbt.ensure_canonical_bars(
            interactive=True,
            console=FakeConsole(),
            wait_seconds=0.05,
            poll_interval=0.01,
        )

        assert out["action"] == "none"
        assert not slot.exists()


class TestNormalizeAndWrite:
    """The cleaner: same contract as `nexus data-restore`."""

    def test_duplicate_and_unsorted_rows_are_cleaned(self, tmp_path):
        frame = pl.DataFrame(
            {
                "time": [1777655760, 1777655700, 1777655700],
                "open": [1.0, 2.0, 2.0],
                "high": [1.0, 2.0, 2.0],
                "low": [1.0, 2.0, 2.0],
                "close": [1.0, 2.0, 2.0],
                "tick_volume": [1, 2, 2],
                "spread": [1, 2, 2],
                "real_volume": [0, 0, 0],
                "time_utc": ["2026-05-01T17:16:00", "2026-05-01T17:15:00", "2026-05-01T17:15:00"],
            }
        )
        out = tmp_path / "bars.parquet"
        with pytest.raises(ValueError):
            # too few rows after cleaning -> refused, never padded
            dbt._normalize_and_write(frame, out)

    def test_canonical_writes_sorted_monotonic_parquet(self, tmp_path):
        rows = 11_000
        frame = _make_frame(rows)
        out = tmp_path / "bars.parquet"
        written = dbt._normalize_and_write(frame, out)
        assert written["rows"] >= dbt.MIN_SEED_ROWS
        assert out.exists()
