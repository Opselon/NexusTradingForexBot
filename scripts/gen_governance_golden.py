#!/usr/bin/env python3
"""Golden model-governance fixture generator (TEST-LG-16).

Deterministic: the same inputs always produce the same files. Run via the
project venv: .venv/Scripts/python.exe scripts/gen_governance_golden.py
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"


def seeded_vector(seed: int = 7, dim: int = 50) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(0.0, 0.6, dim).tolist()
    v[3] = float("nan")
    v[7] = float("inf")
    v[8] = float("-inf")
    # canonical sanitization + clipping (mirrors live to_tensor_input)
    out = []
    for x in v:
        cleaned = 0.0 if (math.isnan(x) or x in (float("inf"), float("-inf"))) else x
        out.append(float(max(-3.0, min(3.0, cleaned))))
    return out


def fixed_extras() -> list[float]:
    """10 extras from a fixed 60-bar candle window (schema_augment contract)."""
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from nexus_scalp.features.schema_augment import NUM_EXTRA_60D, compute_60d_extras

    rng = np.random.default_rng(11)
    n = 80
    closes = 2400.0 + np.cumsum(rng.normal(0.0, 0.8, n))
    highs = closes + np.abs(rng.normal(0.2, 0.5, n))
    lows = closes - np.abs(rng.normal(0.2, 0.5, n))
    opens = np.concatenate(([closes[0]], closes[:-1]))
    vols = rng.integers(40, 180, n).astype(float)
    extras = compute_60d_extras(
        opens=opens.tolist(),
        highs=highs.tolist(),
        lows=lows.tolist(),
        closes=closes.tolist(),
        volumes=vols.tolist(),
    )
    assert len(extras) == NUM_EXTRA_60D
    return [float(v) for v in extras]


def main() -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    v50 = seeded_vector()
    extras = fixed_extras()
    payload50 = {"schema_id": "scalp_v1", "dimension": 50, "vector": v50}
    payload60 = {"schema_id": "scalp_v2", "extras_dimension": len(extras), "extras": extras}
    for name, payload in (("golden_50d.json", payload50), ("golden_60d_extras.json", payload60)):
        raw = json.dumps(payload, indent=1, sort_keys=True)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        payload["content_hash"] = digest
        (GOLDEN / name).write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
        print(f"{name}: hash={digest}")

    # Alignment expectation (50D + extras + 12 news = 72D when news on).
    from nexus_scalp.governance.alignment import challenger_input_for

    v, al = challenger_input_for(
        v50,
        champion_schema_id="scalp_v1",
        challenger_schema_id="scalp_v2",
        challenger_dimension=60,
        extras_60d=extras,
    )
    assert al == "NEWS_EXTENDED" and len(v) == 60 and v[:50] == v50 and v[50:] == extras, (
        "60D alignment golden"
    )
    (GOLDEN / "golden_alignment.json").write_text(
        json.dumps(
            {
                "alignment": al,
                "input_dimension_news_off": 60,
                "input_dimension_news_on": 72,
                "extras_first_named": "regime_compression",
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print("golden_alignment.json written")


if __name__ == "__main__":
    main()
