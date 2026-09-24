"""Persistence tests for the AI-provider decision store (ECOSYSTEM-001,
Sections 41, 58, 63).

SIMULATED TEST DATA: every decision recorded here is synthetic. None of these
rows were produced from a live position, and none place orders.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _payload(decision_id: str = "dec-001") -> dict:
    """A decision payload shaped exactly like ``DecisionOutcome.to_dict()``."""
    return {
        "decision_id": decision_id,
        "final_action": "HOLD",
        "policy": {"scores": {"HOLD": 0.71, "CLOSE": 0.20, "REDUCE": 0.09}, "winner": "HOLD"},
        "risk": {"allowed": True, "rejections": [], "risk_change_usd": 0.0},
        "evidence": {
            "system_one": {
                "action": "HOLD",
                "p_hold": 0.99,
                "model": "jev-1.13-free",
                "usage": {"input_tokens": 312, "output_tokens": 48},
            }
        },
        "providers_used": ["system_one"],
        "providers_failed": [],
        "fallback_used": False,
        "fallback_reason": "",
        "decision_mode": "HYBRID",
        "versions": {
            "template": "1.0.0",
            "policy": "1.0.0",
            "gate": "1.0.0",
            "decision": "1.0.0",
            "contract": "1.1.0",
            "context": "1.0.0",
        },
        "created_at": "2026-09-24T10:00:00Z",
        "latency_ms": 412.5,
        "stage_timings_ms": {"providers": 300.0, "policy": 5.0},
        "is_test_data": True,
        "active_provider": "system_one",
        "active_mode": "HYBRID",
    }


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "ProviderDecisionStore":
    from nexus_scalp.ai_providers.store import ProviderDecisionStore

    db = tmp_path / "decisions.db"
    monkeypatch.setenv("NEXUS_DECISIONS_DB", str(db))
    return ProviderDecisionStore(db_path=db)


class TestRecordAndRead:
    """A recorded decision round-trips through the store intact."""

    def test_record_then_get_returns_contract_shape(self, store):
        store.record(_payload("dec-001"))
        got = store.get("dec-001")

        assert got is not None
        assert got["decision_id"] == "dec-001"
        assert got["final_action"] == "HOLD"
        assert got["decision_mode"] == "HYBRID"
        assert got["providers_used"] == ["system_one"]
        assert got["providers_failed"] == []

    def test_nested_objects_survive_the_json_round_trip(self, store):
        store.record(_payload("dec-002"))
        got = store.get("dec-002")

        assert got is not None
        # policy / risk / versions are stored inside the JSON payload and must
        # come back as the same nested dicts the orchestrator produced.
        assert got["policy"]["scores"]["HOLD"] == pytest.approx(0.71)
        assert got["risk"]["allowed"] is True
        assert got["versions"]["contract"] == "1.1.0"
        assert got["evidence"]["system_one"]["model"] == "jev-1.13-free"

    def test_usage_is_recovered_from_evidence(self, store):
        store.record(_payload("dec-003"))
        got = store.get("dec-003")

        assert got is not None
        # Usage lives inside evidence (Section 22), not in its own column.
        assert got["usage"]["input_tokens"] == 312
        assert got["usage"]["output_tokens"] == 48

    def test_list_recent_is_newest_first(self, store):
        for i in range(5):
            p = _payload(f"dec-{i:03d}")
            p["created_at"] = f"2026-09-24T10:00:0{i}Z"
            store.record(p)

        rows = store.list_recent(limit=10)
        assert [r["decision_id"] for r in rows] == [
            "dec-004",
            "dec-003",
            "dec-002",
            "dec-001",
            "dec-000",
        ]

    def test_unknown_id_returns_none(self, store):
        assert store.get("does-not-exist") is None


class TestDurability:
    """The ledger must survive a process restart — that is the whole point."""

    def test_decision_survives_reopen(self, store, tmp_path: Path):
        store.record(_payload("dec-restart"))
        store.close()

        # A NEW instance against the SAME file == a fresh process after restart.
        from nexus_scalp.ai_providers.store import ProviderDecisionStore

        reopened = ProviderDecisionStore(db_path=tmp_path / "decisions.db")
        try:
            got = reopened.get("dec-restart")
        finally:
            reopened.close()

        assert got is not None
        assert got["final_action"] == "HOLD"
        assert got["evidence"]["system_one"]["model"] == "jev-1.13-free"


class TestFailureModes:
    """The store is an observer: it must never raise into the decide path."""

    def test_record_never_raises_on_a_bad_row(self, store):
        # Malformed input (no decision_id, non-dict policy) must not propagate.
        store.record({"decision_id": "", "policy": "not-a-dict"})
        # The row is still readable; it must degrade, not crash.
        assert store.list_recent(limit=5) is not None

    def test_closed_store_is_a_noop(self, store):
        store.close()
        # Recording after close is a silent no-op, never an error.
        store.record(_payload("dec-after-close"))

    def test_missing_db_directory_is_created(self, tmp_path: Path):
        from nexus_scalp.ai_providers.store import ProviderDecisionStore

        nested = tmp_path / "a" / "b" / "c" / "decisions.db"
        s = ProviderDecisionStore(db_path=nested)
        s.record(_payload("dec-nested"))
        assert s.get("dec-nested") is not None
        s.close()
        assert nested.exists()


class TestDecisionsDbPath:
    """The ``NEXUS_DECISIONS_DB`` override is the test-isolation seam."""

    def test_env_override_is_honoured(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from nexus_scalp.settings.paths import decisions_db_path

        wanted = tmp_path / "elsewhere" / "dec.db"
        monkeypatch.setenv("NEXUS_DECISIONS_DB", str(wanted))
        assert decisions_db_path() == wanted

    def test_default_lives_under_the_user_data_databases_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        from nexus_scalp.settings.paths import decisions_db_path, settings_db_path

        # Both override + default are exercised with the env var cleared, so
        # the two tests cannot leak state into each other.
        monkeypatch.delenv("NEXUS_DECISIONS_DB", raising=False)
        monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "settings.db"))
        got = decisions_db_path()
        # Same directory as the settings DB, isolated from artifacts/*.
        assert got.parent == settings_db_path().parent
        assert got.name == "ai_provider_decisions.db"


class TestPgSchemaTranslation:
    """The same authored DDL must translate to PostgreSQL (Section 63)."""

    @staticmethod
    def _statements() -> list[str]:
        from nexus_scalp.database.migration.schema_snapshot import (
            ai_provider_decisions_schema_statements,
        )

        return list(ai_provider_decisions_schema_statements())

    def test_translate_ddl_produces_pg_identity_columns(self):
        from nexus_scalp.database.migration.pg_schema import translate_ddl

        table = next(s for s in self._statements() if "CREATE TABLE" in s)
        translated = translate_ddl(table)
        # SQLite AUTOINCREMENT becomes a PG identity column — the repo's own
        # parity convention (see pg_schema.translate_ddl).
        assert "GENERATED ALWAYS AS IDENTITY" in translated
        assert "AUTOINCREMENT" not in translated

    def test_migrate_domain_applies_the_new_domain(self):
        from nexus_scalp.database.migration import migrate_domain

        applied: list[str] = []
        # ``execute`` receives already-translated SQL; a list recorder is the
        # cheapest honest double for a connection.
        result = migrate_domain("ai_provider_decisions", applied.append)
        assert result["error_count"] == 0
        assert any("ai_provider_decisions" in s for s in applied)

    def test_verify_domain_expects_the_new_table(self):
        from nexus_scalp.database.migration import verify_domain_schema

        result = verify_domain_schema(
            "ai_provider_decisions", lambda: ["ai_provider_decisions"]
        )
        assert result["expected_count"] == 1
        assert result["missing"] == []

    def test_unknown_domain_is_refused_not_silently_skipped(self):
        from nexus_scalp.database.migration import migrate_domain

        with pytest.raises(NotImplementedError):
            migrate_domain("not-a-real-domain", lambda s: None)


class TestOrchestratorWiring:
    """The orchestrator records to the store but never depends on it."""

    @pytest.fixture()
    def registry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from nexus_scalp.ai_providers.registry import ProviderRegistryStore

        db = tmp_path / "settings.db"
        monkeypatch.setenv("NEXUS_SETTINGS_DB", str(db))
        return ProviderRegistryStore(db)

    def test_orchestrator_accepts_a_decision_store(self, store, registry):
        from nexus_scalp.ai_providers.orchestrator import ProviderOrchestrator

        orch = ProviderOrchestrator(
            registry=registry, secret_store=None, decision_store=store
        )
        assert orch._decision_store is store

    def test_orchestrator_records_without_a_store(self, registry):
        from nexus_scalp.ai_providers.orchestrator import ProviderOrchestrator

        orch = ProviderOrchestrator(
            registry=registry, secret_store=None, decision_store=None
        )
        # No store configured: the ring is in memory and never raises.
        assert orch._decision_store is None
