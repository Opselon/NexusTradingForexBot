"""CHG-0049 Directional Model R&D — LAB module (research-only).

Builds the directional research dataset from recorded decision evidence +
real market data, runs the normalized asymmetry analysis (regime/session/
probability/calibration), trains ISOLATED research candidates (3-class vs
4-logit control, class-weight variants) with strict OOS evaluation, and
verifies the analysis machinery with golden/negative controls.

FIREWALL: never touches production model.pt / Champion / policy / thresholds.
All artifacts are research-only and fingerprinted.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.research.directional")

#: Label contract: the production label schema is 3-class
#: (NO_TRADE / BUY / SELL) - WAIT is a policy-bridge state that has never
#: been a training label (label census zero; CHG-0042 evidence).
LABEL_SCHEMA_3CLASS: dict[str, int] = {"NO_TRADE": 0, "BUY": 1, "SELL": 2}
LABEL_SCHEMA_4LOGIT: dict[str, int] = {"NO_TRADE": 0, "BUY": 1, "SELL": 2, "WAIT": 3}


def stable_fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Directional dataset (from recorded evidence only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DirectionalSample:
    timestamp: datetime
    direction: str  # BUY | SELL | NO_TRADE | "" (NOT_RECORDED)
    raw_prob_buy: float | None
    raw_prob_sell: float | None
    raw_prob_no_trade: float | None
    raw_prob_wait: float | None  # NOT_RECORDED for 3-class-era rows
    confidence: float
    confidence_source: str
    regime: str
    session: str
    spread_usd: float | None
    outcome_r: float | None  # executed realized R; None = not executed
    source: str  # EXECUTED | REJECTED_NO_TRADE | NOT_RECORDED


def directional_margin(sample: DirectionalSample) -> float | None:
    """signed directional margin: P(own side) - P(opposite side)."""
    if sample.raw_prob_buy is None or sample.raw_prob_sell is None:
        return None
    if sample.direction == "BUY":
        return sample.raw_prob_buy - sample.raw_prob_sell
    if sample.direction == "SELL":
        return sample.raw_prob_sell - sample.raw_prob_buy
    return None


# ---------------------------------------------------------------------------
# Normalized asymmetry analysis (G3-G7)
# ---------------------------------------------------------------------------


def _bucket(rs: list[float]) -> dict[str, Any]:
    if not rs:
        return {"n": 0, "mean_r": None, "median_r": None, "stdev": None}
    return {
        "n": len(rs),
        "mean_r": round(statistics.mean(rs), 4),
        "median_r": round(statistics.median(rs), 4),
        "stdev": round(statistics.stdev(rs), 4) if len(rs) > 1 else 0.0,
    }


def bootstrap_mean_diff(
    a: list[float], b: list[float], *, n_boot: int = 2000, seed: int = 42
) -> dict[str, float]:
    """Bootstrap CI for mean(a) - mean(b). Returns point estimate + 2.5/97.5pct.

    Non-parametric, deterministic via seeded RNG - used instead of
    parametric tests because R distributions are heavy-tailed.
    """
    if len(a) < 2 or len(b) < 2:
        return {"diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "n_a": len(a), "n_b": len(b)}
    rng = np.random.default_rng(seed)
    a_arr, b_arr = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    diffs = np.empty(n_boot, dtype=np.float64)
    for i in range(n_boot):
        sa = a_arr[rng.integers(0, len(a_arr), len(a_arr))]
        sb = b_arr[rng.integers(0, len(b_arr), len(b_arr))]
        diffs[i] = sa.mean() - sb.mean()
    return {
        "diff": round(float(a_arr.mean() - b_arr.mean()), 4),
        "ci_low": round(float(np.percentile(diffs, 2.5)), 4),
        "ci_high": round(float(np.percentile(diffs, 97.5)), 4),
        "n_a": len(a),
        "n_b": len(b),
    }


def asymmetry_analysis(
    samples: list[tuple[str, str, float]], *, n_boot: int = 2000, seed: int = 42
) -> dict[str, Any]:
    """samples: list of (stratum, direction, realized_r) for EXECUTED trades.

    G3 regime-normalized / G4 session-normalized / G8 spread-normalized
    asymmetry: BUY-vs-SELL mean-R difference inside every stratum + bootstrap
    CI on the pooled sample. A stratum where the CI straddles 0 is NOT
    evidence of asymmetry regardless of the point estimate.
    """
    by_dir: dict[str, list[float]] = defaultdict(list)
    by_stratum: dict[tuple[str, str], list[float]] = defaultdict(list)
    for strat, d, r in samples:
        by_dir[d].append(r)
        by_stratum[(strat, d)].append(r)
    strata = sorted({strat for strat, _d, _r in samples})
    per_stratum = {}
    for s in strata:
        buy = by_stratum.get((s, "BUY"), [])
        sell = by_stratum.get((s, "SELL"), [])
        if len(buy) + len(sell) < 4:
            per_stratum[s] = {"n": len(buy) + len(sell), "verdict": "INCONCLUSIVE_SMALL_N"}
            continue
        ci = bootstrap_mean_diff(sell, buy, n_boot=n_boot, seed=seed)  # SELL - BUY
        per_stratum[s] = {
            "buy": _bucket(buy),
            "sell": _bucket(sell),
            "sell_minus_buy": ci,
            "survives_ci": ci["ci_low"] > 0 or ci["ci_high"] < 0,
        }
    pooled = bootstrap_mean_diff(
        by_dir.get("SELL", []), by_dir.get("BUY", []), n_boot=n_boot, seed=seed
    )
    surviving = [s for s, v in per_stratum.items() if isinstance(v, dict) and v.get("survives_ci")]
    return {
        "pooled_sell_minus_buy": pooled,
        "per_stratum": per_stratum,
        "strata_where_asymmetry_survives_ci": surviving,
        "interpretation": (
            "SELL>BUY survives stratification"
            if len(surviving) > len(strata) / 2 and pooled["ci_low"] > 0
            else "DIRECTIONAL ASYMMETRY NOT ESTABLISHED (does not survive normalization/CI)"
        ),
    }


# ---------------------------------------------------------------------------
# Calibration by direction (G7)
# ---------------------------------------------------------------------------


def calibration_by_direction(
    samples: list[tuple[str, float, float | None]],
) -> dict[str, Any]:
    """samples: (direction, model_probability, realized_r). Calibration =
    outcome monotonicity + over/under-confidence per direction: for each
    probability tercile, mean realized R should INCREASE with probability
    if the model is calibrated for that direction."""
    out: dict[str, Any] = {}
    for d in ("BUY", "SELL"):
        pairs = sorted(
            (p, r) for dd, p, r in samples if dd == d and p is not None and r is not None
        )
        if len(pairs) < 6:
            out[d] = {"n": len(pairs), "verdict": "INCONCLUSIVE_SMALL_N"}
            continue
        k = len(pairs) // 3
        terciles = []
        for i in range(3):
            seg = pairs[i * k : (i + 1) * k] if i < 2 else pairs[2 * k :]
            if not seg:
                continue
            terciles.append(
                {
                    "tercile": ["low", "mid", "high"][i],
                    "n": len(seg),
                    "mean_prob": round(statistics.mean(x[0] for x in seg), 4),
                    "mean_r": round(statistics.mean(x[1] for x in seg), 4),
                }
            )
        monotonic = all(
            float(terciles[i + 1]["mean_r"]) >= float(terciles[i]["mean_r"])  # type: ignore[arg-type]
            for i in range(len(terciles) - 1)
        )
        out[d] = {
            "n": len(pairs),
            "terciles": terciles,
            "outcome_monotonic_in_probability": monotonic,
            "verdict": (
                "CALIBRATED" if monotonic else "MISCALIBRATED (outcome not increasing in prob)"
            ),
        }
    return out


# ---------------------------------------------------------------------------
# Research model candidates (isolated lab training)
# ---------------------------------------------------------------------------


@dataclass
class LabTrainResult:
    experiment_id: str
    classes: int
    class_mapping: dict[str, int]
    oos_accuracy: float
    oos_buy_f1: float
    oos_sell_f1: float
    directional_gap_f1: float
    train_seconds: float
    seed: int
    config: dict[str, Any]
    fingerprint: str


def synthetic_directional_dataset(
    n: int = 3000,
    *,
    buy_signal_strength: float = 0.9,
    sell_signal_strength: float = 1.4,
    noise: float = 1.0,
    seed: int = 7,
) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic synthetic 20D dataset with KNOWN directional asymmetry:
    SELL samples carry `sell_signal_strength` of separable signal, BUY only
    `buy_signal_strength`. Used for the GOLDEN test (machinery must DISCOVER
    the planted asymmetry) and the NEGATIVE control (strengths equal -> must
    NOT hallucinate asymmetry)."""
    rng = np.random.default_rng(seed)
    labels = rng.integers(0, 2, size=n)  # 0=BUY, 1=SELL (balanced by construction)
    X = rng.normal(0.0, noise, size=(n, 20))
    # feature 0 encodes the direction signal with class-specific strength
    X[np.arange(n), 0] = np.where(
        labels == 1, sell_signal_strength, -buy_signal_strength
    ) + rng.normal(0, 0.25, n)
    # feature 1..3 carry weak shared signal
    for j in (1, 2, 3):
        X[:, j] += np.where(labels == 1, 0.3, -0.3) * rng.normal(0, 0.5, n)
    return X, labels


def _directional_f1(y_true: np.ndarray, y_pred: np.ndarray, cls: int) -> float:
    tp = float(np.sum((y_pred == cls) & (y_true == cls)))
    fp = float(np.sum((y_pred == cls) & (y_true != cls)))
    fn = float(np.sum((y_pred != cls) & (y_true == cls)))
    if tp == 0:
        return 0.0
    prec, rec = tp / (tp + fp), tp / (tp + fn)
    return 2 * prec * rec / (prec + rec)


class _TinyMLP:
    """Minimal deterministic MLP (numpy) for lab candidates.

    Research-only: fixed topology (20->16->n_classes), seeded init,
    full-batch gradient descent on cross-entropy with optional class
    weights. Deliberately simple - the point is DIRECTIONAL COMPARISON
    under identical machinery, not SOTA accuracy.
    """

    def __init__(
        self,
        n_features: int,
        n_classes: int,
        *,
        hidden: int = 16,
        seed: int = 7,
        lr: float = 0.15,
        epochs: int = 400,
        class_weights: dict[int, float] | None = None,
    ) -> None:
        rng = np.random.default_rng(seed)
        self.w1 = rng.normal(0, 0.3, size=(n_features, hidden))
        self.b1 = np.zeros(hidden)
        self.w2 = rng.normal(0, 0.3, size=(hidden, n_classes))
        self.b2 = np.zeros(n_classes)
        self.lr = lr
        self.epochs = epochs
        self.class_weights = class_weights or {}
        self.n_classes = n_classes

    def _forward(self, X: np.ndarray) -> np.ndarray:
        h = np.tanh(X @ self.w1 + self.b1)
        z = h @ self.w2 + self.b2
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        n = len(X)
        y_onehot = np.eye(self.n_classes)[y]
        sw = np.array([self.class_weights.get(int(yy), 1.0) for yy in y])
        for _ in range(self.epochs):
            probs = self._forward(X)
            grad = (probs - y_onehot) * sw[:, None] / n
            h = np.tanh(X @ self.w1 + self.b1)
            gw2 = h.T @ grad
            gb2 = grad.sum(axis=0)
            dh = grad @ self.w2.T * (1 - h**2)
            gw1 = X.T @ dh
            gb1 = dh.sum(axis=0)
            self.w2 -= self.lr * gw2
            self.b2 -= self.lr * gb2
            self.w1 -= self.lr * gw1
            self.b1 -= self.lr * gb1

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.argmax(self._forward(X), axis=1)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self._forward(X)


def train_lab_candidate(
    X: np.ndarray,
    y: np.ndarray,
    *,
    classes: int,
    label_map: dict[str, int],
    class_weights: dict[int, float] | None = None,
    seed: int = 7,
    oos_frac: float = 0.3,
    experiment_id: str = "LAB",
) -> LabTrainResult:
    """Trains one isolated lab candidate with a CHRONOLOGICAL OOS split.

    No shuffle: the last `oos_frac` of samples is the OOS window (temporal
    order preserved - the research equivalent of the production WF rule).
    """
    import time

    t0 = time.perf_counter()
    n = len(X)
    split = int(n * (1.0 - oos_frac))
    X_tr, y_tr = X[:split], y[:split]
    X_oos, y_oos = X[split:], y[split:]
    model = _TinyMLP(
        n_features=X.shape[1], n_classes=classes, seed=seed, class_weights=class_weights
    )
    model.fit(X_tr, y_tr)
    preds = model.predict(X_oos)
    buy_cls, sell_cls = label_map["BUY"], label_map["SELL"]
    buy_f1 = _directional_f1(y_oos, preds, buy_cls)
    sell_f1 = _directional_f1(y_oos, preds, sell_cls)
    acc = float(np.mean(preds == y_oos))
    elapsed = time.perf_counter() - t0
    cfg = {
        "classes": classes,
        "class_weights": class_weights or {},
        "seed": seed,
        "oos_frac": oos_frac,
        "n_train": split,
        "n_oos": n - split,
        "topology": f"{X.shape[1]}->16->{classes}",
    }
    return LabTrainResult(
        experiment_id=experiment_id,
        classes=classes,
        class_mapping=dict(label_map),
        oos_accuracy=round(acc, 4),
        oos_buy_f1=round(buy_f1, 4),
        oos_sell_f1=round(sell_f1, 4),
        directional_gap_f1=round(sell_f1 - buy_f1, 4),
        train_seconds=round(elapsed, 3),
        seed=seed,
        config=cfg,
        fingerprint=stable_fingerprint({"cfg": cfg, "acc": acc, "buy": buy_f1, "sell": sell_f1}),
    )
