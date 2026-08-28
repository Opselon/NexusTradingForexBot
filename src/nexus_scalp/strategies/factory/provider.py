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
#: Settings DB key for the API base URL (NOT secret).
LLM_BASE_URL_KEY: str = "factory.llm_base_url"
#: Settings DB key for the model name (NOT secret).
LLM_MODEL_KEY: str = "factory.llm_model"
#: Settings DB key for the model temperature (NOT secret).
LLM_TEMPERATURE_KEY: str = "factory.llm_temperature"

#: Prompt template version — every candidate records which prompt version
#: produced it (spec 86). Bump when the DSL grammar/prompt changes.
PROMPT_VERSION: str = "factory-dsl-v3.1"

#: Default openai-compatible endpoint suffix.
_CHAT_COMPLETIONS_PATH = "/chat/completions"

#: Hard request budget guards (spec 45 / 72).
DEFAULT_MAX_REQUESTS_PER_GENERATION: int = 60
DEFAULT_TIMEOUT_SEC: float = 120.0
DEFAULT_MAX_TOKENS: int = 8192

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
    prompt_version: str = PROMPT_VERSION

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
            body = resp.text
            # Some compatible endpoints append SSE framing to the JSON body
            # (e.g. "}data: [DONE]") — strip it before parsing.
            marker = body.rfind("data: [DONE]")
            if marker > 0:
                body = body[:marker].rstrip()
            data = json.loads(body)
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

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = True,
    ) -> dict[str, Any] | None:
        """Generic single JSON-object completion (reused by News Intelligence AI
        analysis and any other JSON-producing task).

        Returns a parsed dict, or None on ANY failure (unconfigured / network /
        HTTP / malformed JSON). Never raises. The API key stays server-side in
        the secret store and is never logged.
        """
        if not self.available():
            return None
        if self._budget_exhausted():
            self.usage.last_error = "request budget exhausted"
            return None
        try:
            import httpx
        except ImportError:  # pragma: no cover
            logger.warning("[STRATEGY_FACTORY] httpx unavailable")
            return None

        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature if temperature is None else float(temperature),
            "max_tokens": self.max_tokens if max_tokens is None else int(max_tokens),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}
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
            return None
        latency_ms = (time.perf_counter() - started) * 1000.0
        self.usage.last_latency_ms = latency_ms
        self.usage.total_latency_ms += latency_ms
        if resp.status_code != 200:
            self.usage.failures += 1
            self.usage.last_error = f"HTTP:{resp.status_code}"
            logger.warning("[STRATEGY_FACTORY] provider HTTP failure", status=resp.status_code)
            return None
        body = resp.text
        marker = body.rfind("data: [DONE]")
        if marker > 0:
            body = body[:marker].rstrip()
        try:
            data = json.loads(body)
        except Exception:
            self.usage.failures += 1
            self.usage.last_error = "BAD_JSON_RESPONSE"
            return None
        usage = data.get("usage") or {}
        self.usage.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.usage.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        self.usage.total_tokens += int(usage.get("total_tokens", 0) or 0)
        content = ""
        try:
            content = (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            return None
        parsed = self._try_parse(content)
        if isinstance(parsed, dict):
            return parsed
        repaired = self._repair(content)
        parsed = self._try_parse(repaired)
        if isinstance(parsed, dict):
            return parsed
        self.usage.last_error = "NO_VALID_JSON_IN_RESPONSE"
        return None

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
        # Some compatible endpoints append SSE framing to the JSON body
        # (e.g. "}data: [DONE]") — strip it before parsing.
        text = content.strip()
        marker = text.rfind("data: [DONE]")
        if marker > 0:
            text = text[:marker].rstrip()
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _repair(content: str) -> str:
        """Repair common LLM JSON artifacts (markdown fences, SSE trailing,
        prose)."""
        text = content.strip()
        marker = text.rfind("data: [DONE]")
        if marker > 0:
            text = text[:marker].rstrip()
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
        """Builds the LONG structured generation prompt (prompt v3.1, 2026-08-21).

        The prompt teaches the model the EXACT post-generation pipeline the
        engine runs on every candidate: GENERATE -> VALIDATE -> BACKTEST ->
        WALK-FORWARD -> OOS -> ROBUSTNESS -> SCORE -> RANK -> ELITE -> EVOLVE.
        The model proposes HYPOTHESES only; the engine measures everything
        (spec 34/35/69/70/83/86).

        v3.1 upgrade (2026-08-21): the prompt now includes the BENCHMARK
        surface (strategy-aware backtests via DSL filter coverage, walk-forward
        repr, OOS explainability) so the model understands HOW its hypotheses
        will be graded and what the API returns for AI ranking — and it is
        told to diversify thresholds not just families (the pre-fix failure
        was threshold-homogeneity leading to 40 identical scores).
        """
        feature_list = ", ".join(context.get("feature_ids") or [])
        timeframes = ", ".join(context.get("timeframes") or [])
        symbols = ", ".join(context.get("symbols") or [])
        max_cond = context.get("max_conditions", 9)
        max_feat = context.get("max_features", 6)
        max_tf = context.get("max_timeframes", 1)
        benchmark_note = (
            "BENCHMARK (how each hypothesis is graded, 2026-08-21): every candidate is backtested "
            "against ITS OWN ledger slice — the DSL filters are evaluated over real historical 50D "
            "feature_snapshot vectors (same vectors the live ScalpFeatureEngine produced) to select only "
            "the samples the strategy would have entered; walk-forward and OOS each re-run on that slice. "
            "The API GET /api/factory/benchmarks?generation_id=Gx returns {coverage_pct, backtest {expectancy_r, "
            "profit_factor}, walk_forward {pass_rate, degradation}, oos {status, reason}, score {final_score, verdict}, "
            "primary_failure, decision} per candidate. Threshold choices MATTER — a filter `dist_to_ema > 0.7` "
            "vs `> 0.0` yields a different slice and a different score; diversify thresholds. "
        )
        # PROMPT_VERSION is declared at module top; bump doc comment only here.
        schema_fields = """{
  "schema_version": "1.0",
  "hypothesis": {
    "statement": "one-sentence mechanism being hypothesized",
    "market_mechanism": "economic/structural explanation",
    "expected_regime": ["trending" | "ranging" | "high_volatility" | "low_volatility"],
    "invalidation": ["condition that falsifies the thesis"],
    "abstain_conditions": ["conditions under which the strategy must NOT trade"]
  },
  "family": "one of TREND_FOLLOWING, MEAN_REVERSION, BREAKOUT, REVERSAL, MOMENTUM, VOLATILITY_EXPANSION, VOLATILITY_CONTRACTION, LIQUIDITY_SWEEP, SESSION, MULTI_TIMEFRAME, HYBRID",
  "market": {"symbols": ["XAUUSD"], "timeframes": ["M1"]},
  "context": {"optional": "regime/session/volatility filters as objects"},
  "setup": {"structure": "preconditions you look for before entry"},
  "entry": {"logic": "entry logic name", "confirmation": ["feature_ids that confirm the entry"]},
  "filters": [{"feature": "feature_id", "op": "gt|lt|between", "value": 0.0}],
  "exit": {"mode": "fixed_rr|trailing|target|chandelier", "rr": 2.0},
  "risk": {"risk_governance": "global"},
  "constraints": {"no_future_data": true}
}"""
        system = (
            "You are a senior quantitative researcher designing rule-based scalping strategy "
            "hypotheses for XAUUSD. You propose strategy HYPOTHESES only - you NEVER claim, "
            "compute or predict performance. The engine measures everything through a "
            "deterministic pipeline.\n"
            "\n"
            "ENGINE PIPELINE (every candidate goes through ALL stages, in order):\n"
            "  1. GENERATE - you (or the deterministic generators) propose raw DSL hypotheses.\n"
            "  2. VALIDATE - hard structural gates: schema, symbols/timeframes, feature "
            "existence, causality (no_future_data), complexity budget, canonical dedup. "
            "Any failure here REJECTS the candidate before it is ever tested.\n"
            "  3. BACKTEST - deterministic backtest over historical bars.\n"
            "  4. WALK-FORWARD - rolling window evaluation (train/validate/roll).\n"
            "  5. OOS - out-of-sample evaluation on data never seen during tuning.\n"
            "  6. ROBUSTNESS - stability under parameter/sample perturbation.\n"
            "  7. SCORE - weighted selection score across research/oos/robustness/"
            "consistency/complexity/sample/regime/drawdown dimensions.\n"
            "  8. RANK - 9 rank dimensions with explainable components.\n"
            "  9. ELITE - only VALIDATED candidates with final_score >= 0.60 enter the "
            "elite pool (bounded by elite_size).\n"
            " 10. EVOLVE - the next generation preserves elites and mutates/crosses/"
            "explores around them.\n"
            "\n" + benchmark_note + "\n"
            "HARD RULES:\n"
            "1. Use ONLY features from the approved catalog below. NEVER invent indicators.\n"
            "2. Every strategy MUST declare no_future_data: true - signals are computed on "
            "CLOSED bars only. Never reference the current forming bar high/low/close.\n"
            "3. Prefer simple, robust, generalizable logic over optimized complexity. A "
            "strategy with 20 out-of-sample trades and clean robustness beats a curve-fit "
            "200-trade monster.\n"
            "4. Stay within the complexity budget: at most "
            + str(max_cond)
            + " conditions, "
            + str(max_feat)
            + " distinct features, "
            + str(max_tf)
            + " timeframe(s).\n"
            "5. Choose ONE strategy family per strategy and align the entry logic with it.\n"
            "6. Your output is DATA. It will be validated by strict deterministic gates and "
            "any invalid strategy is rejected before it is ever tested.\n"
            "7. Diversify: cover DIFFERENT families and mechanisms - do not emit near-identical "
            "variations of a single idea.\n"
            f"Approved feature catalog ({len(context.get('feature_ids') or [])}): {feature_list}\n"
            f"Supported timeframes: {timeframes}\n"
            f"Supported symbols: {symbols}\n"
            f"Complexity limits: max conditions {max_cond}, max features {max_feat}, "
            f"max timeframes {max_tf}\n"
            f"DSL schema (JSON envelope): {schema_fields}\n"
            'Return ONLY a JSON object: {"strategies": [ {dsl-object}, ... ]}. '
            "No markdown, no prose, no code fences."
        )
        user = "Generate exactly " + str(n) + " DISTINCT strategies covering DIFFERENT families.\n"
        if context.get("research_memory"):
            user += (
                "Research memory from previous generations (learn from it, do not repeat "
                "what already failed):\n" + str(context["research_memory"]) + "\n"
            )
        if context.get("generation_objective"):
            user += "Generation objective: " + str(context["generation_objective"]) + "\n"
        user += (
            "Important: do NOT emit dozens of near-identical variations of one strategy. "
            "Diversify across families and mechanisms. Output ONLY the JSON object."
        )
        return system, user


__all__ = ["LLM_API_KEY_SECRET", "LLMGenerationProvider", "ProviderUsage"]
