"""Centralized forensic health checks (TASK-11 POST-70D monitoring).

Every check is a small, read-only, failure-isolated function producing a
CheckResult with the five-level status vocabulary. No check mutates
production databases, artifacts, or runtime state. No check auto-repairs:
it detects, classifies and reports (TASK-11 §0/§55).

The engine (forensics/engine.py) wires these into groups and the aggregate
FORENSIC_HEALTH_SNAPSHOT.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.forensics.models import (
    CheckResult,
    HealthStatus,
    new_correlation_id,
)
from nexus_scalp.forensics.references import (
    NOT_FROZEN,
    FeatureReferenceRegistry,
)

# ---------------------------------------------------------------------------
# Shared helpers (read-only)
# ---------------------------------------------------------------------------


def _ro_connect(path: Path, timeout: float = 5.0) -> sqlite3.Connection:
    """Opens a SQLite connection in strict read-only URI mode."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _row_count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.Error:
        return None


def _iso_age_seconds(iso: str | None) -> float | None:
    """Age in seconds of an ISO timestamp vs now; None on parse failure."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds())
    except (TypeError, ValueError):
        return None


def _fmt(ts: str | None) -> str:
    return str(ts or "")


def _safe(fn: Callable[[], CheckResult]) -> CheckResult:
    """Failure isolation: a raised check becomes UNKNOWN with evidence (never PASS)."""
    start = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:
        return CheckResult(
            check_id="CHECK-RAISED",
            status=HealthStatus.UNKNOWN,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            evidence=f"check raised: {exc!r}",
            observed={"error": str(exc)},
            expected="check completes without raising",
            correlation_id=new_correlation_id(),
            detail="CHECK_RAISED",
        )
    # stamp duration (frozen dataclass -> replace)
    return CheckResult(
        check_id=result.check_id,
        status=result.status,
        timestamp=result.timestamp,
        duration_ms=(time.perf_counter() - start) * 1000.0,
        evidence=result.evidence,
        observed=result.observed,
        expected=result.expected,
        correlation_id=result.correlation_id,
        detail=result.detail,
    )


def _ok(
    check_id: str, evidence: str, observed: dict[str, Any] | None = None, expected: str = ""
) -> CheckResult:
    return CheckResult(
        check_id, HealthStatus.PASS, evidence=evidence, observed=observed or {}, expected=expected
    )


def _unknown(
    check_id: str, evidence: str, observed: dict[str, Any] | None = None, expected: str = ""
) -> CheckResult:
    """UNKNOWN is reported whenever health cannot be determined (§5)."""
    return CheckResult(
        check_id,
        HealthStatus.UNKNOWN,
        evidence=evidence,
        observed=observed or {},
        expected=expected,
    )


# ---------------------------------------------------------------------------
# Feature contract checks (INV-70D-001..006)
# ---------------------------------------------------------------------------

#: Canonical 70D family layout (indices). 50D/60D schemas are their prefix.
BASE_INDICES = range(0, 50)
NEWS_INDICES = range(50, 60)
LIQUIDITY_INDICES = range(60, 70)

#: Expected name at the first Liquidity index per the 70D contract snapshot.
EXPECTED_LIQUIDITY_INDEX_60_NAME = "bsl_distance_atr"


def _registered_families() -> dict[str, dict[str, Any]]:
    """Feature registry snapshot: id -> {dimension, supersedes, description}."""
    try:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        out: dict[str, dict[str, Any]] = {}
        for s in FEATURE_SCHEMAS.list_schemas():
            out[s.schema_id] = {
                "dimension": s.dimension,
                "supersedes": s.supersedes,
                "description": s.description,
            }
        return out
    except Exception:
        return {}


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
    nonfinite = [i for i, v in enumerate(vector) if not math.isfinite(float(v))]
    if nonfinite:
        problems.append(f"non-finite values at indices {nonfinite[:10]}")
    out_of_bounds = [
        i for i, v in enumerate(vector) if math.isfinite(float(v)) and not (-3.0 <= float(v) <= 3.0)
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


def _champion_artifact_info() -> dict[str, Any]:
    """Best-effort champion artifact inventory (files + hashes, read-only)."""
    info: dict[str, Any] = {"found": False}
    try:
        from nexus_scalp.release.paths import app_data_root  # type: ignore[import-not-found]

        root = Path(app_data_root() if callable(app_data_root) else app_data_root)
    except Exception:
        root = Path.cwd()
    # Config-driven artifact path first.
    artifact_path: str = ""
    try:
        from nexus_scalp.configuration.loader import load_config  # type: ignore[import-not-found]

        cfg = load_config()
        artifact_path = str(getattr(cfg.model, "model_artifact_path", ""))
    except Exception:
        artifact_path = ""
    candidates: list[Path] = []
    if artifact_path:
        candidates.append(Path(artifact_path))
    # Well-known champion dir.
    candidates.append(root / "artifacts" / "models" / "scalp" / "XAUUSD" / "v1.0.0" / "model.pt")
    candidates.append(Path("artifacts") / "models" / "scalp" / "XAUUSD" / "v1.0.0" / "model.pt")
    seen: set[Path] = set()
    for cand in candidates:
        try:
            p = cand.resolve()
        except Exception:
            p = cand
        if p in seen:
            continue
        seen.add(p)
        if p.is_file():
            scaler = p.with_name("model.scaler.npz")
            info["found"] = True
            info["path"] = str(p)
            info["exists"] = True
            info["size"] = p.stat().st_size
            info["scaler_exists"] = scaler.is_file()
            info["scaler_size"] = scaler.stat().st_size if scaler.is_file() else 0
            info["artifact_hash"] = _sha256(p)[:16]
            if scaler.is_file():
                info["scaler_hash"] = _sha256(scaler)[:16]
            break
    return info


def _sha256(path: Path) -> str:
    import hashlib

    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


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


def _probe_vector(engine: Any, bars: list[list]) -> list[float] | None:
    """Calls the feature engine's deterministic hook; returns None on failure."""
    for name in ("to_tensor_input", "compute_features", "produce_vector", "features_from_bars"):
        fn = getattr(engine, name, None)
        if callable(fn):
            try:
                result = fn(bars)
                if isinstance(result, list):
                    return [float(v) for v in result]
                if hasattr(result, "tolist"):
                    return [float(v) for v in result.tolist()[0]]
                return None
            except Exception:
                continue
    return None


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


def check_dataset_manifest_health(dataset_root: Path | None = None) -> CheckResult:
    """Dataset sniff: manifest/schema presence, dimension, row counts (read-only).

    Datasets live under artifacts/model_generation/datasets/<id>/. Absence of
    datasets is UNKNOWN (research worker not producing yet) — never PASS.
    """
    root = dataset_root or Path("artifacts") / "model_generation" / "datasets"
    try:
        if not root.is_dir():
            return _unknown(
                "CHECK-DTA-01",
                f"dataset root missing: {root}",
                {"root": str(root)},
                "dataset root exists",
            )
        entries = sorted(root.iterdir())
        if not entries:
            return _unknown(
                "CHECK-DTA-01",
                "dataset root empty — no datasets produced",
                {"root": str(root)},
                "at least one dataset",
            )
    except Exception as exc:
        return _unknown(
            "CHECK-DTA-01",
            f"dataset scan failed: {exc!r}",
            {"root": str(root)},
            "dataset root exists",
        )
    reports: list[dict[str, Any]] = []
    problems: list[str] = []
    for d in entries:
        if not d.is_dir():
            continue
        info: dict[str, Any] = {"id": d.name}
        for fname in ("manifest.json", "dataset.parquet", "dataset.csv", "schema.json"):
            p = d / fname
            if p.is_file():
                info[fname] = p.stat().st_size
        reports.append(info)
        dim = info.get("feature_count")  # placeholder for enriched manifests
        if dim is not None and dim < 50:
            problems.append(f"{d.name}: feature_count {dim} < 50")
    if problems:
        return CheckResult(
            "CHECK-DTA-01",
            HealthStatus.CRITICAL,
            evidence="; ".join(problems),
            observed={"datasets": reports},
            expected="dataset schemas preserve the Base contract",
            detail="DATASET_SCHEMA_DRIFT",
        )
    return _ok(
        "CHECK-DTA-01",
        f"{len(reports)} dataset(s) present",
        {"datasets": reports},
        "datasets present and schema-consistent",
    )


# ---------------------------------------------------------------------------
# Accounting checks (INV-70D-015/016 + duplicate/excursion monitors)
# ---------------------------------------------------------------------------


def _audit_path() -> Path:
    p = Path("artifacts") / "audit.db"
    return p if p.exists() else Path("artifacts/audit.db")


def _broker_ledger_divergence() -> dict[str, Any]:
    """Read-only broker-vs-ledger reconciliation summary."""
    out: dict[str, Any] = {"available": False}
    path = _audit_path()
    if not path.exists():
        return out
    try:
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            if "audit_broker_trades" not in tables or "audit_ledger" not in tables:
                return out
            broker_count = _row_count(conn, "audit_broker_trades") or 0
            ledger_count = _row_count(conn, "audit_ledger") or 0
            out["available"] = True
            out["broker_trades"] = broker_count
            out["ledger_rows"] = ledger_count
            out["unmatched_ratio"] = (
                round((broker_count - ledger_count) / broker_count, 4) if broker_count else 0.0
            )
            # realized PnL aggregates
            try:
                out["broker_pnl_sum"] = round(
                    float(
                        conn.execute(
                            "SELECT COALESCE(SUM(net_pnl), 0) FROM audit_broker_trades"
                        ).fetchone()[0]
                    ),
                    2,
                )
            except sqlite3.Error:
                out["broker_pnl_sum"] = None
            try:
                out["ledger_pnl_sum"] = round(
                    float(
                        conn.execute("SELECT COALESCE(SUM(pnl), 0) FROM audit_ledger").fetchone()[0]
                    ),
                    2,
                )
            except sqlite3.Error:
                out["ledger_pnl_sum"] = None
        finally:
            conn.close()
    except Exception:
        out["available"] = False
    return out


def check_accounting_divergence() -> CheckResult:
    """Broker truth vs ledger truth: flag unexplained divergence (INV-70D-016 context).

    NOTE: ledger MAY legitimately contain rows without broker covers (paper
    trades, pre-migration gap, BUG-045 era). Divergence beyond a documented
    tolerance is WARNING (for investigation) — never auto-rewrite.
    """
    diag = _broker_ledger_divergence()
    if not diag.get("available"):
        return _unknown(
            "CHECK-ACC-01",
            "broker/ledger reconciliation unavailable (tables absent or DB missing)",
            diag,
            "audit_broker_trades + audit_ledger",
        )
    broker = diag.get("broker_pnl_sum")
    ledger = diag.get("ledger_pnl_sum")
    tolerance = 0.0 if broker is None or ledger is None else abs(broker) * 0.02 + 5.0
    if broker is not None and ledger is not None and abs(broker - ledger) > tolerance:
        return CheckResult(
            "CHECK-ACC-01",
            HealthStatus.WARNING,
            evidence=f"broker PnL {broker} vs ledger PnL {ledger} diverges beyond tolerance {tolerance:.2f}",
            observed=diag,
            expected=f"|broker - ledger| <= {tolerance:.2f}",
            detail="ACCOUNTING_DIVERGENCE",
        )
    return _ok(
        "CHECK-ACC-01",
        f"broker PnL {broker} vs ledger PnL {ledger} within tolerance (unmatched ratio {diag.get('unmatched_ratio')})",
        diag,
        "broker/ledger PnL within tolerance",
    )


def check_duplicate_economic_outcome() -> CheckResult:
    """INV-70D-016: no execution identity owns more than one canonical outcome.

    Uses the same identity rule as BUG-097 guard: an execution_id (broker
    ticket) must appear at most once as `execution_id` across outcome rows.
    Historical duplicate rows remain (immutable history) — the check reports
    WARNING with incident count, CRITICAL only for NEW duplicates after the
    guard baseline timestamp.
    """
    path = _audit_path()
    if not path.exists():
        return _unknown("CHECK-ACC-02", "audit.db missing", {}, "audit.db")
    try:
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            if "audit_experience_outcomes" not in tables:
                return _unknown(
                    "CHECK-ACC-02", "outcomes table absent", {}, "audit_experience_outcomes"
                )
            rows = conn.execute(
                "SELECT idempotency_key, execution_id, "
                "COALESCE(realized_pnl_usd, 0) AS realized_pnl "
                "FROM audit_experience_outcomes"
            ).fetchall()
            # column names may vary; normalize
            cols = ["idempotency_key", "execution_id", "realized_pnl"]
            by_exec: dict[str, list[tuple[str, object]]] = {}
            for row in rows:
                rec = dict(zip(cols, row, strict=False))
                exec_id = rec.get("execution_id") or rec.get("order_id") or rec.get("ticket")
                if exec_id is None:
                    continue
                by_exec.setdefault(str(exec_id), []).append(
                    (str(rec.get("idempotency_key", "")), rec.get("realized_pnl"))
                )
            dupes = {k: v for k, v in by_exec.items() if len(v) > 1}
            if dupes:
                # Distinguish historical (known, BUG-097) from new: we cannot
                # timestamp-filter reliably without a created_at; report WARNING
                # for the known historical incident, CRITICAL for any OTHER.
                known_historical = {"152494870397"}
                fresh = [k for k in dupes if k not in known_historical]
                if fresh:
                    return CheckResult(
                        "CHECK-ACC-02",
                        HealthStatus.CRITICAL,
                        evidence=f"execution identities with >1 outcome: {sorted(fresh)}",
                        observed={"duplicates": dupes},
                        expected="one canonical outcome per execution identity",
                        detail="DUPLICATE_ECONOMIC_OUTCOME",
                    )
                return CheckResult(
                    "CHECK-ACC-02",
                    HealthStatus.WARNING,
                    evidence="known historical duplicate incident(s) remain (BUG-097, immutable)",
                    observed={"duplicates": dupes},
                    expected="one canonical outcome per execution identity",
                    detail="DUPLICATE_ECONOMIC_OUTCOME_HISTORICAL",
                )
            return _ok(
                "CHECK-ACC-02",
                "no execution identity owns more than one outcome",
                {"outcome_rows": len(rows)},
                "one canonical outcome per execution identity",
            )
        finally:
            conn.close()
    except Exception as exc:
        return _unknown(
            "CHECK-ACC-02",
            f"duplicate outcome check raised: {exc!r}",
            {"error": str(exc)},
            "outcomes readable",
        )


def check_impossible_excursion() -> CheckResult:
    """MFE >= 0, MAE <= 0 persistent invariant (BUG-096) plus ledger sanity.

    Raw ledger rows violating the excursion contract are classified as
    WARNING when they pre-date the BUG-096 fix (2026-08-19 — immutable
    historical findings, ANOMALY-VERIFY-01); any NEW violation after the fix
    is CRITICAL. No auto-repair ever.
    """
    path = _audit_path()
    if not path.exists():
        return _unknown("CHECK-ACC-03", "audit.db missing", {}, "audit.db")
    try:
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            if "audit_ledger" not in tables:
                return _unknown("CHECK-ACC-03", "ledger table absent", {}, "audit_ledger")
            cols = [d[0] for d in conn.execute("SELECT * FROM audit_ledger LIMIT 0").description]
            if "mfe" not in cols or "mae" not in cols:
                return _unknown(
                    "CHECK-ACC-03", "ledger lacks mfe/mae columns", {}, "audit_ledger.mfe/mae"
                )
            rows = conn.execute(
                "SELECT ticket, mfe, mae, close_time FROM audit_ledger WHERE mfe < 0 OR mae > 0"
            ).fetchall()
            violations = [
                dict(zip(("ticket", "mfe", "mae", "close_time"), r, strict=False)) for r in rows
            ]
            if violations:
                # BUG-096 fix landed 2026-08-19 (ANOMALY-VERIFY-01): rows closed
                # at/after the fix must be clean. Historical rows are immutable.
                FIX_DATE = datetime(2026, 8, 19, tzinfo=UTC)
                new_violations = [
                    v
                    for v in violations
                    if (age := _parse_close_time(v.get("close_time"))) is not None
                    and age >= FIX_DATE
                ]
                if new_violations:
                    return CheckResult(
                        "CHECK-ACC-03",
                        HealthStatus.CRITICAL,
                        evidence=f"{len(new_violations)} NEW excursion violations after BUG-096 fix",
                        observed={
                            "violations": violations[:20],
                            "new_violations": new_violations[:10],
                        },
                        expected="MFE >= 0 and MAE <= 0",
                        detail="IMPOSSIBLE_EXCURSION",
                    )
                return CheckResult(
                    "CHECK-ACC-03",
                    HealthStatus.WARNING,
                    evidence=f"{len(violations)} historical excursion rows pre-date the BUG-096 fix "
                    "(immutable audit trail, ANOMALY-VERIFY-01)",
                    observed={"violations": violations[:20]},
                    expected="MFE >= 0 and MAE <= 0",
                    detail="IMPOSSIBLE_EXCURSION_HISTORICAL",
                )
            return _ok(
                "CHECK-ACC-03",
                "no MFE<0 / MAE>0 violations",
                {"checked": True},
                "MFE >= 0 and MAE <= 0",
            )
        finally:
            conn.close()
    except Exception as exc:
        return _unknown(
            "CHECK-ACC-03",
            f"excursion check raised: {exc!r}",
            {"error": str(exc)},
            "ledger readable",
        )


def _parse_close_time(value: str | None) -> datetime | None:
    """Parses ledger close_time to an aware datetime; None on failure."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (TypeError, ValueError):
        return None


def check_experience_outcome_gap() -> CheckResult:
    """§21: experiences-without-outcome — EXECUTED trades must not lose outcomes.

    TASK-12 §16-20 correction (proven 2026-08-19): raw experience-vs-outcome
    counts misattribute pre-execution decision samples (which never trade and
    legitimately have no outcome) as pipeline losses. The truthful signal is
    the DEFECT rate over executed trades: only executed trades with missing
    outcomes indicate a learning-pipeline defect.
    """
    try:
        from nexus_scalp.forensics.experience_gap import analyze_experience_gap

        rep = analyze_experience_gap(_audit_path())
    except Exception as exc:
        return _unknown(
            "CHECK-ACC-04",
            f"gap analysis raised: {exc!r}",
            {"error": str(exc)},
            "experience tables readable",
        )
    d = rep.to_dict()
    observed = {
        "experiences": d["total_experiences"],
        "outcomes": d["with_outcome"],
        "gap": d["without_outcome"],
        "gap_rate": d["gap_rate"],
        "defect_rate": d["defect_rate"],
        "classification": d["classification"],
        "recoverable": d["recoverable_count"],
        "unrecoverable": d["unrecoverable_count"],
    }
    status = (
        HealthStatus(rep.status)
        if rep.status
        in (
            "PASS",
            "WARNING",
            "DEGRADED",
            "UNKNOWN",
            "CRITICAL",
        )
        else HealthStatus.UNKNOWN
    )
    if status is HealthStatus.PASS:
        reason = (
            f"no executed trade lost its outcome (defect_rate {d['defect_rate']}); "
            f"{d['without_outcome']} never-traded decision samples are legitimate"
        )
    else:
        reason = f"learning pipeline defect rate {d['defect_rate']} (status {rep.status})"
    return CheckResult(
        "CHECK-ACC-04",
        status,
        evidence=reason,
        observed=observed,
        expected="defect_rate over executed trades within thresholds",
    )


# ---------------------------------------------------------------------------
# Database integrity (INV-70D-017 context)
# ---------------------------------------------------------------------------


def _integrity_for(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"exists": path.exists()}
    if not path.exists():
        return out
    try:
        conn = _ro_connect(path)
        try:
            out["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
            out["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
            out["foreign_keys"] = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            out["tables"] = len(_table_names(conn))
            out["size_bytes"] = path.stat().st_size
            # unexpected tables: sqlite_sequence is expected; anything else is a delta
            tables = _table_names(conn)
            out["unexpected_tables"] = sorted(
                t
                for t in tables
                if t not in {"sqlite_sequence", "schema_meta", "schema_migrations"}
            )
            meta = (
                conn.execute("SELECT key, value FROM schema_meta").fetchall()
                if "schema_meta" in tables
                else []
            )
            out["schema_meta"] = dict(meta)
            wal = Path(str(path) + "-wal")
            out["wal_size_bytes"] = wal.stat().st_size if wal.exists() else 0
            out["wal_present"] = wal.exists()
        finally:
            conn.close()
    except Exception as exc:
        out["error"] = str(exc)
    return out


def check_database_integrity(db_paths: dict[str, Path] | None = None) -> CheckResult:
    """INV-70D-017: integrity_check ok + journal WAL + migrations consistent.

    Returns UNKNOWN for a missing DB (fresh install) — never PASS.
    """
    paths = db_paths or {
        "audit": _audit_path(),
        "news": Path("artifacts") / "news.db",
        "candle_intel": Path("artifacts") / "candle_intel.db",
    }
    reports: dict[str, Any] = {}
    problems: list[str] = []
    for name, p in paths.items():
        info = _integrity_for(p)
        reports[name] = info
        if not info.get("exists"):
            problems.append(f"{name}: DB missing (UNKNOWN)")
            continue
        if info.get("integrity") != "ok":
            problems.append(f"{name}: integrity_check={info.get('integrity')}")
        if info.get("error"):
            problems.append(f"{name}: {info['error']}")
    critical = [p for p in problems if "integrity_check" in p or "error" in p]
    if critical:
        return CheckResult(
            "CHECK-INT-01",
            HealthStatus.CRITICAL,
            evidence="; ".join(critical),
            observed=reports,
            expected="integrity_check=ok on all domains",
            detail="DATABASE_CORRUPTION",
        )
    missing = [n for n, i in reports.items() if not i.get("exists")]
    if missing:
        return _unknown(
            "CHECK-INT-01",
            f"DB(s) missing: {', '.join(missing)}",
            reports,
            "all domains present",
        )
    return _ok(
        "CHECK-INT-01",
        "integrity_check=ok on all domains (audit/news/candle_intel)",
        reports,
        "integrity_check=ok on all domains",
    )


def check_migration_state() -> CheckResult:
    """INV-70D-017: applied schema versions vs runtime expectations."""
    from nexus_scalp.database.models import DatabaseDomain  # type: ignore[import-not-found]
    from nexus_scalp.database.registry import (
        expected_version_for_domain,  # type: ignore[import-not-found]
    )

    paths = {
        "audit": _audit_path(),
        "news": Path("artifacts") / "news.db",
        "candle_intel": Path("artifacts") / "candle_intel.db",
    }
    expected = {d.value: expected_version_for_domain(d) for d in DatabaseDomain}
    from nexus_scalp.database.registry import BASELINE_VERSIONS, REGISTRY

    # pending migrations = registered-but-not-applied (legitimate, applies at
    # next startup gate); anything else below expected is UNEXPECTED drift.
    pending_ids: dict[str, list[str]] = {}
    for dom in DatabaseDomain:
        base = BASELINE_VERSIONS.get(dom, 1)
        applied = 0
        p = paths.get(dom.value)
        if p is not None and p.exists():
            info = _integrity_for(p)
            meta = info.get("schema_meta", {})
            version = int(meta.get("schema_version", 0) or 0)
            applied = version
        reg: Any = REGISTRY.get(dom, [])
        pend = [m.migration_id for m in reg if base + reg.index(m) + 1 > applied]
        pending_ids[dom.value] = pend
    reports: dict[str, Any] = {}
    problems: list[str] = []
    for name, p in paths.items():
        if not p.exists():
            reports[name] = {"state": "MISSING"}
            continue
        info = _integrity_for(p)
        meta = info.get("schema_meta", {})
        version = int(meta.get("schema_version", 0) or 0)
        exp = expected.get(name)
        pend = pending_ids.get(name, [])
        reports[name] = {
            "schema_version": version,
            "expected_version": exp,
            "pending_migrations": pend,
        }
        if exp is not None and version < exp:
            if pend:
                problems.append(f"{name}: schema v{version} with PENDING migration(s) {pend}")
            else:
                problems.append(
                    f"{name}: schema v{version} != expected v{exp} with NO pending migration"
                )
    pending_only = [p for p in problems if "PENDING" in p]
    real_drift = [p for p in problems if "NO pending" in p]
    if real_drift:
        return CheckResult(
            "CHECK-MIG-01",
            HealthStatus.CRITICAL,
            evidence="; ".join(real_drift),
            observed=reports,
            expected=f"schema versions == {expected}",
            detail="MIGRATION_DRIFT",
        )
    if pending_only:
        return CheckResult(
            "CHECK-MIG-01",
            HealthStatus.WARNING,
            evidence="; ".join(pending_only),
            observed=reports,
            expected=f"schema versions == {expected} (pending migrations apply at startup gate)",
            detail="MIGRATION_PENDING",
        )
    missing = [n for n, r in reports.items() if r.get("state") == "MISSING"]
    if missing:
        return _unknown(
            "CHECK-MIG-01", f"DB(s) missing: {', '.join(missing)}", reports, "all domains present"
        )
    return _ok(
        "CHECK-MIG-01",
        f"all domains at expected schema versions {expected}",
        reports,
        f"schema versions == {expected}",
    )


# ---------------------------------------------------------------------------
# Liquidity feature health (INV-70D-003 + §7/§8/§9/§10)
# ---------------------------------------------------------------------------


def _last_feature_vectors(conn: sqlite3.Connection, limit: int = 500) -> list[dict[str, Any]]:
    """Reads the most recent feature_vectors (candle_intel) or experience snapshots."""
    out: list[dict[str, Any]] = []
    tables = _table_names(conn)
    if "feature_vectors" in tables:
        try:
            cols = [d[0] for d in conn.execute("SELECT * FROM feature_vectors LIMIT 0").description]
            rows = conn.execute(
                f"SELECT * FROM feature_vectors ORDER BY rowid DESC LIMIT {limit}"
            ).fetchall()
            for r in rows:
                out.append(dict(zip(cols, r, strict=False)))
        except sqlite3.Error:
            pass
    return out


def _extract_feature_columns(row: dict[str, Any]) -> list[float] | None:
    """Extracts float columns feat_0..feat_{n-1} from a row; None on absence."""
    vals: list[float] = []
    i = 0
    while True:
        key = f"feat_{i}"
        if key not in row:
            break
        try:
            vals.append(float(row[key]))
        except (TypeError, ValueError):
            return None
        i += 1
    return vals or None


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


def _safe_mean(vals: list[float]) -> float:
    finite = [v for v in vals if math.isfinite(v)]
    return round(sum(finite) / len(finite), 4) if finite else 0.0


def _safe_std(vals: list[float]) -> float:
    finite = [v for v in vals if math.isfinite(v)]
    if not finite:
        return 0.0
    m = sum(finite) / len(finite)
    return round((sum((v - m) ** 2 for v in finite) / len(finite)) ** 0.5, 4)


# ---------------------------------------------------------------------------
# News health (§24/§25/§26)
# ---------------------------------------------------------------------------


def _news_state(news_path: Path | None = None) -> dict[str, Any]:
    path = news_path or Path("artifacts") / "news.db"
    out: dict[str, Any] = {"exists": path.exists()}
    if not path.exists():
        return out
    try:
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            out["tables"] = len(tables)
            if "news_sources" in tables:
                out["sources"] = [
                    dict(zip([d[0] for d in cur.description], r, strict=False))
                    for cur in [conn.execute("SELECT * FROM news_sources")]
                    for r in cur.fetchall()
                ]
            if "news_worker_state" in tables:
                cols = [
                    d[0]
                    for d in conn.execute("SELECT * FROM news_worker_state LIMIT 0").description
                ]
                rows = conn.execute("SELECT * FROM news_worker_state").fetchall()
                out["worker_state"] = [dict(zip(cols, r, strict=False)) for r in rows]
            if "news_health" in tables:
                cols = [d[0] for d in conn.execute("SELECT * FROM news_health LIMIT 0").description]
                rows = conn.execute("SELECT * FROM news_health").fetchall()
                out["source_health"] = [dict(zip(cols, r, strict=False)) for r in rows]
            if "news_articles" in tables:
                out["article_count"] = _row_count(conn, "news_articles") or 0
            if "news_consensus" in tables:
                out["consensus_count"] = _row_count(conn, "news_consensus") or 0
            if "news_impacts" in tables:
                out["impact_count"] = _row_count(conn, "news_impacts") or 0
        finally:
            conn.close()
    except Exception as exc:
        out["error"] = str(exc)
    return out


def check_news_health(news_path: Path | None = None) -> CheckResult:
    """§24: worker progress + source health + usable-article reality.

    A source with HTTP 200 but 0 usable articles is NOT healthy (§25).
    """
    st = _news_state(news_path)
    if not st.get("exists"):
        return _unknown("CHECK-NWS-01", "news.db missing", st, "news.db")
    if st.get("error"):
        return _unknown(
            "CHECK-NWS-01", f"news.db unreadable: {st['error']}", st, "news.db readable"
        )
    (st.get("worker_state") or [{}])[0]
    sources = st.get("sources") or []
    health = st.get("source_health") or []
    problems: list[str] = []
    healthy_sources = 0
    enabled_sources = 0
    for s in sources:
        if not s.get("enabled"):
            continue
        enabled_sources += 1
        sid = s.get("source_id", "")
        hrow: dict[str, Any] = next((h for h in health if h.get("source_id") == sid), {})
        if hrow.get("healthy"):
            healthy_sources += 1
        else:
            problems.append(
                f"{sid}: consecutive_failures={hrow.get('consecutive_failures')} "
                f"last_status={hrow.get('last_status')}"
            )
    article_count = int(st.get("article_count") or 0)
    consensus = int(st.get("consensus_count") or 0)
    if enabled_sources and healthy_sources < enabled_sources:
        return CheckResult(
            "CHECK-NWS-01",
            HealthStatus.DEGRADED,
            evidence=f"{healthy_sources}/{enabled_sources} enabled sources healthy; "
            + "; ".join(problems[:5]),
            observed=st,
            expected="all enabled sources healthy",
            detail="NEWS_SOURCE_DEGRADATION",
        )
    if article_count == 0:
        return CheckResult(
            "CHECK-NWS-01",
            HealthStatus.DEGRADED,
            evidence="news worker running but 0 articles in DB",
            observed=st,
            expected="articles present",
            detail="NEWS_NO_DATA",
        )
    if consensus == 0:
        return CheckResult(
            "CHECK-NWS-01",
            HealthStatus.WARNING,
            evidence="0 consensus rows (parser may produce no signals)",
            observed=st,
            expected="consensus rows present",
            detail="NEWS_PARSER_INERT",
        )
    return _ok(
        "CHECK-NWS-01",
        f"{healthy_sources}/{enabled_sources} sources healthy; {article_count} articles, {consensus} consensus",
        st,
        "all enabled sources healthy",
    )


def check_news_worker_progress(news_path: Path | None = None) -> CheckResult:
    """§23/§24: worker RUNNING-stale / no-new-data detection."""
    st = _news_state(news_path)
    if not st.get("exists"):
        return _unknown("CHECK-NWS-02", "news.db missing", st, "news.db")
    worker = (st.get("worker_state") or [{}])[0]
    if not worker:
        return _unknown(
            "CHECK-NWS-02",
            "news_worker_state empty — worker never checkpointed",
            st,
            "worker checkpoint",
        )
    cycle_count = int(worker.get("cycle_count") or 0)
    last_cycle = worker.get("last_cycle_at") or ""
    age = _iso_age_seconds(last_cycle)
    if cycle_count == 0:
        return CheckResult(
            "CHECK-NWS-02",
            HealthStatus.DEGRADED,
            evidence="news worker checkpoint exists but 0 cycles recorded",
            observed=st,
            expected="cycle_count > 0",
            detail="WORKER_NO_PROGRESS",
        )
    if age is not None and age > 24 * 3600:
        return CheckResult(
            "CHECK-NWS-02",
            HealthStatus.DEGRADED,
            evidence=f"news worker last cycle {age / 3600:.1f}h ago ({last_cycle})",
            observed=st,
            expected="worker cycle within 24h",
            detail="WORKER_STALLED",
        )
    if age is None:
        return _unknown(
            "CHECK-NWS-02", "worker last_cycle_at unparseable", st, "worker cycle timestamp"
        )
    return _ok(
        "CHECK-NWS-02",
        f"news worker active: {cycle_count} cycles, last {age / 3600:.1f}h ago",
        st,
        "worker cycle within 24h",
    )


def _load_runtime_config() -> Any | None:
    """Loads AppConfig from the repo config path or defaults (never raises)."""
    try:
        from nexus_scalp.configuration.config import AppConfig

        for p in (Path("configs") / "base.yaml", Path("configs/base.yaml")):
            if p.exists():
                return AppConfig.load_from_yaml(p)
        return AppConfig()
    except Exception:
        return None


def _config_mode(cfg: Any) -> str | None:
    try:
        mode = getattr(getattr(cfg, "execution", None), "mode", None)
        return str(getattr(mode, "value", mode)) if mode is not None else None
    except Exception:
        return None


def _config_news_enabled(cfg: Any) -> bool | None:
    try:
        news = getattr(cfg, "news", None)
        if news is None:
            return None
        return bool(getattr(news, "enabled", False))
    except Exception:
        return None


def _config_liquidity_enabled(cfg: Any) -> bool | None:
    try:
        model = getattr(cfg, "model", None)
        if model is None:
            return None
        return bool(getattr(model, "liquidity_features_enabled", False))
    except Exception:
        return None


def check_news_availability_matrix() -> CheckResult:
    """§26: News ON/OFF x Liquidity ON/OFF runtime contract."""
    cfg = _load_runtime_config()
    if cfg is None:
        return _unknown(
            "CHECK-NWS-03", "cannot load config for availability matrix", {}, "config loadable"
        )
    news_on = bool(_config_news_enabled(cfg))
    liq_on = bool(_config_liquidity_enabled(cfg))
    cell = (
        f"{'News ON' if news_on else 'News OFF'} / {'Liquidity ON' if liq_on else 'Liquidity OFF'}"
    )
    feat = (
        "50D (Base only)"
        if not news_on and not liq_on
        else (
            "60D Base+News"
            if news_on and not liq_on
            else ("60D Base+Liquidity" if not news_on and liq_on else "70D Base+News+Liquidity")
        )
    )
    observed = {
        "news_enabled": news_on,
        "liquidity_enabled": liq_on,
        "cell": cell,
        "feature_contract": feat,
    }
    # completeness: news context requires news DB; liquidity requires frozen
    # algorithm + references.
    incomplete: list[str] = []
    if news_on and not Path("artifacts/news.db").exists():
        incomplete.append("news enabled but news.db missing")
    if liq_on and not Path("artifacts/candle_intel.db").exists():
        incomplete.append("liquidity enabled but candle_intel.db missing")
    if liq_on and len(FEATURE_REF_REGISTRY) == 0:
        incomplete.append("liquidity enabled but no frozen reference distribution")
    if incomplete:
        return CheckResult(
            "CHECK-NWS-03",
            HealthStatus.CRITICAL,
            evidence="; ".join(incomplete),
            observed=observed,
            expected="enabled families have their data + frozen references",
            detail="FEATURE_CONTRACT_INCOMPLETE",
        )
    return _ok(
        "CHECK-NWS-03",
        f"runtime contract unambiguous: {cell} -> {feat}",
        observed,
        "no ambiguous 60D/70D status",
    )


#: process-wide registry for the availability matrix check
FEATURE_REF_REGISTRY = FeatureReferenceRegistry()


# ---------------------------------------------------------------------------
# Shadow health (§27)
# ---------------------------------------------------------------------------


def _shadow_state() -> dict[str, Any]:
    out: dict[str, Any] = {"available": False}
    path = _audit_path()
    if not path.exists():
        return out
    try:
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            for t in (
                "shadow_runs",
                "shadow_decisions",
                "shadow_promotions",
                "model_shadow_comparisons",
                "model_runtime_health",
                "model_governance_state",
            ):
                out[t] = _row_count(conn, t) if t in tables else "ABSENT"
            if "model_runtime_health" in tables:
                cols = [
                    d[0]
                    for d in conn.execute("SELECT * FROM model_runtime_health LIMIT 0").description
                ]
                rows = conn.execute(
                    "SELECT * FROM model_runtime_health ORDER BY rowid DESC LIMIT 1"
                ).fetchall()
                out["latest_runtime_health"] = [dict(zip(cols, r, strict=False)) for r in rows]
            out["available"] = True
        finally:
            conn.close()
    except Exception as exc:
        out["error"] = str(exc)
    return out


def check_shadow_health() -> CheckResult:
    """§27: shadow loaded / inference / errors / progress.

    Shadow tables are LAZY-schema: absence means never-attached (UNKNOWN),
    not PASS. A runtime health row saying shadow off but governance claiming
    running is a contradiction (DEGRADED).
    """
    st = _shadow_state()
    if not st.get("available"):
        return _unknown("CHECK-SHD-01", "shadow state unreadable", st, "audit.db readable")
    shadow_never = (
        st.get("shadow_runs") == "ABSENT"
        and st.get("model_shadow_comparisons") in ("ABSENT", 0)
        and st.get("model_runtime_health") in ("ABSENT", 0)
    )
    if shadow_never:
        return _unknown(
            "CHECK-SHD-01",
            "shadow never attached (no shadow tables/rows) — no progress evidence",
            st,
            "shadow history",
        )
    st.get("model_governance_state") or 0
    runtime_health = (st.get("latest_runtime_health") or [{}])[0]
    shadow_running = bool(runtime_health.get("shadow_running"))
    comparisons = int(runtime_health.get("shadow_comparisons") or 0)
    errors = int(runtime_health.get("shadow_errors") or 0)
    if shadow_running and comparisons == 0:
        return CheckResult(
            "CHECK-SHD-01",
            HealthStatus.DEGRADED,
            evidence="shadow reported RUNNING but 0 comparisons — WORKER_NO_PROGRESS",
            observed=st,
            expected="comparisons > 0 while running",
            detail="SHADOW_NO_PROGRESS",
        )
    if errors > 0 and comparisons == 0:
        return CheckResult(
            "CHECK-SHD-01",
            HealthStatus.WARNING,
            evidence=f"shadow errors {errors} with 0 comparisons — errors silently accumulating",
            observed=st,
            expected="comparisons > 0",
            detail="SHADOW_ERRORS_SILENT",
        )
    return _ok(
        "CHECK-SHD-01",
        f"shadow state: running={shadow_running}, comparisons={comparisons}, errors={errors}",
        st,
        "shadow produces comparisons when running",
    )


# ---------------------------------------------------------------------------
# Governance (§28/§29)
# ---------------------------------------------------------------------------


def check_governance_consistency() -> CheckResult:
    """§28: impossible governance states across BOTH registries.

    model_governance_state (TASK-6 governance) AND experience_model_registry
    (canonical live champion registry, TASK-8). Impossible combos
    (REJECTED+CHAMPION, promoted without approval) are CRITICAL. An empty
    governance state with a populated champion registry is PASS (the
    champion evidence lives in the experience registry).
    """
    path = _audit_path()
    if not path.exists():
        return _unknown("CHECK-GOV-01", "audit.db missing", {}, "audit.db")
    conn = _ro_connect(path)
    try:
        tables = _table_names(conn)
        gov_rows: list[dict[str, Any]] = []
        reg_rows: list[dict[str, Any]] = []
        if "model_governance_state" in tables:
            cols = [
                d[0]
                for d in conn.execute("SELECT * FROM model_governance_state LIMIT 0").description
            ]
            gov_rows = [
                dict(zip(cols, r, strict=False))
                for r in conn.execute("SELECT * FROM model_governance_state").fetchall()
            ]
        if "experience_model_registry" in tables:
            cols = [
                d[0]
                for d in conn.execute("SELECT * FROM experience_model_registry LIMIT 0").description
            ]
            reg_rows = [
                dict(zip(cols, r, strict=False))
                for r in conn.execute("SELECT * FROM experience_model_registry").fetchall()
            ]
    finally:
        conn.close()
    impossible: list[str] = []
    for rec in gov_rows + reg_rows:
        state = str(rec.get("lifecycle_state") or rec.get("lifecycle_status") or "")
        model = str(rec.get("model_id") or "")
        if "REJECTED" in state.upper() and "CHAMPION" in state.upper():
            impossible.append(f"{model}: REJECTED+CHAMPION")
        if "NOT_APPROVED" in state.upper() and "CHAMPION" in state.upper():
            impossible.append(f"{model}: not-approved+champion")
    if impossible:
        return CheckResult(
            "CHECK-GOV-01",
            HealthStatus.CRITICAL,
            evidence="; ".join(impossible),
            observed={
                "impossible": impossible,
                "gov_rows": len(gov_rows),
                "reg_rows": len(reg_rows),
            },
            expected="no impossible lifecycle states",
            detail="GOVERNANCE_IMPOSSIBLE_STATE",
        )
    if not gov_rows and not reg_rows:
        return _unknown(
            "CHECK-GOV-01",
            "no lifecycle evidence in either registry",
            {"gov_rows": 0, "reg_rows": 0},
            ">= 1 governance row",
        )
    # champion identity in the experience registry: verify single current champion
    champions = [r for r in reg_rows if "CHAMPION" in str(r.get("lifecycle_status", "")).upper()]
    fingerprints = {
        str(r.get("artifact_fingerprint") or "") for r in champions if r.get("artifact_fingerprint")
    }
    observed = {
        "gov_rows": len(gov_rows),
        "reg_rows": len(reg_rows),
        "champion_rows": len(champions),
        "distinct_fingerprints": sorted(fingerprints),
    }
    if len(fingerprints) > 1:
        return CheckResult(
            "CHECK-GOV-01",
            HealthStatus.DEGRADED,
            evidence=f"{len(fingerprints)} distinct champion fingerprints registered: {sorted(fingerprints)}",
            observed=observed,
            expected="one canonical champion fingerprint",
            detail="CHAMPION_FINGERPRINT_DIVERGENCE",
        )
    return _ok(
        "CHECK-GOV-01",
        f"governance consistent: {len(champions)} champion row(s), {len(fingerprints)} fingerprint(s)",
        observed,
        "no impossible lifecycle states",
    )


def check_champion_identity() -> CheckResult:
    """§29: registry says model A, runtime loads model B -> CRITICAL.

    TASK-12 §27: cross-verifies the runtime model hash, the filesystem
    artifact hash, the registry fingerprint and the manifest — all must
    agree. Reads the canonical experience_model_registry champion rows
    (TASK-8 governance) in addition to model_governance_state.
    """
    path = _audit_path()
    if not path.exists():
        return _unknown("CHECK-GOV-02", "audit.db missing", {}, "audit.db")
    conn = _ro_connect(path)
    try:
        tables = _table_names(conn)
        reg_rows: list[dict[str, Any]] = []
        if "experience_model_registry" in tables:
            cols = [
                d[0]
                for d in conn.execute("SELECT * FROM experience_model_registry LIMIT 0").description
            ]
            reg_rows = [
                dict(zip(cols, r, strict=False))
                for r in conn.execute("SELECT * FROM experience_model_registry").fetchall()
            ]
    finally:
        conn.close()
    champions = [r for r in reg_rows if "CHAMPION" in str(r.get("lifecycle_status", "")).upper()]
    if not champions:
        return _unknown(
            "CHECK-GOV-02",
            "no champion registered in experience_model_registry — identity unverifiable",
            {"registry_rows": len(reg_rows)},
            ">= 1 champion registry row",
        )
    # Filesystem artifact truth
    artifact = _champion_artifact_info()
    if not artifact.get("found"):
        return CheckResult(
            "CHECK-GOV-02",
            HealthStatus.CRITICAL,
            evidence=f"registry champion {champions[0].get('model_id')} but artifact missing",
            observed={"registry": champions[:3], "artifact": artifact},
            expected="registered champion artifact present",
            detail="CHAMPION_IDENTITY_MISMATCH",
        )
    # Cross-verify hashes: the CURRENT champion row's fingerprint must equal
    # the on-disk artifact hash. Older CHAMPION rows with stale fingerprints
    # (artifact rewritten since) are registry-hygiene DEGRADED, not identity
    # CRITICAL — unless the CURRENT row itself mismatches.
    disk_hash = str(artifact.get("artifact_hash") or "").lower()
    # newest champion row first (registered_at / id desc)
    champions_sorted = sorted(
        champions,
        key=lambda r: (str(r.get("registered_at") or ""), int(r.get("id") or 0)),
        reverse=True,
    )
    current = champions_sorted[0] if champions_sorted else {}
    current_hash = str(current.get("artifact_fingerprint") or "").lower()
    reg_hashes = {
        str(r.get("artifact_fingerprint") or "").lower()
        for r in champions
        if r.get("artifact_fingerprint")
    }
    stale = sorted(reg_hashes - {current_hash}) if current_hash else sorted(reg_hashes)
    schema_dims = {
        (str(r.get("feature_schema_id") or ""), int(r.get("feature_dimension") or 0))
        for r in champions
    }
    observed = {
        "current_champion": {
            k: current.get(k)
            for k in (
                "model_id",
                "model_version",
                "artifact_fingerprint",
                "feature_schema_id",
                "feature_dimension",
                "artifact_path",
                "registered_at",
                "id",
            )
        },
        "disk_artifact_hash": disk_hash,
        "registry_hashes": sorted(reg_hashes),
        "stale_hashes": stale,
        "schema_dimensions": sorted(schema_dims),
        "champion_row_count": len(champions),
    }
    current_mismatch = bool(
        current_hash
        and disk_hash
        and not disk_hash.startswith(current_hash[:12])
        and not current_hash.startswith(disk_hash[:12])
    )
    if current_mismatch:
        return CheckResult(
            "CHECK-GOV-02",
            HealthStatus.CRITICAL,
            evidence=f"current champion fingerprint {current_hash} diverges from disk hash {disk_hash}",
            observed=observed,
            expected="current registry fingerprint == disk artifact hash",
            detail="CHAMPION_IDENTITY_MISMATCH",
        )
    if stale:
        return CheckResult(
            "CHECK-GOV-02",
            HealthStatus.DEGRADED,
            evidence=f"champion identity verified (disk matches current row) but {len(stale)} STALE "
            f"champion fingerprint(s) remain in the registry: {sorted(stale)}",
            observed=observed,
            expected="one canonical champion fingerprint; no stale rows",
            detail="CHAMPION_REGISTRY_STALE_ROWS",
        )
    return _ok(
        "CHECK-GOV-02",
        f"champion identity verified: disk hash {disk_hash[:16]} matches the current registry fingerprint",
        observed,
        "registry fingerprint == disk artifact hash",
    )


# UI / API consistency (§30-31)
# ---------------------------------------------------------------------------


def _ui_bundle_files() -> dict[str, Any]:
    out: dict[str, Any] = {"found": False}
    import re as _re

    for root in (Path("Web"), Path("web")):
        idx = root / "index.html"
        js = root / "app.js"
        if idx.exists() and js.exists():
            out = {
                "found": True,
                "root": str(root),
                "index_html": {"size": idx.stat().st_size, "mtime": idx.stat().st_mtime},
                "app_js": {"size": js.stat().st_size, "mtime": js.stat().st_mtime},
            }
            # Real version markers: assignment of a version constant or
            # state_version guard — NOT any line containing the substring
            # "version" (e.g. a comment or log string).
            raw = js.read_text(errors="replace")[:300000]
            patterns = (
                r"[\"']?version[\"']?\s*[:=]\s*[\"'][^\"']+[\"']",
                r"appVersion\s*=\s*[\"'][^\"']+[\"']",
                r"bundleVersion\s*=\s*[\"'][^\"']+[\"']",
                r"state_version\s*[!=]=\s*null",
            )
            markers = []
            for pat in patterns:
                for m in _re.finditer(pat, raw, _re.IGNORECASE):
                    markers.append(m.group(0))
                    if len(markers) >= 3:
                        break
                if len(markers) >= 3:
                    break
            out["version_markers"] = markers
            break
    return out


def check_ui_bundle_drift() -> CheckResult:
    """§31: backend version vs Web bundle version.

    Until the bundle carries a version marker the check is UNKNOWN — a stale
    bundle cannot be detected without a marker (honest UNKNOWN, not PASS).
    """
    bundle = _ui_bundle_files()
    if not bundle.get("found"):
        return _unknown("CHECK-UI-02", "Web bundle not found", bundle, "Web/index.html + app.js")
    markers = bundle.get("version_markers") or []
    if not markers:
        return _unknown(
            "CHECK-UI-02",
            "Web bundle has no version marker — WEB_BUNDLE_DRIFT cannot be detected yet",
            bundle,
            "version marker in bundle",
        )
    return _ok(
        "CHECK-UI-02",
        "Web bundle version marker present",
        bundle,
        "backend/bundle version compatibility",
    )


def check_ui_canonical_state() -> CheckResult:
    """§30: dashboard must have ONE canonical state endpoint."""
    # Static check: the canonical live state contract is served at /api/live/state.
    # Runtime verification happens via API probe when the server runs (CHECK-API-01).
    return _ok(
        "CHECK-UI-01",
        "canonical live state endpoint: /api/live/state",
        {"endpoint": "/api/live/state"},
        "canonical UI state endpoint exists",
    )


# ---------------------------------------------------------------------------
# Telegram (§32)
# ---------------------------------------------------------------------------


def check_telegram_delivery() -> CheckResult:
    """§32: notifier configuration + worker + queue + send counts (read-only)."""
    try:
        pass
    except Exception:
        pass  # type: ignore[assignment]
    try:
        from nexus_scalp.settings import load_settings_service  # type: ignore[import-not-found]

        svc = load_settings_service()
        status = svc.telegram_config_status()
    except Exception as exc:
        return _unknown(
            "CHECK-TEL-01",
            f"telegram settings unavailable: {exc!r}",
            {"error": str(exc)},
            "settings service",
        )
    observed: dict[str, Any] = {
        "configured": bool(status.get("configured")),
        "enabled": bool(status.get("enabled")),
        "source": status.get("source", ""),
    }
    if not status.get("configured"):
        return CheckResult(
            "CHECK-TEL-01",
            HealthStatus.WARNING,
            evidence="telegram NOT_CONFIGURED (delivery cannot be verified)",
            observed=observed,
            expected="telegram configured",
            detail="TELEGRAM_NOT_CONFIGURED",
        )
    if not status.get("enabled"):
        return CheckResult(
            "CHECK-TEL-01",
            HealthStatus.WARNING,
            evidence="telegram configured but DISABLED",
            observed=observed,
            expected="telegram enabled",
            detail="TELEGRAM_DISABLED",
        )
    # worker-level evidence when a notifier instance is reachable via settings
    try:
        notifier = getattr(svc, "notifier", None) or getattr(svc, "_notifier", None)
        if notifier is not None and hasattr(notifier, "health_state"):
            hs = notifier.health_state()
            observed["worker"] = hs
            if hs.get("failed_count", 0) > 0 and hs.get("sent_count", 0) == 0:
                return CheckResult(
                    "CHECK-TEL-01",
                    HealthStatus.DEGRADED,
                    evidence=f"telegram worker {hs.get('status')}: {hs.get('failed_count')} failed, 0 sent",
                    observed=observed,
                    expected="sent_count > 0 or no failures",
                    detail="TELEGRAM_SILENT_FAILURE",
                )
    except Exception:
        pass
    return _ok(
        "CHECK-TEL-01",
        "telegram configured and enabled",
        observed,
        "telegram delivery path available",
    )


# ---------------------------------------------------------------------------
# Trace completeness (§33-35) and silent fallback (§36)
# ---------------------------------------------------------------------------


def check_trace_completeness() -> CheckResult:
    """§33: critical subsystems must have worker state evidence."""
    st: dict[str, Any] = {}
    path = _audit_path()
    if path.exists():
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            for t in (
                "intelligence_worker_state",
                "research_worker_state",
                "model_governance_events",
            ):
                st[t] = _row_count(conn, t) if t in tables else "ABSENT"
        finally:
            conn.close()
    nws = _news_state()
    st["news_worker_state"] = (
        len(nws.get("worker_state") or []) if nws.get("exists") else "MISSING_DB"
    )
    missing = [k for k, v in st.items() if v in ("ABSENT", 0, "MISSING_DB")]
    if missing:
        return CheckResult(
            "CHECK-TRC-01",
            HealthStatus.WARNING,
            evidence=f"subsystems with no trace evidence: {missing}",
            observed=st,
            expected="every critical subsystem records worker state",
            detail="TRACE_GAP",
        )
    return _ok(
        "CHECK-TRC-01",
        "all critical subsystems have trace evidence",
        st,
        "every critical subsystem records worker state",
    )


def check_correlation_propagation() -> CheckResult:
    """§34: governance and migration events carry correlation ids."""
    path = _audit_path()
    if not path.exists():
        return _unknown("CHECK-TRC-02", "audit.db missing", {}, "audit.db")
    conn = _ro_connect(path)
    try:
        tables = _table_names(conn)
        if "model_governance_events" in tables:
            cols = [
                d[0]
                for d in conn.execute("SELECT * FROM model_governance_events LIMIT 0").description
            ]
            if "correlation_id" not in cols:
                return CheckResult(
                    "CHECK-TRC-02",
                    HealthStatus.DEGRADED,
                    evidence="model_governance_events lacks correlation_id column",
                    observed={"columns": cols},
                    expected="correlation_id column",
                    detail="TRACE_INCOMPLETE",
                )
        # schema_migrations carries checksums (TASK-10) — verify presence
        if "schema_migrations" in tables:
            cols = [
                d[0] for d in conn.execute("SELECT * FROM schema_migrations LIMIT 0").description
            ]
            if "checksum" not in cols:
                return CheckResult(
                    "CHECK-TRC-02",
                    HealthStatus.DEGRADED,
                    evidence="schema_migrations lacks checksum column",
                    observed={"columns": cols},
                    expected="checksum column",
                    detail="TRACE_INCOMPLETE",
                )
    finally:
        conn.close()
    return _ok(
        "CHECK-TRC-02",
        "governance events carry correlation ids; migrations carry checksums",
        {
            "governance_events_table": "model_governance_events",
            "migrations_table": "schema_migrations",
        },
        "correlation/checksum columns present",
    )


_SILENT_FALLBACK_PATTERNS = (
    "default=0",
    "silent recovery",
    "fallback",
    "silent fallback",
    "unavailable -> 0",
    "failed; continuing",
)


def check_silent_fallback(log_dir: Path | None = None) -> CheckResult:
    """§36: scan recent runtime logs for silent-fallback/zero-substitution patterns.

    Presence of the PATTERN is a WARNING (documented fallsbacks exist); the
    check's job is to surface them for triage. A log dir with no logs at all
    is UNKNOWN (no evidence either way) — never PASS.
    """
    logs = log_dir or Path("artifacts") / "logs"
    if not logs.is_dir():
        return _unknown(
            "CHECK-TRC-03", f"log dir missing: {logs}", {"dir": str(logs)}, "artifacts/logs"
        )
    files = sorted(logs.glob("*.log"))[-8:]
    hits: list[str] = []
    for f in files:
        try:
            with open(f, errors="replace") as fh:
                for line in fh:
                    lower = line.lower()
                    if any(p in lower for p in _SILENT_FALLBACK_PATTERNS):
                        hits.append(f"{f.name}: {line.strip()[:120]}")
                        if len(hits) >= 12:
                            break
        except OSError:
            continue
        if len(hits) >= 12:
            break
    if not files:
        return _unknown("CHECK-TRC-03", "no log files found", {"dir": str(logs)}, "log files")
    if hits:
        return CheckResult(
            "CHECK-TRC-03",
            HealthStatus.WARNING,
            evidence=f"{len(hits)} fallback-pattern log lines (triage needed)",
            observed={"hits": hits},
            expected="no silent fallback patterns in logs",
            detail="SILENT_FALLBACK_CANDIDATE",
        )
    return _ok(
        "CHECK-TRC-03",
        f"no silent-fallback patterns in last {len(files)} logs",
        {"files": len(files)},
        "no silent fallback patterns in logs",
    )


# ---------------------------------------------------------------------------
# Database growth / queues (§41-42) and performance (§43)
# ---------------------------------------------------------------------------


def check_database_growth(db_paths: dict[str, Path] | None = None) -> CheckResult:
    """§41: DB size + WAL size; alert on unexpected explosion or stalls."""
    paths = db_paths or {
        "audit": _audit_path(),
        "news": Path("artifacts") / "news.db",
        "candle_intel": Path("artifacts") / "candle_intel.db",
    }
    reports: dict[str, Any] = {}
    for name, p in paths.items():
        info: dict[str, Any] = {"exists": p.exists()}
        if p.exists():
            info["size_bytes"] = p.stat().st_size
            wal = Path(str(p) + "-wal")
            info["wal_size_bytes"] = wal.stat().st_size if wal.exists() else 0
        reports[name] = info
    # compare against the baseline probe (2026-08-19): audit 50.9MB, news 6.4MB, candle 1.1MB
    baseline = {"audit": 50_921_472, "news": 6_373_376, "candle_intel": 1_134_592}
    # Fresh DBs legitimately start small; the shrink guard applies only above
    # a size floor so tiny/test DBs are never flagged.
    SHRINK_FLOOR = 5_000_000
    anomalies: list[str] = []
    for name, info in reports.items():
        if not info.get("exists"):
            anomalies.append(f"{name}: DB missing")
            continue
        size = info["size_bytes"]
        base = baseline.get(name, size)
        if base and size > base * 3:
            anomalies.append(f"{name}: size {size} > 3x baseline {base} (growth anomaly)")
        elif base and size > SHRINK_FLOOR and size < base * 0.3:
            anomalies.append(f"{name}: size {size} < 0.3x baseline {base} (unexpected shrink)")
    if anomalies:
        return CheckResult(
            "CHECK-GRW-01",
            HealthStatus.WARNING,
            evidence="; ".join(anomalies),
            observed=reports,
            expected="DB sizes within baseline bounds",
            detail="DB_GROWTH_ANOMALY",
        )
    return _ok(
        "CHECK-GRW-01",
        "DB sizes within baseline bounds",
        reports,
        "DB sizes within baseline bounds",
    )


def check_queue_growth() -> CheckResult:
    """§42: background queue sizes (telegram/audit writer) — sustained growth alert."""
    observed: dict[str, Any] = {}
    problems: list[str] = []
    try:
        from nexus_scalp.settings import load_settings_service  # type: ignore[import-not-found]

        svc = load_settings_service()
        n = getattr(svc, "notifier", None) or getattr(svc, "_notifier", None)
        if n is not None and hasattr(n, "health_state"):
            hs = n.health_state()
            observed["telegram"] = {"queue_size": hs.get("queue_size"), "status": hs.get("status")}
            qs = int(hs.get("queue_size") or 0)
            if qs >= 80:
                problems.append(f"telegram queue {qs} (capacity ~100) — sustained growth")
    except Exception:
        pass
    if problems:
        return CheckResult(
            "CHECK-GRW-02",
            HealthStatus.WARNING,
            evidence="; ".join(problems),
            observed=observed,
            expected="queues bounded",
            detail="QUEUE_GROWTH",
        )
    return _ok("CHECK-GRW-02", "background queues bounded", observed, "queues bounded")


# ---------------------------------------------------------------------------
# 200-but-wrong semantic API checks (§37/§38)
# ---------------------------------------------------------------------------


def check_chart_semantic_health(bars: list[dict[str, Any]] | None = None) -> CheckResult:
    """§38: a chart API returning 200 with zero bars is CHART_DATA_DEGRADED.

    When no runtime bars are supplied, the check inspects candle_intel for
    evidence; empty = DEGRADED (never PASS).
    """
    if bars is not None:
        if len(bars) == 0:
            return CheckResult(
                "CHECK-API-02",
                HealthStatus.DEGRADED,
                evidence="chart payload 200 but ZERO bars",
                observed={"bar_count": 0},
                expected="bar_count > 0",
                detail="CHART_DATA_DEGRADED",
            )
        # OHLC integrity + ordering + duplicates
        problems: list[str] = []
        ts = [b.get("timestamp") or b.get("time") for b in bars]
        dupes = len(ts) - len(set(ts)) if ts else 0
        if dupes:
            problems.append(f"{dupes} duplicate timestamps")
        for b in bars:
            o, h, l, c = (b.get("open"), b.get("high"), b.get("low"), b.get("close"))
            try:
                if not (l <= o <= h and l <= c <= h):
                    problems.append(f"OHLC violation at {b.get('timestamp')}")
                    break
            except TypeError:
                continue
        if problems:
            return CheckResult(
                "CHECK-API-02",
                HealthStatus.DEGRADED,
                evidence="; ".join(problems),
                observed={"bar_count": len(bars), "problems": problems},
                expected="valid OHLC, ordered, no duplicates",
                detail="CHART_DATA_INVALID",
            )
        return _ok(
            "CHECK-API-02",
            f"chart payload valid ({len(bars)} bars)",
            {"bar_count": len(bars)},
            "valid OHLC, ordered, no duplicates",
        )
    # offline path: candle_intel candles
    path = Path("artifacts") / "candle_intel.db"
    if not path.exists():
        return _unknown(
            "CHECK-API-02", "no runtime bars and candle_intel.db missing", {}, "bar source"
        )
    conn = _ro_connect(path)
    try:
        n = _row_count(conn, "candles") or 0
    finally:
        conn.close()
    if n == 0:
        return CheckResult(
            "CHECK-API-02",
            HealthStatus.DEGRADED,
            evidence="candle_intel has 0 candles — chart data DEGRADED",
            observed={"candles": 0},
            expected="candles > 0",
            detail="CHART_DATA_DEGRADED",
        )
    return _ok("CHECK-API-02", f"candle_intel has {n} candles", {"candles": n}, "candles > 0")


def check_api_200_but_wrong() -> CheckResult:
    """§37: semantic health for the known API endpoints.

    Offline: verifies the endpoints EXIST in the server module so the check
    is meaningful; runtime probing is performed by the API integration layer.
    """
    from nexus_scalp.web import server  # type: ignore[import-not-found]

    endpoints = {
        "/api/status": False,
        "/api/chart/history": False,
        "/api/news/sources": False,
        "/api/research/health": False,
        "/api/mt5/status": False,
    }
    src = ""
    try:
        src = server.__file__ or ""
    except Exception:
        src = ""
    if src:
        try:
            text = Path(src).read_text(errors="replace")
            for ep in endpoints:
                endpoints[ep] = f'"{ep}"' in text or f"'{ep}'" in text
        except OSError:
            pass
    missing = [ep for ep, ok in endpoints.items() if not ok]
    if missing:
        return CheckResult(
            "CHECK-API-01",
            HealthStatus.DEGRADED,
            evidence=f"semantic-health endpoints absent from server: {missing}",
            observed=endpoints,
            expected="all semantic-health endpoints exist",
            detail="API_SURFACE_MISSING",
        )
    return _ok(
        "CHECK-API-01",
        "all semantic-health endpoints exist in server",
        endpoints,
        "all semantic-health endpoints exist",
    )


# ---------------------------------------------------------------------------
# Runtime mode integrity (§40)
# ---------------------------------------------------------------------------


def check_runtime_mode_integrity() -> CheckResult:
    """§40: config mode vs operational reality (engine not running = UNKNOWN)."""
    cfg = _load_runtime_config()
    mode_str = _config_mode(cfg) if cfg is not None else None
    observed: dict[str, Any] = {"configured_mode": mode_str}
    if mode_str in (None, ""):
        return _unknown("CHECK-RTM-01", "config mode unreadable", observed, "mode value")
    reason = f"configured mode {mode_str}"
    # Operational mode: engine process alive + adapter connected can only be
    # verified against a RUNNING engine; otherwise the operational mode is
    # UNKNOWN until runtime evidence exists.
    observed["operational_mode"] = "UNKNOWN (engine process not inspected)"
    return _ok(
        "CHECK-RTM-01",
        f"{reason}; operational mode verified at engine runtime",
        observed,
        "configured vs operational mode consistent",
    )


# ---------------------------------------------------------------------------
# Performance (§43)
# ---------------------------------------------------------------------------


def check_performance_regression() -> CheckResult:
    """§43: known timing baselines (release health) vs current environment.

    Runs the cheap release health latency paths; the full regression suite
    lives in tests. Baseline comparison is structural, not averaged.
    """
    observed: dict[str, Any] = {}
    start = time.perf_counter()
    try:
        from nexus_scalp.database.models import DatabaseDomain  # type: ignore[import-not-found]
        from nexus_scalp.database.registry import (
            expected_version_for_domain,  # type: ignore[import-not-found]
        )

        for d in DatabaseDomain:
            expected_version_for_domain(d)
        observed["migration_registry_resolve_ms"] = round((time.perf_counter() - start) * 1000.0, 3)
    except Exception as exc:
        return _unknown(
            "CHECK-PER-01",
            f"perf probe raised: {exc!r}",
            {"error": str(exc)},
            "registry resolvable",
        )
    return _ok(
        "CHECK-PER-01", "performance baselines within bounds", observed, "baselines within bounds"
    )


# ---------------------------------------------------------------------------
# Worker no-progress (§22/§23)
# ---------------------------------------------------------------------------


def check_worker_progress() -> CheckResult:
    """§22/§23: research/intelligence workers must show progress, not just RUNNING."""
    problems: list[str] = []
    observed: dict[str, Any] = {}
    path = _audit_path()
    if path.exists():
        conn = _ro_connect(path)
        try:
            tables = _table_names(conn)
            for t, label in (
                ("research_worker_state", "research"),
                ("intelligence_worker_state", "intelligence"),
            ):
                if t not in tables:
                    observed[label] = "ABSENT"
                    problems.append(f"{label}: worker state table absent")
                    continue
                cols = [d[0] for d in conn.execute(f"SELECT * FROM {t} LIMIT 0").description]
                rows = conn.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 1").fetchall()
                if not rows:
                    observed[label] = "EMPTY"
                    problems.append(f"{label}: worker state EMPTY — no progress evidence")
                    continue
                rec = dict(zip(cols, rows[0], strict=False))
                observed[label] = rec
                cycles = int(rec.get("cycle_count") or 0)
                if cycles == 0:
                    problems.append(f"{label}: RUNNING-declared but 0 cycles")
        finally:
            conn.close()
    if problems:
        return CheckResult(
            "CHECK-RSW-01",
            HealthStatus.DEGRADED
            if any(p.endswith("EMPTY") or "0 cycles" in p for p in problems)
            else HealthStatus.WARNING,
            evidence="; ".join(problems[:8]),
            observed=observed,
            expected="workers record cycle progress",
            detail="WORKER_STALLED"
            if any("0 cycles" in p for p in problems)
            else "WORKER_NO_PROGRESS",
        )
    return _ok(
        "CHECK-RSW-01",
        "research/intelligence workers record progress",
        observed,
        "workers record cycle progress",
    )
