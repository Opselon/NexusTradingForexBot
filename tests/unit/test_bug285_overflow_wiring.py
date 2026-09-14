"""NSE-Swarm role-10 wiring pin: the BUG-285 financial-overflow drain must be
hooked to the audit worker's idle pass (the boot-only retention trap)."""

from __future__ import annotations

import inspect

from nexus_scalp.adapters.database.audit_repository import AuditRepository


def test_maintenance_wires_the_overflow_recovery_surface() -> None:
    """The recovery counters are only meaningful if the drain actually runs in
    a live process. _process_queue_worker is the one loop guaranteed to tick
    for the engine's lifetime (started from AuditRepository.__init__, kept
    alive by every producer); the drain MUST fire from its idle branch.
    """
    worker = inspect.getsource(AuditRepository._process_queue_worker)
    assert "_drain_financial_overflow_due(conn)" in worker
    # idle-branch placement: the scan runs only when the queue produced no
    # batch, so a busy writer never pays the directory scan.
    idle_at = worker.find("if not batch:")
    call_at = worker.find("_drain_financial_overflow_due(conn)")
    assert idle_at != -1 and call_at > idle_at


def test_drain_never_touches_the_tick_pipeline() -> None:
    """INV-001 guard: overflow recovery is confined to the audit worker. No
    live-loop / tick-pipeline module may call the drain directly."""
    from pathlib import Path

    import nexus_scalp.application as app_pkg

    live_dir = Path(app_pkg.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(live_dir.rglob("*.py")):
        if "_drain_financial_overflow_due" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == [], offenders
