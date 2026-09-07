"""Agent 14 QA wave 2 — RED regression tests: dataset identity + immutability
on the ML-path (DatasetFactory / ArtifactStore provenance).

Contracts (DatasetArtifactImmutability v1 extension, CHG-0061 wave 2):
  W1  ArtifactStore.save_dataset refuses to overwrite an existing dataset
      id unless allow_overwrite=True (corrections must mint a new id).
  W2  ArtifactStore.save_dataset with allow_overwrite=True records the
      supersede in the manifest (immutable provenance trail).
  W3  read_dataset integrity: stored manifest dataset_hash must match the
      actual parquet bytes on read; mismatch -> DatasetCorruptionError
      (DETECT/REJECT for the ML path).
  W4  deterministic_dataset_id reflects sample CONTENT: two builds over
      inputs differing in one close price mint different ids (id is
      content-aware, not only config-addressed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from nexus_scalp.model_generation.artifact_store import (
    ArtifactConflictError,
    ArtifactStore,
    DatasetCorruptionError,
    sha256_file,
)


def _bars(n: int = 60, close_shift: float = 0.0) -> pl.DataFrame:
    base = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    rows = []
    for i in range(n):
        ts = base + timedelta(minutes=i)
        o = 3300.0 + (i % 20) * 0.5
        rows.append(
            {
                "timestamp": ts,
                "open": o,
                "high": o + 2.0,
                "low": o - 2.0,
                "close": o + 0.2 + close_shift,
                "tick_volume": 100,
                "spread": 20,
                "atr": 1.5,
            }
        )
    return pl.DataFrame(rows)


def _manifest(ds_id: str) -> dict[str, Any]:
    return {
        "dataset_id": ds_id,
        "dataset_version": "1.0.0",
        "row_counts": {"total": 10},
        "temporal_range": {
            "start": "2026-08-01T00:00:00+00:00",
            "end": "2026-08-01T00:09:00+00:00",
        },
        "symbol": "XAUUSD",
        "timeframe": "M1",
    }


# ---------------------------------------------------------------------------
# W1/W2 — save_dataset immutability
# ---------------------------------------------------------------------------


def test_save_dataset_refuses_overwrite_of_existing_id(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path)
    frame = _bars(10)
    store.save_dataset("ds_test_overwrite", frame, _manifest("ds_test_overwrite"))
    before = sha256_file(store.dataset_path("ds_test_overwrite"))

    with pytest.raises(ArtifactConflictError) as excinfo:
        store.save_dataset(
            "ds_test_overwrite", _bars(10, close_shift=1.0), _manifest("ds_test_overwrite")
        )
    assert "immutable" in str(excinfo.value).lower() or "conflict" in str(excinfo.value).lower()
    # bytes untouched
    assert sha256_file(store.dataset_path("ds_test_overwrite")) == before


def test_save_dataset_allow_overwrite_records_supersede(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path)
    frame = _bars(10)
    store.save_dataset("ds_test_supersede", frame, _manifest("ds_test_supersede"))
    first_hash = sha256_file(store.dataset_path("ds_test_supersede"))

    store.save_dataset(
        "ds_test_supersede",
        _bars(10, close_shift=1.0),
        _manifest("ds_test_supersede"),
        allow_overwrite=True,
    )
    second_hash = sha256_file(store.dataset_path("ds_test_supersede"))
    assert first_hash != second_hash
    man = store.read_dataset_manifest("ds_test_supersede") or {}
    assert man.get("superseded_dataset_hash") == first_hash, (
        "overwrite must record the PREVIOUS bytes in the manifest (provenance trail)"
    )
    assert man.get("dataset_hash") == second_hash


# ---------------------------------------------------------------------------
# W3 — read-path integrity on the ML artifact store
# ---------------------------------------------------------------------------


def test_read_dataset_detects_tampered_parquet(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path)
    frame = _bars(10)
    store.save_dataset("ds_test_tamper", frame, _manifest("ds_test_tamper"))
    p = store.dataset_path("ds_test_tamper")
    mutated = frame.with_columns(pl.lit(9999.0).alias("close"))
    mutated.write_parquet(p)  # simulate in-place tamper, manifest NOT updated
    with pytest.raises(DatasetCorruptionError):
        store.read_dataset("ds_test_tamper")


def test_read_dataset_detects_swapped_parquet(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path)
    store.save_dataset("ds_a", _bars(10), _manifest("ds_a"))
    store.save_dataset("ds_b", _bars(10, close_shift=5.0), _manifest("ds_b"))
    a = store.dataset_path("ds_a")
    b = store.dataset_path("ds_b")
    backup = a.read_bytes()
    b.replace(a)
    with pytest.raises(DatasetCorruptionError):
        store.read_dataset("ds_a")
    a.write_bytes(backup)


def test_read_dataset_passes_when_integrity_holds(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path)
    frame = _bars(10)
    store.save_dataset("ds_ok", frame, _manifest("ds_ok"))
    out = store.read_dataset("ds_ok")
    assert out is not None and out.height == 10


# ---------------------------------------------------------------------------
# W4 — deterministic_dataset_id content sensitivity
# ---------------------------------------------------------------------------


def test_dataset_factory_id_moves_when_content_changes(tmp_path) -> None:
    from nexus_scalp.model_generation.dataset_factory import DatasetFactory
    from nexus_scalp.model_generation.sample_factory import SampleFactory

    df = _bars(120)
    feat_cols = {
        f"feat_{i}": [(i + 1) * 0.01 + (r % 5) * 0.001 for r in range(120)] for i in range(50)
    }
    df = df.with_columns([pl.Series(name, vals) for name, vals in feat_cols.items()])

    def build(data: pl.DataFrame, root: Path) -> str:
        store = ArtifactStore(root=root)
        factory = DatasetFactory(store=store, sample_factory=SampleFactory())
        return factory.build(data, symbol="XAUUSD", timeframe="M1")["dataset_id"]

    id1 = build(df, tmp_path / "one")
    id2 = build(df, tmp_path / "two")
    assert id1 == id2, "identical inputs must mint identical ids (determinism)"

    df_mut = df.with_columns(
        pl.when(pl.arange(0, df.height) == 60)
        .then(pl.col("close") + 0.01)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    id3 = build(df_mut, tmp_path / "three")
    assert id3 != id1, (
        "a one-cent close mutation MUST change the dataset id "
        "(content-aware identity, not config-only)"
    )


# ---------------------------------------------------------------------------
# W5 — deterministic rebuild is IDEMPOTENT (CLI contract vs store immutability)
# ---------------------------------------------------------------------------
# The CLI (nexus model-dataset-build) builds deterministically: same input ->
# same dataset_id. Re-running the command must exit OK (idempotent reuse of
# the intact existing artifact), while the store's immutability contract
# still refuses DIFFERENT content under the same id and corrupt artifacts.


def _factory_frame(rows: int = 120, close_shift: float = 0.0) -> pl.DataFrame:
    base = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    data = {
        "timestamp": [base + timedelta(minutes=i) for i in range(rows)],
        "open": [3300.0] * rows,
        "high": [3301.0] * rows,
        "low": [3299.0] * rows,
        "close": [3300.5 + (i % 7) * 0.1 + close_shift for i in range(rows)],
        "volume": [100.0] * rows,
        "atr": [0.5] * rows,
    }
    for i in range(50):
        data[f"feat_{i}"] = [(i + 1) * 0.01 + (r % 5) * 0.001 for r in range(rows)]
    return pl.DataFrame(data)


def _build(root: Path, data: pl.DataFrame) -> dict[str, Any]:
    from nexus_scalp.model_generation.dataset_factory import DatasetFactory
    from nexus_scalp.model_generation.sample_factory import SampleFactory

    factory = DatasetFactory(store=ArtifactStore(root=root), sample_factory=SampleFactory())
    return factory.build(data, symbol="XAUUSD", timeframe="M1")


def test_w5_rebuild_with_same_input_reuses_artifact(tmp_path) -> None:
    """Same input twice in the SAME store: second build is an idempotent
    no-op returning the existing handle (reused=True), exit-implied OK."""
    df = _factory_frame()
    h1 = _build(tmp_path, df)
    h2 = _build(tmp_path, df)
    assert h2["dataset_id"] == h1["dataset_id"]
    assert h2.get("reused") is True, "intact same-content artifact must be REUSED, not rebuilt"


def test_w5b_rebuild_refuses_different_content_same_id(tmp_path, monkeypatch) -> None:
    """A content change that somehow maps to the SAME id (digest collision
    or manual manifest) must still raise the immutability conflict."""
    from nexus_scalp.model_generation.dataset_factory import DatasetFactory
    from nexus_scalp.model_generation.sample_factory import SampleFactory

    df = _factory_frame()
    df_mut = df.with_columns(
        pl.when(pl.arange(0, df.height) == 60)
        .then(pl.col("close") + 0.01)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    store = ArtifactStore(root=tmp_path)
    factory = DatasetFactory(store=store, sample_factory=SampleFactory())
    h = factory.build(df, symbol="XAUUSD", timeframe="M1")
    # Force the mutated frame to claim the SAME id (simulate id collision):
    # patch deterministic id resolution by writing the mutated manifest under
    # the original id with the ORIGINAL content digest.
    monkeypatch.setattr(
        "nexus_scalp.model_generation.dataset_factory.deterministic_dataset_id",
        lambda *a, **k: h["dataset_id"],
    )
    with pytest.raises(ArtifactConflictError):
        factory.build(df_mut, symbol="XAUUSD", timeframe="M1")


def test_w5c_rebuild_refuses_corrupt_existing_artifact(tmp_path) -> None:
    """Existing artifact bytes tampered (manifest hash mismatch): rebuild
    must REFUSE, never silently overwrite corrupt evidence."""
    df = _factory_frame()
    h = _build(tmp_path, df)
    parquet = tmp_path / "datasets" / h["dataset_id"] / "dataset.parquet"
    backup = parquet.read_bytes()
    parquet.write_bytes(b"CORRUPTED")
    try:
        with pytest.raises(ArtifactConflictError):
            _build(tmp_path, df)
    finally:
        parquet.write_bytes(backup)
