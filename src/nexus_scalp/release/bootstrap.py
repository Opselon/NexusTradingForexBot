"""FIRST-RUN BOOTSTRAP anchors — packaged launch readiness helpers (BUG-298).

The starter-MINT seam itself lives in ``release.model_bootstrap`` (BUG-269
recipe, BUG-296 productization — single owner, ``nexus repair --model`` and
the docker entrypoint both use it). This module owns what that seam could
NOT cover — the PyInstaller-packaged first-start readiness of the v9.0.12
crash class:

  * ``resolve_model_path``  — config-declared relative artifact paths anchor
      to the canonical runtime workspace (the double-click CWD is arbitrary;
      BUG-149 anchored artifacts but the config string stayed relative, so
      the packaged boot looked for model.pt under a CWD that isn't the
      bundle root).
  * ``anchor_workspace``    — frozen installs chdir() to the bundle dir and
      materialize the runtime subdir skeleton before any consumer resolves
      a relative path.
  * ``ensure_packaged_config_dir`` — mirror the read-only bundled configs
      (``_internal/configs`` under onedir) into ``<root>/configs`` so the
      canonical execution-cost artifact (and every other
      ``configs/<file>`` consumer) resolves where the runtime looks
      (v9.0.12 warning: ``canonical execution assumptions missing:
      ...\\installed\\configs\\execution_assumptions.json``).
  * ``bundle_status``       — honest, never-raising classification of the
      serving slot through the EXACT engine load gates (integrity digest,
      fresh-init canary, behavioral probe), consumed by the first-run
      provisioning coordinator (model_provisioning) and the CLI.
  * ``mint_starter_bundle`` — thin delegation to release.model_bootstrap.
      The mint is the explicitly-labeled DEV STARTER (offline/CI/emergency
      fallback), never presented as a production model: PATH A (official
      signed download) and PATH B (local training) are the product paths.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.release import model_bootstrap as mb
from nexus_scalp.release import paths as rpaths

logger = get_logger("nexus_scalp.release.bootstrap")

#: Determinism contract lives with the mint seam (release.model_bootstrap);
#: re-exported so docker/CI call-sites keep one import surface.
MINT_SEED = mb.MINT_SEED
DATA_SEED = mb.DATA_SEED
MINT_STEPS = mb.MINT_STEPS

#: The canonical serving bundle this release boots with (70D scalp_v3).
STARTER_SCHEMA_ID = "scalp_v3"
STARTER_DIM = 70
STARTER_CLASSES = 3

#: Bundle states surfaced to CLI/health callers.
STATE_OK = "OK"
STATE_MISSING = "MISSING"
STATE_UNSERVABLE = "UNSERVABLE"
STATE_INVALID = "INVALID"

#: Manifest note marker of the model_bootstrap seam (starter identity).
STARTER_NOTE_DEFAULT = "release bootstrap provisioned (DEV STARTER — PAPER-safe offline fallback)"

BootstrapError = mb.ProvisioningError


def sha256_file(path: Path) -> str:
    return mb.sha256_file(path)


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


def mint_starter_bundle(model_path: Path | str, *, force: bool = False) -> dict[str, Any]:
    """Provision the labeled DEV STARTER via the canonical seam.

    Local pre-check first (never clobber what cannot be judged: an INVALID
    classification raises instead of delegating; an already-OK bundle is
    kept without touching disk), then delegates to
    ``release.model_bootstrap.provision`` — the single owner of the mint
    recipe and its refusal table (governed CHAMPION never clobbered;
    verified foreign bundles KEPT; degenerate re-mint; E8 sidecar
    stamping). Returns the seam's dict plus the historical keys the
    first-run coordinator reads (``provisioned`` / ``state`` /
    ``model_sha256``).
    """
    p = Path(model_path)
    pre = bundle_status(p)
    if pre["state"] == STATE_INVALID:
        raise BootstrapError(
            f"cannot judge existing bundle, refusing to overwrite: {pre['detail']}"
        )
    if pre["state"] == STATE_OK and not force:
        return {
            "provisioned": False,
            "reason": "bundle verified and servable",
            "path": str(p),
            "state": STATE_OK,
            "detail": pre["detail"],
            "model_sha256": sha256_file(p),
        }
    result = mb.provision(p, force=force, note=STARTER_NOTE_DEFAULT)
    status = str(result.get("status", ""))
    out: dict[str, Any] = {
        # MINTED = a fresh/re-minted starter. SKIPPED/KEPT = the slot already
        # holds what this seam should leave alone (starter present / verified
        # foreign artifact). REFUSED = governed champion (never touched).
        "provisioned": status == "MINTED",
        "reason": str(result.get("reason", status)),
        "path": str(model_path),
        "state": STATE_OK,
        "detail": str(result.get("detail", "")),
        "raw": result,
    }
    final = bundle_status(p)
    out["state"] = final["state"]
    if final["state"] == STATE_OK:
        try:
            out["model_sha256"] = sha256_file(p)
        except OSError:
            pass
    return out


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
    import shutil

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
    "DATA_SEED",
    "MINT_SEED",
    "MINT_STEPS",
    "STATE_INVALID",
    "STATE_MISSING",
    "STATE_OK",
    "STATE_UNSERVABLE",
    "BootstrapError",
    "anchor_workspace",
    "bundled_configs_dir",
    "bundle_status",
    "canonical_starter_path",
    "ensure_packaged_config_dir",
    "json",
    "mint_starter_bundle",
    "resolve_model_path",
    "sha256_file",
]
