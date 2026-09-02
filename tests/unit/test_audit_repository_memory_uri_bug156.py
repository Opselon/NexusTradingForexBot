"""BUG-156 regression guard: in-memory SQLite URIs must never be workspace-anchored.

d21df07 (BUG-149) anchored every relative SQLite path to the runtime workspace
and caught `:memory:` in the net, turning `sqlite:///:memory:` into
`<CWD>/:memory:` -> OperationalError. The anchoring guard must exclude
in-memory pseudo-URIs; file-backed relative paths stay anchored (BUG-149
behavior preserved).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.release import paths as release_paths


def _anchor_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point get_runtime_workspace() at tmp_path to observe the anchor decision."""
    monkeypatch.setattr(release_paths, "get_runtime_workspace", lambda: tmp_path)
    return tmp_path


def test_bug156_memory_uri_is_not_anchored_and_fully_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sqlite:///:memory: constructs, accepts a write, and persists to readback."""
    ws = _anchor_probe(tmp_path, monkeypatch)
    repo = AuditRepository(db_url="sqlite:///:memory:")
    try:
        assert repo._db_path == "file::memory:?cache=shared"
        repo.log_order(
            order_id="bug156-t1",
            ticket=156001,
            symbol="EURUSD",
            action="BUY",
            price=1.10,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            reason="bug156 regression",
        )
        repo._queue.join()
        import sqlite3

        conn = sqlite3.connect("file::memory:?cache=shared", uri=True)
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM audit_orders WHERE order_id='bug156-t1'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert rows == 1, "in-memory audit DB must persist the order via the worker queue"
        # the workspace must NOT have received a bogus anchored file
        assert not (ws / ":memory:").exists()
    finally:
        repo.close()


def test_bug156_file_uri_scheme_is_not_anchored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """file: URIs are already explicit; the anchor must not touch them."""
    _anchor_probe(tmp_path, monkeypatch)
    repo = AuditRepository(db_url="sqlite:///file::memory:?cache=shared")
    try:
        assert repo._db_path == "file::memory:?cache=shared"
    finally:
        repo.close()


def test_bug149_relative_file_path_still_anchored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUG-149 behavior preserved: relative FILE paths anchor to the workspace."""
    ws = _anchor_probe(tmp_path, monkeypatch)
    repo = AuditRepository(db_url="sqlite:///artifacts/audit_bug149_probe.db")
    try:
        assert repo._db_path == str(ws / "artifacts" / "audit_bug149_probe.db")
        assert (ws / "artifacts").is_dir(), "anchored artifact dir must be created"
    finally:
        repo.close()
