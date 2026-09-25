"""Value lineage & source-of-truth tracing (TASK-12 spec 7/8/38/39/40/41/42/43).

For important values (Balance, Equity, PnL, Open positions, Model ID,
Feature vector, Liquidity state, News state, Strategy ID, Realized R):

    SOURCE OF TRUTH -> TRANSFORMATIONS -> CACHES -> PERSISTENCE -> API -> UI

If a UI value is wrong, the tracer walks backward until the first incorrect
value is found (first-failure identification, spec 6). The trace is purely
diagnostic — nothing here can change behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from nexus_scalp.incidents.models import LineageStep, ValueTrace

#: Registries of canonical producers per domain (spec 7).
PRODUCERS: dict[str, str] = {
    "balance": "MT5 broker account info (get_account_info / account snapshot)",
    "equity": "MT5 broker account info (get_account_info / account snapshot)",
    "pnl": "MT5 deal history (broker truth) -> deal snapshot",
    "realized_r": "ledger net_pnl_usd / approved_volume (risk unit) -> outcome recovery",
    "open_positions": "MT5 broker positions (authoritative; INV-011)",
    "model_id": "model registry / champion artifact manifest",
    "feature_vector": "features/scalp_features.py from completed bars + decision tick (INV-008)",
    "liquidity_state": "features/liquidity_engine.py (candidate-only)",
    "news_state": "news engine (ingest -> analysis -> context)",
    "strategy_id": "strategies/ registry (content-addressed candidates)",
    "request_id": "execution context propagation (order manager -> experience)",
    "mt5_timebase": "MT5 server time (broker-local epoch; BUG-070)",
}

TRANSFORMATIONS: dict[str, tuple[str, ...]] = {
    "pnl": (
        "broker adapter normalization",
        "deal snapshot",
        "reconciliation",
        "accounting core",
        "API payload",
        "UI render",
    ),
    "realized_r": (
        "outcome recovery",
        "risk-unit conversion (R multiple)",
        "reconstruction_source check",
        "accounting core",
        "API payload",
        "UI render",
    ),
    "open_positions": (
        "broker snapshot",
        "exposure cache",
        "policy check",
        "UI render",
    ),
    "feature_vector": (
        "bar aggregator (reseed REPLACE+ALIGN)",
        "feature calculation",
        "normalization/clip",
        "vector assembly",
        "model inference",
    ),
}


class LineageEngine:
    """Builds value traces and walks them to find the first divergence."""

    def __init__(
        self,
        *,
        query_hooks: dict[str, Callable[[], Any]] | None = None,
    ) -> None:
        """``query_hooks``: optional per-step probes returning observed values
        (used by the forensic baseline to fetch DB/log values read-only).
        """
        self.query_hooks = query_hooks or {}

    # ------------------------------------------------------------------
    # Trace construction
    # ------------------------------------------------------------------

    def trace(self, field: str, source_timestamp: datetime | None = None) -> ValueTrace:
        """Canonical lineage for a value field (spec 8)."""
        source = PRODUCERS.get(field, "unknown producer")
        steps = [
            LineageStep("TRANSFORMATION", name, timestamp=source_timestamp)
            for name in TRANSFORMATIONS.get(field, ())
        ]
        return ValueTrace(
            field=field,
            source=source,
            source_timestamp=source_timestamp,
            transformations=tuple(steps),
        )

    def pnl_trace(self, source_timestamp: datetime | None = None) -> ValueTrace:
        return self.trace("pnl", source_timestamp)

    def realized_r_trace(self, source_timestamp: datetime | None = None) -> ValueTrace:
        return self.trace("realized_r", source_timestamp)

    def exposure_trace(self, source_timestamp: datetime | None = None) -> ValueTrace:
        """Exposure value lineage — the historical MAX_EXPOSURE false-block path."""
        source = PRODUCERS["open_positions"]
        steps = (
            LineageStep(
                "SOURCE_OF_TRUTH",
                "MT5 broker positions (authoritative, INV-011)",
                timestamp=source_timestamp,
            ),
            LineageStep(
                "TRANSFORMATION", "broker snapshot normalization", timestamp=source_timestamp
            ),
            LineageStep("CACHE", "in-memory exposure cache (session), refresh on broker sync"),
            LineageStep("TRANSFORMATION", "MAX_EXPOSURE policy check"),
            LineageStep("API", "/api/status exposure section"),
            LineageStep("UI", "dashboard exposure widget"),
        )
        return ValueTrace(
            field="open_positions",
            source=source,
            source_timestamp=source_timestamp,
            transformations=steps,
        )

    def model_output_trace(self, source_timestamp: datetime | None = None) -> ValueTrace:
        """Model output lineage (spec 18: earliest incorrect layer)."""
        steps = (
            LineageStep("SOURCE_OF_TRUTH", "feature vector (validated, finite, clipped)"),
            LineageStep("TRANSFORMATION", "model inference (Champion artifact, deterministic)"),
            LineageStep("TRANSFORMATION", "probability -> action classification"),
            LineageStep("TRANSFORMATION", "signal policy + rule matrix"),
            LineageStep("API", "/api/live/state ai_decision"),
            LineageStep("UI", "dashboard AI decision widget"),
        )
        return ValueTrace(
            field="model_output",
            source="Champion model artifact + features",
            source_timestamp=source_timestamp,
            transformations=steps,
        )

    def ui_value_trace(self, field: str, source_timestamp: datetime | None = None) -> ValueTrace:
        """UI value lineage — WHY UI EMPTY diagnosis (spec 21/43)."""
        canonical = self.trace(field, source_timestamp)
        steps = [
            *list(canonical.transformations),
            LineageStep("API", f"/api endpoints serving {field}"),
            LineageStep("TRANSFORMATION", "JS loader fetch"),
            LineageStep("TRANSFORMATION", "renderer"),
            LineageStep("UI", f"dashboard {field} widget"),
        ]
        return ValueTrace(
            field=field,
            source=canonical.source,
            source_timestamp=source_timestamp,
            transformations=tuple(steps),
        )

    # ------------------------------------------------------------------
    # First-divergence walk (spec 6/7)
    # ------------------------------------------------------------------

    def find_first_divergence(
        self,
        trace: ValueTrace,
        *,
        symptom: str,
        known_bad_steps: list[str] | None = None,
    ) -> dict[str, Any]:
        """Walks a value's lineage backward from the symptom to the earliest
        step that diverged from truth.

        ``known_bad_steps``: step names already proven incorrect (logs/DB
        evidence); when provided, the FIRST of them in hop order is the
        divergence point. Without it, the tracer inspects query hooks.

        Returns a diagnostic dict — NEVER a mutation.
        """
        hops = trace.hops()
        divergence: dict[str, Any] | None = None
        inspected: list[dict[str, Any]] = []
        bad_set = {str(b).strip().lower() for b in (known_bad_steps or [])}
        for hop in hops:
            probe = self.query_hooks.get(hop["name"].strip().lower())
            observed: Any = None
            if probe is not None:
                try:
                    observed = probe()
                except Exception as exc:  # pragma: no cover - defensive
                    observed = f"PROBE_ERROR: {exc}"
            match = hop["name"].strip().lower() in bad_set or (
                observed is not None and _value_suspect(observed)
            )
            inspected.append(
                {
                    "stage": hop["stage"],
                    "name": hop["name"],
                    "observed": observed,
                    "suspect": match,
                }
            )
            if match and divergence is None:
                divergence = {
                    "stage": hop["stage"],
                    "name": hop["name"],
                    "observed": observed,
                    "symptom": symptom,
                    "reason": "first hop where the value diverged from truth",
                }
        return {
            "field": trace.field,
            "symptom": symptom,
            "inspected_hops": inspected,
            "first_divergence": divergence,
            "divergence_found": divergence is not None,
        }


def _value_suspect(value: Any) -> bool:
    """Heuristic: is an observed value suspicious? (None/empty/zero-where-
    non-zero-expected/NaN.) Evidence-driven; the caller supplies the value."""
    if value is None:
        return True
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("", "none", "null", "nan", "n/a", "waiting", "empty", "0", "0.0"):
            return True
        return False
    if isinstance(value, (int, float)):
        try:
            import math

            if not math.isfinite(float(value)):
                return True
        except (TypeError, ValueError):
            return True
        return abs(float(value)) < 1e-12
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def build_simple_trace(field: str, hops: list[str]) -> ValueTrace:
    """Utility: build a ValueTrace from a plain list of hop names (spec 8)."""
    steps = tuple(LineageStep("TRANSFORMATION", h) for h in hops[1:])
    return ValueTrace(
        field=field,
        source=hops[0] if hops else "unknown",
        transformations=steps,
        source_timestamp=None,
        cache_layers=(),
        persistence=(),
        consumers=(),
    )


__all__ = [
    "PRODUCERS",
    "TRANSFORMATIONS",
    "LineageEngine",
    "build_simple_trace",
]
