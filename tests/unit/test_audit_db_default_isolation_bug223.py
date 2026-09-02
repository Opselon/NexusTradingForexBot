"""BUG-223 regression guard: the AuditRepository IMPLICIT default must never
resolve into the production artifacts tree under pytest.

Production evidence (artifacts/audit.db, 2026-08-31..09-02): unit tests
constructing OrderLifecycleManager without audit_repo (test_hardened_protocol
safety-state machine, etc.) appended 957 test_req_N rows (XAUUSD price=2000.0,
REJECTED) to the live trading ledger, because the implicit default
"sqlite:///artifacts/audit.db" is BUG-149-anchored to the runtime workspace,
which IS the repo root for every pytest run.

The seal has two halves; both must hold:
  1. conftest.py sets NEXUS_AUDIT_DB per pytest run, and the repository honors
     it for the implicit default ONLY (explicit db_url/config callers keep
     authority - NEXUS_AUDIT_DB must never hijack them);
  2. the implicit default keeps its BUG-149 workspace anchoring when no
     override is set (packaged/CLI behavior unchanged).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.adapters.database import audit_repository as ar
from nexus_scalp.adapters.database.audit_repository import AuditRepository


@pytest.fixture()
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NEXUS_AUDIT_DB", raising=False)
    return monkeypatch


def test_conftest_isolates_implicit_default_away_from_repo_artifacts() -> None:
    """Under the real pytest run, NEXUS_AUDIT_DB is set by conftest and points
    OUTSIDE the repo artifacts tree."""
    env_db = os.environ.get("NEXUS_AUDIT_DB", "")
    assert env_db, "conftest must set NEXUS_AUDIT_DB for every pytest run"
    assert "audit_db" in env_db  # tmp_path_factory.mktemp("audit_db") run dir
    assert not Path(env_db).resolve().is_relative_to(Path("artifacts").resolve()), (
        "implicit-default target must not live in the repo artifacts tree"
    )


def test_implicit_default_honors_env_override(tmp_path: Path, _clean_env) -> None:
    """A bare AuditRepository() with NEXUS_AUDIT_DB set lands in the temp file,
    NOT in the workspace artifacts tree (the 957-row contamination path)."""
    target = tmp_path / "audit.db"
    _clean_env.setenv("NEXUS_AUDIT_DB", str(target))
    # Default flush interval: the worker's q.get wait is the flush interval, so
    # a huge interval would starve the worker and stall flush()/close().
    repo = AuditRepository()
    try:
        assert Path(repo._db_path) == target, (
            f"implicit default must resolve to NEXUS_AUDIT_DB, got {repo._db_path}"
        )
        # Worker round-trip proves the sealed path is the one actually used.
        repo.log_order(
            order_id="bug223-t1",
            ticket=223001,
            symbol="EURUSD",
            action="BUY",
            price=1.10,
            stop_loss=1.09,
            take_profit=1.12,
            volume=0.01,
            reason="bug223 regression",
        )
        assert repo.flush(timeout_sec=5.0), "queued order row must drain via flush()"
        conn = sqlite3.connect(target)
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM audit_orders WHERE order_id='bug223-t1'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert rows == 1
    finally:
        repo.close()


def test_env_override_never_hijacks_explicit_callers(tmp_path: Path, _clean_env) -> None:
    """NEXUS_AUDIT_DB is an override for the IMPLICIT default only: an explicit
    db_url caller must keep its own path even with the env var set."""
    env_target = tmp_path / "env.db"
    explicit = tmp_path / "explicit.db"
    _clean_env.setenv("NEXUS_AUDIT_DB", str(env_target))
    repo = AuditRepository(db_url=f"sqlite:///{explicit.as_posix()}")
    try:
        assert Path(repo._db_path) == explicit
        assert not env_target.exists(), "env override must not be consulted"
    finally:
        repo.close()


def test_implicit_default_without_env_keeps_bug149_anchoring(
    tmp_path: Path, _clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No override set => the legacy default still anchors to the runtime
    workspace (BUG-149 behavior preserved for packaged/CLI runs)."""
    import nexus_scalp.release.paths as release_paths

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(release_paths, "get_runtime_workspace", lambda: ws)
    # Default flush interval (see test_implicit_default_honors_env_override).
    repo = AuditRepository()
    try:
        assert Path(repo._db_path) == ws / "artifacts" / "audit.db"
    finally:
        repo.close()
