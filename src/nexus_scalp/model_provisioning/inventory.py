"""Model inventory read-plane for the provisioning control plane.

CONTRACT (phases 13/30/31/32/35/37): the provisioning page must show EVERY
servable NSE model with identity, artifact, schema, dimension, hash, source,
status, and which one is ACTIVE — without loading artifacts into RAM and
without one DB query per model.

Design decisions and why:

* **Metadata-only listing.** A directory walk + ``manifest.json`` /
  ``model.meta.json`` parse (both are small JSON sidecars). ``model.pt`` is
  NEVER opened, so a 100-model inventory costs the same as a 10-model one
  (phase 31). No torch import, no weight load, no scaler read.
* **One authoritative active answer.** The active model comes from the champion
  manager (the runtime owner), never from filenames and never from the UI. If
  that answers ``None``, nothing is reported active (phase 37).
* **The official/local model is NOT special-cased.** The serving slot is just
  another bundle directory in the inventory, reached through the same code
  path (phase 35).
* **No duplicate registry.** This module READS the existing lifecycle registry
  and the existing install-state sidecars; it owns no store (phase 19).
* **Fail-closed on absent metadata.** A bundle directory whose sidecars are
  unreadable is reported with ``status=UNKNOWN`` and the reason — never
  invented and never silently dropped.
* **Pagination** via ``limit`` (default 100, hard cap) on the metadata list, so
  a large inventory stays bounded.

This is a READ plane. It performs no activation, no switch, no delete and no
write of any kind.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_provisioning.inventory")

#: Hard ceiling on the number of bundle entries returned in one call. The
#: metadata walk is O(bundles) with no artifact loads, but the response stays
#: bounded for the UI regardless of disk growth.
_MAX_LIMIT = 500
_DEFAULT_LIMIT = 100

#: The two sidecars that describe a bundle. ``manifest.json`` is the signed
#: verification record (names ``input_dim``); ``model.meta.json`` is the
#: training declaration (``num_features`` / ``feature_schema_dimension`` /
#: ``model_head_classes``). Neither is a weights file.
_MANIFEST = "manifest.json"
_META = "model.meta.json"
_MODEL_PT = "model.pt"
_SCALER_SUFFIX = ".scaler.npz"

#: Bundle roots, relative to the runtime workspace. The serving tree
#: (``artifacts/models/scalp/...``) and the training candidate tree
#: (``artifacts/model_generation/models/...``) are BOTH included — the official
#: model is managed in the same inventory as trained candidates (phase 35).
_BUNDLE_ROOTS = (
    Path("artifacts") / "models",
    Path("artifacts") / "model_generation" / "models",
)

#: Extensions that identify a model weights file inside a bundle dir.
_WEIGHT_EXTS = (".pt", ".pth", ".onnx")


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read a small JSON sidecar. Returns None on any failure (fail-closed)."""
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _iso(mtime: float | None) -> str | None:
    if mtime is None:
        return None
    try:
        return datetime.fromtimestamp(mtime, tz=UTC).isoformat(timespec="seconds")
    except (OSError, ValueError, OverflowError):
        return None


def _weights_file(bundle_dir: Path) -> Path | None:
    """The primary weights file of a bundle dir (``model.pt`` preferred)."""
    pt = bundle_dir / _MODEL_PT
    if pt.is_file():
        return pt
    for entry in sorted(bundle_dir.iterdir()):
        if entry.is_file() and entry.suffix.lower() in _WEIGHT_EXTS:
            return entry
    return None


def _hash_prefix(weights: Path, limit: int = 16) -> str | None:
    """First ``limit`` hex chars of the artifact fingerprint.

    Uses the EXISTING provenance fingerprinter (``experience.provenance``) so
    the inventory hash and the registry hash are computed by the same code —
    never two hashing implementations agreeing by accident.

    EXPENSIVE: this reads the whole artifact. Callers must decide explicitly
    whether they want it (see ``list_model_inventory(include_hashes=...)``) —
    a 100-model listing must never read 100 weight files.
    """
    try:
        from nexus_scalp.experience.provenance import fingerprint_artifact
    except Exception:  # pragma: no cover - import guard, never blocks listing
        return None
    try:
        digest = fingerprint_artifact(weights)
    except Exception:
        return None
    if not isinstance(digest, str) or not digest:
        return None
    return digest[:limit] or None


def _scaler_for(weights: Path) -> Path | None:
    """Sibling scaler: ``<stem>.scaler.npz`` next to the weights file."""
    sibling = weights.with_name(weights.name + _SCALER_SUFFIX)
    return sibling if sibling.is_file() else None


def _bundle_entry(
    bundle_dir: Path,
    *,
    active_artifact: Path | None,
    origin_hint: str,
    include_hashes: bool,
) -> dict[str, Any] | None:
    """One inventory row from a bundle directory, or None if it is not a bundle.

    A directory is a bundle when it holds a weights file AND at least one of
    the two descriptive sidecars. Directories failing that test are skipped
    (they are supporting state, not models).
    """
    weights = _weights_file(bundle_dir)
    if weights is None:
        return None
    manifest_path = bundle_dir / _MANIFEST
    meta_path = bundle_dir / _META
    manifest = _read_json(manifest_path)
    meta = _read_json(meta_path)
    if manifest is None and meta is None:
        return None

    # input_dim comes from the signed manifest; num_features /
    # feature_schema_dimension from the training declaration. Prefer the
    # manifest (it is the verification record) and fall back to the meta.
    dimension = None
    schema_id = None
    classes = None
    if manifest:
        try:
            dimension = (
                int(manifest.get("input_dim")) if manifest.get("input_dim") is not None else None
            )
        except (TypeError, ValueError):
            dimension = None
    if meta:
        for key in ("feature_schema_dimension", "num_features"):
            if dimension is None:
                try:
                    dimension = int(meta.get(key)) if meta.get(key) is not None else None
                except (TypeError, ValueError):
                    dimension = None
        schema_id = meta.get("feature_schema_id") or meta.get("schema_id")
        try:
            classes = (
                int(meta.get("model_head_classes"))
                if meta.get("model_head_classes") is not None
                else None
            )
        except (TypeError, ValueError):
            classes = None
    if schema_id is None:
        schema_id = manifest.get("feature_schema_id") if manifest else None

    scaler = _scaler_for(weights)
    try:
        stat = weights.stat()
        size = stat.st_size
        modified = _iso(stat.st_mtime)
    except OSError:
        size = None
        modified = None

    artifact_str = str(weights)
    active = bool(active_artifact is not None and _same_artifact(active_artifact, weights))

    return {
        "model_id": _first(manifest, meta, "model_id") or bundle_dir.name,
        "version": _first(manifest, meta, "model_version")
        or _first(manifest, meta, "version")
        or "",
        "source": origin_hint,
        "name": bundle_dir.name,
        "artifact": artifact_str,
        "artifact_exists": weights.is_file(),
        "hash": _hash_prefix(weights) if include_hashes else None,
        "hash_pending": not include_hashes,
        "scaler": str(scaler) if scaler is not None else None,
        "scaler_exists": scaler is not None,
        "schema_id": schema_id,
        "dimension": dimension,
        "classes": classes,
        "size_bytes": size,
        "modified_iso": modified,
        "active": active,
        # Status vocabulary is the lifecycle registry's; when the registry row
        # is unavailable we report UNKNOWN rather than inventing a state.
        "status": "UNKNOWN",
        "detail": "metadata-only listing; lifecycle state lives in the registry",
    }


def _first(a: dict[str, Any] | None, b: dict[str, Any] | None, key: str) -> Any:
    for src in (a, b):
        if src and src.get(key):
            return src.get(key)
    return None


def _same_artifact(active: Path, candidate: Path) -> bool:
    """Same artifact, without trusting raw string comparison of separators."""
    try:
        return active.resolve() == candidate.resolve()
    except OSError:
        return str(active).casefold() == str(candidate).casefold()


def _active_artifact_path() -> Path | None:
    """The ONE authoritative active artifact, from the runtime owner.

    Reads the champion manager the same way ``/api/models/champion`` does. Any
    failure returns None (fail-closed: unknown active beats a guessed active).
    """
    try:
        from nexus_scalp.model_provisioning.service import serving_model_path
    except Exception:
        return None
    try:
        p = serving_model_path()
    except Exception:
        return None
    return p if p.is_file() else None


def _walk_bundles(root: Path) -> list[Path]:
    """Bundle directories under ``root``: a dir holding a weights file + a
    descriptive sidecar. Depth is bounded to keep the walk cheap on a large
    artifact tree, and unreadable subtrees are skipped rather than fatal."""
    found: list[Path] = []
    if not root.is_dir():
        return found
    try:
        stack: list[tuple[Path, int]] = [(root, 0)]
        seen: set[Path] = {root.resolve()}
        while stack:
            current, depth = stack.pop()
            try:
                entries = sorted(current.iterdir())
            except OSError:
                continue
            has_weights = False
            has_sidecar = False
            subdirs: list[Path] = []
            for entry in entries:
                try:
                    if entry.is_dir():
                        subdirs.append(entry)
                    elif entry.is_file():
                        low = entry.name.lower()
                        if low.endswith(_WEIGHT_EXTS):
                            has_weights = True
                        elif low in (_MANIFEST, _META):
                            has_sidecar = True
                except OSError:
                    continue
            if has_weights and has_sidecar:
                found.append(current)
                # A bundle dir holds its own model; its subdirs are still
                # walked because variants nest this way, but depth is bounded.
            if depth < 6:
                for sub in subdirs:
                    try:
                        resolved = sub.resolve()
                    except OSError:
                        continue
                    if resolved not in seen:
                        seen.add(resolved)
                        stack.append((sub, depth + 1))
    except OSError as exc:
        logger.debug("inventory walk failed under %s: %s", root, exc)
    return found


def list_model_inventory(
    limit: int = _DEFAULT_LIMIT, include_hashes: bool = False
) -> dict[str, Any]:
    """READ-ONLY model inventory across all bundle roots + the Studio registry.

    Returns ``{"available": bool, "models": [...], "active_artifact": str,
    "total": int, "limited": bool, "planes": {...}}``. Every field is derived;
    nothing is cached in process state, so the answer is always the current
    disk + DB truth.

    TWO PLANES exist on this system and are reported SEPARATELY (phase 37):

    * ``serving`` — the bundle the live engine actually runs, resolved from the
      runtime configuration. Exactly one entry carries ``active: true``.
    * ``studio`` — every checkpoint registered in Model Studio's own registry
      (``artifacts/models.db``). These are operator workspaces, NOT the live
      model; a registry row marked CHAMPION there does NOT mean the engine is
      serving it. Keeping the planes explicit is what stops the UI from
      presenting a Studio checkpoint as the live model.
    """
    limit = max(1, min(int(limit), _MAX_LIMIT))
    active = _active_artifact_path()

    try:
        from nexus_scalp.release.paths import get_runtime_workspace

        workspace = get_runtime_workspace()
    except Exception as exc:  # pragma: no cover - workspace resolution is core
        logger.debug("inventory workspace unresolved: %s", exc)
        return {
            "available": False,
            "models": [],
            "active_artifact": None,
            "total": 0,
            "limited": False,
        }

    entries: list[dict[str, Any]] = []
    seen_artifacts: set[str] = set()
    for rel in _BUNDLE_ROOTS:
        root = workspace / rel
        for bundle_dir in _walk_bundles(root):
            origin = "serving-slot" if rel == Path("artifacts") / "models" else "trained-candidate"
            entry = _bundle_entry(
                bundle_dir,
                active_artifact=active,
                origin_hint=origin,
                include_hashes=include_hashes,
            )
            if entry is not None:
                entry["plane"] = "serving" if entry.get("active") else "bundle"
                entries.append(entry)
                seen_artifacts.add(_artifact_key(entry.get("artifact")))

    # Studio registry plane: ONE query for all rows (never one per model).
    studio = _studio_registry_rows(workspace=workspace, active_artifact=active, seen=seen_artifacts)
    entries.extend(studio)

    # Two stable passes: newest-modified first, then the ACTIVE model is
    # promoted to the top. (A single mixed tuple cannot express the two
    # opposite directions without the active flag dominating mtime.)
    entries.sort(key=lambda item: str(item.get("modified_iso") or ""), reverse=True)
    entries.sort(key=lambda item: bool(item.get("active")), reverse=True)
    total = len(entries)
    limited = total > limit
    return {
        "available": True,
        "models": entries[:limit],
        "active_artifact": str(active) if active is not None else None,
        "total": total,
        "limited": limited,
        "limit": limit,
        # Plane census so the UI can state plainly where the active answer came
        # from instead of inferring it.
        "planes": {
            "serving": str(active) if active is not None else None,
            "active_count": sum(1 for e in entries if e.get("active")),
            "studio_registered": sum(1 for e in entries if e.get("plane") == "studio"),
            "on_disk_bundles": sum(1 for e in entries if e.get("plane") != "studio"),
        },
    }


def _artifact_key(path: Any) -> str:
    if not path:
        return ""
    try:
        return str(Path(str(path)).resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _resolve_registered(weights_path: str, workspace: Path) -> Path | None:
    """Resolve a Studio registry ``weights_path`` to a real file.

    The registry stores REPO_ROOT-relative POSIX-ish paths. Under a worktree
    run the recorded REPO_ROOT may differ from the tree whose ``artifacts/``
    the engine is using, so try, in order: as-given (absolute), the runtime
    workspace, then the registry module's own REPO_ROOT. The FIRST existing
    candidate wins; none existing is reported honestly as missing (never a
    fabricated path).
    """
    if not weights_path:
        return None
    candidates: list[Path] = []
    raw = Path(weights_path)
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(workspace / raw)
        try:
            from nexus_scalp.model_generation.model_registry import REPO_ROOT

            candidates.append(Path(str(REPO_ROOT)) / raw)
        except Exception:
            pass
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def _studio_registry_rows(
    *,
    workspace: Path,
    active_artifact: Path | None,
    seen: set[str],
) -> list[dict[str, Any]]:
    """Every registered Model Studio checkpoint as inventory rows.

    Fail-closed: a registry that cannot be opened yields NO rows (and a debug
    log) rather than an exception that would blank the whole inventory.
    """
    try:
        from nexus_scalp.model_generation.model_registry import get_model_registry
    except Exception as exc:
        logger.debug("studio registry unavailable: %s", exc)
        return []
    try:
        registry = get_model_registry()
        records = registry.list_models()
    except Exception as exc:
        logger.debug("studio registry listing failed: %s", exc)
        return []

    active_key = _artifact_key(active_artifact) if active_artifact is not None else ""
    rows: list[dict[str, Any]] = []
    for rec in records:
        weights = _resolve_registered(str(getattr(rec, "weights_path", "") or ""), workspace)
        key = _artifact_key(weights) if weights is not None else ""
        if key and key in seen:
            continue  # already listed from disk — one row per artifact
        if key:
            seen.add(key)
        scaler_raw = str(getattr(rec, "scaler_path", "") or "")
        scaler = _resolve_registered(scaler_raw, workspace) if scaler_raw else None
        is_active = bool(key and active_key and key == active_key)
        try:
            mtime = weights.stat().st_mtime if weights is not None else None
        except OSError:
            mtime = None
        metrics = getattr(rec, "metrics", None)
        rows.append(
            {
                "model_id": str(getattr(rec, "id", "") or ""),
                "version": str(getattr(rec, "version", "") or ""),
                # `stage` is the STUDIO lifecycle word (CHAMPION/STAGING/...).
                # It is reported as-is; `active` stays the live-engine truth.
                "source": "studio-registry",
                "plane": "studio",
                "name": str(getattr(rec, "name", "") or ""),
                "artifact": str(weights)
                if weights is not None
                else str(getattr(rec, "weights_path", "") or ""),
                "artifact_exists": weights is not None,
                "hash": (str(getattr(rec, "sha256", "") or "")[:16] or None),
                "scaler": str(scaler) if scaler is not None else None,
                "scaler_exists": scaler is not None,
                "schema_id": None,
                "dimension": _coerce_int(getattr(rec, "dimension", None)),
                "classes": None,
                "size_bytes": weights.stat().st_size if weights is not None else None,
                "modified_iso": _iso(mtime),
                "active": is_active,
                "status": str(getattr(rec, "stage", "") or "UNKNOWN"),
                "created_iso": str(getattr(rec, "created_at", "") or "") or None,
                "loaded_iso": str(getattr(rec, "loaded_at", "") or "") or None,
                "archived": str(getattr(rec, "stage", "") or "").upper() == "ARCHIVED",
                "metrics": metrics if isinstance(metrics, dict) else {},
                "detail": "registered in Model Studio (hot-load workspace plane)",
            }
        )
    return rows


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
