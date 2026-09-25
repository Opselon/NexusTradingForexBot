"""DATA-RAW-02 — `nexus data-restore`: offline bars restore, no MT5 terminal.

Context (CONTRACT §1 evidence pass): `nexus data-fetch` is the ONLY shipped
producer of the canonical bars parquet ``data/raw/<SYM>_<TF>.parquet`` and it
requires a live, logged-in MT5 terminal. CI/agent machines and fresh installs
have none, so the DATA health check sits at FAIL/NOT_INITIALIZED and training
cannot run. ``data-restore`` is the offline producer: it copies a known-good
parquet backup (or converts a CSV export) into the canonical path and
re-validates the written file.

These tests exercise the REAL Typer command in-process (no subprocess, no
broker import anywhere in the restore path). Every test runs from an empty
temp CWD so nothing ever touches the operator's real ``data/raw``.

Contract under test (CONTRACT §2 LANE 2):
  1. options --source/--symbol/--timeframe/--out/--json (+ --force guard);
  2. copy-or-convert into the canonical parquet with the exact column schema;
  3. post-write validation (min rows, monotonic time, OHLC sanity);
  4. refuse-to-overwrite unless --force;
  5. deterministic xc codes: source missing -> EXIT_USAGE, unreadable/corrupt
     -> EXIT_RUNTIME, target exists and not --force -> EXIT_USAGE;
  6. importable/callable WITHOUT a live MT5 terminal.
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from nexus_scalp.cli import doctor as doctor_mod
from nexus_scalp.cli.main import app
from nexus_scalp.release import exit_codes as xc

runner = CliRunner()

#: The exact schema `nexus data-fetch` writes and every consumer reads.
CANON_COLUMNS = [
    "time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
    "time_utc",
]

_REAL_BACKUP = Path(
    "C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/nse_main_ref/data/raw/XAUUSD_M1.parquet"
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _bars_frame(
    rows: int, *, start_epoch: int = 1_777_655_700, price: float = 4656.0
) -> pl.DataFrame:
    """Deterministic, schema-canonical bars (1-minute cadence, sane OHLC)."""
    opens = [price + i * 0.001 for i in range(rows)]
    return pl.DataFrame(
        {
            "time": [start_epoch + 60 * i for i in range(rows)],
            "open": opens,
            "high": [o + 1.0 for o in opens],
            "low": [o - 1.0 for o in opens],
            "close": [o + 0.5 for o in opens],
            "tick_volume": [100 + i for i in range(rows)],
            "spread": [4 for _ in range(rows)],
            "real_volume": [0 for _ in range(rows)],
            "time_utc": [(start_epoch + 60 * i) * 1_000_000 for i in range(rows)],
        }
    ).with_columns(pl.col("time_utc").cast(pl.Datetime("us")))


@pytest.fixture
def backup_parquet(tmp_path: Path) -> Path:
    """A known-good parquet backup outside the worktree's data/raw."""
    p = tmp_path / "backup" / "XAUUSD_M1.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    _bars_frame(1500).write_parquet(p)
    return p


@pytest.fixture
def backup_csv(tmp_path: Path) -> Path:
    """A known-good CSV export (the legacy reader-only format)."""
    p = tmp_path / "backup" / "XAUUSD_M1.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    _bars_frame(1500).write_csv(p)
    return p


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty CWD so the canonical default path never hits real data/raw."""
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


def _invoke(args: list[str]):
    return runner.invoke(app, args)


def _json_of(res) -> dict:
    """Parse the trailing JSON document the CLI emits under --json."""
    lines = (res.stdout or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("{"):
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"no JSON document in output: {(res.stdout or '')[:300]!r}")


# ---------------------------------------------------------------------------
# 1. registration + MT5-free importability
# ---------------------------------------------------------------------------
def test_command_registered_with_documented_options() -> None:
    res = _invoke(["data-restore", "--help"])
    assert res.exit_code == 0
    text = res.stdout
    for flag in ("--source", "--symbol", "--timeframe", "--out", "--json"):
        assert flag in text
    assert "--force" in text


def test_restore_path_imports_no_mt5_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The restore path must stay importable with NO MT5 terminal available.

    The classic failure mode is an import-time ``import MetaTrader5``: on a
    machine without the terminal the DLL load fails and the command is
    unusable precisely when it is needed. Guard the contract structurally:
    block the broker modules and re-import the CLI clean.
    """
    import builtins
    import importlib
    import sys

    blocked = ("MetaTrader5", "nexus_scalp.adapters.mt5.mt5_adapter")
    real_import = builtins.__import__

    def _guard(name: str, *args, **kwargs):
        if name.split(".", maxsplit=1)[0] in blocked or name in blocked:
            raise ImportError(f"blocked for test: {name}")
        return real_import(name, *args, **kwargs)

    for mod in [m for m in list(sys.modules) if m.split(".")[0] in blocked]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    # A stale cached doctor module would defeat the import guard.
    monkeypatch.delitem(sys.modules, "nexus_scalp.cli.doctor", raising=False)
    monkeypatch.setattr(builtins, "__import__", _guard)

    mod = importlib.import_module("nexus_scalp.cli.doctor")
    assert mod._default_data_raw_path("XAUUSD", "M1").name == "XAUUSD_M1.parquet"
    names = [c.name for c in app.registered_commands if c.name]
    assert "data-restore" in names
    assert "data-fetch" in names


# ---------------------------------------------------------------------------
# 2. happy paths: parquet copy + CSV -> parquet conversion
# ---------------------------------------------------------------------------
def test_restore_from_parquet_backup(workspace: Path, backup_parquet: Path) -> None:
    res = _invoke(["data-restore", "--source", str(backup_parquet)])
    assert res.exit_code == xc.EXIT_OK, res.stdout
    out = workspace / "data" / "raw" / "XAUUSD_M1.parquet"
    assert out.exists()
    got = pl.read_parquet(out)
    assert got.columns == CANON_COLUMNS
    assert got.height == 1500
    # schema parity with the producer's dtypes
    assert got["time"].dtype == pl.Int64
    assert got["time_utc"].dtype == pl.Datetime("us")
    for col in ("open", "high", "low", "close"):
        assert got[col].dtype == pl.Float64
    for col in ("tick_volume", "spread", "real_volume"):
        assert got[col].dtype == pl.Int64
    # epoch-seconds contract: time == time_utc truncated to seconds
    assert bool((got["time"] * 1_000_000 == got["time_utc"].cast(pl.Int64)).all())
    assert got["time"].is_sorted()


def test_restore_from_csv_backup_converts_to_canonical_parquet(
    workspace: Path, backup_csv: Path
) -> None:
    res = _invoke(["data-restore", "--source", str(backup_csv)])
    assert res.exit_code == xc.EXIT_OK, res.stdout
    out = workspace / "data" / "raw" / "XAUUSD_M1.parquet"
    assert out.exists()
    got = pl.read_parquet(out)
    assert got.columns == CANON_COLUMNS
    assert got.height == 1500
    assert got["time"].dtype == pl.Int64
    assert got["time_utc"].dtype == pl.Datetime("us")
    assert bool((got["time"] * 1_000_000 == got["time_utc"].cast(pl.Int64)).all())


def test_restore_from_directory_picks_symbol_parquet(workspace: Path, backup_parquet: Path) -> None:
    res = _invoke(["data-restore", "--source", str(backup_parquet.parent)])
    assert res.exit_code == xc.EXIT_OK, res.stdout
    assert (workspace / "data" / "raw" / "XAUUSD_M1.parquet").exists()


def test_restore_from_directory_falls_back_to_csv(workspace: Path, backup_csv: Path) -> None:
    # keep only the CSV sibling in the directory
    for sibling in backup_csv.parent.iterdir():
        if sibling.suffix == ".parquet":
            sibling.unlink()
    res = _invoke(["data-restore", "--source", str(backup_csv.parent)])
    assert res.exit_code == xc.EXIT_OK, res.stdout
    got = pl.read_parquet(workspace / "data" / "raw" / "XAUUSD_M1.parquet")
    assert got.height == 1500


def test_restore_honours_symbol_timeframe_and_out(workspace: Path, backup_parquet: Path) -> None:
    custom = workspace / "elsewhere" / "XAUUSD_M5.parquet"
    res = _invoke(
        [
            "data-restore",
            "--source",
            str(backup_parquet),
            "--symbol",
            "xauusd",
            "--timeframe",
            "m5",
            "--out",
            str(custom),
        ]
    )
    assert res.exit_code == xc.EXIT_OK, res.stdout
    assert custom.exists()
    assert not (workspace / "data" / "raw" / "XAUUSD_M1.parquet").exists()


def test_restore_json_payload_contract(workspace: Path, backup_parquet: Path) -> None:
    res = _invoke(["data-restore", "--source", str(backup_parquet), "--json"])
    assert res.exit_code == xc.EXIT_OK, res.stdout
    data = _json_of(res)
    assert data["valid"] is True
    assert data["rows"] == 1500
    assert data["symbol"] == "XAUUSD"
    assert data["timeframe"] == "M1"
    assert data["exit_code"] == xc.EXIT_OK
    assert data["output"].endswith("XAUUSD_M1.parquet")
    assert "start" in data and "end" in data
    assert data["error"] is None


def test_restore_real_100k_backup_if_present(workspace: Path) -> None:
    """The operator's real 100k-bar MT5 capture (CONTRACT §1 fixture)."""
    if not _REAL_BACKUP.exists():
        pytest.skip(f"real backup not available: {_REAL_BACKUP}")
    res = _invoke(["data-restore", "--source", str(_REAL_BACKUP)])
    assert res.exit_code == xc.EXIT_OK, res.stdout
    got = pl.read_parquet(workspace / "data" / "raw" / "XAUUSD_M1.parquet")
    assert got.height == 100_000
    assert got.columns == CANON_COLUMNS
    assert got["time"].is_sorted()
    assert got["time"].n_unique() == got.height


# ---------------------------------------------------------------------------
# 3. refuse-to-overwrite unless --force
# ---------------------------------------------------------------------------
def test_refuses_to_overwrite_existing_target(workspace: Path, backup_parquet: Path) -> None:
    target = workspace / "data" / "raw" / "XAUUSD_M1.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = _bars_frame(999)
    original.write_parquet(target)
    res = _invoke(["data-restore", "--source", str(backup_parquet)])
    assert res.exit_code == xc.EXIT_USAGE, res.stdout
    # the operator's existing data is untouched
    assert pl.read_parquet(target).height == 999
    assert "refusing to overwrite" in res.stdout.lower()
    assert "--force" in res.stdout


def test_force_overwrites_existing_target(workspace: Path, backup_parquet: Path) -> None:
    target = workspace / "data" / "raw" / "XAUUSD_M1.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    _bars_frame(999).write_parquet(target)
    res = _invoke(["data-restore", "--source", str(backup_parquet), "--force"])
    assert res.exit_code == xc.EXIT_OK, res.stdout
    assert pl.read_parquet(target).height == 1500


# ---------------------------------------------------------------------------
# 4. deterministic failure modes / exit codes
# ---------------------------------------------------------------------------
def test_missing_source_is_exit_usage(workspace: Path) -> None:
    res = _invoke(["data-restore", "--source", str(workspace / "nope")])
    assert res.exit_code == xc.EXIT_USAGE, res.stdout
    assert "does not exist" in res.stdout.lower()
    jres = _invoke(["data-restore", "--source", str(workspace / "nope"), "--json"])
    data = _json_of(jres)
    assert data["exit_code"] == xc.EXIT_USAGE


def test_empty_source_option_is_exit_usage(workspace: Path) -> None:
    res = _invoke(["data-restore"])
    assert res.exit_code == xc.EXIT_USAGE, res.stdout
    assert "source" in res.stdout.lower()


def test_directory_without_bars_is_exit_usage(tmp_path: Path) -> None:
    empty = tmp_path / "emptydir"
    empty.mkdir()
    res = _invoke(["data-restore", "--source", str(empty)])
    assert res.exit_code == xc.EXIT_USAGE, res.stdout


def test_unsupported_source_extension_is_exit_usage(tmp_path: Path) -> None:
    junk = tmp_path / "bars.txt"
    junk.write_text("not a bars file")
    res = _invoke(["data-restore", "--source", str(junk)])
    assert res.exit_code == xc.EXIT_USAGE, res.stdout


def test_corrupt_parquet_is_exit_runtime(tmp_path: Path, workspace: Path) -> None:
    corrupt = tmp_path / "XAUUSD_M1.parquet"
    corrupt.write_bytes(b"not a real parquet payload at all")
    res = _invoke(["data-restore", "--source", str(corrupt)])
    assert res.exit_code == xc.EXIT_RUNTIME, res.stdout
    assert "could not read" in res.stdout.lower()
    jres = _invoke(["data-restore", "--source", str(corrupt), "--json"])
    data = _json_of(jres)
    assert data["exit_code"] == xc.EXIT_RUNTIME
    assert data["error"]
    # a failed read must not leave temp junk in the target directory
    raw_dir = workspace / "data" / "raw"
    assert not any(raw_dir.iterdir()) if raw_dir.exists() else True


def test_csv_with_unparseable_time_is_exit_runtime(tmp_path: Path, workspace: Path) -> None:
    """Every row fails time parsing -> normalize raises -> clean runtime error."""
    p = tmp_path / "XAUUSD_M1.csv"
    p.write_text(
        "time,open,high,low,close,tick_volume,spread,real_volume,time_utc\n"
        "garbage,1.0,2.0,0.5,1.5,1,1,0,not-a-date\n"
    )
    res = _invoke(["data-restore", "--source", str(p)])
    assert res.exit_code == xc.EXIT_RUNTIME, res.stdout


def test_too_few_rows_is_exit_runtime(tmp_path: Path, workspace: Path) -> None:
    """A stub backup below the min-rows gate is not a silent PASS."""
    p = tmp_path / "XAUUSD_M1.parquet"
    _bars_frame(10).write_parquet(p)
    res = _invoke(["data-restore", "--source", str(p)])
    assert res.exit_code == xc.EXIT_RUNTIME, res.stdout
    assert "minimum" in res.stdout.lower()
    # the invalid restore is never published: no canonical file, no temp file
    raw_dir = workspace / "data" / "raw"
    assert not (raw_dir / "XAUUSD_M1.parquet").exists()
    assert not any(raw_dir.iterdir()) if raw_dir.exists() else True


# ---------------------------------------------------------------------------
# 5. post-write validation actually rejects bad bars
# ---------------------------------------------------------------------------
def test_validation_detects_duplicate_timestamps(tmp_path: Path) -> None:
    """Duplicate timestamps are dropped by normalization, so exercise the
    duplicate branch directly on the written-file validator."""
    base = _bars_frame(1500)
    dup = pl.concat([base, base.head(1)]).sort("time")
    p = tmp_path / "target.parquet"
    dup.write_parquet(p)
    checks = doctor_mod._validate_restored_bars(p)
    assert checks["valid"] is False
    assert "duplicate" in checks["error"].lower()


def test_validation_detects_bad_ohlc(tmp_path: Path) -> None:
    bad = _bars_frame(1500)
    # high below the open/close candle -> OHLC sanity failure
    bad = bad.with_columns(pl.col("high").clip(0.0, 0.001))
    p = tmp_path / "target.parquet"
    bad.write_parquet(p)
    checks = doctor_mod._validate_restored_bars(p)
    assert checks["valid"] is False
    assert "OHLC" in checks["error"].upper()


def test_validation_detects_unsorted_time(tmp_path: Path) -> None:
    """A non-monotonic written file must be caught (the restore writer sorts,
    but the validator is the consumer's last line of defence)."""
    bad = _bars_frame(1500).with_columns(
        pl.col("time").reverse().alias("time"),
        pl.col("time_utc").reverse().alias("time_utc"),
    )
    p = tmp_path / "target.parquet"
    bad.write_parquet(p)
    checks = doctor_mod._validate_restored_bars(p)
    assert checks["valid"] is False
    assert "sorted" in checks["error"].lower()


def test_validation_rejects_short_file(tmp_path: Path) -> None:
    p = tmp_path / "target.parquet"
    _bars_frame(10).write_parquet(p)
    checks = doctor_mod._validate_restored_bars(p)
    assert checks["valid"] is False
    assert "minimum" in checks["error"].lower()


def test_validation_accepts_canonical_file(tmp_path: Path) -> None:
    p = tmp_path / "target.parquet"
    _bars_frame(1500).write_parquet(p)
    checks = doctor_mod._validate_restored_bars(p)
    assert checks["valid"] is True
    assert checks["rows"] == 1500
    assert checks["error"] is None


# ---------------------------------------------------------------------------
# 6. data-fetch hint points at the restore command
# ---------------------------------------------------------------------------
def test_data_fetch_hint_mentions_restore() -> None:
    """CONTRACT §2 LANE 2 item 6: the MT5-failure panel must advertise the
    offline restore path (hint text only; no behavior change)."""
    src = Path(doctor_mod.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
    # the one-line hint is emitted verbatim (line-wrapped in source)
    assert "or run `nexus data-restore --source <file_or_dir>`" in src
    assert "to restore a backup." in src
    assert '"MT5 not available"' in src
    # and it is attached to the MT5-connect failure branch of data-fetch
    panel_i = src.find('"MT5 not available"')
    hint_i = src.find("to restore a backup.")
    assert 0 < panel_i < hint_i


def test_data_fetch_mt5_failure_panel_renders_the_restore_hint() -> None:
    """Behavioural check: the rendered MT5-connect failure panel really shows
    the one-line restore hint (the operator's actionable offline path)."""
    import nexus_scalp.adapters.mt5.mt5_adapter as mt5_mod

    class _NoTerminal:
        def connect(self) -> None:
            raise RuntimeError("terminal not running")

        def disconnect(self) -> None:
            pass

    original = mt5_mod.DirectMT5Adapter
    mt5_mod.DirectMT5Adapter = _NoTerminal  # type: ignore[assignment, misc]
    try:
        res = _invoke(["data-fetch", "--symbol", "XAUUSD"])
    finally:
        mt5_mod.DirectMT5Adapter = original  # type: ignore[assignment, misc]
    assert res.exit_code == xc.EXIT_RUNTIME, res.stdout
    assert "MT5 not available" in res.stdout
    assert "data-restore --source" in res.stdout


# ---------------------------------------------------------------------------
# 7. helper-level unit checks
# ---------------------------------------------------------------------------
def test_helpers_contract(tmp_path: Path) -> None:
    assert doctor_mod._default_data_raw_path("xauusd", "m1") == Path("data/raw/XAUUSD_M1.parquet")
    assert list(doctor_mod._BARS_COLUMNS) == CANON_COLUMNS
    # source resolution: missing path -> (None, reason)
    resolved, why = doctor_mod._resolve_data_restore_source(
        tmp_path / "missing.parquet", "XAUUSD", "M1"
    )
    assert resolved is None
    assert why is not None
    # source resolution: a file is taken as-is
    f = tmp_path / "any_name.parquet"
    f.write_bytes(b"x")
    resolved, why = doctor_mod._resolve_data_restore_source(f, "XAUUSD", "M1")
    assert resolved == f.resolve()
    assert why is None
