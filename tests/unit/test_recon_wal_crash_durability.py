"""RECON CRITICAL GATE — WAL mode + crash durability (Tier 1).

Verified gap from the DB forensic lane (2026-09-14): `journal_mode` appears
NOWHERE in tests (grep over tests/ produced one comment) — the "SQLite WAL +
background writer" claim was asserted only in production code
(audit_repository.py:283-286), never proven. A silent migration off WAL
(e.g. a PR setting journal_mode=DELETE) degrades concurrent-reader behavior on
the live trading DB with zero test signal.

Deterministic: fresh temp DBs only, no shared machine state. The crash
simulation runs the writer in a CHILD PROCESS that hard-exits (os._exit)
after flush() — skipping every cleanup handler, exactly like a killed trading
process — and the parent then reopens the file and proves durability.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
from datetime import UTC, datetime

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TradeProposal

_CRASH_PROBE = textwrap.dedent(
    """
    import os, sys
    from datetime import UTC, datetime
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.domain.enums import ActionType
    from nexus_scalp.domain.models import TradeProposal

    db = sys.argv[1]
    repo = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.05)
    for i in range(6):
        p = TradeProposal(
            request_id=f"CRASH-{i}",
            execution_id=f"EXEC-CRASH-{i}",
            symbol="XAUUSD",
            generated_at=datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC),
            action=ActionType.BUY,
            confidence=0.8,
            proposed_entry=4400.0,
            stop_loss=4390.0,
            take_profit=4420.0,
            risk_reward_ratio=2.0,
            # distinct decision identity: same-minute identical proposals are
            # deduped by _signal_dedup_key BY DESIGN (BUG-054)
            reason_code=f"MODEL_SIGNAL_PROBE_{i}",
        )
        repo.log_signal(p)
    ok = repo.flush(timeout_sec=15.0)
    # HARD EXIT: no close(), no atexit, no WAL checkpoint — process death.
    os._exit(0 if ok else 3)
    """
)


def _proposal(rid: str) -> TradeProposal:
    return TradeProposal(
        request_id=rid,
        execution_id=f"EXEC-{rid}",
        symbol="XAUUSD",
        generated_at=datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC),
        action=ActionType.BUY,
        confidence=0.8,
        proposed_entry=4400.0,
        stop_loss=4390.0,
        take_profit=4420.0,
        risk_reward_ratio=2.0,
    )


def _journal_mode(db) -> str:
    con = sqlite3.connect(db)
    try:
        return str(con.execute("PRAGMA journal_mode;").fetchone()[0]).upper()
    finally:
        con.close()


def test_file_backed_repository_boots_in_wal_mode(tmp_path):
    """Production boot must leave the audit DB in WAL journal mode — the
    concurrency contract the tick hot path and every reader depend on."""
    db = tmp_path / "audit.db"
    repo = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.05)
    try:
        repo.log_signal(_proposal("WAL-1"))
        assert repo.flush(timeout_sec=10.0) is True
    finally:
        repo.close()
    # Reconnect AFTER graceful close: the persisted journal mode is WAL
    # (SQLite remembers it in the DB header for WAL databases).
    assert _journal_mode(db) == "WAL"


def test_flushed_rows_survive_process_death(tmp_path):
    """Committed-before-crash is durable: the child process flushes (queue
    drained == sqlite COMMIT) and hard-exits via os._exit(). The reopened
    file must contain every decision row — read-after-write durability under
    real crash semantics, which no in-process close() test can falsify."""
    db = tmp_path / "crash.db"
    probe = tmp_path / "probe.py"
    probe.write_text(_CRASH_PROBE, encoding="utf-8")
    src_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    env = dict(os.environ, PYTHONPATH=src_root, PYTHONDONTWRITEBYTECODE="1")
    # NEXUS_AUDIT_DB must NOT leak from the conftest isolation fixture into the
    # probe (it must write EXACTLY this file).
    env.pop("NEXUS_AUDIT_DB", None)
    res = subprocess.run(
        [sys.executable, str(probe), str(db)],
        timeout=120,
        capture_output=True,
        text=True,
        env=env,
        check=False,  # we inspect returncode + stderr explicitly below
    )
    assert res.returncode == 0, f"probe failed rc={res.returncode}: {res.stderr[-800:]}"

    con = sqlite3.connect(db)
    try:
        n = con.execute("SELECT COUNT(*) FROM audit_signals").fetchone()[0]
    finally:
        con.close()
    assert n == 6, f"only {n}/6 flushed decision rows survived the process crash"
    # The crash left WAL sidecars behind (proof the writer never checkpointed
    # gracefully) yet SQLite replays them on first open — durability holds.
    con = sqlite3.connect(db)
    try:
        assert con.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    finally:
        con.close()


def test_in_memory_shared_cache_repo_never_anchors_junk_file_in_cwd(tmp_path, monkeypatch):
    """BUG-156 lineage, journal-branch pin: ':memory:' URIs rewrite to
    file::memory:?cache=shared and must take the journal_mode=MEMORY branch —
    a stray .db/.db-wal beside the CWD is machine pollution and silent
    cross-run coupling."""
    monkeypatch.chdir(tmp_path)
    repo = AuditRepository(db_url="sqlite:///:memory:", flush_interval_sec=0.05)
    try:
        repo.log_signal(_proposal("MEM-1"))
        assert repo.flush(timeout_sec=5.0) is True
    finally:
        repo.close()
    leaked = [p for p in tmp_path.iterdir() if p.suffix in {".db", ".wal", ".shm"}]
    assert not leaked, f"in-memory repo wrote files: {leaked}"
