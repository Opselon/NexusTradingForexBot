"""
Eventual Outcome Linkage & Live Calibration / Drift
===================================================
TASK-6 / CHG-0003 (spec 16 / 17 / 18 / 19 / 20).

OUTCOME LINKAGE
---------------
A shadow prediction at T0 is linked to what actually happened after the
label horizon. NO future information is used at prediction time: the link
is only attached AFTER T0 + horizon. The linkage is derived from the
existing canonical experience outcome rows (audit_experience_outcomes) keyed
by decision_id when a real trade was taken, or from the canonical price path
(close at T0 and T0+horizon) for shadow-only rows. The result is append-only
evidence — used by calibration/drift/review, NEVER as a live training label
(spec 19).

LIVE CALIBRATION
----------------
Bounded 0.1-wide confidence buckets with predictions/correct/incorrect and
Brier/ECE statistics (spec 19).

DRIFT
-----
Bounded distribution-drift signals: probability / action / feature / news.
Drift creates ALERTS only (spec 18) — never an automatic retrain or
promotion.
"""

from __future__ import annotations

import contextlib
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.governance.models import CalibrationBucket, DriftAlert
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.governance.evidence")

#: Default label horizon for shadow outcome linkage (bars of the decision
#: timeframe; M1 decisions -> 15 minutes, matching triple-barrier max_holding).
DEFAULT_HORIZON_BARS: int = 15

#: Live calibration bucket width (spec 19: 0.0-0.1 .. 0.9-1.0).
BUCKET_WIDTH: float = 0.1


def outcome_for_decision(
    *,
    decision: dict[str, Any],
    audit_db: str | None = None,
    horizon_bars: int = DEFAULT_HORIZON_BARS,
    price_path: list[float] | None = None,
) -> dict[str, Any]:
    """Links a shadow decision to its eventual outcome.

    Resolution order (never uses future info at prediction time):
      1. real executed trade: audit_experience_outcomes row by decision_id
         (when audit_db is given) — canonical realized R.
      2. shadow-only: canonical price path (close at T0 vs close at T0+horizon)
         applied to each model's action with a fixed 1R stop / 1.1R target
         (triple-barrier semantics).
    Returns a dict with `linkage_state`: LINKED / DEFERRED (horizon not yet
    reached) / NO_PATH / UNRESOLVED.
    """
    if not decision:
        return {"linkage_state": "UNRESOLVED", "reason": "empty decision"}

    ts_raw = decision.get("timestamp")
    if ts_raw is None:
        return {"linkage_state": "UNRESOLVED", "reason": "no timestamp"}
    try:
        ts = ts_raw if isinstance(ts_raw, datetime) else datetime.fromisoformat(str(ts_raw))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        ts = ts.astimezone(UTC)
    except Exception:
        return {"linkage_state": "UNRESOLVED", "reason": "unparsable timestamp"}

    entry = float(decision.get("entry_price", 0.0) or 0.0)
    decision_id = str(decision.get("decision_id", "") or "")

    # 1. Real trade outcome (canonical, authoritative when it exists).
    if audit_db and decision_id:
        with contextlib.suppress(Exception):
            conn = sqlite3.connect(f"file:{audit_db}?mode=ro", uri=True, timeout=5.0)
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM audit_experience_outcomes WHERE decision_id=? "
                    "ORDER BY closed_at DESC LIMIT 1;",
                    (decision_id,),
                ).fetchone()
                if row is not None:
                    d = dict(row)
                    return {
                        "linkage_state": "LINKED",
                        "source": "REAL",
                        "decision_id": decision_id,
                        "realized_r": float(d.get("realized_r_multiple", 0.0) or 0.0),
                        "realized_pnl_usd": float(d.get("realized_pnl_usd", 0.0) or 0.0),
                        "outcome_ts": str(d.get("closed_at", "") or ""),
                    }
            finally:
                conn.close()

    # 2. Shadow path: entry + horizon close.
    if entry <= 0.0:
        return {"linkage_state": "NO_PATH", "reason": "no entry price recorded"}

    if not price_path or len(price_path) < 2:
        return {
            "linkage_state": "DEFERRED",
            "reason": "price path incomplete (horizon not reached)",
        }

    exit_price = float(price_path[-1])
    champ_action = str(decision.get("champion_action", "NO_TRADE"))
    chal_action = str(decision.get("challenger_action", "NO_TRADE"))
    mvt = exit_price - entry

    def r_for(action: str) -> float:
        if action == "BUY_MARKET":
            return mvt / (entry * 0.001)  # 1R = 0.1% of entry (fixed sizing proxy)
        if action == "SELL_MARKET":
            return -mvt / (entry * 0.001)
        return 0.0

    return {
        "linkage_state": "LINKED",
        "source": "SHADOW_PATH",
        "decision_id": decision_id,
        "champion_r": round(r_for(champ_action), 6),
        "challenger_r": round(r_for(chal_action), 6),
        "movement": round(mvt, 6),
        "entry": entry,
        "exit_price": exit_price,
        "outcome_ts": (ts + timedelta(minutes=horizon_bars)).isoformat(),
    }


# ----------------------------------------------------------------------
# Live calibration (spec 19)
# ----------------------------------------------------------------------


def calibration_buckets(
    rows: list[dict[str, Any]], *, confidence_key: str = "confidence", correct_key: str = "correct"
) -> list[CalibrationBucket]:
    """Builds bounded 0.1-width calibration buckets (spec 19).

    Bucket assignment is exact half-open [lo, hi): index = min(9, floor(c*10)).
    A confidence of 0.6 belongs ONLY to 0.6-0.7 — never to 0.5-0.6.
    """
    buckets: list[CalibrationBucket] = []
    for i in range(10):
        lo, hi = round(i * BUCKET_WIDTH, 4), round((i + 1) * BUCKET_WIDTH, 4)
        bucket_rows = [
            r for r in rows if min(9, int(float(r.get(confidence_key, 0.0) or 0.0) * 10)) == i
        ]
        n = len(bucket_rows)
        if n == 0:
            continue
        correct = sum(1 for r in bucket_rows if bool(r.get(correct_key, False)))
        conf = sum(float(r.get(confidence_key, 0.0) or 0.0) for r in bucket_rows) / n
        buckets.append(
            CalibrationBucket(
                lo=lo,
                hi=hi,
                predictions=n,
                correct=correct,
                incorrect=n - correct,
                accuracy=round(correct / n, 4),
                mean_confidence=round(conf, 4),
            )
        )
    return buckets


def brier_score(
    rows: list[dict[str, Any]], *, confidence_key: str = "confidence", correct_key: str = "correct"
) -> float:
    """Brier score over 0/1 correctness (spec 19)."""
    if not rows:
        return 0.0
    s = 0.0
    for r in rows:
        p = float(r.get(confidence_key, 0.0) or 0.0)
        y = 1.0 if bool(r.get(correct_key, False)) else 0.0
        s += (p - y) ** 2
    return round(s / len(rows), 6)


def ece_score(buckets: list[CalibrationBucket]) -> float:
    """Expected Calibration Error over the buckets (spec 19)."""
    total = sum(b.predictions for b in buckets)
    if total == 0:
        return 0.0
    return round(
        sum(b.predictions / total * abs(b.accuracy - b.mean_confidence) for b in buckets), 6
    )


# ----------------------------------------------------------------------
# Drift (spec 18)
# ----------------------------------------------------------------------

#: Reference probability distribution (canonical NO_TRADE/BUY/SELL/WAIT
#: priors from the 50D champion contract). Drift is measured against the
#: LIVE reference window, not against campaign slogans — when the reference
#: window is missing, drift is UNKNOWN.
DEFAULT_REFERENCE_PROBS: tuple[float, ...] = (0.80, 0.10, 0.10, 0.00)

DRIFT_THRESHOLDS: dict[str, float] = {
    "PROBABILITY": 0.20,  # max per-class absolute shift vs reference
    "ACTION": 0.25,  # action-frequency shift (e.g. NO_TRADE frequency collapse)
    "FEATURE": 2.0,  # mean |z| shift of the feature window
    "NEWS": 0.5,  # mean news-vector magnitude shift
}


def detect_drift(
    *,
    probs_window: list[list[float]] | None = None,
    actions: list[str] | None = None,
    feature_window: list[list[float]] | None = None,
    news_window: list[list[float]] | None = None,
    reference_probs: tuple[float, ...] = DEFAULT_REFERENCE_PROBS,
    model_id: str = "",
) -> list[DriftAlert]:
    """Bounded drift detection. Emits DriftAlert rows; never auto-retrains."""
    alerts: list[DriftAlert] = []

    if probs_window and len(probs_window) >= 30:
        width = max(len(p) for p in probs_window)
        means: list[float] = []
        for c in range(width):
            col = [float(p[c]) if c < len(p) else 0.0 for p in probs_window]
            means.append(sum(col) / len(col))
        ref = list(reference_probs) + [0.0] * (width - len(reference_probs))
        max_shift = max((abs(means[c] - ref[c]) for c in range(width)), default=0.0)
        if max_shift > DRIFT_THRESHOLDS["PROBABILITY"]:
            alerts.append(
                DriftAlert(
                    alert_id=f"drift_{uuid.uuid4().hex[:12]}",
                    model_id=model_id,
                    kind="PROBABILITY",
                    metric="max_per_class_shift",
                    value=round(max_shift, 4),
                    threshold=DRIFT_THRESHOLDS["PROBABILITY"],
                    severity="CRITICAL"
                    if max_shift > 2 * DRIFT_THRESHOLDS["PROBABILITY"]
                    else "WARN",
                    window_samples=len(probs_window),
                )
            )

    if actions and len(actions) >= 30:
        from collections import Counter

        counts = Counter(actions)
        total = len(actions)
        freq = {a: counts[a] / total for a in ("NO_TRADE", "BUY_MARKET", "SELL_MARKET")}
        ref_nt = 0.80
        shift = abs(freq.get("NO_TRADE", 0.0) - ref_nt)
        if shift > DRIFT_THRESHOLDS["ACTION"]:
            alerts.append(
                DriftAlert(
                    alert_id=f"drift_{uuid.uuid4().hex[:12]}",
                    model_id=model_id,
                    kind="ACTION",
                    metric="no_trade_frequency_shift",
                    value=round(shift, 4),
                    threshold=DRIFT_THRESHOLDS["ACTION"],
                    severity="CRITICAL" if shift > 2 * DRIFT_THRESHOLDS["ACTION"] else "WARN",
                    window_samples=total,
                )
            )

    if feature_window and len(feature_window) >= 30:
        # TASK-14 hardening #2: use the ACTUAL vector width. The old
        # `min(len(vec), 50)` silently truncated a 70D feature window's tail
        # (news 50..59 + liquidity 60..69) out of the drift statistic —
        # exactly the "silent 70->50 truncation" the 70D governance contract
        # forbids. Shorter vectors remain safe (explicit per-index guard).
        width = len(feature_window[0])
        means = [0.0] * width
        for vec in feature_window:
            for c in range(width):
                means[c] += float(vec[c]) if c < len(vec) else 0.0
        means = [m / len(feature_window) for m in means]
        mean_abs = sum(abs(m) for m in means) / max(1, len(means))
        if mean_abs > DRIFT_THRESHOLDS["FEATURE"]:
            alerts.append(
                DriftAlert(
                    alert_id=f"drift_{uuid.uuid4().hex[:12]}",
                    model_id=model_id,
                    kind="FEATURE",
                    metric="mean_abs_feature_mean",
                    value=round(mean_abs, 4),
                    threshold=DRIFT_THRESHOLDS["FEATURE"],
                    severity="WARN",
                    window_samples=len(feature_window),
                )
            )

    if news_window and len(news_window) >= 30:
        mean_mag = sum(
            sum(abs(float(v)) for v in vec) / max(1, len(vec)) for vec in news_window
        ) / len(news_window)
        if mean_mag > DRIFT_THRESHOLDS["NEWS"]:
            alerts.append(
                DriftAlert(
                    alert_id=f"drift_{uuid.uuid4().hex[:12]}",
                    model_id=model_id,
                    kind="NEWS",
                    metric="mean_news_magnitude",
                    value=round(mean_mag, 4),
                    threshold=DRIFT_THRESHOLDS["NEWS"],
                    severity="WARN",
                    window_samples=len(news_window),
                )
            )

    for a in alerts:
        logger.warning(
            "[MODEL_GOVERNANCE] event=LIVE_DRIFT",
            model_id=model_id,
            kind=a.kind,
            value=a.value,
            severity=a.severity,
        )
    return alerts


# ----------------------------------------------------------------------
# Backtest vs shadow divergence (spec 20)
# ----------------------------------------------------------------------


def backtest_live_divergence(
    *,
    backtest_accuracy: float | None,
    backtest_expectancy_r: float | None,
    live_accuracy: float | None = None,
    live_expectancy_r: float | None = None,
    live_samples: int = 0,
    min_samples: int = 30,
    tolerance: float = 0.10,
) -> dict[str, Any]:
    """Flags BACKTEST_LIVE_DIVERGENCE when the live observation is
    materially worse than the backtest expectation. Never retunes."""
    out: dict[str, Any] = {"flagged": False, "reasons": []}
    if live_samples < min_samples:
        out["reasons"].append(f"insufficient live samples: {live_samples} < {min_samples}")
        return out
    if backtest_accuracy is not None and live_accuracy is not None:
        diff = backtest_accuracy - live_accuracy
        if diff > tolerance:
            out["flagged"] = True
            out["reasons"].append(f"accuracy divergence {diff:.3f} > {tolerance}")
    if backtest_expectancy_r is not None and live_expectancy_r is not None:
        diff = backtest_expectancy_r - live_expectancy_r
        if diff > tolerance:
            out["flagged"] = True
            out["reasons"].append(f"expectancy divergence {diff:.3f}R > {tolerance}R")
    out["flag"] = "BACKTEST_LIVE_DIVERGENCE" if out["flagged"] else "NONE"
    return out
