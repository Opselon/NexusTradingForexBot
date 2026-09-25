"""DATA-RAW-03: release/health check_data() — canonical parquet gate.

The doctor DATA category is a first-run honesty gate: a missing canonical
``data/raw/XAUUSD_M1.parquet`` must read FAIL / NOT_INITIALIZED and block
training. This suite pins the contract added on top of that gate:

* the FAIL / NOT_INITIALIZED verdict survives every change (never weakened);
* a missing parquet that has a readable sibling/configured bars source points
  at the exact ``nexus data-restore --source <path>`` repair command;
* a PASS reports the row count and the first/last bar time span;
* duplicate / non-monotonic bar times surface as WARNING;
* the canonical path falls back to repo-root-relative resolution when the
  CWD-relative lookup misses, without changing existing CWD-relative PASS.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

from nexus_scalp.release import health as H
from nexus_scalp.release import state_taxonomy as tax

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_START = dt.datetime(2026, 5, 1, 17, 15, tzinfo=dt.UTC)


def _bars(n: int, start: dt.datetime = DEFAULT_START, step: int = 1) -> object:
    import polars as pl

    return pl.DataFrame(
        {
            "time": [int((start + dt.timedelta(minutes=step * i)).timestamp()) for i in range(n)],
            "open": [2000.0] * n,
            "high": [2001.0] * n,
            "low": [1999.0] * n,
            "close": [2000.5] * n,
            "tick_volume": [1] * n,
            "spread": [10] * n,
            "real_volume": [0] * n,
            "time_utc": [start + dt.timedelta(minutes=step * i) for i in range(n)],
        }
    )


def _epoch_bars(n: int, start: dt.datetime = DEFAULT_START) -> object:
    """The MT5 layout: epoch-seconds ``time`` column, no ``time_utc``."""
    import polars as pl

    return pl.DataFrame(
        {
            "time": [int((start + dt.timedelta(minutes=i)).timestamp()) for i in range(n)],
            "open": [2000.0] * n,
            "high": [2001.0] * n,
            "low": [1999.0] * n,
            "close": [2000.5] * n,
        }
    )


def _engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> H.HealthEngine:
    monkeypatch.chdir(tmp_path)
    # A fresh HOME/LOCALAPPDATA keeps pydantic-settings from inheriting an
    # operator config — the PaperDataConfig default raw_bars_path is probed.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    return H.HealthEngine()


@pytest.fixture()
def repo_root(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point health's repo-root resolution at the test sandbox."""
    monkeypatch.setattr(H, "_repo_root", lambda: REPO_ROOT)
    return REPO_ROOT


@pytest.fixture()
def repo_root_no_csv(monkeypatch: pytest.MonkeyPatch) -> Path:
    """``repo_root`` guaranteed free of stray repo-root ``data/raw`` artifacts.

    The integration worktree is a real checkout: a locally-restored
    ``data/raw/XAUUSD_M1.{parquet,csv}`` (gitignored, operator-local, never in
    the PR) can leak into the probe order and satisfy an earlier candidate than
    the one under test. This fixture points repo-root resolution at a clean
    temp dir so each probe candidate is the ONLY candidate.
    """
    root = Path(tempfile.mkdtemp())
    monkeypatch.setattr(H, "_repo_root", lambda: root)
    return root


class _FakeConfig:
    """Minimal stand-in exposing a configured REPLAY ``raw_bars_path``."""

    def __init__(self, raw_bars_path: str) -> None:
        self.paper_data = type("PaperData", (), {"raw_bars_path": raw_bars_path})()


class _FakeConfigModule:
    """``nexus_scalp.configuration.config`` replacement for probing tests.

    ``check_data`` imports ``AppConfig`` lazily from that module, so swapping the
    module exercises the configured-raw_bars_path probe without depending on
    pydantic-settings attribute patching semantics.
    """

    def __init__(self, backup: Path) -> None:
        self.AppConfig = lambda: _FakeConfig(str(backup))


def _write(frame: object, *segments: str) -> Path:
    raw = Path(*segments)
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / "XAUUSD_M1.parquet"
    frame.write_parquet(path)
    return path


# ---------------------------------------------------------------------------
# 1. The gate is never weakened
# ---------------------------------------------------------------------------
class TestMissingParquetStillFails:
    def test_missing_parquet_is_fail_not_initialized(self, tmp_path, monkeypatch):
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.category == "DATA"
        assert entry.verdict == "FAIL"
        assert entry.state == tax.NOT_INITIALIZED
        assert "canonical bars file missing" in entry.reason
        assert "XAUUSD_M1.parquet" in entry.reason

    def test_missing_parquet_keeps_legacy_remedy(self, tmp_path, monkeypatch):
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "FAIL"
        # The original MT5/backup suggestion is preserved verbatim.
        assert "copy_rates" in entry.suggestion
        assert "restore a data/raw backup" in entry.suggestion

    def test_missing_parquet_has_no_restore_hint_when_no_source(
        self, tmp_path, monkeypatch, repo_root
    ):
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "FAIL"
        assert "data-restore" not in entry.suggestion

    def test_fail_still_blocks_ready(self, tmp_path, monkeypatch):
        eng = _engine(tmp_path, monkeypatch)
        entries = [e for e in eng.run_all() if e.category == "DATA"]
        assert entries and entries[0].verdict == "FAIL"
        verdict, _ = eng.overall(entries)
        # A DATA FAIL degrades the aggregate; it must never read READY.
        assert verdict != "READY"

    def test_missing_parquet_reports_documented_relative_path(self, tmp_path, monkeypatch):
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert "XAUUSD_M1.parquet" in entry.reason
        assert "canonical bars file missing" in entry.reason


# ---------------------------------------------------------------------------
# 2. Actionable repair command when a bars source exists
# ---------------------------------------------------------------------------
class TestRepairHintForAvailableSource:
    def test_csv_sibling_yields_exact_restore_command(self, tmp_path, monkeypatch, repo_root):
        raw = repo_root / "data" / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        csv = raw / "XAUUSD_M1.csv"
        _bars(50).write_csv(csv)
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "FAIL"
        assert entry.state == tax.NOT_INITIALIZED
        assert f"nexus data-restore --source {csv}" in entry.suggestion
        assert "canonicalize it into data/raw/XAUUSD_M1.parquet" in entry.suggestion

    def test_csv_sibling_cwd_relative_is_found(self, tmp_path, monkeypatch, repo_root):
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        _bars(50).write_csv(raw / "XAUUSD_M1.csv")
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "FAIL"
        assert "data-restore" in entry.suggestion

    def test_configured_raw_bars_path_is_probed(self, tmp_path, monkeypatch, repo_root_no_csv):
        backup = tmp_path / "backup" / "XAUUSD_M1.parquet"
        backup.parent.mkdir(parents=True, exist_ok=True)
        _bars(100).write_parquet(backup)
        monkeypatch.delitem(sys.modules, "nexus_scalp.configuration.config", raising=False)
        monkeypatch.setitem(
            sys.modules, "nexus_scalp.configuration.config", _FakeConfigModule(backup)
        )
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "FAIL"
        assert f"nexus data-restore --source {backup}" in entry.suggestion

    def test_empty_sibling_is_not_a_source(self, tmp_path, monkeypatch, repo_root):
        raw = repo_root / "data" / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "XAUUSD_M1.csv").write_text("", encoding="utf-8")
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "FAIL"
        assert "data-restore" not in entry.suggestion


# ---------------------------------------------------------------------------
# 3. PASS reports row count + time span
# ---------------------------------------------------------------------------
class TestPassReportsCountAndSpan:
    def test_pass_reason_has_rows_and_span(self, tmp_path, monkeypatch):
        _write(_bars(20000), str(tmp_path), "data", "raw")
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "PASS"
        assert entry.state == tax.AVAILABLE
        assert "20000 rows" in entry.reason
        assert "span" in entry.reason
        # 20000 M1 bars from 2026-05-01 17:15 -> 2026-05-15 14:34 UTC
        assert "2026-05-01 17:15" in entry.reason
        assert "2026-05-15 14:34" in entry.reason
        assert "UTC" in entry.reason

    def test_pass_reports_epoch_time_column(self, tmp_path, monkeypatch):
        _write(_epoch_bars(20000), str(tmp_path), "data", "raw")
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "PASS"
        assert "2026-05-01 17:15" in entry.reason
        assert "2026-05-15 14:34" in entry.reason

    def test_fewer_than_10k_rows_is_warning(self, tmp_path, monkeypatch):
        _write(_bars(500), str(tmp_path), "data", "raw")
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "WARNING"
        assert "500 rows" in entry.reason
        assert "walk-forward needs >= 10k" in entry.reason

    def test_old_file_is_warning(self, tmp_path, monkeypatch):
        path = _write(_bars(20000), str(tmp_path), "data", "raw")
        old = time.time() - 45 * 86400
        os.utime(path, (old, old))
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "WARNING"
        assert "days ago" in entry.reason

    def test_unreadable_parquet_is_fail(self, tmp_path, monkeypatch):
        raw = tmp_path / "data" / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "XAUUSD_M1.parquet").write_text("not a parquet", encoding="utf-8")
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "FAIL"
        assert "unreadable" in entry.reason


# ---------------------------------------------------------------------------
# 4. Duplicate / non-monotonic bar warnings (independent of the 10k gate)
# ---------------------------------------------------------------------------
class TestBarQualityWarnings:
    def test_duplicate_bar_times_warn(self, tmp_path, monkeypatch):
        import polars as pl

        base = _bars(500)
        # Descending tail appended to an ascending base: 5 duplicated stamps
        # AND 1 non-monotonic boundary — both warnings must surface.
        frame = pl.concat([base, base.tail(5).reverse()])
        _write(frame, str(tmp_path), "data", "raw")
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "WARNING"
        assert "5 duplicate bar times" in entry.reason
        assert "non-monotonic" in entry.reason

    def test_non_monotonic_bar_times_warn(self, tmp_path, monkeypatch):
        import polars as pl

        frame = _bars(500, step=-1)  # descending stamps
        _write(frame, str(tmp_path), "data", "raw")
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "WARNING"
        assert "non-monotonic" in entry.reason

    def test_clean_file_has_no_quality_warning(self, tmp_path, monkeypatch):
        _write(_bars(20000), str(tmp_path), "data", "raw")
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "PASS"
        assert "duplicate" not in entry.reason
        assert "non-monotonic" not in entry.reason


# ---------------------------------------------------------------------------
# 5. Repo-root-relative fallback resolution
# ---------------------------------------------------------------------------
class TestRepoRootFallback:
    def test_parquet_found_from_another_cwd(self, tmp_path, monkeypatch, repo_root):
        raw = repo_root / "data" / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        _bars(20000).write_parquet(raw / "XAUUSD_M1.parquet")
        # Run from a CWD where the relative path does NOT resolve.
        other = tmp_path / "elsewhere"
        other.mkdir()
        monkeypatch.chdir(other)
        eng = H.HealthEngine()
        entry = eng.check_data()
        assert entry.verdict == "PASS"
        assert entry.state == tax.AVAILABLE
        assert "20000 rows" in entry.reason

    def test_cwd_relative_pass_still_works(self, tmp_path, monkeypatch, repo_root):
        _write(_bars(20000), str(tmp_path), "data", "raw")
        eng = _engine(tmp_path, monkeypatch)
        entry = eng.check_data()
        assert entry.verdict == "PASS"
        assert "20000 rows" in entry.reason

    def test_repo_root_is_src_nexus_scalp_release_parents3(self):
        # health.py lives at src/nexus_scalp/release/health.py -> parents[3]
        here = Path(H.__file__).resolve()
        assert here.parents[3] == REPO_ROOT


def test_check_data_is_callable_without_data(tmp_path, monkeypatch):
    # The check stays dependency-light: constructing the engine never needs
    # polars, and the DATA probe fails closed rather than raising.
    eng = H.HealthEngine()
    assert callable(eng.check_data)
