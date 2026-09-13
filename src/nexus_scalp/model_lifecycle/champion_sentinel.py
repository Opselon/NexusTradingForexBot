"""BUG-257 (remainder): champion-drift SENTINEL — between-boot detection of
out-of-process rewrites of the serving artifact.

Context
=======
The P0-2 trust anchor (``application/live/model_bundle_store.
_verify_champion_registry_binding``) compares the serving ``model.pt`` bytes
against the governed CHAMPION fingerprint at BOOT only. BUG-271 gave every
GOVERNED in-place replacement an attribution trail (provenance re-registration
+ champion supersession, logged CHAMPION_FINGERPRINT_SUPERSEDED). The residual
gap is DETECTION while the engine runs: if a foreign process re-lands drifted
or tampered bytes on the serving path (the exact BUG-257 shape — the bb1f0afe
drift re-appeared at 2026-09-11 04:13 from an unidentified writer), the running
engine serves poisoned bytes silently and the refusal only fires at the NEXT
boot — after unbounded trading on unverified weights.

Semantics
=========
* ALERT-ONLY. This module NEVER blocks, mutates, quarantines, or halts. The
  enforcement path stays the boot anchor (INV-015 fail-closed load); the
  sentinel converts a between-boot drift into an observable, timestamped
  alarm (structured CRITICAL log + Telegram, throttled by the maintenance
  cycle that owns it).
* Same comparison the anchor makes: sha256-prefix-16 of the serving artifact
  (``fingerprint_artifact``) vs the newest CHAMPION row's
  ``artifact_fingerprint``. MATCH / DRIFT / INERT mirror the anchor's
  postures (INERT = no champion row / no fingerprint / artifact absent —
  never a false alarm on cold states).
* Governed-writer races are absorbed UPSTREAM by the caller: a DRIFT verdict
  only alerts after a SECOND consecutive sighting (see
  ``MaintenanceCycle``), because a governed replacement queues its registry
  update on the FIFO audit worker and a sentinel read between the file write
  and the queued supersession must not cry wolf.

Import discipline: this module stays torch/polars/pydantic-free at module
scope (maintenance-path gating lesson); registry status values and the
fingerprint helper are imported lazily inside the probe.
"""

from __future__ import annotations

import contextlib
import sqlite3
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.champion_sentinel")

#: Verdicts (string constants; the maintenance stage + tests pin these).
STATUS_MATCH = "MATCH"
STATUS_DRIFT = "DRIFT"
STATUS_INERT = "INERT"


def evaluate_champion_drift(
    *,
    champion_row: dict[str, Any] | None,
    serving_fingerprint: str,
    serving_path: str = "",
) -> dict[str, Any]:
    """PURE verdict (no I/O): governed CHAMPION fingerprint vs serving bytes.

    Mirrors the boot trust anchor's comparison exactly so an alert here means
    "the boot anchor would refuse right now". INERT postures (no row, empty
    fingerprint, absent/unreadable artifact) never escalate to DRIFT — cold
    starts and non-champion artifacts are normal states.
    """
    if champion_row is None:
        return {"status": STATUS_INERT, "reason": "no_champion_row"}
    governed = str(champion_row.get("artifact_fingerprint", "") or "").strip().lower()
    base: dict[str, Any] = {
        "governed_sha16": governed,
        "serving_sha16": str(serving_fingerprint or "").strip().lower(),
        "serving_path": str(serving_path or ""),
        "champion_row_model_id": str(champion_row.get("model_id", "") or ""),
        "champion_registered_at": str(champion_row.get("registered_at", "") or ""),
    }
    if not governed:
        return {**base, "status": STATUS_INERT, "reason": "champion_row_has_no_fingerprint"}
    if not base["serving_sha16"]:
        return {**base, "status": STATUS_INERT, "reason": "serving_artifact_absent_or_unreadable"}
    if base["serving_sha16"] == governed:
        return {**base, "status": STATUS_MATCH}
    return {
        **base,
        "status": STATUS_DRIFT,
        "reason": (
            f"serving sha16 {base['serving_sha16']} != governed CHAMPION {governed} "
            "(out-of-process rewrite suspected; boot anchor would refuse)"
        ),
    }


def _serving_artifact_path(om: Any) -> str:
    """The artifact actually serving: the loaded bundle's own path when a
    bundle exists (hot-swap safe), else the configured model artifact path."""
    bundle = None
    with contextlib.suppress(Exception):
        lock = getattr(om, "_bundle_lock", None)
        if lock is not None:
            with lock:
                bundle = getattr(om, "_bundle", None)
        else:
            bundle = getattr(om, "_bundle", None)
    path = getattr(bundle, "artifact_path", None) if bundle is not None else None
    if not path:
        cfg = getattr(om, "config", None)
        path = getattr(getattr(cfg, "model", None), "model_artifact_path", None)
    return str(path or "")


def probe_champion_drift(om: Any) -> dict[str, Any]:
    """I/O leg: read the newest CHAMPION row + fingerprint the serving bytes.

    Reads through the URI-aware single connect site (``audit._connect_sqlite``)
    so ``file:`` URI audit paths work exactly like the live engine's other
    off-tick readers. Returns an INERT verdict (never raises) when the audit
    surface is not a readable SQLite repository. Runs OFF the tick path
    (asyncio.to_thread by the maintenance caller).
    """
    audit = getattr(om, "audit", None)
    if audit is None or not getattr(audit, "_is_sqlite", False):
        return {"status": STATUS_INERT, "reason": "no_sqlite_audit"}
    serving_path = _serving_artifact_path(om)
    if not serving_path:
        return {"status": STATUS_INERT, "reason": "no_serving_artifact_path"}
    try:
        from nexus_scalp.model_lifecycle.models import ModelStatus

        conn = audit._connect_sqlite(5.0)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT model_id, model_version, artifact_fingerprint, artifact_path, "
                "registered_at FROM experience_model_registry "
                "WHERE lifecycle_status=? ORDER BY registered_at DESC LIMIT 1;",
                (ModelStatus.CHAMPION.value,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as e:
        # Registry unreadable -> INERT (the boot anchor keeps its own
        # fail-closed posture; the sentinel must never invent an alarm).
        return {"status": STATUS_INERT, "reason": "registry_read_failed", "detail": str(e)[:200]}
    from nexus_scalp.experience.provenance import fingerprint_artifact

    return evaluate_champion_drift(
        champion_row=dict(row) if row is not None else None,
        serving_fingerprint=fingerprint_artifact(serving_path),
        serving_path=serving_path,
    )
