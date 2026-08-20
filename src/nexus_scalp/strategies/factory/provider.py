"""
LLM Generation Provider — Optional Assisted Generation
=======================================================
STRATEGY FACTORY (2026-08-20).

The external LLM is an OPTIONAL assisted-generation source (spec 33 / 34 /
69 / 70). The provider:

  * is config-driven (base URL, model, api key) — NO hardcoded secrets
    (spec 33 / 91); the API key lives in the secure secret store and is
    never included in prompts or logs;
  * NEVER raises into the orchestrator — every failure path returns []
    and records a PROVIDER_FAILURE (mirrors news/analysis pipeline pattern);
  * returns ONLY structured JSON (the DSL); invalid JSON is repaired once,
    validated, then rejected safely (spec 34 / 90);
  * NEVER computes or claims performance — the research pipeline is the only
    source of measured results (spec 69 / 70);
  * tracks requests / tokens / estimated cost / latency per call for the
    cost-control ledger (spec 45 / 97).

api_key is injected at construction from the secure store; the provider
never logs it.
"""

from __future__ import annotations

import json
import time
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.settings.secret_store import SecureSecretStore

logger = get_logger("nexus_scalp.strategies.factory.provider")

#: Secret-store key for the generation API key (same store as telegram).
LLM_API_KEY_SECRET: str = "factory.llm_api_key"

#: Default openai-compatible endpoint suffix.
_CHAT_COMPLETIONS_PATH = "/chat/completions"

#: Hard request budget guards (spec 45 / 72).
DEFAULT_MAX_REQUESTS_PER_GENERATION: int = 60
DEFAULT_TIMEOUT_SEC: float = 45.0
DEFAULT_MAX_TOKENS: int = 4096

#: JSON-schema-compatible shape the provider EXPECTS from the model. The
#: response must be {"strategies": [ {dsl...}, ... ]}
EXPECTED_RESPONSE_KEYS = frozenset({"strategies"})


class ProviderUsage:
    """Thread-safe usage/cost ledger for one provider instance (spec 45)."""

    def __init__(self) -> None:
        self.requests = 0
        self.failures = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.last_latency_ms = 0.0
        self.total_latency_ms = 0.0
        self.last_error = ""

    def snapshot(self) -> dict[str, Any]:
        # Estimated cost: simple 1M-token price model; configurable via constructor.
        cost = self.total_tokens * 0.000_002  # $2 per 1M tokens blended
        return {
            "requests": self.requests,
            "failures": self.failures,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(cost, 4),
            "last_latency_ms": round(self.last_latency_ms, 1),
            "total_latency_ms": round(self.total_latency_ms, 1),
            "last_error": self.last_error,
        }


class LLMGenerationProvider:
    """OpenAI-compatible chat-completions provider for strategy DSL generation.

    Deterministic fallback: when `available()` is False (no key / no base /
    no model) the orchestrator simply uses the deterministic generator — the
    factory NEVER depends on the LLM for correctness.
    """

    provider_name: str = "openai-compatible"
    #: Prompt version — every candidate records which prompt version produced
    #: it (spec 86).
    prompt_version: str = "factory-dsl-v1"

    def __init__(
        self,
        *,
        api_base_url: str = "",
        model: str = "",
        api_key: str = "",
        prompt_text: str = "",
        request_timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        max_requests_per_generation: int = DEFAULT_MAX_REQUESTS_PER_GENERATION,
        temperature: float = 0.7,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        secret_store: SecureSecretStore | None = None,
        seed: int = 20260820,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.model = model
        self.request_timeout_sec = float(request_timeout_sec)
        self.max_requests_per_generation = int(max_requests_per_generation)
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.seed = int(seed)
        self.prompt_text = prompt_text
        self.usage = ProviderUsage()
        self._api_key = api_key or self._load_key(secret_store)
        self._window_start: float = time.time()
        self._window_requests = 0

    @staticmethod
    def _load_key(secret_store: SecureSecretStore | None) -> str:
        if secret_store is None:
            return ""
        try:
            return str(secret_store.get_secret(LLM_API_KEY_SECRET) or "")
        except Exception:  # pragma: no cover - defensive
            return ""

    def available(self) -> bool:
        """True only when base URL + model + key are all configured."""
        return bool(self.api_base_url and self.model and self._api_key)

    def _budget_exhausted(self) -> bool:
        return self.usage.requests >= self.max_requests_per_generation

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_dsls(
        self,
        prompt_context: dict[str, Any],
        n: int,
    ) -> list[dict[str, Any]]:
        """Requests `n` strategy DSL dicts from the model.

        Returns a list of RAW DSL dicts (unvalidated!). The orchestrator runs
        them through the full structural gate chain; invalid entries are
        rejected with UNSUPPORTED_FEATURE / INVALID_SCHEMA / LOOKAHEAD_RISK.
        Never raises.
        """
        if not self.available():
            logger.warning("[STRATEGY_FACTORY] provider unavailable — deterministic path")
            return []
        if self._budget_exhausted():
            logger.warning("[STRATEGY_FACTORY] LLM budget exhausted")
            self.usage.last_error = "request budget exhausted"
            return []

        try:
            import httpx
        except ImportError:  # pragma: no cover
            logger.warning("[STRATEGY_FACTORY] httpx unavailable")
            return []

        system, user = self._build_messages(prompt_context, n)
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        url = f"{self.api_base_url}{_CHAT_COMPLETIONS_PATH}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()
        self.usage.requests += 1
        self._window_requests += 1
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=self.request_timeout_sec)
        except Exception as e:
            self.usage.failures += 1
            self.usage.last_error = f"NETWORK:{type(e).__name__}"
            logger.warning("[STRATEGY_FACTORY] provider network failure", error=type(e).__name__)
            return []
        latency_ms = (time.perf_counter() - started) * 1000.0
        self.usage.last_latency_ms = latency_ms
        self.usage.total_latency_ms += latency_ms

        if resp.status_code != 200:
            self.usage.failures += 1
            self.usage.last_error = f"HTTP:{resp.status_code}"
            logger.warning("[STRATEGY_FACTORY] provider HTTP failure", status=resp.status_code)
            return []

        try:
            data = resp.json()
        except Exception:
            self.usage.failures += 1
            self.usage.last_error = "BAD_JSON_RESPONSE"
            return []

        usage = data.get("usage") or {}
        self.usage.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.usage.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        self.usage.total_tokens += int(usage.get("total_tokens", 0) or 0)

        content = ""
        try:
            content = (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            pass
        raw_dsls = self._extract_dsl_list(content)
        if not raw_dsls:
            self.usage.last_error = "NO_VALID_STRATEGIES_IN_RESPONSE"
        return raw_dsls[:n]

    # ------------------------------------------------------------------
    # Response parsing (repair-once, then reject safely — spec 34)
    # ------------------------------------------------------------------

    def _extract_dsl_list(self, content: str) -> list[dict[str, Any]]:
        if not content:
            return []
        # 1) Direct JSON object.
        parsed = self._try_parse(content)
        if isinstance(parsed, dict) and isinstance(parsed.get("strategies"), list):
            return [s for s in parsed["strategies"] if isinstance(s, dict)]
        if isinstance(parsed, list):
            return [s for s in parsed if isinstance(s, dict)]
        # 2) Repair: strip markdown fences / prose around JSON.
        repaired = self._repair(content)
        parsed = self._try_parse(repaired)
        if isinstance(parsed, dict) and isinstance(parsed.get("strategies"), list):
            return [s for s in parsed["strategies"] if isinstance(s, dict)]
        if isinstance(parsed, list):
            return [s for s in parsed if isinstance(s, dict)]
        return []

    @staticmethod
    def _try_parse(content: str) -> Any:
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _repair(content: str) -> str:
        """Repair common LLM JSON artifacts (markdown fences, prose)."""
        text = content.strip()
        if text.startswith("```"):
            # strip ```json ... ```
            lines = text.splitlines()
            body = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(body)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        return text

    # ------------------------------------------------------------------
    # Prompt construction (spec 34 / 35 / 83 / 86)
    # ------------------------------------------------------------------

    def _build_messages(self, context: dict[str, Any], n: int) -> tuple[str, str]:
        feature_list = ", ".join(context.get("feature_ids") or [])
        system = (
            "You are a quantitative research scientist and strategy designer. "
            "You produce STRATEGY HYPOTHESES in a structured DSL. Rules:\n"
            "1. Only use features from the approved catalog. Never invent indicators.\n"
            "2. Never claim or compute backtest performance. You propose; the engine measures.\n"
            "3. Prefer simple, robust, generalizable structures over complex optimized ones.\n"
            "4. Every strategy must state a hypothesis: market mechanism, expected regime, "
            "invalidation and abstain conditions.\n"
            '5. Return ONLY a JSON object: {"strategies": [ {dsl-object}, ... ]}. '
            "No markdown, no prose.\n"
            f"Approved feature catalog ({len(context.get('feature_ids') or [])}): {feature_list}\n"
            f"Supported timeframes: {context.get('timeframes')}\n"
            f"Supported symbols: {context.get('symbols')}\n"
            f"Complexity limits: max conditions {context.get('max_conditions')}, "
            f"max features {context.get('max_features')}, max timeframes {context.get('max_timeframes')}\n"
            "DSL schema: {schema_version, hypothesis:{statement,market_mechanism,expected_regime,"
            "invalidation,abstain_conditions}, family, market:{symbols,timeframes}, context, setup, "
            "entry:{logic,confirmation[]}, filters:[{feature,op,value}], exit:{mode}, risk, "
            "constraints:{no_future_data:true}}\n"
            "A strategy that performs spectacularly in-sample but fails out-of-sample is inferior "
            "to a simpler strategy with stable OOS behavior."
        )
        user = "Generate exactly " + str(n) + " DISTINCT strategies.\n"
        if context.get("research_memory"):
            user += (
                "Research memory for the previous generations:\n"
                + str(context["research_memory"])
                + "\n"
            )
        if context.get("generation_objective"):
            user += "Generation objective: " + str(context["generation_objective"]) + "\n"
        user += (
            "Diversity requirement: cover multiple strategy families; do NOT emit hundreds of "
            "variations of one strategy. Output ONLY the JSON object."
        )
        return system, user


__all__ = ["LLM_API_KEY_SECRET", "LLMGenerationProvider", "ProviderUsage"]
