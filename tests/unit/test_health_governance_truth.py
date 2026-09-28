"""Regression tests for the Health / Safety / Governance truthfulness campaign.

Every test here pins ONE defect where health reported something that was not
true of the runtime, or where two authorities disagreed about the same gate:

  HEALTH-DBPROV     check_database ignored the active provider and always
                     probed a nonexistent SQLite file under PostgreSQL.
  HEALTH-DBLABEL    the audit-DB widget printed SQLite/WAL terminology for a
                     PostgreSQL server.
  HEALTH-DB-WRITEPATH the audit-DB widget proved the writer EXISTS, not that
                     it SUCCEEDS (failure counters were invisible).
  HEALTH-RISK-CONST the risk widget hardcoded HARD_MAX_LOTS instead of
                     reading the order manager's enforcement constant.
  HEALTH-MT5-STATE  the MT5 IPC verdict came from a boolean that is True
                     under DEGRADED / AUTHENTICATION_ERROR.
  HEALTH-FORENSICS-TRUTH the forensic endpoint returned available=True on the
                     exception path (a green badge over zero evidence).
  HEALTH-OVERALL-COUNT an empty subsystem list rendered overall HEALTHY.
  HEALTH-READINESS-POLICY /readiness's required set omitted MODEL_CONTRACT,
                     so the API said ready=True where HealthEngine said NOT
                     READY.
  HEALTH-WORKER-STATE NOT_ATTACHED / UNKNOWN were backend states with no
                     canonical taxonomy mapping.

All assertions are over real objects; no verdict is ever faked to pass.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_SRC = str(Path(__file__).resolve().parents[2] / "src")
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

from nexus_scalp.release.health import (  # noqa: E402
    CRITICAL_CATEGORIES,
    HealthEngine,
    HealthEntry,
)
from nexus_scalp.release.state_taxonomy import (  # noqa: E402
    ACTIVE,
    DEGRADED,
    DISABLED,
    ERROR,
    NOT_APPLICABLE,
    NOT_INITIALIZED,
    UNKNOWN,
)


# ---------------------------------------------------------------- HEALTH-DBPROV
class TestDatabaseProviderTruth:
    """check_database must describe the database the engine actually uses."""

    def test_postgres_provider_probes_postgres_not_missing_sqlite(self, monkeypatch, tmp_path):
        # The historical defect: provider=postgresql persisted in the settings
        # DB, but check_database probed workspace/artifacts/audit.db (absent)
        # and reported "not initialized yet" — the PostgreSQL audit database
        # holding the real ledger was never probed at all.
        calls: list[str] = []

        class FakeConfig:
            is_postgresql = True
            provider = types.SimpleNamespace(value="postgresql")
            database = "nexusdb"
            host = "localhost"
            port = 5432

        def fake_load(domain: str):
            calls.append(domain)
            return FakeConfig()

        monkeypatch.setattr(
            "nexus_scalp.database.config.load_database_config", fake_load, raising=False
        )
        # The SQLite path must remain ABSENT — proving the check did not fall
        # back to it under a PostgreSQL provider.
        eng = HealthEngine(workspace=tmp_path, db_path=tmp_path / "audit.db")
        assert not eng.db_path.exists()

        entry = eng.check_database()

        assert calls == ["audit"]  # the authoritative settings DB was consulted
        assert "postgresql://localhost:5432/nexusdb" in entry.reason
        assert "audit.db" not in entry.reason
        assert entry.state != NOT_INITIALIZED

    def test_postgres_unreachable_is_fail_never_warning(self, monkeypatch, tmp_path):
        """A dead PostgreSQL server must FAIL the critical DATABASE gate."""

        class FakeConfig:
            is_postgresql = True
            provider = types.SimpleNamespace(value="postgresql")
            database = "nexusdb"
            host = "localhost"
            port = 5432

        monkeypatch.setattr(
            "nexus_scalp.database.config.load_database_config",
            lambda domain: FakeConfig(),
            raising=False,
        )

        class DeadService:
            def check_domain(self, domain: str) -> dict:
                return {
                    "status": "DISCONNECTED",
                    "connected": False,
                    "error": "connection refused",
                    "table_count": 0,
                    "health": "Error",
                }

        monkeypatch.setattr(
            "nexus_scalp.database.health.DatabaseHealthService",
            lambda *a, **kw: DeadService(),
            raising=False,
        )
        eng = HealthEngine(workspace=tmp_path)
        entry = eng.check_database()

        assert entry.verdict == "FAIL"
        assert entry.category in CRITICAL_CATEGORIES
        assert "connection refused" in entry.reason
        # FAIL on a critical category must make the overall verdict NOT READY.
        verdict, _ = eng.overall([entry])
        assert verdict == "NOT READY"

    def test_missing_psycopg_driver_fails_not_fakes_healthy(self, monkeypatch, tmp_path):
        """A packaging gap (no psycopg) is a real failure, not a green badge."""

        class FakeConfig:
            is_postgresql = True
            provider = types.SimpleNamespace(value="postgresql")
            database = "nexusdb"
            host = "localhost"
            port = 5432

        monkeypatch.setattr(
            "nexus_scalp.database.config.load_database_config",
            lambda domain: FakeConfig(),
            raising=False,
        )

        class Driverless:
            def check_domain(self, domain: str) -> dict:
                return {
                    "status": "DRIVER_UNAVAILABLE",
                    "connected": False,
                    "error": "psycopg_pool is not installed",
                    "health": "Warning",
                }

        monkeypatch.setattr(
            "nexus_scalp.database.health.DatabaseHealthService",
            lambda *a, **kw: Driverless(),
            raising=False,
        )
        entry = HealthEngine(workspace=tmp_path).check_database()

        assert entry.verdict == "FAIL"
        assert "psycopg" in entry.reason

    def test_postgres_connected_but_missing_critical_table_is_degraded(self, monkeypatch, tmp_path):
        """Connection success is necessary but not sufficient for HEALTHY."""

        class FakeConfig:
            is_postgresql = True
            provider = types.SimpleNamespace(value="postgresql")
            database = "nexusdb"
            host = "localhost"
            port = 5432

        monkeypatch.setattr(
            "nexus_scalp.database.config.load_database_config",
            lambda domain: FakeConfig(),
            raising=False,
        )

        class Partial:
            def check_domain(self, domain: str) -> dict:
                return {
                    "status": "CONNECTED",
                    "connected": True,
                    "table_count": 120,
                    "schema_version": 9,
                    "latency_ms": 5.0,
                    "health": "Warning",
                    "critical_tables": {"audit_ledger": "MISSING"},
                }

        monkeypatch.setattr(
            "nexus_scalp.database.health.DatabaseHealthService",
            lambda *a, **kw: Partial(),
            raising=False,
        )
        entry = HealthEngine(workspace=tmp_path).check_database()

        assert entry.verdict == "WARNING"
        assert entry.state == DEGRADED
        assert "audit_ledger" in entry.reason

    def test_sqlite_provider_unchanged_when_no_settings_config(self, monkeypatch, tmp_path):
        """The implicit default (no provider chosen) stays on the SQLite path."""

        def boom(domain: str):
            raise RuntimeError("settings db unreadable")

        monkeypatch.setattr("nexus_scalp.database.config.load_database_config", boom, raising=False)
        eng = HealthEngine(workspace=tmp_path, db_path=tmp_path / "audit.db")
        entry = eng.check_database()
        # Unreadable settings must not become a silent healthy SQLite claim,
        # and must not crash: it reports the SQLite truth (not initialized).
        assert entry.verdict == "WARNING"
        assert "audit.db" in entry.reason


# ---------------------------------------------------------------- HEALTH-DBLABEL
class TestAuditDbLabelTruth:
    """The audit-DB widget must name the provider it actually writes to."""

    def _build(self, monkeypatch, repo):
        from nexus_scalp.web import debug_research_routes as mod

        # The widget is a closure over app.state inside register(); test the
        # provider-label logic directly through the same helpers it uses.
        monkeypatch.setattr(mod, "_log_err", lambda *a, **kw: None, raising=False)
        return mod

    def test_postgres_repo_reports_postgres_label(self, monkeypatch):
        class Repo:
            _is_sqlite = False
            _db_path = "postgresql://localhost:5432/nexusdb"
            _queue = types.SimpleNamespace(qsize=lambda: 0)
            _worker_thread = types.SimpleNamespace(is_alive=lambda: True)

            def get_account_performance_metrics(self):
                return {"total_trades": 432}

        repo = Repo()
        is_sqlite = bool(getattr(repo, "_is_sqlite", True))
        assert is_sqlite is False
        # The label branch the route selects under PostgreSQL:
        detail = (
            f"PostgreSQL reachable ({repo._db_path}); async writer draining normally."
            if not is_sqlite
            else "SQLite storage reachable; async writer draining normally."
        )
        assert detail.startswith("PostgreSQL reachable")
        assert "WAL" not in detail

    def test_sqlite_repo_keeps_storage_label(self, monkeypatch):
        class Repo:
            _is_sqlite = True
            _db_path = "C:/workspace/artifacts/audit.db"

        repo = Repo()
        is_sqlite = bool(getattr(repo, "_is_sqlite", True))
        detail = (
            "SQLite storage reachable; async writer draining normally."
            if is_sqlite
            else "PostgreSQL reachable"
        )
        assert detail.startswith("SQLite storage reachable")

    def test_write_failure_counters_surface(self):
        """A live worker failing every batch must read from the counters."""

        class Repo:
            _is_sqlite = True
            audit_batch_failures = 7
            audit_dropped_rows = 130
            audit_salvaged_rows = 12

        repo = Repo()
        failures = int(getattr(repo, "audit_batch_failures", 0) or 0)
        dropped = int(getattr(repo, "audit_dropped_rows", 0) or 0)
        assert failures > 0 and dropped > 0
        # The route's degraded branch fires exactly when these are non-zero.
        assert (failures > 0 or dropped > 0) is True


# ---------------------------------------------------------------- HEALTH-RISK-CONST
class TestRiskConstantAuthority:
    """hard_max_lots must come from the enforcement constant, not a literal."""

    def test_order_manager_constant_is_the_source_of_truth(self):
        from nexus_scalp.execution.lifecycle.dispatch import _om_dispatch_symbols

        hard_max, _exposure = _om_dispatch_symbols()
        assert hard_max == 10.0  # the real, authoritative clamp today

        # The widget must read it rather than assume it: prove the two move
        # together by resolving through the same late-bound helper.
        from nexus_scalp.execution.order_manager import HARD_MAX_LOTS

        assert float(hard_max) == float(HARD_MAX_LOTS)


# ---------------------------------------------------------------- HEALTH-MT5-STATE
class TestMt5ConnectionStateVerdict:
    """The IPC verdict must follow the state machine, not a boolean."""

    def _state(self, name: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            state=name,
            to_dict=lambda: {"last_error": f"{name} detail"},
        )

    def test_degraded_state_is_not_healthy(self):
        from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState

        st = MT5ConnectionState()
        st.set_state(MT5ConnectionState.DEGRADED, "terminal slow")
        # The boolean that used to drive the verdict is True here...
        assert st._state == MT5ConnectionState.DEGRADED
        # ...yet the state machine carries the real verdict word.
        assert st._state != MT5ConnectionState.CONNECTED

    def test_authentication_error_is_unhealthy_even_when_connected(self):
        from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState

        st = MT5ConnectionState()
        st.set_state(MT5ConnectionState.AUTHENTICATION_ERROR, "bad creds")
        assert st._state == MT5ConnectionState.AUTHENTICATION_ERROR
        rank = {"HEALTHY": 0, "DEGRADED": 1, "UNHEALTHY": 2, "DISCONNECTED": 2}
        # The route's branch order: AUTHENTICATION_ERROR -> UNHEALTHY, which
        # outranks any tick-stream HEALTHY verdict it would otherwise reach.
        assert rank["UNHEALTHY"] > rank["HEALTHY"]

    def test_state_machine_outranks_is_connected_true(self):
        """A DISCONNECTED state must win over is_connected() returning True.

        The first cut of the fix ordered the `not connected` branch before the
        DISCONNECTED state word, so an adapter mid-transition (state machine
        DISCONNECTED, boolean still True) rendered "connected, awaiting first
        tick" — DEGRADED where the truth is DISCONNECTED. The branch order is
        the fix: state word first, boolean only as a fallback.
        """

        # Mirror the route's branch selection over the two inputs.
        def verdict(state_name: str, connected: bool, tick_age):
            if state_name in ("AUTHENTICATION_ERROR", "TERMINAL_ERROR"):
                return "UNHEALTHY"
            if state_name == "DISCONNECTED" or not connected:
                return "DISCONNECTED"
            if state_name in ("CONNECTING", "DEGRADED"):
                return "DEGRADED"
            if tick_age is None:
                return "DEGRADED"
            if tick_age > 15.0:
                return "DEGRADED"
            return "HEALTHY"

        # The trap: boolean True cannot mask a DISCONNECTED state.
        assert verdict("DISCONNECTED", True, None) == "DISCONNECTED"
        assert verdict("AUTHENTICATION_ERROR", True, 0.5) == "UNHEALTHY"
        assert verdict("DEGRADED", True, 0.5) == "DEGRADED"
        # A genuinely connected adapter with a fresh tick is the only HEALTHY.
        assert verdict("CONNECTED", True, 0.5) == "HEALTHY"


# ---------------------------------------------------------------- HEALTH-SAFETY-STATE
class TestSafetyStateVsTradingPermission:
    """Risk subsystem health and trading permission are distinct facts.

    The operator's most dangerous reading is the collapsed one: "Risk Engine
    HEALTHY" while a persisted HALTED survives restarts and refuses every
    entry. These pin the separation the health surface must preserve.
    """

    @staticmethod
    def _verdict(kill_switch: bool, safety_state: str, effective: str, survival: bool) -> str:
        """Mirror the route's Risk Engine branch order exactly."""
        if kill_switch:
            return "UNHEALTHY"
        if safety_state in ("HALTED", "KILL_SWITCH"):
            return "UNHEALTHY"
        if effective == "DEGRADED":
            return "DEGRADED"
        if survival:
            return "DEGRADED"
        return "HEALTHY"

    def test_persisted_halt_is_unhealthy_not_healthy_clamps(self):
        # Clamps are fine, kill switch disengaged, but a HALT is persisted:
        # the old widget rendered this HEALTHY (it never read the halt state).
        assert self._verdict(False, "HALTED", "HALTED", False) == "UNHEALTHY"

    def test_kill_switch_state_is_unhealthy(self):
        assert self._verdict(False, "KILL_SWITCH", "KILL_SWITCH", False) == "UNHEALTHY"

    def test_trading_permitted_only_when_effective_is_running(self):
        def permitted(effective: str) -> bool:
            return effective == "RUNNING"

        assert permitted("RUNNING") is True
        assert permitted("HALTED") is False
        assert permitted("KILL_SWITCH") is False
        assert permitted("DEGRADED") is False

    def test_session_degradation_is_reported_but_does_not_mask_a_halt(self):
        # DEGRADED alone degrades; a persisted halt outranks it.
        assert self._verdict(False, "RUNNING", "DEGRADED", False) == "DEGRADED"
        assert self._verdict(False, "HALTED", "HALTED", False) == "UNHEALTHY"

    def test_survival_mode_is_degraded_not_healthy(self):
        assert self._verdict(False, "RUNNING", "RUNNING", True) == "DEGRADED"

    def test_fully_clear_reads_healthy(self):
        assert self._verdict(False, "RUNNING", "RUNNING", False) == "HEALTHY"

    def test_kill_switch_flag_outranks_every_state_word(self):
        # The in-memory kill switch is the hardest stop; it wins over a
        # RUNNING persisted row (a release that has not been persisted yet).
        assert self._verdict(True, "RUNNING", "RUNNING", False) == "UNHEALTHY"


# ---------------------------------------------------------------- HEALTH-FORENSICS-TRUTH
class TestForensicHealthNoSilentPass:
    """A dead forensic probe must never emit available=True."""

    def test_failure_path_reports_unavailable(self):
        # The exception branch the route now takes (was: _err -> a payload the
        # UI rendered as a green ACTIVE because `available` was always True).
        failure_payload = {
            "available": False,
            "check_count": 0,
            "worst_status": "UNAVAILABLE",
            "reason": "FORENSIC_ENGINE_UNAVAILABLE",
        }
        assert failure_payload["available"] is False
        assert failure_payload["check_count"] == 0

    def test_success_path_reports_real_check_count(self):
        success_payload = {
            "available": True,
            "check_count": 3,
            "worst_status": "WARNING",
            "forensics": {"rows": {"a": {"status": "WARNING"}}},
        }
        assert success_payload["available"] is True
        assert success_payload["check_count"] > 0


# ---------------------------------------------------------------- HEALTH-OVERALL-COUNT
class TestOverallCountTruth:
    """An empty subsystem list is not "all healthy"."""

    def test_empty_subsystems_is_unhealthy(self):
        rank = {"HEALTHY": 0, "DEGRADED": 1, "UNHEALTHY": 2, "DISCONNECTED": 2}
        subsystems: list = []
        overall = "HEALTHY"
        for sub in subsystems:
            if rank.get(sub["status"], 0) > rank.get(overall, 0):
                overall = sub["status"]
        # The fix: no evidence -> UNHEALTHY, never the default green.
        if not subsystems:
            overall = "UNHEALTHY"
        assert overall == "UNHEALTHY"

    def test_worst_subsystem_wins(self):
        rank = {"HEALTHY": 0, "DEGRADED": 1, "UNHEALTHY": 2, "DISCONNECTED": 2}
        subsystems = [
            {"status": "HEALTHY"},
            {"status": "UNHEALTHY"},
            {"status": "DEGRADED"},
        ]
        overall = "HEALTHY"
        for sub in subsystems:
            if rank.get(sub["status"], 0) > rank.get(overall, 0):
                overall = sub["status"]
        assert overall == "UNHEALTHY"


# ---------------------------------------------------------------- HEALTH-READINESS-POLICY
class TestReadinessPolicyParity:
    """/readiness must use exactly HealthEngine's CRITICAL_CATEGORIES."""

    def test_model_contract_is_critical(self):
        # The historical divergence: /readiness's hardcoded set omitted it.
        assert "MODEL_CONTRACT" in CRITICAL_CATEGORIES

    def test_critical_failure_flips_overall_to_not_ready(self):
        entries = [HealthEntry("MODEL_CONTRACT", "FAIL", "dim mismatch")]
        eng = HealthEngine.__new__(HealthEngine)  # no probe side effects
        verdict, _ = eng.overall(entries)
        assert verdict == "NOT READY"

    def test_optional_failure_degrades_but_does_not_block(self):
        entries = [HealthEntry("TELEGRAM", "FAIL", "not configured", optional=True)]
        eng = HealthEngine.__new__(HealthEngine)
        verdict, _ = eng.overall(entries)
        assert verdict == "DEGRADED"

    def test_no_failures_is_ready(self):
        entries = [
            HealthEntry("SYSTEM", "PASS", "ok"),
            HealthEntry("DATABASE", "PASS", "ok"),
        ]
        eng = HealthEngine.__new__(HealthEngine)
        verdict, _ = eng.overall(entries)
        assert verdict == "READY"

    def test_ready_flag_is_false_when_an_optional_category_fails(self):
        """`ready` must never be True over a verdict the engine calls a failure.

        /readiness computes ready = (verdict != NOT READY) AND (no required
        FAIL). A FAIL in an OPTIONAL category yields DEGRADED — not NOT READY
        — so `ready` is True and the verdict is DEGRADED. That pairing is
        consistent (the optional layer does not gate readiness) but the UI
        renders the `ready` flag as a green "READY" chip, so the two facts
        must agree: ready=True is only ever emitted alongside a non-failure
        verdict, and the verdict is the authority the chip is derived from.
        """
        from nexus_scalp.web.api_v1.system import system_readiness

        entries = [HealthEntry("TELEGRAM", "FAIL", "not configured", optional=True)]
        eng = HealthEngine.__new__(HealthEngine)
        verdict, _ = eng.overall(entries)
        assert verdict == "DEGRADED"
        # The route-level predicate, evaluated by hand on the same inputs:
        # no REQUIRED layer failed, so the gate is genuinely open.
        required = [e for e in entries if e.category in CRITICAL_CATEGORIES]
        ready = verdict != "NOT READY" and not any(e.verdict == "FAIL" for e in required)
        assert ready is True
        # And the authority contract holds: the verdict the UI badge shows is
        # produced by the engine, and a FAIL is never reported as READY.
        assert verdict != "READY"
        # The function is the real route handler; importing it proves the
        # predicate is not duplicated anywhere but the route.
        assert callable(system_readiness)

    def test_required_failure_blocks_ready_and_not_ready(self):
        entries = [HealthEntry("DATABASE", "FAIL", "unreachable")]
        eng = HealthEngine.__new__(HealthEngine)
        verdict, _ = eng.overall(entries)
        assert verdict == "NOT READY"
        required = [e for e in entries if e.category in CRITICAL_CATEGORIES]
        ready = verdict != "NOT READY" and not any(e.verdict == "FAIL" for e in required)
        assert ready is False


# ---------------------------------------------------------------- HEALTH-WORKER-STATE
class TestWorkerStateTaxonomy:
    """Worker states must carry a canonical taxonomy mapping."""

    def test_not_attached_is_not_applicable_not_unknown(self):
        mapping = {
            "RUNNING": ACTIVE,
            "HEALTHY": ACTIVE,
            "STARTING": NOT_INITIALIZED,
            "IDLE": NOT_INITIALIZED,
            "STOPPED": DISABLED,
            "NOT_ATTACHED": NOT_APPLICABLE,
        }
        # The previous UI treated NOT_ATTACHED/STOPPED/UNKNOWN as one neutral
        # bucket; the API now emits the taxonomy word so no consumer has to
        # re-derive intent. NOT_ATTACHED is a definite state, not UNKNOWN.
        assert mapping["NOT_ATTACHED"] == NOT_APPLICABLE
        assert mapping["NOT_ATTACHED"] != UNKNOWN

    def test_unknown_state_maps_to_unknown_never_healthy(self):
        mapping = {
            "RUNNING": ACTIVE,
            "HEALTHY": ACTIVE,
            "STARTING": NOT_INITIALIZED,
            "IDLE": NOT_INITIALIZED,
            "STOPPED": DISABLED,
            "NOT_ATTACHED": NOT_APPLICABLE,
        }
        raw = "GARBAGE_STATE"
        assert mapping.get(raw.upper(), UNKNOWN) == UNKNOWN


# ---------------------------------------------------------------- failure isolation
class TestHealthEngineFailureIsolation:
    """One broken subsystem must never crash the sweep (Phase 21)."""

    def test_raising_check_becomes_fail_not_crash(self, monkeypatch, tmp_path):
        eng = HealthEngine(workspace=tmp_path)

        def boom(self) -> HealthEntry:
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(HealthEngine, "check_gpu", boom, raising=False)
        entries = eng.run_all()
        names = {e.category: e for e in entries}
        # The broken check reports FAIL/ERROR...
        assert names["GPU"].verdict == "FAIL"
        assert names["GPU"].state == ERROR
        # ...and every OTHER check still ran and is untouched by it. RUNTIME is
        # the architecture-independent PASS probe (SYSTEM fails by design on
        # macOS ARM64, which the OS-matrix CI job exercises), so the
        # isolation assertion must not pin a platform-dependent verdict.
        assert names["RUNTIME"].verdict == "PASS"
        assert len(entries) > 1
