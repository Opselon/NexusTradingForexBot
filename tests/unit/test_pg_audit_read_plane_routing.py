"""PG-READ-PLANE-002 — audit read/write routing regression tests.

Covers the four defects repaired by ``fix/postgres-audit-read-plane``:

* a WRITE (broker-history sync / rule toggle / retention purge) routed through
  the READ guard, so it silently no-op'd on PostgreSQL and logged a bogus
  "no read plane registered for domain 'audit'";
* ``overflow_pending_count`` (a filesystem count) gated per-provider;
* the query timer starting BEFORE pool checkout, so connection setup was
  billed as SQL execution time (the 218ms ``audit_signals`` reading).

Every test is deterministic and needs no live PostgreSQL server: the fabric's
read/write slots and the pooled backends are replaced with in-process fakes,
exactly as the neighbouring ``test_pg_*`` suites do.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository

REPO_SOURCE = pathlib.Path("src/nexus_scalp/adapters/database/audit_repository.py")


def _make_repo(tmp_path: Any) -> AuditRepository:
    return AuditRepository()


# =====================================================================
# Test A — the audit domain resolves a READ plane under a pooled provider
# =====================================================================
class TestAuditReadPlaneRegistration:
    def test_read_plane_resolves_for_pooled_provider(self, monkeypatch: Any) -> None:
        """``_registered_audit_read_plane()`` must return a query-capable plane.

        The regression: the plane resolved, but four WRITE paths never asked
        for it (they degraded through the read guard instead). This test pins
        the resolver itself and the property the write paths depend on.
        """
        sentinel = _FakeReadPlane()
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: sentinel,
            raising=True,
        )
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        assert repo._registered_audit_read_plane() is sentinel
        assert hasattr(sentinel, "query")
        assert not hasattr(sentinel, "execute")

    def test_registered_plane_must_not_be_write_shaped(self) -> None:
        """A WRITE-shaped backend must never be accepted as the read plane."""

        class _WriteShaped:
            def execute(self, *a: Any, **k: Any) -> None: ...

            def query(self, *a: Any, **k: Any) -> list[Any]:
                return []

        backend = _WriteShaped()
        rejected = hasattr(backend, "execute") or not hasattr(backend, "query")
        assert rejected, "a backend exposing execute() is write-shaped"


# =====================================================================
# Test B — a write path must not silently degrade on PostgreSQL
# =====================================================================
class TestNoSilentDegradationOnWrites:
    def test_broker_history_sync_writes_through_the_write_backend(self, monkeypatch: Any) -> None:
        """The sync must reach the WRITE backend — never the read guard.

        Before the fix the PG branch returned fabricated zero telemetry and
        logged "no read plane registered for domain 'audit'" while persisting
        nothing.
        """
        calls: list[list[tuple[str, tuple[Any, ...]]]] = []

        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        monkeypatch.setattr(
            AuditRepository,
            "_provider_execute_write_counted",
            lambda self, statements: calls.append(list(statements)) or [1] * len(statements),
            raising=True,
        )
        guarded: list[str] = []
        monkeypatch.setattr(
            AuditRepository,
            "_provider_read_guard",
            lambda self, op, default, **kw: guarded.append(op) or default(),
            raising=True,
        )

        result = repo.sync_broker_history([], [], symbol="XAUUSD")

        assert guarded == [], "the write path must never call the READ guard"
        assert len(calls) == 1, "the write path must issue exactly one atomic batch"
        # the watermark upsert is always the last statement of the batch
        assert "audit_broker_history_meta" in calls[0][-1][0]
        assert "error" not in result

    def test_toggle_trading_rule_persists_through_the_write_backend(self, monkeypatch: Any) -> None:
        calls: list[list[tuple[str, tuple[Any, ...]]]] = []
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        monkeypatch.setattr(
            AuditRepository,
            "_provider_execute_write",
            lambda self, statements: calls.append(list(statements)) or True,
            raising=True,
        )
        monkeypatch.setattr(
            AuditRepository,
            "_provider_read_guard",
            lambda self, op, default, **kw: pytest.fail("the write path called the READ guard"),
            raising=True,
        )

        assert repo.toggle_trading_rule("RULE_X", True) is True
        assert len(calls) == 1
        sql, args = calls[0][0]
        assert "UPDATE trading_rules_config" in sql
        assert args == (1, "RULE_X")

    def test_purge_old_audit_data_runs_on_postgresql(self, monkeypatch: Any) -> None:
        """The retention purge must not return ``{'error': 'not sqlite'}``."""
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        repo._signal_retention_days = 7.0
        repo._moving_retention_days = 3.0
        repo._telemetry_retention_days = 13.0
        repo._purge_batch_size = 500
        # A batch smaller than bsize terminates the loop after one round trip.
        monkeypatch.setattr(
            AuditRepository,
            "_provider_execute_write_counted",
            lambda self, statements: [0],
            raising=True,
        )

        result = repo.purge_old_audit_data()

        assert "error" not in result, f"purge must not report a provider error: {result}"
        assert set(result["deleted"]) == {"audit_signals", "position_moving", "guard_telemetry"}

    def test_overflow_pending_count_is_provider_independent(self, monkeypatch: Any) -> None:
        """A filesystem count must work identically on both providers."""
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        monkeypatch.setattr(
            AuditRepository,
            "_overflow_dir",
            lambda self: pathlib.Path("/nonexistent/overflow/dir"),
            raising=True,
        )
        monkeypatch.setattr(
            AuditRepository,
            "_provider_read_guard",
            lambda self, op, default, **kw: pytest.fail("a filesystem count is not a DB read"),
            raising=True,
        )
        assert repo.overflow_pending_count() == 0


# =====================================================================
# Test C — a MISSING read plane must be observable, never faked
# =====================================================================
class TestMissingReadPlaneIsObservable:
    def test_degradation_is_counted_and_warned_not_silent(self, monkeypatch: Any) -> None:
        """With no plane, the guard counts the degradation and warns."""
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        repo.provider_reads_routed = 0
        repo.provider_read_route_errors = 0
        repo.provider_read_degraded_total = 0
        repo.provider_read_degraded_ops = {}
        repo._provider_read_guard_state = {}
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: None,
            raising=True,
        )

        result = repo._provider_read_guard(
            "get_ledger_row",
            lambda: None,
            sql="SELECT * FROM audit_ledger WHERE ticket = ?",
            args=(1,),
            kind="row",
        )

        assert result is None
        assert repo.provider_read_degraded_total == 1, "a missing plane must be COUNTED"
        assert repo.provider_read_degraded_ops == {"get_ledger_row": 1}
        assert repo.provider_reads_routed == 0

    def test_metrics_surface_reports_the_degradation(self, monkeypatch: Any) -> None:
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        repo.provider_reads_routed = 0
        repo.provider_read_route_errors = 0
        repo.provider_read_degraded_total = 0
        repo.provider_read_degraded_ops = {}
        repo._provider_read_guard_state = {}
        monkeypatch.setattr(
            AuditRepository, "_registered_audit_read_plane", lambda self: None, raising=True
        )
        repo._provider_read_guard(
            "get_ledger_row", lambda: None, sql="SELECT 1 FROM audit_ledger", kind="row"
        )
        metrics = repo.provider_read_metrics()
        assert metrics["read_plane_registered"] is False
        assert metrics["read_degraded"] is True
        assert metrics["provider_read_degraded_total"] == 1


# =====================================================================
# Test D — the audit_signals recent-record query routes through the plane
# =====================================================================
class TestAuditQueryRouting:
    def test_recent_signals_query_uses_the_read_plane(self, monkeypatch: Any) -> None:
        """The exact statement from the 218ms report must hit the read plane."""
        seen: list[tuple[str, tuple[Any, ...]]] = []

        class _RecordingPlane:
            def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
                seen.append((sql, tuple(args)))
                return [
                    {
                        "request_id": "r1",
                        "symbol": "XAUUSD",
                        "action": "BUY",
                        "generated_at": "2026-09-27T00:00:00+00:00",
                        "payload": "{}",
                    }
                ]

        plane = _RecordingPlane()
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        repo.provider_reads_routed = 0
        repo.provider_read_route_errors = 0
        repo.provider_read_degraded_total = 0
        repo.provider_read_degraded_ops = {}
        repo._provider_read_guard_state = {}
        monkeypatch.setattr(
            AuditRepository, "_registered_audit_read_plane", lambda self: plane, raising=True
        )

        rows = repo.get_recent_predictions(limit=40)

        assert len(rows) == 1
        assert seen, "the query must reach the read plane"
        sql, args = seen[0]
        assert "FROM audit_signals" in sql
        assert "ORDER BY id DESC" in sql
        assert args == (40,)
        assert repo.provider_reads_routed == 1
        assert repo.provider_read_degraded_total == 0

    def test_ledger_read_uses_the_read_plane(self, monkeypatch: Any) -> None:
        class _EmptyPlane:
            def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
                assert "FROM audit_ledger" in sql
                return []

        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        repo.provider_reads_routed = 0
        repo.provider_read_route_errors = 0
        repo.provider_read_degraded_total = 0
        repo.provider_read_degraded_ops = {}
        repo._provider_read_guard_state = {}
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: _EmptyPlane(),
            raising=True,
        )
        assert repo.get_ledger_trades(limit=5) == []
        assert repo.provider_reads_routed == 1

    def test_account_performance_metrics_routes_through_the_guard(self, monkeypatch: Any) -> None:
        """A missing plane must be COUNTED, not silently answered with zeros.

        This method used to bypass the guard entirely: with no plane it
        returned fabricated zeros while moving no counter and logging nothing —
        a healthy-looking dashboard over a dead read path.
        """
        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        repo.provider_reads_routed = 0
        repo.provider_read_route_errors = 0
        repo.provider_read_degraded_total = 0
        repo.provider_read_degraded_ops = {}
        repo._provider_read_guard_state = {}
        monkeypatch.setattr(
            AuditRepository, "_registered_audit_read_plane", lambda self: None, raising=True
        )

        metrics = repo.get_account_performance_metrics()

        assert metrics["total_trades"] == 0, "the documented default still returns"
        assert repo.provider_read_degraded_total == 1, (
            "a missing plane must move the degradation counter"
        )
        assert "get_account_performance_metrics" in repo.provider_read_degraded_ops

    def test_account_performance_metrics_uses_real_plane_data(self, monkeypatch: Any) -> None:
        class _LedgerPlane:
            def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
                if "audit_account_snapshots" in sql:
                    return [{"balance": 100.0, "equity": 100.0}, {"balance": 90.0, "equity": 90.0}]
                return [
                    {"pnl": 10.0, "commission": 0.0, "swap": 0.0, "duration_sec": 60.0},
                    {"pnl": -5.0, "commission": 0.0, "swap": 0.0, "duration_sec": 30.0},
                ]

        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = False
        repo.provider_reads_routed = 0
        repo.provider_read_route_errors = 0
        repo.provider_read_degraded_total = 0
        repo.provider_read_degraded_ops = {}
        repo._provider_read_guard_state = {}
        monkeypatch.setattr(
            AuditRepository,
            "_registered_audit_read_plane",
            lambda self: _LedgerPlane(),
            raising=True,
        )

        metrics = repo.get_account_performance_metrics()
        assert metrics["total_trades"] == 2
        assert metrics["win_rate"] == 50.0
        assert repo.provider_read_degraded_total == 0


# =====================================================================
# Test E — write and read resolve the SAME configured provider
# =====================================================================
class TestProviderConsistency:
    def test_write_and_read_planes_are_distinct_pools_for_one_domain(self) -> None:
        """The fabric holds independent READ and WRITE slots for ``audit``."""
        from nexus_scalp.database import fabric

        assert hasattr(fabric, "get_domain_backend")
        assert hasattr(fabric, "register_domain_read_backend")
        assert hasattr(fabric, "provision_domain")

    def test_repository_gates_use_sqlite_flag_not_a_second_source(self) -> None:
        """One provider decision (``_is_sqlite``) drives every gate.

        A second, independently-derived provider check is how a write path
        drifts onto the read plane; the source must keep exactly one.
        """
        source = REPO_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "sync_broker_history":
                gates = [
                    n
                    for n in ast.walk(node)
                    if isinstance(n, ast.If) and "_is_sqlite" in ast.unparse(n.test)
                ]
                assert gates, "sync_broker_history must gate on the provider"
                # the write branch must not hand a read-shaped default to a caller
                body = ast.unparse(node)
                assert "_provider_execute_write_counted" in body, (
                    "the PG branch must use the counted WRITE path"
                )
                assert 'orders_inserted": 0,\n' not in body or "error" in body, (
                    "a provider failure must be reported, not fabricated as a clean sync"
                )
                return
        pytest.fail("sync_broker_history not found in the repository source")


# =====================================================================
# The query timer must exclude pool checkout (the 218ms report)
# =====================================================================
class TestQueryTimerExcludesPoolCheckout:
    def test_pg_pool_query_timer_wraps_only_execution(self) -> None:
        """``query_timer`` must start AFTER ``self.connection()``.

        The 218ms ``audit_signals`` reading was TCP connect + auth + session
        setup billed as SQL time; real execution is sub-millisecond.
        """
        src = pathlib.Path("src/nexus_scalp/database/fabric/pg_planes.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(src)
        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "_run":
                continue
            body = ast.unparse(node)
            if "query_timer" not in body:
                continue
            checked += 1
            assert "self.connection() as conn, conn.cursor() as cur" in body, (
                "the connection/cursor must be acquired OUTSIDE the timed block "
                f"(found: {body[:200]})"
            )
            # and the timer must be the INNER context manager: walk the with
            # statements and confirm the pooled connection is acquired by an
            # OUTER with (never inside the same with as query_timer).
            outer_with, inner_with = None, None
            for item in ast.walk(node):
                if not isinstance(item, ast.With):
                    continue
                ctxs = [ast.unparse(c.context_expr) for c in item.items]
                if any("query_timer" in c for c in ctxs):
                    inner_with = item
                if any("connection()" in c for c in ctxs):
                    outer_with = item
            assert outer_with is not None and inner_with is not None, (
                "expected an outer with acquiring the pooled connection and an "
                "inner with running query_timer"
            )
            assert outer_with is not inner_with, (
                "query_timer and the pooled connection must NOT share one with "
                "(checkout time would be billed as SQL execution time)"
            )
        assert checked >= 2, f"expected the query + scalar timers, found {checked}"


class _FakeReadPlane:
    """Minimal read plane: query-capable, no ``execute`` (not write-shaped)."""

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return []

    def query_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        return None
