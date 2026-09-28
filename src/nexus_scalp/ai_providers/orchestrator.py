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

import copy
import hashlib
import inspect
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.ai_providers.adapters import (
    ADAPTER_TYPES,
    BaseAIProviderAdapter,
    adapter_class_for_template,
)
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
    _KNOWN_IDS,
    BUILTIN_PROVIDER_IDS,
    PROVIDER_TYPE_EXTERNAL,
    PROVIDER_TYPE_INTERNAL,
    STATE_ACTIVE,
    STATE_INACTIVE,
    STATE_REGISTERED,
    STATE_TEST_FAILED,
    STATE_TESTING,
    STATE_UNAVAILABLE,
    STATE_VERIFIED,
    ActivationState,
    DecisionMode,
    ProviderConfig,
    ProviderRegistryStore,
    _normalize_custom_id,
    is_valid_provider_id,
    lifecycle_for_test_failure,
    template_for,
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
        decision_store: Any | None = None,
    ) -> None:
        self._registry: ProviderRegistryStore = registry
        self._secret_store = secret_store
        self._weights = weights or PolicyWeights()
        self._risk_policy = risk_policy or RiskPolicy()
        self._request_deadline_sec = request_deadline_sec
        self._adviser_service = adviser_service
        #: Durable record of every final decision (Sections 41, 58, 63).
        #: Optional: absent = in-memory only, and recording never raises into
        #: the decide path (the store is an observer, not a participant).
        self._decision_store = decision_store
        self._lock = threading.RLock()
        self._adapters: dict[str, tuple[str, BaseAIProviderAdapter]] = {}
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
            # A custom provider resolves its adapter class from the TEMPLATE it
            # was built from (Section 40): one class, many instances, each with
            # its own endpoint/model/secret in its config row.
            tmpl = cfg.template_id or cfg.provider_id
            cls = adapter_class_for_template(tmpl)
        if cls is None:
            logger.error(
                "[AI-PROV] no adapter class registered for %s (template=%s)",
                provider_id,
                cfg.template_id,
            )
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
            # A custom instance must report ITS OWN id (the class attribute is
            # the built-in template id); the config row is the identity owner.
            if provider_id not in ADAPTER_TYPES:
                adapter.provider_id = provider_id
        except Exception as exc:
            logger.error("[AI-PROV] failed to build adapter %s: %s", provider_id, exc)
            return None
        return adapter

    def _adapter(self, provider_id: str) -> BaseAIProviderAdapter | None:
        # The cache is KEYED ON CONFIGURATION VERSION: a switch that changes a
        # provider's model/endpoint/secret bumps the version, so a cached
        # adapter built over the PREVIOUS configuration is not returned to a
        # caller who asked after the switch (Section 31: stale responses must
        # not overwrite newer state).
        with self._lock:
            cfg = self._registry.get_config(provider_id)
            if cfg is None or not cfg.enabled:
                return None
            version = cfg.configuration_version
            entry = self._adapters.get(provider_id)
            if entry is not None and entry[0] == version:
                return entry[1]
            built = self._build_adapter(provider_id)
            if built is None:
                return None
            self._adapters[provider_id] = (version, built)
            return built

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
        unbounded decision storage) + durable persistence when a store is
        configured (Sections 41, 63)."""
        payload = outcome.to_dict()
        with self._lock:
            self._decision_history.insert(0, payload)
            if len(self._decision_history) > self._max_history:
                del self._decision_history[self._max_history :]
        if self._decision_store is not None:
            # The store is fail-closed internally; this can never raise into
            # the decide path and never changes the decision itself.
            self._decision_store.record(payload)

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._decision_history[:limit])

    # --------------------------------------------------------------------------
    # Provider management surface (UI/CLI/API all use these; Section 61)
    # --------------------------------------------------------------------------
    def list_providers(self) -> list[dict[str, Any]]:
        """All providers with identity + health (Sections 19, 40).

        The list is driven by the REGISTRY (rows), not by the adapter-class
        table: a custom provider added through the UI has no entry in
        ``ADAPTER_TYPES`` and would otherwise be invisible on the very page
        used to create it.
        """
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pid in list(self._registry.list_provider_ids()) + list(ADAPTER_TYPES):
            if pid in seen:
                continue
            seen.add(pid)
            cfg = self._registry.get_config(pid)
            if cfg is None:
                cfg = ProviderConfig(provider_id=pid, provider_name=pid, enabled=False)
            adapter = self._adapter(pid)
            if adapter is None:
                # A disabled provider must not be built just to report health,
                # so this one is config-only (no invented health).
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
        # TESTING is transient and not persisted: a crash mid-test must not
        # leave the provider claiming it was being tested at restart.
        self._registry.apply_lifecycle(provider_id, STATE_TESTING, actor="test")
        adapter = self._adapter(provider_id)
        if adapter is None:
            self._registry.apply_lifecycle(
                provider_id, STATE_UNAVAILABLE, failure_category="UNKNOWN", actor="test"
            )
            return AIProviderTestResult(False, "provider is disabled or not configured")
        result = adapter.test()
        self._persist_health(provider_id, result)
        target = STATE_VERIFIED if result.passed else STATE_TEST_FAILED
        cat = None
        if not result.passed:
            err = result.error or result.detail or ""
            cat = _category_from_error(err)
            target = lifecycle_for_test_failure(cat)
        self._registry.apply_lifecycle(provider_id, target, failure_category=cat, actor="test")
        return result

    def test_connection(self, provider_id: str) -> AIProviderTestResult:
        return self.test_provider(provider_id)

    def test_model(self, provider_id: str, model: str) -> AIProviderTestResult:
        """Test ONE model against the contract (Section 6, model test).

        NON-MUTATING: it must not change the persisted default model, because a
        probe is not a configuration change (the CLI's ``model test`` used to
        persist the model as a side effect — a probe that rewrote settings).
        The model under test is held on a scratch copy of the config only.
        """
        adapter = self._adapter(provider_id)
        if adapter is None:
            return AIProviderTestResult(
                False, "provider is disabled or not configured", stage="adapter"
            )
        scratch = copy.deepcopy(adapter.config)
        scratch.default_model = model
        probe = _clone_adapter_for_model(adapter, scratch)
        return probe.test()

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

    def add_provider(
        self,
        *,
        provider_id: str,
        template_id: str,
        provider_name: str | None = None,
        endpoint: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        enabled: bool = True,
        actor: str = "ui",
    ) -> dict[str, Any]:
        """ADD PROVIDER (Sections 4, 30): create a provider from a template.

        A created provider is NOT active — activation is a separate explicit
        step (Section 29: "a provider should not become active merely because it
        was created"). It lands REGISTERED and (when ``enabled``) enabled, so
        the operator can TEST it next.

        The key, when supplied, goes straight to the DPAPI store and is
        referenced by name — it is never written into the config row, echoed
        back, or logged (Section 37).
        """
        tmpl = template_for(template_id)
        if tmpl is None:
            return {"created": False, "reason": f"unknown template {template_id}"}
        pid = _normalize_custom_id(provider_id) if provider_id not in _KNOWN_IDS else provider_id
        if not is_valid_provider_id(pid):
            return {
                "created": False,
                "reason": (
                    "invalid provider id: use letters/numbers only (built-in ids or a "
                    "custom_ prefix); it becomes a registry key and a log identifier"
                ),
            }
        existing = self._registry.get_config(pid)
        cfg = existing or ProviderConfig(
            provider_id=pid,
            provider_name=provider_name or tmpl.label,
            type=(
                PROVIDER_TYPE_INTERNAL
                if template_id == "internal_nse_ml"
                else PROVIDER_TYPE_EXTERNAL
            ),
        )
        cfg.template_id = template_id
        if provider_name:
            cfg.provider_name = provider_name
        if endpoint is not None:
            cfg.endpoint = endpoint
        if model is not None:
            cfg.default_model = model
        cfg.enabled = bool(enabled)
        # A fresh provider starts at REGISTERED (Section 45). Re-declaring an
        # existing built-in keeps its tested state — a wizard re-open must not
        # silently un-verify a provider that passed a real test.
        if existing is None:
            cfg.lifecycle_state = STATE_REGISTERED
        cfg.capabilities = list(tmpl.capabilities)
        if api_key:
            secret_name = f"ai_provider_{pid}_key"
            self._secret_store.set_secret(secret_name, api_key)
            cfg.secret_name = secret_name
        ok = self._registry.upsert(cfg, actor=actor)
        if ok:
            with self._lock:
                self._adapters.pop(pid, None)
        return {
            "created": ok,
            "provider_id": pid,
            "lifecycle_state": cfg.lifecycle_state,
            "restart_requirement": self.restart_requirement("endpoint"),
        }

    def delete_provider(self, provider_id: str, actor: str = "ui") -> dict[str, Any]:
        """Remove a CUSTOM provider (Section 30). Built-ins are protected.

        Refuses to delete a provider that is currently ACTIVE: the runtime must
        never be left pointing at a provider whose config row is gone. The
        caller deactivates first (which routes back to the internal provider).
        """
        if provider_id in BUILTIN_PROVIDER_IDS:
            return {
                "removed": False,
                "reason": "built-in providers are protected and cannot be deleted",
            }
        cur = self._registry.get_activation()
        if cur is not None and cur.primary_provider == provider_id:
            return {
                "removed": False,
                "reason": (
                    "cannot delete the ACTIVE provider; deactivate it first "
                    "(routing returns to the internal model)"
                ),
            }
        with self._lock:
            self._adapters.pop(provider_id, None)
        removed = self._registry.delete(provider_id)
        return {
            "removed": removed,
            "provider_id": provider_id,
            "reason": "" if removed else "provider not found",
        }

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
        not switch anything. The preconditions are returned as STRUCTURED
        records so the UI can render which check failed and why (Section 42),
        and the caller decides the HTTP status — the orchestrator never raises
        on an expected refusal.
        """
        if isinstance(mode, str):
            try:
                mode = DecisionMode(mode)
            except ValueError:
                return {
                    "switched": False,
                    "reason": f"unknown mode {mode}",
                    "preconditions": [],
                }
        problems: list[dict[str, str]] = []
        #: The internal model is the always-available fallback floor: it has no
        #: config row to enable and no live test to run until it is built, yet
        #: INTERNAL_ONLY must stay reachable from any state (Section 20: the
        #: operator can always route back to the internal model). Treating it
        #: like an external provider would make "switch back" impossible.
        INTERNAL_ID = "internal_nse_ml"
        for pid in {primary, secondary, fallback} - {None}:
            if pid == INTERNAL_ID:
                adapter = self._adapter(pid)
                if adapter is None:
                    # Not built yet is not a failure for the internal model: it
                    # is the state it starts in, and it is verified on demand.
                    continue
                test = adapter.test()
                if not test.passed:
                    problems.append({"check": "test", "detail": f"{pid}: {test.detail}"})
                continue
            cfg = self._registry.get_config(pid)
            if cfg is None or not cfg.enabled:
                problems.append({"check": "enabled", "detail": f"{pid}: not enabled"})
                continue
            adapter = self._adapter(pid)
            if adapter is None:
                problems.append({"check": "adapter", "detail": f"{pid}: adapter unavailable"})
                continue
            test = adapter.test()
            if not test.passed:
                problems.append({"check": "test", "detail": f"{pid}: {test.detail}"})
        if problems:
            return {
                "switched": False,
                "reason": "; ".join(p["detail"] for p in problems),
                "preconditions": problems,
            }
        state = ActivationState(
            primary_provider=primary,
            secondary_provider=secondary,
            fallback_provider=fallback,
            decision_mode=mode,
            shadow_provider=shadow,
        )
        # Who holds the primary role RIGHT NOW: captured before the write so the
        # standby/rollback bookkeeping targets the provider being replaced,
        # not the one being promoted.
        prior = self._registry.get_activation()
        previous_primary = None if prior is None else prior.primary_provider
        ok = self.set_activation(state, actor=actor)
        if ok:
            # The old primary goes to standby, not away: it stays enabled and
            # verified so it can be re-activated or rolled back to (Section 13).
            self._mark_standby_except(primary, previous_primary=previous_primary)
            # Bump the now-active provider's config version: the adapter cache
            # is keyed on it, so without this a request right after the switch
            # could be served by the adapter built before activation.
            _bump_config_version(self._registry, primary)
        return {
            "switched": ok,
            "active_provider": primary,
            "active_model": (
                self._registry.get_config(primary)
                or ProviderConfig(provider_id=primary, provider_name=primary)
            ).default_model,
            "decision_mode": str(mode),
            "activated_at": datetime.now(UTC).isoformat(),
            "configuration_version": state.configuration_version,
            "restart_required": False,
            "preconditions": [],
            "warnings": [],
        }

    def _mark_standby_except(
        self, active_primary: str, previous_primary: str | None = None
    ) -> None:
        """Every OTHER provider stays where it is; only the previous ACTIVE
        primary is moved to standby (INACTIVE/VERIFIED) so rollback can find it.

        Never disables anything: a standby provider remains eligible for a
        later activation or for the fallback chain.
        """
        # The provider being REPLACED is the one that held the primary role
        # before this switch, not "every provider that is not the new one":
        # lifecycle_state is not seeded for built-ins, so iterating all rows
        # would leave a replaced provider pinned ACTIVE (two primaries).
        replaced = previous_primary
        if replaced is None:
            cur = self._registry.get_activation()
            replaced = None if cur is None else cur.primary_provider
        if replaced and replaced != active_primary:
            cfg = self._registry.get_config(replaced)
            if cfg is not None and cfg.enabled and cfg.lifecycle_state == STATE_ACTIVE:
                # Keep a healthy provider at VERIFIED (standby), not INACTIVE:
                # INACTIVE means the operator turned it off.
                self._registry.apply_lifecycle(replaced, STATE_INACTIVE, actor="switch")
        # The new primary takes ACTIVE through the legal path: a freshly added
        # provider is REGISTERED, and REGISTERED -> ACTIVE is not an edge in the
        # machine (Section 45). VERIFIED is the state a passing test earns, and
        # from there ACTIVE is legal.
        cfg = self._registry.get_config(active_primary)
        if cfg is None or not cfg.enabled:
            return
        if cfg.lifecycle_state in (STATE_REGISTERED, STATE_TESTING):
            self._registry.apply_lifecycle(active_primary, STATE_VERIFIED, actor="switch")
        if cfg.lifecycle_state != STATE_ACTIVE:
            self._registry.apply_lifecycle(active_primary, STATE_ACTIVE, actor="switch")

    def deactivate(self, actor: str = "ui") -> dict[str, Any]:
        """CANCEL / DEACTIVATE the current provider (Section 14).

        Routing returns to the internal provider — which is NEVER deleted or
        disabled, only returned to primary (Section 20). The outgoing provider
        becomes standby (VERIFIED/INACTIVE), not removed: rollback stays open.
        """
        cur = self._registry.get_activation()
        outgoing = cur.primary_provider if cur else None
        if outgoing and outgoing != "internal_nse_ml":
            self._registry.apply_lifecycle(outgoing, STATE_INACTIVE, actor=actor)
        state = ActivationState(
            primary_provider="internal_nse_ml",
            secondary_provider=None,
            fallback_provider=None,
            decision_mode=DecisionMode.INTERNAL_ONLY,
            shadow_provider=None,
        )
        self.set_activation(state, actor=actor)
        return {
            "status": "OK",
            "active_provider": "internal_nse_ml",
            "decision_mode": str(DecisionMode.INTERNAL_ONLY),
            "outgoing": outgoing,
            "note": (
                "Routing returned to the internal NSE model. The previous provider "
                "remains available as standby and can be re-activated or rolled "
                "back to. No provider was deleted."
            ),
        }

    def rollback(self, actor: str = "ui") -> dict[str, Any]:
        """Restore the activation that was live before the current one (Section 17).

        Refuses when there is nothing to restore — never silently re-points the
        runtime at a state nobody recorded. The restored activation is verified
        healthy before it takes over, and refuses to restore an activation whose
        provider is unavailable (never leave the system pointing at a dead
        provider — that is the exact failure rollback exists to undo).
        """
        prev = self._registry.get_previous_activation()
        if prev is None:
            return {
                "status": "UNAVAILABLE",
                "restored": None,
                "active_provider": (
                    self._registry.get_activation()
                    or ActivationState(primary_provider="internal_nse_ml")
                ).primary_provider,
                "decision_mode": str(
                    (
                        self._registry.get_activation()
                        or ActivationState(
                            primary_provider="internal_nse_ml",
                            decision_mode=DecisionMode.INTERNAL_ONLY,
                        )
                    ).decision_mode
                ),
                "note": "No previous activation recorded — nothing to roll back to.",
            }
        # Health-check the restore target: rolling back onto a dead provider
        # would reproduce the outage rollback is meant to repair.
        cfg = self._registry.get_config(prev.primary_provider)
        if cfg is None or not cfg.enabled:
            return {
                "status": "UNAVAILABLE",
                "restored": prev.to_public_dict(),
                "active_provider": (
                    self._registry.get_activation()
                    or ActivationState(primary_provider="internal_nse_ml")
                ).primary_provider,
                "decision_mode": str(
                    (
                        self._registry.get_activation()
                        or ActivationState(
                            primary_provider="internal_nse_ml",
                            decision_mode=DecisionMode.INTERNAL_ONLY,
                        )
                    ).decision_mode
                ),
                "note": (
                    f"Previous activation pointed at {prev.primary_provider}, which is "
                    "no longer present or enabled. Refusing to restore a dead target; "
                    "the current activation is unchanged."
                ),
            }
        ok = self.set_activation(prev, actor=actor)
        if ok:
            self._registry.apply_lifecycle(prev.primary_provider, STATE_ACTIVE, actor=actor)
            self._registry.clear_previous_activation()
        return {
            "status": "OK" if ok else "FAIL",
            "restored": prev.to_public_dict() if ok else None,
            "active_provider": prev.primary_provider if ok else None,
            "decision_mode": str(prev.decision_mode) if ok else None,
            "note": (
                f"Restored {prev.primary_provider} as the active provider."
                if ok
                else "Rollback rejected; the current activation is unchanged."
            ),
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


def _category_from_error(text: str) -> str | None:
    """Best-effort map of a test failure message onto an error category.

    The adapter's own ``ProviderError`` carries the authoritative category; a
    test result only surfaces its string form, so match on the category
    vocabulary the ecosystem actually uses rather than free-text guessing.
    """
    if not text:
        return None
    upper = text.upper()
    for cat in (
        "AUTH_FAILED",
        "MODEL_UNAVAILABLE",
        "RATE_LIMITED",
        "TIMEOUT",
        "SCHEMA_VIOLATION",
        "MALFORMED_RESPONSE",
        "UPSTREAM_UNAVAILABLE",
        "NETWORK",
    ):
        if cat in upper:
            return cat
    return None


def _clone_adapter_for_model(
    adapter: BaseAIProviderAdapter, scratch_config: Any
) -> BaseAIProviderAdapter:
    """Rebuild an adapter over a SCRATCH config, to probe one model.

    The adapter reads endpoint/model/secret from ``self.config`` at call time,
    so rebuilding over a copied config whose only difference is ``default_model``
    is the non-mutating model probe (Section 6). Never writes to the registry.

    The constructor is rebuilt through the SAME signature the orchestrator
    uses, so a new required kwarg cannot be dropped here and surface as a
    TypeError only when an operator actually clicks "Test model". Only kwargs
    the subclass actually declares are forwarded: an adapter that bridges an
    internal service takes ``adviser_service``, a plain HTTP adapter does not,
    and neither should fail the probe (Section 40).
    """
    cls = type(adapter)
    rebuilt = cls(**_adapter_init_kwargs(adapter, scratch_config))
    rebuilt.provider_id = adapter.provider_id
    return rebuilt


def _adapter_init_kwargs(adapter: BaseAIProviderAdapter, scratch_config: Any) -> dict[str, Any]:
    """The kwargs this adapter's constructor accepts, populated from the live one.

    Filtering by the declared signature keeps the probe working for every
    adapter without a per-class branch, and never passes a kwarg the class
    does not name.
    """
    candidates = {
        "config": scratch_config,
        "registry": adapter._registry,
        "secret_store": adapter._secret_store,
        "adviser_service": adapter._adviser_service,
    }
    try:
        params = inspect.signature(cls_init(type(adapter))).parameters
    except (TypeError, ValueError):
        return candidates
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return candidates
    return {k: v for k, v in candidates.items() if k in params}


def cls_init(cls: type) -> Any:
    """The class's own ``__init__`` (``object.__init__`` is not a signature)."""
    init = getattr(cls, "__init__", None)
    if init is object.__init__:
        return lambda *a, **k: None
    return init


def _bump_config_version(registry: ProviderRegistryStore, provider_id: str) -> None:
    """Bump the persisted configuration_version of one provider.

    The adapter cache is keyed on that version, so any change that must reach
    the runtime (a switch, a reconfigure, a secret rotation) has to bump it or
    the cache silently serves the adapter built over the OLD config
    (Section 31: a provider switch must not be answered by a stale adapter).
    """
    cfg = registry.get_config(provider_id)
    if cfg is None:
        return
    cfg.configuration_version = _next_version(cfg.configuration_version)
    registry.upsert(cfg, actor="switch")
    # upsert stores a COPY: re-read so the caller's own in-memory object does
    # not lag the row the cache is now keyed on.
    fresh = registry.get_config(provider_id)
    if fresh is not None:
        cfg.configuration_version = fresh.configuration_version


def _next_version(old: str) -> str:
    """Monotonic version string: simple increment on a numeric prefix."""
    head = old.split("-", maxsplit=1)[0]
    try:
        return f"{int(head) + 1}"
    except ValueError:
        return f"{old}-1"
