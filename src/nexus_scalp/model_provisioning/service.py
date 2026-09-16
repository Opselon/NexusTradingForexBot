"""Provisioning SERVICE — serving-slot truth, origins, and first-run flow.

Single authority for "what is in the serving slot and why". The acquisition
origin is recorded LOCALLY (install-state sidecar) because the signed
manifest of an official bundle cannot carry local install metadata without
breaking its signature; the sidecar lives next to the bundle and is
re-derived conservatively whenever it is absent or disagrees with disk.

Origins (honest classification, drives every first-setup label):

    OFFICIAL        PATH A — signed official bundle, fully verified chain
    USER_TRAINED    PATH B — locally trained, all gates passed, installed
    GOVERNED        registry CHAMPION binding present (promotion lifecycle)
    DEV_STARTER     release.bootstrap deterministic mint — explicitly a
                    DEVELOPMENT/RECOVERY starter, never presented as the
                    user's production model
    UNKNOWN         a bundle exists but provenance cannot be established
    MISSING/UNSERVABLE

The dev starter REMAINS available (offline boots, CI, emergency recovery)
but the first-run coordinator prefers OFFICIAL, then USER-TRAINED, and the
starter path is loud + labeled, per the operator redesign directive.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.model_provisioning import official as official_mod
from nexus_scalp.model_provisioning.states import LifecycleState
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_provisioning.service")

ORIGIN_OFFICIAL = "OFFICIAL"
ORIGIN_USER_TRAINED = "USER_TRAINED"
ORIGIN_GOVERNED = "GOVERNED"
ORIGIN_DEV_STARTER = "DEV_STARTER"
ORIGIN_UNKNOWN = "UNKNOWN"

#: sidecar written next to the serving bundle (never inside the signed manifest)
INSTALL_STATE_FILE = "install-state.json"

#: starter note markers understood from release.bootstrap / provision_model
_STARTER_MARKERS = ("release bootstrap", "docker entrypoint provisioned", "PAPER starter")


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def serving_model_path() -> Path:
    from nexus_scalp.configuration.config import AppConfig
    from nexus_scalp.release import bootstrap as rb

    return rb.canonical_starter_path(AppConfig())


def candidate_root() -> Path:
    from nexus_scalp.release.paths import get_runtime_workspace

    return get_runtime_workspace() / "artifacts" / "model_generation" / "models"


def candidate_dir() -> Path:
    """Isolated candidate dir for local training (never the serving slot)."""
    d = candidate_root() / f"local_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def install_state_path(model_path: Path | None = None) -> Path:
    return (model_path or serving_model_path()).parent / INSTALL_STATE_FILE


def read_install_state(model_path: Path | None = None) -> dict[str, Any]:
    p = install_state_path(model_path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_install(
    *,
    model_path: Path,
    origin: str,
    bundle_id: str = "",
    model_version: str = "",
    model_sha256: str = "",
    installed_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the local install-state sidecar (origin + provenance facts)."""
    state = {
        "schema": "nexus_install_state_v1",
        "origin": origin,
        "bundle_id": bundle_id,
        "model_version": model_version,
        "model_sha256": model_sha256,
        "installed_at": installed_at or utcnow_iso(),
        "updated_at": utcnow_iso(),
    }
    if extra:
        state.update(extra)
    try:
        model_path.parent.mkdir(parents=True, exist_ok=True)
        install_state_path(model_path).write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("[PROVISION] event=INSTALL_STATE_WRITE_FAILED %s", exc)
    return state


def _bundle_note(model_path: Path) -> str:
    m = model_path.parent / "manifest.json"
    try:
        return str(json.loads(m.read_text(encoding="utf-8")).get("note", ""))
    except (OSError, ValueError):
        return ""


def _starter_record(model_path: Path) -> bool:
    """True when manifest.json identifies a seam-owned starter: either the
    release.model_bootstrap provisioner marker (canonical, BUG-296 seam) or
    a known starter note (docker provisioner, historical bundles)."""
    m = model_path.parent / "manifest.json"
    try:
        record = json.loads(m.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(record, dict):
        return False
    from nexus_scalp.release import model_bootstrap as mb

    if str(record.get("provisioner") or "") == mb.PROVISIONER_MARKER:
        return True
    note = str(record.get("note", "")).lower()
    return any(marker.lower() in note for marker in _STARTER_MARKERS)


def _looks_like_starter(model_path: Path) -> bool:
    return _starter_record(model_path)


def _champion_bound(model_path: Path) -> bool:
    """Registry CHAMPION row fingerprint matches the on-disk artifact.

    Reuses the engine's own trust-anchor query shape (audit DB,
    experience_model_registry) — inert-False when no registry is readable
    (fresh installs have none; that alone never proves ORIGIN_GOVERNED)."""
    import hashlib

    try:
        import sqlite3

        from nexus_scalp.database.audit_repository import AuditRepository

        audit = AuditRepository()
        if not getattr(audit, "_is_sqlite", False):
            return False
        db = getattr(audit, "_db_path", None)
        if not db:
            return False
        conn = sqlite3.connect(db, timeout=3.0)
        try:
            row = conn.execute(
                "SELECT artifact_fingerprint FROM experience_model_registry "
                "WHERE lifecycle_status='CHAMPION' ORDER BY registered_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        governed = str((row or [""])[0] or "").strip().lower()
        if not governed or not model_path.exists():
            return False
        h = hashlib.sha256()
        with open(model_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16] == governed
    except Exception:
        return False


class SlotClassification:
    """What the serving slot holds: state + origin + facts."""

    def __init__(
        self, state: LifecycleState, origin: str, detail: str, path: Path, sha256: str = ""
    ) -> None:
        self.state = state
        self.origin = origin
        self.detail = detail
        self.path = Path(path)
        self.sha256 = sha256

    @property
    def servable(self) -> bool:
        return self.state in (LifecycleState.VERIFIED, LifecycleState.READY)

    @property
    def is_starter(self) -> bool:
        return self.origin == ORIGIN_DEV_STARTER

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "origin": self.origin,
            "detail": self.detail,
            "path": str(self.path),
            "sha256": self.sha256[:16],
            "servable": self.servable,
            "starter": self.is_starter,
        }


def classify_serving_slot(model_path: Path | None = None) -> SlotClassification:
    """Disk truth + install-state + registry binding -> honest origin.

    Precedence: a bundle whose SHA256 matches the recorded install-state is
    that origin. A starter note in the manifest is OVERRIDING (a dev starter
    cannot launder itself into OFFICIAL by editing a sidecar — the note lives
    inside the signed/hashed manifest itself). Registry CHAMPION fingerprint
    match promotes origin to GOVERNED (governance beats acquisition path).
    No state file at all + digest-valid bundle => UNKNOWN (never assumed).
    """
    from nexus_scalp.release import bootstrap as rb

    p = Path(model_path or serving_model_path())
    status = rb.bundle_status(p)
    if status["state"] == rb.STATE_MISSING:
        return SlotClassification(LifecycleState.MISSING, "", status["detail"], p)
    if status["state"] in (rb.STATE_UNSERVABLE, rb.STATE_INVALID):
        st = (
            LifecycleState.REJECTED
            if status["state"] == rb.STATE_INVALID
            else LifecycleState.MISSING
        )
        return SlotClassification(st, "", status["detail"], p)

    sha = rb.sha256_file(p)
    install_state = read_install_state(p)
    origin = str(install_state.get("origin", "") or "")
    recorded_sha = str(install_state.get("model_sha256", "") or "")
    if origin and recorded_sha != sha:
        # sidecar disagrees with disk -> do not trust it
        origin = ""
    if _looks_like_starter(p):
        origin = ORIGIN_DEV_STARTER  # manifest note wins over any sidecar claim
    if not origin and _champion_bound(p):
        origin = ORIGIN_GOVERNED
    if not origin:
        origin = ORIGIN_UNKNOWN
    if origin == ORIGIN_DEV_STARTER:
        # Starter is servable but explicitly NOT production-truth: state is
        # VERIFIED (engine can load it) with the starter label surfaced.
        return SlotClassification(
            LifecycleState.VERIFIED, origin, "dev starter (offline/CI/emergency recovery)", p, sha
        )
    return SlotClassification(
        LifecycleState.READY, origin, "bundle verified through engine gates", p, sha
    )


def install_candidate(trained_model: Path, *, origin: str, note: str = "") -> dict[str, Any]:
    """Governed install of a locally trained candidate into the serving slot.

    Refuses unless the current occupant is MISSING, a DEV STARTER, or
    unservable. A GOVERNED/OFFICIAL/USER occupant is never displaced here —
    that requires the promotion lifecycle. Candidate is verified in staging
    BEFORE any swap; the swap writes the manifest LAST."""
    import os

    from nexus_scalp.release import bootstrap as rb

    serving = serving_model_path()
    current = classify_serving_slot(serving)
    replaceable = (
        current.state == LifecycleState.MISSING
        or current.origin == ORIGIN_DEV_STARTER
        or current.state == LifecycleState.REJECTED
    )
    if current.servable and not replaceable:
        return {
            "installed": False,
            "serving_path": str(serving),
            "install_skipped": f"serving slot holds {current.origin} bundle — use governed promotion",
        }

    src_dir = Path(trained_model).parent
    dst_dir = serving.parent
    dst_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".cand_", dir=str(dst_dir)))
    try:
        for name in ("model.pt", "model.scaler.npz", "model.meta.json"):
            src = src_dir / name
            if not src.exists():
                return {
                    "installed": False,
                    "serving_path": str(serving),
                    "install_error": f"candidate missing {name}",
                }
            shutil.copy2(src, staging / name)
        (staging / "manifest.json").write_text(
            json.dumps(
                {
                    "model_sha256": rb.sha256_file(staging / "model.pt"),
                    "metadata_sha256": rb.sha256_file(staging / "model.meta.json"),
                    "scaler_sha256": rb.sha256_file(staging / "model.scaler.npz"),
                    "manifest_version": "1",
                    "note": note or "local candidate (walk-forward trained)",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        verdict = rb.bundle_status(staging / "model.pt")
        if verdict["state"] != rb.STATE_OK:
            return {
                "installed": False,
                "serving_path": str(serving),
                "install_error": f"candidate failed serving gates before swap: {verdict['state']} {verdict['detail']}",
            }
        for name in ("model.pt", "model.scaler.npz", "model.meta.json"):
            os.replace(staging / name, dst_dir / name)
        os.replace(staging / "manifest.json", dst_dir / "manifest.json")
        record_install(
            model_path=serving,
            origin=origin,
            model_sha256=rb.sha256_file(serving),
            model_version="1.0.0",
            bundle_id=f"local-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        )
        final = rb.bundle_status(serving)
        return {
            "installed": final["state"] == rb.STATE_OK,
            "serving_path": str(serving),
            "serve_state": final["state"],
            "origin": origin,
        }
    finally:
        with contextlib.suppress(Exception):
            shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# Provisioner durable state (for UI resume + honest status)
# ---------------------------------------------------------------------------
def provisioner_state_path() -> Path:
    from nexus_scalp.release.paths import get_runtime_workspace

    return get_runtime_workspace() / "artifacts" / "provisioner_state.json"


def write_provisioner_state(state: str, **fields: Any) -> dict[str, Any]:
    p = provisioner_state_path()
    payload: dict[str, Any] = {"state": state, "updated_at": utcnow_iso(), **fields}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if p.exists():
            with contextlib.suppress(ValueError):
                existing = json.loads(p.read_text(encoding="utf-8"))
        existing.update(payload)
        p.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except OSError:
        pass
    return payload


def read_provisioner_state() -> dict[str, Any]:
    p = provisioner_state_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# First-run coordinator (the shared domain operation CLI + Web both call)
# ---------------------------------------------------------------------------
class FirstRunCoordinator:
    """Chooses/executes the model-acquisition path with honest status.

    ``recommended_action()`` answers "what should first setup offer right
    now" without touching anything. ``ensure_serving_model(mode)`` is the
    engine-boot seam: PAPER accepts a labeled starter as a last-resort
    offline fallback (recorded as DEV_STARTER so nothing about it is
    presented as production); SHADOW/LIVE demand a real bundle.
    """

    def __init__(self, official: official_mod.OfficialBundleSource | None = None) -> None:
        self._official = official

    @property
    def official(self) -> official_mod.OfficialBundleSource:
        return self._official if self._official is not None else official_mod.OfficialBundleSource()

    def slot(self) -> SlotClassification:
        return classify_serving_slot()

    def recommended_action(self) -> dict[str, Any]:
        s = self.slot()
        official_ready = self.official.configured
        env: dict[str, Any] = {}
        if not s.servable:
            return {
                "action": "download_official"
                if official_ready
                else ("train_local" if self._torch() else "starter_offline"),
                "official_configured": official_ready,
                "slot": s.as_dict(),
            }
        if s.is_starter:
            return {
                "action": "upgrade_from_starter",
                "reason": "serving slot holds the DEV STARTER (offline fallback) — "
                "official download or local training is the production path",
                "official_configured": official_ready,
                "slot": s.as_dict(),
            }
        return {"action": "none", "slot": s.as_dict(), "environment": env}

    @staticmethod
    def _torch() -> bool:
        from nexus_scalp.model_provisioning.pipeline import detect_ml_environment

        return bool(detect_ml_environment().get("torch"))

    def download_official(self, work_dir: Path | None = None) -> dict[str, Any]:
        """PATH A end-to-end (verify chain inside). Raises on any failure —
        NEVER installs an unverified model, NEVER falls back silently."""
        from nexus_scalp.release import bootstrap as rb

        write_provisioner_state(LifecycleState.DOWNLOADING.value, path="official")
        try:
            verified = self.official.download_and_verify(work_dir=work_dir)
        except official_mod.OfficialBundleError as exc:
            write_provisioner_state(LifecycleState.REJECTED.value, path="official", error=str(exc))
            raise
        write_provisioner_state(
            LifecycleState.VERIFYING.value, path="official", bundle_id=verified.bundle_id
        )
        serving = serving_model_path()
        install = official_mod.install_verified_bundle(verified, serving, origin=ORIGIN_OFFICIAL)
        final = rb.bundle_status(serving)
        ok = final["state"] == rb.STATE_OK
        write_provisioner_state(
            (LifecycleState.READY if ok else LifecycleState.REJECTED).value,
            path="official",
            bundle_id=verified.bundle_id,
            servable=ok,
        )
        return {**install, "servable": ok, "origin": ORIGIN_OFFICIAL}

    def train_local(self, request: Any, progress: Any = None) -> dict[str, Any]:
        """PATH B entry (delegates to pipeline.train_local_model)."""
        from nexus_scalp.model_provisioning import pipeline

        write_provisioner_state(
            LifecycleState.TRAINING.value, path="local", source=str(request.source_file)
        )
        out = pipeline.train_local_model(request, progress=progress)
        outcome = out.get("outcome")
        state = {
            "INSTALLED": LifecycleState.READY,
            "CANDIDATE": LifecycleState.CANDIDATE,
            "CANCELLED": LifecycleState.MISSING,
        }.get(str(outcome), LifecycleState.VALIDATION_FAILED)
        write_provisioner_state(state.value, path="local", outcome=outcome)
        return out

    def ensure_serving_model(self, mode: str) -> dict[str, Any]:
        """Engine-boot seam (called by engine_boot). Mode: paper|shadow|live.

        PAPER: verify slot; if empty/unservable, prefer OFFICIAL when a
        source is configured + reachable, else the labeled DEV STARTER so an
        offline first-run still boots (loudly classified, never presented
        as production). SHADOW/LIVE: never auto-acquire anything — a real
        bundle must already be installed through PATH A/B or promotion.
        """
        from nexus_scalp.release import bootstrap as rb

        serving = serving_model_path()
        cls = classify_serving_slot(serving)
        if cls.servable:
            return {"action": "keep", **cls.as_dict()}
        if mode in ("shadow", "live"):
            return {
                "action": "refuse",
                "reason": f"no verified serving bundle for {mode.upper()} mode",
                **cls.as_dict(),
            }
        if self.official.configured:
            try:
                out = self.download_official()
                return {"action": "official", **cls.as_dict(), **out}
            except official_mod.OfficialBundleError as exc:
                logger.warning(
                    "[FIRST-RUN] event=OFFICIAL_UNAVAILABLE fallback=starter error=%s",
                    str(exc)[:200],
                )
        prov = rb.mint_starter_bundle(serving)
        if prov.get("provisioned"):
            from nexus_scalp.release.bootstrap import sha256_file

            record_install(
                model_path=serving,
                origin=ORIGIN_DEV_STARTER,
                model_sha256=sha256_file(serving),
                model_version="starter",
                bundle_id="dev-starter",
            )
            write_provisioner_state(
                LifecycleState.VERIFIED.value, path="starter", origin=ORIGIN_DEV_STARTER
            )
        return {
            "action": "starter",
            "provisioned": prov.get("provisioned", False),
            **classify_serving_slot(serving).as_dict(),
        }


__all__ = [
    "INSTALL_STATE_FILE",
    "ORIGIN_DEV_STARTER",
    "ORIGIN_GOVERNED",
    "ORIGIN_OFFICIAL",
    "ORIGIN_UNKNOWN",
    "ORIGIN_USER_TRAINED",
    "FirstRunCoordinator",
    "LifecycleState",
    "SlotClassification",
    "candidate_dir",
    "candidate_root",
    "classify_serving_slot",
    "install_candidate",
    "provisioner_state_path",
    "read_install_state",
    "read_provisioner_state",
    "record_install",
    "serving_model_path",
    "utcnow_iso",
    "write_provisioner_state",
]
