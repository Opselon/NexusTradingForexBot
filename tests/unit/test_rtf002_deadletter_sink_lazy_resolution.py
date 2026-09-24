"""RTF-002 regression — the PostgreSQL dead-letter write sink must not be
permanently ``None`` because of construction order.

The 2026-09-24 runtime log is flooded with::

    DEAD-LETTER WRITE IMPOSSIBLE — no write sink on a non-SQLite
    dead-letter store; financial record unrecoverable.

because ``AuditRepository.__init__`` builds the ``DeadLetterStore`` (which
captures ``_dead_letter_write_sink()``) BEFORE ``_build_write_plane()`` runs,
and the write plane is what provisions the audit domain on the fabric. The
capture observed ``get_domain_backend("audit") is None`` on a fresh process,
cached it forever, and every failed row was then unrecoverable.

The sink must resolve the backend lazily, at call time.

NOTE: no network. ``_build_pooled_write_backend`` is stubbed so constructing
the repository never dials the (fake) PostgreSQL DSN.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.database.fabric import (
    get_domain_backend,
    register_domain_backend,
    unregister_domain_backend,
)

PG_URL = "postgresql://user:secret@127.0.0.1:59999/nexusdb"


class _RecordingBackend:
    """Stands in for the fabric's pooled write backend (pg_planes shape:
    ``execute`` for the dead-letter sink, ``execute_one`` for the write
    plane's background worker / overflow replay)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.fail = False

    def execute(self, sql: str, args: tuple = ()) -> None:
        if self.fail:
            raise RuntimeError("provider down")
        self.calls.append((sql, args))

    def execute_one(self, query: str, args: tuple = ()) -> None:
        self.execute(query, args)

    def close(self) -> None:
        """No-op: the plane's worker closes its backend on shutdown."""
        return None


@pytest.fixture()
def pg_repo(monkeypatch: pytest.MonkeyPatch) -> AuditRepository:
    """A PostgreSQL AuditRepository whose write plane is satisfied by a stub
    backend, WITHOUT dialling the (unreachable, fake) DSN.

    ``_build_pooled_write_backend`` is stubbed to return the recording
    backend, which ``_build_write_plane`` accepts as the domain's pooled
    plane. No network is touched by any test in this module.
    """
    backend = _RecordingBackend()
    register_domain_backend("audit", backend)
    monkeypatch.setattr(
        AuditRepository,
        "_build_pooled_write_backend",
        lambda self: backend,
    )
    r = AuditRepository(db_url=PG_URL)
    yield r
    r.close()
    unregister_domain_backend("audit")


def test_sink_is_not_none_when_domain_unprovisioned() -> None:
    """The historic bug: ``_dead_letter_write_sink`` captured the backend at
    construction; when the domain was unprovisioned it returned ``None``,
    which the store treats as 'no write sink' and every failed row became
    unrecoverable. The sink must be returned regardless of provisioning state
    so the store has somewhere to put the row the moment the domain comes up.

    Constructed on a bare repository (no plane, no registry): this is exactly
    what ``AuditRepository.__init__`` observed before the write plane existed.
    """
    unregister_domain_backend("audit")
    bare = object.__new__(AuditRepository)
    bare._is_sqlite = False  # type: ignore[attr-defined]
    sink = bare._dead_letter_write_sink()
    assert sink is not None, (
        "sink captured None from an unprovisioned domain — "
        "DEAD-LETTER WRITE IMPOSSIBLE would fire for every failed row"
    )
    assert callable(sink)
    # With no backend registered it reports failure, never fakes success.
    assert sink("INSERT INTO audit_signals (x) VALUES (?)", (1,)) is False


def test_sink_resolves_backend_lazily_after_provisioning() -> None:
    """The core contract: unprovisioned at first call, then the write plane
    provisions the domain — the SAME sink object must start landing rows.
    A construction-captured ``None`` can never recover; a lazy resolve can."""
    unregister_domain_backend("audit")
    repo = object.__new__(AuditRepository)
    repo._is_sqlite = False  # type: ignore[attr-defined]
    sink = repo._dead_letter_write_sink()

    assert sink("INSERT INTO audit_signals (x) VALUES (?)", (1,)) is False

    backend = _RecordingBackend()
    register_domain_backend("audit", backend)

    assert sink("INSERT INTO audit_signals (x) VALUES (?)", (2,)) is True
    assert len(backend.calls) == 1
    assert backend.calls[0][1] == (2,)
    unregister_domain_backend("audit")


def test_sink_failure_is_returned_false_not_raised(
    pg_repo: AuditRepository,
) -> None:
    """A provider error inside the sink must not escape into the caller — the
    dead-letter path is itself a recovery path and ``record()`` never raises."""
    sink = pg_repo._dead_letter_write_sink()
    backend = get_domain_backend("audit", readonly=False)
    assert isinstance(backend, _RecordingBackend)
    backend.fail = True
    assert sink("INSERT INTO audit_signals (x) VALUES (?)", (3,)) is False


def test_sqlite_domain_still_has_no_sink() -> None:
    """The SQLite path keeps its own conn_factory sink; returning one here
    would double-write."""
    r = AuditRepository(db_url="sqlite:///:memory:")
    try:
        assert r._dead_letter_write_sink() is None
    finally:
        r.close()


def test_store_receives_the_sink_and_lands_a_row(
    pg_repo: AuditRepository,
) -> None:
    """End-to-end through the composed store: a PostgreSQL repository's
    dead-letter store was handed a real sink at construction, and a failed
    audit row is persisted through the fabric instead of being lost. This is
    the full path the runtime log showed failing."""
    store = pg_repo.dead_letter_store
    assert store is not None
    assert store._write_sink is not None, (
        "DeadLetterStore was constructed with write_sink=None"
    )

    backend = get_domain_backend("audit", readonly=False)
    assert isinstance(backend, _RecordingBackend)

    ok = store.record(
        query="INSERT INTO audit_signals (request_id) VALUES (?)",
        args=("r-1",),
        error=RuntimeError('column "signal_dedup_key" does not exist'),
    )
    assert ok is True, "dead-letter row was not persisted to the sink"
    assert len(backend.calls) == 1
    assert backend.calls[0][0].strip().upper().startswith(
        "INSERT INTO AUDIT_DEAD_LETTER"
    )
