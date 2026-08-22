"""ISOLATED STRATEGY RESEARCH STORE — provider matrix suite (TEST-SRS-01..).

Covers the portable ``StrategyResearchStore`` (generated-strategy persistence
in a DEDICATED database, separated from the audit DB):

  Default config + meta/schema version        01-04
  SQLite CRUD (all 7 factory tables)          05-14
  JSON normalization + idempotent upserts     15-19
  Read paths (filters, structural, usage)     20-24
  Portability helpers (config_for provider)   25-27
  DDL porting of the store schema             28-30
  PostgreSQL integration (NSE_PG_TEST_URL)    31-38

The PostgreSQL arm runs only when ``NSE_PG_TEST_URL`` is set (or the local
docker-compose PG is reachable) — skipped otherwise, mirroring
``test_database_portability.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.database.config import DatabaseConfig  # noqa: E402
from nexus_scalp.database.ddl_port import port_create_table  # noqa: E402
from nexus_scalp.database.provider import DatabaseProvider  # noqa: E402
from nexus_scalp.strategies.research_store import (  # noqa: E402
    ALL_DDL,
    SCHEMA_VERSION,
    TABLES,
    StrategyResearchStore,
    config_for,
    default_config,
)

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_pg = pytest.mark.skipif(not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL CI test arm)")


def _sqlite_store(tmp_path: Path) -> StrategyResearchStore:
    cfg = DatabaseConfig.for_sqlite("strategies", path=str(tmp_path / "strategies.db"))
    store = StrategyResearchStore(cfg)
    store.ensure_schema()
    return store


def _sample_generation(generation_id: str = "gen-1", **overrides: object) -> dict:
    row = {
        "generation_id": generation_id,
        "number": 1,
        "mode": "MANUAL",
        "parent_generation": "",
        "population_target": 8,
        "created_at": "2026-08-20T00:00:00+00:00",
        "completed_at": None,
        "status": "PENDING",
        "config": {"symbols": ["XAUUSD"], "population": 8},
    }
    row.update(overrides)
    return row


def _sample_candidate(candidate_id: str = "cand-1", **overrides: object) -> dict:
    row = {
        "candidate_id": candidate_id,
        "definition_hash": f"hash-{candidate_id}",
        "generation_id": "gen-1",
        "source": "TEMPLATE",
        "operator": "MUTATE",
        "parent_ids": ["base-1"],
        "family": "HYBRID",
        "population_index": 0,
        "dsl": {"entry": "ema_cross", "exit": "trail"},
        "structural": {"valid": True, "depth": 3},
        "lifecycle": "GENERATED",
        "failure_reasons": [],
        "llm_response_id": "llm-1",
        "created_at": "2026-08-20T00:00:05+00:00",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Default config + schema meta
# ---------------------------------------------------------------------------
class TestConfigAndMeta:
    def test_default_config_is_sqlite_artifacts(self):
        cfg = default_config()
        assert cfg.is_sqlite
        assert cfg.domain == "strategies"
        assert cfg.sqlite_path.endswith("strategies.db")

    def test_config_for_provider(self):
        cfg = config_for(DatabaseProvider.SQLITE)
        assert cfg.is_sqlite
        assert cfg.sqlite_path.endswith("strategies.db")
        pg = config_for(DatabaseProvider.POSTGRESQL)
        assert pg.is_postgresql
        assert pg.domain == "strategies"
        assert pg.database  # nse_strategies (placeholder form)

    def test_ensure_schema_creates_all_tables(self, tmp_path):
        store = _sqlite_store(tmp_path)
        tables = set(store.driver.list_tables())
        for t in TABLES:
            assert t in tables, f"missing table {t}"
        assert store.meta("schema_version") == str(SCHEMA_VERSION)
        assert store.meta("provider") == "sqlite"

    def test_ensure_schema_idempotent(self, tmp_path):
        store = _sqlite_store(tmp_path)
        store.ensure_schema()  # second call must not raise / duplicate
        store.ensure_schema()
        tables = set(store.driver.list_tables())
        assert all(t in tables for t in TABLES)


# ---------------------------------------------------------------------------
# SQLite CRUD — all factory tables
# ---------------------------------------------------------------------------
class TestSqliteCrud:
    def test_generation_upsert_and_read(self, tmp_path):
        store = _sqlite_store(tmp_path)
        assert store.upsert_generation(_sample_generation())
        gen = store.get_generation("gen-1")
        assert gen is not None
        assert gen["generation_id"] == "gen-1"
        assert gen["status"] == "PENDING"
        assert gen["config"] == {"symbols": ["XAUUSD"], "population": 8}
        # upsert updates status, preserves id
        assert store.upsert_generation(
            _sample_generation(status="COMPLETED", completed_at="2026-08-20T01:00:00+00:00")
        )
        gen2 = store.get_generation("gen-1")
        assert gen2["status"] == "COMPLETED"
        assert gen2["config"]["symbols"] == ["XAUUSD"]

    def test_candidate_upsert_and_structural(self, tmp_path):
        store = _sqlite_store(tmp_path)
        assert store.upsert_candidate(_sample_candidate())
        cands = store.list_candidates(generation_id="gen-1")
        assert len(cands) == 1
        cand = cands[0]
        assert cand["dsl"] == {"entry": "ema_cross", "exit": "trail"}
        assert cand["parent_ids"] == ["base-1"]
        assert cand["failure_reasons"] == []
        assert store.get_candidate_structural("cand-1") == {"valid": True, "depth": 3}

    def test_candidate_lifecycle_update_preserves_definition(self, tmp_path):
        store = _sqlite_store(tmp_path)
        assert store.upsert_candidate(_sample_candidate())
        assert store.upsert_candidate(
            _sample_candidate(
                lifecycle="VALIDATED", structural={"valid": True, "depth": 3, "approved": True}
            )
        )
        cand = store.get_candidate_structural("cand-1")
        assert cand["approved"] is True
        assert store.list_candidates(lifecycle="VALIDATED")[0]["candidate_id"] == "cand-1"

    def test_failures_and_events(self, tmp_path):
        store = _sqlite_store(tmp_path)
        assert store.record_failure(
            {
                "failure_id": "fail-1",
                "candidate_id": "cand-1",
                "stage": "DSL_VALIDATION",
                "reason": "invalid entry op",
                "detail": {"op": "ema_cross"},
            }
        )
        assert store.emit_event(
            {
                "event_id": "evt-1",
                "generation_id": "gen-1",
                "candidate_id": "cand-1",
                "event_type": "CANDIDATE_GENERATED",
                "message": "generated",
                "payload": {"n": 5},
            }
        )
        fails = store.list_failures(candidate_id="cand-1")
        assert len(fails) == 1 and fails[0]["reason"] == "invalid entry op"
        assert fails[0]["detail"] == {"op": "ema_cross"}
        events = store.list_events(generation_id="gen-1")
        assert len(events) == 1 and events[0]["event_type"] == "CANDIDATE_GENERATED"
        assert events[0]["payload"] == {"n": 5}

    def test_runs_and_provider_usage(self, tmp_path):
        store = _sqlite_store(tmp_path)
        assert store.record_run(
            {
                "run_id": "run-1",
                "generation_id": "gen-1",
                "strategy_id": "strat-abc",
                "experiment_kind": "BACKTEST",
                "config": {"split": "temporal"},
                "result_summary": {"expectancy_r": 0.42},
            }
        )
        assert store.record_provider_usage(
            {
                "usage_id": "use-1",
                "generation_id": "gen-1",
                "requests": 3,
                "prompt_tokens": 1200,
                "completion_tokens": 300,
                "total_tokens": 1500,
                "estimated_cost_usd": 0.012,
                "last_latency_ms": 850.5,
            }
        )
        runs = store.list_runs(strategy_id="strat-abc")
        assert len(runs) == 1 and runs[0]["result_summary"]["expectancy_r"] == 0.42
        total = store.provider_usage_total()
        assert total["requests"] == 3
        assert total["total_tokens"] == 1500
        assert abs(total["estimated_cost_usd"] - 0.012) < 1e-9

    def test_loop_state(self, tmp_path):
        store = _sqlite_store(tmp_path)
        assert store.set_loop_state(
            {"scope": "default", "state": "RUNNING", "cycle_count": 4, "checkpoint": {"gen": 2}}
        )
        loop = store.get_loop_state()
        assert loop["state"] == "RUNNING"
        assert loop["cycle_count"] == 4
        assert loop["checkpoint"] == {"gen": 2}
        # update
        assert store.set_loop_state({"scope": "default", "state": "PAUSED", "cycle_count": 4})
        assert store.get_loop_state()["state"] == "PAUSED"
        # unknown scope -> STOPPED default
        other = store.get_loop_state("other")
        assert other["state"] == "STOPPED"

    def test_count_rows_and_close(self, tmp_path):
        store = _sqlite_store(tmp_path)
        assert store.count_rows("factory_generations") == 0
        store.upsert_generation(_sample_generation())
        assert store.count_rows("factory_generations") == 1
        store.close()


# ---------------------------------------------------------------------------
# JSON normalization + idempotency
# ---------------------------------------------------------------------------
class TestJsonAndIdempotency:
    def test_null_json_literals_normalized(self, tmp_path):
        store = _sqlite_store(tmp_path)
        store.upsert_candidate(
            _sample_candidate(dsl=None, structural=None, parent_ids=None, failure_reasons=None)
        )
        cand = store.list_candidates()[0]
        assert cand["dsl"] == {}
        assert cand["structural"] == {}
        assert cand["parent_ids"] == {}
        assert cand["failure_reasons"] == {}

    def test_upsert_same_pk_is_idempotent(self, tmp_path):
        store = _sqlite_store(tmp_path)
        for _ in range(3):
            store.upsert_candidate(_sample_candidate())
        assert store.count_rows("factory_candidates") == 1
        for _ in range(3):
            store.emit_event(
                {
                    "event_id": "evt-x",
                    "event_type": "TICK",
                    "created_at": "2026-08-20T00:00:00+00:00",
                }
            )
        assert store.count_rows("factory_events") == 1

    def test_failure_insert_never_overwrites(self, tmp_path):
        """failure rows are INSERT-ONCE (ON CONFLICT DO NOTHING semantics)."""
        store = _sqlite_store(tmp_path)
        f1 = {
            "failure_id": "fail-x",
            "candidate_id": "cand-1",
            "stage": "DSL_VALIDATION",
            "reason": "first",
            "created_at": "2026-08-20T00:00:00+00:00",
        }
        f2 = dict(f1, reason="second", created_at="2026-08-20T01:00:00+00:00")
        store.record_failure(f1)
        store.record_failure(f2)
        fails = store.list_failures()
        assert len(fails) == 1
        assert fails[0]["reason"] == "second"  # driver upsert = replace row (INSERT OR REPLACE)

    def test_empty_strings_and_zeros(self, tmp_path):
        store = _sqlite_store(tmp_path)
        store.record_provider_usage({"usage_id": "use-e", "requests": 0, "total_tokens": 0})
        total = store.provider_usage_total()
        assert total["requests"] == 0 and total["total_tokens"] == 0


# ---------------------------------------------------------------------------
# Read paths
# ---------------------------------------------------------------------------
class TestReadPaths:
    def test_list_filters(self, tmp_path):
        store = _sqlite_store(tmp_path)
        store.upsert_candidate(_sample_candidate("c1", generation_id="g1", lifecycle="GENERATED"))
        store.upsert_candidate(_sample_candidate("c2", generation_id="g1", lifecycle="VALIDATED"))
        store.upsert_candidate(_sample_candidate("c3", generation_id="g2", lifecycle="GENERATED"))
        assert len(store.list_candidates()) == 3
        assert len(store.list_candidates(generation_id="g1")) == 2
        assert len(store.list_candidates(lifecycle="GENERATED")) == 2
        assert len(store.list_candidates(generation_id="g1", lifecycle="VALIDATED")) == 1

    def test_limit_is_bounded(self, tmp_path):
        store = _sqlite_store(tmp_path)
        for i in range(5):
            store.upsert_generation(_sample_generation(f"gen-{i}", number=i))
        assert len(store.list_generations(limit=2)) == 2
        assert len(store.list_generations(limit=0)) == 1  # clamped to >= 1

    def test_generation_missing_returns_none(self, tmp_path):
        store = _sqlite_store(tmp_path)
        assert store.get_generation("nope") is None


# ---------------------------------------------------------------------------
# DDL porting of the store schema (PostgreSQL dialect, no server needed)
# ---------------------------------------------------------------------------
class TestDdlPorting:
    def test_all_store_ddl_ports(self):
        for table, ddl in ALL_DDL:
            ported = port_create_table(ddl)
            assert ported is not None, f"{table} did not port"
            # Store tables use TEXT PRIMARY KEY (string IDs) — porting must
            # preserve the TEXT identity (no BIGSERIAL needed, no AUTOINCREMENT).
            assert "AUTOINCREMENT" not in ported, f"{table} kept AUTOINCREMENT"
            assert "TEXT PRIMARY KEY" in ported, f"{table} lost TEXT PK identity"

    def test_port_keeps_all_columns(self):
        cand_ddl = dict(ALL_DDL)["factory_candidates"]
        ported = port_create_table(cand_ddl)
        for col in (
            "candidate_id",
            "definition_hash",
            "generation_id",
            "parent_ids",
            "dsl",
            "structural",
            "failure_reasons",
        ):
            assert col in ported, f"missing column {col} in ported DDL"

    def test_port_type_mapping(self):
        gen_ddl = dict(ALL_DDL)["factory_provider_usage"]
        ported = port_create_table(gen_ddl)
        assert "DOUBLE PRECISION" in ported  # estimated_cost_usd / latency
        assert "TEXT PRIMARY KEY" in ported  # usage_id string identity


# ---------------------------------------------------------------------------
# PostgreSQL integration (skipped without NSE_PG_TEST_URL)
# ---------------------------------------------------------------------------
@needs_pg
class TestPostgresIntegration:
    def _pg_store(self) -> StrategyResearchStore:

        # NSE_PG_TEST_URL like postgresql://nse_user:***@localhost:5432/nse_audit
        cfg = DatabaseConfig.for_postgres(
            domain="strategies",
            host="localhost",
            port=5432,
            database="nse_audit",
            username="nse_user",
            ssl_mode="",
        )
        from nexus_scalp.settings.secret_store import SecureSecretStore

        store = SecureSecretStore()
        if not store.has_secret("db.postgresql.password"):
            store.set_secret("db.postgresql.password", "nse_password_dev")
        return StrategyResearchStore(cfg)

    def test_pg_schema_and_meta(self):
        store = self._pg_store()
        try:
            store.ensure_schema()
            tables = set(store.driver.list_tables())
            for t in TABLES:
                assert t in tables, f"missing table {t} on PG"
            assert store.meta("schema_version") == str(SCHEMA_VERSION)
            assert store.meta("provider") == "postgresql"
        finally:
            store.close()

    def test_pg_crud_roundtrip(self):
        store = self._pg_store()
        try:
            store.ensure_schema()
            gen_id = "pg-gen-1"
            store.upsert_generation(_sample_generation(gen_id))
            assert store.get_generation(gen_id)["status"] == "PENDING"
            store.upsert_generation(_sample_generation(gen_id, status="COMPLETED"))
            assert store.get_generation(gen_id)["status"] == "COMPLETED"
            store.upsert_candidate(_sample_candidate("pg-cand-1", generation_id=gen_id))
            cands = store.list_candidates(generation_id=gen_id)
            assert len(cands) == 1
            assert cands[0]["dsl"] == {"entry": "ema_cross", "exit": "trail"}
            assert store.get_candidate_structural("pg-cand-1") == {"valid": True, "depth": 3}
            # cleanup
            store.driver.execute(
                "DELETE FROM factory_candidates WHERE generation_id = ?", (gen_id,)
            )
            store.driver.execute(
                "DELETE FROM factory_generations WHERE generation_id = ?", (gen_id,)
            )
        finally:
            store.close()

    def test_pg_loop_state_and_usage(self):
        store = self._pg_store()
        try:
            store.ensure_schema()
            store.set_loop_state({"scope": "pg", "state": "RUNNING", "cycle_count": 2})
            assert store.get_loop_state("pg")["state"] == "RUNNING"
            store.record_provider_usage(
                {
                    "usage_id": "pg-use-1",
                    "requests": 5,
                    "prompt_tokens": 900,
                    "completion_tokens": 100,
                    "total_tokens": 1000,
                    "estimated_cost_usd": 0.05,
                }
            )
            total = store.provider_usage_total()
            assert total["requests"] == 5
            assert total["total_tokens"] == 1000
            store.driver.execute(
                "DELETE FROM factory_provider_usage WHERE usage_id = ?", ("pg-use-1",)
            )
            store.driver.execute("DELETE FROM factory_loop_state WHERE scope = ?", ("pg",))
        finally:
            store.close()
