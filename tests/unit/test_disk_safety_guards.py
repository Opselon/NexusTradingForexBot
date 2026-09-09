"""DISK-SAFETY REGRESSIONS (2026-09-09 disk-leak mission).

The host filled its disk twice in one day from TWO repo-side leaks:

1. Core dumps: a crashing interpreter (Linux teardown abort,
   NX-RUNTIMEGATE-ABORT class) wrote multi-GB core files under
   /var/lib/apport/coredump — 4GB/day on a 48GB host. conftest.py +
   scripts/ci/runtime_gate.py now set RLIMIT_CORE=0 at import so neither
   pytest runs nor gate children can ever dump core.

2. In-memory audit DB junk file: with the shared in-memory audit URI
   ("file::memory:?cache=shared"), every raw sqlite3.connect(...) call that
   omitted uri=True treated the URI STRING as a literal FILE NAME, creating
   a junk file named 'file::memory:?cache=shared' in the process CWD on
   each flush/dead-letter/read of an in-memory repository. All connect
   sites now route through AuditRepository._connect_sqlite (single URI
   contract) and the dead-letter factory passes uri=True.

These tests pin both contracts at the unit level.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository

_CWD_JUNK = "file::memory:?cache=shared"


# ---------------------------------------------------------------------------
# 1. core dumps disabled under pytest (and gate children)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="resource module is POSIX-only")
def test_core_dumps_disabled_under_pytest() -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX guard (belt to skipif)
        pytest.skip("no resource module")

    soft, _hard = resource.getrlimit(resource.RLIMIT_CORE)
    assert soft == 0, (
        "tests/conftest.py must set RLIMIT_CORE=0 — a crashed interpreter "
        "otherwise writes multi-GB core dumps (disk-leak incident 2026-09-09)"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="resource module is POSIX-only")
def test_gate_children_inherit_zero_core_limit() -> None:
    """The limit set in conftest is inherited by every child process (the
    same mechanism protects runtime_gate-spawned stages via its own guard)."""
    code = "import resource; print(resource.getrlimit(resource.RLIMIT_CORE)[0])"
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert out.stdout.strip() == "0"


# ---------------------------------------------------------------------------
# 2. in-memory audit DB must not leak a literal URI-named file into CWD
# ---------------------------------------------------------------------------


def _cwd_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_memory_uri_repo_never_creates_literal_uri_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full write->flush->read->dead-letter lifecycle in :memory: mode leaves
    NO junk file named after the URI in the working directory."""
    ws = _cwd_snapshot(tmp_path, monkeypatch)
    repo = AuditRepository(db_url="sqlite:///:memory:")
    try:
        repo.log_order(
            order_id="diskleak-t1",
            ticket=1,
            symbol="EURUSD",
            action="BUY",
            price=1.10,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            reason="disk leak regression",
        )
        repo._queue.join()
        # dead-letter path (would previously sqlite3.connect the URI literally)
        repo.record_dead_letter(
            query="INSERT INTO nope VALUES (?)",
            args=("x",),
            error=RuntimeError("diskleak probe"),
            payload_note="unit probe",
        )
        repo.flush(timeout_sec=5.0)
    finally:
        repo.close()

    leaked = ws / _CWD_JUNK
    assert not leaked.exists(), (
        "in-memory audit DB created a literal 'file::memory:?cache=shared' "
        "file in CWD — a raw sqlite3.connect call is missing uri=True"
    )
    assert not Path(_CWD_JUNK).exists()


def test_every_repo_connect_site_honors_uri_contract(tmp_path: Path) -> None:
    """A file-backed repo works through the SAME helper (no regression for
    the real artifacts/audit.db path) and the helper adds uri only for
    file: URIs."""
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'file_mode.db'}")
    try:
        repo.log_order(
            order_id="diskleak-t2",
            ticket=2,
            symbol="EURUSD",
            action="BUY",
            price=1.10,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            reason="file mode regression",
        )
        repo.flush(timeout_sec=5.0)
        conn = sqlite3.connect(f"file:{tmp_path / 'file_mode.db'}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM audit_orders WHERE order_id='diskleak-t2'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert rows == 1
    finally:
        repo.close()


def test_dead_letter_factory_uses_uri_for_memory_paths() -> None:
    """The dead-letter store borrowed from an in-memory repo must reconnect
    with uri=True (a plain sqlite3.connect would create the literal file)."""
    repo = AuditRepository(db_url="sqlite:///:memory:")
    try:
        assert repo.dead_letter_store._db_path == "file::memory:?cache=shared"
        assert repo.dead_letter_store._conn_factory is not sqlite3.connect
        # and it actually works without touching the filesystem:
        ok = repo.dead_letter_store.record(
            query="INSERT INTO nope VALUES (?)",
            args=("x",),
            error=RuntimeError("uri factory probe"),
            payload_note="unit probe",
        )
        assert ok is True
    finally:
        repo.close()
