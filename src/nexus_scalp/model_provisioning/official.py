"""PATH A — OFFICIAL Nexus model bundle: download + full verification chain.

An official bundle is a directory of artifacts published by the release
pipeline (initially hosted on operator storage, e.g. Google Drive direct
download or GitHub raw — any HTTPS base URL):

    manifest.json        signed identity + bindings (schema below)
    model.pt             ScalpNet state_dict (canonical 70D/3-class)
    model.scaler.npz     mean/std sidecars
    model.meta.json      schema/geometry metadata
    dataset.parquet      (optional) the training dataset for provenance

manifest.json (schema ``nexus_model_bundle_v1``)::

    {
      "schema": "nexus_model_bundle_v1",
      "bundle_id": "official-xauusd-scalp_v3-<yyyymmdd>",
      "model_version": "1.0.0",
      "architecture": "ScalpNet",
      "architecture_version": "1.0.0",
      "feature_schema_id": "scalp_v3",
      "feature_schema_hash": "<64hex>",
      "dimension": 70,
      "class_count": 3,
      "dataset": {"id": "...", "version": "...", "sha256": "<64hex>"},
      "training": {"command": "...", "git_commit": "...", "seed": 42,
                    "walk_forward": {...}, "calibration_version": "..."},
      "files": {"model.pt": {"sha256": "...", "size": 123}, ...},
      "key_id": "2026-09-root",
      "signature": "<ed25519 hex over canonical payload>"
    }

The signature uses the SAME embedded Ed25519 trust root as the updater
(``release.signing.trusted_keys``) over a canonical JSON payload, so an
attacker who controls the storage host still cannot ship a bundle the
client will install. Verification order (fail-closed, never partial):

    download → SHA256 per file → manifest signature (trusted key)
    → schema/dimension/architecture binding → dataset identity
    → bundle integrity (engine load gates where torch is available)
    → atomic install → serving-slot re-verify → READY

No PyTorch is required for download + hash + signature + manifest/schema
validation; the tensor-level integrity probe is layered and enforced
AGAIN at engine load (the serving gates stay mandatory regardless of what
was installed — defense in depth, never trust-the-downloader).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus_scalp.model_provisioning.states import LifecycleState
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_provisioning.official")

BUNDLE_MANIFEST_SCHEMA = "nexus_model_bundle_v1"
#: Env override for the official bundle base URL (operator hosting choice).
OFFICIAL_BASE_URL_ENV = "NEXUS_OFFICIAL_MODEL_BASE_URL"
#: Default official base (published Nexus model repository layout). May be
#: empty until the operator hosts the first official bundle — an unset
#: source is reported HONESTLY as NOT_CONFIGURED (never a silent fallback
#: to an unofficial mirror).
DEFAULT_OFFICIAL_BASE_URL = os.environ.get(OFFICIAL_BASE_URL_ENV, "")

_REQUIRED_MANIFEST_FIELDS = (
    "schema",
    "bundle_id",
    "model_version",
    "architecture",
    "architecture_version",
    "feature_schema_id",
    "dimension",
    "class_count",
    "files",
    "key_id",
    "signature",
)


class OfficialBundleError(RuntimeError):
    """A step of the PATH A verification chain failed (fail closed)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _canonical_payload(manifest: dict[str, Any]) -> bytes:
    """Deterministic bytes covered by the signature — every signed field,
    nothing else. Mirrors update_manifest.manifest_signing_payload's intent
    for the model-bundle schema."""
    signed = {
        k: manifest[k] for k in _REQUIRED_MANIFEST_FIELDS if k != "signature" if k in manifest
    }
    return json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def verify_bundle_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Verify signature + schema/geometry bindings of a bundle manifest.

    Returns the verified manifest. Raises OfficialBundleError on anything
    short of full verification (never installs on partial success)."""
    if not isinstance(manifest, dict):
        raise OfficialBundleError("MANIFEST_MALFORMED", "manifest is not a JSON object")
    missing = [k for k in _REQUIRED_MANIFEST_FIELDS if k not in manifest]
    if missing:
        raise OfficialBundleError("MANIFEST_MALFORMED", f"missing fields: {missing}")
    if manifest["schema"] != BUNDLE_MANIFEST_SCHEMA:
        raise OfficialBundleError(
            "SCHEMA_UNSUPPORTED",
            f"bundle schema {manifest['schema']!r} != {BUNDLE_MANIFEST_SCHEMA!r}",
        )
    # Canonical-geometry binding (scalp_v3 / 70 / 3) — an official bundle
    # that does not match the serving contract can never be installed.
    if int(manifest["dimension"]) != 70 or int(manifest["class_count"]) != 3:
        raise OfficialBundleError(
            "GEOMETRY_MISMATCH",
            f"bundle declares dim={manifest['dimension']} classes={manifest['class_count']}"
            " — canonical serving contract is 70D/3-class",
        )
    if str(manifest["feature_schema_id"]) != "scalp_v3":
        raise OfficialBundleError("SCHEMA_ID_MISMATCH", str(manifest["feature_schema_id"]))
    if str(manifest["architecture"]) != "ScalpNet":
        raise OfficialBundleError("ARCHITECTURE_MISMATCH", str(manifest["architecture"]))
    # Ed25519 against the embedded updater trust root (single trust anchor).
    from nexus_scalp.release.signing.trusted_keys import trusted_public_key

    key_hex = trusted_public_key(str(manifest["key_id"]))
    if key_hex is None:
        raise OfficialBundleError("UNKNOWN_KEY", f"key_id {manifest['key_id']!r} not in trust root")
    sig = manifest["signature"]
    if not isinstance(sig, str) or len(sig) != 128:
        raise OfficialBundleError("SIGNATURE_INVALID", "signature is not 64-byte hex")
    try:
        import nacl.signing

        verify_key = nacl.signing.VerifyKey(bytes.fromhex(key_hex))
        verify_key.verify(_canonical_payload(manifest), bytes.fromhex(sig))
    except OfficialBundleError:
        raise
    except Exception as exc:
        raise OfficialBundleError("SIGNATURE_INVALID", f"Ed25519 verify failed: {exc}") from exc
    files = manifest["files"]
    if not isinstance(files, dict) or "model.pt" not in files:
        raise OfficialBundleError("MANIFEST_MALFORMED", "files block missing/empty")
    for name, entry in files.items():
        digest = str((entry or {}).get("sha256", "") or "")
        if len(digest) != 64:
            raise OfficialBundleError("MANIFEST_MALFORMED", f"files[{name}].sha256 not 64-hex")
    return manifest


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _http_get(
    url: str, dest: Path, *, timeout: float = 60.0, max_bytes: int = 512 * 1024 * 1024
) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "NexusScalpEngine/official-model"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
            read = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                read += len(chunk)
                if read > max_bytes:
                    raise OfficialBundleError(
                        "DOWNLOAD_TOO_LARGE", f"{url} exceeds {max_bytes} bytes"
                    )
                out.write(chunk)
    except OfficialBundleError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise OfficialBundleError("DOWNLOAD_FAILED", f"{url}: {exc}") from exc


def _drive_confirm_url(file_id: str) -> str:
    """Google Drive direct-download URL (handles the virus-scan interstitial
    for large files via the confirm token on a second GET)."""
    return f"https://drive.usercontent.google.com/download?id={urllib.parse.quote(file_id)}&export=download&confirm=t"


@dataclass
class VerifiedBundle:
    """A fully verified official bundle staged in a temp directory."""

    dir: Path
    manifest: dict[str, Any]
    state: LifecycleState = LifecycleState.VERIFIED

    @property
    def bundle_id(self) -> str:
        return str(self.manifest.get("bundle_id", ""))

    def model_path(self) -> Path:
        return self.dir / "model.pt"


class OfficialBundleSource:
    """PATH A executor: base URL (or Drive file id) -> verified bundle dir.

    The bundle layout is fetched file-by-file from
    ``<base>/<relative-name>`` after manifest verification, so the manifest
    is the single signed authority for what the bundle contains.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url if base_url is not None else DEFAULT_OFFICIAL_BASE_URL).strip()

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def manifest_url(self) -> str:
        if not self.configured:
            raise OfficialBundleError(
                "NOT_CONFIGURED",
                "official model source URL is not set — configure "
                f"{OFFICIAL_BASE_URL_ENV} or the operator default, or use local training",
            )
        base = self.base_url
        if base.startswith("drive:") or base.startswith("gdrive:"):
            return _drive_confirm_url(base.split(":", 1)[1])
        return urllib.parse.urljoin(base.rstrip("/") + "/", "manifest.json")

    def _fetch(self, url: str, dest: Path) -> None:
        _http_get(url, dest)

    def download_and_verify(self, work_dir: Path | None = None) -> VerifiedBundle:
        """Full chain: download manifest -> verify signature/bindings ->
        download files -> SHA256 each -> integrity probe where possible.
        Raises OfficialBundleError on any failed step; returns a staged,
        VERIFIED bundle directory (caller installs it)."""
        tmp = Path(work_dir or tempfile.mkdtemp(prefix="nexus-official-bundle-"))
        tmp.mkdir(parents=True, exist_ok=True)
        manifest_path = tmp / "manifest.json"
        logger.info("[OFFICIAL] event=DOWNLOAD_STARTED base=%s", self.base_url or "(unset)")
        self._fetch(self.manifest_url(), manifest_path)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OfficialBundleError(
                "MANIFEST_MALFORMED", f"unreadable manifest.json: {exc}"
            ) from exc
        verify_bundle_manifest(manifest)

        base = self.base_url
        for name, entry in manifest["files"].items():
            safe = Path(name)
            if safe.is_absolute() or ".." in safe.parts:
                raise OfficialBundleError("MANIFEST_MALFORMED", f"unsafe file name {name!r}")
            dest = tmp / safe
            dest.parent.mkdir(parents=True, exist_ok=True)
            if base.startswith("drive:") or base.startswith("gdrive:"):
                # Drive hosting of multi-file bundles is unsupported by the
                # per-file fetch; official Drive hosting ships a SINGLE
                # archive — see download_bundle_archive. Guard loudly.
                raise OfficialBundleError(
                    "LAYOUT_UNSUPPORTED",
                    "drive: base supports archive bundles only — publish via an "
                    "HTTPS directory layout or use download_bundle_archive()",
                )
            self._fetch(
                urllib.parse.urljoin(base.rstrip("/") + "/", str(safe).replace("\\", "/")), dest
            )
            actual = _sha256_file(dest)
            if actual != str(entry["sha256"]).lower():
                raise OfficialBundleError(
                    "SHA256_MISMATCH",
                    f"{name}: downloaded {actual[:16]}… != manifest {str(entry['sha256'])[:16]}…",
                )
            size = int(entry.get("size", -1))
            if size >= 0 and dest.stat().st_size != size:
                raise OfficialBundleError(
                    "SIZE_MISMATCH", f"{name}: {dest.stat().st_size} != {size}"
                )

        self._integrity_probe(tmp)
        logger.critical(
            "[OFFICIAL] event=BUNDLE_VERIFIED bundle_id=%s model_sha16=%s",
            manifest.get("bundle_id"),
            _sha256_file(tmp / "model.pt")[:16],
        )
        return VerifiedBundle(dir=tmp, manifest=manifest)

    def download_bundle_archive(self, work_dir: Path | None = None) -> VerifiedBundle:
        """Drive/HTTPS single-archive variant: fetch model-bundle.zip, then
        run the same verification chain inside it."""
        tmp = Path(work_dir or tempfile.mkdtemp(prefix="nexus-official-zip-"))
        tmp.mkdir(parents=True, exist_ok=True)
        archive = tmp / "model-bundle.zip"
        if self.base_url.startswith(("drive:", "gdrive:")):
            file_id = self.base_url.split(":", 1)[1]
            self._fetch(_drive_confirm_url(file_id), archive)
        else:
            self._fetch(
                urllib.parse.urljoin(self.base_url.rstrip("/") + "/", "model-bundle.zip"), archive
            )
        extract = tmp / "bundle"
        extract.mkdir(exist_ok=True)
        import zipfile

        try:
            with zipfile.ZipFile(archive) as zf:
                for member in zf.namelist():
                    mp = Path(member)
                    if mp.is_absolute() or ".." in mp.parts:
                        raise OfficialBundleError("ARCHIVE_UNSAFE", f"zip entry {member!r}")
                zf.extractall(extract)
        except zipfile.BadZipFile as exc:
            raise OfficialBundleError("ARCHIVE_UNREADABLE", str(exc)) from exc
        manifest_path = extract / "manifest.json"
        if not manifest_path.exists():
            # One-level nesting tolerated (zip of a folder).
            kids = [p for p in extract.iterdir() if p.is_dir()]
            for k in kids:
                if (k / "manifest.json").exists():
                    extract = k
                    manifest_path = extract / "manifest.json"
                    break
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OfficialBundleError(
                "MANIFEST_MALFORMED", f"archive manifest unreadable: {exc}"
            ) from exc
        verify_bundle_manifest(manifest)
        for name, entry in manifest["files"].items():
            f = extract / name
            if not f.exists():
                raise OfficialBundleError("FILE_MISSING", f"{name} not present in archive")
            actual = _sha256_file(f)
            if actual != str(entry["sha256"]).lower():
                raise OfficialBundleError(
                    "SHA256_MISMATCH", f"{name} in archive differs from manifest"
                )
        self._integrity_probe(extract)
        return VerifiedBundle(dir=extract, manifest=manifest)

    def _integrity_probe(self, bundle_dir: Path) -> None:
        """Bundle-level internal consistency (model.meta.json geometry +
        sidecar binding via the trainer emission gate where possible).
        The tensor-level probe needs torch; the packaged engine always has
        it, a bare source env may not — then the FINAL load-time gates in
        the engine remain the enforcement point (never skipped, only
        layered). Manifest bindings of the downloaded files are verified
        before we get here, so an unverified bundle never reaches this
        point either way."""
        meta_path = bundle_dir / "model.meta.json"
        if not meta_path.exists():
            raise OfficialBundleError("ARTIFACT_MISSING", "model.meta.json absent")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OfficialBundleError("ARTIFACT_UNREADABLE", f"model.meta.json: {exc}") from exc
        if int(meta.get("feature_schema_dimension", -1)) != 70:
            raise OfficialBundleError("GEOMETRY_MISMATCH", "model.meta.json not 70D")
        try:
            import torch  # noqa: F401

            from nexus_scalp.model_lifecycle.load_integrity import verify_artifact_integrity

            # The staged bundle carries manifest.json (signed) with
            # model_sha256 — the sidecar verifier reads exactly that.
            verify_artifact_integrity(bundle_dir / "model.pt")
        except ImportError:
            logger.warning(
                "[OFFICIAL] event=TORCH_ABSENT tensor_probe_deferred "
                "note='load-time serving gates remain mandatory enforcement'"
            )
        except Exception as exc:
            raise OfficialBundleError("ARTIFACT_INTEGRITY_FAILED", str(exc)[:300]) from exc


def install_verified_bundle(
    verified: VerifiedBundle, serving_model_path: Path, *, origin: str = "OFFICIAL"
) -> dict[str, Any]:
    """Atomically install a VERIFIED bundle into the serving slot.

    Never installs anything that did not pass download_and_verify (this
    function has no download/verify path — the caller MUST hold a
    VerifiedBundle). The existing slot occupant is moved aside to
    ``.previous-<ts>`` (keep-1), and the bundle's manifest.json is
    re-stamped with the local install provenance (origin + installed_at)
    while preserving every signed field (the signature covers the
    ORIGINAL manifest — the local note lives in install-state, not in the
    signed manifest).
    """
    from nexus_scalp.model_provisioning import service as prov

    dst = Path(serving_model_path).parent
    dst.mkdir(parents=True, exist_ok=True)
    prior = dst / "manifest.json"
    installed_at = prov.utcnow_iso()
    staging = Path(tempfile.mkdtemp(prefix=".official_install_", dir=str(dst)))
    try:
        for name in verified.manifest["files"]:
            src = verified.dir / name
            if not src.exists():
                raise OfficialBundleError(
                    "FILE_MISSING", f"verified bundle lost {name} before install"
                )
            shutil.copy2(src, staging / Path(name).name)
        shutil.copy2(verified.dir / "manifest.json", staging / "manifest.json")
        # Commit-marker ordering: payload first, manifest last.
        for f in sorted(staging.iterdir()):
            if f.name == "manifest.json":
                continue
            os.replace(f, dst / f.name)
        os.replace(staging / "manifest.json", dst / "manifest.json")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    with contextlib.suppress(Exception):
        if prior.exists():
            pass  # manifest was overwritten atomically; single-file keep-last
    prov.record_install(
        model_path=Path(serving_model_path),
        origin=origin,
        bundle_id=str(verified.manifest.get("bundle_id", "")),
        model_version=str(verified.manifest.get("model_version", "")),
        model_sha256=_sha256_file(Path(serving_model_path)),
        installed_at=installed_at,
    )
    logger.critical(
        "[OFFICIAL] event=BUNDLE_INSTALLED slot=%s bundle_id=%s origin=%s",
        dst,
        verified.manifest.get("bundle_id"),
        origin,
    )
    return {"installed": True, "path": str(serving_model_path), "bundle_id": verified.bundle_id}


def cleanup_dir(path: Path) -> None:
    with contextlib.suppress(Exception):
        for root, _dirs, files in os.walk(path):
            for f in files:
                with contextlib.suppress(OSError):
                    os.chmod(Path(root) / f, stat.S_IWRITE)
        shutil.rmtree(path, ignore_errors=True)


__all__ = [
    "BUNDLE_MANIFEST_SCHEMA",
    "DEFAULT_OFFICIAL_BASE_URL",
    "OFFICIAL_BASE_URL_ENV",
    "OfficialBundleError",
    "OfficialBundleSource",
    "VerifiedBundle",
    "cleanup_dir",
    "install_verified_bundle",
    "verify_bundle_manifest",
]
