"""Official model acquisition: signed revision-2 contract and verified staging.

The nexus_model_bundle_v1 family is retained; contract_version=2 deliberately
rejects the former partial-field signature. Every field except signature is
canonicalized. Discovery is not availability and origin is not governance.
Missing runtime verification dependencies fail closed as VERIFICATION_PENDING.
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
#: Fixed discovery channel. A configured URL never implies a published model.
DEFAULT_OFFICIAL_BASE_URL = (
    "https://github.com/Opselon/NexusTradingForexBot/releases/download/official-model-stable"
)

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
    signed = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(
        signed, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


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
    try:
        from nexus_scalp.model_provisioning.official_contract import validate_contract

        validate_contract(manifest)
    except OfficialBundleError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise OfficialBundleError("MANIFEST_MALFORMED", str(exc)) from exc
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
    from nexus_scalp.model_provisioning.official_transport import http_get

    http_get(url, dest, timeout=timeout, max_bytes=max_bytes)


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
    """Signed release discovery; verified bytes staged before any installation."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (
            base_url
            if base_url is not None
            else os.environ.get(OFFICIAL_BASE_URL_ENV, DEFAULT_OFFICIAL_BASE_URL)
        ).strip()
        self._fetch_limit = 256 * 1024
        self._cancel_event: Any = None

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def manifest_url(self) -> str:
        from nexus_scalp.model_provisioning.official_contract import https_url

        if not self.configured:
            raise OfficialBundleError("NOT_CONFIGURED", "official source explicitly disabled")
        base = https_url(self.base_url)
        if urllib.parse.urlsplit(base).path.endswith("/manifest.json"):
            return base
        return base.rstrip("/") + "/manifest.json"

    def _fetch(self, url: str, dest: Path) -> None:
        from nexus_scalp.model_provisioning.official_transport import http_get

        http_get(
            url, dest, timeout=60.0, max_bytes=self._fetch_limit, cancel_event=self._cancel_event
        )

    def download_and_verify(
        self, work_dir: Path | None = None, *, cancel_event: Any = None
    ) -> VerifiedBundle:
        from nexus_scalp.model_provisioning.official_contract import MAX_MANIFEST_BYTES
        from nexus_scalp.model_provisioning.official_staging import (
            check_cancel,
            read_json,
            verify_file,
        )

        check_cancel(cancel_event)
        if work_dir is not None:
            Path(work_dir).mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="nexus-official-bundle-", dir=work_dir))
        self._cancel_event = cancel_event
        try:
            self._fetch_limit = MAX_MANIFEST_BYTES
            try:
                self._fetch(self.manifest_url(), tmp / "manifest.json")
            except OfficialBundleError as exc:
                if exc.code == "HTTP_NOT_FOUND":
                    raise OfficialBundleError(
                        "OFFICIAL_MODEL_NOT_PUBLISHED",
                        "no manifest published at the official source",
                    ) from exc
                raise
            check_cancel(cancel_event)
            manifest = read_json(tmp / "manifest.json")
            verify_bundle_manifest(manifest)
            for name, entry in manifest["files"].items():
                check_cancel(cancel_event)
                self._fetch_limit = entry["size"]
                urls = [entry["url"], *entry.get("mirrors", [])]
                for index, url in enumerate(urls):
                    check_cancel(cancel_event)
                    try:
                        self._fetch(url, tmp / name)
                        verify_file(tmp / name, entry)
                        break
                    except OfficialBundleError as exc:
                        (tmp / name).unlink(missing_ok=True)
                        if exc.code == "CANCELLED" or index == len(urls) - 1:
                            raise
                check_cancel(cancel_event)
            self._integrity_probe(tmp)
            check_cancel(cancel_event)
            return VerifiedBundle(dir=tmp, manifest=manifest)
        except BaseException:
            cleanup_dir(tmp)
            raise
        finally:
            self._cancel_event = None

    def download_bundle_archive(self, work_dir: Path | None = None) -> VerifiedBundle:
        raise OfficialBundleError(
            "LAYOUT_UNSUPPORTED",
            "archives/Drive shorthand disabled; use signed per-file HTTPS URLs",
        )

    def _integrity_probe(self, bundle_dir: Path) -> None:
        from nexus_scalp.model_provisioning.official_runtime import verify_runtime

        verify_runtime(bundle_dir)


def install_verified_bundle(
    verified: VerifiedBundle,
    serving_model_path: Path,
    *,
    origin: str = "OFFICIAL",
    cancel_event: Any = None,
) -> dict[str, Any]:
    """Thin delegate: all transaction logic lives in official_install."""
    from nexus_scalp.model_provisioning.official_install import (
        install_verified_bundle as _install,
    )

    return _install(verified, serving_model_path, origin=origin, cancel_event=cancel_event)


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
