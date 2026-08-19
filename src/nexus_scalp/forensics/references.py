"""Frozen reference distributions for feature health (TASK-11).

The Liquidity/News drift, deadness and flood checks compare LIVE observed
statistics against FROZEN training/reference distributions. This module is
the canonical home of those references.

CRITICAL DISCIPLINE (TASK-11 §8/§55):
- The monitor NEVER rewrites a feature; it classifies NORMAL / WATCH /
  WARNING / CRITICAL and reports.
- When no frozen reference exists for a feature family (e.g. 70D liquidity
  while the 70D series is BLOCKED), checks MUST return UNKNOWN — never
  fabricate a reference.
- References are registered here ONLY by an explicit, governed action
  (dataset freeze / model train), never automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Sentinel for "no reference registered".
NOT_FROZEN: str = "NOT_FROZEN"


@dataclass(frozen=True)
class FeatureReferenceStats:
    """Frozen per-feature distribution summary used by drift/deadness checks."""

    feature_index: int
    feature_name: str
    family: str  # base | news | liquidity
    mean: float
    std: float
    min_: float
    max_: float
    missing_rate: float = 0.0
    zero_rate: float = 0.0
    saturation_rate: float = 0.0
    mode_value: float | None = None
    mode_fraction: float = 0.0
    n: int = 0
    source: str = ""  # e.g. dataset id / training set id

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_index": self.feature_index,
            "feature_name": self.feature_name,
            "family": self.family,
            "mean": self.mean,
            "std": self.std,
            "min": self.min_,
            "max": self.max_,
            "missing_rate": self.missing_rate,
            "zero_rate": self.zero_rate,
            "saturation_rate": self.saturation_rate,
            "mode_value": self.mode_value,
            "mode_fraction": self.mode_fraction,
            "n": self.n,
            "source": self.source,
        }


class FeatureReferenceRegistry:
    """Append-only registry of frozen per-feature reference distributions.

    Registering a reference is an explicit governance action. Re-registering
    the same (family, index) requires ``replace=True`` so a silent overwrite
    can never hide drift.
    """

    def __init__(self) -> None:
        self._refs: dict[tuple[str, int], FeatureReferenceStats] = {}

    def register(self, ref: FeatureReferenceStats, replace: bool = False) -> FeatureReferenceStats:
        key = (ref.family, ref.feature_index)
        existing = self._refs.get(key)
        if existing is not None and not replace:
            # Same identity re-register is a no-op; a DIFFERENT reference
            # identity requires explicit replace (governance discipline).
            if existing.source != ref.source:
                raise ValueError(
                    f"Reference for {ref.family}[{ref.feature_index}] already frozen "
                    f"from {existing.source}; pass replace=True to re-freeze explicitly"
                )
            return existing
        self._refs[key] = ref
        return ref

    def get(self, family: str, feature_index: int) -> FeatureReferenceStats | None:
        return self._refs.get((family, feature_index))

    def family_names(self) -> list[str]:
        return sorted({k[0] for k in self._refs})

    def entries(self) -> list[FeatureReferenceStats]:
        return sorted(self._refs.values(), key=lambda r: (r.family, r.feature_index))

    def __len__(self) -> int:
        return len(self._refs)


def compute_reference_stats(
    *,
    feature_index: int,
    feature_name: str,
    family: str,
    values: list[float],
    missing_count: int = 0,
    total: int | None = None,
    source: str = "",
    clip_low: float = -3.0,
    clip_high: float = 3.0,
) -> FeatureReferenceStats:
    """Computes frozen stats from a deterministic training/reference sample.

    ``values`` must be finite (non-finite entries are counted as missing).
    The saturation rate measures values AT the clip bounds.
    """
    finite = [float(v) for v in values if _isfinite(v)]
    missing = len(values) - len(finite) + int(missing_count)
    total_n = int(total if total is not None else len(values))
    n = len(finite)
    if n == 0:
        raise ValueError(f"Cannot freeze reference for {family}[{feature_index}]: no finite values")
    mean = sum(finite) / n
    var = sum((v - mean) ** 2 for v in finite) / n
    std = var**0.5
    zero_rate = sum(1 for v in finite if abs(v) < 1e-12) / n
    sat = sum(1 for v in finite if v <= clip_low or v >= clip_high) / n
    from collections import Counter

    mode_value, mode_count = Counter(finite).most_common(1)[0] if finite else (None, 0)
    return FeatureReferenceStats(
        feature_index=feature_index,
        feature_name=feature_name,
        family=family,
        mean=mean,
        std=std,
        min_=min(finite),
        max_=max(finite),
        missing_rate=(missing / total_n) if total_n else 0.0,
        zero_rate=zero_rate,
        saturation_rate=sat,
        mode_value=mode_value,
        mode_fraction=(mode_count / n) if n else 0.0,
        n=n,
        source=source,
    )


def _isfinite(v: float) -> bool:
    import math

    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


#: Process-wide frozen references (empty until an explicit freeze action).
FEATURE_REFERENCES = FeatureReferenceRegistry()


# ---------------------------------------------------------------------------
# Frozen reference loading from the proven 70D golden baseline (TASK-12 §23)
# ---------------------------------------------------------------------------

#: Canonical 70D liquidity feature names at indices 60..69 (contract snapshot).
LIQUIDITY_70D_FEATURE_NAMES: tuple[str, ...] = (
    "bsl_distance_atr",  # 60
    "ssl_distance_atr",  # 61
    "eqh_strength",  # 62
    "eql_strength",  # 63
    "htf_liquidity_score",  # 64
    "internal_liquidity_distance",  # 65
    "external_liquidity_distance",  # 66
    "liquidity_confluence",  # 67
    "liquidity_sweep_state",  # 68
    "post_sweep_displacement",  # 69
)

#: Proven golden baseline document (TASK-02/03 70D series, 2026-08-19).
GOLDEN_BASELINE_PATH: str = "docs/LIQUIDITY_70D_GOLDEN_BASELINE.json"


def freeze_liquidity_references_from_golden(
    baseline_path: str | Path | None = None,
    registry: FeatureReferenceRegistry | None = None,
    *,
    replace: bool = False,
) -> FeatureReferenceRegistry:
    """Freezes the 10 liquidity reference distributions from the GOLDEN
    BASELINE (proven, versioned, provenance-backed — §23/§24).

    Only the golden baseline document may be used; the function refuses to
    freeze from any other source. Returns the registry (process-wide default
    when not supplied).
    """
    import json as _json

    path = Path(baseline_path or GOLDEN_BASELINE_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"golden baseline not found at {path} — cannot freeze unvalidated references"
        )
    data = _json.loads(path.read_text(encoding="utf-8"))
    # provenance guard (§24): must be the canonical golden baseline
    schema = data.get("schema_id", "")
    if schema != "scalp_liquidity_v1":
        raise ValueError(
            f"refusing to freeze from non-golden schema {schema!r} "
            "(only scalp_liquidity_v1 golden baseline is canonical)"
        )
    source = f"{path.name}@{data.get('git_head_commit', 'unknown')}"
    per_feature = data.get("per_feature", {})
    reg = registry if registry is not None else FeatureReferenceRegistry()
    for idx, name in enumerate(LIQUIDITY_70D_FEATURE_NAMES, start=60):
        stats = per_feature.get(name)
        if stats is None:
            raise ValueError(f"golden baseline missing feature {name!r} — freeze aborted")
        ref = FeatureReferenceStats(
            feature_index=idx,
            feature_name=name,
            family="liquidity",
            mean=float(stats["mean"]),
            std=float(stats["std"]),
            min_=float(stats["min"]),
            max_=float(stats["max"]),
            missing_rate=float(stats.get("missing_rate", 0.0)) / 100.0
            if stats.get("missing_rate", 0) > 1
            else float(stats.get("missing_rate", 0.0)),
            zero_rate=float(stats.get("zero_rate", 0.0)) / 100.0
            if stats.get("zero_rate", 0) > 1
            else float(stats.get("zero_rate", 0.0)),
            saturation_rate=float(stats.get("saturation_rate", 0.0)) / 100.0
            if stats.get("saturation_rate", 0) > 1
            else float(stats.get("saturation_rate", 0.0)),
            mode_value=stats.get("mode_value"),
            mode_fraction=float(stats.get("mode_fraction", 0.0)),
            n=int(stats.get("n", 0)),
            source=source,
        )
        reg.register(ref, replace=replace)
    return reg
