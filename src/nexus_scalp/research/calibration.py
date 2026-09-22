"""Probability calibration for ScalpNet softmax outputs (ML-VAL-002).

Implements the Guo et al. (2017) "On Calibration of Modern Neural Networks"
toolbox for the 3-class (NO_TRADE / BUY / SELL) trading contract:

* :func:`compute_ece`      — Expected Calibration Error over confidence bins
* :func:`reliability_diagram` — bin table feeding the reliability plot
* :func:`brier_score`      — multiclass Brier score
* :func:`log_loss`         — NLL of the predicted class probability
* :class:`TemperatureScaler` — learns a single scalar ``T > 0`` on logits that
  minimizes NLL on a held-out validation fold; probabilities are then
  ``softmax(logits / T)``.

Design constraints (repo conventions):

* The live inference path is ``scalp_v1`` (50D) with a 3-class head — the 4th
  WAIT logit is contractually DEAD (``model_lifecycle/model_class_contract.py``).
  All functions here operate on the leading ``n_classes`` columns; a 4-wide
  legacy checkpoint is narrowed with a loud warning rather than silently
  treated as 4 classes.
* Heavy imports (``numpy``/``torch``) stay lazy at function scope so this module
  imports cleanly in the slim test venv (no torch needed to ``import`` it).
* Temperature fitting is deterministic (fixed seed, closed-form-free LBFGS-free
  convex solve via Adam is unnecessary — NLL in T is 1-D and smooth, so a plain
  bisection on the derivative converges monotonically and reproducibly).
* Nothing here mutates production weights: ``NON_GOALS`` of the task forbid it.
  ``TemperatureScaler`` wraps a model or raw logits; application is opt-in via
  :meth:`TemperatureScaler.apply_to_logits`.

The module deliberately does NOT replace the existing binary
``model_lifecycle.confidence_calibration.evaluate_calibration`` (Platt on
win/loss confidence) — that is the runtime risk-sizing path. This is the
research/validation-grade multiclass evaluation used to prove the live
softmax is (or is not) trustworthy before a policy threshold change.
"""

from __future__ import annotations

__all__ = [
    "CalibrationReport",
    "TemperatureScaler",
    "apply_temperature",
    "brier_score",
    "compute_ece",
    "log_loss",
    "nll_loss",
    "reliability_diagram",
    "validate_probs",
    "wrap_model_temperature",
]

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from torch import nn


# ---------------------------------------------------------------------------
# Input validation (shared by every metric + the scaler)
# ---------------------------------------------------------------------------


def validate_probs(probs: Any, *, n_classes: int | None = None) -> tuple[int, int]:
    """Validate a (N, C) probability matrix and return ``(n_rows, n_cols)``.

    Raises ``ValueError`` on any shape/finite/nonnegativity violation. Keeps
    every public function's failure mode identical and fail-loud.
    """
    import numpy as np

    if not isinstance(probs, np.ndarray):
        raise TypeError("probs must be a numpy.ndarray")
    if probs.ndim != 2:
        raise ValueError(f"probs must be 2-D (N, C), got shape {probs.shape}")
    n, c = probs.shape
    if n == 0:
        raise ValueError("probs must contain at least one row")
    if c < 2:
        raise ValueError(f"probs must have >=2 classes, got {c}")
    if n_classes is not None and c != n_classes:
        raise ValueError(f"expected {n_classes} classes, got {c}")
    if not np.isfinite(probs).all():
        raise ValueError("probs contains non-finite values")
    if (probs < 0.0).any():
        raise ValueError("probs contains negative values")
    return int(n), int(c)


def _bin_mask(conf: Any, b: int, n_bins: int, edges: Any) -> Any:
    """Confidence mask for bin ``b``.

    Bins are half-open ``[lo, hi)`` except the LAST bin, which is closed
    ``[lo, 1.0]``. The closed top edge is required so a perfectly-calibrated
    flat model (all confidence exactly ``1/C``) is captured in a real bin
    rather than orphaned above the last edge, and so a confidence of exactly
    ``1.0`` lands in the top bin.
    """
    if b < n_bins - 1:
        return (conf >= edges[b]) & (conf < edges[b + 1])
    return (conf >= edges[b]) & (conf <= edges[b + 1])


def _validate_labels(labels: Any, n: int, c: int) -> Any:
    import numpy as np

    y = np.asarray(labels)
    if y.shape != (n,):
        raise ValueError(f"labels shape {y.shape} != ({n},)")
    if not np.issubdtype(y.dtype, np.integer):
        raise ValueError(f"labels must be integer class indices, dtype={y.dtype}")
    if y.min() < 0 or y.max() >= c:
        raise ValueError(f"label {int(y.max())} out of range [0, {c})")
    if not np.isfinite(y).all():
        raise ValueError("labels contain non-finite values")
    return y


def _as_probs(logits_or_probs: Any, *, source: str) -> Any:
    """Coerce ``logits_or_probs`` to a finite float array."""
    import numpy as np

    if isinstance(logits_or_probs, np.ndarray):
        arr = logits_or_probs.astype(np.float64, copy=True)
    else:
        arr = np.asarray(logits_or_probs, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"{source} must be 2-D (N, C), got shape {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError(f"{source} contains non-finite values")
    return arr


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_ece(
    probs: Any,
    labels: Any,
    *,
    n_bins: int = 10,
    n_classes: int | None = None,
) -> float:
    """Expected Calibration Error (Guo et al. 2017, eq. 3) for multiclass.

    ``ECE = sum_b (n_b / N) * |acc_b - conf_b|`` where ``conf_b`` is the mean
    max-probability in bin *b* and ``acc_b`` the fraction of samples in that
    bin whose argmax equals the label. Bins partition ``[0, 1]`` on the
    max-probability; the top bin is closed on the right so a perfect ``1.0``
    confidence lands in the last bin.

    Returns ``0.0`` for a degenerate single-sample-perfect input and a number
    in ``[0, 1]`` otherwise (lower = better calibrated).
    """
    if n_bins < 1:
        raise ValueError(f"n_bins must be >= 1, got {n_bins}")
    n, c = validate_probs(probs, n_classes=n_classes)
    y = _validate_labels(labels, n, c)

    import numpy as np

    p = probs.astype(np.float64, copy=True)
    p /= np.clip(p.sum(axis=1, keepdims=True), 1e-12, None)
    conf = p.max(axis=1)
    pred = p.argmax(axis=1)
    correct = (pred == y).astype(np.float64)

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for b in range(n_bins):
        mask = _bin_mask(conf, b, n_bins, edges)
        nb = int(mask.sum())
        if nb == 0:
            continue
        ece += (nb / n) * abs(correct[mask].mean() - conf[mask].mean())
    return round(float(ece), 6)


def reliability_diagram(
    probs: Any,
    labels: Any,
    *,
    n_bins: int = 10,
    n_classes: int | None = None,
) -> list[dict[str, Any]]:
    """Bin table for a reliability diagram, one dict per populated bin.

    Each entry: ``bin_index``, ``range``, ``n``, ``mean_confidence``,
    ``accuracy``, ``gap`` (accuracy - confidence). Empty bins are omitted so
    the caller can plot a sparse reliability curve without zero-weight points.
    """
    if n_bins < 1:
        raise ValueError(f"n_bins must be >= 1, got {n_bins}")
    n, c = validate_probs(probs, n_classes=n_classes)
    y = _validate_labels(labels, n, c)

    import numpy as np

    p = probs.astype(np.float64, copy=True)
    p /= np.clip(p.sum(axis=1, keepdims=True), 1e-12, None)
    conf = p.max(axis=1)
    pred = p.argmax(axis=1)
    correct = (pred == y).astype(np.float64)

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows: list[dict[str, Any]] = []
    for b in range(n_bins):
        mask = _bin_mask(conf, b, n_bins, edges)
        nb = int(mask.sum())
        if nb == 0:
            continue
        acc = float(correct[mask].mean())
        cf = float(conf[mask].mean())
        rows.append(
            {
                "bin_index": b,
                "range": f"[{edges[b]:.2f}, {edges[b + 1]:.2f}]",
                "n": nb,
                "mean_confidence": round(cf, 6),
                "accuracy": round(acc, 6),
                "gap": round(acc - cf, 6),
            }
        )
    return rows


def brier_score(
    probs: Any,
    labels: Any,
    *,
    n_classes: int | None = None,
) -> float:
    """Multiclass Brier score: mean squared error vs. the one-hot label."""
    n, c = validate_probs(probs, n_classes=n_classes)
    y = _validate_labels(labels, n, c)

    import numpy as np

    p = probs.astype(np.float64, copy=True)
    p /= np.clip(p.sum(axis=1, keepdims=True), 1e-12, None)
    onehot = np.zeros_like(p)
    onehot[np.arange(n), y] = 1.0
    return round(float(np.mean((p - onehot) ** 2)), 6)


def log_loss(
    probs: Any,
    labels: Any,
    *,
    eps: float = 1e-12,
    n_classes: int | None = None,
) -> float:
    """Negative log-likelihood of the true class (lower = better)."""
    if not (0.0 < eps < 1.0):
        raise ValueError(f"eps must be in (0, 1), got {eps}")
    n, c = validate_probs(probs, n_classes=n_classes)
    y = _validate_labels(labels, n, c)

    import numpy as np

    p = probs.astype(np.float64, copy=True)
    p /= np.clip(p.sum(axis=1, keepdims=True), 1e-12, None)
    true_p = np.clip(p[np.arange(n), y], eps, 1.0)
    return round(float(-np.mean(np.log(true_p))), 6)


def nll_loss(probs: Any, labels: Any, **kw: Any) -> float:
    """Alias of :func:`log_loss` (Guo et al. optimize NLL when fitting T)."""
    return log_loss(probs, labels, **kw)


# ---------------------------------------------------------------------------
# Temperature scaling
# ---------------------------------------------------------------------------

_T_MIN = 0.05
_T_MAX = 100.0


def _nll_of_t(t: float, logits: Any, y: Any) -> float:
    """NLL of ``softmax(logits / t)`` — strictly used inside the fit loop."""
    import numpy as np

    z = logits / t
    z = z - z.max(axis=1, keepdims=True)
    exp = np.exp(z)
    p = exp / exp.sum(axis=1, keepdims=True)
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-12, None))))


def fit_temperature(
    logits: Any,
    labels: Any,
    *,
    n_classes: int | None = None,
    tol: float = 1e-9,
    max_iter: int = 200,
) -> float:
    """Fit the scalar temperature ``T`` minimizing NLL on a validation fold.

    NLL(T) is smooth and unimodal in ``log T`` for a fixed logit set, so a
    golden-section search on ``log T`` converges monotonically and
    deterministically (no optimizer state, no seed dependence, no GPU). This is
    deliberately the convex 1-D case of the Guo et al. recipe: they fit T by
    LBFGS on NLL, which for one parameter reduces to the same optimum.

    Raises ``ValueError`` when the fold is degenerate (single class, empty, or
    a logit spread that pushes T outside ``[_T_MIN, _T_MAX]`` — the task's
    ABORT_CONDITIONS for exploding/vanishing logits).
    """
    z = _as_probs(logits, source="logits")
    if n_classes is not None and z.shape[1] != n_classes:
        raise ValueError(f"expected {n_classes} logit columns, got {z.shape[1]}")
    n, c = z.shape
    if n < 2:
        raise ValueError(f"need >=2 validation rows to fit T, got {n}")
    y = _validate_labels(labels, n, c)

    import numpy as np

    if len(np.unique(y)) < 2:
        raise ValueError(
            "validation fold is single-class: temperature is unidentifiable "
            "(NLL is monotone in T, optimum runs to T=0)"
        )
    if not np.isfinite(z).all():
        raise ValueError("logits contain non-finite values")

    lo, hi = math.log(_T_MIN), math.log(_T_MAX)
    invphi = (math.sqrt(5.0) - 1.0) / 2.0  # 1/phi
    invphi2 = (3.0 - math.sqrt(5.0)) / 2.0  # 1/phi^2
    a, b = lo, hi
    c1 = a + invphi2 * (b - a)
    c2 = a + invphi * (b - a)
    f1 = _nll_of_t(math.exp(c1), z, y)
    f2 = _nll_of_t(math.exp(c2), z, y)
    it = 0
    while b - a > tol and it < max_iter:
        if f1 < f2:
            b, c2, f2 = c2, c1, f1
            c1 = a + invphi2 * (b - a)
            f1 = _nll_of_t(math.exp(c1), z, y)
        else:
            a, c1, f1 = c1, c2, f2
            c2 = a + invphi * (b - a)
            f2 = _nll_of_t(math.exp(c2), z, y)
        it += 1
    t_star = math.exp(0.5 * (a + b))
    if not math.isfinite(t_star) or t_star <= _T_MIN or t_star >= _T_MAX:
        raise ValueError(
            f"fitted temperature {t_star!r} is degenerate (outside "
            f"[{_T_MIN}, {_T_MAX}]) — check for exploding validation logits"
        )
    return round(float(t_star), 6)


def apply_temperature(logits: Any, temperature: float, *, n_classes: int | None = None) -> Any:
    """``softmax(logits / T)`` as a numpy (N, C) probability matrix.

    ``temperature == 1.0`` returns raw softmax unchanged (identity). ``T < 1``
    sharpens (more confident), ``T > 1`` flattens (less confident).
    """
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError(f"temperature must be finite and > 0, got {temperature!r}")
    z = _as_probs(logits, source="logits")
    if n_classes is not None and z.shape[1] != n_classes:
        raise ValueError(f"expected {n_classes} logit columns, got {z.shape[1]}")

    import numpy as np

    scaled = z / temperature
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    exp = np.exp(scaled)
    return exp / exp.sum(axis=1, keepdims=True)


@dataclass
class TemperatureScaler:
    """Learns ``T`` on a validation fold, then re-scores any logits.

    A fitted scaler is a pure function of logits: it never touches production
    model weights (task NON_GOALS). Use :meth:`apply_to_logits` to score, or
    :meth:`wrap` to layer it in front of a torch model's softmax in research
    code.
    """

    temperature: float = 1.0
    n_classes: int | None = None
    fitted: bool = field(default=False, compare=False)
    fit_n: int = field(default=0, compare=False)
    fit_metrics: dict[str, float] = field(default_factory=dict, compare=False)

    # -- fitting ---------------------------------------------------------

    def fit(
        self,
        logits: Any,
        labels: Any,
        *,
        n_classes: int | None = None,
        **fit_kw: Any,
    ) -> TemperatureScaler:
        """Fit ``T`` on a validation fold. Returns self (chainable)."""
        z = _as_probs(logits, source="logits")
        c = n_classes or self.n_classes
        self.n_classes = c if c is not None else int(z.shape[1])
        self.temperature = fit_temperature(z, labels, n_classes=self.n_classes, **fit_kw)
        y = _validate_labels(labels, z.shape[0], self.n_classes)
        pre = apply_temperature(z, 1.0, n_classes=self.n_classes)
        post = apply_temperature(z, self.temperature, n_classes=self.n_classes)
        self.fit_n = int(z.shape[0])
        self.fit_metrics = {
            "ece_before": compute_ece(pre, y, n_classes=self.n_classes),
            "ece_after": compute_ece(post, y, n_classes=self.n_classes),
            "brier_before": brier_score(pre, y, n_classes=self.n_classes),
            "brier_after": brier_score(post, y, n_classes=self.n_classes),
            "nll_before": log_loss(pre, y, n_classes=self.n_classes),
            "nll_after": log_loss(post, y, n_classes=self.n_classes),
        }
        self.fitted = True
        return self

    # -- scoring ---------------------------------------------------------

    def apply_to_logits(self, logits: Any) -> Any:
        """Calibrated probabilities for raw logits (raises if unfitted)."""
        if not self.fitted:
            raise RuntimeError("TemperatureScaler.apply_to_logits before fit()")
        return apply_temperature(logits, self.temperature, n_classes=self.n_classes)

    def apply_to_probs(self, probs: Any) -> Any:
        """Re-calibrate an existing probability matrix.

        The inverse-softmax recovery is monotone per row (log-probabilities
        are shifted by a per-row constant), so the argmax and therefore the
        accuracy are unchanged — only the confidence moves. Raises on rows
        that are not strictly positive.
        """
        if not self.fitted:
            raise RuntimeError("TemperatureScaler.apply_to_probs before fit()")
        import numpy as np

        validate_probs(probs, n_classes=self.n_classes)
        p = np.asarray(probs, dtype=np.float64)
        if (p <= 0.0).any():
            raise ValueError(
                "apply_to_probs requires strictly positive probabilities "
                "(zero-probability rows make log-softmax non-invertible)"
            )
        logits = np.log(np.clip(p, 1e-12, None))
        return apply_temperature(logits, self.temperature, n_classes=self.n_classes)

    def to_dict(self) -> dict[str, Any]:
        """Serializable artifact (mirrors repo calibration-artifact style)."""
        return {
            "temperature": self.temperature,
            "n_classes": self.n_classes,
            "fitted": self.fitted,
            "fit_n": self.fit_n,
            "fit_metrics": dict(self.fit_metrics),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemperatureScaler:
        """Reconstruct a scaler from an artifact dict (``fitted`` preserved)."""
        if not isinstance(data, dict):
            raise TypeError("from_dict requires a dict")
        t = data.get("temperature", 1.0)
        if not isinstance(t, (int, float)) or not math.isfinite(t) or t <= 0.0:
            raise ValueError(f"invalid temperature in artifact: {t!r}")
        out = cls(temperature=float(t), n_classes=data.get("n_classes"))
        out.fitted = bool(data.get("fitted", False))
        out.fit_n = int(data.get("fit_n", 0))
        metrics = data.get("fit_metrics") or {}
        out.fit_metrics = {k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))}
        return out

    # -- torch interop (research-only) ------------------------------------

    def wrap(self, model: nn.Module) -> nn.Module:
        """Return a model whose forward returns temperature-scaled softmax.

        Research/validation path only: the live 50D inference service keeps its
        own softmax (INV-012 frozen tensor contract). Wrapping is opt-in.
        """
        import torch
        from torch import nn

        if not self.fitted:
            raise RuntimeError("TemperatureScaler.wrap before fit()")
        scaler = self

        class _Scaled(nn.Module):  # pragma: no cover - exercised via wrap test
            def __init__(self, base: nn.Module) -> None:
                super().__init__()
                self.base = base

            def forward(self, *args: Any, **kw: Any) -> Any:
                logits = self.base(*args, **kw)
                return torch.softmax(logits / scaler.temperature, dim=-1)

        return _Scaled(model)


def wrap_model_temperature(model: nn.Module, temperature: float) -> nn.Module:
    """Convenience: wrap a torch model with a fixed temperature (no fitting)."""
    return TemperatureScaler(temperature=temperature, fitted=True).wrap(model)


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


@dataclass
class CalibrationReport:
    """Before/after calibration comparison for a validation fold."""

    n: int
    n_classes: int
    temperature: float
    ece_before: float
    ece_after: float
    brier_before: float
    brier_after: float
    nll_before: float
    nll_after: float
    reliability_before: list[dict[str, Any]] = field(default_factory=list)
    reliability_after: list[dict[str, Any]] = field(default_factory=list)
    improved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "n_classes": self.n_classes,
            "temperature": self.temperature,
            "ece_before": self.ece_before,
            "ece_after": self.ece_after,
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
            "nll_before": self.nll_before,
            "nll_after": self.nll_after,
            "improved": self.improved,
            "reliability_before": self.reliability_before,
            "reliability_after": self.reliability_after,
        }

    def summary_lines(self) -> list[str]:
        return [
            f"n={self.n} n_classes={self.n_classes} T={self.temperature}",
            f"ECE   {self.ece_before} -> {self.ece_after}"
            f" ({'improved' if self.ece_after < self.ece_before else 'no gain'})",
            f"Brier {self.brier_before} -> {self.brier_after}",
            f"NLL   {self.nll_before} -> {self.nll_after}",
        ]


def evaluate_temperature_scaling(
    val_logits: Any,
    labels: Any,
    *,
    n_bins: int = 10,
    n_classes: int | None = None,
) -> CalibrationReport:
    """Full before/after evaluation: fit T on the fold, report both sides.

    Task IMPLEMENTATION_PLAN steps 3-5 in one call. The fold is used both to
    fit and to evaluate, which is the optimistic in-sample bound; the caller
    should additionally fit-on-train / evaluate-on-val via ``TemperatureScaler``
    for the honest OOS number (exercised in the test battery).
    """
    z = _as_probs(val_logits, source="val_logits")
    c = n_classes or int(z.shape[1])
    y = _validate_labels(labels, z.shape[0], c)
    scaler = TemperatureScaler(n_classes=c).fit(z, y)
    pre = apply_temperature(z, 1.0, n_classes=c)
    post = apply_temperature(z, scaler.temperature, n_classes=c)
    return CalibrationReport(
        n=int(z.shape[0]),
        n_classes=c,
        temperature=scaler.temperature,
        ece_before=compute_ece(pre, y, n_bins=n_bins, n_classes=c),
        ece_after=compute_ece(post, y, n_bins=n_bins, n_classes=c),
        brier_before=brier_score(pre, y, n_classes=c),
        brier_after=brier_score(post, y, n_classes=c),
        nll_before=log_loss(pre, y, n_classes=c),
        nll_after=log_loss(post, y, n_classes=c),
        reliability_before=reliability_diagram(pre, y, n_bins=n_bins, n_classes=c),
        reliability_after=reliability_diagram(post, y, n_bins=n_bins, n_classes=c),
        improved=bool(
            compute_ece(post, y, n_bins=n_bins, n_classes=c)
            < compute_ece(pre, y, n_bins=n_bins, n_classes=c)
        ),
    )
