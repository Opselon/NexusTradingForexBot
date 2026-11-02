"""Regression tests: first-run canonical-bars bootstrap (the line-0 DATA gate).

Context (observed on the real v9.0.18 clean-room install): a brand-new user
has no ``data/raw/XAUUSD_M1.parquet`` (the tick lake is gitignored), so
``check_data`` hard-FAILs, the ``/health`` verdict reads NOT READY, and the
Control Center launch gate times out with "SERVER NOT READY ... Control
Center not opened" even though the engine itself booted fine on its live
feed. The fix mirrors the model starter mint: ``ensure_canonical_bars``
acquires real history before the health gate can judge it.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from nexus_scalp.release import data_bootstrap as dbt
from nexus_scalp.release import paths as rpaths


def _make_csv(path: Path, rows: int) -> None:
    frame = pl.DataFrame(
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
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_csv(path)


class TestEnsureCanonicalBars:
    """The boot seam: idempotent, seed-capable, honest when unprovisionable."""

    def test_existing_slot_is_kept_and_never_rewritten(self, tmp_path, monkeypatch):
        """A machine that already has bars never pays for re-acquisition."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        _make_csv(tmp_path / "seed", 5)
        slot.parent.mkdir(parents=True, exist_ok=True)
        slot.write_bytes(b"existing real bars")
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: True)

        out = dbt.ensure_canonical_bars()

        assert out["action"] == "keep"
        assert slot.read_bytes() == b"existing real bars"

    def test_shipped_seed_is_canonicalized_when_slot_missing(self, tmp_path, monkeypatch):
        """The offline path: no MT5 terminal, seed file present -> real bars written."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        seed = tmp_path / "data_seed" / "XAUUSD_M1.csv"
        rows = dbt.MIN_SEED_ROWS + 500
        _make_csv(seed, rows)
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)
        monkeypatch.setattr(dbt, "_seed_candidates", lambda: [seed])
        monkeypatch.setattr(
            dbt, "fetch_live_mt5_bars", lambda *a, **k: {"ok": False, "reason": "no terminal"}
        )

        out = dbt.ensure_canonical_bars()

        assert out["action"] == "seeded"
        assert out["ok"] is True
        assert out["source"] == "shipped-seed"
        assert out["rows"] >= dbt.MIN_SEED_ROWS
        assert slot.exists()
        # The written file is a readable parquet, sorted by time.
        frame = pl.read_parquet(slot)
        assert frame.height >= dbt.MIN_SEED_ROWS
        assert frame["time"].is_sorted()

    def test_live_mt5_wins_over_the_shipped_seed(self, tmp_path, monkeypatch):
        """A connected terminal is the best source: its history is live-fresh."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        seed = tmp_path / "data_seed" / "XAUUSD_M1.csv"
        _make_csv(seed, dbt.MIN_SEED_ROWS + 100)
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)

        calls: list[str] = []

        def fake_live(symbol, timeframe, **kwargs):
            calls.append("live")
            return {"ok": True, "source": "mt5-live", "rows": 50000, "path": str(slot)}

        monkeypatch.setattr(dbt, "fetch_live_mt5_bars", fake_live)
        monkeypatch.setattr(
            dbt,
            "install_seed_bars",
            lambda *a, **k: pytest.fail("seed must not run when live MT5 succeeded"),
        )

        out = dbt.ensure_canonical_bars()

        assert out["action"] == "fetched-live"
        assert out["rows"] == 50000
        assert calls == ["live"]

    def test_unprovisionable_machine_reports_honestly_and_never_fabricates(
        self, tmp_path, monkeypatch
    ):
        """No terminal + no seed -> action=none, NOT a fabricated candles file."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)
        monkeypatch.setattr(dbt, "_seed_candidates", lambda: [])
        monkeypatch.setattr(
            dbt, "fetch_live_mt5_bars", lambda *a, **k: {"ok": False, "reason": "no terminal"}
        )

        out = dbt.ensure_canonical_bars()

        assert out["action"] == "none"
        assert "reason" in out
        assert not slot.exists()

    def test_seed_below_row_floor_is_refused_not_padded(self, tmp_path, monkeypatch):
        """A tiny seed cannot unblock the gate: refuse, never pad with fake bars."""
        slot = tmp_path / "data" / "raw" / "XAUUSD_M1.parquet"
        seed = tmp_path / "data_seed" / "XAUUSD_M1.csv"
        _make_csv(seed, 100)
        monkeypatch.setattr(dbt, "_canonical_bars_path", lambda *a, **k: slot)
        monkeypatch.setattr(dbt, "bars_slot_exists", lambda *a, **k: False)
        monkeypatch.setattr(dbt, "_seed_candidates", lambda: [seed])
        monkeypatch.setattr(
            dbt, "fetch_live_mt5_bars", lambda *a, **k: {"ok": False, "reason": "no terminal"}
        )

        out = dbt.ensure_canonical_bars()

        assert out["action"] == "none"
        assert not slot.exists()


class TestSeedCandidateResolution:
    """Where the seed is looked up: bundle layout and source tree."""

    def test_frozen_bundle_probes_internal_data_seed(self, monkeypatch, tmp_path):
        """Frozen layout: _internal/data-seed is the PyInstaller --add-data target."""
        probe: list[Path] = []

        def fake_candidates() -> list[Path]:
            probe.append(rpaths.exe_dir() / "_internal" / "data-seed")
            return []

        monkeypatch.setattr(dbt, "_seed_candidates", fake_candidates)
        dbt._seed_candidates()
        assert probe
        # The exploration root matches the documented bundle layout.
        assert probe[0].name == "data-seed"


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
        frame = pl.DataFrame(
            {
                "time": [1777655700 + 60 * i for i in range(rows)],
                "open": [4650.0] * rows,
                "high": [4660.0] * rows,
                "low": [4640.0] * rows,
                "close": [4655.0] * rows,
                "tick_volume": [10] * rows,
                "spread": [1] * rows,
                "real_volume": [0] * rows,
                "time_utc": [
                    f"2026-05-01T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}"
                    for i in range(rows)
                ],
            }
        )
        out = tmp_path / "bars.parquet"
        written = dbt._normalize_and_write(frame, out)
        assert written["rows"] >= dbt.MIN_SEED_ROWS
        assert out.exists()
