"""Trading Decision Trace — event contract (trace_schema_version 1).

A strongly typed, JSON-serializable event contract for the async decision
trace observer. This module is PURE: no I/O, no engine imports, stdlib only,
so importing it can never fail from a hot-path's perspective (BUG-311 class:
every trace call site imports it behind a guarded fallback).

Truth rules encoded here (contract, not policy):
- Every field is optional-by-default: absence renders as UNKNOWN / NOT
  OBSERVED downstream — the contract never fabricates a default that could
  be mistaken for runtime evidence (no "", 0.0 or False stand-ins for
  unknown facts; callers simply omit the key).
- ``detail`` is a bounded, sanitized, redacted mapping. Arbitrary objects
  never reach the browser (payload-size + secret safety).
- ``terminal=True`` marks an observed end of a decision path. Downstream
  stages that produced NO event are provenance gaps, not passes.

Schema evolution: bump TRACE_SCHEMA_VERSION on any breaking field change;
consumers must reject unknown major versions instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

TRACE_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Taxonomy. These constants name the stages the runtime ACTUALLY emits today
# (verified at the instrumentation call sites). They are NOT a closed enum:
# any future backend stage flows through verbatim and is rendered by the
# frontend as a discovered / UNMAPPED node. Never add a stage name here that
# no runtime event emits.
# ---------------------------------------------------------------------------
class TraceStage:
    """Namespace of currently-emitted stage names (free strings elsewhere)."""

    MARKET = "MARKET"
    FEATURES = "FEATURES"
    REGIME = "REGIME"
    INFERENCE = "INFERENCE"
    POLICY = "POLICY"
    POST_POLICY = "POST_POLICY"
    DECISION = "DECISION"
    RISK = "RISK"
    EXECUTION = "EXECUTION"
    MT5 = "MT5"
    ORDER = "ORDER"
    # Paper mode never contacts an MT5 terminal — its gateway events use
    # GATEWAY (detail.gateway="paper") so a paper fill can never be read
    # as broker-reached evidence (§24 truth rule).
    GATEWAY = "GATEWAY"


# Observed terminal states of a decision path (frontend terminal semantics).
# NO_TRADE is terminal-without-execution (model or gate origin is carried in
# status_reason, not conflated with REJECTED).
TERMINAL_STATUSES = frozenset(
    {"REJECTED", "NO_TRADE", "EXECUTED", "FAILED", "TIMEOUT", "ERROR", "CANCELLED"}
)

# Gate/rule verdicts the runtime actually produces (plus UNKNOWN, which is
# the ONLY honest value when evidence is missing).
VERDICT_STATUSES = frozenset({"PASS", "REJECT", "SKIP", "ERROR", "TIMEOUT", "UNKNOWN"})

# Metadata keys redacted before any payload can leave the process (§54).
_SENSITIVE_KEY_TOKENS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private",
    "bearer",
    "cookie",
    "login",
)

_REDACTED = "***REDACTED***"

_MAX_DETAIL_KEYS = 40
_MAX_STR = 400
_MAX_DEPTH = 3
_MAX_LIST = 64


def _is_sensitive(key: str) -> bool:
    kl = str(key).lower()
    return any(tok in kl for tok in _SENSITIVE_KEY_TOKENS)


def sanitize_detail(value: Any, _depth: int = 0) -> Any:
    """Bound + JSON-safe + secret-redacted normalization for event metadata.

    Deterministic, allocation-bounded. Never raises: a value it cannot
    understand becomes its repr string (capped), never an exception.
    """
    try:
        if _depth > _MAX_DEPTH:
            return "<max-depth>"
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, str):
            return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…<truncated>"
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            n = 0
            for k, v in value.items():
                if n >= _MAX_DETAIL_KEYS:
                    out["…"] = "<truncated>"
                    break
                key = str(k)
                out[key] = _REDACTED if _is_sensitive(key) else sanitize_detail(v, _depth + 1)
                n += 1
            return out
        if isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)[:_MAX_LIST]
            out_list = [sanitize_detail(v, _depth + 1) for v in items]
            if len(value) > _MAX_LIST:
                out_list.append(f"<{len(value) - _MAX_LIST} more omitted>")
            return out_list
        if isinstance(value, datetime):
            return value.isoformat()
        # Enums, numpy scalars, torch dtypes, pydantic models …
        v = getattr(value, "value", None)
        if isinstance(v, (str, int, float, bool)):
            return sanitize_detail(v, _depth + 1)
        if hasattr(value, "item"):  # numpy scalar
            try:
                return sanitize_detail(value.item(), _depth + 1)
            except Exception:
                pass
        return sanitize_detail(str(value), _depth + 1)
    except Exception:
        return "<unserializable>"


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(slots=True)
class TraceEvent:
    """One observed runtime fact in a decision's causal chain.

    identity:  event_id (unique), trace_id (one market decision pipeline
               execution), parent_event_id (causality), sequence (observer
               monotonic ordering), decision_id (canonical EXEC-… id = the
               audit_signals.request_id join key).
    time:      timestamp = wall clock for display; monotonic_ns = duration
               math (never wall clock).
    truth:     stage/component/event_type/status are verbatim runtime
               facts; detail carries the raw evidence; terminal=True marks
               an observed end of path.
    """

    event_id: str
    trace_id: str
    sequence: int
    timestamp: str
    monotonic_ns: int
    stage: str
    component: str
    event_type: str
    status: str
    parent_event_id: str | None = None
    symbol: str | None = None
    decision_id: str | None = None
    latency_us: int | None = None
    terminal: bool = False
    unmapped: bool = False  # rootless non-MARKET event (linkage gap)
    provenance_gap: bool = False  # caller observed missing upstream evidence
    detail: dict[str, Any] = field(default_factory=dict)
    trace_schema_version: int = TRACE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Flat JSON dict; None-valued optional fields are OMITTED (never
        rendered downstream as an invented default)."""
        d: dict[str, Any] = {
            "event_id": self.event_id,
            "trace_id": self.trace_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "monotonic_ns": self.monotonic_ns,
            "stage": self.stage,
            "component": self.component,
            "event_type": self.event_type,
            "status": self.status,
            "terminal": self.terminal,
            "trace_schema_version": self.trace_schema_version,
        }
        if self.parent_event_id is not None:
            d["parent_event_id"] = self.parent_event_id
        if self.symbol is not None:
            d["symbol"] = self.symbol
        if self.decision_id is not None:
            d["decision_id"] = self.decision_id
        if self.latency_us is not None:
            d["latency_us"] = self.latency_us
        if self.unmapped:
            d["unmapped"] = True
        if self.provenance_gap:
            d["provenance_gap"] = True
        if self.detail:
            d["detail"] = self.detail
        return d


def decision_summary_dict(summary: dict[str, Any]) -> dict[str, Any]:
    """Normalize an always-on decision summary for transport.

    Summaries are the low-overhead record emitted even when detailed tracing
    is OFF, so opening the UI can hydrate recent decisions. Keys omitted by
    the caller stay omitted (UNKNOWN downstream)."""
    out: dict[str, Any] = {
        "kind": "decision_summary",
        "trace_schema_version": TRACE_SCHEMA_VERSION,
    }
    for k, v in summary.items():
        if v is None:
            continue
        if _is_sensitive(k):
            out[k] = _REDACTED
        else:
            out[k] = sanitize_detail(v)
    return out


def proposal_summary(proposal: Any, *, status: str, trace_id: str | None = None) -> dict[str, Any]:
    """Build an always-on decision-summary from a TradeProposal.

    Duck-typed over the canonical proposal fields only (no domain import —
    the contract stays dependency-free). Every field is read from the
    proposal as the runtime produced it; None stays None so the key is
    omitted downstream instead of becoming an invented default.

    ``status`` is caller-asserted truth: REJECTED (a gate/executor boundary
    blocked it), NO_TRADE (the model/policy chose no trade with no blocking
    evidence), APPROVED (survived all observed gates; downstream stages
    still pending).
    """

    def _gen_key() -> str | None:
        exec_id = getattr(proposal, "execution_id", None)
        req_id = getattr(proposal, "request_id", None)
        return str(exec_id or req_id or "") or None

    generated = getattr(proposal, "generated_at", None)
    exec_id = getattr(proposal, "execution_id", None)
    out: dict[str, Any] = {
        "status": status,
        "trace_id": trace_id or None,
        "request_id": getattr(proposal, "request_id", None) or None,
        "decision_id": _gen_key(),
        "execution_id": exec_id or None,
        "symbol": getattr(proposal, "symbol", None) or None,
        "timestamp": generated.isoformat() if hasattr(generated, "isoformat") else None,
        "action": getattr(proposal, "action", None),
        "decision_stage": getattr(proposal, "decision_stage", None) or None,
        "blocked_by": getattr(proposal, "blocked_by", None) or None,
        "reason_code": getattr(proposal, "reason_code", None) or None,
        "rejection_reason": getattr(proposal, "rejection_reason", None) or None,
        "model_action": getattr(proposal, "model_action", None) or None,
        "confidence": getattr(proposal, "confidence", None),
        "regime": getattr(proposal, "regime", None) or None,
        "regime_confidence": getattr(proposal, "regime_confidence", None),
        "guardian_status": getattr(proposal, "guardian_status", None) or None,
        "execution_path": getattr(proposal, "execution_mode", None) or None,
        "final_action": getattr(proposal, "final_action", None) or None,
        "risk_checks": getattr(proposal, "risk_checks", None),
        "ticket": getattr(proposal, "ticket", None) or None,
    }
    if trace_id:
        out["trace_id"] = trace_id
    return out
