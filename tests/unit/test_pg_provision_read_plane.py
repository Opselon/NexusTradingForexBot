"""PG-READ-PLANE-001 Lane A — the read plane must be registered on provision.

The defect (D1 in the wave contract, ~2200 occurrences in the live log)::

    [DB-FABRIC] audit provider read degraded ... no read plane registered
    for domain 'audit'

Root cause: ``provision_domain()`` registered ONLY the write backend, so
``AuditRepository._registered_audit_read_plane()`` called
``get_domain_backend("audit", readonly=True)`` and always got ``None`` —
every gated audit read fell back to its documented default while the data
sat in PostgreSQL.

These tests pin the fixed contract WITHOUT touching a live server:

* a provisioned domain resolves BOTH a write backend and a read plane;
* the two are distinct objects over distinct pools, and the read plane is
  never write-shaped (``query``/``query_one``/``scalar``, no ``execute``);
* re-provisioning is idempotent — it replaces both planes and closes the old
  pools;
* a failed read-pool open fails closed (raises, registers nothing) rather
  than registering a write-only domain while reporting success;
* SQLite never goes through ``provision_domain`` and is untouched.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from nexus_scalp.database import fabric as fabric_mod
from nexus_scalp.database.fabric import (
    DatabaseFabric,
    _DomainBackends,
    get_domain_backend,
    provision_domain,
    register_domain_backend,
    register_domain_read_backend,
    unregister_domain_backend,
)

# =====================================================================
# Fake pooled planes — same seam as a real ``PgReadPlane`` / ``PgWritePlane``
# =====================================================================


class _FakeReadPlane:
    """Minimal stand-in for a ``PgReadPlane`` (read-only surface)."""

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1

    def _record(self, sql: str, args: Any) -> tuple[str, str, tuple[Any, ...]]:
        sql_norm = sql.strip().upper()
        self.calls.append(("read", sql_norm, tuple(args)))
        return sql_norm

    def query(
        self, sql: str, args: Any = (), *, allow_replica: bool = False
    ) -> list[dict[str, Any]]:
        if self._record(sql, args).startswith("SELECT"):
            return [{"ok": 1}]
        return []

    def query_one(
        self, sql: str, args: Any = (), *, allow_replica: bool = False
    ) -> dict[str, Any] | None:
        rows = self.query(sql, args, allow_replica=allow_replica)
        return rows[0] if rows else None

    def scalar(self, sql: str, args: Any = (), *, allow_replica: bool = False) -> Any:
        rows = self.query(sql, args, allow_replica=allow_replica)
        return rows[0]["ok"] if rows else None


class _FakeWritePlane:
    """Minimal stand-in for a ``PgWritePlane`` (write-only surface)."""

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self.executed: list[str] = []

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1

    def execute(self, sql: str, args: Any = ()) -> None:
        self.executed.append(sql)


class _BrokenPoolReadPlane(_FakeReadPlane):
    """A read plane whose pool cannot be opened (read pool unavailable)."""

    def open(self) -> None:
        raise RuntimeError("pg pool is not open")


class _RecordingFabric:
    """Stand-in fabric exposing the two accessors ``provision_domain`` uses.

    Holding one instance per domain lets a test assert the provisioned planes
    are the objects ``write_backend`` / ``read_backend`` actually returned.
    """

    def __init__(self, *, write: Any, read: Any) -> None:
        self._write = write
        self._read = read
        self.opened = 0
        self.closed = 0

    def write_backend(self, domain: str) -> Any:
        return self._write

    def read_backend(self, domain: str) -> Any:
        return self._read

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1


@pytest.fixture(autouse=True)
def _clean_domain_registry() -> Generator[None, None, None]:
    """No test may observe another test's registration."""
    saved = dict(fabric_mod._DOMAIN_BACKENDS)
    try:
        yield
    finally:
        fabric_mod._DOMAIN_BACKENDS.clear()
        fabric_mod._DOMAIN_BACKENDS.update(saved)


@pytest.fixture()
def _stub_fabric(monkeypatch: pytest.MonkeyPatch) -> dict[str, _RecordingFabric]:
    """Redirect ``provision_domain`` off the network onto fake planes.

    ``DatabaseFabric`` and ``FabricConfig`` still run for real (the DSN is
    only ever *parsed*, never dialled), so the provisioning path itself is
    the code under test.
    """
    fabrics: dict[str, _RecordingFabric] = {}

    def fake_fabric(domain: str, domain_cfg: Any) -> Any:
        f = _RecordingFabric(write=_FakeWritePlane(), read=_FakeReadPlane())
        fabrics[domain] = f
        return f

    def fake_migrate(domain: str, execute: Any) -> dict[str, Any]:
        execute("CREATE TABLE IF NOT EXISTS probe (id INTEGER)")
        return {"applied": 1, "error_count": 0}

    monkeypatch.setattr(fabric_mod, "DatabaseFabric", fake_fabric)
    monkeypatch.setattr(
        "nexus_scalp.database.migration.migrate_domain", fake_migrate, raising=False
    )
    return fabrics


# =====================================================================
# Registration shape
# =====================================================================


class TestProvisionRegistersReadPlane:
    def test_unprovisioned_domain_resolves_no_write_and_no_read(self) -> None:
        assert get_domain_backend("audit") is None
        assert get_domain_backend("audit", readonly=True) is None

    def test_provision_registers_write_and_read(self, _stub_fabric: Any) -> None:
        fabrics = _stub_fabric
        write = provision_domain("audit", "postgresql://localhost:5432/nse_audit")

        assert get_domain_backend("audit") is write
        read = get_domain_backend("audit", readonly=True)
        assert read is not None
        assert read is not write
        assert fabrics["audit"].write_backend("audit") is write
        assert fabrics["audit"].read_backend("audit") is read

    def test_read_plane_is_not_write_shaped(self, _stub_fabric: Any) -> None:
        """The audit read guard refuses a backend exposing ``execute``."""
        provision_domain("audit", "postgresql://localhost:5432/nse_audit")
        read = get_domain_backend("audit", readonly=True)

        assert hasattr(read, "query")
        assert hasattr(read, "query_one")
        assert hasattr(read, "scalar")
        assert not hasattr(read, "execute")

    def test_read_pool_is_actually_opened(self, _stub_fabric: Any) -> None:
        provision_domain("audit", "postgresql://localhost:5432/nse_audit")
        read = get_domain_backend("audit", readonly=True)

        assert read is not None
        assert read.opened == 1
        assert read.closed == 0
        assert read.query("SELECT 1") == [{"ok": 1}]
        assert read.query_one("SELECT 1") == {"ok": 1}
        assert read.scalar("SELECT 1") == 1

    def test_other_domains_are_not_affected(self, _stub_fabric: Any) -> None:
        provision_domain("audit", "postgresql://localhost:5432/nse_audit")

        assert get_domain_backend("ledger") is None
        assert get_domain_backend("ledger", readonly=True) is None

    def test_provision_returns_the_write_backend(self, _stub_fabric: Any) -> None:
        backend = provision_domain("audit", "postgresql://localhost:5432/nse_audit")

        assert isinstance(backend, _FakeWritePlane)
        # schema bootstrap ran through the write plane only
        assert backend.executed
        assert not hasattr(get_domain_backend("audit", readonly=True), "executed")


# =====================================================================
# Idempotency
# =====================================================================


class TestProvisionIsIdempotent:
    def test_reprovision_replaces_and_closes_both_pools(self, _stub_fabric: Any) -> None:
        first_write = provision_domain("audit", "postgresql://localhost:5432/nse_audit")
        first_read = get_domain_backend("audit", readonly=True)

        second_write = provision_domain("audit", "postgresql://localhost:5432/nse_audit")
        second_read = get_domain_backend("audit", readonly=True)

        assert second_write is not first_write
        assert second_read is not first_read
        # the old pools are closed exactly once, not leaked
        assert first_write.closed == 1
        assert first_read is not None and first_read.closed == 1
        # the new pools are live and open
        assert second_write.closed == 0
        assert second_read is not None and second_read.closed == 0

    def test_registry_holds_one_entry_per_domain(self, _stub_fabric: Any) -> None:
        provision_domain("audit", "postgresql://localhost:5432/nse_audit")
        provision_domain("audit", "postgresql://localhost:5432/nse_audit")

        assert list(fabric_mod._DOMAIN_BACKENDS) == ["audit"]
        entry = fabric_mod._DOMAIN_BACKENDS["audit"]
        assert isinstance(entry, _DomainBackends)
        assert entry.write is get_domain_backend("audit")
        assert entry.read is get_domain_backend("audit", readonly=True)

    def test_register_helpers_are_independent_slots(self) -> None:
        write = _FakeWritePlane()
        read = _FakeReadPlane()

        register_domain_backend("audit", write)
        assert get_domain_backend("audit") is write
        assert get_domain_backend("audit", readonly=True) is None

        register_domain_read_backend("audit", read)
        assert get_domain_backend("audit") is write
        assert get_domain_backend("audit", readonly=True) is read

        # replacing the write slot keeps the read plane
        other_write = _FakeWritePlane()
        register_domain_backend("audit", other_write)
        assert get_domain_backend("audit") is other_write
        assert get_domain_backend("audit", readonly=True) is read

        unregister_domain_backend("audit")
        assert get_domain_backend("audit") is None
        assert get_domain_backend("audit", readonly=True) is None


# =====================================================================
# Fail-closed
# =====================================================================


class TestProvisionFailsClosed:
    def test_read_pool_open_failure_raises_and_registers_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write = _FakeWritePlane()
        read = _BrokenPoolReadPlane()
        fabric = _RecordingFabric(write=write, read=read)
        monkeypatch.setattr(fabric_mod, "DatabaseFabric", lambda domain, cfg: fabric)
        monkeypatch.setattr(
            "nexus_scalp.database.migration.migrate_domain",
            lambda domain, exec_: {"applied": 0, "error_count": 0},
            raising=False,
        )

        with pytest.raises(RuntimeError, match="pg pool is not open"):
            provision_domain("audit", "postgresql://localhost:5432/nse_audit")

        # No write-only domain may be left registered.
        assert get_domain_backend("audit") is None
        assert get_domain_backend("audit", readonly=True) is None
        # The write pool opened on the way in is cleaned up.
        assert write.closed == 1
        assert fabric.closed == 1


# =====================================================================
# SQLite is untouched
# =====================================================================


class TestSQLitePathUntouched:
    def test_sqlite_fabric_open_never_registers_pooled_backends(self, tmp_path: Any) -> None:
        """``provision_domain`` is never the SQLite path: an SQLite fabric's
        planes are owned by ``open()`` and stay out of the pooled registry."""
        from nexus_scalp.database.fabric import FabricConfig

        cfg = FabricConfig.sqlite_default(workspace=str(tmp_path))
        fabric = DatabaseFabric("audit", cfg.for_domain("audit"))
        try:
            fabric.open()
            assert get_domain_backend("audit") is None
            assert get_domain_backend("audit", readonly=True) is None
            assert get_domain_backend("audit", readonly=False) is None
        finally:
            fabric.close()

    def test_provision_domain_is_postgresql_only(self) -> None:
        """``provision_domain`` must stay unreachable from SQLite: it builds
        a PostgreSQL fabric config and never branches on the provider."""
        source = _module_source()
        body = source[source.index("def provision_domain") :]
        assert "FabricConfig.for_postgresql(" in body
        assert "is_sqlite" not in body
        assert "sqlite_" not in body

    def test_registry_entries_hold_both_planes_only(self) -> None:
        register_domain_backend("audit", _FakeWritePlane())
        entry = fabric_mod._DOMAIN_BACKENDS["audit"]
        assert isinstance(entry, _DomainBackends)
        assert entry.read is None
        unregister_domain_backend("audit")


def _module_source() -> str:
    import inspect

    return inspect.getsource(fabric_mod)
