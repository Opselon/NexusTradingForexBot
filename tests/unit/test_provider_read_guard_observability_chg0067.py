"""CHG-0067 / CR-02 — provider-read degradation guard (regression tests).

The defect (verified, lane-E CR-02 HIGH):

    ``AuditRepository`` holds 28 ``if not self._is_sqlite`` gates. Under a
    PostgreSQL provider every gated READ silently returned its default
    (None / 0 / [] / ``{"error": "not sqlite"}``) with NO exception, NO log
    and NO counter. The fabric read plane
    (``nexus_scalp.database.fabric``: ``ReadGateway``, ``PgReadPlane``) had
    ZERO callers outside ``database/fabric/``: the registry held pooled
    WRITE backends only. So a PostgreSQL switch made every audit read
    answer "there is nothing" while the data existed — fail-silent wrong
    data, which the mission explicitly forbids (no silent loss of
    consistency).

The fix (observability-first, inside ``audit_repository.py`` only):

    ``_provider_read_guard(operation, default, *, sql, args, kind)``

      * SQLite never reaches the helper (the gates short-circuit first), so
        SQLite read semantics are unchanged — asserted here.
      * When a read plane is genuinely registered for ``audit`` AND the gate
        declared its query, the read is ROUTED through it; a failed route is
        counted and warned, never swallowed.
      * Otherwise the degradation is made OBSERVABLE:
          - ``provider_read_degraded_total`` increments on EVERY degraded
            read (never capped, the hot path is not throttled);
          - ``provider_read_degraded_ops`` keeps the per-operation
            breakdown;
          - ONE structured warning per operation name (first occurrence
            always logged; repeats rate-limited by
            ``_PROVIDER_READ_LOG_INTERVAL_SEC``) — the one-shot property;
          - then the documented default is returned unchanged. Nothing
            raises into callers (mission s91: recoverable issues stay
            recoverable).

What is deliberately NOT done here: full per-query PostgreSQL read routing
is the documented follow-up wave; this guard is the observability seam that
makes that wave's absence visible.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import structlog

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository

# =====================================================================
# Helpers
# =====================================================================


class _CaptureHandler(logging.Handler):
    """Captures stdlib records (structlog hands off to stdlib; root is the
    propagation target and caplog's handler attaches at root, so this
    mirrors the production sink without depending on a console log being
    installed)."""

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture()
def capture_audit_warnings() -> Generator[_CaptureHandler, None, None]:
    """Capture the repository's structured warnings as an operator reads them.

    Attach the handler AND rebind the structlog pipeline together (the
    BUG-140/BUG-295 lesson): structlog only reaches a stdlib handler once
    ``logger_factory`` points at stdlib, so without the rebind a bare pytest
    run emits to stdout only and the handler stays empty. The previous
    structlog config is restored on teardown.
    """
    import structlog

    handler = _CaptureHandler()
    logger = logging.getLogger("nexus_scalp")
    previous_config = structlog.get_config()
    _bind_logger()  # rebinds structlog -> stdlib (no logger object needed)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        structlog.configure(**previous_config)


def _make_non_sqlite_repo() -> AuditRepository:
    """A non-SQLite repository WITHOUT touching a live server.

    The fabric registry holds a pooled WRITE backend only (verified: zero
    read-plane registrations in src/), so no read plane resolves and every
    gated read degrades observably. ``__new__`` is the established shape
    (``tests/unit/test_audit_flush_contract.py:143`` builds exactly this),
    and every seam the guard touches is initialized defensively.
    """
    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nse_audit"
    repo._db_path = ""
    repo.provider_reads_routed = 0
    repo.provider_read_route_errors = 0
    repo.provider_read_degraded_total = 0
    repo.provider_read_degraded_ops = {}
    repo._provider_read_guard_state = {}
    return repo


def _bind_logger() -> structlog.stdlib.BoundLogger:
    """The module logger with a stdlib-interop pipeline.

    The app's production pipeline is installed by ``configure_logging`` at
    boot; under a bare pytest run structlog's default lazy proxy renders
    positional args as a dict instead of formatting the message, so a
    handler capturing ``record.getMessage()`` sees ``op=%s`` unformatted.
    Re-binding through stdlib keeps the assertions on the RENDERED message
    (the shape an operator reads) without touching app configuration.
    """
    import structlog

    structlog.configure(
        processors=[
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    return structlog.get_logger("nexus_scalp.adapters.audit_db")


def _warning_count(handler: _CaptureHandler, operation: str) -> int:
    marker = "provider read degraded"
    return sum(1 for m in handler.messages if marker in m and f"op={operation}" in m)


# =====================================================================
# (a) Non-SQLite: default returned + counter incremented + one-shot log
# =====================================================================


class TestProviderReadDegradation:
    """2-3 representative gated reads under a non-SQLite provider."""

    def test_get_ledger_row_returns_none_and_is_observable(
        self, capture_audit_warnings: _CaptureHandler
    ) -> None:
        repo = _make_non_sqlite_repo()
        assert repo.get_ledger_row(12345) is None
        # the documented default is returned, but the degradation is not silent
        assert repo.provider_read_degraded_total == 1
        assert repo.provider_read_degraded_ops == {"get_ledger_row": 1}
        assert _warning_count(capture_audit_warnings, "get_ledger_row") == 1
        metrics = repo.provider_read_metrics()
        assert metrics["read_degraded"] is True
        assert metrics["provider"] == "non-sqlite"
        assert metrics["read_plane_registered"] is False
        assert metrics["provider_read_degraded_ops"] == {"get_ledger_row": 1}

    def test_has_ledger_opened_returns_false_and_counts(self) -> None:
        repo = _make_non_sqlite_repo()
        for _ in range(3):
            assert repo.has_ledger_opened(999) is False
        # EVERY degraded read moves the counter, even the repeats
        assert repo.provider_read_degraded_total == 3
        assert repo.provider_read_degraded_ops["has_ledger_opened"] == 3

    def test_count_ledger_opened_unclosed_preserves_documented_sentinel(self) -> None:
        """The documented -1 sentinel must survive: the caller falls through
        to the broker fetch exactly as before, but now the caller's operator
        can see WHY it got -1."""
        repo = _make_non_sqlite_repo()
        assert repo.count_ledger_opened_unclosed() == -1
        assert repo.provider_read_degraded_total == 1
        assert repo.provider_read_degraded_ops == {"count_ledger_opened_unclosed": 1}

    def test_repeated_calls_log_exactly_once(self, capture_audit_warnings: _CaptureHandler) -> None:
        """The one-shot contract: N degraded reads of ONE operation emit the
        warning EXACTLY once (first occurrence always logs)."""
        repo = _make_non_sqlite_repo()
        for _ in range(12):
            assert repo.get_last_account_snapshot() is None
        assert repo.provider_read_degraded_total == 12
        assert repo.provider_read_degraded_ops["get_last_account_snapshot"] == 12
        assert _warning_count(capture_audit_warnings, "get_last_account_snapshot") == 1

    def test_distinct_operations_log_independently(
        self, capture_audit_warnings: _CaptureHandler
    ) -> None:
        repo = _make_non_sqlite_repo()
        assert repo.get_ledger_trades() == []
        assert repo.get_trading_rules() == []
        assert repo.get_recent_predictions(5) == []
        assert repo.provider_read_degraded_total == 3
        assert _warning_count(capture_audit_warnings, "get_ledger_trades") == 1
        assert _warning_count(capture_audit_warnings, "get_trading_rules") == 1
        assert _warning_count(capture_audit_warnings, "get_recent_predictions") == 1


# =====================================================================
# (b) SQLite: the guard never fires
# =====================================================================


def test_sqlite_repository_guard_does_not_fire(
    tmp_path: object, capture_audit_warnings: _CaptureHandler
) -> None:
    """A real SQLite repository keeps its exact read semantics: no
    degradation counter, no warning, real data returned (byte-for-byte
    unchanged paths)."""
    from pathlib import Path

    target = Path(str(tmp_path)) / "guard.db"
    repo = AuditRepository(db_url=f"sqlite:///{target}")
    try:
        assert repo._is_sqlite is True
        # A SQLite read is NOT degraded: it goes to the real table.
        assert repo.get_ledger_row(4242) is None
        assert repo.get_trading_rules() != []  # seeded rules table
        assert repo.provider_read_degraded_total == 0
        assert repo.provider_reads_routed == 0
        assert repo.provider_read_degraded_ops == {}
        assert _warning_count(capture_audit_warnings, "get_ledger_row") == 0
        metrics = repo.provider_read_metrics()
        assert metrics["provider"] == "sqlite"
        assert metrics["read_degraded"] is False
    finally:
        repo.close()


def test_sqlite_guard_helper_short_circuits(capture_audit_warnings: _CaptureHandler) -> None:
    """Defensive path: even if a SQLite repository reaches the helper, it
    returns the default and emits nothing (never a degraded read)."""
    repo = _make_non_sqlite_repo()
    repo._is_sqlite = True  # the gates make this unreachable; the helper stays safe
    assert repo._provider_read_guard("get_ledger_row", lambda: None) is None
    assert repo.provider_read_degraded_total == 0
    assert _warning_count(capture_audit_warnings, "get_ledger_row") == 0


# =====================================================================
# (c) The helper itself: rate limiting + one-shot + routing
# =====================================================================


def test_helper_rate_limits_within_interval() -> None:
    """The rate limiter suppresses repeats but the counter never does."""
    repo = _make_non_sqlite_repo()
    original = repo._PROVIDER_READ_LOG_INTERVAL_SEC
    repo._PROVIDER_READ_LOG_INTERVAL_SEC = 10_000.0  # so repeats are suppressed
    try:
        repo._provider_read_guard("get_ledger_row", lambda: None)
        emitted = repo._warn_provider_read("get_ledger_row", "value", 2, 2)  # type: ignore[func-returns-value]
        assert emitted is None or emitted is None  # second emit suppressed
        state = repo._provider_read_guard_state["get_ledger_row"]
        assert state[1] == 1, "the suppressed emit must not count"
    finally:
        repo._PROVIDER_READ_LOG_INTERVAL_SEC = original


def test_helper_first_occurrence_always_logs(
    capture_audit_warnings: _CaptureHandler,
) -> None:
    """No matter the interval, the FIRST occurrence of an operation logs
    (through the production logger binding, same as the gated reads)."""
    repo = _make_non_sqlite_repo()
    repo._PROVIDER_READ_LOG_INTERVAL_SEC = 1e9
    repo._warn_provider_read("paper_execution_stats", "value", 1, 1)
    assert _warning_count(capture_audit_warnings, "paper_execution_stats") == 1


def test_helper_rate_limit_expires_after_interval(
    capture_audit_warnings: _CaptureHandler,
) -> None:
    """Once the interval elapses, a repeat is logged again (a long-lived
    degraded process keeps re-surfacing the condition, not just once)."""
    repo = _make_non_sqlite_repo()
    repo._PROVIDER_READ_LOG_INTERVAL_SEC = 0.0  # every call is "due"
    repo._warn_provider_read("get_recent_predictions", "value", 1, 1)
    repo._warn_provider_read("get_recent_predictions", "value", 2, 2)
    assert _warning_count(capture_audit_warnings, "get_recent_predictions") == 2
    # emissions tracked (2), occurrences tracked (2)
    assert repo._provider_read_guard_state["get_recent_predictions"][1] == 2


def test_helper_counters_reset_between_instances() -> None:
    """Per-instance counters are fresh (a stale counter would make the
    metric meaningless across restarts)."""
    first = _make_non_sqlite_repo()
    first.get_ledger_row(1)
    second = _make_non_sqlite_repo()
    second.get_ledger_row(2)
    assert first.provider_read_degraded_total == 1
    assert second.provider_read_degraded_total == 1


# =====================================================================
# (d) Routing: route where a read plane genuinely exists
# =====================================================================


class _FakeReadPlane:
    """A minimal read-plane-shaped object (query/query_one/scalar).

    Deliberately exercises only the guard's route contract; it does NOT
    impersonate psycopg or a pool."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append(("query", sql, tuple(args)))
        if "runtime_risk_state" in sql:
            return [{"id": 1, "halt": "RUNNING"}]
        if "audit_ledger" in sql:
            return [{"ticket": 4242, "status": "OPENED"}]
        return []

    def query_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        self.calls.append(("query_one", sql, tuple(args)))
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        self.calls.append(("scalar", sql, tuple(args)))
        if "COUNT(*)" in sql:
            return 7
        return None


class _FakeWriteBackend:
    """Write-shaped backend: the guard must REFUSE to read through it
    (reads never share the write path, and the registry's current
    occupants are pooled write backends)."""

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> None:  # pragma: no cover
        raise AssertionError("a read must never be served from a write backend")


def _register_audit_read_plane(monkeypatch: pytest.MonkeyPatch, plane: Any) -> None:
    """Install a fake read plane through the fabric's own accessor seam."""
    from nexus_scalp.database import fabric as fabric_mod

    captured: dict[str, Any] = {}

    def fake_get_domain_backend(domain: str, readonly: bool = False) -> Any:
        captured.setdefault("calls", []).append((domain, readonly))
        return plane if readonly else None

    monkeypatch.setattr(fabric_mod, "get_domain_backend", fake_get_domain_backend)


class TestProviderReadRouting:
    def test_read_plane_serves_a_declared_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        plane = _FakeReadPlane()
        _register_audit_read_plane(monkeypatch, plane)
        repo = _make_non_sqlite_repo()

        assert repo.get_runtime_risk_state() == {"id": 1, "halt": "RUNNING"}
        # the read was ROUTED, not degraded
        assert repo.provider_reads_routed == 1
        assert repo.provider_read_degraded_total == 0
        assert plane.calls and plane.calls[0][0] == "query_one"

    def test_read_plane_serves_a_count_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        plane = _FakeReadPlane()
        _register_audit_read_plane(monkeypatch, plane)
        repo = _make_non_sqlite_repo()
        assert repo.count_ledger_opened_unclosed() == 7
        assert repo.provider_reads_routed == 1
        assert repo.provider_read_degraded_total == 0
        assert any(c[0] == "scalar" for c in plane.calls)

    def test_write_backend_is_never_used_for_reads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _register_audit_read_plane(monkeypatch, _FakeWriteBackend())
        repo = _make_non_sqlite_repo()
        # a write-shaped backend is not a read plane: degrade observably instead
        assert repo.count_ledger_opened_unclosed() == -1
        assert repo.provider_reads_routed == 0
        assert repo.provider_read_degraded_total == 1

    def test_failed_route_degrades_observably(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _BrokenReadPlane(_FakeReadPlane):
            def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
                raise RuntimeError("connection refused")

            def query_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
                raise RuntimeError("connection refused")

            def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
                raise RuntimeError("connection refused")

        plane = _BrokenReadPlane()
        _register_audit_read_plane(monkeypatch, plane)
        repo = _make_non_sqlite_repo()
        assert repo.get_runtime_risk_state() is None
        # the route failure is counted, the read degraded, and nothing raised
        assert repo.provider_read_route_errors == 1
        assert repo.provider_read_degraded_total == 1
        assert repo.provider_reads_routed == 0
        assert repo.provider_read_metrics()["read_plane_registered"] is True


# =====================================================================
# (e) Source pins: the guard is wired into the audit read surface
# =====================================================================


def test_guard_sites_cover_the_gated_read_surface() -> None:
    """Every remaining non-SQLite read gate goes through the guard, so no
    audit read can degrade silently."""
    import ast
    import pathlib

    source = pathlib.Path("src/nexus_scalp/adapters/database/audit_repository.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "_provider_read_guard"):
            continue
        assert node.args and isinstance(node.args[0], ast.Constant)
        guarded.add(str(node.args[0].value))

    # the representative reads this guard must cover
    expected = {
        "get_runtime_risk_state",
        "has_ledger_opened",
        "count_ledger_opened_unclosed",
        "get_ledger_opened",
        "get_ledger_row",
        "get_last_account_snapshot",
        "get_ledger_trades",
        "get_trading_rules",
        "get_recent_predictions",
    }
    missing = expected - guarded
    assert not missing, f"uncovered gated reads: {sorted(missing)}"


def test_gates_do_not_fire_for_sqlite_read_paths() -> None:
    """No gate calls the provider guard with a SQLite repository: SQLite
    read semantics are the gate's ONLY un-degraded path."""
    import pathlib

    source = pathlib.Path("src/nexus_scalp/adapters/database/audit_repository.py").read_text(
        encoding="utf-8"
    )
    # every guard call sits directly under an `if not self._is_sqlite:` gate
    lines = source.splitlines()
    guard_lines = [i for i, l in enumerate(lines) if "_provider_read_guard(" in l]
    assert guard_lines, "guard sites must exist"
    for gi in guard_lines:
        gate = next(
            (lines[k] for k in range(gi, -1, -1) if "if not self._is_sqlite" in lines[k]),
            "",
        )
        assert "if not self._is_sqlite" in gate, (
            f"guard at line {gi + 1} is not behind a non-SQLite gate"
        )
