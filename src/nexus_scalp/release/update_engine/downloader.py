"""Extracted from release/updater.py (update-package split); behavior preserved."""

from __future__ import annotations

import hashlib
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nexus_scalp.release.update_engine.discovery import (
    HashVerifier,
    UpdateDiscovery,
)


class SafeDownloader:
    """Stage-area downloads: <name>.part -> verify -> rename.

    The running installation is never touched until the payload is verified.
    Resumption is supported for interrupted transfers (Range requests).
    """

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def resume_supported() -> bool:
        return True

    def _candidate_path(self, name: str) -> Path:
        complete = self.cache_dir / name
        if complete.exists():
            return complete
        return self.cache_dir / f"{name}.part"

    @staticmethod
    def _validate_resume_response(resp: Any, requested_from: int) -> tuple[bool, int]:
        """Interpret the response for a (possibly) Range request.

        Returns (resumed, prefix_bytes): whether the server actually
        resumed at requested_from and how many prefix bytes the hasher
        must seed from the local .part. Fails safe: any ambiguity is
        treated as a FULL (non-resumed) transfer. BUG-171.
        """
        status = getattr(resp, "status", None) or getattr(resp, "code", 0)
        if requested_from <= 0:
            return (False, 0)
        if status == 206:
            content_range = resp.headers.get("Content-Range", "") if resp.headers else ""
            m = re.match(r"bytes (\d+)-\d+/\d+", content_range.strip())
            if m and int(m.group(1)) == requested_from:
                return (True, requested_from)
            # 206 without a verifiable Content-Range start: ambiguous ->
            # treat as full body (restart). Fails safe against corruption.
            return (False, 0)
        # 200 (or anything else) with a Range header: server ignored the
        # range — full body follows. Restart from zero.
        return (False, 0)

    def download(
        self,
        url: str,
        dest_name: str,
        expected_sha256: str | None = None,
        timeout: int = 300,
        chunk_size: int = 1024 * 1024,
        max_retries: int = 3,
    ) -> Path:
        part = self.cache_dir / f"{dest_name}.part"
        headers = {"User-Agent": UpdateDiscovery.USER_AGENT}
        existing = part.stat().st_size if part.exists() else 0
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        req = urllib.request.Request(url, headers=headers)
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    # BUG-171: validate that the server actually honored a
                    # Range request. A proxy that ignores Range replies 200
                    # with the FULL body; blindly appending would corrupt the
                    # .part (prefix + full body) and every retry would fail
                    # SHA verification with a misleading error.
                    resumed, prefix_bytes = self._validate_resume_response(
                        resp, requested_from=existing
                    )
                    # Resume-safe hash: the hasher must cover the ALREADY
                    # downloaded bytes too, or a resumed file always fails
                    # verification and the partial is discarded (BUG-122).
                    h = hashlib.sha256()
                    if resumed and prefix_bytes > 0:
                        with open(part, "rb") as pf:
                            while block := pf.read(chunk_size):
                                h.update(block)
                    elif not resumed and existing > 0:
                        # Full body for a Range request: the server replaced
                        # the transfer — restart from zero, overwrite the part.
                        part.unlink(missing_ok=True)
                    mode = "ab" if (resumed and prefix_bytes > 0) else "wb"
                    with open(part, mode) as f:
                        while block := resp.read(chunk_size):
                            f.write(block)
                            h.update(block)
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt >= max_retries:
                    raise
                attempt += 1
                time.sleep(min(2**attempt * 2, 30))
                existing = part.stat().st_size if part.exists() else 0
                headers = {"User-Agent": UpdateDiscovery.USER_AGENT}
                if existing > 0:
                    headers["Range"] = f"bytes={existing}-"
                req = urllib.request.Request(url, headers=headers)
                continue
        final = self.cache_dir / dest_name
        if expected_sha256 and not HashVerifier.verify_sha256(part, expected_sha256):
            part.unlink(missing_ok=True)
            raise ValueError("SHA-256 mismatch — corrupt download discarded, NOT installed")
        os.replace(part, final)
        return final


# ---------------------------------------------------------------------------
# LIVE-safety + quiesce (sections 13/14)
# ---------------------------------------------------------------------------
