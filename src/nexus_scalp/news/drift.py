"""Drift monitor for the news 10D family (Phase 2C).

Measures distribution shift of the live news vector against a reference
window using PSI (population stability index) — the metric is implemented
locally (no new dependency) following the same conventions as
governance/evidence.py's drift alerts (bounded thresholds, no auto-action).

Verdicts:
    NORMAL / WARNING / CRITICAL / INSUFFICIENT_DATA

Rules (Phase 2C):
    * fewer than MIN_SAMPLES reference or current samples -> INSUFFICIENT_DATA,
      and NO behavioral change may be triggered from that verdict,
    * PSI deciles need care with degenerate (all-zero) news windows: a
      constant reference bucketizes into a single bin; current samples in
      other bins then score huge PSI. This is REAL signal (live news vector
      left the training distribution) but tiny windows must not alarm —
      hence the sample floor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Minimum samples per window before PSI is meaningful.
MIN_SAMPLES: int = 30
#: PSI thresholds (standard convention; mission rule 6: evidence-backed
#: industry defaults, documented, configurable at the call site).
PSI_WARNING: float = 0.10
PSI_CRITICAL: float = 0.25
#: News 10D family slice of the 70D vector.
NEWS_SLICE: tuple[int, int] = (50, 60)


class DriftVerdict(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class NewsDriftResult:
    verdict: DriftVerdict
    psi: float
    per_feature_psi: tuple[float, ...]
    reference_samples: int
    current_samples: int
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "psi": round(self.psi, 4),
            "per_feature_psi": [round(v, 4) for v in self.per_feature_psi],
            "reference_samples": self.reference_samples,
            "current_samples": self.current_samples,
            "detail": self.detail,
        }


def _psi_1d(reference: list[float], current: list[float], bins: int = 10) -> float:
    """PSI between two 1-D samples using quantile bins of the reference.

    Degenerate reference (all equal): reference occupies one bin; current
    samples equal to that value contribute 0 shift, anything else counts at
    full share — the honest reading of 'left the reference distribution'.
    """
    if not reference or not current:
        return 0.0
    sorted_ref = sorted(reference)
    n_ref = len(sorted_ref)
    edges = [sorted_ref[min(n_ref - 1, int(i * n_ref / bins))] for i in range(1, bins)]
    edges = sorted(set(edges))
    if not edges:
        # Constant reference: honest reading of "left the reference
        # distribution" — samples equal to the reference contribute 0;
        # anything else counts as mass moved OUT of the only bin.
        ref_val = sorted_ref[0]
        share_else = sum(1 for v in current if abs(v - ref_val) > 1e-12) / len(current)
        if share_else <= 0.0:
            return 0.0
        if share_else >= 1.0:
            return float("inf")
        # Symmetric PSI with smoothing floor eps on the empty bin:
        #   bin1: p=1-share, q=1-share -> contributes (q-p)ln(q/p)=0
        #   bin2: p=0(+eps),  q=share           -> dominates
        eps = 1e-6
        p1 = 1.0 - share_else
        q1 = 1.0 - share_else
        p2 = eps
        q2 = share_else
        return (q1 - p1) * math.log(q1 / p1) + (q2 - p2) * math.log(q2 / p2)

    def _bin_index(v: float) -> int:
        lo, hi = 0, len(edges)
        while lo < hi:
            mid = (lo + hi) // 2
            if v > edges[mid]:
                lo = mid + 1
            else:
                hi = mid
        return lo

    ref_counts = [0] * (len(edges) + 1)
    for v in reference:
        ref_counts[_bin_index(v)] += 1
    cur_counts = [0] * (len(edges) + 1)
    for v in current:
        cur_counts[_bin_index(v)] += 1
    psi = 0.0
    n_cur = len(current)
    for rc, cc in zip(ref_counts, cur_counts, strict=False):
        p = rc / n_ref
        q = cc / n_cur
        if p <= 0.0 or q <= 0.0:
            # standard smoothing floor
            p = max(p, 1e-6)
            q = max(q, 1e-6)
        psi += (q - p) * math.log(q / p)
    return max(0.0, psi)


def news_drift_check(
    *,
    reference_window: list[list[float]],
    current_window: list[list[float]],
    psi_warning: float = PSI_WARNING,
    psi_critical: float = PSI_CRITICAL,
    min_samples: int = MIN_SAMPLES,
) -> NewsDriftResult:
    """PSI drift over the NEWS 10D slice (indices 50..59) of 70D vectors.

    `reference_window` / `current_window` are lists of full 70D vectors (or
    raw 10D news blocks — slices outside 50..59 are ignored when 70D is
    given; 10D lists are used as-is).
    """

    def _news_slice(vectors: list[list[float]]) -> list[list[float]]:
        out: list[list[float]] = []
        for v in vectors:
            if len(v) == 70:
                out.append([float(x) for x in v[NEWS_SLICE[0] : NEWS_SLICE[1]]])
            elif len(v) == 10:
                out.append([float(x) for x in v])
            # other widths: ignore (contract mismatch handled elsewhere)
        return out

    ref = _news_slice(reference_window)
    cur = _news_slice(current_window)
    if len(ref) < min_samples or len(cur) < min_samples:
        return NewsDriftResult(
            verdict=DriftVerdict.INSUFFICIENT_DATA,
            psi=0.0,
            per_feature_psi=(),
            reference_samples=len(ref),
            current_samples=len(cur),
            detail=(
                f"need >= {min_samples} samples per window "
                f"(ref={len(ref)}, cur={len(cur)}); no action may be taken"
            ),
        )
    width = len(ref[0]) if ref else 10
    per_feature: list[float] = []
    for i in range(width):
        per_feature.append(_psi_1d([r[i] for r in ref], [c[i] for c in cur]))
    # aggregate: mean PSI across the 10 features (bounded, interpretable)
    psi = sum(per_feature) / len(per_feature) if per_feature else 0.0
    if not math.isfinite(psi):
        return NewsDriftResult(
            verdict=DriftVerdict.CRITICAL,
            psi=float("inf"),
            per_feature_psi=tuple(per_feature),
            reference_samples=len(ref),
            current_samples=len(cur),
            detail="non-finite PSI (distribution left all reference bins)",
        )
    if psi >= psi_critical:
        verdict = DriftVerdict.CRITICAL
    elif psi >= psi_warning:
        verdict = DriftVerdict.WARNING
    else:
        verdict = DriftVerdict.NORMAL
    return NewsDriftResult(
        verdict=verdict,
        psi=psi,
        per_feature_psi=tuple(per_feature),
        reference_samples=len(ref),
        current_samples=len(cur),
    )
