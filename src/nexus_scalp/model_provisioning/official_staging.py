"""Disk truth helpers for official staging; no serving mutation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexus_scalp.model_provisioning.official_contract import MAX_MANIFEST_BYTES, require


def check_cancel(event: Any) -> None:
    require(event is None or not event.is_set(), "CANCELLED", "official acquisition cancelled")


def read_json(path: Path) -> dict[str, Any]:
    from nexus_scalp.model_provisioning.official import OfficialBundleError

    try:
        require(
            not path.is_symlink() and path.is_file(),
            "MANIFEST_MALFORMED",
            "regular JSON file required",
        )
        require(
            path.stat().st_size <= MAX_MANIFEST_BYTES, "MANIFEST_MALFORMED", "JSON exceeds limit"
        )

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            obj: dict[str, Any] = {}
            for key, item in pairs:
                require(key not in obj, "MANIFEST_MALFORMED", "duplicate JSON key")
                obj[key] = item
            return obj

        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        require(isinstance(value, dict), "MANIFEST_MALFORMED", "JSON object required")
        return value
    except (OSError, ValueError, RecursionError) as exc:
        raise OfficialBundleError("MANIFEST_MALFORMED", str(exc)) from exc


def verify_file(path: Path, entry: dict[str, Any]) -> None:
    from nexus_scalp.model_provisioning.official import _sha256_file

    require(not path.is_symlink() and path.is_file(), "FILE_MISSING", path.name)
    require(path.stat().st_size == entry["size"], "SIZE_MISMATCH", path.name)
    require(_sha256_file(path) == entry["sha256"].lower(), "SHA256_MISMATCH", path.name)
