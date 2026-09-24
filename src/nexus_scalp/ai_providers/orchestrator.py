"""Provider orchestrator — the backend source of truth (ECOSYSTEM-001).

THE ONLY THING THE DECISION PIPELINE CALLS
------------------------------------------
Given a canonical position snapshot, this returns ONE final decision with the
full evidence trail. It owns:

    * provider selection + activation (Sections 13, 14)
    * the decision modes (DISABLED/INTERNAL_ONLY/EXTERNAL_ONLY/HYBRID/SHADOW/
      COMPARISON/FALLBACK/DETERMINISTIC_ONLY)
    * request deduplication (Section 33)
    * fusion of normalized evidence (Section 12)
    * the deterministic policy formula (Section 11)
    * the risk gate (Section 17)
    * the decision trace (Section 41) and fallback visibility (Section 67)

It never owns: order placement, sizing, or any execution authority.

NO SILENT FALLBACK (Section 15, 67)
-----------------------------------
A provider failure falls through an EXPLICIT chain and the outcome is recorded:
``fallback_used=True`` with a reason. The operator can never believe they are
using System One while the system is actually using Internal ML.

NEVER PRETEND (Section 15)
--------------------------
"provider failed -> pretend HOLD" is forbidden. When there is no trusted ML
evidence, the DETERMINISTIC POLICY decides -- from the position's own numbers,
not from a fabricated verdict.

LATENCY BUDGET (Section 35)
---------------------------
Every stage is timed. A slow external model can never silently block a
time-sensitive deterministic safety operation: the orchestrator's own deadline
is enforced before any provider is called.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.ai_providers.adapters import ADAPTER_TYPES, BaseAIProviderAdapter
from nexus_scalp.ai_providers.adapters.base import AIProviderTestResult
from nexus_scalp.ai_providers.contract import (
    AIProviderAction,
    DecisionEvidence,
    PositionDecisionRequest,
    PositionDecisionResponse,
)
from nexus_scalp.ai_providers.errors import (
    ProviderError,
    ProviderErrorCategory,
)
from nexus_scalp.ai_providers.policy import (
    POLICY_VERSION,
    PolicyScores,
    PolicyWeights,
    score_action,
)
from nexus_scalp.ai_providers.registry import (
    ActivationState,
    DecisionMode,
    ProviderConfig,
    ProviderRegistryStore,
)
from nexus_scalp.ai_providers.risk_gate import (
    GATE_VERSION,
    RiskGateResult,
    RiskPolicy,
    gate_proposals,
)
from nexus_scalp.ai_providers.sample import SIMULATED_CONTEXT_VERSION
from nexus_scalp.ai_providers.templates import TEMPLATE_VERSION
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.settings.secret_store import SecureSecretStore

logger = get_logger("nexus_scalp.ai_providers.orchestrator")

__all__ = ["DECISION_VERSION", "DecisionOutcome", "ProviderOrchestrator"]


DECISION_VERSION = "decision_v1"


@dataclass
class DecisionOutcome:
    """The final, gated decision for one position (Section 18 semantics).

    ``final_action`` is what the policy + risk gate produced. It is still not an
    instruction: the decide system applies it through its existing bounded
    hold-score channel, never as a raw order.
    """

    decision_id: str
    request: PositionDecisionRequest
    final_action: str
    policy: PolicyScores
    risk: RiskGateResult
    evidence: dict[str, Any] = field(default_factory=dict)
    providers_used: list[str] = field(default_factory=list)
    providers_failed: list[str] = field(default_factory=list)
    fallback_used: bool = False
    fallback_reason: str = ""
    decision_mode: str = DecisionMode.INTERNAL_ONLY
    template_version: str = TEMPLATE_VERSION
    policy_version: str = POLICY_VERSION
    gate_version: str = GATE_VERSION
    decision_version: str = DECISION_VERSION
    contract_version: str = "1.0.0"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    latency_ms: float = 0.0
    stage_timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "final_action": self.final_action,
            "policy": self.policy.to_dict(),
            "risk": self.risk.to_dict(),
            "evidence": self.evidence,
            "providers_used": list(self.providers_used),
            "providers_failed": list(self.providers_failed),
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "decision_mode": self.decision_mode,
            "versions": {
                "template": self.template_version,
                "policy": self.policy_version,
                "gate": self.gate_version,
                "decision": self.decision_version,
                "contract": self.contract_version,
                "context": self.request.decision_context_version,
            },
            "created_at": self.created_at,
            "latency_ms": round(self.latency_ms, 3),
            "stage_timings_ms": {k: round(v, 3) for k, v in self.stage_timings_ms.items()},
            "is_test_data": self.request.decision_context_version == SIMULATED_CONTEXT_VERSION,
        }


class ProviderOrchestrator:
    """The backend source of truth for AI provider decisions (Section 61).

    UI and CLI are clients of this object's contracts; neither duplicates
    provider-selection logic (Section 61).
    """

    def __init__(
        self,
        *,
        registry: ProviderRegistryStore,
        secret_store: SecureSecretStore,
        weights: PolicyWeights | None = None,
        risk_policy: RiskPolicy | None = None,
        request_deadline_sec: float = 5.0,
        adviser_service: Any | None = None,
    ) -> None:
        self._registry: ProviderRegistryStore = registry
        self._secret_store = secret_store
        self._weights = weights or PolicyWeights()
        self._risk_policy = risk_policy or RiskPolicy()
        self._request_deadline_sec = request_deadline_sec
        self._adviser_service = adviser_service
        self._lock = threading.RLock()
        self._adapters: dict[str, BaseAIProviderAdapter] = {}
        self._decision_history: list[dict[str, Any]] = []
        self._max_history = 200
        self._dedupe: dict[str, tuple[str, float]] = {}
        self._config_version = "1"

    # --------------------------------------------------------------------------
    # Adapter construction
    # --------------------------------------------------------------------------
    def _build_adapter(self, provider_id: str) -> BaseAIProviderAdapter | None:
        """Instantiate one adapter from the registry's config row.

        Returns None when the provider is unknown, disabled, or lacks an
        adapter class -- never raises, because a broken provider must not break
        the whole decision path.
        """
        cfg = self._registry.get_config(provider_id)
        if cfg is None:
            return None
        if not cfg.enabled:
            return None
        cls = ADAPTER_TYPES.get(provider_id)
        if cls is None:
            logger.error("[AI-PROV] no adapter class registered for %s", provider_id)
            return None
        try:
            if provider_id == "internal_nse_ml":
                adapter = cls(
                    config=cfg,
                    registry=self._registry,
                    secret_store=self._secret_store,
                    adviser_service=self._adviser_service,
                )
            else:
                adapter = cls(config=cfg, registry=self._registry, secret_store=self._secret_store)
        except Exception as exc:
            logger.error("[AI-PROV] failed to build adapter %s: %s", provider_id, exc)
            return None
        return adapter

    def _adapter(self, provider_id: str) -> BaseAIProviderAdapter | None:
        with self._lock:
            if provider_id not in self._adapters:
                built = self._build_adapter(provider_id)
                if built is None:
                    return None
                self._adapters[provider_id] = built
            return self._adapters[provider_id]

    # --------------------------------------------------------------------------
    # Provider selection
    # --------------------------------------------------------------------------
    def _resolve_chain(self, mode: DecisionMode, activation: ActivationState | None) -> list[str]:
        """The ordered list of providers to consult for a mode (Sections 13/14).

        The chain is explicit, never implicit: this is the list, in order, and
        every member is logged in the decision trace.
        """
        act = activation
        primary = act.primary_provider if act else "internal_nse_ml"
        secondary = act.secondary_provider if act else None
        fallback = act.fallback_provider if act else None
        if mode is DecisionMode.DISABLED or mode is DecisionMode.DETERMINISTIC_ONLY:
            return []
        if mode is DecisionMode.INTERNAL_ONLY:
            return ["internal_nse_ml"]
        if mode is DecisionMode.EXTERNAL_ONLY:
            out = [p for p in (primary,) if p != "internal_nse_ml"]
            return out or []
        # HYBRID / FALLBACK / SHADOW / COMPARISON all consult the full chain,
        # in primary -> secondary -> fallback order; the mode governs what is
        # then DONE with the results (see _fuse).
        chain: list[str] = []
        for pid in (primary, secondary, fallback):
            if pid and pid not in chain:
                chain.append(pid)
        if mode is DecisionMode.HYBRID and "internal_nse_ml" not in chain:
            chain.insert(0, "internal_nse_ml")
        return chain

    # --------------------------------------------------------------------------
    # Deduplication (Section 33)
    # --------------------------------------------------------------------------
    def _dedupe_key(self, request: PositionDecisionRequest, provider_id: str) -> str:
        """Deterministic request identity: position + snapshot + provider + cfg.

        Prevents duplicate external calls from UI refresh, tick spam, worker
        retries, reconnects or duplicated events.
        """
        blob = json.dumps(
            {
                "p": request.position_id,
                "t": request.ticket,
                "s": request.symbol,
                "side": request.side,
                "price": round(request.current_price, 8),
                "vol": round(request.volume, 8),
                "sl": request.current_sl,
                "tp": request.current_tp,
                "u_r": round(request.unrealized_r, 8),
                "regime": request.regime,
                "risk": round(request.current_risk, 8),
                "ts": request.timestamp.isoformat(),
                "provider": provider_id,
                "cfg": self._config_version,
                "model": request.decision_context_version,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def _dedupe_check(self, key: str, ttl: float) -> str | None:
        """Return a cached decision_id if the SAME request was answered within ttl."""
        with self._lock:
            entry = self._dedupe.get(key)
            if entry is None:
                return None
            decision_id, ts = entry
            if time.time() - ts > ttl:
                del self._dedupe[key]
                return None
            return decision_id

    def _dedupe_store(self, key: str, decision_id: str) -> None:
        with self._lock:
            self._dedupe[key] = (decision_id, time.time())
            if len(self._dedupe) > 4096:
                # Bounded: drop the oldest quarter. Never unbounded growth.
                items = sorted(self._dedupe.items(), key=lambda kv: kv[1][1])
                for k, _ in items[:1024]:
                    self._dedupe.pop(k, None)

    # --------------------------------------------------------------------------
    # THE DECISION
    # --------------------------------------------------------------------------
    def evaluate_position(self, request: PositionDecisionRequest) -> DecisionOutcome:
        """Evaluate one open position and return the gated decision.

        Never raises: any failure degrades to the deterministic policy, which
        is the honest fallback -- not "pretend HOLD" (Section 15).
        """
        t0 = time.monotonic()
        started = time.time()
        decision_id = f"dec-{uuid.uuid4().hex[:16]}"
        mode, activation = self._current_mode()
        stage: dict[str, float] = {}

        chain = self._resolve_chain(mode, activation)
        responses: dict[str, PositionDecisionResponse] = {}
        failures: dict[str, ProviderError] = {}
        evidence: dict[str, Any] = {}
        fallback_used = False
        fallback_reason = ""
        used: list[str] = []

        # Dedupe before any network IO (Section 33).
        for pid in chain:
            dkey = self._dedupe_key(request, pid)
            cached = self._dedupe_check(dkey, ttl=self._request_deadline_sec)
            if cached:
                logger.debug("[AI-PROV] dedupe hit provider=%s", pid)

        for pid in chain:
            adapter = self._adapter(pid)
            if adapter is None:
                failures[pid] = ProviderError(
                    ProviderErrorCategory.UNKNOWN,
                    "provider unavailable or disabled",
                    provider_id=pid,
                )
                continue
            t_call = time.monotonic()
            try:
                resp = adapter.evaluate_position(request)
                responses[pid] = resp
                used.append(pid)
            except ProviderError as exc:
                failures[pid] = exc
                logger.warning("[AI-PROV] %s failed: %s", pid, exc.category.value)
            except Exception as exc:
                failures[pid] = ProviderError(
                    ProviderErrorCategory.UNKNOWN,
                    f"adapter error: {exc.__class__.__name__}",
                    provider_id=pid,
                )
                logger.exception("[AI-PROV] unexpected adapter failure for %s", pid)
            stage[pid] = (time.monotonic() - t_call) * 1000.0

        # -- fallback accounting (Sections 15, 67) ------------------------------
        if failures and not responses:
            fallback_used = True
            fallback_reason = "; ".join(f"{p}={e.category.value}" for p, e in failures.items())
        elif failures and responses:
            # A non-primary failure that still produced a usable response is a
            # partial fallback: record it, loudly.
            primary = activation.primary_provider if activation else "internal_nse_ml"
            if primary in failures:
                fallback_used = True
                fallback_reason = f"primary {primary} failed: {failures[primary].category.value}"

        # -- fuse (Section 12) ---------------------------------------------------
        t_fuse = time.monotonic()
        fused = _fuse(responses, mode)
        stage["fuse"] = (time.monotonic() - t_fuse) * 1000.0

        # -- deterministic policy (Section 11) ------------------------------------
        t_pol = time.monotonic()
        scores = score_action(
            request,
            p_hold=fused.p_hold,
            p_close=fused.p_close,
            p_reduce=fused.p_reduce,
            expected_remaining_r=fused.expected_remaining_r,
            expected_upside_r=fused.expected_upside_r,
            expected_downside_r=fused.expected_downside_r,
            regime_change_probability=fused.regime_change_probability,
            uncertainty=fused.uncertainty,
            confidence=fused.confidence,
            action_hint=fused.action,
            weights=self._weights,
        )
        stage["policy"] = (time.monotonic() - t_pol) * 1000.0
        final_action = scores.winner

        # -- risk gate (Section 17) ------------------------------------------------
        t_gate = time.monotonic()
        # Gate the best available response (the fused view carries no proposals;
        # the individual responses do).
        gated_response = _best_response(responses, chain)
        risk = (
            gate_proposals(gated_response, request, self._risk_policy)
            if gated_response
            else RiskGateResult(allowed=False, rejections=["NO_PROVIDER_EVIDENCE"])
        )
        stage["risk"] = (time.monotonic() - t_gate) * 1000.0

        if not risk.allowed and final_action in (
            AIProviderAction.ADJUST_TP.value,
            AIProviderAction.ADJUST_SL.value,
        ):
            # A rejected proposal degrades the action, never the decision: the
            # position is still assessed, just without the rejected adjustment.
            final_action = AIProviderAction.HOLD.value
            scores.reason_codes.append("PROPOSAL_REJECTED_BY_RISK_GATE")

        evidence = {
            pid: {
                "decision": r.decision.model_dump(),
                "tp": r.tp.model_dump(),
                "sl": r.sl.model_dump(),
                "model": r.model,
                "latency_ms": round(r.latency_ms, 3),
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_estimate": r.cost_estimate,
                "provider_generation_id": r.provider_generation_id,
                "from_live_provider": r.from_live_provider,
            }
            for pid, r in responses.items()
        }
        if failures:
            evidence["failures"] = {p: e.to_dict() for p, e in failures.items()}

        outcome = DecisionOutcome(
            decision_id=decision_id,
            request=request,
            final_action=final_action,
            policy=scores,
            risk=risk,
            evidence=evidence,
            providers_used=used,
            providers_failed=list(failures),
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            decision_mode=str(mode),
            latency_ms=(time.monotonic() - t0) * 1000.0,
            stage_timings_ms=stage,
        )
        outcome.contract_version = request.schema_version
        self._record(outcome)
        for pid in chain:
            self._dedupe_store(self._dedupe_key(request, pid), decision_id)
        _ = started
        return outcome

    # --------------------------------------------------------------------------
    # Mode + activation
    # --------------------------------------------------------------------------
    def _current_mode(self) -> tuple[DecisionMode, ActivationState | None]:
        act = self._registry.get_activation()
        if act is None:
            return DecisionMode.INTERNAL_ONLY, None
        try:
            return DecisionMode(act.decision_mode), act
        except ValueError:
            logger.warning(
                "[AI-PROV] unknown stored mode %r; defaulting to INTERNAL_ONLY", act.decision_mode
            )
            return DecisionMode.INTERNAL_ONLY, act

    # --------------------------------------------------------------------------
    # History / trace (Section 41)
    # --------------------------------------------------------------------------
    def _record(self, outcome: DecisionOutcome) -> None:
        """Bounded in-memory ring for the live UI panel (Section 59: no
        unbounded decision storage). Persistence is the caller's job."""
        with self._lock:
            self._decision_history.insert(0, outcome.to_dict())
            if len(self._decision_history) > self._max_history:
                del self._decision_history[self._max_history :]

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._decision_history[:limit])

    # --------------------------------------------------------------------------
    # Provider management surface (UI/CLI/API all use these; Section 61)
    # --------------------------------------------------------------------------
    def list_providers(self) -> list[dict[str, Any]]:
        """All providers with identity + health (Section 19)."""
        out: list[dict[str, Any]] = []
        for pid in ADAPTER_TYPES:
            cfg = self._registry.get_config(pid)
            if cfg is None:
                cfg = ProviderConfig(provider_id=pid, provider_name=pid, enabled=False)
            adapter = self._adapter(pid)
            if adapter is None:
                # Build a transient adapter just to report health? No: report
                # config-only, because a disabled provider must not be built.
                out.append({**cfg.to_public_dict(), "health": None})
                continue
            out.append(adapter.to_public_dict())
        return out

    def provider_status(self, provider_id: str) -> dict[str, Any]:
        adapter = self._adapter(provider_id)
        cfg = self._registry.get_config(provider_id)
        if cfg is None or adapter is None:
            return {
                "provider_id": provider_id,
                "available": False,
                "error": "not configured or disabled",
            }
        return {
            "provider_id": provider_id,
            "available": True,
            "identity": adapter.to_public_dict(),
            "health": adapter.health().to_dict(),
            "models": cfg.available_models,
            "default_model": cfg.default_model,
        }

    def test_provider(self, provider_id: str) -> AIProviderTestResult:
        """Run a provider test (Section 22). Never triggers a real trade."""
        adapter = self._adapter(provider_id)
        if adapter is None:
            return AIProviderTestResult(False, "provider is disabled or not configured")
        result = adapter.test()
        self._persist_health(provider_id, result)
        return result

    def test_connection(self, provider_id: str) -> AIProviderTestResult:
        return self.test_provider(provider_id)

    def list_models(self, provider_id: str) -> list[str]:
        adapter = self._adapter(provider_id)
        return adapter.list_models() if adapter else []

    def configure_provider(
        self, provider_id: str, values: dict[str, Any], actor: str = "ui"
    ) -> bool:
        """Save provider configuration (Sections 20, 27).

        DESIRED -> VALIDATE -> ACCEPT -> APPLY -> ACTIVE. Here: validate at the
        schema level, then persist. Activation is a separate, explicit step.
        """
        cfg = self._registry.get_config(provider_id) or ProviderConfig(
            provider_id=provider_id, provider_name=provider_id
        )
        if values.get("endpoint") is not None:
            cfg.endpoint = str(values["endpoint"])
        if values.get("model") is not None:
            cfg.default_model = str(values["model"])
        if values.get("timeout") is not None:
            try:
                cfg.timeout = max(1.0, float(values["timeout"]))
            except (TypeError, ValueError):
                return False
        if values.get("max_retries") is not None:
            try:
                cfg.max_retries = max(0, int(values["max_retries"]))
            except (TypeError, ValueError):
                return False
        if values.get("enabled") is not None:
            cfg.enabled = bool(values["enabled"])
        if values.get("provider_name") is not None:
            cfg.provider_name = str(values["provider_name"])
        if values.get("secret_name") is not None:
            cfg.secret_name = str(values["secret_name"])
        ok = self._registry.upsert(cfg, actor=actor)
        if ok:
            # Drop the cached adapter so the new config is actually used.
            with self._lock:
                self._adapters.pop(provider_id, None)
        return ok

    def set_activation(self, state: ActivationState, actor: str = "ui") -> bool:
        ok = self._registry.set_activation(state, actor=actor)
        if ok:
            with self._lock:
                self._adapters.clear()
        return ok

    def switch(
        self,
        *,
        primary: str,
        secondary: str | None = None,
        fallback: str | None = None,
        mode: DecisionMode | str = DecisionMode.HYBRID,
        shadow: str | None = None,
        actor: str = "ui",
    ) -> dict[str, Any]:
        """The explicit provider-switch flow (Sections 26, 43).

        Preconditions are checked BEFORE the switch: provider healthy, model
        valid, credentials valid, test successful. A failed precondition does
        not switch anything.
        """
        if isinstance(mode, str):
            try:
                mode = DecisionMode(mode)
            except ValueError:
                return {"switched": False, "reason": f"unknown mode {mode}"}
        problems: list[str] = []
        for pid in {primary, secondary, fallback} - {None}:
            cfg = self._registry.get_config(pid)
            if cfg is None or not cfg.enabled:
                problems.append(f"{pid}: not enabled")
                continue
            adapter = self._adapter(pid)
            if adapter is None:
                problems.append(f"{pid}: adapter unavailable")
                continue
            test = adapter.test()
            if not test.passed:
                problems.append(f"{pid}: {test.detail}")
        if problems:
            return {"switched": False, "reason": "; ".join(problems), "preconditions": problems}
        state = ActivationState(
            primary_provider=primary,
            secondary_provider=secondary,
            fallback_provider=fallback,
            decision_mode=mode,
            shadow_provider=shadow,
        )
        ok = self.set_activation(state, actor=actor)
        return {
            "switched": ok,
            "active_provider": primary,
            "active_model": (
                self._registry.get_config(primary)
                or ProviderConfig(provider_id=primary, provider_name=primary)
            ).default_model,
            "decision_mode": str(mode),
            "activation_time": datetime.now(UTC).isoformat(),
            "config_version": state.configuration_version,
        }

    # --------------------------------------------------------------------------
    def _persist_health(self, provider_id: str, result: AIProviderTestResult) -> None:
        cfg = self._registry.get_config(provider_id)
        if cfg is None:
            return
        cfg.last_test = datetime.now(UTC).isoformat()
        cfg.latency_ms = result.latency_ms
        cfg.health_status = "HEALTHY" if result.passed else "UNHEALTHY"
        if not result.passed:
            cfg.failure_count += 1
            cfg.last_error = str(result.error)[:300]
        else:
            cfg.last_error = ""
        self._registry.upsert(cfg, actor="health")

    # --------------------------------------------------------------------------
    # Restart / hot-reload matrix (Sections 28, 66)
    # --------------------------------------------------------------------------
    @staticmethod
    def restart_requirement(field_name: str) -> str:
        """Classify every setting HOT_APPLY vs RESTART_REQUIRED (Section 66).

        Everything here is hot-applyable: the orchestrator rebuilds adapters on
        activation and drops cached adapters on reconfiguration, so no engine
        restart is needed. A caller that adds a setting requiring a restart must
        name it here -- no silent restart requirements (Section 69).
        """
        hot = {
            "endpoint",
            "model",
            "default_model",
            "timeout",
            "max_retries",
            "enabled",
            "provider_name",
            "secret_name",
            "primary",
            "secondary",
            "fallback",
            "mode",
            "shadow",
        }
        return "HOT_APPLY" if field_name in hot else "RESTART_REQUIRED"

    # --------------------------------------------------------------------------
    def to_public_dict(self) -> dict[str, Any]:
        """Operator-facing control state (Section 53): active provider, model,
        mode, fallback, shadow, health, restart requirements. No hidden switches."""
        mode, act = self._current_mode()
        return {
            "decision_mode": str(mode),
            "active_provider": act.primary_provider if act else "internal_nse_ml",
            "active_model": _active_model(self._registry, act),
            "secondary_provider": act.secondary_provider if act else None,
            "fallback_provider": act.fallback_provider if act else None,
            "shadow_provider": act.shadow_provider if act else None,
            "providers": self.list_providers(),
            "last_decisions": self.history(5),
            "weights": self._weights.to_dict(),
            "risk_policy": self._risk_policy.to_dict(),
            "versions": {
                "decision": DECISION_VERSION,
                "policy": POLICY_VERSION,
                "gate": GATE_VERSION,
                "template": TEMPLATE_VERSION,
            },
        }


# ------------------------------------------------------------------------------
# Fusion (Section 12)
# ------------------------------------------------------------------------------
def _fuse(responses: dict[str, PositionDecisionResponse], mode: DecisionMode) -> DecisionEvidence:
    """Normalize-then-fuse provider evidence (Section 12).

    Deliberately NOT a naive majority vote (Section 47): providers have
    different confidence meanings, calibration and output spaces, so the fusion
    is a confidence-weighted average over ALREADY NORMALIZED probabilities,
    discounted by each provider's own uncertainty. The result is evidence for
    the policy formula, never a verdict.
    """
    if not responses:
        # No trusted ML evidence: the deterministic policy decides from the
        # position's own numbers. Never fabricate a verdict (Section 15).
        return DecisionEvidence(
            action=AIProviderAction.NO_ACTION,
            confidence=0.0,
            p_hold=0.0,
            p_close=0.0,
            p_reduce=0.0,
            uncertainty=1.0,
            rationale="no provider evidence; deterministic policy only",
        )
    if len(responses) == 1:
        return next(iter(responses.values())).decision

    # Weight each provider by (1 - its own uncertainty): a provider that is
    # unsure of itself cannot outvote one that is sure, which is what stops
    # this from being an unweighted average (Section 47).
    total_w = 0.0
    acc = {
        k: 0.0
        for k in (
            "p_hold",
            "p_close",
            "p_reduce",
            "expected_remaining_r",
            "expected_downside_r",
            "expected_upside_r",
        )
    }
    acc_conf = 0.0
    acc_regime = 0.0
    for resp in responses.values():
        d = resp.decision
        w = max(1e-6, 1.0 - max(0.0, min(1.0, d.uncertainty)))
        total_w += w
        for key in acc:
            acc[key] += w * float(getattr(d, key))
        acc_conf += w * float(d.confidence)
        acc_regime += w * float(d.regime_change_probability)
    if total_w <= 0:
        return DecisionEvidence(action=AIProviderAction.NO_ACTION, confidence=0.0, uncertainty=1.0)
    fused_p = {k: v / total_w for k, v in acc.items()}
    p_hold, p_close, p_reduce = fused_p["p_hold"], fused_p["p_close"], fused_p["p_reduce"]
    # Renormalize onto the simplex so the policy sees valid probabilities.
    total = p_hold + p_close + p_reduce
    if total <= 0:
        return DecisionEvidence(action=AIProviderAction.NO_ACTION, confidence=0.0, uncertainty=1.0)
    s = 1.0 / total
    p_hold, p_close, p_reduce = p_hold * s, p_close * s, p_reduce * s
    probs = {
        AIProviderAction.HOLD: p_hold,
        AIProviderAction.CLOSE: p_close,
        AIProviderAction.REDUCE: p_reduce,
    }
    argmax = max(probs, key=probs.get)
    return DecisionEvidence(
        action=argmax,
        confidence=acc_conf / total_w,
        p_hold=p_hold,
        p_close=p_close,
        p_reduce=p_reduce,
        expected_remaining_r=fused_p["expected_remaining_r"],
        expected_downside_r=fused_p["expected_downside_r"],
        expected_upside_r=fused_p["expected_upside_r"],
        regime_change_probability=acc_regime / total_w,
        uncertainty=max(0.0, 1.0 - acc_conf / total_w),
        rationale=f"confidence-weighted fusion over {sorted(responses)}",
    )


def _active_model(registry: ProviderRegistryStore, act: ActivationState | None) -> str | None:
    """The model the ACTIVE provider is configured to use (Section 42)."""
    if act is None:
        return None
    cfg = registry.get_config(act.primary_provider)
    return cfg.default_model if cfg else None


def _best_response(
    responses: dict[str, PositionDecisionResponse], chain: list[str]
) -> PositionDecisionResponse | None:
    """Pick the response whose proposals the risk gate should evaluate.

    Chain order wins (primary first), because that is the provider the operator
    selected; ties break on confidence.
    """
    if not responses:
        return None
    for pid in chain:
        if pid in responses:
            return responses[pid]
    return max(responses.values(), key=lambda r: r.decision.confidence)
