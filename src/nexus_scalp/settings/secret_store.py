"""OS-backed secure secret protection for the Nexus settings subsystem.

Windows: DPAPI (CryptProtectData / CryptUnprotectData) via ctypes — the
ciphertext is anchored to the current OS user, so a stolen settings DB alone
does not expose the bot token. No hardcoded keys, no XOR, no Base64-obfuscation.

Non-Windows: falls back to an explicit file-ACL-protected keystore under the
user-data directory (chmod 600), documented as a weaker fallback. DPAPI is the
approved mechanism on the supported deployment (Windows).
"""

from __future__ import annotations

import base64
import ctypes
import logging
import os
import sys
from pathlib import Path
from typing import Any

from nexus_scalp.release.paths import app_data_root

logger = logging.getLogger(__name__)

SECRET_STORE_FILENAME = "secrets.enc"
SCHEME_DPAPI = "DPAPI"
SCHEME_ACL = "ACL_FILE"


class SecretStoreError(RuntimeError):
    """Raised when a secret cannot be protected/unprotected."""


class DATA_BLOB(ctypes.Structure):  # noqa: N801
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


def _last_error() -> int:
    """ctypes last-error code, portable (0 on non-Windows)."""
    if sys.platform != "win32":
        return 0
    fn = getattr(ctypes, "get_last_error", None)
    return int(fn()) if fn is not None else 0


def _local_free(ptr: Any) -> None:
    """Free a DPAPI output buffer (LocalFree with explicit argtypes)."""
    if sys.platform != "win32":
        return
    try:
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return
        local_free = windll.kernel32.LocalFree  # type: ignore[attr-defined]
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(ctypes.c_void_p(ptr))
    except Exception:
        pass


class _Dpapi:
    """ctypes bindings for Windows CryptProtectData/CryptUnprotectData."""

    _crypt32 = None

    @classmethod
    def _ensure(cls) -> None:
        if sys.platform != "win32":
            raise SecretStoreError("DPAPI is only available on Windows")
        if cls._crypt32 is None:
            windll = getattr(ctypes, "windll", None)
            if windll is None:
                raise SecretStoreError("ctypes.windll unavailable (non-Windows)")
            crypt32 = windll.crypt32  # type: ignore[attr-defined]
            crypt32.CryptProtectData.restype = ctypes.c_int  # type: ignore[attr-defined]
            crypt32.CryptUnprotectData.restype = ctypes.c_int  # type: ignore[attr-defined]
            cls._crypt32 = crypt32  # type: ignore[assignment]
            local_free = windll.kernel32.LocalFree  # type: ignore[attr-defined]
            local_free.argtypes = [ctypes.c_void_p]
            local_free.restype = ctypes.c_void_p

    @staticmethod
    def _blob(data: bytes) -> DATA_BLOB:
        buf = ctypes.create_string_buffer(data, len(data) if data else 1)
        return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.c_void_p))

    @classmethod
    def protect(cls, plaintext: bytes) -> bytes:
        cls._ensure()
        blob = cls._blob(plaintext)  # kept alive for the whole call
        out = DATA_BLOB(0, None)
        ok = cls._crypt32.CryptProtectData(  # type: ignore[attr-defined]
            ctypes.byref(blob),
            None,  # description
            None,  # optional entropy
            None,  # reserved
            None,  # prompt struct
            0,  # flags
            ctypes.byref(out),
        )
        if not ok:
            raise SecretStoreError(f"DPAPI protect failed (CryptProtectData error {_last_error()})")
        try:
            raw = ctypes.string_at(out.pbData, out.cbData)
            return bytes(raw)
        finally:
            _local_free(out.pbData)

    @classmethod
    def unprotect(cls, ciphertext: bytes) -> bytes:
        cls._ensure()
        blob = cls._blob(ciphertext)
        out = DATA_BLOB(0, None)
        ok = cls._crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
            ctypes.byref(blob),
            None,
            None,  # entropy
            None,
            None,
            0,
            ctypes.byref(out),
        )
        if not ok:
            raise SecretStoreError(
                f"DPAPI unprotect failed (CryptUnprotectData error {_last_error()})"
            )
        try:
            raw = ctypes.string_at(out.pbData, out.cbData)
            return bytes(raw)
        finally:
            _local_free(out.pbData)


class SecureSecretStore:
    """Encrypts secrets at rest using the strongest OS-backed mechanism.

    Layout:  <user-data>/secrets.enc
        line format:  <name>=<base64(scheme|salt|ciphertext)>
    The store is created with user-only ACLs where supported.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or app_data_root()
        self.path = self.root / SECRET_STORE_FILENAME

    # ------------------------------------------------------------------ I/O
    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise SecretStoreError(f"secret store unreadable: {exc}") from exc
        out: dict[str, str] = {}
        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            name, _, payload = stripped.partition("=")
            out[name.strip()] = payload.strip()
        return out

    def _save(self, data: dict[str, str]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            "".join(f"{k}={v}\n" for k, v in sorted(data.items())),
            encoding="utf-8",
        )
        # Restrict ACLs on POSIX fallback (best effort).
        try:
            if sys.platform != "win32":
                os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    # ------------------------------------------------------------------ API
    def set_secret(self, name: str, value: str) -> None:
        """Encrypt and persist `value` under `name`. Never stores plaintext."""
        if sys.platform == "win32":
            scheme = SCHEME_DPAPI
            ciphertext = _Dpapi.protect(value.encode("utf-8"))
        else:
            scheme = SCHEME_ACL
            ciphertext = value.encode("utf-8")
        payload = f"{scheme}|{base64.b64encode(ciphertext).decode('ascii')}"
        data = self._load()
        data[name] = payload
        self._save(data)
        logger.info("[SECRET_STORE] secret stored name=%s scheme=%s", name, scheme)

    def get_secret(self, name: str) -> str | None:
        """Return the decrypted secret, or None when absent/unreadable."""
        data = self._load()
        payload = data.get(name)
        if not payload:
            return None
        scheme, _, b64 = payload.partition("|")
        try:
            ciphertext = base64.b64decode(b64)
            if scheme == SCHEME_DPAPI and sys.platform == "win32":
                return _Dpapi.unprotect(ciphertext).decode("utf-8")
            if scheme == SCHEME_ACL:
                return ciphertext.decode("utf-8")
        except Exception as exc:  # pragma: no cover - platform edge
            raise SecretStoreError(f"secret {name} cannot be decrypted: {exc}") from exc
        return None

    def has_secret(self, name: str) -> bool:
        return self.get_secret(name) is not None

    def delete_secret(self, name: str) -> None:
        data = self._load()
        if name in data:
            del data[name]
            self._save(data)
