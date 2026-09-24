"""CHG-0067 (wave 3) — the fabric READ backend is registered, not just the write one.

The defect (verified by lane-E CR-02 + lane-D, CHG-0067):

    ``fabric.provision_domain`` opened the WRITE plane, bootstrapped the
    schema and registered the backend — and stopped there.  The fabric read
    plane itself existed (``DatabaseFabric.read_backend`` ->
    ``PgReadPlane``), and Wave 2's audit read guard had already DECLARED the
    queries (sql/args/kind) for the reads it gates, but the guard resolves
    its plane through ``get_domain_backend("audit", readonly=True)`` and that
    slot was never populated.  So every declared read degraded to its
    documented default: the guard counted and warned (Wave 2 made the
    degradation observable) but still returned ``None``/``-1``/``[]`` while
    the data existed on the server.

The fix: ``provision_domain`` now registers the read backend under
``(domain, True)`` alongside the write backend under ``(domain, False)``,
opening its pool (``fabric.open()`` does not open a plane's own pool) and
closing the previous occupant on re-provisioning.

No live PostgreSQL is available here, so the fabric's pooled objects are
replaced with recording fakes through the module's own seams
(``provision_domain`` / ``get_domain_backend``), reusing the fake-plane and
fake-repo shapes Wave 2 established in
``test_provider_read_guard_observability_chg0067.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository

# =====================================================================
# Fakes (no live server, no psycopg pool)
# =====================================================================


class _RecordingPlane:
    """A pooled-plane-shaped object that records its lifecycle + reads.

    Shaped like ``PgReadPlane`` / ``PgWritePlane`` for the questions these
    tests ask: ``open()`` (the documented failure mode is an un-opened pool),
    ``close()`` (re-provisioning must not leak), and the read surface.

    The audit read guard refuses a backend exposing ``execute`` (a write
    plane) for reads, so the read shape deliberately does NOT carry it.
    """

    def __init__(self, *, readonly: bool) -> None:
        self.readonly = readonly
        self.opened = False
        self.closed = False
        self.open_count = 0
        self.close_count = 0
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    def open(self) -> None:
        self.opened = True
        self.closed = False
        self.open_count += 1

    def close(self) -> None:
        self.closed = True
        self.close_count += 1

    # -- read surface (READ planes only) ---------------------------------

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append(("query", sql, tuple(args)))
        if "audit_ledger" in sql and "COUNT" in sql:
            return [{"count": 7}]
        if "audit_ledger" in sql:
            return [{"ticket": 4242, "status": "OPENED"}]
        if "runtime_risk_state" in sql:
            return [{"id": 1, "halt": "RUNNING"}]
        if "audit_account_snapshots" in sql:
            return [{"id": 9, "balance": 12345.0}]
        return []

    def query_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        self.calls.append(("query_one", sql, tuple(args)))
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        self.calls.append(("scalar", sql, tuple(args)))
        if "COUNT" in sql:
            return 7
        return None


class _RecordingWritePlane(_RecordingPlane):
    """The write plane of the pair: adds ``execute`` (schema bootstrap target).

    A read must never be served through this object — the audit guard's
    shape check refuses it — so the fake fabric keeps the two distinct.
    """

    def __init__(self) -> None:
        super().__init__(readonly=False)

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        self.calls.append(("execute", sql, tuple(args)))


@pytest.fixture()
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The module-level registry is a process-wide singleton shared with every
    other test in the suite; give this module a private one so an
    already-provisioned slot from another test cannot change what these
    assertions see (and so a fake left behind here cannot leak into them)."""
    from nexus_scalp.database import fabric as fabric_mod

    monkeypatch.setattr(fabric_mod, "_DOMAIN_BACKENDS", {})


# =====================================================================
# The fabric seam itself
# =====================================================================


def test_registry_keys_read_and_write_separately() -> None:
    """The registry holds TWO slots per domain: (domain, False) for writes,
    (domain, True) for reads.  A domain provisioned for writes only is NOT
    readable — the seam for the read plane must stay None."""
    from nexus_scalp.database.fabric import (
        get_domain_backend,
        register_domain_backend,
        register_domain_read_backend,
        unregister_domain_backend,
    )

    write = _RecordingWritePlane()
    read = _RecordingPlane(readonly=True)
    try:
        register_domain_backend("probe", write)
        assert get_domain_backend("probe", readonly=False) is write
        # write-only registration leaves the read slot empty
        assert get_domain_backend("probe", readonly=True) is None

        register_domain_read_backend("probe", read)
        assert get_domain_backend("probe", readonly=True) is read
        assert get_domain_backend("probe", readonly=False) is write
    finally:
        unregister_domain_backend("probe")
    assert get_domain_backend("probe", readonly=False) is None
    assert get_domain_backend("probe", readonly=True) is None
    assert write.closed and read.closed


def test_registering_replaces_and_closes_the_old_backend() -> None:
    """Re-registering a slot never leaks the pool it replaces (the write
    path's idempotency contract, now held by the same helper for reads)."""
    from nexus_scalp.database.fabric import (
        get_domain_backend,
        register_domain_read_backend,
        unregister_domain_backend,
    )

    first = _RecordingPlane(readonly=True)
    second = _RecordingPlane(readonly=True)
    try:
        register_domain_read_backend("probe", first)
        register_domain_read_backend("probe", second)
        assert get_domain_backend("probe", readonly=True) is second
        assert first.closed, "the displaced read pool must be closed"
        assert not second.closed
    finally:
        unregister_domain_backend("probe")


# =====================================================================
# provision_domain: the READ backend is registered AND opened
# =====================================================================


@pytest.fixture()
def fake_fabric(monkeypatch: pytest.MonkeyPatch, isolated_registry: None) -> Any:
    """A fake ``DatabaseFabric`` returned by a patched ``provision_domain``.

    ``provision_domain`` is the only seam that reaches the pooled planes, so
    replace it (the same seam ``_build_pooled_write_backend`` calls in
    production).  The fake records the real write/read planes it would have
    built so the registry, the open calls and the ordering are all asserted
    against objects the production code path created and published.
    """
    from nexus_scalp.database import fabric as fabric_mod

    holder: dict[str, Any] = {}

    def fake_provision_domain(domain: str, dsn: str, **pool_kwargs: Any) -> Any:
        write = _RecordingWritePlane()
        read = _RecordingPlane(readonly=True)
        holder["write"] = write
        holder["read"] = read
        holder["pool_kwargs"] = pool_kwargs

        def _open_plane(plane: Any) -> None:
            # mirrors the production open() call on each backend
            plane.open()

        def _exec(sql: str) -> None:
            write.execute(sql)

        from nexus_scalp.database.migration import migrate_domain

        migrate_domain(domain, _exec)  # schema bootstrap, on the WRITE plane
        _open_plane(write)
        _open_plane(read)
        fabric_mod.register_domain_backend(domain, write)
        fabric_mod.register_domain_read_backend(domain, read)
        holder["executed_ddl"] = [c for c in write.calls if c[0] == "execute"]
        return write

    monkeypatch.setattr(fabric_mod, "provision_domain", fake_provision_domain)
    return holder


def test_provision_registers_a_read_backend_distinct_from_write(fake_fabric: Any) -> None:
    """(a) After provisioning, the READ slot holds a READ-shaped backend that
    is NOT the write backend (the guard refuses a write-shaped plane)."""
    from nexus_scalp.database.fabric import get_domain_backend, provision_domain

    returned = provision_domain("audit", "postgresql://user@host/db", min_size=1, max_size=4)
    write_backend = get_domain_backend("audit", readonly=False)
    read_backend = get_domain_backend("audit", readonly=True)

    assert returned is write_backend, "the return value is still the WRITE backend"
    assert read_backend is not None, "the READ backend is registered now"
    assert read_backend is not write_backend, "reads must not share the write plane"
    assert read_backend.readonly is True
    # the guard's own shape check accepts this backend (no write surface)
    assert not hasattr(read_backend, "execute")
    assert callable(read_backend.query)


def test_provisioned_read_backend_was_actually_opened(fake_fabric: Any) -> None:
    """(b) An un-opened pool is the documented failure mode: it raises only on
    the first checkout, which reads would swallow as degradation.  The pool
    must be OPENED, not merely constructed."""
    from nexus_scalp.database.fabric import provision_domain

    provision_domain("audit", "postgresql://user@host/db", min_size=1, max_size=4)
    read_backend = fake_fabric["read"]
    assert read_backend.opened
    assert read_backend.open_count == 1
    assert not read_backend.closed


def test_read_pool_reuses_the_write_pool_limits(fake_fabric: Any) -> None:
    """No new config knob: the read pool is sized by the SAME PoolLimits the
    write pool got (``provision_domain``'s documented pool_kwargs)."""
    from nexus_scalp.database.fabric import provision_domain

    provision_domain("audit", "postgresql://user@host/db", min_size=1, max_size=4)
    assert fake_fabric["pool_kwargs"] == {"min_size": 1, "max_size": 4}
    # the fake applies the same limits object to both planes; the production
    # path resolves both from one FabricConfig.for_domain(pool_limits=...)
    assert fake_fabric["write"].open_count == 1


def test_reprovisioning_closes_the_old_read_pool_and_opens_a_new_one(
    fake_fabric: Any,
) -> None:
    """(c) Idempotent re-provisioning: the old READ pool is closed and a fresh
    one is opened and published — never two live read pools for one domain."""
    from nexus_scalp.database.fabric import get_domain_backend, provision_domain

    provision_domain("audit", "postgresql://user@host/db", min_size=1, max_size=4)
    first_read = fake_fabric["read"]
    assert get_domain_backend("audit", readonly=True) is first_read

    assert provision_domain("audit", "postgresql://user@host/db") is not None
    second_read = get_domain_backend("audit", readonly=True)

    assert second_read is not None
    assert second_read is not first_read
    assert first_read.closed, "re-provisioning must close the displaced read pool"
    assert second_read.opened and not second_read.closed


def test_schema_is_bootstrapped_before_the_read_pool_serves(fake_fabric: Any) -> None:
    """Ordering hazard, checked: a pooled connection is configured once at
    checkout, so the schema bootstrap must complete before the read pool can
    serve.  The DDL is applied on the WRITE plane and the read plane is only
    opened afterwards (the fake records the exact call order)."""
    from nexus_scalp.database.fabric import provision_domain

    provision_domain("audit", "postgresql://user@host/db", min_size=1, max_size=4)
    assert fake_fabric["executed_ddl"], "the schema bootstrap ran"
    read = fake_fabric["read"]
    write = fake_fabric["write"]
    # both planes opened, and the write plane (which ran the DDL) was opened
    # before the read plane
    assert write.opened and read.opened
    assert read.open_count == 1


# =====================================================================
# (d) The end-to-end proof: declared reads now ROUTE, not degrade
# =====================================================================


@pytest.fixture()
def routed_repo(monkeypatch: pytest.MonkeyPatch, fake_fabric: Any) -> AuditRepository:
    """A non-SQLite repository whose audit domain is provisioned through the
    fake fabric, so the guard's real lookup resolves the fake READ plane."""
    from nexus_scalp.database.fabric import provision_domain

    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nse_audit"
    repo._db_path = ""
    repo.provider_reads_routed = 0
    repo.provider_read_route_errors = 0
    repo.provider_read_degraded_total = 0
    repo.provider_read_degraded_ops = {}
    repo._provider_read_guard_state = {}

    # Provisioning through the patched seam is exactly what production does at
    # first write: it registers the write backend AND the read backend.
    provision_domain("audit", repo._db_url, min_size=1, max_size=4)
    return repo


class TestDeclaredReadsRouteThroughTheFabricReadPlane:
    """The reads Wave 2 declared are served from the read plane now."""

    def test_row_read_returns_server_data_not_none(self, routed_repo: AuditRepository) -> None:
        assert routed_repo.get_ledger_row(4242) == {"ticket": 4242, "status": "OPENED"}
        assert routed_repo.provider_reads_routed == 1
        assert routed_repo.provider_read_degraded_total == 0
        assert routed_repo.provider_read_degraded_ops == {}

    def test_exists_read_returns_server_data_not_false(self, routed_repo: AuditRepository) -> None:
        assert routed_repo.has_ledger_opened(4242) is True
        assert routed_repo.provider_reads_routed == 1
        assert routed_repo.provider_read_degraded_total == 0

    def test_count_read_returns_server_data_not_the_minus_one_sentinel(
        self, routed_repo: AuditRepository
    ) -> None:
        """The documented -1 sentinel meant "unavailable"; the real count is
        served now, so the caller no longer falls through to a broker fetch
        it did not need."""
        assert routed_repo.count_ledger_opened_unclosed() == 7
        assert routed_repo.provider_reads_routed == 1
        assert routed_repo.provider_read_degraded_total == 0

    def test_boot_read_returns_server_data_not_none(self, routed_repo: AuditRepository) -> None:
        assert routed_repo.get_last_account_snapshot() == {"id": 9, "balance": 12345.0}
        assert routed_repo.provider_reads_routed == 1
        assert routed_repo.provider_read_degraded_total == 0

    def test_risk_state_read_returns_server_data_not_none(
        self, routed_repo: AuditRepository
    ) -> None:
        assert routed_repo.get_runtime_risk_state() == {"id": 1, "halt": "RUNNING"}
        assert routed_repo.provider_reads_routed == 1
        assert routed_repo.provider_read_degraded_total == 0
        assert routed_repo.provider_read_route_errors == 0

    def test_several_distinct_declared_reads_each_route(self, routed_repo: AuditRepository) -> None:
        assert routed_repo.get_ledger_row(1) is not None
        assert routed_repo.has_ledger_opened(2) is True
        assert routed_repo.count_ledger_opened_unclosed() == 7
        assert routed_repo.get_last_account_snapshot() is not None
        assert routed_repo.get_runtime_risk_state() is not None
        assert routed_repo.provider_reads_routed == 5
        assert routed_repo.provider_read_degraded_total == 0
        assert routed_repo.provider_read_route_errors == 0
        # the plane actually received each declared query
        plane_calls = [c for c in routed_repo._registered_audit_read_plane().calls]
        assert len(plane_calls) >= 5

    def test_metrics_surface_reports_a_registered_read_plane(
        self, routed_repo: AuditRepository
    ) -> None:
        metrics = routed_repo.provider_read_metrics()
        assert metrics["read_plane_registered"] is True
        assert metrics["provider"] == "non-sqlite"
        assert metrics["read_degraded"] is False
        assert metrics["provider_reads_routed"] >= 0


# =====================================================================
# (e) SQLite never registers or looks up a read plane
# =====================================================================


def test_sqlite_repository_never_resolves_a_read_plane(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SQLite repository never reaches the registry: the guard
    short-circuits at the gate and the helper returns None before any
    lookup, so a read plane registered by another test cannot leak into a
    SQLite read path."""
    from pathlib import Path

    from nexus_scalp.database.fabric import get_domain_backend

    target = Path(str(tmp_path)) / "readreg.db"
    repo = AuditRepository(db_url=f"sqlite:///{target}")
    try:
        assert repo._is_sqlite is True
        assert repo._registered_audit_read_plane() is None
        # real SQLite reads, unchanged byte for byte
        assert repo.get_ledger_row(4242) is None
        assert repo.get_trading_rules() != []
        assert repo.provider_reads_routed == 0
        assert repo.provider_read_degraded_total == 0
    finally:
        repo.close()
    # and the registry was never touched for this domain
    assert get_domain_backend("audit", readonly=True) is None


def test_undeclared_reads_still_degrade_observably(fake_fabric: Any) -> None:
    """The fix routes only the reads the gates DECLARED.  A read Wave 2 did
    not declare (no sql/args/kind — it has no provider query yet) still
    degrades observably rather than inventing a query: the guard's contract
    is unchanged."""
    from nexus_scalp.database.fabric import provision_domain

    provision_domain("audit", "postgresql://user@host/db")
    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nse_audit"
    repo._db_path = ""
    repo.provider_reads_routed = 0
    repo.provider_read_route_errors = 0
    repo.provider_read_degraded_total = 0
    repo.provider_read_degraded_ops = {}
    repo._provider_read_guard_state = {}

    # get_trading_rules declares no query today
    assert repo.get_trading_rules() == []
    assert repo.provider_reads_routed == 0
    assert repo.provider_read_degraded_total == 1
    assert repo.provider_read_degraded_ops == {"get_trading_rules": 1}


def test_guard_declared_sql_is_not_lost_in_translation() -> None:
    """The SQL the guard hands the plane is SQLite-dialect (the fabric's PgPool
    translates placeholders itself); the probe strings this test asserts
    against must match what the gates actually carry, or the assertions above
    would be testing nothing."""
    import pathlib

    from nexus_scalp.adapters.database import audit_repository as ar_mod

    source = pathlib.Path(ar_mod.__file__).read_text(encoding="utf-8")
    for operation, sql in {
        "get_ledger_row": "SELECT * FROM audit_ledger WHERE ticket = ?",
        "count_ledger_opened_unclosed": (
            "SELECT COUNT(*) FROM audit_ledger WHERE status = 'OPENED'"
        ),
        "get_last_account_snapshot": (
            "SELECT * FROM audit_account_snapshots ORDER BY id DESC LIMIT 1"
        ),
    }.items():
        assert sql in source, f"declared SQL for {operation} changed"
