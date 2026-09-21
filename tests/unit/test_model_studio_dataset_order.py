"""Regression tests for the Model Studio dataset inventory ordering.

Covers the "select 1Min, it falls back to 1Day" defect (TASK-POSA-002):

  * the inventory is ranked granularity-first (M1 first), NOT
    lexicographically — ``sorted()`` puts ``XAUUSD_D1`` above ``XAUUSD_M1``,
    and every UI's "first entry" default then silently binds the DAILY file;
  * the fix lives in the backend inventory, so BOTH consoles (legacy Web/
    and React frontend/) inherit it from one change;
  * the ingestion route refuses an unsupported timeframe instead of silently
    clamping it to M1 (a second silent-reversion path);
  * an unknown suffix never outranks a known granularity.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from nexus_scalp.web.model_studio_routes import (
    _dataset_candidates,
    _granularity_rank,
    _source_rank,
)
from nexus_scalp.web.server import create_app


class TestGranularityRanking:
    """The inventory must present the finest timeframe first."""

    def test_m1_ranks_first(self) -> None:
        assert _granularity_rank("XAUUSD_M1.parquet") == 0
        assert _granularity_rank("XAUUSD_M1.csv") == 0

    def test_d1_does_not_outrank_m1(self) -> None:
        # This is the exact defect: lexicographic sort puts D1 first.
        assert _granularity_rank("XAUUSD_D1.parquet") > _granularity_rank("XAUUSD_M1.parquet")

    def test_full_order_is_monotonic_by_bars(self) -> None:
        order = ["M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
        ranks = [_granularity_rank(f"XAUUSD_{tf}.parquet") for tf in order]
        assert ranks == list(range(10))

    def test_unknown_suffix_sorts_last(self) -> None:
        # An unrecognised file can never outrank a known granularity.
        assert _granularity_rank("XAUUSD_UNKNOWN.parquet") > _granularity_rank("XAUUSD_MN1.parquet")
        assert _granularity_rank("random_file.csv") > _granularity_rank("XAUUSD_M1.csv")

    def test_source_precedence_broker_first(self) -> None:
        # Broker truth (mt5) beats synthetic beats derived CSV.
        assert _source_rank("XAUUSD_M1.mt5.parquet") < _source_rank("XAUUSD_M1.synthetic.parquet")
        assert _source_rank("XAUUSD_M1.synthetic.parquet") < _source_rank("XAUUSD_M1.csv")
        # Legacy unsuffixed parquet still outranks its raw CSV sibling.
        assert _source_rank("XAUUSD_M1.parquet") < _source_rank("XAUUSD_M1.csv")

    def test_real_inventory_starts_with_m1(self) -> None:
        """Against the real data/raw inventory the first entry must be M1.

        Before the fix the first entry was XAUUSD_D1.csv, which is what the
        selector bound whenever the operator had no prior selection.
        """
        names = [c[0] for c in _dataset_candidates()]
        assert names, "test inventory is empty"
        assert names[0].startswith("XAUUSD_M1"), f"M1 is not first; first is {names[0]}"

    def test_inventory_position_datasets_visible(self) -> None:
        """The generator's artifacts/datasets output must be selectable.

        Without artifacts/datasets in the allowlist the inventory — and the
        safe-path selectors — could not see the datasets the generator had
        just written.
        """
        names = [c[0] for c in _dataset_candidates()]
        assert any(n.startswith("pos_ds_") for n in names), "no pos_ds_ datasets in inventory"


class TestIngestionFailsLoud:
    """An unsupported timeframe must be refused, never clamped to M1."""

    def setup_method(self) -> None:
        # WEB-AUTH gates every route; disable it for the test client so the
        # request reaches the handler (the auth-401 env is a known-foreign red).
        import os

        os.environ["NSE_WEB_AUTH_DISABLE"] = "1"
        self.client = TestClient(create_app())

    def test_unsupported_timeframe_is_rejected(self) -> None:
        # H1 is a real timeframe but not one the M1-scalp ingester accepts;
        # clamping it to M1 would silently relabel the stored data.
        res = self.client.post(
            "/api/model-studio/datasets/download",
            json={
                "symbol": "XAUUSD",
                "timeframe": "H1",
                "bars": 500,
                "source": "synthetic",
            },
        )
        assert res.status_code == 400
        assert "H1" in res.text
