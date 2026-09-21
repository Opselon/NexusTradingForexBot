"""Starter model-bundle bootstrap — the canonical PAPER-starter mint seam (BUG-296 / Z-B1).

WHERE/WHY: the engine fail-closes a missing/legacy model artifact at LOAD
(``application/live/model_bundle_store._load_or_create_bundle`` → LOAD_REJECTED /
LEGACY_UNVERIFIED), but a fresh clone ships NO model and the only non-docker
provisioning path was "undocumented, run the docker entrypoint script by hand"
(lane-05 E1/E4). This module owns the ONE mint recipe (the BUG-269 seed-999
TRAINED starter) and its integrity sidecar stamping, so ``nexus repair --model``
and ``docker/provision_model.py`` cannot drift apart.

Evidence contract (docs/audit/wave_20260914/05_zero_state.md):
  * E8 — every mint MUST stamp manifest.json + model.meta.json +
    model.scaler.npz or the NEXT boot classifies the bundle LEGACY_UNVERIFIED
    and bricks. The scaler digest is BOUND in the manifest (scaler_sha256) so
    the P0-2 trust anchor verifies the scaler too, not only the weights.
  * E9 — provisioning must REFUSE to clobber a digest-VERIFIED, servable,
    externally-owned bundle, and must NEVER touch a governed registry CHAMPION
    (the bb1f0afe clobber class). ``--force`` re-provisions a non-champion
    starter only.
  * BUG-269 — a digest-valid but behaviorally-DEGENERATE bundle is re-minted
    (digest trust alone must never be the skip condition), and an unservable
    mint fails LOUD (PROVISIONING_ERROR) at this seam, never as a boot loop.

BOUNDARY: filesystem + torch only; never loads the serving bundle into an
engine, never mutates the registry, never logs secrets. Import-light at module
scope (torch/numpy stay function-local — the slim onefile CLI imports this via
the repair command only when --model is passed).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.release.model_bootstrap")

#: Determinism contract inherited from scripts/ci/runtime_gate.py (BUG-154):
#: manual_seed BEFORE construction, fixed data generator, 30 AdamW steps —
#: constructing first made the bundle's behavioral health machine-dependent.
MINT_SEED = 999
DATA_SEED = 1234
MINT_STEPS = 30

#: Marker written into provisioned manifests: identifies bundles this seam owns
#: (safe to skip/re-provision) versus externally-owned verified artifacts
#: (refused without --force).
PROVISIONER_MARKER = "nexus.model_bootstrap"


class ProvisioningError(RuntimeError):
    """Fail-loud at the provisioning seam (never relax the serving gate)."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bundle_is_servable(model: Path) -> tuple[bool, str]:
    """True when the bundle passes the engine's LOAD-TIME serving gates.

    Runs the same two content checks _load_or_create_bundle enforces before
    weights ever serve (model_lifecycle.integrity): fresh-init canary +
    behavioral anti-degenerate probe. Imports are lazy and NOT caught here —
    a missing torch/nexus_scalp must propagate so callers keep (never clobber)
    a digest-verified bundle they cannot judge.
    """
    from nexus_scalp.model_lifecycle.integrity import (
        check_model_behavioral_health,
        detect_untrained_fresh_init,
    )

    is_fresh, fresh_detail = detect_untrained_fresh_init(model, None)
    if is_fresh:
        return False, f"canonical fresh init ({fresh_detail})"
    healthy, detail, _metrics = check_model_behavioral_health(model, None)
    if not healthy:
        return False, f"behavioral health failed ({detail})"
    return True, "servable"


def mint_trained_starter(model: Path) -> None:
    """Deterministic TRAINED mint — byte-stable across hosts (BUG-154)."""
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    torch.manual_seed(MINT_SEED)
    net = ScalpNet(num_features=70, num_classes=3)
    net.train()
    gen = torch.Generator().manual_seed(DATA_SEED)
    x = torch.randn(256, 70, generator=gen)
    y = torch.randint(0, 3, (256,), generator=gen)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3)
    for _ in range(MINT_STEPS):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(net(x, return_logits=True), y)
        loss.backward()
        opt.step()
    net.eval()
    torch.save(net.state_dict(), model)


def write_sidecars(model: Path, *, note: str) -> dict[str, str]:
    """Stamp the integrity sidecars for a freshly minted weights file (E8).

    Order matters when the scaler digest is BOUND: weights -> scaler -> meta ->
    manifest last (the manifest declares both digests; writing it last keeps
    any crash between steps classifiable, never self-inconsistent).
    """
    import numpy as np

    from nexus_scalp.features.schema_contract import SCHEMA_ID

    art = model.parent
    art.mkdir(parents=True, exist_ok=True)
    scaler = art / "model.scaler.npz"
    np.savez(scaler, mean=np.zeros(70), std=np.ones(70))
    # The class-head count is read from the checkpoint this function stamps,
    # so the meta and the tensor can never disagree (the mint's num_classes
    # literal is not restated here).
    import torch

    _cls = torch.load(model, map_location="cpu", weights_only=True).get("classifier.weight")
    if not hasattr(_cls, "shape"):
        raise ProvisioningError(
            "PROVISIONING_ERROR: minted checkpoint has no classifier weight "
            "tensor, so the class-head count is not derivable — fix the mint "
            "recipe; never relax the serving gate."
        )
    (art / "model.meta.json").write_text(
        json.dumps(
            {
                "feature_schema_id": SCHEMA_ID,
                "feature_schema_dimension": 70,
                "model_head_classes": int(_cls.shape[0]),
                "model_sha256": sha256_file(model),
                "note": note,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    digest = sha256_file(model)
    (art / "manifest.json").write_text(
        json.dumps(
            {
                "model_sha256": digest,
                "scaler_sha256": sha256_file(scaler),
                "manifest_version": "1",
                "provisioner": PROVISIONER_MARKER,
                "note": note,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"model_sha256": digest, "scaler_sha256": sha256_file(scaler)}


def mint_starter_bundle(model: Path, *, note: str) -> dict[str, Any]:
    """Mint + stamp + gate: the full provisioning half of provision().

    Fails LOUDLY at this seam (PROVISIONING_ERROR) when the minted starter
    would not pass the engine's serving gates — a future torch/RNG-semantic
    shift must surface here with probe detail, never as a mysterious
    MODEL_LOAD_REJECTED boot loop (BUG-269 contract).
    """
    model.parent.mkdir(parents=True, exist_ok=True)
    mint_trained_starter(model)
    ok, detail = bundle_is_servable(model)
    if not ok:
        raise ProvisioningError(
            f"PROVISIONING_ERROR: minted starter failed the serving gates "
            f"({detail}) — fix the mint recipe; never relax the serving gate. "
            f"artifact={model}"
        )
    digests = write_sidecars(model, note=note)
    return {"status": "MINTED", "artifact": str(model), **digests}


def _champion_binding(target: Path) -> dict[str, Any]:
    """Read the governed CHAMPION row for this artifact (registry truth).

    Mirrors the P0-2 trust anchor's query (model_bundle_store
    ._verify_champion_registry_binding): newest CHAMPION row in the audit DB's
    experience_model_registry. Any registry-unavailable shape (no DB, no
    table, no lifecycle column yet, non-sqlite provider) yields an INERT
    verdict — cold/zero states have no champion and must stay provisionable.
    """
    out: dict[str, Any] = {"governed": False, "reason": "INERT_NO_CHAMPION_ROW"}
    try:
        from nexus_scalp.database.config import load_database_config
        from nexus_scalp.database.provider import DatabaseProvider

        cfg = load_database_config("audit")
        if cfg.provider is not DatabaseProvider.SQLITE:
            out["reason"] = "INERT_NON_SQLITE"
            return out
        db_path = cfg.sqlite_path or cfg.database
        if not db_path or not Path(str(db_path)).exists():
            return out
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            row = conn.execute(
                "SELECT model_id, artifact_fingerprint, artifact_path "
                "FROM experience_model_registry "
                "WHERE lifecycle_status='CHAMPION' ORDER BY registered_at DESC LIMIT 1;"
            ).fetchone()
        finally:
            conn.close()
    except Exception as e:  # missing table/column, locked DB — all INERT shapes
        logger.debug("[MODEL_BOOTSTRAP] champion registry read inert", error=str(e)[:200])
        return out
    if not row:
        return out
    champion_id, governed_fp, row_path = (
        str(row[0] or ""),
        str(row[1] or "").strip().lower(),
        str(row[2] or ""),
    )

    def _norm(p: Any) -> str:
        return str(p or "").replace("\\", "/")

    fingerprint_match = bool(governed_fp) and target.exists() and _sha16(target) == governed_fp
    path_match = False
    if row_path:
        path_match = _norm(row_path) == _norm(target)
        if not path_match:
            with contextlib.suppress(OSError):
                path_match = Path(row_path).resolve() == target.resolve()
    if fingerprint_match or path_match:
        out.update(
            governed=True,
            reason="CHAMPION_GOVERNED",
            champion_model_id=champion_id,
            governed_sha16=governed_fp,
        )
    return out


def _sha16(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _is_declared_starter(model: Path) -> bool:
    """True when manifest.json was written by this seam (marker + digest)."""
    manifest = model.parent / "manifest.json"
    if not manifest.exists():
        return False
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(record, dict)
        and str(record.get("provisioner") or "") == PROVISIONER_MARKER
        and str(record.get("model_sha256") or "").lower() == sha256_file(model).lower()
    )


def provision(
    model: Path,
    *,
    force: bool = False,
    note: str = "nexus repair --model provisioned (PAPER starter)",
) -> dict[str, Any]:
    """Idempotent, refusal-guarded starter provisioning for one artifact path.

    Decision table (lane-05 E8/E9 + BUG-269):
      * governed CHAMPION (registry row binds fingerprint or path) -> REFUSED,
        never clobbered, --force does NOT override (governance is the only
        writer of champion authority).
      * digest-VERIFIED + servable + externally-owned -> KEPT (not a starter —
        --force does not override either; delete the bundle first if an
        operator truly wants a starter there). E9 clobber class.
      * digest-VERIFIED + servable + declared starter -> SKIPPED (idempotent);
        --force re-mints the starter.
      * digest-VERIFIED + NOT servable -> re-mint (BUG-269 silent-skip trap:
        digest trust alone is never the skip condition).
      * LEGACY_UNVERIFIED (bare-mint brick, E8) / HASH_MISMATCH / missing
        manifest / missing artifact -> mint a fresh starter with sidecars.
    """
    model = Path(model)
    champ = _champion_binding(model)
    if champ.get("governed"):
        return {
            "status": "REFUSED",
            "reason": "CHAMPION_GOVERNED",
            "artifact": str(model),
            "detail": (
                "artifact is the governed registry CHAMPION "
                f"(model_id={champ.get('champion_model_id')}) — provisioning never "
                "overwrites it; retirement/promotion is the governance path"
            ),
        }

    from nexus_scalp.model_lifecycle.load_integrity import (
        ArtifactIntegrityError,
        ArtifactIntegrityStatus,
        verify_artifact_integrity,
    )

    verdict_status: ArtifactIntegrityStatus | None = None
    try:
        verdict_status = verify_artifact_integrity(model).status
    except ArtifactIntegrityError as e:
        verdict_status = e.verdict.status
    except Exception:  # unreadable dir etc — treat as unprovisioned
        verdict_status = ArtifactIntegrityStatus.LOAD_REJECTED

    if verdict_status is ArtifactIntegrityStatus.VERIFIED:
        declared = _is_declared_starter(model)
        try:
            servable, detail = bundle_is_servable(model)
        except ImportError:
            # Cannot judge servability without torch — never clobber a
            # digest-verified bundle we cannot read (docker: torch-less is a
            # broken image anyway; the historical contract was keep + rc 0).
            return {
                "status": "KEPT",
                "reason": "TORCH_UNAVAILABLE",
                "artifact": str(model),
                "detail": "torch/nexus_scalp unavailable for the serving probe; kept existing verified bundle",
            }
        if declared:
            if servable and not force:
                return {
                    "status": "SKIPPED",
                    "reason": "STARTER_ALREADY_PRESENT",
                    "artifact": str(model),
                    "detail": "declared starter verified and servable — nothing to do",
                }
            # --force on a declared non-champion starter, or a starter that
            # turned unservable (torch drift): re-mint.
            return mint_starter_bundle(model, note=note)
        if not servable:
            # BUG-269: digest-match alone is NOT sufficient — re-mint the
            # degenerate bundle rather than skip it. (A mounted trained
            # champion passes the probe and lands in the KEPT arm below.)
            with contextlib.suppress(Exception):
                logger.warning(
                    "[MODEL_BOOTSTRAP] re-minting digest-valid but UNSERVABLE bundle",
                    detail=detail,
                )
            return mint_starter_bundle(model, note=note)
        return {
            "status": "KEPT",
            "reason": "VERIFIED_ARTIFACT_PRESENT",
            "artifact": str(model),
            "detail": (
                "digest-VERIFIED, servable, externally-owned artifact kept "
                "byte-for-byte (E9 clobber class) — this seam only provisions "
                "starters; governance owns trained bundles"
            ),
        }

    # Anything else (absent, empty, LEGACY_UNVERIFIED brick (E8), stale/tampered
    # or corrupt manifest) is a broken zero state for THIS artifact: mint.
    return mint_starter_bundle(model, note=note)


def resolve_configured_artifact(workspace: Path | None = None) -> Path:
    """The artifact path the engine will actually load on the next boot.

    Same precedence as LiveEngine: persisted runtime-config snapshot (BUG-136
    rehydrate) layers over the bootstrap config (user config yaml when it
    exists — engine_boot's first candidate — else AppConfig defaults);
    relative paths anchor to the runtime workspace.
    """
    from nexus_scalp.configuration.config import AppConfig
    from nexus_scalp.release.paths import get_runtime_workspace, get_user_config_path

    ws = Path(workspace or get_runtime_workspace())

    def _anchor(raw: Any) -> Path:
        p = Path(str(raw or ""))
        return p if p.is_absolute() else ws / p

    bootstrap: Any = None
    user_cfg = get_user_config_path()
    if user_cfg.exists():
        with contextlib.suppress(Exception):
            bootstrap = AppConfig.load_from_yaml(user_cfg)
    if bootstrap is None:
        bootstrap = AppConfig()

    with contextlib.suppress(Exception):
        from nexus_scalp.configuration.runtime_config import (
            PersistentConfigStore,
            RuntimeConfigStore,
        )
        from nexus_scalp.settings.service import SettingsService

        svc = SettingsService()
        try:
            store = RuntimeConfigStore(persistent=PersistentConfigStore(svc), bootstrap=bootstrap)
            snap_path = store.get_snapshot().model.model_artifact_path
            if snap_path:
                return _anchor(snap_path)
        finally:
            with contextlib.suppress(Exception):
                svc.close()
    return _anchor(bootstrap.model.model_artifact_path)
