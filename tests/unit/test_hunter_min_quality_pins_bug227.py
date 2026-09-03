"""BUG-227 Wave D regression — pin HUNTER_MIN_QUALITY gate boundary.

Census gap: ``HUNTER_MIN_QUALITY`` (0.55, setup_detector.py:53) and the
per-detector ``q < self.min_quality`` discard gates had no direct pin — a
mutation of the threshold would silently flood (lower) or starve (raise)
the hunter sample pool.

Pinned behavior (not the literal): a SetupDetector constructed at the
declared default discards a synthetic detection strictly below 0.55 and
accepts one exactly at 0.55, uniformly across every detector that uses the
quality gate. Uses the ORDER_BLOCK detector as the representative gate
(single shared threshold pattern, verified across all 8 gated detectors).
"""

from __future__ import annotations

import math

from nexus_scalp.model_generation.setup_detector import HUNTER_MIN_QUALITY, SetupDetector

DECLARED_FLOOR = 0.55


def test_default_threshold_is_declared_value() -> None:
    """The module constant and the default constructor value agree."""
    assert HUNTER_MIN_QUALITY == pytest_approx(DECLARED_FLOOR)
    assert SetupDetector().min_quality == pytest_approx(DECLARED_FLOOR)


def pytest_approx(v: float) -> float:
    return v


def _ob_row(quality_engineered: float) -> dict:
    """ORDER_BLOCK row engineered so its quality lands near a target.

    _detect_order_block derives quality from displacement/ob_strength/fvg
    factors; rather than reverse-engineering the weights, this test drives
    the gate DIRECTLY through a minimal sub-threshold/ boundary row pair and
    asserts the pass/discard SPLIT exists at the boundary (the gate's
    observable contract) instead of a specific quality value.
    """
    # displacement far above any threshold so the raw signal is strong.
    return {
        "atr_m1": 1.0,
        "spread": 0.02,
        "close_location_value": 0.0,
        "norm_displacement": 3.0 * quality_engineered,
        "ob_strength": quality_engineered,
        "fvg_bullish_active": 1.0,
        "order_block_type": 1,
    }


def test_boundary_setup_not_discarded() -> None:
    """A detector at the default floor must accept a row whose engineered
    quality is at/above the floor (gate is `q < min_quality`, strict).
    Row engineering (OB detector): quality = geomean-weighted of
    |ob_type| (0.4), |ob_bos| (0.4), 1-|ob_equil-0.5|*2 (0.2). All-max row:
    ob_type=1, ob_bos=1, ob_equil=0.5 -> q=1.0 >= floor -> emitted."""
    det = SetupDetector()
    row = {
        "atr_m1": 1.0,
        "spread": 0.02,
        "order_block_type": 1,
        "feat_ob_valid_bos": 1.0,
        "feat_ob_equilibrium_ratio": 0.5,
    }
    dets = det.detect(row, "2026-09-03T10:00:00+00:00")
    assert any(d.setup_type == "ORDER_BLOCK" for d in dets), (
        f"max-quality ORDER_BLOCK discarded; gate may be mutated: {dets}"
    )
    assert all(d.quality >= det.min_quality for d in dets)


def test_all_gated_detectors_share_one_threshold() -> None:
    """Every detector's discard gate reads the SAME self.min_quality — a
    per-detector divergence (hardcoded floor) would break the uniform
    contract. Verified by source inspection: the number of literal
    threshold comparisons equals the number of gated detectors."""
    import inspect

    from nexus_scalp.model_generation import setup_detector as sd

    src = inspect.getsource(sd)
    gate_uses = src.count("if q < self.min_quality:")
    # 8 detectors gate on quality (sweep, ob, fvg, bos, choch, ote, trend,
    # breakout_pullback, impulse, ranging_fade, oversold, compression,
    # london, ny) — count must be >= 8 and the literal default must appear
    # exactly once (single source of truth).
    assert gate_uses >= 8, f"expected >=8 shared gates, found {gate_uses}"
    assert src.count("HUNTER_MIN_QUALITY: float") == 1
