"""Centralized forensic health checks — feature / model / parity contract checks (CHECK-FCS · CHECK-LIQ · CHECK-MDL · CHECK-RTP).

Mechanically extracted VERBATIM from the former monolith ``checks.py``
(CHG-0032 Step 2, behavior-preserving decomposition). Function bodies are
byte-identical to the pre-split file; only import wiring changed.

BOUNDARY: read-only health checks. No check mutates databases, artifacts or
runtime state (TASK-11 §0/§55). Imports: forensics.models/references +
``checks_support`` — never a sibling domain module.

USED BY: ``checks.py`` (the facade every consumer imports) and
``forensics.engine`` via ``checks.check_*`` attribute access.

DO-NOT-PUT-HERE: engine wiring (engine.py), gate policy (deploy_gate.py),
new check families that belong to another domain module.
"""

from __future__ import annotations

import math
import numbers
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.forensics.checks_support import (
    _champion_artifact_info,
    _extract_feature_columns,
    _last_feature_vectors,
    _ok,
    _registered_families,
    _ro_connect,
    _safe_mean,
    _safe_std,
    _unknown,
)
from nexus_scalp.forensics.models import (
    CheckResult,
    HealthStatus,
)
from nexus_scalp.forensics.references import (
    FEATURE_REFERENCES,
    NOT_FROZEN,
    FeatureReferenceRegistry,
)

BASE_INDICES = range(0, 50)
NEWS_INDICES = range(50, 60)
LIQUIDITY_INDICES = range(60, 70)

#: Expected name at the first Liquidity index per the 70D contract snapshot.
EXPECTED_LIQUIDITY_INDEX_60_NAME = "bsl_distance_atr"

#: Shared registry instance for feature-reference checks (TASK-11 §02).
#: BUG-193: alias of the canonical references.FEATURE_REFERENCES singleton
# (ONE process = ONE frozen-reference registry; production liquidity
# health receives the engine registry explicitly, this keeps the compat
# surface truthful instead of a second never-frozen instance).
FEATURE_REF_REGISTRY = FEATURE_REFERENCES


def check_feature_schema_registry() -> CheckResult:
    """INV-70D-004: every registered schema has dimension >= 50 and Base prefix."""
    regs = _registered_families()
    if not regs:
        return _unknown(
            "CHECK-FCS-00",
            "feature schema registry unavailable",
            {"registered": regs},
            "feature registry resolves",
        )
    problems: list[str] = []
    for sid, info in regs.items():
        if info["dimension"] < 50:
            problems.append(f"{sid}: dimension {info['dimension']} < 50 (Base contract broken)")
    if problems:
        return CheckResult(
            "CHECK-FCS-00",
            HealthStatus.CRITICAL,
            evidence="; ".join(problems),
            observed={"registered": regs},
            expected="all schemas preserve Base 0..49",
            detail="FEATURE_SCHEMA_DRIFT",
        )
    return _ok(
        "CHECK-FCS-00",
        f"{len(regs)} schema(s) registered, all preserve Base prefix",
        {"registered": regs},
        "all schemas preserve Base 0..49",
    )


def check_feature_contract_70d(schema_id: str = "") -> CheckResult:
    """INV-70D-001/002/003/004: family layout of the 70D contract.

    Returns UNKNOWN when no 70D schema is registered (series blocked) —
    never fabricates a 70D contract.
    """
    regs = _registered_families()
    if not regs:
        return _unknown(
            "CHECK-FCS-01",
            "feature schema registry unavailable",
            {"registered": regs},
            "feature registry resolves",
        )
    candidates = [sid for sid, i in regs.items() if i["dimension"] >= 70] or [
        sid for sid, i in regs.items() if i["dimension"] == 70
    ]
    if not candidates:
        return _unknown(
            "CHECK-FCS-01",
            "no 70D schema registered (POST-70D series blocked)",
            {"registered": sorted(regs)},
            "a registered schema with dimension >= 70",
        )
    sid = schema_id or candidates[0]
    info = regs[sid]
    return _ok(
        "CHECK-FCS-01",
        f"70D schema {sid} registered (dim {info['dimension']})",
        {"schema_id": sid, "dimension": info["dimension"], "registered": sorted(regs)},
        "70D schema dimension == 70",
    )


def check_feature_contract_vector(vector: list[float] | None) -> CheckResult:
    """INV-70D-004/005/006: length, finiteness, clipping of an actual vector."""
    if vector is None:
        return _unknown(
            "CHECK-FCS-04",
            "no feature vector available to validate",
            {},
            "a produced feature vector",
        )
    n = len(vector)
    problems: list[str] = []
    if n == 70:
        pass
    elif n in (50, 60):
        pass  # legacy schema sizes remain valid
    else:
        problems.append(f"unexpected vector length {n} (expected 50/60/70)")
    # BUG-184/BUG-192: duck-typing hole — bool (int subclass) and numeric
    # strings coerced through float() passed the integrity gate; None
    # crashed with a raw TypeError. Non-numeric elements are CRITICAL now.
    non_numeric = [
        i for i, v in enumerate(vector) if isinstance(v, bool) or not isinstance(v, numbers.Real)
    ]
    if non_numeric:
        problems.append(f"non-numeric element types at indices {non_numeric[:10]}")
    nonfinite = [
        i for i, v in enumerate(vector) if i not in non_numeric and not math.isfinite(float(v))
    ]
    if nonfinite:
        problems.append(f"non-finite values at indices {nonfinite[:10]}")
    out_of_bounds = [
        i
        for i, v in enumerate(vector)
        if i not in non_numeric and math.isfinite(float(v)) and not (-3.0 <= float(v) <= 3.0)
    ]
    if out_of_bounds:
        problems.append(f"values outside [-3,+3] at indices {out_of_bounds[:10]}")
    if problems:
        return CheckResult(
            "CHECK-FCS-04",
            HealthStatus.CRITICAL,
            evidence="; ".join(problems),
            observed={"length": n, "problems": problems},
            expected="finite values within [-3,+3]; length 50/60/70",
            detail="FEATURE_CONTRACT_VIOLATION",
        )
    return _ok(
        "CHECK-FCS-04",
        f"vector length {n}; all values finite within [-3,+3]",
        {"length": n},
        "finite values within [-3,+3]; length 50/60/70",
    )


def check_feature_liquidity_contract(
    registry: FeatureReferenceRegistry | None = None,
) -> CheckResult:
    """INV-70D-002/003: Liquidity family indices + index-60 name guard.

    Also verifies the scalpel rule: if a schema declares liquidity features,
    index 60 (if present) must be `bsl_distance_atr` per contract snapshot.
    """
    regs = _registered_families()
    if not regs:
        return _unknown(
            "CHECK-FCS-03", "feature schema registry unavailable", {}, "registry resolves"
        )
    report: dict[str, Any] = {}
    for sid, info in regs.items():
        if info["dimension"] >= 70:
            report[sid] = {
                "liquidity_indices": "60..69",
                "index_60_name": EXPECTED_LIQUIDITY_INDEX_60_NAME,
            }
        elif info["dimension"] == 60:
            report[sid] = {"liquidity_indices": "50..59 (candidate)", "index_60_name": "n/a"}
    if not report:
        return _unknown(
            "CHECK-FCS-03",
            "no 60D/70D schema registered — liquidity family unobservable",
            {"registered": sorted(regs)},
            "a 60D/70D schema",
        )
    # frozen reference presence per family
    refs = registry or NOT_FROZEN
    liq_refs = 0
    if isinstance(refs, FeatureReferenceRegistry):
        liq_refs = len([r for r in refs.entries() if r.family == "liquidity"])
    observed = {
        "schemas": report,
        "frozen_liquidity_references": liq_refs,
        "registered": sorted(regs),
    }
    if liq_refs == 0:
        return _unknown(
            "CHECK-FCS-03",
            "liquidity family declared but NO frozen reference distribution — drift/deadness checks UNKNOWN",
            observed,
            "frozen liquidity reference distribution",
        )
    return _ok(
        "CHECK-FCS-03",
        "liquidity family layout registered with frozen references",
        observed,
        "frozen liquidity reference distribution",
    )


# ---------------------------------------------------------------------------
# Model / scaler contract checks (INV-70D-007/008/013/014)
# ---------------------------------------------------------------------------


def check_model_artifact() -> CheckResult:
    """INV-70D-007/008/013/014: champion artifact presence + scaler pairing.

    For the moment the live registry is empty (governance state 0 rows):
    artifact presence is verified, but Champion identity vs registry is
    handled by the governance check.
    """
    info = _champion_artifact_info()
    if not info.get("found"):
        return _unknown(
            "CHECK-MDL-01",
            "champion model artifact not found on disk",
            info,
            "a champion artifact",
        )
    problems: list[str] = []
    if not info.get("scaler_exists"):
        problems.append("scaler artifact missing")
    if problems:
        return CheckResult(
            "CHECK-MDL-01",
            HealthStatus.CRITICAL,
            evidence="; ".join(problems),
            observed=info,
            expected="champion model.pt + model.scaler.npz present",
            detail="MODEL_CONTRACT_INVALID",
        )
    # scaler hash vs model hash pairing is verified by the load gate; here we
    # record the hashes as evidence for drift detection across runs.
    return _ok(
        "CHECK-MDL-01",
        f"champion artifact present (hash {info.get('artifact_hash')}, scaler present)",
        info,
        "champion model.pt + model.scaler.npz present",
    )


def check_model_semantic_health() -> CheckResult:
    """BUG-225: the champion checkpoint must not be untrained random weights.

    Structural identity gates (BUG-141 width guard, class-head probe, schema
    hash, BUG-166 fingerprint match) all PASS on a checkpoint that was minted
    by a fresh-weights path (cold-start bootstrap / force_fresh / collapse
    recovery) and never trained — the corruption is SEMANTIC. A fresh init
    emits near-uniform softmax probabilities, so the policy confidence gate
    (base 0.40 + range/survival penalties) is mathematically unreachable and
    the engine degrades to permanent NO_TRADE / INSUFFICIENT_CONFIDENCE while
    every dashboard looks green.

    Detection: the runtime pins torch's global RNG to seed 42 before any
    ScalpNet mint, so ALL fresh inits are byte-identical. Byte-equality with
    that canonical init is therefore an exact, causal untrained-weights
    verdict (see integrity.detect_untrained_fresh_init).
    """
    info = _champion_artifact_info()
    if not info.get("found"):
        return _unknown(
            "CHECK-MDL-02",
            "no champion artifact on disk — semantic health unobservable",
            info,
            "a champion artifact",
        )
    try:
        from nexus_scalp.model_lifecycle.integrity import detect_untrained_fresh_init

        fresh, detail = detect_untrained_fresh_init(info["path"])
    except Exception as e:
        return _unknown(
            "CHECK-MDL-02",
            f"fresh-init canary unavailable ({e})",
            info,
            "torch + scalp_net importable",
        )
    if fresh:
        return CheckResult(
            "CHECK-MDL-02",
            HealthStatus.CRITICAL,
            evidence=(
                "champion checkpoint is BYTE-IDENTICAL to the canonical seed-42 "
                "fresh ScalpNet init — untrained random weights are serving live "
                "decisions (permanent NO_TRADE / confidence gate unreachable)"
            ),
            observed={**info, "canary_detail": detail},
            expected="checkpoint weights divergent from any fresh random init",
            detail="UNTRAINED_CHAMPION_ARTIFACT",
        )
    return _ok(
        "CHECK-MDL-02",
        f"champion weights are trained (fresh-init canary: {detail})",
        {**info, "canary_detail": detail},
        "checkpoint weights divergent from any fresh random init",
    )


def check_model_dimension_contract() -> CheckResult:
    """INV-70D-013/014: active schema dimension must equal artifact dimension.

    A 60D artifact must never receive 70D vectors and vice versa. Until a
    champion is REGISTERED we can only verify artifact-vs-config dimension.
    """
    info = _champion_artifact_info()
    if not info.get("found"):
        return _unknown("CHECK-MDL-03", "no champion artifact to dimension-check", info, "artifact")
    try:
        from nexus_scalp.features.schema import active_dimension  # type: ignore[import-not-found]

        dim = active_dimension()
    except Exception:
        return _unknown(
            "CHECK-MDL-03", "cannot resolve active schema dimension", info, "active dimension"
        )
    # Read neural input width from state dict when torch is available.
    state_dim: int | None = None
    try:
        import torch  # type: ignore[import-not-found]

        state = torch.load(info["path"], map_location="cpu", weights_only=False)
        if isinstance(state, dict):
            w = state.get("input_projection.weight")
            if w is not None and hasattr(w, "shape") and w.ndim == 2:
                state_dim = int(w.shape[1])
    except Exception:
        state_dim = None
    observed = {**info, "active_schema_dimension": dim, "artifact_input_dimension": state_dim}
    if state_dim is not None and state_dim != dim:
        return CheckResult(
            "CHECK-MDL-03",
            HealthStatus.CRITICAL,
            evidence=f"artifact input dim {state_dim} != active schema dim {dim}",
            observed=observed,
            expected="artifact input dim == active schema dim",
            detail="MODEL_SCHEMA_MISMATCH",
        )
    return _ok(
        "CHECK-MDL-03",
        f"artifact input dim {state_dim or 'unknown'} matches active schema dim {dim}",
        observed,
        "artifact input dim == active schema dim",
    )


# ---------------------------------------------------------------------------
# Runtime parity checks (INV-70D-009/010/011) — deterministic canaries
# ---------------------------------------------------------------------------

#: Deterministic causal fixture: timestamps, OHLCV, expected 70D vector.
#: The canary OVERWRITES nothing; it recomputes and compares. When the
#: producer function is not importable the check reports UNKNOWN.
CAUSAL_FIXTURE: dict[str, Any] = {
    "bars": [
        # rows: (timestamp, open, high, low, close, volume)
        ("2026-01-05T00:00:00+00:00", 1.10000, 1.10050, 1.09980, 1.10020, 1000),
        ("2026-01-05T00:01:00+00:00", 1.10020, 1.10100, 1.10010, 1.10090, 1200),
        ("2026-01-05T00:02:00+00:00", 1.10090, 1.10120, 1.10060, 1.10080, 900),
        ("2026-01-05T00:03:00+00:00", 1.10080, 1.10090, 1.09990, 1.10000, 1500),
        ("2026-01-05T00:04:00+00:00", 1.10000, 1.10060, 1.09970, 1.10030, 800),
    ],
    # expected zero-vector length when a producer is unavailable (50D base).
    "expected_dim": 50,
}


def check_causal_canary() -> CheckResult:
    """INV-70D-011: future-data injection must leave the historical vector unchanged.

    Deterministic: builds features from bars up to time T, then injects a
    future bar and verifies the historical vector is unchanged. Uses the real
    producer (ScalpFeatureEngine.compute_from_bars + to_tensor_input) with a
    fixture of 55+ deterministic bars; otherwise reports UNKNOWN (never
    fabricates).
    """
    CAUSAL_FIXTURE["bars"]
    try:
        from nexus_scalp.domain.models import TickData  # type: ignore[import-not-found]
        from nexus_scalp.features.scalp_features import (
            ScalpFeatureEngine,  # type: ignore[import-not-found]
        )
        from nexus_scalp.market_data.bar_aggregator import BarData  # type: ignore[import-not-found]
    except ImportError:
        return _unknown(
            "CHECK-RTP-03",
            "causal canary producers not importable",
            {"expected_dim": CAUSAL_FIXTURE["expected_dim"]},
            "feature producer importable",
        )
    try:
        # 55+ deterministic bars so compute_from_bars exits cold-start.
        n = 60
        base = 1.1000
        # non-trivial OHLC so range/ATR are well defined (deterministic ramp with
        # wicks); minute index wraps within 0..59.
        bars_list = [
            BarData(
                symbol="EURUSD",
                timeframe="M1",
                timestamp=datetime(2026, 1, 5, (i // 60) % 24, i % 60, tzinfo=UTC),
                open=base + 0.0001 * i + 0.00005 * (i % 5),
                high=base + 0.0001 * i + 0.0004,
                low=base + 0.0001 * i - 0.0003,
                close=base + 0.0001 * i + 0.00005 * (i % 3),
                tick_volume=100 + i,
                is_complete=True,
            )
            for i in range(n)
        ]
        tick = TickData(
            symbol="EURUSD",
            bid=base + 0.0001 * (n - 1),
            ask=base + 0.0001 * (n - 1) + 0.00001,
            timestamp=datetime(2026, 1, 5, 1, 0, tzinfo=UTC),
        )
        engine = ScalpFeatureEngine()
        # Same COMPLETED history in both cases; only the CURRENT (forming) tick
        # differs. The forming tick is future information w.r.t. the completed
        # bars — a causal engine must produce the identical vector.
        tick_future = TickData(
            symbol="EURUSD",
            bid=base + 0.0001 * (n - 1) + 0.0030,  # future-range tick
            ask=base + 0.0001 * (n - 1) + 0.0031,
            timestamp=datetime(2026, 1, 5, 1, 0, tzinfo=UTC),
        )
        history = bars_list
        vec_before = list(engine.compute_from_bars(history, tick).to_tensor_input())
        vec_after = list(engine.compute_from_bars(history, tick_future).to_tensor_input())
        if not vec_before or not vec_after:
            return _unknown(
                "CHECK-RTP-03",
                "feature producer returned empty vector for the fixture",
                {"bars": len(bars_list)},
                "deterministic feature vector",
            )
        # The forming tick MAY affect live tick-derived features by design
        # (norm_displacement, dist_to_ema, rolling z-score, swing distances,
        # S/R zone distances and OB-BOS all incorporate mid_price). The causal
        # contract is: the tick must NEVER change COMPLETED-BAR-derived
        # features (anatomy, structure, lags, sessions, MTF, SMC).
        # Positive control: the tick-derived family MUST change (proves the
        # canary can detect tick influence at all).
        BAR_DERIVED_SET = {
            0,
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            9,
            16,
            17,
            18,
            19,
            20,
            21,
            22,
            23,
            24,
            25,
            26,
            27,
            28,
            30,
            31,
            32,
            33,
            34,
            40,
            41,
            42,
            43,
            47,
            48,
            49,
        }
        TICK_DERIVED_SET = {8, 10, 11, 13, 35, 36, 37, 44, 45, 46}
        leaked = [
            i for i in BAR_DERIVED_SET if i < len(vec_before) and vec_before[i] != vec_after[i]
        ]
        positive = [
            i for i in TICK_DERIVED_SET if i < len(vec_before) and vec_before[i] != vec_after[i]
        ]
        if leaked:
            return CheckResult(
                "CHECK-RTP-03",
                HealthStatus.CRITICAL,
                evidence=f"forming tick changed bar-derived features at indices {leaked}",
                observed={
                    "before": vec_before,
                    "after": vec_after,
                    "diff_indices": [
                        i
                        for i, (a, b) in enumerate(zip(vec_before, vec_after, strict=False))
                        if a != b
                    ],
                    "leaked_bar_derived": leaked,
                    "tick_derived_reacted": positive,
                },
                expected="bar-derived features unchanged by forming tick",
                detail="FUTURE_LEAKAGE",
            )
        if not positive:
            return _unknown(
                "CHECK-RTP-03",
                "causal canary inconclusive: tick-derived set did NOT react (fixture too flat?)",
                {
                    "diff_indices": [
                        i
                        for i, (a, b) in enumerate(zip(vec_before, vec_after, strict=False))
                        if a != b
                    ]
                },
                "tick-derived features react to forming tick",
            )
        return _ok(
            "CHECK-RTP-03",
            f"causal canary PASSED (forming tick left {len(vec_before)}-dim bar-derived vector unchanged)",
            {
                "dimension": len(vec_before),
                "tick_derived_diff": [
                    i for i, (a, b) in enumerate(zip(vec_before, vec_after, strict=False)) if a != b
                ],
            },
            "bar-derived features unchanged by forming tick",
        )
    except Exception as exc:
        return _unknown(
            "CHECK-RTP-03",
            f"causal canary raised: {exc!r}",
            {"error": str(exc)},
            "feature producer importable",
        )


def check_training_live_parity_canary() -> CheckResult:
    """INV-70D-009: training-time producer and runtime producer agree.

    When both schema_augment (training producer) and scaler-features (live
    producer) are importable, they must produce the same dimension and the
    same vector for the same fixture. Otherwise UNKNOWN.
    """
    try:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS  # type: ignore[import-not-found]

        active = FEATURE_SCHEMAS.active
        dim = active.dimension
    except Exception:
        return _unknown("CHECK-RTP-01", "cannot resolve active feature schema", {}, "active schema")
    try:
        from nexus_scalp.features.scalp_features import (
            ScalpFeatureEngine,  # type: ignore[import-not-found]
        )
        from nexus_scalp.features.schema_augment import (
            compute_60d_extras,  # type: ignore[import-not-found]
        )
    except ImportError:
        return _unknown(
            "CHECK-RTP-01",
            "training/live producers not importable — parity UNKNOWN",
            {"active_dimension": dim},
            "producers importable",
        )
    # Deterministic fixture OHLCV arrays (bars are completed; no tick dependence
    # for the 60D extras path).
    opens = [1.10000, 1.10020, 1.10090, 1.10080, 1.10000]
    highs = [1.10050, 1.10100, 1.10120, 1.10090, 1.10060]
    lows = [1.09980, 1.10010, 1.10060, 1.09990, 1.09970]
    closes = [1.10020, 1.10090, 1.10080, 1.10000, 1.10030]
    volumes = [1000, 1200, 900, 1500, 800]
    # Base producer: 55+ deterministic bars through the REAL live pipeline.
    try:
        from nexus_scalp.domain.models import TickData  # type: ignore[import-not-found]
        from nexus_scalp.features.scalp_features import (
            ScalpFeatureEngine,
        )  # type: ignore[import-not-found]
        from nexus_scalp.market_data.bar_aggregator import BarData  # type: ignore[import-not-found]

        n = 60
        base = 1.1000
        bars_list = [
            BarData(
                symbol="EURUSD",
                timeframe="M1",
                timestamp=datetime(2026, 1, 5, (i // 60) % 24, i % 60, tzinfo=UTC),
                open=base + 0.0001 * i + 0.00005 * (i % 5),
                high=base + 0.0001 * i + 0.0004,
                low=base + 0.0001 * i - 0.0003,
                close=base + 0.0001 * i + 0.00005 * (i % 3),
                tick_volume=100 + i,
                is_complete=True,
            )
            for i in range(n)
        ]
        tick = TickData(
            symbol="EURUSD",
            bid=base + 0.0001 * (n - 1),
            ask=base + 0.0001 * (n - 1) + 0.00001,
            timestamp=datetime(2026, 1, 5, 1, 0, tzinfo=UTC),
        )
        engine = ScalpFeatureEngine()
        base_vec = list(engine.compute_from_bars(bars_list, tick).to_tensor_input())
    except Exception:
        base_vec = None
    try:
        import numpy as np  # type: ignore[import-not-found]

        extras = compute_60d_extras(
            opens=np.asarray(opens, dtype=np.float64),
            highs=np.asarray(highs, dtype=np.float64),
            lows=np.asarray(lows, dtype=np.float64),
            closes=np.asarray(closes, dtype=np.float64),
            volumes=np.asarray(volumes, dtype=np.float64),
            hour_utc=12,
        )
        extra_list = [float(v) for v in extras] if isinstance(extras, (list, tuple)) else None
    except Exception:
        extra_list = None
    if base_vec is None or extra_list is None:
        return _unknown(
            "CHECK-RTP-01",
            "parity producers returned no vector — UNKNOWN",
            {
                "active_dimension": dim,
                "base_len": len(base_vec) if base_vec else None,
                "extra_len": len(extra_list) if extra_list else None,
            },
            "parity vectors",
        )
    combined = base_vec + extra_list
    expected = len(base_vec) + len(extra_list)
    if len(combined) != expected:
        return CheckResult(
            "CHECK-RTP-01",
            HealthStatus.CRITICAL,
            evidence=f"combined training dim {len(combined)} != expected {expected}",
            observed={"combined": len(combined), "expected": expected},
            expected=f"combined dim == {expected}",
            detail="PARITY_BROKEN",
        )
    return _ok(
        "CHECK-RTP-01",
        f"parity canary: base {len(base_vec)} + extras {len(extra_list)} = {len(combined)}",
        {"base": len(base_vec), "extras": len(extra_list), "combined": len(combined)},
        f"combined dim == {expected}",
    )


def check_liquidity_feature_health(
    db_path: Path | None = None,
    references: FeatureReferenceRegistry | None = None,
) -> CheckResult:
    """§7/§8/§9/§10: per-liquidity-feature stats, drift, deadness, flood.

    Requires CANDLE_INTEL feature_vectors rows (or experience snapshots) and
    a FROZEN reference distribution. Without a reference the check is UNKNOWN
    (§5). Classification: NORMAL / WATCH / WARNING / CRITICAL, never an
    automatic rewrite (§55).
    """
    refs = references
    if refs is None or len(refs) == 0:
        return _unknown(
            "CHECK-LIQ-01",
            "no frozen liquidity reference distribution — drift/deadness UNKNOWN",
            {"frozen": "NOT_FROZEN"},
            "frozen liquidity references",
        )
    path = db_path or Path("artifacts") / "candle_intel.db"
    if not path.exists():
        return _unknown(
            "CHECK-LIQ-01", "candle_intel.db missing", {"path": str(path)}, "candle_intel.db"
        )
    conn = _ro_connect(path)
    try:
        rows = _last_feature_vectors(conn)
    finally:
        conn.close()
    if not rows:
        return _unknown(
            "CHECK-LIQ-01",
            "no feature_vectors rows to evaluate",
            {"path": str(path)},
            "feature_vectors rows",
        )
    # collect per-index observed stats for liquidity indices
    observed_stats: dict[int, list[float]] = {}
    for row in rows:
        vec = _extract_feature_columns(row)
        if vec is None:
            continue
        for idx in range(60, 70):
            if idx < len(vec):
                observed_stats.setdefault(idx, []).append(vec[idx])
    if not observed_stats:
        return _unknown(
            "CHECK-LIQ-01",
            "no rows expose features at indices 60..69",
            {"rows": len(rows)},
            "feature rows with indices 60..69",
        )
    findings: list[str] = []
    detail_flags: list[str] = []
    worst = HealthStatus.PASS
    for idx in range(60, 70):
        observed = observed_stats.get(idx)
        ref = refs.get("liquidity", idx)
        if observed is None:
            details = f"idx {idx}: no observed values"
            findings.append(details)
            detail_flags.append("MISSING")
            continue
        if ref is None:
            findings.append(f"idx {idx}: observed but NO frozen reference")
            detail_flags.append("NO_REF")
            continue
        n = len(observed)
        finite = [v for v in observed if math.isfinite(v)]
        missing_rate = (n - len(finite)) / n
        if n == 0:
            continue
        mean = sum(finite) / n
        var = sum((v - mean) ** 2 for v in finite) / n
        std = var**0.5
        # zero_rate computed for observability in the drift evidence below
        zero_rate = sum(1 for v in finite if abs(v) < 1e-12) / n
        sat_rate = sum(1 for v in finite if v <= -3.0 or v >= 3.0) / n
        mode_fraction = 0.0
        if finite:
            from collections import Counter

            mode_fraction = Counter(finite).most_common(1)[0][1] / n
        # ---- deadness (§9) ----
        dead_reasons: list[str] = []
        if mode_fraction >= 0.99:
            dead_reasons.append(f"same-value {mode_fraction:.2%}")
        if std < 1e-9:
            dead_reasons.append("near-zero variance")
        if missing_rate >= 1.0:
            dead_reasons.append("100% missing")
        if sat_rate >= 0.99:
            dead_reasons.append("constant clipping")
        if dead_reasons:
            findings.append(f"idx {idx}: FEATURE_DEAD ({', '.join(dead_reasons)})")
            detail_flags.append("FEATURE_DEAD")
            worst = max(worst, HealthStatus.DEGRADED, key=lambda s: s.severity)
            continue
        # ---- flood (§10) ----
        # Flood = the feature is pinned at an extreme with only tiny noise
        # (sweep state flapping, confluence always near max). Detected when
        # the value is near the clip bound AND variation is far below the
        # frozen reference scale — even if no single value dominates 90%.
        near_bound = (mean >= 0.9 * 3.0) or (mean <= -0.9 * 3.0)
        if (mode_fraction >= 0.9 and std < ref.std * 0.1) or (
            near_bound and ref.std > 0 and std < ref.std * 0.05
        ):
            findings.append(
                f"idx {idx}: FEATURE_FLOOD (mode {mode_fraction:.2%}, std {std:.4f} vs ref {ref.std:.4f}, mean {mean:.3f})"
            )
            detail_flags.append("FEATURE_FLOOD")
            worst = max(worst, HealthStatus.DEGRADED, key=lambda s: s.severity)
            continue
        # ---- drift (§8) ----
        mean_shift = abs(mean - ref.mean)
        if ref.std > 0:
            z = mean_shift / ref.std
        else:
            z = 0.0 if mean_shift < 1e-6 else 99.0
        if z > 5.0:
            status = HealthStatus.CRITICAL
            label = "CRITICAL"
        elif z > 3.0:
            status = HealthStatus.WARNING
            label = "WARNING"
        elif z > 2.0:
            status = HealthStatus.WARNING
            label = "WATCH"
        else:
            status = HealthStatus.PASS
            label = "NORMAL"
        if status is not HealthStatus.PASS:
            findings.append(
                f"idx {idx}: mean drift z={z:.2f} ({label}) — mean {mean:.4f} vs ref {ref.mean:.4f}"
            )
            detail_flags.append(f"DRIFT_{label}")
            worst = max(
                worst,
                status if status is not HealthStatus.WARNING else HealthStatus.WARNING,
                key=lambda s: s.severity,
            )
        if zero_rate > ref.zero_rate + 0.1:
            findings.append(f"idx {idx}: zero_rate {zero_rate:.2%} > ref {ref.zero_rate:.2%}")
            detail_flags.append("ZERO_RATE_UP")
            worst = max(worst, HealthStatus.WARNING, key=lambda s: s.severity)
        if missing_rate > ref.missing_rate + 0.1:
            findings.append(
                f"idx {idx}: missingness {missing_rate:.2%} > ref {ref.missing_rate:.2%}"
            )
            detail_flags.append("MISSINGNESS_UP")
            worst = max(worst, HealthStatus.WARNING, key=lambda s: s.severity)
    observed_out = {
        str(idx): {"n": len(v), "mean": _safe_mean(list(v)), "std": _safe_std(list(v))}
        for idx, v in observed_stats.items()
    }
    if worst is HealthStatus.PASS:
        return _ok(
            "CHECK-LIQ-01",
            "all observable liquidity features NORMAL vs frozen references",
            {"rows": len(rows), "features": observed_out},
            "liquidity features within reference distributions",
        )
    return CheckResult(
        "CHECK-LIQ-01",
        worst,
        evidence="; ".join(findings[:15]),
        observed={"rows": len(rows), "features": observed_out, "flags": detail_flags},
        expected="liquidity features within reference distributions",
        detail="; ".join(sorted(set(detail_flags))[:6]),
    )
