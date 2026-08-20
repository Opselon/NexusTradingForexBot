"""
STRATEGY FACTORY — LLM PROVIDER + SETTINGS + STORE PORTABILITY SUITE
====================================================================
2026-08-20 (Strategy-AI wiring task).

Covers the newly wired assisted-generation path end to end:

  * settings layer: factory.llm_* keys, encrypted API key in the OS secret
    store (DPAPI/ACL), base-url/model/temperature in the settings DB, masked
    UI status (never plaintext), hot-rebuild config reader;
  * provider: strong v2 prompt construction (catalog-exact, causality,
    complexity budget, JSON envelope), response parsing (markdown fences,
    prose, SSE `data: [DONE]` leftover), budget/usage ledger, failure
    isolation (never raises, NEVER fabricates DSLs);
  * orchestrator: LLM-assisted slice in Generation-0 populations (source==LLM
    isolation), provider usage persistence, deterministic fallback when the
    provider is unconfigured or fails;
  * store: DB-portable write/read round-trip on SQLite AND PostgreSQL
    (psycopg driver) — factory research memory survives a provider switch
    (DATABASE PORTABILITY + factory spec 38/41).

Safety contract (spec 9/34/69/70/90): every LLM candidate is UNTRUSTED INPUT
that must pass the SAME deterministic structural gates; the provider never
computes performance; generated strategies never go live.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
    ExecutionContext,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    OutcomeDecomposition,
    PositionBehavior,
    StrategyContext,
)
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.dataset import ResearchDatasetBuilder
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.strategies.factory.dsl import canonicalize_dsl
from nexus_scalp.strategies.factory.models import (
    EvolutionConfig,
    FactoryCandidate,
    StrategyFamily,
)
from nexus_scalp.strategies.factory.orchestrator import StrategyFactory
from nexus_scalp.strategies.factory.provider import (
    LLMGenerationProvider,
    PROMPT_VERSION,
    ProviderUsage,
)
from nexus_scalp.strategies.factory.store import provider_usage_total, record_provider_usage
from nexus_scalp.strategies.factory.validators import validate_candidate
from nexus_scalp.settings.secret_store import SecureSecretStore
from nexus_scalp.settings.service import SettingsDatabase, SettingsService
from nexus_scalp.strategies.research_store import StrategyResearchStore, default_config

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def audit_repo(tmp_path):
    db_file = tmp_path / "test_strategy_factory_ai.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_file}")
    yield repo
    repo.close()


def flush(repo):
    repo._queue.join()


def make_record(key: str, decision_ts: datetime, regime: str = "TRENDING") -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=f"req_{key}",
        idempotency_key=key,
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=decision_ts,
        strategy_id="strat_research",
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id="strat_research",
            symbol="XAUUSD",
            session="ALL",
            regime=regime,
            volatility_regime="HIGH",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id=CANONICAL_FEATURE_SCHEMA_ID,
            feature_dimension=CANONICAL_FEATURE_DIMENSION,
            values=[0.0] * CANONICAL_FEATURE_DIMENSION,
        ),
        action="BUY_MARKET",
        entry_reason="SMC_GOD_MODE",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
    )


def make_outcome(record: ExperienceRecord, realized_r: float) -> ExperienceOutcome:
    return ExperienceOutcome(
        idempotency_key=record.idempotency_key,
        execution_id=f"ticket_{record.idempotency_key}",
        outcome_timestamp=record.decision_timestamp + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason="TP" if realized_r > 0 else "SL",
        realized_pnl_usd=realized_r * 100.0,
        realized_r_multiple=realized_r,
        approved_volume=0.1,
        behavior=PositionBehavior(
            mfe_r=max(0.5, realized_r) if realized_r > 0 else 0.2,
            mae_r=0.2,
            mae_points=2.0,
            mfe_points=5.0,
            expected_duration_sec=900.0,
            duration_sec=300.0,
        ),
        execution=ExecutionContext(),
        decomposition=OutcomeDecomposition(
            strategy_quality=0.5,
            entry_quality=0.4,
            position_management_quality=0.4,
            exit_quality=0.4,
            execution_quality=0.5,
            final_outcome_r=realized_r,
        ),
        behavioral_flags=[],
    )


def make_factory(repo, size: int = 8) -> tuple[StrategyFactory, ResearchPipeline]:
    ledger = ExperienceLedger(audit_repo=repo)
    registry = StrategyRegistry(audit_repo=repo)
    dataset_builder = ResearchDatasetBuilder(ledger=ledger)
    pipeline = ResearchPipeline(dataset_builder=dataset_builder, registry=registry)
    cfg = EvolutionConfig(generation_size=size, elite_size=3, max_generations=2)
    factory = StrategyFactory(
        audit_repo=repo,
        research_pipeline=pipeline,
        config=cfg,
    )
    return factory, pipeline


@pytest.fixture()
def settings_env(tmp_path: Path):
    svc = SettingsService(
        db=SettingsDatabase(tmp_path / "app_settings.db"),
        secret_store=SecureSecretStore(tmp_path / "secrets"),
    )
    yield svc
    try:
        svc.close()
    except Exception:
        pass


def _llm_dsl(family: str = "TREND_FOLLOWING", feature: str = "norm_rsi") -> dict:
    return {
        "schema_version": "1.0",
        "hypothesis": {
            "statement": "LLM hypothesis",
            "market_mechanism": "m",
            "expected_regime": ["trending"],
            "invalidation": [],
            "abstain_conditions": [],
        },
        "family": family,
        "market": {"symbols": ["XAUUSD"], "timeframes": ["M1"]},
        "context": {},
        "setup": {},
        "entry": {"logic": "x", "confirmation": [feature]},
        "filters": [{"feature": feature, "op": "gt", "value": 0.0}],
        "exit": {"mode": "fixed_rr", "rr": 2.0},
        "risk": {"risk_governance": "global"},
        "constraints": {"no_future_data": True},
    }


def _fake_provider_returning(dsl_dicts: list[dict]) -> LLMGenerationProvider:
    """Provider stub that returns canned DSL dicts (no network)."""
    provider = LLMGenerationProvider(api_base_url="http://x/v1", model="m", api_key="k")

    class _Stub:
        def __init__(self, dsls: list[dict]) -> None:
            self._dsls = dsls

        def generate_dsls(self, context, n: int) -> list[dict]:
            provider.usage.requests += 1
            return list(self._dsls[:n])

    provider.generate_dsls = _Stub(dsl_dicts).generate_dsls  # type: ignore[method-assign]
    return provider


def _candidate_row(candidate_id: str = "SF-test123") -> dict:
    return {
        "candidate_id": candidate_id,
        "definition_hash": "abc123",
        "generation_id": "G1",
        "source": "LLM",
        "operator": "NONE",
        "parent_ids": [],
        "family": "TREND_FOLLOWING",
        "population_index": 0,
        "dsl": {"schema_version": "1.0", "family": "TREND_FOLLOWING"},
        "structural": {"passed": True},
        "lifecycle": "GENERATED",
        "failure_reasons": [],
        "llm_response_id": "resp_xyz",
        "created_at": "2026-08-20T00:00:00+00:00",
    }


def _usage_row() -> dict:
    return {
        "usage_id": "u_test1",
        "generation_id": "G1",
        "requests": 3,
        "failures": 1,
        "prompt_tokens": 100,
        "completion_tokens": 200,
        "total_tokens": 300,
        "estimated_cost_usd": 0.001,
        "last_latency_ms": 55.5,
        "last_error": "",
        "created_at": "2026-08-20T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# 1. Settings layer — encrypted key + UI-changeable config
# ---------------------------------------------------------------------------


def test_factory_llm_config_default_unconfigured(settings_env):
    status = settings_env.factory_llm_config_status()
    assert status["configured"] is False
    assert status["api_key_present"] is False
    assert status["masked_api_key"] == ""
    assert status["source"] == "NOT_CONFIGURED"


def test_factory_llm_config_save_roundtrip(settings_env):
    svc = settings_env
    result = svc.set_factory_llm_config(
        api_key="sk-test-1234567890",
        base_url="http://178.105.20.69:20128/v1",
        model="claude-opus-5",
        temperature=0.7,
        actor="test",
    )
    assert result["success"] is True
    status = svc.factory_llm_config_status()
    assert status["configured"] is True
    assert status["api_key_present"] is True
    assert "sk-test-1234567890" not in str(status)  # masked, never plaintext
    assert status["masked_api_key"].endswith("7890")
    assert status["base_url"] == "http://178.105.20.69:20128/v1"
    assert status["model"] == "claude-opus-5"
    assert status["temperature"] == 0.7


def test_factory_llm_key_encrypted_at_rest(settings_env, tmp_path):
    settings_env.set_factory_llm_config(api_key="sk-super-secret-key", actor="test")
    secrets_file = tmp_path / "secrets" / "secrets.enc"
    raw = secrets_file.read_bytes()
    assert b"sk-super-secret-key" not in raw  # encrypted at rest (DPAPI/ACL)


def test_factory_llm_config_rebuild_reads_persisted(settings_env):
    svc = settings_env
    svc.set_factory_llm_config(
        api_key="sk-rebuild-1",
        base_url="http://localhost:9999/v1",
        model="test-model",
        actor="test",
    )
    cfg = svc.get_factory_llm_config()
    assert cfg["api_key"] == "sk-rebuild-1"
    assert cfg["api_base_url"] == "http://localhost:9999/v1"
    assert cfg["model"] == "test-model"
    provider = LLMGenerationProvider(
        api_base_url=cfg["api_base_url"],
        model=cfg["model"],
        api_key=cfg["api_key"],
        secret_store=svc.secrets,
    )
    assert provider.available() is True
    assert provider.api_base_url == "http://localhost:9999/v1"


def test_factory_llm_mutability_classification(settings_env):
    svc = settings_env
    svc.db.set("factory.llm_api_key", "x")
    svc.db.set("factory.llm_base_url", "http://x/v1")
    svc.db.set("factory.llm_model", "m")
    assert svc.db.get("factory.llm_api_key").mutability == "SECRET"
    assert svc.db.get("factory.llm_base_url").mutability == "HOT_RESTRICTED"
    assert svc.db.get("factory.llm_model").mutability == "HOT_RESTRICTED"


# ---------------------------------------------------------------------------
# 2. Provider — strong prompt + parsing + failure isolation
# ---------------------------------------------------------------------------


def test_provider_prompt_contains_catalog_and_constraints():
    provider = LLMGenerationProvider(
        api_base_url="http://x/v1",
        model="m",
        api_key="k",
        secret_store=None,
    )
    ctx = {
        "feature_ids": ["norm_rsi", "norm_atr_ratio"],
        "timeframes": ["M1", "M5"],
        "symbols": ["XAUUSD"],
        "max_conditions": 9,
        "max_features": 6,
        "max_timeframes": 2,
        "generation_objective": "Grow the elite pool.",
    }
    system, user = provider._build_messages(ctx, 2)
    assert "norm_rsi" in system
    assert "norm_atr_ratio" in system
    assert "no_future_data" in system
    assert "XAUUSD" in system
    assert "9" in system  # complexity budget
    assert "Grow the elite pool." in user
    assert "DISTINCT strategies" in user
    assert '"strategies"' in system


def test_provider_prompt_version_recorded():
    provider = LLMGenerationProvider(api_base_url="http://x/v1", model="m", api_key="k")
    assert provider.prompt_version == PROMPT_VERSION


def test_provider_parses_fenced_json():
    provider = LLMGenerationProvider(api_base_url="http://x/v1", model="m", api_key="k")
    content = '```json\n{"strategies": [{"schema_version": "1.0", "family": "TREND_FOLLOWING"}]}\n```'
    out = provider._extract_dsl_list(content)
    assert len(out) == 1
    assert out[0]["family"] == "TREND_FOLLOWING"


def test_provider_parses_sse_leftover():
    provider = LLMGenerationProvider(api_base_url="http://x/v1", model="m", api_key="k")
    content = '{"strategies": [{"schema_version": "1.0"}]}data: [DONE]'
    out = provider._extract_dsl_list(content)
    assert len(out) == 1
    assert out[0]["schema_version"] == "1.0"


def test_provider_parses_prose_wrapping():
    provider = LLMGenerationProvider(api_base_url="http://x/v1", model="m", api_key="k")
    content = 'Sure here is the JSON:\n{"strategies": [{"schema_version": "1.0", "family": "MEAN_REVERSION"}]}'
    out = provider._extract_dsl_list(content)
    assert len(out) == 1
    assert out[0]["family"] == "MEAN_REVERSION"


def test_provider_never_raises_and_records_failure():
    import sys

    import httpx

    class _RaisingClient:
        def post(self, *a, **k):
            raise httpx.ReadTimeout("boom")

    orig_module = sys.modules.get("httpx")
    fake_httpx = type(sys)("fake_httpx")
    fake_httpx.post = _RaisingClient().post
    sys.modules["httpx"] = fake_httpx  # replace so the lazy import resolves to the fake
    provider = LLMGenerationProvider(api_base_url="http://x/v1", model="m", api_key="k")
    try:
        out = provider.generate_dsls({}, 2)
        assert out == []  # never raises
    finally:
        sys.modules["httpx"] = orig_module
    assert provider.usage.failures == 1
    assert provider.usage.last_error.startswith("NETWORK:")


def test_provider_usage_budget_exhausted():
    provider = LLMGenerationProvider(
        api_base_url="http://x/v1",
        model="m",
        api_key="k",
        max_requests_per_generation=1,
    )
    provider.usage.requests = 1
    assert provider._budget_exhausted() is True
    assert provider.generate_dsls({}, 2) == []  # budget guard, no request
    assert provider.usage.last_error == "request budget exhausted"


# ---------------------------------------------------------------------------
# 3. Orchestrator — LLM slice isolation + usage ledger
# ---------------------------------------------------------------------------


def test_orchestrator_llm_slice_replaces_slot(audit_repo):
    dsl = canonicalize_dsl(_llm_dsl())
    factory = make_factory(audit_repo, size=8)[0]
    factory.provider = _fake_provider_returning([_llm_dsl()])
    population = factory.generate_population("G1")
    llm_rows = [c for c in population if c.source.value == "LLM"]
    assert len(llm_rows) >= 1
    assert llm_rows[0].dsl == dsl
    assert 1 <= len(llm_rows) <= 3  # ~30% of 8 = 2-3 slots


def test_orchestrator_llm_usage_persisted_after_generation(audit_repo):
    factory = make_factory(audit_repo, size=6)[0]
    factory.provider = _fake_provider_returning([])
    factory.generate_population("G1")
    flush(audit_repo)
    total = provider_usage_total(factory._research_backend)
    assert total["requests"] >= 1


def test_orchestrator_deterministic_when_provider_unavailable(audit_repo):
    factory = make_factory(audit_repo, size=8)[0]
    factory.provider = LLMGenerationProvider()  # no key/base/model
    population = factory.generate_population("G1")
    # population may exceed the requested size due to family-coverage
    # injection (_ensure_family_coverage); the key contract is that the
    # deterministic path still generates a full population with NO LLM rows.
    assert len(population) >= 8
    assert all(c.source.value != "LLM" for c in population)


def test_orchestrator_llm_candidates_go_through_validation(audit_repo):
    factory = make_factory(audit_repo, size=8)[0]
    bad_dsl = _llm_dsl(feature="rsi_14")  # INVENTED feature
    factory.provider = _fake_provider_returning([bad_dsl])
    population = factory.generate_population("G1")
    llm_candidates = [c for c in population if c.source.value == "LLM"]
    assert llm_candidates, "LLM candidate must be present for the test to be meaningful"
    verdict = validate_candidate(
        llm_candidates[0],
        budgets=factory._budgets(),
        symbols=factory.symbols,
    )
    assert verdict.passed is False
    assert verdict.failure_reason.value == "UNSUPPORTED_FEATURE"


# ---------------------------------------------------------------------------
# 4. Store portability — SQLite + PostgreSQL round-trip
# ---------------------------------------------------------------------------


def test_store_sqlite_roundtrip(tmp_path):
    store = StrategyResearchStore(default_config(str(tmp_path)))
    store.ensure_schema()
    assert store.upsert_candidate(_candidate_row()) is True
    rows = store.list_candidates()
    assert len(rows) == 1
    assert rows[0]["source"] == "LLM"
    assert rows[0]["llm_response_id"] == "resp_xyz"
    assert rows[0]["dsl"]["family"] == "TREND_FOLLOWING"  # JSON column decoded
    store.close()


def test_store_postgres_roundtrip():
    from nexus_scalp.database.provider import DatabaseProvider
    from nexus_scalp.strategies.research_store import config_for

    try:
        cfg = config_for(DatabaseProvider.POSTGRESQL)
        store = StrategyResearchStore(cfg)
        store.ensure_schema()
    except Exception as e:  # pragma: no cover - optional PG
        pytest.skip(f"PostgreSQL unavailable: {e}")
        return
    try:
        assert store.upsert_candidate(_candidate_row()) is True
        rows = store.list_candidates()
        got = next((r for r in rows if r["candidate_id"] == "SF-test123"), None)
        assert got is not None
        assert got["source"] == "LLM"
        assert got["llm_response_id"] == "resp_xyz"
        assert isinstance(got["dsl"], dict)
        assert got["dsl"]["family"] == "TREND_FOLLOWING"
    finally:
        store.close()


def test_store_provider_usage_roundtrip_sqlite(tmp_path):
    store = StrategyResearchStore(default_config(str(tmp_path)))
    store.ensure_schema()
    assert store.record_provider_usage(_usage_row()) is True
    total = store.provider_usage_total()
    assert total["requests"] == 3
    assert total["total_tokens"] == 300
    store.close()


def test_record_provider_usage_legacy_audit_roundtrip(audit_repo):
    usage = _usage_row()
    usage["usage_id"] = "u_legacy1"
    usage["requests"] = 1
    usage["total_tokens"] = 30
    assert record_provider_usage(audit_repo, usage) is True
    flush(audit_repo)
    total = provider_usage_total(audit_repo)
    assert total["requests"] >= 1


# ---------------------------------------------------------------------------
# 5. Web routes — UI-controllable LLM config
# ---------------------------------------------------------------------------


def test_factory_llm_config_routes_registered():
    from nexus_scalp.web.server import create_app

    app = create_app(engine_ref=None)
    paths: set[str] = set()

    def _walk(routes, depth: int = 0) -> None:
        for r in routes:
            if type(r).__name__ == "_IncludedRouter":
                try:
                    _walk(r.effective_candidates(), depth + 1)
                    continue
                except Exception:
                    pass
            if hasattr(r, "routes") and getattr(r, "routes", None):
                _walk(r.routes, depth + 1)
            p = getattr(r, "path", "") or ""
            if p:
                paths.add(p)

    _walk(app.routes)
    assert "/api/factory/llm-config" in paths, "GET llm-config route missing"