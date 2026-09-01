"""BUG-176 regression: `model-dataset-build --schema` must be validated +
threaded, and raw-bars input must fail with an actionable contract error.

Fails-before contract (evidence: scratch/reviewer_user_hunt_2026-08-31.md,
commit 1f60832):
  * ``--schema scalp_v9_bogus`` was DECLARED BUT IGNORED: the value never
    reached DatasetFactory/SampleFactory (which default ``scalp_v1``), so a
    bogus id was silently accepted and a DIFFERENT schema's dataset was
    built with exit 0 (reviewer probes p-schemabogus);
  * the documented user path with RAW bars (data/raw/XAUUSD_M1.parquet,
    plain OHLCV) crashed with the labeler's raw traceback
    ``ValueError: DataFrame must contain either 'atr_m1' or 'atr' column.``
    (exit 1). The e2e suite only passed because its fixture fabricates
    feat_0..49 + atr columns — false confidence.

Passes-after contract pinned here:
  1. unknown schema id -> "Unknown schema" error panel listing the valid
     ids + EXIT_USAGE, no dataset built;
  2. a valid non-default schema id is honored end-to-end: the built dataset
     manifest records the requested schema and the frame width matches the
     schema dimension;
  3. raw bars without pre-computed features -> actionable
     "Raw bars are missing required pre-computed columns" panel naming the
     missing columns (feat_* per schema + atr_m1/atr) and pointing at the
     docs — never a raw traceback;
  4. the fabricate-everything happy path still works (regression guard).
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from nexus_scalp.cli.main import app
from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.release import exit_codes as xc

runner = CliRunner()


def _make_bars_csv(
    path: Path,
    rows: int = 300,
    seed: int = 42,
    dim: int = 50,
    with_atr: bool = True,
    raw: bool = False,
) -> Path:
    """Deterministic bars CSV; ``raw=True`` produces plain OHLCV only."""
    rng = random.Random(seed)
    price = 2400.0
    data: list[dict] = []
    for i in range(rows):
        o = price
        c = price + rng.uniform(-1.5, 1.5)
        h = max(o, c) + abs(rng.uniform(0, 1))
        low = min(o, c) - abs(rng.uniform(0, 1))
        row: dict = {
            "timestamp": f"2026-01-01 {i // 60 % 24:02d}:{i % 60:02d}:00",
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "volume": 100.0,
        }
        if with_atr:
            row["atr"] = 0.8
        if not raw:
            for j in range(dim):
                row[f"feat_{j}"] = rng.uniform(-2, 2)
        data.append(row)
        price = c
    pl.DataFrame(data).write_csv(path)
    return path


def test_bug176_bogus_schema_rejected_with_exit_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--schema scalp_v9_bogus` -> Unknown schema panel + EXIT_USAGE (2).
    Fails-before: silently accepted, a scalp_v1 dataset was built, exit 0."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts").mkdir()
    bars = _make_bars_csv(tmp_path / "bars.csv")
    res = runner.invoke(app, ["model-dataset-build", "--bars", str(bars), "--schema", "scalp_v9_bogus"])
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_USAGE
    assert "Unknown schema" in out
    # the hint lists the VALID schema ids
    for schema in FEATURE_SCHEMAS.list_schemas():
        assert schema.schema_id in out
    assert "Dataset built" not in out, "no dataset artifact may be built"
    assert "Traceback" not in out


def test_bug176_valid_schema_id_is_threaded_and_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--schema scalp_v3` (70D) builds a dataset whose manifest records
    scalp_v3 and whose frame width matches the schema dimension.
    Fails-before: manifest silently recorded scalp_v1 regardless."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts").mkdir()
    dim = FEATURE_SCHEMAS.resolve("scalp_v3").dimension
    bars = _make_bars_csv(tmp_path / "bars70.csv", dim=dim)
    res = runner.invoke(app, ["model-dataset-build", "--bars", str(bars), "--schema", "scalp_v3"])
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_OK, out
    m = re.search(r"dataset_id: (ds_[0-9a-f]+)", out)
    assert m, out
    ds_dir = tmp_path / "artifacts" / "model_generation" / "datasets" / m.group(1)
    manifest = json.loads((ds_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["feature_schema_id"] == "scalp_v3"
    frame = pl.read_parquet(ds_dir / "dataset.parquet")
    width = len([c for c in frame.columns if c.startswith("feat_")])
    assert width == dim, f"frame width {width} must match schema dimension {dim}"


def test_bug176_raw_bars_get_actionable_error_not_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plain OHLCV bars -> clean actionable panel listing required columns
    (feat_* + atr_m1/atr) + docs pointer, EXIT_RUNTIME.
    Fails-before: raw 'ValueError: DataFrame must contain either atr_m1 or
    atr column.' traceback."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts").mkdir()
    bars = _make_bars_csv(tmp_path / "raw.csv", raw=True)
    res = runner.invoke(app, ["model-dataset-build", "--bars", str(bars)])
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_RUNTIME
    assert "missing required pre-computed columns" in out
    assert "feat_0" in out and "feat_49" in out, "missing feature columns must be named"
    assert "atr_m1" in out or "atr" in out, "missing ATR column must be named"
    assert "docs/" in out, "error must point at the input-contract docs"
    assert "Traceback" not in out
    assert "Dataset built" not in out


def test_bug176_raw_bars_with_wrong_width_features_still_fails_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """50 feat_* columns against a 70D schema request -> the width/feature
    contract error (clean panel), never a silent schema swap or traceback."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts").mkdir()
    bars = _make_bars_csv(tmp_path / "bars50.csv", dim=50)
    res = runner.invoke(app, ["model-dataset-build", "--bars", str(bars), "--schema", "scalp_v3"])
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_RUNTIME
    assert "missing required pre-computed columns" in out
    assert "Traceback" not in out


def test_bug176_default_scalp_v1_happy_path_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: fully-featured scalp_v1 input still builds OK."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts").mkdir()
    bars = _make_bars_csv(tmp_path / "bars.csv", dim=FEATURE_SCHEMAS.resolve("scalp_v1").dimension)
    res = runner.invoke(app, ["model-dataset-build", "--bars", str(bars)])
    out = res.stdout + (res.stderr or "")
    assert res.exit_code == xc.EXIT_OK, out
    assert "Dataset built" in out
