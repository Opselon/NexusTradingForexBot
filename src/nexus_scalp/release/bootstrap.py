"""FIRST-RUN BOOTSTRAP — a freshly installed release must START (BUG-293).

Defect class this closes (v9.0.12 windows release, 2026-09-15):
a clean install ran ``start`` -> LiveEngine construction -> the P0 artifact
trust gates refused ``model.pt`` with ``LOAD_REJECTED: artifact missing or
empty`` and the EXE hard-crashed (PYI ``Failed to execute script
'packaged_main'``). Nothing in the release pipeline ever minted a serving
model bundle: minting existed only in ``docker/provision_model.py`` and
``scripts/ci/runtime_gate.py``, so "download, install, run" was structurally
broken while every CI gate stayed green (no gate ever launched the packaged
EXE in a clean-install START shape).

Contract:

  * ``bundle_status``   — honest classification of the serving bundle WITHOUT
                          ever raising (OK / MISSING / UNSERVABLE / INVALID).
  * ``mint_starter_bundle`` — provision a REAL trained starter bundle (same
                          deterministic 30-step AdamW recipe as the docker
                          provisioner — seed 999 BEFORE construction,
                          byte-stable across hosts, passes the fresh-init and
                          behavioral serving gates by TRAINING, not by
                          relaxing them). Writes the full bundle
                          (model.pt + model.scaler.npz + model.meta.json +
                          manifest.json with sha256 bindings) atomically and
                          re-verifies it through the exact load-time gates the
                          engine will apply. NEVER overwrites an existing
                          artifact (governed champions take precedence).
  * ``anchor_workspace`` — packaged runs resolve relative runtime paths
                          (artifacts/, configs/, data/) against the process
                          CWD; a double-click inherits an arbitrary CWD. For
                          frozen installs the workspace root IS the bundle
                          directory (exe_dir) — chdir there once at boot.
  * ``ensure_packaged_config_dir`` — mirror the bundled read-only configs
                          (``_internal/configs`` under PyInstaller onedir)
                          into the workspace ``configs/`` so every consumer
                          that expects ``<installed>/configs/...`` finds the
                          canonical artifacts (execution_assumptions.json
                          included). Copy-if-missing only: user edits win.

Non-goals: never trains on real market data (that is ``nexus train-once``),
never promotes a registry CHAMPION row, never relaxes any serving gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.release import paths as rpaths

logger = get_logger("nexus_scalp.release.bootstrap")

#: Determinism contract shared with docker/provision_model.py and
#: scripts/ci/runtime_gate.py (BUG-154): manual_seed BEFORE construction,
#: fixed data generator, 30 steps. Changing any of these changes the starter
#: bytes — the serving gates stay the same.
MINT_SEED = 999
DATA_SEED = 1234
MINT_STEPS = 30

#: The canonical serving bundle this release boots with (70D scalp_v3).
STARTER_SCHEMA_ID = "scalp_v3"
STARTER_DIM = 70
STARTER_CLASSES = 3

#: Bundle states surfaced to CLI/health callers.
STATE_OK = "OK"
STATE_MISSING = "MISSING"
STATE_UNSERVABLE = "UNSERVABLE"
STATE_INVALID = "INVALID"


class BootstrapError(RuntimeError):
    """Provisioning attempted and refused — the starter mint failed a gate."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_model_path(model_artifact_path: str | Path) -> Path:
    """Anchor a config-declared artifact path to the canonical runtime root.

    Absolute paths pass through unchanged. Relative paths resolve against the
    runtime workspace (bundle dir when frozen, CWD otherwise) — matching the
    BUG-149 artifacts convention — so the packaged starter always lands where
    the engine will look for it.
    """
    p = Path(model_artifact_path)
    if p.is_absolute():
        return p
    return (rpaths.get_runtime_workspace() / p).resolve()


def bundle_status(model_path: Path | str) -> dict[str, Any]:
    """Classify the serving bundle WITHOUT raising and WITHOUT mutating.

    Runs the exact three load-time gates _load_or_create_bundle applies:
    integrity verification (digest manifest), fresh-init canary, behavioral
    health probe. Returns {"state": OK|MISSING|UNSERVABLE|INVALID,
    "detail": str, "path": str}.
    """
    p = Path(model_path)
    out: dict[str, Any] = {"state": STATE_OK, "detail": "", "path": str(p)}
    try:
        if not p.exists() or p.stat().st_size == 0:
            out.update(state=STATE_MISSING, detail="artifact missing or empty")
            return out
        from nexus_scalp.model_lifecycle.load_integrity import (
            ArtifactIntegrityError,
            verify_artifact_integrity,
        )

        try:
            verify_artifact_integrity(p)
        except ArtifactIntegrityError as exc:
            # LEGACY_UNVERIFIED still serves nothing by default (the engine
            # refuses it without the explicit operator opt-in) — classify as
            # UNSERVABLE so first-run provisioning replaces it deliberately.
            out.update(
                state=STATE_UNSERVABLE
                if exc.verdict.status.value != "LOAD_REJECTED"
                else STATE_MISSING,
                detail=exc.verdict.reason,
            )
            return out
        from nexus_scalp.model_lifecycle.integrity import (
            check_model_behavioral_health,
            detect_untrained_fresh_init,
        )

        is_fresh, fresh_detail = detect_untrained_fresh_init(p, None)
        if is_fresh:
            out.update(state=STATE_UNSERVABLE, detail=f"canonical fresh init ({fresh_detail})")
            return out
        healthy, health_detail, _m = check_model_behavioral_health(p, None)
        if not healthy:
            out.update(state=STATE_UNSERVABLE, detail=f"behavioral health ({health_detail})")
            return out
        return out
    except Exception as exc:  # classification must never break a boot path
        out.update(state=STATE_INVALID, detail=f"{type(exc).__name__}: {exc}"[:300])
        return out


def _mint_trained_starter(model: Path) -> None:
    """Deterministic TRAINED mint — byte-stable across hosts (BUG-154)."""
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    torch.manual_seed(MINT_SEED)
    net = ScalpNet(num_features=STARTER_DIM, num_classes=STARTER_CLASSES)
    net.train()
    gen = torch.Generator().manual_seed(DATA_SEED)
    x = torch.randn(256, STARTER_DIM, generator=gen)
    y = torch.randint(0, STARTER_CLASSES, (256,), generator=gen)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3)
    for _ in range(MINT_STEPS):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(net(x, return_logits=True), y)
        loss.backward()
        opt.step()
    net.eval()
    cpu_state = {k: v.detach().cpu() for k, v in net.state_dict().items()}
    torch.save(cpu_state, model)


def mint_starter_bundle(model_path: Path | str, *, force: bool = False) -> dict[str, Any]:
    """Provision the canonical serving bundle if it is absent/unservable.

    Idempotent: an existing bundle that PASSES the load-time gates is kept
    byte-for-byte (a governed champion is never clobbered). A digest-valid
    but unservable bundle (fresh-init / degenerate / legacy-unverified) is
    replaced ONLY with ``force`` semantics already implied by the MISSING /
    UNSERVABLE classification — an INVALID verdict (probe could not judge)
    never overwrites. Returns a provenance dict. Raises BootstrapError when
    the minted starter itself fails the gates (fail loudly at the seam).
    """
    p = Path(model_path)
    status = bundle_status(p)
    if status["state"] == STATE_OK:
        logger.info("[BOOTSTRAP] event=STARTER_PRESENT path=%s", p.name)
        return {"provisioned": False, "reason": "bundle verified and servable", **status}
    if status["state"] == STATE_INVALID:
        # Cannot judge (torch/dep failure) — never clobber what we cannot read.
        raise BootstrapError(
            f"cannot judge existing bundle, refusing to overwrite: {status['detail']}"
        )

    import numpy as np

    from nexus_scalp.features.schema_contract import SCHEMA_ID

    if SCHEMA_ID != STARTER_SCHEMA_ID:  # drift tripwire: starter must match canonical schema
        raise BootstrapError(f"starter schema drift: SCHEMA_ID={SCHEMA_ID} != {STARTER_SCHEMA_ID}")

    p.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".starter_", dir=str(p.parent)))
    try:
        staged_model = staging / "model.pt"
        _mint_trained_starter(staged_model)
        staged_scaler = staging / "model.scaler.npz"
        np.savez(staged_scaler, mean=np.zeros(STARTER_DIM), std=np.ones(STARTER_DIM))
        staged_meta = staging / "model.meta.json"
        staged_meta.write_text(
            json.dumps(
                {
                    "feature_schema_id": STARTER_SCHEMA_ID,
                    "feature_schema_dimension": STARTER_DIM,
                    "num_classes": STARTER_CLASSES,
                    "model_head_classes": STARTER_CLASSES,
                    "label_contract": {
                        "schema_id": "triple_barrier_3class_v1",
                        "class_count": STARTER_CLASSES,
                    },
                    "note": "release bootstrap starter (PAPER-safe, deterministically trained mint)",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        staged_manifest = staging / "manifest.json"
        staged_manifest.write_text(
            json.dumps(
                {
                    "model_sha256": sha256_file(staged_model),
                    "metadata_sha256": sha256_file(staged_meta),
                    "scaler_sha256": sha256_file(staged_scaler),
                    "manifest_version": "1",
                    "note": "release bootstrap provisioned (PAPER starter — replace via train-once + promotion)",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # Verify the STAGED bundle through the exact gates the engine applies
        # (manifest first: verify_artifact_integrity reads sidecars next to the
        # model file). A starter that cannot pass here must never ship.
        verdict = bundle_status(staged_model)
        if verdict["state"] != STATE_OK:
            raise BootstrapError(
                f"BOOTSTRAP_PROVISIONING_ERROR: minted starter failed serving gates "
                f"({verdict['state']}: {verdict['detail']}) — fix the mint recipe; "
                "never relax the serving gate"
            )

        # Atomic publish (manifest LAST — commit-marker convention shared with
        # emission_gate.publish_bundle_atomic).
        for name in ("model.pt", "model.scaler.npz", "model.meta.json"):
            os.replace(staging / name, p.parent / name)
        os.replace(staging / "manifest.json", p.parent / "manifest.json")

        final = bundle_status(p)
        if final["state"] != STATE_OK:
            raise BootstrapError(f"published starter failed verification: {final['detail']}")
        logger.critical(
            "[BOOTSTRAP] event=STARTER_PROVISIONED path=%s prev_state=%s sha16=%s",
            p,
            status["state"],
            sha256_file(p)[:16],
        )
        return {
            "provisioned": True,
            "reason": f"starter minted over prior state {status['state']}",
            "path": str(p),
            "model_sha256": sha256_file(p),
            "state": STATE_OK,
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def canonical_starter_path(config: Any | None = None) -> Path:
    """Resolve the serving bundle path from the effective runtime config."""
    rel = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
    try:
        if config is not None:
            rel = str(config.model.model_artifact_path) or rel
    except Exception:
        pass
    return resolve_model_path(rel)


def anchor_workspace() -> None:
    """BUG-293: freeze-relative runtime roots for packaged launches.

    The bundle directory (exe_dir) is the canonical runtime root (BUG-149
    convention for artifacts/). Relative config/data paths elsewhere resolve
    against the process CWD, which a double-click controls — chdir once and
    make the runtime subdirs so consumers find a complete tree.
    """
    if not rpaths.is_frozen():
        return
    root = rpaths.get_runtime_workspace()
    try:
        root.mkdir(parents=True, exist_ok=True)
        os.chdir(root)
    except OSError as exc:
        logger.warning("[BOOTSTRAP] workspace chdir refused: %s", exc)
        return
    for sub in rpaths.RUNTIME_SUBDIRS:
        try:
            (root / sub).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def bundled_configs_dir() -> Path | None:
    """Locate the read-only configs directory shipped inside the bundle."""
    if not rpaths.is_frozen():
        return None
    candidates = [
        rpaths.exe_dir() / "_internal" / "configs",
        rpaths.exe_dir() / "configs",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def ensure_packaged_config_dir() -> None:
    """Mirror bundled configs into the workspace ``configs/`` (copy-if-missing).

    PyInstaller onedir extracts ``--add-data configs`` to ``_internal/configs``
    while the runtime resolves canonical consumers at ``<root>/configs`` (the
    error surface of BUG-293: ``canonical execution assumptions missing:
    ...\\installed\\configs\\execution_assumptions.json``). Copy the canonical
    YAML/JSON artifacts over without ever touching existing user files.
    """
    src = bundled_configs_dir()
    if src is None:
        return
    dst = rpaths.get_runtime_workspace() / "configs"
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(src.glob("*")):
            if f.suffix.lower() not in (".yaml", ".yml", ".json"):
                continue
            target = dst / f.name
            if not target.exists():
                shutil.copy2(f, target)
    except OSError as exc:
        logger.warning("[BOOTSTRAP] configs mirror failed (non-fatal): %s", exc)


__all__ = [
    "STATE_INVALID",
    "STATE_MISSING",
    "STATE_OK",
    "STATE_UNSERVABLE",
    "BootstrapError",
    "anchor_workspace",
    "bundle_status",
    "bundled_configs_dir",
    "canonical_starter_path",
    "ensure_packaged_config_dir",
    "mint_starter_bundle",
    "resolve_model_path",
    "sha256_file",
]
