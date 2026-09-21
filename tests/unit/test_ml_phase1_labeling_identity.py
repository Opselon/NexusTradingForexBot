"""ML-PHASE1 STEP-8 experience identity + STEP-10 labeling diagnostics."""

from __future__ import annotations

import pytest


class TestProvenanceFutureWriteContract:
    """STEP-8: the real serving identity is recorded on every FUTURE write.

    A 70D inference must never be recorded as 50D. Historical rows are not
    rewritten — this guards the write path only.
    """

    def test_rejects_scalp_v3_with_50_dimension(self) -> None:
        from nexus_scalp.experience.provenance import _validate_schema_dimension_pair

        with pytest.raises(ValueError, match="70D contract"):
            _validate_schema_dimension_pair("scalp_v3", 50)

    def test_rejects_scalp_v1_with_70_dimension(self) -> None:
        from nexus_scalp.experience.provenance import _validate_schema_dimension_pair

        with pytest.raises(ValueError):
            _validate_schema_dimension_pair("scalp_v1", 70)

    def test_rejects_missing_schema_id(self) -> None:
        from nexus_scalp.experience.provenance import _validate_schema_dimension_pair

        with pytest.raises(ValueError, match="required"):
            _validate_schema_dimension_pair("", 50)

    def test_rejects_colliding_unregistered_id(self) -> None:
        """An unknown schema id adopting a registered dimension is ambiguous."""
        from nexus_scalp.experience.provenance import _validate_schema_dimension_pair

        with pytest.raises(ValueError, match="collides"):
            _validate_schema_dimension_pair("scalp_v4_fake", 70)

    def test_accepts_canonical_pairs(self) -> None:
        from nexus_scalp.experience.provenance import _validate_schema_dimension_pair

        _validate_schema_dimension_pair("scalp_v1", 50)
        _validate_schema_dimension_pair("scalp_v2", 60)
        _validate_schema_dimension_pair("scalp_v3", 70)

    def test_register_model_rejects_impossible_pair(self, tmp_path) -> None:
        """The registration path itself fails closed on a bad pair."""
        from nexus_scalp.adapters.database.audit_repository import AuditRepository
        from nexus_scalp.experience.provenance import ModelRegistry

        repo = AuditRepository(db_url="sqlite:///:memory:")
        registry = ModelRegistry(audit_repo=repo)
        artifact = tmp_path / "model.pt"
        artifact.write_bytes(b"\x00" * 16)
        with pytest.raises(ValueError, match="70D contract"):
            registry.register_model(
                artifact_path=artifact,
                model_version="v1",
                feature_schema_id="scalp_v3",
                feature_dimension=50,
            )


class TestLabelingDiagnostics:
    """STEP-10: the NO_TRADE imbalance source, quantified (not changed).

    The labeler is NOT modified this phase — this suite pins the measured
    evidence behind the verdict (B: overly restrictive config) so a future
    change is a deliberate decision, not drift.
    """

    def test_default_config_is_feasibility_dominant(self) -> None:
        """Under the shipped config (TP=1.1*ATR, friction>=0.35, real M1
        spread) the FEASIBILITY gate rejects the large majority of bars:
        tp_dist = atr*1.1 must exceed max(0.35, spread), and the median M1
        ATR is only ~$2 while the spread frequently exceeds it."""
        import numpy as np
        import polars as pl

        bars = pl.read_csv("data/raw/XAUUSD_M1.csv").sort("time")
        hl = bars["high"].to_numpy().astype(np.float64)
        lo = bars["low"].to_numpy().astype(np.float64)
        cl = bars["close"].to_numpy().astype(np.float64)
        prev = np.concatenate(([cl[0]], cl[:-1]))
        tr = np.maximum.reduce([hl - lo, np.abs(hl - prev), np.abs(lo - prev)])
        W = 14
        atr = np.full(len(cl), np.nan)
        if len(cl) > W:
            atr[W - 1] = tr[:W].mean()
            for i in range(W, len(cl)):
                atr[i] = (atr[i - 1] * (W - 1) + tr[i]) / W
        spread = bars["spread"].to_numpy().astype(np.float64)
        feasible = np.nan_to_num(atr, nan=0.0) * 1.1 > np.maximum(0.35, spread)
        # the imbalance is CONFIG-driven: the feasibility rate is far below
        # 50%, so most bars are structurally ineligible to carry a signal.
        assert float(np.mean(feasible)) < 0.5

    def test_evaluated_split_is_near_balanced(self) -> None:
        """Among bars that ARE evaluated, BUY/SELL are nearly symmetric and
        NO_TRADE is ~52% — so the 87.9% figure is NOT a labeler bug; it is
        the feasibility gate + stride subsampling, i.e. config + selection."""
        import numpy as np
        import polars as pl

        from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler

        bars = pl.read_csv("data/raw/XAUUSD_M1.csv").sort("time")
        hl = bars["high"].to_numpy().astype(np.float64)
        lo = bars["low"].to_numpy().astype(np.float64)
        cl = bars["close"].to_numpy().astype(np.float64)
        prev = np.concatenate(([cl[0]], cl[:-1]))
        tr = np.maximum.reduce([hl - lo, np.abs(hl - prev), np.abs(lo - prev)])
        W = 14
        atr = np.full(len(cl), np.nan)
        if len(cl) > W:
            atr[W - 1] = tr[:W].mean()
            for i in range(W, len(cl)):
                atr[i] = (atr[i - 1] * (W - 1) + tr[i]) / W
        bars = bars.with_columns(pl.Series("atr_m1", atr))
        out = TripleBarrierLabeler(
            no_trade_stride_bars=2, include_diagnostics=True
        ).label_dataframe(bars, include_diagnostics=True)
        ev = out.filter(pl.col("is_eval_sample"))
        counts = {d["label"]: d["count"] for d in ev["label"].value_counts().to_dicts()}
        n = max(1, ev.height)
        buy = counts.get("BUY_MARKET", 0) / n
        sell = counts.get("SELL_MARKET", 0) / n
        no_trade = counts.get("NO_TRADE", 0) / n
        # directional classes are symmetric (no bull/bear bias) and each is
        # ~24% — the imbalance lives in NO_TRADE, dominated by SL_HIT and
        # DUAL_HIT_NEUTRALIZED, i.e. honest barrier outcomes.
        assert abs(buy - sell) < 0.02
        assert 0.45 < no_trade < 0.60
        assert buy > 0.15 and sell > 0.15
